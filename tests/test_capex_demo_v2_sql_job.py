from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from autarch.capex import demo_v2_sql_job as sql_job
from autarch.capex.demo_v2_job import run_demo_v2_workflow


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
    def __init__(self, tasks, *, fail_assessments=False):
        self.tasks = tasks
        self.fail_assessments = fail_assessments
        self.calls = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        parent = self

        class Cursor:
            query = ""

            def execute(self, query, parameters=()):
                self.query = query
                parent.calls.append((query, tuple(parameters)))
                return self

            def executemany(self, query, rows):
                parent.calls.append((query, list(rows)))
                if parent.fail_assessments and "INSERT INTO [capex].[Assessment]" in query:
                    raise RuntimeError("Simulated assessment insert failure")

            def fetchall(self):
                if "[pub].[v1_TaskMaster]" in self.query:
                    return [(t["taskCode"],) for t in parent.tasks]
                return []

            def fetchone(self):
                return None

            def close(self):
                pass

        return Cursor()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
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
