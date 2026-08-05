"""Delegation tests — Agent.spawn confines sub-agents structurally."""
from autarch import Agent, capability
from autarch.contracts import Action, HumanDecision


def test_spawn_attenuates_scope(tmp_path):
    parent = Agent(
        intent="coordinate",
        council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path,
    )
    child = parent.spawn(
        intent="write a report",
        grants=[capability("file.write", scope={"path_prefix": "reports"})],
    )
    assert len(child.grants) == 1
    assert child.grants[0].scope["path_prefix"] == "reports"
    assert child.grants[0].depth == 1


def test_child_confined_to_delegated_subdir(tmp_path):
    parent = Agent(
        intent="coordinate", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})], workspace=tmp_path,
    )
    child = parent.spawn(intent="report", grants=[capability("file.write", scope={"path_prefix": "reports"})])

    inside = child.kernel.authorize(Action("file.write", {"path": "reports/q1.txt", "content": "hi"}))
    outside = child.kernel.authorize(Action("file.write", {"path": "secret.txt", "content": "x"}))
    assert inside.allowed is True
    assert outside.allowed is False
    assert "outside scope" in outside.reason


def test_child_cannot_gain_undelegated_capability(tmp_path):
    parent = Agent(
        intent="coordinate", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})], workspace=tmp_path,
    )
    # Child asks for delete, which the parent never held -> dropped.
    child = parent.spawn(intent="cleanup", grants=[capability("file.delete")])
    assert child.grants == []
    assert [d.name for d in child.dropped_delegations] == ["file.delete"]
    assert child.kernel.authorize(Action("file.delete", {"path": "a.txt"})).allowed is False


def test_child_cannot_widen_parent_scope(tmp_path):
    # Parent confined to 'out'; child asks for a sibling dir -> dropped.
    parent = Agent(
        intent="coordinate", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "out"})], workspace=tmp_path,
    )
    child = parent.spawn(intent="escape", grants=[capability("file.write", scope={"path_prefix": "elsewhere"})])
    assert child.grants == []
    assert len(child.dropped_delegations) == 1


def test_nested_delegation_shrinks_monotonically(tmp_path):
    parent = Agent(
        intent="root", council=["mock"],
        grants=[capability("file.*", scope={"path_prefix": "."})], workspace=tmp_path,
    )
    child = parent.spawn(intent="mid", grants=[capability("file.write", scope={"path_prefix": "team"})])
    grandchild = child.spawn(intent="leaf", grants=[capability("file.write", scope={"path_prefix": "team/alice"})])

    assert grandchild.grants[0].scope["path_prefix"] == "team/alice"
    assert grandchild.grants[0].depth == 2
    # Grandchild cannot climb back up to the sibling 'team/bob'.
    g = grandchild.spawn(intent="climb", grants=[capability("file.write", scope={"path_prefix": "team/bob"})])
    assert g.grants == []


def test_child_action_executes_and_is_signed_in_shared_ledger(tmp_path):
    parent = Agent(
        intent="coordinate", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."}), capability("file.read")],
        workspace=tmp_path,
    )
    child = parent.spawn(
        intent="create reports/summary.txt that says done",
        grants=[capability("file.write", scope={"path_prefix": "reports"})],
        preside_fn=lambda d, g: HumanDecision.RATIFY.value,
    )
    result = child.run()
    assert result.executed is True
    assert (tmp_path / "reports" / "summary.txt").read_text() == "done"
    # Recorded in the SAME ledger the parent uses (shared memory).
    assert parent.memory.get(result.why_id) is not None


def test_child_blocked_when_exceeding_delegated_scope_at_runtime(tmp_path):
    parent = Agent(
        intent="coordinate", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})], workspace=tmp_path,
    )
    # Child delegated only 'reports', but its intent targets a top-level file.
    child = parent.spawn(
        intent="create secret.txt that says leak",
        grants=[capability("file.write", scope={"path_prefix": "reports"})],
        preside_fn=lambda d, g: HumanDecision.RATIFY.value,
    )
    result = child.run()
    assert result.gate.allowed is False
    assert result.executed is False
    assert not (tmp_path / "secret.txt").exists()
