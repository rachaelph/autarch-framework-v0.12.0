from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import pytest

from autarch.capex.invoice_ready import build_invoice_ready_event
from autarch.capex.outbox_dispatcher import (
    OutboxDispatchError,
    dispatch_next_invoice_ready,
)


ROOT = Path(__file__).resolve().parents[1]


def _event() -> dict[str, Any]:
    package = json.loads(
        (
            ROOT
            / "contracts"
            / "examples"
            / "invoice-package-v1-bruegmann.json"
        ).read_text(encoding="utf-8")
    )
    return build_invoice_ready_event(
        package,
        occurred_at_utc=datetime(
            2026,
            10,
            19,
            19,
            0,
            tzinfo=timezone.utc,
        ),
    )


def _row(
    *,
    attempt_count: int = 1,
    payload: Optional[str] = None,
) -> tuple[Any, ...]:
    event = _event()
    return (
        event["eventId"],
        event["packageId"],
        event["eventType"],
        event["schemaVersion"],
        event["correlationId"],
        payload
        if payload is not None
        else json.dumps(event),
        attempt_count,
    )


class RecordingCursor:
    def __init__(self, connection: "RecordingConnection") -> None:
        self.connection = connection
        self.closed = False

    def execute(
        self,
        operation: str,
        parameters: Sequence[Any] = (),
    ) -> "RecordingCursor":
        self.connection.calls.append(
            (operation, tuple(parameters))
        )
        if (
            self.connection.fail_on is not None
            and self.connection.fail_on in operation
        ):
            raise RuntimeError("simulated database failure")
        return self

    def fetchone(self) -> Optional[Sequence[Any]]:
        if not self.connection.rows:
            return None
        return self.connection.rows.pop(0)

    def close(self) -> None:
        self.closed = True


class RecordingConnection:
    def __init__(
        self,
        *,
        rows: Optional[list[Optional[Sequence[Any]]]] = None,
        fail_on: Optional[str] = None,
    ) -> None:
        self.rows = list(rows or [])
        self.fail_on = fail_on
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.cursors: list[RecordingCursor] = []

    def cursor(self) -> RecordingCursor:
        cursor = RecordingCursor(self)
        self.cursors.append(cursor)
        return cursor

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class RecordingPublisher:
    def __init__(self, *, error: Optional[Exception] = None) -> None:
        self.error = error
        self.events: list[Mapping[str, Any]] = []

    def publish(self, event: Mapping[str, Any]) -> None:
        self.events.append(event)
        if self.error is not None:
            raise self.error


def _sql_calls(
    connection: RecordingConnection,
    fragment: str,
) -> list[tuple[str, tuple[Any, ...]]]:
    return [
        call
        for call in connection.calls
        if fragment in call[0]
    ]


def test_dispatch_claims_sends_and_marks_published() -> None:
    connection = RecordingConnection(rows=[_row()])
    publisher = RecordingPublisher()

    dispatched = dispatch_next_invoice_ready(
        connection,
        publisher,
    )

    assert dispatched
    assert publisher.events == [_event()]
    assert connection.commit_count == 2
    assert connection.rollback_count == 0
    assert all(cursor.closed for cursor in connection.cursors)

    claim_sql = _sql_calls(connection, "WITH candidate")[0][0]
    assert "UPDLOCK, READPAST, ROWLOCK" in claim_sql
    assert "OutboxStatus = 'PUBLISHING'" in claim_sql
    assert "LockedAtUtc <= DATEADD" in claim_sql

    published = _sql_calls(
        connection,
        "OutboxStatus = 'PUBLISHED'",
    )
    assert len(published) == 1
    assert published[0][1] == (_event()["eventId"],)


def test_dispatch_returns_false_when_nothing_is_eligible() -> None:
    connection = RecordingConnection()
    publisher = RecordingPublisher()

    assert not dispatch_next_invoice_ready(
        connection,
        publisher,
    )

    assert publisher.events == []
    assert connection.commit_count == 1
    assert connection.rollback_count == 0


def test_publish_failure_is_recorded_with_retry_delay() -> None:
    connection = RecordingConnection(rows=[_row()])
    publisher = RecordingPublisher(
        error=RuntimeError("broker unavailable")
    )

    with pytest.raises(
        OutboxDispatchError,
        match="broker unavailable",
    ):
        dispatch_next_invoice_ready(
            connection,
            publisher,
            base_retry_seconds=30,
            max_retry_seconds=3600,
        )

    failed = _sql_calls(
        connection,
        "OutboxStatus = 'FAILED'",
    )
    assert len(failed) == 1
    assert failed[0][1][0] == 30
    assert "broker unavailable" in failed[0][1][1]
    assert connection.commit_count == 2
    assert connection.rollback_count == 0


def test_retry_delay_is_exponential_and_bounded() -> None:
    connection = RecordingConnection(
        rows=[_row(attempt_count=8)]
    )
    publisher = RecordingPublisher(
        error=RuntimeError("still unavailable")
    )

    with pytest.raises(OutboxDispatchError):
        dispatch_next_invoice_ready(
            connection,
            publisher,
            base_retry_seconds=30,
            max_retry_seconds=600,
        )

    failed = _sql_calls(
        connection,
        "OutboxStatus = 'FAILED'",
    )
    assert failed[0][1][0] == 600


def test_invalid_payload_is_recorded_as_failure() -> None:
    connection = RecordingConnection(
        rows=[_row(payload="{not-json")]
    )
    publisher = RecordingPublisher()

    with pytest.raises(
        OutboxDispatchError,
        match="failed",
    ):
        dispatch_next_invoice_ready(
            connection,
            publisher,
        )

    assert publisher.events == []
    assert len(
        _sql_calls(
            connection,
            "OutboxStatus = 'FAILED'",
        )
    ) == 1
    assert connection.commit_count == 2
    assert connection.rollback_count == 0


def test_sent_message_relies_on_duplicate_detection_if_finalize_fails() -> None:
    connection = RecordingConnection(
        rows=[_row()],
        fail_on="Outbox record could not be marked PUBLISHED",
    )
    publisher = RecordingPublisher()

    with pytest.raises(
        OutboxDispatchError,
        match="duplicate detection",
    ):
        dispatch_next_invoice_ready(
            connection,
            publisher,
        )

    assert publisher.events == [_event()]
    assert connection.commit_count == 1
    assert connection.rollback_count == 1


def test_no_nested_transactions_in_dispatcher_sql() -> None:
    """Regression: ensure dispatcher never emits BEGIN TRANSACTION.

    Nested transactions (autocommit=False + BEGIN TRANSACTION) cause
    rollback issues when publishing succeeds but finalization fails.
    """
    connection = RecordingConnection(rows=[_row()])
    publisher = RecordingPublisher()

    dispatch_next_invoice_ready(
        connection,
        publisher,
    )

    for sql_text, _ in connection.calls:
        assert "BEGIN TRANSACTION" not in sql_text, (
            f"Found BEGIN TRANSACTION in SQL: {sql_text}"
        )
