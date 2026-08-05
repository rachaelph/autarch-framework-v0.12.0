"""Tests for the resilience layer — retry, rate limiting, and circuit breaking.

Time is injected (a fake monotonic clock whose ``sleep`` advances it), so these
tests are fully deterministic and run instantly — no real waiting.
"""
from __future__ import annotations

import urllib.error

import pytest

from autarch import (
    Agent,
    CircuitBreaker,
    CircuitOpen,
    ListSink,
    ModelError,
    ModelUnavailable,
    RateLimit,
    RateLimited,
    Resilient,
    RetryPolicy,
    make_resilient,
)
from autarch.intelligence.base import ModelProvider
from autarch.intelligence.factory import build_provider
from autarch.intelligence.mock import MockProvider
from autarch.intelligence.ollama import OllamaProvider
from autarch.resilience import (
    Disposition,
    _Breaker,
    _Limiter,
    classify,
    count_tokens,
)
from autarch.events import (
    PROVIDER_CIRCUIT_OPEN,
    PROVIDER_RETRY,
    PROVIDER_THROTTLED,
)


# --------------------------------------------------------------------------- #
# Test doubles                                                                 #
# --------------------------------------------------------------------------- #
class FakeClock:
    """A monotonic clock whose sleep advances time instead of blocking."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def time(self) -> float:
        return self.now

    def sleep(self, dt: float) -> None:
        self.now += max(0.0, dt)


class Scripted(ModelProvider):
    """Returns or raises a scripted sequence of outcomes, counting calls."""

    def __init__(self, *outcomes):
        self.name = "scripted"
        self._outcomes = list(outcomes)
        self.calls = 0

    def complete(self, prompt, system=None):
        self.calls += 1
        item = self._outcomes.pop(0) if self._outcomes else self._last
        self._last = item
        if isinstance(item, BaseException):
            raise item
        return item


def _resilient(provider, **kwargs):
    clk = FakeClock()
    kwargs.setdefault("clock", clk.time)
    kwargs.setdefault("sleep", clk.sleep)
    kwargs.setdefault("retry", RetryPolicy(jitter=False))
    return Resilient(provider, **kwargs), clk


# --------------------------------------------------------------------------- #
# Classification                                                               #
# --------------------------------------------------------------------------- #
def test_classify_typed_errors():
    assert classify(RateLimited("x", retry_after=3))[0] is Disposition.THROTTLED
    assert classify(RateLimited("x", retry_after=3))[1] == 3
    assert classify(ModelUnavailable("x"))[0] is Disposition.RETRYABLE
    assert classify(ModelError("x"))[0] is Disposition.TERMINAL


def test_classify_http_and_network_errors():
    assert classify(urllib.error.HTTPError("u", 429, "rl", {}, None))[0] is Disposition.THROTTLED
    assert classify(urllib.error.HTTPError("u", 503, "down", {}, None))[0] is Disposition.RETRYABLE
    assert classify(urllib.error.HTTPError("u", 400, "bad", {}, None))[0] is Disposition.TERMINAL
    assert classify(urllib.error.URLError("boom"))[0] is Disposition.RETRYABLE
    assert classify(TimeoutError())[0] is Disposition.RETRYABLE
    assert classify(ValueError("nope"))[0] is Disposition.TERMINAL


def test_count_tokens_is_positive_and_zero_for_empty():
    assert count_tokens("") == 0
    assert count_tokens("hello world") >= 1


# --------------------------------------------------------------------------- #
# Retry                                                                        #
# --------------------------------------------------------------------------- #
def test_retry_succeeds_after_transient_failures():
    provider = Scripted(ModelUnavailable("down"), ModelUnavailable("down"), "ok")
    r, clk = _resilient(provider, retry=RetryPolicy(max_attempts=3, base_delay=1.0, jitter=False))
    assert r.complete("hi") == "ok"
    assert provider.calls == 3
    assert clk.now > 1000.0  # it backed off between attempts


def test_retry_gives_up_after_max_attempts():
    provider = Scripted(ModelUnavailable("down"), ModelUnavailable("down"), ModelUnavailable("down"))
    r, _ = _resilient(provider, retry=RetryPolicy(max_attempts=3, base_delay=0.1, jitter=False))
    with pytest.raises(ModelUnavailable):
        r.complete("hi")
    assert provider.calls == 3


def test_terminal_error_is_not_retried():
    provider = Scripted(ModelError("bad request"))
    r, _ = _resilient(provider)
    with pytest.raises(ModelError):
        r.complete("hi")
    assert provider.calls == 1


def test_success_passes_through_untouched():
    provider = Scripted("answer")
    r, clk = _resilient(provider)
    assert r.complete("hi") == "answer"
    assert provider.calls == 1
    assert clk.now == 1000.0  # no waiting on the happy path


def test_retry_emits_events():
    provider = Scripted(ModelUnavailable("down"), "ok")
    sink = ListSink()
    r, _ = _resilient(provider, retry=RetryPolicy(max_attempts=3, base_delay=0.1, jitter=False), sink=sink)
    assert r.complete("hi") == "ok"
    assert PROVIDER_RETRY in sink.kinds()


# --------------------------------------------------------------------------- #
# Throttling — the "never fail on rate limits" guarantee                       #
# --------------------------------------------------------------------------- #
def test_throttle_honors_retry_after():
    provider = Scripted(RateLimited("slow down", retry_after=5.0), "ok")
    sink = ListSink()
    r, clk = _resilient(provider, sink=sink)
    assert r.complete("hi") == "ok"
    assert clk.now == pytest.approx(1005.0)  # waited exactly the Retry-After
    throttled = sink.of_kind(PROVIDER_THROTTLED)
    assert throttled and throttled[0].data["retry_after"] == 5.0


def test_throttle_does_not_consume_retry_budget():
    # Even with a tiny retry budget, repeated rate limits do NOT surface as a
    # failure — the framework waits them out. This is the core promise.
    provider = Scripted(*([RateLimited("rl", retry_after=1.0)] * 5 + ["ok"]))
    r, _ = _resilient(provider, retry=RetryPolicy(max_attempts=2, jitter=False))
    assert r.complete("hi") == "ok"
    assert provider.calls == 6
    assert r.circuit_state == "closed"  # throttling never trips the breaker


def test_throttle_eventually_gives_up_after_max_waits():
    provider = Scripted(RateLimited("rl", retry_after=1.0))  # always throttled
    r, _ = _resilient(provider, retry=RetryPolicy(max_throttle_waits=3, jitter=False))
    with pytest.raises(RateLimited):
        r.complete("hi")


# --------------------------------------------------------------------------- #
# Proactive rate limiting (token bucket)                                       #
# --------------------------------------------------------------------------- #
def test_rate_limiter_paces_requests_per_minute():
    clk = FakeClock()
    lim = _Limiter(RateLimit(requests_per_minute=60), clk.time, clk.sleep)
    start = clk.now
    for _ in range(60):          # drain the initial burst
        lim.await_capacity(0)
    assert clk.now == start      # burst is instant
    lim.await_capacity(0)        # the 61st must wait ~1s for a refill at 60/min
    assert clk.now - start == pytest.approx(1.0, abs=1e-6)


def test_rate_limiter_is_token_aware():
    clk = FakeClock()
    lim = _Limiter(RateLimit(tokens_per_minute=600), clk.time, clk.sleep)  # 10 tokens/s
    start = clk.now
    lim.await_capacity(600)      # spend the whole bucket at once
    assert clk.now == start
    lim.await_capacity(100)      # need 100 more tokens -> 10s at 10/s
    assert clk.now - start == pytest.approx(10.0, abs=1e-6)


def test_oversized_request_is_clamped_not_deadlocked():
    clk = FakeClock()
    lim = _Limiter(RateLimit(tokens_per_minute=100), clk.time, clk.sleep)
    lim.await_capacity(100)               # drain
    lim.await_capacity(10_000)            # bigger than the whole bucket
    # Clamped to one full bucket (100 tokens at 100/min = 60s) rather than forever.
    assert clk.now == pytest.approx(1060.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# Adaptive control (AIMD)                                                      #
# --------------------------------------------------------------------------- #
def test_aimd_narrows_on_throttle_and_recovers_on_success():
    clk = FakeClock()
    lim = _Limiter(
        RateLimit(requests_per_minute=60, backoff_factor=0.5, recover_step=0.1, min_fraction=0.1),
        clk.time,
        clk.sleep,
    )
    lim.on_throttle(None)
    assert lim._fraction == pytest.approx(0.5)
    lim.on_throttle(None)
    assert lim._fraction == pytest.approx(0.25)
    for _ in range(20):
        lim.on_success()
    assert lim._fraction == pytest.approx(1.0)  # additively recovered, capped at 1.0


def test_aimd_respects_minimum_floor():
    clk = FakeClock()
    lim = _Limiter(RateLimit(requests_per_minute=60, backoff_factor=0.5, min_fraction=0.1), clk.time, clk.sleep)
    for _ in range(20):
        lim.on_throttle(None)
    assert lim._fraction == pytest.approx(0.1)  # never throttles itself to a halt


# --------------------------------------------------------------------------- #
# Circuit breaker                                                              #
# --------------------------------------------------------------------------- #
def test_breaker_opens_after_threshold_then_fast_fails():
    clk = FakeClock()
    br = _Breaker(CircuitBreaker(failure_threshold=3, cooldown=30.0), clk.time)
    for _ in range(3):
        br.allow()
        br.settle("failure")
    assert br.state == "open"
    with pytest.raises(CircuitOpen):
        br.allow()


def test_breaker_half_opens_after_cooldown_and_closes_on_success():
    clk = FakeClock()
    br = _Breaker(CircuitBreaker(failure_threshold=1, cooldown=30.0), clk.time)
    br.allow()
    br.settle("failure")
    assert br.state == "open"
    clk.now += 31.0
    br.allow()                 # cooldown elapsed -> half-open trial permitted
    assert br.state == "half_open"
    br.settle("success")
    assert br.state == "closed"


def test_breaker_reopens_if_trial_fails():
    clk = FakeClock()
    br = _Breaker(CircuitBreaker(failure_threshold=1, cooldown=10.0), clk.time)
    br.allow()
    br.settle("failure")
    clk.now += 11.0
    br.allow()
    assert br.state == "half_open"
    br.settle("failure")
    assert br.state == "open"


def test_resilient_circuit_fails_fast_and_emits():
    provider = Scripted(ModelUnavailable("down"), ModelUnavailable("down"), ModelUnavailable("down"))
    sink = ListSink()
    r, _ = _resilient(
        provider,
        retry=RetryPolicy(max_attempts=1, jitter=False),
        breaker=CircuitBreaker(failure_threshold=2, cooldown=30.0),
        sink=sink,
    )
    for _ in range(2):
        with pytest.raises(ModelUnavailable):
            r.complete("hi")
    assert provider.calls == 2
    with pytest.raises(CircuitOpen):
        r.complete("hi")          # opens: does NOT call the provider again
    assert provider.calls == 2
    assert PROVIDER_CIRCUIT_OPEN in sink.kinds()


# --------------------------------------------------------------------------- #
# Wiring                                                                       #
# --------------------------------------------------------------------------- #
def test_resilient_reports_inner_name():
    assert Resilient(Scripted("x")).name == "scripted"


def test_make_resilient_is_idempotent():
    inner = Scripted("x")
    once = make_resilient(inner)
    assert make_resilient(once) is once


def test_build_provider_wraps_ollama_but_not_mock():
    assert isinstance(build_provider("ollama"), Resilient)
    assert isinstance(build_provider("ollama", resilient=False), OllamaProvider)
    assert isinstance(build_provider("mock"), MockProvider)
    assert not isinstance(build_provider("mock"), Resilient)


def test_agent_uses_resilient_providers_by_default():
    agent = Agent("read a file", council=["ollama:llama3"])
    assert all(isinstance(p, Resilient) for p in agent.providers)


def test_offline_agent_still_runs_with_resilient_wiring():
    # End-to-end smoke: the mock path is untouched and a run still completes.
    agent = Agent("read notes.txt", council=["mock"])
    result = agent.run()
    assert result is not None
