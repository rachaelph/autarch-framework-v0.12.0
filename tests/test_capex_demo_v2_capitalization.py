from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

import pytest

from autarch.capex.demo_v2_capitalization import (
    CapitalizationInputError,
    CapitalizationNatureRule,
    CapitalizationRuleSet,
    assess_capitalization_for_invoice,
)
from autarch.capex.demo_v2_contracts import (
    BundlingDisposition,
    CapitalizationRecommendation,
    NatureResult,
    TaskDetermination,
)
from autarch.capex.demo_v2_normalization import (
    DemoContextOverlay,
    NormalizationDataQuality,
    NormalizationStatus,
    NormalizedInvoiceLine,
    NormalizedInvoicePackage,
    NormalizedSource,
)
from autarch.capex.demo_v2_reference import (
    DemoPolicySnapshot,
    build_demo_policy_snapshot,
)


NOW = datetime(2026, 11, 2, 18, 20, tzinfo=timezone.utc)
HASH = "a" * 64


def _line(
    line_id: str,
    description: str,
    *,
    unit_cost: Optional[float] = 1000.0,
    currency: str = "USD",
) -> NormalizedInvoiceLine:
    return NormalizedInvoiceLine(
        line_id=line_id,
        line_number=int(line_id.rsplit("-", 1)[-1]),
        description=description,
        quantity=1,
        unit_cost=unit_cost,
        line_amount=unit_cost or 100.0,
        currency=currency,
    )


def _overlay(
    *,
    project_status: str = "OPEN",
    bundling_in_scope: bool = True,
) -> DemoContextOverlay:
    return DemoContextOverlay(
        overlay_version="test-overlay",
        target_invoice_id="invoice-1",
        project_id="DEMO-PROJECT-001",
        site_id="DEMO-SITE-001",
        project_status=project_status,
        bundling_in_scope=bundling_in_scope,
    )


def _package(
    lines: List[NormalizedInvoiceLine],
    *,
    country_region: str = "North America",
    currency: str = "USD",
    context_overlay: Optional[DemoContextOverlay] = None,
) -> NormalizedInvoicePackage:
    return NormalizedInvoicePackage(
        package_id="package-1",
        run_id="normalize-1",
        invoice_id="invoice-1",
        vendor_name="Demo Vendor",
        invoice_total=sum(line.line_amount for line in lines),
        currency=currency,
        country_region=country_region,
        normalization_status=NormalizationStatus.NORMALIZED,
        lines=lines,
        context_overlay=context_overlay,
        data_quality=NormalizationDataQuality(),
        source=NormalizedSource(
            file_name="demo.json",
            sha256="b" * 64,
            source_system="TEST",
        ),
        created_at_utc=NOW,
    )


def _task(
    line_id: str,
    *,
    status: str = "RESOLVED",
    task_code: Optional[str] = "1000",
) -> TaskDetermination:
    review = status == "REVIEW"
    return TaskDetermination.model_validate(
        {
            "workflowRunId": "task-workflow",
            "packageId": "package-1",
            "invoiceId": "invoice-1",
            "lineId": line_id,
            "resolutionStatus": status,
            "suggestedTaskCode": None if review else task_code,
            "matchMethod": "REVIEW" if review else "EXACT_TASK",
            "confidence": 0.0 if review else 1.0,
            "unresolvedReason": (
                "No governed task mapping." if review else None
            ),
            "rationale": (
                "Task requires review."
                if review
                else "Task is governed."
            ),
            "evidenceRefs": [f"line:{line_id}"],
            "taskMasterSha256": HASH,
            "promptVersion": "demo-v2-task-v1",
            "createdAtUtc": NOW,
        }
    )


def _approved_policy(
    *,
    approve_below_threshold: bool = True,
) -> DemoPolicySnapshot:
    payload = build_demo_policy_snapshot().model_dump(
        mode="json",
        by_alias=True,
    )
    payload["capitalization"][
        "operationalNatureRulesApproved"
    ] = True
    approved_ids = {
        "threshold-equality",
        "threshold-basis",
        "nature-rule-library",
    }
    if approve_below_threshold:
        approved_ids.add("below-threshold-exception")
    for setting in payload["settings"]:
        if setting["settingId"] in approved_ids:
            setting["status"] = "WORKFLOW_CONFIRMED"
            setting["reviewWhenUnconfirmed"] = False
    payload["unresolvedConfirmationIds"] = [
        setting_id
        for setting_id in payload["unresolvedConfirmationIds"]
        if setting_id not in approved_ids
    ]
    return DemoPolicySnapshot.model_validate(payload)


