"""Offline tests for REST-backed search adapters (Azure AI Search, Elasticsearch)."""
import json

from autarch.adapters.search_rest import (AzureAISearchAdapter,
                                          ElasticsearchAdapter)
from autarch.contracts import Action
from autarch.intelligence.embedding import HashingEmbedder


def test_azure_keyword_search_shapes_request_and_response():
    seen = {}

    def transport(url, body, headers, timeout):
        seen["url"] = url
        seen["key"] = headers.get("api-key")
        seen["payload"] = json.loads(body)
        return json.dumps({"value": [
            {"id": "1", "content": "refund policy details", "@search.score": 3.2, "category": "policy"},
            {"id": "2", "content": "shipping info", "@search.score": 1.1, "category": "logistics"},
        ]}).encode()

    a = AzureAISearchAdapter("https://svc.search.windows.net", "kb", "secret-key",
                             _transport=transport)
    r = a.execute(Action("search.query", {"query": "refund", "k": 2}))
    assert r.ok
    assert "indexes/kb/docs/search" in seen["url"]
    assert seen["key"] == "secret-key"
    assert seen["payload"]["search"] == "refund"
    hits = r.output["hits"]
    assert hits[0]["id"] == "1" and hits[0]["metadata"]["category"] == "policy"


def test_azure_vector_search_sends_embedding():
    seen = {}

    def transport(url, body, headers, timeout):
        seen["payload"] = json.loads(body)
        return json.dumps({"value": []}).encode()

    a = AzureAISearchAdapter("https://svc.search.windows.net", "kb", "k",
                             embedder=HashingEmbedder(dim=8), _transport=transport)
    a.execute(Action("search.query", {"query": "money back", "k": 3}))
    vq = seen["payload"]["vectorQueries"][0]
    assert vq["kind"] == "vector" and len(vq["vector"]) == 8


def test_elasticsearch_match_and_knn():
    def match_transport(url, body, headers, timeout):
        payload = json.loads(body)
        assert "match" in payload["query"]
        return json.dumps({"hits": {"hits": [
            {"_id": "a", "_score": 2.0, "_source": {"content": "hello", "tag": "x"}}]}}).encode()

    a = ElasticsearchAdapter("http://localhost:9200", "docs", _transport=match_transport)
    r = a.execute(Action("search.query", {"query": "hello"}))
    assert r.output["hits"][0]["id"] == "a"
    assert r.output["hits"][0]["metadata"]["tag"] == "x"

    def knn_transport(url, body, headers, timeout):
        payload = json.loads(body)
        assert "knn" in payload
        return json.dumps({"hits": {"hits": []}}).encode()

    a2 = ElasticsearchAdapter("http://localhost:9200", "docs",
                              embedder=HashingEmbedder(dim=8), _transport=knn_transport)
    assert a2.execute(Action("search.query", {"query": "x"})).ok


def test_min_score_and_k_cap_inherited():
    def transport(url, body, headers, timeout):
        return json.dumps({"value": [
            {"id": str(i), "content": f"doc{i}", "@search.score": 0.1} for i in range(20)
        ]}).encode()

    a = AzureAISearchAdapter("https://s.search.windows.net", "i", "k",
                             max_k=3, min_score=0.5, _transport=transport)
    r = a.execute(Action("search.query", {"query": "x", "k": 50}))
    # min_score filters everything (scores 0.1 < 0.5)
    assert r.output["hits"] == []
