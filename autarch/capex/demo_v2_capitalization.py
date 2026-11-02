"""Deterministic capitalization assessments for CapEx Demo V2."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .demo_v2_contracts import (
    BundlingDisposition,
    CapitalizationAssessment,
    CapitalizationRecommendation,
    NatureResult,
    TaskDetermination,
    TaskResolutionStatus,
)
from .demo_v2_normalization import (
    NormalizedInvoiceLine,
    NormalizedInvoicePackage,
)
from .demo_v2_reference import (
    ApprovalStatus,
    DemoPolicySnapshot,
    PolicySetting,
)


_SPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^a-z0-9]+")
_CONFIRMED_STATUSES = {
    ApprovalStatus.POLICY_SUPPORTED,
    ApprovalStatus.WORKFLOW_CONFIRMED,
}
_OPEN_PROJECT_STATUSES = {"OPEN", "ACTIVE"}
_CLOSED_PROJECT_STATUSES = {"CLOSED", "COMPLETE", "COMPLETED"}


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class _CapitalizationRuleModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )


class CapitalizationNatureRule(_CapitalizationRuleModel):
    """One approved exact-match operational nature rule."""

    rule_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    nature_result: Literal[
        NatureResult.CAPITALIZABLE,
        NatureResult.OPEX_BY_NATURE,
    ]
    task_codes: List[str] = Field(default_factory=list)
    exact_descriptions: List[str] = Field(default_factory=list)
    repair_maintenance_indicator: bool = False
    never_capitalize: bool = False
    policy_citation: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_rule(self) -> "CapitalizationNatureRule":
        if not self.task_codes and not self.exact_descriptions:
            raise ValueError(
                "a nature rule requires a taskCode or exactDescription"
            )
        if len(self.task_codes) != len(set(self.task_codes)):
            raise ValueError("taskCodes must not contain duplicates")
        normalized_descriptions = [
            _normalized_text(value)
            for value in self.exact_descriptions
        ]
        if len(normalized_descriptions) != len(
            set(normalized_descriptions)
        ):
            raise ValueError(
                "exactDescriptions must not contain normalized duplicates"
            )
        if (
            self.repair_maintenance_indicator
            or self.never_capitalize
        ) and self.nature_result != NatureResult.OPEX_BY_NATURE:
            raise ValueError(
                "repair/maintenance and never-capitalize rules must be OPEX"
            )
        return self


class CapitalizationRuleSet(_CapitalizationRuleModel):
    """Versioned operational nature rules supplied by finance."""

    rule_set_id: str = Field(min_length=1)
    approved: bool
    rules: List[CapitalizationNatureRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_rules(self) -> "CapitalizationRuleSet":
        rule_ids = [rule.rule_id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("rules must not contain duplicate ruleId values")
        return self


class CapitalizationInputError(ValueError):
    """Task and invoice inputs cannot form a deterministic assessment."""


def _normalized_text(value: str) -> str:
    return _SPACE.sub(
        " ",
        _NON_WORD.sub(" ", value.strip().lower()),
    ).strip()


def _settings_by_id(
    policy: DemoPolicySnapshot,
) -> Dict[str, PolicySetting]:
    return {
        setting.setting_id: setting
        for setting in policy.settings
    }


def _setting_confirmed(
    policy: DemoPolicySnapshot,
    settings: Mapping[str, PolicySetting],
    setting_id: str,
) -> bool:
    setting = settings.get(setting_id)
    return bool(
        setting is not None
        and setting.status in _CONFIRMED_STATUSES
        and setting_id not in policy.unresolved_confirmation_ids
    )


def _matching_rules(
    line: NormalizedInvoiceLine,
    task: TaskDetermination,
    rule_set: Optional[CapitalizationRuleSet],
    policy: DemoPolicySnapshot,
) -> List[CapitalizationNatureRule]:
    if (
        rule_set is None
        or not rule_set.approved
        or not policy.capitalization.operational_nature_rules_approved
    ):
        return []
    description = _normalized_text(line.description)
    task_code = task.suggested_task_code
    return [
        rule
        for rule in rule_set.rules
        if (
            task_code is not None
            and task_code in rule.task_codes
        )
        or description
        in {
            _normalized_text(value)
            for value in rule.exact_descriptions
        }
    ]


def _task_map(
    package: NormalizedInvoicePackage,
    determinations: Sequence[TaskDetermination],
) -> Dict[str, TaskDetermination]:
    line_ids = [line.line_id for line in package.lines]
    result_ids = [result.line_id for result in determinations]
    duplicate_ids = sorted(
        {
            line_id
            for line_id in result_ids
            if result_ids.count(line_id) > 1
        }
    )
    if duplicate_ids:
        raise CapitalizationInputError(
            "task determinations contain duplicate line IDs: "
            + ", ".join(duplicate_ids)
        )
    if set(line_ids) != set(result_ids):
        raise CapitalizationInputError(
            "task determinations must contain exactly one result per "
            "invoice line"
        )
    results = {result.line_id: result for result in determinations}
    for line_id, result in results.items():
        if (
            result.package_id != package.package_id
            or result.invoice_id != package.invoice_id
        ):
            raise CapitalizationInputError(
                f"task determination {line_id} does not belong to package "
                f"{package.package_id}"
            )
    return results


def _rule_evidence(
    rules: Sequence[CapitalizationNatureRule],
) -> List[str]:
    evidence: List[str] = []
    for rule in rules:
        evidence.extend(
            [
                f"rule:{rule.rule_id}@{rule.version}",
                rule.policy_citation,
            ]
        )
    return list(dict.fromkeys(evidence))


def _review_assessment(
    *,
    package: NormalizedInvoicePackage,
    line: NormalizedInvoiceLine,
    task: TaskDetermination,
    workflow_run_id: str,
    policy: DemoPolicySnapshot,
    created_at_utc: datetime,
    nature_result: NatureResult,
    repair_maintenance_indicator: bool,
    never_capitalize_hit: bool,
    unit_cost_meets_threshold: Optional[bool],
    threshold_amount: Optional[float],
    threshold_currency: Optional[str],
    outcome_category: str,
    missing_inputs: Sequence[str],
    review_reasons: Sequence[str],
    rationale: str,
    policy_citations: Sequence[str],
) -> CapitalizationAssessment:
    return CapitalizationAssessment(
        workflow_run_id=workflow_run_id,
        package_id=package.package_id,
        invoice_id=package.invoice_id,
        line_id=line.line_id,
        suggested_task_code=task.suggested_task_code,
        nature_result=nature_result,
        repair_maintenance_indicator=repair_maintenance_indicator,
        never_capitalize_hit=never_capitalize_hit,
        unit_cost_meets_threshold=unit_cost_meets_threshold,
        threshold_amount=threshold_amount,
        threshold_currency=threshold_currency,
        recommendation=CapitalizationRecommendation.REVIEW,
        outcome_category=outcome_category,
        bundling_disposition=BundlingDisposition.REVIEW,
        missing_inputs=list(dict.fromkeys(missing_inputs)),
        review_reasons=list(dict.fromkeys(review_reasons)),
        rationale=rationale,
        policy_citations=list(dict.fromkeys(policy_citations)),
        used_llm=False,
        policy_version=policy.policy_id,
        created_at_utc=created_at_utc,
    )


def _assess_line(
    package: NormalizedInvoicePackage,
    line: NormalizedInvoiceLine,
    task: TaskDetermination,
    policy: DemoPolicySnapshot,
    rule_set: Optional[CapitalizationRuleSet],
    *,
    workflow_run_id: str,
    created_at_utc: datetime,
) -> CapitalizationAssessment:
    settings = _settings_by_id(policy)
    citations = [f"policy:{policy.policy_id}"]
    missing_inputs: List[str] = []
    review_reasons: List[str] = []
    outcome_category = "REVIEW_UNRESOLVED_POLICY_OR_INPUT"

    if task.resolution_status != TaskResolutionStatus.RESOLVED:
        missing_inputs.append("suggestedTaskCode")
        review_reasons.append(
            "A governed task determination is required before "
            "capitalization."
        )
        outcome_category = "REVIEW_TASK_UNRESOLVED"

    rules = _matching_rules(line, task, rule_set, policy)
    nature_result = NatureResult.REVIEW
    repair_maintenance = False
    never_capitalize = False
    if not rules:
        citations.append("setting:nature-rule-library")
        missing_inputs.append("approvedNatureRule")
        review_reasons.append(
            "No approved operational nature rule matched this line."
        )
        if outcome_category == "REVIEW_UNRESOLVED_POLICY_OR_INPUT":
            outcome_category = "REVIEW_NATURE_UNRESOLVED"
    else:
        nature_results = {rule.nature_result for rule in rules}
        if len(nature_results) != 1:
            review_reasons.append(
                "Approved nature rules produced conflicting results."
            )
            if outcome_category == "REVIEW_UNRESOLVED_POLICY_OR_INPUT":
                outcome_category = "REVIEW_NATURE_RULE_CONFLICT"
        else:
            nature_result = next(iter(nature_results))
            repair_maintenance = any(
                rule.repair_maintenance_indicator
                for rule in rules
            )
            never_capitalize = any(
                rule.never_capitalize for rule in rules
            )
            if rule_set is not None:
                citations.append(f"ruleset:{rule_set.rule_set_id}")
            citations.extend(_rule_evidence(rules))

    threshold_amount: Optional[float] = None
    threshold_currency: Optional[str] = None
    unit_cost_meets_threshold: Optional[bool] = None
    threshold_missing_input: Optional[str] = None
    threshold_review_reason: Optional[str] = None
    threshold_outcome_category: Optional[str] = None
    region_matches = (
        _normalized_text(package.country_region)
        == _normalized_text("North America")
    )
    if region_matches:
        threshold_amount = policy.capitalization.base_threshold
        threshold_currency = policy.capitalization.currency
        citations.append("setting:base-threshold")
        if line.currency != policy.capitalization.currency:
            citations.append("setting:budget-exchange-rates")
            threshold_missing_input = "approvedExchangeRate"
            threshold_review_reason = (
                f"No governed conversion from {line.currency} to "
                f"{policy.capitalization.currency} is available."
            )
            threshold_outcome_category = "REVIEW_NON_USD"
        elif line.unit_cost is None:
            threshold_missing_input = "unitCost"
            threshold_review_reason = (
                "The source unit cost required by the configured threshold "
                "basis is missing."
            )
            threshold_outcome_category = "REVIEW_MISSING_UNIT_COST"
        else:
            unit_cost_meets_threshold = (
                line.unit_cost >= threshold_amount
            )
    else:
        missing_inputs.append("approvedRegionalPolicy")
        review_reasons.append(
            f"No approved capitalization policy was supplied for "
            f"{package.country_region}."
        )
        if outcome_category == "REVIEW_UNRESOLVED_POLICY_OR_INPUT":
            outcome_category = "REVIEW_REGION_UNSUPPORTED"

    if (
        nature_result != NatureResult.OPEX_BY_NATURE
        and threshold_missing_input
        and threshold_review_reason
    ):
        missing_inputs.append(threshold_missing_input)
        review_reasons.append(threshold_review_reason)
        if outcome_category == "REVIEW_UNRESOLVED_POLICY_OR_INPUT":
            outcome_category = (
                threshold_outcome_category
                or "REVIEW_THRESHOLD_INPUT"
            )

    if nature_result == NatureResult.OPEX_BY_NATURE:
        if review_reasons:
            return _review_assessment(
                package=package,
                line=line,
                task=task,
                workflow_run_id=workflow_run_id,
                policy=policy,
                created_at_utc=created_at_utc,
                nature_result=nature_result,
                repair_maintenance_indicator=repair_maintenance,
                never_capitalize_hit=never_capitalize,
                unit_cost_meets_threshold=unit_cost_meets_threshold,
                threshold_amount=threshold_amount,
                threshold_currency=threshold_currency,
                outcome_category=outcome_category,
                missing_inputs=missing_inputs,
                review_reasons=review_reasons,
                rationale=(
                    "The OPEX nature result is retained, but another required "
                    "governed input is unresolved."
                ),
                policy_citations=citations,
            )
        if never_capitalize:
            outcome_category = "OPEX_NEVER_CAPITALIZE"
        elif repair_maintenance:
            outcome_category = "OPEX_REPAIR_MAINTENANCE"
        else:
            outcome_category = "OPEX_BY_NATURE"
        return CapitalizationAssessment(
            workflow_run_id=workflow_run_id,
            package_id=package.package_id,
            invoice_id=package.invoice_id,
            line_id=line.line_id,
            suggested_task_code=task.suggested_task_code,
            nature_result=nature_result,
            repair_maintenance_indicator=repair_maintenance,
            never_capitalize_hit=never_capitalize,
            unit_cost_meets_threshold=unit_cost_meets_threshold,
            threshold_amount=threshold_amount,
            threshold_currency=threshold_currency,
            recommendation=CapitalizationRecommendation.OPEX,
            outcome_category=outcome_category,
            bundling_disposition=BundlingDisposition.NOT_ELIGIBLE,
            rationale=(
                "An approved deterministic nature rule requires expense "
                "treatment before the monetary threshold."
            ),
            policy_citations=citations,
            used_llm=False,
            policy_version=policy.policy_id,
            created_at_utc=created_at_utc,
        )

    if nature_result == NatureResult.CAPITALIZABLE:
        required_threshold_settings = (
            "threshold-equality",
            "threshold-basis",
        )
        unresolved_threshold_settings = [
            setting_id
            for setting_id in required_threshold_settings
            if not _setting_confirmed(
                policy,
                settings,
                setting_id,
            )
        ]
        if unresolved_threshold_settings:
            citations.extend(
                f"setting:{setting_id}"
                for setting_id in unresolved_threshold_settings
            )
            missing_inputs.extend(unresolved_threshold_settings)
            review_reasons.append(
                "Threshold equality and basis require finance confirmation."
            )
            if outcome_category == "REVIEW_UNRESOLVED_POLICY_OR_INPUT":
                outcome_category = "REVIEW_THRESHOLD_POLICY"

    if review_reasons or nature_result == NatureResult.REVIEW:
        return _review_assessment(
            package=package,
            line=line,
            task=task,
            workflow_run_id=workflow_run_id,
            policy=policy,
            created_at_utc=created_at_utc,
            nature_result=nature_result,
            repair_maintenance_indicator=repair_maintenance,
            never_capitalize_hit=never_capitalize,
            unit_cost_meets_threshold=unit_cost_meets_threshold,
            threshold_amount=threshold_amount,
            threshold_currency=threshold_currency,
            outcome_category=outcome_category,
            missing_inputs=missing_inputs,
            review_reasons=review_reasons,
            rationale=(
                "Deterministic partial results were retained, but the line "
                "cannot receive a governed CapEx/OpEx recommendation."
            ),
            policy_citations=citations,
        )

    if unit_cost_meets_threshold:
        return CapitalizationAssessment(
            workflow_run_id=workflow_run_id,
            package_id=package.package_id,
            invoice_id=package.invoice_id,
            line_id=line.line_id,
            suggested_task_code=task.suggested_task_code,
            nature_result=nature_result,
            repair_maintenance_indicator=False,
            never_capitalize_hit=False,
            unit_cost_meets_threshold=True,
            threshold_amount=threshold_amount,
            threshold_currency=threshold_currency,
            recommendation=CapitalizationRecommendation.CAPEX,
            outcome_category="CAPEX_MEETS_UNIT_THRESHOLD",
            bundling_disposition=BundlingDisposition.NOT_IN_SCOPE,
            rationale=(
                "The approved capitalizable nature rule matched and unit "
                "cost meets the governed threshold."
            ),
            policy_citations=citations,
            used_llm=False,
            policy_version=policy.policy_id,
            created_at_utc=created_at_utc,
        )

    if not _setting_confirmed(
        policy,
        settings,
        "below-threshold-exception",
    ):
        citations.append("setting:below-threshold-exception")
        return _review_assessment(
            package=package,
            line=line,
            task=task,
            workflow_run_id=workflow_run_id,
            policy=policy,
            created_at_utc=created_at_utc,
            nature_result=nature_result,
            repair_maintenance_indicator=False,
            never_capitalize_hit=False,
            unit_cost_meets_threshold=False,
            threshold_amount=threshold_amount,
            threshold_currency=threshold_currency,
            outcome_category="REVIEW_BELOW_THRESHOLD_EXCEPTION_POLICY",
            missing_inputs=["below-threshold-exception"],
            review_reasons=[
                "Below-threshold exception handling requires finance "
                "confirmation."
            ],
            rationale=(
                "The line is below the base threshold, but exception policy "
                "is unresolved."
            ),
            policy_citations=citations,
        )

    overlay = package.context_overlay
    if overlay is None:
        return _review_assessment(
            package=package,
            line=line,
            task=task,
            workflow_run_id=workflow_run_id,
            policy=policy,
            created_at_utc=created_at_utc,
            nature_result=nature_result,
            repair_maintenance_indicator=False,
            never_capitalize_hit=False,
            unit_cost_meets_threshold=False,
            threshold_amount=threshold_amount,
            threshold_currency=threshold_currency,
            outcome_category="REVIEW_BUNDLING_CONTEXT",
            missing_inputs=[
                "siteId",
                "projectId",
                "projectStatus",
                "bundlingInScope",
            ],
            review_reasons=[
                "Below-threshold treatment requires governed project and "
                "site context."
            ],
            rationale=(
                "The line is below threshold, but bundling context is "
                "unavailable."
            ),
            policy_citations=citations,
        )

    project_status = overlay.project_status.strip().upper()
    if not overlay.bundling_in_scope:
        bundling_disposition = BundlingDisposition.NOT_IN_SCOPE
        outcome_category = "OPEX_BELOW_THRESHOLD_NOT_IN_SCOPE"
    elif project_status in _CLOSED_PROJECT_STATUSES:
        bundling_disposition = BundlingDisposition.NOT_ELIGIBLE
        outcome_category = "OPEX_BELOW_THRESHOLD_CLOSED_PROJECT"
    elif project_status in _OPEN_PROJECT_STATUSES:
        bundling_disposition = BundlingDisposition.CANDIDATE
        outcome_category = "OPEX_POTENTIAL_FUTURE_BUNDLING"
    else:
        return _review_assessment(
            package=package,
            line=line,
            task=task,
            workflow_run_id=workflow_run_id,
            policy=policy,
            created_at_utc=created_at_utc,
            nature_result=nature_result,
            repair_maintenance_indicator=False,
            never_capitalize_hit=False,
            unit_cost_meets_threshold=False,
            threshold_amount=threshold_amount,
            threshold_currency=threshold_currency,
            outcome_category="REVIEW_PROJECT_STATUS",
            missing_inputs=["approvedProjectStatus"],
            review_reasons=[
                f"Project status {overlay.project_status!r} is not governed "
                "for deterministic bundling treatment."
            ],
            rationale=(
                "The line is below threshold, but project status cannot be "
                "evaluated deterministically."
            ),
            policy_citations=citations,
        )
    return CapitalizationAssessment(
        workflow_run_id=workflow_run_id,
        package_id=package.package_id,
        invoice_id=package.invoice_id,
        line_id=line.line_id,
        suggested_task_code=task.suggested_task_code,
        nature_result=nature_result,
        repair_maintenance_indicator=False,
        never_capitalize_hit=False,
        unit_cost_meets_threshold=False,
        threshold_amount=threshold_amount,
        threshold_currency=threshold_currency,
        recommendation=CapitalizationRecommendation.OPEX,
        outcome_category=outcome_category,
        bundling_disposition=bundling_disposition,
        rationale=(
            "The approved capitalizable nature rule matched, but unit cost "
            "is below the governed base threshold."
        ),
        policy_citations=citations,
        used_llm=False,
        policy_version=policy.policy_id,
        created_at_utc=created_at_utc,
    )


def assess_capitalization_for_invoice(
    package: NormalizedInvoicePackage,
    task_determinations: Sequence[TaskDetermination],
    policy: DemoPolicySnapshot,
    *,
    workflow_run_id: str,
    rule_set: Optional[CapitalizationRuleSet] = None,
    created_at_utc: Optional[datetime] = None,
) -> List[CapitalizationAssessment]:
    """Return one deterministic assessment per normalized invoice line."""
    task_by_line = _task_map(package, task_determinations)
    created_at = created_at_utc or datetime.now(timezone.utc)
    return [
        _assess_line(
            package,
            line,
            task_by_line[line.line_id],
            policy,
            rule_set,
            workflow_run_id=workflow_run_id,
            created_at_utc=created_at,
        )
        for line in package.lines
    ]


__all__ = [
    "CapitalizationInputError",
    "CapitalizationNatureRule",
    "CapitalizationRuleSet",
    "assess_capitalization_for_invoice",
]
