from __future__ import annotations

import json
import urllib.error
from io import BytesIO

import pytest

from autarch.capex import demo_v2_translation as translation
from autarch.capex.demo_v2_normalization import LineDescriptionTranslation
from autarch.capex.demo_v2_task_suggestion import LangChainTaskSuggestionModel, suggest_tasks_for_invoice
from tests.test_capex_demo_v2_task_suggestion import (
    NOW, RecordingProvider, _line, _package, _snapshot,
)


class Response(BytesIO):
    headers = {"X-requestid": "test-translator-request"}


def _result(text="Electrical installation", language="en", source="fr"):
    return {
        "detectedLanguage": {"language": source, "score": 0.99},
        "translations": [{"language": language, "text": text}],
    }


def _client(**overrides):
    args = {"endpoint": "https://test.cognitiveservices.azure.com", "region": "eastus2",
            "token_provider": lambda: "TEST_TOKEN"}
    args.update(overrides)
    return translation.AzureNmtTranslator(**args)


def test_nmt_request_and_ordered_response_preserve_language_evidence(monkeypatch):
    requests = []

    def send(request, timeout):
        requests.append(request)
        assert timeout == 60
        return Response(json.dumps({"value": [
            _result(), _result("Filter change", source="da"),
        ]}).encode())

    monkeypatch.setattr(translation.urllib.request, "urlopen", send)
    results = _client().translate(["Installation \u00e9lectrique", "Filterskift"])
    assert [item.english_text for item in results] == ["Electrical installation", "Filter change"]
    assert [item.source_language for item in results] == ["fr", "da"]
    assert all(item.request_id == "test-translator-request" for item in results)
    request = requests[0]
    assert request.full_url == (
        "https://test.cognitiveservices.azure.com/translator/text/translate?api-version=2026-06-06"
    )
    assert request.get_header("Authorization") == "Bearer TEST_TOKEN"
    assert json.loads(request.data) == {"inputs": [
        {"text": text, "targets": [
            {"language": "en", "deploymentName": "general", "allowFallback": False},
        ]} for text in ["Installation \u00e9lectrique", "Filterskift"]
    ]}


@pytest.mark.parametrize("response", [
    {"value": []},
    {"value": [_result(), _result()]},
    {"value": [_result(language="fr")]},
    {"value": [_result(text="   ")]},
    {"value": [{"translations": [{"language": "en", "text": "Missing source language"}]}]},
    {"error": {"message": "Service did not translate"}},
])
def test_unusable_response_never_becomes_a_successful_translation(monkeypatch, response):
    monkeypatch.setattr(translation.urllib.request, "urlopen",
                        lambda *args, **kwargs: Response(json.dumps(response).encode()))
    with pytest.raises(translation.TranslationError):
        _client().translate(["Test source"])


