"""Model pricing — a real per-token price table so budgets meter actual spend.

The economic kernel meters cost, but until now there was no table of real prices,
so ``cost`` was always 0.0 for model calls. This module ships current published
list prices (USD per 1M tokens) for common models and a ``token_cost`` helper the
economic layer can use to estimate spend from a model call.

Prices change; treat these as sensible defaults, override per deployment via
``PriceBook(overrides=...)``. Costs are estimates for *pacing and budgeting*, not
billing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

# USD per 1,000,000 tokens: (input, output). Published list prices; override freely.
_PRICES_PER_MTOK: Dict[str, tuple] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "o3-mini": (1.10, 4.40),
    # Anthropic
    "claude-3-5-sonnet-latest": (3.00, 15.00),
    "claude-3-5-haiku-latest": (0.80, 4.00),
    "claude-3-opus-latest": (15.00, 75.00),
    # Local / offline
    "ollama": (0.0, 0.0),
    "mock": (0.0, 0.0),
    # Embeddings (input-only; output price is 0). USD per 1M tokens.
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
    "text-embedding-ada-002": (0.10, 0.0),
}
_FALLBACK = (1.00, 3.00)  # unknown cloud model: a conservative middle estimate


def _stem(name: str) -> str:
    """Drop a trailing '-latest' or '-<date/version>' so families match."""
    for suffix in ("-latest",):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    # strip a trailing -YYYYMMDD or -NNN version tag
    head, _, tail = name.rpartition("-")
    if head and tail.isdigit():
        return head
    return name


def _match_price(model: str) -> tuple:
    if model in _PRICES_PER_MTOK:
        return _PRICES_PER_MTOK[model]
    target = _stem(model)
    # Longest-prefix stem match so 'gpt-4o-mini' beats 'gpt-4o', and a dated id
    # like 'claude-3-5-haiku-20241022' resolves to the 'claude-3-5-haiku' family.
    best_key = None
    for key in _PRICES_PER_MTOK:
        key_stem = _stem(key)
        if target == key_stem or target.startswith(key_stem + "-") or model.startswith(key_stem):
            if best_key is None or len(key_stem) > len(_stem(best_key)):
                best_key = key
    if best_key is not None:
        return _PRICES_PER_MTOK[best_key]
    if model.startswith("ollama") or model == "mock":
        return (0.0, 0.0)
    return _FALLBACK


def estimate_tokens(text: str) -> int:
    """A ~4-chars/token estimate — fine for budgeting, not for billing."""
    return max(1, (len(text) + 3) // 4)


@dataclass
class PriceBook:
    """Resolves a model name to a per-token price and estimates a call's cost."""

    overrides: Optional[Dict[str, tuple]] = None

    def price(self, model: str) -> tuple:
        if self.overrides and model in self.overrides:
            return self.overrides[model]
        return _match_price(model)

    def token_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        p_in, p_out = self.price(model)
        return (prompt_tokens * p_in + completion_tokens * p_out) / 1_000_000.0

    def estimate_call_cost(
        self,
        model: str,
        prompt: str,
        est_completion_tokens: int = 400,
    ) -> float:
        """Estimate the USD cost of one completion from the prompt text."""
        return self.token_cost(model, estimate_tokens(prompt), est_completion_tokens)


DEFAULT_PRICE_BOOK = PriceBook()
