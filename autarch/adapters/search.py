"""Search adapters — governed access to AI search / vector indexes.

The same governance the kernel gives file and SQL access, applied to *semantic*
search: an agent can query a vector index for relevant data, but only through a
granted, scoped, audited ``search.query`` capability. Results per query are
capped, an optional minimum relevance filters noise, and every search is recorded
in the signed ledger.

``SearchAdapter`` is the seam. Point it at any index:

    * ``VectorSearchAdapter``  — a real, dependency-free in-memory vector index
      built on the framework's own ``EmbeddingProvider`` (so it works offline with
      the hashing embedder, locally with Ollama, or in the cloud with
      ``OpenAIEmbedder``). Good for small corpora, tests, and demos.
    * External indexes — subclass ``SearchAdapter`` and implement ``search`` to
      call Azure AI Search, Elasticsearch/OpenSearch kNN, Pinecone, pgvector, etc.
      The governance, capability surface, and audit trail come for free.

Governance note: as with SQL, the strongest access boundary is the index's own
credentials/ACLs. This layer adds capability-scoping and an audit trail on top.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..contracts import Action, ActionResult
from .base import Adapter


@dataclass
class SearchHit:
    id: str
    text: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "score": round(self.score, 6),
                "metadata": self.metadata}


class SearchAdapter(Adapter):
    """Base governed search adapter. Subclass and implement ``search``."""

    name = "search"

    def __init__(self, *, max_k: int = 10, min_score: float = 0.0):
        self.max_k = max(1, max_k)
        self.min_score = min_score

    def capabilities(self) -> List[str]:
        return ["search.query"]

    def schema(self) -> Dict[str, Dict[str, str]]:
        return {"search.query": {"query": "string (natural language)", "k": "int (top-k, capped)"}}

    def execute(self, action: Action) -> ActionResult:
        if action.capability != "search.query":
            return ActionResult(False, error=f"unsupported capability '{action.capability}'")
        params = action.params or {}
        query = params.get("query") or params.get("q")
        if not query or not isinstance(query, str):
            return ActionResult(False, error="missing 'query'")
        k = min(int(params.get("k", 5)), self.max_k)
        try:
            hits = [h for h in self.search(query, k) if h.score >= self.min_score][:k]
        except Exception as exc:
            return ActionResult(False, error=f"{type(exc).__name__}: {exc}")
        return ActionResult(True, output={"query": query, "hits": [h.to_dict() for h in hits]})

    def search(self, query: str, k: int) -> List[SearchHit]:
        raise NotImplementedError


class VectorSearchAdapter(SearchAdapter):
    """A dependency-free in-memory vector index over an EmbeddingProvider.

    Real semantic search (cosine over embeddings), governed and audited. Swap the
    embedder to change fidelity: hashing (offline), Ollama (local), or OpenAI
    (cloud). For large corpora, subclass SearchAdapter to front a real index.
    """

    name = "vector-search"

    def __init__(self, embedder, *, max_k: int = 10, min_score: float = 0.0):
        super().__init__(max_k=max_k, min_score=min_score)
        self._embedder = embedder
        self._docs: Dict[str, SearchHit] = {}
        self._vectors: Dict[str, List[float]] = {}

    def index(self, doc_id: str, text: str, metadata: Optional[dict] = None) -> None:
        self._docs[doc_id] = SearchHit(doc_id, text, 0.0, metadata or {})
        self._vectors[doc_id] = self._embedder.embed(text)

    def index_many(self, items) -> None:
        """items: iterable of (doc_id, text) or (doc_id, text, metadata)."""
        for item in items:
            if len(item) == 3:
                self.index(item[0], item[1], item[2])
            else:
                self.index(item[0], item[1])

    def search(self, query: str, k: int) -> List[SearchHit]:
        from ..intelligence.embedding import cosine

        qvec = self._embedder.embed(query)
        scored = []
        for doc_id, vec in self._vectors.items():
            base = self._docs[doc_id]
            scored.append(SearchHit(base.id, base.text, cosine(qvec, vec), base.metadata))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:k]
