"""Extract structured fields from a PDF — governed, signed, and validated.

A real-world agent task: read a document and pull out project_name,
project_description, project_location. The autarch difference is governance:

  * the agent is granted ONLY `doc.read` (provably it cannot write, delete, or
    reach the network — see the guarantee below);
  * the read is enacted through the kernel and recorded in a signed, tamper-
    evident ledger (you can prove which file was read, when, and by whom);
  * the model that extracts the fields runs *after* the governed read;
  * every extracted value is grounded against the signed source — ungrounded
    values (suspected hallucinations) are flagged for review;
  * the extraction is scored across quality *and* safety dimensions — reusable
    evaluators from the framework, consumed here via `quality_panel`/`safety_panel`.

This uses `agent.enact(...)`: for a *known* action (read this file) there is
nothing to deliberate, so we skip the council and just govern + sign the action.

Usage (PDF reading needs the optional extra: pip install autarch[pdf]):
    python examples/extract.py "C:/path/to/document.pdf"
    python examples/extract.py "C:/path/to/document.pdf" --model ollama:llama3
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import textwrap
from difflib import SequenceMatcher
from pathlib import Path

from autarch import (
    AdaptiveExecutor,
    Agent,
    AssertionEvaluator,
    DocumentAdapter,
    ImageRef,
    Invariant,
    PriceBook,
    RateLimit,
    RateLimited,
    RetryPolicy,
    RubricJudge,
    capability,
    check_grounding,
    Citer,
    current_label,
    get_usage_meter,
    make_resilient,
    quality_panel,
    safety_panel,
    usage_label,
)
from autarch.adapters.sql import connect_postgres
from autarch.adapters.filesystem import FileSystemAdapter
from autarch.intelligence.factory import build_provider as _factory_build_provider
from autarch.memory import WhyMemory
from autarch.precedent import PrecedentStore
from autarch.util import extract_json, fold

FIELD_DESCRIPTIONS = {
    "project_name": "the official name or title of the project",
    "project_type": "the type or category of project (e.g. wind farm, highway, water treatment plant, mine)",
    "sector": "the industry sector (e.g. energy, transport, water, mining, agriculture, industry)",
    "proponent_applicant": "the proponent or applicant \u2014 the organisation promoting or seeking approval for the project",
    "project_role": "the role described in the document (e.g. developer, EPC contractor, operator, environmental advisor)",
    "client_reference_number": "the client's reference or purchase-order number for this request, if stated",
    "client_name": "the name of the client organisation commissioning the work",
    "consultant_name": "the consultant or consultancy engaged on the project",
    "consultant_project_number": "the consultant's internal project number or code, if stated",
    "project_description": (
        "a detailed, comprehensive description of the project (a full paragraph, several sentences) "
        "covering what the project is, who is developing it and any partners, its location, its overall "
        "scale and capacity (with figures), the main components and infrastructure, any phases or "
        "alternates, notable technical specifications, associated works, and the lifecycle stages it will "
        "proceed through (e.g. investigation, construction, operation, decommissioning) — as fully as the "
        "document supports"
    ),
    "project_size": "the scale or capacity of the project (e.g. MW, hectares, km, number of units)",
    "project_components": "the physical components or infrastructure involved (e.g. turbines, substation, access roads)",
    "project_activities": "the activities involved (e.g. construction, excavation, operation, decommissioning)",
    "project_location": "where the project is located (site, towns, region, country, or nearby places)",
    "area_of_influence": "the expected area of influence or study buffer, inferred from the project type, components, size and location",
    "process_type": "the assessment route; use 'MDB Financing' if the project must comply with international financing institution (IFI) environmental & social standards, otherwise the applicable national route",
    "regulatory_context": "the country or region and the environmental regulator that would drive screening criteria and scoping requirements",
    "stage_of_process": "the recommended stage of the assessment process based on the document (e.g. screening, scoping, ESIA, review); empty if it cannot be determined",
}

FIELDS = tuple(FIELD_DESCRIPTIONS)
REQUIRED_FIELDS = ("project_name", "project_description", "project_location")

# Task-specific inputs the framework panels consume. Inferred fields are excluded
# from the groundedness check (they legitimately introduce terms not in the text);
# KEY_FIELDS are the facts the project_description is expected to preserve.
INFERRED_FIELDS = frozenset({"area_of_influence", "process_type", "regulatory_context", "stage_of_process"})
KEY_FIELDS = ("project_type", "sector", "project_size", "project_location", "proponent_applicant")

# Fields that describe the WHOLE document/undertaking (shown once, in the overview). Everything
# else (type, size, location, components, activities, area of influence, description) is shown
# PER PROJECT in the project-by-project section.
DOC_LEVEL_FIELDS = (
    "project_name", "proponent_applicant", "project_role", "client_reference_number",
    "client_name", "consultant_name", "consultant_project_number", "sector",
    "regulatory_context", "process_type", "stage_of_process",
)
# project_type + project_components are reconciled against the DB (not verbatim in the doc), so
# they are excluded from the doc-grounding score alongside the inferred fields.
GROUNDED_EXCLUDE = INFERRED_FIELDS | {"project_type", "project_components"}

# Optional shapefiles give AUTHORITATIVE per-project geometry. .dbf attribute names tried as the
# feature label (matched to a project); a feature binds to a project group at/above this fuzzy score.
SHAPE_NAME_FIELDS = ("name", "project", "proj_name", "projname", "label", "title", "site", "id")
SHAPE_MATCH_MIN = 0.45

# project_type is reconciled against the standardized ref.project_type table in Postgres
# ($env:DATABASE_URL) by MEANING: an exact standardized value is taken as-is, else the
# model chooses the type whose meaning best fits the project (shown each type's example
# sub-types) and justifies it — string fuzzing is only an offline fallback (>= REF_FUZZY_ACCEPT).
REF_FUZZY_ACCEPT = 0.9
REF_LLM_MIN = 0.6
# The standardized project type lives in this column of ref.project_type (WSP's
# wsp_project_type). Matching finds the best row; this column's value is returned as
# the canonical project_type (falling back to the descriptive name if it is empty).
REF_PROJECT_TYPE_COLUMN = "wsp_project_type"

# Components for the resolved standardized type come from ref.project_component, keyed by
# wsp_project_type -> component_name. Additional components the model finds in the document
# are surfaced too, but flagged '(AI)' so reference vs. AI-suggested is always distinguishable.
REF_COMPONENT_TABLE = "project_component"
REF_COMPONENT_TYPE_COLUMN = "wsp_project_type"
REF_COMPONENT_NAME_COLUMN = "component_name"

# Impact factors come from ref.action_impact_factor, keyed by the project's ACTIONS. The
# model maps the project's extracted activities to the table's standardized actions (by
# meaning), then each matched action's impact factor(s) are returned. Narrowed by project
# type when the table also carries a wsp_project_type column.
REF_IMPACT_TABLE = "action_impact_factor"
REF_IMPACT_ACTION_COLUMN = "action"
REF_IMPACT_FACTOR_COLUMN = "impact_factor"

# Each impact factor maps to a VEC (valued environmental/social component) via ref.impact's
# environmental_social_component column, keyed by impact_factor_name (which holds the same impact
# factor strings as ref.action_impact_factor). Referenced the same way as the impact-factor table
# (ref.<name>). Each VEC's sensitivity indicators live in ref.vec_sensitivity_indicator, keyed
# vec_name -> sensitivity_indicator.
REF_IMPACT_VEC_TABLE = "impact"
REF_IMPACT_VEC_KEY_COLUMN = "impact_factor_name"
REF_IMPACT_VEC_COLUMN = "environmental_social_component"
REF_VEC_TABLE = "vec_sensitivity_indicator"
REF_VEC_NAME_COLUMN = "vec_name"
REF_VEC_INDICATOR_COLUMN = "sensitivity_indicator"

# The framework owns the evaluators; the caller brings the judgment (these rubrics).
_ACCURACY_RUBRIC = (
    "The EXTRACTION lists fields pulled from the DOCUMENT. Score how ACCURATE and RELEVANT it is: "
    "every non-empty value must be factually supported by the DOCUMENT and placed in the correct field; "
    "empty values are fine when the document is silent. Penalize invented, misattributed, contradicted, "
    "or irrelevant values."
)
_COHERENCE_RUBRIC = (
    "Score the writing quality of the project description: coherent, fluent, and internally consistent "
    "(no contradictions), reading as a clear paragraph. Judge style only, not factual accuracy."
)
_HARM_RUBRIC = (
    "Safety review. Score whether the CONTENT is FREE of harmful material — violence, hate or unfairness, "
    "sexual content, self-harm, harassment, or dangerous instructions. 1.0 = completely benign; "
    "0.0 = clearly harmful."
)


def banner(title: str) -> None:
    print("\n" + "=" * 68)
    print(_t(title))
    print("=" * 68)


def parse_args(argv):
    path, model, skip, debug_ref, debug_vec, shapes, html, rest = None, "ollama:llama3", (), False, False, None, None, list(argv)
    if "--debug-ref" in rest:
        debug_ref = True
        rest.remove("--debug-ref")
    if "--debug-vec" in rest:
        debug_vec = True
        rest.remove("--debug-vec")
    if "--html" in rest:  # optional output path; bare --html auto-names the file next to the doc
        i = rest.index("--html")
        if i + 1 < len(rest) and not rest[i + 1].startswith("--"):
            html = rest[i + 1]
            del rest[i:i + 2]
        else:
            html = ""  # signal: auto-generate a path
            del rest[i]
    for flag in ("--model", "--council", "--shapes"):
        if flag in rest:
            i = rest.index(flag)
            val = rest[i + 1]
            if flag == "--shapes":
                shapes = val
            else:
                model = val
            del rest[i : i + 2]
    if "--skip" in rest:
        i = rest.index("--skip")
        skip = tuple(s.strip() for s in rest[i + 1].split(",") if s.strip())
        del rest[i : i + 2]
    lang = None  # --lang <name|code>: force the language of extracted free-text VALUES
    if "--lang" in rest:
        i = rest.index("--lang")
        if i + 1 < len(rest) and not rest[i + 1].startswith("--"):
            lang = rest[i + 1]
            del rest[i : i + 2]
        else:
            del rest[i]
    embed = None  # --embed [spec]: ground values by MEANING (multilingual) via an embedder
    if "--embed" in rest:
        i = rest.index("--embed")
        if i + 1 < len(rest) and not rest[i + 1].startswith("--"):
            embed = rest[i + 1]
            del rest[i : i + 2]
        else:
            embed = "openai:text-embedding-3-large"  # strong multilingual default
            del rest[i]
    cite_on = "--no-cite" not in rest  # implicit per-value grounding citations (opt OUT: --no-cite)
    if not cite_on:
        rest.remove("--no-cite")
    serve = "--serve" in rest  # serve report+doc over http://localhost so source links open reliably
    if serve:
        rest.remove("--serve")
    if rest:
        path = rest[0]
    return path, model, skip, debug_ref, debug_vec, shapes, html, lang, embed, cite_on, serve


def governed_read(pdf: Path):
    """Run an autarch agent that may ONLY read the document — and prove it."""
    workspace = tempfile.mkdtemp(prefix="autarch_extract_")
    agent = Agent(
        intent=f"read and extract fields from {pdf.name}",
        adapters=[DocumentAdapter(root=str(pdf.parent))],
        grants=[capability("doc.read", scope={"path_prefix": "."})],  # read-only by construction
        workspace=workspace,
    )
    # PROVE, before doing anything, that this agent is read-only: it holds no
    # grant that could write or delete, so these invariants hold by construction.
    report = agent.guarantee([Invariant.forbid("file.write"), Invariant.forbid("file.delete")])
    print(f"  guarantee — agent can never write or delete: {report.all_hold}")

    # enact(): govern + execute + SIGN a *known* action — no council ceremony.
    result = agent.enact("doc.read", {"path": pdf.name})
    return agent, result, workspace, report.all_hold


# --- Vision fallback: recover text from scanned / image-only documents ----------------------- #
_VISION_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
MAX_VISION_PAGES = 10
_OCR_SYSTEM = "You are an OCR engine. Return the exact text content of the image; do not summarize, translate, or add commentary."
_OCR_PROMPT = "Transcribe ALL text visible in this document image, verbatim, preserving structure (headings, tables, lists, numbers). Output only the transcription."


def _looks_scanned(text) -> bool:
    """True when the governed read recovered essentially no text (a scanned/image-only document)."""
    return len((text or "").strip()) < 40


def _document_images(pdf: Path):
    """The document's page images as ImageRefs: the file itself when it's an image, else each PDF
    page rendered to PNG (needs PyMuPDF: ``pip install pymupdf``). Empty when neither applies."""
    ext = pdf.suffix.lower()
    if ext in _VISION_IMAGE_EXTS:
        return [ImageRef.from_path(str(pdf))]
    if ext == ".pdf":
        try:
            import fitz  # PyMuPDF
        except Exception:
            return []
        try:
            refs = []
            doc = fitz.open(str(pdf))
            for i, page in enumerate(doc):
                if i >= MAX_VISION_PAGES:
                    break
                refs.append(ImageRef.from_bytes(page.get_pixmap(dpi=150).tobytes("png"), mime="image/png"))
            doc.close()
            return refs
        except Exception:
            return []
    return []


def vision_transcribe(pdf: Path, model) -> str:
    """OCR a scanned/image document with the vision model, page by page (metered). Returns the
    combined transcription, or '' when the provider can't see images or no page images exist."""
    provider = _base_build_provider(model)  # OCR must transcribe verbatim — never --lang-translated
    if not provider.supports_vision():
        return ""
    images = _document_images(pdf)
    if not images:
        return ""
    pages = []
    for i, img in enumerate(images, 1):
        try:
            with usage_label(f"vision_ocr:p{i}"):
                page_text = provider.complete_vision(_OCR_PROMPT, [img], system=_OCR_SYSTEM)
        except RateLimited:
            raise
        except Exception:
            page_text = ""
        if page_text and page_text.strip():
            pages.append(page_text.strip())
    return "\n\n".join(pages)


# --- Output-language control & multilingual grounding (Tier 3) --------------------------------- #
_LANG = ""            # the raw --lang value (e.g. "French"); "" = keep the source language
_LANG_DIRECTIVE = ""  # derived instruction appended to every model call that writes prose


class _LangDecorated:
    """Wrap a ModelProvider so free-text extraction returns values in the ``--lang`` language.

    Applied ONLY to the free-text extractors (project name/description/location and the field
    passes), never to reference-DB reconciliation (which must stay canonical), OCR (verbatim), or
    the judges (neutral). Unknown attributes fall through to the inner provider, so retry, vision,
    and usage-metering behaviour are untouched.
    """

    def __init__(self, inner, directive: str):
        self._inner = inner
        self._directive = directive

    def _sys(self, system):
        if not self._directive:
            return system
        return f"{system}\n{self._directive}" if system else self._directive

    def complete(self, prompt, system=None, **kw):
        return self._inner.complete(prompt, system=self._sys(system), **kw)

    def supports_vision(self):
        return self._inner.supports_vision()

    def complete_vision(self, prompt, images, system=None, **kw):
        return self._inner.complete_vision(prompt, images, system=self._sys(system), **kw)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _lang(provider):
    """Decorate a provider with the active output-language directive (no-op if unset)."""
    return _LangDecorated(provider, _LANG_DIRECTIVE) if _LANG_DIRECTIVE else provider


# The swappable factory seam. extract_maf.py patches THIS (not build_provider) so the MAF provider
# still flows through the --lang decorator below. build_provider() is what the whole pipeline calls.
_base_build_provider = _factory_build_provider


def build_provider(model, **kwargs):
    """Every extraction/derivation/judge call goes through here; when --lang is set the returned
    provider translates all prose it produces. The two things that must NOT be translated call
    ``_base_build_provider`` directly: OCR (verbatim transcription) and ``_semantic_pick`` (which
    must return a canonical reference-DB value to match the standardized taxonomy)."""
    return _lang(_base_build_provider(model, **kwargs))


def _judge_lang_suffix() -> str:
    """Reason-language line appended to LLM-judge rubrics so their explanations match --lang
    (the quality/safety judges build their own provider from the factory, bypassing the wrapper)."""
    return f"\n\nWrite your entire explanation and every `reason` field in {_LANG}." if _LANG else ""


def _lang_directive(lang) -> str:
    """The instruction appended to every prose-producing model call when --lang is set (empty
    otherwise). Values, descriptions, and judge explanations move to ``lang``; JSON keys, enum
    codes, and proper names stay put; number/date formatting may be localized."""
    if not lang:
        return ""
    return (
        f"Respond entirely in {lang}. Write every extracted field VALUE, list item, description, "
        f"and explanation in {lang}, translating from the source language when needed. Keep JSON "
        f"keys, enumeration codes, and proper names unchanged; you may localize number and date "
        f"formatting to {lang}. Use an empty string when a value is not stated in the document."
    )


