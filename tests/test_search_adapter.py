"""Tests for the governed search / vector-index adapter."""
from autarch.adapters.search import SearchAdapter, VectorSearchAdapter
from autarch.agent import Agent, capability
from autarch.contracts import Action
from autarch.intelligence.embedding import EmbeddingProvider


class StubEmbedder(EmbeddingProvider):
    """money/refund near axis 0, shipping near axis 1 — paraphrase-aware."""

    name = "stub"
    dim = 3
    _V = {
        "our refund policy allows returns within 30 days": [1.0, 0.0, 0.0],
        "packages ship in two business days": [0.0, 1.0, 0.0],
        "how do I get my money back": [0.96, 0.02, 0.0],
    }

    def embed(self, text):
        return self._V.get(text, [0.0, 0.0, 1.0])


def _index():
    a = VectorSearchAdapter(StubEmbedder(), max_k=5)
    a.index("d1", "our refund policy allows returns within 30 days", {"kind": "policy"})
    a.index("d2", "packages ship in two business days", {"kind": "logistics"})
    return a


def test_semantic_search_finds_paraphrase():
    a = _index()
    r = a.execute(Action("search.query", {"query": "how do I get my money back", "k": 1}))
    assert r.ok
    hits = r.output["hits"]
    assert hits[0]["id"] == "d1"  # refund note, despite zero shared words
    assert hits[0]["metadata"]["kind"] == "policy"


def test_k_is_capped_by_max_k():
    a = VectorSearchAdapter(StubEmbedder(), max_k=1)
    a.index_many([("d1", "our refund policy allows returns within 30 days"),
                  ("d2", "packages ship in two business days")])
    r = a.execute(Action("search.query", {"query": "how do I get my money back", "k": 10}))
    assert len(r.output["hits"]) == 1


def test_min_score_filters_noise():
    a = VectorSearchAdapter(StubEmbedder(), min_score=0.5)
    a.index("d1", "our refund policy allows returns within 30 days")
    # a query orthogonal to everything indexed -> filtered out
    r = a.execute(Action("search.query", {"query": "totally unrelated gibberish xyz"}))
    assert r.output["hits"] == []


def test_missing_query_errors():
    a = _index()
    assert not a.execute(Action("search.query", {})).ok


def test_base_adapter_requires_implementation():
    import pytest
    base = SearchAdapter()
    with pytest.raises(NotImplementedError):
        base.search("q", 3)


def test_governed_through_kernel(tmp_path):
    a = _index()
    agent = Agent(
        intent="find relevant docs",
        grants=[capability("search.query")],
        adapters=[a],
        workspace=str(tmp_path),
        auto_preside=False,
    )
    ok = agent.enact("search.query", {"query": "how do I get my money back", "k": 1})
    assert ok.executed
    # an ungranted capability on the same adapter is denied by the kernel
    denied = agent.enact("search.admin", {"action": "wipe"})
    assert not denied.executed
