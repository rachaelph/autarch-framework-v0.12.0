"""ExtractionAdapter — governed extraction from structured AND unstructured docs.

Two governed capabilities turn documents into data an agent can use:

  * ``doc.parse``   — *deterministic* structured extraction. Auto-detects the
    format and returns clean data: CSV/TSV -> list of row dicts, JSON -> the
    parsed object, plain text / Markdown / HTML -> text (HTML tags stripped),
    PDF -> extracted text (optional ``pypdf``). No model, no guessing.

  * ``doc.extract`` — *smart* schema-guided extraction. Given a document (or raw
    text) and a set of fields, a model pulls those fields out of unstructured
    prose and returns a single JSON object. This is the "read a messy invoice /
    contract / email and give me {invoice_no, date, total}" pattern — governed:
    the file read is capability-scoped and audited, the model output is coerced to
    your schema, and unknown/uncertain fields come back null (never hallucinated
    into a required shape).

File access is confined to a root directory (defense-in-depth path safety), so an
extractor can be granted *only* ``doc.parse`` / ``doc.extract`` on one folder and
provably nothing else. Large documents are chunked for the model with the first
chunk carrying the schema, so extraction degrades gracefully rather than failing.
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..contracts import Action, ActionResult
from .base import Adapter

_STRUCTURED = {".csv", ".tsv", ".json"}
_TEXTUAL = {".txt", ".md", ".markdown", ".text", ".log", ".html", ".htm"}
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")


class ExtractionAdapter(Adapter):
    """Governed structured + unstructured document extraction."""

    name = "extraction"

    def __init__(self, root: str = "./docs", model=None, *, max_chars: int = 12000,
                 chunk_chars: int = 8000):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._model = model  # a ModelProvider or spec; required only for doc.extract
        self.max_chars = max_chars
        self.chunk_chars = chunk_chars

    def capabilities(self) -> List[str]:
        return ["doc.parse", "doc.extract"]

    def schema(self) -> Dict[str, Dict[str, str]]:
        return {
            "doc.parse": {"path": "string (file to parse) OR text: string"},
            "doc.extract": {
                "path": "string (file) OR text: string",
                "fields": "list[str] or {field: description} to extract",
            },
        }

    def execute(self, action: Action) -> ActionResult:
        try:
            params = action.params or {}
            if action.capability == "doc.parse":
                return self._parse(params)
            if action.capability == "doc.extract":
                return self._extract(params)
            return ActionResult(False, error=f"unsupported capability '{action.capability}'")
        except PermissionError as exc:
            return ActionResult(False, error=str(exc))
        except Exception as exc:
            return ActionResult(False, error=f"{type(exc).__name__}: {exc}")

    # -- deterministic structured parsing --------------------------------
    def _parse(self, params: dict) -> ActionResult:
        text, suffix = self._load(params)
        if suffix in (".csv", ".tsv"):
            delim = "\t" if suffix == ".tsv" else ","
            rows = list(csv.DictReader(io.StringIO(text), delimiter=delim))
            return ActionResult(True, output={"format": suffix.lstrip("."),
                                              "records": rows, "count": len(rows)})
        if suffix == ".json":
            data = json.loads(text)
            kind = "records" if isinstance(data, list) else "object"
            return ActionResult(True, output={"format": "json", kind: data})
        # textual / unstructured
        clean = self._to_text(text, suffix)
        return ActionResult(True, output={"format": (suffix.lstrip(".") or "text"),
                                          "text": clean, "chars": len(clean)})

    # -- smart schema-guided extraction ----------------------------------
    def _extract(self, params: dict) -> ActionResult:
        fields = params.get("fields")
        if not fields:
            return ActionResult(False, error="doc.extract needs 'fields'")
        model = self._resolve_model()
        if model is None:
            return ActionResult(False, error="doc.extract needs a model (pass model= to the adapter)")

        text, suffix = self._load(params)
        text = self._to_text(text, suffix)
        field_names = list(fields.keys()) if isinstance(fields, dict) else list(fields)
        merged: Dict[str, Any] = {name: None for name in field_names}

        for chunk in self._chunks(text):
            prompt = self._extract_prompt(fields, field_names, chunk)
            try:
                raw = model.complete(prompt, system=_EXTRACT_SYSTEM)
            except Exception as exc:
                return ActionResult(False, error=f"extraction model failed: {exc}")
            data = _safe_json(raw)
            # fill only still-missing fields, so earlier chunks win and we never
            # overwrite a found value with a later null
            for name in field_names:
                if merged[name] in (None, "") and data.get(name) not in (None, ""):
                    merged[name] = data[name]
            if all(merged[n] not in (None, "") for n in field_names):
                break  # every field found; stop early

        found = sum(1 for v in merged.values() if v not in (None, ""))
        return ActionResult(True, output={"fields": merged, "found": found,
                                          "requested": len(field_names)})

    # -- helpers ----------------------------------------------------------
    def _load(self, params: dict):
        """Return (raw_text, suffix). Accepts inline 'text' or a governed file path."""
        if params.get("text") is not None:
            return str(params["text"]), (params.get("format") or ".txt")
        path = params.get("path") or params.get("file")
        if not path:
            raise KeyError("path or text")
        target = self._safe(path)
        if not target.exists():
            return "", target.suffix.lower()
        suffix = target.suffix.lower()
        if suffix == ".pdf":
            return self._read_pdf(target), suffix
        return target.read_text(encoding="utf-8", errors="replace"), suffix

    def _to_text(self, text: str, suffix: str) -> str:
        if suffix in (".html", ".htm"):
            text = _TAG.sub(" ", text)
        text = _WS.sub(" ", text)
        return text[: self.max_chars].strip()

    def _chunks(self, text: str) -> List[str]:
        if len(text) <= self.chunk_chars:
            return [text]
        return [text[i:i + self.chunk_chars] for i in range(0, len(text), self.chunk_chars)]

    def _resolve_model(self):
        if self._model is None:
            return None
        if isinstance(self._model, str):
            from ..intelligence.factory import build_provider

            self._model = build_provider(self._model)
        return self._model

    @staticmethod
    def _extract_prompt(fields, field_names, chunk: str) -> str:
        if isinstance(fields, dict):
            spec = "\n".join(f'  "{name}": {desc}' for name, desc in fields.items())
        else:
            spec = "\n".join(f'  "{name}": <value or null>' for name in field_names)
        return _EXTRACT_TEMPLATE.format(spec=spec, keys=json.dumps(field_names), doc=chunk)

    @staticmethod
    def _read_pdf(target: Path) -> str:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError(
                "reading PDFs needs the optional `pypdf` package "
                "(pip install pypdf); text/csv/json/html work with no dependency"
            ) from exc
        reader = PdfReader(str(target))
        return "\n".join((page.extract_text() or "") for page in reader.pages)

    def _safe(self, path: str) -> Path:
        target = (self.root / path).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError(f"path '{path}' escapes document root {self.root}") from exc
        return target


_EXTRACT_SYSTEM = (
    "You extract structured fields from a document. Respond with ONLY a single "
    "JSON object and nothing else. If a field is absent or uncertain, use null. "
    "Never invent values."
)

_EXTRACT_TEMPLATE = """Extract these fields from the DOCUMENT. Use null for anything
not clearly present — do not guess.
FIELDS:
{{
{spec}
}}
Return ONLY a JSON object with exactly these keys: {keys}
DOCUMENT:
{doc}
"""


def _safe_json(raw: str) -> dict:
    from ..util import extract_json

    data = extract_json(raw)
    return data if isinstance(data, dict) else {}
