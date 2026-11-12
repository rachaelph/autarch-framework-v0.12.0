from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import pytest

from autarch.capex import demo_v2_sql_job as sql_job
from autarch.capex.demo_v2_job import run_demo_v2_workflow
from autarch.capex.demo_v2_llm_showcase import SqlLlmShowcase
from autarch.capex.demo_v2_reference import canonical_sha256
from autarch.capex.persistence import DatabaseCursor
from autarch.errors import ModelError
from autarch.intelligence.base import ModelProvider


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "examples" / "data" / "capex"
NOW = datetime(2026, 11, 4, 18, 30, tzinfo=timezone.utc)
FIXTURE = "test-fixture"
LABEL = "UNAPPROVED SYNTHETIC TEST"


@pytest.fixture
def tables() -> dict[str, list[dict[str, Any]]]:
    catalog = json.loads((DATA / "reference-v2" / "task-master-us-v2.json").read_text())
    result = {name: [] for name in sql_job._QUERIES}
    scenarios = [
        ("TEST-USD-A", "USD", 12),
        ("TEST-USD-B", "USD", 10),
        *((f"TEST-DKK-{number}", "DKK", 1) for number in range(1, 8)),
        ("TEST-EUR-1", "EUR", 1),
    ]
    for invoice_id, currency, count in scenarios:
        package_id = f"package-{invoice_id}"
        region = "North America" if currency == "USD" else "Europe"
        invoice_lines = []
        for number in range(1, count + 1):
            amount = 75
            description = "Synthetic capital equipment"
            if invoice_id == "TEST-USD-A" and number == 12:
                amount, description = 125, "Shipping"
            elif invoice_id == "TEST-USD-B":
                amount = 0 if number in {3, 8} else 1500
                if number == 10:
                    amount, description = 75, "REIMBURSABLE synthetic expense"
            elif currency == "DKK":
                amount = 15000 if invoice_id == "TEST-DKK-1" else 25000
            elif currency == "EUR":
                amount = 15000
            known_values = invoice_id == "TEST-USD-A" and number < 12
            invoice_lines.append(dict(
                InvoiceID=invoice_id, PackageId=package_id,
                InvoiceLineId=f"{invoice_id}:line-{number}", LineNumber=number,
                Description=description,
                PartNumber=f"KNOWN-PART-{number}" if known_values else None,
                Quantity=1 if known_values else None,
                UnitCost=amount if known_values else None, LineAmount=amount,
                SourceLineJson={"Account Number": "TEST.0303"} if currency == "DKK" else {},
            ))
        invoice = dict(
            InvoiceID=invoice_id, InvoiceNumber=invoice_id, InvoiceCurrency=currency,
            CountryRegion=region, InvoiceTotal=sum(line["LineAmount"] for line in invoice_lines),
            InvoiceDate="2026-06-01" if currency == "USD" else None,
            VendorID=f"TEST-V-{invoice_id}" if currency == "USD" else None,
            VendorName=f"Synthetic vendor {invoice_id}", PONumber=None,
        )
        result["invoices"].append(invoice)
        result["packages"].append(dict(
            PackageId=package_id, InvoiceId=invoice_id, InvoiceNumber=invoice_id,
            InvoiceDate=invoice["InvoiceDate"], VendorId=invoice["VendorID"],
            VendorName=invoice["VendorName"], Currency=currency,
            InvoiceTotal=invoice["InvoiceTotal"], CountryRegion=region, PoNumber=None,
            RunId=f"prepare-{invoice_id}", PackageStatus="PREPARING", SourceMetadataJson={},
            SourcePackageName="synthetic-unit-test.json", SourceSha256="1" * 64,
            SourceSystem="SYNTHETIC_UNIT_TEST", CreatedAtUtc=NOW.isoformat(),
        ))
        result["lines"].extend(invoice_lines)
    result["tasks"] = [
        {**task, "regionCode": "US", "sourceFile": catalog["source"]["fileName"],
         "sourceSheet": catalog["source"]["sheetName"], "sourceVersion": catalog["source"]["sourceVersion"],
         "sourceSha256": catalog["source"]["sha256"]}
        for task in catalog["tasks"]
    ]
    for ordinal, (currency, region, amount) in enumerate(
        (("USD", "North America", 1000), ("DKK", "Europe", 7500), ("EUR", "Europe", 1000)), start=1,
    ):
        result["projects"].append(dict(
            AFEProjectID=f"DEMO-P-{currency}", ProjectStatus="OPEN", ProjectType="SYNTHETIC_DEMO",
            ProjectTotalBudget=1000000, BundlingInScope=True, CountryRegion=region,
            ProjectDescription=LABEL,
        ))
        result["sites"].append(dict(SiteID=f"DEMO-S-{currency}", CountryRegion=region, IsLeased=False))
        result["thresholds"].append(dict(
            ThresholdID=ordinal, GovernedThreshold=amount, Currency=currency,
            ThresholdType="TEST_UNIT_COST_GTE", CountryRegion=region,
            CapitalizationPolicyReference=LABEL, EffectiveFrom="2026-01-01", EffectiveTo="2026-12-31",
        ))
    for invoice in result["invoices"]:
        invoice["PONumber"] = f"TEST-PO-{invoice['InvoiceNumber']}"
    invoices = {i["InvoiceID"]: i for i in result["invoices"]}
    packages = {p["PackageId"]: p for p in result["packages"]}
    for package in result["packages"]:
        invoice = invoices[package["InvoiceId"]]
        currency = invoice["InvoiceCurrency"]
        threshold = next(t for t in result["thresholds"] if t["Currency"] == currency)
        vendor = invoice["VendorID"] or f"TEST-V-{invoice['InvoiceNumber']}"
        suffix = currency if currency != "USD" else f"USD-{invoice['InvoiceNumber']}"
        project_id, site_id = f"DEMO-P-{suffix}", f"DEMO-S-{suffix}"
        if not any(p["AFEProjectID"] == project_id for p in result["projects"]):
            template = next(p for p in result["projects"] if p["AFEProjectID"] == f"DEMO-P-{currency}")
            result["projects"].append({**template, "AFEProjectID": project_id})
            result["sites"].append(dict(SiteID=site_id, CountryRegion=invoice["CountryRegion"], IsLeased=False))
        package["PoNumber"] = invoice["PONumber"]
        package["SourceMetadataJson"]["demoFixture"] = dict(
            fixtureId=FIXTURE, synthetic=True, businessApproved=False,
            projectId=project_id, siteId=site_id,
            projectBudget=1000000, projectBudgetCurrency=currency,
            projectStatus="OPEN", projectType="SYNTHETIC_DEMO", bundlingInScope=True, isLeased=False,
            headerPoNumber=invoice["PONumber"], effectiveVendorId=vendor,
            effectiveInvoiceDate=invoice["InvoiceDate"] or "2026-10-30",
            taskCatalogRegion="US", regionalMappingIsSynthetic=currency != "USD",
            thresholdType=threshold["ThresholdType"], thresholdId=threshold["ThresholdID"],
            unitCostThreshold=threshold["GovernedThreshold"], thresholdCurrency=currency,
            thresholdComparison="GREATER_THAN_OR_EQUAL", thresholdBasis="UNIT_COST",
            belowThresholdExceptionEnabled=False, currencyTreatment="NATIVE_CURRENCY_NO_FX",
            groupingKey="TASK_CODE,SITE_ID,PROJECT_ID,CURRENCY", minimumBundleMembers=2,
            historicalLookbackEnabled=False, unknownNatureBehavior="REVIEW",
            syntheticNatureRules=[
                dict(taskCode="5225", natureResult="CAPITALIZABLE", policyReference=LABEL),
                dict(taskCode="0000", natureResult="UNKNOWN", policyReference=LABEL),
            ], provenance=LABEL,
        )
        result["orders"].append(dict(PONumber=invoice["PONumber"], VendorID=vendor, AFEProjectID=project_id))
    used_projects = {p["SourceMetadataJson"]["demoFixture"]["projectId"] for p in result["packages"]}
    used_sites = {p["SourceMetadataJson"]["demoFixture"]["siteId"] for p in result["packages"]}
    result["projects"] = [p for p in result["projects"] if p["AFEProjectID"] in used_projects]
    result["sites"] = [s for s in result["sites"] if s["SiteID"] in used_sites]
    for line in result["lines"]:
        package = packages[line["PackageId"]]
        context = package["SourceMetadataJson"]["demoFixture"]
        code = "0000" if line["Description"] == "Shipping" or "REIMBURSABLE" in line["Description"] else "5225"
        part = line["PartNumber"] or f"TEST-PART-{len(result['parts'])}"
        quantity = line["Quantity"] if line["Quantity"] is not None else 1
        cost = line["UnitCost"] if line["UnitCost"] is not None else line["LineAmount"] / quantity
        line["SourceLineJson"]["demoFixture"] = dict(
            fixtureId=FIXTURE, synthetic=True, effectiveVendorId=context["effectiveVendorId"],
            effectiveInvoiceDate=context["effectiveInvoiceDate"], effectivePartNumber=part,
            effectiveQuantity=quantity, effectiveUnitCost=cost, poNumber=context["headerPoNumber"],
            poLineNumber=line["LineNumber"], poTaskCode=code, projectId=context["projectId"], siteId=context["siteId"],
            taskCatalogRegion="US", taskMasterSourceSha256=catalog["source"]["sha256"],
            partMappingBusinessConfirmed=False, assumption="Synthetic test input only.",
        )
        result["parts"].append(dict(
            PartMasterID=len(result["parts"]) + 1, VendorID=context["effectiveVendorId"], PartNumber=part,
            MappedTaskCode=code, Source=LABEL, ConflictStatus="SYNTHETIC_UNCONFIRMED",
            ConfirmedBy=None, ConfirmedAtUtc=None, IsCurrent=True,
        ))
        result["po_lines"].append(dict(
            PONumber=context["headerPoNumber"], POLineNumber=line["LineNumber"],
            AFEProjectID=context["projectId"], SiteID=context["siteId"],
            VendorID=context["effectiveVendorId"], POTaskCode=code, PODescription=LABEL,
        ))
    result["never"] = [dict(Keyword="[DEMO-CONSUMABLE]", PolicyReference=LABEL, Active=True)]
    result["repair"] = [dict(Keyword="[DEMO-REPAIR]", PolicyReference=LABEL)]
    return result


