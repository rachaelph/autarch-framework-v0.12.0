"""Direct finite-batch runner for the CapEx Demo V2 workflow."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from urllib.parse import unquote, urlparse

from pydantic import ValidationError

from .agent_invocation_models import TaskSuggestion, TaskSuggestionLine
from .demo_v2_bundling import evaluate_bundle_candidates
from .demo_v2_capitalization import (
    CapitalizationRuleSet,
    assess_capitalization_for_invoice,
)
from .demo_v2_contracts import (
    DemoWorkflowRequest,
    DemoWorkflowResult,
    PersistenceCounts,
    TaskDetermination,
    WorkflowError,
    WorkflowPackageSummary,
)
from .demo_v2_persistence import persist_demo_v2_results
from .demo_v2_reference import DemoPolicySnapshot, TaskMasterSnapshot
from .demo_v2_synthetic import (
    apply_synthetic_line_values,
    synthetic_setting_ids,
    validate_synthetic_register,
)
from .demo_v2_task_suggestion import (
    TaskSuggestionModel,
    build_azure_task_suggestion_model,
    suggest_tasks_for_invoice,
)
from .demo_v2_normalization import (
    JCWorkbookNormalizationResult,
    NormalizedInvoicePackage,
)
from .sql_connection import connect_azure_sql
from .agent_invocation_persistence import persist_task_suggestion


TaskResolver = Callable[
    [NormalizedInvoicePackage, DemoWorkflowRequest],
    Sequence[TaskDetermination],
]

EXIT_SUCCEEDED = 0
EXIT_PARTIAL = 2
EXIT_FAILED = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stage_error(
    *,
    stage: str,
    code: str,
    message: str,
    package_id: Optional[str] = None,
) -> WorkflowError:
    return WorkflowError(
        stage=stage,
        code=code,
        message=message,
        packageId=package_id,
    )


def _validate_request_inputs(
    request: DemoWorkflowRequest,
    packages: Sequence[NormalizedInvoicePackage],
    task_master: TaskMasterSnapshot,
    policy: DemoPolicySnapshot,
) -> list[WorkflowError]:
    errors: list[WorkflowError] = []
    packages_by_id = {package.package_id: package for package in packages}

    if len(packages_by_id) != len(packages):
        errors.append(
            _stage_error(
                stage="VALIDATION",
                code="DUPLICATE_PACKAGE",
                message="Loaded packages must have unique package IDs.",
            )
        )

    for package_ref in request.packages:
        package = packages_by_id.get(package_ref.package_id)
        if package is None:
            errors.append(
                _stage_error(
                    stage="VALIDATION",
                    code="PACKAGE_NOT_LOADED",
                    message="The requested normalized package was not loaded.",
                    package_id=package_ref.package_id,
                )
            )
            continue
        if (
            package_ref.expected_invoice_id is not None
            and package.invoice_id != package_ref.expected_invoice_id
        ):
            errors.append(
                _stage_error(
                    stage="VALIDATION",
                    code="INVOICE_ID_MISMATCH",
                    message=(
                        f"Expected invoice {package_ref.expected_invoice_id}, found "
                        f"{package.invoice_id}."
                    ),
                    package_id=package.package_id,
                )
            )
        if package.run_id != package_ref.run_id:
            errors.append(
                _stage_error(
                    stage="VALIDATION",
                    code="RUN_ID_MISMATCH",
                    message=(
                        f"Expected package run {package_ref.run_id}, found "
                        f"{package.run_id}."
                    ),
                    package_id=package.package_id,
                )
            )
        if (
            package_ref.expected_source_sha256 is not None
            and package.source.sha256 != package_ref.expected_source_sha256
        ):
            errors.append(
                _stage_error(
                    stage="VALIDATION",
                    code="SOURCE_HASH_MISMATCH",
                    message="The normalized package source hash does not match the request.",
                    package_id=package.package_id,
                )
            )

    requested_ids = {package.package_id for package in request.packages}
    for unexpected_id in sorted(set(packages_by_id) - requested_ids):
        errors.append(
            _stage_error(
                stage="VALIDATION",
                code="UNEXPECTED_PACKAGE",
                message="A loaded package was not present in the workflow request.",
                package_id=unexpected_id,
            )
        )

    if request.task_region != task_master.region_code:
        errors.append(
            _stage_error(
                stage="VALIDATION",
                code="TASK_MASTER_REGION_MISMATCH",
                message=(
                    f"Expected TaskMaster region {request.task_region}, found "
                    f"{task_master.region_code}."
                ),
            )
        )
    if (
        request.capitalization_policy_version is not None
        and request.capitalization_policy_version != policy.policy_id
    ):
        errors.append(
            _stage_error(
                stage="VALIDATION",
                code="POLICY_VERSION_MISMATCH",
                message=(
                    f"Expected capitalization policy "
                    f"{request.capitalization_policy_version}, found {policy.policy_id}."
                ),
            )
        )
    if (
        request.bundling_policy_version is not None
        and request.bundling_policy_version != policy.policy_id
    ):
        errors.append(
            _stage_error(
                stage="VALIDATION",
                code="BUNDLING_POLICY_VERSION_MISMATCH",
                message=(
                    f"Expected bundling policy {request.bundling_policy_version}, "
                    f"found {policy.policy_id}."
                ),
            )
        )
    return errors


def _validate_task_determinations(
    *,
    package: NormalizedInvoicePackage,
    request: DemoWorkflowRequest,
    determinations: Sequence[TaskDetermination],
) -> list[TaskDetermination]:
    determinations_by_line = {
        determination.line_id: determination
        for determination in determinations
    }
    if len(determinations_by_line) != len(determinations):
        raise ValueError("Task determinations contain duplicate line IDs.")
    expected_line_ids = {line.line_id for line in package.lines}
    if set(determinations_by_line) != expected_line_ids:
        raise ValueError(
            "Task determination line IDs must exactly match the normalized package."
        )
    for determination in determinations:
        if determination.workflow_run_id != request.workflow_run_id:
            raise ValueError(
                "Task determination workflowRunId does not match the request."
            )
        if determination.package_id != package.package_id:
            raise ValueError(
                "Task determination packageId does not match the normalized package."
            )
        if determination.invoice_id != package.invoice_id:
            raise ValueError(
                "Task determination invoiceId does not match the normalized package."
            )
    return [
        determinations_by_line[line.line_id]
        for line in package.lines
    ]


def _default_task_resolver(
    *,
    task_master: TaskMasterSnapshot,
    model: TaskSuggestionModel,
    created_at_utc: datetime,
) -> TaskResolver:
    def resolve(
        package: NormalizedInvoicePackage,
        request: DemoWorkflowRequest,
    ) -> Sequence[TaskDetermination]:
        return suggest_tasks_for_invoice(
            package,
            task_master,
            workflow_run_id=request.workflow_run_id,
            model=model,
            model_deployment=request.model_deployment,
            prompt_version=request.task_prompt_version,
            created_at_utc=created_at_utc,
        )

    return resolve


def _persist_task_determinations(
    *,
    connection: Any,
    package: NormalizedInvoicePackage,
    request: DemoWorkflowRequest,
    determinations: Sequence[TaskDetermination],
) -> int:
    event_seed = f"{request.workflow_run_id}:{package.package_id}".encode("utf-8")
    event_id = f"capex-v2-{hashlib.sha256(event_seed).hexdigest()}"
    suggestion = TaskSuggestion(
        package_id=package.package_id,
        invoice_id=package.invoice_id,
        run_id=package.run_id,
        correlation_id=request.workflow_run_id,
        lines=[
            TaskSuggestionLine(
                line_id=determination.line_id,
                recommendation="REVIEW",
                task_code=determination.suggested_task_code,
                confidence=determination.confidence,
                rationale=determination.rationale,
                evidence_refs=list(determination.evidence_refs),
            )
            for determination in determinations
        ],
        overall_recommendation="REVIEW",
        created_at_utc=max(
            determination.created_at_utc
            for determination in determinations
        ),
    )
    outcome = persist_task_suggestion(
        connection=connection,
        suggestion=suggestion,
        event_id=event_id,
        region_code=request.task_region,
        model_deployment=request.model_deployment,
        prompt_version=request.task_prompt_version,
        task_master_sha256=determinations[0].task_master_sha256.upper(),
    )
    return outcome


def run_demo_v2_workflow(
    *,
    request: DemoWorkflowRequest,
    packages: Sequence[NormalizedInvoicePackage],
    assessment_packages: Optional[
        Sequence[NormalizedInvoicePackage]
    ] = None,
    task_master: TaskMasterSnapshot,
    policy: DemoPolicySnapshot,
    task_model: Optional[TaskSuggestionModel] = None,
    task_resolver: Optional[TaskResolver] = None,
    rule_set: Optional[CapitalizationRuleSet] = None,
    synthetic_setting_ids: Sequence[str] = (),
    connection: Any = None,
    requested_at_utc: Optional[datetime] = None,
    completed_at_utc: Optional[datetime] = None,
) -> DemoWorkflowResult:
    """Run one complete, finite Demo V2 workflow request."""
    requested_at = requested_at_utc or request.requested_at_utc
    completed_at = completed_at_utc or _utc_now()
    errors = _validate_request_inputs(request, packages, task_master, policy)
    if errors:
        return DemoWorkflowResult(
            workflowRunId=request.workflow_run_id,
            status="FAILED",
            startedAtUtc=requested_at,
            completedAtUtc=max(requested_at, completed_at),
            packages=[],
            taskDeterminations=[],
            assessments=[],
            bundleGroups=[],
            persistence=PersistenceCounts(),
            errors=errors,
        )

    if task_resolver is None:
        if task_model is None:
            raise ValueError("task_model or task_resolver is required.")
        task_resolver = _default_task_resolver(
            task_master=task_master,
            model=task_model,
            created_at_utc=requested_at,
        )

    packages_by_id = {package.package_id: package for package in packages}
    assessment_packages_by_id = {
        package.package_id: package
        for package in (assessment_packages or packages)
    }
    if set(assessment_packages_by_id) != set(packages_by_id):
        raise ValueError(
            "Assessment packages must exactly match source package IDs."
        )
    for package_id, source_package in packages_by_id.items():
        assessment_package = assessment_packages_by_id[package_id]
        if (
            assessment_package.invoice_id != source_package.invoice_id
            or [line.line_id for line in assessment_package.lines]
            != [line.line_id for line in source_package.lines]
        ):
            raise ValueError(
                "Assessment packages may overlay values but cannot change "
                "invoice or line identity."
            )
    successful_packages: list[NormalizedInvoicePackage] = []
    all_tasks: list[TaskDetermination] = []
    all_assessments = []
    package_tasks: dict[str, list[TaskDetermination]] = {}

    for package_ref in request.packages:
        package = packages_by_id[package_ref.package_id]
        assessment_package = assessment_packages_by_id[package.package_id]
        try:
            determinations = _validate_task_determinations(
                package=package,
                request=request,
                determinations=task_resolver(package, request),
            )
            assessments = assess_capitalization_for_invoice(
                assessment_package,
                determinations,
                policy,
                workflow_run_id=request.workflow_run_id,
                rule_set=rule_set,
                allow_synthetic=request.allow_synthetic_inputs,
                synthetic_setting_ids=synthetic_setting_ids,
                created_at_utc=requested_at,
            )
            successful_packages.append(assessment_package)
            package_tasks[assessment_package.package_id] = determinations
            all_tasks.extend(determinations)
            all_assessments.extend(assessments)
        except Exception as exc:
            errors.append(
                _stage_error(
                    stage="PACKAGE_PROCESSING",
                    code=type(exc).__name__.upper(),
                    message=str(exc),
                    package_id=package.package_id,
                )
            )

    bundle_groups = []
    if all_assessments:
        try:
            bundling = evaluate_bundle_candidates(
                successful_packages,
                all_assessments,
                policy,
                workflow_run_id=request.workflow_run_id,
                created_at_utc=requested_at,
            )
            all_assessments = bundling.assessments
            bundle_groups = bundling.groups
        except Exception as exc:
            errors.append(
                _stage_error(
                    stage="BUNDLING",
                    code=type(exc).__name__.upper(),
                    message=str(exc),
                )
            )

    persisted_tasks = 0
    persistence = PersistenceCounts()
    if request.persist_results:
        if connection is None:
            errors.append(
                _stage_error(
                    stage="PERSISTENCE",
                    code="CONNECTION_NOT_CONFIGURED",
                    message="persistResults is true but no SQL connection was provided.",
                )
            )
        elif all_assessments and not any(
            error.stage == "BUNDLING" for error in errors
        ):
            try:
                for package in successful_packages:
                    persisted_tasks += _persist_task_determinations(
                        connection=connection,
                        package=package,
                        request=request,
                        determinations=package_tasks[package.package_id],
                    )
                persistence_outcome = persist_demo_v2_results(
                    connection=connection,
                    assessments=all_assessments,
                    groups=bundle_groups,
                    workflow_run_id=request.workflow_run_id,
                    created_at_utc=completed_at,
                )
                persistence = PersistenceCounts(
                    taskSuggestions=persisted_tasks,
                    assessments=persistence_outcome.assessments,
                    bundleGroups=persistence_outcome.bundle_groups,
                    bundleMembers=persistence_outcome.bundle_members,
                )
            except Exception as exc:
                errors.append(
                    _stage_error(
                        stage="PERSISTENCE",
                        code=type(exc).__name__.upper(),
                        message=str(exc),
                    )
                )

    assessments_by_package: dict[str, list[Any]] = {}
    for assessment in all_assessments:
        assessments_by_package.setdefault(assessment.package_id, []).append(
            assessment
        )

    package_summaries: list[WorkflowPackageSummary] = []
    for package in successful_packages:
        tasks = package_tasks[package.package_id]
        assessments = assessments_by_package.get(package.package_id, [])
        task_counts = Counter(item.resolution_status for item in tasks)
        assessment_counts = Counter(item.recommendation for item in assessments)
        package_summaries.append(
            WorkflowPackageSummary(
                packageId=package.package_id,
                invoiceId=package.invoice_id,
                lineCount=len(package.lines),
                resolvedTaskCount=task_counts["RESOLVED"],
                reviewTaskCount=task_counts["REVIEW"],
                capexCount=assessment_counts["CAPEX"],
                opexCount=assessment_counts["OPEX"],
                reviewAssessmentCount=assessment_counts["REVIEW"],
            )
        )

    if request.fail_on_review and any(
        item.resolution_status == "REVIEW" for item in all_tasks
    ):
        errors.append(
            _stage_error(
                stage="COMPLETION",
                code="TASK_REVIEW_REQUIRED",
                message="At least one task determination requires human review.",
            )
        )
    if request.fail_on_review and any(
        item.recommendation == "REVIEW" for item in all_assessments
    ):
        errors.append(
            _stage_error(
                stage="COMPLETION",
                code="ASSESSMENT_REVIEW_REQUIRED",
                message="At least one capitalization assessment requires human review.",
            )
        )

    if not package_summaries:
        status = "FAILED"
    elif errors:
        status = "PARTIAL"
    else:
        status = "SUCCEEDED"

    return DemoWorkflowResult(
        workflowRunId=request.workflow_run_id,
        status=status,
        startedAtUtc=requested_at,
        completedAtUtc=max(requested_at, completed_at),
        packages=package_summaries,
        taskDeterminations=all_tasks,
        assessments=all_assessments,
        bundleGroups=bundle_groups,
        persistence=persistence,
        errors=errors,
    )


def _credential() -> Any:
    from azure.identity import DefaultAzureCredential

    client_id = os.getenv("AZURE_CLIENT_ID")
    return DefaultAzureCredential(
        managed_identity_client_id=client_id or None,
    )


def _is_windows_path(value: str) -> bool:
    return (
        len(value) >= 3
        and value[1] == ":"
        and value[2] in {"/", "\\"}
    )


def _read_bytes(uri: str, *, credential: Any = None) -> bytes:
    if _is_windows_path(uri):
        return Path(uri).read_bytes()
    parsed = urlparse(uri)
    if parsed.scheme in {"http", "https"}:
        from azure.storage.blob import BlobClient

        blob = BlobClient.from_blob_url(
            uri,
            credential=credential or _credential(),
        )
        return blob.download_blob().readall()
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path.lstrip("/")))
    elif parsed.scheme:
        raise ValueError(f"Unsupported resource URI scheme: {parsed.scheme}")
    else:
        path = Path(uri)
    return path.read_bytes()


def _load_json(uri: str, *, credential: Any = None) -> Any:
    return json.loads(_read_bytes(uri, credential=credential).decode("utf-8"))


def _package_from_payload(
    payload: Any,
    *,
    package_id: str,
) -> NormalizedInvoicePackage:
    if (
        isinstance(payload, dict)
        and payload.get("schemaVersion") == "2.0-jc-ledger"
    ):
        workbook = JCWorkbookNormalizationResult.model_validate(payload)
        matches = [
            package
            for package in workbook.packages
            if package.package_id == package_id
        ]
        if len(matches) != 1:
            raise ValueError(
                "JC workbook artifact must contain exactly one requested "
                f"package: {package_id}."
            )
        return matches[0]
    return NormalizedInvoicePackage.model_validate(payload)


def _write_result(
    uri: str,
    result: DemoWorkflowResult,
    *,
    credential: Any = None,
) -> None:
    payload = (
        result.model_dump_json(by_alias=True, indent=2) + "\n"
    ).encode("utf-8")
    if _is_windows_path(uri):
        path = Path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return
    parsed = urlparse(uri)
    if parsed.scheme in {"http", "https"}:
        from azure.storage.blob import BlobClient

        blob = BlobClient.from_blob_url(
            uri,
            credential=credential or _credential(),
        )
        blob.upload_blob(payload, overwrite=False)
        return
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path.lstrip("/")))
    elif parsed.scheme:
        raise ValueError(f"Unsupported output URI scheme: {parsed.scheme}")
    else:
        path = Path(uri)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the direct CapEx Demo V2 finite batch.",
    )
    parser.add_argument(
        "--request-uri",
        default=os.getenv("CAPEX_V2_REQUEST_URI"),
        required=os.getenv("CAPEX_V2_REQUEST_URI") is None,
    )
    parser.add_argument(
        "--task-master-uri",
        default=os.getenv("CAPEX_V2_TASK_MASTER_URI"),
        required=os.getenv("CAPEX_V2_TASK_MASTER_URI") is None,
    )
    parser.add_argument(
        "--policy-uri",
        default=os.getenv("CAPEX_V2_POLICY_URI"),
        required=os.getenv("CAPEX_V2_POLICY_URI") is None,
    )
    parser.add_argument(
        "--rule-set-uri",
        default=os.getenv("CAPEX_V2_RULE_SET_URI"),
    )
    parser.add_argument(
        "--synthetic-register-uri",
        default=os.getenv("CAPEX_V2_SYNTHETIC_REGISTER_URI"),
    )
    parser.add_argument(
        "--output-uri",
        default=os.getenv("CAPEX_V2_OUTPUT_URI"),
        required=os.getenv("CAPEX_V2_OUTPUT_URI") is None,
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint for the direct Container Apps batch."""
    args = _parser().parse_args(argv)
    credential = _credential()
    connection = None
    try:
        request = DemoWorkflowRequest.model_validate(
            _load_json(args.request_uri, credential=credential)
        )
        packages = [
            _package_from_payload(
                _load_json(
                    package.package_uri,
                    credential=credential,
                ),
                package_id=package.package_id,
            )
            for package in request.packages
        ]
        task_master = TaskMasterSnapshot.model_validate(
            _load_json(args.task_master_uri, credential=credential)
        )
        policy = DemoPolicySnapshot.model_validate(
            _load_json(args.policy_uri, credential=credential)
        )
        rule_set = (
            CapitalizationRuleSet.model_validate(
                _load_json(args.rule_set_uri, credential=credential)
            )
            if args.rule_set_uri
            else None
        )
        if request.allow_synthetic_inputs and rule_set is None:
            raise ValueError(
                "Synthetic input mode requires CAPEX_V2_RULE_SET_URI."
            )
        assessment_packages = packages
        register = None
        setting_ids: Sequence[str] = ()
        if request.allow_synthetic_inputs:
            if not args.synthetic_register_uri:
                raise ValueError(
                    "Synthetic input mode requires "
                    "CAPEX_V2_SYNTHETIC_REGISTER_URI."
                )
            register = validate_synthetic_register(
                _load_json(
                    args.synthetic_register_uri,
                    credential=credential,
                )
            )
            assessment_packages = [
                apply_synthetic_line_values(package, register)[0]
                for package in packages
            ]
            setting_ids = synthetic_setting_ids(register)
        model = build_azure_task_suggestion_model(
            deployment=request.model_deployment,
            endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            managed_identity_client_id=os.getenv("AZURE_CLIENT_ID"),
        )
        if request.persist_results:
            connection = connect_azure_sql(
                server_fqdn=os.environ["CAPEX_SQL_SERVER"],
                database_name=os.environ["CAPEX_SQL_DATABASE"],
                managed_identity_client_id=os.getenv("AZURE_CLIENT_ID"),
            )
        result = run_demo_v2_workflow(
            request=request,
            packages=packages,
            assessment_packages=assessment_packages,
            task_master=task_master,
            policy=policy,
            task_model=model,
            rule_set=rule_set,
            synthetic_setting_ids=setting_ids,
            connection=connection,
        )
        _write_result(args.output_uri, result, credential=credential)
        if result.status == "SUCCEEDED":
            return EXIT_SUCCEEDED
        if result.status == "PARTIAL":
            return EXIT_PARTIAL
        return EXIT_FAILED
    except (KeyError, OSError, ValueError, ValidationError) as exc:
        print(f"CapEx Demo V2 configuration failure: {exc}", file=sys.stderr)
        return EXIT_FAILED
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
