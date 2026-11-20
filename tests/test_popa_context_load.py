import csv
import json
import re
from pathlib import Path

import pytest

from tools.build_popa_context_load import REQUIRED, SQL_FIELDS, TEMPLATE, build_payload, main, render_handoff
from tools.build_capex_source_invoice_load import render_sql


def _row(**changes):
    row = {field: "NULL" for field in REQUIRED}
    row.update({
        "System_Key": "1", "System_Name": "USA", "Type_Order": "4",
        "Project_Number": "00012-A", "Project_Name": "Fixture project",
        "Project_Type": "Capital", "Project_Status": "Approved",
        "Project_Start_Date": "2026-01-01", "Total_Budget": "NULL",
        "Site_Id": "00123", "PO_Number": "000456", "PO_Line_Number": "1",
        "PO_Description": "Equipment", "Task_Type": "0007",
        "Bus_Entity": "001", "Supplier_Name": "Example supplier",
    })
    row.update(changes)
    return row


def _csv(tmp_path: Path, rows, name="source.csv"):
    path = tmp_path / name
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_context_only_preserves_identifiers_without_fabricating_required_facts(tmp_path):
    path = _csv(tmp_path, [_row()])
    data = build_payload([("US", path)])
    project, site, order, line = [
        data[key][0] for key in ("projects", "sites", "purchaseOrders", "purchaseOrderLines")
    ]
    assert project["AFEProjectID"] == "00012-A"
    assert project["ProjectTotalBudget"] is None
    assert "BundlingInScope" not in project
    assert site["SiteID"] == "00123" and site["CountryRegion"] == "US"
    assert "IsLeased" not in site
    assert order["PONumber"] == "000456" and "AFEProjectID" not in order
    assert line["POTaskCode"] == "0007" and line["BusinessUnit"] == "001"
    assert line["SupplierName"] == "Example supplier" and "VendorID" not in line
    assert "VendorID" in next(r for r in data["review"] if r["table"] == "prep.PurchaseOrderLine")["newRowPrerequisites"]
    assert not ({"invoices", "invoiceLines", "approvedCoding"} & data.keys())
    disposition = {r["sourceColumn"]: r["disposition"] for r in data["fieldDisposition"]}
    assert "NOT VendorID" in disposition["Supplier_Name"]


def test_invoice_distribution_rows_enrich_context_but_never_manufacture_pos(tmp_path):
    path = _csv(tmp_path, [_row(Type_Order="3", PO_Number="123456", PO_Line_Number="3")])
    data = build_payload([("US", path)])
    assert len(data["projects"]) == len(data["sites"]) == 1
    assert data["purchaseOrders"] == data["purchaseOrderLines"] == []


def test_project_budget_is_taken_once_and_never_summed_across_distributions(tmp_path):
    path = _csv(tmp_path, [
        _row(Type_Order="1", Total_Budget="125000.2500", Site_Id="", PO_Number="", PO_Line_Number=""),
        _row(Total_Budget="NULL"),
        _row(Total_Budget="NULL", Task_Type="0010", PO_Line_Number="2"),
    ])
    data = build_payload([("CA", path)])
    assert data["projects"][0]["ProjectTotalBudget"] == "125000.2500"
    assert data["projects"][0]["SourceRowCount"] == 3
    assert data["issues"] == []


def test_identical_distributions_collapse_but_multi_site_or_task_is_a_blocker(tmp_path):
    path = _csv(tmp_path, [
        _row(), _row(), _row(PO_Line_Number="2"),
        _row(PO_Line_Number="2", Site_Id="00456", Task_Type="0010"),
    ])
    data = build_payload([("US", path)])
    assert len(data["purchaseOrderLines"]) == 2
    good, conflict = data["purchaseOrderLines"]
    assert good["SourceRowCount"] == 2 and good["SiteID"] == "00123"
    assert conflict["SiteID"] is None and conflict["POTaskCode"] is None
    assert len(data["issues"]) == 1
    assert "SiteID" in data["issues"][0]["Reason"] and "POTaskCode" in data["issues"][0]["Reason"]


