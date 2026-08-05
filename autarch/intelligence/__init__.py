"""The Intelligence Bus — pluggable model providers.

Any model can join the council as a voice. Your model is primary; GPT, Claude,
and local models are hot-swappable guests behind one interface.
"""
from __future__ import annotations

from .base import ModelProvider
from .factory import build_provider
from .mock import MockProvider
from .ollama import OllamaProvider

__all__ = ["ModelProvider", "build_provider", "MockProvider", "OllamaProvider"]
