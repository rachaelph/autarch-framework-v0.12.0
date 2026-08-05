"""The Capability Kernel — the deterministic gate at the heart of Autarch.

AI proposes; the kernel disposes. This module contains NO intelligence. It is
pure, deterministic policy: given an action and a set of grants, decide whether
the action is allowed. Deny by default.
"""
from __future__ import annotations

from . import scoping
from .contracts import Action, CapabilityGrant, GateResult


class CapabilityKernel:
    """Mediates every action against explicitly granted capabilities."""

    def __init__(self, grants):
        self._grants = list(grants)

    @property
    def grants(self):
        return list(self._grants)

    def authorize(self, action: Action) -> GateResult:
        """Return a deterministic verdict on whether `action` may proceed."""
        for grant in self._grants:
            if grant.matches(action.capability):
                ok, reason = self._check_constraints(grant, action)
                if ok:
                    return GateResult(True, reason or f"granted by '{grant.name}'", grant)
                return GateResult(False, reason, grant)
        return GateResult(
            False,
            f"no grant for capability '{action.capability}' (deny by default)",
        )

    @staticmethod
    def _check_constraints(grant: CapabilityGrant, action: Action):
        """Enforce scope and limits via the scope algebra (deterministic).

        Defense-in-depth: adapters re-check too. The full set of recognized
        constraints (path confinement, host/port allowlists, enums, regex,
        forbidden substrings/data-classes, and numeric ceilings) lives in
        ``autarch.scoping`` so the kernel, delegation, and the guarantee prover
        all share one source of truth.
        """
        return scoping.evaluate(grant.scope, grant.limits, action.params)
