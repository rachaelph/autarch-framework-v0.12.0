"""The economic kernel: every action carries a budget, enforced before it runs.

Capability security asks "is this allowed?"; economics asks "can we afford it?".
An agent (and the whole tree of sub-agents it spawns) runs under one budget; when
the next action would bust a ceiling, it is refused — deterministically, before
execution. This is what lets agents run at scale without runaway spend or risk.

Run from the repo root:
    python examples/economy.py
"""
import shutil
from pathlib import Path

from autarch import Agent, Budget, capability
from autarch.contracts import HumanDecision


def banner(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def main() -> None:
    workspace = Path("./sandbox/_economy")
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)

    banner("1) A budget meters every action")
    # Allow up to 2 model/tool 'calls' for this run.
    budget = Budget(limits={"calls": 2})
    print(f"  budget: {budget.snapshot()}")

    for i in (1, 2, 3):
        agent = Agent(
            intent=f"create note{i}.txt that says entry {i}",
            council=["mock"],
            grants=[capability("file.write", scope={"path_prefix": "."})],
            workspace=workspace,
            budget=budget,  # the SAME pool across all three runs
        )
        result = agent.run()
        status = "executed" if result.executed else f"REFUSED ({result.budget_decision.reason})"
        print(f"  action {i}: {status}  | budget now {budget.snapshot()}")

    banner("2) Risk budgets refuse high-hazard actions")
    risk_budget = Budget(limits={"risk": 3})
    # A delete carries risk 5 by default -> over a risk budget of 3.
    result = Agent(
        intent="delete note1.txt",
        council=["mock"],
        grants=[capability("file.delete", scope={"path_prefix": "."})],
        workspace=workspace,
        budget=risk_budget,
        preside_fn=lambda d, g: HumanDecision.RATIFY.value,
    ).run()
    print(f"  capability gate allowed: {result.gate.allowed}")
    print(f"  economic kernel refused: {not result.budget_decision.ok} - {result.budget_decision.reason}")
    print(f"  executed: {result.executed} (the gate said yes; the budget said no)")

    banner("3) Custom prices: a real cost ceiling")
    # Price a 'payment.send' capability and cap spend at $1.00. We script the
    # council to propose the payment so the demo is deterministic offline.
    import json

    from autarch.intelligence.mock import MockProvider

    payer = MockProvider(name="payer", scripted={
        "ROLE: PROPOSER": json.dumps(
            {"capability": "payment.send", "params": {"amount": 250}, "rationale": "pay invoice"}
        ),
        "ROLE: CHALLENGER": json.dumps({"verdict": "approve", "reasons": "ok"}),
    })
    agent = Agent(
        intent="send a payment",
        council=[payer],
        grants=[capability("payment.send")],
        workspace=workspace,
        budget=Budget(limits={"cost": 1.00}),
        prices={"payment.send": {"cost": 2.50, "calls": 1, "risk": 9}},
        preside_fn=lambda d, g: HumanDecision.RATIFY.value,
        adapters=[],  # no adapter -> we only care about the economic verdict here
    )
    result = agent.run()
    bd = result.budget_decision
    print(f"  a $2.50 payment under a $1.00 budget -> refused: {bd is not None and not bd.ok}")
    if bd:
        print(f"  reason: {bd.reason}")

    banner("4) Shared budget across a team of sub-agents")
    pool = Budget(limits={"calls": 1})
    orchestrator = Agent(
        intent="coordinate", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=workspace, budget=pool,
    )
    worker_a = orchestrator.spawn(
        intent="create team/a.txt that says alice",
        grants=[capability("file.write", scope={"path_prefix": "team"})],
        preside_fn=lambda d, g: HumanDecision.RATIFY.value,
    )
    worker_b = orchestrator.spawn(
        intent="create team/b.txt that says bob",
        grants=[capability("file.write", scope={"path_prefix": "team"})],
        preside_fn=lambda d, g: HumanDecision.RATIFY.value,
    )
    ra = worker_a.run()
    rb = worker_b.run()
    print(f"  worker A executed: {ra.executed}")
    print(f"  worker B executed: {rb.executed} (the shared pool was already spent)")
    print("\nAllowed is not the same as affordable. The economic kernel enforces both.")


if __name__ == "__main__":
    main()
