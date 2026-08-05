"""Why-Memory integrity tests — the tamper-evident hash chain."""
import json

from autarch.contracts import WhyRecord
from autarch.memory import WhyMemory


def _record(intent="do x", capability="file.write", executed=True):
    return WhyRecord(
        intent_text=intent,
        capability=capability,
        params={"path": "a.txt"},
        rationale="r",
        proposer="mock",
        challenger="mock",
        critique_verdict="approve",
        critique_reasons="ok",
        gate_allowed=True,
        gate_reason="granted",
        human_decision="ratify",
        executed=executed,
        result_ok=True,
        result_output="done",
        result_error=None,
        undo=None,
    )


def test_record_is_sealed_and_verifies(tmp_path):
    mem = WhyMemory(tmp_path / "why.db")
    rid = mem.record(_record())
    assert mem.verify(rid) is True
    assert mem.get_seal(rid) is not None


def test_chain_intact_for_multiple_records(tmp_path):
    mem = WhyMemory(tmp_path / "why.db")
    for i in range(5):
        mem.record(_record(intent=f"do {i}"))
    ok, broken = mem.verify_chain()
    assert ok is True
    assert broken is None


def test_tamper_is_detected(tmp_path):
    mem = WhyMemory(tmp_path / "why.db")
    rid = mem.record(_record(intent="original"))
    mem.record(_record(intent="second"))

    # Tamper directly with the stored payload, bypassing the API.
    tampered = json.dumps({**json.loads(
        mem._conn.execute("SELECT payload FROM why WHERE id = ?", (rid,)).fetchone()["payload"]
    ), "intent_text": "HACKED"})
    mem._conn.execute("UPDATE why SET payload = ? WHERE id = ?", (tampered, rid))
    mem._conn.commit()

    assert mem.verify(rid) is False
    ok, broken = mem.verify_chain()
    assert ok is False
    assert broken == rid


def test_deletion_breaks_chain(tmp_path):
    mem = WhyMemory(tmp_path / "why.db")
    mem.record(_record(intent="first"))
    rid2 = mem.record(_record(intent="second"))
    mem.record(_record(intent="third"))

    # Remove the middle record -> linkage from third no longer matches.
    mem._conn.execute("DELETE FROM why WHERE id = ?", (rid2,))
    mem._conn.commit()

    ok, broken = mem.verify_chain()
    assert ok is False


def test_verify_unknown_is_none(tmp_path):
    mem = WhyMemory(tmp_path / "why.db")
    assert mem.verify("why_does_not_exist") is None


def test_legacy_unsealed_db_does_not_crash(tmp_path):
    # Simulate a pre-Phase-3 database: a 'why' table without seal columns.
    import sqlite3

    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE why (id TEXT PRIMARY KEY, created_at REAL, intent_text TEXT, "
        "capability TEXT, executed INTEGER, payload TEXT NOT NULL)"
    )
    payload = json.dumps({
        "intent_text": "old", "capability": "file.write", "params": {}, "rationale": "",
        "proposer": "mock", "challenger": "mock", "critique_verdict": "approve",
        "critique_reasons": "", "gate_allowed": True, "gate_reason": "", "human_decision": "ratify",
        "executed": True, "result_ok": True, "result_output": "x", "result_error": None,
        "undo": None, "id": "why_legacy", "created_at": 1.0,
    })
    conn.execute(
        "INSERT INTO why (id, created_at, intent_text, capability, executed, payload) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("why_legacy", 1.0, "old", "file.write", 1, payload),
    )
    conn.commit()
    conn.close()

    # Opening it should migrate (add seal columns) without error.
    mem = WhyMemory(db)
    assert mem.get("why_legacy") is not None
    assert mem.verify("why_legacy") is None  # legacy row is unsealed
    # New records still seal and the chain verifies (legacy row is skipped).
    mem.record(_record(intent="new after legacy"))
    ok, _ = mem.verify_chain()
    assert ok is True
