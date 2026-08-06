import sys
from types import SimpleNamespace

from autarch.adapters.document import DocumentAdapter


def _reader_with(text):
    page = SimpleNamespace(extract_text=lambda: text)
    return SimpleNamespace(pages=[page])


def test_pdf_uses_text_layer_without_ocr(monkeypatch, tmp_path):
    monkeypatch.setitem(
        sys.modules,
        "pypdf",
        SimpleNamespace(PdfReader=lambda _path: _reader_with("embedded text")),
    )
    monkeypatch.setattr(
        DocumentAdapter,
        "_ocr_pdf",
        lambda _target: (_ for _ in ()).throw(AssertionError("OCR should not run")),
    )

    assert DocumentAdapter._read_pdf(tmp_path / "invoice.pdf") == "embedded text"


def test_image_only_pdf_falls_back_to_ocr(monkeypatch, tmp_path):
    monkeypatch.setitem(
        sys.modules,
        "pypdf",
        SimpleNamespace(PdfReader=lambda _path: _reader_with("")),
    )
    monkeypatch.setattr(DocumentAdapter, "_ocr_pdf", lambda _target: "OCR text")

    assert DocumentAdapter._read_pdf(tmp_path / "invoice.pdf") == "OCR text"