@pytest.mark.parametrize("failure", [
    urllib.error.HTTPError("https://test", 429, "rate limited", {}, None),
    urllib.error.URLError("network unavailable"),
])
def test_service_failure_is_explicit_without_an_untranslated_fallback(monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(translation.urllib.request, "urlopen", fail)
    with pytest.raises(translation.TranslationError, match="Azure Translator"):
        _client().translate(["Test source"])


@pytest.mark.parametrize("texts,expected_sizes", [
    (["x"] * 1001, [1000, 1]),
    (["x" * 25001, "y" * 25000], [1, 1]),
    (["\U0001f527" * 12501, "x" * 24999], [1, 1]),
])
def test_batches_obey_array_and_character_limits(monkeypatch, texts, expected_sizes):
    batch_sizes = []

    def send(request, timeout):
        inputs = json.loads(request.data)["inputs"]
        batch_sizes.append(len(inputs))
        return Response(json.dumps({"value": [_result() for _ in inputs]}).encode())

    monkeypatch.setattr(translation.urllib.request, "urlopen", send)
    assert len(_client().translate(texts)) == len(texts)
    assert batch_sizes == expected_sizes


def test_invalid_description_is_rejected_before_any_billable_request(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid descriptions must be rejected before sending any text.")

    monkeypatch.setattr(translation.urllib.request, "urlopen", unexpected)
    with pytest.raises(translation.TranslationError, match="50,000"):
        _client().translate(["Valid text", "x" * 50001])


@pytest.mark.parametrize("endpoint", [
    "http://test", "https://user:password@test", "https://test?key=secret",
    "https://test/translate?api-version=3.0",
])
def test_invalid_endpoint_is_rejected(endpoint):
    with pytest.raises(ValueError, match="HTTPS base endpoint"):
        _client(endpoint=endpoint)


def test_translation_is_separate_from_source_and_does_not_create_an_exact_task_match():
    class Translator:
        def translate(self, descriptions):
            assert descriptions == ["Travaux FWKD7819574"]
            return [LineDescriptionTranslation(
                source_language="fr", language_detection_score=0.99, english_text="BUILDING",
            )]

    source = _package([_line("line-1", "Travaux FWKD7819574")])
    source.lines[0].part_number = "000123"
    original = source.model_dump(mode="json")
    translated = translation.translate_invoice_lines(source, Translator())
    assert source.model_dump(mode="json") == original
    before = source.lines[0].model_dump(exclude={"translation"})
    assert translated.lines[0].model_dump(exclude={"translation"}) == before
    assert translated.lines[0].description_for_matching == "BUILDING"
    provider = RecordingProvider(response=json.dumps({"lines": [{
        "lineId": "line-1", "resolutionStatus": "REVIEW", "taskCode": None,
        "confidence": 0.1, "rationale": "Mock review", "evidenceRefs": ["line:line-1"],
        "unresolvedReason": "Mock review",
    }]}))
    tasks = suggest_tasks_for_invoice(
        translated, _snapshot(), workflow_run_id="test-run",
        model=LangChainTaskSuggestionModel(provider), created_at_utc=NOW,
    )
    assert tasks[0].suggested_task_code is None
    assert len(provider.calls) == 1
    payload = json.loads(provider.calls[0][0].split("\n", 1)[1].split("\n\nReturn JSON")[0])
    line = payload["lines"][0]
    assert line["description"] == "Travaux FWKD7819574"
    assert line["partNumber"] == "000123"
    assert line["translation"]["englishText"] == "BUILDING"
    assert line["candidates"][0]["taskCode"] == "1000"


def test_package_rejects_missing_line_translations():
    class Translator:
        def translate(self, descriptions):
            return []

    with pytest.raises(translation.TranslationError, match="every invoice line"):
        translation.translate_invoice_lines(_package([_line("line-1", "Original")]), Translator())


def test_identical_source_descriptions_are_translated_once_with_independent_line_metadata():
    calls = []

    class Translator:
        def translate(self, descriptions):
            calls.append(list(descriptions))
            return [LineDescriptionTranslation(
                source_language="fr", language_detection_score=0.99,
                english_text=f"English: {text}", request_id="mock-request",
            ) for text in descriptions]

    package = _package([
        _line("line-1", "Installation"),
        _line("line-2", "Installation"),
        _line("line-3", "installation"),
        _line("line-4", "Installation "),
    ])
    before = package.model_dump(mode="json")
    result = translation.translate_invoice_lines(package, Translator())
    assert calls == [["Installation", "installation", "Installation "]]
    assert package.model_dump(mode="json") == before
    assert [line.line_id for line in result.lines] == [line.line_id for line in package.lines]
    assert [line.translation.english_text for line in result.lines] == [
        "English: Installation", "English: Installation", "English: installation", "English: Installation ",
    ]
    assert result.lines[0].translation.request_id == result.lines[1].translation.request_id == "mock-request"
    result.lines[0].translation.english_text = "Analyst edit to one line"
    assert result.lines[1].translation.english_text == "English: Installation"


def test_invalid_source_description_is_rejected_before_calling_an_injected_translator():
    calls = []

    class Translator:
        def translate(self, descriptions):
            calls.append(list(descriptions))
            return [LineDescriptionTranslation(
                source_language="fr", language_detection_score=0.99, english_text="Mock English",
            ) for _ in descriptions]

    with pytest.raises(translation.TranslationError):
        translation.translate_invoice_lines(_package([_line("line-1", "   ")]), Translator())
    assert calls == []


def test_reuse_crosses_invoice_boundaries_but_never_crosses_batches():
    calls = []

    class Translator:
        def translate(self, descriptions):
            calls.append(list(descriptions))
            return [LineDescriptionTranslation(
                source_language="fr", language_detection_score=0.99, english_text=f"English: {text}",
            ) for text in descriptions]

    first = _package([_line("line-1", "Installation"), _line("line-2", "Filterskift")])
    second = _package([_line("line-3", "Installation"), _line("line-4", "installation")]).model_copy(update={
        "package_id": "second", "invoice_id": "invoice-second", "po_number": "000841305",
        "source_references": {"Customer PO": "FWKD0001234"},
    })
    originals = [package.model_dump(mode="json") for package in (first, second)]
    translator = Translator()
    result = translation.translate_invoice_packages([first, second], translator)
    translation.translate_invoice_packages(result, translator)
    assert calls == [["Installation", "Filterskift", "installation"]] * 2
    assert [package.model_dump(mode="json") for package in (first, second)] == originals
    for package, original in zip(result, (first, second)):
        assert package.model_dump(exclude={"lines"}) == original.model_dump(exclude={"lines"})
        for line, source_line in zip(package.lines, original.lines):
            assert line.model_dump(exclude={"translation"}) == source_line.model_dump(exclude={"translation"})
            assert line.translation.english_text == f"English: {source_line.description}"
    assert result[1].nuvolo_context.work_order_no == "0001234"


@pytest.mark.parametrize(
    "invalid", ["   ", "x" * 50001, "\U0001f527" * 25001],
    ids=["blank", "oversize-ascii", "oversize-utf16"],
)
def test_invalid_later_invoice_prevents_all_translation_requests(invalid):
    class Translator:
        def translate(self, descriptions):
            pytest.fail("Every invoice must be validated before any text is submitted.")

    first = _package([_line("line-1", "Valid source")])
    second = _package([_line("line-2", invalid)])
    with pytest.raises(translation.TranslationError):
        translation.translate_invoice_packages([first, second], Translator())


def test_duplicate_expansion_cannot_hide_missing_unique_translations():
    class Translator:
        def translate(self, descriptions):
            assert list(descriptions) == ["Installation", "Filterskift"]
            return [LineDescriptionTranslation(
                source_language="fr", language_detection_score=0.99, english_text="Installation",
            )]

    package = _package([
        _line("line-1", "Installation"), _line("line-2", "Installation"), _line("line-3", "Filterskift"),
    ])
    with pytest.raises(translation.TranslationError, match="unique description"):
        translation.translate_invoice_packages([package], Translator())
    assert all(line.translation is None for line in package.lines)


def test_reused_translation_cannot_contain_blank_english_text():
    class Translator:
        def translate(self, descriptions):
            return [LineDescriptionTranslation(
                source_language="fr", language_detection_score=0.99, english_text="   ",
            )]

    package = _package([_line("line-1", "Installation"), _line("line-2", "Installation")])
    with pytest.raises(translation.TranslationError, match="nonempty English"):
        translation.translate_invoice_lines(package, Translator())
