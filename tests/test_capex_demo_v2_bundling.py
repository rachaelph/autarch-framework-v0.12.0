from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pytest

from autarch.capex.demo_v2_bundling import (
    BundlingInputError,
    evaluate_bundle_candidates,
)
from autarch.capex.demo_v2_contracts import (
    BundlingDisposition,
    CapitalizationAssessment,
    CapitalizationRecommendation,
    NatureResult,
)
from autarch.capex.demo_v2_normalization import (
    DemoContextOverlay,
    NormalizationDataQuality,
    NormalizationStatus,
    NormalizedInvoiceLine,
    NormalizedInvoicePackage,
    NormalizedSource,
)
from autarch.capex.demo_v2_reference import build_demo_policy_snapshot


NOW = datetime(2026, 11, 2, 19, 0, tzinfo=timezone.utc)


def _package(
    package_id: str,
    invoice_id: str,
    line_id: str,
    amount: float,
    *,
    vendor_name: str = "Demo Vendor",
    po_number: Optional[str] = None,
    currency: str = "USD",
    project_id: str = "DEMO-PROJECT-001",
    site_id: str = "DEMO-SITE-001",
    project_status: str = "OPEN",
    bundling_in_scope: bool = True,
    include_overlay: bool = True,
    quantity: Optional[float] = 1.0,
    unit_cost: Optional[float] = None,
) -> NormalizedInvoicePackage:
    overlay = (
        DemoContextOverlay(
            overlay_version="test-overlay",
            target_invoice_id=invoice_id,
            project_id=project_id,
            site_id=site_id,
            project_status=project_status,
            bundling_in_scope=bundling_in_scope,
        )
        if include_overlay
        else None
    )
    return NormalizedInvoicePackage(
        package_id=package_id,
        run_id=f"normalize-{package_id}",
        invoice_id=invoice_id,
        invoice_number=invoice_id,
        vendor_name=vendor_name,
        invoice_total=amount,
        currency=currency,
        country_region="North America",
        po_number=po_number,
        normalization_status=NormalizationStatus.NORMALIZED,
        lines=[
            NormalizedInvoiceLine(
                line_id=line_id,
                line_number=1,
                description="Eligible capital equipment",
                quantity=quantity,
                unit_cost=amount if unit_cost is None else unit_cost,
                line_amount=amount,
                currency=currency,
            )
        ],
        context_overlay=overlay,
        data_quality=NormalizationDataQuality(),
        source=NormalizedSource(
            file_name=f"{package_id}.json",
            sha256="a" * 64,
            source_system="TEST",
        ),
        created_at_utc=NOW,
    )


def _assessment(
    package: NormalizedInvoicePackage,
    *,
    task_code: str = "1000",
    disposition: BundlingDisposition = BundlingDisposition.CANDIDATE,
) -> CapitalizationAssessment:
    line = package.lines[0]
    recommendation = (
        CapitalizationRecommendation.REVIEW
        if disposition == BundlingDisposition.REVIEW
        else CapitalizationRecommendation.OPEX
    )
    return CapitalizationAssessment(
        workflow_run_id="capitalization-1",
        package_id=package.package_id,
        invoice_id=package.invoice_id,
        line_id=line.line_id,
        suggested_task_code=task_code,
        nature_result=NatureResult.CAPITALIZABLE,
        repair_maintenance_indicator=False,
        never_capitalize_hit=False,
        unit_cost_meets_threshold=False,
        threshold_amount=1000,
        threshold_currency=line.currency,
        recommendation=recommendation,
        outcome_category=(
            "REVIEW_BUNDLING_CONTEXT"
            if disposition == BundlingDisposition.REVIEW
            else "OPEX_POTENTIAL_FUTURE_BUNDLING"
        ),
        bundling_disposition=disposition,
        missing_inputs=["siteId"] if recommendation.value == "REVIEW" else [],
        review_reasons=(
            ["Site context is missing."]
            if recommendation.value == "REVIEW"
            else []
        ),
        rationale="Deterministic capitalization result.",
        policy_citations=["policy:test-policy"],
        policy_version="test-policy",
        created_at_utc=NOW,
    )


def test_groups_matching_lines_across_invoices_vendors_and_purchase_orders() -> None:
    first = _package(
        "package-1",
        "invoice-1",
        "line-1",
        600,
        vendor_name="Vendor A",
        po_number="PO-1",
    )
    second = _package(
        "package-2",
        "invoice-2",
        "line-2",
        400,
        vendor_name="Vendor B",
        po_number="PO-2",
    )
    policy = build_demo_policy_snapshot()

    result = evaluate_bundle_candidates(
        [first, second],
        [_assessment(first), _assessment(second)],
        policy,
        workflow_run_id="bundle-run-1",
        created_at_utc=NOW,
    )

    assert result.eligible_member_count == 2
    assert result.grouped_member_count == 2
    assert result.ungrouped_candidate_count == 0
    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.total_amount == 1000
    assert group.threshold_amount == 1000
    assert group.disposition == BundlingDisposition.CANDIDATE
    assert {member.invoice_id for member in group.members} == {
        "invoice-1",
        "invoice-2",
    }
    assert all(
        assessment.bundling_disposition == BundlingDisposition.GROUPED
        for assessment in result.assessments
    )
    assert all(
        assessment.bundle_group_id == group.bundle_group_id
        for assessment in result.assessments
    )
    assert "meets or exceeds" in group.rationale


