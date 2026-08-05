"""Governed recall memory — tests for all five agent-memory problems.

Each test maps to one of the failure modes that sink naive vector-store memory:
forgetting/updating, noisy retrieval, cost/context blow-up, multi-agent
coordination, and memory poisoning. Everything here runs offline and
deterministically (the semantic path uses the dependency-free HashingEmbedder).
"""
from __future__ import annotations

import pytest

from autarch import (
    Agent,
    CapabilityDenied,
    EPISODIC,
    HashingEmbedder,
    MemoryEntry,
    RecallMemory,
    SEMANTIC,
    capability,
)
from autarch.recall import effective_strength


def _mem(tmp_path, **kwargs) -> RecallMemory:
    return RecallMemory(tmp_path / "recall.db", **kwargs)


# -- Problem 2: noisy retrieval — relevance beats similarity -------------------
def test_remember_and_recall_ranks_relevant_first(tmp_path):
    mem = _mem(tmp_path)
    mem.remember("The deployment runbook lives in the ops wiki.", subject="ops")
    mem.remember("The CEO's dog is named Biscuit.", subject="trivia")
    hits = mem.recall("where is the deployment runbook", k=1)
    assert len(hits) == 1
    assert "runbook" in hits[0].content


def test_structural_filter_excludes_keyword_noise(tmp_path):
    # Two memories share the keyword "budget"; only one is logically about Q3
    # finance. Structural filtering returns the relevant one, not the lookalike.
    mem = _mem(tmp_path)
    mem.remember("Q3 marketing budget is $50k.", subject="finance.q3")
    mem.remember("Budget extra time for the flaky test suite.", subject="engineering")
    hits = mem.recall("budget", subject="finance.q3")
    assert [h.subject for h in hits] == ["finance.q3"]


def test_semantic_path_runs_with_embedder(tmp_path):
    mem = _mem(tmp_path, embedder=HashingEmbedder(dim=128))
    mem.remember("Postgres connection pooling settings for production.")
    mem.remember("Favorite pizza toppings among the team.")
    hits = mem.recall("production postgres pooling", k=1)
    assert "Postgres" in hits[0].content
    assert hits[0].embedding is not None  # embedding was computed and stored


# -- Problem 1: forgetting & updating -----------------------------------------
def test_supersede_returns_current_belief(tmp_path):
    mem = _mem(tmp_path)
    old = mem.remember("The staging URL is https://old.example.com", subject="staging.url")
    mem.supersede(old, "The staging URL is https://new.example.com")

    current = mem.recall("staging url", subject="staging.url")
    assert len(current) == 1
    assert "new.example.com" in current[0].content

    history = mem.recall("staging url", subject="staging.url", include_superseded=True)
    assert len(history) == 2  # the old belief is retired, not lost


def test_decay_lowers_strength_and_sweep_forgets(tmp_path):
    mem = _mem(tmp_path)
    now = 1_000_000.0
    stale = mem.remember("Transient note.", created_at=now - 10_000, decay_rate=0.001)
    fresh = mem.remember("Durable fact.", created_at=now, decay_rate=0.0)

    assert effective_strength(mem.get(stale), now) < 0.01
    assert effective_strength(mem.get(fresh), now) >= 1.0

    forgotten = mem.decay_sweep(min_strength=0.01, now=now)
    assert stale in forgotten and fresh not in forgotten
    assert mem.get(stale) is None and mem.get(fresh) is not None


def test_reinforcement_keeps_used_memory_alive(tmp_path):
    mem = _mem(tmp_path)
    now = 1_000_000.0
    used = mem.remember("Frequently needed.", created_at=now - 10_000, decay_rate=0.0005)
    unused = mem.remember("Rarely needed.", created_at=now - 10_000, decay_rate=0.0005)

    for _ in range(5):
        mem.reinforce([used], now=now)

    forgotten = mem.decay_sweep(min_strength=0.01, now=now)
    assert unused in forgotten
    assert used not in forgotten  # reinforcement lifted it above the floor


def test_recall_reinforces_returned_memories(tmp_path):
    mem = _mem(tmp_path)
    mid = mem.remember("Reinforce me on recall.")
    assert mem.get(mid).use_count == 0
    mem.recall("reinforce")
    assert mem.get(mid).use_count == 1


# -- Problem 3: cost scaling vs data loss -------------------------------------
def test_recall_respects_token_budget(tmp_path):
    mem = _mem(tmp_path)
    for i in range(6):
        mem.remember("shared topic entry number " + str(i) + " padding.")  # ~40 chars

    unbounded = mem.recall("shared topic", k=10)
    bounded = mem.recall("shared topic", token_budget=25)
    assert len(unbounded) == 6
    assert 0 < len(bounded) < len(unbounded)  # context can't blow up


