"""Assembly and JSON Schema validation for InvoicePackage v1."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from importlib import resources
from typing import Any, Dict, List, Mapping, Optional, Sequence
from uuid import NAMESPACE_URL, uuid5

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError


_SCHEMA_VERSION = "1.0"
_TOTAL_TOLERANCE = Decimal("0.01")


class InvoicePackageAssemblyError(ValueError):
    """Raised when normalized invoice data cannot produce a valid package."""


def _optional_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _required_text(
    value: Any,
    *,
    field: str,
) -> str:
    text = _optional_text(value)
    if text is None:
        raise InvoicePackageAssemblyError(
            f"InvoicePackage source is missing required field '{field}'"
        )
    return text


def _number(
    value: Any,
    *,
    field: str,
    minimum: Optional[float] = None,
) -> float:
    if isinstance(value, bool):
        raise InvoicePackageAssemblyError(
            f"InvoicePackage field '{field}' must be numeric"
        )

    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise InvoicePackageAssemblyError(
            f"InvoicePackage field '{field}' must be numeric"
        ) from exc

    if not math.isfinite(result):
        raise InvoicePackageAssemblyError(
            f"InvoicePackage field '{field}' must be finite"
        )
    if minimum is not None and result < minimum:
        raise InvoicePackageAssemblyError(
            f"InvoicePackage field '{field}' must be >= {minimum}"
        )

    return result


def _positive_optional_integer(
    value: Any,
    *,
    field: str,
) -> Optional[int]:
    if value in (None, ""):
        return None

    if isinstance(value, bool):
        raise InvoicePackageAssemblyError(
            f"InvoicePackage field '{field}' must be a positive integer"
        )

    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise InvoicePackageAssemblyError(
            f"InvoicePackage field '{field}' must be a positive integer"
        ) from exc

    if str(result) != str(value).strip() or result < 1:
        raise InvoicePackageAssemblyError(
            f"InvoicePackage field '{field}' must be a positive integer"
        )

    return result


def _require_utc_datetime(value: str) -> str:
    text = _required_text(
        value,
        field="created_at_utc",
    )

    try:
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise InvoicePackageAssemblyError(
            "created_at_utc must be an ISO 8601 date-time"
        ) from exc

    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise InvoicePackageAssemblyError(
            "created_at_utc must include a UTC offset"
        )

    return text


def derive_package_id(
    run_id: str,
    invoice_id: str,
) -> str:
    """Return a stable package ID for one invoice within one run."""
    normalized_run_id = _required_text(
        run_id,
        field="run_id",
    )
    normalized_invoice_id = _required_text(
        invoice_id,
        field="invoice_id",
    )
    identity = (
        "circlek:capex:invoice-package:"
        f"{normalized_run_id}:{normalized_invoice_id}"
    )
    return f"pkg-{uuid5(NAMESPACE_URL, identity)}"


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema_text = (
        resources.files("autarch.capex.schemas")
        .joinpath("invoice-package-v1.schema.json")
        .read_text(encoding="utf-8")
    )
    schema = json.loads(schema_text)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )


def validate_invoice_package(
    package: Mapping[str, Any],
) -> None:
    """Validate a package against the packaged Draft 2020-12 schema."""
    try:
        _validator().validate(dict(package))
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path)
        location = f" at {path}" if path else ""
        raise InvoicePackageAssemblyError(
            f"InvoicePackage v1 validation failed{location}: "
            f"{exc.message}"
        ) from exc


def _append_warning(
    warnings: List[str],
    warning: str,
) -> None:
    if warning not in warnings:
        warnings.append(warning)


def build_invoice_package(
    invoice: Mapping[str, Any],
    *,
    run_id: str,
    created_at_utc: str,
    package_id: Optional[str] = None,
    business_unit: Optional[str] = None,
    project_status: Optional[str] = None,
    project_type: Optional[str] = None,
    bundling_in_scope: Optional[bool] = None,
) -> Dict[str, Any]:
    """Build and validate InvoicePackage v1 from normalized ABBYY data."""
    if not isinstance(invoice, Mapping):
        raise InvoicePackageAssemblyError(
            "invoice must be a mapping"
        )

    normalized_run_id = _required_text(
        run_id,
        field="run_id",
    )
    invoice_id = _required_text(
        invoice.get("invoice_id"),
        field="invoice_id",
    )
    normalized_package_id = (
        _required_text(package_id, field="package_id")
        if package_id is not None
        else derive_package_id(
            normalized_run_id,
            invoice_id,
        )
    )
    normalized_created_at = _require_utc_datetime(
        created_at_utc
    )

    source_lines = invoice.get("lines")
    if (
        not isinstance(source_lines, Sequence)
        or isinstance(source_lines, (str, bytes))
        or not source_lines
    ):
        raise InvoicePackageAssemblyError(
            "invoice must contain at least one line"
        )

    po_number = _optional_text(
        invoice.get("po_number")
    )
    project_id = (
        _optional_text(invoice.get("afe_number"))
        or _optional_text(invoice.get("project_number"))
    )
    default_site_id = _optional_text(
        invoice.get("site_id")
    )

    lines: List[Dict[str, Any]] = []
    source_line_ids = set()

    for line_number, source_line in enumerate(
        source_lines,
        start=1,
    ):
        if not isinstance(source_line, Mapping):
            raise InvoicePackageAssemblyError(
                f"invoice line {line_number} must be a mapping"
            )

        source_line_id = _required_text(
            source_line.get("line_id") or line_number,
            field=f"lines[{line_number}].line_id",
        )
        if source_line_id in source_line_ids:
            raise InvoicePackageAssemblyError(
                f"duplicate invoice line identifier: {source_line_id}"
            )
        source_line_ids.add(source_line_id)

        site_id = (
            _optional_text(source_line.get("site_id"))
            or default_site_id
        )

        lines.append(
            {
                "invoiceLineId": (
                    f"{invoice_id}:{source_line_id}"
                ),
                "lineNumber": line_number,
                "description": _required_text(
                    source_line.get("description"),
                    field=(
                        f"lines[{line_number}].description"
                    ),
                ),
                "partNumber": _optional_text(
                    source_line.get("part_number")
                ),
                "quantity": _number(
                    source_line.get("quantity"),
                    field=f"lines[{line_number}].quantity",
                    minimum=0.0,
                ),
                "unitCost": _number(
                    source_line.get("unit_price"),
                    field=f"lines[{line_number}].unit_price",
                ),
                "lineAmount": _number(
                    source_line.get("amount"),
                    field=f"lines[{line_number}].amount",
                ),
                "poLineNumber": _positive_optional_integer(
                    source_line.get("po_line_number"),
                    field=(
                        f"lines[{line_number}].po_line_number"
                    ),
                ),
                "poDescription": _optional_text(
                    source_line.get("po_description")
                ),
                "poTaskCode": _optional_text(
                    source_line.get("actual_task")
                ),
                "afeProjectId": project_id,
                "siteId": site_id,
            }
        )

    warnings: List[str] = []
    source_warnings = invoice.get(
        "data_quality_warnings",
        [],
    )
    if source_warnings is not None:
        if (
            not isinstance(source_warnings, Sequence)
            or isinstance(source_warnings, (str, bytes))
        ):
            raise InvoicePackageAssemblyError(
                "data_quality_warnings must be a sequence"
            )
        for warning in source_warnings:
            normalized_warning = _required_text(
                warning,
                field="data_quality_warnings",
            )
            _append_warning(warnings, normalized_warning)

    missing_po = po_number is None
    missing_project = project_id is None
    missing_site = all(
        line["siteId"] is None
        for line in lines
    )

    if missing_po:
        _append_warning(
            warnings,
            "Purchase-order context was not supplied.",
        )
    if missing_project:
        _append_warning(
            warnings,
            "AFE/project context was not supplied.",
        )
    if missing_site:
        _append_warning(
            warnings,
            "Site context was not supplied.",
        )

    invoice_total = _number(
        invoice.get("total"),
        field="total",
        minimum=0.0,
    )
    line_total = sum(
        Decimal(str(line["lineAmount"]))
        for line in lines
    )

    if (
        abs(Decimal(str(invoice_total)) - line_total)
        >= _TOTAL_TOLERANCE
    ):
        _append_warning(
            warnings,
            (
                "Invoice total differs from the extracted line total; "
                "tax or another header-level charge may account for "
                "the difference."
            ),
        )

    resolved_business_unit = (
        _optional_text(business_unit)
        or _optional_text(invoice.get("business_unit_id"))
        or _optional_text(invoice.get("business_unit"))
    )
    resolved_bundling = (
        bool(invoice.get("bundling_in_scope", False))
        if bundling_in_scope is None
        else bool(bundling_in_scope)
    )

    package: Dict[str, Any] = {
        "schemaVersion": _SCHEMA_VERSION,
        "packageId": normalized_package_id,
        "runId": normalized_run_id,
        "invoice": {
            "invoiceId": invoice_id,
            "invoiceNumber": _required_text(
                invoice.get("invoice_number"),
                field="invoice_number",
            ),
            "invoiceDate": _optional_text(
                invoice.get("invoice_date")
            ),
            "vendorId": _optional_text(
                invoice.get("vendor_id")
            ),
            "vendorName": _required_text(
                invoice.get("vendor"),
                field="vendor",
            ),
            "currency": _required_text(
                invoice.get("currency"),
                field="currency",
            ).upper(),
            "invoiceTotal": invoice_total,
            "poNumber": po_number,
            "countryRegion": _required_text(
                invoice.get("region"),
                field="region",
            ),
            "sourceSystem": _required_text(
                invoice.get("source_system") or "ABBYY",
                field="source_system",
            ),
        },
        "lines": lines,
        "context": {
            "businessUnit": resolved_business_unit,
            "projectStatus": (
                _optional_text(project_status)
                or _optional_text(
                    invoice.get("project_status")
                )
            ),
            "projectType": (
                _optional_text(project_type)
                or _optional_text(
                    invoice.get("project_type")
                )
            ),
            "bundlingInScope": resolved_bundling,
        },
        "dataQuality": {
            "missingPo": missing_po,
            "missingProject": missing_project,
            "missingSite": missing_site,
            "warnings": warnings,
        },
        "source": {
            "packageName": _required_text(
                invoice.get("source"),
                field="source",
            ),
            "sourceSha256": _required_text(
                invoice.get("source_sha256"),
                field="source_sha256",
            ).lower(),
        },
        "createdAtUtc": normalized_created_at,
    }

    validate_invoice_package(package)
    return package


__all__ = [
    "InvoicePackageAssemblyError",
    "build_invoice_package",
    "derive_package_id",
    "validate_invoice_package",
]
