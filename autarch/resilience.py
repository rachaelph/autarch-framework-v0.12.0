"""Resilience — the framework never falls over on rate limits or flaky providers.

Every LLM call in Autarch flows through one seam: ``ModelProvider.complete()``.
This module wraps that seam so the *whole* system inherits production-grade
resilience with **zero developer code**:

  - **Proactive rate limiting** (a token bucket over requests/min *and* tokens/min)
    that *waits* for capacity instead of firing and failing — so you never trip a
    provider's limit in the first place. Tell Autarch your provider's limits
    once and it guarantees you stay under them.
  - **Retry with exponential backoff + full jitter** on transient failures,
    honoring a server's ``Retry-After`` when present.
  - **A circuit breaker** that fails fast while a provider is down, so you don't
    burn quota (or budget) hammering a dead endpoint.
  - **Adaptive control (AIMD)** — every throttle signal multiplicatively narrows
    the effective rate; sustained success additively widens it back. The pace
    tunes itself to whatever the provider currently tolerates, automatically, on
    the transactions you actually fire.

Pure Python + stdlib (``threading``), thread-safe for the parallel council. This
is a *consequence* layer: every retry, throttle, and trip is an observable event,
so resilience is something you can see and prove — not just hope for.

Design rule, consistent with the rest of Autarch: the wrapper never changes a
result, only *when* and *whether* a call is made. ``Resilient`` is a drop-in
``ModelProvider`` and reports the inner provider's ``name`` unchanged.
"""
from __future__ import annotations

import math
import random
import threading
import time
import urllib.error
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, List, Optional, Tuple

from .errors import CircuitOpen, ModelError, ModelUnavailable, RateLimited
from .events import (
    PROVIDER_CIRCUIT_OPEN,
    PROVIDER_RECOVERED,
    PROVIDER_RETRY,
    PROVIDER_THROTTLED,
    EventSink,
    NullSink,
    emit,
)
from .intelligence.base import ModelProvider


