"""The Council: watch your AIs disagree, then preside over them.

Demonstrates Phase 2 — multi-voice deliberation, policy-as-code, and precedent.
Run from the repo root:
    python examples/council.py
"""
import shutil
from pathlib import Path

from autarch import Agent, Policy, PolicyEffect, capability
from autarch.contracts import HumanDecision


def banner(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def main() -> None:
    workspace = Path("./sandbox/_council")
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    (workspace / "a.txt").write_text("hello")

    grants = [
        capability("file.read"),
        capability("file.write", scope={"path_prefix": "."}),
        capability("file.move", scope={"path_prefix": "."}),
        capability("file.delete", scope={"path_prefix": "."}),
    ]

    # 1) A council that genuinely disagrees: bold approves, cautious vetoes.
    banner("1) The council disagrees about deleting a file")
    result = Agent(
        intent="delete the file a.txt",
        council=["mock:bold", "mock:cautious"],
        grants=grants,
        workspace=workspace,
    ).run()
    d = result.deliberation
    for critic in d.critiques:
        print(f"  {critic.voice:<14} -> {critic.verdict}: {critic.rationale}")
    print(f"  tally={dict(d.tally)}  recommendation={d.recommendation}  disagreement={d.has_disagreement}")
    print(f"  executed={result.executed} (auto-presiding overrules on a veto)")

    # 2) Policy-as-code: even a granted, approved action can require ratification.
    banner("2) Policy-as-code escalates a large write")
    big = "A" * 400
    policies = [
        Policy(
            name="large-write",
            effect=PolicyEffect.REQUIRE_RATIFY.value,
            capability="file.write",
            when=lambda p: len(str(p.get("content", ""))) > 280,
            reason="Large writes need a human.",
        )
    ]
    auto = Agent(
        intent=f"create big.txt that says {big}",
        council=["mock"], grants=grants, workspace=workspace, policies=policies,
    ).run()
    print(f"  policy={auto.policy.note()}")
    print(f"  executed={auto.executed} (auto-presiding may not ratify what policy escalates)")

    # 3) Precedent: overrule once, and it is remembered and applied next time.
    banner("3) Precedent — your ruling is remembered")
    Agent(
        intent="move a.txt to b.txt", council=["mock"], grants=grants, workspace=workspace,
        preside_fn=lambda d, g: HumanDecision.OVERRULE.value,
    ).run()
    again = Agent(
        intent="move a.txt to b.txt", council=["mock"], grants=grants, workspace=workspace,
    ).run()
    print(f"  precedent={again.precedent.note() if again.precedent else None}")
    print(f"  decision={again.human_decision}  executed={again.executed}")
    print("\nThe council proposes; you preside; your judgment endures.")


if __name__ == "__main__":
    main()
