"""Embeddings — the optional semantic seam for recall memory.

Autarch's recall memory ranks candidates by a *hybrid* of signals: exact lexical
overlap, recency/salience, structural filters, and — when an embedder is
supplied — semantic similarity. Semantic search *alone* is noisy ("similar" is
not "relevant"), so it is one voice in the blend, never the sole arbiter.

Two embedders ship:

* :class:`HashingEmbedder` — deterministic, offline, zero-dependency. Feature-
  hashes tokens into a fixed-width, L2-normalized vector. It captures lexical
  overlap well enough to exercise the whole pipeline with no model and no
  network, so tests stay deterministic. It is NOT a learned semantic model.
* :class:`OllamaEmbedder` — real learned embeddings from a local Ollama model
  (``nomic-embed-text`` by default), over the standard library only. Data never
  leaves the machine.

Both satisfy the tiny :class:`EmbeddingProvider` seam, so a future fine-tuned
embedder drops in without touching the memory substrate.
"""
from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from typing import List, Optional

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric tokens — deterministic and dependency-free."""
    return _TOKEN_RE.findall((text or "").lower())


def cosine(a: Optional[List[float]], b: Optional[List[float]]) -> float:
    """Cosine similarity of two equal-length vectors (0 if either is empty/zero)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class EmbeddingProvider(ABC):
    """Turns text into a vector. The only thing recall memory asks of semantics."""

    name: str = "embedder"
    dim: int = 0

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """Return a numeric vector for `text`."""
        raise NotImplementedError


class HashingEmbedder(EmbeddingProvider):
    """Deterministic, offline feature-hashing embedder (no deps, no network).

    Hashes each token into one of `dim` buckets with a signed contribution, then
    L2-normalizes so cosine similarity equals the dot product and vector
    magnitude never skews ranking. Two texts sharing vocabulary land close
    together — enough to drive and test the semantic path without a real model.
    It does not capture learned meaning (synonyms, paraphrase); use
    :class:`OllamaEmbedder` for that.
    """

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.name = f"hash:{dim}"

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        for tok in tokenize(text):
            digest = hashlib.sha1(tok.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


class OllamaEmbedder(EmbeddingProvider):
    """Real local embeddings via the Ollama HTTP API (stdlib only, offline box).

    Requires a running Ollama with an embedding model pulled
    (``ollama pull nomic-embed-text``). No third-party Python dependency.
    """

    def __init__(
        self,
        model: str = "nomic-embed-text",
        host: str = "http://localhost:11434",
        timeout: float = 60.0,
    ):
        self.model = model
        self.name = f"ollama:{model}"
        self._endpoint = f"{host.rstrip('/')}/api/embeddings"
        self._timeout = timeout

    def embed(self, text: str) -> List[float]:
        import json
        import urllib.error
        import urllib.request

        from ..errors import ModelError, ModelUnavailable

        data = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
        request = urllib.request.Request(
            self._endpoint, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise ModelUnavailable(
                f"Ollama embeddings unreachable at {self._endpoint}: {exc}. "
                f"Is `ollama serve` running and `{self.model}` pulled?",
                context={"model": self.model},
            ) from exc
        vector = body.get("embedding")
        if not vector:
            raise ModelError(
                f"Ollama returned no embedding for model '{self.model}'",
                context={"model": self.model},
            )
        self.dim = len(vector)
        return [float(x) for x in vector]
