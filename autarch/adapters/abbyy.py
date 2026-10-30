"""Governed deterministic ingestion of ABBYY Vantage invoice ZIP packages."""
from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..contracts import Action, ActionResult
from .base import Adapter


_US_COUNTRIES = {
    "US",
    "USA",
    "UNITED STATES",
    "UNITED STATES OF AMERICA",
}
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_DEFAULT_MAX_CSV_BYTES = 10 * 1024 * 1024


class ABBYYPackageError(ValueError):
    """Raised when an ABBYY package is missing or contains invalid data."""


def _member_basename(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def _select_member(
    names: Sequence[str],
    *,
    label: str,
    predicate: Any,
) -> str:
    matches = [
        name
        for name in names
        if not name.endswith("/") and predicate(_member_basename(name))
    ]
    if len(matches) != 1:
        raise ABBYYPackageError(
            f"ABBYY package must contain exactly one {label}; "
            f"found {len(matches)}"
        )
    return matches[0]


def _csv_rows(
    archive: zipfile.ZipFile,
    member: str,
    *,
    max_csv_bytes: int,
) -> Tuple[List[Dict[str, str]], List[str]]:
    info = archive.getinfo(member)
    if info.file_size > max_csv_bytes:
        raise ABBYYPackageError(
            f"ABBYY CSV member exceeds {max_csv_bytes} bytes: {member}"
        )

    try:
        text = archive.read(member).decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise ABBYYPackageError(
            f"ABBYY CSV member is not valid UTF-8: {member}"
        ) from exc

    reader = csv.DictReader(io.StringIO(text))
    fieldnames = list(reader.fieldnames or [])
    if not fieldnames:
        raise ABBYYPackageError(
            f"ABBYY CSV member has no header row: {member}"
        )

    return list(reader), fieldnames


def _require_columns(
    fieldnames: Sequence[str],
    required: Sequence[str],
    *,
    member: str,
) -> None:
    missing = sorted(set(required) - set(fieldnames))
    if missing:
        raise ABBYYPackageError(
            f"ABBYY CSV member {member} is missing columns: "
            f"{', '.join(missing)}"
        )


def _required_text(
    row: Mapping[str, Any],
    field: str,
    *,
    record: str,
) -> str:
    value = str(row.get(field) or "").strip()
    if not value:
        raise ABBYYPackageError(
            f"ABBYY {record} is missing required field '{field}'"
        )
    return value


def _first_text(
    row: Mapping[str, Any],
    fields: Sequence[str],
) -> str:
    for field in fields:
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def _number(
    value: Any,
    *,
    field: str,
    record: str,
    blank_value: Optional[float] = None,
) -> float:
    text = str(value or "").strip()
    if not text:
        if blank_value is not None:
            return blank_value
        raise ABBYYPackageError(
            f"ABBYY {record} is missing numeric field '{field}'"
        )

    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]

    normalized = (
        text.replace(",", "")
        .replace("$", "")
        .strip()
    )

    try:
        result = float(normalized)
    except ValueError as exc:
        raise ABBYYPackageError(
            f"ABBYY {record} has invalid numeric field "
            f"'{field}': {value!r}"
        ) from exc

    if not math.isfinite(result):
        raise ABBYYPackageError(
            f"ABBYY {record} has non-finite numeric field '{field}'"
        )

    return -result if negative else result


def _optional_number(
    value: Any,
    *,
    field: str,
    record: str,
) -> Optional[float]:
    if not str(value or "").strip():
        return None
    return _number(
        value,
        field=field,
        record=record,
    )


