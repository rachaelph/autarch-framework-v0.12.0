from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from autarch.capex import demo_v2_adf_job as job
from autarch.capex import demo_v2_adf_sql as sql
from autarch.capex.demo_v2_adf_sql import persist_adf_results
from autarch.capex.demo_v2_asset_classes import is_eu_invoice
from autarch.capex.demo_v2_llm_showcase import _AuditedProvider
from autarch.capex.demo_v2_potential_tags import screen_potential_tags
from autarch.capex.demo_v2_review import ReviewStore
from autarch.capex.demo_v2_sql_policy import (
    SqlBudgetExchangeRate, SqlPolicyReferences, assess_governed_invoice,
    convert_line_to_usd, load_sql_policy_references,
)
from tests.test_capex_demo_v2_adf_integration import ConditionalModel, use_mock_render
from tests.test_capex_demo_v2_adf_job import run
from tests.test_capex_demo_v2_adf_sql import AtomicSqlHarness, native_helpers
from tests.test_capex_demo_v2_potential_tags import screened_input, TagModel
from tests.test_capex_demo_v2_task_suggestion import _line, _package


@pytest.fixture
def references():
    return SqlPolicyReferences.model_validate({
        "thresholds": [{
            "thresholdId": index, "governedThreshold": amount,
            "thresholdType": "FRIDAY_DEMO_UNIT_COST_GTE",
            "countryRegion": region, "currency": currency, "projectBudgetMin": None,
            "policyReference": "UNAPPROVED SYNTHETIC DEMO: mock policy, not finance approval",
            "effectiveFrom": "2020-01-01", "effectiveTo": "2030-12-31",
        } for index, (currency, amount, region) in enumerate([
            ("USD", 2500, "North America"), ("EUR", 2000, "Europe"), ("DKK", 12000, "Europe"),
        ], 1)],
        "exclusions": [{
            "exceptionId": 1, "itemCategory": "Routine maintenance", "keyword": "routine repair",
            "policyReference": "MOCK TEST ONLY: maintenance policy",
            "applicabilityRule": "Only ordinary maintenance without life/capacity improvement; "
                                 "qualifying replacement and major inspection need separate assessment.",
        }],
        "repairIndicators": [{
            "indicatorId": 1, "keyword": "routine repair", "category": "Maintenance",
            "signalStrength": 1, "policyReference": "MOCK TEST ONLY: review actual maintenance context.",
        }],
        "aroIndicators": [{
            "indicatorId": 1, "keyword": "tank inerting", "category": "Tank preparation",
            "policyReference": "MOCK TEST ONLY: require actual tank removal or closure context.",
            "requiresRemovalContext": True,
        }],
        "outcomeCategories": [{
            "categoryCode": code, "categoryName": code, "decisionCriteria": "MOCK TEST ONLY: contextual criteria.",
            "reviewConsiderations": "MOCK TEST ONLY: guidance, not established facts.",
            "suggestedAction": "MOCK TEST ONLY: analyst review.", "enabledForDemo": code != "CAPEX_POTENTIAL_BUNDLING",
        } for code in (
            "CAPEX", "NUVOLO_RM", "OPEX_BELOW_THRESHOLD", "OPEX_BY_NATURE",
            "CAPEX_POTENTIAL_BUNDLING", "CAPEX_MISSING_SITE", "ARO_TAG",
        )],
    })


class GovernedModel(ConditionalModel):
    def __init__(self, *, bad_category=False):
        super().__init__()
        self.bad_category = bad_category

    def complete(self, prompt, system=None):
        if prompt.startswith("Invoice request JSON:"):
            return super().complete(prompt, system)
        request = json.loads(prompt)
        if request["stage"] == "ACCOUNTING_DECISION" and "threshold" in request:
            self.requests.append(request)
            self.calls += 1
            threshold = request["threshold"]
            items = []
            for line in request["processingPackage"]["lines"]:
                if request.get("assessmentBasis") == "INVOICE_LINE_USD":
                    recommendation = request["standardThresholdDecisionsIfCapitalizable"][line["lineId"]]
                    missing = recommendation == "REVIEW"
                else:
                    missing = line["unitCost"] is None or threshold is None
                    recommendation = "REVIEW" if missing else (
                        "CAPEX" if line["unitCost"] >= float(threshold["governedThreshold"]) else "OPEX"
                    )
                items.append({
                    "lineId": line["lineId"], "natureResult": "CAPITALIZABLE",
                    "recommendation": recommendation, "repairMaintenanceIndicator": False,
                    "neverCapitalizeHit": False, "appliedExceptionIds": [],
                    "sourceQuote": line["description"], "rationale": "MOCK TEST ONLY: supplied SQL threshold basis.",
                    "policyCitations": [
                        f"sql:ref.CapitalizationThreshold:{threshold['thresholdId']}"
                    ] if threshold else request["allowedPolicyCitations"][:1],
                    "missingInputs": [
                        "Applicable USD policy or approved FX" if request.get("assessmentBasis") == "INVOICE_LINE_USD"
                        else "Printed unit cost or applicable policy"
                    ] if missing else [],
                    "reviewReasons": ["MOCK TEST ONLY: missing governed threshold facts."] if missing else [],
                })
            return json.dumps({"items": items})
        if request["stage"] == "ASSESSMENT_CATEGORY":
            self.requests.append(request)
            descriptions = {
                (package["packageId"], line["lineId"]): line["description"]
                for package in request["packages"] for line in package["lines"]
            }
            return json.dumps({"lines": [{
                "packageId": item["packageId"], "lineId": item["lineId"],
                "categorySuggestion": "ARO_TAG" if self.bad_category else (
                    item["allowedCategoryCodes"][0] if item["allowedCategoryCodes"] else None
                ),
                "rationale": "MOCK TEST ONLY: constrained primary category; bundling unassessed.",
                "sourceQuote": descriptions[(item["packageId"], item["lineId"])],
            } for item in request["lineConstraints"]]})
        return super().complete(prompt, system)


@pytest.fixture
def governed(monkeypatch, references):
    use_mock_render(monkeypatch)
    monkeypatch.setattr(sql, "load_adf_task_master", lambda connection, expected: expected)
    monkeypatch.setattr(sql, "load_adf_asset_class_master", lambda connection, expected: expected)
    monkeypatch.setattr(job, "load_sql_policy_references", lambda connection: references)
    captured = {}

    def capture(connection, **kwargs):
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(sql, "persist_adf_results", capture)
    written = {}
    model = GovernedModel()
    summary = run(
        model, written, connection=object(), fresh_pdf_extraction=True,
        allow_provisional_pdf_recommendations=True, demo_recommendations=True,
        governed_sql_references=True,
    )
    return summary, written, captured, model


