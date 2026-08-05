"""Rewind — governed, audited reversal of past actions.

Because every executed action captured undo information, Autarch can scrub time
backwards. Crucially, a rewind is **not a backdoor**: each reversal is itself an
action that passes through the capability kernel and is recorded as a new
why-record (linked to the original via `rewind_of`). You can even rewind a
rewind.

You can rewind the last N actions, everything since a time window, or a single
action — and *keep* chosen actions untouched ("undo the last hour, but keep the
file I created").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .adapters.base import Adapter
from .contracts import Action, GateResult, WhyRecord, new_id
from .kernel import CapabilityKernel
from .memory import WhyMemory


@dataclass
class RewindStep:
    original_id: str
    capability: str
    params: dict
    gate_allowed: bool
    gate_reason: str
    executed: bool
    error: Optional[str] = None
    new_why_id: Optional[str] = None


def parse_duration(text: str) -> float:
    """Parse a human duration into seconds.

    Accepts '30s', '5m', '2h', '1d', or '1 hour', '30 minutes', '2 days'.
    """
    text = text.strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([a-z]*)", text)
    if not match:
        raise ValueError(f"cannot parse duration: {text!r}")
    value = float(match.group(1))
    unit = match.group(2) or "s"
    factors = {
        "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
        "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
        "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
        "d": 86400, "day": 86400, "days": 86400,
    }
    if unit not in factors:
        raise ValueError(f"unknown time unit: {unit!r}")
    return value * factors[unit]


def undo_to_action(undo: dict) -> Optional[Action]:
    """Translate captured undo information into a concrete reversing Action."""
    capability = undo.get("capability")
    if capability == "file.write":
        return Action("file.write", {"path": undo["path"], "content": undo.get("restore") or ""})
    if capability == "file.delete":
        return Action("file.delete", {"path": undo["path"]})
    if capability == "file.move":
        return Action("file.move", {"path": undo["path"], "dest": undo["dest"]})
    return None


class Rewinder:
    def __init__(
        self,
        memory: WhyMemory,
        kernel: CapabilityKernel,
        adapters_by_capability: Dict[str, Adapter],
        on_step: Optional[Callable[[RewindStep], None]] = None,
    ):
        self.memory = memory
        self.kernel = kernel
        self._by_capability = adapters_by_capability
        self.on_step = on_step

    def candidates(
        self,
        ids: Optional[List[str]] = None,
        last: Optional[int] = None,
        since_seconds: Optional[float] = None,
        keep_capabilities: Optional[set] = None,
        keep_ids: Optional[set] = None,
    ) -> List[WhyRecord]:
        """Reversible, executed actions to undo — newest first."""
        if ids:
            pool = [r for r in (self.memory.get(i) for i in ids) if r is not None]
        elif since_seconds is not None:
            pool = self.memory.since(since_seconds)
        else:
            pool = self.memory.all()

        keep_capabilities = keep_capabilities or set()
        keep_ids = keep_ids or set()
        selected: List[WhyRecord] = []
        for rec in pool:  # already newest-first from memory
            if not rec.executed or not rec.undo:
                continue
            if rec.rewind_of:  # don't undo an undo by default
                continue
            if rec.capability in keep_capabilities or rec.id in keep_ids:
                continue
            selected.append(rec)
            if last is not None and len(selected) >= last:
                break
        return selected

    def rewind(self, records: List[WhyRecord]) -> List[RewindStep]:
        """Reverse each record, governed by the kernel and recorded as new why."""
        steps: List[RewindStep] = []
        for rec in records:  # newest first
            action = undo_to_action(rec.undo or {})
            if action is None:
                step = RewindStep(rec.id, rec.capability, {}, False,
                                  "no reversal known for this action", False,
                                  error="irreversible")
                steps.append(step)
                if self.on_step:
                    self.on_step(step)
                continue

            gate = self.kernel.authorize(action)
            executed, error, new_why_id = False, None, None
            if gate.allowed:
                adapter = self._by_capability.get(action.capability)
                if adapter is None:
                    error = f"no adapter for '{action.capability}'"
                else:
                    result = adapter.execute(action)
                    executed = result.ok
                    error = result.error
                    new_why_id = self._record_rewind(rec, action, gate, result)
            else:
                error = gate.reason

            step = RewindStep(
                rec.id, action.capability, action.params,
                gate.allowed, gate.reason, executed, error, new_why_id,
            )
            steps.append(step)
            if self.on_step:
                self.on_step(step)
        return steps

    def _record_rewind(self, original: WhyRecord, action: Action, gate: GateResult, result) -> str:
        record = WhyRecord(
            intent_text=f"rewind of {original.id} ({original.capability})",
            capability=action.capability,
            params=action.params,
            rationale=f"Reverse {original.capability} performed in {original.id}.",
            proposer="system:rewind",
            challenger="system:rewind",
            critique_verdict="approve",
            critique_reasons="Autarch-initiated reversal.",
            gate_allowed=gate.allowed,
            gate_reason=gate.reason,
            human_decision="ratify",
            executed=result.ok,
            result_ok=result.ok,
            result_output=result.output,
            result_error=result.error,
            undo=result.undo,
            recommendation="approve",
            voices=["system:rewind"],
            tally={"approve": 1},
            proposal_disagreement=False,
            rounds=1,
            rewind_of=original.id,
        )
        return self.memory.record(record)
