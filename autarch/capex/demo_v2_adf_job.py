"""Opt-in ADF source-to-GPT batch with explicit SQL/review persistence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Callable, Mapping, Optional, Sequence

from ..errors import ModelError
from ..intelligence.base import ModelProvider
from .demo_v2_contracts import (
    CapitalizationAssessment, DemoWorkflowRequest, DemoWorkflowResult,
    BundlingDisposition, CapitalizationRecommendation, NatureResult,
    PersistenceCounts, TaskDetermination, TaskMatchMethod, TaskResolutionStatus,
    WorkflowExecutionStatus, WorkflowPackageSummary,
)
from .demo_v2_asset_classes import AssetClassMaster, is_eu_invoice, load_asset_class_master
from .demo_v2_potential_tags import screen_potential_tags
from .demo_v2_llm_showcase import _AuditedProvider
from .demo_v2_intake import _join, _write_artifact, prepare_intake, read_source
from .demo_v2_job import _package_from_payload, run_demo_v2_workflow
from .demo_v2_llm_decisions import SqlLlmDecisionWorkflow
from .demo_v2_normalization import NormalizedInvoicePackage
from .demo_v2_reference import DemoPolicySnapshot, TaskMasterSnapshot, canonical_sha256
from .demo_v2_sql_policy import (
    SqlPolicyReferences, assess_governed_invoice, load_sql_policy_references,
    suggest_assessment_categories,
)
from .persistence import DatabaseConnection


class AdfDecisionWorkflow(SqlLlmDecisionWorkflow):
    adf_context: Optional[dict[str, object]] = None
    governed_references: SqlPolicyReferences | None = None

    def assess(
        self, package: NormalizedInvoicePackage, tasks: Sequence[TaskDetermination],
        policy: DemoPolicySnapshot, request: DemoWorkflowRequest,
    ) -> list[CapitalizationAssessment]:
        if self.governed_references is None:
            return super().assess(package, tasks, policy, request)
        provider = _AuditedProvider(
            self.provider, self.calls, deployment=self.deployment,
            stage="ACCOUNTING_DECISION", package_id=package.package_id,
        )
        assessments, batch = assess_governed_invoice(
            package, tasks, self.governed_references, provider, request,
            allow_demo=self.demo_recommendations,
        )
        self.accounting_decisions[package.package_id] = batch
        return assessments

    def finish(self, result: DemoWorkflowResult) -> None:
        if self.governed_references is None:
            return super().finish(result)
        accepted = [call for call in self.calls if call.response is not None and call.validation_error is None]
        if result.status != "SUCCEEDED" or len(self.accounting_decisions) != len(self.sources) \
                or sum(call.stage in {"TASK_SUGGESTION", "ASSET_CLASS_SUGGESTION"} for call in accepted) \
                    != len(self.sources) \
                or sum(call.stage == "ACCOUNTING_DECISION" for call in accepted) != len(self.sources) \
                or any(call.stage == "BUNDLE_DECISION" for call in self.calls):
            raise ValueError("The complete SQL-governed classification/accounting workflow did not succeed.")
        self.complete = True

    def evidence(self, result: DemoWorkflowResult) -> dict[str, object]:
        payload = super().evidence(result)
        payload["provenance"] = f"ORIGINAL_SOURCE_DEMO_WITH_UNAPPROVED_POLICY:{result.workflow_run_id}"
        if self.adf_context is not None:
            payload.update(self.adf_context)
        if self.governed_references is not None:
            payload.update(
                bundlingMaxAttempts=0, bundlingEligibilityAuthority="DEFERRED_NOT_EVALUATED",
                decisionPromptVersions={
                    "accounting": "adf-sql-governed-accounting-v1",
                    "potentialTags": "sql-governed-potential-tags-v1",
                    "category": "adf-sql-assessment-category-v1", "bundling": None,
                },
            )
        return payload


def _read_pinned(
    uri: str, expected_sha256: str, read: Callable[[str], bytes],
) -> bytes:
    if len(expected_sha256) != 64 or any(char not in "0123456789abcdef" for char in expected_sha256):
        raise ValueError("Reference SHA-256 must contain exactly 64 lowercase hexadecimal characters.")
    data = read(uri)
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError(f"Reference bytes changed; review and pin the approved snapshot: {uri}")
    return data


def _with_reference_reviews(
    result: DemoWorkflowResult, packages: Sequence[NormalizedInvoicePackage],
    exceptions: Sequence[Mapping[str, object]], master: TaskMasterSnapshot,
    request: DemoWorkflowRequest, policy: DemoPolicySnapshot,
    asset_master: AssetClassMaster,
) -> DemoWorkflowResult:
    tasks = list(result.task_determinations)
    assessments = list(result.assessments)
    summaries = list(result.packages)
    reasons_by_package: dict[str, list[str]] = {}
    for item in exceptions:
        reasons = item["reasons"]
        if not isinstance(reasons, list) or not all(isinstance(reason, str) for reason in reasons):
            raise ValueError("Reference exception reasons must be a list of strings.")
        reasons_by_package[str(item["packageId"])] = reasons
    for package in packages:
        if package.package_id not in reasons_by_package:
            continue
        reasons = reasons_by_package[package.package_id]
        reason = " ".join(reasons)
        for line in package.lines:
            tasks.append(TaskDetermination(
                workflow_run_id=request.workflow_run_id, package_id=package.package_id,
                invoice_id=package.invoice_id, line_id=line.line_id,
                classification_type="ASSET_CLASS" if is_eu_invoice(package) else "TASK",
                resolution_status=TaskResolutionStatus.REVIEW, match_method=TaskMatchMethod.REVIEW, confidence=0,
                unresolved_reason=reason, rationale=reason,
                evidence_refs=[f"source:{package.source.sha256}", f"line:{line.line_id}"],
                task_master_sha256=canonical_sha256(asset_master.model_dump(mode="json", by_alias=True))
                    if is_eu_invoice(package) else master.source.sha256.lower(),
                task_master_version=asset_master.source_sha256 if is_eu_invoice(package) else master.snapshot_id,
                model_deployment=None,
                prompt_version="adf-reference-review-v1", created_at_utc=result.completed_at_utc,
            ))
            assessments.append(CapitalizationAssessment(
                workflow_run_id=request.workflow_run_id, package_id=package.package_id,
                invoice_id=package.invoice_id, line_id=line.line_id,
                nature_result=NatureResult.REVIEW, repair_maintenance_indicator=False,
                never_capitalize_hit=False, recommendation=CapitalizationRecommendation.REVIEW,
                outcome_category="REFERENCE_OR_EXTRACTION_REVIEW",
                bundling_disposition=BundlingDisposition.REVIEW, missing_inputs=reasons, review_reasons=reasons,
                rationale=reason, used_llm=False, model_version=None,
                policy_version=policy.policy_id, created_at_utc=result.completed_at_utc,
            ))
        summaries.append(WorkflowPackageSummary(
            package_id=package.package_id, invoice_id=package.invoice_id,
            line_count=len(package.lines), resolved_task_count=0,
            review_task_count=0 if is_eu_invoice(package) else len(package.lines),
            review_asset_class_count=len(package.lines) if is_eu_invoice(package) else 0,
            capex_count=0, opex_count=0, review_assessment_count=len(package.lines),
        ))
    return DemoWorkflowResult.model_validate({
        **result.model_dump(mode="python"),
        "task_determinations": tasks, "assessments": assessments, "packages": summaries,
    })


def run_adf_batch(
    *,
    input_base_uri: str,
    output_base_uri: str,
    workflow_run_id: str,
    task_master_uri: str,
    task_master_sha256: str,
    policy_uri: str,
    policy_sha256: str,
    provider: ModelProvider,
    connection: Optional[DatabaseConnection] = None,
    fresh_pdf_extraction: bool = False,
    allow_provisional_pdf_recommendations: bool = False,
    demo_recommendations: bool = False,
    governed_sql_references: bool = False,
    read: Callable[[str], bytes] = read_source,
    write: Callable[[str, object], None] = _write_artifact,
) -> dict[str, object]:
    if governed_sql_references and connection is None:
        raise ValueError("Governed SQL references require --sql-persist and an Azure SQL connection.")
    if allow_provisional_pdf_recommendations and not fresh_pdf_extraction:
        raise ValueError("Provisional PDF recommendations require fresh PDF extraction.")
    if demo_recommendations and not (fresh_pdf_extraction and allow_provisional_pdf_recommendations):
        raise ValueError("Demo recommendations require fresh extraction and provisional-demo PDF mode.")
    master_bytes = _read_pinned(task_master_uri, task_master_sha256, read)
    policy_bytes = _read_pinned(policy_uri, policy_sha256, read)
    master = TaskMasterSnapshot.model_validate_json(master_bytes)
    asset_master = load_asset_class_master()
    policy = DemoPolicySnapshot.model_validate_json(policy_bytes)
    if policy.region != "NORTH_AMERICA":
        raise ValueError("This intake worker requires a North American policy snapshot.")
    if connection is not None:
        from .demo_v2_adf_sql import load_adf_asset_class_master, load_adf_task_master

        master = load_adf_task_master(connection, master)
        asset_master = load_adf_asset_class_master(connection, asset_master)
    references = load_sql_policy_references(connection) \
        if governed_sql_references and connection is not None else None
    policies = {policy.capitalization.currency: policy}
    if demo_recommendations and references is None:
        for currency, amount in {"USD": 1000, "EUR": 1000, "DKK": 7500}.items():
            payload = policy.model_dump(mode="json", by_alias=True)
            payload["capitalization"].update(currency=currency, baseThreshold=amount)
            for setting in payload["settings"]:
                setting.update(
                    status="PROPOSED_DEMO_ASSUMPTION",
                    evidence="Explicit conditional ADF demo; native currency, no FX; not approved regional policy.",
                )
                if setting["settingId"] == "base-threshold":
                    setting["value"] = {"amount": amount, "currency": currency}
            policies[currency] = DemoPolicySnapshot.model_validate(payload)
    if fresh_pdf_extraction and not provider.supports_vision():
        raise ValueError("Fresh ADF PDF extraction requires a vision-capable provider.")
    artifacts: dict[str, object] = {}
    source_bytes: dict[str, bytes] = {}

    def pinned_read(uri: str) -> bytes:
        if uri not in source_bytes:
            source_bytes[uri] = read(uri)
        return source_bytes[uri]

    def retain(uri: str, document: object) -> None:
        write(uri, document)
        artifacts[uri] = document

    intake = prepare_intake(
        input_base_uri=input_base_uri, output_base_uri=output_base_uri,
        workflow_run_id=workflow_run_id, policy_version=policy.policy_id,
        read=pinned_read, write=retain,
    )
    request = DemoWorkflowRequest.model_validate(artifacts[intake.request_uri])
    packages = [
        _package_from_payload(artifacts[reference.package_uri], package_id=reference.package_id)
        for reference in request.packages
    ]
    if references is not None:
        policies = {}
        for package in packages:
            if not package.source.file_name.lower().endswith((".zip", ".xlsx")):
                raise ValueError("SQL-governed demo routing requires original ZIP or XLSX source provenance.")
            threshold, _ = references.threshold_for(
                package, request.requested_at_utc.date(), allow_demo=demo_recommendations,
            )
            if threshold is None:
                continue
            native = policy.model_dump(mode="json", by_alias=True)
            native["region"] = "EUROPE" if is_eu_invoice(package) else "NORTH_AMERICA"
            native["capitalization"].update(
                currency=threshold.currency, baseThreshold=float(threshold.governed_threshold),
            )
            for setting in native["settings"]:
                if setting["settingId"] == "base-threshold":
                    setting["evidence"] = f"{threshold.citation}; {threshold.policy_reference}"
                    setting["value"] = {"amount": float(threshold.governed_threshold), "currency": threshold.currency}
            selected = DemoPolicySnapshot.model_validate(native)
            if threshold.currency in policies and policies[threshold.currency] != selected:
                raise ValueError("Conflicting regional policy snapshots cannot share one currency handoff.")
            policies[threshold.currency] = selected
    run_uri = _join(output_base_uri, workflow_run_id)
    if references is not None:
        write(_join(run_uri, "sql-reference-snapshot.json"), {
            "sha256": references.sha256, "policyAsOfDate": request.requested_at_utc.date().isoformat(),
            "policyDateBasis": "DEMO_RUN_DATE_NOT_HISTORICAL_BOOKING_APPROVAL",
            "references": references.model_dump(mode="json", by_alias=True),
        })
    extraction_documents: dict[str, dict] = {}
    extraction_calls: list[dict] = []
    if fresh_pdf_extraction:
        from .demo_v2_adf_extraction import extract_adf_pdfs

        extracted = extract_adf_pdfs(
            packages=packages, intake=intake, provider=provider, run_uri=run_uri,
            read=pinned_read, write=write,
            allow_provisional_recommendations=allow_provisional_pdf_recommendations,
            corroborate_archive_currency=demo_recommendations,
        )
        packages = extracted.packages
        extraction_documents = extracted.documents
        extraction_calls = extracted.calls
        for package in packages:
            write(_join(run_uri, "processing-packages", f"{package.package_id}.json"),
                  package.model_dump(mode="json", by_alias=True))
        request = request.model_copy(update={"packages": [
            reference.model_copy(update={
                "package_uri": _join(run_uri, "processing-packages", f"{reference.package_id}.json"),
            }) for reference in request.packages
        ]})
    reference_exceptions: list[dict[str, object]] = []
    model_packages = []
    package_uris = {reference.package_id: reference.package_uri for reference in request.packages}
    for package in packages:
        reasons = []
        if package.package_id in extraction_documents:
            reasons.extend(extraction_documents[package.package_id]["blockingReviewReasons"])
        if references is None and not is_eu_invoice(package) and not demo_recommendations and (master.region_code != "US" or package.country_region not in {
            "US", "USA", "United States", "North America",
        }):
            reasons.append("No governed regional TaskMaster snapshot is supplied for this invoice.")
        if references is None and package.currency not in policies:
            reasons.append("No governed native-currency policy is supplied; no FX conversion is authorized.")
        if reasons:
            reference_exceptions.append({
                "packageId": package.package_id, "invoiceId": package.invoice_id,
                "packageUri": package_uris[package.package_id],
                "sourceSha256": package.source.sha256,
                "lineIds": [line.line_id for line in package.lines],
                "currency": package.currency, "countryRegion": package.country_region,
                "route": "REVIEW_REQUIRED", "reasons": reasons, "modelCalls": 0,
                "normalizationReviewReasons": package.data_quality.review_reasons,
            })
        else:
            model_packages.append(package)
    exceptions_uri = _join(run_uri, "reference-exceptions.json")
    write(exceptions_uri, {
        "workflowRunId": workflow_run_id, "packages": reference_exceptions,
        "businessApproved": False,
    })
    if not model_packages and not fresh_pdf_extraction:
        raise ValueError("No invoice has matching regional TaskMaster and native-currency policy references.")
    model_ids = {package.package_id for package in model_packages}
    full_request = request
    model_request = request.model_copy(update={
        "packages": [reference for reference in request.packages if reference.package_id in model_ids],
    }) if model_packages else None
    if model_request is not None and demo_recommendations:
        model_request = model_request.model_copy(update={"allow_synthetic_inputs": True})
    model_request_uri = _join(run_uri, "model-request.json")
    write(model_request_uri, model_request.model_dump(mode="json", by_alias=True) if model_request else {
        "workflowRunId": workflow_run_id, "packages": [], "route": "ALL_SOURCES_REQUIRE_REVIEW",
    })
    input_hash = canonical_sha256({
        "intake": intake.model_dump(mode="json", by_alias=True),
        "taskMasterSha256": task_master_sha256, "policySha256": policy_sha256,
        "sqlTaskMasterSha256": canonical_sha256(master.model_dump(mode="json", by_alias=True)) if connection else None,
        "assetClassMasterSha256": canonical_sha256(asset_master.model_dump(mode="json", by_alias=True)),
        "freshPdfExtraction": fresh_pdf_extraction,
        "demoRecommendations": demo_recommendations,
        "sqlPolicyReferencesSha256": references.sha256 if references else None,
        "currencyPolicies": {key: value.model_dump(mode="json", by_alias=True) for key, value in policies.items()},
        "processingPackages": [package.model_dump(mode="json", by_alias=True) for package in packages],
    })
    workflow = AdfDecisionWorkflow(
        sources=model_packages, provider=provider, deployment=request.model_deployment,
        task_master=master, fixture_id=workflow_run_id, input_sha256=input_hash,
        demo_recommendations=demo_recommendations,
        asset_class_master=asset_master,
    )
    workflow.governed_references = references
    evidence_uri = _join(run_uri, "llm-evidence.json")
    result_uri = _join(run_uri, "result.json")
    workflow.adf_context = {
        "inputSha256": input_hash, "taskMasterSha256": task_master_sha256,
        "taskMasterSnapshotSha256": canonical_sha256(master.model_dump(mode="json", by_alias=True)),
        "policySha256": policy_sha256, "policyApprovalStatus": policy.approval_status,
        "sourceManifestUri": _join(run_uri, "intake.json"),
        "modelRequestUri": model_request_uri, "referenceExceptionsUri": exceptions_uri,
        "freshPdfExtraction": fresh_pdf_extraction, "extractionCalls": extraction_calls,
        "allowProvisionalPdfRecommendations": allow_provisional_pdf_recommendations,
        "demoRecommendations": demo_recommendations,
        "demoAssumptions": {
            "nativeThresholds": {key: value.capitalization.base_threshold for key, value in policies.items()},
            "fxConversion": False, "businessApproved": False,
            "crossRegionUsCatalog": False,
            "missingUnitCostBasis": "CONDITIONAL_LINE_AMOUNT_PROXY" if demo_recommendations else "REVIEW",
            "sourceValuesOverwritten": False,
        },
        "currencyPolicies": {key: value.model_dump(mode="json", by_alias=True) for key, value in policies.items()},
        "provisionalPackageIds": [
            package_id for package_id, document in extraction_documents.items()
            if document["provisionalRecommendations"]
        ],
        "extractionCallsAttempted": len(extraction_calls),
        "taskMasterSource": "pub.v1_TaskMaster" if connection is not None else "PINNED_JSON",
        "assetClassMasterSource": "ref.assetClassMaster" if connection is not None else "PINNED_JSON",
        "assetClassMasterSha256": canonical_sha256(asset_master.model_dump(mode="json", by_alias=True)),
        "assetClassSourceWorkbookSha256": asset_master.source_sha256,
        "classificationRouting": "SOURCE_ZIP_NA_TASKMASTER;SOURCE_XLSX_EU_ASSETCLASSMASTER",
        "governedSqlReferences": references is not None,
        "sqlPolicyReferencesSha256": references.sha256 if references else None,
        "sqlReferenceSnapshotUri": _join(run_uri, "sql-reference-snapshot.json") if references else None,
        "bundlingDeferred": references is not None,
    }
    if references is not None:
        workflow.adf_context["demoAssumptions"] = {
            "nativeThresholds": {key: value.capitalization.base_threshold for key, value in policies.items()},
            "thresholdAuthority": "SQL_REFERENCE_ROWS_WITH_ORIGINAL_APPROVAL_LABELS",
            "policyDateBasis": "DEMO_RUN_DATE_NOT_HISTORICAL_BOOKING_APPROVAL",
            "fxConversion": False, "businessApproved": False, "crossRegionUsCatalog": False,
            "missingUnitCostBasis": "REVIEW_NO_LINE_AMOUNT_PROXY", "sourceValuesOverwritten": False,
        }

    def persist_evidence(document: dict[str, object]) -> None:
        document.update(workflow.adf_context or {})
        document["provenance"] = f"ORIGINAL_SOURCE_DEMO_WITH_UNAPPROVED_POLICY:{workflow_run_id}"
        write(evidence_uri, document)

    result: Optional[DemoWorkflowResult] = None
    model_result: Optional[DemoWorkflowResult] = None
    try:
        if model_request is not None:
            model_result = run_demo_v2_workflow(
                request=model_request, packages=model_packages, task_master=master, policy=policy,
                task_resolver=workflow.resolve, assessment_resolver=workflow.assess,
                bundle_resolver=workflow.bundle,
                currency_policies=policies if demo_recommendations and references is None else None,
                enable_bundling=references is None,
            )
            result = model_result
            workflow.finish(model_result)
    except (ModelError, ValueError, OSError) as exc:
        workflow.error = f"{type(exc).__name__}: {exc}"
        if result is not None:
            evidence = workflow.evidence(result)
        else:
            evidence = {
                "workflowRunId": workflow_run_id, "status": "INCOMPLETE",
                "error": workflow.error,
                "calls": [call.model_dump(mode="json", by_alias=True) for call in workflow.calls],
            }
        persist_evidence(evidence)
        if result is not None:
            write(result_uri, result.model_dump(mode="json", by_alias=True))
        raise
    if model_result is None:
        result = DemoWorkflowResult(
            workflow_run_id=workflow_run_id, status=WorkflowExecutionStatus.SUCCEEDED,
            started_at_utc=request.requested_at_utc, completed_at_utc=datetime.now(timezone.utc),
            packages=[], task_determinations=[], assessments=[],
        )
    assert result is not None
    # Keep the JSON-only default's original model-subset contract.
    if connection is not None or fresh_pdf_extraction:
        result = _with_reference_reviews(result, packages, reference_exceptions, master, full_request, policy, asset_master)
    tagged_packages = [package for package in packages
                       if package.package_id in {item.package_id for item in result.task_determinations}]
    tag_provider = _AuditedProvider(
        provider, workflow.calls, deployment=request.model_deployment, stage="POTENTIAL_TAGS",
    )
    try:
        result = screen_potential_tags(tagged_packages, result, tag_provider, sql_references=references)
        if references is not None:
            result = suggest_assessment_categories(
                tagged_packages, result, references, _AuditedProvider(
                    provider, workflow.calls, deployment=request.model_deployment, stage="ASSESSMENT_CATEGORY",
                ),
            )
    except (ModelError, ValueError, OSError) as exc:
        workflow.error = f"Potential screening/category suggestion failed: {type(exc).__name__}: {exc}"
        workflow.complete = False
        if isinstance(exc, ValueError) and workflow.calls \
                and workflow.calls[-1].stage in {"POTENTIAL_TAGS", "ASSESSMENT_CATEGORY"}:
            workflow.calls[-1].validation_error = str(exc)
        persist_evidence(workflow.evidence(result))
        raise
    if model_result is not None:
        model_result = model_result.model_copy(update={"task_determinations": [
            item for item in result.task_determinations if item.package_id in model_ids
        ], "assessments": [item for item in result.assessments if item.package_id in model_ids]})
    if connection is not None:
        # These candidate counts become authoritative only after SQL readback and batch.json.
        result = result.model_copy(update={"persistence": PersistenceCounts(
            task_suggestions=sum(item.classification_type == "TASK" for item in result.task_determinations),
            asset_class_suggestions=sum(item.classification_type == "ASSET_CLASS" for item in result.task_determinations),
            assessments=len(result.assessments),
            bundle_groups=len(result.bundle_groups),
            bundle_members=sum(len(group.members) for group in result.bundle_groups),
        )})
    assert workflow.adf_context is not None
    workflow.adf_context["potentialReviewTags"] = {
        "promptVersion": "sql-governed-potential-tags-v1" if references else "potential-review-tags-v1",
        "approvalStatus": "POTENTIAL_REVIEW_ONLY",
        "screenedLineCount": sum(item.potential_tags is not None for item in result.task_determinations),
        "changesAccountingOrBundlingDecisions": False, "calculatesAroLiability": False,
    }
    workflow.adf_context["completeResultSha256"] = canonical_sha256(
        result.model_dump(mode="json", by_alias=True),
    )
    evidence = workflow.evidence(model_result or result)
    if model_result is None:
        evidence["status"] = "TAG_SCREENING_ONLY_REFERENCE_REVIEW"
    persist_evidence(evidence)
    write(result_uri, result.model_dump(mode="json", by_alias=True))
    sql_counts: dict[str, int] = {}
    review_uri = None
    if connection is not None:
        from .demo_v2_adf_review import build_adf_review
        from .demo_v2_adf_sql import persist_adf_results

        review = build_adf_review(
            intake=intake, packages=packages, result=result, evidence=evidence,
            master=master, policy=policy, exceptions=reference_exceptions,
            extraction_documents=extraction_documents,
            policies=policies,
            asset_class_codes=[item.asset_class_code for item in asset_master.classes],
        )
        review_uri = _join(run_uri, "review.json")
        write(review_uri, review)
        try:
            if references is not None and load_sql_policy_references(connection).sha256 != references.sha256:
                raise ValueError("SQL policy references changed during inference; no results were committed.")
            sql_counts = persist_adf_results(
                connection, request=full_request, packages=packages, result=result,
                model_result=model_result, workflow=workflow if model_result else None,
                review=review, read=pinned_read,
            )
        except Exception as exc:
            write(_join(run_uri, "sql-failure.json"), {
                "workflowRunId": workflow_run_id, "status": "FAILED",
                "error": f"{type(exc).__name__}: {exc}", "completionPublished": False,
            })
            raise
    summary: dict[str, object] = {
        "schemaVersion": "1.0-capex-adf-batch",
        "demoRecommendations": demo_recommendations,
        "governedSqlReferences": references is not None,
        "sqlPolicyReferencesSha256": references.sha256 if references else None,
        "bundlingDeferred": references is not None,
        "categorySuggestionCount": sum(item.category_suggestion is not None for item in result.assessments),
        "resolvedTaskCount": sum(task.classification_type == "TASK" and
            task.resolution_status == TaskResolutionStatus.RESOLVED for task in result.task_determinations),
        "resolvedAssetClassCount": sum(task.classification_type == "ASSET_CLASS" and
            task.resolution_status == TaskResolutionStatus.RESOLVED for task in result.task_determinations),
        "capexCount": sum(item.recommendation == CapitalizationRecommendation.CAPEX for item in result.assessments),
        "opexCount": sum(item.recommendation == CapitalizationRecommendation.OPEX for item in result.assessments),
        "stage": "MODEL_WORKFLOW_COMPLETED" if model_result is not None else "REFERENCE_REVIEW_COMPLETED",
        "workflowRunId": workflow_run_id,
        "status": "REVIEW_REQUIRED" if reference_exceptions or any(
            document["provisionalRecommendations"] for document in extraction_documents.values()
        ) or any(
            item.recommendation.value == "REVIEW" for item in result.assessments
        ) or any(
            item.resolution_status.value == "REVIEW" for item in result.task_determinations
        ) or (
            workflow.bundle_decisions is not None
            and any(item.decision == "REVIEW" for item in workflow.bundle_decisions.items)
        ) else "RECOMMENDATIONS_READY",
        "sourceFormat": "ORIGINAL_PDF_AND_JC_LEDGER" if fresh_pdf_extraction else "ABBYY_CSV_AND_JC_LEDGER",
        "freshPdfExtraction": fresh_pdf_extraction,
        "allowProvisionalPdfRecommendations": allow_provisional_pdf_recommendations,
        "provisionalPackageCount": sum(
            bool(document["provisionalRecommendations"]) for document in extraction_documents.values()
        ),
        "inputSha256": input_hash,
        "taskMasterSha256": task_master_sha256,
        "taskMasterSnapshotSha256": workflow.adf_context["taskMasterSnapshotSha256"],
        "policySha256": policy_sha256,
        "policyApprovalStatus": policy.approval_status,
        "sourceCount": len(intake.sources),
        "packageCount": intake.package_count,
        "lineCount": sum(len(package.lines) for package in packages),
        "preprocessedLineCount": intake.line_count,
        "excludedRecordCount": intake.excluded_record_count,
        "modelPackageCount": len(model_packages),
        "referenceExceptionPackageCount": len(reference_exceptions),
        "referenceExceptionLineCount": sum(
            len(package.lines) for package in packages if package.package_id not in model_ids
        ),
        "taskSuggestionCount": sum(item.classification_type == "TASK" for item in result.task_determinations),
        "assetClassSuggestionCount": sum(item.classification_type == "ASSET_CLASS" for item in result.task_determinations),
        "potentialTagScreenedLineCount": sum(item.potential_tags is not None for item in result.task_determinations),
        "potentialAroLineCount": sum(any(tag.tag == "POTENTIAL_ARO" for tag in item.potential_tags or [])
                                   for item in result.task_determinations),
        "assessmentCount": len(result.assessments),
        "bundleDecisionCount": len(workflow.bundle_decisions.items) if workflow.bundle_decisions else 0,
        "bundleGroupCount": len(result.bundle_groups),
        "modelCallsAttempted": len(workflow.calls) + len(extraction_calls),
        "modelResponsesReceived": sum(call.response is not None for call in workflow.calls) + len(extraction_calls),
        "taskMasterSource": "pub.v1_TaskMaster" if connection else "PINNED_JSON",
        "sqlPersistence": sql_counts, "reviewUri": review_uri,
        "reviewStatus": "AWAITING_ANALYST" if connection else "NOT_IMPORTED",
        "resultUri": result_uri, "evidenceUri": evidence_uri, "referenceExceptionsUri": exceptions_uri,
        "intakeUri": _join(run_uri, "intake.json"),
        "requestUri": intake.request_uri, "modelRequestUri": model_request_uri,
        "completedAtUtc": datetime.now(timezone.utc).isoformat(),
        "persistence": "AZURE_SQL_AND_IMMUTABLE_JSON" if connection else "IMMUTABLE_JSON_ARTIFACTS",
        "sqlWrites": sum(sql_counts.values()), "humanApproval": False, "erpWrites": 0,
    }
    write(_join(run_uri, "batch.json"), summary)
    return summary


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-base-uri", required=True)
    parser.add_argument("--output-base-uri", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--task-master-uri", required=True)
    parser.add_argument("--task-master-sha256", required=True)
    parser.add_argument("--policy-uri", required=True)
    parser.add_argument("--policy-sha256", required=True)
    parser.add_argument("--execute", action="store_true", required=True,
                        help="Explicitly opt in to billable model calls.")
    parser.add_argument("--sql-persist", action="store_true",
                        help="Read SQL TaskMaster and atomically persist results plus analyst-review handoff.")
    parser.add_argument("--governed-sql-references", action="store_true",
                        help="Use SQL policy/indicator/category tables; honor their basis and defer bundling.")
    parser.add_argument("--fresh-pdf-extraction", action="store_true",
                        help="Extract original PDF members with vision, rather than using ABBYY CSV facts.")
    parser.add_argument("--pdf-recommendation-mode", choices=("strict", "provisional-demo"), default="strict",
                        help="Demo-only opt-in for reliable line evidence with incomplete details; analyst review remains required.")
    parser.add_argument("--recommendation-mode", choices=("strict", "conditional-demo"), default="strict",
                        help="Unapproved native-currency policies, semantic catalog lookup and conditional expenditure advice.")
    args = parser.parse_args(argv)
    if args.pdf_recommendation_mode == "provisional-demo" and not args.fresh_pdf_extraction:
        parser.error("provisional-demo requires --fresh-pdf-extraction")
    if args.governed_sql_references and not args.sql_persist:
        parser.error("--governed-sql-references requires --sql-persist")
    from ..intelligence.azure_openai import AzureOpenAIProvider

    provider = AzureOpenAIProvider(
        deployment="gpt-5.1", endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        managed_identity_client_id=os.environ["AZURE_CLIENT_ID"], json_mode=True,
    )
    connection = None
    try:
        if args.sql_persist:
            from .sql_connection import connect_azure_sql

            connection = connect_azure_sql(
                server_fqdn=os.environ["CAPEX_SQL_SERVER"], database_name=os.environ["CAPEX_SQL_DATABASE"],
                managed_identity_client_id=os.environ["AZURE_CLIENT_ID"],
            )
        summary = run_adf_batch(
            input_base_uri=args.input_base_uri, output_base_uri=args.output_base_uri,
            workflow_run_id=args.workflow_run_id, task_master_uri=args.task_master_uri,
            task_master_sha256=args.task_master_sha256, policy_uri=args.policy_uri,
            policy_sha256=args.policy_sha256, provider=provider, connection=connection,
            fresh_pdf_extraction=args.fresh_pdf_extraction,
            allow_provisional_pdf_recommendations=args.pdf_recommendation_mode == "provisional-demo",
            demo_recommendations=args.recommendation_mode == "conditional-demo",
            governed_sql_references=args.governed_sql_references,
        )
    finally:
        if connection is not None:
            connection.close()
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
