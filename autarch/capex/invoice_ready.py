"""InvoiceReady v1 event assembly and validation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from importlib import resources
from typing import Any, Dict, Mapping, Optional
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from .invoice_package import validate_invoice_package


_EVENT_TYPE = "InvoiceReady"
_SCHEMA_VERSION = "1.0"


class InvoiceReadyAssemblyError(ValueError):
    """Raised when an InvoiceReady v1 event cannot be assembled."""


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvoiceReadyAssemblyError(
            f"{field} must be a non-empty string"
        )
    return value.strip()


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvoiceReadyAssemblyError(
            "occurred_at_utc must include a UTC offset"
        )

    normalized = value.astimezone(timezone.utc)
    return (
        normalized.isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def derive_invoice_ready_event_id(package_id: str) -> str:
    """Return the stable event ID for a prepared package."""
    normalized_package_id = _required_text(
        package_id,
        field="package_id",
    )
    identity = (
        "circlek:capex:invoice-ready:"
        f"{normalized_package_id}"
    )
    return f"evt-{uuid5(NAMESPACE_URL, identity)}"


def derive_invoice_correlation_id(
    run_id: str,
    invoice_id: str,
) -> str:
    """Return a stable correlation ID for one invoice run."""
    normalized_run_id = _required_text(
        run_id,
        field="run_id",
    )
    normalized_invoice_id = _required_text(
        invoice_id,
        field="invoice_id",
    )
    identity = (
        "circlek:capex:invoice-correlation:"
        f"{normalized_run_id}:{normalized_invoice_id}"
    )
    return f"corr-{uuid5(NAMESPACE_URL, identity)}"


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema_text = (
        resources.files("autarch.capex.schemas")
        .joinpath("invoice-ready-v1.schema.json")
        .read_text(encoding="utf-8")
    )
    schema = json.loads(schema_text)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )


def validate_invoice_ready(
    event: Mapping[str, Any],
) -> None:
    """Validate an InvoiceReady v1 event."""
    try:
        _validator().validate(dict(event))
    except ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path)
        location = path or "<root>"
        raise InvoiceReadyAssemblyError(
            f"InvoiceReady v1 validation failed at "
            f"{location}: {exc.message}"
        ) from exc


def build_invoice_ready_event(
    package: Mapping[str, Any],
    *,
    package_uri: Optional[str] = None,
    occurred_at_utc: Optional[datetime] = None,
    event_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build and validate the pointer-only event for a package."""
    validate_invoice_package(package)

    package_id = _required_text(
        package["packageId"],
        field="package.packageId",
    )
    run_id = _required_text(
        package["runId"],
        field="package.runId",
    )
    invoice = package["invoice"]
    invoice_id = _required_text(
        invoice["invoiceId"],
        field="package.invoice.invoiceId",
    )

    normalized_uri = (
        _required_text(package_uri, field="package_uri")
        if package_uri is not None
        else (
            "urn:circlek:capex:invoice-package:"
            f"{quote(package_id, safe='')}"
        )
    )

    event = {
        "eventId": (
            _required_text(event_id, field="event_id")
            if event_id is not None
            else derive_invoice_ready_event_id(package_id)
        ),
        "eventType": _EVENT_TYPE,
        "schemaVersion": _SCHEMA_VERSION,
        "invoiceId": invoice_id,
        "packageId": package_id,
        "runId": run_id,
        "packageUri": normalized_uri,
        "occurredAtUtc": _format_utc(
            occurred_at_utc
            if occurred_at_utc is not None
            else datetime.now(timezone.utc)
        ),
        "correlationId": (
            _required_text(
                correlation_id,
                field="correlation_id",
            )
            if correlation_id is not None
            else derive_invoice_correlation_id(
                run_id,
                invoice_id,
            )
        ),
    }

    validate_invoice_ready(event)
    return event


__all__ = [
    "InvoiceReadyAssemblyError",
    "build_invoice_ready_event",
    "derive_invoice_correlation_id",
    "derive_invoice_ready_event_id",
    "validate_invoice_ready",
]
