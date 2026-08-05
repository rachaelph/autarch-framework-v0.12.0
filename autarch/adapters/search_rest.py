"""REST-backed search adapters — governed access to external AI search indexes.

Subclasses of :class:`~autarch.adapters.search.SearchAdapter` that call a real
search service over ``urllib`` (no third-party dependency). Governance,
capability-scoping, top-k caps, min-score filtering, and the audit trail are
inherited; each subclass only implements the HTTP request/response shaping for
its service. The transport is injectable so shaping is unit-testable offline.

Shipped:
  * :class:`AzureAISearchAdapter`   — Azure AI Search (semantic/vector/keyword).
  * :class:`ElasticsearchAdapter`   — Elasticsearch/OpenSearch (kNN or query DSL).
  * :class:`RestSearchAdapter`      — a base for any JSON search API (Pinecone,
                                      Weaviate, pgvector-behind-HTTP, ...).

Honest boundary: these are governed *seams*, tested offline against recorded
response shapes. Live behavior depends on your service, index, and credentials.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, List, Optional

from ..errors import AdapterError
from .search import SearchAdapter, SearchHit


class RestSearchAdapter(SearchAdapter):
    """Base for search services with a JSON-over-HTTP query API."""

    name = "rest-search"

    def __init__(
        self,
        *,
        max_k: int = 10,
        min_score: float = 0.0,
        timeout: float = 30.0,
        _transport: Optional[Callable[[str, bytes, dict, float], bytes]] = None,
    ):
        super().__init__(max_k=max_k, min_score=min_score)
        self._timeout = timeout
        # seam for tests: (url, body, headers, timeout) -> response bytes
        self._transport = _transport or self._http

    def _http(self, url: str, body: bytes, headers: dict, timeout: float) -> bytes:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise AdapterError(
                f"{self.name} HTTP {exc.code}: {exc.reason}",
                context={"status": exc.code},
            ) from exc
        except urllib.error.URLError as exc:
            raise AdapterError(f"{self.name} unreachable: {exc.reason}") from exc

    def _post(self, url: str, payload: dict, headers: dict) -> dict:
        raw = self._transport(url, json.dumps(payload).encode("utf-8"), headers, self._timeout)
        return json.loads(raw.decode("utf-8"))


class AzureAISearchAdapter(RestSearchAdapter):
    """Governed queries against an Azure AI Search index.

    Set ``vector_query`` with an embedder to do vector search; otherwise a
    keyword/semantic text search is issued. ``text_field`` names the field whose
    content is returned as each hit's text.
    """

    name = "azure-search"

    def __init__(
        self,
        endpoint: str,
        index: str,
        api_key: str,
        *,
        api_version: str = "2023-11-01",
        text_field: str = "content",
        id_field: str = "id",
        embedder=None,
        vector_field: str = "contentVector",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._url = (
            f"{endpoint.rstrip('/')}/indexes/{index}/docs/search"
            f"?api-version={api_version}"
        )
        self._api_key = api_key
        self._text_field = text_field
        self._id_field = id_field
        self._embedder = embedder
        self._vector_field = vector_field

    def search(self, query: str, k: int) -> List[SearchHit]:
        payload: dict = {"top": k}
        if self._embedder is not None:
            payload["vectorQueries"] = [{
                "kind": "vector",
                "vector": self._embedder.embed(query),
                "fields": self._vector_field,
                "k": k,
            }]
        else:
            payload["search"] = query
        headers = {"Content-Type": "application/json", "api-key": self._api_key}
        data = self._post(self._url, payload, headers)
        hits = []
        for row in data.get("value", []):
            hits.append(SearchHit(
                id=str(row.get(self._id_field, "")),
                text=str(row.get(self._text_field, "")),
                score=float(row.get("@search.score", 0.0)),
                metadata={key: value for key, value in row.items()
                          if not key.startswith("@") and key != self._text_field},
            ))
        return hits


class ElasticsearchAdapter(RestSearchAdapter):
    """Governed queries against Elasticsearch/OpenSearch (kNN or match)."""

    name = "elasticsearch"

    def __init__(
        self,
        endpoint: str,
        index: str,
        *,
        api_key: Optional[str] = None,
        text_field: str = "content",
        embedder=None,
        vector_field: str = "content_vector",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._url = f"{endpoint.rstrip('/')}/{index}/_search"
        self._api_key = api_key
        self._text_field = text_field
        self._embedder = embedder
        self._vector_field = vector_field

    def search(self, query: str, k: int) -> List[SearchHit]:
        if self._embedder is not None:
            payload = {"knn": {"field": self._vector_field,
                               "query_vector": self._embedder.embed(query),
                               "k": k, "num_candidates": max(k * 10, 100)},
                       "size": k}
        else:
            payload = {"query": {"match": {self._text_field: query}}, "size": k}
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"ApiKey {self._api_key}"
        data = self._post(self._url, payload, headers)
        hits = []
        for row in data.get("hits", {}).get("hits", []):
            src = row.get("_source", {})
            hits.append(SearchHit(
                id=str(row.get("_id", "")),
                text=str(src.get(self._text_field, "")),
                score=float(row.get("_score", 0.0)),
                metadata={k: v for k, v in src.items() if k != self._text_field},
            ))
        return hits
