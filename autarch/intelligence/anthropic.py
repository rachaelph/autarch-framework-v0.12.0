"""Anthropic (Claude) provider — a real cloud voice for the council (stdlib only).

Talks to the Anthropic Messages API with ``urllib`` alone, preserving the
zero-dependency promise, and raises the same typed model errors as the other
providers so resilience wraps it automatically. The transport is injectable for
offline unit testing.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable, Optional

from ..errors import ModelError, ModelUnavailable, RateLimited
from .base import ModelProvider
from .openai import _raise_for_status  # shared HTTP-status translation

_DEFAULT_ENDPOINT = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"


class AnthropicProvider(ModelProvider):
    def __init__(
        self,
        model: str = "claude-3-5-haiku-latest",
        api_key: Optional[str] = None,
        endpoint: str = _DEFAULT_ENDPOINT,
        timeout: float = 60.0,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        _transport: Optional[Callable[[bytes, dict, float], bytes]] = None,
    ):
        self.model = model
        self.name = f"anthropic:{model}"
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._endpoint = endpoint
        self._timeout = timeout
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._transport = _transport or self._http

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        if not self._api_key and self._transport is self._http:
            raise ModelError(
                "Anthropic API key missing. Set ANTHROPIC_API_KEY or pass api_key=...",
                context={"model": self.model},
            )
        payload = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": _API_VERSION,
        }
        raw = self._transport(body, headers, self._timeout)
        data = json.loads(raw.decode("utf-8"))
        try:
            # content is a list of blocks; concatenate the text blocks.
            parts = [b.get("text", "") for b in data["content"] if b.get("type") == "text"]
            return "".join(parts)
        except (KeyError, TypeError) as exc:
            raise ModelError(
                f"Anthropic response had no completion: {data}",
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
                f"Anthropic not reachable at {self._endpoint}: {exc.reason}",
                context={"model": self.model},
            ) from exc
