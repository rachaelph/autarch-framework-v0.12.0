"""Strict contracts for the direct-batch CapEx Demo V2 workflow."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Literal, Optional

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from typing_extensions import Annotated


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CurrencyCode = Annotated[
    str,
    BeforeValidator(
        lambda value: value.strip().upper()
        if isinstance(value, str)
        else value
    ),
    StringConstraints(
        pattern=r"^[A-Z]{3}$",
    ),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class DemoV2Model(BaseModel):
    """Base model for strict camelCase Demo V2 JSON contracts."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )


class TaskResolutionStatus(str, Enum):
    """Outcome of governed task determination."""

    RESOLVED = "RESOLVED"
    REVIEW = "REVIEW"


class TaskMatchMethod(str, Enum):
    """Method used to determine a governed task code."""

    EXACT_PART = "EXACT_PART"
    EXACT_TASK = "EXACT_TASK"
    LANGCHAIN = "LANGCHAIN"
    REVIEW = "REVIEW"


class NatureResult(str, Enum):
    """Deterministic nature-of-spend classification."""

    CAPITALIZABLE = "CAPITALIZABLE"
    OPEX_BY_NATURE = "OPEX_BY_NATURE"
    REVIEW = "REVIEW"


class CapitalizationRecommendation(str, Enum):
    """Line-level CapEx/OpEx recommendation."""

    CAPEX = "CAPEX"
    OPEX = "OPEX"
    REVIEW = "REVIEW"


class BundlingDisposition(str, Enum):
    """Line or group status in deterministic bundling evaluation."""

    NOT_IN_SCOPE = "NOT_IN_SCOPE"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    CANDIDATE = "CANDIDATE"
    GROUPED = "GROUPED"
    REVIEW = "REVIEW"


class WorkflowExecutionStatus(str, Enum):
    """Terminal status for the direct batch execution."""

    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must include a UTC offset")
    return value


class InvoicePackageReference(DemoV2Model):
    """Reference to one normalized invoice-package input."""

    package_id: NonEmptyString
    run_id: NonEmptyString
    package_uri: NonEmptyString
    expected_invoice_id: Optional[NonEmptyString] = None
    expected_source_sha256: Optional[Sha256] = None


class DemoWorkflowRequest(DemoV2Model):
    """Direct Container Apps Job input for one Demo V2 execution."""

    schema_version: Literal["2.0"] = "2.0"
    workflow_run_id: NonEmptyString
    requested_at_utc: datetime
    packages: List[InvoicePackageReference] = Field(min_length=1)
    task_region: NonEmptyString = "US"
    model_deployment: NonEmptyString
    task_prompt_version: NonEmptyString
    capitalization_policy_version: Optional[NonEmptyString] = None
    bundling_policy_version: Optional[NonEmptyString] = None
    persist_results: bool = True
    fail_on_review: bool = False

    _validate_requested_at_utc = field_validator("requested_at_utc")(
        _require_utc
    )

    @model_validator(mode="after")
    def validate_package_references(self) -> "DemoWorkflowRequest":
        package_ids = [item.package_id for item in self.packages]
        if len(package_ids) != len(set(package_ids)):
            raise ValueError("packages must not contain duplicate packageId values")
        return self