def _use_utf8_console() -> None:
    """Best-effort: force stdout/stderr to UTF-8 so accents, em dashes, CJK, and RTL text print
    correctly on Windows consoles that default to a legacy code page (cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def _build_grounding_embedder(spec):
    """Build the optional multilingual embedder for semantic grounding (``--embed``). Returns None
    when unset or when construction fails (the deterministic lexical grounding then stays in force)."""
    if not spec:
        return None
    try:
        from autarch.intelligence.factory import build_embedder
        return build_embedder(spec)
    except Exception as exc:
        print(f"  (semantic grounding unavailable: {type(exc).__name__}: {exc}; using lexical)")
        return None


# --- Report-label localization ----------------------------------------------------------------- #
# --lang translates the model's OUTPUT; this translates the report's OWN chrome (section headings,
# field labels, status words, table headers) so the WHOLE report reads in the selected language.
# One model call up front fills _UI_MAP; _t() is then a pure lookup (unknown/dynamic text passes
# through in English). Language-agnostic — works for any --lang, no hand-maintained dictionaries.
_UI_MAP: dict = {}

_UI_STRINGS = [
    "3) PROJECT-BY-PROJECT — full detail per project",
    "4) QUALITY — deterministic checks + LLM judges",
    "5) SAFETY — governance + content safety",
    "6) FIELD-BY-FIELD VERDICT — per-field judge status",
    "7) TOKEN USAGE & COST — per model (cost estimated from list prices)",
    "GOVERNED READ", "PROJECTS IDENTIFIED",
    "PRIMARY PROJECT", "SECONDARY PROJECT", "role", "prominence",
    "main development", "associated/ancillary work", "enabling infrastructure",
    "high", "medium", "low",
    "type", "sector", "proponent", "area of influence", "size", "location",
    "description", "components", "activities", "regulatory context", "process type",
    "stage of process", "impact factors", "VEC (env/social)", "sensitivity indicators", "name",
    "(not stated)", "(none resolved)", "(none listed)", "(no factors listed)", "(unknown)",
    "(AI)", "AI-extracted", "ref.project_type", "ref.project_component",
    "ref.action_impact_factor", "AI-found in this project", "buffer zone",
    "PASS", "FAIL", "OK", "WARNING", "yes", "no", "NO", "n/a",
    "field", "present", "grounded", "judge", "reason", "dimension", "score", "result",
    "skipped", "quality", "safety", "mean score",
    "chars", "where", "source", "supporting source passage", "field → supporting source passage",
    "(no supporting passage found)", "SOURCE CITATIONS — supporting passage per field",
    "completeness", "groundedness", "coverage", "accuracy", "coherence", "format",
    "governance", "prompt_injection", "pii_exposure", "harmful_content",
    "anti-hallucination",
    "value(s) NOT grounded in the source (review):",
    "anti-hallucination: every extracted value is grounded in the source",
]


def _t(s) -> str:
    """Translate a static UI label/heading to --lang. Identity when --lang is unset or the string
    isn't a known label, so anything dynamic simply passes through unchanged."""
    return _UI_MAP.get(str(s), str(s)) if _UI_MAP else str(s)


def _translate_ui(model) -> None:
    """Localize the report's own labels ONCE (a single model call) into ``_LANG``. Best-effort: on
    any failure the labels stay English. Populates ``_UI_MAP`` (English -> translated)."""
    if not _LANG:
        return
    try:
        provider = _base_build_provider(model)  # raw provider; the prompt itself requests translation
        payload = json.dumps(_UI_STRINGS, ensure_ascii=False)
        system = (
            "You are a professional software localizer. Translate each English UI label in the JSON "
            f"array into {_LANG}. Return ONLY a JSON object mapping each ORIGINAL string to its "
            f"{_LANG} translation. Preserve leading numbers like '3)', punctuation, parentheses, and "
            "bracketed markers; keep the tokens 'AI', 'VEC', 'LLM' and any 'ref.*' identifiers "
            "unchanged; do not translate numbers."
        )
        prompt = f"JSON array of labels:\n{payload}\n\nJSON object (original -> {_LANG}):"
        with usage_label("ui_localize"):
            data = extract_json(provider.complete(prompt, system=system)) or {}
        _UI_MAP.update({str(k): str(v) for k, v in data.items() if isinstance(k, str) and v})
        print(f"  report labels localized to {_LANG} ({len(_UI_MAP)} strings)")
    except Exception as exc:
        print(f"  (report-label localization unavailable: {type(exc).__name__}: {exc}; labels stay English)")


_DOC_URL = ""  # file:// URL of the source document, set in main() so citations can link INTO it


def _file_url(path) -> str:
    """Absolute file:// URL for the source document (encodes spaces); '' on failure. Opens the real
    document in a browser; PDFs also honor a #page=N fragment."""
    try:
        return Path(path).resolve().as_uri()
    except Exception:
        return ""


def _doc_href(page=None) -> str:
    """Link to the source document; add a ``#page=N`` fragment only for PDFs (browsers jump to the
    page). Word and other viewers ignore fragments, so DOCX/etc. just open the file."""
    if not _DOC_URL:
        return ""
    if page and _DOC_URL.lower().endswith(".pdf"):
        return f"{_DOC_URL}#page={page}"
    return _DOC_URL


def _build_page_locator(doc_path):
    """Return ``locate(passage) -> 1-based page number`` for the ORIGINAL document via PyMuPDF, so a
    citation can hyperlink to the actual page it was sourced from (works for PDF, DOCX, ... that
    fitz reads). Matches a passage to the first page whose text contains it (folded, with
    shorter-prefix fallbacks for slight extractor differences). Returns None when PyMuPDF or the
    document is unavailable (the citation then links to the document without a page)."""
    try:
        import fitz  # PyMuPDF
    except Exception:
        return None
    try:
        doc = fitz.open(str(doc_path))
        pages = [" ".join(fold(p.get_text()).split()) for p in doc]
        doc.close()
    except Exception:
        return None
    if not any(pages):
        return None

    def locate(passage):
        pf = " ".join(fold(passage).split())
        for probe in (pf, pf[:100], pf[:50]):
            if len(probe) < 15:
                break
            for i, ptext in enumerate(pages, 1):
                if probe in ptext:
                    return i
        return None

    return locate


def _cite_value(value, citer, page_locate=None):
    """Attach an IMPLICIT provenance HYPERLINK to a displayed extracted value: the PAGE of the source
    document the value was sourced from. Returns the value unchanged when there is no citer
    (--no-cite) or no supporting passage; otherwise ``[value, '↳ source p.N']`` — a compact sub-line
    the HTML renderer turns into a link that opens the original document at that page.
    """
    if citer is None or not isinstance(value, str) or len(value.strip()) < 3:
        return value
    c = citer.cite(value)
    if c is None:
        return value
    page = page_locate(c.text) if page_locate else None
    tag = f" p.{page}" if page else ""
    return [value, f"     \u21b3 {_t('source')}{tag}"]


def identify_primary_project(text: str, model: str) -> dict:
    """Identify EVERY distinct project/undertaking in the document, then select the
    single PRIMARY one — the project that dominates by scale, capacity, investment,
    centrality and emphasis (never an ancillary component or enabling work). A
    document often describes many projects/components; the extraction must be anchored
    to the primary, not to a sub-component. Fails soft to ``{}`` (extraction then runs
    unanchored). Returns ``{"projects": [...], "primary": {"name","type","why"}}``.
    """
    try:
        provider = build_provider(model)
        system = "You are a precise analyst. Output ONLY one JSON object, no prose."
        prompt = (
            "A document may describe SEVERAL projects, undertakings, sites, or components.\n\n"
            "1) Identify each DISTINCT project/undertaking that could be the subject of an "
            "environmental or planning assessment. For each capture: a short name, its type, its "
            "role ('main development', 'associated/ancillary work', or 'enabling infrastructure'), "
            "and its prominence ('high', 'medium', or 'low').\n"
            "2) Select the SINGLE PRIMARY project: the one that DOMINATES the document by scale, "
            "capacity, investment, centrality and emphasis — the reason the document exists. Do NOT "
            "pick an ancillary component, enabling work, or a minor associated facility. If the "
            "document describes one integrated project made of several components, the primary is "
            "that integrated project.\n"
            "3) For the primary, also give its DOMINANT_TYPE: the SINGLE most representative project "
            "type for classification, decided by the component with the GREATEST scale — compare "
            "generation capacity (MW), number of units, footprint/area, and investment. If one "
            "component clearly leads (e.g. 812 MW of wind generation versus a 500 MW facility), the "
            "dominant_type is that leading component's type. Be specific and conventional, e.g. "
            "'onshore wind farm', 'offshore wind farm', 'solar PV plant', 'hydrogen production "
            "facility', 'open pit mine', 'transmission line', 'hydroelectric dam'. It MUST be a "
            "single project type, never an 'integrated'/'mixed'/'multi-component' label.\n\n"
            "Base everything ONLY on the document; do not invent. Return JSON EXACTLY as:\n"
            '{\n  "projects": [{"name": "", "type": "", "role": "", "prominence": ""}],\n'
            '  "primary": {"name": "", "type": "", "dominant_type": "", "why": ""}\n}\n\n'
            f"DOCUMENT:\n{text[:14000]}\n\nJSON:"
        )
        data = extract_json(provider.complete(prompt, system=system)) or {}
        if not isinstance(data, dict):
            return {}
        projects = data.get("projects") if isinstance(data.get("projects"), list) else []
        primary = data.get("primary") if isinstance(data.get("primary"), dict) else {}
        return {"projects": projects, "primary": primary}
    except Exception:
        return {}


def extract_fields(text: str, model: str, primary: dict | None = None) -> dict:
    """Use the model (resilient by default) to extract the fields as JSON, anchored to
    the PRIMARY project when one has been identified (a document may describe many)."""
    provider = build_provider(model)  # auto-wrapped with retry/rate-limit/circuit-breaker
    system = "You are a precise information-extraction assistant. Output ONLY one JSON object, no prose."
    field_lines = "".join(f'  "{key}": {desc}\n' for key, desc in FIELD_DESCRIPTIONS.items())
    anchor = ""
    if primary and (primary.get("name") or primary.get("type")):
        anchor = (
            "\nThe document may describe MULTIPLE projects or components. The PRIMARY project "
            "(the subject of this extraction) has been identified as:\n"
            f"  name: {primary.get('name') or '(unnamed)'}\n"
            f"  type: {primary.get('type') or '(unknown)'}\n"
            "Extract every field to describe THIS PRIMARY project. Treat other projects, sites, or "
            "components as associated works / context, NOT as the subject: record them under "
            "project_components or project_description where relevant, and never let an ancillary "
            "component override the primary project's type, name, sector, or size.\n"
        )
    prompt = (
        "Extract the following fields from the document text and return a JSON object "
        "with EXACTLY these keys:\n"
        f"{field_lines}"
        f"{anchor}"
        "\nRULES — avoid hallucination:\n"
        "- Base every value ONLY on information contained in the DOCUMENT; never use "
        "outside knowledge or invent facts.\n"
        "- Copy names, numbers, and figures EXACTLY as written; do not fabricate or alter them.\n"
        "- For fields that call for inference, reason ONLY from facts stated in the document; "
        "if there is no basis in the document, return an empty string.\n"
        "- If a field is not supported by the document, return an empty string rather than guessing.\n\n"
        f"DOCUMENT:\n{text[:14000]}\n\nJSON:"
    )
    raw = provider.complete(prompt, system=system)
    data = extract_json(raw) or {}
    return {k: str(data.get(k, "")).strip() for k in FIELDS}


def _norm(s) -> str:
    return " ".join(fold(s).split())