def build(tables):
    return sql_job.build_sql_demo_inputs(
        tables, fixture_id=FIXTURE, expected_invoices=10, expected_lines=30,
    )


def run(inputs, *, persist=False, **kwargs):
    return run_demo_v2_workflow(
        request=inputs.request("sql-test-run", persist=persist, now=NOW),
        packages=inputs.sources, assessment_packages=inputs.enriched,
        task_master=inputs.task_master, policy=next(iter(inputs.policies.values())),
        currency_policies=inputs.policies, task_resolver=inputs.resolve,
        rule_set=inputs.rule_set, synthetic_setting_ids=sql_job._SETTINGS,
        requested_at_utc=NOW, completed_at_utc=NOW, **kwargs,
    )


def test_full_source_scope_and_original_values_are_preserved(tables):
    before = copy.deepcopy(tables)
    inputs = build(tables)
    assert tables == before
    assert len(inputs.sources) == 10
    assert len(inputs.matches) == 30
    assert sum(l.quantity is None for p in inputs.sources for l in p.lines) == 19
    assert sum(l.unit_cost is None for p in inputs.sources for l in p.lines) == 19
    assert all(l.quantity is not None and l.unit_cost is not None for p in inputs.enriched for l in p.lines)
    assert sum(p.vendor_id is None for p in inputs.sources) == 8
    assert sum(p.invoice_date is None for p in inputs.sources) == 8
    assert any(l.source_task_code == "0303" for p in inputs.sources for l in p.lines)
    assert set(inputs.policies) == {"USD", "EUR", "DKK"}
    assert all(p.approval_status == "PROPOSED_DEMO_ASSUMPTION" for p in inputs.policies.values())
    assert inputs.manifest()["taskMaster"]["activeTaskCount"] == 761
    snapshot = inputs.manifest()["sqlSnapshot"]
    assert inputs.input_sha256 == sql_job.canonical_sha256({
        name: sorted(rows, key=sql_job.canonical_sha256) for name, rows in snapshot.items()
    })


def test_all_thirty_lines_flow_through_task_accounting_and_bundling(tables):
    result = run(build(tables))
    assert result.status == "SUCCEEDED", result.errors
    assert len(result.packages) == 10
    assert len(result.task_determinations) == len(result.assessments) == 30
    assert {a.threshold_currency for a in result.assessments} == {"USD", "EUR", "DKK"}
    assert {a.recommendation for a in result.assessments} == {"CAPEX", "OPEX", "REVIEW"}
    assert all(t.model_deployment is None and t.match_method == "EXACT_PART" for t in result.task_determinations)
    catalog = {task.task_code: task for task in build(tables).task_master.tasks}
    assert all(task.suggested_asset_class == catalog[task.suggested_task_code].description
               for task in result.task_determinations if task.suggested_task_code is not None)
    assert all(any(e.startswith("synthetic-fixture:") for e in t.evidence_refs) for t in result.task_determinations)
    assert all(not a.used_llm for a in result.assessments)
    group = next(g for g in result.bundle_groups if len(g.members) == 11)
    assert group.currency == "USD"
    assert group.total_amount == 825
    assert group.threshold_amount == 1000
    assert all(g.total_amount > 0 for g in result.bundle_groups)
    assert all(g.currency == next(a.threshold_currency for a in result.assessments
                                 if a.line_id == g.members[0].line_id) for g in result.bundle_groups)


@pytest.mark.parametrize("damage", [
    "missing_line", "duplicate_part", "changed_quantity", "wrong_threshold_currency",
    "stale_catalog", "untagged_eu_proxy", "expired_policy", "wrong_invoice_link", "changed_project",
])
def test_invalid_snapshots_fail_before_workflow(tables, damage):
    if damage == "missing_line":
        tables["lines"].pop()
    elif damage == "duplicate_part":
        tables["parts"].append(tables["parts"][0].copy())
    elif damage == "changed_quantity":
        tables["lines"][0]["SourceLineJson"]["demoFixture"]["effectiveQuantity"] = 99
    elif damage == "wrong_threshold_currency":
        tables["thresholds"][0]["Currency"] = "EUR"
    elif damage == "stale_catalog":
        tables["lines"][0]["SourceLineJson"]["demoFixture"]["taskMasterSourceSha256"] = "a" * 64
    elif damage == "untagged_eu_proxy":
        tables["packages"][-1]["SourceMetadataJson"]["demoFixture"]["regionalMappingIsSynthetic"] = False
    elif damage == "expired_policy":
        tables["thresholds"][0]["EffectiveTo"] = "2026-01-02"
    elif damage == "wrong_invoice_link":
        tables["lines"][0]["InvoiceID"] = "different-invoice"
    else:
        tables["projects"][0]["ProjectTotalBudget"] = 0
    with pytest.raises((ValueError, KeyError)):
        build(tables)


def test_conflicting_part_mapping_becomes_review(tables):
    tables["parts"][0]["MappedTaskCode"] = "0007"
    result = run(build(tables))
    line_id = tables["lines"][0]["InvoiceLineId"]
    task = next(t for t in result.task_determinations if t.line_id == line_id)
    assert task.resolution_status == "REVIEW"
    assert task.suggested_task_code is None
    assert next(a for a in result.assessments if a.line_id == line_id).recommendation == "REVIEW"


def test_snapshot_digest_is_order_independent(tables):
    expected = build(tables).input_sha256
    for rows in tables.values():
        rows.reverse()
    assert build(tables).input_sha256 == expected


def test_native_dkk_bundles_use_dkk_threshold_without_conversion(tables):
    dkk_lines = [l for l in tables["lines"] if l["InvoiceID"].startswith("TEST-DKK-")]
    for line in dkk_lines[:2]:
        override = line["SourceLineJson"]["demoFixture"]
        override["effectiveQuantity"] = 100
        override["effectiveUnitCost"] = line["LineAmount"] / 100
    result = run(build(tables))
    assert result.status == "SUCCEEDED", result.errors
    group = next(g for g in result.bundle_groups if g.currency == "DKK")
    assert group.threshold_amount == 7500
    assert group.total_amount == 40000
    assert all(a.threshold_currency == "DKK" and a.threshold_amount == 7500
               for a in result.assessments if a.invoice_id.startswith("TEST-DKK-"))


