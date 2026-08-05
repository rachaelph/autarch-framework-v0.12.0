"""Per-origin memory tests — the foundation that lets mesh ledgers merge."""
from autarch.contracts import WhyRecord
from autarch.memory import WhyMemory


def _rec(intent="do x"):
    return WhyRecord(
        intent_text=intent, capability="file.write", params={"path": "a.txt"},
        rationale="r", proposer="mock", challenger="mock",
        critique_verdict="approve", critique_reasons="ok",
        gate_allowed=True, gate_reason="granted", human_decision="ratify",
        executed=True, result_ok=True, result_output="done", result_error=None, undo=None,
    )


def test_records_carry_origin(tmp_path):
    mem = WhyMemory(tmp_path / "m.db", node_id="node_a")
    rid = mem.record(_rec())
    assert mem.origins() == ["node_a"]
    assert mem.has(rid)


def test_two_origins_each_verify(tmp_path):
    # Simulate a merged ledger: records from two different nodes in one store.
    a = WhyMemory(tmp_path / "m.db", node_id="node_a")
    a.record(_rec("from a 1"))
    a.record(_rec("from a 2"))

    # A second logical node writing into the SAME store (as if imported).
    b = WhyMemory(tmp_path / "m.db", node_id="node_b")
    b.record(_rec("from b 1"))

    ok, broken = b.verify_chain()
    assert ok is True and broken is None
    assert set(b.origins()) == {"node_a", "node_b"}


def test_import_row_is_union(tmp_path):
    source = WhyMemory(tmp_path / "src.db", node_id="node_a")
    rid = source.record(_rec("shared"))
    row = source.export_rows()[0]

    dest = WhyMemory(tmp_path / "dst.db", node_id="node_b")
    assert dest.import_row(row) is True   # added
    assert dest.import_row(row) is False  # idempotent: already present
    assert dest.has(rid)
    # Imported row keeps its author's origin and verifies under it.
    assert dest.origins() == ["node_a"]
    assert dest.verify(rid) is True


def test_default_origin_is_local(tmp_path):
    mem = WhyMemory(tmp_path / "m.db")
    mem.record(_rec())
    assert mem.origins() == ["local"]
