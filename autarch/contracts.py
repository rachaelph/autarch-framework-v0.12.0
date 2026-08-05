"""Core contracts — the typed vocabulary of Autarch.

These are the stable interfaces the whole system is built around. Models, tools,
and substrates are pluggable; these contracts are the part we own.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


def new_id(prefix: str) -> str:
    """A short, readable, unique id (e.g. 'act_9f2c1a...')."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class Intent:
    """What the user wants. The entry point to every deliberation."""

    text: str
    id: str = field(default_factory=lambda: new_id("intent"))
    created_at: float = field(default_factory=time.time)


@dataclass
class CapabilityGrant:
    """A typed, scoped permission.

    Deny-by-default: only capabilities explicitly granted may be exercised.
    `scope` constrains *where* (e.g. a path prefix); `limits` constrains *how much*
    (e.g. max bytes). `depth`/`delegated_from` track delegation: a grant handed to
    a sub-agent must be a *subset* of its parent (see `delegation.attenuate_grant`).
    """

    name: str
    scope: dict = field(default_factory=dict)
    limits: dict = field(default_factory=dict)
    depth: int = 0
    delegated_from: str = ""

    def matches(self, capability_name: str) -> bool:
        """Exact match, or a trailing wildcard like 'file.*'."""
        if self.name == capability_name:
            return True
        if self.name.endswith(".*"):
            return capability_name.startswith(self.name[:-1])
        return False

    def covers(self, capability_name: str) -> bool:
        """Whether this grant's name authorizes `capability_name` (alias of matches)."""
        return self.matches(capability_name)


@dataclass
class Action:
    """A concrete effect a councilor proposes to perform on the world."""

    capability: str
    params: dict = field(default_factory=dict)
    rationale: str = ""
    id: str = field(default_factory=lambda: new_id("act"))


class Verdict(str, Enum):
    PROPOSE = "propose"
    APPROVE = "approve"
    VETO = "veto"
    REVISE = "revise"


class HumanDecision(str, Enum):
    RATIFY = "ratify"
    OVERRULE = "overrule"
    SEND_BACK = "send_back"
    AUTO = "auto"
    PENDING = "pending"


@dataclass
class GateResult:
    """The deterministic kernel's verdict on whether an action may proceed."""

    allowed: bool
    reason: str
    grant: Optional[CapabilityGrant] = None


@dataclass
class ActionResult:
    """The outcome of executing an action through an adapter."""

    ok: bool
    output: Any = None
    undo: Optional[dict] = None
    error: Optional[str] = None


@dataclass
class WhyRecord:
    """The full justification for one action — the heart of provable trust.

    Everything needed to answer 'why did you do that?' is captured here and
    persisted, so any past action can be explained, audited, or reversed.
    """

    intent_text: str
    capability: str
    params: dict
    rationale: str
    proposer: str
    challenger: str
    critique_verdict: str
    critique_reasons: str
    gate_allowed: bool
    gate_reason: str
    human_decision: str
    executed: bool
    result_ok: Optional[bool]
    result_output: Any
    result_error: Optional[str]
    undo: Optional[dict]
    # --- Phase 2: council plurality, policy & precedent (all defaulted so
    #     records written by earlier versions still deserialize cleanly) ---
    recommendation: str = ""
    voices: list = field(default_factory=list)
    tally: dict = field(default_factory=dict)
    proposal_disagreement: bool = False
    rounds: int = 1
    precedent_note: str = ""
    policy_note: str = ""
    rewind_of: str = ""  # id of the action this record reverses, if any
    cost: dict = field(default_factory=dict)  # estimated economic cost of the action
    # --- Phase E: governed evaluation (quality verdict on the action's output) ---
    eval_score: Optional[float] = None
    eval_passed: Optional[bool] = None
    eval_reasons: str = ""
    evaluator: str = ""
    # --- Provenance: cryptographic authorship (kept OUT of the hashed payload;
    #     populated from dedicated columns on read) ---
    signer: str = ""       # node id of the signer (bound to signer_key)
    signer_key: str = ""   # signer's Ed25519 public key, hex
    signature: str = ""    # Ed25519 signature over the record's seal, hex
    id: str = field(default_factory=lambda: new_id("why"))
    created_at: float = field(default_factory=time.time)