def test_governed_batch_uses_sql_values_routes_all_invoices_and_defers_bundling(governed, references):
    summary, written, captured, model = governed
    assert summary["modelPackageCount"] == summary["packageCount"] == 10
    assert summary["taskSuggestionCount"] == 2 and summary["assetClassSuggestionCount"] == 8
    assert summary["assessmentCount"] == 10
    assert summary["bundleDecisionCount"] == summary["bundleGroupCount"] == 0
    assert summary["bundlingDeferred"] and summary["governedSqlReferences"]
    assert summary["sqlPolicyReferencesSha256"] == references.sha256
    evidence = written[summary["evidenceUri"]]
    assert evidence["demoAssumptions"]["nativeThresholds"] == {}
    assert evidence["demoAssumptions"]["missingUnitCostBasis"] == "NOT_REQUIRED_FOR_INVOICE_LINE_BASIS"
    assert evidence["demoAssumptions"]["fxConversion"]
    assert len([call for call in evidence["calls"] if call["stage"] == "ACCOUNTING_DECISION"]) == 10
    assert len([call for call in evidence["calls"] if call["stage"] == "ASSESSMENT_CATEGORY"]) == 1
    assert not any(call["stage"] == "BUNDLE_DECISION" for call in evidence["calls"])
    assert captured["model_result"].assessments == captured["result"].assessments
    for package in captured["packages"]:
        assessments = [item for item in captured["result"].assessments if item.package_id == package.package_id]
        tasks = [item for item in captured["result"].task_determinations if item.package_id == package.package_id]
        assert all(item.classification_type == ("ASSET_CLASS" if is_eu_invoice(package) else "TASK") for item in tasks)
        assert all(item.recommendation == "REVIEW" and item.unit_cost_meets_threshold is None for item in assessments)
        assert all(item.category_suggestion is None and item.threshold_amount is None for item in assessments)
        assert all(item.currency_conversion is not None for item in assessments)
        assert all("no native-currency fallback" in " ".join(item.review_reasons) for item in assessments)
        assert all(item.bundling_disposition == "REVIEW" for item in assessments)
    accounts = [request for request in model.requests if request.get("stage") == "ACCOUNTING_DECISION"]
    assert all(request["neverCapitalizeExceptions"][0]["applicabilityRule"] ==
               references.exclusions[0].applicability_rule for request in accounts)


@pytest.mark.parametrize("source,europe", [
    ("renamed-europe.xlsx", True), ("JC-named-north-america.ZIP", False),
])
def test_regional_routing_uses_source_format_not_jc_prefix(source, europe):
    package = _package([_line("1", "MOCK TEST ONLY: equipment")])
    package = package.model_copy(update={"source": package.source.model_copy(update={"file_name": source})})
    assert is_eu_invoice(package) is europe


@pytest.mark.parametrize("change", ["currency", "region", "expired", "ambiguous", "unsupported_basis", "project_budget"])
def test_missing_or_ambiguous_policy_is_not_replaced_by_a_currency_default(usd_references, change):
    package = _package([_line("1", "Equipment")])
    package = package.model_copy(update={
        "currency": "USD", "country_region": "US",
        "source": package.source.model_copy(update={"file_name": "na.zip"}),
    })
    payload = usd_references.model_dump(mode="json")
    row = payload["thresholds"][0]
    if change == "currency":
        row["currency"] = "CAD"
    elif change == "region":
        row["country_region"] = "Europe"
    elif change == "expired":
        row["effective_to"] = "2021-01-01"
    elif change == "ambiguous":
        payload["thresholds"].append({**row, "threshold_id": 99})
    elif change == "unsupported_basis":
        row["threshold_type"] = "UNSPECIFIED_EXPENDITURE"
    else:
        row["project_budget_min"] = None
    selected, reason = SqlPolicyReferences.model_validate(payload).threshold_for(
        package, date(2026, 11, 11), allow_demo=True,
    )
    assert selected is None and reason
    unapproved = usd_references.model_copy(update={"thresholds": [
        usd_references.thresholds[0].model_copy(update={"policy_reference": "UNAPPROVED MOCK policy"}),
    ]})
    selected, reason = unapproved.threshold_for(package, date(2026, 11, 11), allow_demo=False)
    assert selected is None and "opt-in" in reason


def test_reference_drift_aborts_before_sql_commit_or_completion(governed, monkeypatch, references):
    _, _, _, model = governed
    changed = references.model_copy(update={"thresholds": references.thresholds[:-1]})
    calls = iter([references, changed])
    monkeypatch.setattr(job, "load_sql_policy_references", lambda connection: next(calls))
    committed = []
    monkeypatch.setattr(sql, "persist_adf_results", lambda *args, **kwargs: committed.append(kwargs))
    written = {}
    with pytest.raises(ValueError, match="changed during inference"):
        run(GovernedModel(), written, connection=object(), fresh_pdf_extraction=True,
            allow_provisional_pdf_recommendations=True, demo_recommendations=True, governed_sql_references=True)
    assert not committed and not any(uri.endswith("/batch.json") for uri in written)
    assert next(value for uri, value in written.items() if uri.endswith("/sql-failure.json"))["status"] == "FAILED"


def test_incompatible_category_is_rejected_with_audited_feedback(governed):
    written = {}
    with pytest.raises(ValueError, match="ASSESSMENT_CATEGORY failed validation"):
        run(GovernedModel(bad_category=True), written, connection=object(), fresh_pdf_extraction=True,
            allow_provisional_pdf_recommendations=True, demo_recommendations=True, governed_sql_references=True)
    assert not any(uri.endswith("/batch.json") for uri in written)
    evidence = next(value for uri, value in written.items() if uri.endswith("/llm-evidence.json"))
    rejected = [call for call in evidence["calls"] if call["stage"] == "ASSESSMENT_CATEGORY"]
    assert len(rejected) == 2 and all(call["validationError"] for call in rejected)
    assert evidence["status"] != "SUCCEEDED"


