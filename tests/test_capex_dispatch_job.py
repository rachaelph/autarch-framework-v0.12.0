from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import autarch.capex.dispatch_job as dispatch_job
from autarch.capex.outbox_dispatcher import OutboxDispatchError


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakePublisher:
    def __init__(self) -> None:
        self.closed = False

    def publish(self, event: Any) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def test_dispatch_job_stops_when_outbox_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = iter([True, True, False])

    monkeypatch.setattr(
        dispatch_job,
        "dispatch_next_invoice_ready",
        lambda connection, publisher: next(results),
    )

    published = dispatch_job.run_dispatch_job(
        FakeConnection(),
        FakePublisher(),
        max_messages=10,
    )

    assert published == 2


def test_dispatch_job_honors_message_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def dispatch(connection: Any, publisher: Any) -> bool:
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr(
        dispatch_job,
        "dispatch_next_invoice_ready",
        dispatch,
    )

    published = dispatch_job.run_dispatch_job(
        FakeConnection(),
        FakePublisher(),
        max_messages=3,
    )

    assert published == 3
    assert calls == 3


def test_dispatch_job_rejects_invalid_limit() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        dispatch_job.run_dispatch_job(
            FakeConnection(),
            FakePublisher(),
            max_messages=0,
        )


def test_cli_closes_resources_after_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    connection = FakeConnection()
    publisher = FakePublisher()

    monkeypatch.setattr(
        dispatch_job,
        "run_dispatch_job",
        lambda connection, publisher, *, max_messages: 0,
    )

    result = dispatch_job.main(
        [
            "--sql-server",
            "sql.database.windows.net",
            "--sql-database",
            "capex",
            "--service-bus-namespace",
            "bus.servicebus.windows.net",
        ],
        connection_factory=lambda **kwargs: connection,
        publisher_factory=lambda **kwargs: publisher,
    )

    assert result == 0
    assert connection.closed
    assert publisher.closed
    events = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
    ]
    assert [event["event"] for event in events] == [
        "invoice_ready_dispatch_started",
        "invoice_ready_dispatch_completed",
    ]
    assert events[0]["dispatchRunId"]
    assert (
        events[0]["dispatchRunId"]
        == events[1]["dispatchRunId"]
    )
    assert events[1]["publishedCount"] == 0


def test_cli_reports_dispatch_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    connection = FakeConnection()
    publisher = FakePublisher()

    def fail(
        connection: Any,
        publisher: Any,
        *,
        max_messages: int,
    ) -> int:
        raise OutboxDispatchError("publish failed")

    monkeypatch.setattr(
        dispatch_job,
        "run_dispatch_job",
        fail,
    )

    result = dispatch_job.main(
        [
            "--sql-server",
            "sql.database.windows.net",
            "--sql-database",
            "capex",
            "--service-bus-namespace",
            "bus.servicebus.windows.net",
        ],
        connection_factory=lambda **kwargs: connection,
        publisher_factory=lambda **kwargs: publisher,
    )

    assert result == 1
    assert connection.closed
    assert publisher.closed
    events = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
    ]
    assert [event["event"] for event in events] == [
        "invoice_ready_dispatch_started",
        "invoice_ready_dispatch_failed",
    ]
    assert (
        events[0]["dispatchRunId"]
        == events[1]["dispatchRunId"]
    )
    assert events[1]["error"] == (
        "OutboxDispatchError: publish failed"
    )


def test_dispatcher_container_apps_job_is_finite_and_secretless() -> None:
    bicep = (
        Path(__file__).parents[1]
        / "infra"
        / "container-apps"
        / "capex-invoice-ready-dispatch-job.bicep"
    ).read_text(encoding="utf-8-sig")

    required_fragments = [
        "Microsoft.App/jobs@2025-01-01",
        "type: 'UserAssigned'",
        "param userAssignedIdentityResourceId string",
        "param userAssignedIdentityClientId string",
        "identity: userAssignedIdentityResourceId",
        "name: 'AZURE_CLIENT_ID'",
        "triggerType: scheduleEnabled ? 'Schedule' : 'Manual'",
        "param scheduleEnabled bool = true",
        "cronExpression string = '*/1 * * * *'",
        "parallelism: 1",
        "replicaCompletionCount: 1",
        "'capex-dispatch'",
        "name: 'CAPEX_SQL_SERVER'",
        "name: 'CAPEX_SQL_DATABASE'",
        "name: 'CAPEX_SERVICE_BUS_NAMESPACE'",
        "name: 'CAPEX_SERVICE_BUS_TOPIC'",
        "name: 'CAPEX_DISPATCH_MAX_MESSAGES'",
        "output managedIdentityResourceId string",
        "output managedIdentityClientId string",
    ]
    for fragment in required_fragments:
        assert fragment in bicep

    lowered = bicep.lower()
    assert "connectionstring" not in lowered
    assert "sharedaccesskey" not in lowered
    assert "password" not in lowered
    assert "systemassigned" not in lowered