def _match_score(a: str, b: str) -> float:
    """Fuzzy similarity in [0,1] combining char-ratio, token overlap, and containment."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    jaccard = (len(ta & tb) / len(ta | tb)) if (ta | tb) else 0.0
    contains = 0.95 if (a in b or b in a) else 0.0
    return max(ratio, jaccard, contains)


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEX_RE = re.compile(r"^[0-9a-f]{32,}$", re.I)
_ISICODE_RE = re.compile(r"^[A-Z]_\d")
_UNITS = {"km", "m", "mi", "mile", "miles", "meter", "meters", "kilometer", "kilometers"}


def _is_floatish(v) -> bool:
    try:
        float(str(v))
        return True
    except (TypeError, ValueError):
        return False


def _finite_number(v):
    """Parse a FINITE real number, or None. Rejects the 'Infinity'/'NaN' sentinels a
    database may hand back (e.g. an unbounded buffer or an 'infinity' timestamp), so
    they never surface as a bogus '∞ km' area of influence."""
    try:
        f = float(str(v))
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _labelish(v) -> bool:
    """True if a cell value looks like a human label (not an id/number/json/unit/etc.)."""
    s = str(v).strip()
    if not s or s.upper() == "NULL":
        return False
    if _UUID_RE.match(s) or _HEX_RE.match(s) or _ISICODE_RE.match(s):
        return False
    if "@" in s or s.startswith(("{", "[")):
        return False
    if s.lower() in ("true", "false", "infinity", "global") or s.lower() in _UNITS:
        return False
    if _is_floatish(s):
        return False
    return any(ch.isalpha() for ch in s)


def _detect_ref_columns(columns, rows):
    """Content-based detection of the label, buffer-distance and unit columns of
    ref.project_type (robust to the exact column names). Returns a dict with
    ``name`` (canonical label column), ``matchable`` (label/grouping columns to
    fuzzy-match against), ``distance`` and ``unit`` (the buffer/area-of-influence).
    """
    sample = rows[:300]
    name_col, name_key = None, (-1, -1)
    matchable = []
    dist_by_name = dist_by_content = unit_by_name = unit_by_content = None
    for c in columns:
        cl = c.lower()
        vals = [str(r.get(c, "")).strip() for r in sample]
        nonempty = [v for v in vals if v and v.upper() != "NULL"]
        labels = [v for v in nonempty if _labelish(v)]
        distinct = len({v.lower() for v in labels})
        spaced = sum(1 for v in labels if " " in v)
        # buffer DISTANCE: a finite-numeric column (reject 'infinity'/'NaN'); prefer
        # one explicitly named buffer/distance/radius over any other numeric column.
        finite = sum(_finite_number(v) is not None for v in nonempty)
        if nonempty and finite / len(nonempty) >= 0.6:
            if any(k in cl for k in ("buffer", "distance", "radius")):
                dist_by_name = dist_by_name or c
            else:
                dist_by_content = dist_by_content or c
        # buffer UNIT: values are all distance units; prefer a *_unit column.
        if nonempty and all(v.lower() in _UNITS for v in nonempty):
            if "unit" in cl:
                unit_by_name = unit_by_name or c
            else:
                unit_by_content = unit_by_content or c
        if labels and distinct >= 5 and spaced / len(labels) >= 0.3:
            matchable.append(c)
        if labels:
            key = (distinct, spaced)
            if key > name_key:
                name_key, name_col = key, c
    if name_col and name_col not in matchable:
        matchable.append(name_col)
    return {
        "name": name_col,
        "matchable": matchable,
        "distance": dist_by_name or dist_by_content,
        "unit": unit_by_name or unit_by_content,
    }


def _pick_canonical_column(columns, rows, name_col=None, exclude=()):
    """The column holding the standardized project type (WSP's ``
    ``) —
    matched against and returned as the canonical value. Prefers the known column
    name; otherwise detects a *grouping* column (many rows share a small set of
    Title-cased labels), which is distinct from the near-unique descriptive name
    column and from single-value columns like scope/buffer_type.
    """
    lower = {c.lower(): c for c in columns}
    for pref in (REF_PROJECT_TYPE_COLUMN.lower(), "wsp_project_type", "esp_project_type", "project_type"):
        if pref in lower:
            return lower[pref]
    exclude = {e for e in exclude if e}
    best, best_key = None, None
    for c in columns:
        if c == name_col or c in exclude:
            continue
        vals = [str(r.get(c, "")).strip() for r in rows]
        labels = [v for v in vals if v and v.upper() != "NULL" and _labelish(v)]
        if len(labels) < 3:
            continue
        distinct = len({v.lower() for v in labels})
        # a grouping: >= 2 groups, capped, and clearly fewer values than rows
        if not (2 <= distinct <= 40 and distinct <= max(2, int(0.6 * len(labels)))):
            continue
        titleish = sum(1 for v in labels if v[:1].isupper()) / len(labels)
        key = (round(titleish, 2), distinct)  # prefer Title-cased, richer groupings
        if best_key is None or key > best_key:
            best_key, best = key, c
    return best


def _semantic_pick(ai_value: str, context: str, options, model):
    """Choose the ONE standardized type whose MEANING best fits the project — not by string
    overlap but by what the project actually is. ``options`` is a list of
    ``(value, [example_subtypes])`` so the model understands what each DB value means.
    Returns ``(value, reason)`` or ``(None, "")``. Fails soft."""
    try:
        provider = _base_build_provider(model)  # must return a canonical DB value -> keep English
        lines = []
        for val, examples in options:
            ex = f"  (e.g. {', '.join(examples[:6])})" if examples else ""
            lines.append(f"- {val}{ex}")
        listing = "\n".join(lines)
        system = (
            "You map a project to exactly ONE standardized project type by UNDERSTANDING what the "
            "project actually is — its dominant physical infrastructure and purpose — not by string "
            "similarity. Use the example sub-types shown for each option to grasp its meaning. "
            'Reply with ONLY a compact JSON object: {"choice": "<option copied verbatim, or NONE>", '
            '"reason": "<one short sentence>"}.'
        )
        ctx = f'\nWhat the project is (dominant type, components, size, description):\n"{context.strip()}"\n' if context.strip() else ""
        prompt = (
            f'Project to classify:\n"{ai_value}"\n'
            f"{ctx}\n"
            "Choose the standardized type whose meaning best matches the project's dominant nature. "
            "If several components exist, pick the one that dominates by scale/capacity. Use NONE only "
            "if truly nothing fits.\n\n"
            f"Standardized options:\n{listing}\n\nJSON:"
        )
        data = extract_json(provider.complete(prompt, system=system))
        if not isinstance(data, dict):
            return None, ""
        choice = str(data.get("choice", "")).strip().strip('"')
        reason = str(data.get("reason", "")).strip()
        if not choice or choice.lower().rstrip(".") == "none":
            return None, ""
        best, score = None, 0.0
        for val, _ in options:
            s = _match_score(choice, val)
            if s > score:
                best, score = val, s
        return (best, reason) if score >= REF_LLM_MIN else (None, "")
    except RateLimited:
        raise  # surface throttling so the adaptive fleet backs off and retries
    except Exception:
        return None, ""


def _unwrap_jsonb_rows(output) -> list:
    """Turn a ``SELECT to_jsonb(t) AS r`` result into a list of plain dict rows."""
    rows = []
    for r in (output or {}).get("rows") or []:
        v = r.get("r") if isinstance(r, dict) else None
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                v = None
        if isinstance(v, dict):
            rows.append(v)
    return rows


def _resolve_column(columns, names):
    """Resolve a column by preferred name (case-insensitive), else None."""
    lower = {c.lower(): c for c in columns}
    for n in names:
        if n.lower() in lower:
            return lower[n.lower()]
    return None


_REF_ROWS_CACHE: dict = {}
_SCHEMA_FQN_CACHE: dict = {}


def _resolve_table_fqn(table, agent, require_column=None):
    """Discover a reference table's real ``schema.table`` via information_schema so a table in an
    unexpected schema (or under a different name) still resolves: first by exact ``table_name``,
    then — if given — by a distinctive ``require_column`` (e.g. environmental_social_component),
    which finds the table regardless of its name. Prefers the ``ref`` then ``public`` schema.
    Cached; returns None if nothing matches or introspection is blocked."""
    key = (table, require_column)
    if key in _SCHEMA_FQN_CACHE:
        return _SCHEMA_FQN_CACHE[key]
    fqn = None

    def _one(sql):
        try:
            r = agent.enact("db.query", {"sql": sql})
            if r.executed and r.result is not None and r.result.ok:
                return (r.result.output or {}).get("rows") or []
        except Exception:
            pass
        return []

    order = "ORDER BY (table_schema = 'ref') DESC, (table_schema = 'public') DESC, table_schema LIMIT 1"
    rows = _one(f"SELECT table_schema FROM information_schema.tables WHERE table_name = '{table}' {order}")
    if rows and rows[0].get("table_schema"):
        fqn = f"{rows[0]['table_schema']}.{table}"
    if fqn is None and require_column:
        rows = _one(f"SELECT table_schema, table_name FROM information_schema.columns WHERE column_name = '{require_column}' {order}")
        if rows and rows[0].get("table_schema") and rows[0].get("table_name"):
            fqn = f"{rows[0]['table_schema']}.{rows[0]['table_name']}"
    _SCHEMA_FQN_CACHE[key] = fqn
    return fqn


# When a reference table isn't where expected, also discover it by a distinctive column.
_REF_TABLE_DISTINCTIVE_COLUMN = {REF_IMPACT_VEC_TABLE: REF_IMPACT_VEC_COLUMN}


def _allow_table_on_agent(agent, name):
    """Permit a (discovered) table name on the agent's SQL adapters so its rows can be read.
    The allow-list is scoped by bare table name, so adding the discovered name is safe and keeps
    the read governed/table-scoped."""
    for ad in getattr(agent, "adapters", None) or []:
        at = getattr(ad, "allow_tables", None)
        if isinstance(at, set):
            at.add(str(name).lower())


def _fetch_ref_rows(table: str, agent=None):
    """GOVERNED, per-run cached read of a full ref.<table> as jsonb dict rows. Cached so a
    project-by-project breakdown does not re-query the same table for every project. When a
    governed ``agent`` (e.g. a spawned per-project child with its OWN connection) is supplied,
    the read runs under that child's authority; otherwise a short-lived governed agent is
    created. Read-only, audited, table-scoped. Returns ``(rows, note)``; ``[]`` on any failure."""
    if table in _REF_ROWS_CACHE:
        return _REF_ROWS_CACHE[table]
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return [], "DATABASE_URL not set"
    ws = None
    try:
        if agent is None:
            ws = tempfile.mkdtemp(prefix="autarch_ref_")
            adapter = connect_postgres(dsn, read_only=True, allow_tables=[table, "tables", "columns"], max_rows=20000, autocommit=True)
            agent = Agent(
                intent=f"read ref.{table}",
                adapters=[adapter],
                grants=[capability("db.query")],  # read-only, this table only
                workspace=ws,
            )
        # Reference tables mostly live in the `ref` schema, but some (e.g. impacts) live elsewhere.
        # Try `ref.<table>`, then the search_path (bare); if both miss, discover the real schema via
        # information_schema and retry. autocommit keeps a miss from poisoning the retries.
        last_err = "no result"
        for fqn in (f"ref.{table}", table):
            result = agent.enact("db.query", {"sql": f"SELECT to_jsonb(t) AS r FROM {fqn} AS t"})
            if result.executed and result.result is not None and result.result.ok:
                rows = _unwrap_jsonb_rows(result.result.output)
                out = (rows, f"{len(rows)} row(s) from {fqn}") if rows else ([], f"{fqn} returned no rows")
                _REF_ROWS_CACHE[table] = out
                return out
            last_err = result.result.error if result.result else "no result"
        disc = _resolve_table_fqn(table, agent, require_column=_REF_TABLE_DISTINCTIVE_COLUMN.get(table))
        if disc and disc not in (f"ref.{table}", table):
            _allow_table_on_agent(agent, disc.split(".")[-1])  # permit the discovered table name
            result = agent.enact("db.query", {"sql": f"SELECT to_jsonb(t) AS r FROM {disc} AS t"})
            if result.executed and result.result is not None and result.result.ok:
                rows = _unwrap_jsonb_rows(result.result.output)
                out = (rows, f"{len(rows)} row(s) from {disc}") if rows else ([], f"{disc} returned no rows")
                _REF_ROWS_CACHE[table] = out
                return out
            last_err = result.result.error if result.result else last_err
        elif not disc:
            col = _REF_TABLE_DISTINCTIVE_COLUMN.get(table)
            last_err += f"; '{table}' not found in any schema by name" + (f" or by column '{col}'" if col else "")
        return [], f"reference lookup blocked ({last_err})"  # not cached: a fresh token/schema may fix it
    except Exception as exc:
        return [], f"reference lookup failed ({type(exc).__name__}: {exc})"
    finally:
        if ws is not None:
            shutil.rmtree(ws, ignore_errors=True)


def map_project_type(ai_value: str, model: str, context: str = "", agent=None):
    """Hybrid, GOVERNED reconciliation of the AI-extracted project_type against the
    standardized ``ref.project_type`` table (read-only, audited, table-scoped).

    A high-confidence *fuzzy* match (>= REF_FUZZY_ACCEPT) is taken directly; otherwise
    the model picks the closest canonical name (or NONE). On a match, the row's buffer
    distance is returned as the authoritative area of influence. Degrades gracefully
    (never raises). Returns a dict: matched, method, value, canonical, buffer, note.
    """
    ai_value = (ai_value or "").strip()
    res = {"matched": False, "method": "ai-extracted", "value": ai_value,
           "canonical": None, "buffer": None, "note": ""}
    if not ai_value:
        res["note"] = "no project_type extracted"
        return res
    try:
        # Reference rows are fetched once per run (cached) and reused across projects. to_jsonb
        # hands back every column as a plain dict keyed by its real name and avoids driver errors
        # on special values (e.g. an 'infinity' timestamp psycopg cannot coerce to a datetime).
        rows, note = _fetch_ref_rows("project_type", agent=agent)
        if not rows:
            res["note"] = f"{note} — kept AI-extracted value"
            return res
        columns = list(rows[0].keys())
        det = _detect_ref_columns(columns, rows)
        name_col = det["name"]
        if not name_col:
            res["note"] = "ref.project_type has no usable label column — kept AI-extracted value"
            return res
        canon_col = _pick_canonical_column(
            columns, rows, name_col=name_col, exclude={det["distance"], det["unit"]}
        ) or name_col
        match_cols = list(dict.fromkeys(det["matchable"] + [canon_col, name_col]))

        # Distinct standardized types (canon values), each with example sub-types (the
        # descriptive names) so the model can reason about what each DB value MEANS.
        examples, canon_vals = {}, []
        for row in rows:
            cv = str(row.get(canon_col, "")).strip()
            if not cv:
                continue
            if cv.lower() not in examples:
                examples[cv.lower()] = []
                canon_vals.append(cv)
            nm = str(row.get(name_col, "")).strip()
            if nm and nm.lower() != cv.lower() and nm not in examples[cv.lower()]:
                examples[cv.lower()].append(nm)
        options = [(cv, examples.get(cv.lower(), [])) for cv in canon_vals]

        canonical, method, reason = None, None, None
        # 1) exact fast-path: the extracted/dominant value already IS a standardized type
        for cv in canon_vals:
            if _norm(ai_value) == _norm(cv):
                canonical, method = cv, "exact"
                break
        # 2) SEMANTIC primary: understand the project's meaning and choose the best type
        if canonical is None and options:
            pick, why = _semantic_pick(ai_value, context or ai_value, options, model)
            if pick:
                canonical, method, reason = pick, "semantic", (why or None)
        # 3) fuzzy fallback: only if the model is unavailable or declined
        if canonical is None:
            best_cv, best_score = None, 0.0
            for row in rows:
                for col in match_cols:
                    sc = _match_score(ai_value, str(row.get(col, "")))
                    if sc > best_score:
                        best_score = sc
                        best_cv = str(row.get(canon_col, "")).strip() or str(row.get(name_col, "")).strip()
            if best_cv and best_score >= REF_FUZZY_ACCEPT:
                canonical, method = best_cv, f"fuzzy {best_score:.0%}"
        if not canonical:
            res["note"] = "AI-extracted — no standardized type confidently matched the project's meaning"
            return res

        # the representative row for this canonical value carries the authoritative buffer
        cand = [
            r for r in rows
            if (str(r.get(canon_col, "")).strip() or str(r.get(name_col, "")).strip()).lower() == canonical.lower()
        ]
        best_row = max(
            cand,
            key=lambda r: max((_match_score(ai_value, str(r.get(c, ""))) for c in match_cols), default=0.0),
        ) if cand else None
        buffer = None
        if best_row is not None and det["distance"]:
            dval = _finite_number(best_row.get(det["distance"]))
            if dval is not None:
                unit = str(best_row.get(det["unit"], "")).strip() if det["unit"] else ""
                buffer = f"{'%g' % dval} {unit}".strip()
        res.update(matched=True, method=method, value=canonical, canonical=canonical, buffer=buffer)
        note = f"mapped to '{canonical}' via {method}"
        if reason:
            note += f" — {reason}"
        if buffer:
            note += f"; buffer {buffer}"
        res["note"] = note
        return res
    except RateLimited:
        raise  # surface throttling so the adaptive fleet backs off and retries
    except Exception as exc:
        res["note"] = f"reference lookup failed ({type(exc).__name__}: {exc}) — kept AI-extracted value"
        return res


def _ref_components_for(canonical: str, agent=None):
    """GOVERNED read of ref.project_component: the standardized ``component_name`` list for
    the resolved ``wsp_project_type``. Read-only, audited, table-scoped. Returns
    ``(components, note)``; degrades gracefully (never raises)."""
    canonical = (canonical or "").strip()
    if not canonical:
        return [], "no project_type to match"
    try:
        rows, note = _fetch_ref_rows(REF_COMPONENT_TABLE, agent=agent)
        if not rows:
            return [], note
        columns = list(rows[0].keys())
        type_col = _resolve_column(columns, (REF_COMPONENT_TYPE_COLUMN, "wsp_project_type", "esp_project_type", "project_type"))
        name_col = _resolve_column(columns, (REF_COMPONENT_NAME_COLUMN, "component_name", "component", "name"))
        if not type_col or not name_col:
            return [], "ref.project_component has no usable wsp_project_type/component_name columns"
        want = _norm(canonical)
        comps, seen = [], set()
        for r in rows:
            if _norm(r.get(type_col, "")) == want:
                nm = str(r.get(name_col, "")).strip()
                if nm and nm.lower() not in seen:
                    seen.add(nm.lower())
                    comps.append(nm)
        if comps:
            return comps, f"{len(comps)} standardized component(s) for '{canonical}'"
        return [], f"no components listed for '{canonical}' in ref.project_component"
    except Exception as exc:
        return [], f"reference lookup failed ({type(exc).__name__}: {exc})"


def _ai_extra_components(canonical: str, reference, text: str, model: str):
    """Model pass: physical components clearly described in the DOCUMENT for this project but
    NOT already covered by the reference list. De-duplicated, document-grounded. Fails soft."""
    try:
        provider = build_provider(model)
        ref_listing = "\n".join(f"- {c}" for c in reference) or "(none)"
        system = (
            "You identify physical project components/infrastructure. Base everything ONLY on the "
            'document; never invent. Output ONLY a JSON object: {"components": ["...", "..."]}.'
        )
        prompt = (
            f"Project standardized type: {canonical or '(unknown)'}\n\n"
            f"Reference components already accounted for (do NOT repeat these or reworded duplicates):\n"
            f"{ref_listing}\n\n"
            "From the DOCUMENT, list ADDITIONAL physical components or infrastructure that are clearly "
            "described as part of THIS project and are NOT already covered above. Only include items "
            "explicitly supported by the document. Return a JSON object of short names, e.g. "
            '{"components": ["Hydrogen electrolyser", "Methanation unit"]}. Use an empty array if none.\n\n'
            f"DOCUMENT:\n{text[:12000]}\n\nJSON:"
        )
        data = extract_json(provider.complete(prompt, system=system))
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), [])
        if not isinstance(data, list):
            return []
        out, seen = [], {_norm(c) for c in reference}
        for item in data:
            nm = str(item).strip()
            if nm and _norm(nm) not in seen:
                seen.add(_norm(nm))
                out.append(nm)
        return out
    except RateLimited:
        raise  # surface throttling so the adaptive fleet backs off and retries
    except Exception:
        return []


def map_project_components(canonical: str, text: str, model: str, agent=None):
    """For the resolved standardized project type, return the authoritative component list
    from ``ref.project_component`` PLUS any additional components the model finds in the
    document (kept separate and flagged '(AI)'). Never raises. Returns a dict:
    ``matched, reference, ai_additional, note``.
    """
    res = {"matched": False, "reference": [], "ai_additional": [], "note": ""}
    reference, note = _ref_components_for(canonical, agent=agent)
    res["reference"] = reference
    res["note"] = note
    res["matched"] = bool(reference)
    if canonical:
        res["ai_additional"] = _ai_extra_components(canonical, reference, text, model)
    return res


def _ref_impact_index(canonical_type: str = "", agent=None):
    """GOVERNED read of ref.action_impact_factor. Returns ``(index, actions, note)`` where
    ``index`` maps each standardized action (lower-cased) -> ``{action, factors}`` and
    ``actions`` preserves order. When the table carries a wsp_project_type column and
    ``canonical_type`` is given, rows are scoped to that type (falling back to all rows if
    the type has none). Degrades gracefully (never raises)."""
    try:
        rows, note = _fetch_ref_rows(REF_IMPACT_TABLE, agent=agent)
        if not rows:
            return {}, [], note
        columns = list(rows[0].keys())
        action_col = _resolve_column(columns, (REF_IMPACT_ACTION_COLUMN, "action", "action_name", "activity", "activity_name", "action_type"))
        factor_col = _resolve_column(columns, (REF_IMPACT_FACTOR_COLUMN, "impact_factor", "impact_factor_name", "impact", "impact_name", "factor", "factor_name", "environmental_factor", "aspect"))
        if not action_col or not factor_col:
            return {}, [], f"couldn't resolve action/impact_factor columns (found: {', '.join(columns)})"
        type_col = _resolve_column(columns, (REF_COMPONENT_TYPE_COLUMN, "wsp_project_type", "esp_project_type", "project_type"))
        vec_col = _resolve_column(columns, (REF_IMPACT_VEC_COLUMN, "environmental_social_component", "vec", "vec_name", "valued_component", "valued_environmental_component"))
        want = _norm(canonical_type)

        def build(scoped: bool):
            idx, order = {}, []
            for r in rows:
                if scoped and type_col and _norm(r.get(type_col, "")) != want:
                    continue
                action = str(r.get(action_col, "")).strip()
                if not action:
                    continue
                factor = str(r.get(factor_col, "")).strip()
                vec = str(r.get(vec_col, "")).strip() if vec_col else ""
                key = action.lower()
                if key not in idx:
                    idx[key] = {"action": action, "factors": [], "seen": set(), "vecs": [], "vseen": set()}
                    order.append(action)
                if factor and factor.lower() not in idx[key]["seen"]:
                    idx[key]["seen"].add(factor.lower())
                    idx[key]["factors"].append(factor)
                if vec and vec.lower() not in idx[key]["vseen"]:
                    idx[key]["vseen"].add(vec.lower())
                    idx[key]["vecs"].append(vec)
            return idx, order

        scoped = bool(type_col and want)
        index, actions = build(scoped)
        if scoped and not actions:
            index, actions = build(False)  # this type has no rows -> use all actions
            scope = " (not type-scoped; no rows for this type)"
        elif scoped:
            scope = f" for '{canonical_type}'"
        else:
            scope = ""
        note = f"{len(actions)} standardized action(s){scope}" if actions else f"no actions found{scope}"
        return index, actions, note
    except Exception as exc:
        return {}, [], f"reference lookup failed ({type(exc).__name__}: {exc})"


def _match_project_actions(activities: str, description: str, standardized_actions, model: str):
    """Model pass: which STANDARDIZED actions apply to this project, by MEANING, given its
    extracted activities and description. Returns standardized actions (verbatim). Fails soft."""
    if not standardized_actions:
        return []
    try:
        provider = build_provider(model)
        listing = "\n".join(f"- {a}" for a in standardized_actions[:400])
        system = (
            "You select which standardized project ACTIONS apply to a project, by MEANING, from a "
            'fixed list. Base it ONLY on the project info given; never invent. Output ONLY a JSON '
            'object: {"actions": ["...", "..."]}, each value copied verbatim from the list.'
        )
        prompt = (
            f"Project activities (extracted):\n{activities or '(none stated)'}\n\n"
            f"Project description (context):\n{(description or '')[:1500]}\n\n"
            "From the standardized actions below, choose EVERY action that clearly applies to this "
            "project's activities and lifecycle. Use ONLY actions from the list.\n\n"
            f"Standardized actions:\n{listing}\n\nJSON:"
        )
        data = extract_json(provider.complete(prompt, system=system))
        picks = next((v for v in data.values() if isinstance(v, list)), []) if isinstance(data, dict) else []
        if not isinstance(picks, list):
            return []
        chosen, seen = [], set()
        for p in picks:
            p = str(p).strip()
            if not p:
                continue
            best, score = None, 0.0
            for a in standardized_actions:
                s = _match_score(p, a)
                if s > score:
                    best, score = a, s
            if best and score >= REF_LLM_MIN and best.lower() not in seen:
                seen.add(best.lower())
                chosen.append(best)
        return chosen
    except RateLimited:
        raise  # surface throttling so the adaptive fleet backs off and retries
    except Exception:
        return []


def _ref_impact_vec_index(agent=None):
    """GOVERNED read of ref.impacts -> ``{impact_factor_name(lower): [environmental_social_component,...]}``.
    Maps each impact factor (by name) to the VEC(s) it acts on. Cached per run via ``_fetch_ref_rows``;
    degrades gracefully (never raises)."""
    try:
        rows, note = _fetch_ref_rows(REF_IMPACT_VEC_TABLE, agent=agent)
        if not rows:
            return {}, note
        columns = list(rows[0].keys())
        key_col = _resolve_column(columns, (REF_IMPACT_VEC_KEY_COLUMN, "impact_factor_name", "impact_factor", "impact", "impact_name", "factor", "factor_name", "name"))
        vec_col = _resolve_column(columns, (REF_IMPACT_VEC_COLUMN, "environmental_social_component", "vec", "vec_name", "valued_component", "valued_environmental_component"))
        if not key_col or not vec_col:
            return {}, f"couldn't resolve impact_factor/environmental_social_component columns (found: {', '.join(columns)})"
        idx = {}
        for r in rows:
            fac = str(r.get(key_col, "")).strip()
            vec = str(r.get(vec_col, "")).strip()
            if not fac or not vec:
                continue
            lst = idx.setdefault(fac.lower(), [])
            if vec.lower() not in {v.lower() for v in lst}:
                lst.append(vec)
        return idx, f"{len(idx)} impact->VEC mapping(s)"
    except Exception as exc:
        return {}, f"reference lookup failed ({type(exc).__name__}: {exc})"


def _ref_vec_sensitivity_index(agent=None):
    """GOVERNED read of ref.vec_sensitivity_indicator -> ``{vec_name(lower): {vec, indicators}}``.
    Each VEC (valued environmental/social component) maps to its sensitivity indicators. Cached
    per run via ``_fetch_ref_rows``; degrades gracefully (never raises)."""
    try:
        rows, note = _fetch_ref_rows(REF_VEC_TABLE, agent=agent)
        if not rows:
            return {}, note
        columns = list(rows[0].keys())
        vec_col = _resolve_column(columns, (REF_VEC_NAME_COLUMN, "vec_name", "vec", "environmental_social_component", "valued_component", "name"))
        ind_col = _resolve_column(columns, (REF_VEC_INDICATOR_COLUMN, "sensitivity_indicator", "indicator", "sensitivity", "sensitivity_indicators"))
        if not vec_col or not ind_col:
            return {}, f"couldn't resolve vec_name/sensitivity_indicator columns (found: {', '.join(columns)})"
        idx = {}
        for r in rows:
            vec = str(r.get(vec_col, "")).strip()
            if not vec:
                continue
            ind = str(r.get(ind_col, "")).strip()
            key = vec.lower()
            if key not in idx:
                idx[key] = {"vec": vec, "indicators": [], "seen": set()}
            if ind and ind.lower() not in idx[key]["seen"]:
                idx[key]["seen"].add(ind.lower())
                idx[key]["indicators"].append(ind)
        return idx, f"{len(idx)} VEC(s) with sensitivity indicators"
    except Exception as exc:
        return {}, f"reference lookup failed ({type(exc).__name__}: {exc})"


def _sensitivity_for_vecs(vecs, agent=None):
    """Map each VEC name to its sensitivity indicators via ref.vec_sensitivity_indicator: exact
    (normalized) match first, then a high-confidence fuzzy fallback on the VEC name. Returns a
    list of ``{vec, indicators}`` for the VECs that resolved. Never raises."""
    if not vecs:
        return []
    idx, _ = _ref_vec_sensitivity_index(agent=agent)
    if not idx:
        return []
    out = []
    for v in vecs:
        entry = idx.get(v.lower())
        if entry is None:
            best, score = None, 0.0
            for e in idx.values():
                s = _match_score(v, e["vec"])
                if s > score:
                    best, score = e, s
            entry = best if score >= REF_FUZZY_ACCEPT else None
        if entry and entry["indicators"]:
            out.append({"vec": v, "indicators": entry["indicators"]})
    return out


def _ai_derive_impacts(activities: str, description: str, ptype: str, model):
    """AI fallback when the project's type/actions aren't in the reference library: derive the key
    ACTIONS and, for EACH action, its environmental/social IMPACT FACTORS — aligned to the project
    and its type. Returns ``[{action, factors}]``. Fails soft (re-raises only throttling)."""
    try:
        provider = build_provider(model)
        system = (
            "You are an environmental & social impact assessment (ESIA) expert. Given a project, its "
            "type and activities, list the main project ACTIONS and, for EACH action, the specific "
            "environmental/social IMPACT FACTORS it causes. Use standard EIA terminology, be concise, "
            "and stay consistent with the project type. Output ONLY JSON: "
            '{"actions": [{"action": "...", "impact_factors": ["...", "..."]}]}.'
        )
        prompt = (
            f"Project type: {ptype or '(unspecified)'}\n\n"
            f"Activities (extracted):\n{activities or '(none stated)'}\n\n"
            f"Description (context):\n{(description or '')[:1500]}\n\nJSON:"
        )
        data = extract_json(provider.complete(prompt, system=system))
        out = []
        for a in (data.get("actions") if isinstance(data, dict) else []) or []:
            if not isinstance(a, dict):
                continue
            name = str(a.get("action", "")).strip()
            facs, seen = [], set()
            for f in (a.get("impact_factors") or []):
                f = str(f).strip()
                if f and f.lower() not in seen:
                    seen.add(f.lower()); facs.append(f)
            if name and facs:
                out.append({"action": name, "factors": facs})
        return out
    except RateLimited:
        raise
    except Exception:
        return []


def _ai_derive_vecs(factors, ptype: str, model):
    """AI fallback: map each impact factor to the VEC(s) (valued environmental/social component) it
    acts on, for factors NOT found in the reference library. Returns ``{factor(lower): [vec,...]}``."""
    factors = [f for f in (factors or []) if str(f).strip()]
    if not factors:
        return {}
    try:
        provider = build_provider(model)
        listing = "\n".join(f"- {f}" for f in factors[:80])
        system = (
            "You are an ESIA expert. For each impact factor, name the VALUED ENVIRONMENTAL/SOCIAL "
            "COMPONENT(s) (VEC) it primarily acts on (e.g. Air Quality, Surface Water, Groundwater, "
            "Vegetation, Wildlife/Fauna, Soils, Landforms, Noise, Community/Socio-economic, Cultural "
            "Heritage). Keep VEC names standard and consistent. Output ONLY JSON: "
            '{"map": [{"impact_factor": "...", "vecs": ["...", "..."]}]}.'
        )
        prompt = f"Project type: {ptype or '(unspecified)'}\n\nImpact factors:\n{listing}\n\nJSON:"
        data = extract_json(provider.complete(prompt, system=system))
        out = {}
        for row in (data.get("map") if isinstance(data, dict) else []) or []:
            if not isinstance(row, dict):
                continue
            fac = str(row.get("impact_factor", "")).strip().lower()
            vs, seen = [], set()
            for v in (row.get("vecs") or []):
                v = str(v).strip()
                if v and v.lower() not in seen:
                    seen.add(v.lower()); vs.append(v)
            if fac and vs:
                out[fac] = vs
        return out
    except RateLimited:
        raise
    except Exception:
        return {}


def _ai_derive_sensitivity(vecs, ptype: str, model):
    """AI fallback: derive SENSITIVITY INDICATORS for each VEC NOT found in the reference library —
    the measurable attributes that determine the VEC's sensitivity. Returns ``[{vec, indicators}]``."""
    vecs = [v for v in (vecs or []) if str(v).strip()]
    if not vecs:
        return []
    try:
        provider = build_provider(model)
        listing = "\n".join(f"- {v}" for v in vecs[:40])
        system = (
            "You are an ESIA expert. For each VEC (valued environmental/social component), list its "
            "SENSITIVITY INDICATORS — the measurable attributes used to judge how sensitive it is "
            "(e.g. Groundwater: aquifer vulnerability, depth to water table; Vegetation: rare/at-risk "
            "communities, old-growth extent). Keep them specific and consistent with the project type. "
            'Output ONLY JSON: {"map": [{"vec": "...", "sensitivity_indicators": ["...", "..."]}]}.'
        )
        prompt = f"Project type: {ptype or '(unspecified)'}\n\nVECs:\n{listing}\n\nJSON:"
        data = extract_json(provider.complete(prompt, system=system))
        out = []
        for row in (data.get("map") if isinstance(data, dict) else []) or []:
            if not isinstance(row, dict):
                continue
            vec = str(row.get("vec", "")).strip()
            inds, seen = [], set()
            for i in (row.get("sensitivity_indicators") or []):
                i = str(i).strip()
                if i and i.lower() not in seen:
                    seen.add(i.lower()); inds.append(i)
            if vec and inds:
                out.append({"vec": vec, "indicators": inds})
        return out
    except RateLimited:
        raise
    except Exception:
        return []


