#!/usr/bin/env python3
"""CICT-187 live acceptance for durable, idempotent TaskSuggestion persistence."""

from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pyodbc

from autarch.capex.agent_invocation_models import TaskSuggestion, TaskSuggestionLine
from autarch.capex.agent_invocation_persistence import (
    TaskSuggestionPersistenceError,
    load_task_catalog,
    persist_task_suggestion,
)


SERVER = "cict-ai-cus-sql.database.windows.net"
DATABASE = "cict-ai-cus-sqldb"
REGION_CODE = "US"
MODEL_DEPLOYMENT = "gpt-5.1"
PROMPT_VERSION = "task-determination-v1"
TASK_MASTER_SHA256 = (
    "CC41B492A5DE454EFD7405066EF1EAA6CDE1FF050C8E71ED180831197B6EBA13"
)
SQL_COPT_SS_ACCESS_TOKEN = 1256


def get_azure_sql_token() -> str:
    result = subprocess.run(
        [
            __import__("shutil").which("az") or "az",
            "account",
            "get-access-token",
            "--resource",
            "https://database.windows.net/",
            "--query",
            "accessToken",
            "--output",
            "tsv",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("Azure CLI returned an empty Azure SQL access token.")
    return token


def select_odbc_driver() -> str:
    installed = set(pyodbc.drivers())
    for candidate in (
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
    ):
        if candidate in installed:
            return candidate
    raise RuntimeError(
        "ODBC Driver 17 or 18 for SQL Server is required. "
        f"Installed drivers: {sorted(installed)}"
    )


def connect_to_database(token: str) -> pyodbc.Connection:
    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack(
        f"<I{len(token_bytes)}s",
        len(token_bytes),
        token_bytes,
    )
    driver = select_odbc_driver()
    connection_string = (
        f"Driver={{{driver}}};"
        f"Server=tcp:{SERVER},1433;"
        f"Database={DATABASE};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )
    return pyodbc.connect(
        connection_string,
        attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct},
        autocommit=False,
    )


def build_suggestion(
    *,
    suffix: str,
    task_codes: tuple[str | None, ...],
) -> TaskSuggestion:
    lines = [
        TaskSuggestionLine(
            line_id=str(index),
            recommendation="REVIEW" if task_code is None else "CAPEX",
            task_code=task_code,
            asset_class_code=None,
            confidence=0.50 if task_code is None else 0.95,
            rationale=(
                "CICT-187 unresolved acceptance line."
                if task_code is None
                else "CICT-187 governed-code acceptance line."
            ),
            evidence_refs=[],
            policy_ids=[],
        )
        for index, task_code in enumerate(task_codes, start=1)
    ]
    return TaskSuggestion(
        package_id=f"cict-187-pkg-{suffix}",
        invoice_id=f"cict-187-invoice-{suffix}",
        run_id=f"cict-187-run-{suffix}",
        correlation_id=f"cict-187-corr-{suffix}",
        lines=lines,
        overall_recommendation="REVIEW",
        agent_execution_id=f"cict-187-exec-{suffix}",
        agent_session_id=f"cict-187-session-{suffix}",
        created_at_utc=datetime.now(timezone.utc),
        status="PENDING",
    )


def scalar(cursor: pyodbc.Cursor, sql: str, *params: Any) -> Any:
    cursor.execute(sql, params)
    row = cursor.fetchone()
    if row is None:
        raise AssertionError("Expected a scalar query result.")
    return row[0]


def verify_permissions(cursor: pyodbc.Cursor) -> str:
    cursor.execute(
        """
        SELECT
            USER_NAME(),
            HAS_PERMS_BY_NAME(
                N'pub.v1_TaskMaster', N'OBJECT', N'SELECT'
            ),
            HAS_PERMS_BY_NAME(
                N'task.TaskSuggestion', N'OBJECT', N'SELECT'
            ),
            HAS_PERMS_BY_NAME(
                N'task.TaskSuggestion', N'OBJECT', N'INSERT'
            ),
            HAS_PERMS_BY_NAME(
                N'task.TaskSuggestion', N'OBJECT', N'UPDATE'
            ),
            HAS_PERMS_BY_NAME(
                N'task.TaskSuggestion', N'OBJECT', N'DELETE'
            );
        """
    )
    row = cursor.fetchone()
    if row is None:
        raise AssertionError("Permission verification returned no result.")
    user = str(row[0])
    actual = tuple(int(value) for value in row[1:])
    expected = (1, 1, 1, 1, 0)
    if actual != expected:
        raise AssertionError(
            f"Unexpected permissions for {user}: {actual}; expected {expected}."
        )
    return user


def fetch_event_rows(
    cursor: pyodbc.Cursor,
    event_id: str,
) -> list[tuple[Any, ...]]:
    cursor.execute(
        """
        SELECT
            [lineId],
            [resultStatus],
            [recommendation],
            [taskCode],
            [modelDeployment],
            [promptVersion],
            [taskMasterSha256],
            [correlationId],
            [runId]
        FROM [task].[TaskSuggestion]
        WHERE [eventId] = ?
        ORDER BY [lineId];
        """,
        (event_id,),
    )
    return [tuple(row) for row in cursor.fetchall()]


def run_acceptance(connection: pyodbc.Connection) -> dict[str, Any]:
    cursor = connection.cursor()
    try:
        database_user = verify_permissions(cursor)
        catalog = load_task_catalog(connection, region_code=REGION_CODE)
        if len(catalog) < 2:
            raise AssertionError("At least two governed US TaskMaster codes are required.")

        governed_codes = (
            catalog[0]["task_code"],
            catalog[1]["task_code"],
        )
        suffix = uuid4().hex[:12]
        success_event_id = f"cict-187-acceptance-{suffix}"
        invalid_event_id = f"cict-187-invalid-{suffix}"
        success_suggestion = build_suggestion(
            suffix=suffix,
            task_codes=(governed_codes[0], None, governed_codes[1]),
        )

        print("CICT-187 proposed live records")
        print(f"  Database user:       {database_user}")
        print(f"  Success event ID:    {success_event_id}")
        print(f"  Invalid event ID:    {invalid_event_id}")
        print(f"  Governed task codes: {governed_codes}")
        print("  Success lines:       3 (2 SUGGESTED, 1 UNRESOLVED)")

        preexisting = scalar(
            cursor,
            """
            SELECT COUNT(*)
            FROM [task].[TaskSuggestion]
            WHERE [eventId] IN (?, ?);
            """,
            success_event_id,
            invalid_event_id,
        )
        if int(preexisting) != 0:
            raise AssertionError(
                f"Expected no pre-existing acceptance rows, found {preexisting}."
            )

        inserted = persist_task_suggestion(
            connection,
            success_suggestion,
            event_id=success_event_id,
            region_code=REGION_CODE,
            model_deployment=MODEL_DEPLOYMENT,
            prompt_version=PROMPT_VERSION,
            task_master_sha256=TASK_MASTER_SHA256,
        )
        if inserted != 3:
            raise AssertionError(f"Expected 3 persisted rows, got {inserted}.")

        first_rows = fetch_event_rows(cursor, success_event_id)
        if len(first_rows) != 3:
            raise AssertionError(
                f"Expected 3 durable rows after first write, got {len(first_rows)}."
            )
        for row in first_rows:
            (
                _line_id,
                result_status,
                _recommendation,
                task_code,
                model_deployment,
                prompt_version,
                task_master_sha256,
                correlation_id,
                run_id,
            ) = row
            expected_status = "SUGGESTED" if task_code is not None else "UNRESOLVED"
            if result_status != expected_status:
                raise AssertionError(
                    f"Expected {expected_status} for task code {task_code!r}; "
                    f"got {result_status!r}."
                )
            if model_deployment != MODEL_DEPLOYMENT:
                raise AssertionError("Unexpected model deployment provenance.")
            if prompt_version != PROMPT_VERSION:
                raise AssertionError("Unexpected prompt version provenance.")
            if task_master_sha256 != TASK_MASTER_SHA256:
                raise AssertionError("Unexpected TaskMaster hash provenance.")
            if correlation_id != success_suggestion.correlation_id:
                raise AssertionError("Unexpected correlation ID.")
            if run_id != success_suggestion.run_id:
                raise AssertionError("Unexpected run ID.")

        replay_count = persist_task_suggestion(
            connection,
            success_suggestion,
            event_id=success_event_id,
            region_code=REGION_CODE,
            model_deployment=MODEL_DEPLOYMENT,
            prompt_version=PROMPT_VERSION,
            task_master_sha256=TASK_MASTER_SHA256,
        )
        replay_rows = fetch_event_rows(cursor, success_event_id)
        if replay_count != 3 or replay_rows != first_rows:
            raise AssertionError(
                "Matching replay did not return the same three durable rows."
            )

        duplicate_groups = scalar(
            cursor,
            """
            SELECT COUNT(*)
            FROM
            (
                SELECT [eventId], [lineId]
                FROM [task].[TaskSuggestion]
                WHERE [eventId] = ?
                GROUP BY [eventId], [lineId]
                HAVING COUNT(*) > 1
            ) AS duplicates;
            """,
            success_event_id,
        )
        if int(duplicate_groups) != 0:
            raise AssertionError(
                f"Found {duplicate_groups} duplicate event-line groups."
            )

        conflicting_suggestion = build_suggestion(
            suffix=suffix,
            task_codes=(governed_codes[1], None, governed_codes[0]),
        )
        try:
            persist_task_suggestion(
                connection,
                conflicting_suggestion,
                event_id=success_event_id,
                region_code=REGION_CODE,
                model_deployment=MODEL_DEPLOYMENT,
                prompt_version=PROMPT_VERSION,
                task_master_sha256=TASK_MASTER_SHA256,
            )
        except TaskSuggestionPersistenceError as exc:
            if "already belongs to different" not in str(exc):
                raise AssertionError(
                    f"Conflicting replay failed unexpectedly: {exc}"
                ) from exc
        else:
            raise AssertionError("Conflicting replay was not rejected.")

        invalid_task_code = f"CICT-187-NOT-GOVERNED-{suffix}"
        if any(
            item["task_code"] == invalid_task_code
            for item in catalog
        ):
            raise AssertionError("Generated invalid task code exists in TaskMaster.")
        invalid_suggestion = build_suggestion(
            suffix=f"invalid-{suffix}",
            task_codes=(governed_codes[0], invalid_task_code),
        )
        try:
            persist_task_suggestion(
                connection,
                invalid_suggestion,
                event_id=invalid_event_id,
                region_code=REGION_CODE,
                model_deployment=MODEL_DEPLOYMENT,
                prompt_version=PROMPT_VERSION,
                task_master_sha256=TASK_MASTER_SHA256,
            )
        except TaskSuggestionPersistenceError as exc:
            if "outside pub.v1_TaskMaster" not in str(exc):
                raise AssertionError(
                    f"Invalid-code batch failed unexpectedly: {exc}"
                ) from exc
        else:
            raise AssertionError("Invalid-code batch was not rejected.")

        invalid_rows = scalar(
            cursor,
            """
            SELECT COUNT(*)
            FROM [task].[TaskSuggestion]
            WHERE [eventId] = ?;
            """,
            invalid_event_id,
        )
        if int(invalid_rows) != 0:
            raise AssertionError(
                f"Invalid batch left {invalid_rows} partial durable rows."
            )

        return {
            "databaseUser": database_user,
            "successEventId": success_event_id,
            "invalidEventId": invalid_event_id,
            "governedTaskCodes": list(governed_codes),
            "initialPersistedRowCount": inserted,
            "matchingReplayRowCount": replay_count,
            "finalDurableRowCount": len(replay_rows),
            "suggestedCount": sum(
                row[1] == "SUGGESTED"
                for row in replay_rows
            ),
            "unresolvedCount": sum(
                row[1] == "UNRESOLVED"
                for row in replay_rows
            ),
            "duplicateEventLineGroups": int(duplicate_groups),
            "conflictingReplayRejected": True,
            "invalidBatchRejected": True,
            "partialRowsFromInvalidBatch": int(invalid_rows),
            "modelDeployment": MODEL_DEPLOYMENT,
            "promptVersion": PROMPT_VERSION,
            "taskMasterSha256": TASK_MASTER_SHA256,
        }
    finally:
        cursor.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run CICT-187 acceptance against the live Circle K sandbox."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Acknowledge that the harness will retain acceptance rows in Azure SQL.",
    )
    args = parser.parse_args()
    if not args.execute:
        parser.error(
            "live writes are disabled by default; rerun with --execute "
            "after reviewing this script"
        )

    connection: pyodbc.Connection | None = None
    try:
        print("Acquiring Azure SQL token from Azure CLI...")
        connection = connect_to_database(get_azure_sql_token())
        evidence = run_acceptance(connection)
        print("\nCICT-187 LIVE ACCEPTANCE PASS")
        print(json.dumps(evidence, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"\nCICT-187 LIVE ACCEPTANCE FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