def test_country_and_system_key_collisions_are_not_silently_combined(tmp_path):
    us = _csv(tmp_path, [_row()], "us.csv")
    ca = _csv(tmp_path, [_row(System_Name="MACS", System_Key="2")], "ca.csv")
    data = build_payload([("US", us), ("CA", ca)])
    assert {r["TableName"] for r in data["issues"]} == {
        "prep.Project", "prep.Site", "prep.PurchaseOrder", "prep.PurchaseOrderLine",
    }
    assert all("SourceCountry/System_Key/System_Name" in r["Reason"] for r in data["issues"])
    assert data["projects"][0]["CountryRegion"] is None


@pytest.mark.parametrize("changes", [
    {"Total_Budget": "0.00001"}, {"Total_Budget": "NaN"},
    {"Total_Budget": "1000000000000000"}, {"Project_Start_Date": "not-a-date"},
    {"Project_Number": "X" * 129}, {"PO_Line_Number": "1.5"}, {"Type_Order": "9"},
])
def test_invalid_or_lossy_source_values_fail_explicitly(tmp_path, changes):
    path = _csv(tmp_path, [_row(**changes)])
    with pytest.raises(ValueError):
        build_payload([("US", path)])


def test_non_popa_file_and_eu_assignment_are_rejected(tmp_path):
    payment = _csv(tmp_path, [{"Payment Type": "Example"}])
    with pytest.raises(ValueError, match="POPA headers"):
        build_payload([("US", payment)])
    source = _csv(tmp_path, [_row()], "popa.csv")
    with pytest.raises(ValueError, match="never EU"):
        build_payload([("EU", source)])
    with pytest.raises(ValueError, match="mix environments"):
        build_payload([("US", source), ("US", source)])


def test_conflicting_budget_remains_a_blocker_instead_of_selecting_first(tmp_path):
    path = _csv(tmp_path, [_row(Total_Budget="100.00"), _row(Total_Budget="200.00")])
    data = build_payload([("US", path)])
    assert data["projects"][0]["ProjectTotalBudget"] is None
    assert any("ProjectTotalBudget" in r["Reason"] for r in data["issues"])


def test_generated_sql_has_only_four_permanent_write_targets_and_preserves_quote_data(tmp_path):
    path = _csv(tmp_path, [_row(PO_Description="O'Brien's equipment \\ parts")])
    data = build_payload([("CA", path)])
    script = render_sql(TEMPLATE.read_text(encoding="utf-8"), data)
    match = re.search(r"DECLARE @Payload nvarchar\(max\) = N'((?:[^']|'')*)';", script)
    assert match
    decoded = json.loads(match[1].replace("''", "'"))
    assert decoded["purchaseOrderLines"][0]["PODescription"] == "O'Brien's equipment \\ parts"
    targets = set(re.findall(r"INSERT\s+INTO\s+(prep\.\w+)", script, re.I))
    assert targets == {"prep.Project", "prep.Site", "prep.PurchaseOrder", "prep.PurchaseOrderLine"}
    assert not re.search(r"\b(?:UPDATE|DELETE|MERGE|TRUNCATE|ALTER|DROP)\s+(?:INTO\s+|TABLE\s+)?(?:prep|capex|task|review|ref|ops|mvp_\w+)\.", script, re.I)
    preview = script.index("IF @ApplyChanges = 0")
    blocker = script.index("IF EXISTS (SELECT 1 FROM #Issues)", preview)
    assert preview < blocker < script.index("INSERT INTO prep.Project")
    assert "ROLLBACK TRANSACTION" in script[preview:blocker]
    assert "DECLARE @ApplyChanges bit = 0" in script
    assert "SET XACT_ABORT ON" in script and "BEGIN CATCH" in script