@pytest.mark.parametrize(("field", "reference", "qualifies"), [
    ("Customer PO", "FWKD7819574", True),
    ("Customer PO", "FWKD123ABC", True),
    ("Customer PO", "FWKD7819574 / FWKD8121179", True),
    ("Customer PO", "XFWKD7819574", True),
    ("Customer PO", "FFX0001234", True),
    ("Customer PO", "FFXP0011831", True),
    ("Customer PO", "FFX", True),
    ("Customer PO", "FFX123ABC", True),
    ("Customer PO", "XFFX7819574", False),
    ("Customer PO", "PO reference FFX7819574", False),
    ("Work Order Number", "FFX7819574", False),
    ("Invoice Number", "FFX7819574", False),
    ("Work Order Number", "FWKD7819574", False),
    ("Invoice Number", "FWKD7819574", False),
    ("Customer PO", "841305", False),
])
@pytest.mark.parametrize("repair_indicator", [False, True])
def test_nuvolo_category_requires_qualifying_purchase_order_even_with_nuvolo_source_label(
    references, screened_input, field, reference, qualifies, repair_indicator,
):
    from autarch.capex.demo_v2_contracts import CapitalizationAssessment
    from autarch.capex.demo_v2_sql_policy import suggest_assessment_categories

    package, result, response = screened_input
    package = package.model_copy(update={
        "source_references": {field: reference},
        "source": package.source.model_copy(update={"source_system": "NUVOLO"}),
    })
    result = screen_potential_tags([package], result, TagModel(response))
    result = result.model_copy(update={"assessments": [CapitalizationAssessment(
        workflow_run_id=result.workflow_run_id, package_id=package.package_id, invoice_id=package.invoice_id,
        line_id=line.line_id, nature_result="OPEX_BY_NATURE", repair_maintenance_indicator=repair_indicator,
        never_capitalize_hit=False, recommendation="OPEX", outcome_category="SQL_GOVERNED_OPEX",
        bundling_disposition="NOT_ELIGIBLE", rationale="MOCK TEST ONLY: ordinary maintenance.",
        created_at_utc=package.created_at_utc,
    ) for line in package.lines]})

    requests = []

    class CategoryModel(GovernedModel):
        def complete(self, prompt, system=None):
            payload = json.loads(prompt)
            requests.append(payload)
            return json.dumps({"lines": [{
                "packageId": item["packageId"], "lineId": item["lineId"],
                "categorySuggestion": "NUVOLO_RM" if "NUVOLO_RM" in item["allowedCategoryCodes"] else "OPEX_BY_NATURE",
                "sourceQuote": next(line.description for line in package.lines if line.line_id == item["lineId"]),
                "rationale": "MOCK TEST ONLY: use the constrained supported category.",
            } for item in payload["lineConstraints"]]})

    actual = suggest_assessment_categories(
        [package], result, references,
        _AuditedProvider(CategoryModel(), [], deployment="MOCK_TEST_ONLY", stage="ASSESSMENT_CATEGORY"),
    )
    assert all((item.category_suggestion == "NUVOLO_RM") == qualifies for item in actual.assessments)
    assert all(item.recommendation == "OPEX" for item in actual.assessments)
    if qualifies:
        assert all(item["allowedCategoryCodes"] == ["NUVOLO_RM"] for item in requests[0]["lineConstraints"])
    assert actual.task_determinations == result.task_determinations
    assert all(item.repair_maintenance_indicator == repair_indicator for item in actual.assessments)


@pytest.mark.parametrize("missing", [False, True])
def test_nuvolo_category_cannot_silently_disappear_from_the_sql_catalog(governed, references, missing):
    from autarch.capex.demo_v2_sql_policy import suggest_assessment_categories

    _, _, captured, _ = governed
    first, *others = captured["packages"]
    packages = [first.model_copy(update={"source_references": {"Customer PO": "FWKD7819574"}}), *others]
    references = references.model_copy(update={"outcome_categories": [
        row.model_copy(update={"enabled_for_demo": False}) if row.category_code == "NUVOLO_RM" else row
        for row in references.outcome_categories if not (missing and row.category_code == "NUVOLO_RM")
    ]})
    model = GovernedModel()
    with pytest.raises(ValueError, match="enabled NUVOLO_RM"):
        suggest_assessment_categories(
            packages, captured["result"], references,
            _AuditedProvider(model, [], deployment="MOCK_TEST_ONLY", stage="ASSESSMENT_CATEGORY"),
        )
    assert model.requests == []


@pytest.mark.parametrize("corrupt", [False, True])
def test_categories_and_deferred_bundling_commit_atomically(usd_governed, native_helpers, tmp_path, corrupt):
    _, _, captured, _ = usd_governed
    path = tmp_path / "governed-review.db"
    ReviewStore(path).close()
    connection = AtomicSqlHarness(path)
    try:
        if corrupt:
            connection.db.execute("""
                CREATE TRIGGER capex.corrupt_category AFTER UPDATE OF CategorySuggestion ON Assessment
                BEGIN UPDATE Assessment SET CategorySuggestion='CORRUPTED' WHERE rowid=NEW.rowid; END;
            """)
            with pytest.raises(ValueError, match="exact category suggestion"):
                persist_adf_results(connection, **captured)
            assert connection.db.execute("SELECT COUNT(*) FROM capex.Assessment").fetchone()[0] == 0
            assert connection.commits == 0 and connection.rollbacks >= 1
        else:
            counts = persist_adf_results(connection, **captured)
            actual = connection.db.execute("""
                SELECT PackageId, LineId, CategorySuggestion FROM capex.Assessment
            """).fetchall()
            assert set(actual) == {
                (item.package_id, item.line_id, item.category_suggestion) for item in captured["result"].assessments
            }
            assert counts["modelBundleDecisions"] == 0 and counts["assessments"] == 10
            assert connection.commits == 1 and connection.rollbacks == 0
        assert connection.cursors_open == 0
    finally:
        connection.close()


def test_sql_mode_rejects_missing_connection_before_any_model_call():
    model = GovernedModel()
    with pytest.raises(ValueError, match="require --sql-persist"):
        run(model, {}, governed_sql_references=True)
    assert model.calls == 0


@pytest.mark.parametrize("empty", [False, True])
def test_reference_loader_validates_real_row_shape_and_rolls_back_read_transaction(references, empty):
    class Connection:
        query = ""
        rollbacks = 0
        closed = False

        def cursor(self):
            return self

        def execute(self, query):
            self.query = query

        def fetchall(self):
            tables = {
                "ref.CapitalizationThreshold": references.thresholds,
                "ref.NeverCapitalizeException": references.exclusions,
                "ref.RepairMaintenanceIndicator": references.repair_indicators,
                "ref.AROIndicator": references.aro_indicators,
                "ref.AssessmentOutcomeCategory": references.outcome_categories,
                "ref.BudgetExchangeRate": references.budget_exchange_rates,
            }
            rows = next(rows for table, rows in tables.items() if f"FROM {table} " in self.query)
            return [(json.dumps([] if empty else [
                row.model_dump(mode="json", by_alias=True) for row in rows
            ]),)]

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed = True

    connection = Connection()
    if empty:
        with pytest.raises(ValueError, match="at least 1 item"):
            load_sql_policy_references(connection)
    else:
        assert load_sql_policy_references(connection) == references
    assert connection.closed and connection.rollbacks == 1


