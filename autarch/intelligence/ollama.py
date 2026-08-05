"""Ollama provider — run a real model locally, fully offline.

Talks to the local Ollama HTTP API using only the standard library, so the
package keeps zero runtime dependencies. Data never leaves the machine.

Hardened for real use: JSON-constrained output (Ollama's ``format: json``), a low
temperature for reliable structure, and typed errors (``RateLimited`` /
``ModelUnavailable`` / ``ModelError``) so the resilience layer can back off,
retry, or fail fast appropriately. Retry/backoff itself lives in
``autarch.resilience`` (applied automatically by ``build_provider``), keeping a
single source of truth for resilience. Data never leaves the machine.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from ..errors import ModelError, ModelUnavailable, RateLimited
from .base import ModelProvider


class OllamaProvider(ModelProvider):
    def __init__(
        self,
        model: str = "llama3",
        host: str = "http://localhost:11434",
        timeout: float = 120.0,
        json_mode: bool = True,
        temperature: float = 0.2,
        retries: int = 0,
    ):
        self.model = model
        self.name = f"ollama:{model}"
        self._endpoint = f"{host.rstrip('/')}/api/generate"
        self._timeout = timeout
        self._json_mode = json_mode
        self._temperature = temperature
        self._retries = max(0, retries)

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": self._temperature},
        }
        if self._json_mode:
            # Constrain the model to emit a single valid JSON value.
            payload["format"] = "json"
        if system:
            payload["system"] = system
        data = json.dumps(payload).encode("utf-8")

        last_exc: Optional[Exception] = None
        for _ in range(self._retries + 1):
            request = urllib.request.Request(
                self._endpoint,
                data=data,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                    return body.get("response", "")
            except urllib.error.HTTPError as exc:
                # Typed so the resilience layer can react correctly: 429 -> wait,
                # 5xx -> retry, everything else -> a hard error not worth retrying.
                if exc.code == 429:
                    retry_after = None
                    try:
                        raw = exc.headers.get("Retry-After") if exc.headers else None
                        retry_after = float(raw) if raw else None
                    except (TypeError, ValueError):
                        retry_after = None
                    raise RateLimited(
                        f"Ollama rate limited model '{self.model}' (HTTP 429).",
                        retry_after=retry_after,
                        context={"model": self.model},
                    ) from exc
                if exc.code in (500, 502, 503, 504):
                    raise ModelUnavailable(
                        f"Ollama returned HTTP {exc.code} for model '{self.model}': {exc.reason}.",
                        context={"model": self.model, "status": exc.code},
                    ) from exc
                raise ModelError(
                    f"Ollama returned HTTP {exc.code} for model '{self.model}': {exc.reason}. "
                    f"If the model is missing, run `ollama pull {self.model}`.",
                    context={"model": self.model, "status": exc.code},
                ) from exc
            except urllib.error.URLError as exc:
                last_exc = exc  # transient (e.g. server starting) — retry
                continue

        raise ModelUnavailable(
            f"Ollama not reachable at {self._endpoint}: {last_exc}. "
            "Is it installed and is `ollama serve` running?",
            context={"model": self.model, "endpoint": self._endpoint},
        ) from last_exc