def test_portal_preview_returns_counts_blockers_and_approvals_in_one_result_set(tmp_path):
    source = _csv(tmp_path, [_row()])
    payload = build_payload([("US", source)])
    script = render_handoff(payload, tmp_path)["popa-context-portal-preview.sql"]
    diagnostics = script.split(";WITH CandidateCounts AS", 1)[1].split("IF @ApplyChanges = 0", 1)[0]
    assert re.search(r"SELECT N'1_SUMMARY'.*FROM CandidateCounts\s+UNION ALL\s+"
                     r"SELECT N'2_BLOCKER'.*FROM #Issues\s+UNION ALL\s+"
                     r"SELECT N'3_PROJECT_APPROVAL'.*FROM #ProjectApproval\s+UNION ALL\s+"
                     r"SELECT N'4_VENDOR_MAPPING'.*FROM #VendorMapping\s+"
                     r"ORDER BY ResultType, TableName, RecordKey;", diagnostics, re.DOTALL)
    assert "not inserted rows" in diagnostics
    assert "SELECT * FROM #VendorMapping;" not in script
    assert "SELECT * FROM #ProjectApproval;" not in script


def test_generation_never_overwrites_a_reviewed_sql_handoff(tmp_path, monkeypatch):
    source = _csv(tmp_path, [_row()])
    output = tmp_path / "handoff"
    output.mkdir()
    reviewed = output / "popa-context-load.sql"
    reviewed.write_text("-- reviewed approvals", encoding="utf-8")
    monkeypatch.setattr("sys.argv", [
        "build_popa_context_load", "--us-csv", str(source), "--output-dir", str(output),
    ])
    with pytest.raises(FileExistsError, match="reviewed SQL"):
        main()
    assert reviewed.read_text(encoding="utf-8") == "-- reviewed approvals"


def test_short_launcher_keeps_source_facts_and_checks_in_separate_includes(tmp_path):
    path = _csv(tmp_path, [_row(PO_Description="O'Brien $(UNDEFINED) \\ parts")])
    payload = build_payload([("CA", path)])
    parts = render_handoff(payload, tmp_path / "handoff")
    launcher = parts["popa-context-load.sql"]
    assert len(launcher.splitlines()) <= 12 and len(launcher) < 2000
    assert "DECLARE @ApplyChanges bit = 0;" in launcher
    assert ':r "$(HandoffDirectory)\\popa-context-data.sql"' in launcher
    assert ':r "$(HandoffDirectory)\\popa-context-engine.sql"' in launcher
    data = parts["popa-context-data.sql"]
    assert "$(" not in data
    match = re.search(r"DECLARE @Payload nvarchar\(max\) = N'((?:[^']|'')*)';", data)
    assert match
    decoded = json.loads(match[1].replace("''", "'"))
    assert "review" not in decoded and "fieldDisposition" not in decoded
    assert decoded["sourceFiles"] == payload["sourceFiles"]
    for key, fields in SQL_FIELDS.items():
        assert decoded[key] == [{field: row[field] for field in fields} for row in payload[key]]
    assert decoded["purchaseOrderLines"][0]["PODescription"] == "O'Brien $(UNDEFINED) \\ parts"
    engine = parts["popa-context-engine.sql"]
    assert "DECLARE @Payload" not in engine and "DECLARE @ApplyChanges" not in engine
    assert engine.index("IF @ApplyChanges = 0") < engine.index("INSERT INTO prep.Project")
    assert "ROLLBACK TRANSACTION" in engine and "IF EXISTS (SELECT 1 FROM #Issues)" in engine


def test_short_package_contains_a_complete_preview_without_executing_sql(tmp_path, monkeypatch):
    from zipfile import ZipFile

    source = _csv(tmp_path, [_row()])
    output = tmp_path / "handoff"
    monkeypatch.setattr("sys.argv", [
        "build_popa_context_load", "--us-csv", str(source), "--output-dir", str(output),
    ])
    assert main() == 0
    with ZipFile(output / "popa-context-handoff.zip") as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {
            "popa-context-load.sql", "popa-context-data.sql", "popa-context-engine.sql",
            "popa-context-review.csv", "popa-field-disposition.csv",
            "popa-context-portal-preview.sql",
            "popa-context-site-po-preview.sql",
            "popa-context-project-line-preview.sql", "popa-context-nullable-schema-preview.sql",
            "popa-context.json",
        }
        assert all(archive.read(name) == (output / name).read_bytes() for name in archive.namelist())