def parse_abbyy_archive(
    path: Path,
    *,
    max_csv_bytes: int = _DEFAULT_MAX_CSV_BYTES,
    preserve_missing_numbers: bool = False,
) -> Dict[str, Any]:
    """Parse one ABBYY ZIP without extracting files or invoking a model."""
    source_path = Path(path)
    if source_path.suffix.lower() != ".zip":
        raise ABBYYPackageError(
            f"ABBYY input must be a ZIP package: {source_path.name}"
        )
    if not source_path.is_file():
        raise ABBYYPackageError(
            f"ABBYY package not found: {source_path}"
        )

    source_bytes = source_path.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()

    try:
        with zipfile.ZipFile(io.BytesIO(source_bytes)) as archive:
            names = archive.namelist()

            header_member = _select_member(
                names,
                label="invoice header CSV",
                predicate=lambda name: (
                    name.lower().endswith(".csv")
                    and name.startswith("Non-Trade Invoices")
                    and "Line Items" not in name
                ),
            )
            lines_member = _select_member(
                names,
                label="line-item CSV",
                predicate=lambda name: (
                    name.lower().endswith(".csv")
                    and name.startswith("Line Items_")
                ),
            )

            header_rows, header_fields = _csv_rows(
                archive,
                header_member,
                max_csv_bytes=max_csv_bytes,
            )
            line_rows, line_fields = _csv_rows(
                archive,
                lines_member,
                max_csv_bytes=max_csv_bytes,
            )
    except zipfile.BadZipFile as exc:
        raise ABBYYPackageError(
            f"ABBYY package is not a valid ZIP: {source_path.name}"
        ) from exc

    _require_columns(
        header_fields,
        [
            "Transaction id",
            "Invoice Number",
            "Invoice Date",
            "Business Unit/Country",
            "Currency",
            "Total",
        ],
        member=header_member,
    )
    _require_columns(
        line_fields,
        [
            "Description",
            "Position",
            "Quantity",
            "Total Price",
            "Unit Price",
        ],
        member=lines_member,
    )

    if len(header_rows) != 1:
        raise ABBYYPackageError(
            "ABBYY invoice header CSV must contain exactly one row; "
            f"found {len(header_rows)}"
        )
    if not line_rows:
        raise ABBYYPackageError(
            "ABBYY line-item CSV must contain at least one row"
        )

    header = header_rows[0]
    invoice_id = _required_text(
        header,
        "Transaction id",
        record="invoice header",
    )
    invoice_number = _required_text(
        header,
        "Invoice Number",
        record="invoice header",
    )
    country = _required_text(
        header,
        "Business Unit/Country",
        record="invoice header",
    )
    currency = _required_text(
        header,
        "Currency",
        record="invoice header",
    ).upper()
    if not _CURRENCY.fullmatch(currency):
        raise ABBYYPackageError(
            f"ABBYY invoice header has invalid currency: {currency!r}"
        )

    vendor = _first_text(
        header,
        ["Vendor/Name", "Vendor/Invoice Vendor Name"],
    )
    if not vendor:
        raise ABBYYPackageError(
            "ABBYY invoice header is missing a vendor name"
        )

    invoice_total = _number(
        header.get("Total"),
        field="Total",
        record="invoice header",
    )
    if invoice_total < 0:
        raise ABBYYPackageError(
            "ABBYY invoice total cannot be negative"
        )

    lines: List[Dict[str, Any]] = []
    line_ids = set()

    for index, row in enumerate(line_rows, start=1):
        record = f"line {index}"
        description = _required_text(
            row,
            "Description",
            record=record,
        )
        line_id = str(row.get("Position") or index).strip()
        if line_id in line_ids:
            raise ABBYYPackageError(
                f"ABBYY line identifier is duplicated: {line_id}"
            )
        line_ids.add(line_id)

        amount_field = (
            "Total Price"
            if str(row.get("Total Price") or "").strip()
            else "Net Price"
        )
        amount = _number(
            row.get(amount_field),
            field=amount_field,
            record=record,
        )
        quantity = (
            _optional_number(
                row.get("Quantity"),
                field="Quantity",
                record=record,
            )
            if preserve_missing_numbers
            else _number(
                row.get("Quantity"),
                field="Quantity",
                record=record,
                blank_value=0.0,
            )
        )
        unit_price = (
            _optional_number(
                row.get("Unit Price"),
                field="Unit Price",
                record=record,
            )
            if preserve_missing_numbers
            else _number(
                row.get("Unit Price"),
                field="Unit Price",
                record=record,
                blank_value=0.0,
            )
        )
        if quantity is not None and quantity < 0:
            raise ABBYYPackageError(
                f"ABBYY {record} quantity cannot be negative"
            )

        lines.append(
            {
                "line_id": line_id,
                "description": description,
                "part_number": _first_text(
                    row,
                    [
                        "Article Number BU",
                        "Article Number Vendor",
                    ],
                ),
                "quantity": quantity,
                "unit_price": unit_price,
                "amount": amount,
                "actual_task": _first_text(
                    row,
                    ["Job Type", "Project"],
                ),
            }
        )

    return {
        "invoice_id": invoice_id,
        "transaction_id": invoice_id,
        "invoice_number": invoice_number,
        "invoice_date": str(
            header.get("Invoice Date") or ""
        ).strip(),
        "vendor_id": str(
            header.get("Vendor/ID") or ""
        ).strip(),
        "vendor": vendor,
        "country": country,
        "region": (
            "North America"
            if country.upper() in _US_COUNTRIES
            else "Europe"
        ),
        "state": str(
            header.get("Business Unit/State") or ""
        ).strip(),
        "business_unit_id": str(
            header.get("Business Unit/ID") or ""
        ).strip(),
        "currency": currency,
        "total": invoice_total,
        "po_number": "",
        "afe_number": str(
            header.get("AFE Number") or ""
        ).strip(),
        "project_number": str(
            header.get("Work Order Number") or ""
        ).strip(),
        "business_capex_opex": str(
            header.get("CAPEX/OPEX") or ""
        ).strip(),
        "lines": lines,
        "source": source_path.name,
        "source_sha256": source_sha256,
        "abbyy_confidence": _number(
            str(header.get("Confidence") or "").replace("%", ""),
            field="Confidence",
            record="invoice header",
            blank_value=0.0,
        ),
    }


