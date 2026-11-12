from __future__ import annotations

import json
from datetime import date

import pytest

from autarch.capex import demo_v2_adf_job as job
from autarch.capex import demo_v2_adf_sql as sql
from autarch.capex.demo_v2_adf_sql import persist_adf_results
from autarch.capex.demo_v2_asset_classes import is_eu_invoice
from autarch.capex.demo_v2_llm_showcase import _AuditedProvider
from autarch.capex.demo_v2_potential_tags import screen_potential_tags
from autarch.capex.demo_v2_review import ReviewStore
from autarch.capex.demo_v2_sql_policy import (
    SqlPolicyReferences, assess_governed_invoice, load_sql_policy_references,
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
                missing = line["unitCost"] is None or threshold is None
                recommendation = "REVIEW" if missing else (
                    "CAPEX" if line["unitCost"] >= float(threshold["governedThreshold"]) else "OPEX"
                )
                items.append({
                    "lineId": line["lineId"], "natureResult": "CAPITALIZABLE",
                    "recommendation": recommendation, "repairMaintenanceIndicator": False,
                    "neverCapitalizeHit": False, "appliedExceptionIds": [],
                    "sourceQuote": line["description"], "rationale": "MOCK TEST ONLY: actual SQL unit-cost basis.",
                    "policyCitations": [
                        f"sql:ref.CapitalizationThreshold:{threshold['thresholdId']}"
                    ] if threshold else request["allowedPolicyCitations"][:1],
                    "missingInputs": ["Printed unit cost or applicable policy"] if missing else [],
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
    assert evidence["demoAssumptions"]["nativeThresholds"] == {"USD": 2500, "EUR": 2000, "DKK": 12000}
    assert evidence["demoAssumptions"]["missingUnitCostBasis"] == "REVIEW_NO_LINE_AMOUNT_PROXY"
    assert not evidence["demoAssumptions"]["fxConversion"]
    assert len([call for call in evidence["calls"] if call["stage"] == "ACCOUNTING_DECISION"]) == 10
    assert len([call for call in evidence["calls"] if call["stage"] == "ASSESSMENT_CATEGORY"]) == 1
    assert not any(call["stage"] == "BUNDLE_DECISION" for call in evidence["calls"])
    assert captured["model_result"].assessments == captured["result"].assessments
    for package in captured["packages"]:
        assessments = [item for item in captured["result"].assessments if item.package_id == package.package_id]
        tasks = [item for item in captured["result"].task_determinations if item.package_id == package.package_id]
        assert all(item.classification_type == ("ASSET_CLASS" if is_eu_invoice(package) else "TASK") for item in tasks)
        if is_eu_invoice(package):
            assert all(item.recommendation == "REVIEW" and item.unit_cost_meets_threshold is None for item in assessments)
            assert all(item.category_suggestion is None for item in assessments)
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
def test_missing_or_ambiguous_policy_is_not_replaced_by_a_currency_default(references, change):
    package = _package([_line("1", "Equipment")])
    package = package.model_copy(update={
        "currency": "USD", "country_region": "US",
        "source": package.source.model_copy(update={"file_name": "na.zip"}),
    })
    payload = references.model_dump(mode="json")
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
        row["project_budget_min"] = "30000"
    selected, reason = SqlPolicyReferences.model_validate(payload).threshold_for(
        package, date(2026, 11, 11), allow_demo=True,
    )
    assert selected is None and reason
    selected, reason = references.threshold_for(package, date(2026, 11, 11), allow_demo=False)
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


@pytest.mark.parametrize("corrupt", [False, True])
def test_categories_and_deferred_bundling_commit_atomically(governed, native_helpers, tmp_path, corrupt):
    _, _, captured, _ = governed
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