class TaskDetermination(DemoV2Model):
    """Governed task-code result for one invoice line."""

    schema_version: Literal["2.0"] = "2.0"
    workflow_run_id: NonEmptyString
    package_id: NonEmptyString
    invoice_id: NonEmptyString
    line_id: NonEmptyString
    resolution_status: TaskResolutionStatus
    suggested_task_code: Optional[NonEmptyString] = None
    suggested_asset_class: Optional[NonEmptyString] = None
    item_identified: Optional[NonEmptyString] = None
    match_method: TaskMatchMethod
    confidence: float = Field(ge=0.0, le=1.0)
    unresolved_reason: Optional[NonEmptyString] = None
    rationale: NonEmptyString
    evidence_refs: List[NonEmptyString] = Field(default_factory=list)
    task_master_version: Optional[NonEmptyString] = None
    task_master_sha256: Sha256
    model_deployment: Optional[NonEmptyString] = None
    prompt_version: NonEmptyString
    created_at_utc: datetime

    _validate_created_at_utc = field_validator("created_at_utc")(_require_utc)

    @model_validator(mode="after")
    def validate_resolution(self) -> "TaskDetermination":
        if self.resolution_status == TaskResolutionStatus.RESOLVED:
            if self.suggested_task_code is None:
                raise ValueError(
                    "resolved task determination requires suggestedTaskCode"
                )
            if self.match_method == TaskMatchMethod.REVIEW:
                raise ValueError(
                    "resolved task determination cannot use REVIEW matchMethod"
                )
            if self.unresolved_reason is not None:
                raise ValueError(
                    "resolved task determination cannot include unresolvedReason"
                )
        else:
            if self.unresolved_reason is None:
                raise ValueError(
                    "review task determination requires unresolvedReason"
                )
            if self.match_method != TaskMatchMethod.REVIEW:
                raise ValueError(
                    "review task determination must use REVIEW matchMethod"
                )
        return self


class CapitalizationAssessment(DemoV2Model):
    """Deterministic capitalization result for one invoice line."""

    schema_version: Literal["2.0"] = "2.0"
    workflow_run_id: NonEmptyString
    package_id: NonEmptyString
    invoice_id: NonEmptyString
    line_id: NonEmptyString
    suggested_task_code: Optional[NonEmptyString] = None
    nature_result: NatureResult
    repair_maintenance_indicator: bool
    never_capitalize_hit: bool
    unit_cost_meets_threshold: Optional[bool] = None
    threshold_amount: Optional[float] = Field(default=None, ge=0.0)
    threshold_currency: Optional[CurrencyCode] = None
    recommendation: CapitalizationRecommendation
    outcome_category: NonEmptyString
    bundling_disposition: BundlingDisposition
    bundle_group_id: Optional[NonEmptyString] = None
    missing_inputs: List[NonEmptyString] = Field(default_factory=list)
    review_reasons: List[NonEmptyString] = Field(default_factory=list)
    rationale: NonEmptyString
    policy_citations: List[NonEmptyString] = Field(default_factory=list)
    used_llm: bool = False
    model_version: Optional[NonEmptyString] = None
    policy_version: Optional[NonEmptyString] = None
    created_at_utc: datetime

    _validate_created_at_utc = field_validator("created_at_utc")(_require_utc)

    @model_validator(mode="after")
    def validate_assessment(self) -> "CapitalizationAssessment":
        if (self.threshold_amount is None) != (
            self.threshold_currency is None
        ):
            raise ValueError(
                "thresholdAmount and thresholdCurrency must be provided together"
            )
        if self.missing_inputs and (
            self.recommendation != CapitalizationRecommendation.REVIEW
        ):
            raise ValueError(
                "an assessment with missingInputs must recommend REVIEW"
            )
        if self.recommendation == CapitalizationRecommendation.REVIEW:
            if not self.review_reasons:
                raise ValueError(
                    "a REVIEW assessment requires at least one reviewReason"
                )
        elif self.review_reasons:
            raise ValueError(
                "a non-REVIEW assessment cannot include reviewReasons"
            )
        if self.bundling_disposition == BundlingDisposition.REVIEW and (
            self.recommendation != CapitalizationRecommendation.REVIEW
        ):
            raise ValueError(
                "REVIEW bundling disposition requires a REVIEW recommendation"
            )
        if self.bundling_disposition == BundlingDisposition.GROUPED:
            if self.bundle_group_id is None:
                raise ValueError(
                    "GROUPED bundling disposition requires bundleGroupId"
                )
        elif self.bundle_group_id is not None:
            raise ValueError(
                "bundleGroupId is only valid for GROUPED bundling disposition"
            )
        if self.used_llm and self.model_version is None:
            raise ValueError("usedLlm=true requires modelVersion")
        return self


