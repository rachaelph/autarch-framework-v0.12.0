"""Multi-voice council tests — genuine, visible disagreement."""
import json

from autarch.council.deliberation import Council
from autarch.contracts import Intent
from autarch.intelligence.mock import MockProvider


def test_personas_disagree_on_delete():
    # bold approves; cautious vetoes -> most-cautious-wins => veto, with disagreement.
    council = Council(
        [MockProvider(persona="bold"), MockProvider(persona="cautious")],
        capabilities=["file.read", "file.write", "file.delete"],
    )
    delib = council.deliberate(Intent("delete the file secret.txt"))
    assert delib.motion.capability == "file.delete"
    assert delib.tally.get("approve") == 1
    assert delib.tally.get("veto") == 1
    assert delib.recommendation == "veto"
    assert delib.has_disagreement is True
    assert set(delib.voices) == {"mock:bold", "mock:cautious"}


def test_personas_agree_on_write():
    council = Council(
        [MockProvider(persona="bold"), MockProvider(persona="cautious")],
        capabilities=["file.read", "file.write"],
    )
    delib = council.deliberate(Intent("write a file note.txt that says hi"))
    assert delib.motion.capability == "file.write"
    assert delib.tally.get("approve") == 2
    assert delib.recommendation == "approve"
    assert delib.has_disagreement is False


def test_proposal_disagreement_detected():
    # Two voices that propose *different* actions via scripted proposer output.
    p1 = MockProvider(name="p1", scripted={
        "ROLE: PROPOSER": json.dumps(
            {"capability": "file.write", "params": {"path": "a.txt", "content": "x"}, "rationale": "r1"}
        )
    })
    p2 = MockProvider(name="p2", scripted={
        "ROLE: PROPOSER": json.dumps(
            {"capability": "file.read", "params": {"path": "a.txt"}, "rationale": "r2"}
        )
    })
    council = Council([p1, p2], capabilities=["file.read", "file.write"])
    delib = council.deliberate(Intent("do something with a.txt"))
    assert delib.proposal_disagreement is True
    # Tie in support resolves to insertion order -> p1's write is the motion.
    assert delib.motion.capability == "file.write"
    assert delib.has_disagreement is True


def test_exclude_yields_no_motion():
    council = Council([MockProvider()], capabilities=["file.delete"])
    delib = council.deliberate(Intent("delete the file x.txt"), exclude={"file.delete"})
    assert delib.motion is None
    assert delib.recommendation == "veto"
