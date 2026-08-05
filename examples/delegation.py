"""Delegation: a parent hands a sub-agent strictly weaker authority.

True object-capability: the child can never exceed what it was given, enforced
structurally by the kernel — the foundation for safe multi-agent systems.

Run from the repo root:
    python examples/delegation.py
"""
import shutil
from pathlib import Path

from autarch import Agent, capability
from autarch.contracts import Action, HumanDecision


def banner(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def main() -> None:
    workspace = Path("./sandbox/_delegation")
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)

    # The orchestrator can write anywhere in the workspace.
    orchestrator = Agent(
        intent="coordinate a team of agents",
        council=["mock"],
        grants=[
            capability("file.write", scope={"path_prefix": "."}),
            capability("file.read"),
        ],
        workspace=workspace,
    )

    banner("1) Delegate a STRICTLY WEAKER capability to a worker")
    # The worker is confined to the 'reports' subdirectory — and nothing else.
    worker = orchestrator.spawn(
        intent="create reports/q1.txt that says quarterly complete",
        grants=[capability("file.write", scope={"path_prefix": "reports"})],
        preside_fn=lambda d, g: HumanDecision.RATIFY.value,
    )
    g = worker.grants[0]
    print(f"  worker grant: {g.name}  scope={g.scope}  depth={g.depth} (delegated from '{g.delegated_from}')")

    banner("2) Inside its scope: allowed")
    inside = worker.kernel.authorize(Action("file.write", {"path": "reports/q1.txt", "content": "ok"}))
    print(f"  write reports/q1.txt -> {'ALLOWED' if inside.allowed else 'DENIED'}")

    banner("3) Outside its scope: structurally denied")
    outside = worker.kernel.authorize(Action("file.write", {"path": "payroll.txt", "content": "x"}))
    print(f"  write payroll.txt   -> {'ALLOWED' if outside.allowed else 'DENIED'} - {outside.reason}")

    banner("4) A capability never delegated: dropped at spawn")
    greedy = orchestrator.spawn(intent="delete everything", grants=[capability("file.delete")])
    print(f"  greedy worker grants: {[gg.name for gg in greedy.grants]}")
    print(f"  dropped at delegation: {[d.name for d in greedy.dropped_delegations]}")

    banner("5) Nested delegation only ever shrinks authority")
    team_lead = orchestrator.spawn(intent="lead a sub-team",
                                   grants=[capability("file.write", scope={"path_prefix": "reports"})])
    intern = team_lead.spawn(intent="draft one section",
                             grants=[capability("file.write", scope={"path_prefix": "reports/drafts"})])
    print(f"  orchestrator: file.write @ .")
    print(f"  team_lead   : file.write @ reports        (depth {team_lead.grants[0].depth})")
    print(f"  intern      : file.write @ reports/drafts  (depth {intern.grants[0].depth})")
    climb = intern.spawn(intent="reach payroll", grants=[capability("file.write", scope={"path_prefix": "payroll"})])
    print(f"  intern tries to reach 'payroll' -> granted: {[c.name for c in climb.grants]} (empty = denied)")

    banner("6) The worker actually does its job, governed and signed")
    result = worker.run()
    print(f"  executed: {result.executed} -> {result.result.output if result.result else None}")
    print(f"  recorded in the shared ledger: {result.why_id}")
    print("\nAuthority only ever flows downhill. A child cannot out-reach its parent.")


if __name__ == "__main__":
    main()
