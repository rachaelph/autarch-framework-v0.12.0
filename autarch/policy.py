"""Policy-as-code — declarative rules the kernel consults, deterministically.

The capability kernel decides *whether a capability was granted*. Policies add a
second, declarative layer: even granted actions may be denied or escalated to
require explicit human ratification ("never delete outside the sandbox", "large
writes need a human", "spend over $X must be ratified").

Effects, strictest first:
    deny            -> the action may never execute (like a kernel denial)
    require_ratify  -> auto-presiding may not ratify; a human must explicitly say yes
    allow           -> no objection from this policy
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional

from .contracts import Action


class PolicyEffect(str, Enum):
    ALLOW = "allow"
    REQUIRE_RATIFY = "require_ratify"
    DENY = "deny"


# Strictness ordering for combining multiple matching policies.
_STRICTNESS = {PolicyEffect.ALLOW.value: 0, PolicyEffect.REQUIRE_RATIFY.value: 1, PolicyEffect.DENY.value: 2}


def _capability_matches(pattern: str, capability: str) -> bool:
    if pattern == "*" or pattern == capability:
        return True
    if pattern.endswith(".*"):
        return capability.startswith(pattern[:-1])
    return False


@dataclass
class Policy:
    """A single declarative rule.

    `capability` is an exact name, a wildcard like 'file.*', or '*'.
    `when` is an optional predicate over the action params; if omitted the policy
    matches whenever the capability matches.
    """

    name: str
    effect: str
    capability: str = "*"
    when: Optional[Callable[[dict], bool]] = None
    reason: str = ""

    def matches(self, action: Action) -> bool:
        if not _capability_matches(self.capability, action.capability):
            return False
        if self.when is None:
            return True
        try:
            return bool(self.when(action.params))
        except Exception:
            # A faulty predicate must never crash the kernel; treat as no match.
            return False


@dataclass
class PolicyDecision:
    """The combined verdict of all policies for one action."""

    effect: str = PolicyEffect.ALLOW.value
    reasons: List[str] = field(default_factory=list)
    applied: List[str] = field(default_factory=list)

    @property
    def denies(self) -> bool:
        return self.effect == PolicyEffect.DENY.value

    @property
    def requires_ratify(self) -> bool:
        return self.effect == PolicyEffect.REQUIRE_RATIFY.value

    def note(self) -> str:
        if self.effect == PolicyEffect.ALLOW.value:
            return ""
        return f"{self.effect}: {'; '.join(self.reasons)}"


class PolicyEngine:
    def __init__(self, policies: Optional[List[Policy]] = None):
        self.policies = list(policies or [])

    def evaluate(self, action: Action) -> PolicyDecision:
        decision = PolicyDecision()
        for policy in self.policies:
            if not policy.matches(action):
                continue
            decision.applied.append(policy.name)
            if _STRICTNESS[policy.effect] > _STRICTNESS[decision.effect]:
                decision.effect = policy.effect
            if policy.reason:
                decision.reasons.append(policy.reason)
        return decision
