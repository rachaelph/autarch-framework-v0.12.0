"""Lossless normalization contracts and adapters for CapEx Demo V2."""
from __future__ import annotations

import hashlib
import math
import re
import zipfile
from collections import Counter
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence
from xml.etree import ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..adapters.abbyy import parse_abbyy_archive
from .invoice_package import derive_package_id


_XML_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": (
        "http://schemas.openxmlformats.org/officeDocument/"
        "2006/relationships"
    ),
}
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_APPROVED_JC_DOCUMENT_TYPES = {"AI"}
_REVERSAL_JC_DOCUMENT_TYPES = {"JP"}


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class NormalizationModel(BaseModel):
    """Strict camelCase base for generated normalization artifacts."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
        allow_inf_nan=False,
    )


class NormalizationStatus(str, Enum):
    """Whether normalization is complete or requires governed review."""

    NORMALIZED = "NORMALIZED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class DemoContextOverlay(NormalizationModel):
    """Synthetic context kept separate from immutable source data."""

    schema_version: Literal["1.0"] = "1.0"
    overlay_version: str = Field(min_length=1)
    target_invoice_id: str = Field(min_length=1)
    project_id: str = Field(pattern=r"^DEMO-")
    site_id: str = Field(pattern=r"^DEMO-")
    project_status: str = Field(min_length=1)
    bundling_in_scope: bool
    synthetic: Literal[True] = True


class NormalizedSource(NormalizationModel):
    """Immutable source identity for one normalized artifact."""

    file_name: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_system: str = Field(min_length=1)


class NormalizedInvoiceLine(NormalizationModel):
    """Lossless line representation that distinguishes missing from zero."""

    line_id: str = Field(min_length=1)
    line_number: int = Field(ge=1)
    description: str = Field(min_length=1)
    part_number: Optional[str] = None
    quantity: Optional[float] = Field(default=None, ge=0.0)
    unit_cost: Optional[float] = None
    line_amount: float
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    source_transaction_type: Optional[str] = None
    source_document_number: Optional[str] = None
    source_invoice_number: Optional[str] = None
    source_account: Optional[str] = None
    source_task_code: Optional[str] = None


class ExcludedLedgerRecord(NormalizationModel):
    """A ledger row intentionally excluded from invoice aggregation."""

    record_id: str = Field(min_length=1)
    transaction_type: str = Field(min_length=1)
    currency: Optional[str] = None
    amount: Optional[float] = None
    reason: str = Field(min_length=1)


class NormalizationDataQuality(NormalizationModel):
    """Machine-readable source gaps and review blockers."""

    missing_quantity_line_ids: List[str] = Field(default_factory=list)
    missing_unit_cost_line_ids: List[str] = Field(default_factory=list)
    source_currencies: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    review_reasons: List[str] = Field(default_factory=list)


class NormalizedInvoicePackage(NormalizationModel):
    """Demo V2 invoice package with explicit missing-value semantics."""

    schema_version: Literal["2.0-normalized"] = "2.0-normalized"
    package_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    invoice_id: str = Field(min_length=1)
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    vendor_id: Optional[str] = None
    vendor_name: str = Field(min_length=1)
    invoice_total: Optional[float] = Field(default=None, ge=0.0)
    currency: Optional[str] = Field(
        default=None,
        pattern=r"^[A-Z]{3}$",
    )
    country_region: str = Field(min_length=1)
    po_number: Optional[str] = None
    normalization_status: NormalizationStatus
    lines: List[NormalizedInvoiceLine] = Field(min_length=1)
    context_overlay: Optional[DemoContextOverlay] = None
    data_quality: NormalizationDataQuality
    source: NormalizedSource
    created_at_utc: datetime

    @field_validator("created_at_utc")
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("createdAtUtc must include a UTC offset")
        return value

    @model_validator(mode="after")
    def validate_package(self) -> "NormalizedInvoicePackage":
        line_ids = [line.line_id for line in self.lines]
        if len(line_ids) != len(set(line_ids)):
            raise ValueError("lines must not contain duplicate lineId values")
        if (
            self.context_overlay is not None
            and self.context_overlay.target_invoice_id != self.invoice_id
        ):
            raise ValueError(
                "contextOverlay targetInvoiceId must match invoiceId"
            )
        if (
            self.normalization_status == NormalizationStatus.NORMALIZED
            and self.data_quality.review_reasons
        ):
            raise ValueError(
                "NORMALIZED package cannot contain reviewReasons"
            )
        return self


class JCWorkbookNormalizationResult(NormalizationModel):
    """Classification result for one JC project/accounting workbook."""

    schema_version: Literal["2.0-jc-ledger"] = "2.0-jc-ledger"
    run_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    packages: List[NormalizedInvoicePackage] = Field(default_factory=list)
    excluded_records: List[ExcludedLedgerRecord] = Field(default_factory=list)
    source_currencies: List[str] = Field(default_factory=list)
    review_reasons: List[str] = Field(default_factory=list)
    source: NormalizedSource
    created_at_utc: datetime

    _validate_created_at_utc = field_validator("created_at_utc")(
        NormalizedInvoicePackage.validate_utc
    )


def _text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _number(value: Any, *, field: str) -> float:
    if value in (None, "") or isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        result = float(str(value).replace(",", "").strip())
    except ValueError as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _optional_number(value: Any, *, field: str) -> Optional[float]:
    if _text(value) is None:
        return None
    return _number(value, field=field)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _review_reasons(
    missing_quantity: Sequence[str],
    missing_unit_cost: Sequence[str],
) -> List[str]:
    reasons: List[str] = []
    if missing_quantity:
        reasons.append(
            "Source quantity is missing for one or more invoice lines."
        )
    if missing_unit_cost:
        reasons.append(
            "Source unit cost is missing for one or more invoice lines."
        )
    return reasons


def normalize_abbyy_for_demo_v2(
    path: Path,
    *,
    run_id: str,
    created_at_utc: datetime,
    context_overlay: Optional[DemoContextOverlay] = None,
) -> NormalizedInvoicePackage:
    """Normalize one ABBYY ZIP without converting missing values to zero."""
    invoice = parse_abbyy_archive(
        path,
        preserve_missing_numbers=True,
    )
    currency = str(invoice["currency"]).upper()
    lines = [
        NormalizedInvoiceLine(
            line_id=f"{invoice['invoice_id']}:{line['line_id']}",
            line_number=index,
            description=line["description"],
            part_number=_text(line.get("part_number")),
            quantity=line.get("quantity"),
            unit_cost=line.get("unit_price"),
            line_amount=line["amount"],
            currency=currency,
        )
        for index, line in enumerate(invoice["lines"], start=1)
    ]
    missing_quantity = [
        line.line_id for line in lines if line.quantity is None
    ]
    missing_unit_cost = [
        line.line_id for line in lines if line.unit_cost is None
    ]
    review_reasons = _review_reasons(
        missing_quantity,
        missing_unit_cost,
    )
    line_total = sum(line.line_amount for line in lines)
    warnings: List[str] = []
    if abs(float(invoice["total"]) - line_total) >= 0.01:
        warnings.append(
            "Invoice total differs from extracted line total; tax or "
            "another header-level charge may account for the difference."
        )

    return NormalizedInvoicePackage(
        package_id=derive_package_id(run_id, invoice["invoice_id"]),
        run_id=run_id,
        invoice_id=invoice["invoice_id"],
        invoice_number=invoice["invoice_number"],
        invoice_date=_text(invoice.get("invoice_date")),
        vendor_id=_text(invoice.get("vendor_id")),
        vendor_name=invoice["vendor"],
        invoice_total=invoice["total"],
        currency=currency,
        country_region=invoice["region"],
        po_number=_text(invoice.get("po_number")),
        normalization_status=(
            NormalizationStatus.REVIEW_REQUIRED
            if review_reasons
            else NormalizationStatus.NORMALIZED
        ),
        lines=lines,
        context_overlay=context_overlay,
        data_quality=NormalizationDataQuality(
            missing_quantity_line_ids=missing_quantity,
            missing_unit_cost_line_ids=missing_unit_cost,
            source_currencies=[currency],
            warnings=warnings,
            review_reasons=review_reasons,
        ),
        source=NormalizedSource(
            file_name=path.name,
            sha256=invoice["source_sha256"],
            source_system="ABBYY",
        ),
        created_at_utc=created_at_utc,
    )


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference)
    value = 0
    for char in letters.group() if letters else "A":
        value = value * 26 + ord(char) - 64
    return value - 1


def _unique_headers(values: Sequence[str]) -> List[str]:
    counts: Counter[str] = Counter()
    headers: List[str] = []
    for index, value in enumerate(values):
        base = str(value or f"Column {index + 1}").strip()
        counts[base] += 1
        headers.append(
            base
            if counts[base] == 1
            else f"{base}__{counts[base]}"
        )
    return headers


def read_xlsx_rows(path: Path) -> List[Dict[str, str]]:
    """Read the first XLSX worksheet using only the standard library."""
    source = Path(path)
    if source.read_bytes()[:8] == bytes.fromhex(
        "D0CF11E0A1B11AE1"
    ):
        raise ValueError(
            f"{source.name} is encrypted; provide an authorized XLSX export"
        )

    with zipfile.ZipFile(source) as archive:
        shared: List[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(
                archive.read("xl/sharedStrings.xml")
            )
            shared = [
                "".join(
                    node.text or ""
                    for node in item.findall(".//m:t", _XML_NS)
                )
                for item in root.findall("m:si", _XML_NS)
            ]

        workbook = ET.fromstring(
            archive.read("xl/workbook.xml")
        )
        relationships = ET.fromstring(
            archive.read("xl/_rels/workbook.xml.rels")
        )
        targets = {
            node.attrib["Id"]: node.attrib["Target"]
            for node in relationships
        }
        sheet = workbook.find("m:sheets/m:sheet", _XML_NS)
        if sheet is None:
            raise ValueError(f"{source.name} contains no worksheets")
        relationship_id = sheet.attrib[f"{{{_XML_NS['r']}}}id"]
        target = (
            targets[relationship_id]
            .replace("\\", "/")
            .lstrip("/")
        )
        member = target if target.startswith("xl/") else f"xl/{target}"
        root = ET.fromstring(archive.read(member))

    matrix: List[List[str]] = []
    for row in root.findall(".//m:sheetData/m:row", _XML_NS):
        values: Dict[int, str] = {}
        for cell in row.findall("m:c", _XML_NS):
            value_node = cell.find("m:v", _XML_NS)
            inline_node = cell.find("m:is", _XML_NS)
            raw = value_node.text if value_node is not None else ""
            if inline_node is not None:
                raw = "".join(
                    node.text or ""
                    for node in inline_node.findall(
                        ".//m:t",
                        _XML_NS,
                    )
                )
            if cell.attrib.get("t") == "s" and raw:
                raw = shared[int(raw)]
            values[_column_index(cell.attrib.get("r", "A1"))] = (
                str(raw or "")
            )
        matrix.append(
            [
                values.get(index, "")
                for index in range(max(values, default=-1) + 1)
            ]
        )

    if not matrix:
        return []
    headers = _unique_headers(matrix[0])
    return [
        {
            header: row[index] if index < len(row) else ""
            for index, header in enumerate(headers)
        }
        for row in matrix[1:]
        if any(str(value).strip() for value in row)
    ]


def normalize_jc_workbook(
    path: Path,
    *,
    run_id: str,
    created_at_utc: datetime,
    context_overlays: Optional[
        Mapping[str, DemoContextOverlay]
    ] = None,
) -> JCWorkbookNormalizationResult:
    """Classify JC rows and build packages only from approved AI rows."""
    rows = read_xlsx_rows(path)
    source = NormalizedSource(
        file_name=path.name,
        sha256=_sha256(path),
        source_system="JDE_PROJECT_ACCOUNTING",
    )
    data_rows = [
        row
        for row in rows
        if _text(row.get("Account Number"))
        and _text(row.get("Do Ty"))
    ]
    if not data_rows:
        raise ValueError(f"{path.name} contains no JC accounting rows")

    project_ids = {
        value
        for value in (
            _text(row.get("Business Unit"))
            for row in data_rows
        )
        if value
    }
    if len(project_ids) != 1:
        raise ValueError(
            f"{path.name} must contain exactly one Business Unit"
        )
    project_id = next(iter(project_ids))
    source_currencies = sorted(
        {
            value.upper()
            for value in (
                _text(row.get("Cur Cod"))
                for row in data_rows
            )
            if value and _CURRENCY.fullmatch(value.upper())
        }
    )

    approved_rows: List[Mapping[str, Any]] = []
    excluded: List[ExcludedLedgerRecord] = []
    for index, row in enumerate(data_rows, start=1):
        transaction_type = str(row["Do Ty"]).strip().upper()
        record_id = (
            f"{_text(row.get('Doc Number')) or index}:"
            f"{_text(row.get('JE Line Number')) or index}"
        )
        currency = _text(row.get("Cur Cod"))
        amount = _optional_number(
            row.get("LT 1 Amount"),
            field=f"row {index} LT 1 Amount",
        )
        if transaction_type in _APPROVED_JC_DOCUMENT_TYPES:
            approved_rows.append(row)
            continue
        reason = (
            "Accrual/reversal row excluded to prevent double counting."
            if transaction_type in _REVERSAL_JC_DOCUMENT_TYPES
            else "Transaction type is not approved for invoice aggregation."
        )
        excluded.append(
            ExcludedLedgerRecord(
                record_id=record_id,
                transaction_type=transaction_type,
                currency=(
                    currency.upper() if currency else None
                ),
                amount=amount,
                reason=reason,
            )
        )

    grouped_rows: Dict[str, List[Mapping[str, Any]]] = {}
    for index, row in enumerate(approved_rows, start=1):
        invoice_number = _text(row.get("Invoice Number"))
        if invoice_number is None:
            invoice_number = (
                f"MISSING-INVOICE-{_text(row.get('Doc Number')) or index}"
            )
        grouped_rows.setdefault(invoice_number, []).append(row)

    packages: List[NormalizedInvoicePackage] = []
    overlays = context_overlays or {}
    for invoice_number, invoice_rows in grouped_rows.items():
        currencies = sorted(
            {
                str(row.get("Cur Cod") or "").strip().upper()
                for row in invoice_rows
                if str(row.get("Cur Cod") or "").strip()
            }
        )
        if len(currencies) != 1:
            raise ValueError(
                f"invoice {invoice_number} mixes currencies: "
                f"{', '.join(currencies)}"
            )
        currency = currencies[0]
        invoice_id = f"{project_id}:{invoice_number}"
        lines: List[NormalizedInvoiceLine] = []
        for line_number, row in enumerate(invoice_rows, start=1):
            account = str(row["Account Number"]).strip()
            source_task_code = account.rsplit(".", 1)[-1]
            document_number = _text(row.get("Doc Number"))
            line_id = (
                f"{invoice_id}:"
                f"{document_number or line_number}:"
                f"{_text(row.get('JE Line Number')) or line_number}"
            )
            lines.append(
                NormalizedInvoiceLine(
                    line_id=line_id,
                    line_number=line_number,
                    description=(
                        _text(row.get("Account Description"))
                        or "JC accounting line"
                    ),
                    quantity=_optional_number(
                        row.get("Units"),
                        field=f"{line_id} Units",
                    ),
                    unit_cost=None,
                    line_amount=_number(
                        row.get("LT 1 Amount"),
                        field=f"{line_id} LT 1 Amount",
                    ),
                    currency=currency,
                    source_transaction_type="AI",
                    source_document_number=document_number,
                    source_invoice_number=invoice_number,
                    source_account=account,
                    source_task_code=source_task_code,
                )
            )

        missing_quantity = [
            line.line_id for line in lines if line.quantity is None
        ]
        missing_unit_cost = [
            line.line_id for line in lines if line.unit_cost is None
        ]
        reasons = _review_reasons(
            missing_quantity,
            missing_unit_cost,
        )
        reasons.extend(
            [
                "JC source task code is not governed by the supplied "
                "US/Canadian TaskMaster.",
                "JC accounting rows are not a complete invoice-line export.",
            ]
        )
        warnings: List[str] = []
        if len(source_currencies) > 1:
            warnings.append(
                "Workbook contains multiple currencies; packages are "
                "separated by invoice and currency and are not aggregated."
            )
        if excluded:
            warnings.append(
                f"{len(excluded)} unapproved or reversal row(s) were excluded."
            )
        overlay = overlays.get(invoice_id)
        packages.append(
            NormalizedInvoicePackage(
                package_id=derive_package_id(run_id, invoice_id),
                run_id=run_id,
                invoice_id=invoice_id,
                invoice_number=invoice_number,
                vendor_name=(
                    _text(invoice_rows[0].get("Explanation"))
                    or "Unknown JC vendor"
                ),
                invoice_total=sum(
                    line.line_amount for line in lines
                ),
                currency=currency,
                country_region="Europe",
                po_number=_text(
                    invoice_rows[0].get("Purchase Order")
                ),
                normalization_status=(
                    NormalizationStatus.REVIEW_REQUIRED
                ),
                lines=lines,
                context_overlay=overlay,
                data_quality=NormalizationDataQuality(
                    missing_quantity_line_ids=missing_quantity,
                    missing_unit_cost_line_ids=missing_unit_cost,
                    source_currencies=source_currencies,
                    warnings=warnings,
                    review_reasons=reasons,
                ),
                source=source,
                created_at_utc=created_at_utc,
            )
        )

    review_reasons: List[str] = []
    if len(source_currencies) > 1:
        review_reasons.append(
            "Mixed source currencies cannot be aggregated without an "
            "approved conversion policy."
        )
    if excluded:
        review_reasons.append(
            "Excluded ledger transaction types require Circle K confirmation."
        )

    return JCWorkbookNormalizationResult(
        run_id=run_id,
        project_id=project_id,
        packages=packages,
        excluded_records=excluded,
        source_currencies=source_currencies,
        review_reasons=review_reasons,
        source=source,
        created_at_utc=created_at_utc,
    )


__all__ = [
    "DemoContextOverlay",
    "ExcludedLedgerRecord",
    "JCWorkbookNormalizationResult",
    "NormalizationDataQuality",
    "NormalizationStatus",
    "NormalizedInvoiceLine",
    "NormalizedInvoicePackage",
    "NormalizedSource",
    "normalize_abbyy_for_demo_v2",
    "normalize_jc_workbook",
    "read_xlsx_rows",
]
