"""Token-usage metering — capture per-call token counts and estimate spend.

Providers record each model call here (real counts from the API when available, else a
cheap char-based estimate) so a whole run can report input/output tokens and an estimated
cost — without changing the ``ModelProvider.complete`` contract. Thread-safe, so the parallel
executor's workers can all record into one process-wide meter.

    from autarch import get_usage_meter
    get_usage_meter().reset()
    ...run work...
    print(get_usage_meter().totals())   # {'calls', 'prompt_tokens', 'completion_tokens', ...}
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .pricing import PriceBook

_local = threading.local()


def _current_label() -> str:
    return getattr(_local, "label", "") or ""


def current_label() -> str:
    """The active ``usage_label`` for the CALLING thread ('' if none). A provider that records usage
    on a *different* thread (e.g. MAFModelProvider's loop) should capture this on the caller and pass
    it to ``record_usage(label=...)`` so the phase isn't lost."""
    return _current_label()


class usage_label:
    """Context manager to tag every model call recorded within it with a pipeline-phase label.

    Thread-local, so parallel workers each set their own::

        with usage_label("extract_fields"):
            provider.complete(...)
    """

    def __init__(self, name: str) -> None:
        self.name = str(name or "")

    def __enter__(self) -> "usage_label":
        self._prev = getattr(_local, "label", "")
        _local.label = self.name
        return self

    def __exit__(self, *exc) -> bool:
        _local.label = self._prev
        return False


@dataclass
class CallUsage:
    """One model call's token usage."""

    model: str
    prompt_tokens: int
    completion_tokens: int
    estimated: bool  # True => counts were estimated (API returned no usage), not authoritative
    ts: float = field(default_factory=time.time)
    source: str = ""  # which provider recorded it (azure, maf, ...)
    label: str = ""  # pipeline phase, from a usage_label(...) context
    started: float = 0.0  # wall-clock start of the model call (0 if unknown)
    ended: float = 0.0  # wall-clock end of the model call (0 if unknown)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def duration(self) -> float:
        return max(0.0, self.ended - self.started) if (self.started and self.ended) else 0.0


class UsageMeter:
    """Thread-safe accumulator of model token usage across a run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: List[CallUsage] = []

    def record(self, model, prompt_tokens, completion_tokens, *, estimated: bool = False,
               source: str = "", label: Optional[str] = None, started: float = 0.0, ended: float = 0.0) -> CallUsage:
        cu = CallUsage(
            model=str(model or "?"),
            prompt_tokens=max(0, int(prompt_tokens or 0)),
            completion_tokens=max(0, int(completion_tokens or 0)),
            estimated=bool(estimated),
            source=str(source or ""),
            label=str(label if label is not None else _current_label()),
            started=float(started or 0.0),
            ended=float(ended or 0.0),
        )
        with self._lock:
            self._calls.append(cu)
        return cu

    @property
    def calls(self) -> List[CallUsage]:
        with self._lock:
            return list(self._calls)

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()

    def totals(self) -> Dict[str, object]:
        calls = self.calls
        p = sum(c.prompt_tokens for c in calls)
        c = sum(c.completion_tokens for c in calls)
        return {
            "calls": len(calls),
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": p + c,
            "any_estimated": any(cu.estimated for cu in calls),
        }

    def by_model(self) -> Dict[str, Dict[str, int]]:
        agg: Dict[str, Dict[str, int]] = {}
        for c in self.calls:
            d = agg.setdefault(c.model, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
            d["calls"] += 1
            d["prompt_tokens"] += c.prompt_tokens
            d["completion_tokens"] += c.completion_tokens
        return agg

    def cost(self, price_book: Optional[PriceBook] = None) -> float:
        pb = price_book or PriceBook()
        return sum(pb.token_cost(c.model, c.prompt_tokens, c.completion_tokens) for c in self.calls)


_GLOBAL_METER = UsageMeter()


def get_usage_meter() -> UsageMeter:
    """The process-wide default meter (reset it at the start of a run)."""
    return _GLOBAL_METER


def record_usage(model, prompt_tokens, completion_tokens, *, estimated: bool = False,
                 source: str = "", label: Optional[str] = None, started: float = 0.0, ended: float = 0.0) -> CallUsage:
    """Record one model call into the global meter (used by providers; fail-soft at call sites)."""
    return _GLOBAL_METER.record(model, prompt_tokens, completion_tokens, estimated=estimated,
                                source=source, label=label, started=started, ended=ended)
