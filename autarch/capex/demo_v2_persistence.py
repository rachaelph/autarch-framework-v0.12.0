"""Atomic, append-only persistence for CapEx Demo V2 results."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Sequence, Tuple

from .demo_v2_contracts import (
    BundleCandidateGroup,
    BundlingDisposition,
    CapitalizationAssessment,
    PersistenceCounts,
)
from .persistence import DatabaseConnection


class DemoV2PersistenceError(RuntimeError):
    """Raised when Demo V2 result persistence cannot complete safely."""


_TRANSACTION_PREAMBLE = """
SET XACT_ABORT ON;
"""

_SELECT_EXISTING_RUN = """
SELECT
    [resultSha256],
    [assessmentCount],
    [bundleGroupCount],
    [bundleMemberCount]
FROM [capex].[AssessmentRun] WITH (UPDLOCK, HOLDLOCK)
WHERE [workflowRunId] = ?;
"""

_INSERT_RUN = """
INSERT INTO [capex].[AssessmentRun]
(
    [workflowRunId],
    [schemaVersion],
    [resultSha256],
    [assessmentCount],
    [bundleGroupCount],
    [bundleMemberCount],
    [createdAtUtc]
)
VALUES (?, '2.0', ?, ?, ?, ?, ?);
"""

_INSERT_ASSESSMENTS = """
INSERT INTO [capex].[Assessment]
(
    [workflowRunId],
    [schemaVersion],
    [packageId],
    [invoiceId],
    [lineId],
    [suggestedTaskCode],
    [natureResult],
    [repairMaintenanceIndicator],
    [neverCapitalizeHit],
    [unitCostMeetsThreshold],
    [thresholdAmount],
    [thresholdCurrency],
    [recommendation],
    [outcomeCategory],
    [bundlingDisposition],
    [bundleGroupId],
    [missingInputs],
    [reviewReasons],
    [rationale],
    [policyCitations],
    [usedLlm],
    [modelVersion],
    [policyVersion],
    [createdAtUtc]
)
VALUES
(
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
);
"""

_INSERT_GROUPS = """
INSERT INTO [capex].[BundleCandidateGroup]
(
    [workflowRunId],
    [schemaVersion],
    [bundleGroupId],
    [suggestedTaskCode],
    [siteId],
    [projectId],
    [currency],
    [totalAmount],
    [thresholdAmount],
    [disposition],
    [missingInputs],
    [rationale],
    [policyCitations],
    [policyVersion],
    [createdAtUtc]
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_MEMBERS = """
INSERT INTO [capex].[BundleCandidateMember]
(
    [workflowRunId],
    [bundleGroupId],
    [packageId],
    [invoiceId],
    [lineId],
    [quantity],
    [unitCost],
    [lineAmount]
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?);
"""


def _json_array(values: Sequence[str]) -> str:
    return json.dumps(
        list(values),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _utc_naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None)


def _canonical_payload(
    workflow_run_id: str,
    assessments: Sequence[CapitalizationAssessment],
    groups: Sequence[BundleCandidateGroup],
) -> Dict[str, object]:
    assessment_payloads = sorted(
        (
            assessment.model_dump(mode="json", by_alias=True)
            for assessment in assessments
        ),
        key=lambda item: (
            str(item["packageId"]),
            str(item["lineId"]),
        ),
    )
    group_payloads: List[Dict[str, object]] = []
    for group in sorted(groups, key=lambda item: item.bundle_group_id):
        payload = group.model_dump(mode="json", by_alias=True)
        members = payload["members"]
        assert isinstance(members, list)
        payload["members"] = sorted(
            members,
            key=lambda item: (
                str(item["packageId"]),
                str(item["lineId"]),
            ),
        )
        group_payloads.append(payload)
    return {
        "schemaVersion": "2.0",
        "workflowRunId": workflow_run_id,
        "assessments": assessment_payloads,
        "bundleGroups": group_payloads,
    }


def _result_sha256(payload: Dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_inputs(
    workflow_run_id: str,
    assessments: Sequence[CapitalizationAssessment],
    groups: Sequence[BundleCandidateGroup],
) -> None:
    if not workflow_run_id.strip():
        raise DemoV2PersistenceError("workflow_run_id must be non-empty")
    if not assessments:
        raise DemoV2PersistenceError(
            "At least one capitalization assessment is required."
        )

    assessment_by_line: Dict[
        Tuple[str, str],
        CapitalizationAssessment,
    ] = {}
    for assessment in assessments:
        if assessment.workflow_run_id != workflow_run_id:
            raise DemoV2PersistenceError(
                "Every assessment workflowRunId must match the persisted run."
            )
        key = (assessment.package_id, assessment.line_id)
        if key in assessment_by_line:
            raise DemoV2PersistenceError(
                "Assessments contain duplicate packageId/lineId values."
            )
        assessment_by_line[key] = assessment

    group_ids = set()
    member_keys = set()
    grouped_member_keys = set()
    for group in groups:
        if group.workflow_run_id != workflow_run_id:
            raise DemoV2PersistenceError(
                "Every bundle group workflowRunId must match the persisted run."
            )
        if group.bundle_group_id in group_ids:
            raise DemoV2PersistenceError(
                "Bundle groups contain a duplicate bundleGroupId."
            )
        group_ids.add(group.bundle_group_id)
        member_total = Decimal("0")
        for member in group.members:
            key = (member.package_id, member.line_id)
            if key in member_keys:
                raise DemoV2PersistenceError(
                    "A line cannot belong to multiple persisted bundle groups."
                )
            member_keys.add(key)
            assessment = assessment_by_line.get(key)
            if assessment is None:
                raise DemoV2PersistenceError(
                    "Every bundle member must reference a persisted assessment."
                )
            if group.disposition == BundlingDisposition.REVIEW:
                if (
                    assessment.bundling_disposition
                    != BundlingDisposition.REVIEW
                    or assessment.bundle_group_id is not None
                ):
                    raise DemoV2PersistenceError(
                        "Review bundle members must reference REVIEW "
                        "assessments without a group ID."
                    )
            else:
                if (
                    assessment.bundling_disposition
                    != BundlingDisposition.GROUPED
                    or assessment.bundle_group_id != group.bundle_group_id
                ):
                    raise DemoV2PersistenceError(
                        "Bundle member and assessment group references "
                        "must match."
                    )
                grouped_member_keys.add(key)
            member_total += Decimal(str(member.line_amount))
        if member_total != Decimal(str(group.total_amount)):
            raise DemoV2PersistenceError(
                f"Bundle group {group.bundle_group_id!r} total does not "
                "reconcile to its members."
            )

    grouped_assessment_keys = {
        key
        for key, assessment in assessment_by_line.items()
        if assessment.bundling_disposition == BundlingDisposition.GROUPED
    }
    if grouped_assessment_keys != grouped_member_keys:
        raise DemoV2PersistenceError(
            "Every GROUPED assessment must have exactly one bundle member."
        )


def _assessment_values(
    assessments: Sequence[CapitalizationAssessment],
) -> List[Tuple[Any, ...]]:
    return [
        (
            item.workflow_run_id,
            item.schema_version,
            item.package_id,
            item.invoice_id,
            item.line_id,
            item.suggested_task_code,
            item.nature_result.value,
            item.repair_maintenance_indicator,
            item.never_capitalize_hit,
            item.unit_cost_meets_threshold,
            (
                None
                if item.threshold_amount is None
                else _decimal(item.threshold_amount)
            ),
            item.threshold_currency,
            item.recommendation.value,
            item.outcome_category,
            item.bundling_disposition.value,
            item.bundle_group_id,
            _json_array(item.missing_inputs),
            _json_array(item.review_reasons),
            item.rationale,
            _json_array(item.policy_citations),
            item.used_llm,
            item.model_version,
            item.policy_version,
            _utc_naive(item.created_at_utc),
        )
        for item in assessments
    ]


def _group_values(
    groups: Sequence[BundleCandidateGroup],
) -> List[Tuple[Any, ...]]:
    return [
        (
            item.workflow_run_id,
            item.schema_version,
            item.bundle_group_id,
            item.suggested_task_code,
            item.site_id,
            item.project_id,
            item.currency,
            _decimal(item.total_amount),
            (
                None
                if item.threshold_amount is None
                else _decimal(item.threshold_amount)
            ),
            item.disposition.value,
            _json_array(item.missing_inputs),
            item.rationale,
            _json_array(item.policy_citations),
            item.policy_version,
            _utc_naive(item.created_at_utc),
        )
        for item in groups
    ]


def _member_values(
    groups: Sequence[BundleCandidateGroup],
) -> List[Tuple[Any, ...]]:
    return [
        (
            group.workflow_run_id,
            group.bundle_group_id,
            member.package_id,
            member.invoice_id,
            member.line_id,
            _decimal(member.quantity),
            _decimal(member.unit_cost),
            _decimal(member.line_amount),
        )
        for group in groups
        for member in group.members
    ]


def persist_demo_v2_results(
    connection: DatabaseConnection,
    assessments: Sequence[CapitalizationAssessment],
    groups: Sequence[BundleCandidateGroup],
    *,
    workflow_run_id: str,
    created_at_utc: datetime | None = None,
) -> PersistenceCounts:
    """Persist one complete assessment run atomically and idempotently."""
    normalized_run_id = workflow_run_id.strip()
    _validate_inputs(normalized_run_id, assessments, groups)
    payload = _canonical_payload(normalized_run_id, assessments, groups)
    result_sha256 = _result_sha256(payload)
    assessment_rows = _assessment_values(assessments)
    group_rows = _group_values(groups)
    member_rows = _member_values(groups)
    counts = PersistenceCounts(
        assessments=len(assessment_rows),
        bundle_groups=len(group_rows),
        bundle_members=len(member_rows),
    )
    created_at = _utc_naive(created_at_utc or datetime.now(timezone.utc))

    cursor = connection.cursor()
    transaction_started = False
    try:
        cursor.execute(_TRANSACTION_PREAMBLE)
        transaction_started = True
        cursor.execute(_SELECT_EXISTING_RUN, (normalized_run_id,))
        existing = cursor.fetchone()
        if existing is not None:
            expected = (
                result_sha256,
                counts.assessments,
                counts.bundle_groups,
                counts.bundle_members,
            )
            actual = (
                str(existing[0]),
                int(existing[1]),
                int(existing[2]),
                int(existing[3]),
            )
            if actual != expected:
                raise DemoV2PersistenceError(
                    "The workflow run ID already belongs to different "
                    "assessment or bundle results."
                )
            connection.commit()
            transaction_started = False
            return counts

        cursor.execute(
            _INSERT_RUN,
            (
                normalized_run_id,
                result_sha256,
                counts.assessments,
                counts.bundle_groups,
                counts.bundle_members,
                created_at,
            ),
        )
        cursor.executemany(_INSERT_ASSESSMENTS, assessment_rows)
        if group_rows:
            cursor.executemany(_INSERT_GROUPS, group_rows)
        if member_rows:
            cursor.executemany(_INSERT_MEMBERS, member_rows)
        connection.commit()
        transaction_started = False
        return counts
    except DemoV2PersistenceError:
        if transaction_started:
            connection.rollback()
        raise
    except Exception as exc:
        if transaction_started:
            connection.rollback()
        raise DemoV2PersistenceError(
            f"Demo V2 persistence failed: {exc}"
        ) from exc
    finally:
        cursor.close()


__all__ = [
    "DemoV2PersistenceError",
    "persist_demo_v2_results",
]
