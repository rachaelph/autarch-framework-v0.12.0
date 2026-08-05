"""Governed orchestration — a master decomposes work, spawns safe children, and
synthesizes one answer. Fully offline (mock provider + deterministic planner).

    python examples/orchestration.py

The point: this is the supervisor/worker pattern everyone builds — but here every
child is capability-attenuated, tool-isolated, budget-bounded, and signed into one
audit ledger. Three scenarios show the governed lifecycle, parallel execution with
per-child signed sub-chains, and a safety gate that refuses an unsafe fleet.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from autarch import (
    Agent,
    GovernanceError,
    Invariant,
    ListSink,
    Orchestrator,
    SpecialistRegistry,
    capability,
)


def scenario_lifecycle() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="autarch_orch_"))
    events = ListSink()
    master = Agent(
        "coordinate the report workflow",
        grants=[capability("file.write"), capability("file.read")],  # NOT delete
        workspace=workspace,
        events=events,
    )
    intent = (
        "create a file called report.txt that says quarterly numbers look strong "
        "then read report.txt "
        "then delete report.txt"
    )
    print("=" * 70)
    print("[1] GOVERNED LIFECYCLE  (master -> children -> synthesis)")
    print("=" * 70)
    print(f"\nIntent:\n  {intent}\n")

    result = Orchestrator(master).run(intent)

    print("Plan (decomposed subtasks):")
    for i, task in enumerate(result.plan.subtasks, 1):
        print(f"  {i}. {task.description}   [requests: {task.grants[0].name}]")

    print("\nGoverned execution (each child confined to its delegated authority):")
    for i, child in enumerate(result.children, 1):
        status = "done   " if child.executed else "BLOCKED"
        dropped = ", ".join(g.name for g in child.dropped_grants)
        note = f"  (governance withheld: {dropped})" if dropped else ""
        print(f"  {status} {i}. {child.subtask.description}{note}")

    print("\nMaster's unified synthesis:")
    for line in result.synthesis.splitlines():
        print(f"  {line}")
    print(f"\n  {result.executed_count}/{len(result.children)} executed; the delete "
          "was refused by governance, not by trust.")


def scenario_parallel() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="autarch_orch_par_"))
    master = Agent(
        "fan out research", grants=[capability("file.write")], workspace=workspace,
    )
    intent = "create a.txt that says alpha and create b.txt that says beta and create c.txt that says gamma"
    print("\n" + "=" * 70)
    print("[2] PARALLEL FLEET  (independent children run concurrently)")
    print("=" * 70)

    result = Orchestrator(master, max_parallel=3).run(intent)
    ok, _ = master.memory.verify_chain()
    origins = [o for o in master.memory.origins() if ":" in o]
    print(f"\n  {result.executed_count}/{len(result.children)} children executed in parallel")
    print(f"  each wrote its own signed sub-chain: {len(origins)} distinct origins")
    print(f"  merged tamper-evident ledger still verifies: {ok}")


def scenario_guarantee_gate() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="autarch_orch_gate_"))
    master = Agent(
        "handle sensitive files", grants=[capability("file.write")], workspace=workspace,
    )
    print("\n" + "=" * 70)
    print("[3] GUARANTEE GATE  (prove the fleet safe BEFORE it runs)")
    print("=" * 70)

    # Require that no child can ever write — but the master holds file.write, so
    # the invariant fails and the whole orchestration is refused up front.
    guard = Orchestrator(master, guarantees=[Invariant.forbid("file.write")])
    try:
        guard.run("create x.txt that says hi")
        print("  (unexpected) orchestration ran")
    except GovernanceError as exc:
        print(f"\n  refused before spawning any child:\n    {exc.message}")

    # The same fleet with a satisfiable invariant proceeds normally.
    safe = Orchestrator(master, guarantees=[Invariant.forbid("file.delete")])
    result = safe.run("create y.txt that says ok")
    print(f"\n  with a holding invariant (forbid delete), it runs: "
          f"{result.executed_count}/{len(result.children)} executed")


def main() -> None:
    scenario_lifecycle()
    scenario_parallel()
    scenario_guarantee_gate()
    print("\n" + "=" * 70)
    print("Dynamic multi-agent orchestration — attenuated, isolated, budgeted,")
    print("provable, and fully audited. The safe version of master-child.")
    print("=" * 70)


if __name__ == "__main__":
    main()