@pytest.mark.parametrize("citation", [
    "sql:ref.AROIndicator:1", "sql:ref.AROIndicator:999", "sql:ref.RepairMaintenanceIndicator:1", None,
])
def test_sql_tag_screening_requires_the_correct_reference_and_keeps_context(
    references, screened_input, citation,
):
    package, result, response = screened_input
    tag = response["lines"][1]["potentialTags"][0]
    tag["referenceIds"] = [citation] if citation else []
    response["lines"][1]["potentialTags"] = [tag]
    provider = TagModel(response)
    references = references.model_copy(update={"aro_indicators": [
        references.aro_indicators[0].model_copy(update={"keyword": "Temporary fencing"}),
    ]})
    if citation == "sql:ref.AROIndicator:1":
        tagged = screen_potential_tags([package], result, provider, sql_references=references)
        assert tagged.task_determinations[1].potential_tags[0].reference_ids == [citation]
        assert tagged.assessments == result.assessments
    else:
        with pytest.raises(ValueError, match="applicable active SQL indicator"):
            screen_potential_tags([package], result, provider, sql_references=references)


@pytest.mark.parametrize("failure", ["unit_cost", "exclusion_identity", "source_quote"])
def test_governed_accounting_rejects_unsupported_facts_and_rule_claims(governed, references, failure):
    _, _, captured, _ = governed
    package = next(package for package in captured["packages"] if is_eu_invoice(package))
    tasks = [item for item in captured["result"].task_determinations if item.package_id == package.package_id]

    class InvalidModel(GovernedModel):
        def complete(self, prompt, system=None):
            response = json.loads(super().complete(prompt, system))
            for item in response["items"]:
                if failure == "unit_cost":
                    item.update(recommendation="CAPEX", missingInputs=[], reviewReasons=[])
                elif failure == "exclusion_identity":
                    item.update(natureResult="OPEX_BY_NATURE", recommendation="OPEX",
                                neverCapitalizeHit=True, appliedExceptionIds=[], missingInputs=[], reviewReasons=[])
                else:
                    item["sourceQuote"] = "An invented description not on this invoice"
            return json.dumps(response)

    calls = []
    provider = _AuditedProvider(
        InvalidModel(), calls, deployment="gpt-5.1", stage="ACCOUNTING_DECISION", package_id=package.package_id,
    )
    with pytest.raises(ValueError, match="failed validation after two responses"):
        assess_governed_invoice(package, tasks, references, provider, captured["request"], allow_demo=True)
    assert len(calls) == 2 and all(call.validation_error for call in calls)
    assert all(line.unit_cost is None for line in package.lines)


@pytest.fixture
def usd_references(references):
    payload = references.model_dump(mode="json")
    payload["thresholds"] = [{
        "threshold_id": 10, "governed_threshold": "1000",
        "threshold_type": "USD_BUDGET_EXPENDITURE", "country_region": "Global",
        "currency": "USD", "project_budget_min": "30000", "minimum_site_amount": "300",
        "policy_version": "MOCK-USD-POLICY",
        "policy_reference": "MOCK TEST ONLY: USD expenditure policy and project exception.",
        "effective_from": "2026-01-01", "effective_to": "2026-12-31",
    }]
    return SqlPolicyReferences.model_validate(payload)


def _budget_rate(**changes):
    return SqlBudgetExchangeRate.model_validate({
        "budgetExchangeRateId": 1, "sourceCurrency": "EUR", "targetCurrency": "USD",
        "usdPerSourceUnit": "1.25", "countryRegion": "Global", "budgetPeriod": "MOCK-2026",
        "rateVersion": "MOCK-V1", "sourceReference": "MOCK TEST ONLY, not a Circle K rate",
        "effectiveFrom": "2026-01-01", "effectiveTo": "2026-12-31",
        "approvalStatus": "APPROVED", "approvedBy": "MOCK", "approvedAtUtc": "2026-01-01T00:00:00",
        **changes,
    })


def _f27_budget_rates():
    script = (Path(__file__).resolve().parents[1] / "sql" / "data" /
              "012_import_f27_budget_exchange_rates.sql").read_text(encoding="utf-8")
    schedule = re.findall(r"\('([A-Z]{3})',(\d+\.\d{4})\)", script)
    dates = dict(re.findall(r"DECLARE @(EffectiveFrom|EffectiveTo) date='(\d{4}-\d{2}-\d{2})';", script))
    return [
        _budget_rate(
            budgetExchangeRateId=index, sourceCurrency=currency, usdPerSourceUnit=amount,
            budgetPeriod="FY27", rateVersion="F27 FX Rate",
            effectiveFrom=dates["EffectiveFrom"], effectiveTo=dates["EffectiveTo"],
            sourceReference="User-supplied F27 schedule and dates; MOCK approval for tests only",
        )
        for index, (currency, amount) in enumerate(schedule, 1) if currency != "USD"
    ]


@pytest.mark.parametrize("currency,expected_usd", [
    ("USD", "1000"), ("EUR", "1166.2"), ("CAD", "724.3"), ("CHF", "1240.8"),
    ("DKK", "155.8"), ("GBP", "1346.3"), ("HKD", "128.2"), ("INR", "11.4"),
    ("NOK", "100.3"), ("PLN", "274"), ("SEK", "106.3"),
])
def test_f27_import_rates_multiply_source_amounts_and_preserve_provenance(
    usd_references, currency, expected_usd,
):
    line = _line("1", "Equipment").model_copy(update={
        "currency": currency, "unit_cost": None, "quantity": None, "line_amount": 1000,
    })
    package = _package([line]).model_copy(update={"currency": currency})
    references = usd_references.model_copy(update={"budget_exchange_rates": _f27_budget_rates()})
    conversion = convert_line_to_usd(package, line, references, date(2026, 11, 12))
    assert conversion.line_amount_usd == Decimal(expected_usd)
    assert conversion.original_currency == currency and conversion.original_line_amount == 1000
    assert conversion.original_unit_cost is None and conversion.unit_cost_usd is None
    assert conversion.rate_version == (None if currency == "USD" else "F27 FX Rate")