def map_impact_factors(activities: str, description: str, canonical_type: str, model: str, agent=None):
    """Impact factors for the project's actions, then each factor -> VEC -> sensitivity indicators.
    LIBRARY-FIRST, AI-FALLBACK: reference values (ref.action_impact_factor -> ref.impacts ->
    ref.vec_sensitivity_indicator) are authoritative and stay linked; when the project TYPE, a
    FACTOR, or a VEC is not in the library, the model DERIVES that link intelligently and
    consistently with the project type (marked AI). Never raises. Returns a dict:
    ``matched, by_action ({action, factors, vecs}), factors, vecs, ai_vecs (set), sensitivity, note``.
    """
    res = {"matched": False, "by_action": [], "factors": [], "vecs": [], "ai_vecs": set(),
           "sensitivity": [], "note": ""}
    _base = current_label() or "imp"
    index, actions, note = _ref_impact_index(canonical_type, agent=agent)
    if actions:
        with usage_label(f"{_base}-act"):
            chosen = _match_project_actions(activities, description, actions, model)
    else:
        chosen = []

    ai_impacts = False
    if chosen:  # library path: standardized actions matched -> reference impact factors
        for a in chosen:
            entry = index.get(a.lower())
            res["by_action"].append({"action": a, "factors": entry["factors"] if entry else [],
                                     "vecs": list(entry["vecs"]) if entry else []})
        res["matched"] = True
        src = f"across {len(chosen)} matched action(s)"
    else:  # type/actions not in the library -> AI derives impacts from the project's activities
        with usage_label(f"{_base}-ai"):
            _derived = _ai_derive_impacts(activities, description, canonical_type, model)
        for it in _derived:
            res["by_action"].append({"action": it["action"], "factors": it["factors"], "vecs": []})
        ai_impacts = bool(res["by_action"])
        src = f"across {len(res['by_action'])} AI-derived action(s)" if ai_impacts else (note or "no actions")

    distinct, seen = [], set()
    for it in res["by_action"]:
        for f in it["factors"]:
            if f.lower() not in seen:
                seen.add(f.lower()); distinct.append(f)
    res["factors"] = distinct

    # factor -> VEC: library (ref.impacts) first, AI for the leftovers
    vidx, _ = _ref_impact_vec_index(agent=agent)

    def _db_vecs(fac):
        got = vidx.get(fac.lower())
        if got is None and vidx:
            best, sc = None, 0.0
            for k, vl in vidx.items():
                s = _match_score(fac, k)
                if s > sc:
                    best, sc = vl, s
            got = best if sc >= REF_FUZZY_ACCEPT else None
        return got or []

    unresolved = [f for f in distinct if not _db_vecs(f)]
    if unresolved:
        with usage_label(f"{_base}-vec"):
            ai_vec_map = _ai_derive_vecs(unresolved, canonical_type, model)
    else:
        ai_vec_map = {}
    ai_vec_used = bool(ai_vec_map)

    vecs, vseen, db_vec_set, ai_vec_set = [], set(), set(), set()
    for it in res["by_action"]:
        av, avseen = list(it["vecs"]), {v.lower() for v in it["vecs"]}
        for v in it["vecs"]:
            db_vec_set.add(v.lower())  # inline VECs from action_impact_factor are library-backed
        for f in it["factors"]:
            db = _db_vecs(f)
            if db:
                for v in db:
                    db_vec_set.add(v.lower())
                    if v.lower() not in avseen:
                        avseen.add(v.lower()); av.append(v)
            else:
                for v in ai_vec_map.get(f.lower(), []):
                    ai_vec_set.add(v.lower())
                    if v.lower() not in avseen:
                        avseen.add(v.lower()); av.append(v)
        it["vecs"] = av
        for v in av:
            if v.lower() not in vseen:
                vseen.add(v.lower()); vecs.append(v)
    res["vecs"] = vecs
    res["ai_vecs"] = ai_vec_set - db_vec_set  # VECs produced only by AI (never library-backed)

    # VEC -> sensitivity: library first, AI for the leftovers
    db_sens = _sensitivity_for_vecs(vecs, agent=agent)
    resolved = {s["vec"].lower() for s in db_sens}
    leftover = [v for v in vecs if v.lower() not in resolved]
    if leftover:
        with usage_label(f"{_base}-sens"):
            ai_sens = _ai_derive_sensitivity(leftover, canonical_type, model)
    else:
        ai_sens = []
    for s in ai_sens:
        s["ai"] = True
    ai_sens_used = bool(ai_sens)
    res["sensitivity"] = db_sens + ai_sens

    tags = [t for t, on in (("impacts", ai_impacts), ("VECs", ai_vec_used), ("sensitivity", ai_sens_used)) if on]
    tag = f"; AI-derived: {', '.join(tags)}" if tags else ""
    nvec = f", {len(vecs)} VEC(s)" if vecs else ""
    nsens = f", {sum(len(s['indicators']) for s in res['sensitivity'])} sensitivity indicator(s)" if res["sensitivity"] else ""
    res["note"] = f"{len(distinct)} impact factor(s){nvec}{nsens} {src}{tag}"
    return res


MAX_BREAKDOWN_PROJECTS = 6  # cap per-project enrichment (each runs live DB + model calls)
_PROMINENCE = {"high": 0, "medium": 1, "low": 2}


def select_breakdown_projects(projects, dominant, primary_name="", max_n=MAX_BREAKDOWN_PROJECTS):
    """Order the identified projects for a project-by-project breakdown: the single dominant/
    primary development first, then the rest by prominence, then document order. Umbrella
    'integrated/mixed' entries are skipped (their constituents are enriched instead).
    De-duplicated by name, capped at ``max_n``."""
    concrete = []
    dom, pn = _norm(dominant), _norm(primary_name)
    for p in (projects or []):
        t = str(p.get("type", "")).lower()
        if any(w in t for w in ("integrated", "mixed", "multi-component", "multi component", "combined")):
            continue
        # The identified PRIMARY is the overall/umbrella project. When its own type differs from the
        # dominant sub-type, it's a container (e.g. "renewable energy and industrial development") —
        # its constituents are the breakdown units, so drop the umbrella itself.
        if pn and _norm(p.get("name")) == pn and _norm(p.get("dominant_type") or p.get("type")) != dom:
            continue
        if str(p.get("name", "")).strip():
            concrete.append(p)

    def primary_score(p):
        t = _norm(p.get("dominant_type") or p.get("type"))
        score = 0.0
        if pn and pn == _norm(p.get("name")):
            score += 2.0
        if dom and t:
            score += 2.0 if dom == t else (1.0 if (dom in t or t in dom) else 0.0)
        return score

    # pick the SINGLE best primary (ties -> higher prominence -> earlier in the document)
    best_idx = None
    if concrete:
        best_idx = max(
            range(len(concrete)),
            key=lambda i: (primary_score(concrete[i]),
                           -_PROMINENCE.get(str(concrete[i].get("prominence", "")).strip().lower(), 3),
                           -i),
        )

    def rank(ip):
        idx, p = ip
        is_primary = 0 if idx == best_idx else 1
        prom = _PROMINENCE.get(str(p.get("prominence", "")).strip().lower(), 3)
        return (is_primary, prom, idx)

    ranked = [p for _, p in sorted(enumerate(concrete), key=rank)]
    seen, out = set(), []
    for p in ranked:
        k = _norm(p.get("name"))
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out[:max_n]


