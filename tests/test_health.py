"""Health/readiness tests."""
from autarch import Agent, capability, health_check
from autarch.health import STATUS_ERROR, STATUS_OK


def test_health_empty_workspace(tmp_path):
    report = health_check(tmp_path)
    assert report["status"] in (STATUS_OK, "degraded")
    assert report["checks"]["storage"]["ok"] is True
    assert report["checks"]["ledger"]["ok"] is True
    assert "version" in report


def test_health_after_a_run(tmp_path):
    Agent(
        intent="create a.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})], workspace=tmp_path,
    ).run()
    report = health_check(tmp_path)
    assert report["checks"]["storage"]["records"] >= 1
    assert report["checks"]["ledger"]["ok"] is True


def test_health_reports_identity_presence(tmp_path):
    import pytest
    pytest.importorskip("cryptography")
    Agent(
        intent="create a.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})], workspace=tmp_path,
    ).run()
    report = health_check(tmp_path)
    assert report["checks"]["identity"]["present"] is True


def test_health_detects_tampered_ledger(tmp_path):
    import json

    agent = Agent(
        intent="create a.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})], workspace=tmp_path,
    )
    result = agent.run()
    # Tamper with the sealed payload directly.
    conn = agent.memory._conn
    row = conn.execute("SELECT payload FROM why WHERE id=?", (result.why_id,)).fetchone()
    bad = json.dumps({**json.loads(row["payload"]), "intent_text": "HACKED"})
    conn.execute("UPDATE why SET payload=? WHERE id=?", (bad, result.why_id))
    conn.commit()
    agent.memory.close()

    report = health_check(tmp_path)
    assert report["checks"]["ledger"]["ok"] is False
    assert report["status"] == STATUS_ERROR
