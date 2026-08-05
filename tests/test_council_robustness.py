"""Council robustness — real models are messy. The council must fail CLOSED.

These simulate the kinds of output a live model produces (prose, fences, invalid
verdicts, outright failures) WITHOUT needing Ollama, so the safety guarantees are
verified deterministically.
"""
import json

from autarch.council.deliberation import Council
from autarch.contracts import Intent, Verdict
from autarch.intelligence.base import ModelProvider
from autarch.intelligence.mock import MockProvider


CAPS = ["file.read", "file.write", "file.delete"]


class RaisingProvider(ModelProvider):
    """A model that always errors — e.g. Ollama not running."""

    name = "boom"

    def complete(self, prompt, system=None):
        raise RuntimeError("connection refused")


def _scripted(proposer=None, challenger=None, name="m"):
    scripted = {}
    if proposer is not None:
        scripted["ROLE: PROPOSER"] = proposer
    if challenger is not None:
        scripted["ROLE: CHALLENGER"] = challenger
    return MockProvider(name=name, scripted=scripted)


def test_unparseable_critique_fails_closed_to_revise():
    # Proposer is clean; challenger rambles with no JSON -> must default to revise,
    # never silently approve.
    provider = _scripted(
        proposer=json.dumps({"capability": "file.write", "params": {"path": "a.txt"}, "rationale": "r"}),
        challenger="Hmm, I think this is probably fine but I'm not totally sure...",
    )
    delib = Council([provider], CAPS).deliberate(Intent("write a.txt"))
    assert delib.recommendation == Verdict.REVISE.value
    assert delib.has_disagreement is True


def test_invalid_verdict_fails_closed_to_revise():
    provider = _scripted(
        proposer=json.dumps({"capability": "file.write", "params": {}, "rationale": "r"}),
        challenger=json.dumps({"verdict": "maybe", "reasons": "unsure"}),
    )
    delib = Council([provider], CAPS).deliberate(Intent("write a.txt"))
    assert delib.recommendation == Verdict.REVISE.value


def test_fenced_proposer_output_is_parsed():
    provider = _scripted(
        proposer='```json\n{"capability": "file.read", "params": {"path": "a.txt"}}\n```',
        challenger=json.dumps({"verdict": "approve", "reasons": "fine"}),
    )
    delib = Council([provider], CAPS).deliberate(Intent("read a.txt"))
    assert delib.motion.capability == "file.read"
    assert delib.recommendation == Verdict.APPROVE.value


def test_prose_wrapped_proposer_output_is_parsed():
    provider = _scripted(
        proposer='Sure, here you go: {"capability": "file.write", "params": {"path": "b.txt"}} done.',
        challenger=json.dumps({"verdict": "approve", "reasons": "ok"}),
    )
    delib = Council([provider], CAPS).deliberate(Intent("write b.txt"))
    assert delib.motion.capability == "file.write"
    assert delib.motion.params["path"] == "b.txt"


def test_failing_proposer_abstains_not_crashes():
    delib = Council([RaisingProvider()], CAPS).deliberate(Intent("do something"))
    assert delib.motion is None
    assert delib.recommendation == Verdict.VETO.value  # nothing safe to do


def test_failing_challenger_fails_closed():
    # Proposer (mock) yields a motion; the challenger model errors -> revise.
    good_proposer = MockProvider(name="proposer")
    broken = RaisingProvider()
    council = Council([good_proposer, broken], CAPS)
    delib = council.deliberate(Intent("write notes.txt that says hi"))
    assert delib.motion is not None
    # At least one voice could not vouch for safety -> not a clean approve.
    assert delib.recommendation in (Verdict.REVISE.value, Verdict.VETO.value)
    assert delib.has_disagreement is True


def test_proposer_returns_non_object_abstains():
    provider = _scripted(
        proposer="[1, 2, 3]",  # array, not an action object
        challenger=json.dumps({"verdict": "approve", "reasons": "ok"}),
    )
    delib = Council([provider], CAPS).deliberate(Intent("do x"))
    assert delib.motion is None
