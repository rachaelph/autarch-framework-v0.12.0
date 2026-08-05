"""OpenAI embeddings — real semantic recall with just an API key (stdlib only).

Autarch's recall memory already blends lexical overlap, recency/salience,
structural filters, and — when an embedder is supplied — *semantic* similarity.
The only zero-setup embedder that shipped was :class:`HashingEmbedder`, which
captures vocabulary overlap but not learned meaning, and the only real one
(:class:`OllamaEmbedder`) needs a local model running. This provider closes that
gap the same way the chat providers do: real learned embeddings over plain
``urllib``, no third-party dependency, so meaning-aware recall works out of the
box with only ``OPENAI_API_KEY`` set.

The transport is injectable (``_transport=``) so request/response shaping is unit-
testable offline, with no network and no key.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable, List, Optional

from ..errors import ModelError, ModelUnavailable
from .embedding import EmbeddingProvider
from .openai import _raise_for_status  # shared HTTP-status -> typed-error mapping

_DEFAULT_ENDPOINT = "https://api.openai.com/v1/embeddings"

# Published output dimensions of the current text-embedding-3 family.
_KNOWN_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class OpenAIEmbedder(EmbeddingProvider):
    """Learned semantic embeddings from the OpenAI embeddings API."""

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        endpoint: str = _DEFAULT_ENDPOINT,
        timeout: float = 30.0,
        dimensions: Optional[int] = None,
        organization: Optional[str] = None,
        _transport: Optional[Callable[[bytes, dict, float], bytes]] = None,
    ):
        self.model = model
        self.name = f"openai:{model}"
        # dim is advertised up front (recall may inspect it); refined from the
        # first real response if the API returns a different length.
        self.dim = dimensions or _KNOWN_DIMS.get(model, 1536)
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._endpoint = endpoint
        self._timeout = timeout
        self._dimensions = dimensions  # optional server-side truncation
        self._organization = organization
        self._transport = _transport or self._http

    def embed(self, text: str) -> List[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed several texts in one request (the API accepts a list)."""
        if not texts:
            return []
        if not self._api_key and self._transport is self._http:
            raise ModelError(
                "OpenAI API key missing. Set OPENAI_API_KEY or pass api_key=...",
                context={"model": self.model},
            )
        payload = {"model": self.model, "input": list(texts)}
        if self._dimensions:
            payload["dimensions"] = self._dimensions
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        if self._organization:
            headers["OpenAI-Organization"] = self._organization

        raw = self._transport(body, headers, self._timeout)
        data = json.loads(raw.decode("utf-8"))
        try:
            # The API may return rows out of order; sort by 'index' to be safe.
            rows = sorted(data["data"], key=lambda r: r.get("index", 0))
            vectors = [[float(x) for x in row["embedding"]] for row in rows]
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelError(
                f"OpenAI embeddings response was malformed: {data}",
                context={"model": self.model},
            ) from exc
        if vectors:
            self.dim = len(vectors[0])
        return vectors

    def _http(self, body: bytes, headers: dict, timeout: float) -> bytes:
        request = urllib.request.Request(self._endpoint, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            _raise_for_status(exc, self.model)
        except urllib.error.URLError as exc:
            raise ModelUnavailable(
                f"OpenAI embeddings unreachable at {self._endpoint}: {exc.reason}",
                context={"model": self.model},
            ) from exc
