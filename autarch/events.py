"""Structured observability — every run emits a typed event stream.

Production systems need to *see* what an agent did, in real time and after the
fact, in a machine-readable form. Autarch emits a structured ``Event`` at each
lifecycle step (run start, deliberation, gate, policy, budget, decision,
execution, completion). Events flow to a pluggable ``EventSink``.

The default sink is a no-op (zero overhead, zero dependencies). A ``ListSink``
captures events for tests and inspection; a ``CallbackSink`` forwards them to any
function — including, optionally, an OpenTelemetry or JSON-lines exporter wired up
by the host. Emission never raises: a broken sink cannot break a run.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

# Canonical event kinds (stable strings for downstream consumers).
RUN_START = "run.start"
RUN_RESUMED = "run.resumed"
DELIBERATION_COMPLETE = "deliberation.complete"
GATE_CHECKED = "gate.checked"
POLICY_CHECKED = "policy.checked"
BUDGET_CHECKED = "budget.checked"
DECISION_MADE = "decision.made"
ACTION_EXECUTED = "action.executed"
EVALUATION_COMPLETE = "evaluation.complete"
RUN_COMPLETE = "run.complete"
RUN_BLOCKED = "run.blocked"
# Provider resilience (rate limits, retries, circuit breaker).
PROVIDER_RETRY = "provider.retry"
PROVIDER_THROTTLED = "provider.throttled"
PROVIDER_CIRCUIT_OPEN = "provider.circuit_open"
PROVIDER_RECOVERED = "provider.recovered"
# Orchestration (a master decomposes -> provisions children -> synthesizes).
ORCHESTRATION_DECOMPOSED = "orchestration.decomposed"
CHILD_SPAWNED = "orchestration.child_spawned"
CHILD_COMPLETE = "orchestration.child_complete"
ORCHESTRATION_SYNTHESIZED = "orchestration.synthesized"


@dataclass
class Event:
    kind: str
    run_id: str
    data: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"ts": self.ts, "run_id": self.run_id, "kind": self.kind, "data": self.data}


class EventSink(ABC):
    """Receives the event stream from a run."""

    @abstractmethod
    def emit(self, event: Event) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class NullSink(EventSink):
    """Discards events. The default — zero overhead."""

    def emit(self, event: Event) -> None:
        pass


class ListSink(EventSink):
    """Collects events in memory (for tests, inspection, or buffering)."""

    def __init__(self) -> None:
        self.events: List[Event] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)

    def kinds(self) -> List[str]:
        return [e.kind for e in self.events]

    def of_kind(self, kind: str) -> List[Event]:
        return [e for e in self.events if e.kind == kind]


class CallbackSink(EventSink):
    """Forwards each event to a callable (e.g. a logger or OTel exporter)."""

    def __init__(self, fn: Callable[[Event], None]) -> None:
        self._fn = fn

    def emit(self, event: Event) -> None:
        self._fn(event)


def emit(sink: EventSink, kind: str, run_id: str, **data: Any) -> None:
    """Emit an event, never raising if the sink misbehaves."""
    try:
        sink.emit(Event(kind=kind, run_id=run_id, data=data))
    except Exception:
        # Observability must never break the thing it observes.
        pass
