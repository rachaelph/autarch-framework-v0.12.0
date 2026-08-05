"""Parallel council tests — voices query their models concurrently.

The council polls N providers in parallel (stdlib threads), so deliberation
latency is the slowest model, not the sum — while order and determinism are
preserved.
"""
import json
import time

from autarch.council.deliberation import Council
from autarch.contracts import Intent, Verdict
from autarch.intelligence.base import ModelProvider
from autarch.intelligence.mock import MockProvider

CAPS = ["file.read", "file.write", "file.delete"]


class SlowProvider(ModelProvider):
    """A provider that blocks (like a real network call) for `delay` seconds."""

    def __init__(self, name, delay=0.2):
        self.name = name
        self.delay = delay

    def complete(self, prompt, system=None):
        time.sleep(self.delay)
        if "ROLE: PROPOSER" in prompt:
            return json.dumps({"capability": "file.write", "params": {"path": "a.txt"}, "rationale": "r"})
        return json.dumps({"verdict": "approve", "reasons": "ok"})


def test_parallel_is_faster_than_sequential():
    providers = [SlowProvider(f"s{i}", delay=0.2) for i in range(3)]
    council = Council(providers, CAPS, max_workers=8)
    start = time.monotonic()
    council.deliberate(Intent("write a.txt"))
    elapsed = time.monotonic() - start
    # Two rounds (propose + critique) of 3 parallel 0.2s calls ~ 0.4s, far under
    # the ~1.2s a sequential council would take.
    assert elapsed < 0.9


def test_sequential_path_when_max_workers_one():
    providers = [SlowProvider(f"s{i}", delay=0.15) for i in range(3)]
    council = Council(providers, CAPS, max_workers=1)
    start = time.monotonic()
    council.deliberate(Intent("write a.txt"))
    elapsed = time.monotonic() - start
    # Sequential: 6 calls * 0.15s ~ 0.9s, clearly more than the parallel path.
    assert elapsed > 0.7


def test_parallel_preserves_determinism():
    # Two voices proposing different actions; tie resolves to insertion order.
    p1 = MockProvider(name="p1", scripted={
        "ROLE: PROPOSER": json.dumps({"capability": "file.write", "params": {"path": "a.txt"}, "rationale": "r1"}),
        "ROLE: CHALLENGER": json.dumps({"verdict": "approve", "reasons": "ok"}),
    })
    p2 = MockProvider(name="p2", scripted={
        "ROLE: PROPOSER": json.dumps({"capability": "file.read", "params": {"path": "a.txt"}, "rationale": "r2"}),
        "ROLE: CHALLENGER": json.dumps({"verdict": "revise", "reasons": "hmm"}),
    })
    council = Council([p1, p2], CAPS, max_workers=8)
    first = council.deliberate(Intent("do something"))
    for _ in range(20):
        again = council.deliberate(Intent("do something"))
        assert again.motion.capability == first.motion.capability
        assert again.motion.params == first.motion.params
        assert again.tally == first.tally
        assert again.voices == first.voices  # order stable


def test_parallel_and_sequential_agree():
    providers = [
        MockProvider(name="bold", persona="bold"),
        MockProvider(name="cautious", persona="cautious"),
    ]
    par = Council(providers, CAPS, max_workers=8).deliberate(Intent("delete the file x.txt"))
    seq = Council(providers, CAPS, max_workers=1).deliberate(Intent("delete the file x.txt"))
    assert par.recommendation == seq.recommendation
    assert par.tally == seq.tally
    assert par.motion.capability == seq.motion.capability


class _Boom(ModelProvider):
    name = "boom"

    def complete(self, prompt, system=None):
        raise RuntimeError("down")


def test_failing_voice_in_parallel_set_abstains():
    # A failing provider must not crash the parallel round; it abstains.
    good = MockProvider(name="good")
    council = Council([good, _Boom()], CAPS, max_workers=8)
    delib = council.deliberate(Intent("write notes.txt that says hi"))
    assert delib.motion is not None  # the healthy voice carried it
    # The failing voice's critique fails closed (revise), so not a clean approve.
    assert delib.recommendation in (Verdict.REVISE.value, Verdict.VETO.value)