@pytest.mark.parametrize("as_of,eligible", [
    (date(2026, 4, 26), False), (date(2026, 4, 27), True),
    (date(2027, 4, 25), True), (date(2027, 4, 26), False),
])
def test_f27_rates_apply_only_within_inclusive_fiscal_dates(usd_references, as_of, eligible):
    line = _line("1", "Equipment").model_copy(update={
        "currency": "DKK", "line_amount": 38014.39, "unit_cost": None, "quantity": None,
    })
    package = _package([line]).model_copy(update={"currency": "DKK"})
    references = usd_references.model_copy(update={"budget_exchange_rates": _f27_budget_rates()})
    conversion = convert_line_to_usd(package, line, references, as_of)
    assert conversion.original_line_amount == Decimal("38014.39")
    if eligible:
        assert conversion.conversion_status == "CONVERTED"
        assert conversion.line_amount_usd == Decimal("5922.641962")
    else:
        assert conversion.conversion_status == "REVIEW" and conversion.review_reason
        assert conversion.line_amount_usd is None and conversion.rate_to_usd is None


def test_budget_conversion_preserves_source_and_missing_unit_price(usd_references):
    line = _line("1", "Equipment").model_copy(update={
        "currency": "EUR", "unit_cost": None, "quantity": None, "line_amount": 800.03,
    })
    package = _package([line]).model_copy(update={"currency": "EUR"})
    references = usd_references.model_copy(update={"budget_exchange_rates": [_budget_rate()]})
    conversion = convert_line_to_usd(package, line, references, date(2026, 11, 12))
    assert conversion.line_amount_usd == Decimal("1000.0375000000")
    assert conversion.original_line_amount == Decimal("800.03")
    assert conversion.unit_cost_usd is None and conversion.original_unit_cost is None
    assert conversion.rate_to_usd == Decimal("1.25") and conversion.budget_exchange_rate_id == 1
    assert conversion.budget_period == "MOCK-2026" and conversion.rate_version == "MOCK-V1"
    assert line.line_amount == 800.03 and line.currency == "EUR" and line.quantity is None


@pytest.mark.parametrize("problem", ["missing", "pending", "expired", "ambiguous", "scope", "currency_mismatch"])
def test_budget_rate_failures_require_review_without_inventing_usd_amounts(usd_references, problem):
    line = _line("1", "Equipment").model_copy(update={"currency": "EUR"})
    package = _package([line]).model_copy(update={"currency": "EUR"})
    rates = [_budget_rate()]
    if problem == "missing":
        rates = []
    elif problem == "pending":
        rates = [_budget_rate(approvalStatus="PENDING")]
    elif problem == "expired":
        rates = [_budget_rate(effectiveTo="2026-06-30")]
    elif problem == "ambiguous":
        rates.append(_budget_rate(budgetExchangeRateId=2, rateVersion="MOCK-V2"))
    elif problem == "scope":
        rates = [_budget_rate(countryRegion="Canada")]
    else:
        package = package.model_copy(update={"currency": "DKK"})
    conversion = convert_line_to_usd(
        package, line, usd_references.model_copy(update={"budget_exchange_rates": rates}),
        date(2026, 11, 12),
    )
    assert conversion.conversion_status == "REVIEW" and conversion.review_reason
    assert conversion.original_currency == "EUR" and conversion.original_line_amount == 100
    assert conversion.line_amount_usd is None and conversion.rate_to_usd is None


def test_usd_identity_conversion_needs_no_budget_rate(usd_references):
    package = _package([_line("1", "Equipment")])
    conversion = convert_line_to_usd(package, package.lines[0], usd_references, date(2026, 11, 12))
    assert conversion.rate_to_usd == 1 and conversion.budget_exchange_rate_id is None
    assert conversion.unit_cost_usd == conversion.original_unit_cost == 100
    assert conversion.line_amount_usd == conversion.original_line_amount == 100


@pytest.mark.parametrize("amount,expected_usd", [
    (0, "0.0000000000"), (1e-10, "0.0000000001"),
])
def test_conversion_json_uses_exact_fixed_point_for_sql_decimal_readers(
    usd_references, amount, expected_usd,
):
    line = _line("1", "Equipment").model_copy(update={"line_amount": amount, "unit_cost": amount})
    package = _package([line])
    conversion = convert_line_to_usd(package, line, usd_references, date(2026, 11, 12))
    payload = json.loads(conversion.model_dump_json(by_alias=True))
    assert payload["lineAmountUsd"] == payload["unitCostUsd"] == expected_usd
    for field in ("originalLineAmount", "originalUnitCost", "rateToUsd"):
        assert "e" not in payload[field].lower()
        assert Decimal(payload[field]) == Decimal(str(amount if field != "rateToUsd" else 1))


@pytest.fixture
def usd_governed(monkeypatch, usd_references, request):
    if getattr(request, "param", False):
        usd_references = usd_references.model_copy(update={"budget_exchange_rates": _f27_budget_rates()})
    use_mock_render(monkeypatch)
    monkeypatch.setattr(sql, "load_adf_task_master", lambda connection, expected: expected)
    monkeypatch.setattr(sql, "load_adf_asset_class_master", lambda connection, expected: expected)
    monkeypatch.setattr(job, "load_sql_policy_references", lambda connection: usd_references)
    captured = {}
    monkeypatch.setattr(sql, "persist_adf_results", lambda connection, **kwargs: captured.update(kwargs) or {})
    written = {}
    model = GovernedModel()
    summary = run(
        model, written, connection=object(), fresh_pdf_extraction=True,
        allow_provisional_pdf_recommendations=True, demo_recommendations=True,
        governed_sql_references=True,
    )
    return summary, written, captured, model


@pytest.mark.parametrize("usd_governed", [False, True], indirect=True, ids=["missing-rates", "f27-rates"])
def test_usd_policy_routes_all_currencies_without_native_fallback_or_fake_project_totals(usd_governed):
    summary, written, captured, model = usd_governed
    assert summary["modelPackageCount"] == 10
    for package in captured["packages"]:
        assessments = [a for a in captured["result"].assessments if a.package_id == package.package_id]
        for item in assessments:
            assert item.threshold_currency == "USD" and item.threshold_amount == 1000
            assert item.currency_conversion is not None
            assert item.unit_cost_meets_threshold is None
            amount = item.currency_conversion.line_amount_usd
            expected = "REVIEW" if amount is None else ("CAPEX" if amount >= 1000 else "OPEX")
            assert item.recommendation == expected
            assert item.line_amount_usd_meets_threshold == (amount >= 1000 if amount is not None else None)
            assert item.category_suggestion == (
                "CAPEX" if expected == "CAPEX" else "OPEX_BELOW_THRESHOLD" if expected == "OPEX" else None
            )
            assert item.bundling_disposition == "REVIEW"
    for request in [r for r in model.requests if r.get("stage") == "ACCOUNTING_DECISION"]:
        assert request["threshold"]["minimumSiteAmount"] == "300"
        assert request["threshold"]["projectBudgetMin"] == "30000"
        assert request["assessmentBasis"] == "INVOICE_LINE_USD" and request["currencyConversions"]
        assert request["projectExceptionStatus"] == "NOT_EVALUATED_NO_AUTHORITATIVE_SITE_PROJECT_CONTEXT"
    evidence = written[summary["evidenceUri"]]
    assert evidence["demoAssumptions"]["nativeThresholds"] == {}
    assert evidence["demoAssumptions"]["assessmentBasis"] == "INVOICE_LINE_USD"
    assert evidence["demoAssumptions"]["missingUnitCostBasis"] == "NOT_REQUIRED_FOR_INVOICE_LINE_BASIS"
    assert not written[summary["reviewUri"]]["policySnapshots"]


