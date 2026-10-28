"""Transactional, idempotent TaskSuggestion persistence."""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from .agent_invocation_models import TaskSuggestion
from .persistence import DatabaseConnection


class TaskSuggestionPersistenceError(RuntimeError):
    """Raised when TaskSuggestion persistence cannot complete."""


_TRANSACTION_PREAMBLE = """
SET XACT_ABORT ON;
"""

_SELECT_TASK_CODES = """
SELECT [taskCode]
FROM [pub].[v1_TaskMaster]
WHERE [regionCode] = ?;
"""

_SELECT_TASK_CATALOG = """
SELECT
    [taskCode],
    [description]
FROM [pub].[v1_TaskMaster]
WHERE [regionCode] = ?
ORDER BY [taskCode];
"""

_SELECT_EXISTING = """
SELECT
    [packageId],
    [invoiceId],
    [runId],
    [correlationId],
    [lineId],
    [resultStatus],
    [taskCode]
FROM [task].[TaskSuggestion] WITH (UPDLOCK, HOLDLOCK)
WHERE [eventId] = ?
ORDER BY [lineId];
"""

_INSERT_LINES = """
INSERT INTO [task].[TaskSuggestion]
(
    [packageId],
    [invoiceId],
    [runId],
    [correlationId],
    [eventId],
    [lineId],
    [resultStatus],
    [recommendation],
    [taskCode],
    [assetClassCode],
    [confidence],
    [rationale],
    [evidenceRefs],
    [policyIds],
    [agentExecutionId],
    [agentSessionId],
    [modelDeployment],
    [promptVersion],
    [taskMasterSha256],
    [createdAtUtc],
    [status]
)
VALUES
(
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
);
"""


def _json_array(values: Sequence[str]) -> str:
    return json.dumps(
        list(values),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _result_status(task_code: str | None) -> str:
    return "SUGGESTED" if task_code else "UNRESOLVED"


def _line_values(
    suggestion: TaskSuggestion,
    *,
    event_id: str,
    model_deployment: str,
    prompt_version: str,
    task_master_sha256: str,
) -> list[tuple[Any, ...]]:
    values: list[tuple[Any, ...]] = []

    for line in suggestion.lines:
        line_id = line.line_id.strip()
        task_code = (
            line.task_code.strip()
            if line.task_code is not None
            else None
        )

        if not line_id:
            raise TaskSuggestionPersistenceError(
                "Every TaskSuggestion line requires a line ID."
            )

        values.append(
            (
                suggestion.package_id,
                suggestion.invoice_id,
                suggestion.run_id,
                suggestion.correlation_id,
                event_id,
                line_id,
                _result_status(task_code),
                line.recommendation,
                task_code,
                line.asset_class_code,
                line.confidence,
                line.rationale,
                _json_array(line.evidence_refs),
                _json_array(line.policy_ids),
                suggestion.agent_execution_id,
                suggestion.agent_session_id,
                model_deployment,
                prompt_version,
                task_master_sha256,
                suggestion.created_at_utc.replace(tzinfo=None),
                suggestion.status,
            )
        )

    return values


def _validate_replay(
    existing_rows: Sequence[Sequence[Any]],
    expected_rows: Sequence[Sequence[Any]],
) -> None:
    existing = {
        str(row[4]): (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[5]),
            None if row[6] is None else str(row[6]),
        )
        for row in existing_rows
    }
    expected = {
        str(row[5]): (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[6]),
            None if row[8] is None else str(row[8]),
        )
        for row in expected_rows
    }

    if existing != expected:
        raise TaskSuggestionPersistenceError(
            "The event ID already belongs to different "
            "TaskSuggestion results."
        )


