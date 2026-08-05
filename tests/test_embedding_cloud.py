"""Tests for the cloud embedder and semantic recall (offline, no keys)."""
import json

import pytest

from autarch.errors import ModelError
from autarch.intelligence.embedding import EmbeddingProvider
from autarch.intelligence.factory import build_embedder
from autarch.intelligence.openai_embedding import OpenAIEmbedder
from autarch.intelligence.pricing import DEFAULT_PRICE_BOOK
from autarch.recall import SEMANTIC, RecallMemory


def _fake_transport(vectors_by_text):
    """Return a transport that maps each input text to a scripted vector."""
    def transport(body, headers, timeout):
        payload = json.loads(body)
        assert headers["Authorization"].startswith("Bearer ")
        data = [{"index": i, "embedding": vectors_by_text[t]}
                for i, t in enumerate(payload["input"])]
        return json.dumps({"data": data, "model": payload["model"]}).encode()
    return transport


def test_embed_request_and_response_shape():
    t = _fake_transport({"hello world": [0.1, 0.2, 0.3]})
    emb = OpenAIEmbedder(api_key="sk-test", _transport=t)
    vec = emb.embed("hello world")
    assert vec == [0.1, 0.2, 0.3]
    assert emb.dim == 3  # refined from the response


def test_embed_batch_preserves_order_even_if_api_reorders():
    def transport(body, headers, timeout):
        payload = json.loads(body)
        # deliberately return rows out of order
        data = [{"index": 1, "embedding": [9.0]}, {"index": 0, "embedding": [1.0]}]
        return json.dumps({"data": data, "model": payload["model"]}).encode()
    emb = OpenAIEmbedder(api_key="k", _transport=transport)
    assert emb.embed_batch(["a", "b"]) == [[1.0], [9.0]]


def test_missing_key_raises():
    with pytest.raises(ModelError):
        OpenAIEmbedder(api_key="").embed("hi")


def test_malformed_response_raises():
    emb = OpenAIEmbedder(api_key="k", _transport=lambda b, h, t: b'{"data": [{}]}')
    with pytest.raises(ModelError):
        emb.embed("hi")


def test_factory_builds_embedders_without_keys():
    assert build_embedder("hash").name.startswith("hash")
    assert build_embedder("hash:512").dim == 512
    assert build_embedder("openai").name.startswith("openai")
    assert build_embedder("openai:text-embedding-3-large").name.endswith("3-large")


def test_factory_passes_through_provider_objects():
    e = build_embedder("hash")
    assert build_embedder(e) is e


def test_embedding_pricing_present():
    # input-priced, output free
    assert DEFAULT_PRICE_BOOK.price("text-embedding-3-small") == (0.02, 0.0)


# --- the payoff: semantic recall finds meaning, not just shared words ---------

class StubEmbedder(EmbeddingProvider):
    """A tiny deterministic embedder where 'money back' ~ 'refund', not 'shipping'.

    This simulates what a real learned model does: paraphrases land near each
    other in vector space even with zero shared vocabulary.
    """

    name = "stub"
    dim = 3
    # axis 0 = refunds/money, axis 1 = shipping/delivery, axis 2 = noise
    _VECTORS = {
        "our refund policy allows returns within 30 days": [1.0, 0.0, 0.0],
        "packages ship in two business days": [0.0, 1.0, 0.0],
        "how do I get my money back": [0.95, 0.05, 0.0],  # paraphrase of refund
    }

    def embed(self, text):
        return self._VECTORS.get(text, [0.0, 0.0, 1.0])


def test_semantic_recall_beats_lexical(tmp_path):
    mem = RecallMemory(str(tmp_path / "recall.db"), embedder=StubEmbedder())
    mem.remember("our refund policy allows returns within 30 days", kind=SEMANTIC)
    mem.remember("packages ship in two business days", kind=SEMANTIC)

    # The query shares NO content words with the refund note ("money back" vs
    # "refund/returns"), so a purely lexical search would miss it. Semantics wins.
    hits = mem.recall("how do I get my money back", k=1)
    assert hits, "expected a recall hit"
    assert "refund policy" in hits[0].content
