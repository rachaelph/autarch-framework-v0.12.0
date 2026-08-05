"""Durable run journal — crash-safe, resumable execution.

The cardinal sin of agent execution is doing the same side effect twice after a
crash. Autarch's journal records the lifecycle of every run in a durable
SQLite table (WAL-mode), so a process that dies mid-run can be **resumed without
re-executing** an action that already happened.

The journal stores, per run: the intent, a status, the current step, and a JSON
payload (including the final ``why_id`` once an action has executed). Resume is
idempotent: if a run already reached a terminal state, its recorded outcome is
returned as-is; the side effect is never repeated.

Steps (monotonic): ``created -> deliberated -> decided -> executed -> complete``
with terminal states ``complete``, ``blocked``, and ``failed``.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .util import configure_sqlite

STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_BLOCKED = "blocked"
STATUS_FAILED = "failed"
_TERMINAL = {STATUS_COMPLETE, STATUS_BLOCKED, STATUS_FAILED}


@dataclass
class RunState:
    run_id: str
    intent: str
    status: str
    step: str
    why_id: Optional[str]
    created_at: float
    updated_at: float
    payload: dict

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL


class RunJournal:
    def __init__(self, db_path="./.autarch/runs.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        configure_sqlite(self._conn)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                intent TEXT,
                status TEXT NOT NULL,
                step TEXT NOT NULL,
                why_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def start(self, run_id: str, intent: str) -> None:
        """Begin (or no-op if already present) a run record."""
        now = time.time()
        self._conn.execute(
            "INSERT OR IGNORE INTO runs (run_id, intent, status, step, why_id, created_at, updated_at, payload) "
            "VALUES (?, ?, ?, ?, NULL, ?, ?, '{}')",
            (run_id, intent, STATUS_RUNNING, "created", now, now),
        )
        self._conn.commit()

    def record_step(
        self,
        run_id: str,
        step: str,
        status: str = STATUS_RUNNING,
        why_id: Optional[str] = None,
        data: Optional[dict] = None,
    ) -> None:
        """Persist a step transition. Merges `data` into the run payload."""
        row = self._conn.execute("SELECT payload FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        payload = json.loads(row["payload"]) if row else {}
        if data:
            payload.update(data)
        self._conn.execute(
            "UPDATE runs SET step = ?, status = ?, why_id = COALESCE(?, why_id), "
            "updated_at = ?, payload = ? WHERE run_id = ?",
            (step, status, why_id, time.time(), json.dumps(payload), run_id),
        )
        self._conn.commit()

    def get(self, run_id: str) -> Optional[RunState]:
        row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return RunState(
            run_id=row["run_id"],
            intent=row["intent"],
            status=row["status"],
            step=row["step"],
            why_id=row["why_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            payload=json.loads(row["payload"]),
        )

    def unfinished(self) -> List[RunState]:
        """Runs that started but never reached a terminal state (crash recovery)."""
        rows = self._conn.execute(
            "SELECT * FROM runs WHERE status = ? ORDER BY created_at ASC", (STATUS_RUNNING,)
        ).fetchall()
        return [self.get(r["run_id"]) for r in rows]  # type: ignore[misc]

    def close(self) -> None:
        self._conn.close()
