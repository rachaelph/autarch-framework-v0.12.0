"""N-node gossip tests — epidemic convergence across 3+ nodes."""
from autarch import Agent, capability
from autarch.mesh import Realm
from autarch.memory import WhyMemory
from autarch.provenance import NodeIdentity
from autarch.transport import MeshServer, gossip


def _node(tmp_path, name, realm_key):
    realm = Realm.join("home", realm_key)
    ident = NodeIdentity.create()
    db = tmp_path / f"{name}.db"
    return {"realm": realm, "ident": ident, "db": db}


def _record_on(node, tmp_path, name, text):
    mem = WhyMemory(node["db"], node_id=node["realm"].node_id, identity=node["ident"])
    Agent(
        intent=f"create {name}.txt that says {text}", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path / f"wk_{name}", memory=mem, node_id=node["realm"].node_id,
    ).run()
    rec_id = mem.all()[0].id
    mem.close()
    return rec_id


def _has(node, rec_id):
    mem = WhyMemory(node["db"], node_id=node["realm"].node_id)
    try:
        return mem.has(rec_id)
    finally:
        mem.close()


# --- realm peers ----------------------------------------------------------

def test_realm_peers_persist(tmp_path):
    realm = Realm.create("home")
    assert realm.add_peer("http://x:1/") is True
    assert realm.add_peer("http://x:1") is False  # normalized dup
    realm.save(tmp_path)
    loaded = Realm.load(tmp_path)
    assert loaded.peers == ["http://x:1"]


def test_realm_remove_peer(tmp_path):
    realm = Realm.create("home")
    realm.add_peer("http://x:1")
    assert realm.remove_peer("http://x:1") is True
    assert realm.peers == []


# --- gossip ---------------------------------------------------------------

def test_gossip_two_nodes(tmp_path):
    base = Realm.create("home")
    a = _node(tmp_path, "a", base.key_hex)
    b = _node(tmp_path, "b", base.key_hex)
    rec = _record_on(a, tmp_path, "a", "hi")

    srv_b = MeshServer(b["realm"], b["db"], port=0)
    url_b = srv_b.start()
    try:
        a["realm"].add_peer(url_b)
        mem_a = WhyMemory(a["db"], node_id=a["realm"].node_id, identity=a["ident"])
        report = gossip(a["realm"].peers, mem_a, a["realm"])
        mem_a.close()
        assert report.reached == 1
        assert report.total_propagated >= 1
        assert _has(b, rec) is True  # B received A's record
    finally:
        srv_b.stop()


def test_gossip_transitive_convergence(tmp_path):
    # Chain topology A -> B -> C. A's record must reach C through B.
    base = Realm.create("home")
    a = _node(tmp_path, "a", base.key_hex)
    b = _node(tmp_path, "b", base.key_hex)
    c = _node(tmp_path, "c", base.key_hex)
    rec = _record_on(a, tmp_path, "a", "fromA")

    srv_b = MeshServer(b["realm"], b["db"], port=0)
    srv_c = MeshServer(c["realm"], c["db"], port=0)
    url_b, url_c = srv_b.start(), srv_c.start()
    try:
        a["realm"].add_peer(url_b)   # A -> B
        b["realm"].add_peer(url_c)   # B -> C
        assert _has(c, rec) is False

        # Round 1: A gossips B (B gains A's record).
        mem_a = WhyMemory(a["db"], node_id=a["realm"].node_id, identity=a["ident"])
        gossip(a["realm"].peers, mem_a, a["realm"])
        mem_a.close()
        # Round 2: B gossips C (C gains A's record transitively).
        mem_b = WhyMemory(b["db"], node_id=b["realm"].node_id)
        gossip(b["realm"].peers, mem_b, b["realm"])
        mem_b.close()

        assert _has(c, rec) is True  # converged across the whole chain
    finally:
        srv_b.stop()
        srv_c.stop()


def test_gossip_tolerates_unreachable_peer(tmp_path):
    base = Realm.create("home")
    a = _node(tmp_path, "a", base.key_hex)
    b = _node(tmp_path, "b", base.key_hex)
    _record_on(a, tmp_path, "a", "hi")

    srv_b = MeshServer(b["realm"], b["db"], port=0)
    url_b = srv_b.start()
    try:
        a["realm"].add_peer(url_b)
        a["realm"].add_peer("http://127.0.0.1:1")  # nothing listening
        mem_a = WhyMemory(a["db"], node_id=a["realm"].node_id, identity=a["ident"])
        report = gossip(a["realm"].peers, mem_a, a["realm"], timeout=2.0)
        mem_a.close()
        # One good, one dead — the round completed without raising.
        assert report.reached == 1
        assert any(not p["ok"] for p in report.peers)
    finally:
        srv_b.stop()


def test_gossip_is_idempotent(tmp_path):
    base = Realm.create("home")
    a = _node(tmp_path, "a", base.key_hex)
    b = _node(tmp_path, "b", base.key_hex)
    _record_on(a, tmp_path, "a", "hi")

    srv_b = MeshServer(b["realm"], b["db"], port=0)
    url_b = srv_b.start()
    try:
        a["realm"].add_peer(url_b)
        mem_a = WhyMemory(a["db"], node_id=a["realm"].node_id, identity=a["ident"])
        first = gossip(a["realm"].peers, mem_a, a["realm"])
        second = gossip(a["realm"].peers, mem_a, a["realm"])
        mem_a.close()
        assert first.total_propagated >= 1
        assert second.total_propagated == 0  # nothing new the second time
    finally:
        srv_b.stop()


def test_gossip_converges_policy(tmp_path):
    # A defines a realm policy; it propagates to B's realm when A pushes to B's
    # server (the server's import merges shared policies into its realm object).
    base = Realm.create("home", policies=[
        {"name": "no-delete", "effect": "deny", "capability": "file.delete", "reason": "rule"}
    ])
    a = {"realm": base, "ident": NodeIdentity.create(), "db": tmp_path / "a.db"}
    _record_on(a, tmp_path, "a", "hi")
    b = _node(tmp_path, "b", base.key_hex)  # B joined with NO policies
    assert b["realm"].realm_policies() == []

    srv_b = MeshServer(b["realm"], b["db"], port=0)
    url_b = srv_b.start()
    try:
        a["realm"].add_peer(url_b)
        mem_a = WhyMemory(a["db"], node_id=a["realm"].node_id, identity=a["ident"])
        gossip(a["realm"].peers, mem_a, a["realm"])
        mem_a.close()
        # A's push imported its bundle into B's server, converging the policy
        # onto the realm object the server holds.
        assert any(p["capability"] == "file.delete" for p in b["realm"].policies)
    finally:
        srv_b.stop()
