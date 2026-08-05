"""What's new in v0.10 — the governance upgrade, end to end.

Demonstrates the six capabilities added on top of the capability kernel:
  1. General scope algebra   — host allowlists, spend ceilings, data-class guards
  2. Deliberative debate     — voices that rebut each other across rounds
  3. Async approval plane     — out-of-band, quorum-based ratification
  4. Governance gateway       — govern any agent's actions over HTTP
  5. Compliance evidence      — SOC2 / EU-AI-Act / HIPAA control reports
  6. Policy DSL + kernel proof — declarative policy, simulated & diffed; kernel
                                invariants checked by exhaustion

Run from the repo root:
    python examples/governance_upgrade.py
"""
import shutil
from pathlib import Path

from autarch import (Agent, ApprovalQueue, ComplianceReporter,
                     GovernanceGateway, GatewayClient, capability,
                     compile_policies, diff, markdown_report, scoping,
                     simulate, verify_kernel)
from autarch.contracts import Action
from autarch.policy import Policy, PolicyEffect


def banner(title: str) -> None:
    print("\n" + "=" * 66)
    print(title)
    print("=" * 66)


def main() -> None:
    workspace = Path("./sandbox/_upgrade")
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)

    # 1. SCOPE ALGEBRA -------------------------------------------------------
    banner("1. General scope algebra — constraints beyond path_prefix")
    grant = capability(
        "net.fetch",
        scope={"host_allowlist": ["api.github.com"]},
        limits={"amount_max": 100},
    )
    print("constraints:", scoping.describe(grant.scope, grant.limits))
    ok, why = scoping.evaluate(grant.scope, grant.limits, {"url": "https://evil.com/x"})
    print("fetch evil.com ->", ok, "|", why)

    # 6a. KERNEL PROOF -------------------------------------------------------
    banner("2. Kernel invariants — checked by exhaustion")
    result = verify_kernel()
    print(result.summary())

    # 6b. POLICY DSL ---------------------------------------------------------
    banner("3. Policy DSL — simulate and diff before shipping")
    before = compile_policies([])
    after = compile_policies([
        {"name": "no-crypto", "effect": "deny", "capability": "payment.*",
         "when": {"param": "currency", "op": "in", "value": ["BTC", "ETH"]},
         "reason": "no crypto payouts"},
    ])
    samples = [Action("payment.send", {"amount": 10, "currency": "USD"}),
               Action("payment.send", {"amount": 10, "currency": "BTC"})]
    for change in diff(before, after, samples):
        print(f"  policy change: {change['params']}: "
              f"{change['before']} -> {change['after']}")

    # 2. DEBATE --------------------------------------------------------------
    banner("4. Deliberative debate — voices rebut each other")
    agent = Agent(
        intent="write a short note to notes.txt",
        council=["mock:bold", "mock:cautious"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=str(workspace),
        debate_rounds=2,
    )
    run = agent.run()
    delib = run.deliberation
    print(f"debate rounds held: {len(delib.transcript)} | "
          f"recommendation: {delib.recommendation}")

    # 3 + 4. GATEWAY + APPROVAL ---------------------------------------------
    banner("5. Governance gateway + async approval plane")
    gw = GovernanceGateway(
        grants=[capability("file.write", scope={"path_prefix": "."}),
                capability("file.delete")],
        workspace=str(workspace / "gw"),
        policies=[Policy(name="delete-needs-human",
                         effect=PolicyEffect.REQUIRE_RATIFY.value,
                         capability="file.delete")],
    )
    gw.serve(port=8790)
    import time
    time.sleep(0.3)
    client = GatewayClient("http://127.0.0.1:8790")
    client.enact("file.write", {"path": "data.txt", "content": "hello"})
    parked = client.enact("file.delete", {"path": "data.txt"}, actor="agent-42")
    print("delete parked for approval:", parked["status"])
    approval_id = client.pending()["pending"][0]["id"]
    done = client.ratify(approval_id, by="alice")
    print("after human ratify:", done["status"], "| executed:", done.get("executed"))
    gw.stop()

    # 5. COMPLIANCE ----------------------------------------------------------
    banner("6. Compliance evidence — auditor-ready controls")
    report = ComplianceReporter(gw.agent.memory).report(node="gateway")
    print(markdown_report(report))


if __name__ == "__main__":
    main()