def test_prefers_human_final_task_and_is_idempotent_for_same_run() -> None:
    first = _package("package-1", "invoice-1", "line-1", 100)
    second = _package("package-2", "invoice-2", "line-2", 200)
    assessments = [
        _assessment(first, task_code="1000"),
        _assessment(second, task_code="2000"),
    ]
    policy = build_demo_policy_snapshot()

    without_override = evaluate_bundle_candidates(
        [first, second],
        assessments,
        policy,
        workflow_run_id="bundle-run-1",
        created_at_utc=NOW,
    )
    first_run = evaluate_bundle_candidates(
        [first, second],
        assessments,
        policy,
        workflow_run_id="bundle-run-1",
        final_task_codes={("package-2", "line-2"): "1000"},
        created_at_utc=NOW,
    )
    replay = evaluate_bundle_candidates(
        [second, first],
        list(reversed(assessments)),
        policy,
        workflow_run_id="bundle-run-1",
        final_task_codes={("package-2", "line-2"): "1000"},
        created_at_utc=NOW,
    )

    assert without_override.groups == []
    assert len(first_run.groups) == 1
    assert first_run.groups == replay.groups
    assert first_run.groups[0].suggested_task_code == "1000"


def test_singletons_closed_projects_and_missing_context_are_not_grouped() -> None:
    singleton = _package("package-1", "invoice-1", "line-1", 100)
    closed = _package(
        "package-2",
        "invoice-2",
        "line-2",
        200,
        project_status="CLOSED",
    )
    missing = _package(
        "package-3",
        "invoice-3",
        "line-3",
        300,
        include_overlay=False,
    )

    result = evaluate_bundle_candidates(
        [singleton, closed, missing],
        [
            _assessment(singleton),
            _assessment(
                closed,
                disposition=BundlingDisposition.NOT_ELIGIBLE,
            ),
            _assessment(
                missing,
                disposition=BundlingDisposition.REVIEW,
            ),
        ],
        build_demo_policy_snapshot(),
        workflow_run_id="bundle-run-1",
        created_at_utc=NOW,
    )

    assert result.groups == []
    assert result.eligible_member_count == 1
    assert result.ungrouped_candidate_count == 1
    assert result.assessments[0].bundling_disposition == (
        BundlingDisposition.CANDIDATE
    )


def test_different_currency_candidates_do_not_match() -> None:
    usd = _package("package-1", "invoice-1", "line-1", 100, currency="USD")
    cad = _package("package-2", "invoice-2", "line-2", 200, currency="CAD")

    result = evaluate_bundle_candidates(
        [usd, cad],
        [_assessment(usd), _assessment(cad)],
        build_demo_policy_snapshot(),
        workflow_run_id="bundle-run-1",
        created_at_utc=NOW,
    )

    assert result.groups == []
    assert result.ungrouped_candidate_count == 2


def test_zero_value_candidates_do_not_create_a_bundle() -> None:
    first = _package("package-1", "invoice-1", "line-1", 0)
    second = _package("package-2", "invoice-2", "line-2", 0)

    result = evaluate_bundle_candidates(
        [first, second],
        [_assessment(first), _assessment(second)],
        build_demo_policy_snapshot(),
        workflow_run_id="bundle-run-1",
        created_at_utc=NOW,
    )

    assert result.groups == []
    assert result.eligible_member_count == 2
    assert result.grouped_member_count == 0
    assert result.ungrouped_candidate_count == 2
    assert all(
        assessment.bundling_disposition == BundlingDisposition.CANDIDATE
        and assessment.bundle_group_id is None
        for assessment in result.assessments
    )


def test_zero_value_line_can_remain_in_a_positive_value_bundle() -> None:
    first = _package("package-1", "invoice-1", "line-1", 0)
    second = _package("package-2", "invoice-2", "line-2", 100)

    result = evaluate_bundle_candidates(
        [first, second],
        [_assessment(first), _assessment(second)],
        build_demo_policy_snapshot(),
        workflow_run_id="bundle-run-1",
        created_at_utc=NOW,
    )

    assert len(result.groups) == 1
    assert result.groups[0].total_amount == 100
    assert result.grouped_member_count == 2
    assert result.ungrouped_candidate_count == 0


def test_candidate_with_inconsistent_context_or_values_fails_explicitly() -> None:
    missing_context = _package(
        "package-1",
        "invoice-1",
        "line-1",
        100,
        include_overlay=False,
    )
    missing_quantity = _package(
        "package-2",
        "invoice-2",
        "line-2",
        200,
        quantity=None,
    )
    policy = build_demo_policy_snapshot()

    with pytest.raises(BundlingInputError, match="no context overlay"):
        evaluate_bundle_candidates(
            [missing_context],
            [_assessment(missing_context)],
            policy,
            workflow_run_id="bundle-run-1",
            created_at_utc=NOW,
        )
    with pytest.raises(BundlingInputError, match="positive quantity"):
        evaluate_bundle_candidates(
            [missing_quantity],
            [_assessment(missing_quantity)],
            policy,
            workflow_run_id="bundle-run-1",
            created_at_utc=NOW,
        )