class ABBYYInvoiceAdapter(Adapter):
    """Governed read-only adapter for ABBYY Vantage invoice packages."""

    name = "abbyy_invoice"

    def __init__(
        self,
        root: str = "./invoices",
        *,
        max_csv_bytes: int = _DEFAULT_MAX_CSV_BYTES,
    ):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_csv_bytes = max_csv_bytes

    def capabilities(self) -> List[str]:
        return [
            "invoice.read_abbyy",
            "invoice.list_abbyy",
        ]

    def schema(self) -> Dict[str, Dict[str, str]]:
        return {
            "invoice.read_abbyy": {
                "path": "string (ABBYY Vantage ZIP package)",
            },
            "invoice.list_abbyy": {},
        }

    def normalize_params(
        self,
        capability: str,
        params: dict,
    ) -> dict:
        if not isinstance(params, dict):
            return {}
        normalized = dict(params)
        if "path" not in normalized:
            for synonym in (
                "file",
                "filename",
                "invoice",
                "package",
            ):
                if synonym in normalized:
                    normalized["path"] = normalized[synonym]
                    break
        return normalized

    def execute(self, action: Action) -> ActionResult:
        try:
            params = self.normalize_params(
                action.capability,
                action.params or {},
            )
            if action.capability == "invoice.read_abbyy":
                return self._read(params)
            if action.capability == "invoice.list_abbyy":
                return self._list()
            return ActionResult(
                False,
                error=(
                    "unsupported capability "
                    f"'{action.capability}'"
                ),
            )
        except KeyError as exc:
            return ActionResult(
                False,
                error=f"missing parameter: {exc}",
            )
        except (ABBYYPackageError, PermissionError) as exc:
            return ActionResult(False, error=str(exc))
        except Exception as exc:
            return ActionResult(
                False,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _read(self, params: Mapping[str, Any]) -> ActionResult:
        target = self._safe(str(params["path"]))
        invoice = parse_abbyy_archive(
            target,
            max_csv_bytes=self.max_csv_bytes,
        )
        return ActionResult(True, output=invoice)

    def _list(self) -> ActionResult:
        packages = [
            str(path.relative_to(self.root))
            for path in sorted(self.root.rglob("*.zip"))
            if path.is_file()
        ]
        return ActionResult(
            True,
            output={
                "packages": packages,
                "count": len(packages),
            },
        )

    def _safe(self, path: str) -> Path:
        target = (self.root / path).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError(
                f"path '{path}' escapes invoice root {self.root}"
            ) from exc
        return target


__all__ = [
    "ABBYYInvoiceAdapter",
    "ABBYYPackageError",
    "parse_abbyy_archive",
]
