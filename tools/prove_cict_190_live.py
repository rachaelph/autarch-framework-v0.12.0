"""Live replay-safety proof for the CapEx InvoiceReady workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from azure.identity import ManagedIdentityCredential
from azure.servicebus import (
    ServiceBusClient,
    ServiceBusMessage,
)

from autarch.capex.invoice_ready import validate_invoice_ready
from autarch.capex.sql_connection import connect_azure_sql


EVENT_ID = "evt-06d9fc90-73ce-5e2f-ac87-32b9fbc72841"
PACKAGE_ID = "pkg-2a26689e-5097-5cfe-b18c-10d34d75c0da"
INVOICE_ID = "135d03ac-3598-498a-83fe-d273d046bfea"
RUN_ID = "run-20261022T031623Z-eee6efc5"
CORRELATION_ID = "corr-82167cbd-60d4-5c1d-b854-f6e335fd7b6a"
MODEL_DEPLOYMENT = "gpt-5.1"
PROMPT_VERSION = "task-determination-v1"
TASK_MASTER_SHA256 = (
    "CC41B492A5DE454EFD7405066EF1EAA6CDE1FF050C8E71ED180831197B6EBA13"
)
TASK_REGION = "US"
EXPECTED_LINE_COUNT = 12
DEFAULT_SQL_CONNECT_ATTEMPTS = 4
DEFAULT_SQL_RETRY_DELAY_SECONDS = 30
DEFAULT_INITIAL_WAIT_SECONDS = 360
DEFAULT_REPLAY_WAIT_SECONDS = 300
DEFAULT_POLL_SECONDS = 15


class ProofError(RuntimeError):
    """Raised when the live workflow does not meet its contract."""


def _required_setting(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ProofError(f"Required environment setting is missing: {name}")
    return value


def _assert_managed_identity_runtime() -> None:
    if not (os.getenv("IDENTITY_ENDPOINT") or os.getenv("MSI_ENDPOINT")):
        raise ProofError(
            "This proof must run in an Azure managed-identity runtime."
        )


def build_event() -> dict[str, str]:
    """Build the immutable event body used by every proof publication."""

    event = {
        "eventId": EVENT_ID,
        "eventType": "InvoiceReady",
        "schemaVersion": "1.0",
        "invoiceId": INVOICE_ID,
        "packageId": PACKAGE_ID,
        "runId": RUN_ID,
        "packageUri": (
            "https://cictaicusst.blob.core.windows.net/landing/"
            f"capex/output/{RUN_ID}/{PACKAGE_ID}.json"
        ),
        "occurredAtUtc": "2026-10-22T03:16:23.000Z",
        "correlationId": CORRELATION_ID,
    }
    validate_invoice_ready(event)
    return event


def _build_message(
    event: Mapping[str, str],
    *,
    broker_message_id: str,
) -> ServiceBusMessage:
    payload = json.dumps(
        dict(event),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return ServiceBusMessage(
        payload,
        message_id=broker_message_id,
        correlation_id=event["correlationId"],
        subject=event["eventType"],
        content_type="application/json",
        application_properties={
            "eventType": event["eventType"],
            "schemaVersion": event["schemaVersion"],
            "packageId": event["packageId"],
            "runId": event["runId"],
        },
    )


def _publish(
    sender: Any,
    event: Mapping[str, str],
    *,
    broker_message_id: str,
    proof_stage: str,
) -> None:
    sender.send_messages(
        _build_message(
            event,
            broker_message_id=broker_message_id,
        )
    )
    print(
        json.dumps(
            {
                "event": "invoice-ready-published",
                "proofStage": proof_stage,
                "eventId": event["eventId"],
                "brokerMessageId": broker_message_id,
                "payloadSha256": hashlib.sha256(
                    json.dumps(
                        dict(event),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest().upper(),
            },
            sort_keys=True,
        ),
        flush=True,
    )


def _connect_sql_with_retry(
    *,
    server_fqdn: str,
    database_name: str,
    managed_identity_client_id: str,
) -> Any:
    for attempt in range(1, DEFAULT_SQL_CONNECT_ATTEMPTS + 1):
        try:
            connection = connect_azure_sql(
                server_fqdn=server_fqdn,
                database_name=database_name,
                managed_identity_client_id=managed_identity_client_id,
            )
            print(
                json.dumps(
                    {
                        "event": "sql-connected",
                        "attempt": attempt,
                        "maximumAttempts": DEFAULT_SQL_CONNECT_ATTEMPTS,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return connection
        except Exception as exc:
            if attempt == DEFAULT_SQL_CONNECT_ATTEMPTS:
                raise ProofError(
                    "Azure SQL connection failed after "
                    f"{attempt} attempts: {type(exc).__name__}: {exc}"
                ) from exc
            print(
                json.dumps(
                    {
                        "event": "sql-connect-retry",
                        "attempt": attempt,
                        "maximumAttempts": DEFAULT_SQL_CONNECT_ATTEMPTS,
                        "delaySeconds": DEFAULT_SQL_RETRY_DELAY_SECONDS,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            time.sleep(DEFAULT_SQL_RETRY_DELAY_SECONDS)

    raise AssertionError("SQL retry loop exited unexpectedly.")


def _fetch_governed_codes(connection: Any) -> set[str]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT [taskCode]
            FROM [pub].[v1_TaskMaster]
            WHERE [regionCode] = ?;
            """,
            (TASK_REGION,),
        )
        return {str(row[0]) for row in cursor.fetchall()}
    finally:
        cursor.close()


