"""N-node gossip: a record spreads across the whole mesh epidemically.

With three nodes in a chain (A knows B, B knows C), a record created on A reaches
C *through* B — without A and C ever talking directly. Because the ledger is a
grow-only set merged by id, gossip converges like an epidemic: a few rounds and
everyone has everything. Self-contained: stdlib HTTP, no broker.

Run from the repo root:
    python examples/gossip.py
"""
import shutil
from pathlib import Path

from autarch import Agent, capability
from autarch.mesh import Realm
from autarch.memory import WhyMemory
from autarch.provenance import NodeIdentity
from autarch.transport import MeshServer, gossip


def banner(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def has(node, rec_id) -> bool:
    mem = WhyMemory(node["db"], node_id=node["realm"].node_id)
    try:
        return mem.has(rec_id)
    finally:
        mem.close()


def gossip_from(node):
    mem = WhyMemory(node["db"], node_id=node["realm"].node_id, identity=node.get("ident"))
    try:
        return gossip(node["realm"].peers, mem, node["realm"])
    finally:
        mem.close()


def main() -> None:
    root = Path("./sandbox/_gossip")
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)

    base = Realm.create("home")
    nodes = {}
    for name in ("A", "B", "C"):
        nodes[name] = {
            "realm": Realm.join("home", base.key_hex),
            "ident": NodeIdentity.create(),
            "db": root / f"{name}.db",
        }

    banner("1) Three nodes in a CHAIN: A -> B -> C")
    print("  A only knows B; B only knows C. A and C never talk directly.")

    # A records an action.
    mem_a = WhyMemory(nodes["A"]["db"], node_id=nodes["A"]["realm"].node_id, identity=nodes["A"]["ident"])
    Agent(
        intent="create report.txt that says authored on A",
        council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=root / "wkA", memory=mem_a, node_id=nodes["A"]["realm"].node_id,
    ).run()
    rec = mem_a.all()[0].id
    mem_a.close()
    print(f"  A authored record {rec}")

    # Start B and C servers; wire the chain topology.
    srv_b = MeshServer(nodes["B"]["realm"], nodes["B"]["db"], port=0)
    srv_c = MeshServer(nodes["C"]["realm"], nodes["C"]["db"], port=0)
    url_b, url_c = srv_b.start(), srv_c.start()
    nodes["A"]["realm"].add_peer(url_b)   # A -> B
    nodes["B"]["realm"].add_peer(url_c)   # B -> C

    try:
        banner("2) Before any gossip")
        print(f"  B has A's record: {has(nodes['B'], rec)}   C has A's record: {has(nodes['C'], rec)}")

        banner("3) Round 1 — A gossips its peers (B)")
        print(f"  {gossip_from(nodes['A']).summary()}")
        print(f"  B has A's record: {has(nodes['B'], rec)}   C has A's record: {has(nodes['C'], rec)}")

        banner("4) Round 2 — B gossips its peers (C)")
        print(f"  {gossip_from(nodes['B']).summary()}")
        print(f"  B has A's record: {has(nodes['B'], rec)}   C has A's record: {has(nodes['C'], rec)}")

        banner("5) Converged")
        print(f"  C verifies A's authorship transitively: ", end="")
        mem_c = WhyMemory(nodes["C"]["db"], node_id=nodes["C"]["realm"].node_id)
        print(mem_c.verify_provenance(rec))
        mem_c.close()
        print("\nNo broker. No direct A-C link. The record spread across the mesh on its own.")
    finally:
        srv_b.stop()
        srv_c.stop()


if __name__ == "__main__":
    main()