def test_expired_usd_policy_does_not_fall_back_to_legacy_native_policy(usd_references, references):
    expired = usd_references.thresholds[0].model_copy(update={"effective_to": date(2026, 6, 30)})
    mixed = references.model_copy(update={"thresholds": [*references.thresholds, expired]})
    package = _package([_line("1", "Equipment")])
    threshold, reason = mixed.threshold_for(package, date(2026, 11, 12), allow_demo=True)
    assert threshold is None and reason


def test_native_only_policy_still_converts_but_cannot_authorize_threshold_classification(
    usd_governed, references,
):
    _, _, captured, _ = usd_governed
    package = captured["packages"][0].model_copy(update={"currency": "EUR", "lines": [
        line.model_copy(update={"currency": "EUR", "line_amount": 5000, "unit_cost": 5000})
        for line in captured["packages"][0].lines
    ]})
    tasks = [item for item in captured["result"].task_determinations if item.package_id == package.package_id]
    references = references.model_copy(update={"budget_exchange_rates": [_budget_rate()]})
    model = GovernedModel()
    assessments, _ = assess_governed_invoice(
        package, tasks, references,
        _AuditedProvider(model, [], deployment="MOCK_TEST_ONLY", stage="ACCOUNTING_DECISION"),
        captured["request"], allow_demo=True,
    )
    for assessment in assessments:
        assert assessment.currency_conversion.line_amount_usd == 6250
        assert assessment.currency_conversion.original_line_amount == 5000
        assert assessment.recommendation == "REVIEW"
        assert assessment.threshold_amount is None and assessment.threshold_currency is None
        assert assessment.unit_cost_meets_threshold is None
        assert assessment.line_amount_usd_meets_threshold is None
        assert "no native-currency fallback" in " ".join(assessment.review_reasons)


@pytest.mark.parametrize("country", ["CA", "CAN", " Canada "])
def test_canadian_invoice_cannot_use_us_catalog_even_with_governed_sql_or_demo_opt_in(
    usd_governed, monkeypatch, country,
):
    from autarch.capex import demo_v2_adf_extraction as extraction

    _, _, captured, _ = usd_governed
    original = extraction.extract_adf_pdfs
    canadian_ids = []

    def extract_canadian(**kwargs):
        batch = original(**kwargs)
        first = batch.packages[0]
        batch.packages[0] = first.model_copy(update={"country_region": country})
        canadian_ids.append(first.package_id)
        return batch

    monkeypatch.setattr(extraction, "extract_adf_pdfs", extract_canadian)
    written = {}
    summary = run(
        GovernedModel(), written, connection=object(), fresh_pdf_extraction=True,
        allow_provisional_pdf_recommendations=True, demo_recommendations=True,
        governed_sql_references=True,
    )
    tasks = [item for item in captured["result"].task_determinations if item.package_id in canadian_ids]
    assert tasks and all(item.resolution_status == "REVIEW" for item in tasks)
    assert all(item.suggested_task_code is None and item.suggested_asset_class is None for item in tasks)
    assert all("MACS or CTI" in item.unresolved_reason for item in tasks)
    assert summary["modelPackageCount"] == 9
    calls = written[summary["evidenceUri"]]["calls"]
    assert not any(call.get("packageId") in canadian_ids and call["stage"] in {
        "TASK_RETRIEVAL", "TASK_SUGGESTION", "ASSET_CLASS_SUGGESTION", "ACCOUNTING_DECISION",
    } for call in calls)
    assessments = [item for item in captured["result"].assessments if item.package_id in canadian_ids]
    assert all(item.recommendation == "REVIEW" and item.currency_conversion is not None for item in assessments)


def test_extraction_review_lines_keep_currency_evidence_and_persist_with_model_lines(
    usd_governed, monkeypatch, native_helpers, tmp_path,
):
    from autarch.capex import demo_v2_adf_extraction as extraction

    _, _, captured, _ = usd_governed
    original = extraction.extract_adf_pdfs
    review_ids = []

    def extract_with_review(**kwargs):
        batch = original(**kwargs)
        package_id = next(iter(batch.documents))
        batch.documents[package_id]["blockingReviewReasons"] = ["MOCK TEST ONLY: required header unreadable."]
        review_ids.append(package_id)
        return batch

    monkeypatch.setattr(extraction, "extract_adf_pdfs", extract_with_review)
    written = {}
    summary = run(
        GovernedModel(), written, connection=object(), fresh_pdf_extraction=True,
        allow_provisional_pdf_recommendations=True, demo_recommendations=True, governed_sql_references=True,
    )
    result = captured["result"]
    review = [item for item in result.assessments if item.package_id in review_ids]
    assert review and all(item.outcome_category == "REFERENCE_OR_EXTRACTION_REVIEW" for item in review)
    assert all(item.recommendation == "REVIEW" and item.currency_conversion is not None for item in review)
    assert all(item.currency_conversion.rate_to_usd == 1 for item in review)
    assert all(item.currency_conversion is not None for item in result.assessments)
    assert summary["assessmentCount"] == 10 and summary["modelPackageCount"] == 9
    path = tmp_path / "review-with-currency.db"
    ReviewStore(path).close()
    connection = AtomicSqlHarness(path)
    try:
        persist_adf_results(connection, **captured)
        assert connection.db.execute("SELECT COUNT(*) FROM capex.InvoiceLineAmount").fetchone()[0] == 10
        assert connection.commits == 1
    finally:
        connection.close()


