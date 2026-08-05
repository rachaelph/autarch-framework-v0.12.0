"""Tests for the async approval plane."""
import os

import pytest

from autarch.approval import (EXPIRED, OVERRULED, PENDING, RATIFIED,
                              Approval, ApprovalQueue)


def _queue(tmp_path):
    return ApprovalQueue(os.path.join(tmp_path, "approvals.db"))


def test_submit_and_ratify_single(tmp_path):
    q = _queue(tmp_path)
    a = q.request("delete logs", "file.delete", {"path": "logs"})
    assert a.pending
    out = q.ratify(a.id, by="alice")
    assert out.status == RATIFIED and out.decided_by == "alice"


def test_quorum_requires_multiple_votes(tmp_path):
    q = _queue(tmp_path)
    a = q.request("wire funds", "payment.send", {"amount": 9000}, quorum=2)
    assert q.ratify(a.id, by="alice").status == PENDING
    assert q.ratify(a.id, by="alice").status == PENDING  # same voter doesn't double-count
    assert q.ratify(a.id, by="bob").status == RATIFIED


def test_overrule_decides_immediately(tmp_path):
    q = _queue(tmp_path)
    a = q.request("wire funds", "payment.send", {"amount": 9000}, quorum=3)
    out = q.overrule(a.id, by="cfo", reason="over limit")
    assert out.status == OVERRULED and out.decision_reason == "over limit"


def test_cannot_ratify_after_overrule(tmp_path):
    q = _queue(tmp_path)
    a = q.request("x", "y")
    q.overrule(a.id, by="boss")
    # further votes are ignored; status stays OVERRULED
    assert q.ratify(a.id, by="alice").status == OVERRULED


def test_ttl_expiry(tmp_path):
    q = _queue(tmp_path)
    a = q.request("x", "y", ttl_seconds=-1)  # already expired
    assert q.pending_list() == []
    assert q.ratify(a.id, by="alice").status == EXPIRED


def test_pending_list_and_get(tmp_path):
    q = _queue(tmp_path)
    a = q.request("a", "cap.a")
    q.request("b", "cap.b")
    assert len(q.pending_list()) == 2
    assert q.get(a.id).capability == "cap.a"


def test_wait_returns_on_decision(tmp_path):
    q = _queue(tmp_path)
    a = q.request("x", "y")
    q.ratify(a.id, by="alice")
    out = q.wait(a.id, timeout=1.0)
    assert out.ratified


def test_missing_approval_raises(tmp_path):
    q = _queue(tmp_path)
    with pytest.raises(KeyError):
        q.ratify("nope")
