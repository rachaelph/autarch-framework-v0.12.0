"""Tests for multi-round council debate (voices responding to each other)."""
import json

from autarch.council.deliberation import Council
from autarch.contracts import Intent, Verdict
from autarch.intelligence.base import ModelProvider


class Scripted(ModelProvider):
    """A provider whose critique and rebuttal behaviour are scripted."""

    def __init__(self, name, first_verdict, rebut_to=None, rebut_when=None):
        self.name = name
        self._first = first_verdict
        self._rebut_to = rebut_to
        self._rebut_when = rebut_when  # substring that triggers the change

    def complete(self, prompt, system=None):
        if "rebuttal round" in prompt:
            if self._rebut_to and (self._rebut_when is None or self._rebut_when in prompt):
                return json.dumps({"verdict": self._rebut_to, "reasons": "reconsidered"})
            return json.dumps({"verdict": self._first, "reasons": "holding"})
        if "ROLE: CHALLENGER" in prompt:
            return json.dumps({"verdict": self._first, "reasons": "initial"})
        return json.dumps({
            "capability": "file.write",
            "params": {"path": "a.txt", "content": "x"},
            "rationale": "proceed",
        })


def _council(*voices):
    return Council(list(voices), capabilities=["file.write"], max_workers=1)


def test_debate_records_transcript_per_round():
    c = _council(
        Scripted("swayable", "approve", rebut_to="revise", rebut_when="veto"),
        Scripted("hawk", "veto"),
    )
    d = c.debate(Intent("write a file"), debate_rounds=2)
    assert len(d.transcript) >= 2
    # round 1: swayable approves; hawk vetoes
    assert d.transcript[0]["swayable"][0] == "approve"
    # after debate: swayable moved to revise, hawk holds veto -> recommendation veto
    assert d.recommendation == Verdict.VETO.value


def test_debate_stops_early_on_consensus():
    c = _council(
        Scripted("a", "approve"),
        Scripted("b", "approve"),
    )
    d = c.debate(Intent("write a file"), debate_rounds=5)
    # unanimous from the start: no extra rebuttal rounds recorded
    assert len(d.transcript) == 1
    assert d.recommendation == Verdict.APPROVE.value


def test_debate_stabilizes_and_halts():
    # nobody changes their mind -> positions stabilize, loop halts (no infinite run)
    c = _council(
        Scripted("a", "approve"),
        Scripted("b", "veto"),
    )
    d = c.debate(Intent("write a file"), debate_rounds=10)
    assert len(d.transcript) <= 3  # round1 + one rebuttal that stabilizes


def test_single_round_when_debate_disabled():
    c = _council(Scripted("a", "approve"), Scripted("b", "veto"))
    d = c.debate(Intent("x"), debate_rounds=0)
    assert d.transcript == []
