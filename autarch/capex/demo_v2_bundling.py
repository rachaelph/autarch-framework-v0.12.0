"""Deterministic bundle-candidate evaluation for CapEx Demo V2."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .demo_v2_contracts import (
    BundleCandidateGroup,
    BundleCandidateMember,
    BundlingDisposition,
    CapitalizationAssessment,
    derive_bundle_group_id,
)
from .demo_v2_normalization import (
    NormalizedInvoiceLine,
    NormalizedInvoicePackage,
)
from .demo_v2_reference import DemoPolicySnapshot


LineKey = Tuple[str, str]
GroupingKey = Tuple[str, str, str, str]
_OPEN_PROJECT_STATUSES = {"OPEN"}


class BundlingInputError(ValueError):
    """Raised when candidate inputs cannot be grouped safely."""


@dataclass(frozen=True)
class BundlingEvaluation:
    """Bundle groups and line assessments after deterministic evaluation."""

    groups: List[BundleCandidateGroup]
    assessments: List[CapitalizationAssessment]
    eligible_member_count: int
    grouped_member_count: int
    ungrouped_candidate_count: int


@dataclass(frozen=True)
class _Candidate:
    assessment: CapitalizationAssessment
    package: NormalizedInvoicePackage
    line: NormalizedInvoiceLine
    effective_task_code: str
    site_id: str
    project_id: str
    currency: str
    quantity: float
    unit_cost: float


def _package_map(
    packages: Sequence[NormalizedInvoicePackage],
) -> Dict[str, NormalizedInvoicePackage]:
    package_by_id = {package.package_id: package for package in packages}
    if len(package_by_id) != len(packages):
        raise BundlingInputError("packages contain duplicate packageId values")
    return package_by_id


def _assessment_map(
    assessments: Sequence[CapitalizationAssessment],
) -> Dict[LineKey, CapitalizationAssessment]:
    assessment_by_line = {
        (assessment.package_id, assessment.line_id): assessment
        for assessment in assessments
    }
    if len(assessment_by_line) != len(assessments):
        raise BundlingInputError(
            "assessments contain duplicate packageId/lineId values"
        )
    return assessment_by_line


def _line_map(
    package: NormalizedInvoicePackage,
) -> Dict[str, NormalizedInvoiceLine]:
    return {line.line_id: line for line in package.lines}


def _candidate_for_assessment(
    assessment: CapitalizationAssessment,
    package_by_id: Mapping[str, NormalizedInvoicePackage],
    final_task_codes: Mapping[LineKey, str],
) -> _Candidate:
    package = package_by_id.get(assessment.package_id)
    if package is None:
        raise BundlingInputError(
            f"assessment references unknown packageId {assessment.package_id!r}"
        )
    line = _line_map(package).get(assessment.line_id)
    if line is None:
        raise BundlingInputError(
            f"assessment references unknown lineId {assessment.line_id!r}"
        )
    effective_task_code = final_task_codes.get(
        (assessment.package_id, assessment.line_id),
        assessment.classification_key or "",
    ).strip()
    if not effective_task_code:
        raise BundlingInputError(
            f"candidate line {assessment.line_id!r} has no effective task code"
        )
    overlay = package.context_overlay
    if overlay is None:
        raise BundlingInputError(
            f"candidate line {assessment.line_id!r} has no context overlay"
        )
    if not overlay.bundling_in_scope:
        raise BundlingInputError(
            f"candidate line {assessment.line_id!r} is outside bundling scope"
        )
    if overlay.project_status.strip().upper() not in _OPEN_PROJECT_STATUSES:
        raise BundlingInputError(
            f"candidate line {assessment.line_id!r} does not have an open "
            "project"
        )
    if line.quantity is None or line.quantity <= 0:
        raise BundlingInputError(
            f"candidate line {assessment.line_id!r} requires positive quantity"
        )
    if line.unit_cost is None or line.unit_cost < 0:
        raise BundlingInputError(
            f"candidate line {assessment.line_id!r} requires non-negative "
            "unit cost"
        )
    if line.line_amount < 0:
        raise BundlingInputError(
            f"candidate line {assessment.line_id!r} requires non-negative "
            "line amount"
        )
    return _Candidate(
        assessment=assessment,
        package=package,
        line=line,
        effective_task_code=effective_task_code,
        site_id=overlay.site_id,
        project_id=overlay.project_id,
        currency=line.currency,
        quantity=line.quantity,
        unit_cost=line.unit_cost,
    )


def _grouping_key(candidate: _Candidate) -> GroupingKey:
    return (
        candidate.effective_task_code,
        candidate.site_id,
        candidate.project_id,
        candidate.currency.upper(),
    )


def _group_rationale(
    *,
    member_count: int,
    total_amount: float,
    threshold_amount: float,
    currency: str,
) -> str:
    if total_amount >= threshold_amount:
        threshold_result = (
            "meets or exceeds the governed threshold and requires analyst "
            "confirmation as a potential single asset"
        )
    else:
        threshold_result = (
            "remains below the governed threshold and is retained as a "
            "potential future bundle while the project is open"
        )
    return (
        f"{member_count} eligible lines share the governed task, site, "
        f"project, and currency. Their combined current-run value of "
        f"{total_amount:.2f} {currency} {threshold_result}."
    )


def evaluate_bundle_candidates(
    packages: Sequence[NormalizedInvoicePackage],
    assessments: Sequence[CapitalizationAssessment],
    policy: DemoPolicySnapshot,
    *,
    workflow_run_id: str,
    final_task_codes: Optional[Mapping[LineKey, str]] = None,
    created_at_utc: Optional[datetime] = None,
    currency_policies: Optional[Mapping[str, DemoPolicySnapshot]] = None,
) -> BundlingEvaluation:
    """Group eligible lines by effective task, site, project, and currency."""
    if not workflow_run_id.strip():
        raise BundlingInputError("workflow_run_id must be non-empty")
    package_by_id = _package_map(packages)
    assessment_by_line = _assessment_map(assessments)
    task_overrides = final_task_codes or {}
    unknown_overrides = set(task_overrides) - set(assessment_by_line)
    if unknown_overrides:
        raise BundlingInputError(
            "final_task_codes reference unknown assessment lines"
        )

    candidate_groups: Dict[GroupingKey, List[_Candidate]] = defaultdict(list)
    for assessment in assessments:
        if assessment.bundling_disposition != BundlingDisposition.CANDIDATE:
            continue
        candidate = _candidate_for_assessment(
            assessment,
            package_by_id,
            task_overrides,
        )
        candidate_groups[_grouping_key(candidate)].append(candidate)

    minimum_members = policy.bundling.minimum_candidate_members
    created_at = created_at_utc or datetime.now(timezone.utc)
    groups: List[BundleCandidateGroup] = []
    grouped_ids: Dict[LineKey, str] = {}
    eligible_member_count = sum(len(items) for items in candidate_groups.values())

    for key in sorted(candidate_groups):
        candidates = sorted(
            candidate_groups[key],
            key=lambda item: (
                item.package.package_id,
                item.line.line_id,
            ),
        )
        if len(candidates) < minimum_members:
            continue
        task_code, site_id, project_id, currency = key
        group_policy = (currency_policies or {}).get(currency, policy)
        if group_policy.capitalization.currency != currency:
            raise BundlingInputError("Bundle currency has no matching threshold policy")
        group_id = derive_bundle_group_id(
            workflow_run_id,
            task_code,
            site_id,
            project_id,
            currency,
        )
        total_decimal = sum(
            (Decimal(str(item.line.line_amount)) for item in candidates),
            start=Decimal("0"),
        )
        if total_decimal == 0:
            continue
        total_amount = float(total_decimal)
        threshold_amount = group_policy.capitalization.base_threshold
        citations = {
            f"policy:{group_policy.policy_id}",
            "setting:base-threshold",
            "setting:bundling-key",
        }
        for item in candidates:
            overlay = item.package.context_overlay
            if overlay is not None and overlay.synthetic:
                citations.add(
                    f"synthetic-context:{overlay.overlay_version}"
                )
            grouped_ids[
                (item.assessment.package_id, item.assessment.line_id)
            ] = group_id
        groups.append(
            BundleCandidateGroup(
                workflow_run_id=workflow_run_id,
                bundle_group_id=group_id,
                suggested_task_code=None if task_code.startswith("EU_ASSET_CLASS:") else task_code,
                suggested_asset_class_code=task_code.removeprefix("EU_ASSET_CLASS:")
                    if task_code.startswith("EU_ASSET_CLASS:") else None,
                site_id=site_id,
                project_id=project_id,
                currency=currency,
                total_amount=total_amount,
                threshold_amount=threshold_amount,
                disposition=BundlingDisposition.CANDIDATE,
                members=[
                    BundleCandidateMember(
                        package_id=item.package.package_id,
                        invoice_id=item.package.invoice_id,
                        line_id=item.line.line_id,
                        quantity=item.quantity,
                        unit_cost=item.unit_cost,
                        line_amount=item.line.line_amount,
                    )
                    for item in candidates
                ],
                rationale=_group_rationale(
                    member_count=len(candidates),
                    total_amount=total_amount,
                    threshold_amount=threshold_amount,
                    currency=currency,
                ),
                policy_citations=sorted(citations),
                policy_version=group_policy.policy_id,
                created_at_utc=created_at,
            )
        )

    updated_assessments = [
        assessment.model_copy(
            update={
                "bundling_disposition": BundlingDisposition.GROUPED,
                "bundle_group_id": grouped_ids[
                    (assessment.package_id, assessment.line_id)
                ],
            }
        )
        if (assessment.package_id, assessment.line_id) in grouped_ids
        else assessment
        for assessment in assessments
    ]
    grouped_member_count = len(grouped_ids)
    return BundlingEvaluation(
        groups=groups,
        assessments=updated_assessments,
        eligible_member_count=eligible_member_count,
        grouped_member_count=grouped_member_count,
        ungrouped_candidate_count=(
            eligible_member_count - grouped_member_count
        ),
    )


__all__ = [
    "BundlingEvaluation",
    "BundlingInputError",
    "evaluate_bundle_candidates",
]