def test_sql_reader_preserves_nulls_parameterizes_scope_and_never_writes(tables):
    calls = []

    class Cursor:
        query = ""
        closed = False

        def execute(self, query, parameters=()):
            calls.append((query, parameters))
            self.query = query

        def fetchall(self):
            name = next(n for n, q in sql_job._QUERIES.items() if q == self.query)
            payload = json.dumps(tables[name])
            halfway = len(payload) // 2
            return [(payload[:halfway],), (payload[halfway:],)]

        def close(self):
            self.closed = True

    cursor = Cursor()

    class Connection:
        def cursor(self):
            return cursor

    inputs = sql_job.load_sql_demo_inputs(
        Connection(), fixture_id=FIXTURE, expected_invoices=10, expected_lines=30,
    )
    assert len(inputs.matches) == 30
    assert cursor.closed
    assert len(calls) == 13
    for query, params in calls[1:]:
        assert query.lstrip().startswith("SELECT")
        assert "INCLUDE_NULL_VALUES" in query
        assert FIXTURE not in query
        assert params in ((), (FIXTURE,))


def test_non_synthetic_workflow_rejects_currency_overrides(tables):
    inputs = build(tables)
    result = run_demo_v2_workflow(
        request=inputs.request("not-synthetic", persist=False, now=NOW).model_copy(
            update={"allow_synthetic_inputs": False}),
        packages=inputs.sources, task_master=inputs.task_master,
        policy=next(iter(inputs.policies.values())), currency_policies=inputs.policies,
        task_resolver=lambda *_: pytest.fail("Must fail before task determination"),
    )
    assert result.status == "FAILED"
    assert result.errors[0].code == "SYNTHETIC_POLICY_OPT_IN_REQUIRED"


class OutputConnection:
    def __init__(
        self, tasks, *, fail_assessments=False, llm_schema_ready=True,
        fail_llm_insert=False, fail_llm_commit=False, native_schema_ready=True,
        linked_assessments=30, fail_bundle_decisions=False,
    ):
        self.tasks = tasks
        self.fail_assessments = fail_assessments
        self.calls = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.llm_schema_ready = llm_schema_ready
        self.fail_llm_insert = fail_llm_insert
        self.fail_llm_commit = fail_llm_commit
        self.native_schema_ready = native_schema_ready
        self.linked_assessments = linked_assessments
        self.fail_bundle_decisions = fail_bundle_decisions
        self.llm_row: Optional[tuple[Any, ...]] = None
        self.llm_pending_row: Optional[tuple[Any, ...]] = None

    def cursor(self) -> DatabaseCursor:
        parent = self

        class Cursor:
            query = ""

            def execute(self, operation: str, parameters: Sequence[Any] = ()) -> Any:
                self.query = operation
                parent.calls.append((operation, tuple(parameters)))
                if "INSERT INTO capex.LlmShowcaseRun" in operation:
                    if parent.fail_llm_insert:
                        raise RuntimeError("Simulated LLM SQL insert failure")
                    parent.llm_pending_row = tuple(parameters)
                return self

            def executemany(self, operation: str, parameters: Sequence[Sequence[Any]]) -> Any:
                parent.calls.append((operation, list(parameters)))
                if parent.fail_assessments and "INSERT INTO [capex].[Assessment]" in operation:
                    raise RuntimeError("Simulated assessment insert failure")
                if parent.fail_bundle_decisions and "INSERT INTO capex.BundleDecision" in operation:
                    raise RuntimeError("Simulated model bundle decision insert failure")

            def fetchall(self) -> Sequence[Sequence[Any]]:
                if "[pub].[v1_TaskMaster]" in self.query:
                    return [(t["taskCode"],) for t in parent.tasks]
                return []

            def fetchone(self) -> Optional[Sequence[Any]]:
                if "OBJECT_ID(N'capex.BundleDecision'" in self.query:
                    return (1,) * 8 if parent.native_schema_ready else (None,) * 8
                if "TaskSuggestionID IS NOT NULL" in self.query:
                    return (parent.linked_assessments,)
                if "OBJECT_ID(N'capex.LlmShowcaseRun'" in self.query:
                    return (1, 1, 1) if parent.llm_schema_ready else (None, None, None)
                if "SELECT EvidenceSha256 FROM capex.LlmShowcaseRun" in self.query and parent.llm_row:
                    return (parent.llm_row[6],)
                if "SELECT 1 FROM capex.LlmShowcaseRun" in self.query and parent.llm_row:
                    return (1,)
                return None

            def close(self) -> None:
                pass

        return Cursor()

    def commit(self) -> None:
        if self.llm_pending_row is not None:
            if self.fail_llm_commit:
                raise RuntimeError("Simulated LLM SQL commit failure")
            self.llm_row = self.llm_pending_row
            self.llm_pending_row = None
        self.commits += 1

    def rollback(self) -> None:
        self.llm_pending_row = None
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def test_complete_workflow_hands_all_lines_to_real_persistence_writers(tables):
    connection = OutputConnection(tables["tasks"])
    result = run(build(tables), persist=True, connection=connection)
    assert result.status == "SUCCEEDED", result.errors
    assert result.persistence.task_suggestions == result.persistence.assessments == 30
    assert result.persistence.bundle_groups == 1
    assert result.persistence.bundle_members == 11
    inserts = [(q, rows) for q, rows in connection.calls if q.lstrip().startswith("INSERT")]
    assert sum(len(rows) for q, rows in inserts if "INSERT INTO [task].[TaskSuggestion]" in q) == 30
    assert sum(len(rows) for q, rows in inserts if "INSERT INTO [capex].[Assessment]" in q) == 30
    task_rows = [r for q, rows in inserts if "INSERT INTO [task].[TaskSuggestion]" in q for r in rows]
    assert all(row[16] == "REFERENCE_ONLY" for row in task_rows)
    assert all(row[9] is not None for row in task_rows)
    assert all(row[3] == "sql-test-run" for row in task_rows)
    assert connection.commits == 11
    assert connection.rollbacks == 0
    assert not any("UPDATE" in query or "DELETE" in query for query, _ in connection.calls)


def test_partial_persistence_reports_tasks_that_already_committed(tables):
    connection = OutputConnection(tables["tasks"], fail_assessments=True)
    result = run(build(tables), persist=True, connection=connection)
    assert result.status == "PARTIAL"
    assert result.persistence.task_suggestions == 30
    assert result.persistence.assessments == 0
    assert result.errors[0].stage == "PERSISTENCE"
    assert connection.commits == 10
    assert connection.rollbacks == 1


def test_cli_default_only_validates_and_writes_immutable_snapshot(tables, monkeypatch, tmp_path):
    inputs = build(tables)
    connection = OutputConnection(tables["tasks"])
    monkeypatch.setenv("CAPEX_SQL_SERVER", "example.database.windows.net")
    monkeypatch.setenv("CAPEX_SQL_DATABASE", "example")
    monkeypatch.setattr(sql_job, "connect_azure_sql", lambda **_: connection)
    monkeypatch.setattr(sql_job, "load_sql_demo_inputs", lambda *_, **__: inputs)
    monkeypatch.setattr(sql_job, "run_demo_v2_workflow",
                        lambda **_: pytest.fail("Preflight must not process or persist anything"))
    snapshot = tmp_path / "input.json"
    args = ["--fixture-id", FIXTURE, "--allow-synthetic-inputs",
            "--expected-invoices", "10", "--expected-lines", "30",
            "--snapshot-uri", str(snapshot)]
    assert sql_job.main(args) == 0
    assert connection.closed and connection.rollbacks == 1 and connection.commits == 0
    assert json.loads(snapshot.read_text())["lineCount"] == 30
    with pytest.raises(FileExistsError):
        sql_job.main(args)


