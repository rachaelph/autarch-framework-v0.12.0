"""FileSystemAdapter — the first real capability: local file control.

Every operation is confined to a sandbox root (defense-in-depth against path
traversal, even if the kernel were misconfigured) and captures undo information
so actions are reversible.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List

from ..contracts import Action, ActionResult
from .base import Adapter

# Real models name parameters inconsistently; map common synonyms onto the
# adapter's canonical names so free-form proposals still execute.
_PARAM_SYNONYMS = {
    "path": ("path", "filename", "file", "filepath", "file_path", "name", "target"),
    "content": ("content", "text", "data", "body", "contents"),
    "dest": ("dest", "destination", "to", "dst", "new_path", "newpath", "target_path"),
}


class FileSystemAdapter(Adapter):
    name = "filesystem"

    def __init__(self, root="./sandbox"):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def capabilities(self) -> List[str]:
        return ["file.read", "file.write", "file.move", "file.delete"]

    def schema(self) -> Dict[str, Dict[str, str]]:
        return {
            "file.read": {"path": "string (file path)"},
            "file.write": {"path": "string (file path)", "content": "string (text to write)"},
            "file.move": {"path": "string (source path)", "dest": "string (destination path)"},
            "file.delete": {"path": "string (file path)"},
        }

    def execute(self, action: Action) -> ActionResult:
        try:
            params = self.normalize_params(action.capability, action.params)
            handler = {
                "file.read": self._read,
                "file.write": self._write,
                "file.move": self._move,
                "file.delete": self._delete,
            }.get(action.capability)
            if handler is None:
                return ActionResult(False, error=f"unsupported capability '{action.capability}'")
            return handler(params)
        except KeyError as exc:
            return ActionResult(False, error=f"missing parameter: {exc}")
        except PermissionError as exc:
            return ActionResult(False, error=str(exc))
        except Exception as exc:  # surface, never crash the kernel
            return ActionResult(False, error=f"{type(exc).__name__}: {exc}")

    # -- param normalization ----------------------------------------------
    def normalize_params(self, capability: str, params: dict) -> dict:
        """Map synonym parameter names onto canonical ones (path/content/dest)."""
        if not isinstance(params, dict):
            return {}
        normalized = dict(params)
        for canonical, synonyms in _PARAM_SYNONYMS.items():
            if canonical in normalized:
                continue
            for syn in synonyms:
                if syn in params:
                    normalized[canonical] = params[syn]
                    break
        return normalized


    # -- handlers ---------------------------------------------------------
    def _read(self, params: dict) -> ActionResult:
        target = self._safe(params["path"])
        if not target.exists():
            return ActionResult(False, error=f"file not found: {params['path']}")
        return ActionResult(True, output=target.read_text(encoding="utf-8"))

    def _write(self, params: dict) -> ActionResult:
        target = self._safe(params["path"])
        prior = target.read_text(encoding="utf-8") if target.exists() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        content = params.get("content", "")
        target.write_text(content, encoding="utf-8")
        undo = {
            "capability": "file.write" if prior is not None else "file.delete",
            "path": params["path"],
            "restore": prior,
        }
        return ActionResult(
            True,
            output=f"wrote {len(content)} chars to {params['path']}",
            undo=undo,
        )

    def _delete(self, params: dict) -> ActionResult:
        target = self._safe(params["path"])
        if not target.exists():
            return ActionResult(False, error=f"file not found: {params['path']}")
        prior = target.read_text(encoding="utf-8")
        target.unlink()
        undo = {"capability": "file.write", "path": params["path"], "restore": prior}
        return ActionResult(True, output=f"deleted {params['path']}", undo=undo)

    def _move(self, params: dict) -> ActionResult:
        src = self._safe(params["path"])
        dest = self._safe(params["dest"])
        if not src.exists():
            return ActionResult(False, error=f"file not found: {params['path']}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        undo = {"capability": "file.move", "path": params["dest"], "dest": params["path"]}
        return ActionResult(True, output=f"moved {params['path']} -> {params['dest']}", undo=undo)

    # -- safety -----------------------------------------------------------
    def _safe(self, path: str) -> Path:
        """Resolve `path` and guarantee it stays inside the sandbox root."""
        target = (self.root / path).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError(
                f"path '{path}' escapes sandbox root {self.root}"
            ) from exc
        return target
