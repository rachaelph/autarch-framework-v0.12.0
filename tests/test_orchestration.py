"""Governed orchestration — Phase 1 core (deterministic, offline).

Covers the master-child lifecycle (decompose -> provision -> execute ->
synthesize) and the governance that makes Autarch's version distinct: a child
can never exceed its master, tools are isolated per subtask, and every handoff is
observable. Everything here runs offline with the mock provider + RulePlanner.
"""
from __future__ import annotations

from autarch import (
    Agent,
    ConcatSynthesizer,
    ListSink,
    Orchestrator,
    Plan,
    RulePlanner,
    Subtask,
    capability,
)
from autarch.orchestration import ChildResult


def _master(tmp_path, grants, events=None):
    return Agent(
        "orchestrate",
        grants=grants,
        workspace=tmp_path,
        events=events,
    )


# -- RulePlanner: task decomposition ------------------------------------------
def test_rule_planner_splits_on_connectives():
    plan = RulePlanner().decompose("create report.txt then read report.txt")
    assert isinstance(plan, Plan)
    assert [t.description for t in plan.subtasks] == ["create report.txt", "read report.txt"]


def test_rule_planner_single_clause_is_one_subtask():
    plan = RulePlanner().decompose("read the config file")
    assert len(plan.subtasks) == 1


def test_rule_planner_infers_capability_and_grants():
    plan = RulePlanner().decompose("create notes.txt and delete temp.txt")
    caps = [t.grants[0].name for t in plan.subtasks]
    assert caps == ["file.write", "file.delete"]
    assert plan.subtasks[0].tools == ["file.write"]


# -- Plan waves: dependency ordering ------------------------------------------
def test_plan_waves_respect_dependencies():
    a = Subtask(description="first", id="task_a")
    b = Subtask(description="second", depends_on=["task_a"], id="task_b")
    plan = Plan(intent="x", subtasks=[b, a])  # deliberately out of order
    waves = plan.waves()
    assert [t.id for t in waves[0]] == ["task_a"]
    assert [t.id for t in waves[1]] == ["task_b"]


def test_plan_waves_break_cycles_gracefully():
    a = Subtask(description="a", depends_on=["task_b"], id="task_a")
    b = Subtask(description="b", depends_on=["task_a"], id="task_b")
    waves = Plan(intent="x", subtasks=[a, b]).waves()
    # A cycle must not hang; the remainder is flushed as a final wave.
    assert sum(len(w) for w in waves) == 2


# -- Orchestrator lifecycle ---------------------------------------------------
def test_orchestrator_runs_children_and_synthesizes(tmp_path):
    master = _master(tmp_path, [capability("file.write"), capability("file.read")])
    result = Orchestrator(master).run(
        "create a file called report.txt that says numbers look strong then read report.txt"
    )
    assert result.executed_count >= 1
    assert "report.txt" in result.synthesis
    assert len(result.children) == 2
    assert all(isinstance(c, ChildResult) for c in result.children)


def test_orchestrator_default_run_uses_master_intent(tmp_path):
    master = Agent(
        "create hello.txt that says hi",
        grants=[capability("file.write")],
        workspace=tmp_path,
    )
    result = Orchestrator(master).run()
    assert result.plan.intent == "create hello.txt that says hi"
    assert result.executed_count == 1


# -- Governance: a child can never exceed its master --------------------------
def test_child_cannot_exceed_master_authority(tmp_path):
    # Master may write/read but NOT delete. A "delete" subtask is dropped at
    # provision, and even if proposed, the kernel denies it — nothing is deleted.
    master = _master(tmp_path, [capability("file.write"), capability("file.read")])
    result = Orchestrator(master).run(
        "create report.txt that says hi then delete report.txt"
    )
    by_desc = {c.subtask.description: c for c in result.children}
    delete_child = by_desc["delete report.txt"]
    assert delete_child.executed is False
    assert any(g.name == "file.delete" for g in delete_child.dropped_grants)


def test_provision_attenuates_and_isolates_tools(tmp_path):
    master = _master(tmp_path, [capability("file.read")])
    orch = Orchestrator(master)
    # Subtask asks to write (master lacks it) and gets the fs adapter for reading.
    child = orch.provision(Subtask(description="read data", grants=[capability("file.read")], tools=["file.read"]))
    assert [g.name for g in child.grants] == ["file.read"]
    # Tool isolation: a subtask that declares no tools gets no adapters.
    bare = orch.provision(Subtask(description="think", tools=[]))
    assert bare.adapters == []


