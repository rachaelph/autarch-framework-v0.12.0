"""OpenAI provider — a real cloud voice for the council (stdlib only).

Talks to the OpenAI Chat Completions API using nothing but ``urllib``, so the
package keeps its zero-dependency promise. Raises the same typed model errors as
the Ollama provider (``RateLimited`` / ``ModelUnavailable`` / ``ModelError``) so
the resilience layer (retry, proactive rate limiting, circuit breaker) wraps it
automatically via ``build_provider``.

The transport is injectable (``_transport=``) so the request/response shaping is
unit-testable offline without a network or an API key.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable, Optional

from ..errors import ModelError, ModelUnavailable, RateLimited
from .base import ModelProvider

_DEFAULT_ENDPOINT = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider(ModelProvider):
    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: Optional[str] = None,
        endpoint: str = _DEFAULT_ENDPOINT,
        timeout: float = 60.0,
        temperature: float = 0.2,
        json_mode: bool = True,
        organization: Optional[str] = None,
        _transport: Optional[Callable[[bytes, dict, float], bytes]] = None,
    ):
        self.model = model
        self.name = f"openai:{model}"
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._endpoint = endpoint
        self._timeout = timeout
        self._temperature = temperature
        self._json_mode = json_mode
        self._organization = organization
        # A seam for tests: (body_bytes, headers, timeout) -> response_bytes.
        self._transport = _transport or self._http

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        if not self._api_key and self._transport is self._http:
            raise ModelError(
                "OpenAI API key missing. Set OPENAI_API_KEY or pass api_key=...",
                context={"model": self.model},
            )
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self._temperature,
        }
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}
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
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError(
                f"OpenAI response had no completion: {data}",
                context={"model": self.model},
            ) from exc

    def _http(self, body: bytes, headers: dict, timeout: float) -> bytes:
        request = urllib.request.Request(self._endpoint, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            _raise_for_status(exc, self.model)
        except urllib.error.URLError as exc:
            raise ModelUnavailable(
                f"OpenAI not reachable at {self._endpoint}: {exc.reason}",
                context={"model": self.model},
            ) from exc


def _raise_for_status(exc: "urllib.error.HTTPError", model: str) -> None:
    """Translate an HTTP error into a typed model error the resilience layer knows."""
    if exc.code == 429:
        retry_after = None
        try:
            raw = exc.headers.get("Retry-After") if exc.headers else None
            retry_after = float(raw) if raw else None
        except (TypeError, ValueError):
            retry_after = None
        raise RateLimited(
            f"OpenAI rate limited model '{model}' (HTTP 429).",
            retry_after=retry_after,
            context={"model": model},
        ) from exc
    if exc.code in (500, 502, 503, 504):
        raise ModelUnavailable(
            f"OpenAI returned HTTP {exc.code} for '{model}': {exc.reason}.",
            context={"model": model, "status": exc.code},
        ) from exc
    raise ModelError(
        f"OpenAI returned HTTP {exc.code} for '{model}': {exc.reason}.",
        context={"model": model, "status": exc.code},
    ) from exc
