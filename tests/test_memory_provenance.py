"""Provenance through the memory + mesh: signed, attributable, non-repudiable."""
import pytest

from autarch import Agent, capability
from autarch.mesh import Realm, export_bundle, import_bundle
from autarch.memory import WhyMemory
from autarch.provenance import NodeIdentity

pytest.importorskip("cryptography")


def _signed_memory(tmp_path, name="m.db"):
    identity = NodeIdentity.create()
    mem = WhyMemory(tmp_path / name, node_id=identity.node_id, identity=identity)
    return mem, identity


def _rec(intent="do x"):
    from autarch.contracts import WhyRecord

    return WhyRecord(
        intent_text=intent, capability="file.write", params={"path": "a.txt"},
        rationale="r", proposer="mock", challenger="mock",
        critique_verdict="approve", critique_reasons="ok",
        gate_allowed=True, gate_reason="granted", human_decision="ratify",
        executed=True, result_ok=True, result_output="done", result_error=None, undo=None,
    )


def test_recorded_action_is_signed(tmp_path):
    mem, identity = _signed_memory(tmp_path)
    rid = mem.record(_rec())
    rec = mem.get(rid)
    assert rec.signer == identity.node_id
    assert rec.signer_key == identity.public_hex
    assert rec.signature  # non-empty
    assert mem.verify_provenance(rid) is True


def test_unsigned_memory_has_no_provenance(tmp_path):
    mem = WhyMemory(tmp_path / "m.db")  # no identity
    rid = mem.record(_rec())
    assert mem.get(rid).signature == ""
    assert mem.verify_provenance(rid) is None  # nothing to verify


def test_tampered_payload_breaks_provenance(tmp_path):
    import json

    mem, _ = _signed_memory(tmp_path)
    rid = mem.record(_rec("original"))
    # Alter the stored payload directly.
    row = mem._conn.execute("SELECT payload FROM why WHERE id=?", (rid,)).fetchone()
    tampered = json.dumps({**json.loads(row["payload"]), "intent_text": "HACKED"})
    mem._conn.execute("UPDATE why SET payload=? WHERE id=?", (tampered, rid))
    mem._conn.commit()
    assert mem.verify_provenance(rid) is False
    assert mem.verify(rid) is False


def test_forged_signature_is_rejected(tmp_path):
    mem, _ = _signed_memory(tmp_path)
    rid = mem.record(_rec())
    sig = mem._conn.execute("SELECT signature FROM why WHERE id=?", (rid,)).fetchone()[0]
    forged = ("ff" if sig[:2] != "ff" else "00") + sig[2:]
    mem._conn.execute("UPDATE why SET signature=? WHERE id=?", (forged, rid))
    mem._conn.commit()
    # Content intact (seal ok) but signature no longer verifies.
    assert mem.verify(rid) is True
    assert mem.verify_provenance(rid) is False


def test_spoofed_signer_id_is_rejected(tmp_path):
    # Claiming a different node id than the one bound to the key must fail.
    mem, _ = _signed_memory(tmp_path)
    rid = mem.record(_rec())
    mem._conn.execute("UPDATE why SET signer=? WHERE id=?", ("node_imposter000", rid))
    mem._conn.commit()
    assert mem.verify_provenance(rid) is False


def test_provenance_travels_across_the_mesh(tmp_path):
    # An action signed on node A is verifiable as A's work after syncing to B,
    # even though B holds the shared realm key.
    realm = Realm.create("home")
    laptop = NodeIdentity.create()
    mem_a = WhyMemory(tmp_path / "a.db", node_id=laptop.node_id, identity=laptop)
    Agent(
        intent="create x.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path / "wa", memory=mem_a, node_id=laptop.node_id,
    ).run()
    signed_id = mem_a.all()[0].id

    blob = export_bundle(mem_a, realm)
    phone = Realm.join("home", realm.key_hex)
    mem_b = WhyMemory(tmp_path / "b.db", node_id=phone.node_id)
    import_bundle(mem_b, phone, blob)

    # The phone verifies authorship cryptographically — and the author is the laptop.
    assert mem_b.verify_provenance(signed_id) is True
    assert mem_b.get(signed_id).signer == laptop.node_id


def test_realm_member_cannot_forge_another_nodes_record(tmp_path):
    # A malicious realm member alters an imported record's content; even though it
    # could re-encrypt with the shared key, it cannot produce a valid signature
    # for the original author -> rejected.
    import json

    realm = Realm.create("home")
    laptop = NodeIdentity.create()
    mem_a = WhyMemory(tmp_path / "a.db", node_id=laptop.node_id, identity=laptop)
    Agent(
        intent="transfer 10 dollars", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path / "wa", memory=mem_a, node_id=laptop.node_id,
    ).run()

    rows = mem_a.export_rows()
    # Forge the content of the laptop's record but keep its signer + signature.
    rows[0]["payload"] = rows[0]["payload"].replace("10 dollars", "10000 dollars")
    forged = json.dumps({"version": 1, "realm": "home", "from_node": "evil", "records": rows}).encode()
    from autarch.mesh import Cipher

    blob = Cipher(realm.key).encrypt(forged)

    phone = Realm.join("home", realm.key_hex)
    mem_b = WhyMemory(tmp_path / "b.db", node_id=phone.node_id)
    report = import_bundle(mem_b, phone, blob)
    # The seal check rejects the altered payload outright.
    assert report.rejected == 1
    assert report.added == 0