def _rules(*rules: CapitalizationNatureRule) -> CapitalizationRuleSet:
    return CapitalizationRuleSet(
        rule_set_id="finance-rules-v1",
        approved=True,
        rules=list(rules),
    )


CAPITAL_RULE = CapitalizationNatureRule(
    rule_id="capital-equipment",
    version="1",
    nature_result=NatureResult.CAPITALIZABLE,
    task_codes=["1000"],
    policy_citation="Finance rule: capital equipment.",
)
NEVER_CAPITALIZE_RULE = CapitalizationNatureRule(
    rule_id="never-capitalize-shipping",
    version="1",
    nature_result=NatureResult.OPEX_BY_NATURE,
    exact_descriptions=["Shipping"],
    never_capitalize=True,
    policy_citation="Finance rule: standalone shipping.",
)
REPAIR_RULE = CapitalizationNatureRule(
    rule_id="routine-repair",
    version="1",
    nature_result=NatureResult.OPEX_BY_NATURE,
    exact_descriptions=["Routine repair"],
    repair_maintenance_indicator=True,
    policy_citation="Finance rule: routine repair and maintenance.",
)


@pytest.mark.parametrize("unit_cost", [1000.0, 1000.01, 2500.0])
def test_capitalizable_unit_cost_at_or_above_threshold_is_capex(
    unit_cost: float,
) -> None:
    package = _package([_line("line-1", "Equipment", unit_cost=unit_cost)])

    result = assess_capitalization_for_invoice(
        package,
        [_task("line-1")],
        _approved_policy(),
        workflow_run_id="workflow-1",
        rule_set=_rules(CAPITAL_RULE),
        created_at_utc=NOW,
    )[0]

    assert result.recommendation == CapitalizationRecommendation.CAPEX
    assert result.unit_cost_meets_threshold is True
    assert result.outcome_category == "CAPEX_MEETS_UNIT_THRESHOLD"
    assert result.used_llm is False
    assert "rule:capital-equipment@1" in result.policy_citations


def test_below_threshold_open_project_is_opex_bundle_candidate() -> None:
    package = _package(
        [_line("line-1", "Equipment", unit_cost=999.99)],
        context_overlay=_overlay(),
    )

    result = assess_capitalization_for_invoice(
        package,
        [_task("line-1")],
        _approved_policy(),
        workflow_run_id="workflow-1",
        rule_set=_rules(CAPITAL_RULE),
        created_at_utc=NOW,
    )[0]

    assert result.recommendation == CapitalizationRecommendation.OPEX
    assert result.unit_cost_meets_threshold is False
    assert result.bundling_disposition == BundlingDisposition.CANDIDATE
    assert result.outcome_category == "OPEX_POTENTIAL_FUTURE_BUNDLING"


def test_below_threshold_missing_context_is_explicit_review() -> None:
    package = _package(
        [_line("line-1", "Equipment", unit_cost=500.0)]
    )

    result = assess_capitalization_for_invoice(
        package,
        [_task("line-1")],
        _approved_policy(),
        workflow_run_id="workflow-1",
        rule_set=_rules(CAPITAL_RULE),
        created_at_utc=NOW,
    )[0]

    assert result.recommendation == CapitalizationRecommendation.REVIEW
    assert result.nature_result == NatureResult.CAPITALIZABLE
    assert result.unit_cost_meets_threshold is False
    assert set(result.missing_inputs) == {
        "siteId",
        "projectId",
        "projectStatus",
        "bundlingInScope",
    }


def test_below_threshold_closed_project_is_not_bundle_eligible() -> None:
    result = assess_capitalization_for_invoice(
        _package(
            [_line("line-1", "Equipment", unit_cost=500.0)],
            context_overlay=_overlay(project_status="CLOSED"),
        ),
        [_task("line-1")],
        _approved_policy(),
        workflow_run_id="workflow-1",
        rule_set=_rules(CAPITAL_RULE),
        created_at_utc=NOW,
    )[0]

    assert result.recommendation == CapitalizationRecommendation.OPEX
    assert result.bundling_disposition == BundlingDisposition.NOT_ELIGIBLE
    assert result.outcome_category == (
        "OPEX_BELOW_THRESHOLD_CLOSED_PROJECT"
    )


