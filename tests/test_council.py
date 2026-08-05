"""Council tests — proposer + challenger deliberation with the mock provider."""
from autarch.council.deliberation import Council
from autarch.contracts import Intent
from autarch.intelligence.mock import MockProvider


def _council():
    return Council([MockProvider()], capabilities=["file.read", "file.write", "file.delete"])


def test_proposes_write_for_create_intent():
    delib = _council().deliberate(Intent("create a file called notes.txt that says hi"))
    assert delib.action is not None
    assert delib.action.capability == "file.write"
    assert delib.action.params["path"] == "notes.txt"
    assert delib.action.params["content"] == "hi"


def test_challenger_approves_low_risk():
    delib = _council().deliberate(Intent("write a file report.md that says done"))
    assert delib.recommendation == "approve"
    assert delib.has_disagreement is False


def test_challenger_flags_delete():
    delib = _council().deliberate(Intent("delete the file secret.txt"))
    assert delib.action.capability == "file.delete"
    assert delib.recommendation == "revise"
    assert delib.has_disagreement is True


def test_proposes_read_for_show_intent():
    delib = _council().deliberate(Intent("show me the file data.csv"))
    assert delib.action.capability == "file.read"
    assert delib.action.params["path"] == "data.csv"
