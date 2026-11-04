import hashlib
import json
import re
from pathlib import Path

import pytest

from tools.build_capex_demo_v2_request import _load_packages
from tools.build_capex_source_invoice_load import (
    BEGIN_PAYLOAD,
    CREATED_AT,
    END_PAYLOAD,
    ROOT,
    SOURCE_DIR,
    _money,
    _source_integrity,
    extract_sources,
    render_sql,
)


@pytest.fixture(scope="module")
def extraction() -> dict:
    return extract_sources()


def test_extracts_all_distinct_invoice_records_from_both_jc_files(extraction: dict) -> None:
    assert len(extraction["invoices"]) == 10
    assert len(extraction["packages"]) == 10
    assert len(extraction["lines"]) == 30
    assert len(extraction["sourceFiles"]) == 4
    counts = {item["fileName"]: item["invoiceCount"] for item in extraction["sourceFiles"]}
    assert counts["JC2000A07465-Ariba.xlsx"] == 7
    assert counts["JC2000A07814 - Ariba+Proj.tool.xlsx"] == 1
    assert len({row["InvoiceID"] for row in extraction["invoices"]}) == 10
    excluded = [
        row for source in extraction["sourceFiles"] for row in source["excludedRecords"]
    ]
    assert len(excluded) == 7
    assert {row["transactionType"] for row in excluded} == {"JP", "OV"}
    for package in extraction["packages"]:
        source = SOURCE_DIR / package["SourcePackageName"]
        assert package["SourceSha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_preserves_original_three_invoice_and_line_identities(extraction: dict) -> None:
    packages = {row["PackageId"]: row for row in extraction["packages"]}
    lines = {row["InvoiceLineId"]: row for row in extraction["lines"]}
    for _, original in _load_packages():
        loaded = packages[original.package_id]
        assert loaded["InvoiceId"] == original.invoice_id
        assert loaded["RunId"] == original.run_id
        assert loaded["CreatedAtUtc"] == CREATED_AT.isoformat()
        assert loaded["SourceSha256"] == hashlib.sha256(
            (SOURCE_DIR / loaded["SourcePackageName"]).read_bytes()
        ).hexdigest()
        integrity = loaded["SourceMetadataJson"]["sourceIntegrity"]
        assert integrity["referenceSourceSha256"] == original.source.sha256
        assert integrity["currentSourceSha256"] == loaded["SourceSha256"]
        assert integrity["normalizedSourceFactsMatchReference"] is True
        for line in original.lines:
            row = lines[line.line_id]
            assert row["PackageId"] == original.package_id
            assert row["InvoiceID"] == original.invoice_id
            assert row["Description"] == line.description
            assert row["Quantity"] == line.quantity
            assert row["UnitCost"] == line.unit_cost
            assert row["LineAmount"] == line.line_amount


def test_captures_confirmed_vendor_ids_and_preserves_jc_address_numbers(extraction: dict) -> None:
    invoices = {row["InvoiceNumber"]: row for row in extraction["invoices"]}
    assert invoices["INV12-62027"]["VendorID"] == "2153631"
    assert invoices["36764068-GRP005"]["VendorID"] == "2117718"
    assert invoices["INV12-62027"]["InvoiceDate"] == "2026-08-26"
    assert invoices["36764068-GRP005"]["InvoiceDate"] == "2026-07-31"
    assert invoices["20294"]["VendorName"] == "GILBARCO GMBH"
    assert invoices["20294"]["InvoiceCurrency"] == "EUR"
    assert invoices["20294"]["InvoiceTotal"] == 16123.79
    assert invoices["20294"]["VendorID"] is None
    assert invoices["20294"]["InvoiceDate"] is None
    jc = next(row for row in extraction["packages"] if row["InvoiceNumber"] == "20294")
    assert jc["BusinessUnit"] == "JC2000A07814"
    assert jc["SourceMetadataJson"]["sourceAddressNumbers"] == ["15862618"]
    assert set(jc["SourceMetadataJson"]["missingHeaderFields"]) == {"InvoiceDate", "VendorID"}
    assert jc["SourceMetadataJson"]["relatedNonInvoiceRows"][0]["Purchase Order"] == "12038910"
    assert jc["PoNumber"] is None
    for line in extraction["lines"]:
        if line["InvoiceID"].startswith("JC"):
            assert line["Quantity"] is None
            assert line["UnitCost"] is None
            assert line["PoTaskCode"] is None
            assert line["SourceLineJson"]["Do Ty"] == "AI"
            assert line["SourceLineJson"]["Address Number"]


def test_keeps_ambiguous_orders_and_tax_context_without_fabrication(extraction: dict) -> None:
    bruegmann = next(row for row in extraction["packages"] if row["VendorId"] == "2153631")
    metadata = bruegmann["SourceMetadataJson"]
    assert {row["Order Number"] for row in metadata["orderCandidates"]} == {
        "831249", "SO12-85719", "2746514"
    }
    assert all(row["Order Checked"] == "False" for row in metadata["orderCandidates"])
    assert metadata["headerFields"]["Taxes/Total Taxes"] == "99.33"
    assert bruegmann["PoNumber"] is None
    assert bruegmann["BusinessUnit"] == "Circle K Northern Tier BU"
    shipping = next(row for row in extraction["lines"] if row["Description"] == "Shipping")
    assert shipping["Quantity"] is None
    assert shipping["UnitCost"] is None
    assert shipping["LineAmount"] == 617.09
    assert all(row["ProjectStatus"] is None for row in extraction["packages"])
    assert all(row["PackageStatus"] == "PREPARING" for row in extraction["packages"])
    assert all(row["BundlingInScope"] is False for row in extraction["packages"])
    assert all(row["AfeProjectId"] is None and row["SiteId"] is None for row in extraction["lines"])
    encoded = json.dumps(extraction)
    assert "DEMO-PROJECT-" not in encoded
    assert "DEMO-SITE-" not in encoded
    assert "Vendor/Bank Account" not in encoded
    assert "Vendor/Bank Code" not in encoded


def test_sql_rendering_is_repeatable_and_escapes_quotes(extraction: dict) -> None:
    template = (ROOT / "sql" / "admin" / "load_capex_demo_source_invoices.sql").read_text(
        encoding="utf-8"
    )
    rendered = render_sql(template, extraction)
    assert rendered == render_sql(rendered, extraction)
    match = re.search(
        re.escape(BEGIN_PAYLOAD) + r"\nDECLARE @Payload nvarchar\(max\) = N'(.*?)';\n"
        + re.escape(END_PAYLOAD), rendered, re.DOTALL,
    )
    assert match is not None
    assert json.loads(match.group(1).replace("''", "'")) == extraction
    assert "O''Brien" in render_sql(template, {"vendor": "O'Brien"})
    with pytest.raises(ValueError, match="exactly one"):
        render_sql("missing markers", extraction)


def test_load_is_atomic_replay_safe_and_only_writes_the_three_requested_tables() -> None:
    sql = (ROOT / "sql" / "admin" / "load_capex_demo_source_invoices.sql").read_text(
        encoding="utf-8"
    )
    assert set(re.findall(r"INSERT INTO (\w+\.\w+)", sql)) == {
        "prep.Invoice", "prep.InvoicePackage", "prep.InvoiceLine"
    }
    assert not re.search(r"\b(?:UPDATE|DELETE FROM|TRUNCATE TABLE)\s+\w+\.", sql, re.I)
    assert sql.index("BEGIN TRANSACTION;") < sql.index("INTO #SourceInvoice")
    assert "No data was overwritten." in sql
    assert "WITH (TABLOCKX, HOLDLOCK)" in sql
    assert "SOURCE_INVOICES_LOADED" in sql
    assert "EXCEPT SELECT * FROM #SourceInvoice" in sql
    assert "EXCEPT SELECT * FROM #SourcePackage" in sql
    assert "EXCEPT SELECT * FROM #SourceLine" in sql
    assert "Persisted source values do not match" in sql
    assert "EXCEPT SELECT * FROM ref.TaskMaster" in sql
    assert "EXCEPT SELECT * FROM #TaskMasterBeforeLoad" in sql
    assert "IF XACT_STATE() <> 0" in sql
    assert "ROLLBACK TRANSACTION;" in sql
    assert "DROP TABLE IF EXISTS #SourceInvoice;" in sql


def test_source_gap_migration_preserves_existing_values_and_runtime_contract() -> None:
    sql = (
        ROOT / "sql" / "migrations" / "010_preserve_source_invoice_gaps.sql"
    ).read_text(encoding="utf-8")
    for change in (
        "prep.Invoice ALTER COLUMN InvoiceDate date NULL",
        "prep.Invoice ALTER COLUMN VendorID nvarchar(128) NULL",
        "prep.InvoiceLine ALTER COLUMN Quantity decimal(19,4) NULL",
        "prep.InvoiceLine ALTER COLUMN UnitCost decimal(19,4) NULL",
    ):
        assert change in sql
    assert "ADD SourceMetadataJson nvarchar(max) NULL" in sql
    assert "ADD SourceLineJson nvarchar(max) NULL" in sql
    assert sql.count("ISJSON(") == 2
    assert "ADD CONSTRAINT UQ_DM_Invoice_Vendor_Number" in sql
    assert "UNIQUE (VendorID, InvoiceNumber)" in sql
    assert "WITH CHECK ADD CONSTRAINT CK_InvoiceLine_Quantity" in sql
    assert "CHECK (Quantity >= 0)" in sql
    assert not re.search(r"\b(?:INSERT INTO|UPDATE|DELETE FROM)\s+\w+\.", sql, re.I)
    view = (
        ROOT / "sql" / "migrations" / "002_create_published_invoice_package_view.sql"
    ).read_text(encoding="utf-8")
    assert "WHERE p.PackageStatus = 'READY'" in view


def test_decimal_storage_never_turns_missing_into_zero() -> None:
    assert _money(None) is None
    assert _money(0) == 0
    assert _money(36552.800000000003) == 36552.8
    with pytest.raises(ValueError, match="losslessly"):
        _money(1.23456)
    with pytest.raises(ValueError, match="exceeds"):
        _money(1e15)


def test_source_integrity_allows_hash_changes_only_when_source_facts_still_match() -> None:
    original = _load_packages()[0][1].model_copy(update={"context_overlay": None})
    changed_hash = original.model_copy(
        update={"source": original.source.model_copy(update={"sha256": "a" * 64})}
    )
    assert _source_integrity(changed_hash, original)["sourceHashChanged"] is True
    changed_facts = changed_hash.model_copy(update={"vendor_id": "different-vendor"})
    with pytest.raises(ValueError, match="Source facts changed"):
        _source_integrity(changed_facts, original)
