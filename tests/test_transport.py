"""Network transport tests — stdlib HTTP node-to-node sync."""
import pytest

from autarch import Agent, capability
from autarch.mesh import Realm
from autarch.memory import WhyMemory
from autarch.provenance import NodeIdentity
from autarch.transport import MeshServer, health, pull, push, sync


def _seed_node_a(tmp_path):
    """A realm with one signed record on node A, plus its db path and identity."""
    realm = Realm.create("home", policies=[
        {"name": "no-delete", "effect": "deny", "capability": "file.delete", "reason": "rule"}
    ])
    identity = NodeIdentity.create()
    db = tmp_path / "a.db"
    mem = WhyMemory(db, node_id=identity.node_id, identity=identity)
    Agent(
        intent="create report.txt that says quarterly", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path / "wa", memory=mem, node_id=identity.node_id,
    ).run()
    record_id = mem.all()[0].id
    mem.close()
    return realm, db, record_id


def test_health_endpoint(tmp_path):
    realm, db, _ = _seed_node_a(tmp_path)
    server = MeshServer(realm, db, port=0)
    url = server.start()
    try:
        info = health(url)
        assert info["ok"] is True
        assert info["node"] == realm.node_id
    finally:
        server.stop()


def test_pull_copies_records_and_provenance(tmp_path):
    realm, db, record_id = _seed_node_a(tmp_path)
    server = MeshServer(realm, db, port=0)
    url = server.start()
    try:
        phone = Realm.join("home", realm.key_hex)
        mem_b = WhyMemory(tmp_path / "b.db", node_id=phone.node_id)
        report = pull(url, mem_b, phone)
        assert report.added == 1
        # The pulled record's authorship verifies cryptographically on node B.
        assert mem_b.verify_provenance(record_id) is True
        assert mem_b.get(record_id).intent_text == "create report.txt that says quarterly"
    finally:
        server.stop()


def test_pull_converges_shared_policy(tmp_path):
    realm, db, _ = _seed_node_a(tmp_path)
    server = MeshServer(realm, db, port=0)
    url = server.start()
    try:
        phone = Realm.join("home", realm.key_hex)  # no policies
        mem_b = WhyMemory(tmp_path / "b.db", node_id=phone.node_id)
        report = pull(url, mem_b, phone)
        assert report.policies_added == 1
        assert phone.realm_policies()[0].capability == "file.delete"
    finally:
        server.stop()


def test_push_sends_to_peer(tmp_path):
    # Node A serves an EMPTY ledger; node B pushes its record to A.
    realm = Realm.create("home")
    db_a = tmp_path / "a.db"
    WhyMemory(db_a, node_id=realm.node_id).close()  # create empty
    server = MeshServer(realm, db_a, port=0)
    url = server.start()
    try:
        phone = Realm.join("home", realm.key_hex)
        mem_b = WhyMemory(tmp_path / "b.db", node_id=phone.node_id, identity=NodeIdentity.create())
        Agent(
            intent="create x.txt that says hi", council=["mock"],
            grants=[capability("file.write", scope={"path_prefix": "."})],
            workspace=tmp_path / "wb", memory=mem_b, node_id=phone.node_id,
        ).run()
        result = push(url, mem_b, phone)
        assert result["added"] == 1
        # Node A's ledger now contains node B's record.
        mem_a = WhyMemory(db_a, node_id=realm.node_id)
        assert len(mem_a.all()) == 1
        mem_a.close()
    finally:
        server.stop()


def test_sync_is_bidirectional_and_idempotent(tmp_path):
    realm, db, _ = _seed_node_a(tmp_path)
    server = MeshServer(realm, db, port=0)
    url = server.start()
    try:
        phone = Realm.join("home", realm.key_hex)
        mem_b = WhyMemory(tmp_path / "b.db", node_id=phone.node_id)
        _, pulled1 = sync(url, mem_b, phone)
        assert pulled1.added == 1
        # Second sync adds nothing new.
        _, pulled2 = sync(url, mem_b, phone)
        assert pulled2.added == 0 and pulled2.skipped >= 1
    finally:
        server.stop()


def test_wrong_realm_key_pull_is_rejected(tmp_path):
    realm, db, _ = _seed_node_a(tmp_path)
    server = MeshServer(realm, db, port=0)
    url = server.start()
    try:
        stranger = Realm.create("home")  # same name, different key
        mem = WhyMemory(tmp_path / "s.db", node_id=stranger.node_id)
        with pytest.raises((ValueError, RuntimeError)):
            pull(url, mem, stranger)
    finally:
        server.stop()


def test_unreachable_peer_raises(tmp_path):
    realm = Realm.create("home")
    mem = WhyMemory(tmp_path / "b.db", node_id=realm.node_id)
    with pytest.raises(RuntimeError):
        pull("http://127.0.0.1:1", mem, realm, timeout=2.0)  # nothing listening