class BundleCandidateMember(DemoV2Model):
    """Invoice line participating in one bundle candidate."""

    package_id: NonEmptyString
    invoice_id: NonEmptyString
    line_id: NonEmptyString
    quantity: float = Field(gt=0.0)
    unit_cost: float = Field(ge=0.0)
    line_amount: float = Field(ge=0.0)


class BundleCandidateGroup(DemoV2Model):
    """Deterministically grouped invoice lines evaluated for bundling."""

    schema_version: Literal["2.0"] = "2.0"
    workflow_run_id: NonEmptyString
    bundle_group_id: NonEmptyString
    suggested_task_code: NonEmptyString
    site_id: Optional[NonEmptyString] = None
    project_id: Optional[NonEmptyString] = None
    currency: CurrencyCode
    total_amount: float = Field(ge=0.0)
    threshold_amount: Optional[float] = Field(default=None, ge=0.0)
    disposition: BundlingDisposition
    members: List[BundleCandidateMember] = Field(min_length=2)
    missing_inputs: List[NonEmptyString] = Field(default_factory=list)
    rationale: NonEmptyString
    policy_citations: List[NonEmptyString] = Field(default_factory=list)
    policy_version: Optional[NonEmptyString] = None
    created_at_utc: datetime

    _validate_created_at_utc = field_validator("created_at_utc")(_require_utc)

    @model_validator(mode="after")
    def validate_group(self) -> "BundleCandidateGroup":
        member_keys = [
            (item.package_id, item.line_id) for item in self.members
        ]
        if len(member_keys) != len(set(member_keys)):
            raise ValueError(
                "members must not contain duplicate packageId/lineId pairs"
            )
        if self.disposition not in {
            BundlingDisposition.CANDIDATE,
            BundlingDisposition.GROUPED,
            BundlingDisposition.REVIEW,
        }:
            raise ValueError(
                "bundle group disposition must be CANDIDATE, GROUPED, or REVIEW"
            )
        if self.disposition == BundlingDisposition.REVIEW:
            if not self.missing_inputs:
                raise ValueError(
                    "a REVIEW bundle group requires at least one missingInput"
                )
        else:
            if self.missing_inputs:
                raise ValueError(
                    "a non-REVIEW bundle group cannot include missingInputs"
                )
            if self.site_id is None or self.project_id is None:
                raise ValueError(
                    "a non-REVIEW bundle group requires siteId and projectId"
                )
        return self


class WorkflowPackageSummary(DemoV2Model):
    """Counts and status for one processed invoice package."""

    package_id: NonEmptyString
    invoice_id: NonEmptyString
    line_count: int = Field(ge=1)
    resolved_task_count: int = Field(ge=0)
    review_task_count: int = Field(ge=0)
    capex_count: int = Field(ge=0)
    opex_count: int = Field(ge=0)
    review_assessment_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "WorkflowPackageSummary":
        if self.resolved_task_count + self.review_task_count != self.line_count:
            raise ValueError(
                "task counts must equal lineCount"
            )
        if (
            self.capex_count
            + self.opex_count
            + self.review_assessment_count
            != self.line_count
        ):
            raise ValueError(
                "assessment counts must equal lineCount"
            )
        return self


class PersistenceCounts(DemoV2Model):
    """Rows written by a Demo V2 execution."""

    task_suggestions: int = Field(default=0, ge=0)
    assessments: int = Field(default=0, ge=0)
    bundle_groups: int = Field(default=0, ge=0)
    bundle_members: int = Field(default=0, ge=0)


class WorkflowError(DemoV2Model):
    """Explicit processing error returned by the batch workflow."""

    stage: NonEmptyString
    code: NonEmptyString
    message: NonEmptyString
    package_id: Optional[NonEmptyString] = None
    line_id: Optional[NonEmptyString] = None