def load_task_catalog(
    connection: DatabaseConnection,
    *,
    region_code: str,
) -> list[dict[str, str]]:
    """Load the active governed TaskMaster catalog."""

    normalized_region = region_code.strip().upper()
    if normalized_region not in {"US", "CN"}:
        raise TaskSuggestionPersistenceError(
            f"Unsupported TaskMaster region: {region_code}"
        )

    cursor = connection.cursor()
    try:
        cursor.execute(
            _SELECT_TASK_CATALOG,
            (normalized_region,),
        )
        rows = list(cursor.fetchall())
    except Exception as exc:
        raise TaskSuggestionPersistenceError(
            f"TaskMaster catalog load failed: {exc}"
        ) from exc
    finally:
        cursor.close()

    catalog = [
        {
            "task_code": str(row[0]),
            "description": str(row[1]),
        }
        for row in rows
    ]

    if not catalog:
        raise TaskSuggestionPersistenceError(
            f"No active TaskMaster rows exist for {normalized_region}."
        )

    if len(
        {
            item["task_code"]
            for item in catalog
        }
    ) != len(catalog):
        raise TaskSuggestionPersistenceError(
            "Published TaskMaster contains duplicate task codes."
        )

    return catalog

def persist_task_suggestion(
    connection: DatabaseConnection,
    suggestion: TaskSuggestion,
    *,
    event_id: str,
    region_code: str,
    model_deployment: str,
    prompt_version: str,
    task_master_sha256: str,
) -> int:
    """Persist every TaskSuggestion line in one transaction."""

    if not event_id.strip():
        raise TaskSuggestionPersistenceError(
            "An event ID is required."
        )
    if not suggestion.lines:
        raise TaskSuggestionPersistenceError(
            "At least one TaskSuggestion line is required."
        )
    if len({line.line_id for line in suggestion.lines}) != len(
        suggestion.lines
    ):
        raise TaskSuggestionPersistenceError(
            "TaskSuggestion line IDs must be unique."
        )

    normalized_region = region_code.strip().upper()
    if normalized_region not in {"US", "CN"}:
        raise TaskSuggestionPersistenceError(
            f"Unsupported TaskMaster region: {region_code}"
        )

    normalized_hash = task_master_sha256.strip().upper()
    if re.fullmatch(r"[0-9A-F]{64}", normalized_hash) is None:
        raise TaskSuggestionPersistenceError(
            "TaskMaster SHA-256 must contain 64 uppercase "
            "hexadecimal characters."
        )

    values = _line_values(
        suggestion,
        event_id=event_id.strip(),
        model_deployment=model_deployment.strip(),
        prompt_version=prompt_version.strip(),
        task_master_sha256=normalized_hash,
    )

    cursor = connection.cursor()
    transaction_started = False

    try:
        cursor.execute(_TRANSACTION_PREAMBLE)
        transaction_started = True

        cursor.execute(
            _SELECT_TASK_CODES,
            (normalized_region,),
        )
        allowed_task_codes = {
            str(row[0])
            for row in cursor.fetchall()
        }

        invalid_task_codes = sorted(
            {
                str(row[8])
                for row in values
                if row[8] is not None
                and str(row[8]) not in allowed_task_codes
            }
        )
        if invalid_task_codes:
            raise TaskSuggestionPersistenceError(
                "TaskSuggestion contains task codes outside "
                f"pub.v1_TaskMaster: {invalid_task_codes}"
            )

        cursor.execute(
            _SELECT_EXISTING,
            (event_id.strip(),),
        )
        existing_rows = list(cursor.fetchall())

        if existing_rows:
            _validate_replay(existing_rows, values)
            connection.commit()
            transaction_started = False
            return len(existing_rows)

        cursor.executemany(_INSERT_LINES, values)
        connection.commit()
        transaction_started = False
        return len(values)
    except TaskSuggestionPersistenceError:
        if transaction_started:
            connection.rollback()
        raise
    except Exception as exc:
        if transaction_started:
            connection.rollback()
        raise TaskSuggestionPersistenceError(
            f"TaskSuggestion persistence failed: {exc}"
        ) from exc
    finally:
        cursor.close()


__all__ = [
    "TaskSuggestionPersistenceError",
    "persist_task_suggestion",
]