def test_tool_isolation_excludes_unrequested_adapters(tmp_path):
    from autarch import FileSystemAdapter, from_callables

    calc = from_callables({"add": lambda a, b: a + b})  # a separate tool adapter
    master = Agent(
        "orchestrate",
        grants=[capability("file.write"), capability("tool.add")],
        workspace=tmp_path,
        adapters=[FileSystemAdapter(tmp_path), calc],
    )
    orch = Orchestrator(master)
    child = orch.provision(Subtask(description="write x", grants=[capability("file.write")], tools=["file.write"]))
    child_caps = {c for a in child.adapters for c in a.capabilities()}
    assert "file.write" in child_caps
    assert "tool.add" not in child_caps  # the calculator tool was withheld


# -- Observability: every handoff is emitted ----------------------------------
def test_orchestration_emits_lifecycle_events(tmp_path):
    sink = ListSink()
    master = _master(tmp_path, [capability("file.write")], events=sink)
    Orchestrator(master).run("create a.txt that says hi and create b.txt that says yo")
    kinds = sink.kinds()
    assert "orchestration.decomposed" in kinds
    assert kinds.count("orchestration.child_spawned") == 2
    assert kinds.count("orchestration.child_complete") == 2
    assert "orchestration.synthesized" in kinds


# -- Synthesis: master reports all children, blocked included -----------------
def test_concat_synthesizer_reports_blocked_children(tmp_path):
    master = _master(tmp_path, [capability("file.read")])  # no write/delete
    result = Orchestrator(master, synthesizer=ConcatSynthesizer()).run(
        "delete secret.txt and read notes.txt"
    )
    assert "[blocked]" in result.synthesis  # the denied delete surfaces honestly


def test_children_report_to_master_not_user(tmp_path):
    # The only unified answer is the master's synthesis; children are captured as
    # structured results, never surfaced independently.
    master = _master(tmp_path, [capability("file.write")])
    result = Orchestrator(master).run("create x.txt that says hi and create y.txt that says yo")
    assert isinstance(result.synthesis, str)
    assert len(result.children) == 2
    assert result.why_ids  # each child action was recorded in the signed ledger


# -- Phase 2: model-backed planner & synthesizer (fail-closed) ----------------
def _scripted():
    from autarch.intelligence.mock import MockProvider

    return MockProvider(scripted={
        "ROLE: PLANNER": (
            '{"subtasks": ['
            '{"description": "write the report", "capability": "file.write"},'
            '{"description": "read the report", "capability": "file.read"}]}'
        ),
        "ROLE: SYNTHESIZER": '{"summary": "Wrote and then read the report."}',
    })


def test_model_planner_parses_scripted_json(tmp_path):
    from autarch import ModelPlanner

    plan = ModelPlanner(_scripted()).decompose("build the report", ["file.write", "file.read"])
    assert [t.description for t in plan.subtasks] == ["write the report", "read the report"]
    assert [t.grants[0].name for t in plan.subtasks] == ["file.write", "file.read"]


def test_model_planner_fails_closed_to_fallback(tmp_path):
    from autarch import ModelPlanner
    from autarch.intelligence.mock import MockProvider

    # A plain mock returns "{}" for an unknown prompt -> empty plan -> fallback.
    plan = ModelPlanner(MockProvider()).decompose("create a.txt then read a.txt")
    assert [t.description for t in plan.subtasks] == ["create a.txt", "read a.txt"]


def test_model_planner_fails_closed_on_model_error():
    from autarch import ModelPlanner

    class Boom:
        name = "boom"

        def complete(self, prompt, system=None):
            raise RuntimeError("model down")

    plan = ModelPlanner(Boom()).decompose("read the file")
    assert len(plan.subtasks) == 1  # degraded to RulePlanner, not crashed


def test_model_synthesizer_parses_summary(tmp_path):
    from autarch import ModelSynthesizer

    master = _master(tmp_path, [capability("file.write"), capability("file.read")])
    result = Orchestrator(
        master,
        synthesizer=ModelSynthesizer(_scripted()),
    ).run("create a.txt that says hi and read a.txt")
    assert result.synthesis == "Wrote and then read the report."


def test_model_synthesizer_fails_closed_to_concat(tmp_path):
    from autarch import ModelSynthesizer
    from autarch.intelligence.mock import MockProvider

    master = _master(tmp_path, [capability("file.write")])
    result = Orchestrator(
        master,
        synthesizer=ModelSynthesizer(MockProvider()),  # returns "{}" -> fallback
    ).run("create a.txt that says hi")
    assert "[done]" in result.synthesis  # ConcatSynthesizer format