def test_cli_rejects_changed_preflight_snapshot(tables, monkeypatch, tmp_path):
    connection = OutputConnection(tables["tasks"])
    monkeypatch.setenv("CAPEX_SQL_SERVER", "example.database.windows.net")
    monkeypatch.setenv("CAPEX_SQL_DATABASE", "example")
    monkeypatch.setattr(sql_job, "connect_azure_sql", lambda **_: connection)
    monkeypatch.setattr(sql_job, "load_sql_demo_inputs", lambda *_, **__: build(tables))
    with pytest.raises(ValueError, match="changed after preflight"):
        sql_job.main([
            "--fixture-id", FIXTURE, "--allow-synthetic-inputs",
            "--expected-invoices", "10", "--expected-lines", "30",
            "--snapshot-uri", str(tmp_path / "input.json"),
            "--execute", "--workflow-run-id", "fresh-run",
            "--output-uri", str(tmp_path / "result.json"),
            "--expected-input-sha256", "0" * 64,
        ])
    assert not list(tmp_path.iterdir())
    assert connection.closed and connection.commits == 0


@pytest.mark.parametrize("extra", [
    ["--persist-results"],
    ["--execute"],
    ["--execute", "--workflow-run-id", "run", "--output-uri", "out.json"],
])
def test_cli_rejects_unsafe_flags_before_connecting(monkeypatch, extra):
    monkeypatch.setattr(sql_job, "connect_azure_sql", lambda **_: pytest.fail("Must fail before connecting"))
    with pytest.raises(SystemExit) as exc:
        sql_job.main([
            "--fixture-id", FIXTURE, "--allow-synthetic-inputs",
            "--expected-invoices", "10", "--expected-lines", "30", "--snapshot-uri", "input.json",
            *extra,
        ])
    assert exc.value.code == 2


def test_input_grants_are_object_scoped_select_only():
    sql = (ROOT / "sql" / "migrations" / "011_grant_sql_demo_input_read_access.sql").read_text()
    assert sql.count("GRANT SELECT ON OBJECT::") == 11
    assert "GRANT SELECT ON OBJECT::ref.TaskMaster" not in sql
    for denied in ("GRANT INSERT", "GRANT UPDATE", "GRANT DELETE", "ON SCHEMA::", "CREATE TABLE"):
        assert denied not in sql
    for name in ("prep.Invoice", "prep.InvoicePackage", "prep.InvoiceLine",
                 "prep.Project", "prep.Site", "prep.PurchaseOrder", "prep.PurchaseOrderLine",
                 "ref.PartMaster", "ref.CapitalizationThreshold",
                 "ref.NeverCapitalizeException", "ref.RepairMaintenanceIndicator"):
        assert f"GRANT SELECT ON OBJECT::{name} TO [cict-ai-cus-capex-id]" in sql


class ShowcaseProvider(ModelProvider):
    name = "test-provider"

    def __init__(self, *, failure=None):
        self.failure = failure
        self.prompts = []

    def complete(self, prompt, system=None):
        self.prompts.append((prompt, system))
        if prompt.startswith("Invoice request JSON:"):
            if self.failure == "task":
                raise ModelError("Simulated model failure")
            request = json.loads(prompt.split("\n", 1)[1].split("\n\nReturn JSON")[0])
            results = []
            for line in request["lines"]:
                candidate = next(iter(line["candidates"]), None)
                results.append({
                    "lineId": line["lineId"], "resolutionStatus": "RESOLVED" if candidate else "REVIEW",
                    "taskCode": candidate["taskCode"] if candidate else None, "confidence": 0.8,
                    "unresolvedReason": None if candidate else "Indistinguishable candidate codes.",
                    "rationale": "Test-only model response grounded in line and catalog.",
                    "evidenceRefs": [f"line:{line['lineId']}"] +
                                    ([f"task:{candidate['taskCode']}"] if candidate else []),
                })
            return json.dumps({"lines": results})
        request = json.loads(prompt)
        if self.failure == "empty":
            return ""
        if self.failure == "malformed":
            return "not JSON"
        items = [{
            "referenceId": item["referenceId"], "outcome": item["outcome"],
            "explanation": "Test-only advisory explanation of synthetic inputs.",
            "evidenceRefs": item["allowedEvidenceRefs"],
        } for item in request["items"]]
        if self.failure == "outcome":
            items[0]["outcome"] = "REVIEW" if items[0]["outcome"] != "REVIEW" else "CAPEX"
        elif self.failure == "coverage":
            items.pop()
        elif self.failure == "duplicate":
            items.append(items[0])
        elif self.failure == "evidence":
            items[0]["evidenceRefs"] = ["invented-policy"]
        return json.dumps({"summary": "Test-only advisory summary.", "items": items})


def model_run(inputs, provider):
    showcase = SqlLlmShowcase(
        provider=provider, deployment="gpt-5.1", task_master=inputs.task_master,
        fixture_id=inputs.fixture_id, input_sha256=inputs.input_sha256,
    )
    result = run_demo_v2_workflow(
        request=inputs.request("sql-llm-test", persist=False, now=NOW, model_deployment="gpt-5.1"),
        packages=inputs.sources, assessment_packages=inputs.enriched,
        task_master=inputs.task_master, policy=next(iter(inputs.policies.values())),
        currency_policies=inputs.policies, task_resolver=showcase.resolve,
        rule_set=inputs.rule_set, synthetic_setting_ids=sql_job._SETTINGS,
    )
    return result, showcase


def test_model_showcase_captures_real_boundary_calls_and_preserves_accounting(tables):
    inputs = build(tables)
    provider = ShowcaseProvider()
    result, showcase = model_run(inputs, provider)
    assert result.status == "SUCCEEDED", result.errors
    assert len(result.task_determinations) == len(result.assessments) == 30
    before = result.model_dump(mode="json", by_alias=True)
    showcase.explain(result, sources=inputs.sources, processing=inputs.enriched, policies=inputs.policies)
    assert result.model_dump(mode="json", by_alias=True) == before
    assert all(not item.used_llm for item in result.assessments)
    evidence = showcase.evidence(result)
    assert evidence["status"] == "SUCCEEDED"
    assert evidence["workflowResultSha256"] == canonical_sha256(before)
    assert evidence["llmResponsesReceived"] == evidence["llmCallsAttempted"] == len(provider.prompts)
    assert sum(call.stage == "ACCOUNTING_EXPLANATION" for call in showcase.calls) == 10
    assert sum(call.stage == "BUNDLE_EXPLANATION" for call in showcase.calls) == 1
    assert sum(len(batch.items) for batch in showcase.accounting.values()) == 30
    model_tasks = [task for task in result.task_determinations if task.model_deployment]
    assert model_tasks
    assert all(any(ref.startswith("llm-call:") for ref in task.evidence_refs) for task in model_tasks)
    for call in showcase.calls:
        assert call.response and call.error is None and call.duration_seconds >= 0
        if call.stage == "TASK_SUGGESTION":
            assert "referenceMatches" not in call.prompt
            assert "demoFixture" not in call.prompt
            assert "SYNTHETIC-PART" not in call.prompt
    assert inputs.sources[1].lines[0].quantity is None


@pytest.mark.parametrize("failure", ["outcome", "coverage", "duplicate", "evidence", "malformed", "empty"])
def test_advisory_models_cannot_change_outcomes_or_fabricate_evidence(tables, failure):
    inputs = build(tables)
    result, showcase = model_run(inputs, ShowcaseProvider(failure=failure))
    before = result.model_dump()
    with pytest.raises((ValueError, ModelError)):
        showcase.explain(result, sources=inputs.sources, processing=inputs.enriched, policies=inputs.policies)
    assert result.model_dump() == before
    assert showcase.evidence(result)["status"] == "INCOMPLETE"
    assert showcase.calls[-1].prompt


def test_task_model_failure_is_not_replaced_by_reference_answers(tables):
    inputs = build(tables)
    result, showcase = model_run(inputs, ShowcaseProvider(failure="task"))
    assert result.status != "SUCCEEDED"
    assert result.errors
    assert any(call.error for call in showcase.calls)
    assert not any(task.match_method == "EXACT_PART" for task in result.task_determinations)
    with pytest.raises(ValueError, match="partial workflow"):
        showcase.explain(result, sources=inputs.sources, processing=inputs.enriched, policies=inputs.policies)