def test_unknown_project_status_is_review() -> None:
    result = assess_capitalization_for_invoice(
        _package(
            [_line("line-1", "Equipment", unit_cost=500.0)],
            context_overlay=_overlay(project_status="PAUSED"),
        ),
        [_task("line-1")],
        _approved_policy(),
        workflow_run_id="workflow-1",
        rule_set=_rules(CAPITAL_RULE),
        created_at_utc=NOW,
    )[0]

    assert result.recommendation == CapitalizationRecommendation.REVIEW
    assert result.outcome_category == "REVIEW_PROJECT_STATUS"
    assert "approvedProjectStatus" in result.missing_inputs


def test_unconfirmed_below_threshold_exception_is_review() -> None:
    result = assess_capitalization_for_invoice(
        _package(
            [_line("line-1", "Equipment", unit_cost=500.0)],
            context_overlay=_overlay(),
        ),
        [_task("line-1")],
        _approved_policy(approve_below_threshold=False),
        workflow_run_id="workflow-1",
        rule_set=_rules(CAPITAL_RULE),
        created_at_utc=NOW,
    )[0]

    assert result.recommendation == CapitalizationRecommendation.REVIEW
    assert result.unit_cost_meets_threshold is False
    assert result.outcome_category == (
        "REVIEW_BELOW_THRESHOLD_EXCEPTION_POLICY"
    )
    assert "below-threshold-exception" in result.missing_inputs


@pytest.mark.parametrize(
    "rule,description,category,repair,never",
    [
        (
            NEVER_CAPITALIZE_RULE,
            "Shipping",
            "OPEX_NEVER_CAPITALIZE",
            False,
            True,
        ),
        (
            REPAIR_RULE,
            "Routine repair",
            "OPEX_REPAIR_MAINTENANCE",
            True,
            False,
        ),
    ],
)
def test_opex_nature_rules_run_before_threshold(
    rule: CapitalizationNatureRule,
    description: str,
    category: str,
    repair: bool,
    never: bool,
) -> None:
    package = _package(
        [_line("line-1", description, unit_cost=None)]
    )

    result = assess_capitalization_for_invoice(
        package,
        [_task("line-1")],
        _approved_policy(),
        workflow_run_id="workflow-1",
        rule_set=_rules(rule),
        created_at_utc=NOW,
    )[0]

    assert result.recommendation == CapitalizationRecommendation.OPEX
    assert result.nature_result == NatureResult.OPEX_BY_NATURE
    assert result.unit_cost_meets_threshold is None
    assert result.outcome_category == category
    assert result.repair_maintenance_indicator is repair
    assert result.never_capitalize_hit is never


def test_current_unapproved_policy_returns_review_with_partial_threshold() -> None:
    package = _package([_line("line-1", "Equipment", unit_cost=1500.0)])

    result = assess_capitalization_for_invoice(
        package,
        [_task("line-1")],
        build_demo_policy_snapshot(),
        workflow_run_id="workflow-1",
        rule_set=_rules(CAPITAL_RULE),
        created_at_utc=NOW,
    )[0]

    assert result.recommendation == CapitalizationRecommendation.REVIEW
    assert result.nature_result == NatureResult.REVIEW
    assert result.unit_cost_meets_threshold is True
    assert "approvedNatureRule" in result.missing_inputs
    assert result.policy_version == (
        "circle-k-na-demo-unapproved-2026-10-30"
    )


def test_unconfirmed_threshold_policy_retains_comparison_for_review() -> None:
    policy = _approved_policy()
    payload = policy.model_dump(mode="json", by_alias=True)
    for setting in payload["settings"]:
        if setting["settingId"] == "threshold-equality":
            setting["status"] = "BUSINESS_CONFIRMATION_REQUIRED"
            setting["reviewWhenUnconfirmed"] = True
    payload["unresolvedConfirmationIds"].append("threshold-equality")
    policy = DemoPolicySnapshot.model_validate(payload)

    result = assess_capitalization_for_invoice(
        _package([_line("line-1", "Equipment", unit_cost=1000.0)]),
        [_task("line-1")],
        policy,
        workflow_run_id="workflow-1",
        rule_set=_rules(CAPITAL_RULE),
        created_at_utc=NOW,
    )[0]

    assert result.recommendation == CapitalizationRecommendation.REVIEW
    assert result.nature_result == NatureResult.CAPITALIZABLE
    assert result.unit_cost_meets_threshold is True
    assert "threshold-equality" in result.missing_inputs
    assert result.outcome_category == "REVIEW_THRESHOLD_POLICY"


