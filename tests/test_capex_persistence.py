from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import pytest

from autarch.capex.invoice_ready import build_invoice_ready_event
from autarch.capex.persistence import (
    InvoicePersistenceError,
    persist_invoice_package,
)


ROOT = Path(__file__).resolve().parents[1]


def _package() -> dict:
    return json.loads(
        (
            ROOT
            / "contracts"
            / "examples"
            / "invoice-package-v1-bruegmann.json"
        ).read_text(encoding="utf-8")
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
            ("execute", operation, tuple(parameters))
        )
        if (
            self.connection.fail_on is not None
            and self.connection.fail_on in operation
        ):
            raise RuntimeError("simulated database failure")
        return self

    def executemany(
        self,
        operation: str,
        parameters: Sequence[Sequence[Any]],
    ) -> "RecordingCursor":
        rows = tuple(tuple(row) for row in parameters)
        self.connection.calls.append(
            ("executemany", operation, rows)
        )
        if (
            self.connection.fail_on is not None
            and self.connection.fail_on in operation
        ):
            raise RuntimeError("simulated database failure")
        return self

    def fetchone(self) -> Optional[Sequence[Any]]:
        return self.connection.existing

    def close(self) -> None:
        self.closed = True


class RecordingConnection:
    def __init__(
        self,
        *,
        existing: Optional[Sequence[Any]] = None,
        fail_on: Optional[str] = None,
    ) -> None:
        self.existing = existing
        self.fail_on = fail_on
        self.calls: list[tuple[str, str, object]] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.cursor_instance = RecordingCursor(self)

    def cursor(self) -> RecordingCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


def _fixed_time() -> datetime:
    return datetime(
        2026,
        10,
        19,
        19,
        0,
        tzinfo=timezone.utc,
    )


def _operation_indexes(
    connection: RecordingConnection,
    fragment: str,
) -> list[int]:
    return [
        index
        for index, call in enumerate(connection.calls)
        if fragment in call[1]
    ]


def test_persistence_commits_package_and_outbox_atomically() -> None:
    connection = RecordingConnection()
    package = _package()

    event = persist_invoice_package(
        connection,
        package,
        occurred_at_utc=_fixed_time(),
    )

    assert connection.commit_count == 1
    assert connection.rollback_count == 0
    assert connection.cursor_instance.closed

    preamble = _operation_indexes(
        connection,
        "SET XACT_ABORT ON",
    )[0]
    package_insert = _operation_indexes(
        connection,
        "INSERT INTO prep.InvoicePackage",
    )[0]
    line_insert = _operation_indexes(
        connection,
        "INSERT INTO prep.InvoiceLine",
    )[0]
    warning_insert = _operation_indexes(
        connection,
        "INSERT INTO prep.InvoiceDataQualityWarning",
    )[0]
    outbox_insert = _operation_indexes(
        connection,
        "INSERT INTO prep.InvoiceOutbox",
    )[0]
    ready_update = _operation_indexes(
        connection,
        "PackageStatus = 'READY'",
    )[0]

    assert (
        preamble
        < package_insert
        < line_insert
        < warning_insert
        < outbox_insert
        < ready_update
    )

    line_call = connection.calls[line_insert]
    warning_call = connection.calls[warning_insert]
    outbox_call = connection.calls[outbox_insert]

    assert len(line_call[2]) == 12
    assert len(warning_call[2]) == 4

    outbox_parameters = outbox_call[2]
    persisted_payload = json.loads(outbox_parameters[5])
    assert persisted_payload == event
    assert "lines" not in persisted_payload
    assert "invoice" not in persisted_payload

    all_sql = "\n".join(call[1] for call in connection.calls)
    assert "BEGIN TRANSACTION" not in all_sql
    assert package["invoice"]["vendorName"] not in all_sql
    assert package["lines"][0]["description"] not in all_sql


def test_persistence_rolls_back_when_outbox_insert_fails() -> None:
    connection = RecordingConnection(
        fail_on="INSERT INTO prep.InvoiceOutbox",
    )

    with pytest.raises(
        InvoicePersistenceError,
        match="simulated database failure",
    ):
        persist_invoice_package(
            connection,
            _package(),
            occurred_at_utc=_fixed_time(),
        )

    assert connection.commit_count == 0
    assert connection.rollback_count == 1
    assert connection.cursor_instance.closed
    assert not _operation_indexes(
        connection,
        "PackageStatus = 'READY'",
    )


def test_ready_package_retry_returns_existing_event() -> None:
    package = _package()
    existing_event = build_invoice_ready_event(
        package,
        occurred_at_utc=_fixed_time(),
    )
    connection = RecordingConnection(
        existing=(
            package["packageId"],
            "READY",
            package["source"]["sourceSha256"],
            json.dumps(existing_event),
        )
    )

    returned = persist_invoice_package(
        connection,
        package,
        occurred_at_utc=datetime(
            2026,
            10,
            20,
            19,
            0,
            tzinfo=timezone.utc,
        ),
    )

    assert returned == existing_event
    assert connection.commit_count == 1
    assert connection.rollback_count == 0
    assert len(
        _operation_indexes(
            connection,
            "INSERT INTO",
        )
    ) == 0
    assert len(connection.calls) == 2


def test_incomplete_package_retry_replaces_transactional_rows() -> None:
    package = _package()
    connection = RecordingConnection(
        existing=(
            package["packageId"],
            "PREPARING",
            package["source"]["sourceSha256"],
            None,
        )
    )

    persist_invoice_package(
        connection,
        package,
        occurred_at_utc=_fixed_time(),
    )

    delete_index = _operation_indexes(
        connection,
        "DELETE FROM prep.InvoicePackage",
    )[0]
    insert_index = _operation_indexes(
        connection,
        "INSERT INTO prep.InvoicePackage",
    )[0]

    assert delete_index < insert_index
    assert connection.commit_count == 1
    assert connection.rollback_count == 0


def test_retry_rejects_changed_source_content() -> None:
    package = _package()
    connection = RecordingConnection(
        existing=(
            package["packageId"],
            "READY",
            "a" * 64,
            json.dumps(
                build_invoice_ready_event(
                    package,
                    occurred_at_utc=_fixed_time(),
                )
            ),
        )
    )

    with pytest.raises(
        InvoicePersistenceError,
        match="different source content",
    ):
        persist_invoice_package(
            connection,
            package,
            occurred_at_utc=_fixed_time(),
        )

    assert connection.commit_count == 0
    assert connection.rollback_count == 1


def test_incomplete_retry_rejects_changed_source_content() -> None:
    package = _package()
    connection = RecordingConnection(
        existing=(
            package["packageId"],
            "PREPARING",
            "a" * 64,
            None,
        )
    )

    with pytest.raises(
        InvoicePersistenceError,
        match="different source content",
    ):
        persist_invoice_package(
            connection,
            package,
            occurred_at_utc=_fixed_time(),
        )

    assert connection.commit_count == 0
    assert connection.rollback_count == 1
    assert not _operation_indexes(
        connection,
        "DELETE FROM prep.InvoicePackage",
    )