@pytest.mark.parametrize("failure", [None, "outcome"])
def test_cli_llm_mode_writes_separate_immutable_evidence(tables, monkeypatch, tmp_path, failure, capsys):
    from autarch.intelligence import azure_openai

    inputs = build(tables)
    provider = ShowcaseProvider(failure=failure)
    monkeypatch.setenv("CAPEX_SQL_SERVER", "example.database.windows.net")
    monkeypatch.setenv("CAPEX_SQL_DATABASE", "example")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setattr(azure_openai, "AzureOpenAIProvider", lambda **_: provider)
    connection = OutputConnection(tables["tasks"])
    monkeypatch.setattr(sql_job, "connect_azure_sql", lambda **_: connection)
    monkeypatch.setattr(sql_job, "load_sql_demo_inputs", lambda *_, **__: inputs)
    args = [
        "--fixture-id", FIXTURE, "--allow-synthetic-inputs", "--expected-invoices", "10",
        "--expected-lines", "30", "--snapshot-uri", str(tmp_path / "snapshot.json"),
        "--execute", "--persist-results", "--workflow-run-id", "sql-llm-cli-test",
        "--output-uri", str(tmp_path / "result.json"),
        "--expected-input-sha256", inputs.input_sha256, "--llm-showcase",
        "--llm-evidence-uri", str(tmp_path / "llm.json"),
    ]
    if failure:
        with pytest.raises(ValueError, match="authoritative outcomes"):
            sql_job.main(args)
    else:
        assert sql_job.main(args) == 0
    evidence = json.loads((tmp_path / "llm.json").read_text())
    result = json.loads((tmp_path / "result.json").read_text())
    assert evidence["status"] == ("INCOMPLETE" if failure else "SUCCEEDED")
    assert evidence["workflowResultSha256"] == canonical_sha256(result)
    assert result["persistence"]["taskSuggestions"] == result["persistence"]["assessments"] == 30
    assert evidence["calls"][0]["response"]
    assert connection.llm_row is not None
    assert json.loads(connection.llm_row[12]) == evidence
    assert connection.llm_row[2] == evidence["status"]
    assert connection.llm_row[7] == evidence["llmCallsAttempted"]
    assert connection.llm_row[9] == (0 if failure else 10)
    assert connection.llm_row[10] == (0 if failure else 30)
    output = capsys.readouterr().out
    assert ("LLM_SHOWCASE_INCOMPLETE" if failure else "LLM_SHOWCASE_COMPLETE") in output
    assert '"sqlLlmEvidenceRows": 1' in output


@pytest.mark.parametrize("extra", [
    ["--llm-showcase"],
    ["--llm-evidence-uri", "llm.json"],
    ["--execute", "--workflow-run-id", "new", "--output-uri", "result.json",
     "--expected-input-sha256", "a" * 64, "--llm-showcase", "--llm-evidence-uri", "input.json"],
])
def test_cli_llm_safety_flags_fail_before_any_connection(monkeypatch, extra):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setattr(sql_job, "connect_azure_sql", lambda **_: pytest.fail("No connection allowed"))
    with pytest.raises(SystemExit) as exc:
        sql_job.main([
            "--fixture-id", FIXTURE, "--allow-synthetic-inputs", "--expected-invoices", "10",
            "--expected-lines", "30", "--snapshot-uri", "input.json", *extra,
        ])
    assert exc.value.code == 2


def test_sql_llm_outputs_include_full_large_unicode_responses_and_are_immutable(tables):
    class LargeProvider(ShowcaseProvider):
        def complete(self, prompt, system=None):
            response = json.loads(super().complete(prompt, system))
            for line in response.get("lines", []):
                line["rationale"] = "Evidence-driven test response: " + "\u00e9" * 5000
            return json.dumps(response, ensure_ascii=False)

    inputs = build(tables)
    result, showcase = model_run(inputs, LargeProvider())
    showcase.explain(result, sources=inputs.sources, processing=inputs.enriched, policies=inputs.policies)
    connection = OutputConnection(tables["tasks"])
    digest = showcase.persist(connection, result)
    assert connection.commits == 1 and connection.rollbacks == 0
    row = connection.llm_row
    assert row is not None
    payload = json.loads(row[12])
    assert row[0] == result.workflow_run_id and row[6] == digest == canonical_sha256(payload)
    assert row[9:12] == (10, 30, len(result.bundle_groups))
    assert payload == showcase.evidence(result)
    assert len(payload["calls"][0]["response"]) > 4000
    assert "\u00e9" * 5000 in payload["calls"][0]["response"]
    assert payload["bundleExplanation"]["summary"]
    assert {call["stage"] for call in payload["calls"]} == {
        "TASK_SUGGESTION", "ACCOUNTING_EXPLANATION", "BUNDLE_EXPLANATION",
    }
    assert showcase.persist(connection, result) == digest
    assert sum("INSERT INTO capex.LlmShowcaseRun" in query for query, _ in connection.calls) == 1
    assert showcase.calls[0].response is not None
    showcase.calls[0].response += "\n"
    with pytest.raises(ValueError, match="different LLM SQL evidence"):
        showcase.persist(connection, result)
    assert connection.rollbacks == 1
    assert connection.llm_row == row


@pytest.mark.parametrize("failure", ["insert", "commit"])
def test_sql_llm_output_write_failures_rollback(tables, failure):
    inputs = build(tables)
    result, showcase = model_run(inputs, ShowcaseProvider())
    showcase.explain(result, sources=inputs.sources, processing=inputs.enriched, policies=inputs.policies)
    connection = OutputConnection(
        tables["tasks"], fail_llm_insert=failure == "insert", fail_llm_commit=failure == "commit",
    )
    with pytest.raises(RuntimeError, match=f"Simulated LLM SQL {failure} failure"):
        showcase.persist(connection, result)
    assert connection.commits == 0 and connection.rollbacks == 1
    assert connection.llm_row is connection.llm_pending_row is None


def llm_cli_args(inputs, tmp_path):
    return [
        "--fixture-id", FIXTURE, "--allow-synthetic-inputs", "--expected-invoices", "10",
        "--expected-lines", "30", "--snapshot-uri", str(tmp_path / "snapshot.json"),
        "--execute", "--persist-results", "--workflow-run-id", "sql-llm-store-test",
        "--output-uri", str(tmp_path / "result.json"),
        "--expected-input-sha256", inputs.input_sha256, "--llm-showcase",
        "--llm-evidence-uri", str(tmp_path / "llm.json"),
    ]


def configure_llm_cli(monkeypatch, tables, inputs, connection, provider):
    from autarch.intelligence import azure_openai

    monkeypatch.setenv("CAPEX_SQL_SERVER", "example.database.windows.net")
    monkeypatch.setenv("CAPEX_SQL_DATABASE", "example")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setattr(azure_openai, "AzureOpenAIProvider", lambda **_: provider)
    monkeypatch.setattr(sql_job, "connect_azure_sql", lambda **_: connection)
    monkeypatch.setattr(sql_job, "load_sql_demo_inputs", lambda *_, **__: inputs)


def test_missing_sql_llm_table_or_grants_fail_before_paid_calls_or_writes(tables, monkeypatch, tmp_path):
    inputs = build(tables)
    connection = OutputConnection(tables["tasks"], llm_schema_ready=False)
    provider = ShowcaseProvider()
    configure_llm_cli(monkeypatch, tables, inputs, connection, provider)
    with pytest.raises(ValueError, match="012_store_capex_llm_showcase_outputs.sql"):
        sql_job.main(llm_cli_args(inputs, tmp_path))
    assert not provider.prompts
    assert connection.commits == 0
    assert not any("INSERT INTO" in query for query, _ in connection.calls)


