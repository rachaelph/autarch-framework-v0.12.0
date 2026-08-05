"""The Economic Kernel — budgets every execution carries, enforced before it runs.

Capability security answers "is this *allowed*?". Economics answers "can we
*afford* it?". An agent (or a whole tree of sub-agents) runs under a **Budget**:
ceilings on cost, model calls, latency, risk, carbon — whatever you choose to
meter. Before an action executes, the economic kernel checks whether charging its
estimated cost would bust any ceiling; if so, the action is refused. This is what
lets agents run at scale without runaway spend or risk.

Like the capability kernel, this is **deterministic and pre-execution** — not a
post-hoc bill. The cost of an action is estimated by a `CostModel`; on successful
execution the budget is charged.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, Optional

from .contracts import Action

# Default per-capability cost estimates. Keys are arbitrary meters; an agent may
# override or extend these. 'calls' meters model/tool invocations; 'risk' is a
# coarse 0-10 hazard weight; 'cost' is currency units.
_DEFAULT_COSTS: Dict[str, Dict[str, float]] = {
    "file.read": {"calls": 1, "risk": 0, "cost": 0.0},
    "file.write": {"calls": 1, "risk": 1, "cost": 0.0},
    "file.move": {"calls": 1, "risk": 2, "cost": 0.0},
    "file.delete": {"calls": 1, "risk": 5, "cost": 0.0},
}
# Fallback for capabilities not explicitly priced.
_FALLBACK_COST = {"calls": 1, "risk": 3, "cost": 0.0}


class CostModel:
    """Maps an action to an estimated cost vector (deterministic)."""

    def __init__(self, prices: Optional[Dict[str, Dict[str, float]]] = None):
        self._prices = dict(_DEFAULT_COSTS)
        if prices:
            for cap, vec in prices.items():
                self._prices[cap] = dict(vec)

    def estimate(self, action: Action) -> Dict[str, float]:
        cap = action.capability
        if cap in self._prices:
            return dict(self._prices[cap])
        # Wildcard family pricing, e.g. a custom 'payment.*' price.
        for pattern, vec in self._prices.items():
            if pattern.endswith(".*") and cap.startswith(pattern[:-1]):
                return dict(vec)
        return dict(_FALLBACK_COST)


@dataclass
class BudgetDecision:
    """The economic kernel's verdict on whether an action is affordable."""

    ok: bool
    reason: str
    offending_key: Optional[str] = None
    estimate: Dict[str, float] = field(default_factory=dict)


@dataclass
class Budget:
    """Ceilings for a run (or a tree of sub-agents), plus what's been spent."""

    limits: Dict[str, float] = field(default_factory=dict)
    spent: Dict[str, float] = field(default_factory=dict)
    label: str = "budget"

    def __post_init__(self) -> None:
        # A lock so a budget shared across parallel sub-agents stays consistent.
        # Kept off the dataclass fields, so equality/serialization are unaffected.
        self._lock = threading.Lock()

    def remaining(self, key: str) -> Optional[float]:
        if key not in self.limits:
            return None  # unmetered
        return self.limits[key] - self.spent.get(key, 0.0)

    def would_exceed(self, deltas: Dict[str, float]) -> Optional[tuple]:
        """Return (key, limit, projected) for the first ceiling a charge busts."""
        with self._lock:
            for key, amount in deltas.items():
                if key not in self.limits:
                    continue
                projected = self.spent.get(key, 0.0) + amount
                if projected > self.limits[key]:
                    return key, self.limits[key], projected
            return None

    def charge(self, deltas: Dict[str, float]) -> None:
        with self._lock:
            for key, amount in deltas.items():
                self.spent[key] = self.spent.get(key, 0.0) + amount

    def snapshot(self) -> Dict[str, str]:
        """Human-readable spent/limit per metered key."""
        return {k: f"{self.spent.get(k, 0.0):g}/{v:g}" for k, v in self.limits.items()}


class EconomicKernel:
    """Refuses actions whose estimated cost would bust the budget."""

    def __init__(self, budget: Budget, cost_model: Optional[CostModel] = None):
        self.budget = budget
        self.cost_model = cost_model or CostModel()

    def authorize(self, action: Action) -> BudgetDecision:
        estimate = self.cost_model.estimate(action)
        breach = self.budget.would_exceed(estimate)
        if breach is not None:
            key, limit, projected = breach
            return BudgetDecision(
                False,
                f"budget '{key}' would be exceeded: {projected:g} > {limit:g}",
                offending_key=key,
                estimate=estimate,
            )
        return BudgetDecision(True, "within budget", estimate=estimate)

    def charge(self, estimate: Dict[str, float]) -> None:
        self.budget.charge(estimate)