# --------------------------------------------------------------------------- #
# Token accounting                                                            #
# --------------------------------------------------------------------------- #
def count_tokens(text: str) -> int:
    """A dependency-free token estimate (~4 chars/token).

    Deliberately approximate: Autarch ships with no tokenizer so it stays
    self-contained. Pass a real tokenizer to ``Resilient(count_tokens=...)`` when
    you need exactness. Used only for *pacing* (rate limiting), never for billing.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


# --------------------------------------------------------------------------- #
# Failure classification                                                       #
# --------------------------------------------------------------------------- #
class Disposition(Enum):
    """How the resilience layer should treat a failed call."""

    RETRYABLE = "retryable"   # transient — back off and try again
    THROTTLED = "throttled"   # rate limited — slow down, honor Retry-After
    TERMINAL = "terminal"     # won't be fixed by retrying (bad request, auth, ...)


def classify(exc: BaseException) -> Tuple[Disposition, Optional[float]]:
    """Map an exception to a disposition and an optional ``retry_after`` (seconds).

    Recognizes Autarch's typed model errors first, then falls back to raw
    ``urllib`` errors so it works even with providers that don't raise typed
    errors. Anything unrecognized is treated as TERMINAL — we never retry blindly.
    """
    if isinstance(exc, RateLimited):
        return Disposition.THROTTLED, exc.retry_after
    if isinstance(exc, ModelUnavailable):
        return Disposition.RETRYABLE, None
    if isinstance(exc, CircuitOpen):
        return Disposition.TERMINAL, None  # already a fast-fail; don't loop on it

    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 429:
            return Disposition.THROTTLED, _retry_after_seconds(exc)
        if exc.code in (500, 502, 503, 504):
            return Disposition.RETRYABLE, None
        return Disposition.TERMINAL, None

    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return Disposition.RETRYABLE, None

    return Disposition.TERMINAL, None


def _retry_after_seconds(exc: "urllib.error.HTTPError") -> Optional[float]:
    """Parse a ``Retry-After`` header (delta-seconds form) if present."""
    try:
        value = exc.headers.get("Retry-After") if exc.headers else None
    except Exception:
        return None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None  # HTTP-date form is rare for rate limits; ignore safely


# --------------------------------------------------------------------------- #
# Policies (all plain dataclasses — easy to read, easy to tune)               #
# --------------------------------------------------------------------------- #
@dataclass
class RetryPolicy:
    """How to retry transient failures: exponential backoff with full jitter."""

    max_attempts: int = 3          # total tries for a RETRYABLE failure
    base_delay: float = 0.5        # seconds for the first backoff
    max_delay: float = 20.0        # cap on any single backoff
    multiplier: float = 2.0        # exponential growth factor
    jitter: bool = True            # full jitter spreads retries, avoids stampedes
    max_throttle_waits: int = 8    # how many times to wait out a rate limit

    def backoff(self, attempt: int) -> float:
        """Delay before retry ``attempt`` (1-based)."""
        ceiling = min(self.max_delay, self.base_delay * (self.multiplier ** (attempt - 1)))
        if self.jitter:
            return random.uniform(0.0, ceiling)
        return ceiling


@dataclass
class RateLimit:
    """A proactive quota the wrapper *stays under* by waiting, never exceeds.

    Leave a field ``None`` to not limit that dimension. Defaults are all-off so
    local providers (e.g. Ollama) run at full speed; set these to your cloud
    provider's published limits and Autarch guarantees you stay within them.
    """

    requests_per_minute: Optional[int] = None
    tokens_per_minute: Optional[int] = None
    max_concurrency: Optional[int] = None
    # Adaptive (AIMD) control of the effective rate:
    adapt: bool = True
    backoff_factor: float = 0.5    # multiplicative *decrease* on a throttle signal
    recover_step: float = 0.1      # additive *increase* per successful call
    min_fraction: float = 0.1      # never throttle ourselves below 10% of the rate
    default_pause: float = 1.0     # pause when throttled without a Retry-After


@dataclass
class CircuitBreaker:
    """Fail fast while a provider is clearly down, then probe for recovery."""

    failure_threshold: int = 5     # consecutive transient failures before opening
    cooldown: float = 30.0         # seconds to stay open before a trial call
    half_open_max: int = 1         # concurrent trial calls allowed while recovering


# --------------------------------------------------------------------------- #
# Internal mechanisms                                                          #
# --------------------------------------------------------------------------- #
class _Limiter:
    """Thread-safe token bucket(s) + concurrency gate + AIMD adaptation.

    Time is taken from an injectable ``clock``/``sleep`` so behavior is fast and
    deterministic under test. Locks are held only for arithmetic; all waiting
    happens outside the lock.
    """

    def __init__(self, cfg: RateLimit, clock: Callable[[], float], sleep: Callable[[float], None]):
        self._cfg = cfg
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._fraction = 1.0
        self._updated = clock()
        self._paused_until = 0.0
        self._req_cap = cfg.requests_per_minute
        self._tok_cap = cfg.tokens_per_minute
        self._req = float(cfg.requests_per_minute or 0)
        self._tok = float(cfg.tokens_per_minute or 0)
        self._rate_active = bool(self._req_cap or self._tok_cap)
        self._sem = threading.BoundedSemaphore(cfg.max_concurrency) if cfg.max_concurrency else None

    # -- concurrency gate (held only for the duration of the actual call) --
    def enter(self) -> None:
        if self._sem is not None:
            self._sem.acquire()

    def leave(self) -> None:
        if self._sem is not None:
            try:
                self._sem.release()
            except ValueError:
                pass

    # -- proactive rate gate: block until this request fits under the quota --
    def await_capacity(self, tokens: int) -> None:
        if not self._rate_active:
            return
        # A single request larger than the whole bucket would deadlock; clamp it
        # so an oversized prompt is paced as "one full bucket" rather than forever.
        need_tok = float(tokens)
        if self._tok_cap:
            need_tok = min(need_tok, float(self._tok_cap))
        while True:
            with self._lock:
                now = self._clock()
                if now >= self._paused_until:
                    self._refill(now)
                    have_req = (not self._req_cap) or self._req >= 1.0 - 1e-9
                    have_tok = (not self._tok_cap) or self._tok >= need_tok - 1e-9
                    if have_req and have_tok:
                        if self._req_cap:
                            self._req = max(0.0, self._req - 1.0)
                        if self._tok_cap:
                            self._tok = max(0.0, self._tok - need_tok)
                        return
                    wait = self._eta(need_tok)
                else:
                    wait = self._paused_until - now
            self._sleep(max(wait, 0.0))

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._updated)
        self._updated = now
        frac = self._fraction if self._cfg.adapt else 1.0
        if self._req_cap:
            self._req = min(float(self._req_cap), self._req + elapsed * (self._req_cap / 60.0) * frac)
        if self._tok_cap:
            self._tok = min(float(self._tok_cap), self._tok + elapsed * (self._tok_cap / 60.0) * frac)

    def _eta(self, need_tok: float) -> float:
        """Seconds until both buckets hold enough (called under lock)."""
        frac = max(self._fraction if self._cfg.adapt else 1.0, 1e-6)
        waits = []
        if self._req_cap and self._req < 1.0:
            waits.append((1.0 - self._req) / ((self._req_cap / 60.0) * frac))
        if self._tok_cap and self._tok < need_tok:
            waits.append((need_tok - self._tok) / ((self._tok_cap / 60.0) * frac))
        return min(waits) if waits else 0.0

    # -- AIMD: adjust pace from the outcomes of calls we actually fired ----
    def on_success(self) -> None:
        if not self._cfg.adapt:
            return
        with self._lock:
            self._fraction = min(1.0, self._fraction + self._cfg.recover_step)

    def on_throttle(self, retry_after: Optional[float]) -> None:
        with self._lock:
            if self._cfg.adapt:
                self._fraction = max(self._cfg.min_fraction, self._fraction * self._cfg.backoff_factor)
            pause = retry_after if (retry_after and retry_after > 0) else self._cfg.default_pause
            self._paused_until = max(self._paused_until, self._clock() + pause)
            # Drain so nothing fires until the pause elapses.
            self._req = 0.0
            self._tok = 0.0


class _Breaker:
    """A three-state circuit breaker (closed → open → half-open → closed)."""

    def __init__(self, cfg: CircuitBreaker, clock: Callable[[], float], on_open=None, on_close=None):
        self._cfg = cfg
        self._clock = clock
        self._on_open = on_open
        self._on_close = on_close
        self._lock = threading.Lock()
        self._state = "closed"
        self._failures = 0
        self._opened_at = 0.0
        self._half_open = 0

    def allow(self) -> None:
        """Permit a call or raise ``CircuitOpen``. Pairs with exactly one settle()."""
        with self._lock:
            if self._state == "open":
                if self._clock() - self._opened_at >= self._cfg.cooldown:
                    self._state = "half_open"
                    self._half_open = 0
                else:
                    raise CircuitOpen(
                        "Provider circuit is open after repeated failures; failing fast.",
                        context={"cooldown_s": self._cfg.cooldown},
                    )
            if self._state == "half_open":
                if self._half_open >= self._cfg.half_open_max:
                    raise CircuitOpen(
                        "Provider circuit is recovering; trial slot unavailable.",
                        context={"half_open_max": self._cfg.half_open_max},
                    )
                self._half_open += 1

    def settle(self, outcome: str) -> None:
        """Record the result of an allowed call: 'success', 'failure', or 'ignored'."""
        with self._lock:
            if self._state == "half_open":
                self._half_open = max(0, self._half_open - 1)
            if outcome == "success":
                reopened = self._state != "closed"
                self._state = "closed"
                self._failures = 0
                if reopened and self._on_close:
                    self._on_close()
            elif outcome == "failure":
                if self._state == "half_open":
                    self._state = "open"
                    self._opened_at = self._clock()
                    if self._on_open:
                        self._on_open()
                    return
                self._failures += 1
                if self._failures >= self._cfg.failure_threshold and self._state == "closed":
                    self._state = "open"
                    self._opened_at = self._clock()
                    if self._on_open:
                        self._on_open()
            # 'ignored' (terminal/throttle) leaves failure count untouched.

    @property
    def state(self) -> str:
        with self._lock:
            return self._state


# --------------------------------------------------------------------------- #
# The wrapper                                                                  #
# --------------------------------------------------------------------------- #
class Resilient(ModelProvider):
    """Wrap any ``ModelProvider`` with retry, rate limiting, and a circuit breaker.

    Drop-in: it *is* a ``ModelProvider`` and reports the inner ``name`` unchanged,
    so the council, judges, and provenance see no difference — only that calls now
    survive transient failures and never exceed a provider's quota.
    """

    def __init__(
        self,
        inner: ModelProvider,
        retry: Optional[RetryPolicy] = None,
        rate: Optional[RateLimit] = None,
        breaker: Optional[CircuitBreaker] = None,
        *,
        count_tokens: Callable[[str], int] = count_tokens,
        est_completion_tokens: int = 512,
        sink: Optional[EventSink] = None,
        run_id: Optional[str] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._inner = inner
        self.name = getattr(inner, "name", "provider")
        self._retry = retry or RetryPolicy()
        self._breaker_cfg = breaker or CircuitBreaker()
        self._count = count_tokens
        self._est_completion = max(0, est_completion_tokens)
        self._sink = sink or NullSink()
        self._run_id = run_id or f"provider:{self.name}"
        self._clock = clock
        self._sleep = sleep
        self._limiter = _Limiter(rate or RateLimit(), clock, sleep)
        self._breaker = _Breaker(
            self._breaker_cfg,
            clock,
            on_open=lambda: emit(self._sink, PROVIDER_CIRCUIT_OPEN, self._run_id, provider=self.name),
            on_close=lambda: emit(self._sink, PROVIDER_RECOVERED, self._run_id, provider=self.name),
        )

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        tokens = self._count(prompt) + self._count(system or "") + self._est_completion
        return self._call(lambda: self._inner.complete(prompt, system=system), tokens)

    def supports_vision(self) -> bool:
        probe = getattr(self._inner, "supports_vision", None)
        return bool(probe()) if callable(probe) else False

    def complete_vision(self, prompt, images, system=None) -> str:
        tokens = self._count(prompt) + self._count(system or "") + self._est_completion
        return self._call(lambda: self._inner.complete_vision(prompt, images, system=system), tokens)

    def _call(self, do, tokens: int) -> str:
        attempt = 0          # RETRYABLE failures so far
        throttles = 0        # THROTTLED waits so far
        while True:
            self._breaker.allow()                 # may raise CircuitOpen (fast fail)
            self._limiter.await_capacity(tokens)  # block until under quota
            self._limiter.enter()                 # hold a concurrency slot
            outcome = "ignored"
            try:
                result = do()
                outcome = "success"
                self._limiter.on_success()
                return result
            except BaseException as exc:  # noqa: BLE001 - classify, then re-raise or retry
                disposition, retry_after = classify(exc)
                if disposition is Disposition.TERMINAL:
                    raise
                if disposition is Disposition.THROTTLED:
                    self._limiter.on_throttle(retry_after)
                    throttles += 1
                    delay = retry_after if (retry_after and retry_after > 0) else self._retry.backoff(throttles)
                    emit(self._sink, PROVIDER_THROTTLED, self._run_id,
                         provider=self.name, retry_after=retry_after, delay=round(delay, 3),
                         waits=throttles)
                    if throttles > self._retry.max_throttle_waits:
                        raise RateLimited(
                            f"Provider '{self.name}' stayed rate limited after "
                            f"{throttles} waits.",
                            retry_after=retry_after,
                            context={"provider": self.name},
                        ) from exc
                    self._wait(delay)
                    continue
                # RETRYABLE
                outcome = "failure"
                attempt += 1
                if attempt >= self._retry.max_attempts:
                    raise
                delay = self._retry.backoff(attempt)
                emit(self._sink, PROVIDER_RETRY, self._run_id,
                     provider=self.name, attempt=attempt, max_attempts=self._retry.max_attempts,
                     delay=round(delay, 3), error=type(exc).__name__)
                self._wait(delay)
                continue
            finally:
                self._limiter.leave()
                self._breaker.settle(outcome)

    def _wait(self, delay: float) -> None:
        if delay > 0:
            self._sleep(delay)

    @property
    def circuit_state(self) -> str:
        """'closed', 'open', or 'half_open' — exposed for health checks/tests."""
        return self._breaker.state


def make_resilient(
    provider: ModelProvider,
    *,
    retry: Optional[RetryPolicy] = None,
    rate: Optional[RateLimit] = None,
    breaker: Optional[CircuitBreaker] = None,
    sink: Optional[EventSink] = None,
    **kwargs,
) -> Resilient:
    """Convenience wrapper. Idempotent: re-wrapping a ``Resilient`` returns it as-is."""
    if isinstance(provider, Resilient):
        return provider
    return Resilient(provider, retry=retry, rate=rate, breaker=breaker, sink=sink, **kwargs)


# --------------------------------------------------------------------------- #
# Adaptive parallel execution                                                  #
# --------------------------------------------------------------------------- #
@dataclass
class TaskOutcome:
    """The result of one task run by :class:`AdaptiveExecutor` — always produced,
    whether the task ultimately succeeded or failed."""

    index: int
    ok: bool
    value: Any = None
    error: Optional[BaseException] = None
    attempts: int = 0
    throttles: int = 0


class _AdaptiveState:
    """Shared, lock-guarded state for one :meth:`AdaptiveExecutor.run` call."""

    __slots__ = ("tasks", "n", "queue", "outcomes", "remaining", "active", "ceiling", "peak", "cv")

    def __init__(self, tasks: List[Callable[[], Any]], start_ceiling: float):
        self.tasks = tasks
        self.n = len(tasks)
        self.queue = deque((i, 0, 0) for i in range(self.n))  # (index, attempts, throttles)
        self.outcomes: List[Optional[TaskOutcome]] = [None] * self.n
        self.remaining = self.n
        self.active = 0
        self.ceiling = float(start_ceiling)
        self.peak = int(start_ceiling)
        self.cv = threading.Condition()


class AdaptiveExecutor:
    """Run many callables in parallel with **self-tuning concurrency** and **guaranteed
    completion** — the generic engine behind rate-limit-aware, governed fan-out.

    Concurrency follows AIMD (the same control law as :class:`Resilient`): it widens
    additively while tasks succeed and narrows multiplicatively the moment a task is
    *throttled* (a 429 / :class:`RateLimited`), honoring any ``Retry-After``. So the
    number of workers in flight tracks whatever the downstream (an LLM provider, a DB)
    currently tolerates — there is **no fixed parallelism cap** to guess, and you don't
    trip a provider's limit by firing too much at once.

    Every task is retried through transient and throttle failures and **always yields a
    ``TaskOutcome``** (success or a recorded terminal error); one task failing never
    aborts the others. Tasks are plain zero-arg callables, so *any* agent or pipeline
    can use it — pair it with governed ``Agent.spawn`` children for a fully governed,
    adaptively-parallel fleet.

    Layering note: point each task's model calls at a shared :class:`Resilient`
    provider. That wrapper absorbs brief throttles (waiting under the quota); sustained
    throttling surfaces here and narrows the whole fleet. Together they guarantee the
    work completes without rate-limit errors reaching the caller.
    """

    def __init__(
        self,
        *,
        start: int = 2,
        min_concurrency: int = 1,
        max_concurrency: Optional[int] = None,
        recover_step: float = 0.5,
        backoff_factor: float = 0.5,
        max_throttle_waits: int = 20,
        retry: Optional[RetryPolicy] = None,
        thread_cap: int = 64,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        classifier: Callable[[BaseException], Tuple[Disposition, Optional[float]]] = classify,
    ):
        self._start = max(1, int(start))
        self._min = max(1, int(min_concurrency))
        self._max = int(max_concurrency) if max_concurrency else None
        self._recover = max(1e-3, float(recover_step))
        self._backoff = min(0.99, max(0.05, float(backoff_factor)))
        self._max_throttle = max(0, int(max_throttle_waits))
        self._retry = retry or RetryPolicy()
        self._thread_cap = max(1, int(thread_cap))
        self._clock = clock
        self._sleep = sleep
        self._classify = classifier

    # -- public API -------------------------------------------------------
    def map(self, fn: Callable[[Any], Any], items) -> List[TaskOutcome]:
        """Run ``fn(item)`` for every item, adaptively in parallel."""
        items = list(items)
        return self.run([(lambda it=it: fn(it)) for it in items])

    def run(self, tasks) -> List[TaskOutcome]:
        """Run each zero-arg callable, adaptively in parallel. Returns one
        ``TaskOutcome`` per task, in input order. Never raises for a task failure."""
        tasks = list(tasks)
        if not tasks:
            return []
        state = _AdaptiveState(tasks, min(self._start, self._effective_max(len(tasks))))
        workers = min(len(tasks), self._thread_cap)
        threads = [
            threading.Thread(target=self._worker, args=(state,), daemon=True, name=f"adaptive-{i}")
            for i in range(workers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return list(state.outcomes)  # type: ignore[arg-type]  # all slots filled at completion

    @property
    def _all_done_sentinel(self):  # pragma: no cover - documentation aid
        return None

    # -- internals --------------------------------------------------------
    def _effective_max(self, n: int) -> int:
        hi = self._max if self._max is not None else n
        return max(self._min, min(hi, n))

    def _next_item(self, state: _AdaptiveState):
        """Block until an item is takeable under the current ceiling; return it (and
        mark a worker active), or ``None`` when every task has a terminal outcome."""
        with state.cv:
            while True:
                if state.remaining == 0:
                    state.cv.notify_all()
                    return None
                limit = max(self._min, min(self._effective_max(state.n), int(state.ceiling)))
                if state.queue and state.active < limit:
                    item = state.queue.popleft()
                    state.active += 1
                    if state.active > state.peak:
                        state.peak = state.active
                    return item
                state.cv.wait(timeout=0.25)

    def _worker(self, state: _AdaptiveState) -> None:
        while True:
            item = self._next_item(state)
            if item is None:
                return
            self._process(state, *item)

    def _process(self, state: _AdaptiveState, idx: int, attempts: int, throttles: int) -> None:
        try:
            value = state.tasks[idx]()
        except BaseException as exc:  # noqa: BLE001 - classify, retry, or record; never propagate
            self._on_failure(state, idx, attempts, throttles, exc)
            return
        with state.cv:
            state.active -= 1
            state.ceiling = min(float(self._effective_max(state.n)), state.ceiling + self._recover)  # widen
            state.outcomes[idx] = TaskOutcome(idx, True, value, None, attempts + 1, throttles)
            state.remaining -= 1
            state.cv.notify_all()

    def _on_failure(self, state, idx, attempts, throttles, exc) -> None:
        disposition, retry_after = self._classify(exc)
        # THROTTLED: back off the whole fleet, honor Retry-After, retry the task.
        if disposition is Disposition.THROTTLED and throttles < self._max_throttle:
            delay = retry_after if (retry_after and retry_after > 0) else self._retry.backoff(throttles + 1)
            with state.cv:
                state.active -= 1
                state.ceiling = max(float(self._min), state.ceiling * self._backoff)  # narrow
                state.cv.notify_all()
            if delay > 0:
                self._sleep(delay)
            with state.cv:
                state.queue.append((idx, attempts, throttles + 1))
                state.cv.notify_all()
            return
        # RETRYABLE: exponential backoff, retry up to the policy's attempt budget.
        if disposition is Disposition.RETRYABLE and (attempts + 1) < self._retry.max_attempts:
            delay = self._retry.backoff(attempts + 1)
            with state.cv:
                state.active -= 1
                state.cv.notify_all()
            if delay > 0:
                self._sleep(delay)
            with state.cv:
                state.queue.append((idx, attempts + 1, throttles))
                state.cv.notify_all()
            return
        # TERMINAL or budget exhausted: record the failure; siblings carry on.
        with state.cv:
            state.active -= 1
            state.outcomes[idx] = TaskOutcome(idx, False, None, exc, attempts + 1, throttles)
            state.remaining -= 1
            state.cv.notify_all()