def test_cli_sql_llm_failure_retains_incomplete_blob_evidence(tables, monkeypatch, tmp_path, capsys):
    inputs = build(tables)
    connection = OutputConnection(tables["tasks"], fail_llm_insert=True)
    configure_llm_cli(monkeypatch, tables, inputs, connection, ShowcaseProvider())
    with pytest.raises(RuntimeError, match="Simulated LLM SQL insert failure"):
        sql_job.main(llm_cli_args(inputs, tmp_path))
    evidence = json.loads((tmp_path / "llm.json").read_text())
    assert evidence["status"] == "INCOMPLETE"
    assert "not committed to SQL" in evidence["error"]
    assert evidence["calls"]
    assert connection.llm_row is None
    output = capsys.readouterr().out
    assert "LLM_SHOWCASE_INCOMPLETE" in output
    assert '"sqlLlmEvidenceRows": 0' in output


def test_sql_model_outputs_survive_blob_mirror_failure(tables, monkeypatch, tmp_path):
    inputs = build(tables)
    connection = OutputConnection(tables["tasks"])
    configure_llm_cli(monkeypatch, tables, inputs, connection, ShowcaseProvider())
    original_write_json = sql_job._write_json

    def write_json(uri, *args, **kwargs):
        if str(uri) == str(tmp_path / "llm.json"):
            raise OSError("Simulated LLM Blob mirror failure")
        return original_write_json(uri, *args, **kwargs)

    monkeypatch.setattr(sql_job, "_write_json", write_json)
    with pytest.raises(OSError, match="Blob mirror failure"):
        sql_job.main(llm_cli_args(inputs, tmp_path))
    assert connection.llm_row is not None
    assert connection.llm_row[2] == "SUCCEEDED"
    evidence = json.loads(connection.llm_row[12])
    assert evidence["calls"] and len(evidence["accountingExplanations"]) == 10
    assert connection.llm_row[10] == 30
    assert not (tmp_path / "llm.json").exists()


@pytest.fixture
def native_inputs(tables):
    for line in tables["lines"]:
        line["SourceLineJson"]["Account Number"] = "TEST.5225"
    first, second = tables["packages"][:2]
    for field in ("siteId", "projectId"):
        second["SourceMetadataJson"]["demoFixture"][field] = first["SourceMetadataJson"]["demoFixture"][field]
    for order in tables["orders"]:
        if order["PONumber"] == second["PoNumber"]:
            order["AFEProjectID"] = first["SourceMetadataJson"]["demoFixture"]["projectId"]
    for line in tables["lines"]:
        if line["PackageId"] == second["PackageId"]:
            for field in ("siteId", "projectId"):
                line["SourceLineJson"]["demoFixture"][field] = second["SourceMetadataJson"]["demoFixture"][field]
    for line in tables["po_lines"]:
        if line["PONumber"] == second["PoNumber"]:
            line.update(AFEProjectID=second["SourceMetadataJson"]["demoFixture"]["projectId"],
                        SiteID=second["SourceMetadataJson"]["demoFixture"]["siteId"])
    for invoice_id, quantity in (("TEST-USD-B", 30), ("TEST-DKK-1", 10)):
        line = next(line for line in tables["lines"] if line["InvoiceID"] == invoice_id)
        line["SourceLineJson"]["demoFixture"].update(
            effectiveQuantity=quantity, effectiveUnitCost=line["LineAmount"] / quantity)
    return build(tables)


class DecisionProvider(ShowcaseProvider):
    def complete(self, prompt, system=None):
        if prompt.startswith("Invoice request JSON:"):
            return super().complete(prompt, system)
        self.prompts.append((prompt, system))
        request = json.loads(prompt)
        if request["stage"] == "ACCOUNTING_DECISION":
            package = request["processingPackage"]
            tasks = {item["lineId"]: item for item in request["taskSuggestions"]}
            threshold = request["policy"]["capitalization"]["baseThreshold"]
            items = []
            for line in package["lines"]:
                resolved = tasks[line["lineId"]]["resolutionStatus"] == "RESOLVED"
                recommendation = "REVIEW" if not resolved else "CAPEX" if line["unitCost"] >= threshold else "OPEX"
                items.append(dict(
                    lineId=line["lineId"], natureResult="CAPITALIZABLE" if resolved else "REVIEW",
                    recommendation=recommendation, repairMaintenanceIndicator=False,
                    neverCapitalizeHit=False,
                    rationale="Direct test model accounting recommendation.",
                    policyCitations=request["allowedPolicyCitations"], missingInputs=[],
                    reviewReasons=[] if resolved else ["Asset attribution is unresolved."],
                ))
            if self.failure == "accounting_coverage":
                items.pop()
            elif self.failure == "accounting_duplicate":
                items.append(items[0])
            elif self.failure == "accounting_identity":
                items[0]["lineId"] = "invented-line"
            elif self.failure == "accounting_capex":
                items[0]["recommendation"] = "CAPEX"
            elif self.failure == "accounting_repair":
                items[0].update(repairMaintenanceIndicator=True, recommendation="CAPEX")
            elif self.failure == "accounting_evidence":
                items[0]["policyCitations"] = ["invented-policy"]
            elif self.failure == "accounting_review_bundle":
                for item in items:
                    if item["recommendation"] == "REVIEW":
                        item["bundlingEligible"] = True
            return json.dumps(dict(items=items))
        eligible = [item for item in request["assessments"] if item["bundlingDisposition"] == "CANDIDATE"]
        members = [
            next(item for item in eligible if item["invoiceId"] == invoice)
            for invoice in ("TEST-USD-A", "TEST-DKK-1" if self.failure == "bundle_currency" else "TEST-USD-B")
        ]
        identities = [dict(packageId=item["packageId"], invoiceId=item["invoiceId"], lineId=item["lineId"])
                      for item in members]
        amounts = {(p["packageId"], line["lineId"]): line["lineAmount"]
                   for p in request["processingPackages"] for line in p["lines"]}
        group = dict(
            proposalId="test-cross-invoice", members=identities,
            combinedValue=sum(amounts[(item["packageId"], item["lineId"])] for item in members),
            isOneAssetSuggestion=True, rationale="Direct test model cross-invoice asset recommendation.",
            policyCitations=members[0]["policyCitations"],
        )
        grouped = {(item["packageId"], item["lineId"]) for item in members}
        items = [dict(
            packageId=item["packageId"], invoiceId=item["invoiceId"], lineId=item["lineId"],
            decision="BUNDLE" if (item["packageId"], item["lineId"]) in grouped else "NO_BUNDLE",
            proposalId=group["proposalId"] if (item["packageId"], item["lineId"]) in grouped else None,
            confidence=0.9, rationale="Direct test model bundling recommendation.",
            evidenceRefs=[f"line:{item['packageId']}:{item['lineId']}"],
        ) for item in request["assessments"]]
        if self.failure == "bundle_coverage":
            items.pop()
        elif self.failure == "bundle_identity":
            items[0]["invoiceId"] = "invented-invoice"
        elif self.failure == "bundle_sum":
            group["combinedValue"] += 1
        elif self.failure == "bundle_duplicate":
            group["members"].append(group["members"][0])
        elif self.failure == "bundle_unknown":
            group["members"][0]["lineId"] = "invented-line"
        elif self.failure == "bundle_evidence":
            items[0]["evidenceRefs"] = ["invented-evidence"]
        return json.dumps(dict(items=items, groups=[group]))


def native_run(inputs, provider, *, connection=None):
    from autarch.capex.demo_v2_llm_decisions import SqlLlmDecisionWorkflow

    workflow = SqlLlmDecisionWorkflow(
        sources=inputs.sources, provider=provider, deployment="gpt-5.1", task_master=inputs.task_master,
        fixture_id=inputs.fixture_id, input_sha256=inputs.input_sha256,
    )
    result = run_demo_v2_workflow(
        request=inputs.request("sql-native-test", persist=connection is not None, now=NOW, model_deployment="gpt-5.1"),
        packages=inputs.sources, assessment_packages=inputs.enriched,
        task_master=inputs.task_master, policy=next(iter(inputs.policies.values())),
        currency_policies=inputs.policies, task_resolver=workflow.resolve,
        assessment_resolver=workflow.assess, bundle_resolver=workflow.bundle,
        connection=connection, synthetic_setting_ids=sql_job._SETTINGS,
    )
    return result, workflow


