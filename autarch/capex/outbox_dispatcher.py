"""Transactional outbox dispatch for InvoiceReady v1."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol

from .invoice_ready import validate_invoice_ready
from .persistence import DatabaseConnection


_TRANSACTION_PREAMBLE = """
SET XACT_ABORT ON;
"""

_CLAIM_NEXT = """
;WITH candidate AS
(
    SELECT TOP (1)
        outbox.EventId
    FROM prep.InvoiceOutbox AS outbox
        WITH (UPDLOCK, READPAST, ROWLOCK)
    WHERE
        outbox.AttemptCount < ?
        AND
        (
            (
                outbox.OutboxStatus IN ('PENDING', 'FAILED')
                AND outbox.AvailableAtUtc <= SYSUTCDATETIME()
            )
            OR
            (
                outbox.OutboxStatus = 'PUBLISHING'
                AND outbox.LockedAtUtc <= DATEADD(
                    SECOND,
                    -?,
                    SYSUTCDATETIME()
                )
            )
        )
    ORDER BY
        outbox.AvailableAtUtc,
        outbox.InsertedAtUtc
)
UPDATE outbox
SET
    OutboxStatus = 'PUBLISHING',
    AttemptCount = AttemptCount + 1,
    LockedAtUtc = SYSUTCDATETIME(),
    LastError = NULL,
    UpdatedAtUtc = SYSUTCDATETIME()
OUTPUT
    inserted.EventId,
    inserted.PackageId,
    inserted.EventType,
    inserted.EventVersion,
    inserted.CorrelationId,
    inserted.Payload,
    inserted.AttemptCount
FROM prep.InvoiceOutbox AS outbox
INNER JOIN candidate
    ON candidate.EventId = outbox.EventId;
"""

_MARK_PUBLISHED = """
UPDATE prep.InvoiceOutbox
SET
    OutboxStatus = 'PUBLISHED',
    LockedAtUtc = NULL,
    PublishedAtUtc = SYSUTCDATETIME(),
    LastError = NULL,
    UpdatedAtUtc = SYSUTCDATETIME()
WHERE EventId = ?
  AND OutboxStatus = 'PUBLISHING';

IF @@ROWCOUNT <> 1
    THROW 51040, 'Outbox record could not be marked PUBLISHED.', 1;
"""

_MARK_FAILED = """
UPDATE prep.InvoiceOutbox
SET
    OutboxStatus = 'FAILED',
    LockedAtUtc = NULL,
    AvailableAtUtc = DATEADD(
        SECOND,
        ?,
        SYSUTCDATETIME()
    ),
    LastError = ?,
    UpdatedAtUtc = SYSUTCDATETIME()
WHERE EventId = ?
  AND OutboxStatus = 'PUBLISHING';

IF @@ROWCOUNT <> 1
    THROW 51041, 'Outbox record could not be marked FAILED.', 1;
"""


class InvoiceReadyPublisher(Protocol):
    def publish(self, event: Mapping[str, Any]) -> None:
        ...


class OutboxDispatchError(RuntimeError):
    """Raised after a failed publication attempt is recorded."""


@dataclass(frozen=True)
class ClaimedOutboxMessage:
    event_id: str
    package_id: str
    event_type: str
    event_version: str
    correlation_id: str
    payload: str
    attempt_count: int


def _claim_next(
    connection: DatabaseConnection,
    *,
    max_attempts: int,
    lock_timeout_seconds: int,
) -> Optional[ClaimedOutboxMessage]:
    cursor = connection.cursor()

    try:
        cursor.execute(_TRANSACTION_PREAMBLE)
        cursor.execute(
            _CLAIM_NEXT,
            (max_attempts, lock_timeout_seconds),
        )
        row = cursor.fetchone()
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()

    if row is None:
        return None

    return ClaimedOutboxMessage(
        event_id=str(row[0]),
        package_id=str(row[1]),
        event_type=str(row[2]),
        event_version=str(row[3]),
        correlation_id=str(row[4]),
        payload=str(row[5]),
        attempt_count=int(row[6]),
    )


def _mark_published(
    connection: DatabaseConnection,
    event_id: str,
) -> None:
    cursor = connection.cursor()

    try:
        cursor.execute(_TRANSACTION_PREAMBLE)
        cursor.execute(_MARK_PUBLISHED, (event_id,))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def _mark_failed(
    connection: DatabaseConnection,
    *,
    message: ClaimedOutboxMessage,
    error: BaseException,
    base_retry_seconds: int,
    max_retry_seconds: int,
) -> None:
    exponent = max(message.attempt_count - 1, 0)
    retry_seconds = min(
        max_retry_seconds,
        base_retry_seconds * (2 ** exponent),
    )
    error_text = (
        f"{type(error).__name__}: {error}"
    )[:2000]

    cursor = connection.cursor()

    try:
        cursor.execute(_TRANSACTION_PREAMBLE)
        cursor.execute(
            _MARK_FAILED,
            (
                retry_seconds,
                error_text,
                message.event_id,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def dispatch_next_invoice_ready(
    connection: DatabaseConnection,
    publisher: InvoiceReadyPublisher,
    *,
    max_attempts: int = 10,
    lock_timeout_seconds: int = 300,
    base_retry_seconds: int = 30,
    max_retry_seconds: int = 3600,
) -> bool:
    """Publish one eligible outbox record."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if lock_timeout_seconds < 1:
        raise ValueError(
            "lock_timeout_seconds must be at least 1"
        )
    if base_retry_seconds < 1:
        raise ValueError(
            "base_retry_seconds must be at least 1"
        )
    if max_retry_seconds < base_retry_seconds:
        raise ValueError(
            "max_retry_seconds must be greater than or equal "
            "to base_retry_seconds"
        )

    message = _claim_next(
        connection,
        max_attempts=max_attempts,
        lock_timeout_seconds=lock_timeout_seconds,
    )
    if message is None:
        return False

    try:
        event = json.loads(message.payload)
        validate_invoice_ready(event)
        publisher.publish(event)
    except Exception as exc:
        try:
            _mark_failed(
                connection,
                message=message,
                error=exc,
                base_retry_seconds=base_retry_seconds,
                max_retry_seconds=max_retry_seconds,
            )
        except Exception as state_exc:
            raise OutboxDispatchError(
                "InvoiceReady publication failed and the "
                "outbox failure state could not be recorded."
            ) from state_exc

        exhausted = message.attempt_count >= max_attempts
        suffix = (
            " and exhausted its retry limit"
            if exhausted
            else ""
        )
        raise OutboxDispatchError(
            f"InvoiceReady event {message.event_id} failed"
            f"{suffix}: {exc}"
        ) from exc

    try:
        _mark_published(connection, message.event_id)
    except Exception as exc:
        raise OutboxDispatchError(
            "InvoiceReady was sent, but the outbox record could "
            "not be marked PUBLISHED. Broker duplicate detection "
            "must suppress a retry with the same EventId."
        ) from exc

    return True


__all__ = [
    "ClaimedOutboxMessage",
    "InvoiceReadyPublisher",
    "OutboxDispatchError",
    "dispatch_next_invoice_ready",
]
