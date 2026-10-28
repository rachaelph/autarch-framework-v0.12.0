"""Transactional InvoicePackage persistence and outbox creation."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence, Tuple

from .invoice_package import validate_invoice_package
from .invoice_ready import (
    build_invoice_ready_event,
    validate_invoice_ready,
)


class InvoicePersistenceError(RuntimeError):
    """Raised when package and outbox persistence cannot complete."""


class DatabaseCursor(Protocol):
    def execute(
        self,
        operation: str,
        parameters: Sequence[Any] = (),
    ) -> Any:
        ...

    def executemany(
        self,
        operation: str,
        parameters: Sequence[Sequence[Any]],
    ) -> Any:
        ...

    def fetchone(self) -> Optional[Sequence[Any]]:
        ...

    def fetchall(self) -> Sequence[Sequence[Any]]:
        ...

    def close(self) -> None:
        ...


class DatabaseConnection(Protocol):
    def cursor(self) -> DatabaseCursor:
        ...

    def commit(self) -> None:
        ...

    def rollback(self) -> None:
        ...


_TRANSACTION_PREAMBLE = """
SET XACT_ABORT ON;
"""


_SELECT_EXISTING = """
SELECT
    package.PackageId,
    package.PackageStatus,
    package.SourceSha256,
    outbox.Payload
FROM prep.InvoicePackage AS package WITH (UPDLOCK, HOLDLOCK)
LEFT JOIN prep.InvoiceOutbox AS outbox WITH (UPDLOCK, HOLDLOCK)
    ON outbox.PackageId = package.PackageId
    AND outbox.EventType = 'InvoiceReady'
    AND outbox.EventVersion = '1.0'
WHERE package.RunId = ?
  AND package.InvoiceId = ?;