def test_native_workflow_uses_model_for_all_lines_and_groups_across_invoices(native_inputs):
    provider = DecisionProvider()
    result, workflow = native_run(native_inputs, provider)
    assert result.status == "SUCCEEDED", result.errors
    workflow.finish(result)
    assert len(provider.prompts) == 21
    assert len(result.task_determinations) == len(result.assessments) == len(workflow.bundle_decisions.items) == 30
    assert all(task.model_deployment == "gpt-5.1" for task in result.task_determinations)
    assert all(item.used_llm and item.model_version == "gpt-5.1" for item in result.assessments)
    assert {item.recommendation.value for item in result.assessments} == {"CAPEX", "OPEX", "REVIEW"}
    assert all(item.rationale == "Direct test model accounting recommendation." for item in result.assessments)
    group, = result.bundle_groups
    assert {member.invoice_id for member in group.members} == {"TEST-USD-A", "TEST-USD-B"}
    assert group.total_amount == 1575
    assert group.disposition == "CANDIDATE"
    assert group.rationale == "Direct test model cross-invoice asset recommendation."
    assert workflow.evidence(result)["pipelineMode"] == "LLM_DECISIONS"
    task_requests = [json.loads(prompt.split("\n", 1)[1].split("\n\nReturn JSON")[0])
                     for prompt, _ in provider.prompts if prompt.startswith("Invoice request JSON:")]
    assert sum(len(request["lines"]) for request in task_requests) == 30
    assert any(line["description"] == "Shipping" for request in task_requests for line in request["lines"])


@pytest.mark.parametrize("failure", [
    "task", "accounting_coverage", "accounting_duplicate", "accounting_identity",
    "accounting_capex", "accounting_repair", "accounting_evidence", "accounting_review_bundle", "bundle_coverage",
    "bundle_identity", "bundle_sum", "bundle_duplicate", "bundle_unknown", "bundle_evidence", "bundle_currency",
])
def test_native_models_fail_closed_without_reference_fallback(native_inputs, failure):
    result, workflow = native_run(native_inputs, DecisionProvider(failure=failure))
    assert result.status != "SUCCEEDED"
    assert result.errors
    with pytest.raises(ValueError, match="complete three-stage"):
        workflow.finish(result)
    assert workflow.evidence(result)["status"] == "INCOMPLETE"
    assert all(item.used_llm for item in result.assessments)


def test_native_sql_writes_model_decisions_and_task_links(native_inputs, tables):
    connection = OutputConnection(tables["tasks"])
    result, workflow = native_run(native_inputs, DecisionProvider(), connection=connection)
    assert result.status == "SUCCEEDED", result.errors
    workflow.finish(result)
    workflow.persist(connection, result)
    writes = [(query, rows) for query, rows in connection.calls if "INSERT INTO capex.BundleDecision" in query]
    assert len(writes) == 1 and len(writes[0][1]) == 30
    assert sum(row[5] == "BUNDLE" for row in writes[0][1]) == 2
    assert all(row[0] == result.workflow_run_id and row[3] for row in writes[0][1])
    assert any("SET TaskSuggestionID=task.taskSuggestionId" in query for query, _ in connection.calls)
    assert not any("INSERT INTO review." in query for query, _ in connection.calls)
    assert connection.llm_row[7:11] == (21, 21, 10, 30)
    workflow.persist(connection, result)
    assert sum("INSERT INTO capex.BundleDecision" in query for query, _ in connection.calls) == 1


@pytest.mark.parametrize("failure", ["links", "bundle_insert", "evidence_insert"])
def test_native_supplemental_sql_writes_roll_back_together(native_inputs, tables, failure):
    connection = OutputConnection(
        tables["tasks"], linked_assessments=29 if failure == "links" else 30,
        fail_bundle_decisions=failure == "bundle_insert", fail_llm_insert=failure == "evidence_insert")
    result, workflow = native_run(native_inputs, DecisionProvider(), connection=connection)
    workflow.finish(result)
    with pytest.raises((ValueError, RuntimeError)):
        workflow.persist(connection, result)
    assert connection.rollbacks == 1
    assert connection.llm_row is None


@pytest.mark.parametrize("ready", [True, False])
def test_native_cli_schema_preflight_and_persistence(native_inputs, tables, monkeypatch, tmp_path, ready):
    provider = DecisionProvider()
    connection = OutputConnection(tables["tasks"], native_schema_ready=ready)
    configure_llm_cli(monkeypatch, tables, native_inputs, connection, provider)
    args = llm_cli_args(native_inputs, tmp_path)
    args[args.index("--llm-showcase")] = "--llm-workflow"
    if not ready:
        with pytest.raises(ValueError, match="013_store_gpt_bundle_decisions"):
            sql_job.main(args)
        assert not provider.prompts
        assert connection.commits == 0
    else:
        assert sql_job.main(args) == 0
        assert connection.llm_row[7] == 21
        assert json.loads(connection.llm_row[12])["pipelineMode"] == "LLM_DECISIONS"


def test_native_empty_eligibility_still_calls_model_and_requires_empty_groups(native_inputs):
    provider = DecisionProvider()
    result, workflow = native_run(native_inputs, provider)
    from autarch.capex.demo_v2_contracts import BundlingDisposition

    assessments = [item.model_copy(update={
        "bundling_disposition": BundlingDisposition.REVIEW, "bundle_group_id": None,
    }) for item in result.assessments]

    class NoEligibleProvider(ModelProvider):
        name = "test-empty-eligibility"
        request = None

        def complete(self, prompt, system=None):
            self.request = json.loads(prompt)
            assert self.request["eligibleBundleMembers"] == []
            assert self.request["responseSchema"]["properties"]["groups"]["maxItems"] == 0
            return json.dumps(dict(groups=[], items=[dict(
                packageId=item.package_id, invoiceId=item.invoice_id, lineId=item.line_id,
                decision="REVIEW", proposalId=None, confidence=0.8,
                rationale="No eligible asset members.", evidenceRefs=[f"line:{item.package_id}:{item.line_id}"],
            ) for item in assessments]))

    empty_provider = NoEligibleProvider()
    workflow.provider = empty_provider
    bundled = workflow.bundle(
        native_inputs.enriched, assessments, next(iter(native_inputs.policies.values())),
        native_inputs.request("sql-native-test", persist=False, now=NOW, model_deployment="gpt-5.1"),
    )
    assert empty_provider.request is not None
    assert bundled.groups == []
    assert len(workflow.bundle_decisions.items) == 30
    assert all(item.bundling_disposition == "REVIEW" for item in bundled.assessments)


def test_native_accounting_prompt_bounds_each_line_and_excludes_redundant_eligibility(native_inputs):
    from jsonschema import ValidationError, validate

    result, workflow = native_run(native_inputs, DecisionProvider())
    assert result.status == "SUCCEEDED", result.errors
    for call in workflow.calls:
        if call.stage != "ACCOUNTING_DECISION":
            continue
        prompt = json.loads(call.prompt)
        response = json.loads(call.response)
        validate(response, prompt["responseSchema"])
        constraints = {item["lineId"]: item for item in prompt["lineConstraints"]}
        assert set(constraints) == {item["lineId"] for item in response["items"]}
        for item in response["items"]:
            assert "bundlingEligible" not in item
        response["items"][0]["bundlingEligible"] = True
        with pytest.raises(ValidationError):
            validate(response, prompt["responseSchema"])


def test_native_opex_by_nature_cannot_become_a_bundle_candidate(native_inputs):
    class OperatingExpenseProvider(DecisionProvider):
        def complete(self, prompt, system=None):
            response = super().complete(prompt, system)
            if not prompt.startswith("Invoice request JSON:"):
                request = json.loads(prompt)
                if request["stage"] == "ACCOUNTING_DECISION":
                    payload = json.loads(response)
                    line = next((item for item in payload["items"]
                                 if item["lineId"] == "TEST-USD-A:line-2"), None)
                    if line is not None:
                        line.update(natureResult="OPEX_BY_NATURE", recommendation="OPEX",
                                    rationale="Actual model classification of ordinary operating services.")
                        return json.dumps(payload)
            return response

    result, workflow = native_run(native_inputs, OperatingExpenseProvider())
    assert result.status == "SUCCEEDED", result.errors
    workflow.finish(result)
    assessment = next(item for item in result.assessments if item.line_id == "TEST-USD-A:line-2")
    assert assessment.nature_result == "OPEX_BY_NATURE"
    assert assessment.recommendation == "OPEX"
    assert assessment.bundling_disposition == "NOT_ELIGIBLE"
    assert assessment.used_llm and assessment.model_version == "gpt-5.1"
    assert assessment.rationale == "Actual model classification of ordinary operating services."
    bundle_prompt = next(json.loads(call.prompt) for call in workflow.calls if call.stage == "BUNDLE_DECISION")
    assert not any(member["lineId"] == assessment.line_id for member in bundle_prompt["eligibleBundleMembers"])
    assert workflow.evidence(result)["bundlingEligibilityAuthority"] == "CODE_GATE_ON_MODEL_ACCOUNTING_AND_INPUT_FACTS"


