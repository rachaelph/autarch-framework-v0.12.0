"""Precedent — the council remembers your rulings and applies them.

When you overrule (or ratify) an action, that judgment is recorded as a
precedent keyed by capability. Next time a similar motion arises, the precedent
is surfaced and — for overrules — applied automatically, so the council learns
your standards instead of asking the same question twice.

Backed by its own SQLite file so it never contends with the why-memory store.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .util import configure_sqlite


@dataclass
class Precedent:
    capability: str
    decision: str  # "ratify" | "overrule"
    count: int
    last_intent: str
    updated_at: float

    def note(self) -> str:
        verb = {"ratify": "ratified", "overrule": "overruled"}.get(self.decision, self.decision)
        times = "time" if self.count == 1 else "times"
        return f"precedent: you {verb} '{self.capability}' {self.count} {times} before"


class PrecedentStore:
    def __init__(self, db_path="./.autarch/precedents.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        configure_sqlite(self._conn)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS precedents (
                capability TEXT NOT NULL,
                decision TEXT NOT NULL,
                count INTEGER NOT NULL,
                last_intent TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY (capability, decision)
            )
            """
        )
        self._conn.commit()

    def record(self, capability: str, decision: str, intent_text: str = "") -> None:
        """Register one ruling. Idempotent upsert that increments the count."""
        if decision not in ("ratify", "overrule"):
            return
        now = time.time()
        row = self._conn.execute(
            "SELECT count FROM precedents WHERE capability = ? AND decision = ?",
            (capability, decision),
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO precedents (capability, decision, count, last_intent, updated_at) "
                "VALUES (?, ?, 1, ?, ?)",
                (capability, decision, intent_text, now),
            )
        else:
            self._conn.execute(
                "UPDATE precedents SET count = count + 1, last_intent = ?, updated_at = ? "
                "WHERE capability = ? AND decision = ?",
                (intent_text, now, capability, decision),
            )
        self._conn.commit()

    def lookup(self, capability: str) -> Optional[Precedent]:
        """Return the dominant precedent for a capability, or None.

        Dominant = highest count; ties broken by most recently updated.
        """
        rows = self._conn.execute(
            "SELECT capability, decision, count, last_intent, updated_at "
            "FROM precedents WHERE capability = ? "
            "ORDER BY count DESC, updated_at DESC LIMIT 1",
            (capability,),
        ).fetchone()
        if rows is None:
            return None
        return Precedent(
            capability=rows["capability"],
            decision=rows["decision"],
            count=rows["count"],
            last_intent=rows["last_intent"] or "",
            updated_at=rows["updated_at"],
        )

    def close(self) -> None:
        self._conn.close()
