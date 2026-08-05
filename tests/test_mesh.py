"""Mesh tests — identity, encryption, and CRDT sync between nodes."""
import pytest

from autarch import Agent, capability
from autarch.mesh import Cipher, Realm, export_bundle, import_bundle
from autarch.memory import WhyMemory


# -- identity / realm ------------------------------------------------------

def test_realm_create_and_join_share_key():
    a = Realm.create("home")
    b = Realm.join("home", a.key_hex)
    assert a.key == b.key            # same identity
    assert a.node_id != b.node_id    # distinct nodes


def test_realm_save_and_load(tmp_path):
    realm = Realm.create("home", policies=[
        {"name": "no-del", "effect": "deny", "capability": "file.delete", "reason": "x"}
    ])
    realm.save(tmp_path)
    loaded = Realm.load(tmp_path)
    assert loaded.name == "home"
    assert loaded.node_id == realm.node_id
    assert loaded.key == realm.key
    policies = loaded.realm_policies()
    assert policies[0].effect == "deny"
    assert policies[0].capability == "file.delete"


def test_realm_load_absent_is_none(tmp_path):
    assert Realm.load(tmp_path) is None


# -- cryptography ----------------------------------------------------------

def test_cipher_roundtrip():
    key = b"k" * 32
    cipher = Cipher(key)
    blob = cipher.encrypt(b"hello autarch")
    assert blob != b"hello autarch"
    assert cipher.decrypt(blob) == b"hello autarch"


def test_cipher_wrong_key_fails():
    blob = Cipher(b"a" * 32).encrypt(b"secret")
    with pytest.raises(ValueError):
        Cipher(b"b" * 32).decrypt(blob)


def test_cipher_tamper_detected():
    cipher = Cipher(b"k" * 32)
    blob = bytearray(cipher.encrypt(b"secret"))
    blob[-1] ^= 0x01  # flip a ciphertext bit
    with pytest.raises(ValueError):
        cipher.decrypt(bytes(blob))


# -- sync ------------------------------------------------------------------

def test_bundle_is_encrypted_at_rest():
    realm = Realm.create("home")
    mem = WhyMemory(":memory:", node_id=realm.node_id)
    blob = export_bundle(mem, realm)
    assert b"home" not in blob  # realm name not visible in ciphertext


def test_intent_on_one_node_syncs_to_another(tmp_path):
    # THE PHASE 4 ACCEPTANCE: an intent on node A, governed by a shared realm
    # policy, with its memory synced to node B.
    realm_a = Realm.create("home", policies=[
        {"name": "no-delete", "effect": "deny", "capability": "file.delete", "reason": "realm rule"}
    ])
    realm_a.save(tmp_path / "A")
    realm_b = Realm.join("home", realm_a.key_hex, policies=realm_a.policies)
    realm_b.save(tmp_path / "B")

    # Node A performs a governed action under the shared policy.
    agent = Agent(
        intent="create report.txt that says quarterly",
        council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path / "A",
        policies=realm_a.realm_policies(),
        node_id=realm_a.node_id,
    )
    result = agent.run()
    assert result.executed is True
    agent.memory.close()

    # Export A's ledger, import into B.
    mem_a = WhyMemory(tmp_path / "A" / ".autarch" / "why.db", node_id=realm_a.node_id)
    blob = export_bundle(mem_a, realm_a)

    mem_b = WhyMemory(tmp_path / "B" / ".autarch" / "why.db", node_id=realm_b.node_id)
    report = import_bundle(mem_b, realm_b, blob)

    assert report.added == 1
    assert report.from_node == realm_a.node_id
    # Node B can now explain the action that happened on node A.
    synced = mem_b.get(result.why_id)
    assert synced is not None
    assert synced.intent_text == "create report.txt that says quarterly"
    # And the merged ledger is intact under A's origin.
    ok, _ = mem_b.verify_chain()
    assert ok is True
    assert realm_a.node_id in mem_b.origins()