def test_final_category_cannot_use_a_legacy_native_unit_cost_flag(references, screened_input):
    from autarch.capex.demo_v2_contracts import CapitalizationAssessment
    from autarch.capex.demo_v2_sql_policy import suggest_assessment_categories

    package, result, _ = screened_input
    result = result.model_copy(update={"assessments": [
        CapitalizationAssessment(
            workflow_run_id=result.workflow_run_id, package_id=package.package_id, invoice_id=package.invoice_id,
            line_id=line.line_id, nature_result="CAPITALIZABLE", repair_maintenance_indicator=False,
            never_capitalize_hit=False, unit_cost_meets_threshold=False,
            recommendation="OPEX", outcome_category="LEGACY_OPEX", bundling_disposition="NOT_ELIGIBLE",
            rationale="MOCK TEST ONLY: legacy native-unit evidence is not a USD comparison.",
            created_at_utc=package.created_at_utc,
        ) for line in package.lines
    ]})
    actual = suggest_assessment_categories(
        [package], result, references,
        _AuditedProvider(GovernedModel(), [], deployment="MOCK_TEST_ONLY", stage="ASSESSMENT_CATEGORY"),
    )
    assert all(item.category_suggestion is None for item in actual.assessments)


@pytest.mark.parametrize("currency,amount,unit_cost,usd_amount,recommendation", [
    ("USD", 0, None, "0", "OPEX"),
    ("USD", 999.99, None, "999.99", "OPEX"),
    ("USD", 1000, None, "1000", "CAPEX"),
    ("USD", 1000.01, None, "1000.01", "CAPEX"),
    ("USD", 1000, 10, "1000", "CAPEX"),
    ("EUR", 900, None, "1049.58", "CAPEX"),
    ("DKK", 1000, None, "155.8", "OPEX"),
    ("DKK", 38014.39, None, "5922.641962", "CAPEX"),
])
def test_usd_threshold_uses_independent_converted_line_total_not_unit_or_native_amount(
    usd_governed, usd_references, currency, amount, unit_cost, usd_amount, recommendation,
):
    _, _, captured, _ = usd_governed
    package = captured["packages"][0]
    package = package.model_copy(update={"currency": currency, "lines": [
        line.model_copy(update={
            "currency": currency, "line_amount": amount, "unit_cost": unit_cost,
            "quantity": amount / unit_cost if unit_cost is not None else None,
        }) for line in package.lines
    ]})
    tasks = [t for t in captured["result"].task_determinations if t.package_id == package.package_id]
    provider = _AuditedProvider(
        GovernedModel(), [], deployment="gpt-5.1",
        stage="ACCOUNTING_DECISION", package_id=package.package_id,
    )
    assessments, model_batch = assess_governed_invoice(
        package, tasks, usd_references.model_copy(update={"budget_exchange_rates": _f27_budget_rates()}),
        provider, captured["request"], allow_demo=True,
    )
    for assessment, model_item in zip(assessments, model_batch.items, strict=True):
        assert assessment.currency_conversion.line_amount_usd == Decimal(usd_amount)
        assert assessment.recommendation == model_item.recommendation == recommendation
        assert assessment.line_amount_usd_meets_threshold == (recommendation == "CAPEX")
        assert assessment.unit_cost_meets_threshold is None
        assert not assessment.missing_inputs and not assessment.review_reasons
        assert assessment.bundling_disposition == "REVIEW" and assessment.bundle_group_id is None
        assert "independent invoice line USD total" in assessment.rationale


@pytest.mark.parametrize("nature,recommendation", [("OPEX_BY_NATURE", "OPEX"), ("REVIEW", "REVIEW")])
def test_above_threshold_does_not_override_expense_nature_or_unresolved_eligibility(
    usd_governed, usd_references, nature, recommendation,
):
    _, _, captured, _ = usd_governed
    package = next(p for p in captured["packages"] if p.currency == "USD")
    package = package.model_copy(update={"lines": [
        line.model_copy(update={"line_amount": 5000, "unit_cost": None}) for line in package.lines
    ]})
    tasks = [t for t in captured["result"].task_determinations if t.package_id == package.package_id]

    class NatureModel(GovernedModel):
        def complete(self, prompt, system=None):
            response = json.loads(super().complete(prompt, system))
            for item in response["items"]:
                item.update(natureResult=nature, recommendation=recommendation, missingInputs=[],
                            reviewReasons=["Unclear asset attribution"] if nature == "REVIEW" else [])
            return json.dumps(response)

    provider = _AuditedProvider(
        NatureModel(), [], deployment="gpt-5.1",
        stage="ACCOUNTING_DECISION", package_id=package.package_id,
    )
    assessments, _ = assess_governed_invoice(
        package, tasks, usd_references, provider, captured["request"], allow_demo=True,
    )
    assert all(a.recommendation == recommendation and a.line_amount_usd_meets_threshold is True
               for a in assessments)


@pytest.mark.parametrize("recommendation", ["OPEX", "REVIEW", "CAPEX"])
def test_cited_never_capitalize_rule_takes_precedence_over_above_threshold_usd_amount(
    usd_governed, usd_references, recommendation,
):
    _, _, captured, _ = usd_governed
    package = next(p for p in captured["packages"] if p.currency == "USD")
    package = package.model_copy(update={"lines": [
        line.model_copy(update={
            "description": "MOCK TEST ONLY: ordinary routine repair without asset replacement "
                           "or useful-life/capacity improvement.",
            "line_amount": 5000, "unit_cost": None,
        }) for line in package.lines
    ]})
    tasks = [t for t in captured["result"].task_determinations if t.package_id == package.package_id]

    class ExclusionModel(GovernedModel):
        def complete(self, prompt, system=None):
            response = json.loads(super().complete(prompt, system))
            for item in response["items"]:
                item.update(
                    natureResult="CAPITALIZABLE" if recommendation == "CAPEX" else "OPEX_BY_NATURE",
                    recommendation=recommendation, neverCapitalizeHit=True, appliedExceptionIds=[1],
                    missingInputs=[], reviewReasons=["Accounting review"] if recommendation == "REVIEW" else [],
                    rationale="MOCK TEST ONLY: routine maintenance; no replacement or qualifying improvement.",
                    policyCitations=item["policyCitations"] + [usd_references.exclusions[0].citation],
                )
            return json.dumps(response)

    model = ExclusionModel()
    provider = _AuditedProvider(
        model, [], deployment="gpt-5.1",
        stage="ACCOUNTING_DECISION", package_id=package.package_id,
    )
    if recommendation == "CAPEX":
        with pytest.raises(ValueError, match="repair/maintenance or exclusions require"):
            assess_governed_invoice(
                package, tasks, usd_references, provider, captured["request"], allow_demo=True,
            )
        assert model.calls == 2
    else:
        assessments, batch = assess_governed_invoice(
            package, tasks, usd_references, provider, captured["request"], allow_demo=True,
        )
        assert all(a.recommendation == recommendation and a.never_capitalize_hit
                   and a.line_amount_usd_meets_threshold is True for a in assessments)
        assert all(item.never_capitalize_hit and item.recommendation == recommendation for item in batch.items)


