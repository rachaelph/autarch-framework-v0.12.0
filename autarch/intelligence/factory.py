"""Provider factory — turn a string spec into a ModelProvider.

  "mock"           -> deterministic offline provider (balanced persona)
  "mock:cautious"  -> deterministic offline provider with a stricter persona
  "mock:bold"      -> deterministic offline provider that approves freely
  "ollama"         -> local Ollama, default model
  "ollama:llama3"  -> local Ollama, named model
  "azure"          -> Azure OpenAI, deployment from AZURE_OPENAI_DEPLOYMENT
  "azure:gpt-5.4"  -> Azure OpenAI, the deployment you named 'gpt-5.4'
  "openai:gpt-4o"  -> OpenAI (or a bare 'gpt-4o'); needs OPENAI_API_KEY
  "anthropic:..."  -> Anthropic Claude (or a bare 'claude-...'); needs ANTHROPIC_API_KEY
  a ModelProvider  -> returned as-is

Network-backed providers are automatically wrapped for resilience (retry with
backoff + jitter and a circuit breaker) so callers get production-grade behavior
for free. Pass ``resilient=False`` to opt out, or wrap explicitly with
``autarch.make_resilient`` to add proactive rate limiting. The offline mock is
never wrapped — it can't be rate limited and stays fully deterministic.
"""
from __future__ import annotations

from typing import Union

from .base import ModelProvider
from .mock import MockProvider
from .ollama import OllamaProvider


def build_provider(spec: Union[str, ModelProvider], *, resilient: bool = True) -> ModelProvider:
    if isinstance(spec, ModelProvider):
        return spec
    if not isinstance(spec, str):
        raise TypeError(f"Cannot build provider from {type(spec).__name__}")

    if spec == "mock":
        return MockProvider()
    if spec.startswith("mock:"):
        return MockProvider(persona=spec.split(":", 1)[1])
    if spec == "ollama":
        return _wrap(OllamaProvider(), resilient)
    if spec.startswith("ollama:"):
        return _wrap(OllamaProvider(model=spec.split(":", 1)[1]), resilient)

    # Azure OpenAI — references a *deployment*, not a base model. Imported lazily
    # so the package still imports with no key. "azure" | "azure:<deployment>".
    if spec == "azure":
        from .azure_openai import AzureOpenAIProvider

        return _wrap(AzureOpenAIProvider(), resilient)
    if spec.startswith("azure:"):
        from .azure_openai import AzureOpenAIProvider

        return _wrap(AzureOpenAIProvider(deployment=spec.split(":", 1)[1]), resilient)

    # Cloud providers (imported lazily so the package still imports with no key).
    # Accept both explicit "openai:model" / "anthropic:model" forms AND bare
    # model names people naturally write, e.g. "gpt-4o", "claude-3-5-sonnet-latest".
    if spec in ("openai", "gpt"):
        from .openai import OpenAIProvider

        return _wrap(OpenAIProvider(), resilient)
    if spec.startswith("openai:") or spec.startswith("gpt:"):
        from .openai import OpenAIProvider

        return _wrap(OpenAIProvider(model=spec.split(":", 1)[1]), resilient)
    if spec in ("anthropic", "claude"):
        from .anthropic import AnthropicProvider

        return _wrap(AnthropicProvider(), resilient)
    if spec.startswith("anthropic:") or spec.startswith("claude:"):
        from .anthropic import AnthropicProvider

        return _wrap(AnthropicProvider(model=spec.split(":", 1)[1]), resilient)

    # Bare model-name routing by family prefix.
    if spec.startswith(("gpt-", "gpt3", "gpt4", "o1-", "o3-", "o4-", "chatgpt")):
        from .openai import OpenAIProvider

        return _wrap(OpenAIProvider(model=spec), resilient)
    if spec.startswith("claude-"):
        from .anthropic import AnthropicProvider

        return _wrap(AnthropicProvider(model=spec), resilient)

    raise ValueError(
        f"Unknown provider spec: {spec!r}. Try 'mock', 'mock:<persona>', "
        "'ollama[:model]', 'azure[:deployment]', 'openai[:model]' (or a bare 'gpt-4o'), "
        "or 'anthropic[:model]' (or a bare 'claude-3-5-sonnet-latest')."
    )


def _wrap(provider: ModelProvider, resilient: bool) -> ModelProvider:
    """Wrap a network provider with default resilience (retry + circuit breaker)."""
    if not resilient:
        return provider
    # Imported lazily to keep the import graph acyclic (resilience imports errors
    # and events, not the factory).
    from ..resilience import make_resilient

    return make_resilient(provider)


def build_embedder(spec):
    """Turn a string spec into an EmbeddingProvider (mirrors build_provider).

      "hash" / "hash:512"        -> offline feature-hashing embedder (no deps)
      "ollama" / "ollama:model"  -> local learned embeddings via Ollama
      "azure" / "azure:deploy"   -> Azure OpenAI embedding deployment (AAD or key)
      "openai" / "openai:model"  -> cloud learned embeddings (needs OPENAI_API_KEY)
      an EmbeddingProvider       -> returned as-is
    """
    from .embedding import EmbeddingProvider, HashingEmbedder, OllamaEmbedder

    if isinstance(spec, EmbeddingProvider):
        return spec
    if not isinstance(spec, str):
        raise TypeError(f"Cannot build embedder from {type(spec).__name__}")

    if spec == "hash":
        return HashingEmbedder()
    if spec.startswith("hash:"):
        return HashingEmbedder(dim=int(spec.split(":", 1)[1]))
    if spec == "ollama":
        return OllamaEmbedder()
    if spec.startswith("ollama:"):
        return OllamaEmbedder(model=spec.split(":", 1)[1])
    if spec == "azure":
        from .azure_embedding import AzureOpenAIEmbedder

        return AzureOpenAIEmbedder()
    if spec.startswith("azure:"):
        from .azure_embedding import AzureOpenAIEmbedder

        return AzureOpenAIEmbedder(deployment=spec.split(":", 1)[1])
    if spec in ("openai", "gpt"):
        from .openai_embedding import OpenAIEmbedder

        return OpenAIEmbedder()
    if spec.startswith("openai:") or spec.startswith("gpt:"):
        from .openai_embedding import OpenAIEmbedder

        return OpenAIEmbedder(model=spec.split(":", 1)[1])
    if spec.startswith("text-embedding"):
        from .openai_embedding import OpenAIEmbedder

        return OpenAIEmbedder(model=spec)

    raise ValueError(
        f"Unknown embedder spec: {spec!r}. Try 'hash[:dim]', 'ollama[:model]', "
        "'azure[:deployment]', or 'openai[:model]' (or a bare 'text-embedding-3-small')."
    )