class DemoWorkflowResult(DemoV2Model):
    """Complete output contract for one direct Demo V2 execution."""

    schema_version: Literal["2.0"] = "2.0"
    workflow_run_id: NonEmptyString
    status: WorkflowExecutionStatus
    started_at_utc: datetime
    completed_at_utc: datetime
    packages: List[WorkflowPackageSummary]
    task_determinations: List[TaskDetermination]
    assessments: List[CapitalizationAssessment]
    bundle_groups: List[BundleCandidateGroup] = Field(default_factory=list)
    persistence: PersistenceCounts = Field(default_factory=PersistenceCounts)
    errors: List[WorkflowError] = Field(default_factory=list)

    _validate_started_at_utc = field_validator("started_at_utc")(_require_utc)
    _validate_completed_at_utc = field_validator("completed_at_utc")(
        _require_utc
    )

    @model_validator(mode="after")
    def validate_result(self) -> "DemoWorkflowResult":
        if self.completed_at_utc < self.started_at_utc:
            raise ValueError("completedAtUtc cannot precede startedAtUtc")
        if self.status == WorkflowExecutionStatus.SUCCEEDED and self.errors:
            raise ValueError("a SUCCEEDED workflow cannot include errors")
        if self.status == WorkflowExecutionStatus.FAILED and not self.errors:
            raise ValueError("a FAILED workflow must include at least one error")

        task_keys = set()
        for item in self.task_determinations:
            if item.workflow_run_id != self.workflow_run_id:
                raise ValueError(
                    "task determination workflowRunId does not match result"
                )
            key = (item.package_id, item.line_id)
            if key in task_keys:
                raise ValueError(
                    "taskDeterminations contain a duplicate packageId/lineId"
                )
            task_keys.add(key)

        assessment_keys = set()
        for item in self.assessments:
            if item.workflow_run_id != self.workflow_run_id:
                raise ValueError(
                    "assessment workflowRunId does not match result"
                )
            key = (item.package_id, item.line_id)
            if key in assessment_keys:
                raise ValueError(
                    "assessments contain a duplicate packageId/lineId"
                )
            if key not in task_keys:
                raise ValueError(
                    "every assessment must reference a task determination"
                )
            assessment_keys.add(key)

        group_ids = set()
        for group in self.bundle_groups:
            if group.workflow_run_id != self.workflow_run_id:
                raise ValueError(
                    "bundle group workflowRunId does not match result"
                )
            if group.bundle_group_id in group_ids:
                raise ValueError(
                    "bundleGroups contain a duplicate bundleGroupId"
                )
            group_ids.add(group.bundle_group_id)
            for member in group.members:
                if (member.package_id, member.line_id) not in assessment_keys:
                    raise ValueError(
                        "every bundle member must reference an assessment"
                    )

        return self


def derive_bundle_group_id(
    workflow_run_id: str,
    suggested_task_code: str,
    site_id: str,
    project_id: str,
    currency: str,
) -> str:
    """Return a stable bundle identifier for the approved grouping key."""

    values = (
        workflow_run_id,
        suggested_task_code,
        site_id,
        project_id,
        currency.upper(),
    )
    if any(not value.strip() for value in values):
        raise ValueError("bundle group identity values must be non-empty")
    digest = hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()
    return f"bundle-{digest[:24]}"


__all__ = [
    "BundleCandidateGroup",
    "BundleCandidateMember",
    "BundlingDisposition",
    "CapitalizationAssessment",
    "CapitalizationRecommendation",
    "DemoWorkflowRequest",
    "DemoWorkflowResult",
    "InvoicePackageReference",
    "NatureResult",
    "PersistenceCounts",
    "TaskDetermination",
    "TaskMatchMethod",
    "TaskResolutionStatus",
    "WorkflowError",
    "WorkflowExecutionStatus",
    "WorkflowPackageSummary",
    "derive_bundle_group_id",
]
