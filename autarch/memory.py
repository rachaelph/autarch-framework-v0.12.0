"""Why-Memory — the durable, tamper-evident record of every action.

Backed by SQLite (zero-setup, durable, queryable). Every action writes a
WhyRecord. Records are sealed into a hash chain: each seal is
sha256(prev_seal + payload), so altering or removing any past record breaks the
chain. This is what makes "prove it" meaningful — the evidence can be verified,
not just reprinted.

The chain is kept **per origin** (per node). A single, standalone store uses one
origin ("local") and behaves as one global chain. In a mesh, records authored on
different nodes form independent, individually-verifiable sub-chains, so merging
two nodes' ledgers (a grow-only set, union by id) never breaks integrity.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .contracts import WhyRecord
from .provenance import NodeIdentity, derive_node_id, verify_signature
from .util import configure_sqlite


def _seal(prev_seal: str, payload: str) -> str:
    return hashlib.sha256((prev_seal + payload).encode("utf-8")).hexdigest()


def _record_payload(rec: WhyRecord) -> str:
    """Deterministic JSON for hashing — signature fields are excluded so the
    seal is independent of (and can be signed by) the signature."""
    data = asdict(rec)
    data["signer"] = ""
    data["signer_key"] = ""
    data["signature"] = ""
    return json.dumps(data, sort_keys=True)


class WhyMemory:
    def __init__(self, db_path="./.autarch/why.db", node_id: str = "local", identity: "NodeIdentity | None" = None, same_thread: bool = True):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.node_id = node_id
        self.identity = identity
        # ``same_thread=False`` lets a caller that *serializes its own access*
        # (e.g. the lock-guarded governance gateway) share one connection across
        # request threads. WAL + the caller's lock keep it consistent.
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=same_thread)
        self._conn.row_factory = sqlite3.Row
        configure_sqlite(self._conn)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS why (
                id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                intent_text TEXT,
                capability TEXT,
                executed INTEGER,
                payload TEXT NOT NULL
            )
            """
        )
        # Migration: add integrity-seal, origin, and provenance columns to older DBs.
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(why)")}
        for column in ("seal", "prev_seal", "origin", "signer", "signer_key", "signature"):
            if column not in existing:
                self._conn.execute(f"ALTER TABLE why ADD COLUMN {column} TEXT")
        # Compliance: redactions are kept in a SEPARATE overlay so the original
        # sealed payload is never altered — integrity (verify_chain) stays valid
        # while reads/exports present masked PII (right-to-be-forgotten).
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS redactions (
                why_id TEXT NOT NULL,
                field TEXT NOT NULL,
                reason TEXT,
                redacted_at REAL NOT NULL,
                PRIMARY KEY (why_id, field)
            )
            """
        )
        self._conn.commit()

    def _redacted_fields(self, why_id: str) -> set:
        rows = self._conn.execute(
            "SELECT field FROM redactions WHERE why_id = ?", (why_id,)
        ).fetchall()
        return {r["field"] for r in rows}

    def _hydrate(self, row) -> WhyRecord:
        """Reconstruct a WhyRecord, overlaying provenance fields from columns and
        masking any fields that have been redacted for compliance."""
        rec = WhyRecord(**json.loads(row["payload"]))
        keys = row.keys()
        if "signer" in keys:
            rec.signer = row["signer"] or ""
            rec.signer_key = row["signer_key"] or ""
            rec.signature = row["signature"] or ""
        redacted = self._redacted_fields(rec.id)
        for field in redacted:
            if field == "params":
                rec.params = {"[redacted]": True}
            elif hasattr(rec, field):
                setattr(rec, field, "[redacted]")
        return rec

    def _last_seal(self, origin: str) -> str:
        """The most recent seal in this origin's sub-chain (or '' if none)."""
        row = self._conn.execute(
            "SELECT seal FROM why WHERE COALESCE(origin, 'local') = ? AND seal IS NOT NULL "
            "ORDER BY rowid DESC LIMIT 1",
            (origin,),
        ).fetchone()
        return row["seal"] if row else ""

    def record(self, rec: WhyRecord) -> str:
        payload = _record_payload(rec)
        prev_seal = self._last_seal(self.node_id)
        seal = _seal(prev_seal, payload)

        # Provenance: sign the seal so the record is attributable and unforgeable.
        signer = signer_key = signature = ""
        if self.identity is not None and self.identity.can_sign:
            signer = self.identity.node_id
            signer_key = self.identity.public_hex
            signature = self.identity.sign(seal.encode("utf-8"))

        self._conn.execute(
            "INSERT INTO why (id, created_at, intent_text, capability, executed, payload, "
            "seal, prev_seal, origin, signer, signer_key, signature) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rec.id,
                rec.created_at,
                rec.intent_text,
                rec.capability,
                1 if rec.executed else 0,
                payload,
                seal,
                prev_seal,
                self.node_id,
                signer,
                signer_key,
                signature,
            ),
        )
        self._conn.commit()
        return rec.id

    def has(self, why_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM why WHERE id = ?", (why_id,)
        ).fetchone() is not None

    def get(self, why_id: str) -> Optional[WhyRecord]:
        row = self._conn.execute(
            "SELECT payload, signer, signer_key, signature FROM why WHERE id = ?", (why_id,)
        ).fetchone()
        if row is None:
            return None
        return self._hydrate(row)

    def recent(self, limit: int = 10) -> List[WhyRecord]:
        rows = self._conn.execute(
            "SELECT payload, signer, signer_key, signature FROM why ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._hydrate(r) for r in rows]

    def all(self, limit: Optional[int] = None) -> List[WhyRecord]:
        sql = "SELECT payload, signer, signer_key, signature FROM why ORDER BY created_at DESC"
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        rows = self._conn.execute(sql, params).fetchall()
        return [self._hydrate(r) for r in rows]

    def since(self, seconds_ago: float) -> List[WhyRecord]:
        """Records created within the last `seconds_ago` seconds, newest first."""
        cutoff = time.time() - seconds_ago
        rows = self._conn.execute(
            "SELECT payload, signer, signer_key, signature FROM why "
            "WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()
        return [self._hydrate(r) for r in rows]

    # -- mesh / sync ------------------------------------------------------
    def export_rows(self) -> List[dict]:
        """All rows as plain dicts (insertion order), for syncing to peers."""
        rows = self._conn.execute(
            "SELECT id, created_at, intent_text, capability, executed, payload, seal, prev_seal, "
            "origin, signer, signer_key, signature FROM why ORDER BY rowid ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def import_row(self, row: dict) -> bool:
        """Merge one foreign row (grow-only set union by id). Returns True if added.

        The row keeps its origin, seal, and signature so its authoring node's
        sub-chain stays verifiable here and its authorship can be checked. Tamper-
        checking is the caller's responsibility (mesh rejects rows whose seal does
        not match before calling this).
        """
        if self.has(row["id"]):
            return False
        self._conn.execute(
            "INSERT INTO why (id, created_at, intent_text, capability, executed, payload, seal, "
            "prev_seal, origin, signer, signer_key, signature) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                row["created_at"],
                row.get("intent_text"),
                row.get("capability"),
                row.get("executed"),
                row["payload"],
                row.get("seal"),
                row.get("prev_seal"),
                row.get("origin") or "local",
                row.get("signer") or "",
                row.get("signer_key") or "",
                row.get("signature") or "",
            ),
        )
        self._conn.commit()
        return True

    # -- integrity --------------------------------------------------------
    def get_seal(self, why_id: str) -> Optional[str]:
        row = self._conn.execute("SELECT seal FROM why WHERE id = ?", (why_id,)).fetchone()
        return row["seal"] if row else None

    def origins(self) -> List[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT COALESCE(origin, 'local') AS o FROM why ORDER BY o"
        ).fetchall()
        return [r["o"] for r in rows]

    def verify(self, why_id: str) -> Optional[bool]:
        """Verify one record's seal. True/False, or None if unsealed/missing."""
        row = self._conn.execute(
            "SELECT payload, seal, prev_seal FROM why WHERE id = ?", (why_id,)
        ).fetchone()
        if row is None or row["seal"] is None:
            return None
        return _seal(row["prev_seal"] or "", row["payload"]) == row["seal"]

    def verify_provenance(self, why_id: str) -> Optional[bool]:
        """Verify a record's *authorship*. True/False, or None if unsigned/missing.

        A record is authentic iff: its seal is intact, the claimed signer id is
        cryptographically bound to the signer's public key, and the signature over
        the seal verifies under that key. Together these make the record
        attributable and unforgeable — even a realm member with the shared
        symmetric key cannot forge a record attributed to another node.
        """
        row = self._conn.execute(
            "SELECT payload, seal, prev_seal, signer, signer_key, signature FROM why WHERE id = ?",
            (why_id,),
        ).fetchone()
        if row is None or not row["signature"]:
            return None
        seal = row["seal"] or ""
        if _seal(row["prev_seal"] or "", row["payload"]) != seal:
            return False  # content altered → any signature is moot
        if row["signer"] != derive_node_id(row["signer_key"] or ""):
            return False  # claimed identity not bound to its key
        return verify_signature(row["signer_key"], seal.encode("utf-8"), row["signature"])

    def verify_chain(self) -> Tuple[bool, Optional[str]]:
        """Walk every origin's sub-chain. Returns (ok, first_broken_id).

        Each origin (node) is chained independently, so a merged mesh ledger
        verifies as long as each author's records are intact. Legacy rows written
        before sealing (seal IS NULL) are outside the chain and skipped.
        """
        rows = self._conn.execute(
            "SELECT id, payload, seal, prev_seal, COALESCE(origin, 'local') AS origin "
            "FROM why ORDER BY rowid ASC"
        ).fetchall()
        expected_prev: Dict[str, str] = {}
        for row in rows:
            if row["seal"] is None:
                continue  # legacy unsealed row
            origin = row["origin"]
            stored_prev = row["prev_seal"] or ""
            if _seal(stored_prev, row["payload"]) != row["seal"]:
                return False, row["id"]  # payload or seal altered
            if stored_prev != expected_prev.get(origin, ""):
                return False, row["id"]  # a record was removed or reordered
            expected_prev[origin] = row["seal"]
        return True, None

    # -- compliance: audit export, redaction (RTBF), retention ------------
    def export_audit(self, path=None) -> List[dict]:
        """Export the full audit trail (records + seals + signatures + provenance).

        Returns a list of dicts; if `path` is given, also writes them as JSON
        lines. Redactions are applied to the exported view, so exports honor
        right-to-be-forgotten while the integrity proof (seals) is preserved.
        """
        rows = self.export_rows()
        out = []
        for row in rows:
            payload = json.loads(row["payload"])
            redacted = self._redacted_fields(row["id"])
            for field in redacted:
                if field == "params":
                    payload["params"] = {"[redacted]": True}
                elif field in payload:
                    payload[field] = "[redacted]"
            out.append({
                "id": row["id"],
                "created_at": row["created_at"],
                "origin": row.get("origin") or "local",
                "seal": row.get("seal"),
                "signer": row.get("signer") or "",
                "signature_present": bool(row.get("signature")),
                "redacted_fields": sorted(redacted),
                "record": payload,
            })
        if path is not None:
            from pathlib import Path as _Path

            p = _Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("\n".join(json.dumps(item) for item in out), encoding="utf-8")
        return out

    def redact(self, why_id: str, fields=("params", "result_output", "intent_text"), reason: str = "") -> int:
        """Mask PII fields of a record (right-to-be-forgotten), returning the count.

        Redaction is recorded in a SEPARATE overlay — the original sealed payload is
        never altered, so `verify_chain` and `verify_provenance` keep working
        (the integrity proof that *this record existed and was authored by X*
        remains), while every read/export presents the masked values.
        """
        if not self.has(why_id):
            return 0
        now = time.time()
        n = 0
        for field in fields:
            self._conn.execute(
                "INSERT OR IGNORE INTO redactions (why_id, field, reason, redacted_at) VALUES (?, ?, ?, ?)",
                (why_id, field, reason, now),
            )
            n += 1
        self._conn.commit()
        return n

    def is_redacted(self, why_id: str) -> bool:
        return bool(self._redacted_fields(why_id))

    def prune(self, older_than_seconds: float) -> int:
        """Delete records older than a cutoff (data-retention policy). Returns count.

        Note: pruning removes records from their origin's hash chain. Export the
        audit trail first if you need a retained, verifiable copy. Intended for
        retention windows on non-regulated data or post-archive cleanup.
        """
        cutoff = time.time() - older_than_seconds
        ids = [r["id"] for r in self._conn.execute(
            "SELECT id FROM why WHERE created_at < ?", (cutoff,)
        ).fetchall()]
        self._conn.execute("DELETE FROM why WHERE created_at < ?", (cutoff,))
        for rid in ids:
            self._conn.execute("DELETE FROM redactions WHERE why_id = ?", (rid,))
        self._conn.commit()
        return len(ids)

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS c FROM why").fetchone()["c"]

    def close(self) -> None:
        self._conn.close()
