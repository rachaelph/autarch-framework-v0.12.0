"""Opt-in NMT translation of already-extracted invoice line descriptions."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Optional, Protocol, Sequence
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .demo_v2_normalization import LineDescriptionTranslation, NormalizedInvoicePackage


class TranslationError(ValueError):
    """Translation failed; task matching must not proceed on this package."""


def _description_size(description: str) -> int:
    # UTF-16 counting is conservative for supplementary Unicode characters.
    size = len(description.encode("utf-16-le")) // 2
    if not description.strip() or size > 50000:
        raise TranslationError("A description is empty or exceeds Translator's 50,000-character limit.")
    return size


class DescriptionTranslator(Protocol):
    def translate(self, descriptions: Sequence[str]) -> list[LineDescriptionTranslation]:
        ...


class _DetectedLanguage(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    language: str = Field(min_length=2)
    score: float = Field(ge=0, le=1)


class _Translation(BaseModel):
    language: str
    text: str = Field(min_length=1)


class _TranslationResult(BaseModel):
    detected_language: _DetectedLanguage = Field(alias="detectedLanguage")
    translations: list[_Translation] = Field(min_length=1, max_length=1)


class _TranslationResponse(BaseModel):
    value: list[_TranslationResult]


class AzureNmtTranslator:
    def __init__(
        self, *, endpoint: str, region: str,
        token_provider: Optional[Callable[[], str]] = None,
        api_key: Optional[str] = None,
    ):
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password \
                or parsed.query or parsed.fragment or parsed.path.rstrip("/") not in {"", "/translator/text"}:
            raise ValueError("Translator requires an HTTPS base endpoint without credentials or query parameters.")
        if not region.strip() or bool(api_key) == (token_provider is not None):
            raise ValueError("Translator requires a region and exactly one API key or token provider.")
        path = parsed.path.rstrip("/")
        if not path and ".cognitiveservices." in parsed.hostname:
            path = "/translator/text"
        self._url = f"https://{parsed.netloc}{path}/translate?api-version=2026-06-06"
        self._region = region
        self._api_key = api_key
        self._token_provider = token_provider

    def translate(self, descriptions: Sequence[str]) -> list[LineDescriptionTranslation]:
        batches: list[list[str]] = []
        batch: list[str] = []
        characters = 0
        for description in descriptions:
            size = _description_size(description)
            if batch and (len(batch) == 1000 or characters + size > 50000):
                batches.append(batch)
                batch = []
                characters = 0
            batch.append(description)
            characters += size
        if batch:
            batches.append(batch)
        results: list[LineDescriptionTranslation] = []
        for texts in batches:
            results.extend(self._translate_batch(texts))
        return results

    def _translate_batch(self, texts: Sequence[str]) -> list[LineDescriptionTranslation]:
        headers = {"Content-Type": "application/json", "Ocp-Apim-Subscription-Region": self._region}
        if self._api_key:
            headers["Ocp-Apim-Subscription-Key"] = self._api_key
        else:
            assert self._token_provider is not None
            headers["Authorization"] = f"Bearer {self._token_provider()}"
        payload = {"inputs": [{
            "text": text,
            "targets": [{"language": "en", "deploymentName": "general", "allowFallback": False}],
        } for text in texts]}
        request = urllib.request.Request(
            self._url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                request_id = response.headers.get("X-requestid")
        except urllib.error.HTTPError as exc:
            raise TranslationError(
                f"Azure Translator failed (HTTP {exc.code}); check access, language support and quota."
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TranslationError("Azure Translator could not be reached; no translation was accepted.") from exc
        try:
            parsed = _TranslationResponse.model_validate_json(raw)
        except ValidationError:
            raise TranslationError("Azure Translator returned a malformed translation response.") from None
        if len(parsed.value) != len(texts):
            raise TranslationError("Azure Translator response count does not match the requested lines.")
        results = []
        for item in parsed.value:
            translated = item.translations[0]
            if translated.language != "en" or not translated.text.strip():
                raise TranslationError("Azure Translator did not return nonempty English text for every line.")
            results.append(LineDescriptionTranslation(
                source_language=item.detected_language.language,
                language_detection_score=item.detected_language.score,
                english_text=translated.text,
                request_id=request_id,
            ))
        return results


def translate_invoice_lines(
    package: NormalizedInvoicePackage, translator: DescriptionTranslator,
) -> NormalizedInvoicePackage:
    return translate_invoice_packages([package], translator)[0]


def translate_invoice_packages(
    packages: Sequence[NormalizedInvoicePackage], translator: DescriptionTranslator,
) -> list[NormalizedInvoicePackage]:
    """Translate exact distinct source descriptions once, only within this batch."""
    descriptions = list(dict.fromkeys(line.description for package in packages for line in package.lines))
    for description in descriptions:
        _description_size(description)
    if not descriptions:
        return []
    translations = translator.translate(descriptions)
    if len(translations) != len(descriptions):
        raise TranslationError("Translation must cover every invoice line with one result per unique description.")
    if any(item.target_language != "en" or not item.english_text.strip() for item in translations):
        raise TranslationError("Translation must return nonempty English text for every unique description.")
    by_description = dict(zip(descriptions, translations))
    return [package.model_copy(update={"lines": [
        line.model_copy(update={"translation": by_description[line.description].model_copy(deep=True)})
        for line in package.lines
    ]}) for package in packages]
