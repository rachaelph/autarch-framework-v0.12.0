"""Azure OpenAI provider — a real governed voice backed by Azure OpenAI.

Talks to the Azure OpenAI *Chat Completions* REST API using only the standard
library, so the package keeps its zero-runtime-dependency promise (no `openai`
SDK required). Same seam as every other model: it implements `complete()` and
drops into the council or `build_provider("azure:<deployment>")` untouched by the
kernel.

Configuration is read from the environment (never hard-code secrets):

    AZURE_OPENAI_ENDPOINT      e.g. https://my-resource.openai.azure.com
    AZURE_OPENAI_API_KEY       the resource key (kept out of code and logs)
    AZURE_OPENAI_DEPLOYMENT    your *deployment* name (what you named the model
                               when you deployed it — NOT the base model name)
    AZURE_OPENAI_API_VERSION   optional; defaults to a recent GA version. Newer
                               models may need a newer (preview) api-version.

Note: Azure references a **deployment**, not a model. `azure:gpt-5.4` means "the
deployment I named `gpt-5.4`", whatever base model sits behind it.

Hardened for real use like the Ollama provider: optional JSON-constrained output
(`response_format`), and typed errors (`RateLimited` / `ModelUnavailable` /
`ModelError`) so the resilience layer (applied automatically by `build_provider`)
can back off, retry, or fail fast appropriately.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Optional

from ..errors import ModelError, ModelUnavailable, RateLimited
from .base import ModelProvider

# A recent GA api-version. Override via AZURE_OPENAI_API_VERSION for models that
# require a newer (often preview) version — the newest GPT models frequently do.
_DEFAULT_API_VERSION = "2024-10-21"


class AzureOpenAIProvider(ModelProvider):
    def __init__(
        self,
        deployment: Optional[str] = None,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        api_version: Optional[str] = None,
        timeout: float = 120.0,
        json_mode: bool = True,
        temperature: Optional[float] = None,
        max_completion_tokens: Optional[int] = None,
    ):
        # Fall back to environment configuration for anything not passed in.
        self.deployment = deployment or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")
        self.endpoint = (endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT", "")).rstrip("/")
        self._api_key = api_key or os.environ.get("AZURE_OPENAI_API_KEY", "")
        self.api_version = (
            api_version or os.environ.get("AZURE_OPENAI_API_VERSION") or _DEFAULT_API_VERSION
        )
        self.name = f"azure:{self.deployment or '?'}"
        self._timeout = timeout
        self._json_mode = json_mode
        # Omitted by default: the newest models accept only their default
        # temperature and reject an explicit one. Set it only when you need to.
        self._temperature = temperature
        self._max_completion_tokens = max_completion_tokens

    def _url(self) -> str:
        return (
            f"{self.endpoint}/openai/deployments/{self.deployment}"
            f"/chat/completions?api-version={self.api_version}"
        )

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self._chat(messages, json_mode=self._json_mode, est_prompt=prompt, est_system=system or "")

    def supports_vision(self) -> bool:
        return True

    def complete_vision(self, prompt, images, system=None) -> str:
        """Send ``prompt`` plus one or more images to the deployment (needs a vision-capable model,
        e.g. gpt-4o / gpt-5.x). Metered and typed exactly like a text completion."""
        from .vision import openai_vision_content

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": openai_vision_content(prompt, images)})
        # Only force JSON mode when the prompt actually asks for it (Azure requires the word "json"
        # in the messages when response_format=json_object); vision prompts are usually prose.
        json_mode = self._json_mode and "json" in (prompt or "").lower()
        return self._chat(messages, json_mode=json_mode, est_prompt=prompt, est_system=system or "")

    def _chat(self, messages, *, json_mode: bool, est_prompt: str, est_system: str) -> str:
        # Fail fast with an actionable, typed error if misconfigured.
        missing = [
            name
            for name, value in (
                ("AZURE_OPENAI_ENDPOINT", self.endpoint),
                ("AZURE_OPENAI_API_KEY", self._api_key),
                ("AZURE_OPENAI_DEPLOYMENT", self.deployment),
            )
            if not value
        ]
        if missing:
            raise ModelError(
                "Azure OpenAI is not configured; set " + ", ".join(missing),
                context={"missing": missing},
            )

        body: dict = {"messages": messages}
        if json_mode:
            # Constrain the model to emit a single valid JSON object (the caller's
            # prompt must mention JSON, which the extractor's does).
            body["response_format"] = {"type": "json_object"}
        if self._temperature is not None:
            body["temperature"] = self._temperature
        if self._max_completion_tokens is not None:
            body["max_completion_tokens"] = self._max_completion_tokens

        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self._url(),
            data=data,
            headers={"Content-Type": "application/json", "api-key": self._api_key},
        )
        _t0 = time.time()
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
                _t1 = time.time()
                choices = payload.get("choices") or []
                content = choices[0].get("message", {}).get("content", "") if choices else ""
                try:  # record token usage (real from the API, else estimated) — never break the call
                    from .usage import record_usage
                    from .pricing import estimate_tokens

                    usage = payload.get("usage") or {}
                    pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
                    if pt is None and ct is None:
                        record_usage(self.deployment, estimate_tokens((est_system or "") + (est_prompt or "")),
                                     estimate_tokens(content or ""), estimated=True, source="azure", started=_t0, ended=_t1)
                    else:
                        record_usage(self.deployment, pt or 0, ct or 0, estimated=False, source="azure", started=_t0, ended=_t1)
                except Exception:
                    pass
                return content or ""
        except urllib.error.HTTPError as exc:
            # Typed so the resilience layer reacts correctly: 429 -> wait,
            # 5xx -> retry, everything else -> a hard error not worth retrying.
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                detail = ""
            if exc.code == 429:
                retry_after = None
                try:
                    raw = exc.headers.get("Retry-After") if exc.headers else None
                    retry_after = float(raw) if raw else None
                except (TypeError, ValueError):
                    retry_after = None
                raise RateLimited(
                    f"Azure OpenAI rate limited deployment '{self.deployment}' (HTTP 429).",
                    retry_after=retry_after,
                    context={"deployment": self.deployment},
                ) from exc
            if exc.code in (500, 502, 503, 504):
                raise ModelUnavailable(
                    f"Azure OpenAI returned HTTP {exc.code} for '{self.deployment}': {exc.reason}.",
                    context={"deployment": self.deployment, "status": exc.code},
                ) from exc
            raise ModelError(
                f"Azure OpenAI returned HTTP {exc.code} for deployment '{self.deployment}': "
                f"{exc.reason}. {detail}".rstrip(),
                context={"deployment": self.deployment, "status": exc.code},
            ) from exc
        except urllib.error.URLError as exc:
            raise ModelUnavailable(
                f"Azure OpenAI endpoint not reachable at {self.endpoint}: {exc.reason}. "
                "Check AZURE_OPENAI_ENDPOINT and your network.",
                context={"deployment": self.deployment, "endpoint": self.endpoint},
            ) from exc