@pytest.mark.parametrize("unavailable", ["expired-policy", "ambiguous-policy", "expired-fx", "pending-fx", "ambiguous-fx"])
def test_usd_line_comparison_requires_unambiguous_effective_policy_and_approved_fx(
    usd_governed, usd_references, unavailable,
):
    _, _, captured, _ = usd_governed
    package = captured["packages"][0]
    package = package.model_copy(update={"currency": "EUR", "lines": [
        line.model_copy(update={"currency": "EUR", "line_amount": 5000, "unit_cost": None})
        for line in package.lines
    ]})
    thresholds = list(usd_references.thresholds)
    rates = [_budget_rate()]
    if unavailable == "expired-policy":
        thresholds = [thresholds[0].model_copy(update={"effective_to": date(2026, 1, 2)})]
    elif unavailable == "ambiguous-policy":
        thresholds.append(thresholds[0].model_copy(update={"threshold_id": 99}))
    elif unavailable == "expired-fx":
        rates = [_budget_rate(effectiveTo="2026-01-02")]
    elif unavailable == "pending-fx":
        rates = [_budget_rate(approvalStatus="PENDING", approvedBy=None, approvedAtUtc=None)]
    else:
        rates.append(_budget_rate(budgetExchangeRateId=2))
    references = usd_references.model_copy(update={"thresholds": thresholds, "budget_exchange_rates": rates})
    tasks = [t for t in captured["result"].task_determinations if t.package_id == package.package_id]
    provider = _AuditedProvider(
        GovernedModel(), [], deployment="gpt-5.1",
        stage="ACCOUNTING_DECISION", package_id=package.package_id,
    )
    assessments, _ = assess_governed_invoice(
        package, tasks, references, provider, captured["request"], allow_demo=True,
    )
    _, policy_reason = references.threshold_for(
        package, captured["request"].requested_at_utc.date(), allow_demo=True,
    )
    for assessment in assessments:
        assert assessment.recommendation == "REVIEW"
        assert assessment.line_amount_usd_meets_threshold is None
        reason = policy_reason if unavailable.endswith("policy") else assessment.currency_conversion.review_reason
        assert reason and reason in assessment.review_reasons


@pytest.mark.parametrize("amount,recommendation", [(500, "CAPEX"), (1500, "OPEX"), (1500, "REVIEW")])
def test_model_cannot_contradict_usd_line_threshold_or_block_it_for_missing_site(
    usd_governed, usd_references, amount, recommendation,
):
    _, _, captured, _ = usd_governed
    package = next(p for p in captured["packages"] if p.currency == "USD")
    package = package.model_copy(update={"lines": [
        line.model_copy(update={"line_amount": amount, "unit_cost": None}) for line in package.lines
    ]})
    tasks = [t for t in captured["result"].task_determinations if t.package_id == package.package_id]

    class UnsupportedDecision(GovernedModel):
        def complete(self, prompt, system=None):
            response = json.loads(super().complete(prompt, system))
            for item in response["items"]:
                item.update(recommendation=recommendation, missingInputs=[],
                            reviewReasons=["Missing site/project"] if recommendation == "REVIEW" else [])
            return json.dumps(response)

    calls = []
    provider = _AuditedProvider(
        UnsupportedDecision(), calls, deployment="gpt-5.1",
        stage="ACCOUNTING_DECISION", package_id=package.package_id,
    )
    with pytest.raises(ValueError, match="must use the deterministic converted-line threshold decision"):
        assess_governed_invoice(
            package, tasks, usd_references, provider, captured["request"], allow_demo=True,
        )
    assert len(calls) == 2 and all(call.validation_error for call in calls)


def test_nature_based_expense_does_not_require_fabricated_fx_or_project_facts(
    usd_governed, usd_references,
):
    _, _, captured, _ = usd_governed
    package = next(p for p in captured["packages"] if p.currency != "USD")
    tasks = [t for t in captured["result"].task_determinations if t.package_id == package.package_id]

    class NatureExpense(GovernedModel):
        def complete(self, prompt, system=None):
            response = json.loads(super().complete(prompt, system))
            for item in response["items"]:
                item.update(natureResult="OPEX_BY_NATURE", recommendation="OPEX",
                            missingInputs=[], reviewReasons=[])
            return json.dumps(response)

    provider = _AuditedProvider(
        NatureExpense(), [], deployment="gpt-5.1",
        stage="ACCOUNTING_DECISION", package_id=package.package_id,
    )
    assessments, _ = assess_governed_invoice(
        package, tasks, usd_references, provider, captured["request"], allow_demo=True,
    )
    assert all(item.recommendation == "OPEX" and item.currency_conversion.conversion_status == "REVIEW"
               for item in assessments)


@pytest.mark.parametrize("usd_governed", [False, True], indirect=True, ids=["missing-rates", "f27-rates"])
@pytest.mark.parametrize("corrupt", [False, True])
def test_usd_line_amounts_are_preserved_atomically_and_read_back(
    usd_governed, native_helpers, tmp_path, corrupt,
):
    _, _, captured, _ = usd_governed
    path = tmp_path / "usd-review.db"
    ReviewStore(path).close()
    connection = AtomicSqlHarness(path)
    try:
        if corrupt:
            connection.db.execute("""
                CREATE TRIGGER capex.corrupt_usd AFTER INSERT ON InvoiceLineAmount
                BEGIN UPDATE InvoiceLineAmount SET OriginalLineAmount='0' WHERE rowid=NEW.rowid; END;
            """)
            with pytest.raises(ValueError, match="exact original and USD-converted"):
                persist_adf_results(connection, **captured)
            assert connection.db.execute("SELECT COUNT(*) FROM capex.InvoiceLineAmount").fetchone()[0] == 0
            assert connection.db.execute("SELECT COUNT(*) FROM capex.Assessment").fetchone()[0] == 0
            assert connection.commits == 0
        else:
            persist_adf_results(connection, **captured)
            rows = connection.db.execute("""
                SELECT PackageId, LineId, OriginalCurrency, ConversionStatus, LineAmountUsd
                FROM capex.InvoiceLineAmount;
            """).fetchall()
            expected = {(a.package_id, a.line_id): a.currency_conversion
                        for a in captured["result"].assessments}
            assert len(rows) == len(expected) == 10
            for package, line, currency, status, amount in rows:
                conversion = expected[(package, line)]
                assert (currency, status) == (conversion.original_currency, conversion.conversion_status)
                assert (Decimal(amount) if amount is not None else None) == conversion.line_amount_usd
            assert connection.commits == 1
        assert connection.cursors_open == 0
    finally:
        connection.close()
