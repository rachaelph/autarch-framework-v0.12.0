"""Tests for governed document extraction (structured parse + smart field extract)."""
import json

from autarch.adapters.extraction import ExtractionAdapter
from autarch.agent import Agent, capability
from autarch.contracts import Action
from autarch.intelligence.base import ModelProvider


def _adapter(tmp_path, model=None):
    return ExtractionAdapter(root=str(tmp_path), model=model)


# --- deterministic structured parsing -----------------------------------------

def test_parse_csv_to_records(tmp_path):
    (tmp_path / "people.csv").write_text("name,age\nAlice,30\nBob,25\n")
    a = _adapter(tmp_path)
    r = a.execute(Action("doc.parse", {"path": "people.csv"}))
    assert r.ok and r.output["count"] == 2
    assert r.output["records"][0] == {"name": "Alice", "age": "30"}


def test_parse_tsv(tmp_path):
    (tmp_path / "d.tsv").write_text("a\tb\n1\t2\n")
    a = _adapter(tmp_path)
    r = a.execute(Action("doc.parse", {"path": "d.tsv"}))
    assert r.output["records"][0] == {"a": "1", "b": "2"}


def test_parse_json_object_and_list(tmp_path):
    (tmp_path / "o.json").write_text('{"k": 1}')
    (tmp_path / "l.json").write_text('[{"x": 1}, {"x": 2}]')
    a = _adapter(tmp_path)
    assert a.execute(Action("doc.parse", {"path": "o.json"})).output["object"] == {"k": 1}
    assert len(a.execute(Action("doc.parse", {"path": "l.json"})).output["records"]) == 2


def test_parse_html_strips_tags(tmp_path):
    (tmp_path / "p.html").write_text("<html><body><h1>Title</h1><p>Hello world</p></body></html>")
    a = _adapter(tmp_path)
    text = a.execute(Action("doc.parse", {"path": "p.html"})).output["text"]
    assert "Title" in text and "Hello world" in text and "<" not in text


def test_parse_inline_text(tmp_path):
    a = _adapter(tmp_path)
    r = a.execute(Action("doc.parse", {"text": "just some prose", "format": ".txt"}))
    assert r.output["text"] == "just some prose"


def test_path_escape_is_blocked(tmp_path):
    a = _adapter(tmp_path)
    r = a.execute(Action("doc.parse", {"path": "../../etc/passwd"}))
    assert not r.ok and "escapes" in r.error


# --- smart schema-guided extraction (LLM-as-extractor) ------------------------

class ExtractorModel(ModelProvider):
    """A scripted 'model' that pulls fields from an unstructured invoice."""

    name = "extractor"

    def complete(self, prompt, system=None):
        assert "DOCUMENT:" in prompt and "JSON object" in prompt
        # emulate a model reading the messy text and returning structured fields
        return json.dumps({
            "invoice_no": "INV-4471",
            "total": "1240.50",
            "vendor": "Acme Corp",
        })


def test_smart_extraction_from_unstructured_text(tmp_path):
    doc = ("Dear customer, thank you. Invoice INV-4471 from Acme Corp is enclosed. "
           "Amount due: $1,240.50 by month end.")
    (tmp_path / "invoice.txt").write_text(doc)
    a = _adapter(tmp_path, model=ExtractorModel())
    r = a.execute(Action("doc.extract",
                        {"path": "invoice.txt",
                         "fields": ["invoice_no", "total", "vendor"]}))
    assert r.ok
    assert r.output["fields"]["invoice_no"] == "INV-4471"
    assert r.output["fields"]["vendor"] == "Acme Corp"
    assert r.output["found"] == 3


def test_extract_with_field_descriptions(tmp_path):
    a = _adapter(tmp_path, model=ExtractorModel())
    r = a.execute(Action("doc.extract",
                        {"text": "Invoice INV-4471 from Acme Corp, total 1240.50",
                         "fields": {"invoice_no": "the invoice number",
                                    "total": "the amount due",
                                    "vendor": "the company name"}}))
    assert r.output["fields"]["total"] == "1240.50"


def test_extract_without_model_errors(tmp_path):
    a = _adapter(tmp_path)  # no model supplied
    r = a.execute(Action("doc.extract", {"text": "x", "fields": ["a"]}))
    assert not r.ok and "needs a model" in r.error


def test_extract_missing_fields_errors(tmp_path):
    a = _adapter(tmp_path, model=ExtractorModel())
    assert not a.execute(Action("doc.extract", {"text": "x"})).ok


def test_governed_through_kernel(tmp_path):
    (tmp_path / "d.csv").write_text("a,b\n1,2\n")
    agent = Agent("read a doc",
                  grants=[capability("doc.parse")],
                  adapters=[ExtractionAdapter(root=str(tmp_path))],
                  workspace=str(tmp_path / "ws"), auto_preside=False)
    assert agent.enact("doc.parse", {"path": "d.csv"}).executed
    # doc.extract was never granted -> kernel denies it
    assert not agent.enact("doc.extract", {"text": "x", "fields": ["a"]}).executed