def test_orchestrator_end_to_end_with_model_planner_and_synth(tmp_path):
    from autarch import ModelPlanner, ModelSynthesizer

    prov = _scripted()
    master = _master(tmp_path, [capability("file.write"), capability("file.read")])
    result = Orchestrator(
        master,
        planner=ModelPlanner(prov),
        synthesizer=ModelSynthesizer(prov),
    ).run("build the report")
    assert result.executed_count == 2
    assert result.synthesis == "Wrote and then read the report."


# -- Phase 3: specialists -----------------------------------------------------
def test_specialist_registry_defaults():
    from autarch import SpecialistRegistry

    reg = SpecialistRegistry.defaults()
    assert "researcher" in reg and "writer" in reg
    assert reg.get("writer").tools == ["file.write"]
    assert set(reg.names()) >= {"researcher", "writer", "analyst", "security-reviewer"}


def test_orchestrator_applies_specialist_template(tmp_path):
    from autarch import SpecialistRegistry

    master = _master(tmp_path, [capability("file.write"), capability("file.read")])
    orch = Orchestrator(master, registry=SpecialistRegistry.defaults())
    # The subtask names a specialist but declares no grants/tools of its own.
    child = orch.provision(Subtask(description="write the summary", specialist="writer"))
    assert [g.name for g in child.grants] == ["file.write"]


def test_specialist_read_only_child_cannot_write(tmp_path):
    from autarch import SpecialistRegistry

    master = _master(tmp_path, [capability("file.read")])  # master itself can't write
    orch = Orchestrator(master, registry=SpecialistRegistry.defaults())
    child = orch.provision(Subtask(description="review notes.txt", specialist="security-reviewer"))
    assert [g.name for g in child.grants] == ["file.read"]


# -- Phase 3: parallel execution with isolated signed sub-chains --------------
def test_parallel_execution_runs_all_and_keeps_ledger_intact(tmp_path):
    master = _master(tmp_path, [capability("file.write")])
    orch = Orchestrator(master, max_parallel=3)
    result = orch.run(
        "create a.txt that says A and create b.txt that says B and create c.txt that says C"
    )
    assert result.executed_count == 3
    # Each parallel child wrote to its own signed sub-chain; the merged ledger
    # still verifies, and there are three distinct origins.
    ok, broken = master.memory.verify_chain()
    assert ok and broken is None
    assert len([o for o in master.memory.origins() if ":" in o]) == 3


def test_parallel_matches_sequential_outcome(tmp_path):
    intent = "create a.txt that says A and create b.txt that says B and create c.txt that says C"
    seq = Orchestrator(_master(tmp_path / "seq", [capability("file.write")]), max_parallel=1).run(intent)
    par = Orchestrator(_master(tmp_path / "par", [capability("file.write")]), max_parallel=4).run(intent)
    assert seq.executed_count == par.executed_count == 3
    assert {c.subtask.description for c in seq.children} == {c.subtask.description for c in par.children}


# -- Phase 3: per-child sub-budget --------------------------------------------
def test_per_child_subbudget_blocks_expensive_child(tmp_path):
    master = _master(tmp_path, [capability("file.write")])
    orch = Orchestrator(master)
    # A child capped at zero model calls cannot afford to execute anything.
    child = orch.provision(
        Subtask(
            description="create x.txt that says hi",
            grants=[capability("file.write")],
            tools=["file.write"],
            budget={"calls": 0},
        )
    )
    run_result = child.run()
    assert run_result.executed is False
    assert run_result.budget_decision is not None and not run_result.budget_decision.ok


# -- Phase 3: whole-tree guarantee gate ---------------------------------------
def test_guarantee_gate_blocks_when_invariant_violated(tmp_path):
    import pytest

    from autarch import GovernanceError, Invariant

    master = _master(tmp_path, [capability("file.write")])
    orch = Orchestrator(master, guarantees=[Invariant.forbid("file.write")])
    with pytest.raises(GovernanceError):
        orch.run("create x.txt that says hi")


def test_guarantee_gate_allows_when_invariant_holds(tmp_path):
    from autarch import Invariant

    master = _master(tmp_path, [capability("file.write")])  # cannot delete
    orch = Orchestrator(master, guarantees=[Invariant.forbid("file.delete")])
    result = orch.run("create x.txt that says hi")
    assert result.executed_count == 1