@pytest.mark.parametrize("indicator", ["repairMaintenanceIndicator", "neverCapitalizeHit"])
def test_native_excluded_opex_can_remain_under_model_review(native_inputs, indicator):
    from jsonschema import validate

    class ExcludedReviewProvider(DecisionProvider):
        def complete(self, prompt, system=None):
            response = super().complete(prompt, system)
            if not prompt.startswith("Invoice request JSON:"):
                request = json.loads(prompt)
                if request["stage"] == "ACCOUNTING_DECISION":
                    payload = json.loads(response)
                    for item in payload["items"]:
                        if item["lineId"] == "TEST-USD-A:line-2":
                            item.update(
                                natureResult="OPEX_BY_NATURE", recommendation="REVIEW",
                                reviewReasons=["Required operating-expense attribution remains unresolved."],
                                rationale="Model retains expense nature pending attribution.",
                                **{indicator: True},
                            )
                    return json.dumps(payload)
            return response

    result, workflow = native_run(native_inputs, ExcludedReviewProvider())
    assert result.status == "SUCCEEDED", result.errors
    workflow.finish(result)
    assessment = next(item for item in result.assessments if item.line_id == "TEST-USD-A:line-2")
    assert assessment.nature_result == "OPEX_BY_NATURE"
    assert assessment.recommendation == "REVIEW"
    assert assessment.bundling_disposition == "REVIEW"
    assert assessment.rationale == "Model retains expense nature pending attribution."
    for call in workflow.calls:
        if call.stage == "ACCOUNTING_DECISION":
            validate(json.loads(call.response), json.loads(call.prompt)["responseSchema"])


def test_native_accounting_regenerates_one_invalid_response_with_audited_feedback(native_inputs, tables):
    class CorrectingProvider(DecisionProvider):
        rejected = False
        feedback = None

        def complete(self, prompt, system=None):
            response = super().complete(prompt, system)
            if not prompt.startswith("Invoice request JSON:"):
                request = json.loads(prompt)
                if request["stage"] == "ACCOUNTING_DECISION":
                    if "validationFeedback" in request:
                        self.feedback = request["validationFeedback"]
                    elif not self.rejected:
                        self.rejected = True
                        payload = json.loads(response)
                        payload["items"][0].update(
                            repairMaintenanceIndicator=True, natureResult="CAPITALIZABLE",
                            recommendation="CAPEX",
                        )
                        return json.dumps(payload)
            return response

    provider = CorrectingProvider()
    connection = OutputConnection(tables["tasks"])
    result, workflow = native_run(native_inputs, provider, connection=connection)
    assert result.status == "SUCCEEDED", result.errors
    workflow.finish(result)
    workflow.persist(connection, result)
    assert len(workflow.calls) == 22
    rejected, = [call for call in workflow.calls if call.validation_error is not None]
    assert rejected.stage == "ACCOUNTING_DECISION"
    assert json.loads(rejected.response)["items"][0]["repairMaintenanceIndicator"] is True
    assert provider.feedback["rejectedResponse"] == rejected.response
    assert provider.feedback["error"] == rejected.validation_error
    assert "line" in rejected.validation_error.lower()
    evidence = workflow.evidence(result)
    assert evidence["accountingMaxAttemptsPerInvoice"] == 2
    assert connection.llm_row[7:11] == (22, 22, 10, 30)
    for package_id, batch in evidence["accountingDecisions"].items():
        accepted = [call for call in workflow.calls
                    if call.stage == "ACCOUNTING_DECISION" and call.package_id == package_id
                    and call.validation_error is None]
        assert len(accepted) == 1
        assert json.loads(accepted[0].response) == batch


def test_native_repeated_accounting_violation_stops_after_two_audited_attempts(native_inputs, tables):
    connection = OutputConnection(tables["tasks"])
    result, workflow = native_run(native_inputs, DecisionProvider(failure="accounting_repair"), connection=connection)
    assert result.status != "SUCCEEDED"
    assert result.persistence.task_suggestions == result.persistence.assessments == 0
    accounting_calls = [call for call in workflow.calls if call.stage == "ACCOUNTING_DECISION"]
    assert len(accounting_calls) == 20
    assert all(call.validation_error and call.response for call in accounting_calls)
    assert not any(call.stage == "BUNDLE_DECISION" for call in workflow.calls)
    assert connection.commits == 0


@pytest.mark.parametrize("failure", ["coverage", "orphaned_decision"])
def test_native_bundling_regenerates_incomplete_coverage_without_stale_groups(native_inputs, failure):
    class CorrectingBundleProvider(DecisionProvider):
        rejected = False
        feedback = None

        def complete(self, prompt, system=None):
            response = super().complete(prompt, system)
            if not prompt.startswith("Invoice request JSON:"):
                request = json.loads(prompt)
                if request["stage"] == "BUNDLE_DECISION":
                    assert len(request["decisionLineCatalog"]) == 30
                    assert request["responseSchema"]["properties"]["items"]["minItems"] == 30
                    assert request["responseSchema"]["properties"]["items"]["maxItems"] == 30
                    if "validationFeedback" in request:
                        self.feedback = request["validationFeedback"]
                    elif not self.rejected:
                        self.rejected = True
                        payload = json.loads(response)
                        if failure == "coverage":
                            payload["items"] = [item for item in payload["items"] if item["decision"] == "BUNDLE"]
                        else:
                            payload["groups"][0]["proposalId"] = "rejected-proposal"
                            for item in payload["items"]:
                                if item["decision"] == "BUNDLE":
                                    item["proposalId"] = "rejected-proposal"
                            item = next(item for item in payload["items"] if item["decision"] == "NO_BUNDLE")
                            item.update(decision="BUNDLE", proposalId="orphaned-proposal")
                        return json.dumps(payload)
            return response

    provider = CorrectingBundleProvider()
    result, workflow = native_run(native_inputs, provider)
    assert result.status == "SUCCEEDED", result.errors
    workflow.finish(result)
    assert len(workflow.calls) == 22
    rejected, = [call for call in workflow.calls if call.validation_error is not None]
    assert rejected.stage == "BUNDLE_DECISION"
    assert ("missing=" if failure == "coverage" else "exactly one valid group") in rejected.validation_error
    assert provider.feedback["rejectedResponse"] == rejected.response
    assert len(workflow.bundle_decisions.items) == 30
    assert len(workflow.group_ids) == len(result.bundle_groups) == 1
    assert "rejected-proposal" not in workflow.group_ids
    assert workflow.evidence(result)["bundlingMaxAttempts"] == 2


def test_native_transport_errors_do_not_trigger_accounting_validation_regeneration(native_inputs):
    class UnavailableAccountingProvider(DecisionProvider):
        def complete(self, prompt, system=None):
            if not prompt.startswith("Invoice request JSON:"):
                if json.loads(prompt)["stage"] == "ACCOUNTING_DECISION":
                    raise ModelError("Synthetic authentication or transport failure.")
            return super().complete(prompt, system)

    result, workflow = native_run(native_inputs, UnavailableAccountingProvider())
    assert result.status != "SUCCEEDED"
    calls = [call for call in workflow.calls if call.stage == "ACCOUNTING_DECISION"]
    assert len(calls) == 10
    assert all(call.error and call.response is None and call.validation_error is None for call in calls)
    assert not any(call.stage == "BUNDLE_DECISION" for call in workflow.calls)
