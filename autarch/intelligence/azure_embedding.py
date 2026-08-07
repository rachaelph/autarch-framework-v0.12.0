"""Azure OpenAI embeddings — real learned semantic vectors from an Azure deployment.

Mirrors :class:`autarch.intelligence.openai_embedding.OpenAIEmbedder` but talks to an
Azure OpenAI *embedding deployment* (e.g. a ``text-embedding-3-small`` deployment). It
closes the gap left by the chat-only :class:`AzureOpenAIProvider`: the council/recall
had an Azure *voice* but no Azure *embedder*, so semantic mapping on Azure-only tenants
had to fall back to the offline hashing embedder (lexical, not learned).

Auth mirrors the rest of the Azure surface:

* ``AZURE_OPENAI_API_KEY`` — used directly when present (kept out of code and logs).
* otherwise a Microsoft Entra ID **bearer token** via ``azure-identity``. Guest users
  must pin the tenant that owns the resource with ``AZURE_OPENAI_TENANT_ID`` (or
  ``AZURE_TENANT_ID``), otherwise the CLI hands back a home-tenant token the resource
  rejects with "the principal does not have access".

Configuration (env, overridable via constructor args):

    AZURE_OPENAI_ENDPOINT          https://<resource>.openai.azure.com (or .cognitiveservices.azure.com)
    AZURE_OPENAI_EMBED_DEPLOYMENT  the *deployment* name of the embedding model
    AZURE_OPENAI_API_VERSION       optional; defaults to a recent version

The transport is injectable (``_transport=``) so request/response shaping stays unit-
testable offline with no network and no credentials.
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

_DEFAULT_API_VERSION = "2024-02-01"

# Published output dimensions of the current text-embedding-3 family.
_KNOWN_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class AzureOpenAIEmbedder(EmbeddingProvider):
    """Learned semantic embeddings from an Azure OpenAI embedding deployment."""

    def __init__(
        self,
        deployment: Optional[str] = None,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        api_version: Optional[str] = None,
        timeout: float = 30.0,
        dimensions: Optional[int] = None,
        _transport: Optional[Callable[[bytes, dict, float], bytes]] = None,
    ):
        # The embedding deployment is distinct from the chat deployment, so it has its own
        # env var and never falls back to AZURE_OPENAI_DEPLOYMENT (that is the chat model).
        self.deployment = deployment or os.environ.get("AZURE_OPENAI_EMBED_DEPLOYMENT", "")
        self.endpoint = (endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT", "")).rstrip("/")
        self._api_key = api_key or os.environ.get("AZURE_OPENAI_API_KEY", "")
        self.api_version = (
            api_version or os.environ.get("AZURE_OPENAI_API_VERSION") or _DEFAULT_API_VERSION
        )
        self.name = f"azure:{self.deployment or '?'}"
        # dim is advertised up front; refined from the first real response.
        self.dim = dimensions or _KNOWN_DIMS.get(self.deployment, 1536)
        self._dimensions = dimensions  # optional server-side truncation
        self._timeout = timeout
        self._transport = _transport or self._http
        self._token_provider = None  # lazily built AAD bearer-token provider

    def _url(self) -> str:
        return (
            f"{self.endpoint}/openai/deployments/{self.deployment}"
            f"/embeddings?api-version={self.api_version}"
        )

    def embed(self, text: str) -> List[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed several texts in one request (the API accepts a list)."""
        if not texts:
            return []
        if self._transport is self._http:
            missing = [
                name
                for name, value in (
                    ("AZURE_OPENAI_ENDPOINT", self.endpoint),
                    ("AZURE_OPENAI_EMBED_DEPLOYMENT", self.deployment),
                )
                if not value
            ]
            if missing:
                raise ModelError(
                    "Azure OpenAI embedder is not configured; set " + ", ".join(missing),
                    context={"missing": missing},
                )

        payload: dict = {"input": list(texts)}
        if self._dimensions:
            payload["dimensions"] = self._dimensions
        body = json.dumps(payload).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["api-key"] = self._api_key
        else:
            headers["Authorization"] = f"Bearer {self._bearer_token()}"

        raw = self._transport(body, headers, self._timeout)
        data = json.loads(raw.decode("utf-8"))
        try:
            # The API may return rows out of order; sort by 'index' to be safe.
            rows = sorted(data["data"], key=lambda r: r.get("index", 0))
            vectors = [[float(x) for x in row["embedding"]] for row in rows]
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelError(
                f"Azure OpenAI embeddings response was malformed: {data}",
                context={"deployment": self.deployment},
            ) from exc
        if vectors:
            self.dim = len(vectors[0])
        return vectors

    def _bearer_token(self) -> str:
        """Fetch a Microsoft Entra ID token for the Cognitive Services data plane.

        Tenant-pinned for guest principals: a bare CLI credential returns a home-tenant
        token the resource rejects, so honor AZURE_OPENAI_TENANT_ID / AZURE_TENANT_ID.
        """
        if self._token_provider is None:
            try:
                from azure.identity import (
                    AzureCliCredential,
                    ChainedTokenCredential,
                    DefaultAzureCredential,
                    get_bearer_token_provider,
                )
            except ImportError as exc:  # pragma: no cover - env-dependent
                raise ModelUnavailable(
                    "Azure AD auth needs 'azure-identity' (pip install azure-identity), "
                    "or set AZURE_OPENAI_API_KEY.",
                    context={"deployment": self.deployment},
                ) from exc
            tenant = os.environ.get("AZURE_OPENAI_TENANT_ID") or os.environ.get("AZURE_TENANT_ID")
            if tenant:
                credential = AzureCliCredential(tenant_id=tenant)
            else:
                credential = ChainedTokenCredential(AzureCliCredential(), DefaultAzureCredential())
            self._token_provider = get_bearer_token_provider(
                credential, "https://cognitiveservices.azure.com/.default"
            )
        return self._token_provider()

    def _http(self, body: bytes, headers: dict, timeout: float) -> bytes:
        request = urllib.request.Request(self._url(), data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            _raise_for_status(exc, self.deployment)
        except urllib.error.URLError as exc:
            raise ModelUnavailable(
                f"Azure OpenAI embeddings unreachable at {self.endpoint}: {exc.reason}",
                context={"deployment": self.deployment},
            ) from exc
