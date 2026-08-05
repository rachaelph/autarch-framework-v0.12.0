"""Resilience — the framework never falls over on rate limits or flaky providers.

Production agents make many LLM calls under bursty load, so they hit rate limits,
token-quota errors, and transient 5xx/timeout failures. Autarch handles all of
this at the one seam every model call passes through, so developers write ZERO
resilience code:

  1. Retry with exponential backoff + jitter on transient failures.
  2. A token-aware queue that *waits* for quota instead of firing and failing,
     so you never trip a provider's rate limit in the first place.
  3. Adaptive (AIMD) pacing that tunes itself to what the provider tolerates.
  4. A circuit breaker that fails fast while a provider is down.

This example uses a fake clock so it runs offline and instantly — every "wait"
below is simulated, not real time.

Run from the repo root:
    python examples/resilience.py
"""
from autarch import (
    CircuitBreaker,
    ListSink,
    ModelUnavailable,
    RateLimit,
    RateLimited,
    Resilient,
    RetryPolicy,
    make_resilient,
)
from autarch.intelligence.base import ModelProvider
from autarch.intelligence.factory import build_provider
from autarch.resilience import _Limiter


def banner(title):
    print("\n" + "=" * 66)
    print(title)
    print("=" * 66)


class FakeClock:
    """A clock whose 'sleep' advances simulated time instead of blocking."""

    def __init__(self, start=0.0):
        self.now = start

    def time(self):
        return self.now

    def sleep(self, dt):
        self.now += max(0.0, dt)


class FlakyProvider(ModelProvider):
    """A stand-in model that fails a few times before succeeding."""

    def __init__(self, *outcomes):
        self.name = "flaky"
        self._outcomes = list(outcomes)
        self.calls = 0

    def complete(self, prompt, system=None):
        self.calls += 1
        item = self._outcomes.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


# --------------------------------------------------------------------------- #
# 1. Retry rides through transient failures                                    #
# --------------------------------------------------------------------------- #
banner("1. Retry — two transient failures, then success (zero dev code)")
clk = FakeClock()
provider = FlakyProvider(ModelUnavailable("502"), ModelUnavailable("timeout"), '{"ok": true}')
sink = ListSink()
resilient = Resilient(
    provider,
    retry=RetryPolicy(max_attempts=4, base_delay=1.0, jitter=False),
    sink=sink,
    clock=clk.time,
    sleep=clk.sleep,
)
result = resilient.complete("do the thing")
print(f"result          : {result}")
print(f"provider calls  : {provider.calls}  (2 failed, 1 succeeded)")
print(f"simulated wait  : {clk.now:.1f}s of backoff")
print(f"events          : {sink.kinds()}")


# --------------------------------------------------------------------------- #
# 2. Token-aware queue: never exceed a provider's rate limit                   #
# --------------------------------------------------------------------------- #
banner("2. Proactive rate limiting — wait for quota, never fail on it")
clk = FakeClock()
# Tell Autarch the provider's published limit once; it stays under it.
limiter = _Limiter(RateLimit(requests_per_minute=60), clk.time, clk.sleep)  # 1/sec
for i in range(63):
    before = clk.now
    limiter.await_capacity(0)
    waited = clk.now - before
    if i in (0, 59, 60, 61, 62):
        note = "instant (burst)" if waited == 0 else f"waited {waited:.2f}s for quota"
        print(f"request {i + 1:>2}      : {note}")
print("-> the 61st+ requests are paced automatically; none ever error out.")


# --------------------------------------------------------------------------- #
# 3. Adaptive pacing (AIMD) tunes itself to the provider                       #
# --------------------------------------------------------------------------- #
banner("3. Adaptive control — back off on throttle, recover on success")
clk = FakeClock()
limiter = _Limiter(
    RateLimit(requests_per_minute=600, backoff_factor=0.5, recover_step=0.1, min_fraction=0.1),
    clk.time,
    clk.sleep,
)
print(f"effective rate start : {limiter._fraction:.0%}")
limiter.on_throttle(None)
print(f"after one throttle   : {limiter._fraction:.0%}  (halved — slowing down)")
limiter.on_throttle(None)
print(f"after two throttles  : {limiter._fraction:.0%}")
for _ in range(20):
    limiter.on_success()
print(f"after steady success : {limiter._fraction:.0%}  (recovered to full speed)")


# --------------------------------------------------------------------------- #
# 4. Circuit breaker: fail fast while a provider is down                       #
# --------------------------------------------------------------------------- #
banner("4. Circuit breaker — stop hammering a dead endpoint")
clk = FakeClock()
provider = FlakyProvider(*([ModelUnavailable("down")] * 10))
sink = ListSink()
resilient = Resilient(
    provider,
    retry=RetryPolicy(max_attempts=1, jitter=False),
    breaker=CircuitBreaker(failure_threshold=3, cooldown=30.0),
    sink=sink,
    clock=clk.time,
    sleep=clk.sleep,
)
for attempt in range(5):
    try:
        resilient.complete("ping")
        outcome = "ok"
    except RateLimited:
        outcome = "rate limited"
    except ModelUnavailable:
        outcome = "provider error (call made)"
    except Exception as exc:  # CircuitOpen
        outcome = f"FAST-FAIL: {type(exc).__name__} (no call made)"
    print(f"call {attempt + 1}: {outcome}")
print(f"\nactual provider calls : {provider.calls}  (breaker opened after 3 failures)")
print(f"circuit state         : {resilient.circuit_state}")


# --------------------------------------------------------------------------- #
# 5. The point: it is automatic                                                #
# --------------------------------------------------------------------------- #
banner("5. Zero config — network providers are wrapped automatically")
auto = build_provider("ollama:llama3")
print(f"build_provider('ollama:llama3') -> {type(auto).__name__} wrapping '{auto.name}'")
print("Every council member, challenger, and judge inherits this for free.")
print("To add proactive rate limiting for a cloud model, wrap it once:")
print('    make_resilient(MyApiProvider(), rate=RateLimit(requests_per_minute=3500,')
print("                                                    tokens_per_minute=90_000))")

print("\nResilience is a *consequence* the framework governs for you —")
print("so your agents bend under load instead of breaking.")
