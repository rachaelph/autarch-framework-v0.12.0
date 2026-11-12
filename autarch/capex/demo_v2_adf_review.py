"""Build the analyst handoff for original ADF source packages."""
from __future__ import annotations

from typing import Literal, Mapping, Sequence

from pydantic import Field, model_validator

from .demo_v2_contracts import DemoWorkflowResult
from .demo_v2_intake import IntakeManifest
from .demo_v2_normalization import NormalizationModel, NormalizedInvoicePackage
from .demo_v2_reference import DemoPolicySnapshot, TaskMasterSnapshot


class AdfReviewSource(NormalizationModel):
    document_id: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    media_type: Literal[
        "application/pdf", "application/zip",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ]
    archive_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    archive_member: str | None = None


class AdfReviewDocument(NormalizationModel):
    document: AdfReviewSource
    extraction: dict
    review_reasons: list[str]


class AdfReviewPartition(NormalizationModel):
    site_id: None
    project_id: None
    partition_status: Literal["NO_SITE_PROJECT_CONTEXT_SUPPLIED"]
    result: DemoWorkflowResult
    evidence: dict


class AdfReviewEnvelope(NormalizationModel):
    schema_version: Literal["1.0-capex-adf-review"]
    workflow_run_id: str
    status: Literal["REVIEW_REQUIRED"]
    synthetic: Literal[False]
    business_approved: Literal[False]
    popa_writes: Literal[0]
    sql_writes: int = Field(ge=0)
    review_status: Literal["AWAITING_ANALYST"]
    documents: list[AdfReviewDocument] = Field(min_length=1)
    partitions: list[AdfReviewPartition] = Field(min_length=1, max_length=1)
    exception_document_count: int = Field(ge=0)
    llm_calls_attempted: int = Field(ge=0)
    policy_snapshots: dict[str, DemoPolicySnapshot]
    active_task_codes: list[str]
    active_asset_class_codes: list[str] = Field(default_factory=list)
    source_manifest: IntakeManifest

    @model_validator(mode="after")
    def validate_handoff(self) -> "AdfReviewEnvelope":
        batch = self.partitions[0].result
        sources = {
            package_id: source for source in self.source_manifest.sources for package_id in source.package_ids
        }
        summaries = {item.invoice_id: item for item in batch.packages}
        documents = {item.document.document_id: item for item in self.documents}
        task_keys = {(item.package_id, item.line_id, item.invoice_id) for item in batch.task_determinations}
        assessment_keys = {(item.package_id, item.line_id, item.invoice_id) for item in batch.assessments}
        if batch.status != "SUCCEEDED" or batch.workflow_run_id != self.workflow_run_id \
                or self.source_manifest.workflow_run_id != self.workflow_run_id \
                or len(summaries) != len(batch.packages) or len(documents) != len(self.documents) \
                or documents.keys() != summaries.keys() \
                or set(sources) != {item.package_id for item in batch.packages} \
                or task_keys != assessment_keys \
                or len(task_keys) != sum(item.line_count for item in batch.packages) \
                or self.sql_writes != len(task_keys) + len(assessment_keys):
            raise ValueError("ADF review import result, source identities or counts do not match.")
        if self.exception_document_count != sum(bool(item.review_reasons) for item in self.documents):
            raise ValueError("ADF review exception count does not match its documents.")
        for invoice_id, summary in summaries.items():
            lines = {key for key in task_keys if key[0] == summary.package_id and key[2] == invoice_id}
            doc = documents[invoice_id].document
            source = sources[summary.package_id]
            if len(lines) != summary.line_count or doc.uri != source.uri:
                raise ValueError("ADF review source identity or line coverage does not match.")
            if doc.archive_member is not None:
                if source.format != "ABBYY_ZIP" or doc.media_type != "application/pdf" \
                        or doc.archive_sha256 != source.sha256 or not doc.archive_member.lower().endswith(".pdf"):
                    raise ValueError("ADF review PDF must identify its pinned original archive.")
            elif doc.archive_sha256 is not None or doc.sha256 != source.sha256 \
                    or doc.media_type != (
                        "application/zip" if source.format == "ABBYY_ZIP"
                        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    ):
                raise ValueError("ADF review source hash or media type does not match its manifest.")
        return self


def build_adf_review(
    *, intake: IntakeManifest, packages: Sequence[NormalizedInvoicePackage],
    result: DemoWorkflowResult, evidence: dict, master: TaskMasterSnapshot,
    policy: DemoPolicySnapshot, exceptions: Sequence[Mapping[str, object]],
    extraction_documents: Mapping[str, dict] | None = None,
    policies: Mapping[str, DemoPolicySnapshot] | None = None,
    asset_class_codes: Sequence[str] = (),
) -> dict:
    sources = {
        package_id: source for source in intake.sources for package_id in source.package_ids
    }
    exception_reasons = {}
    for item in exceptions:
        reasons = item["reasons"]
        if not isinstance(reasons, list) or not all(isinstance(reason, str) for reason in reasons):
            raise ValueError("Reference exception reasons must be a list of strings.")
        exception_reasons[str(item["packageId"])] = reasons
    documents = []
    for package in packages:
        source = sources[package.package_id]
        extraction = (extraction_documents or {}).get(package.package_id)
        document = extraction["document"] if extraction is not None else {
            "documentId": package.invoice_id, "uri": source.uri,
            "sha256": source.sha256, "mediaType": "application/zip" if source.format == "ABBYY_ZIP"
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        documents.append({
            "document": document,
            "extraction": extraction["extraction"] if extraction is not None else {
                "mode": "ABBYY_PREEXTRACTED_CSV" if source.format == "ABBYY_ZIP" else "JC_WORKBOOK_NORMALIZATION",
                "normalizedInvoice": package.model_dump(mode="json", by_alias=True),
                "freshPdfExtraction": False,
            },
            "reviewReasons": list(dict.fromkeys([
                *package.data_quality.review_reasons,
                *list(exception_reasons.get(package.package_id, [])),
            ])),
        })
    payload = {
        "schemaVersion": "1.0-capex-adf-review",
        "workflowRunId": result.workflow_run_id, "status": "REVIEW_REQUIRED",
        "synthetic": False, "businessApproved": False, "popaWrites": 0,
        "sqlWrites": len(result.task_determinations) + len(result.assessments),
        "reviewStatus": "AWAITING_ANALYST",
        "documents": documents,
        "partitions": [{
            "siteId": None, "projectId": None,
            "partitionStatus": "NO_SITE_PROJECT_CONTEXT_SUPPLIED",
            "result": result.model_dump(mode="json", by_alias=True), "evidence": evidence,
        }],
        "exceptionDocumentCount": sum(bool(item["reviewReasons"]) for item in documents),
        "llmCallsAttempted": evidence.get("llmCallsAttempted", 0) + evidence.get("extractionCallsAttempted", 0),
        "policySnapshots": {
            currency: value.model_dump(mode="json", by_alias=True)
            for currency, value in (policies or {policy.capitalization.currency: policy}).items()
        },
        "activeTaskCodes": [task.task_code for task in master.tasks],
        "activeAssetClassCodes": list(asset_class_codes),
        "sourceManifest": intake.model_dump(mode="json", by_alias=True),
    }
    AdfReviewEnvelope.model_validate(payload)
    return payload
