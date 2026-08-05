"""Durable run journal tests — crash-safe, resumable, no double-execution."""
from autarch import Agent, RunJournal, capability
from autarch.adapters.base import Adapter
from autarch.contracts import ActionResult
from autarch.runlog import STATUS_BLOCKED, STATUS_COMPLETE, STATUS_RUNNING


class CountingAdapter(Adapter):
    """Records how many times a side effect actually executes."""

    name = "counter"

    def __init__(self):
        self.calls = 0

    def capabilities(self):
        return ["file.write", "file.read", "file.move", "file.delete"]

    def execute(self, action):
        self.calls += 1
        return ActionResult(True, output=f"executed #{self.calls}")


# --- journal primitives ---------------------------------------------------

def test_start_and_get(tmp_path):
    j = RunJournal(tmp_path / "runs.db")
    j.start("run_1", "do x")
    state = j.get("run_1")
    assert state.status == STATUS_RUNNING
    assert state.step == "created"
    assert state.is_terminal is False


def test_start_is_idempotent(tmp_path):
    j = RunJournal(tmp_path / "runs.db")
    j.start("run_1", "do x")
    j.record_step("run_1", "deliberated")
    j.start("run_1", "do x AGAIN")  # must not overwrite
    assert j.get("run_1").step == "deliberated"


def test_record_step_merges_payload(tmp_path):
    j = RunJournal(tmp_path / "runs.db")
    j.start("run_1", "do x")
    j.record_step("run_1", "decided", data={"decision": "ratify"})
    j.record_step("run_1", "complete", STATUS_COMPLETE, why_id="why_9", data={"k": "v"})
    state = j.get("run_1")
    assert state.status == STATUS_COMPLETE
    assert state.why_id == "why_9"
    assert state.payload["decision"] == "ratify"
    assert state.payload["k"] == "v"
    assert state.is_terminal is True


def test_unfinished_excludes_terminal(tmp_path):
    j = RunJournal(tmp_path / "runs.db")
    j.start("a", "x")
    j.start("b", "y")
    j.record_step("b", "complete", STATUS_COMPLETE, why_id="why_b")
    unfinished = [s.run_id for s in j.unfinished()]
    assert "a" in unfinished and "b" not in unfinished


# --- agent durability -----------------------------------------------------

def test_run_journals_terminal_state(tmp_path):
    j = RunJournal(tmp_path / "runs.db")
    agent = Agent(
        intent="create a.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path, run_id="run_x", journal=j,
    )
    result = agent.run()
    state = j.get("run_x")
    assert state.status == STATUS_COMPLETE
    assert state.why_id == result.why_id


def test_resume_does_not_re_execute(tmp_path):
    j = RunJournal(tmp_path / "runs.db")
    counter = CountingAdapter()
    agent = Agent(
        intent="create a.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path, adapters=[counter], run_id="run_y", journal=j,
    )
    first = agent.run()
    assert counter.calls == 1

    # A fresh agent resumes the same run id (orchestrator restart).
    counter2 = CountingAdapter()
    resumed = Agent(
        intent="create a.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path, adapters=[counter2], journal=j,
    ).resume("run_y")

    assert resumed.why_id == first.why_id      # same recorded outcome
    assert resumed.executed is True
    assert counter2.calls == 0                 # the side effect was NOT repeated


def test_calling_run_twice_same_id_is_idempotent(tmp_path):
    j = RunJournal(tmp_path / "runs.db")
    counter = CountingAdapter()
    agent = Agent(
        intent="create a.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path, adapters=[counter], run_id="run_z", journal=j,
    )
    agent.run()
    agent.run()  # second call returns cached result
    assert counter.calls == 1


def test_blocked_run_is_journaled_blocked(tmp_path):
    j = RunJournal(tmp_path / "runs.db")
    # No delete grant -> the action is blocked, not executed.
    (tmp_path / "victim.txt").write_text("data")
    agent = Agent(
        intent="delete the file victim.txt", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path, run_id="run_b", journal=j,
    )
    result = agent.run()
    assert result.executed is False
    assert j.get("run_b").status == STATUS_BLOCKED


def test_journal_true_auto_creates_store(tmp_path):
    agent = Agent(
        intent="create a.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path, journal=True,
    )
    assert agent.journal is not None
    result = agent.run()
    assert agent.journal.get(agent.run_id).why_id == result.why_id


def test_no_journal_by_default(tmp_path):
    agent = Agent(
        intent="create a.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path,
    )
    assert agent.journal is None
