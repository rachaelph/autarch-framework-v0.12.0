"""Provenance: every action is signed, attributable, and unforgeable.

Tamper-evidence (the hash chain) proves a record wasn't *altered*. Provenance
proves *who* produced it. This shows: a signed action verifies; a forged
signature is caught; and authorship survives a sync to another device — a realm
member with the shared key still cannot forge another node's signature.

Run from the repo root:
    python examples/provenance.py
"""
import shutil
from pathlib import Path

from autarch import Agent, capability
from autarch.mesh import Realm, export_bundle, import_bundle
from autarch.memory import WhyMemory
from autarch.provenance import NodeIdentity, available


def banner(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def db(ws: Path) -> Path:
    return ws / ".autarch" / "why.db"


def main() -> None:
    if not available():
        print("This demo needs the `cryptography` package: pip install autarch[crypto]")
        return

    root = Path("./sandbox/_provenance")
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)

    # 1) A signed action — attributable to a key-bound node identity.
    banner("1) Every action is cryptographically signed")
    laptop_ws = root / "laptop"
    agent = Agent(
        intent="create ledger.txt that says opening balance 100",
        council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=laptop_ws,
    )
    result = agent.run()
    rec = agent.memory.get(result.why_id)
    print(f"  node identity : {agent.identity.node_id} (derived from its public key)")
    print(f"  action        : {rec.capability} -> {result.executed}")
    print(f"  signed by     : {rec.signer}")
    print(f"  signature     : {rec.signature[:32]}... ({len(rec.signature)//2} bytes)")
    print(f"  provenance    : {'VERIFIED' if agent.memory.verify_provenance(result.why_id) else 'FAIL'}")

    # 2) A forged signature is rejected — integrity and authorship are distinct.
    banner("2) A forged signature is caught (content still intact)")
    sig = agent.memory._conn.execute(
        "SELECT signature FROM why WHERE id=?", (result.why_id,)
    ).fetchone()[0]
    forged = ("ff" if sig[:2] != "ff" else "00") + sig[2:]
    agent.memory._conn.execute(
        "UPDATE why SET signature=? WHERE id=?", (forged, result.why_id)
    )
    agent.memory._conn.commit()
    print(f"  integrity (seal): {'intact' if agent.memory.verify(result.why_id) else 'broken'}")
    print(f"  provenance      : {'VERIFIED' if agent.memory.verify_provenance(result.why_id) else 'FORGED'}")
    print("  -> the signature is independent of tamper-evidence: forgery is caught.")

    # 3) Authorship survives a sync — a realm member cannot impersonate a node.
    banner("3) Authorship is provable across devices (non-repudiation)")
    realm = Realm.create("home")
    laptop = NodeIdentity.load_or_create(laptop_ws)
    fresh_ws = root / "laptop_clean"
    mem_a = WhyMemory(db(fresh_ws), node_id=laptop.node_id, identity=laptop)
    Agent(
        intent="create signed.txt that says authentic",
        council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=fresh_ws, memory=mem_a, node_id=laptop.node_id,
    ).run()
    signed_id = mem_a.all()[0].id

    blob = export_bundle(mem_a, realm)
    phone = Realm.join("home", realm.key_hex)
    phone_ws = root / "phone"
    mem_b = WhyMemory(db(phone_ws), node_id=phone.node_id)
    import_bundle(mem_b, phone, blob)
    print(f"  phone holds the shared realm key, yet it can VERIFY (not forge) authorship:")
    print(f"    record {signed_id} signed by {mem_b.get(signed_id).signer}")
    print(f"    provenance on phone: {'VERIFIED' if mem_b.verify_provenance(signed_id) else 'FAIL'}")
    print("\nTamper-evidence says nothing changed. Provenance says who did it.")


if __name__ == "__main__":
    main()
