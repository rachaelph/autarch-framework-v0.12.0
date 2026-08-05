"""Compliance tests — audit export, RTBF redaction, retention."""
from autarch import Agent, capability
from autarch.contracts import WhyRecord
from autarch.memory import WhyMemory


def _rec(intent="contains 123-45-6789"):
    return WhyRecord(
        intent_text=intent, capability="file.write", params={"path": "a.txt", "content": "secret"},
        rationale="r", proposer="mock", challenger="mock", critique_verdict="approve",
        critique_reasons="ok", gate_allowed=True, gate_reason="granted", human_decision="ratify",
        executed=True, result_ok=True, result_output="done", result_error=None, undo=None,
    )


def test_redaction_masks_fields(tmp_path):
    mem = WhyMemory(tmp_path / "m.db")
    rid = mem.record(_rec())
    mem.redact(rid)
    rec = mem.get(rid)
    assert rec.intent_text == "[redacted]"
    assert rec.params == {"[redacted]": True}
    assert mem.is_redacted(rid) is True


def test_redaction_preserves_chain_integrity(tmp_path):
    mem = WhyMemory(tmp_path / "m.db")
    rid = mem.record(_rec())
    mem.record(_rec("another"))
    assert mem.verify_chain()[0] is True
    mem.redact(rid)
    # The sealed payload is untouched -> the ledger STILL verifies.
    assert mem.verify_chain()[0] is True
    assert mem.verify(rid) is True


def test_redaction_preserves_provenance(tmp_path):
    import pytest
    pytest.importorskip("cryptography")
    from autarch.provenance import NodeIdentity

    ident = NodeIdentity.create()
    mem = WhyMemory(tmp_path / "m.db", node_id=ident.node_id, identity=ident)
    rid = mem.record(_rec())
    mem.redact(rid)
    # Authorship proof survives redaction (we masked PII, not the signature).
    assert mem.verify_provenance(rid) is True


def test_redact_unknown_returns_zero(tmp_path):
    mem = WhyMemory(tmp_path / "m.db")
    assert mem.redact("why_missing") == 0


def test_export_audit_includes_seals_and_redaction(tmp_path):
    mem = WhyMemory(tmp_path / "m.db")
    rid = mem.record(_rec())
    mem.redact(rid, fields=("intent_text",))
    rows = mem.export_audit()
    assert len(rows) == 1
    assert rows[0]["seal"] is not None
    assert rows[0]["redacted_fields"] == ["intent_text"]
    assert rows[0]["record"]["intent_text"] == "[redacted]"


def test_export_audit_to_file(tmp_path):
    import json

    mem = WhyMemory(tmp_path / "m.db")
    mem.record(_rec())
    path = tmp_path / "audit.jsonl"
    mem.export_audit(str(path))
    assert path.exists()
    line = json.loads(path.read_text().strip())
    assert "seal" in line and "record" in line


def test_prune_deletes_old_records(tmp_path):
    import time

    mem = WhyMemory(tmp_path / "m.db")
    rid = mem.record(_rec())
    # Backdate the record so it falls outside the retention window.
    mem._conn.execute("UPDATE why SET created_at = ? WHERE id = ?", (time.time() - 10_000, rid))
    mem._conn.commit()
    pruned = mem.prune(older_than_seconds=3600)
    assert pruned == 1
    assert mem.count() == 0


def test_count(tmp_path):
    mem = WhyMemory(tmp_path / "m.db")
    assert mem.count() == 0
    mem.record(_rec())
    assert mem.count() == 1


def test_agent_redaction_end_to_end(tmp_path):
    agent = Agent(
        intent="create pii.txt that says 123-45-6789", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})], workspace=tmp_path,
    )
    result = agent.run()
    agent.memory.redact(result.why_id, reason="data subject request")
    assert agent.memory.get(result.why_id).intent_text == "[redacted]"
    assert agent.memory.verify_chain()[0] is True
