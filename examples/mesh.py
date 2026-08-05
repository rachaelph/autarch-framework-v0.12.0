"""The Mesh: one identity, one policy, memory synced across nodes.

Demonstrates Phase 4 — two nodes of one realm, an action on node A governed by a
shared policy, then local-first encrypted sync so node B can explain it too.

Run from the repo root:
    python examples/mesh.py
"""
import shutil
from pathlib import Path

from autarch import Agent, capability
from autarch.mesh import Realm, export_bundle, import_bundle
from autarch.memory import WhyMemory
from autarch.substrate import Substrate


def banner(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def db(workspace: Path) -> Path:
    return workspace / ".autarch" / "why.db"


def main() -> None:
    root = Path("./sandbox/_mesh")
    if root.exists():
        shutil.rmtree(root)
    node_a_ws = root / "laptop"
    node_b_ws = root / "phone"

    banner("0) The substrate — the core runs anywhere")
    print("  " + Substrate.detect().describe())

    # 1) Create a realm on the laptop, with a shared "never delete" policy.
    banner("1) Form a realm on the laptop (with a shared policy)")
    realm_a = Realm.create("home", policies=[
        {"name": "no-delete", "effect": "deny", "capability": "file.delete",
         "reason": "This realm forbids deletion on every node."},
    ])
    realm_a.save(node_a_ws)
    print(f"  realm '{realm_a.name}'  node {realm_a.node_id}")
    print(f"  shared key (given to other devices): {realm_a.key_hex[:24]}...")

    # 2) The phone joins the same realm using the shared key.
    banner("2) The phone joins the same realm")
    realm_b = Realm.join("home", realm_a.key_hex, policies=realm_a.policies)
    realm_b.save(node_b_ws)
    print(f"  realm '{realm_b.name}'  node {realm_b.node_id} (same identity, different node)")

    # 3) The laptop performs a governed action under the shared policy.
    banner("3) Laptop acts — governed by the shared realm policy")
    agent = Agent(
        intent="create report.txt that says quarterly numbers",
        council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=node_a_ws,
        policies=realm_a.realm_policies(),
        node_id=realm_a.node_id,
    )
    result = agent.run()
    agent.memory.close()
    print(f"  executed={result.executed}  why={result.why_id}")
    print(f"  (the shared 'no-delete' policy is active on this node: "
          f"{[p.capability + '=' + p.effect for p in realm_a.realm_policies()]})")

    # 4) Local-first sync: export an encrypted bundle, import on the phone.
    banner("4) Sync laptop -> phone (encrypted, no central server)")
    mem_a = WhyMemory(db(node_a_ws), node_id=realm_a.node_id)
    blob = export_bundle(mem_a, realm_a)
    bundle_file = root / "laptop.bundle"
    bundle_file.write_bytes(blob)
    print(f"  exported encrypted bundle: {bundle_file.name} ({len(blob)} bytes of ciphertext)")

    mem_b = WhyMemory(db(node_b_ws), node_id=realm_b.node_id)
    report = import_bundle(mem_b, realm_b, blob)
    print(f"  {report.summary()}")

    # 5) The phone can now explain the action that happened on the laptop.
    banner("5) The phone now shares the laptop's memory")
    synced = mem_b.get(result.why_id)
    print(f"  phone reads {result.why_id}: \"{synced.intent_text}\"")
    ok, _ = mem_b.verify_chain()
    print(f"  merged ledger intact: {ok}  | origins on phone: {mem_b.origins()}")
    print("\nOne identity. One policy. One memory - across your devices.")


if __name__ == "__main__":
    main()