"""

_DELETE_RETRY = """
DELETE FROM prep.InvoicePackage
WHERE PackageId = ?;
"""

_INSERT_PACKAGE = """
INSERT INTO prep.InvoicePackage
(
    PackageId,
    SchemaVersion,
    RunId,
    InvoiceId,
    InvoiceNumber,
    InvoiceDate,
    VendorId,
    VendorName,
    Currency,
    InvoiceTotal,
    PoNumber,
    CountryRegion,
    SourceSystem,
    BusinessUnit,
    ProjectStatus,
    ProjectType,
    BundlingInScope,
    MissingPo,
    MissingProject,
    MissingSite,
    SourcePackageName,
    SourceSha256,
    CreatedAtUtc,
    PackageStatus
)
VALUES
(
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PREPARING'
);
"""

_INSERT_LINE = """
INSERT INTO prep.InvoiceLine
(
    PackageId,
    InvoiceLineId,
    LineNumber,
    Description,
    PartNumber,
    Quantity,
    UnitCost,
    LineAmount,
    PoLineNumber,
    PoDescription,
    PoTaskCode,
    AfeProjectId,
    SiteId
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_WARNING = """
INSERT INTO prep.InvoiceDataQualityWarning
(
    PackageId,
    WarningOrdinal,
    WarningText
)
VALUES (?, ?, ?);
"""

_INSERT_OUTBOX = """
INSERT INTO prep.InvoiceOutbox
(
    EventId,
    PackageId,
    EventType,
    EventVersion,
    CorrelationId,
    Payload,
    OutboxStatus
)
VALUES (?, ?, ?, ?, ?, ?, 'PENDING');
"""

_MARK_READY = """
UPDATE prep.InvoicePackage
SET
    PackageStatus = 'READY',
    UpdatedAtUtc = SYSUTCDATETIME()
WHERE PackageId = ?
  AND PackageStatus = 'PREPARING';

IF @@ROWCOUNT <> 1
    THROW 51020, 'Invoice package could not transition to READY.', 1;
"""


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _package_values(
    package: Mapping[str, Any],
) -> Tuple[Any, ...]:
    invoice = package["invoice"]
    context = package["context"]
    quality = package["dataQuality"]
    source = package["source"]

    return (
        package["packageId"],
        package["schemaVersion"],
        package["runId"],
        invoice["invoiceId"],
        invoice["invoiceNumber"],
        invoice["invoiceDate"],
        invoice["vendorId"],
        invoice["vendorName"],
        invoice["currency"],
        _decimal(invoice["invoiceTotal"]),
        invoice["poNumber"],
        invoice["countryRegion"],
        invoice["sourceSystem"],
        context["businessUnit"],
        context["projectStatus"],
        context["projectType"],
        bool(context["bundlingInScope"]),
        bool(quality["missingPo"]),
        bool(quality["missingProject"]),
        bool(quality["missingSite"]),
        source["packageName"],
        source["sourceSha256"],
        package["createdAtUtc"],
    )


def _line_values(
    package: Mapping[str, Any],
) -> Sequence[Tuple[Any, ...]]:
    package_id = package["packageId"]
    return [
        (
            package_id,
            line["invoiceLineId"],
            line["lineNumber"],
            line["description"],
            line["partNumber"],
            _decimal(line["quantity"]),
            _decimal(line["unitCost"]),
            _decimal(line["lineAmount"]),
            line["poLineNumber"],
            line["poDescription"],
            line["poTaskCode"],
            line["afeProjectId"],
            line["siteId"],
        )
        for line in package["lines"]
    ]


def _warning_values(
    package: Mapping[str, Any],
) -> Sequence[Tuple[Any, ...]]:
    package_id = package["packageId"]
    return [
        (package_id, ordinal, warning)
        for ordinal, warning in enumerate(
            package["dataQuality"]["warnings"],
            start=1,
        )
    ]


def _event_values(
    event: Mapping[str, Any],
) -> Tuple[Any, ...]:
    return (
        event["eventId"],
        event["packageId"],
        event["eventType"],
        event["schemaVersion"],
        event["correlationId"],
        json.dumps(
            dict(event),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


def _existing_event(
    row: Sequence[Any],
    *,
    expected_package_id: str,
    expected_source_sha256: str,
) -> Dict[str, Any]:
    package_id = row[0]
    package_status = row[1]
    source_sha256 = row[2]
    payload = row[3]

    if package_id != expected_package_id:
        raise InvoicePersistenceError(
            "The run and invoice IDs already belong to a "
            "different package ID."
        )
    if source_sha256 != expected_source_sha256:
        raise InvoicePersistenceError(
            "The package retry has different source content."
        )
    if package_status != "READY":
        raise InvoicePersistenceError(
            "Only READY packages can be treated as completed replays."
        )
    if not isinstance(payload, str) or not payload:
        raise InvoicePersistenceError(
            "The READY package has no InvoiceReady outbox payload."
        )

    try:
        event = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise InvoicePersistenceError(
            "The existing outbox payload is not valid JSON."
        ) from exc

    validate_invoice_ready(event)
    return event


def persist_invoice_package(
    connection: DatabaseConnection,
    package: Mapping[str, Any],
    *,
    package_uri: Optional[str] = None,
    occurred_at_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Persist a package and InvoiceReady outbox event atomically."""
    validate_invoice_package(package)

    event = build_invoice_ready_event(
        package,
        package_uri=package_uri,
        occurred_at_utc=occurred_at_utc,
    )

    package_id = package["packageId"]
    run_id = package["runId"]
    invoice_id = package["invoice"]["invoiceId"]
    source_sha256 = package["source"]["sourceSha256"]

    cursor = connection.cursor()
    transaction_started = False

    try:
        cursor.execute(_TRANSACTION_PREAMBLE)
        transaction_started = True
        cursor.execute(
            _SELECT_EXISTING,
            (run_id, invoice_id),
        )
        existing = cursor.fetchone()

        if existing is not None and existing[1] == "READY":
            persisted_event = _existing_event(
                existing,
                expected_package_id=package_id,
                expected_source_sha256=source_sha256,
            )
            connection.commit()
            transaction_started = False
            return persisted_event

        if existing is not None:
            if existing[0] != package_id:
                raise InvoicePersistenceError(
                    "The run and invoice IDs already belong to a "
                    "different package ID."
                )
            if existing[2] != source_sha256:
                raise InvoicePersistenceError(
                    "The package retry has different source content."
                )
            cursor.execute(_DELETE_RETRY, (package_id,))

        cursor.execute(
            _INSERT_PACKAGE,
            _package_values(package),
        )
        cursor.executemany(
            _INSERT_LINE,
            _line_values(package),
        )

        warning_values = _warning_values(package)
        if warning_values:
            cursor.executemany(
                _INSERT_WARNING,
                warning_values,
            )

        cursor.execute(
            _INSERT_OUTBOX,
            _event_values(event),
        )
        cursor.execute(
            _MARK_READY,
            (package_id,),
        )

        connection.commit()
        transaction_started = False
        return event
    except InvoicePersistenceError:
        if transaction_started:
            connection.rollback()
        raise
    except Exception as exc:
        if transaction_started:
            connection.rollback()
        raise InvoicePersistenceError(
            f"Invoice package persistence failed: {exc}"
        ) from exc
    finally:
        cursor.close()


__all__ = [
    "DatabaseConnection",
    "DatabaseCursor",
    "InvoicePersistenceError",
    "persist_invoice_package",
]
