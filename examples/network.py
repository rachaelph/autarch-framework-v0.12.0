"""Network mesh: sync two nodes over HTTP — self-contained, no broker.

This runs a real Autarch mesh server (stdlib http.server) in-process and syncs
a second node against it over a real socket. The bundle is AES-GCM encrypted and
authenticated end-to-end, so the transport is just a pipe — security rides on the
realm key, not on TLS.

Run from the repo root:
    python examples/network.py
"""
import shutil
from pathlib import Path

from autarch import Agent, capability
from autarch.mesh import Realm
from autarch.memory import WhyMemory
from autarch.provenance import NodeIdentity
from autarch.transport import MeshServer, health, sync


def banner(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def main() -> None:
    root = Path("./sandbox/_network")
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    (root / "laptop").mkdir(parents=True)
    (root / "phone").mkdir(parents=True)

    banner("1) The laptop forms a realm and records a signed action")
    realm = Realm.create("home", policies=[
        {"name": "no-delete", "effect": "deny", "capability": "file.delete",
         "reason": "This realm forbids deletion on every node."},
    ])
    laptop = NodeIdentity.create()
    db_a = root / "laptop" / "why.db"
    mem_a = WhyMemory(db_a, node_id=laptop.node_id, identity=laptop)
    Agent(
        intent="create report.txt that says quarterly numbers",
        council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=root / "laptop" / "wk", memory=mem_a, node_id=laptop.node_id,
    ).run()
    mem_a.close()
    print(f"  realm 'home', node {realm.node_id}, 1 signed record")

    banner("2) The laptop serves its ledger over HTTP (stdlib, loopback)")
    server = MeshServer(realm, db_a, workspace=root / "laptop", host="127.0.0.1", port=0)
    url = server.start()
    print(f"  serving at {url}")
    print(f"  health: {health(url)}")

    try:
        banner("3) The phone joins and syncs over the network")
        phone = Realm.join("home", realm.key_hex)
        mem_b = WhyMemory(root / "phone" / "why.db", node_id=phone.node_id)
        pushed, pulled = sync(url, mem_b, phone)
        print(f"  pushed -> peer added {pushed['added']} record(s)")
        print(f"  pulled <- {pulled.summary()}")

        banner("4) The phone now shares the laptop's memory AND policy")
        rec_id = mem_b.all()[0].id
        print(f"  phone reads record {rec_id}: \"{mem_b.get(rec_id).intent_text}\"")
        print(f"  authorship verified on phone: {mem_b.verify_provenance(rec_id)}")
        print(f"  shared policy converged: {[p.capability + '=' + p.effect for p in phone.realm_policies()]}")
        ok, _ = mem_b.verify_chain()
        print(f"  merged ledger intact: {ok}")
        mem_b.close()
        print("\nNo broker. No file passing. One identity, one policy, one memory \u2014 over the wire.")
    finally:
        server.stop()


if __name__ == "__main__":
    main()
