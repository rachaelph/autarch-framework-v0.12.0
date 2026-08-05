"""The approval plane — presiding, out of band and asynchronously.

Until now "you preside" required a human at the CLI at the exact moment an action
was proposed. That does not scale to teams, long-running agents, or mobile
approval. The approval plane decouples *proposing* from *ratifying*: an agent (or
the gateway) submits a pending approval; a human — or a **quorum** of humans, from
any process or device — ratifies or overrules it later, out of band.

It is a small, dependency-free, SQLite-backed queue (WAL, concurrency-safe) with
a blocking ``wait`` (polling) for callers that want to pause until a decision.
Every decision is attributed (``by``) so the audit trail records *who* presided.

This is the durable substrate under a future web/mobile approval console; the
gateway (``autarch.gateway``) exposes it over HTTP.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .contracts import new_id
from .util import configure_sqlite

PENDING = "pending"
RATIFIED = "ratified"
OVERRULED = "overruled"
EXPIRED = "expired"


@dataclass
class Approval:
    """One pending (or decided) request for human ratification."""

    intent_text: str
    capability: str
    params: dict = field(default_factory=dict)
    rationale: str = ""
    requested_by: str = ""
    quorum: int = 1
    status: str = PENDING
    approvals: List[str] = field(default_factory=list)  # who ratified
    decided_by: str = ""
    decision_reason: str = ""
    expires_at: Optional[float] = None
    id: str = field(default_factory=lambda: new_id("appr"))
    created_at: float = field(default_factory=time.time)

    @property
    def pending(self) -> bool:
        return self.status == PENDING

    @property
    def ratified(self) -> bool:
        return self.status == RATIFIED


class ApprovalQueue:
    """A durable, concurrency-safe queue of approvals awaiting a human decision."""

    def __init__(self, db_path="./.autarch/approvals.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        configure_sqlite(self._conn)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    # -- submission -------------------------------------------------------
    def submit(self, approval: Approval) -> str:
        self._conn.execute(
            "INSERT INTO approvals (id, created_at, status, payload) VALUES (?,?,?,?)",
            (approval.id, approval.created_at, approval.status, _dump(approval)),
        )
        self._conn.commit()
        return approval.id

    def request(
        self,
        intent_text: str,
        capability: str,
        params: Optional[dict] = None,
        rationale: str = "",
        requested_by: str = "",
        quorum: int = 1,
        ttl_seconds: Optional[float] = None,
    ) -> Approval:
        """Convenience: build and submit an Approval in one call."""
        approval = Approval(
            intent_text=intent_text,
            capability=capability,
            params=params or {},
            rationale=rationale,
            requested_by=requested_by,
            quorum=max(1, quorum),
            expires_at=(time.time() + ttl_seconds) if ttl_seconds else None,
        )
        self.submit(approval)
        return approval

    # -- decisions --------------------------------------------------------
    def ratify(self, approval_id: str, by: str = "autarch") -> Approval:
        """Record one ratifying vote. Reaches RATIFIED once quorum is met."""
        appr = self._require(approval_id)
        if not appr.pending:
            return appr
        if self._expired(appr):
            return self._store(appr, EXPIRED)
        if by not in appr.approvals:
            appr.approvals.append(by)
        if len(appr.approvals) >= appr.quorum:
            appr.decided_by = by
            return self._store(appr, RATIFIED)
        return self._store(appr, PENDING)  # still gathering votes

    def overrule(self, approval_id: str, by: str = "autarch", reason: str = "") -> Approval:
        """A single overrule decides the request immediately (safety-first)."""
        appr = self._require(approval_id)
        if not appr.pending:
            return appr
        appr.decided_by = by
        appr.decision_reason = reason
        return self._store(appr, OVERRULED)

    # -- reads ------------------------------------------------------------
    def get(self, approval_id: str) -> Optional[Approval]:
        row = self._conn.execute(
            "SELECT payload FROM approvals WHERE id=?", (approval_id,)
        ).fetchone()
        return _load(row["payload"]) if row else None

    def pending_list(self) -> List[Approval]:
        rows = self._conn.execute(
            "SELECT payload FROM approvals WHERE status=? ORDER BY created_at", (PENDING,)
        ).fetchall()
        out = []
        for row in rows:
            appr = _load(row["payload"])
            if self._expired(appr):
                self._store(appr, EXPIRED)
                continue
            out.append(appr)
        return out

    def all(self) -> List[Approval]:
        rows = self._conn.execute(
            "SELECT payload FROM approvals ORDER BY created_at"
        ).fetchall()
        return [_load(r["payload"]) for r in rows]

    def wait(self, approval_id: str, timeout: float = 30.0, poll: float = 0.1) -> Approval:
        """Block until the approval is decided (or the timeout/TTL elapses).

        Polling keeps this dependency-free and safe across processes (the queue is
        the shared source of truth). Returns the final Approval; a caller checks
        ``.ratified`` to proceed.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            appr = self._require(approval_id)
            if not appr.pending:
                return appr
            if self._expired(appr):
                return self._store(appr, EXPIRED)
            time.sleep(poll)
        appr = self._require(approval_id)
        return appr  # still pending at timeout — caller decides what to do

    # -- internals --------------------------------------------------------
    def _require(self, approval_id: str) -> Approval:
        appr = self.get(approval_id)
        if appr is None:
            raise KeyError(f"no approval '{approval_id}'")
        return appr

    @staticmethod
    def _expired(appr: Approval) -> bool:
        return appr.expires_at is not None and time.time() > appr.expires_at

    def _store(self, appr: Approval, status: str) -> Approval:
        appr.status = status
        self._conn.execute(
            "UPDATE approvals SET status=?, payload=? WHERE id=?",
            (status, _dump(appr), appr.id),
        )
        self._conn.commit()
        return appr

    def close(self) -> None:
        self._conn.close()


def _dump(appr: Approval) -> str:
    return json.dumps(appr.__dict__, default=str)


def _load(payload: str) -> Approval:
    data = json.loads(payload)
    return Approval(**data)
