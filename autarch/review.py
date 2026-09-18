"""Durable, digest-bound human review for release artifacts.

``ApprovalQueue`` is intentionally a small asynchronous approval primitive.  A
release package needs stricter semantics: the decision must be bound to the exact
content reviewed, only named reviewers may vote, votes must be concurrency-safe,
and a changed package must supersede its prior decision.  This module provides
that reusable release-control layer without coupling it to a specific product.

The store is dependency-free SQLite in WAL mode. Its event stream is hash chained
so accidental or isolated after-the-fact mutation is detectable. This is not a
substitute for database access control or externally anchored signatures: an
administrator able to rewrite the complete database could recompute an unkeyed
chain. Reviewer names are attributed but not authenticated by this module;
production callers must map an authenticated IdP/service identity to ``reviewer``
rather than trust free-form CLI input.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from .contracts import new_id
from .util import configure_sqlite

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"
SUPERSEDED = "superseded"
_TERMINAL = {APPROVED, REJECTED, EXPIRED, SUPERSEDED}
_VALID_DECISIONS = {APPROVED, REJECTED}


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def canonical_json(value: Any) -> str:
    """Return deterministic JSON suitable for hashes and release identifiers."""
    return json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sha256(value: Any) -> str:
    """SHA-256 of the canonical JSON representation of ``value``."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReviewVote:
    reviewer: str
    decision: str
    comment: str
    identity_provider: str
    created_at: float


@dataclass(frozen=True)
class ReviewComment:
    reviewer: str
    comment: str
    identity_provider: str
    created_at: float


@dataclass
class ReleaseReview:
    """Current state of one content-bound release review."""

    release_key: str
    subject: str
    artifact_digest: str
    required_reviewers: List[str]
    quorum: int
    requested_by: str = ""
    rationale: str = ""
    status: str = PENDING
    expires_at: Optional[float] = None
    id: str = field(default_factory=lambda: new_id("review"))
    created_at: float = field(default_factory=time.time)
    decided_at: Optional[float] = None
    superseded_by: str = ""
    votes: List[ReviewVote] = field(default_factory=list)
    comments: List[ReviewComment] = field(default_factory=list)

    @property
    def pending(self) -> bool:
        return self.status == PENDING

    @property
    def approved(self) -> bool:
        return self.status == APPROVED

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL

    @property
    def approvals(self) -> List[str]:
        return [vote.reviewer for vote in self.votes if vote.decision == APPROVED]

    @property
    def rejections(self) -> List[str]:
        return [vote.reviewer for vote in self.votes if vote.decision == REJECTED]

    @property
    def votes_remaining(self) -> int:
        return max(0, self.quorum - len(self.approvals))

    def valid_for(self, artifact_digest: str) -> bool:
        """Whether this decision authorizes exactly ``artifact_digest``."""
        return self.approved and self.artifact_digest == artifact_digest

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "release_key": self.release_key,
            "subject": self.subject,
            "artifact_digest": self.artifact_digest,
            "required_reviewers": list(self.required_reviewers),
            "quorum": self.quorum,
            "requested_by": self.requested_by,
            "rationale": self.rationale,
            "status": self.status,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "superseded_by": self.superseded_by,
            "votes": [asdict(vote) for vote in self.votes],
            "comments": [asdict(comment) for comment in self.comments],
            "votes_remaining": self.votes_remaining,
        }


