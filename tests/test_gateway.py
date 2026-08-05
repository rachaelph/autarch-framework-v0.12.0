"""Tests for the governance gateway (in-process; no real sockets needed)."""
import pytest

from autarch.agent import capability
from autarch.gateway import GovernanceGateway
from autarch.policy import Policy, PolicyEffect


@pytest.fixture
def gw(tmp_path):
    return GovernanceGateway(
        grants=[
            capability("file.write", scope={"path_prefix": "."}),
            capability("file.delete"),
        ],
        workspace=str(tmp_path),
        policies=[Policy(name="del-human", effect=PolicyEffect.REQUIRE_RATIFY.value,
                         capability="file.delete")],
    )


def test_health_and_capabilities(gw):
    h = gw.health()
    assert h["ok"] and "file.write" in h["capabilities"]
    caps = {c["name"] for c in gw.capabilities()}
    assert {"file.write", "file.delete"} <= caps


def test_governed_write_executes(gw):
    out = gw.enact({"capability": "file.write", "params": {"path": "n.txt", "content": "hi"}})
    assert out["status"] == "executed" and out["executed"]
    assert out["why_id"]


def test_ungranted_capability_denied(gw):
    out = gw.enact({"capability": "file.read", "params": {"path": "n.txt"}})
    assert out["status"] == "denied"
    assert "no grant" in out["gate_reason"]


def test_delete_parks_for_approval_then_executes(gw):
    gw.enact({"capability": "file.write", "params": {"path": "n.txt", "content": "hi"}})
    parked = gw.enact({"capability": "file.delete", "params": {"path": "n.txt"}})
    assert parked["status"] == "pending_approval"
    appr_id = parked["approval_id"]
    assert len(gw.pending_approvals()) == 1
    done = gw.ratify(appr_id, by="alice")
    assert done["executed"] is True


def test_overrule_blocks_execution(gw):
    parked = gw.enact({"capability": "file.delete", "params": {"path": "n.txt"}})
    out = gw.overrule(parked["approval_id"], by="boss", reason="not now")
    assert out["status"] == "overruled"


def test_prove_returns_signed_record(gw):
    out = gw.enact({"capability": "file.write", "params": {"path": "n.txt", "content": "hi"}})
    proof = gw.prove(out["why_id"])
    assert proof["found"] and proof["integrity_ok"]


def test_prove_unknown_id(gw):
    assert gw.prove("nope")["found"] is False
