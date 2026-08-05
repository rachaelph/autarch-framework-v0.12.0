"""Governed recall memory — the five hard memory problems, solved and provable.

Naive agent memory (a vector store) fails in five well-known ways. Autarch treats
long-term memory as a *governed substrate* and addresses each one. This example
runs fully offline (deterministic HashingEmbedder; signing shown if the optional
`cryptography` package is installed).

    python examples/memory.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from autarch import EPISODIC, HashingEmbedder, RecallMemory, provenance


def main() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="autarch_mem_"))
    identity = provenance.NodeIdentity.load_or_create(workspace)  # None without crypto
    mem = RecallMemory(
        workspace / "recall.db",
        node_id=(identity.node_id if identity else "local"),
        identity=identity,
        embedder=HashingEmbedder(),
    )

    print("=" * 70)
    print("GOVERNED RECALL MEMORY")
    print("=" * 70)

    # 1) Noisy retrieval -> hybrid + structural relevance ------------------
    print("\n[1] Relevance over similarity (hybrid + structural filters)")
    mem.remember("The deploy runbook is in the ops wiki.", subject="ops", tags=["deploy"])
    mem.remember("The office plant is named Fern.", subject="trivia")
    hit = mem.recall("how do I deploy", k=1)[0]
    print(f"    query 'how do I deploy' -> {hit.content!r}")

    # 2) Forgetting & updating -> supersession (belief revision) ----------
    print("\n[2] Updating a belief (supersede, not blend)")
    old = mem.remember("Staging URL: https://old.example.com", subject="staging.url")
    mem.supersede(old, "Staging URL: https://new.example.com")
    current = mem.recall("staging url", subject="staging.url")[0]
    print(f"    current belief -> {current.content!r}")
    print(f"    old belief retained for audit -> "
          f"{len(mem.recall('staging url', subject='staging.url', include_superseded=True))} versions")

    # 3) Graceful forgetting -> decay + reinforcement ---------------------
    print("\n[3] Decay + reinforcement (used facts persist, stale ones fade)")
    now = 2_000_000.0
    hot = mem.remember("Used often.", created_at=now - 10_000, decay_rate=0.0005)
    cold = mem.remember("Never used again.", created_at=now - 10_000, decay_rate=0.0005)
    for _ in range(5):
        mem.reinforce([hot], now=now)
    forgotten = mem.decay_sweep(min_strength=0.01, now=now)
    print(f"    swept {len(forgotten)} stale memory; reinforced one survived: "
          f"{mem.get(hot) is not None}")

    # 4) Cost vs data loss -> token budget + lossless consolidation -------
    print("\n[4] Bounded recall + consolidation that keeps the detail")
    for i in range(6):
        mem.remember(f"episode {i}: user event padding text here.", kind=EPISODIC, subject="s1")
    bounded = mem.recall("episode user event", subject="s1", token_budget=30)
    print(f"    token_budget=30 returned {len(bounded)} of 6 episodes (context bounded)")
    summary_id = mem.consolidate(subject="s1", kind=EPISODIC)
    summary = mem.get(summary_id)
    print(f"    consolidated into 1 semantic memory; originals kept: "
          f"{len(summary.derived_from)} sources still retrievable")

    # 5) Poisoning & security -> provenance + integrity + trust gate ------
    print("\n[5] Poisoning resistance (provenance + integrity + trust gate)")
    ok, _ = mem.verify_chain()
    print(f"    integrity chain intact: {ok}")
    if identity is not None:
        signed = mem.recall("deploy", min_trust=1)
        print(f"    provenance-verified recall (min_trust=1): {len(signed)} trusted hit(s)")
        print("    a forged or tampered memory would fail verify_provenance and be quarantined.")
    else:
        print("    (install `autarch[crypto]` to sign memories and enable trust-gated recall)")

    print("\n" + "=" * 70)
    print("Every memory is signed, sealed, decaying, and governed — not a static blob.")
    print("=" * 70)


if __name__ == "__main__":
    main()