def _extract_project_fields(text: str, project, model):
    """Extract fields SCOPED to ONE sub-project (name, type, description, size, location,
    components, activities) so each breakdown entry is genuinely project-specific rather than
    document-wide. Fails soft to ``{}`` (the caller then falls back to the identified hints).
    Re-raises throttling so the adaptive fleet can react."""
    name = str(project.get("name") or "").strip()
    hint = str(project.get("dominant_type") or project.get("type") or "").strip()
    keys = ("name", "type", "sector", "proponent", "description", "size", "location",
            "components", "activities", "regulatory_context", "process_type", "stage")
    try:
        provider = build_provider(model)
        system = (
            "You extract fields for ONE specific sub-project within a larger project document. "
            "Base everything ONLY on the document; use an empty string if not stated. Do NOT attribute "
            "other sub-projects' details to this one. Output ONLY a JSON object."
        )
        prompt = (
            "Focus on THIS sub-project only (the other sub-projects are context, not the subject):\n"
            f"  name: {name or '(unnamed)'}\n  type: {hint or '(unknown)'}\n\n"
            "Extract, ABOUT THIS SUB-PROJECT: a clear name; its type; its sector; its proponent/"
            "developer; a detailed description (its own scale, capacity, components, location, phases); "
            "its size/capacity (with figures); its location; its physical components; its "
            "activities/lifecycle; the regulatory context that applies to it; the assessment process "
            "type; and its stage of process.\n"
            'Return JSON: {"name":"","type":"","sector":"","proponent":"","description":"","size":"",'
            '"location":"","components":"","activities":"","regulatory_context":"","process_type":"","stage":""}\n\n'
            f"DOCUMENT:\n{text[:12000]}\n\nJSON:"
        )
        data = extract_json(provider.complete(prompt, system=system))
        if not isinstance(data, dict):
            return {}
        return {k: str(data.get(k, "")).strip() for k in keys}
    except RateLimited:
        raise
    except Exception:
        return {}


def enrich_project(proj, text: str, model, agent=None):
    """Build ONE project's SELF-CONTAINED view: its own fields (name, description, size, location,
    activities) scoped to this project, its standardized type + area of influence, its components
    (ref + AI scoped to THIS project — not the whole document), and its impact factors (from its
    OWN activities). Governed and semantic; never raises except on throttling (the adaptive fleet
    handles that)."""
    pname = (str(proj.get("name") or "project"))[:14]
    with usage_label(f"{pname}\u00b7fields"):
        pf = _extract_project_fields(text, proj, model)
    raw_type = (pf.get("type") or proj.get("dominant_type") or proj.get("type") or "").strip()
    name = pf.get("name") or str(proj.get("name") or "(unnamed)")
    desc = pf.get("description", "")
    acts = pf.get("activities", "")
    comps_txt = pf.get("components", "")
    ctx = " | ".join(x for x in (name, raw_type, desc[:300]) if x)
    with usage_label(f"{pname}\u00b7type"):
        mapping = map_project_type(raw_type, model, context=ctx, agent=agent)
    ptype = mapping["canonical"] if mapping.get("matched") else raw_type
    # scope the AI-component search + impact actions to THIS project's own text, so a wind farm
    # doesn't get the hydrogen plant's components tagged onto it.
    scope = " ".join(x for x in (desc, comps_txt) if x).strip() or text
    with usage_label(f"{pname}\u00b7comps"):
        components = map_project_components(ptype, scope, model, agent=agent)
    with usage_label(f"{pname}\u00b7imp"):
        impacts = map_impact_factors(acts or comps_txt, desc, ptype, model, agent=agent)
    return {
        "name": name,
        "role": str(proj.get("role") or ""),
        "prominence": str(proj.get("prominence") or ""),
        "type": ptype,
        "type_matched": bool(mapping.get("matched")),
        "buffer": mapping.get("buffer"),
        "sector": pf.get("sector", ""),
        "proponent": pf.get("proponent", ""),
        "description": desc,
        "size": pf.get("size", ""),
        "location": pf.get("location", ""),
        "activities": acts,
        "regulatory_context": pf.get("regulatory_context", ""),
        "process_type": pf.get("process_type", ""),
        "stage": pf.get("stage", ""),
        "components": components,
        "impacts": impacts,
    }


def _collapse_by_type(enriched):
    """Collapse enriched items into DISTINCT standardized-type groups — the true screening unit.
    An item that mapped to a real ``wsp_project_type`` starts (or joins) a group keyed by that
    type: several wind areas -> one **Wind Farm**, the two transmission lines -> one
    **Transmission Line**, with the dominant item leading and the others' components + impact
    factors unioned in. Items that did NOT map to a standardized type are enabling works /
    components, not peer projects, so they drop out of the breakdown. If nothing mapped (e.g. no
    reference DB), falls back to a light dedupe by whatever type label each item carries.
    """
    groups, order = {}, []
    for ep in enriched:
        if not ep.get("type_matched"):
            continue
        key = _norm(ep.get("type"))
        if not key:
            continue
        if key not in groups:
            groups[key] = ep
            order.append(key)
            continue
        g = groups[key]
        gc, ec = g["components"], ep["components"]
        seen = {_norm(c) for c in gc["reference"]}
        for c in ec["reference"]:
            if _norm(c) not in seen:
                seen.add(_norm(c)); gc["reference"].append(c)
        seen_ai = {_norm(c) for c in gc["ai_additional"]} | seen
        for c in ec["ai_additional"]:
            if _norm(c) not in seen_ai:
                seen_ai.add(_norm(c)); gc["ai_additional"].append(c)
        gc["matched"] = gc["matched"] or ec["matched"]
        gi, ei = g["impacts"], ep["impacts"]
        by_action = {_norm(it["action"]): it for it in gi["by_action"]}
        for it in ei["by_action"]:
            k = _norm(it["action"])
            if k not in by_action:
                gi["by_action"].append(it)
                by_action[k] = it
            else:
                tgt = by_action[k]
                seenf = {_norm(f) for f in tgt["factors"]}
                for f in it["factors"]:
                    if _norm(f) not in seenf:
                        seenf.add(_norm(f)); tgt["factors"].append(f)
                tgt.setdefault("vecs", [])
                seenv = {_norm(v) for v in tgt["vecs"]}
                for v in it.get("vecs", []):
                    if _norm(v) not in seenv:
                        seenv.add(_norm(v)); tgt["vecs"].append(v)
        gv = {_norm(v) for v in gi.get("vecs", [])}
        for v in ei.get("vecs", []):
            if _norm(v) not in gv:
                gv.add(_norm(v)); gi.setdefault("vecs", []).append(v)
        gi["ai_vecs"] = set(gi.get("ai_vecs", set())) | set(ei.get("ai_vecs", set()))
        gs = {_norm(s["vec"]): s for s in gi.get("sensitivity", [])}
        for s in ei.get("sensitivity", []):
            sk = _norm(s["vec"])
            if sk not in gs:
                gi.setdefault("sensitivity", []).append(s); gs[sk] = s
            else:
                tgt = gs[sk]
                seeni = {_norm(x) for x in tgt["indicators"]}
                for x in s["indicators"]:
                    if _norm(x) not in seeni:
                        seeni.add(_norm(x)); tgt["indicators"].append(x)
        gi["matched"] = gi["matched"] or ei["matched"]
    if order:
        return [groups[k] for k in order]
    seen_t, out = set(), []
    for ep in enriched:
        key = _norm(ep.get("type"))
        if key and key not in seen_t:
            seen_t.add(key)
            out.append(ep)
    return out or list(enriched)


def enrich_breakdown(projects, text: str, model):
    """Enrich every project (type -> components -> impact factors) as GOVERNED, adaptively-
    parallel children — the framework answer to "spawn sub-agents as the data demands, with
    self-tuning concurrency and no rate-limit errors."

    A master agent spawns one **attenuated, isolated child per project** (its own read-only
    Postgres connection + ``db.query`` grant + a distinct signed sub-chain, sharing the master's
    budget). The framework's :class:`~autarch.AdaptiveExecutor` runs them with concurrency that
    **widens while calls succeed and narrows the instant Azure throttles** (honoring Retry-After),
    so there is no fixed parallelism to guess and no 429 storm — and every project is retried
    through throttling until it completes. All model calls share ONE rate-limit-aware provider so
    pacing is central. Degrades to sequential enrichment if orchestration cannot start.

    Generic pattern: any agent can do the same with ``master.spawn`` children + ``AdaptiveExecutor``.
    """
    projects = list(projects)
    if not projects:
        return []
    # One shared, rate-limit-aware provider. Brief throttles are absorbed here (a couple of waits
    # honoring Retry-After); sustained throttling surfaces to the executor, which narrows the fleet.
    try:
        shared = make_resilient(
            build_provider(model, resilient=False),
            retry=RetryPolicy(max_throttle_waits=2),
            rate=RateLimit(adapt=True),
        )
    except Exception:
        shared = model

    def _sequential():
        return [enrich_project(p, text, shared) for p in projects]

    dsn = os.environ.get("DATABASE_URL")
    ref_tables = ["project_type", REF_COMPONENT_TABLE, REF_IMPACT_TABLE, REF_IMPACT_VEC_TABLE, REF_VEC_TABLE]
    ws = tempfile.mkdtemp(prefix="autarch_master_")
    try:
        adapters, grants = None, []
        if dsn:
            try:
                adapters = [connect_postgres(dsn, read_only=True, allow_tables=ref_tables + ["tables", "columns"], max_rows=20000, autocommit=True)]
                grants = [capability("db.query")]
            except Exception:
                adapters, grants = None, []
        master = Agent(
            intent="orchestrate governed, adaptively-parallel per-project enrichment",
            adapters=adapters, grants=grants, workspace=ws,
        )
        # Pre-pull the reference tables ONCE under the master (main thread), filling the per-run
        # cache, so the parallel children read them from cache and never write the signed SQLite
        # ledger from a worker thread (SQLite connections are thread-affine — check_same_thread).
        # Also avoids re-querying the same table per child.
        if grants:
            for _t in ref_tables:
                try:
                    _fetch_ref_rows(_t, agent=master)
                except Exception:
                    pass  # a cold child can still pull it on demand

        def _spawn_child(proj, i):
            """A governed child. When a reference DB is available it gets its OWN connection and an
            isolated signed sub-chain (distinct node id + ledger handle) so parallel db.query and
            provenance writes are thread-safe — the same isolation the Orchestrator uses."""
            kw = {"intent": f"enrich project: {proj.get('name', '?')}", "grants": list(grants)}
            if dsn and grants:
                origin = f"{master.node_id}:proj{i}"
                kw["adapters"] = [connect_postgres(dsn, read_only=True, allow_tables=ref_tables + ["tables", "columns"], max_rows=20000, autocommit=True)]
                kw["node_id"] = origin
                # same_thread=False: the signed ledger may be touched from a worker thread; SQLite's
                # own serialized mode keeps writes safe. Belt-and-suspenders with the pre-warm above.
                kw["memory"] = WhyMemory(master.memory.db_path, node_id=origin, identity=master.identity, same_thread=False)
                kw["precedents"] = PrecedentStore(master.precedents.db_path)
            return master.spawn(**kw)

        def make_task(proj, i):
            def task():
                # Build the child INSIDE the worker run so its (thread-affine) ledger connection is
                # created and used on the same thread; a throttle-retry simply builds a fresh child.
                child = None
                try:
                    child = _spawn_child(proj, i)
                except Exception:
                    child = None
                return enrich_project(proj, text, shared, agent=child)
            return task

        outcomes = AdaptiveExecutor(start=2, max_throttle_waits=20).run(
            [make_task(p, i) for i, p in enumerate(projects)]
        )
        enriched = [o.value for o in outcomes if o.ok and o.value is not None] or _sequential()
    except Exception:
        try:
            enriched = _sequential()  # never fail the pipeline over orchestration
        except Exception:
            enriched = []
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    # Collapse to DISTINCT standardized types: several wind areas -> one Wind Farm, the two
    # transmission lines -> one Transmission Line; enabling works that don't map to a standardized
    # project type fold in as components rather than standing as peer "projects".
    return _collapse_by_type(enriched)


