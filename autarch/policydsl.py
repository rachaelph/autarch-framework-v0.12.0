"""Policy DSL — declarative, reviewable, testable policy-as-data.

The kernel's ``Policy`` takes a Python callable for its condition. That is
powerful but unreviewable: you cannot serialize it, diff it, version it in the
ledger, or let a non-programmer read it. This module adds a small **declarative
condition language** (pure JSON-serializable data) that compiles into ordinary
``Policy`` objects, plus two things every policy system needs and few have:

  * **simulate** — run a policy set over a batch of sample actions and see exactly
    what each would decide (allow / require_ratify / deny), *before* deploying.
  * **diff** — compare two policy sets over the same samples and report only the
    actions whose decision *changes* — a change-review for governance.

Condition grammar (all JSON):
    {"param": "amount", "op": "gt", "value": 1000}
    {"all": [ <cond>, <cond>, ... ]}          # logical AND
    {"any": [ <cond>, ... ]}                    # logical OR
    {"not": <cond>}
Ops: eq ne gt ge lt le in nin contains startswith endswith matches
A missing param makes a leaf condition False (deny-safe: it won't spuriously match).

A policy is:
    {"name": "big-spend", "effect": "require_ratify", "capability": "payment.*",
     "when": <cond|null>, "reason": "..."}
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from .contracts import Action
from .policy import Policy, PolicyEngine

_LEAF_OPS: Dict[str, Callable[[Any, Any], bool]] = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: _num(a) > _num(b),
    "ge": lambda a, b: _num(a) >= _num(b),
    "lt": lambda a, b: _num(a) < _num(b),
    "le": lambda a, b: _num(a) <= _num(b),
    "in": lambda a, b: a in b,
    "nin": lambda a, b: a not in b,
    "contains": lambda a, b: str(b) in str(a),
    "startswith": lambda a, b: str(a).startswith(str(b)),
    "endswith": lambda a, b: str(a).endswith(str(b)),
    "matches": lambda a, b: re.fullmatch(str(b), str(a)) is not None,
}


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def compile_condition(cond: Optional[dict]) -> Callable[[dict], bool]:
    """Compile a declarative condition into a predicate over action params."""
    if cond is None:
        return lambda params: True
    if "all" in cond:
        subs = [compile_condition(c) for c in cond["all"]]
        return lambda params: all(s(params) for s in subs)
    if "any" in cond:
        subs = [compile_condition(c) for c in cond["any"]]
        return lambda params: any(s(params) for s in subs)
    if "not" in cond:
        sub = compile_condition(cond["not"])
        return lambda params: not sub(params)

    param = cond.get("param")
    op = cond.get("op", "eq")
    value = cond.get("value")
    fn = _LEAF_OPS.get(op)
    if fn is None:
        raise ValueError(f"unknown policy op: {op!r}")

    def leaf(params: dict) -> bool:
        if param not in params:
            return False  # deny-safe: missing data never spuriously matches
        try:
            return bool(fn(params[param], value))
        except Exception:
            return False

    return leaf


def compile_policy(spec: dict) -> Policy:
    """Turn one declarative policy dict into a kernel ``Policy``."""
    return Policy(
        name=spec["name"],
        effect=spec["effect"],
        capability=spec.get("capability", "*"),
        when=compile_condition(spec.get("when")),
        reason=spec.get("reason", ""),
    )


def compile_policies(specs: List[dict]) -> List[Policy]:
    return [compile_policy(s) for s in specs]


def simulate(policies: List[Policy], samples: List[Action]) -> List[dict]:
    """Report each policy set's decision for each sample action."""
    engine = PolicyEngine(policies)
    out = []
    for action in samples:
        decision = engine.evaluate(action)
        out.append({
            "capability": action.capability,
            "params": action.params,
            "effect": decision.effect,
            "applied": decision.applied,
            "reasons": decision.reasons,
        })
    return out


def diff(before: List[Policy], after: List[Policy], samples: List[Action]) -> List[dict]:
    """Return only the sample actions whose decision changes from before->after."""
    b = {(_key(a)): d for a, d in zip(samples, simulate(before, samples))}
    a_sim = simulate(after, samples)
    changes = []
    for action, after_d in zip(samples, a_sim):
        before_d = b[_key(action)]
        if before_d["effect"] != after_d["effect"]:
            changes.append({
                "capability": action.capability,
                "params": action.params,
                "before": before_d["effect"],
                "after": after_d["effect"],
                "now_applied": after_d["applied"],
            })
    return changes


def _key(action: Action) -> str:
    import json

    return f"{action.capability}:{json.dumps(action.params, sort_keys=True)}"