def test_portal_query_roundtrips_compressed_facts_and_preserves_whole_batch_validation(tmp_path):
    import base64
    import gzip
    from tools.build_capex_source_invoice_load import BEGIN_PAYLOAD, END_PAYLOAD

    path = _csv(tmp_path, [
        _row(PO_Description="O'Brien $(UNDEFINED) \\ équipement \u4e2d\u6587"),
        _row(Site_Id="00999"),
    ])
    payload = build_payload([("CA", path)])
    portal = render_handoff(payload, tmp_path)["popa-context-portal-preview.sql"]
    block = re.search(re.escape(BEGIN_PAYLOAD) + r".*?" + re.escape(END_PAYLOAD),
                      portal, re.DOTALL)
    assert block
    chunks = re.findall(r"\+ N'([A-Za-z0-9+/=]+)'", block[0])
    assert chunks and all(len(chunk) <= 4000 for chunk in chunks)
    decoded = json.loads(gzip.decompress(base64.b64decode("".join(chunks))).decode("ascii"))
    assert decoded["sourceFiles"] == payload["sourceFiles"]
    assert decoded["issues"] == payload["issues"] and decoded["issues"]
    for key, fields in SQL_FIELDS.items():
        assert decoded[key] == [{field: row[field] for field in fields} for row in payload[key]]
    assert 'xs:base64Binary(sql:variable("@PopaBase64"))' in block[0]
    assert "CAST(N'' AS nvarchar(max))" in block[0]
    assert "CONVERT(varchar(max), DECOMPRESS(@PopaCompressed))" in block[0]
    original = render_sql(TEMPLATE.read_text(encoding="utf-8"), decoded)
    assert portal[:block.start()].endswith(original.split(BEGIN_PAYLOAD)[0])
    assert portal[block.end():] == original.split(END_PAYLOAD)[1]
    assert portal.count("DECLARE @ApplyChanges bit = 0;") == 1
    assert not re.search(r"^\s*:(?:r|setvar)\b", portal, re.MULTILINE)


def test_explicit_partial_load_retains_site_po_conflicts_without_writing_projects_or_lines(tmp_path):
    import base64
    import gzip

    path = _csv(tmp_path, [_row(), _row(Site_Id="00999")])
    payload = build_payload([("US", path)])
    payload["issues"].append({
        "TableName": "prep.Site", "RecordKey": "00123", "Reason": "Conflicting country",
    })
    partial = render_handoff(payload, tmp_path)["popa-context-site-po-preview.sql"]
    block = partial.split("-- BEGIN GENERATED SOURCE PAYLOAD")[1].split("-- END GENERATED SOURCE PAYLOAD")[0]
    decoded = json.loads(gzip.decompress(base64.b64decode(
        "".join(re.findall(r"\+ N'([A-Za-z0-9+/=]+)'", block))
    )).decode("ascii"))
    assert decoded["sites"] == [{f: r[f] for f in SQL_FIELDS["sites"]} for r in payload["sites"]]
    assert decoded["purchaseOrders"] == [{f: r[f] for f in SQL_FIELDS["purchaseOrders"]}
                                        for r in payload["purchaseOrders"]]
    assert decoded["issues"] == [payload["issues"][-1]]
    assert "projects" not in decoded and "purchaseOrderLines" not in decoded
    assert decoded["pendingProjects"] == len(payload["projects"])
    assert decoded["pendingPurchaseOrderLines"] == len(payload["purchaseOrderLines"])
    assert set(re.findall(r"INSERT\s+INTO\s+(prep\.\w+)", partial, re.I)) == {
        "prep.Site", "prep.PurchaseOrder",
    }
    assert "DECLARE @ApplyChanges bit = 0;" in partial
    assert partial.index("IF EXISTS (SELECT 1 FROM #Issues)") < partial.index("INSERT INTO prep.Site")
    assert "ROLLBACK TRANSACTION" in partial and "BEGIN CATCH" in partial
    assert "SET @InsertedSites=@@ROWCOUNT;" in partial
    assert "SET @InsertedOrders=@@ROWCOUNT;" in partial
    assert "3_PENDING" in partial
    assert partial.index("ORDER BY ResultType,TableName,RecordKey;") < partial.index(
        "IF @ApplyChanges=0\n        ROLLBACK TRANSACTION;"
    )


