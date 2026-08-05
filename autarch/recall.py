"""Recall Memory — long-term agent memory that governs itself.

Most agents bolt a vector database onto the model and inherit its failure modes:
stale facts retrieved with false confidence, "similar-but-irrelevant" noise,
unbounded context growth, and — worst — *poisoned* memory that silently taints
every future session. Autarch treats long-term memory as a **governed
substrate**, giving it the same guarantees as the action ledger (`memory.py`):

* **Provenance** — every memory is Ed25519-signed and attributable; a forged or
  injected memory fails ``verify_provenance``. Poisoning becomes non-silent.
* **Integrity** — memories are sealed into a per-origin hash chain, so tampering
  with what was stored is detectable (``verify_chain``).
* **Governed dynamics** — memories *decay*, are *reinforced* by use, and are
  *superseded* by newer beliefs, so the store updates instead of hoarding stale
  snippets. The immutable, signed assertion never changes; usage and supersession
  live in a separate mutable overlay, so integrity survives every update — the
  same trick that lets the ledger honor right-to-be-forgotten without breaking.
* **Bounded recall** — retrieval is *hybrid* (lexical + semantic + recency +
  structure), trust-gated, and fits a **token budget**, so context can neither
  blow up nor be dominated by mere keyword similarity.
* **Attributable sharing** — because memories are signed, they can travel across
  a multi-agent mesh with authorship intact (no shared-secret forgery), so branch
  work re-joins shared institutional knowledge instead of losing it.

This is the layered architecture the field converged on — working / episodic /
semantic / procedural memory — but with governance the others lack.

Self-contained: pure Python + stdlib SQLite. Semantic search is an *optional*
seam (:class:`~autarch.intelligence.embedding.EmbeddingProvider`); without one,
recall falls back to lexical + structural + recency, which already beats naive
vector similarity for logical relevance.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .contracts import new_id
from .intelligence.embedding import EmbeddingProvider, cosine, tokenize
from .provenance import NodeIdentity, derive_node_id, verify_signature
from .util import configure_sqlite

# Memory kinds — the classic layered taxonomy.
WORKING = "working"        # transient scratch state for the current task
EPISODIC = "episodic"      # time-stamped events ("what happened")
SEMANTIC = "semantic"      # distilled facts ("what is true")
PROCEDURAL = "procedural"  # know-how ("how to do X")
KINDS = (WORKING, EPISODIC, SEMANTIC, PROCEDURAL)

# Fields that are NOT part of the sealed/signed assertion. Usage counters and
# supersession are memory *dynamics* that change after authorship; the signature
# and stored embedding are derived. Keeping them out of the payload lets the
# store evolve while the authored fact stays immutable and verifiable.
_MUTABLE_FIELDS = ("use_count", "last_used_at", "superseded_by")
_DERIVED_FIELDS = ("signer", "signer_key", "signature", "embedding")


@dataclass
class MemoryEntry:
    """One remembered assertion, plus the dynamics that govern its recall.

    The *content*, *kind*, *subject*, *scope*, *salience*, *decay_rate*,
    *source_why_id* and *derived_from* form the immutable, signed assertion. The
    *use_count*, *last_used_at* and *superseded_by* fields are mutable overlay
    state (reinforcement, decay, belief revision) kept outside the seal.
    """

    content: str
    kind: str = SEMANTIC
    subject: str = ""               # entity/topic key for logical grouping
    tags: list = field(default_factory=list)
    scope: str = "default"          # namespace (e.g. "private:agent" vs "realm")
    salience: float = 1.0           # 0..1 base importance
    decay_rate: float = 0.0         # per-second exponential decay (0 = permanent)
    source_why_id: str = ""         # link to the action that learned this
    derived_from: list = field(default_factory=list)  # ids consolidated into this
    # -- mutable overlay (never sealed) --
    superseded_by: str = ""         # id of the belief that replaced this one
    use_count: int = 0
    last_used_at: float = 0.0
    # -- provenance / derived (never sealed) --
    signer: str = ""
    signer_key: str = ""
    signature: str = ""
    embedding: Optional[list] = None
    id: str = field(default_factory=lambda: new_id("mem"))
    created_at: float = field(default_factory=time.time)


def _seal(prev_seal: str, payload: str) -> str:
    return hashlib.sha256((prev_seal + payload).encode("utf-8")).hexdigest()


def _entry_payload(entry: MemoryEntry) -> str:
    """Deterministic JSON of the immutable assertion (mutable/derived excluded)."""
    data = asdict(entry)
    for key in _MUTABLE_FIELDS + _DERIVED_FIELDS:
        data.pop(key, None)
    return json.dumps(data, sort_keys=True)


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) for budget-bounded recall."""
    return max(1, len(text or "") // 4)


def effective_strength(entry: MemoryEntry, now: float) -> float:
    """How strong a memory is right now: salience decayed by age, lifted by use.

    Unused, old, high-decay memories fade toward zero; frequently recalled ones
    are reinforced and persist. This is the signal that lets the store forget
    gracefully instead of hoarding every stale snippet with equal weight.
    """
    age = max(0.0, now - entry.created_at)
    decayed = entry.salience * math.exp(-entry.decay_rate * age)
    reinforcement = 1.0 + math.log1p(max(0, entry.use_count))
    return decayed * reinforcement


def lexical_score(query: str, content: str) -> float:
    """Keyword relevance in [0, 1] — deterministic, no FTS5/vector dependency."""
    q = set(tokenize(query))
    if not q:
        return 0.0
    c = tokenize(content)
    if not c:
        return 0.0
    cset = set(c)
    hits = [t for t in q if t in cset]
    if not hits:
        return 0.0
    coverage = len(hits) / len(q)
    tf = sum(c.count(t) for t in hits) / len(c)
    return min(1.0, 0.8 * coverage + 0.2 * min(1.0, tf * 3.0))


class RecallMemory:
    """A governed, self-revising long-term memory store (SQLite-backed).

    Writes are provenance-signed and hash-chained; reads are hybrid-ranked,
    trust-gated and budget-bounded. Optional `embedder` enables the semantic
    ranking signal; without it, recall uses lexical + structural + recency.
    """

    def __init__(
        self,
        db_path="./.autarch/recall.db",
        node_id: str = "local",
        identity: Optional[NodeIdentity] = None,
        embedder: Optional[EmbeddingProvider] = None,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.node_id = node_id
        self.identity = identity
        self.embedder = embedder
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        configure_sqlite(self._conn)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mem (
                id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                kind TEXT,
                subject TEXT,
                scope TEXT,
                content TEXT,
                salience REAL,
                decay_rate REAL,
                payload TEXT NOT NULL,
                seal TEXT,
                prev_seal TEXT,
                origin TEXT,
                signer TEXT,
                signer_key TEXT,
                signature TEXT,
                embedding TEXT,
                superseded_by TEXT DEFAULT '',
                use_count INTEGER DEFAULT 0,
                last_used_at REAL DEFAULT 0
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_scope ON mem(scope)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_subject ON mem(subject)")
        self._conn.commit()

    # -- write ------------------------------------------------------------
    def _last_seal(self, origin: str) -> str:
        row = self._conn.execute(
            "SELECT seal FROM mem WHERE COALESCE(origin, 'local') = ? AND seal IS NOT NULL "
            "ORDER BY rowid DESC LIMIT 1",
            (origin,),
        ).fetchone()
        return row["seal"] if row else ""

    def record(self, entry: MemoryEntry) -> str:
        """Persist a memory: embed (if configured), seal, sign, store."""
        if entry.embedding is None and self.embedder is not None:
            entry.embedding = self.embedder.embed(entry.content)

        payload = _entry_payload(entry)
        prev_seal = self._last_seal(self.node_id)
        seal = _seal(prev_seal, payload)

        signer = signer_key = signature = ""
        if self.identity is not None and self.identity.can_sign:
            signer = self.identity.node_id
            signer_key = self.identity.public_hex
            signature = self.identity.sign(seal.encode("utf-8"))

        self._conn.execute(
            "INSERT INTO mem (id, created_at, kind, subject, scope, content, salience, "
            "decay_rate, payload, seal, prev_seal, origin, signer, signer_key, signature, "
            "embedding, superseded_by, use_count, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.id, entry.created_at, entry.kind, entry.subject, entry.scope,
                entry.content, entry.salience, entry.decay_rate, payload, seal, prev_seal,
                self.node_id, signer, signer_key, signature,
                json.dumps(entry.embedding) if entry.embedding is not None else None,
                entry.superseded_by, entry.use_count, entry.last_used_at,
            ),
        )
        self._conn.commit()
        return entry.id

    def remember(self, content: str, **kwargs) -> str:
        """Convenience: build and record a :class:`MemoryEntry` from `content`."""
        return self.record(MemoryEntry(content=content, **kwargs))

    def supersede(self, old_id: str, content: Optional[str] = None,
                  entry: Optional[MemoryEntry] = None, **kwargs) -> str:
        """Replace a belief: record the new memory, retire the old (auditably).

        The old entry is not deleted — it is marked ``superseded_by`` the new one,
        so recall returns the *current* belief by default while the revision
        history stays verifiable. New entry inherits the old one's subject/scope/
        kind unless overridden. This is the "update" that stops agents from
        blending stale facts with current ones.
        """
        old = self.get(old_id)
        if entry is None:
            if content is None:
                raise ValueError("supersede requires `content` or an `entry`")
            defaults: dict = {}
            if old is not None:
                defaults = {"subject": old.subject, "scope": old.scope, "kind": old.kind}
            defaults.update(kwargs)
            entry = MemoryEntry(content=content, **defaults)
        new_id_ = self.record(entry)
        if old is not None:
            self._conn.execute(
                "UPDATE mem SET superseded_by = ? WHERE id = ?", (new_id_, old_id)
            )
            self._conn.commit()
        return new_id_

    # -- read -------------------------------------------------------------
    def _hydrate(self, row) -> MemoryEntry:
        entry = MemoryEntry(**json.loads(row["payload"]))
        entry.superseded_by = row["superseded_by"] or ""
        entry.use_count = row["use_count"] or 0
        entry.last_used_at = row["last_used_at"] or 0.0
        entry.signer = row["signer"] or ""
        entry.signer_key = row["signer_key"] or ""
        entry.signature = row["signature"] or ""
        if row["embedding"]:
            entry.embedding = json.loads(row["embedding"])
        return entry

    def get(self, mem_id: str) -> Optional[MemoryEntry]:
        row = self._conn.execute("SELECT * FROM mem WHERE id = ?", (mem_id,)).fetchone()
        return self._hydrate(row) if row is not None else None

    def recall(
        self,
        query: str,
        *,
        k: int = 5,
        kind: Optional[str] = None,
        scope: Optional[str] = None,
        subject: Optional[str] = None,
        tags: Optional[List[str]] = None,
        token_budget: Optional[int] = None,
        min_trust: int = 0,
        include_superseded: bool = False,
        reinforce: bool = True,
        now: Optional[float] = None,
        weights: Optional[Dict[str, float]] = None,
    ) -> List[MemoryEntry]:
        """Retrieve the most relevant memories — hybrid, trust-gated, bounded.

        Ranking blends four signals so "relevant" wins over merely "similar":
        lexical overlap, semantic similarity (if an embedder is set), current
        strength (salience decayed by age, lifted by use), and structural filters
        (`kind`/`scope`/`subject`/`tags`). ``min_trust=1`` quarantines any memory
        whose provenance does not verify (defense against poisoning). With a
        ``token_budget`` the result is greedily filled to fit a context window;
        otherwise the top ``k`` are returned. Recalled memories are reinforced.
        """
        now = time.time() if now is None else now

        sql = "SELECT * FROM mem WHERE 1=1"
        params: list = []
        if kind:
            sql += " AND kind = ?"; params.append(kind)
        if scope:
            sql += " AND scope = ?"; params.append(scope)
        if subject:
            sql += " AND subject = ?"; params.append(subject)
        if not include_superseded:
            sql += " AND (superseded_by IS NULL OR superseded_by = '')"
        rows = self._conn.execute(sql, params).fetchall()
        entries = [self._hydrate(r) for r in rows]

        if tags:
            wanted = set(tags)
            entries = [e for e in entries if wanted & set(e.tags)]
        if min_trust >= 1:
            entries = [e for e in entries if self.verify_provenance(e.id) is True]

        qvec = self.embedder.embed(query) if self.embedder is not None else None
        scored = sorted(
            ((self._score(query, e, qvec, now, weights), e) for e in entries),
            key=lambda pair: pair[0],
            reverse=True,
        )

        chosen: List[MemoryEntry] = []
        if token_budget is not None:
            used = 0
            for _, entry in scored:
                cost = estimate_tokens(entry.content)
                if chosen and used + cost > token_budget:
                    break
                chosen.append(entry)
                used += cost
        else:
            chosen = [entry for _, entry in scored[:k]]

        if reinforce and chosen:
            self.reinforce([e.id for e in chosen], now=now)
            for entry in chosen:
                entry.use_count += 1
                entry.last_used_at = now
        return chosen

    def _score(self, query, entry, qvec, now, weights) -> float:
        if weights is None:
            weights = (
                {"lexical": 0.5, "semantic": 0.3, "strength": 0.2}
                if qvec is not None
                else {"lexical": 0.7, "semantic": 0.0, "strength": 0.3}
            )
        lex = lexical_score(query, entry.content)
        sem = max(0.0, cosine(qvec, entry.embedding)) if (qvec and entry.embedding) else 0.0
        eff = effective_strength(entry, now)
        strength = eff / (1.0 + eff)  # saturate to [0, 1) for a stable blend
        return (
            weights.get("lexical", 0.0) * lex
            + weights.get("semantic", 0.0) * sem
            + weights.get("strength", 0.0) * strength
        )

    # -- dynamics: reinforce, forget, decay, consolidate ------------------
    def reinforce(self, mem_ids: List[str], now: Optional[float] = None) -> None:
        """Strengthen memories that proved useful (recall reinforcement)."""
        now = time.time() if now is None else now
        self._conn.executemany(
            "UPDATE mem SET use_count = use_count + 1, last_used_at = ? WHERE id = ?",
            [(now, mid) for mid in mem_ids],
        )
        self._conn.commit()

    def forget(self, mem_id: str) -> bool:
        """Delete a memory outright (e.g. a source found compromised). Audited by
        the ledger if the learning action was recorded there."""
        cur = self._conn.execute("DELETE FROM mem WHERE id = ?", (mem_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def decay_sweep(self, min_strength: float, now: Optional[float] = None) -> List[str]:
        """Forget live memories whose effective strength has fallen below a floor.

        This is graceful forgetting: unused, aged, high-decay memories are pruned
        while reinforced ones survive. Superseded entries are left to the caller's
        retention policy. Returns the ids forgotten.
        """
        now = time.time() if now is None else now
        rows = self._conn.execute(
            "SELECT * FROM mem WHERE (superseded_by IS NULL OR superseded_by = '')"
        ).fetchall()
        forgotten: List[str] = []
        for row in rows:
            entry = self._hydrate(row)
            if effective_strength(entry, now) < min_strength:
                self.forget(entry.id)
                forgotten.append(entry.id)
        return forgotten

    def consolidate(
        self,
        subject: Optional[str] = None,
        kind: str = EPISODIC,
        scope: Optional[str] = None,
        summarize=None,
        into_kind: str = SEMANTIC,
        demote: bool = True,
    ) -> Optional[str]:
        """Distil a cluster of memories into one higher-level memory — losslessly.

        Gathers the matching cluster (by `kind`, and optionally `subject`/`scope`),
        summarizes it (`summarize(list[str]) -> str`, or a deterministic default),
        and records a new `into_kind` memory that links back to its sources via
        ``derived_from``. Crucially the originals are **kept** (optionally demoted
        in salience), so consolidation never destroys the granular detail that
        naive summarization throws away. Returns the new memory id, or None if the
        cluster was empty.
        """
        sql = "SELECT * FROM mem WHERE kind = ? AND (superseded_by IS NULL OR superseded_by = '')"
        params: list = [kind]
        if subject:
            sql += " AND subject = ?"; params.append(subject)
        if scope:
            sql += " AND scope = ?"; params.append(scope)
        rows = self._conn.execute(sql, params).fetchall()
        cluster = [self._hydrate(r) for r in rows]
        if not cluster:
            return None

        contents = [e.content for e in cluster]
        if summarize is not None:
            summary = summarize(contents)
        else:
            unique = list(dict.fromkeys(contents))  # order-preserving dedupe
            summary = "Consolidated: " + "; ".join(unique)

        new = MemoryEntry(
            content=summary,
            kind=into_kind,
            subject=subject or (cluster[0].subject if cluster else ""),
            scope=scope or (cluster[0].scope if cluster else "default"),
            derived_from=[e.id for e in cluster],
            salience=1.0,
        )
        new_id_ = self.record(new)

        if demote:
            self._conn.executemany(
                "UPDATE mem SET salience = salience * 0.5 WHERE id = ?",
                [(e.id,) for e in cluster],
            )
            self._conn.commit()
        return new_id_

    # -- integrity & provenance (mirrors the action ledger) ---------------
    def verify(self, mem_id: str) -> Optional[bool]:
        row = self._conn.execute(
            "SELECT payload, seal, prev_seal FROM mem WHERE id = ?", (mem_id,)
        ).fetchone()
        if row is None or row["seal"] is None:
            return None
        return _seal(row["prev_seal"] or "", row["payload"]) == row["seal"]

    def verify_provenance(self, mem_id: str) -> Optional[bool]:
        """True/False authorship check, or None if unsigned/missing.

        Authentic iff the seal is intact, the claimed signer id is bound to the
        signer's public key, and the signature over the seal verifies. A forged or
        content-altered memory fails — poisoning cannot masquerade as trusted.
        """
        row = self._conn.execute(
            "SELECT payload, seal, prev_seal, signer, signer_key, signature FROM mem WHERE id = ?",
            (mem_id,),
        ).fetchone()
        if row is None or not row["signature"]:
            return None
        seal = row["seal"] or ""
        if _seal(row["prev_seal"] or "", row["payload"]) != seal:
            return False
        if row["signer"] != derive_node_id(row["signer_key"] or ""):
            return False
        return verify_signature(row["signer_key"], seal.encode("utf-8"), row["signature"])

    def verify_chain(self) -> Tuple[bool, Optional[str]]:
        """Walk every origin's sub-chain. Returns (ok, first_broken_id)."""
        rows = self._conn.execute(
            "SELECT id, payload, seal, prev_seal, COALESCE(origin, 'local') AS origin "
            "FROM mem ORDER BY rowid ASC"
        ).fetchall()
        expected_prev: Dict[str, str] = {}
        for row in rows:
            if row["seal"] is None:
                continue
            origin = row["origin"]
            stored_prev = row["prev_seal"] or ""
            if _seal(stored_prev, row["payload"]) != row["seal"]:
                return False, row["id"]
            if stored_prev != expected_prev.get(origin, ""):
                return False, row["id"]
            expected_prev[origin] = row["seal"]
        return True, None

    # -- mesh / sync (signed memories travel with authorship intact) ------
    def export_rows(self) -> List[dict]:
        rows = self._conn.execute("SELECT * FROM mem ORDER BY rowid ASC").fetchall()
        return [dict(row) for row in rows]

    def import_row(self, row: dict) -> bool:
        """Merge one foreign memory (grow-only union by id). Returns True if added."""
        if self._conn.execute("SELECT 1 FROM mem WHERE id = ?", (row["id"],)).fetchone():
            return False
        self._conn.execute(
            "INSERT INTO mem (id, created_at, kind, subject, scope, content, salience, "
            "decay_rate, payload, seal, prev_seal, origin, signer, signer_key, signature, "
            "embedding, superseded_by, use_count, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"], row["created_at"], row.get("kind"), row.get("subject"),
                row.get("scope"), row.get("content"), row.get("salience"), row.get("decay_rate"),
                row["payload"], row.get("seal"), row.get("prev_seal"), row.get("origin") or "local",
                row.get("signer") or "", row.get("signer_key") or "", row.get("signature") or "",
                row.get("embedding"), row.get("superseded_by") or "",
                row.get("use_count") or 0, row.get("last_used_at") or 0.0,
            ),
        )
        self._conn.commit()
        return True

    def count(self, include_superseded: bool = True) -> int:
        sql = "SELECT COUNT(*) AS c FROM mem"
        if not include_superseded:
            sql += " WHERE superseded_by IS NULL OR superseded_by = ''"
        return self._conn.execute(sql).fetchone()["c"]

    def all(self) -> List[MemoryEntry]:
        rows = self._conn.execute("SELECT * FROM mem ORDER BY created_at DESC").fetchall()
        return [self._hydrate(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