class ReleaseReviewStore:
    """SQLite-backed release-review lifecycle with named, atomic voting."""

    def __init__(self, db_path: Any = "./.autarch/reviews.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        configure_sqlite(self._conn)
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS release_reviews (
                id TEXT PRIMARY KEY,
                release_key TEXT NOT NULL,
                subject TEXT NOT NULL,
                artifact_digest TEXT NOT NULL,
                required_reviewers TEXT NOT NULL,
                quorum INTEGER NOT NULL,
                requested_by TEXT NOT NULL,
                rationale TEXT NOT NULL,
                status TEXT NOT NULL,
                expires_at REAL,
                created_at REAL NOT NULL,
                decided_at REAL,
                superseded_by TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_release_reviews_key
                ON release_reviews(release_key, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_release_reviews_status
                ON release_reviews(status, created_at DESC);

            CREATE TABLE IF NOT EXISTS review_votes (
                request_id TEXT NOT NULL,
                reviewer TEXT NOT NULL,
                decision TEXT NOT NULL,
                comment TEXT NOT NULL,
                identity_provider TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (request_id, reviewer),
                FOREIGN KEY (request_id) REFERENCES release_reviews(id)
            );

            CREATE TABLE IF NOT EXISTS review_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                reviewer TEXT NOT NULL,
                comment TEXT NOT NULL,
                identity_provider TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY (request_id) REFERENCES release_reviews(id)
            );

            CREATE TABLE IF NOT EXISTS review_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL UNIQUE
            );
            """
        )

    @contextmanager
    def _write(self) -> Iterator[None]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    @staticmethod
    def _validate_digest(digest: str) -> str:
        normalized = str(digest).lower().strip()
        if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
            raise ValueError("artifact_digest must be a 64-character SHA-256 hex digest")
        return normalized

    @staticmethod
    def _reviewers(reviewers: Sequence[str]) -> List[str]:
        unique: List[str] = []
        for reviewer in reviewers:
            value = str(reviewer).strip()
            if value and value not in unique:
                unique.append(value)
        if not unique:
            raise ValueError("at least one named reviewer is required")
        return unique

    def submit(
        self,
        release_key: str,
        subject: str,
        artifact_digest: str,
        required_reviewers: Sequence[str],
        *,
        quorum: Optional[int] = None,
        requested_by: str = "",
        rationale: str = "",
        ttl_seconds: Optional[float] = 30 * 24 * 60 * 60,
    ) -> ReleaseReview:
        """Create or idempotently recover a review for exact package content.

        A changed digest supersedes every pending or approved decision for the
        same ``release_key``.  An unchanged pending, approved, or rejected request
        is returned unchanged, preventing duplicate review queues on reruns.
        """
        key = str(release_key).strip()
        title = str(subject).strip()
        if not key or not title:
            raise ValueError("release_key and subject are required")
        digest = self._validate_digest(artifact_digest)
        reviewers = self._reviewers(required_reviewers)
        required = int(quorum if quorum is not None else len(reviewers))
        if required < 1 or required > len(reviewers):
            raise ValueError("quorum must be between one and the named reviewer count")
        ttl = None if ttl_seconds is None else float(ttl_seconds)
        if ttl is not None and (not math.isfinite(ttl) or ttl <= 0):
            raise ValueError("ttl_seconds must be positive and finite, or None")

        now = time.time()
        created_id = new_id("review")
        with self._write():
            latest = self._conn.execute(
                "SELECT * FROM release_reviews WHERE release_key=? ORDER BY created_at DESC LIMIT 1",
                (key,),
            ).fetchone()
            if latest is not None:
                self._expire_row_locked(latest, now)
                latest = self._conn.execute(
                    "SELECT * FROM release_reviews WHERE id=?", (latest["id"],)
                ).fetchone()
                same_review_contract = (
                    latest["subject"] == title
                    and list(json.loads(latest["required_reviewers"])) == reviewers
                    and int(latest["quorum"]) == required
                    and latest["requested_by"] == str(requested_by)
                    and latest["rationale"] == str(rationale)
                )
                if (
                    latest["artifact_digest"] == digest
                    and same_review_contract
                    and latest["status"] in {PENDING, APPROVED, REJECTED}
                ):
                    return self._inflate_locked(latest)

            active = self._conn.execute(
                "SELECT * FROM release_reviews WHERE release_key=? AND status IN (?,?)",
                (key, PENDING, APPROVED),
            ).fetchall()
            for row in active:
                self._conn.execute(
                    "UPDATE release_reviews SET status=?, decided_at=?, superseded_by=? WHERE id=?",
                    (SUPERSEDED, now, created_id, row["id"]),
                )
                self._append_event_locked(
                    row["id"],
                    "superseded",
                    {"replacement_id": created_id, "replacement_digest": digest},
                    now,
                )

            expires_at = now + ttl if ttl is not None else None
            self._conn.execute(
                """INSERT INTO release_reviews
                   (id,release_key,subject,artifact_digest,required_reviewers,quorum,
                    requested_by,rationale,status,expires_at,created_at,decided_at,superseded_by)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    created_id, key, title, digest, canonical_json(reviewers), required,
                    str(requested_by), str(rationale), PENDING, expires_at, now, None, "",
                ),
            )
            self._append_event_locked(
                created_id,
                "submitted",
                {
                    "release_key": key,
                    "artifact_digest": digest,
                    "required_reviewers": reviewers,
                    "quorum": required,
                    "expires_at": expires_at,
                },
                now,
            )
            row = self._conn.execute(
                "SELECT * FROM release_reviews WHERE id=?", (created_id,)
            ).fetchone()
            return self._inflate_locked(row)

    def approve(
        self,
        review_id: str,
        reviewer: str,
        *,
        comment: str = "",
        identity_provider: str = "asserted",
    ) -> ReleaseReview:
        return self._vote(review_id, reviewer, APPROVED, comment, identity_provider)

    def reject(
        self,
        review_id: str,
        reviewer: str,
        *,
        comment: str,
        identity_provider: str = "asserted",
    ) -> ReleaseReview:
        if not str(comment).strip():
            raise ValueError("a rejection comment is required")
        return self._vote(review_id, reviewer, REJECTED, comment, identity_provider)

    def _vote(
        self,
        review_id: str,
        reviewer: str,
        decision: str,
        comment: str,
        identity_provider: str,
    ) -> ReleaseReview:
        if decision not in _VALID_DECISIONS:
            raise ValueError(f"unsupported review decision: {decision}")
        voter = str(reviewer).strip()
        provider = str(identity_provider).strip() or "asserted"
        if not voter:
            raise ValueError("reviewer is required")
        now = time.time()
        with self._write():
            row = self._require_row_locked(review_id)
            self._expire_row_locked(row, now)
            row = self._require_row_locked(review_id)
            if row["status"] != PENDING:
                return self._inflate_locked(row)
            required_reviewers = json.loads(row["required_reviewers"])
            if voter not in required_reviewers:
                raise PermissionError(
                    f"reviewer '{voter}' is not authorized for review '{review_id}'"
                )
            existing = self._conn.execute(
                "SELECT decision FROM review_votes WHERE request_id=? AND reviewer=?",
                (review_id, voter),
            ).fetchone()
            if existing is not None:
                if existing["decision"] != decision:
                    raise ValueError("a reviewer cannot change an already-recorded decision")
                return self._inflate_locked(row)

            self._conn.execute(
                """INSERT INTO review_votes
                   (request_id,reviewer,decision,comment,identity_provider,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (review_id, voter, decision, str(comment), provider, now),
            )
            self._append_event_locked(
                review_id,
                "vote_recorded",
                {
                    "reviewer": voter,
                    "decision": decision,
                    "comment": str(comment),
                    "identity_provider": provider,
                },
                now,
            )
            if decision == REJECTED:
                status = REJECTED
            else:
                approvals = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM review_votes WHERE request_id=? AND decision=?",
                    (review_id, APPROVED),
                ).fetchone()["n"]
                status = APPROVED if int(approvals) >= int(row["quorum"]) else PENDING
            if status != PENDING:
                self._conn.execute(
                    "UPDATE release_reviews SET status=?, decided_at=? WHERE id=?",
                    (status, now, review_id),
                )
                self._append_event_locked(
                    review_id, "review_decided", {"status": status}, now
                )
            return self._inflate_locked(self._require_row_locked(review_id))

    def add_comment(
        self,
        review_id: str,
        reviewer: str,
        comment: str,
        *,
        identity_provider: str = "asserted",
    ) -> ReleaseReview:
        voter = str(reviewer).strip()
        text = str(comment).strip()
        provider = str(identity_provider).strip() or "asserted"
        if not voter or not text:
            raise ValueError("reviewer and comment are required")
        now = time.time()
        with self._write():
            row = self._require_row_locked(review_id)
            required_reviewers = json.loads(row["required_reviewers"])
            if voter not in required_reviewers:
                raise PermissionError(
                    f"reviewer '{voter}' is not authorized for review '{review_id}'"
                )
            self._conn.execute(
                """INSERT INTO review_comments
                   (request_id,reviewer,comment,identity_provider,created_at)
                   VALUES (?,?,?,?,?)""",
                (review_id, voter, text, provider, now),
            )
            self._append_event_locked(
                review_id,
                "comment_added",
                {"reviewer": voter, "comment": text, "identity_provider": provider},
                now,
            )
            return self._inflate_locked(row)

    def supersede(
        self,
        review_id: str,
        *,
        by: str,
        reason: str,
        replacement_id: str = "",
    ) -> ReleaseReview:
        actor = str(by).strip()
        explanation = str(reason).strip()
        if not actor or not explanation:
            raise ValueError("by and reason are required")
        now = time.time()
        with self._write():
            row = self._require_row_locked(review_id)
            if row["status"] in {SUPERSEDED, EXPIRED}:
                return self._inflate_locked(row)
            self._conn.execute(
                "UPDATE release_reviews SET status=?, decided_at=?, superseded_by=? WHERE id=?",
                (SUPERSEDED, now, str(replacement_id), review_id),
            )
            self._append_event_locked(
                review_id,
                "superseded",
                {"by": actor, "reason": explanation, "replacement_id": replacement_id},
                now,
            )
            return self._inflate_locked(self._require_row_locked(review_id))

    def get(self, review_id: str) -> Optional[ReleaseReview]:
        self._refresh_expiry(review_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM release_reviews WHERE id=?", (review_id,)
            ).fetchone()
            return self._inflate_locked(row) if row is not None else None

    def latest(self, release_key: str) -> Optional[ReleaseReview]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM release_reviews WHERE release_key=? ORDER BY created_at DESC LIMIT 1",
                (release_key,),
            ).fetchone()
        return self.get(row["id"]) if row is not None else None

    def list(self, *, status: Optional[str] = None) -> List[ReleaseReview]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM release_reviews"
                + (" WHERE status=?" if status else "")
                + " ORDER BY created_at DESC",
                ((status,) if status else ()),
            ).fetchall()
        return [review for row in rows if (review := self.get(row["id"])) is not None]

    def require_valid(self, review_id: str, artifact_digest: str) -> ReleaseReview:
        """Return an approval only when it matches exact content; otherwise fail closed."""
        review = self.get(review_id)
        if review is None:
            raise KeyError(f"no review '{review_id}'")
        digest = self._validate_digest(artifact_digest)
        if not review.valid_for(digest):
            raise PermissionError(
                f"review '{review_id}' is {review.status} or does not match artifact digest"
            )
        return review

    def events(self, review_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM review_events"
                + (" WHERE request_id=?" if review_id else "")
                + " ORDER BY sequence",
                ((review_id,) if review_id else ()),
            ).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "request_id": row["request_id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
                "previous_hash": row["previous_hash"],
                "event_hash": row["event_hash"],
            }
            for row in rows
        ]

    def verify_chain(self) -> Tuple[bool, Optional[int]]:
        """Verify the complete review-event hash chain."""
        previous = ""
        for event in self.events():
            if event["previous_hash"] != previous:
                return False, int(event["sequence"])
            body = {
                "request_id": event["request_id"],
                "event_type": event["event_type"],
                "payload": event["payload"],
                "created_at": event["created_at"],
                "previous_hash": event["previous_hash"],
            }
            expected = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
            if expected != event["event_hash"]:
                return False, int(event["sequence"])
            previous = event["event_hash"]
        return True, None

    def export(self, destination: Any) -> Path:
        """Export requests and the verifiable event stream as deterministic JSON."""
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        verified, broken = self.verify_chain()
        payload = {
            "schema_version": "autarch.release-review.v1",
            "exported_at": time.time(),
            "chain": {"verified": verified, "broken_sequence": broken},
            "reviews": [review.as_dict() for review in self.list()],
            "events": self.events(),
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def _refresh_expiry(self, review_id: str) -> None:
        now = time.time()
        with self._write():
            row = self._conn.execute(
                "SELECT * FROM release_reviews WHERE id=?", (review_id,)
            ).fetchone()
            if row is not None:
                self._expire_row_locked(row, now)

    def _expire_row_locked(self, row: sqlite3.Row, now: float) -> None:
        expires_at = row["expires_at"]
        if row["status"] == PENDING and expires_at is not None and now >= float(expires_at):
            self._conn.execute(
                "UPDATE release_reviews SET status=?, decided_at=? WHERE id=?",
                (EXPIRED, now, row["id"]),
            )
            self._append_event_locked(row["id"], "expired", {}, now)

    def _require_row_locked(self, review_id: str) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM release_reviews WHERE id=?", (review_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no review '{review_id}'")
        return row

    def _inflate_locked(self, row: sqlite3.Row) -> ReleaseReview:
        votes = self._conn.execute(
            "SELECT * FROM review_votes WHERE request_id=? ORDER BY created_at, reviewer",
            (row["id"],),
        ).fetchall()
        comments = self._conn.execute(
            "SELECT * FROM review_comments WHERE request_id=? ORDER BY created_at, id",
            (row["id"],),
        ).fetchall()
        return ReleaseReview(
            id=row["id"],
            release_key=row["release_key"],
            subject=row["subject"],
            artifact_digest=row["artifact_digest"],
            required_reviewers=list(json.loads(row["required_reviewers"])),
            quorum=int(row["quorum"]),
            requested_by=row["requested_by"],
            rationale=row["rationale"],
            status=row["status"],
            expires_at=row["expires_at"],
            created_at=float(row["created_at"]),
            decided_at=row["decided_at"],
            superseded_by=row["superseded_by"],
            votes=[
                ReviewVote(
                    reviewer=vote["reviewer"],
                    decision=vote["decision"],
                    comment=vote["comment"],
                    identity_provider=vote["identity_provider"],
                    created_at=float(vote["created_at"]),
                )
                for vote in votes
            ],
            comments=[
                ReviewComment(
                    reviewer=comment["reviewer"],
                    comment=comment["comment"],
                    identity_provider=comment["identity_provider"],
                    created_at=float(comment["created_at"]),
                )
                for comment in comments
            ],
        )

    def _append_event_locked(
        self,
        request_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        created_at: float,
    ) -> None:
        previous_row = self._conn.execute(
            "SELECT event_hash FROM review_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous_row["event_hash"] if previous_row else ""
        body = {
            "request_id": request_id,
            "event_type": event_type,
            "payload": dict(payload),
            "created_at": created_at,
            "previous_hash": previous_hash,
        }
        event_hash = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
        self._conn.execute(
            """INSERT INTO review_events
               (request_id,event_type,payload,created_at,previous_hash,event_hash)
               VALUES (?,?,?,?,?,?)""",
            (
                request_id,
                event_type,
                canonical_json(dict(payload)),
                created_at,
                previous_hash,
                event_hash,
            ),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = [
    "APPROVED",
    "EXPIRED",
    "PENDING",
    "REJECTED",
    "SUPERSEDED",
    "ReleaseReview",
    "ReleaseReviewStore",
    "ReviewComment",
    "ReviewVote",
    "canonical_json",
    "canonical_sha256",
]