def test_remaining_load_preserves_distributions_but_imports_only_line_core(tmp_path):
    import base64
    import gzip
    from tools.build_popa_context_load import remaining_table_payload

    source = _csv(tmp_path, [
        _row(PO_Invoice_Distribution_Number="1"),
        _row(PO_Invoice_Distribution_Number="2", Site_Id="00999", Task_Type="0020"),
    ])
    payload = build_payload([("CA", source)])
    assert len(payload["issues"]) == 1
    remaining = remaining_table_payload(payload)
    assert remaining["issues"] == [] and len(remaining["purchaseOrderLines"]) == 1
    core = remaining["purchaseOrderLines"][0]
    assert core["PONumber"] == "000456" and core["POLineNumber"] == 1
    assert core["PODescription"] == "Equipment" and core["BusinessUnit"] == "001"
    assert not ({"AFEProjectID", "SiteID", "POTaskCode", "VendorID"} & core.keys())
    assert [row["PO_Invoice_Distribution_Number"] for row in payload["sourceAllocations"]] == ["1", "2"]
    assert [row["Task_Type"] for row in payload["sourceAllocations"]] == ["0007", "0020"]
    parts = render_handoff(payload, tmp_path)
    script = parts["popa-context-project-line-preview.sql"]
    decoded = json.loads(gzip.decompress(base64.b64decode(
        "".join(re.findall(r"\+ N'([A-Za-z0-9+/=]+)'", script))
    )))
    assert decoded == remaining and decoded["allocationContextImported"] is False
    assert set(re.findall(r"INSERT\s+INTO\s+(prep\.\w+)", script, re.I)) == {
        "prep.Project", "prep.PurchaseOrderLine",
    }
    assert "BusinessUnit,NULL,NULL,NULL,NULL FROM #Lines" in script
    assert "ProjectStartDate,NULL" in script
    assert script.index("IF EXISTS (SELECT 1 FROM #Issues)") < script.index("INSERT INTO prep.Project")
    assert script.index("ORDER BY ResultType,TableName,RecordKey;") < script.index(
        "IF @ApplyChanges=0 ROLLBACK TRANSACTION;"
    )


@pytest.mark.parametrize("change", [
    {"PO_Description": "A conflicting description"},
    {"Bus_Entity": "002"},
    {"System_Name": "MACS"},
])
def test_remaining_load_still_blocks_conflicting_line_core_and_source_scope(tmp_path, change):
    from tools.build_popa_context_load import remaining_table_payload

    source = _csv(tmp_path, [_row(), _row(**change)])
    remaining = remaining_table_payload(build_payload([("US", source)]))
    assert any(issue["TableName"] == "prep.PurchaseOrderLine" for issue in remaining["issues"])


def test_nullable_schema_handoff_changes_only_required_existing_columns():
    from tools.build_popa_context_load import NULLABLE_SCHEMA

    schema = NULLABLE_SCHEMA.read_text(encoding="utf-8")
    assert set(re.findall(r"ALTER TABLE (prep\.\w+) ALTER COLUMN (\w+)", schema)) == {
        ("prep.Project", "ProjectTotalBudget"), ("prep.Project", "BundlingInScope"),
        ("prep.PurchaseOrderLine", "AFEProjectID"), ("prep.PurchaseOrderLine", "SiteID"),
        ("prep.PurchaseOrderLine", "POTaskCode"), ("prep.PurchaseOrderLine", "VendorID"),
    }
    assert "DECLARE @ApplyChanges bit = 0;" in schema
    assert not re.search(r"\b(?:UPDATE|DELETE|INSERT|CREATE TABLE)\s+(?:INTO\s+)?prep\.", schema, re.I)
