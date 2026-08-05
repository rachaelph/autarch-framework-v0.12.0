"""Formal policy guarantees — prove a safety invariant BEFORE an agent runs.

The capability kernel and policy engine are *deterministic*. That means we can
reason about them statically: given a set of grants and policies, we can **prove**
whether a safety invariant holds for *every* possible action — without executing
anything, and without trusting the model.

Three invariants are supported, each over a concrete capability:

  * ``forbid(C)``          — C can never execute, even with human approval.
  * ``require_approval(C)``— C can never *auto*-execute; it always needs an
                             explicit human ratification (the basis of a
                             two-person rule).
  * ``confine(C, prefix)`` — every execution of C acts within ``prefix``.

The analysis is **adversarial**: it assumes the AI/council may propose the worst
allowed action. A proof therefore holds regardless of what any model does.

Honest boundary (soundness, not omniscience):
  * This is a *sound static proof over the deterministic decision logic*
    (grants, grant scopes, and **unconditional** policy effects) — not full
    theorem-proving over arbitrary code.
  * Policies with a runtime predicate (``when`` is not None) are treated
    **conservatively**: they are *not* relied upon for a guarantee, because we
    cannot statically prove the predicate always fires. So a "GUARANTEED" result
    is always sound (never a false guarantee), though the checker may be
    conservative and report "NOT GUARANTEED" where a clever predicate would in
    fact block the action.
  * Because attenuation only ever *narrows* authority, any invariant proven for a
    parent's grants also holds for every sub-agent it spawns (monotonicity).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .contracts import CapabilityGrant
from .delegation import _path_within
from .policy import Policy, PolicyEffect, _capability_matches

FORBID = "forbid"
REQUIRE_APPROVAL = "require_approval"
CONFINE = "confine"


@dataclass
class Invariant:
    """A safety property to prove over a configuration of grants + policies."""

    kind: str
    capability: str
    path_prefix: Optional[str] = None
    description: str = ""

    @classmethod
    def forbid(cls, capability: str, description: str = "") -> "Invariant":
        return cls(FORBID, capability, description=description)

    @classmethod
    def require_approval(cls, capability: str, description: str = "") -> "Invariant":
        return cls(REQUIRE_APPROVAL, capability, description=description)

    @classmethod
    def confine(cls, capability: str, path_prefix: str, description: str = "") -> "Invariant":
        return cls(CONFINE, capability, path_prefix=path_prefix, description=description)

    def label(self) -> str:
        if self.kind == FORBID:
            return f"forbid '{self.capability}'"
        if self.kind == REQUIRE_APPROVAL:
            return f"require approval for '{self.capability}'"
        if self.kind == CONFINE:
            return f"confine '{self.capability}' to '{self.path_prefix}'"
        return f"{self.kind} '{self.capability}'"


@dataclass
class Proof:
    """The result of statically checking one invariant."""

    invariant: Invariant
    holds: bool
    reason: str
    counterexample: str = ""

    @property
    def guaranteed(self) -> bool:
        return self.holds


@dataclass
class GuaranteeReport:
    proofs: List[Proof] = field(default_factory=list)

    @property
    def all_hold(self) -> bool:
        return all(p.holds for p in self.proofs)

    def failures(self) -> List[Proof]:
        return [p for p in self.proofs if not p.holds]


# --------------------------------------------------------------------------- #
# Static analysis
# --------------------------------------------------------------------------- #
def _matching_grants(grants: List[CapabilityGrant], capability: str) -> List[CapabilityGrant]:
    """Grants whose name authorizes `capability` (wildcards included)."""
    return [g for g in grants if g.matches(capability)]


def _unconditional_block(policies: List[Policy], capability: str, effects: set) -> Optional[Policy]:
    """An *unconditional* policy (no `when`) covering `capability` with a blocking effect.

    Conditional policies are deliberately ignored — we cannot prove their
    predicate always fires, so they may not be relied on for a guarantee.
    """
    for policy in policies:
        if policy.when is None and policy.effect in effects and _capability_matches(policy.capability, capability):
            return policy
    return None


def _verify(inv: Invariant, grants: List[CapabilityGrant], policies: List[Policy]) -> Proof:
    matching = _matching_grants(grants, inv.capability)

    if inv.kind == FORBID:
        if not matching:
            return Proof(inv, True, f"no grant authorizes '{inv.capability}' (deny by default)")
        blocker = _unconditional_block(policies, inv.capability, {PolicyEffect.DENY.value})
        if blocker is not None:
            return Proof(inv, True, f"policy '{blocker.name}' unconditionally denies '{inv.capability}'")
        return Proof(
            inv, False,
            f"grant '{matching[0].name}' can authorize '{inv.capability}' with no unconditional deny",
            counterexample=matching[0].name,
        )

    if inv.kind == REQUIRE_APPROVAL:
        if not matching:
            return Proof(inv, True, f"'{inv.capability}' cannot execute at all (no grant), so never without approval")
        blocker = _unconditional_block(
            policies, inv.capability, {PolicyEffect.DENY.value, PolicyEffect.REQUIRE_RATIFY.value}
        )
        if blocker is not None:
            return Proof(inv, True, f"policy '{blocker.name}' forces approval (or denial) of '{inv.capability}'")
        return Proof(
            inv, False,
            f"'{inv.capability}' can auto-execute via grant '{matching[0].name}' with no approval policy",
            counterexample=matching[0].name,
        )

    if inv.kind == CONFINE:
        if not matching:
            return Proof(inv, True, f"'{inv.capability}' cannot execute at all (no grant)")
        if _unconditional_block(policies, inv.capability, {PolicyEffect.DENY.value}) is not None:
            return Proof(inv, True, f"'{inv.capability}' is unconditionally denied, so it never acts at all")
        for grant in matching:
            prefix = grant.scope.get("path_prefix")
            if prefix is None:
                return Proof(
                    inv, False,
                    f"grant '{grant.name}' has no path scope; '{inv.capability}' could act outside '{inv.path_prefix}'",
                    counterexample=grant.name,
                )
            if not _path_within(inv.path_prefix or "", prefix):
                return Proof(
                    inv, False,
                    f"grant '{grant.name}' scope '{prefix}' is outside '{inv.path_prefix}'",
                    counterexample=grant.name,
                )
        return Proof(inv, True, f"every grant for '{inv.capability}' is confined within '{inv.path_prefix}'")

    return Proof(inv, False, f"unknown invariant kind '{inv.kind}'")


def prove_guarantees(
    invariants: List[Invariant],
    grants: List[CapabilityGrant],
    policies: Optional[List[Policy]] = None,
) -> GuaranteeReport:
    """Statically prove each invariant against a grant + policy configuration."""
    pols = list(policies or [])
    return GuaranteeReport([_verify(inv, list(grants), pols) for inv in invariants])
