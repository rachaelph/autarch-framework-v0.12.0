"""Kernel verification — exhaustively check the kernel's safety invariants.

The capability kernel is tiny and deterministic, which means its guarantees can be
*checked by exhaustion* rather than merely spot-tested. This module enumerates a
large bounded space of (grants, action) inputs and asserts the four invariants the
whole security story rests on. It is dependency-free (a small built-in generator,
no ``hypothesis``), so it runs in CI everywhere.

Invariants:
  I1  DENY-BY-DEFAULT   — an action whose capability matches no grant is denied.
  I2  NO-SCOPE-ESCAPE   — a path/host/amount-constrained grant never authorizes an
                          action that violates the constraint.
  I3  ATTENUATION       — a delegated child grant never authorizes an action that
                          its parent grant would itself deny (a child can't out-
                          reach its parent).
  I4  DETERMINISM       — the same (grants, action) always yields the same verdict.

This is a *sound model check over a bounded domain*, not a full theorem-proving
proof (that is what ``docs/kernel.tla`` sketches for a real model checker). A pass
means: across the entire enumerated space, no invariant was violated.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from .contracts import Action, CapabilityGrant
from .delegation import attenuate_grant
from .kernel import CapabilityKernel


@dataclass
class Counterexample:
    invariant: str
    detail: str
    grants: List[Tuple[str, dict, dict]]
    action: Tuple[str, dict]


@dataclass
class VerificationResult:
    checked: int
    invariants: List[str]
    counterexamples: List[Counterexample] = field(default_factory=list)

    @property
    def holds(self) -> bool:
        return not self.counterexamples

    def summary(self) -> str:
        status = "HOLDS" if self.holds else "VIOLATED"
        head = f"kernel invariants {status} over {self.checked} cases"
        if self.holds:
            return head
        c = self.counterexamples[0]
        return f"{head}\n  first counterexample: {c.invariant} — {c.detail}"


# --- bounded input domain ------------------------------------------------------

_CAP_NAMES = ["file.read", "file.write", "file.delete", "net.fetch", "payment.send"]
_GRANT_NAMES = ["file.read", "file.write", "file.*", "net.fetch", "payment.send"]
_PATHS = ["a.txt", "reports/q.txt", "../escape.txt", "reports", "reports/sub/x"]
_PREFIXES = [None, ".", "reports"]
_HOSTS = ["api.github.com", "evil.com"]
_HOST_ALLOW = [None, ["api.github.com"]]
_AMOUNTS = [10, 500]
_AMOUNT_MAX = [None, 100]


def _grants_domain():
    for name in _GRANT_NAMES:
        for prefix in _PREFIXES:
            for host_allow in _HOST_ALLOW:
                for amax in _AMOUNT_MAX:
                    scope = {}
                    if prefix is not None:
                        scope["path_prefix"] = prefix
                    if host_allow is not None:
                        scope["host_allowlist"] = host_allow
                    limits = {}
                    if amax is not None:
                        limits["amount_max"] = amax
                    yield CapabilityGrant(name=name, scope=scope, limits=limits)


def _actions_domain():
    for cap in _CAP_NAMES:
        if cap.startswith("file"):
            for path in _PATHS:
                yield Action(cap, {"path": path, "content": "x"})
        elif cap == "net.fetch":
            for host in _HOSTS:
                yield Action(cap, {"url": f"https://{host}/x"})
        elif cap == "payment.send":
            for amount in _AMOUNTS:
                yield Action(cap, {"amount": amount})


def _violates_scope(grant: CapabilityGrant, action: Action) -> Optional[str]:
    """Ground truth: does this action violate the grant's declared scope?"""
    import os

    prefix = grant.scope.get("path_prefix")
    if prefix is not None and "path" in action.params:
        norm = os.path.normpath(str(action.params["path"]))
        base = os.path.normpath(prefix)
        if os.path.isabs(norm) or norm.startswith(".."):
            return "path escapes sandbox"
        if base not in (".", "") and not (norm == base or norm.startswith(base + os.sep)):
            return "path outside prefix"
    allow = grant.scope.get("host_allowlist")
    if allow and "url" in action.params:
        from urllib.parse import urlparse

        host = urlparse(action.params["url"]).hostname
        if host not in allow:
            return "host not allowed"
    amax = grant.limits.get("amount_max")
    if amax is not None and "amount" in action.params:
        if float(action.params["amount"]) > amax:
            return "amount over ceiling"
    return None


def verify_kernel(max_cases: Optional[int] = None) -> VerificationResult:
    """Enumerate the bounded domain and check every invariant."""
    invariants = ["I1 deny-by-default", "I2 no-scope-escape", "I3 attenuation", "I4 determinism"]
    counters: List[Counterexample] = []
    checked = 0
    grants = list(_grants_domain())
    actions = list(_actions_domain())

    for grant in grants:
        kernel = CapabilityKernel([grant])
        for action in actions:
            checked += 1
            if max_cases and checked > max_cases:
                break
            r1 = kernel.authorize(action)
            r2 = kernel.authorize(action)

            # I4 determinism
            if r1.allowed != r2.allowed:
                counters.append(Counterexample("I4 determinism", "verdict not stable",
                                               [_g(grant)], _a(action)))

            matches = grant.matches(action.capability)
            # I1 deny-by-default
            if not matches and r1.allowed:
                counters.append(Counterexample("I1 deny-by-default",
                                               f"allowed ungranted '{action.capability}'",
                                               [_g(grant)], _a(action)))
            # I2 no-scope-escape
            if matches:
                violation = _violates_scope(grant, action)
                if violation and r1.allowed:
                    counters.append(Counterexample("I2 no-scope-escape",
                                                   f"allowed despite: {violation}",
                                                   [_g(grant)], _a(action)))

    # I3 attenuation: a child never authorizes what its parent denies.
    for parent in grants:
        pk = CapabilityKernel([parent])
        # derive a plausible child (narrow the path or amount) where possible
        child_specs = [
            {"scope": {"path_prefix": "reports"}},
            {"limits": {"amount_max": 50}},
        ]
        for spec in child_specs:
            try:
                child = attenuate_grant(parent, **spec)
            except ValueError:
                continue
            ck = CapabilityKernel([child])
            for action in actions:
                checked += 1
                child_ok = ck.authorize(action).allowed
                parent_ok = pk.authorize(action).allowed
                if child_ok and not parent_ok:
                    counters.append(Counterexample("I3 attenuation",
                                                   "child out-reached parent",
                                                   [_g(parent), _g(child)], _a(action)))

    return VerificationResult(checked=checked, invariants=invariants, counterexamples=counters)


def _g(g: CapabilityGrant):
    return (g.name, dict(g.scope), dict(g.limits))


def _a(a: Action):
    return (a.capability, dict(a.params))