def test_policy_converges_via_sync(tmp_path):
    # A policy defined on node A propagates to node B through sync, even though B
    # joined with no policies of its own. This makes "one policy set" real.
    realm_a = Realm.create("home", policies=[
        {"name": "no-delete", "effect": "deny", "capability": "file.delete", "reason": "realm rule"}
    ])
    realm_b = Realm.join("home", realm_a.key_hex)  # NO policies on join
    assert realm_b.realm_policies() == []

    mem_a = WhyMemory(tmp_path / "a.db", node_id=realm_a.node_id)
    mem_b = WhyMemory(tmp_path / "b.db", node_id=realm_b.node_id)
    blob = export_bundle(mem_a, realm_a)
    report = import_bundle(mem_b, realm_b, blob)

    assert report.policies_added == 1
    # B now enforces the shared policy it never defined locally.
    policies = realm_b.realm_policies()
    assert len(policies) == 1
    assert policies[0].capability == "file.delete"
    assert policies[0].effect == "deny"


def test_policy_merge_is_idempotent(tmp_path):
    realm_a = Realm.create("home", policies=[
        {"name": "no-delete", "effect": "deny", "capability": "file.delete", "reason": "x"}
    ])
    realm_b = Realm.join("home", realm_a.key_hex, policies=realm_a.policies)  # already has it
    mem_a = WhyMemory(tmp_path / "a.db", node_id=realm_a.node_id)
    mem_b = WhyMemory(tmp_path / "b.db", node_id=realm_b.node_id)
    blob = export_bundle(mem_a, realm_a)
    report = import_bundle(mem_b, realm_b, blob)
    assert report.policies_added == 0  # union by name -> no duplicate
    assert len(realm_b.policies) == 1


def test_sync_is_idempotent(tmp_path):
    realm = Realm.create("home")
    mem_a = WhyMemory(tmp_path / "a.db", node_id=realm.node_id)
    Agent(
        intent="create x.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path / "wa", memory=mem_a, node_id=realm.node_id,
    ).run()

    other = Realm.join("home", realm.key_hex)
    mem_b = WhyMemory(tmp_path / "b.db", node_id=other.node_id)
    blob = export_bundle(mem_a, realm)

    first = import_bundle(mem_b, other, blob)
    second = import_bundle(mem_b, other, blob)
    assert first.added == 1
    assert second.added == 0 and second.skipped == 1


def test_foreign_realm_bundle_rejected(tmp_path):
    realm = Realm.create("home")
    stranger = Realm.create("home")  # same name, DIFFERENT key
    mem = WhyMemory(tmp_path / "a.db", node_id=realm.node_id)
    blob = export_bundle(mem, realm)

    mem2 = WhyMemory(tmp_path / "b.db", node_id=stranger.node_id)
    with pytest.raises(ValueError):
        import_bundle(mem2, stranger, blob)  # wrong key -> auth failure


def test_tampered_record_in_bundle_is_rejected(tmp_path):
    import json as _json

    realm = Realm.create("home")
    mem_a = WhyMemory(tmp_path / "a.db", node_id=realm.node_id)
    Agent(
        intent="create x.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path / "wa", memory=mem_a, node_id=realm.node_id,
    ).run()

    # Forge the plaintext bundle: alter a record's payload but keep its seal.
    rows = mem_a.export_rows()
    rows[0]["payload"] = rows[0]["payload"].replace("hi", "HACKED")
    forged = _json.dumps({
        "version": 1, "realm": "home", "from_node": realm.node_id, "records": rows,
    }).encode("utf-8")
    blob = Cipher(realm.key).encrypt(forged)  # validly encrypted by a realm member

    other = Realm.join("home", realm.key_hex)
    mem_b = WhyMemory(tmp_path / "b.db", node_id=other.node_id)
    report = import_bundle(mem_b, other, blob)

    # Auth passes (member key) but per-record seal check rejects the forgery.
    assert report.rejected == 1
    assert report.added == 0
