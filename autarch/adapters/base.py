"""The adapter interface — how the world plugs into Autarch."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List

from ..contracts import Action, ActionResult


class Adapter(ABC):
    name: str = "adapter"

    @abstractmethod
    def capabilities(self) -> List[str]:
        """The capability names this adapter can execute (e.g. 'file.write')."""
        raise NotImplementedError

    @abstractmethod
    def execute(self, action: Action) -> ActionResult:
        """Perform the action. Must enforce its own safety boundaries."""
        raise NotImplementedError

    def schema(self) -> Dict[str, Dict[str, str]]:
        """Optional parameter schema per capability, surfaced to the council.

        Maps capability -> {param_name: type_hint}. Telling a real model the
        exact parameter names a capability expects is what makes free-form model
        output line up with typed adapters. Default: no declared schema.
        """
        return {}

    def normalize_params(self, capability: str, params: dict) -> dict:
        """Canonicalize a proposal's params before gating and execution.

        Real models name parameters inconsistently. An adapter may map synonyms
        onto its canonical names here so that the kernel, the adapter, and the
        audit record all see one consistent shape. Default: unchanged.
        """
        return params
