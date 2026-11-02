"""Governed reference and policy contracts for CapEx Demo V2."""
from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from pathlib import Path
from typing import Dict, List, Literal, Mapping, Optional, Sequence, Set

from pydantic import BaseModel, ConfigDict, Field, model_validator


_RETRIEVAL_STOP_WORDS = {
    "back",
    "black",
    "clear",
    "color",
    "degree",
    "depth",
    "front",
    "height",
    "length",
    "line",
    "non",
    "red",
    "width",
}
_ENVIRONMENTAL_RETRIEVAL_MARKERS = {
    "air quality",
    "biological",
    "conservation",
    "cultural",
    "cutlural",
    "emissions",
    "environment",
    "environmental",
    "envrionmental",
    "greenhouse gas",
    "health risk",
    "joshua tree",
    "noise",
    "vibration",
}


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ReferenceModel(BaseModel):
    """Strict camelCase base for generated reference artifacts."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )


class ApprovalStatus(str, Enum):
    """Business-approval state of a policy value."""

    POLICY_SUPPORTED = "POLICY_SUPPORTED"
    WORKFLOW_CONFIRMED = "WORKFLOW_CONFIRMED"
    PROPOSED_DEMO_ASSUMPTION = "PROPOSED_DEMO_ASSUMPTION"
    BUSINESS_CONFIRMATION_REQUIRED = "BUSINESS_CONFIRMATION_REQUIRED"
    DEFERRED = "DEFERRED"


class ReviewBehavior(str, Enum):
    """Behavior when a governed input or approval is unavailable."""

    REVIEW = "REVIEW"


class TaskReferenceEntry(ReferenceModel):
    """One active governed TaskMaster task used for retrieval."""

    task_code: str = Field(min_length=1)
    description: str = Field(min_length=1)
    active: Literal[True] = True
    gl_cip_major_account: Optional[str] = None
    gl_cip_sub_account: Optional[str] = None
    gl_asset_major_account: Optional[str] = None
    gl_asset_sub_account: Optional[str] = None
    gl_accum_major_account: Optional[str] = None
    gl_accum_sub_account: Optional[str] = None
    gl_depr_major_account: Optional[str] = None
    gl_depr_sub_account: Optional[str] = None
    asset_category_major: str = Field(min_length=1)
    asset_category_minor: str = Field(min_length=1)
    property_type: str = Field(min_length=1)
    search_text: str = Field(min_length=1)


class ReferenceSource(ReferenceModel):
    """Immutable source identity for a governed reference artifact."""

    file_name: str = Field(min_length=1)
    sheet_name: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[A-F0-9]{64}$")


class TaskMasterSnapshot(ReferenceModel):
    """Versioned active US TaskMaster snapshot for Demo V2."""

    schema_version: Literal["2.0-task-master"] = "2.0-task-master"
    snapshot_id: str = Field(min_length=1)
    region_code: Literal["US"] = "US"
    source: ReferenceSource
    total_source_rows: int = Field(ge=1)
    active_task_count: int = Field(ge=1)
    tasks: List[TaskReferenceEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_snapshot(self) -> "TaskMasterSnapshot":
        task_codes = [task.task_code for task in self.tasks]
        if len(task_codes) != len(set(task_codes)):
            raise ValueError("tasks must not contain duplicate taskCode values")
        if self.active_task_count != len(self.tasks):
            raise ValueError("activeTaskCount must match the tasks array")
        if self.active_task_count > self.total_source_rows:
            raise ValueError(
                "activeTaskCount cannot exceed totalSourceRows"
            )
        return self


class PolicySetting(ReferenceModel):
    """One policy setting and its evidence/approval status."""

    setting_id: str = Field(min_length=1)
    value: object
    status: ApprovalStatus
    evidence: str = Field(min_length=1)
    review_when_unconfirmed: bool = True


class CapitalizationPolicy(ReferenceModel):
    """Deterministic capitalization settings for the demo."""

    currency: Literal["USD"] = "USD"
    base_threshold: float = Field(ge=0.0)
    threshold_comparison: Literal["GREATER_THAN_OR_EQUAL"]
    threshold_basis: Literal["UNIT_COST"]
    below_threshold_site_minimum: float = Field(ge=0.0)
    below_threshold_project_minimum: float = Field(ge=0.0)
    below_threshold_exception_enabled: Literal[False] = False
    major_replacement_reference: float = Field(ge=0.0)
    non_usd_behavior: ReviewBehavior = ReviewBehavior.REVIEW
    missing_unit_cost_behavior: ReviewBehavior = ReviewBehavior.REVIEW
    unknown_nature_behavior: ReviewBehavior = ReviewBehavior.REVIEW
    operational_nature_rules_approved: bool = False


class BundlingPolicy(ReferenceModel):
    """Deterministic bundling settings for the demo."""

    grouping_key: List[
        Literal["TASK_CODE", "SITE_ID", "PROJECT_ID", "CURRENCY"]
    ]
    allow_cross_invoice: Literal[True] = True
    allow_cross_vendor: Literal[True] = True
    allow_cross_purchase_order: Literal[True] = True
    minimum_candidate_members: int = Field(ge=2)
    mixed_currency_behavior: ReviewBehavior = ReviewBehavior.REVIEW
    missing_grouping_input_behavior: ReviewBehavior = (
        ReviewBehavior.REVIEW
    )
    historical_lookback_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_grouping_key(self) -> "BundlingPolicy":
        expected = {
            "TASK_CODE",
            "SITE_ID",
            "PROJECT_ID",
            "CURRENCY",
        }
        if set(self.grouping_key) != expected:
            raise ValueError(
                "groupingKey must contain task, site, project, and currency"
            )
        if len(self.grouping_key) != len(expected):
            raise ValueError("groupingKey must not contain duplicates")
        return self


class JCReferenceHandling(ReferenceModel):
    """Explicit review behavior for the supplied JC accounting data."""

    source_task_codes: List[str] = Field(min_length=1)
    governed_mapping_available: Literal[False] = False
    included_transaction_types: List[Literal["AI"]]
    excluded_transaction_types: Dict[
        Literal["OV", "JP"],
        str,
    ]
    result_without_mapping: ReviewBehavior = ReviewBehavior.REVIEW
    status: Literal[
        ApprovalStatus.PROPOSED_DEMO_ASSUMPTION
    ] = ApprovalStatus.PROPOSED_DEMO_ASSUMPTION


class DemoPolicySnapshot(ReferenceModel):
    """Versioned policy and workflow configuration for Demo V2."""

    schema_version: Literal["2.0-policy"] = "2.0-policy"
    policy_id: str = Field(min_length=1)
    approval_status: Literal[
        ApprovalStatus.PROPOSED_DEMO_ASSUMPTION
    ] = ApprovalStatus.PROPOSED_DEMO_ASSUMPTION
    region: Literal["NORTH_AMERICA"] = "NORTH_AMERICA"
    capitalization: CapitalizationPolicy
    bundling: BundlingPolicy
    jc_reference_handling: JCReferenceHandling
    settings: List[PolicySetting] = Field(min_length=1)
    unresolved_confirmation_ids: List[str] = Field(min_length=1)
    evidence_documents: List[str] = Field(min_length=1)
    default_unresolved_behavior: ReviewBehavior = ReviewBehavior.REVIEW

    @model_validator(mode="after")
    def validate_policy_settings(self) -> "DemoPolicySnapshot":
        setting_ids = [setting.setting_id for setting in self.settings]
        if len(setting_ids) != len(set(setting_ids)):
            raise ValueError(
                "settings must not contain duplicate settingId values"
            )
        known_ids = set(setting_ids)
        unknown = (
            set(self.unresolved_confirmation_ids) - known_ids
        )
        if unknown:
            raise ValueError(
                "unresolvedConfirmationIds must reference settings: "
                + ", ".join(sorted(unknown))
            )
        return self


class ReferenceManifest(ReferenceModel):
    """Integrity manifest for generated Demo V2 reference artifacts."""

    schema_version: Literal["2.0-reference-manifest"] = (
        "2.0-reference-manifest"
    )
    task_master_artifact: str = Field(min_length=1)
    task_master_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_artifact: str = Field(min_length=1)
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    governed_task_count: int = Field(ge=1)
    policy_id: str = Field(min_length=1)
    unresolved_confirmation_count: int = Field(ge=0)


def _optional_text(value: object) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _search_text(values: Sequence[object]) -> str:
    text = " ".join(
        str(value).strip()
        for value in values
        if str(value or "").strip()
    ).lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def build_task_master_snapshot(
    rows: Sequence[Mapping[str, str]],
    *,
    source_file: str,
    source_sheet: str,
    source_version: str,
    source_sha256: str,
) -> TaskMasterSnapshot:
    """Build a governed active-task snapshot from verified source rows."""
    tasks: List[TaskReferenceEntry] = []
    seen: Set[str] = set()

    for row_number, row in enumerate(rows, start=2):
        task_code = str(row.get("Task Type", "")).strip()
        description = str(row.get("Description", "")).strip()
        active = str(row.get("Active", "")).strip()
        if not task_code or not description:
            raise ValueError(
                f"TaskMaster row {row_number} is missing code or description"
            )
        if active not in {"0", "1"}:
            raise ValueError(
                f"TaskMaster row {row_number} has invalid Active value"
            )
        if task_code in seen:
            raise ValueError(
                f"TaskMaster contains duplicate task code {task_code}"
            )
        seen.add(task_code)
        if active == "0":
            continue

        asset_major = str(
            row.get("Asset Category Major", "")
        ).strip()
        asset_minor = str(
            row.get("Asset Category Minor", "")
        ).strip()
        property_type = str(row.get("Property Type", "")).strip()
        if not asset_major or not asset_minor or not property_type:
            raise ValueError(
                f"TaskMaster row {row_number} is missing retrieval metadata"
            )

        tasks.append(
            TaskReferenceEntry(
                task_code=task_code,
                description=description,
                gl_cip_major_account=_optional_text(
                    row.get("GL CIP Major Account")
                ),
                gl_cip_sub_account=_optional_text(
                    row.get("GL CIP Sub Account")
                ),
                gl_asset_major_account=_optional_text(
                    row.get("GL Asset Major Account")
                ),
                gl_asset_sub_account=_optional_text(
                    row.get("GL Asset Sub Account")
                ),
                gl_accum_major_account=_optional_text(
                    row.get("GL Accum Major Account")
                ),
                gl_accum_sub_account=_optional_text(
                    row.get("GL Accum Sub Account")
                ),
                gl_depr_major_account=_optional_text(
                    row.get("GL Depr Major Account")
                ),
                gl_depr_sub_account=_optional_text(
                    row.get("GL Depr Sub Account")
                ),
                asset_category_major=asset_major,
                asset_category_minor=asset_minor,
                property_type=property_type,
                search_text=_search_text(
                    (
                        task_code,
                        description,
                        asset_major,
                        asset_minor,
                        property_type,
                    )
                ),
            )
        )

    return TaskMasterSnapshot(
        snapshot_id=(
            f"circle-k-us-taskmaster-{source_version}"
        ),
        source=ReferenceSource(
            file_name=source_file,
            sheet_name=source_sheet,
            source_version=source_version,
            sha256=source_sha256.upper(),
        ),
        total_source_rows=len(rows),
        active_task_count=len(tasks),
        tasks=tasks,
    )


def build_demo_policy_snapshot() -> DemoPolicySnapshot:
    """Return the evidence-based, explicitly unapproved demo policy."""
    settings = [
        PolicySetting(
            setting_id="base-threshold",
            value={"amount": 1000, "currency": "USD"},
            status=ApprovalStatus.POLICY_SUPPORTED,
            evidence="Circle K Reporting Manual, April 2026, pages 46-51.",
            review_when_unconfirmed=False,
        ),
        PolicySetting(
            setting_id="threshold-equality",
            value="GREATER_THAN_OR_EQUAL",
            status=ApprovalStatus.BUSINESS_CONFIRMATION_REQUIRED,
            evidence=(
                "Manual says less than 1,000 is expense; workflow diagram "
                "requests explicit equality confirmation."
            ),
        ),
        PolicySetting(
            setting_id="threshold-basis",
            value="UNIT_COST",
            status=ApprovalStatus.BUSINESS_CONFIRMATION_REQUIRED,
            evidence=(
                "Latest workflow diagram uses unit price; supplied policy "
                "uses the broader term expenditure."
            ),
        ),
        PolicySetting(
            setting_id="below-threshold-exception",
            value={
                "siteMinimum": 300,
                "projectMinimum": 30000,
                "enabled": False,
            },
            status=ApprovalStatus.BUSINESS_CONFIRMATION_REQUIRED,
            evidence="Circle K Reporting Manual, April 2026.",
        ),
        PolicySetting(
            setting_id="nature-rule-library",
            value="NOT_SUPPLIED",
            status=ApprovalStatus.BUSINESS_CONFIRMATION_REQUIRED,
            evidence=(
                "Policy principles are available, but no approved "
                "operational keyword or classification library was supplied."
            ),
        ),
        PolicySetting(
            setting_id="bundling-key",
            value=[
                "TASK_CODE",
                "SITE_ID",
                "PROJECT_ID",
                "CURRENCY",
            ],
            status=ApprovalStatus.WORKFLOW_CONFIRMED,
            evidence="Invoice Bundling Sequence v4.1.",
            review_when_unconfirmed=False,
        ),
        PolicySetting(
            setting_id="historical-lookback",
            value="DEMO_RUN_ONLY",
            status=ApprovalStatus.BUSINESS_CONFIRMATION_REQUIRED,
            evidence="Historical source and lookback period were not supplied.",
        ),
        PolicySetting(
            setting_id="budget-exchange-rates",
            value="NOT_SUPPLIED",
            status=ApprovalStatus.BUSINESS_CONFIRMATION_REQUIRED,
            evidence="No governed non-USD budget-rate source was supplied.",
        ),
        PolicySetting(
            setting_id="nuvolo-prefixes",
            value=["FWKD", "FFX"],
            status=ApprovalStatus.BUSINESS_CONFIRMATION_REQUIRED,
            evidence="Prefixes were proposed in discovery but not confirmed.",
        ),
        PolicySetting(
            setting_id="expected-demo-outcomes",
            value="NOT_SUPPLIED",
            status=ApprovalStatus.BUSINESS_CONFIRMATION_REQUIRED,
            evidence="Finance acceptance outcomes have not been supplied.",
        ),
    ]
    unresolved = [
        setting.setting_id
        for setting in settings
        if setting.status
        == ApprovalStatus.BUSINESS_CONFIRMATION_REQUIRED
    ]

    return DemoPolicySnapshot(
        policy_id="circle-k-na-demo-unapproved-2026-10-30",
        capitalization=CapitalizationPolicy(
            base_threshold=1000,
            threshold_comparison="GREATER_THAN_OR_EQUAL",
            threshold_basis="UNIT_COST",
            below_threshold_site_minimum=300,
            below_threshold_project_minimum=30000,
            major_replacement_reference=10000,
        ),
        bundling=BundlingPolicy(
            grouping_key=[
                "TASK_CODE",
                "SITE_ID",
                "PROJECT_ID",
                "CURRENCY",
            ],
            minimum_candidate_members=2,
        ),
        jc_reference_handling=JCReferenceHandling(
            source_task_codes=["0303", "0601", "0604", "1201", "3307"],
            included_transaction_types=["AI"],
            excluded_transaction_types={
                "OV": (
                    "Not approved for invoice aggregation; retain as "
                    "excluded review evidence."
                ),
                "JP": (
                    "Accrual/reversal rows are excluded to prevent "
                    "double counting."
                ),
            },
        ),
        settings=settings,
        unresolved_confirmation_ids=unresolved,
        evidence_documents=[
            "Reporting Manual - April 2026 - Final",
            "IAS 16 Property, Plant and Equipment",
            "Circle K - CapEx CIP - 8 - Invoice Bundling Sequence - v4.1",
            "Circle K - CapEx CIP - 9a - Class Diagram - Tables - v2.1",
        ],
    )


def rank_task_candidates(
    query: str,
    snapshot: TaskMasterSnapshot,
    *,
    limit: int = 10,
) -> List[TaskReferenceEntry]:
    """Return deterministic token-overlap candidates from governed tasks."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    normalized_query = _search_text((query,))
    query_tokens = {
        token
        for token in normalized_query.split()
        if (
            len(token) > 1
            and not token.isdigit()
            and token not in _RETRIEVAL_STOP_WORDS
        )
    }
    environmental_context = any(
        marker in normalized_query
        for marker in _ENVIRONMENTAL_RETRIEVAL_MARKERS
    )
    if environmental_context:
        query_tokens.add("environmental")
    if not query_tokens:
        return []

    ranked = []
    for task in snapshot.tasks:
        search_tokens = set(task.search_text.split())
        overlap = query_tokens & search_tokens
        if not overlap:
            continue
        semantic_bonus = (
            2
            if environmental_context and "environmental" in search_tokens
            else 0
        )
        score = (
            len(overlap) + semantic_bonus,
            len(overlap) / len(query_tokens),
            -len(search_tokens),
        )
        ranked.append((score, task.task_code, task))

    ranked.sort(
        key=lambda item: (
            -item[0][0],
            -item[0][1],
            -item[0][2],
            item[1],
        )
    )
    return [item[2] for item in ranked[:limit]]


def load_task_master_snapshot(path: Path) -> TaskMasterSnapshot:
    """Load and validate a generated TaskMaster snapshot."""
    return TaskMasterSnapshot.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def load_demo_policy_snapshot(path: Path) -> DemoPolicySnapshot:
    """Load and validate a generated policy snapshot."""
    return DemoPolicySnapshot.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def canonical_sha256(payload: Mapping[str, object]) -> str:
    """Return a stable SHA-256 for a JSON-compatible mapping."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ApprovalStatus",
    "BundlingPolicy",
    "CapitalizationPolicy",
    "DemoPolicySnapshot",
    "JCReferenceHandling",
    "PolicySetting",
    "ReferenceManifest",
    "ReferenceSource",
    "ReviewBehavior",
    "TaskMasterSnapshot",
    "TaskReferenceEntry",
    "build_demo_policy_snapshot",
    "build_task_master_snapshot",
    "canonical_sha256",
    "load_demo_policy_snapshot",
    "load_task_master_snapshot",
    "rank_task_candidates",
]