def test_missing_unit_cost_is_review_for_capitalizable_nature() -> None:
    result = assess_capitalization_for_invoice(
        _package([_line("line-1", "Equipment", unit_cost=None)]),
        [_task("line-1")],
        _approved_policy(),
        workflow_run_id="workflow-1",
        rule_set=_rules(CAPITAL_RULE),
        created_at_utc=NOW,
    )[0]

    assert result.recommendation == CapitalizationRecommendation.REVIEW
    assert result.unit_cost_meets_threshold is None
    assert "unitCost" in result.missing_inputs


def test_non_usd_line_requires_governed_exchange_rate() -> None:
    result = assess_capitalization_for_invoice(
        _package(
            [_line("line-1", "Equipment", unit_cost=1200, currency="EUR")],
            currency="EUR",
        ),
        [_task("line-1")],
        _approved_policy(),
        workflow_run_id="workflow-1",
        rule_set=_rules(CAPITAL_RULE),
        created_at_utc=NOW,
    )[0]

    assert result.recommendation == CapitalizationRecommendation.REVIEW
    assert result.unit_cost_meets_threshold is None
    assert "approvedExchangeRate" in result.missing_inputs


def test_region_outside_policy_scope_is_review() -> None:
    result = assess_capitalization_for_invoice(
        _package(
            [_line("line-1", "Equipment", currency="EUR")],
            country_region="Europe",
            currency="EUR",
        ),
        [_task("line-1")],
        _approved_policy(),
        workflow_run_id="workflow-1",
        rule_set=_rules(CAPITAL_RULE),
        created_at_utc=NOW,
    )[0]

    assert result.recommendation == CapitalizationRecommendation.REVIEW
    assert result.threshold_amount is None
    assert "approvedRegionalPolicy" in result.missing_inputs


def test_unresolved_task_retains_nature_and_threshold_partial_results() -> None:
    rule = CAPITAL_RULE.model_copy(
        update={
            "task_codes": [],
            "exact_descriptions": ["Equipment"],
        }
    )
    result = assess_capitalization_for_invoice(
        _package([_line("line-1", "Equipment", unit_cost=1200.0)]),
        [_task("line-1", status="REVIEW", task_code=None)],
        _approved_policy(),
        workflow_run_id="workflow-1",
        rule_set=_rules(rule),
        created_at_utc=NOW,
    )[0]

    assert result.recommendation == CapitalizationRecommendation.REVIEW
    assert result.nature_result == NatureResult.CAPITALIZABLE
    assert result.unit_cost_meets_threshold is True
    assert "suggestedTaskCode" in result.missing_inputs


def test_conflicting_approved_rules_return_review() -> None:
    conflicting_rule = CapitalizationNatureRule(
        rule_id="expense-equipment",
        version="1",
        nature_result=NatureResult.OPEX_BY_NATURE,
        task_codes=["1000"],
        policy_citation="Finance rule: expense equipment.",
    )

    result = assess_capitalization_for_invoice(
        _package([_line("line-1", "Equipment")]),
        [_task("line-1")],
        _approved_policy(),
        workflow_run_id="workflow-1",
        rule_set=_rules(CAPITAL_RULE, conflicting_rule),
        created_at_utc=NOW,
    )[0]

    assert result.recommendation == CapitalizationRecommendation.REVIEW
    assert result.nature_result == NatureResult.REVIEW
    assert result.outcome_category == "REVIEW_NATURE_RULE_CONFLICT"


def test_task_results_must_cover_each_invoice_line_once() -> None:
    package = _package(
        [
            _line("line-1", "Equipment"),
            _line("line-2", "Equipment"),
        ]
    )

    with pytest.raises(
        CapitalizationInputError,
        match="exactly one result",
    ):
        assess_capitalization_for_invoice(
            package,
            [_task("line-1")],
            _approved_policy(),
            workflow_run_id="workflow-1",
            rule_set=_rules(CAPITAL_RULE),
            created_at_utc=NOW,
        )