def test_consolidate_preserves_originals(tmp_path):
    mem = _mem(tmp_path)
    a = mem.remember("User clicked checkout.", kind=EPISODIC, subject="session42")
    b = mem.remember("User abandoned cart.", kind=EPISODIC, subject="session42")

    new_id = mem.consolidate(subject="session42", kind=EPISODIC)
    assert new_id is not None
    summary = mem.get(new_id)
    assert summary.kind == SEMANTIC
    assert set(summary.derived_from) == {a, b}
    # The granular originals survive — summarization did not destroy detail.
    assert mem.get(a) is not None and mem.get(b) is not None


# -- Problem 4: multi-agent coordination --------------------------------------
def test_scope_isolation(tmp_path):
    mem = _mem(tmp_path)
    mem.remember("Private to agent A.", scope="private:A")
    mem.remember("Shared in the realm.", scope="realm")
    assert len(mem.recall("agent", scope="private:A")) == 1
    assert len(mem.recall("shared", scope="realm")) == 1
    assert all(e.scope == "realm" for e in mem.recall("shared", scope="realm"))


def test_export_import_roundtrip_shares_memory(tmp_path):
    node_a = RecallMemory(tmp_path / "a.db")
    node_b = RecallMemory(tmp_path / "b.db")
    node_a.remember("Institutional knowledge from A.", subject="shared")

    rows = node_a.export_rows()
    added = [node_b.import_row(row) for row in rows]
    assert all(added)  # every foreign memory merged
    assert node_b.import_row(rows[0]) is False  # idempotent (grow-only set)

    hits = node_b.recall("institutional knowledge", subject="shared")
    assert len(hits) == 1
    ok, _ = node_b.verify_chain()
    assert ok


# -- Problem 5: poisoning & security ------------------------------------------
def test_integrity_chain_detects_tampering(tmp_path):
    mem = _mem(tmp_path)
    first = mem.remember("Original, trusted fact.")
    mem.remember("A second fact.")
    ok, broken = mem.verify_chain()
    assert ok and broken is None

    # Tamper with a stored memory directly (simulating a poisoned row).
    mem._conn.execute("UPDATE mem SET payload = ? WHERE id = ?", ("poisoned", first))
    mem._conn.commit()
    ok, broken = mem.verify_chain()
    assert not ok and broken == first


def test_min_trust_quarantines_unsigned_memory(tmp_path):
    mem = _mem(tmp_path)  # no identity -> memories are unsigned
    mem.remember("Cannot prove where this came from.")
    assert len(mem.recall("prove", min_trust=0)) == 1   # allowed when trust not required
    assert len(mem.recall("prove", min_trust=1)) == 0   # quarantined when trust required


def test_provenance_signed_and_forgery_detected(tmp_path):
    pytest.importorskip("cryptography")
    from autarch import NodeIdentity

    identity = NodeIdentity.create()
    mem = RecallMemory(tmp_path / "signed.db", node_id=identity.node_id, identity=identity)
    mid = mem.remember("Authentic, signed memory.")
    assert mem.verify_provenance(mid) is True
    assert len(mem.recall("authentic", min_trust=1)) == 1

    # Forge the signature -> provenance fails and recall quarantines it.
    mem._conn.execute("UPDATE mem SET signature = ? WHERE id = ?", ("00" * 64, mid))
    mem._conn.commit()
    assert mem.verify_provenance(mid) is False
    assert len(mem.recall("authentic", min_trust=1)) == 0


# -- Agent SDK integration ----------------------------------------------------
def test_agent_remember_and_recall(tmp_path):
    agent = Agent("note the api base url", workspace=tmp_path)
    agent.remember("The API base URL is https://api.example.com", subject="api.url")
    hits = agent.recall("api base url")
    assert any("api.example.com" in h.content for h in hits)


def test_agent_governed_write_denied_without_grant(tmp_path):
    agent = Agent("try to write memory", workspace=tmp_path)
    with pytest.raises(CapabilityDenied):
        agent.remember("should be blocked", govern=True)


def test_agent_governed_write_allowed_with_grant(tmp_path):
    agent = Agent(
        "write governed memory",
        grants=[capability("memory.write")],
        workspace=tmp_path,
    )
    mid = agent.remember("permitted memory", govern=True)
    assert agent.recall("permitted")[0].id == mid


def test_spawn_shares_recall_memory(tmp_path):
    parent = Agent("parent", grants=[capability("file.read")], workspace=tmp_path)
    parent.remember("Shared fact about the zeta subsystem.", subject="zeta")
    child = parent.spawn("child task")
    assert child.recall_memory is parent.recall_memory
    hits = child.recall("zeta subsystem", subject="zeta")
    assert len(hits) == 1
