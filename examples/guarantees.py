"""Formal guarantees: PROVE safety invariants before the agent ever runs.

The kernel and policy engine are deterministic, so safety properties can be
*proven* statically — not merely tested. These proofs hold regardless of what the
model proposes, which is exactly what regulated industries need.

Run from the repo root:
    python examples/guarantees.py
"""
import shutil
from pathlib import Path

from autarch import Agent, capability
from autarch.guarantees import Invariant, prove_guarantees
from autarch.policy import Policy, PolicyEffect


def banner(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def show(report) -> None:
    for p in report.proofs:
        mark = "PROVEN  " if p.holds else "FAILED  "
        print(f"  [{mark}] {p.invariant.label()}")
        print(f"            {p.reason}")
        if not p.holds and p.counterexample:
            print(f"            counterexample: grant '{p.counterexample}'")


def main() -> None:
    workspace = Path("./sandbox/_guarantees")
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)

    banner("1) Prove an agent can NEVER delete (it was never granted)")
    agent = Agent(
        intent="summarize the data",
        council=["mock"],
        grants=[capability("file.read"), capability("file.write", scope={"path_prefix": "."})],
        workspace=workspace,
    )
    show(agent.guarantee([Invariant.forbid("file.delete")]))

    banner("2) Two-person rule: payments ALWAYS require human approval")
    grants = [capability("payment.send")]
    with_policy = [Policy("two-person", PolicyEffect.REQUIRE_RATIFY.value, "payment.send",
                          reason="Payments require explicit human ratification.")]
    print("  with the approval policy in place:")
    show(prove_guarantees([Invariant.require_approval("payment.send")], grants, with_policy))
    print("\n  if someone removes the policy, the guarantee BREAKS (caught before running):")
    show(prove_guarantees([Invariant.require_approval("payment.send")], grants, []))

    banner("3) Confine writes to a directory — proven, not hoped")
    confined = [capability("file.write", scope={"path_prefix": "reports"})]
    show(prove_guarantees([Invariant.confine("file.write", "reports")], confined))
    print("\n  a broader grant would be caught:")
    broad = [capability("file.write", scope={"path_prefix": "."})]
    show(prove_guarantees([Invariant.confine("file.write", "reports")], broad))

    banner("4) Delegation PRESERVES guarantees (authority only shrinks)")
    parent = Agent(
        intent="coordinate", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "reports"})], workspace=workspace,
    )
    inv = [Invariant.forbid("file.delete"), Invariant.confine("file.write", "reports")]
    print(f"  parent invariants hold: {parent.guarantee(inv).all_hold}")
    child = parent.spawn(intent="sub-task",
                         grants=[capability("file.write", scope={"path_prefix": "reports/drafts"})])
    print(f"  child  invariants hold: {child.guarantee(inv).all_hold}  (proven for the parent => holds for every child)")

    banner("5) Put it in CI")
    print("  `autarch guarantee --forbid payment.send` exits non-zero if the")
    print("  property can't be proven — so an unsafe config fails the build.")
    print("\nYou don't test that the agent is safe. You prove it.")


if __name__ == "__main__":
    main()
