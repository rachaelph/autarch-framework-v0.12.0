"""Tests for durable TaskSuggestion persistence."""

from __future__ import annotations

from typing import Any

import pytest

from autarch.capex.agent_invocation_models import (
    TaskSuggestion,
    TaskSuggestionLine,
)
from autarch.capex.agent_invocation_persistence import (
    TaskSuggestionPersistenceError,
    persist_task_suggestion,
)

TASK_MASTER_SHA256 = "A" * 64


class FakeCursor:
    def __init__(
        self,
        *,
        allowed_task_codes: tuple[str, ...] = ("2000.10",),
        existing_rows: list[tuple[Any, ...]] | None = None,
        insert_error: Exception | None = None,
    ) -> None:
        self.allowed_task_codes = allowed_task_codes
        self.existing_rows = existing_rows or []
        self.insert_error = insert_error
        self.current_rows: list[tuple[Any, ...]] = []
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.inserted: list[tuple[Any, ...]] = []
        self.closed = False

    def execute(
        self,
        operation: str,
        parameters: tuple[Any, ...] = (),
    ) -> None:
        self.executed.append((operation, parameters))

        if "FROM [pub].[v1_TaskMaster]" in operation:
            self.current_rows = [
                (task_code,)
                for task_code in self.allowed_task_codes
            ]
        elif "FROM [task].[TaskSuggestion]" in operation:
            self.current_rows = list(self.existing_rows)
        else:
            self.current_rows = []

    def executemany(
        self,
        operation: str,
        parameters: list[tuple[Any, ...]],
    ) -> None:
        if self.insert_error is not None:
            raise self.insert_error
        self.inserted.extend(parameters)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.current_rows)

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self.fake_cursor = cursor
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self) -> FakeCursor:
        return self.fake_cursor

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


def _suggestion(
    *,
    task_code: str | None = "2000.10",
) -> TaskSuggestion:
    return TaskSuggestion(
        package_id="pkg-001",
        invoice_id="inv-001",
        run_id="run-001",
        correlation_id="corr-001",
        lines=[
            TaskSuggestionLine(
                line_id="line-001",
                recommendation="REVIEW",
                task_code=task_code,
                confidence=0.85,
                rationale="Matched the governed task catalog.",
            )
        ],
        overall_recommendation="REVIEW",
    )


def _persist(
    connection: FakeConnection,
    suggestion: TaskSuggestion,
) -> int:
    return persist_task_suggestion(
        connection,
        suggestion,
        event_id="evt-001",
        region_code="US",
        model_deployment="task-model",
        prompt_version="task-v1",
        task_master_sha256=TASK_MASTER_SHA256,
    )


def test_persists_all_lines_in_one_commit() -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)

    count = _persist(connection, _suggestion())

    assert count == 1
    assert connection.commit_count == 1
    assert connection.rollback_count == 0
    assert len(cursor.inserted) == 1
    assert cursor.inserted[0][6] == "SUGGESTED"
    assert cursor.inserted[0][8] == "2000.10"
    assert cursor.closed


def test_persists_missing_task_as_unresolved() -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)

    count = _persist(
        connection,
        _suggestion(task_code=None),
    )

    assert count == 1
    assert cursor.inserted[0][6] == "UNRESOLVED"
    assert cursor.inserted[0][8] is None


def test_replay_returns_existing_without_inserting() -> None:
    existing_rows = [
        (
            "pkg-001",
            "inv-001",
            "run-001",
            "corr-001",
            "line-001",
            "SUGGESTED",
            "2000.10",
        )
    ]
    cursor = FakeCursor(existing_rows=existing_rows)
    connection = FakeConnection(cursor)

    count = _persist(connection, _suggestion())

    assert count == 1
    assert cursor.inserted == []
    assert connection.commit_count == 1
    assert connection.rollback_count == 0


def test_rejects_task_outside_published_catalog() -> None:
    cursor = FakeCursor(
        allowed_task_codes=("1000.10",),
    )
    connection = FakeConnection(cursor)

    with pytest.raises(
        TaskSuggestionPersistenceError,
        match="outside pub.v1_TaskMaster",
    ):
        _persist(connection, _suggestion())

    assert connection.commit_count == 0
    assert connection.rollback_count == 1
    assert cursor.inserted == []


def test_rolls_back_insert_failure() -> None:
    cursor = FakeCursor(
        insert_error=RuntimeError("database unavailable"),
    )
    connection = FakeConnection(cursor)

    with pytest.raises(
        TaskSuggestionPersistenceError,
        match="database unavailable",
    ):
        _persist(connection, _suggestion())

    assert connection.commit_count == 0
    assert connection.rollback_count == 1
    assert cursor.closed
