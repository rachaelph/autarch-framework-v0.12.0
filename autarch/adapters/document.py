"""DocumentAdapter — governed, read-only access to documents (PDF, DOCX, txt, md).

Reading a document is a side effect that touches the world, so it belongs behind
the capability kernel: an extractor can be granted *only* `doc.read`, confined to
one folder, and provably nothing else (no write, no delete, no network). Every
read is audited and signed. The model that extracts fields runs *after* this
governed read — see `examples/extract.py`.

PDF text extraction uses the optional `pypdf` package (`pip install autarch[pdf]`).
Image-only PDFs fall back to local OCR with PyMuPDF and RapidOCR. DOCX, plain-text,
and markdown are read natively with the standard library, so the adapter works with
zero dependencies for those formats.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from ..contracts import Action, ActionResult
from .base import Adapter

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text", ".log", ".csv"}


class DocumentAdapter(Adapter):
    name = "document"

    def __init__(self, root="./docs"):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def capabilities(self) -> List[str]:
        return ["doc.read", "doc.list"]

    def schema(self) -> Dict[str, Dict[str, str]]:
        return {
            "doc.read": {"path": "string (document path: .pdf, .docx, .txt, or .md)"},
            "doc.list": {},
        }

    def execute(self, action: Action) -> ActionResult:
        try:
            params = self.normalize_params(action.capability, action.params)
            if action.capability == "doc.read":
                return self._read(params)
            if action.capability == "doc.list":
                return self._list()
            return ActionResult(False, error=f"unsupported capability '{action.capability}'")
        except KeyError as exc:
            return ActionResult(False, error=f"missing parameter: {exc}")
        except PermissionError as exc:
            return ActionResult(False, error=str(exc))
        except Exception as exc:  # surface, never crash the kernel
            return ActionResult(False, error=f"{type(exc).__name__}: {exc}")

    def normalize_params(self, capability: str, params: dict) -> dict:
        """Map common synonyms ('file'/'filename'/'document') onto 'path'."""
        if not isinstance(params, dict):
            return {}
        out = dict(params)
        if "path" not in out:
            for syn in ("file", "filename", "document", "doc", "name"):
                if syn in params:
                    out["path"] = params[syn]
                    break
        return out

    # -- handlers ---------------------------------------------------------
    def _read(self, params: dict) -> ActionResult:
        target = self._safe(params["path"])
        if not target.exists():
            return ActionResult(False, error=f"document not found: {params['path']}")
        suffix = target.suffix.lower()
        if suffix == ".pdf":
            text = self._read_pdf(target)
        elif suffix == ".docx":
            text = self._read_docx(target)
        elif suffix in _TEXT_SUFFIXES:
            text = target.read_text(encoding="utf-8", errors="replace")
        else:
            return ActionResult(False, error=f"unsupported document type '{suffix}'")
        # doc.read is read-only: no undo (it changed nothing).
        return ActionResult(True, output=text)

    def _list(self) -> ActionResult:
        docs = [
            str(p.relative_to(self.root))
            for p in sorted(self.root.rglob("*"))
            if p.is_file() and (p.suffix.lower() in {".pdf", ".docx"} or p.suffix.lower() in _TEXT_SUFFIXES)
        ]
        return ActionResult(True, output=docs)

    @staticmethod
    def _read_pdf(target: Path) -> str:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError(
                "reading PDFs needs the optional `pypdf` package "
                "(pip install autarch[pdf]); .txt/.md work with no dependency"
            ) from exc
        reader = PdfReader(str(target))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        if text.strip():
            return text
        return DocumentAdapter._ocr_pdf(target)

    @staticmethod
    def _ocr_pdf(target: Path) -> str:
        try:
            import numpy as np
            import pymupdf
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise RuntimeError(
                "PDF has no extractable text; scanned PDFs need the OCR dependencies "
                "from `pip install autarch[pdf]`"
            ) from exc

        engine = RapidOCR()
        pages: List[str] = []
        with pymupdf.open(str(target)) as document:
            for page in document:
                pixmap = page.get_pixmap(dpi=300, alpha=False)
                image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                    pixmap.height, pixmap.width, pixmap.n
                )
                result = engine(image)
                pages.append("\n".join(result.txts or []))
        return "\n".join(pages)

    @staticmethod
    def _read_docx(target: Path) -> str:
        """Extract text from a .docx using only the standard library.

        A .docx is a zip archive whose body is `word/document.xml`; we join the
        text runs (`<w:t>`) paragraph by paragraph (`<w:p>`), honoring tabs and
        line breaks. No third-party dependency is required. (Legacy binary `.doc`
        is a different, unsupported format.)
        """
        import zipfile
        from xml.etree import ElementTree as ET

        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        try:
            with zipfile.ZipFile(str(target)) as zf:
                with zf.open("word/document.xml") as handle:
                    root = ET.parse(handle).getroot()
        except KeyError as exc:  # no document body part
            raise RuntimeError("not a valid .docx (missing word/document.xml)") from exc
        except zipfile.BadZipFile as exc:
            raise RuntimeError("not a valid .docx (not a zip archive)") from exc

        lines: List[str] = []
        for para in root.iter(f"{ns}p"):
            parts: List[str] = []
            for node in para.iter():
                if node.tag == f"{ns}t":
                    parts.append(node.text or "")
                elif node.tag == f"{ns}tab":
                    parts.append("\t")
                elif node.tag in (f"{ns}br", f"{ns}cr"):
                    parts.append("\n")
            line = "".join(parts).strip()
            if line:
                lines.append(line)
        return "\n".join(lines)

    # -- safety -----------------------------------------------------------
    def _safe(self, path: str) -> Path:
        """Resolve `path` and guarantee it stays inside the document root."""
        target = (self.root / path).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError(
                f"path '{path}' escapes document root {self.root}"
            ) from exc
        return target
