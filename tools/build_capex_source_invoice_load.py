"""Extract real invoice facts and build a private-network SQL load handoff."""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from autarch.capex.demo_v2_normalization import (
    JCWorkbookNormalizationResult,
    NormalizedInvoicePackage,
    normalize_abbyy_for_demo_v2,
    normalize_jc_workbook,
    read_xlsx_rows,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "examples" / "data" / "capex"
RUN_ID = "capex-demo-v2-normalization-2026-10-30"
CREATED_AT = datetime(2026, 10, 30, tzinfo=timezone.utc)
BEGIN_PAYLOAD = "-- BEGIN GENERATED SOURCE PAYLOAD"
END_PAYLOAD = "-- END GENERATED SOURCE PAYLOAD"
_HEADER_FIELDS = {
    "Transaction id", "Invoice Number", "Invoice Date", "Vendor/ID", "Vendor/Name",
    "Vendor/Invoice Vendor Name", "Vendor/Country", "Business Unit/ID",
    "Business Unit/Name", "Business Unit/Country", "Currency", "Total",
    "Work Order Number", "AFE Number", "CAPEX/OPEX", "Ingested Date",
    "Processing Audit", "Confidence", "Invoice Data/Due Date",
    "Invoice Data/Payment Term", "Taxes/Total Net Amount", "Taxes/Total Taxes",
    "Taxes/Non Taxable Amount", "Freight",
}
_JC_FIELDS = {
    "Account Number", "Account Description", "Do Ty", "Doc Number", "Doc Co",
    "G/L Date", "Explanation", "LT 1 Amount", "LT 1 Debit", "LT 1 Credit",
    "LT 2 Amount", "LT 2 Debit", "LT 2 Credit", "Cur Cod", "Exchange Rate",
    "LT", "Batch Number", "Batch Date", "JE Line Number", "Units", "UM",
    "Address Number", "Invoice Number", "Service/ Tax Date", "Business Unit",
    "Home Business Unit", "Explanation -Remark-", "Historical Rate",
    "Historical Date", "Asset Number", "Purchase Order", "Co",
}
_LINE_FIELDS = {
    "Article Number BU", "Article Number Vendor", "Currency", "Description",
    "Discount", "Discount Percentage", "Is Valid", "Job Type", "Line Notes",
    "Net Price", "Order Date", "Order Item Id", "Order Number", "Period",
    "Position", "Project", "Quantity", "Tax Amount", "Tax Code", "Tax Rate",
    "Total Price", "Unit Price", "Unit of Measurement",
}
_TEXT_LIMITS = {
    "packageid": 128, "schemaversion": 16, "runid": 128, "invoiceid": 128,
    "invoicenumber": 128, "vendorid": 128, "vendorname": 300,
    "invoicecurrency": 3, "currency": 3, "ponumber": 128, "countryregion": 100,
    "sourcesystem": 64, "nuvoloworkorderno": 128, "businessunit": 100,
    "projectstatus": 100, "projecttype": 100, "sourcepackagename": 1024,
    "sourcesha256": 64, "packagestatus": 16, "invoicelineid": 256,
    "description": 2000, "partnumber": 256, "podescription": 2000,
    "potaskcode": 128, "afeprojectid": 128, "siteid": 128,
}


def _one(root: Path, pattern: str) -> Path:
    paths = sorted(root.glob(pattern))
    if len(paths) != 1:
        raise ValueError(f"Expected one {pattern!r} source, found {len(paths)}")
    return paths[0]


def _nonempty(row: dict[str, str], fields: set[str]) -> dict[str, str]:
    return {key: value.strip() for key, value in row.items()
            if key in fields and value and value.strip()}


def _abbyy_rows(path: Path) -> tuple[dict[str, str], list[dict[str, str]], list[dict[str, str]]]:
    headers: list[dict[str, str]] = []
    lines: list[dict[str, str]] = []
    orders: list[dict[str, str]] = []
    with ZipFile(path) as archive:
        for member in archive.infolist():
            name = member.filename.replace("\\", "/").rsplit("/", 1)[-1]
            if not name.lower().endswith(".csv"):
                continue
            if member.file_size > 10 * 1024 * 1024:
                raise ValueError(f"Source CSV exceeds 10 MiB: {member.filename}")
            rows = list(csv.DictReader(io.StringIO(
                archive.read(member).decode("utf-8-sig")
            )))
            if name.startswith("Non-Trade Invoices"):
                headers.extend(rows)
            elif name.startswith("Line Items_"):
                lines.extend(rows)
            elif name.startswith("Purchase Order_"):
                orders.extend(rows)
    if len(headers) != 1 or not lines:
        raise ValueError(f"{path.name} requires one header and source line rows")
    return headers[0], lines, orders


def _money(value: float | None) -> float | None:
    if value is None:
        return None
    decimal = Decimal(str(value))
    if not decimal.is_finite() or abs(decimal) >= Decimal("1000000000000000"):
        raise ValueError(f"Source value exceeds decimal(19,4): {value}")
    quantized = decimal.quantize(Decimal("0.0001"))
    if abs(decimal - quantized) > Decimal("0.00000001"):
        raise ValueError(f"Source value cannot fit decimal(19,4) losslessly: {value}")
    return float(quantized)


def _reference_packages() -> dict[str, NormalizedInvoicePackage]:
    references: dict[str, NormalizedInvoicePackage] = {}
    for name in (
        "bruegmann.normalized.json", "kimley-horn.normalized.json",
        "jc2000a07814.normalized.json", "jc2000a07465.classification.json",
    ):
        data = json.loads((SOURCE_DIR / "normalized-v2" / name).read_text(encoding="utf-8"))
        packages = (
            JCWorkbookNormalizationResult.model_validate(data).packages
            if "packages" in data
            else [NormalizedInvoicePackage.model_validate(data)]
        )
        for package in packages:
            references[package.invoice_id] = package
    return references


def _source_integrity(
    package: NormalizedInvoicePackage,
    reference: NormalizedInvoicePackage,
) -> dict[str, Any]:
    excluded = {"source", "context_overlay"}
    if (
        package.model_dump(exclude=excluded) != reference.model_dump(exclude=excluded)
        or package.source.file_name != reference.source.file_name
        or package.source.source_system != reference.source.source_system
    ):
        raise ValueError(
            f"Source facts changed for {package.invoice_id}; review normalization "
            "before reusing the existing invoice/package identities."
        )
    return {
        "referenceSourceSha256": reference.source.sha256,
        "currentSourceSha256": package.source.sha256,
        "normalizedSourceFactsMatchReference": True,
        "sourceHashChanged": package.source.sha256 != reference.source.sha256,
        "comparisonScope": "Normalized source facts and identifiers; synthetic context excluded. Raw source bytes are identified separately by their current hash.",
    }


def _append_package(
    payload: dict[str, Any],
    package: NormalizedInvoicePackage,
    metadata: dict[str, Any],
    raw_lines: list[dict[str, str]],
    business_unit: str | None,
    work_order: str | None = None,
) -> None:
    if len(raw_lines) != len(package.lines):
        raise ValueError(f"Source row alignment failed for {package.invoice_id}")
    if package.invoice_number is None or package.invoice_total is None or package.currency is None:
        raise ValueError(f"Missing mandatory header facts for {package.invoice_id}")
    if package.invoice_date is not None:
        date.fromisoformat(package.invoice_date)
    metadata["dataQuality"] = package.data_quality.model_dump(by_alias=True)
    metadata["missingHeaderFields"] = [
        key for key, value in (("InvoiceDate", package.invoice_date), ("VendorID", package.vendor_id))
        if value is None
    ]
    metadata["normalizationSchemaVersion"] = package.schema_version
    metadata["normalizationStatus"] = package.normalization_status.value
    metadata["loadControl"] = (
        "PREPARING and BundlingInScope=0 are staging controls, not source business assertions. "
        "No synthetic context, ready event, task mapping, or accounting result was applied."
    )
    payload["invoices"].append({
        "InvoiceID": package.invoice_id, "InvoiceNumber": package.invoice_number,
        "InvoiceDate": package.invoice_date, "VendorID": package.vendor_id,
        "VendorName": package.vendor_name, "InvoiceCurrency": package.currency,
        "InvoiceTotal": _money(package.invoice_total), "PONumber": package.po_number,
        "CountryRegion": package.country_region, "SourceSystem": package.source.source_system,
        "NuvoloWorkOrderNo": work_order, "HasLineItems": True, "RunID": package.run_id,
    })
    payload["packages"].append({
        "PackageId": package.package_id, "SchemaVersion": "1.0", "RunId": package.run_id,
        "InvoiceId": package.invoice_id, "InvoiceNumber": package.invoice_number,
        "InvoiceDate": package.invoice_date, "VendorId": package.vendor_id,
        "VendorName": package.vendor_name, "Currency": package.currency,
        "InvoiceTotal": _money(package.invoice_total), "PoNumber": package.po_number,
        "CountryRegion": package.country_region, "SourceSystem": package.source.source_system,
        "BusinessUnit": business_unit, "ProjectStatus": None, "ProjectType": None,
        "BundlingInScope": False, "MissingPo": package.po_number is None,
        "MissingProject": True, "MissingSite": True,
        "SourcePackageName": package.source.file_name, "SourceSha256": package.source.sha256,
        "CreatedAtUtc": package.created_at_utc.isoformat(), "PackageStatus": "PREPARING",
        "SourceMetadataJson": metadata,
    })
    for line, raw in zip(package.lines, raw_lines):
        payload["lines"].append({
            "PackageId": package.package_id, "InvoiceLineId": line.line_id,
            "InvoiceID": package.invoice_id, "LineNumber": line.line_number,
            "Description": line.description, "PartNumber": line.part_number,
            "Quantity": _money(line.quantity), "UnitCost": _money(line.unit_cost),
            "LineAmount": _money(line.line_amount), "PoLineNumber": None,
            "PoDescription": None, "PoTaskCode": None, "AfeProjectId": None,
            "SiteId": None, "SourceLineJson": raw,
        })


def extract_sources(source_dir: Path = SOURCE_DIR) -> dict[str, Any]:
    """Keep distinct source invoices separate, including both JC workbooks."""
    payload: dict[str, Any] = {
        "schemaVersion": "1.0-source-invoice-load",
        "invoices": [], "packages": [], "lines": [], "sourceFiles": [],
    }
    references = _reference_packages()
    for pattern in ("Bruegmann*.zip", "Kimley*.zip"):
        path = _one(source_dir, pattern)
        package = normalize_abbyy_for_demo_v2(path, run_id=RUN_ID, created_at_utc=CREATED_AT)
        header, lines, orders = _abbyy_rows(path)
        metadata = {
            "sourceIntegrity": _source_integrity(package, references[package.invoice_id]),
            "headerFields": _nonempty(header, _HEADER_FIELDS),
            "orderCandidates": [
                _nonempty(row, {"Order Checked", "Order Number", "Total"})
                for row in orders if row.get("Order Number", "").strip()
            ],
            "orderSelection": "No single confirmed PO identified; unchecked candidates are not a PO assignment.",
        }
        _append_package(
            payload, package, metadata, [_nonempty(row, _LINE_FIELDS) for row in lines],
            header.get("Business Unit/ID", "").strip() or header.get("Business Unit/Name", "").strip() or None,
            header.get("Work Order Number", "").strip() or None,
        )
        payload["sourceFiles"].append({
            "fileName": path.name, "sha256": package.source.sha256,
            "invoiceCount": 1, "excludedRecords": [],
        })
    for pattern in ("JC2000A07465*.xlsx", "JC2000A07814*.xlsx"):
        path = _one(source_dir, pattern)
        result = normalize_jc_workbook(path, run_id=RUN_ID, created_at_utc=CREATED_AT)
        rows = read_xlsx_rows(path)
        for package in result.packages:
            selected = [
                row for row in rows
                if row.get("Do Ty", "").strip().upper() == "AI"
                and row.get("Invoice Number", "").strip() == package.invoice_number
            ]
            metadata = {
                "sourceIntegrity": _source_integrity(package, references[package.invoice_id]),
                "sourceProjectBusinessUnit": result.project_id,
                "sourceAddressNumbers": sorted({
                    row["Address Number"].strip() for row in selected
                    if row.get("Address Number", "").strip()
                }),
                "addressNumberMapping": "SOURCE_ADDRESS_NUMBER_ONLY_NOT_CONFIRMED_VENDOR_ID",
                "dateMapping": "G/L, batch and service/tax dates are retained under their source labels; none is assumed to be InvoiceDate.",
                "sourceDatesAreExcelSerials": True,
                "invoiceScope": "AI invoice records only; JP/OV rows are not invoice lines.",
                "excludedRecords": [item.model_dump(by_alias=True) for item in result.excluded_records],
                "relatedNonInvoiceRows": [
                    _nonempty(row, _JC_FIELDS) for row in rows
                    if row.get("Do Ty", "").strip()
                    and row.get("Do Ty", "").strip().upper() != "AI"
                ],
            }
            _append_package(payload, package, metadata,
                            [_nonempty(row, _JC_FIELDS) for row in selected], result.project_id)
        payload["sourceFiles"].append({
            "fileName": path.name, "sha256": result.source.sha256,
            "invoiceCount": len(result.packages),
            "excludedRecords": [item.model_dump(by_alias=True) for item in result.excluded_records],
        })
    if len({row["InvoiceID"] for row in payload["invoices"]}) != len(payload["invoices"]):
        raise ValueError("Source invoices do not have unique identities")
    for table in ("invoices", "packages", "lines"):
        for row in payload[table]:
            for field, value in row.items():
                if isinstance(value, str) and field.lower() in _TEXT_LIMITS:
                    if len(value.encode("utf-16-le")) // 2 > _TEXT_LIMITS[field.lower()]:
                        raise ValueError(f"{table}.{field} exceeds SQL storage length")
    return payload


def render_sql(template: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    declaration = f"{BEGIN_PAYLOAD}\nDECLARE @Payload nvarchar(max) = N'{encoded.replace(chr(39), chr(39) * 2)}';\n{END_PAYLOAD}"
    result, count = re.subn(
        re.escape(BEGIN_PAYLOAD) + r".*?" + re.escape(END_PAYLOAD),
        lambda _: declaration, template, flags=re.DOTALL,
    )
    if count != 1:
        raise ValueError("SQL handoff requires exactly one source payload block")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--sql-output", type=Path,
                        default=ROOT / "sql" / "admin" / "load_capex_demo_source_invoices.sql")
    parser.add_argument("--json-output", type=Path,
                        default=SOURCE_DIR / "source-load" / "invoice-source-extract.json")
    args = parser.parse_args()
    payload = extract_sources(args.source_dir)
    sql = render_sql(args.sql_output.read_text(encoding="utf-8"), payload)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    args.sql_output.write_text(sql, encoding="utf-8")
    print(f"Extracted {len(payload['invoices'])} invoices, {len(payload['packages'])} packages, "
          f"{len(payload['lines'])} lines. SQL was generated, not executed.")
    print(args.json_output)
    print(args.sql_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