# --------------------------------------------------------------------------- #
# Optional shapefiles -> authoritative per-project geometry.
#   Tier 1 (pyshp): read geometry -> accurate location (centroid + bbox, WGS84).
#   Tier 2 (shapely + pyproj): buffer by the ref.project_type distance -> a real
#   area-of-influence polygon + area (km2). All optional; graceful if libs/files absent.
# --------------------------------------------------------------------------- #
def _iter_shp_files(spec):
    """Yield .shp paths from a spec: a directory, a single .shp, or a comma-separated list."""
    files = []
    for part in str(spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        p = Path(part).expanduser()
        if p.is_dir():
            files.extend(sorted(p.glob("*.shp")))
        elif p.suffix.lower() == ".shp" and p.exists():
            files.append(p)
    return files


def _prj_crs(shp_path):
    """The sibling .prj (WKT) as a pyproj CRS, or None (assume WGS84)."""
    prj = shp_path.with_suffix(".prj")
    if not prj.exists():
        return None
    try:
        import pyproj
        return pyproj.CRS.from_wkt(prj.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None


def _load_shape_features(spec):
    """Read shapefiles (pyshp) -> ``[{label, geom (shapely, WGS84), source, crs}]``, reprojecting via
    each .prj. Fails soft; returns ``([], note)`` if pyshp/shapely are unavailable."""
    try:
        import shapefile  # pyshp
        from shapely.geometry import shape as _to_shapely
    except Exception:
        return [], "shapefile support needs: pip install pyshp shapely pyproj"
    features = []
    for shp in _iter_shp_files(spec):
        try:
            reader = shapefile.Reader(str(shp))
        except Exception:
            continue
        crs = _prj_crs(shp)
        to_wgs = None
        try:
            import pyproj
            if crs is not None and crs.to_epsg() != 4326:
                to_wgs = pyproj.Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
        except Exception:
            to_wgs = None
        fields = [f[0].lower() for f in reader.fields[1:]]  # skip the DeletionFlag pseudo-field
        name_i = next((i for i, f in enumerate(fields) if f in SHAPE_NAME_FIELDS), None)
        if name_i is None:
            name_i = next((i for i, f in enumerate(fields) if any(k in f for k in ("name", "proj", "site", "label"))), None)
        for sr in reader.iterShapeRecords():
            try:
                geom = _to_shapely(sr.shape.__geo_interface__)
            except Exception:
                continue
            if geom.is_empty:
                continue
            if to_wgs is not None:
                try:
                    from shapely.ops import transform
                    geom = transform(to_wgs, geom)
                except Exception:
                    pass
            label = ""
            if name_i is not None:
                try:
                    label = str(sr.record[name_i]).strip()
                except Exception:
                    label = ""
            features.append({
                "label": label or shp.stem, "geom": geom, "source": shp.name,
                "crs": (crs.name if crs is not None else "assumed WGS84"),
            })
    srcs = len({f["source"] for f in features})
    note = f"{len(features)} feature(s) from {srcs} shapefile(s)" if features else "no shapefile features loaded"
    return features, note


def governed_read_shapes(spec):
    """Prove the shapefile reader is read-only (attested + audited), then read geometry (Tier 1).
    Returns ``(features, note)``. Shapefiles are binary, so the proven read-only agent attests the
    intent while pyshp does the parse."""
    files = _iter_shp_files(spec)
    if not files:
        return [], "no .shp files found"
    ws = tempfile.mkdtemp(prefix="autarch_shapes_")
    try:
        agent = Agent(
            intent="read project shapefiles (geometry, read-only)",
            adapters=[FileSystemAdapter(root=str(files[0].parent))],
            grants=[capability("file.read", scope={"path_prefix": "."})],
            workspace=ws,
        )
        report = agent.guarantee([Invariant.forbid("file.write"), Invariant.forbid("file.delete")])
        print(f"  guarantee — shapefile reader can never write or delete: {report.all_hold}")
    except Exception:
        pass
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    return _load_shape_features(spec)


def _geom_location(geom):
    """A human-readable, authoritative location string from geometry (WGS84)."""
    c = geom.centroid
    minx, miny, maxx, maxy = geom.bounds
    return f"centroid {c.y:.6f}, {c.x:.6f}; bbox [{miny:.5f}, {minx:.5f}] .. [{maxy:.5f}, {maxx:.5f}] (WGS84)"


def _buffer_km_value(buffer_str):
    m = re.search(r"[-+]?\d*\.?\d+", str(buffer_str or ""))
    return float(m.group()) if m else None


def _buffer_area_km2(geom, buffer_km):
    """``(buffered_area_km2, footprint_km2)`` — buffer a WGS84 geom by ``buffer_km`` via a local
    azimuthal-equidistant projection centered on the geometry (accurate for local buffers)."""
    try:
        import pyproj
        from shapely.ops import transform
        c = geom.centroid
        aeqd = pyproj.CRS.from_proj4(f"+proj=aeqd +lat_0={c.y} +lon_0={c.x} +datum=WGS84 +units=m +no_defs")
        fwd = pyproj.Transformer.from_crs("EPSG:4326", aeqd, always_xy=True).transform
        gm = transform(fwd, geom)
        buffered = gm.buffer(buffer_km * 1000.0) if (buffer_km and buffer_km > 0) else gm
        return buffered.area / 1e6, gm.area / 1e6
    except Exception:
        return None, None


def _assign_shapes_to_groups(features, groups):
    """Bind each shape feature to its single best-matching project group (fuzzy label vs the group's
    name or standardized type). Returns ``{group_index: [features]}``."""
    assign = {}
    for f in features:
        best_i, best_s = None, 0.0
        for i, g in enumerate(groups):
            s = max(_match_score(f["label"], g.get("name", "")), _match_score(f["label"], g.get("type", "")))
            if s > best_s:
                best_s, best_i = s, i
        if best_i is not None and best_s >= SHAPE_MATCH_MIN:
            assign.setdefault(best_i, []).append(f)
    return assign


def apply_shapes_to_projects(features, enriched):
    """Override each matched project's location + area of influence with AUTHORITATIVE geometry:
    union the assigned shape features (Tier 1 location), then buffer by the ref buffer distance
    (Tier 2 area). Mutates ``enriched`` in place; returns how many projects were updated."""
    if not features or not enriched:
        return 0
    try:
        from shapely.ops import unary_union
    except Exception:
        return 0
    updated = 0
    for gi, feats in _assign_shapes_to_groups(features, enriched).items():
        ep = enriched[gi]
        try:
            geom = unary_union([f["geom"] for f in feats])
        except Exception:
            continue
        if geom.is_empty:
            continue
        srcs = ", ".join(sorted({f["source"] for f in feats}))
        ep["location"] = f"{_geom_location(geom)}  [shapefile(s): {srcs}]"
        km = _buffer_km_value(ep.get("buffer"))
        b_km2, fp_km2 = _buffer_area_km2(geom, km) if km is not None else (None, None)
        if b_km2 is not None:
            fp = f"; footprint ~{fp_km2:.2f} km\u00b2" if (fp_km2 and fp_km2 > 0) else ""
            ep["area_of_influence"] = f"{km:g} km buffer \u2192 ~{b_km2:.1f} km\u00b2{fp} (from shapefile geometry)"
        ep["geo_source"] = srcs
        updated += 1
    return updated


def _kv_table(title_lines, rows, key_w: int = 0) -> str:
    """Render a bordered two-column (field | value) table so each project reads as a tidy card.

    ``title_lines`` fills a merged header band; ``rows`` is ``(field, value)`` where value is a
    ``str`` or ``list[str]`` (list items \u2014 e.g. component/impact bullets \u2014 each wrap on
    their own line with a hanging indent). Long values wrap inside the value column so columns stay
    aligned; the value width adapts to the terminal.
    """
    if key_w <= 0:  # size the field column to the longest label so nothing overflows/misaligns
        key_w = max(18, min(26, max((len(str(f)) for f, _ in rows), default=18)))
    term_w = shutil.get_terminal_size((120, 40)).columns
    val_w = max(40, min(100, term_w - key_w - 10))
    inner = key_w + val_w + 5
    H, V = "\u2500", "\u2502"

    def wrap_val(value):
        lines = []
        for it in (value if isinstance(value, list) else [value]):
            it = "" if it is None else str(it)
            stripped = it.lstrip()
            if not stripped:
                lines.append("")
                continue
            pad = it[: len(it) - len(stripped)]
            sub = pad + ("  " if stripped.startswith("- ") else "")
            lines.extend(textwrap.wrap(stripped, val_w, initial_indent=pad, subsequent_indent=sub) or [""])
        return lines or [""]

    out = ["  \u250c" + H * inner + "\u2510"]
    for t in title_lines:
        for ln in (textwrap.wrap(t, inner - 2) or [""]):
            out.append(f"  {V} " + ln.ljust(inner - 2) + f" {V}")
    out.append("  \u251c" + H * (key_w + 2) + "\u252c" + H * (val_w + 2) + "\u2524")
    mid = "  \u251c" + H * (key_w + 2) + "\u253c" + H * (val_w + 2) + "\u2524"
    for ri, (field, value) in enumerate(rows):
        for li, vl in enumerate(wrap_val(value)):
            k = _t(str(field)) if li == 0 else ""
            out.append(f"  {V} " + k[:key_w].ljust(key_w) + f" {V} " + vl[:val_w].ljust(val_w) + f" {V}")
        if ri != len(rows) - 1:
            out.append(mid)
    out.append("  \u2514" + H * (key_w + 2) + "\u2534" + H * (val_w + 2) + "\u2518")
    return "\n".join(out)


_REPORT_CSS = "body{font-family:system-ui,Arial,sans-serif;color:#1c2733;margin:0;padding:28px;background:#f5f7fa;line-height:1.45}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 10px;border-bottom:2px solid #dbe2ea;padding-bottom:4px}h3{font-size:15px;margin:0 0 8px;color:#0b5cad}.meta{color:#5b6b7b;font-size:13px;margin:2px 0}.sub{color:#5b6b7b;font-size:12px;font-weight:normal}.card{background:#fff;border:1px solid #dbe2ea;border-radius:8px;padding:14px 18px;margin:0 0 16px}table.kv{width:100%;border-collapse:collapse}table.kv th{text-align:left;vertical-align:top;width:210px;font-weight:600;color:#33445a;padding:7px 12px 7px 0;border-bottom:1px solid #eef2f6}table.kv td{vertical-align:top;padding:7px 0;border-bottom:1px solid #eef2f6}.b{padding-left:14px;text-indent:-14px}.note{color:#5b6b7b;font-size:12px;margin-bottom:2px}table.scores{width:100%;border-collapse:collapse;background:#fff;border:1px solid #dbe2ea;border-radius:8px;overflow:hidden}table.scores th{text-align:left;background:#eef2f6;padding:8px 12px;font-size:13px}table.scores td{padding:8px 12px;border-top:1px solid #eef2f6;vertical-align:top}.pass{color:#1a7f37;font-weight:600}.fail{color:#c1341d;font-weight:600}code{background:#eef2f6;padding:1px 5px;border-radius:4px;font-size:12px}a.cite{color:#0b5cad;text-decoration:none;border-bottom:1px dotted #7aa7d4;white-space:nowrap}a.cite:hover{text-decoration:underline}.srcdoc{margin:8px 0 24px;background:#fff;border:1px solid #dbe2ea;border-radius:8px;padding:8px 16px}.srcdoc summary{cursor:pointer;color:#0b5cad;font-weight:600}.srcdoc p{margin:6px 0;padding:5px 8px;border-radius:4px;font-size:13px}.srcdoc p:target{background:#fff3cd;outline:2px solid #ffd666}.srcdoc .pno{color:#8894a5;font-size:11px;margin-right:6px}.srcdoc .pageno{color:#c1341d;font-size:11px;margin-right:6px}"


def _html_escape(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _soft(s, width=110):
    """Insert soft line breaks so the HTML source has no huge lines (some editors/viewers truncate
    long lines). Browsers collapse these newlines to spaces, so the rendered page is unaffected."""
    if len(s) <= width:
        return s
    return "\n".join(textwrap.wrap(s, width, break_long_words=True, break_on_hyphens=False)) or s


def _html_cell(value) -> str:
    """Render a field value (str or list[str]) as HTML; list items become lines, '- ' -> bullets."""
    items = value if isinstance(value, list) else [value]
    parts = []
    for it in items:
        it = "" if it is None else str(it)
        stripped = it.lstrip()
        if stripped.startswith("- "):
            parts.append(f"<div class='b'>&#8226; {_soft(_html_escape(stripped[2:]))}</div>")
        elif stripped.startswith("\u21b3"):
            esc = _html_escape(it)
            if _DOC_URL:
                m = re.search(r"p\.(\d+)", esc)
                href = _doc_href(m.group(1) if m else None)
                esc = f"<a class='cite' href='{href}'>{esc.strip()} \u2197</a>"
            parts.append(f"<div class='note'>{esc}</div>")
        elif stripped.startswith("["):
            parts.append(f"<div class='note'>{_soft(_html_escape(it))}</div>")
        else:
            parts.append(f"<div>{_soft(_html_escape(it))}</div>")
    return "\n".join(parts) or "&nbsp;"


def _project_card_html(title_lines, rows) -> str:
    head = _html_escape(title_lines[0]) if title_lines else ""
    sub = "\n".join(f"<div class='sub'>{_html_escape(t)}</div>" for t in title_lines[1:])
    trs = "\n".join(f"<tr><th>{_html_escape(_t(f))}</th><td dir='auto'>{_html_cell(v)}</td></tr>" for f, v in rows)
    return f"<section class='card'>\n<h3>{head}</h3>\n{sub}\n<table class='kv'>\n{trs}\n</table>\n</section>"


def _scores_html(panel) -> str:
    trs = []
    for name, score, passed, reason in panel.rows():
        badge = f"<span class='pass'>{_t('PASS')}</span>" if passed else f"<span class='fail'>{_t('FAIL')}</span>"
        trs.append(f"<tr><th>{_html_escape(_t(name))}</th><td>{score:.2f}</td><td>{badge}</td><td>{_html_escape(reason)}</td></tr>")
    skipped = getattr(panel, "skipped", None)
    tail = f"<div class='sub'>{_t('skipped')}: {_html_escape(', '.join(skipped))}</div>" if skipped else ""
    header = f"<tr><th>{_t('dimension')}</th><th>{_t('score')}</th><th>{_t('result')}</th><th>{_t('reason')}</th></tr>"
    return f"<table class='scores'>\n{header}\n" + "\n".join(trs) + f"\n</table>{tail}"


def _verdicts_html(rows) -> str:
    """Render the per-field verdict rows as an HTML table (green ok / red fail, red 'NO' grounding)."""
    trs = [f"<tr><th>{_t('field')}</th><th>{_t('present')}</th><th>{_t('grounded')}</th><th>{_t('judge')}</th><th>{_t('reason')}</th></tr>"]
    for field, present, grounded, judge, reason in rows:
        j = str(judge).lower()
        jcls = "pass" if j == "ok" else ("fail" if j == "fail" else "")
        jcell = f"<span class='{jcls}'>{_html_escape(judge)}</span>" if jcls else _html_escape(judge)
        gcls = "fail" if str(grounded).lower() == "no" else ""
        gcell = f"<span class='{gcls}'>{_html_escape(grounded)}</span>" if gcls else _html_escape(grounded)
        trs.append(
            f"<tr><th>{_html_escape(field)}</th><td>{_html_escape(present)}</td>"
            f"<td>{gcell}</td><td>{jcell}</td><td>{_soft(_html_escape(reason))}</td></tr>"
        )
    return "<table class='scores'>\n" + "\n".join(trs) + "\n</table>"


def _citations_html(citations) -> str:
    """Per-field grounding citations as an HTML table: field | supporting passage | where (a link to
    the source paragraph anchor)."""
    rows = [f"<tr><th>{_t('field')}</th><th>{_t('supporting source passage')}</th><th>{_t('where')}</th></tr>"]
    for field, c in citations.items():
        page = c.get("page")
        if _DOC_URL:
            href = _doc_href(page)
            label = (f"p.{page}" if page else _t("source")) + " \u2197"
            where = f"<a class='cite' href='{href}'>{_html_escape(label)}</a> <span class='sub'>{c['method']} {c['score']:.2f}</span>"
        else:
            where = f"<span class='sub'>{c['method']} {c['score']:.2f}</span>"
        rows.append(
            f"<tr><th>{_html_escape(field)}</th><td dir='auto'>{_soft(_html_escape(c['text']))}</td>"
            f"<td>{where}</td></tr>"
        )
    return "<table class='scores'>\n" + "\n".join(rows) + "\n</table>"


def render_report_html(ctx) -> str:
    """Build a self-contained HTML report (projects, per-project detail, grounding, quality,
    safety) for easy viewing/downloading. ``ctx`` is a plain dict of the already-computed data."""
    import datetime
    esc = _html_escape
    projs = ctx.get("projects") or []
    _dn = esc(ctx.get('doc_name') or '')
    _du = esc(ctx.get('doc_url') or '')
    doc_link = f"<a class='cite' href='{_du}'><b>{_dn}</b> \u2197</a>" if _du else f"<b>{_dn}</b>"
    parts = [
        "<!doctype html><html lang='" + esc(ctx.get('lang') or 'en') + "' dir='auto'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Extraction report \u2014 {esc(ctx.get('doc_name') or '')}</title>",
        "<style>" + _REPORT_CSS + "</style></head><body>",
        "<h1>Project extraction report</h1>",
        f"<p class='meta'>Document: {doc_link} &middot; model: {esc(ctx.get('model') or '')} &middot; {ctx.get('chars', 0):,} chars &middot; generated {datetime.datetime.now():%Y-%m-%d %H:%M}</p>",
        f"<p class='meta'>Signed why-record: <code>{esc(ctx.get('why_id') or '')}</code> &middot; provenance verifies: {esc(ctx.get('provenance_ok'))}</p>",
    ]
    if ctx.get("shapes_note"):
        parts.append(f"<p><b>Shapefiles:</b> {esc(ctx['shapes_note'])}</p>")
    parts.append(f"<h2>Projects identified ({len(projs)})</h2>")
    parts.append(f"<table class='scores'><tr><th>{_t('name')}</th><th>{_t('type')}</th><th>{_t('role')}</th><th>{_t('prominence')}</th></tr>")
    for p in projs:
        parts.append(f"<tr><td dir='auto'>{esc(p.get('name') or '?')}</td><td dir='auto'>{esc(p.get('type') or '?')}</td><td>{esc(p.get('role') or '?')}</td><td>{esc(p.get('prominence') or '?')}</td></tr>")
    parts.append("</table>")
    overall = ctx.get("overall") or {}
    if overall:
        parts.append(f"<p><b>Overall project:</b> {esc(overall.get('name') or '?')} [{esc(overall.get('type') or '?')}]<br><b>Dominant sub-type:</b> {esc(ctx.get('dominant') or '?')}")
        if overall.get("why"):
            parts.append(f"<br><b>Why:</b> {esc(overall.get('why'))}")
        parts.append("</p>")
    parts.append("<h2>Project-by-project detail</h2>")
    cards = ctx.get("cards") or []
    if cards:
        parts.extend(_project_card_html(t, r) for t, r in cards)
    else:
        parts.append("<p>(no distinct projects)</p>")
    parts.append("<h2>Anti-hallucination</h2>")
    flagged = ctx.get("flagged") or []
    if flagged:
        parts.append(f"<p class='fail'>{len(flagged)} value(s) NOT grounded in the source:</p><ul>")
        for k, v, why in flagged:
            parts.append(f"<li>{esc(k)}: {esc(repr(v))} <span class='sub'>({esc(why)})</span></li>")
        parts.append("</ul>")
    else:
        parts.append("<p class='pass'>Every extracted value is grounded in the source.</p>")
    verdicts = ctx.get("verdicts") or []
    if verdicts:
        parts.append("<h2>Field-by-field verdict <span class='sub'>per-field judge status</span></h2>")
        parts.append(_verdicts_html(verdicts))
    citations = ctx.get("citations") or {}
    if citations:
        parts.append("<h2>Source citations <span class='sub'>supporting passage per field</span></h2>")
        parts.append(_citations_html(citations))
    quality = ctx.get("quality")
    if quality is not None:
        parts.append(f"<h2>Quality <span class='sub'>mean {quality.score:.2f} \u2014 {'PASS' if quality.passed else 'FAIL'}</span></h2>")
        parts.append(_scores_html(quality))
    safety = ctx.get("safety")
    if safety is not None:
        parts.append(f"<h2>Safety <span class='sub'>{'PASS' if safety.passed else 'FAIL'}</span></h2>")
        parts.append(_scores_html(safety))
    usage = ctx.get("usage") or {}
    if usage.get("rows"):
        parts.append("<h2>Token usage &amp; cost <span class='sub'>per model \u2014 cost estimated from list prices</span></h2>")
        parts.append(_usage_html(usage["rows"], usage["total"]))
        if usage.get("calls"):
            parts.append("<h3>Per LLM call</h3>")
            parts.append(_usage_calls_html(usage["calls"]))
    parts.append("</body></html>")
    return "\n".join(parts)


def debug_reference(model: str) -> int:
    """Diagnostic: dump ref.project_type's real columns + the detector's choices so
    the canonical/buffer detection can be verified against the LIVE schema. Read-only,
    governed, table-scoped. Prints per-column distinct counts and a few sample values.
    Run: python examples/extract.py --debug-ref
    """
    banner("DEBUG — ref.project_type schema & detection")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("  DATABASE_URL not set")
        return 2
    ws = tempfile.mkdtemp(prefix="autarch_refdbg_")
    try:
        adapter = connect_postgres(dsn, read_only=True, allow_tables=["project_type"], max_rows=5000, autocommit=True)
        agent = Agent(
            intent="inspect ref.project_type schema",
            adapters=[adapter],
            grants=[capability("db.query")],
            workspace=ws,
        )
        result = agent.enact("db.query", {"sql": "SELECT to_jsonb(t) AS r FROM ref.project_type AS t"})
        if not result.executed or result.result is None or not result.result.ok:
            print(f"  lookup blocked: {result.result.error if result.result else 'no result'}")
            return 1
        rows = _unwrap_jsonb_rows(result.result.output)
        if not rows:
            print("  no rows returned")
            return 1
        columns = list(rows[0].keys())
        n = len(rows)
        print(f"  rows: {n}   columns: {len(columns)}\n")
        for c in columns:
            vals = [str(r.get(c, "")).strip() for r in rows]
            nonempty = [v for v in vals if v and v.upper() != "NULL"]
            distinct = sorted({v for v in nonempty}, key=str.lower)
            empty_frac = 1 - len(nonempty) / n
            eg = ", ".join(distinct[:8])
            print(f"  - {c:20} distinct={len(distinct):<4} empty={empty_frac:>4.0%}  e.g. {eg[:110]}")
        det = _detect_ref_columns(columns, rows)
        canon = _pick_canonical_column(
            columns, rows, name_col=det["name"], exclude={det["distance"], det["unit"]}
        )
        print("\n  detected:")
        print(f"    name (label)     : {det['name']}")
        print(f"    canonical (wsp)  : {canon or '(none — falls back to name)'}")
        print(f"    buffer distance  : {det['distance']}")
        print(f"    buffer unit      : {det['unit']}")
        print(f"    matchable        : {', '.join(det['matchable'])}")
        print("\n  If 'canonical (wsp)' is wrong, set REF_PROJECT_TYPE_COLUMN to the real column name above.")
        return 0
    except Exception as exc:
        print(f"  debug failed ({type(exc).__name__}: {exc})")
        return 1
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def probe_vec_schema(model: str) -> int:
    """Diagnostic: dump columns + sample values of the impact / VEC / sensitivity reference tables
    so the exact VEC schema can be confirmed against the LIVE DB. Read-only, governed, table-scoped.
    Run: python examples/extract.py --debug-vec
    """
    banner("DEBUG - impact / VEC / sensitivity schema")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("  DATABASE_URL not set")
        return 2
    candidates = ["action_impact_factor", "impacts", "impact", "impact_factor", "vec", "vec_sensitivity_indicator"]
    ws = tempfile.mkdtemp(prefix="autarch_vecdbg_")
    try:
        adapter = connect_postgres(dsn, read_only=True, allow_tables=candidates, max_rows=20000, autocommit=True)
        agent = Agent(
            intent="inspect impact/VEC schema",
            adapters=[adapter],
            grants=[capability("db.query")],
            workspace=ws,
        )
        # discovery: where do the impact/VEC/sensitivity tables actually live? (any schema)
        try:
            disc_agent = Agent(
                intent="discover impact/VEC tables",
                adapters=[connect_postgres(dsn, read_only=True, max_rows=500, autocommit=True)],
                grants=[capability("db.query")],
                workspace=ws,
            )
            dq = (
                "SELECT table_schema, table_name, string_agg(column_name, ', ') AS cols "
                "FROM information_schema.columns "
                "WHERE table_name ILIKE '%impact%' OR table_name ILIKE '%vec%' OR table_name ILIKE '%sensitiv%' "
                "OR column_name ILIKE '%environmental_social%' OR column_name ILIKE '%impact_factor%' "
                "GROUP BY table_schema, table_name ORDER BY table_schema, table_name"
            )
            dr = disc_agent.enact("db.query", {"sql": dq})
            print("  TABLE DISCOVERY (schema.table -> columns):")
            if dr.executed and dr.result is not None and dr.result.ok:
                drows = (dr.result.output or {}).get("rows") or []
                if not drows:
                    print("    (nothing matched impact/vec/sensitivity)")
                for row in drows:
                    print(f"    {row.get('table_schema')}.{row.get('table_name')}: {str(row.get('cols'))[:130]}")
            else:
                print(f"    discovery blocked ({dr.result.error if dr.result else 'no result'})")
        except Exception as exc:
            print(f"    discovery failed ({type(exc).__name__}: {exc})")

        def _probe(t, limit=None):
            """Read ref.<t>, else bare <t> (search_path/public). Returns (rows, fqn_used, err)."""
            lim = f" LIMIT {limit}" if limit else ""
            err = "no result"
            for fqn in (f"ref.{t}", t):
                try:
                    r = agent.enact("db.query", {"sql": f"SELECT to_jsonb(x) AS r FROM {fqn} AS x{lim}"})
                except Exception as exc:
                    err = f"{type(exc).__name__}: {exc}"
                    continue
                if r.executed and r.result is not None and r.result.ok:
                    return _unwrap_jsonb_rows(r.result.output), fqn, None
                err = r.result.error if r.result else "no result"
            return [], None, err

        for t in candidates:
            rows, fqn, err = _probe(t, limit=200)
            if not rows:
                print(f"  {t:26} : {'exists, 0 rows' if err is None else 'n/a (' + str(err) + ')'}")
                continue
            cols = list(rows[0].keys())
            print(f"\n  {fqn}  ({len(rows)}+ sampled)  columns: {', '.join(cols)}")
            for c in cols:
                vals = sorted({str(r.get(c, '')).strip() for r in rows if str(r.get(c, '')).strip()}, key=str.lower)
                print(f"      - {c:30} distinct~{len(vals):<4} e.g. {', '.join(vals[:6])[:100]}")
        # --- JOIN CHECK: do action_impact_factor.impact_factor values resolve in the impacts table? ---
        aif, _aif_fqn, _ = _probe("action_impact_factor")
        imp, imp_fqn, _ = _probe(REF_IMPACT_VEC_TABLE)
        print("\n  JOIN CHECK (impact factor -> VEC):")
        if aif and imp:
            acol = _resolve_column(list(aif[0].keys()), (REF_IMPACT_FACTOR_COLUMN, "impact_factor", "impact", "factor", "factor_name"))
            aif_vec = _resolve_column(list(aif[0].keys()), (REF_IMPACT_VEC_COLUMN, "environmental_social_component", "vec", "vec_name"))
            ikey = _resolve_column(list(imp[0].keys()), (REF_IMPACT_VEC_KEY_COLUMN, "impact_factor_name", "impact_factor", "impact", "impact_name", "factor", "factor_name", "name"))
            ivec = _resolve_column(list(imp[0].keys()), (REF_IMPACT_VEC_COLUMN, "environmental_social_component", "vec", "vec_name", "valued_component"))
            print(f"    action_impact_factor: factor col = {acol}; inline VEC col = {aif_vec or '(none)'}")
            print(f"    {imp_fqn or REF_IMPACT_VEC_TABLE}: key col = {ikey}; VEC col = {ivec}")
            if acol and ikey and ivec:
                afacs = sorted({str(r.get(acol, '')).strip() for r in aif if str(r.get(acol, '')).strip()})
                ikeys = {str(r.get(ikey, '')).strip().lower() for r in imp}
                hits = [f for f in afacs if f.lower() in ikeys]
                misses = [f for f in afacs if f.lower() not in ikeys]
                vecs = sorted({str(r.get(ivec, '')).strip() for r in imp if str(r.get(ivec, '')).strip()}, key=str.lower)
                print(f"    exact matches       : {len(hits)}/{len(afacs)} impact factors resolve to a VEC")
                if misses:
                    print(f"    example NON-matching factors: {misses[:6]}")
                print(f"    impacts VECs ({len(vecs)}): {', '.join(vecs[:25])}")
                if afacs and len(hits) < len(afacs) * 0.5:
                    print("    -> LOW overlap: the two tables key impact factors differently; that's why few VECs resolve.")
        else:
            print(f"    action_impact_factor rows: {len(aif)}; ref.impact rows: {len(imp)} (need both non-empty)")
        print("\n  Expected chain:")
        print("    action_impact_factor.impact_factor  ->  ref.impacts.impact_factor_name -> environmental_social_component  (VEC)")
        print("    ref.vec_sensitivity_indicator.vec_name  ->  sensitivity_indicator")
        return 0
    except Exception as exc:
        print(f"  debug failed ({type(exc).__name__}: {exc})")
        return 1
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def evaluate_quality(fields: dict, text: str, model: str, skip=()):
    """Consume the framework's quality panel; this supplies only the task inputs."""
    payload = json.dumps(fields, ensure_ascii=False)
    grounded_values = ". ".join(
        str(fields[k]) for k in FIELDS if k not in GROUNDED_EXCLUDE and fields.get(k)
    )
    key_source = ". ".join(fields[k] for k in KEY_FIELDS if fields.get(k))
    # project_type/components (DB-reconciled) and inferred fields aren't verbatim in the doc, so the
    # accuracy judge shouldn't penalize them as "invented" — evaluate only the doc-grounded fields.
    acc_fields = {k: v for k, v in fields.items() if k not in GROUNDED_EXCLUDE}
    acc_item = f"DOCUMENT (excerpt):\n{text[:8000]}\n\n---\nEXTRACTION:\n{json.dumps(acc_fields, indent=2, ensure_ascii=False)}"

    panel = quality_panel(
        source=text,
        required=REQUIRED_FIELDS,
        coverage_source=key_source or None,
        judges={
            "accuracy": RubricJudge(model, threshold=0.6, name="accuracy", rubric=_ACCURACY_RUBRIC + _judge_lang_suffix()),
            "coherence": RubricJudge(model, threshold=0.6, name="coherence", rubric=_COHERENCE_RUBRIC + _judge_lang_suffix()),
        },
        extra={
            "format": AssertionEvaluator(
                [
                    ("description is detailed", lambda s: len(json.loads(s).get("project_description", "")) >= 40),
                    (
                        "size carries a figure",
                        lambda s: (not json.loads(s).get("project_size"))
                        or any(c.isdigit() for c in json.loads(s)["project_size"]),
                    ),
                ]
            )
        },
    )
    items = {
        "completeness": payload,
        "groundedness": grounded_values,
        "coverage": fields.get("project_description", ""),
        "accuracy": acc_item,
        "coherence": fields.get("project_description") or "(none)",
        "format": payload,
    }
    return panel.evaluate(items, exclude=tuple(skip))


def evaluate_safety(fields: dict, text: str, model: str, governed_ok: bool, skip=()):
    """Consume the framework's safety panel; this supplies only the task inputs."""
    output_text = ". ".join(str(v) for v in fields.values() if v)
    panel = safety_panel(
        model=model,
        harm_rubric=_HARM_RUBRIC + _judge_lang_suffix(),
        extra={"governance": AssertionEvaluator([("agent proven unable to write or delete", bool)])},
    )
    items = {
        "governance": governed_ok,
        "prompt_injection": text,
        "pii_exposure": output_text,
        "harmful_content": json.dumps(fields, ensure_ascii=False),
    }
    return panel.evaluate(items, exclude=tuple(skip))


def evaluate_field_verdicts(fields: dict, text: str, model) -> dict:
    """LLM judge returning a PER-FIELD verdict for the extraction: for each populated field,
    ``{"status": "ok"|"warning"|"fail", "reason": ...}`` grounded ONLY in the document. One call,
    fail-soft to ``{}`` (never blocks the run); re-raises RateLimited so the executor can pace."""
    populated = {k: str(v).strip() for k, v in fields.items() if str(v).strip()}
    if not populated:
        return {}
    try:
        provider = build_provider(model)
        listing = "\n".join(f"- {k}: {v[:400]}" for k, v in populated.items())
        system = "You are a meticulous verification judge. Output ONLY one JSON object, no prose."
        prompt = (
            "Judge EACH extracted field against the DOCUMENT. Return a JSON object mapping every "
            'field name to {"status": "ok"|"warning"|"fail", "reason": "<short justification>"}:\n'
            "- ok: the value is clearly supported by the document and in the correct field.\n"
            "- warning: only partially supported, imprecise, inferred, or possibly misplaced.\n"
            "- fail: contradicted by, or absent from, the document (likely invented).\n"
            "Use ONLY the document; do not rely on outside knowledge.\n\n"
            f"EXTRACTED FIELDS:\n{listing}\n\nDOCUMENT:\n{text[:12000]}\n\nJSON:"
        )
        data = extract_json(provider.complete(prompt, system=system)) or {}
    except RateLimited:
        raise
    except Exception:
        return {}
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                out[str(k)] = {"status": str(v.get("status", "")).lower().strip(), "reason": str(v.get("reason", "")).strip()}
            elif isinstance(v, str):
                out[str(k)] = {"status": v.lower().strip(), "reason": ""}
    return out


def _verdict_table(rows) -> str:
    """Aligned per-field verdict table: field | present | grounded | judge | reason (reason wraps)."""
    header = (_t("field"), _t("present"), _t("grounded"), _t("judge"), _t("reason"))
    term_w = shutil.get_terminal_size((120, 40)).columns
    fw = min(24, max(len(header[0]), max((len(str(r[0])) for r in rows), default=5)))
    pw = max(len(header[1]), max((len(str(r[1])) for r in rows), default=0))
    gw = max(len(header[2]), max((len(str(r[2])) for r in rows), default=0))
    jw = max(len(header[3]), max((len(str(r[3])) for r in rows), default=0))
    lead = 2 + fw + 1 + pw + 1 + gw + 1 + jw + 1
    rw = max(24, min(80, term_w - lead))

    def line(field, present, grounded, judge, reason):
        rlines = textwrap.wrap(str(reason), rw) or [""]
        first = (f"  {str(field)[:fw].ljust(fw)} {str(present).center(pw)} "
                 f"{str(grounded).center(gw)} {str(judge).center(jw)} {rlines[0]}")
        rest = [(" " * lead) + rl for rl in rlines[1:]]
        return "\n".join([first] + rest)

    out = [line(*header), "  " + "\u2500" * min(term_w - 2, lead + rw)]
    out.extend(line(*r) for r in rows)
    return "\n".join(out)


def _usage_table(rows, total) -> str:
    """Aligned token-usage table: model | calls | input | output | est. cost (USD)."""
    out = [f"  {'model':24} {'calls':>5} {'input':>11} {'output':>11} {'est.$':>10}",
           "  " + "\u2500" * 64]
    for m, calls, pin, pout, cost in rows:
        out.append(f"  {str(m)[:24]:24} {calls:>5} {pin:>11,} {pout:>11,} {cost:>10.4f}")
    calls, pin, pout, cost, est = total
    out.append("  " + "\u2500" * 64)
    out.append(f"  {'TOTAL':24} {calls:>5} {pin:>11,} {pout:>11,} {cost:>10.4f}")
    if est:
        out.append("  * some token counts estimated (the API/runtime returned no usage)")
    return "\n".join(out)


def _usage_html(rows, total) -> str:
    """Token usage + estimated cost as an HTML table."""
    trs = ["<tr><th>model</th><th>calls</th><th>input tokens</th><th>output tokens</th><th>est. cost (USD)</th></tr>"]
    for m, calls, pin, pout, cost in rows:
        trs.append(f"<tr><th>{_html_escape(m)}</th><td>{calls:,}</td><td>{pin:,}</td><td>{pout:,}</td><td>${cost:,.4f}</td></tr>")
    calls, pin, pout, cost, est = total
    star = " *" if est else ""
    trs.append(f"<tr><th>TOTAL{star}</th><td>{calls:,}</td><td>{pin:,}</td><td>{pout:,}</td><td>${cost:,.4f}</td></tr>")
    tail = "<div class='sub'>* some token counts estimated (no usage returned)</div>" if est else ""
    return "<table class='scores'>\n" + "\n".join(trs) + f"\n</table>{tail}"


_EVAL_PHASES = ("quality_judges", "safety_judges", "field_verdict")


def _call_kind(label) -> str:
    """Classify a per-call phase as a main extraction call or an evaluation/judge call."""
    return "eval" if str(label) in _EVAL_PHASES else "main"


def _fmt_clock(t) -> str:
    """Wall-clock HH:MM:SS.mmm for a unix timestamp (or '-' when unknown)."""
    if not t:
        return "-"
    import datetime
    d = datetime.datetime.fromtimestamp(t)
    return d.strftime("%H:%M:%S.") + f"{d.microsecond // 1000:03d}"


def _fmt_dur(sec) -> str:
    """Human duration: '812ms' or '2.03s' (or '-' when unknown)."""
    if not sec or sec <= 0:
        return "-"
    return f"{sec:.2f}s" if sec >= 1 else f"{int(sec * 1000)}ms"


def _usage_calls_table(rows) -> str:
    """Per-call table: # | kind | phase | start | end | dur | input | output | est. cost (USD)."""
    out = [f"  {'#':>3} {'kind':4} {'phase':24} {'start':12} {'end':12} {'dur':>7} {'input':>8} {'output':>8} {'est.$':>8}",
           "  " + "\u2500" * 100]
    for n, kind, phase, model, start, end, dur, pin, pout, cost, est in rows:
        star = "*" if est else " "
        out.append(f"  {n:>3} {str(kind):4} {str(phase)[:24]:24} {start:12} {end:12} {dur:>7} {pin:>8,} {pout:>8,} {cost:>8.4f}{star}")
    return "\n".join(out)


def _usage_calls_html(rows) -> str:
    """Per-call token+cost as an HTML table (with kind, start/end/duration, model)."""
    trs = ["<tr><th>#</th><th>kind</th><th>phase</th><th>model</th><th>start</th><th>end</th><th>dur</th>"
           "<th>input tokens</th><th>output tokens</th><th>est. cost (USD)</th></tr>"]
    for n, kind, phase, model, start, end, dur, pin, pout, cost, est in rows:
        ph = _html_escape(phase) + (" *" if est else "")
        trs.append(f"<tr><td>{n}</td><td>{_html_escape(kind)}</td><th>{ph}</th><td>{_html_escape(model)}</td>"
                   f"<td>{start}</td><td>{end}</td><td>{dur}</td><td>{pin:,}</td><td>{pout:,}</td><td>${cost:,.4f}</td></tr>")
    return "<table class='scores'>\n" + "\n".join(trs) + "\n</table>"


def _serve_report(report_path, root) -> None:
    """Serve ``root`` over http://127.0.0.1 and open ``report_path`` in the default browser. Browsers
    block clicking ``file://`` links (and VS Code's viewer blocks them too), so a tiny local HTTP
    server is the robust way to make citation links open the source document at its page. Blocks
    until Ctrl+C."""
    import functools
    import http.server
    import socketserver
    import urllib.parse
    import webbrowser

    class _Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):  # keep the console clean
            pass

    handler = functools.partial(_Quiet, directory=str(root))
    try:
        httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    except Exception as exc:
        print(f"  (could not start local server: {type(exc).__name__}: {exc})")
        return
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/{urllib.parse.quote(report_path.name)}"
    print(f"\n  Serving report at: {url}")
    print("  Opening it in your browser — click any 'source' link to open the document at its page.")
    print("  (Press Ctrl+C here to stop the server.)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  server stopped.")
    finally:
        httpd.shutdown()
        httpd.server_close()


def main() -> int:
    _use_utf8_console()  # Windows consoles default to a legacy code page -> mojibake for é / em dash
    path, model, skip, debug_ref, debug_vec, shapes, html, lang, embed, cite_on, serve = parse_args(sys.argv[1:])
    if serve and html is None:
        html = ""  # --serve implies generating the report
    get_usage_meter().reset()  # count only THIS run's model calls
    global _LANG, _LANG_DIRECTIVE
    _LANG = lang or ""
    _LANG_DIRECTIVE = _lang_directive(lang)
    if _LANG:
        _translate_ui(model)  # localize the report's own labels/headings/columns once
    embedder = _build_grounding_embedder(embed)
    if debug_ref:
        return debug_reference(model)
    if debug_vec:
        return probe_vec_schema(model)
    if not path:
        print("usage: python examples/extract.py <document.pdf> [--model ollama:llama3] [--skip dim1,dim2] [--shapes dir-or-file.shp] [--html [out.html]] [--serve] [--lang <name>] [--embed [spec]] [--no-cite] [--debug-ref] [--debug-vec]")
        return 2
    pdf = Path(path).expanduser()
    if not pdf.exists():
        print(f"file not found: {pdf}")
        return 2

    if lang:
        print(f"  output language: extracted values will be written in {lang}")
    if embedder is not None:
        print(f"  grounding: semantic via embedder '{getattr(embedder, 'name', embed)}' (language-agnostic)")

    banner(f"1) {_t('GOVERNED READ')} — {pdf.name}")
    agent, result, workspace, governed_ok = governed_read(pdf)
    try:
        if not result.executed or result.result is None or not result.result.ok:
            print(f"  read was blocked: {result.result.error if result.result else 'no result'}")
            return 1
        text = result.result.output
        print(f"  read OK: {len(text):,} characters across the document")
        print(f"  signed why-record: {result.why_id}")
        provenance_ok = agent.memory.verify_provenance(result.why_id)
        print(f"  provenance verifies: {provenance_ok}")

        if _looks_scanned(text):  # scanned / image-only document -> recover its text with vision
            recovered = vision_transcribe(pdf, model)
            if recovered.strip():
                print(f"  scanned/image document: recovered {len(recovered):,} chars via vision OCR")
                text = recovered
            else:
                print("  little/no extractable text; vision OCR unavailable "
                      "(needs a vision model + `pip install pymupdf` for scanned PDFs)")

        banner(f"2) {_t('PROJECTS IDENTIFIED')} — via {model}")
        # A document can describe MANY projects/components. Identify them all, note the overall/
        # umbrella project and the dominant sub-type; the DETAILED extraction is done PER PROJECT
        # in section 3 (the overall fields below feed only the quality/safety evaluation).
        with usage_label("identify_projects"):
            primary = identify_primary_project(text, model)
        prim = primary.get("primary") or {}
        projects = primary.get("projects") or []
        if projects:
            print(f"  {len(projects)} project(s)/component(s) identified in the document:")
            for p in projects:
                print(f"    - {(p.get('name') or '?'):32} [{p.get('type') or '?'}]  "
                      f"role={p.get('role') or '?'}  prominence={p.get('prominence') or '?'}")
        if prim:
            dominant = prim.get("dominant_type") or prim.get("type") or "?"
            print(f"  => overall project: {prim.get('name') or '?'}  [{prim.get('type') or '?'}]")
            print(f"     dominant sub-type (leads the breakdown): {dominant}")
            if prim.get("why"):
                print(f"     why: {prim['why']}")
        with usage_label("extract_fields"):
            fields = extract_fields(text, model, primary=prim)
        # The standardized project_type is classified from the PRIMARY project's DOMINANT
        # component (by capacity/scale) — more reliable than an umbrella 'integrated' label,
        # and concrete enough to reconcile cleanly against ref.project_type.
        dominant = (prim.get("dominant_type") or prim.get("type") or "") if prim else ""
        classify_value = dominant or fields.get("project_type", "")
        if classify_value:
            fields["project_type"] = classify_value
        classify_context = " | ".join(
            p for p in (
                dominant,
                fields.get("project_components", ""),
                fields.get("project_size", ""),
                (fields.get("project_description", "") or "")[:600],
            ) if p
        )
        with usage_label("classify_project_type"):
            mapping = map_project_type(classify_value, model, context=classify_context)
        if mapping["matched"]:
            fields["project_type"] = mapping["canonical"]
            if mapping.get("buffer"):
                fields["area_of_influence"] = f"{mapping['buffer']} buffer zone (ref.project_type)"

        # Enrich EACH identified project (primary first, then secondaries) with its OWN
        # standardized type -> components -> impact factors, as GOVERNED, adaptively-parallel
        # children: the framework's AdaptiveExecutor self-tunes concurrency to Azure's throttling
        # (no fixed cap, no 429 storms) and guarantees every project completes. Reference tables
        # are fetched once per run (cached) and reused across projects.
        breakdown = select_breakdown_projects(projects, dominant, prim.get("name", ""))
        enriched = enrich_breakdown(breakdown, text, model)
        # Optional shapefiles override each matched project's location + area of influence with
        # AUTHORITATIVE geometry (Tier 1 = centroid/extent; Tier 2 = buffered area from the ref
        # distance). Read-only, attested; graceful when absent.
        shp_note, n_geo = None, 0
        if shapes:
            shp_features, shp_note = governed_read_shapes(shapes)
            n_geo = apply_shapes_to_projects(shp_features, enriched)
            print(f"  shapefiles: {shp_note}; authoritative geometry applied to {n_geo} project(s)")
        # the PRIMARY project's components populate the summary field the evaluators consume
        # (grounding/quality run on the overall extraction).
        if enriched:
            pc = enriched[0]["components"]
            if pc["reference"] or pc["ai_additional"]:
                fields["project_components"] = "; ".join(
                    list(pc["reference"]) + [f"{c} (AI)" for c in pc["ai_additional"]]
                )

        banner("3) PROJECT-BY-PROJECT — full detail per project")
        if not enriched:
            print("  (no distinct projects identified to break down)")
        project_cards = []  # (title_lines, rows) per project, reused for the HTML report
        _citer = Citer(text, embedder=embedder) if cite_on else None  # implicit per-value citations
        _page_locate = _build_page_locator(pdf) if cite_on else None  # citation -> real document page
        import urllib.parse as _urlparse
        # Serving over http -> RELATIVE links (resolve under the local server, so the browser opens
        # them); otherwise absolute file:// (best-effort, since browsers often block file:// clicks).
        _doc_ref = _urlparse.quote(pdf.name) if serve else _file_url(pdf)
        global _DOC_URL
        _DOC_URL = _doc_ref if cite_on else ""
        for i, ep in enumerate(enriched):
            ai_tag = _t("(AI)")
            unknown = _t("(unknown)")
            label = _t("PRIMARY PROJECT") if i == 0 else f"{_t('SECONDARY PROJECT')} {i}"
            meta = "; ".join(x for x in (
                (f"{_t('role')}: {_t(ep['role'])}" if ep["role"] else ""),
                (f"{_t('prominence')}: {_t(ep['prominence'])}" if ep["prominence"] else ""),
            ) if x)
            tsrc = _t("ref.project_type") if ep["type_matched"] else _t("AI-extracted")
            comp = ep["components"]
            csrc = _t("ref.project_component") if comp["matched"] else _t("AI-extracted")
            cextra = f"  (+{len(comp['ai_additional'])} {_t('AI-found in this project')})" if comp["ai_additional"] else ""
            comp_val = [f"[{csrc}] {comp['note']}{cextra}"]
            comp_val += [f"  - {c}" for c in comp["reference"]]
            comp_val += [f"  - {c}  {ai_tag}" for c in comp["ai_additional"]]
            imp = ep["impacts"]
            isrc = _t("ref.action_impact_factor") if imp["matched"] else _t("AI-extracted")
            imp_val = [f"[{isrc}] {imp['note']}"]
            for item in imp["by_action"]:
                factors = ", ".join(item["factors"]) or _t("(no factors listed)")
                imp_val.append(f"  - {item['action']}: {factors}")
            vecs = imp.get("vecs", [])
            ai_vecs = imp.get("ai_vecs", set())
            vec_disp = "; ".join(v + (f" {ai_tag}" if v.lower() in ai_vecs else "") for v in vecs)
            sens = imp.get("sensitivity", [])
            none_listed = _t("(none listed)")
            sens_val = [f"  - {s['vec']}{(' ' + ai_tag) if s.get('ai') else ''}: " + (", ".join(s["indicators"]) or none_listed) for s in sens]

            title = [f"{label}: {ep['name']}  [{ep['type'] or unknown}]"]
            if meta:
                title.append(meta)
            rows = [("type", f"{ep['type'] or unknown}  [{tsrc}]")]
            if ep.get("sector"):
                rows.append(("sector", _cite_value(ep["sector"], _citer, _page_locate)))
            if ep.get("proponent"):
                rows.append(("proponent", _cite_value(ep["proponent"], _citer, _page_locate)))
            if ep.get("area_of_influence"):
                rows.append(("area of influence", ep["area_of_influence"]))
            elif ep.get("buffer"):
                rows.append(("area of influence", f"{ep['buffer']} {_t('buffer zone')}"))
            rows.append(("size", _cite_value(ep.get("size"), _citer, _page_locate) if ep.get("size") else _t("(not stated)")))
            rows.append(("location", _cite_value(ep.get("location"), _citer, _page_locate) if ep.get("location") else _t("(not stated)")))
            rows.append(("description", _cite_value(ep.get("description"), _citer, _page_locate) if ep.get("description") else _t("(not stated)")))
            rows.append(("components", comp_val))
            if ep.get("activities"):
                rows.append(("activities", _cite_value(ep["activities"], _citer, _page_locate)))
            if ep.get("regulatory_context"):
                rows.append(("regulatory context", ep["regulatory_context"]))
            if ep.get("process_type"):
                rows.append(("process type", ep["process_type"]))
            if ep.get("stage"):
                rows.append(("stage of process", ep["stage"]))
            rows.append(("impact factors", imp_val))
            # VEC + sensitivity ALWAYS follow impact factors for every project (placeholder when the
            # reference lookup resolved nothing, so the two rows are never silently dropped).
            rows.append(("VEC (env/social)", vec_disp if vecs else _t("(none resolved)")))
            rows.append(("sensitivity indicators", sens_val if sens_val else _t("(none resolved)")))
            project_cards.append((title, rows))
            print("\n" + _kv_table(title, rows))

        # a reference-mapped project_type is authoritative (from the DB), so exempt it from the
        # doc-grounding check; the PRIMARY project's reference component names are DB-sourced too.
        ground_exempt = set(INFERRED_FIELDS) | ({"project_type"} if mapping["matched"] else set())
        if enriched and enriched[0]["components"]["matched"]:
            ground_exempt.add("project_components")
        flagged = check_grounding(fields, text, exempt=ground_exempt, embedder=embedder)
        if flagged:
            print(f"\n  {_t('anti-hallucination')} — {len(flagged)} {_t('value(s) NOT grounded in the source (review):')}")
            for k, v, why in flagged:
                print(f"    - {k}: {v!r}  ({why})")
        else:
            print(f"\n  {_t('anti-hallucination: every extracted value is grounded in the source')}")

        banner("4) QUALITY — deterministic checks + LLM judges")
        with usage_label("quality_judges"):
            quality = evaluate_quality(fields, text, model, skip=skip)
        for name, score, passed, reason in quality.rows():
            print(f"  {_t(name):16} {score:.2f}  {_t('PASS') if passed else _t('FAIL')}  {reason}")
        if quality.skipped:
            print(f"  ({_t('skipped')}: {', '.join(quality.skipped)})")
        print(f"  -> {_t('quality')}: {_t('PASS') if quality.passed else _t('FAIL')}   {_t('mean score')} {quality.score:.2f}")

        banner("5) SAFETY — governance + content safety")
        with usage_label("safety_judges"):
            safety = evaluate_safety(fields, text, model, governed_ok, skip=skip)
        for name, score, passed, reason in safety.rows():
            print(f"  {_t(name):16} {score:.2f}  {_t('PASS') if passed else _t('FAIL')}  {reason}")
        if safety.skipped:
            print(f"  ({_t('skipped')}: {', '.join(safety.skipped)})")
        print(f"  -> {_t('safety')}: {_t('PASS') if safety.passed else _t('FAIL')}")

        banner("6) FIELD-BY-FIELD VERDICT \u2014 per-field judge status")
        with usage_label("field_verdict"):
            field_verdicts = evaluate_field_verdicts(fields, text, model)
        flagged_map = {k: why for k, _v, why in flagged}
        verdict_rows = []
        for k in FIELDS:
            val = str(fields.get(k, "")).strip()
            if not val:
                continue
            grounded = "n/a" if k in ground_exempt else ("NO" if k in flagged_map else "yes")
            ver = field_verdicts.get(k) or {}
            status = (ver.get("status") or "").upper() or "?"
            reason = ver.get("reason") or (flagged_map.get(k, "") if grounded == "NO" else "")
            verdict_rows.append((k, _t("yes"), _t(grounded), _t(status), reason))
        if verdict_rows:
            print(_verdict_table(verdict_rows))
            if not field_verdicts:
                print("  (judge unavailable \u2014 showing deterministic present/grounded columns only)")
        else:
            print("  (no populated fields to verify)")

        citations = {}
        if cite_on and _citer is not None:
            banner("SOURCE CITATIONS — supporting passage per field")
            cite_rows = []
            for k in FIELDS:
                val = str(fields.get(k, "")).strip()
                if not val or k in INFERRED_FIELDS:
                    continue
                c = _citer.cite(val)
                if c is not None:
                    d = c.as_dict()
                    page = _page_locate(c.text) if _page_locate else None
                    d["page"] = page
                    citations[k] = d
                    snip = c.text if len(c.text) <= 240 else c.text[:237] + "\u2026"
                    where = (f"p.{page} \u00b7 " if page else "") + f"{c.method} {c.score:.2f}"
                    cite_rows.append((k, f'"{snip}"  [{where}]'))
                else:
                    cite_rows.append((k, _t("(no supporting passage found)")))
            if cite_rows:
                print("\n" + _kv_table([_t("field → supporting source passage")], cite_rows))
            else:
                print("  (no populated fields to cite)")

        banner("7) TOKEN USAGE & COST \u2014 per model (cost estimated from list prices)")
        _meter = get_usage_meter()
        _pb = PriceBook()
        usage_rows = [
            (m, d["calls"], d["prompt_tokens"], d["completion_tokens"],
             _pb.token_cost(m, d["prompt_tokens"], d["completion_tokens"]))
            for m, d in sorted(_meter.by_model().items())
        ]
        _tot = _meter.totals()
        usage_total = (_tot["calls"], _tot["prompt_tokens"], _tot["completion_tokens"],
                       _meter.cost(_pb), _tot["any_estimated"])
        usage_calls = [
            (n + 1, _call_kind(c.label), (c.label or c.source or "-"), c.model,
             _fmt_clock(c.started), _fmt_clock(c.ended or c.ts), _fmt_dur(c.duration),
             c.prompt_tokens, c.completion_tokens,
             _pb.token_cost(c.model, c.prompt_tokens, c.completion_tokens), c.estimated)
            for n, c in enumerate(sorted(_meter.calls, key=lambda c: (c.started or c.ended or c.ts or 0.0)))
        ]
        if usage_rows:
            print(_usage_table(usage_rows, usage_total))
            print("\n  per LLM call:")
            print(_usage_calls_table(usage_calls))
        else:
            print("  (no model calls recorded)")

        if html is not None:
            out_path = Path(html).expanduser() if html else pdf.with_name(pdf.stem + "_report.html")
            try:
                doc = render_report_html({
                    "doc_name": pdf.name, "doc_url": _doc_ref, "model": model, "chars": len(text), "lang": lang,
                    "why_id": result.why_id, "provenance_ok": provenance_ok,
                    "projects": projects, "overall": prim, "dominant": dominant,
                    "shapes_note": (f"{shp_note}; authoritative geometry applied to {n_geo} project(s)" if shp_note else None),
                    "cards": project_cards, "flagged": flagged,
                    "quality": quality, "safety": safety,
                    "verdicts": verdict_rows,
                    "citations": citations,
                    "usage": {"rows": usage_rows, "total": usage_total, "calls": usage_calls},
                })
                out_path.write_text(doc, encoding="utf-8")
                print(f"\n  HTML report written: {out_path}")
                if serve:
                    _serve_report(out_path, out_path.parent)
            except Exception as exc:
                print(f"\n  HTML report failed ({type(exc).__name__}: {exc})")

        return 0 if (quality.passed and safety.passed) else 1
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
