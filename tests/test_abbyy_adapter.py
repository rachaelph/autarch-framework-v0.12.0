from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any

import pytest

from autarch.adapters.abbyy import (
    ABBYYInvoiceAdapter,
    ABBYYPackageError,
    parse_abbyy_archive,
)
from autarch.contracts import Action


HEADER_FIELDS = [
    "Transaction id",
    "Invoice Number",
    "Invoice Date",
    "Business Unit/Country",
    "Business Unit/ID",
    "Business Unit/State",
    "Currency",
    "Total",
    "Vendor/ID",
    "Vendor/Name",
    "Vendor/Invoice Vendor Name",
    "Confidence",
    "Work Order Number",
    "Vendor/Bank Account",
]

LINE_FIELDS = [
    "Article Number BU",
    "Article Number Vendor",
    "Description",
    "Position",
    "Quantity",
    "Total Price",
    "Net Price",
    "Unit Price",
    "Job Type",
    "Project",
]


def csv_bytes(
    fieldnames: list[str],
    rows: list[dict[str, Any]],
) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=fieldnames,
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def valid_header() -> dict[str, Any]:
    return {
        "Transaction id": "transaction-1",
        "Invoice Number": "INV-001",
        "Invoice Date": "2026-10-19",
        "Business Unit/Country": "US",
        "Business Unit/ID": "site-1",
        "Business Unit/State": "MN",
        "Currency": "USD",
        "Total": "$15.00",
        "Vendor/ID": "vendor-1",
        "Vendor/Name": "Test Vendor",
        "Vendor/Invoice Vendor Name": "",
        "Confidence": "98.5%",
        "Work Order Number": "",
        "Vendor/Bank Account": "must-not-be-returned",
    }


def valid_lines() -> list[dict[str, Any]]:
    return [
        {
            "Article Number BU": "PART-1",
            "Article Number Vendor": "",
            "Description": "Test item",
            "Position": "1",
            "Quantity": "1",
            "Total Price": "$10.00",
            "Net Price": "",
            "Unit Price": "$10.00",
            "Job Type": "",
            "Project": "",
        },
        {
            "Article Number BU": "",
            "Article Number Vendor": "",
            "Description": "Shipping",
            "Position": "2",
            "Quantity": "",
            "Total Price": "$5.00",
            "Net Price": "",
            "Unit Price": "",
            "Job Type": "",
            "Project": "",
        },
    ]


def write_package(
    path: Path,
    *,
    headers: list[dict[str, Any]] | None = None,
    lines: list[dict[str, Any]] | None = None,
    header_fields: list[str] | None = None,
    line_fields: list[str] | None = None,
    include_header: bool = True,
    include_lines: bool = True,
    extra_header: bool = False,
) -> Path:
    header_fields = header_fields or HEADER_FIELDS
    line_fields = line_fields or LINE_FIELDS
    headers = headers if headers is not None else [valid_header()]
    lines = lines if lines is not None else valid_lines()

    with zipfile.ZipFile(
        path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        if include_header:
            archive.writestr(
                "package/Non-Trade Invoices - US.csv",
                csv_bytes(header_fields, headers),
            )
        if extra_header:
            archive.writestr(
                "other/Non-Trade Invoices - Duplicate.csv",
                csv_bytes(header_fields, headers),
            )
        if include_lines:
            archive.writestr(
                "package/Line Items_123.csv",
                csv_bytes(line_fields, lines),
            )

    return path


def test_parse_abbyy_archive_returns_normalized_invoice(
    tmp_path: Path,
) -> None:
    path = write_package(tmp_path / "invoice.zip")

    invoice = parse_abbyy_archive(path)

    assert invoice["invoice_id"] == "transaction-1"
    assert invoice["invoice_number"] == "INV-001"
    assert invoice["vendor"] == "Test Vendor"
    assert invoice["currency"] == "USD"
    assert invoice["total"] == 15.0
    assert invoice["region"] == "North America"
    assert len(invoice["lines"]) == 2
    assert invoice["lines"][0]["part_number"] == "PART-1"
    assert invoice["lines"][1]["quantity"] == 0.0
    assert invoice["source_sha256"] == hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    assert "Vendor/Bank Account" not in invoice
    assert "must-not-be-returned" not in repr(invoice)


def test_governed_adapter_lists_and_reads_packages(
    tmp_path: Path,
) -> None:
    root = tmp_path / "invoices"
    root.mkdir()
    write_package(root / "invoice.zip")

    adapter = ABBYYInvoiceAdapter(str(root))

    listed = adapter.execute(
        Action("invoice.list_abbyy")
    )
    assert listed.ok is True
    assert listed.output == {
        "packages": ["invoice.zip"],
        "count": 1,
    }

    read = adapter.execute(
        Action(
            "invoice.read_abbyy",
            {"path": "invoice.zip"},
        )
    )
    assert read.ok is True
    assert read.output["invoice_number"] == "INV-001"


def test_governed_adapter_rejects_path_escape(
    tmp_path: Path,
) -> None:
    root = tmp_path / "invoices"
    root.mkdir()
    write_package(tmp_path / "outside.zip")

    result = ABBYYInvoiceAdapter(str(root)).execute(
        Action(
            "invoice.read_abbyy",
            {"path": "../outside.zip"},
        )
    )

    assert result.ok is False
    assert "escapes invoice root" in result.error


@pytest.mark.parametrize(
    "options,error",
    [
        (
            {"include_header": False},
            "exactly one invoice header CSV",
        ),
        (
            {"include_lines": False},
            "exactly one line-item CSV",
        ),
        (
            {"extra_header": True},
            "exactly one invoice header CSV",
        ),
    ],
)
def test_invalid_member_layout_is_rejected(
    tmp_path: Path,
    options: dict[str, Any],
    error: str,
) -> None:
    path = write_package(
        tmp_path / "invalid.zip",
        **options,
    )

    with pytest.raises(ABBYYPackageError, match=error):
        parse_abbyy_archive(path)


def test_missing_required_column_is_rejected(
    tmp_path: Path,
) -> None:
    fields = [
        field
        for field in HEADER_FIELDS
        if field != "Transaction id"
    ]
    header = valid_header()
    header.pop("Transaction id")

    path = write_package(
        tmp_path / "missing-column.zip",
        header_fields=fields,
        headers=[header],
    )

    with pytest.raises(
        ABBYYPackageError,
        match="missing columns: Transaction id",
    ):
        parse_abbyy_archive(path)


def test_invalid_numeric_value_is_rejected(
    tmp_path: Path,
) -> None:
    header = valid_header()
    header["Total"] = "not-a-number"

    path = write_package(
        tmp_path / "invalid-number.zip",
        headers=[header],
    )

    with pytest.raises(
        ABBYYPackageError,
        match="invalid numeric field 'Total'",
    ):
        parse_abbyy_archive(path)


def test_duplicate_line_identifier_is_rejected(
    tmp_path: Path,
) -> None:
    lines = valid_lines()
    lines[1]["Position"] = "1"

    path = write_package(
        tmp_path / "duplicate-line.zip",
        lines=lines,
    )

    with pytest.raises(
        ABBYYPackageError,
        match="line identifier is duplicated",
    ):
        parse_abbyy_archive(path)


def test_adapter_is_publicly_exported() -> None:
    from autarch.adapters import (
        ABBYYInvoiceAdapter as ExportedAdapter,
    )

    assert ExportedAdapter is ABBYYInvoiceAdapter
