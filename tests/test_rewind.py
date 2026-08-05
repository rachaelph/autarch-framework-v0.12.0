"""Rewind tests — governed, audited reversal of past actions."""
from autarch import Agent, capability
from autarch.adapters.filesystem import FileSystemAdapter
from autarch.contracts import HumanDecision
from autarch.kernel import CapabilityKernel
from autarch.rewind import Rewinder, parse_duration, undo_to_action


def test_parse_duration():
    assert parse_duration("30s") == 30
    assert parse_duration("5m") == 300
    assert parse_duration("2h") == 7200
    assert parse_duration("1 hour") == 3600
    assert parse_duration("30 minutes") == 1800
    assert parse_duration("1d") == 86400


def test_undo_to_action_mapping():
    write = undo_to_action({"capability": "file.write", "path": "a.txt", "restore": "old"})
    assert write.capability == "file.write" and write.params["content"] == "old"
    delete = undo_to_action({"capability": "file.delete", "path": "a.txt", "restore": None})
    assert delete.capability == "file.delete"
    move = undo_to_action({"capability": "file.move", "path": "b.txt", "dest": "a.txt"})
    assert move.capability == "file.move" and move.params["dest"] == "a.txt"
    assert undo_to_action({"capability": "tool.search"}) is None


def _file_rewinder(workspace, memory):
    grants = [
        capability("file.read", scope={"path_prefix": "."}),
        capability("file.write", scope={"path_prefix": "."}),
        capability("file.move", scope={"path_prefix": "."}),
        capability("file.delete", scope={"path_prefix": "."}),
    ]
    adapter = FileSystemAdapter(workspace)
    by_cap = {c: adapter for c in adapter.capabilities()}
    return Rewinder(memory, CapabilityKernel(grants), by_cap)


def test_rewind_undoes_a_created_file(tmp_path):
    agent = Agent(
        intent="create notes.txt that says hello",
        council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path,
    )
    result = agent.run()
    assert result.executed is True
    assert (tmp_path / "notes.txt").exists()

    rewinder = _file_rewinder(tmp_path, agent.memory)
    records = rewinder.candidates(last=1)
    assert len(records) == 1
    steps = rewinder.rewind(records)

    assert steps[0].executed is True
    # Undo of a creation is a deletion -> the file is gone.
    assert not (tmp_path / "notes.txt").exists()


def test_rewind_is_itself_recorded(tmp_path):
    agent = Agent(
        intent="create a.txt that says hi",
        council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path,
    )
    agent.run()
    before = len(agent.memory.all())

    rewinder = _file_rewinder(tmp_path, agent.memory)
    steps = rewinder.rewind(rewinder.candidates(last=1))

    after = agent.memory.all()
    assert len(after) == before + 1
    rewind_record = agent.memory.get(steps[0].new_why_id)
    assert rewind_record.rewind_of == steps[0].original_id
    assert rewind_record.proposer == "system:rewind"


def test_rewind_keep_capability_preserves_it(tmp_path):
    grants = [
        capability("file.write", scope={"path_prefix": "."}),
        capability("file.move", scope={"path_prefix": "."}),
    ]
    # Create a file (write), then move it.
    Agent(intent="create a.txt that says hi", council=["mock"], grants=grants, workspace=tmp_path).run()
    mover = Agent(intent="move a.txt to b.txt", council=["mock"], grants=grants, workspace=tmp_path)
    mover.run()

    rewinder = _file_rewinder(tmp_path, mover.memory)
    # Keep writes; only reverse the move.
    records = rewinder.candidates(since_seconds=3600, keep_capabilities={"file.write"})
    caps = {r.capability for r in records}
    assert "file.move" in caps
    assert "file.write" not in caps


def test_rewind_does_not_pick_up_prior_rewinds(tmp_path):
    agent = Agent(
        intent="create a.txt that says hi",
        council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path,
    )
    agent.run()
    rewinder = _file_rewinder(tmp_path, agent.memory)
    rewinder.rewind(rewinder.candidates(last=1))  # produces a rewind record

    # A second candidates() call must not select the rewind action itself.
    again = rewinder.candidates(last=5)
    assert all(r.rewind_of == "" for r in again)
