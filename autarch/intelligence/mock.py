"""A deterministic, offline model provider.

Simulates a model so the whole nucleus runs with no API keys and no network.
It reads the council's role markers (PROPOSER / CHALLENGER) and responds with
the JSON the council expects, using simple heuristics over the intent.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from ..util import extract_json
from .base import ModelProvider

# Ordered so that more specific verbs win. Each maps an intent verb to a capability.
_VERB_MAP = [
    (r"\b(delete|remove|erase|rm)\b", "file.delete"),
    (r"\b(move|rename|mv)\b", "file.move"),
    (r"\b(create|write|make|save|add|new|put)\b", "file.write"),
    (r"\b(read|show|open|cat|display|view|print)\b", "file.read"),
]


class MockProvider(ModelProvider):
    """A deterministic stand-in for a real model.

    `persona` shapes only how this voice *critiques* a motion, so a council of
    several personas produces genuine, visible disagreement offline:
      - "balanced" (default): revise deletes; approve everything else.
      - "cautious": veto deletes; revise moves; approve reads/writes.
      - "bold": approve everything.
    """

    _PERSONAS = ("balanced", "cautious", "bold")

    def __init__(self, name: Optional[str] = None, persona: str = "balanced", scripted: Optional[dict] = None):
        if persona not in self._PERSONAS:
            persona = "balanced"
        self.persona = persona
        self.name = name or (f"mock:{persona}" if persona != "balanced" else "mock")
        self._scripted = scripted or {}

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        for key, response in self._scripted.items():
            if key in prompt:
                return response
        if "ROLE: PROPOSER" in prompt:
            return self._propose(prompt)
        if "ROLE: CHALLENGER" in prompt:
            return self._challenge(prompt)
        return "{}"

    # -- proposer ---------------------------------------------------------
    def _propose(self, prompt: str) -> str:
        intent = self._field(prompt, "INTENT")
        text = intent.lower()

        capability = "file.read"
        for pattern, cap in _VERB_MAP:
            if re.search(pattern, text):
                capability = cap
                break

        path = self._guess_path(intent)
        params: dict = {"path": path}
        if capability == "file.write":
            params["content"] = self._guess_content(intent)
        elif capability == "file.move":
            params["dest"] = self._guess_dest(intent) or (path + ".moved")

        action = {
            "capability": capability,
            "params": params,
            "rationale": f"The intent implies {capability} on '{path}'.",
        }
        return json.dumps(action)

    # -- challenger -------------------------------------------------------
    def _challenge(self, prompt: str) -> str:
        action_str = self._field(prompt, "ACTION")
        action = extract_json(action_str) or {}
        cap = action.get("capability", "")
        if not cap:
            return json.dumps(
                {"verdict": "veto", "reasons": "No actionable proposal to review."}
            )

        if self.persona == "bold":
            return json.dumps(
                {"verdict": "approve", "reasons": f"'{cap}' advances the intent; proceed."}
            )

        if self.persona == "cautious":
            if cap == "file.delete":
                return json.dumps(
                    {"verdict": "veto", "reasons": "Deletion is irreversible; I will not approve it."}
                )
            if cap == "file.move":
                return json.dumps(
                    {"verdict": "revise", "reasons": "Moving files can break references; reconsider."}
                )
            return json.dumps(
                {"verdict": "approve", "reasons": f"'{cap}' is acceptable and reversible."}
            )

        # balanced (default)
        if cap == "file.delete":
            return json.dumps(
                {"verdict": "revise", "reasons": "Deletion is irreversible; require explicit ratification."}
            )
        return json.dumps(
            {"verdict": "approve", "reasons": f"'{cap}' is within scope and low risk."}
        )

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _field(prompt: str, marker: str) -> str:
        match = re.search(rf"{marker}:\s*(.*)", prompt)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _guess_path(intent: str) -> str:
        match = re.search(r"(?:called|named)\s+([^\s,]+)", intent, re.I)
        if match:
            return match.group(1).strip("'\"")
        match = re.search(r"\b([\w\-./]+\.\w+)\b", intent)
        if match:
            return match.group(1)
        return "untitled.txt"

    @staticmethod
    def _guess_content(intent: str) -> str:
        match = re.search(
            r"(?:says?|saying|contains?|containing|with (?:text|content)|that says)\s+(.*)",
            intent,
            re.I,
        )
        if match:
            return match.group(1).strip().strip("'\"")
        return ""

    @staticmethod
    def _guess_dest(intent: str) -> Optional[str]:
        match = re.search(r"\bto\s+([^\s,]+)", intent, re.I)
        return match.group(1).strip("'\"") if match else None