def _fetch_rows(connection: Any) -> list[dict[str, Any]]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT
                [lineId],
                [resultStatus],
                [recommendation],
                [taskCode],
                [packageId],
                [invoiceId],
                [runId],
                [correlationId],
                [modelDeployment],
                [promptVersion],
                [taskMasterSha256],
                [createdAtUtc]
            FROM [task].[TaskSuggestion]
            WHERE [eventId] = ?
            ORDER BY [lineId];
            """,
            (EVENT_ID,),
        )
        return [
            {
                "lineId": str(row[0]),
                "resultStatus": str(row[1]),
                "recommendation": str(row[2]),
                "taskCode": None if row[3] is None else str(row[3]),
                "packageId": str(row[4]),
                "invoiceId": str(row[5]),
                "runId": str(row[6]),
                "correlationId": str(row[7]),
                "modelDeployment": str(row[8]),
                "promptVersion": str(row[9]),
                "taskMasterSha256": str(row[10]),
                "createdAtUtc": (
                    row[11].isoformat()
                    if hasattr(row[11], "isoformat")
                    else str(row[11])
                ),
            }
            for row in cursor.fetchall()
        ]
    finally:
        cursor.close()


def validate_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    governed_codes: set[str],
) -> dict[str, Any]:
    """Validate the complete durable result and return proof metrics."""

    if len(rows) != EXPECTED_LINE_COUNT:
        raise ProofError(
            f"Expected {EXPECTED_LINE_COUNT} durable rows, found {len(rows)}."
        )

    line_ids = [str(row["lineId"]) for row in rows]
    if len(set(line_ids)) != EXPECTED_LINE_COUNT:
        raise ProofError("Durable results contain duplicate line IDs.")

    task_codes = [row["taskCode"] for row in rows]
    unresolved = [
        str(row["lineId"])
        for row in rows
        if row["resultStatus"] != "SUGGESTED"
        or row["taskCode"] is None
    ]
    if unresolved:
        raise ProofError(f"Unexpected unresolved lines: {unresolved}")

    ungoverned = sorted(
        {
            str(task_code)
            for task_code in task_codes
            if task_code not in governed_codes
        }
    )
    if ungoverned:
        raise ProofError(f"Ungoverned task codes were persisted: {ungoverned}")

    expected_fields = {
        "packageId": PACKAGE_ID,
        "invoiceId": INVOICE_ID,
        "runId": RUN_ID,
        "correlationId": CORRELATION_ID,
        "modelDeployment": MODEL_DEPLOYMENT,
        "promptVersion": PROMPT_VERSION,
        "taskMasterSha256": TASK_MASTER_SHA256,
    }
    for field, expected in expected_fields.items():
        actual = {str(row[field]) for row in rows}
        if actual != {expected}:
            raise ProofError(
                f"Unexpected {field} provenance: {sorted(actual)}"
            )

    canonical_rows = json.dumps(
        [dict(row) for row in rows],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return {
        "eventId": EVENT_ID,
        "rowCount": len(rows),
        "distinctLineCount": len(set(line_ids)),
        "distinctTaskCodeCount": len(set(task_codes)),
        "duplicateEventLineGroups": len(line_ids) - len(set(line_ids)),
        "unresolvedLineCount": len(unresolved),
        "ungovernedTaskCodeCount": len(ungoverned),
        "durableRowsSha256": hashlib.sha256(
            canonical_rows.encode("utf-8")
        ).hexdigest().upper(),
    }


def _wait_for_initial_rows(
    connection: Any,
    *,
    governed_codes: set[str],
    wait_seconds: int,
    poll_seconds: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        rows = _fetch_rows(connection)
        if len(rows) == EXPECTED_LINE_COUNT:
            return rows, validate_rows(
                rows,
                governed_codes=governed_codes,
            )
        if rows:
            raise ProofError(
                "Transactional persistence exposed a partial result: "
                f"{len(rows)} rows."
            )
        time.sleep(poll_seconds)

    raise ProofError(
        f"Initial event produced no durable result within {wait_seconds} seconds."
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Publish one controlled InvoiceReady event and prove broker "
            "duplicate suppression plus application-level replay safety."
        )
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--initial-wait-seconds",
        type=int,
        default=DEFAULT_INITIAL_WAIT_SECONDS,
    )
    parser.add_argument(
        "--replay-wait-seconds",
        type=int,
        default=DEFAULT_REPLAY_WAIT_SECONDS,
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=DEFAULT_POLL_SECONDS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.execute:
        raise ProofError("Pass --execute to publish the controlled event.")
    if min(
        args.initial_wait_seconds,
        args.replay_wait_seconds,
        args.poll_seconds,
    ) < 1:
        raise ProofError("All wait and poll values must be positive.")

    _assert_managed_identity_runtime()
    client_id = _required_setting("AZURE_CLIENT_ID")
    sql_server = _required_setting("CAPEX_SQL_SERVER")
    sql_database = _required_setting("CAPEX_SQL_DATABASE")
    service_bus_namespace = _required_setting(
        "CAPEX_SERVICE_BUS_NAMESPACE"
    )
    service_bus_topic = _required_setting("CAPEX_SERVICE_BUS_TOPIC")

    event = build_event()
    connection = _connect_sql_with_retry(
        server_fqdn=sql_server,
        database_name=sql_database,
        managed_identity_client_id=client_id,
    )
    credential = ManagedIdentityCredential(client_id=client_id)
    service_bus_client = ServiceBusClient(
        fully_qualified_namespace=service_bus_namespace,
        credential=credential,
    )

    try:
        governed_codes = _fetch_governed_codes(connection)
        baseline_rows = _fetch_rows(connection)
        if baseline_rows:
            raise ProofError(
                "The controlled event already has durable rows. "
                "Use a new prepared invoice package for another initial proof."
            )
        print(
            json.dumps(
                {
                    "event": "cict-190-baseline",
                    "eventId": EVENT_ID,
                    "rowCount": 0,
                    "governedTaskCount": len(governed_codes),
                },
                sort_keys=True,
            ),
            flush=True,
        )

        with service_bus_client:
            with service_bus_client.get_topic_sender(
                topic_name=service_bus_topic
            ) as sender:
                initial_broker_message_id = (
                    f"{EVENT_ID}-initial-proof-{uuid4()}"
                )
                _publish(
                    sender,
                    event,
                    broker_message_id=initial_broker_message_id,
                    proof_stage="initial",
                )
                initial_rows, initial_metrics = _wait_for_initial_rows(
                    connection,
                    governed_codes=governed_codes,
                    wait_seconds=args.initial_wait_seconds,
                    poll_seconds=args.poll_seconds,
                )
                print(
                    json.dumps(
                        {
                            "event": "cict-190-initial-persisted",
                            **initial_metrics,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )

                _publish(
                    sender,
                    event,
                    broker_message_id=initial_broker_message_id,
                    proof_stage="broker-duplicate",
                )
                time.sleep(args.poll_seconds)
                broker_duplicate_rows = _fetch_rows(connection)
                broker_duplicate_metrics = validate_rows(
                    broker_duplicate_rows,
                    governed_codes=governed_codes,
                )
                if broker_duplicate_metrics != initial_metrics:
                    raise ProofError(
                        "Broker duplicate publication changed durable rows."
                    )

                forced_replay_message_id = (
                    f"{EVENT_ID}-application-replay-{uuid4()}"
                )
                _publish(
                    sender,
                    event,
                    broker_message_id=forced_replay_message_id,
                    proof_stage="application-replay",
                )

        time.sleep(args.replay_wait_seconds)
        replay_rows = _fetch_rows(connection)
        replay_metrics = validate_rows(
            replay_rows,
            governed_codes=governed_codes,
        )
        if replay_metrics != initial_metrics:
            raise ProofError(
                "Application replay changed the durable TaskSuggestion result."
            )
        if replay_rows != initial_rows:
            raise ProofError(
                "Application replay changed durable row values."
            )

        print(
            json.dumps(
                {
                    "event": "cict-190-proof-complete",
                    "status": "PASS",
                    "provedAtUtc": datetime.now(timezone.utc).isoformat(),
                    "forcedReplayBrokerMessageId": (
                        forced_replay_message_id
                    ),
                    **replay_metrics,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    finally:
        connection.close()
        credential.close()


if __name__ == "__main__":
    raise SystemExit(main())
