"""Read a capital-project INVOICE (PDF) and produce a governed, audit-ready tax/CIP determination.

This example targets Circle K's CIP-validation pain points directly. Today a reviewer opens each
invoice by hand, hunts through POPA/Laserfiche for project context, and — for only a *sample* of
20K-40K invoices/month — decides the task code, CapEx vs OpEx, and use-tax treatment *after*
payment. Errors surface months later in audit (~55% of exceptions), driving CIP aging and
depreciation surprises. The goal is to "shift left": evaluate EVERY invoice *before* payment, flag
exceptions with evidence, and route anything low-confidence to a human.

What this program does, per invoice, governed end to end by autarch:

  1. GOVERNED READ — an agent granted ONLY ``doc.read`` reads the invoice. It is provably unable to
     write, delete, or reach the network (a static guarantee), and the read is recorded in a signed,
     tamper-evident ledger (you can prove which file was read, when, and by whom). Scanned/image-only
     PDFs (no text layer) fall back to governed vision-OCR through the same model.
  2. INTAKE / EXTRACTION — the invoice HEADER (vendor, amount, PO#, AFE#, state, site, tax charged)
     and every LINE ITEM (description, qty, unit price, amount) are pulled into structured fields.
  3. CLASSIFY EACH LINE (flow step 3) — every line item is classified CapEx vs OpEx (rules + LLM),
     with its own asset category and validated task; an invoice line coded 'repair' that is really a
     new capital asset is re-tasked. A single invoice can legitimately mix CapEx and OpEx lines.
  4. APPLY TAX RULES PER LINE (flow step 4) — each line gets its taxability, expected rate/amount for
     the ship-to state, and a per-line EXCEPTION flag when the tax treatment looks MISCLASSIFIED
     (e.g. taxable tangible property treated as exempt, or bundled labor taxable in that state).
  5. PER-LINE CONFIDENCE + ROUTE (flow step 5) — each line's confidence is the worst of its two
     judges; a line auto-posts when high, or routes to SME review when low OR flagged. Invoice-level
     status = the worst line. A rollup reconciles CapEx/OpEx totals and expected-vs-charged tax into
     the self-assessed use tax owed (Circle K self-assesses; it does not go back to vendors).
  6. GROUNDING + PANELS — header values are checked against the signed source (anti-hallucination);
     accuracy / per-line-soundness / harmful-content LLM judges plus deterministic completeness,
     groundedness, prompt-injection, PII, and governance checks run and are metered for cost.
  7. EVIDENCE PACKAGE — a per-line decision, the rationale for each call, source citations, the
     signed why-record id, panel results, and a token-usage/cost breakdown — the audit trail,
     produced automatically.

The REASONING runs on the **Microsoft Agent Framework** (MAF): autarch's whole stack talks to
intelligence through one seam — ``ModelProvider.complete(prompt, system)`` — so we inject a
:class:`autarch.MAFModelProvider` (completions produced by an ``agent_framework.Agent`` on Azure
OpenAI) and autarch keeps governing, signing, and grounding around it.

Usage:
    # Live, on the Microsoft Agent Framework + Azure OpenAI:
    python examples/extract_invoice.py "C:/path/to/invoice.pdf" --model azure:gpt-5.4 --auth aad
    python examples/extract_invoice.py "C:/path/to/invoice.pdf" --json

    # Zero-setup, fully offline demonstration on a bundled sample invoice:
    python examples/extract_invoice.py --demo

Needs (live):  pip install agent-framework
               AZURE_OPENAI_ENDPOINT + either AZURE_OPENAI_API_KEY or `az login` (Entra ID, --auth aad)
               the deployment via --model azure:<deployment> (or AZURE_OPENAI_DEPLOYMENT).
Without Azure configured, ``--demo`` runs the SAME governed pipeline offline on a deterministic
provider so you can see the full, correct output shape end to end.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

# Run against THIS repository's autarch (the copy that ships MAFModelProvider), regardless of any
# other autarch install on the path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # so `import refdata` (sibling module) resolves

from autarch import (  # noqa: E402
    Agent,
    AssertionEvaluator,
    Citer,
    DocumentAdapter,
    ImageRef,
    Invariant,
    MAFModelProvider,
    PriceBook,
    RubricJudge,
    capability,
    check_grounding,
    get_usage_meter,
    quality_panel,
    safety_panel,
    usage_label,
)
from autarch.intelligence.factory import build_provider  # noqa: E402
from autarch.intelligence.mock import MockProvider  # noqa: E402
from autarch.util import extract_json  # noqa: E402
import refdata  # noqa: E402  governed taxability matrix / task codes / PO master / history
import docintel  # noqa: E402  Azure Document Intelligence prebuilt-invoice extraction (optional)
import decision_cache  # noqa: E402  deterministic classification cache (reproducible determination)

try:
    import agent_framework as _af  # noqa: F401
    _MAF_INSTALLED = True
except Exception:  # pragma: no cover
    _MAF_INSTALLED = False


# --------------------------------------------------------------------------------------------------
# Field contracts. INVOICE_FIELDS are lifted verbatim from the document (grounded against the source);
# the DERIVED determinations (asset/task/capex/tax) are reasoned, so they are exempt from the
# verbatim grounding check and instead carry their own confidence + rationale.
# --------------------------------------------------------------------------------------------------
INVOICE_FIELDS = (
    "vendor_name", "invoice_number", "invoice_date", "po_number", "afe_number",
    "total_amount", "currency", "state", "site_number", "tax_charged", "description",
)
REQUIRED_FIELDS = ("vendor_name", "invoice_number", "total_amount", "description")

DEFAULT_MODEL = "azure:gpt-5.4"
DEFAULT_THRESHOLD = 0.85  # "anything below ~85% confidence routes to a human"

# Diagram step 5 - CAPITALIZATION RULES (logic in code, $2k / $100k thresholds):
CAP_THRESHOLD = 2000.0             # capitalize an asset/project at/above this; below is a de-minimis expense
MAJOR_PROJECT_THRESHOLD = 100000.0  # at/above this it is a MAJOR project -> AFE/board approval + review
# Diagram step 10 - ROUTING confidence tiers (0.85 auto-approve, 0.70-0.85 auto-post w/ review flag):
AUTOPOST_FLAG_THRESHOLD = 0.70
# Period costs (freight, fuel surcharge, travel/mileage, handling) are EXPENSED regardless of the task
# code the model picked - they are never capitalized into an asset.
_PERIOD_COST_RE = re.compile(
    r"\b(freight|shipping|surcharge|trip\s*charge|trip\s*fee|travel|mileage|per\s*diem|handling|"
    r"restock(?:ing)?|expedit)\w*", re.IGNORECASE)

_EXTRACT_SYS = "You are a precise invoice information-extraction assistant. Output ONLY one JSON object, no prose."
_JUDGE_SYS = "You are a capital-asset accounting and use-tax expert for a US convenience-store chain. Output ONLY one JSON object, no prose."

# Fields that are DERIVED/computed rather than lifted verbatim from the invoice, so the deterministic
# grounding check must not penalize them as "invented" (e.g. a normalized amount, an inferred state).
GROUNDED_EXEMPT = ("currency",)

# Reusable LLM-judge rubrics (the framework owns the evaluators; the caller brings the judgment).
_ACCURACY_RUBRIC = (
    "The EXTRACTION lists fields pulled from an INVOICE. Score how ACCURATE and RELEVANT it is: every "
    "non-empty value must be factually supported by the INVOICE and placed in the correct field "
    "(vendor, amounts, PO#, AFE#, state, dates). Empty values are fine when the invoice is silent. "
    "Penalize invented, misattributed, contradicted, or misformatted values."
)
_SOUNDNESS_RUBRIC = (
    "Score whether the capitalization/tax DETERMINATIONS are SOUND and internally consistent with the "
    "invoice: the asset category fits the line items; the task/CapEx-vs-OpEx call follows from whether a "
    "NEW asset is created vs a like-for-like repair; and the tax math (expected rate x taxable base, use "
    "tax owed, exception flag) is arithmetically and logically coherent. Judge reasoning quality, not "
    "whether you would reach an identical dollar figure."
)
_HARM_RUBRIC = (
    "Safety review. Score whether the CONTENT is FREE of harmful material - violence, hate or unfairness, "
    "sexual content, self-harm, harassment, or dangerous instructions. 1.0 = completely benign; "
    "0.0 = clearly harmful."
)


# --------------------------------------------------------------------------------------------------
# A bundled sample invoice + a scripted offline answer key. The scripted answers are attached to the
# offline mock ONLY for ``--demo`` (the invoice is fixed and known), so the pipeline produces a full,
# correct determination with no model configured. For a real PDF + a real model NOTHING is scripted.
# The sample mirrors the prompt's worked example: an under-collected IL tax that becomes an exception.
# --------------------------------------------------------------------------------------------------
DEMO_INVOICE = """\
INVOICE

Vendor:      Gilbarco Veeder-Root
Invoice #:   GVR-2026-04821
Date:        2026-07-18
Bill To:     Circle K Stores Inc.
Site:        0042317  (Store #4317, Naperville, IL)
PO #:        PO-778901
AFE #:       AFE-2025-IL-0091

Description: Supply and installation of two (2) new Encore 700S fuel dispensers at the
             forecourt, including underground flex connectors and dispenser sumps. New
             fuel-island equipment for the remodeled forecourt.

Line Items:
  1. Encore 700S fuel dispenser (new)        Qty 2   Unit $9,500.00    $19,000.00
  2. Dispenser sump kit                      Qty 2   Unit $  650.00    $ 1,300.00
  3. Installation labor & flex connectors    Qty 1   Unit $4,200.00    $ 4,200.00

  Subtotal:                                                            $24,500.00
  Sales tax billed:                                                    $   196.00
  Total:                                                               $24,696.00

Ship-to state: IL (Illinois)
"""

_DEMO_SCRIPT = {
    "STEP: EXTRACT_INVOICE": json.dumps({
        "vendor_name": "Gilbarco Veeder-Root",
        "invoice_number": "GVR-2026-04821",
        "invoice_date": "2026-07-18",
        "po_number": "PO-778901",
        "afe_number": "AFE-2025-IL-0091",
        "total_amount": "24696.00",
        "currency": "USD",
        "state": "IL",
        "site_number": "0042317",
        "tax_charged": "196.00",
        "description": (
            "Supply and installation of two new Encore 700S fuel dispensers, including "
            "underground flex connectors and dispenser sumps, for the remodeled forecourt."
        ),
    }),
    "STEP: EXTRACT_LINES": json.dumps({
        "lines": [
            {"description": "Encore 700S fuel dispenser (new)", "quantity": "2", "unit_price": "9500.00", "amount": "19000.00"},
            {"description": "Dispenser sump kit", "quantity": "2", "unit_price": "650.00", "amount": "1300.00"},
            {"description": "Installation labor & flex connectors", "quantity": "1", "unit_price": "4200.00", "amount": "4200.00"},
        ]
    }),
    "STEP: CLASSIFY_LINES": json.dumps({
        "lines": [
            {"capex_opex": "CapEx", "asset_category": "Fuel Dispenser", "item_type": "Fuel Equipment",
             "task_code": "TC-1040", "suggested_task": "New Fuel Dispenser Installation (Capital)",
             "existing_task_ok": False, "confidence": 0.92,
             "rationale": "Brand-new Encore 700S dispenser tied to AFE-2025-IL-0091 - a new capital asset, not a repair."},
            {"capex_opex": "CapEx", "asset_category": "Fuel-Island Equipment", "item_type": "Fuel Equipment",
             "task_code": "TC-1040", "suggested_task": "New Fuel Dispenser Installation (Capital)",
             "existing_task_ok": True, "confidence": 0.90,
             "rationale": "Sump kit installed with the new dispensers is capitalized into the fuel-island asset."},
            {"capex_opex": "CapEx", "asset_category": "Installation Labor (capitalized)", "item_type": "Fuel Equipment",
             "task_code": "TC-1040", "suggested_task": "New Fuel Dispenser Installation (Capital)",
             "existing_task_ok": True, "confidence": 0.83,
             "rationale": "Labor to place the new dispensers in service is capitalized into the asset cost, not expensed."},
        ]
    }),
    "STEP: TAX_LINES": json.dumps({
        "lines": [
            {"taxable": True, "jurisdiction_state": "IL", "expected_tax_rate": 0.10, "expected_tax_amount": 1900.00,
             "exception": False, "exception_reason": "", "confidence": 0.90,
             "rationale": "Tangible personal property (dispenser) delivered/installed in IL is taxable at ~10%."},
            {"taxable": True, "jurisdiction_state": "IL", "expected_tax_rate": 0.10, "expected_tax_amount": 130.00,
             "exception": False, "exception_reason": "", "confidence": 0.90,
             "rationale": "Sump kit is tangible personal property, taxable in IL."},
            {"taxable": True, "jurisdiction_state": "IL", "expected_tax_rate": 0.10, "expected_tax_amount": 420.00,
             "exception": True, "exception_reason": ("Installation labor bundled with tangible personal property is "
                                                     "taxable in IL; if the vendor treated this labor line as exempt it is a "
                                                     "misclassification. Verify the labor/materials split."),
             "confidence": 0.72, "rationale": "IL taxes installation labor bundled with TPP; flagged for review."},
        ]
    }),
}


# --------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------
def _split_flag(argv, flag):
    """Pop ``flag <value>`` from a copy of argv; return ``(value, remaining_argv)``."""
    rest = list(argv)
    value = None
    if flag in rest:
        i = rest.index(flag)
        if i + 1 < len(rest) and not str(rest[i + 1]).startswith("--"):
            value = rest[i + 1]
            del rest[i:i + 2]
        else:
            del rest[i]
    return value, rest


def parse_args(argv):
    demo = "--demo" in argv
    as_json = "--json" in argv
    argv = [a for a in argv if a not in ("--demo", "--json")]
    # --csv [path]: export the per-line results. Bare --csv auto-names next to the invoice; an
    # explicit ...csv path is used as-is.
    csv_on, csv_path = False, None
    if "--csv" in argv:
        csv_on = True
        i = argv.index("--csv")
        if i + 1 < len(argv) and argv[i + 1].lower().endswith(".csv"):
            csv_path = argv[i + 1]
            del argv[i:i + 2]
        else:
            del argv[i]
    # --html [path]: write a self-contained HTML report. Bare --html auto-names <pdf>_report.html.
    html_on, html_path = False, None
    if "--html" in argv:
        html_on = True
        i = argv.index("--html")
        if i + 1 < len(argv) and argv[i + 1].lower().endswith((".html", ".htm")):
            html_path = argv[i + 1]
            del argv[i:i + 2]
        else:
            del argv[i]
    # --embed [spec]: DETERMINISTIC semantic line->task/item-type mapping via embeddings. Bare
    # --embed uses the offline 'hash' embedder; pass a spec (e.g. ollama:nomic-embed-text) for richer
    # meaning. When unset, the model free-picks the item type/task (non-deterministic).
    embed_spec = None
    if "--embed" in argv:
        i = argv.index("--embed")
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            embed_spec = argv[i + 1]
            del argv[i:i + 2]
        else:
            embed_spec = "hash"
            del argv[i]
    # --doci [endpoint]: extract the PDF with Azure Document Intelligence 'prebuilt-invoice'
    # (structured header + line items WITH per-field confidence; governing state from the ship-to
    # address). Bare --doci uses AZURE_DOCINTEL_ENDPOINT. Falls back to OCR+LLM if unavailable.
    doci_on, doci_endpoint = False, None
    if "--doci" in argv:
        doci_on = True
        i = argv.index("--doci")
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if nxt and not nxt.startswith("--") and not nxt.lower().endswith(".pdf"):
            doci_endpoint = nxt
            del argv[i:i + 2]
        else:
            del argv[i]
    # --no-cache / --cache [path]: DETERMINISTIC decision cache. ON by default (examples/
    # decision_cache.json) so the SAME invoice yields the SAME determination every run; --cache PATH
    # relocates the file, --no-cache disables it (re-derive every line from the model each run).
    cache_disabled = "--no-cache" in argv
    argv = [a for a in argv if a != "--no-cache"]
    cache_override = None
    if "--cache" in argv:
        i = argv.index("--cache")
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            cache_override = argv[i + 1]
            del argv[i:i + 2]
        else:
            del argv[i]
    cache_path = None if cache_disabled else (cache_override or decision_cache.DEFAULT_CACHE_PATH)
    model, argv = _split_flag(argv, "--model")
    auth, argv = _split_flag(argv, "--auth")
    thr, argv = _split_flag(argv, "--threshold")
    path = argv[0] if argv else None
    try:
        threshold = float(thr) if thr is not None else DEFAULT_THRESHOLD
    except ValueError:
        threshold = DEFAULT_THRESHOLD
    return (path, (model or DEFAULT_MODEL), (auth or "auto").lower(), threshold, demo, as_json,
            csv_on, csv_path, html_on, html_path, embed_spec, doci_on, doci_endpoint, cache_path)


def banner(title: str) -> None:
    print("\n" + "=" * 74 + f"\n{title}\n" + "=" * 74)


# --------------------------------------------------------------------------------------------------
# Microsoft Agent Framework wiring (adapted from examples/extract_maf.py).
# --------------------------------------------------------------------------------------------------
def _is_auth_error(exc) -> bool:
    m = str(exc).lower()
    return any(s in m for s in (
        "authenticationtypedisabled", "key based authentication is disabled", "permissiondenied",
        "invalid api key", "access denied", "code: 401", "code: 403", "401", "403",
    ))


def _make_client_factory(deployment, endpoint, api_version, use_aad):
    """Return ``(factory, auth_label)``. ``factory()`` lazily builds a MAF Azure chat client on the
    provider's own event loop (safe to reuse across autarch's thread-pool workers)."""
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    prefer_aad = use_aad or not api_key

    def _factory():
        from openai import AsyncAzureOpenAI
        # Use the Chat Completions client, NOT OpenAIChatClient: agent-framework >=1.12 rewired
        # OpenAIChatClient onto Azure's Responses API, which requests `include=[reasoning.
        # encrypted_content]` and is rejected ("Encrypted content is not supported with this model")
        # by non-reasoning deployments like gpt-4.1. OpenAIChatCompletionClient uses the classic
        # chat/completions route, which works for gpt-4o / gpt-4.1 and any api-version >= 2024-10-21.
        from agent_framework.openai import OpenAIChatCompletionClient

        kwargs = dict(azure_endpoint=endpoint, api_version=api_version)
        if prefer_aad:
            from azure.identity import (
                AzureCliCredential,
                ChainedTokenCredential,
                DefaultAzureCredential,
                get_bearer_token_provider,
            )

            # Pin the credential to the RESOURCE's tenant. The signed-in user's HOME tenant can
            # differ from the tenant that owns the Azure OpenAI resource (e.g. a guest/lab
            # subscription); a token minted for the home tenant is rejected with 401 "Principal does
            # not have access to API/Operation". AZURE_OPENAI_TENANT_ID (or AZURE_TENANT_ID) forces
            # the token into the resource's tenant.
            tenant = os.environ.get("AZURE_OPENAI_TENANT_ID") or os.environ.get("AZURE_TENANT_ID")
            if tenant:
                # Explicit tenant: use ONLY the CLI credential pinned to it. Do NOT chain a fallback
                # here — if the CLI flakily fails, a fallback credential (VS Code / shared cache /
                # home tenant) would silently mint a WRONG-tenant token and 401, which is exactly the
                # intermittent failure we are eliminating.
                credential = AzureCliCredential(tenant_id=tenant)
            else:
                # No tenant hint: prefer the CLI identity that was granted the role, then fall back.
                credential = ChainedTokenCredential(AzureCliCredential(), DefaultAzureCredential())
            kwargs["azure_ad_token_provider"] = get_bearer_token_provider(
                credential, "https://cognitiveservices.azure.com/.default"
            )
        else:
            kwargs["api_key"] = api_key
        return OpenAIChatCompletionClient(model=deployment, async_client=AsyncAzureOpenAI(**kwargs))

    return _factory, ("Entra ID" if prefer_aad else "api-key")


def _connect_maf(deployment, endpoint, api_version, auth_mode):
    """Build + validate a :class:`MAFModelProvider`, auto-falling-back between api-key and Entra ID.
    Returns ``(provider, auth_label)`` or ``(None, None)`` if nothing could connect."""
    has_key = bool(os.environ.get("AZURE_OPENAI_API_KEY"))
    modes = {"aad": [True], "key": [False]}.get(auth_mode, [False, True] if has_key else [True])
    for idx, use_aad in enumerate(modes):
        factory, label = _make_client_factory(deployment, endpoint, api_version, use_aad)
        # Deterministic decoding: temperature 0 + a fixed seed so the SAME invoice yields the SAME
        # classification/tax determination run-to-run (the model, not just the reference data, is
        # reproducible). gpt-4.1 / gpt-4o accept both; reasoning models that reject temperature will
        # ignore it via client_kwargs.
        candidate = MAFModelProvider(
            factory, agent_name="autarch-invoice-agent", model_label=deployment,
            run_kwargs={"client_kwargs": {"temperature": 0, "seed": 7}},
        )
        try:
            candidate.complete("Reply with the single word: OK.")  # one-turn auth probe
            return candidate, label
        except Exception as exc:  # noqa: BLE001
            candidate.close()
            if _is_auth_error(exc) and idx + 1 < len(modes):
                other = "Entra ID" if not use_aad else "api-key"
                print(f"  {label} auth rejected by the resource - retrying with {other} ...")
                continue
            print(f"  MAF/Azure connection failed ({type(exc).__name__}: {exc})")
            return None, None
    return None, None


def resolve_engine(model, auth_mode, demo):
    """Pick the reasoning engine. MAF on Azure when configured; otherwise an offline provider.

    Returns ``(provider, engine_label, is_live)``. For ``--demo`` offline, the provider is scripted
    to the bundled sample so the full determination still runs end to end."""
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    deployment = (
        model.split("azure:", 1)[1] if model.startswith("azure:")
        else (os.environ.get("AZURE_OPENAI_DEPLOYMENT") or model)
    )
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")

    if _MAF_INSTALLED and endpoint:
        provider, label = _connect_maf(deployment, endpoint, api_version, auth_mode)
        if provider is not None:
            return provider, f"Microsoft Agent Framework on '{deployment}' (auth {label})", True

    # Offline fallback.
    if not _MAF_INSTALLED:
        print("  Microsoft Agent Framework not installed (pip install agent-framework).")
    elif not endpoint:
        print("  Azure not configured (set AZURE_OPENAI_ENDPOINT + auth to drive it live via MAF).")
    if demo:
        print("  Running the SAME governed pipeline offline on a deterministic provider.\n")
        return MockProvider(scripted=_DEMO_SCRIPT), "offline deterministic provider (demo)", False
    print("  For a real determination, configure Azure+MAF (or pass --demo to see the full output).\n")
    return MockProvider(), "offline deterministic provider (no reasoning)", False


# --------------------------------------------------------------------------------------------------
# Governed read
# --------------------------------------------------------------------------------------------------
def governed_read(doc: Path):
    """Run an autarch agent that may ONLY read the document - and prove it."""
    workspace = tempfile.mkdtemp(prefix="autarch_invoice_")
    agent = Agent(
        intent=f"read and validate the invoice {doc.name}",
        adapters=[DocumentAdapter(root=str(doc.parent))],
        grants=[capability("doc.read", scope={"path_prefix": "."})],  # read-only by construction
        workspace=workspace,
    )
    report = agent.guarantee([Invariant.forbid("file.write"), Invariant.forbid("file.delete")])
    result = agent.enact("doc.read", {"path": doc.name})
    return agent, result, report.all_hold


# --------------------------------------------------------------------------------------------------
# Vision-OCR fallback. Many AP invoices are SCANNED (image-only PDFs from Laserfiche/email) with no
# text layer, so the governed read recovers nothing. When that happens we render the page(s) and OCR
# them through the SAME MAF vision model - the invoice was already read under the signed, read-only
# grant, so this only transcribes what governance already authorized. Needs a vision-capable model
# (e.g. Azure gpt-4o / gpt-4.1) and PyMuPDF (``pip install pymupdf``) to rasterize PDF pages.
# --------------------------------------------------------------------------------------------------
_VISION_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
MAX_VISION_PAGES = 10
_OCR_SYSTEM = ("You are an OCR engine. Return the exact text content of the image; do not summarize, "
               "translate, or add commentary.")
_OCR_PROMPT = ("Transcribe ALL text visible in this invoice image, verbatim, preserving structure "
               "(vendor, invoice #, PO/AFE, dates, line items, amounts, tax, state). Output only the "
               "transcription.")


def _looks_scanned(text) -> bool:
    """True when the governed read recovered essentially no text (a scanned/image-only document)."""
    return len((text or "").strip()) < 40


def _document_images(doc: Path):
    """The document's page images as ImageRefs: the file itself when it's an image, else each PDF page
    rendered to PNG (needs PyMuPDF). Empty when neither applies."""
    ext = doc.suffix.lower()
    if ext in _VISION_IMAGE_EXTS:
        return [ImageRef.from_path(str(doc))]
    if ext == ".pdf":
        try:
            import fitz  # PyMuPDF
        except Exception:
            return []
        try:
            refs = []
            pdf = fitz.open(str(doc))
            for i, page in enumerate(pdf):
                if i >= MAX_VISION_PAGES:
                    break
                refs.append(ImageRef.from_bytes(page.get_pixmap(dpi=200).tobytes("png"), mime="image/png"))
            pdf.close()
            return refs
        except Exception:
            return []
    return []


def vision_transcribe(doc: Path, provider) -> str:
    """OCR a scanned/image document with the vision model, page by page (metered). Returns the
    combined transcription, or '' when the provider can't see images or no page images exist."""
    if not provider.supports_vision():
        return ""
    images = _document_images(doc)
    if not images:
        return ""
    pages = []
    for i, img in enumerate(images, 1):
        try:
            with usage_label(f"vision_ocr:p{i}"):
                page_text = provider.complete_vision(_OCR_PROMPT, [img], system=_OCR_SYSTEM)
        except Exception:
            page_text = ""
        if page_text and page_text.strip():
            pages.append(page_text.strip())
    return "\n\n".join(pages)


# --------------------------------------------------------------------------------------------------
# Reasoning steps. Every call goes through the single provider seam; each embeds a unique
# ``STEP: <NAME>`` marker (used by the offline demo answer key) and returns parsed JSON.
# --------------------------------------------------------------------------------------------------
def _ask(provider, label, system, prompt) -> dict:
    with usage_label(label):
        raw = provider.complete(prompt, system=system)
    return extract_json(raw) or {}


def extract_invoice_fields(provider, text) -> dict:
    keys = "\n".join(f'  "{k}": ""' for k in INVOICE_FIELDS)
    prompt = (
        "STEP: EXTRACT_INVOICE\n"
        "Extract these fields from the INVOICE and return a JSON object with EXACTLY these keys "
        "(use an empty string when the invoice does not state a value). Amounts as plain numbers, "
        "no currency symbols or thousands separators.\n"
        f"{{\n{keys}\n}}\n"
        "RULES: copy names, numbers, PO#, AFE#, and the site number EXACTLY as written; never invent "
        "a value not in the invoice.\n\n"
        f"INVOICE:\n{text[:14000]}\n\nJSON:"
    )
    data = _ask(provider, "extract_invoice", _EXTRACT_SYS, prompt)
    return {k: str(data.get(k, "")).strip() for k in INVOICE_FIELDS}


def classify_asset(provider, fields) -> dict:
    prompt = (
        "STEP: ASSET_CATEGORY\n"
        "Given this convenience-store capital invoice, identify the ASSET actually being created "
        "(e.g. fuel dispenser, canopy, signage, underground storage tank, refrigeration, remodel, "
        "repair part). Return JSON: "
        '{"asset_category": "", "confidence": 0.0, "rationale": ""}.\n\n'
        f"INVOICE FIELDS:\n{json.dumps(fields, indent=2)}\n\nJSON:"
    )
    return _ask(provider, "asset_category", _JUDGE_SYS, prompt)


# --------------------------------------------------------------------------------------------------
# Per-line pipeline (diagram steps 3-5): extract line items, then classify EACH line CapEx/OpEx,
# apply tax rules to EACH line, and score EACH line's confidence -> high auto-posts, low/exception
# routes to SME review. Batched (one call per stage returns an array) to keep it token-efficient.
# --------------------------------------------------------------------------------------------------
LINE_KEYS = ("description", "quantity", "unit_price", "amount")


def _align(data, n) -> list:
    """Coerce a model reply into exactly ``n`` dict rows aligned to the input lines."""
    raw = data.get("lines") if isinstance(data, dict) else (data if isinstance(data, list) else [])
    out = [r if isinstance(r, dict) else {} for r in (raw or [])]
    out += [{} for _ in range(max(0, n - len(out)))]
    return out[:n]


def extract_line_items(provider, text, header) -> list:
    """Extract EVERY line item from the invoice as structured rows (skip subtotal/tax/total)."""
    prompt = (
        "STEP: EXTRACT_LINES\n"
        "Extract EVERY line item from the INVOICE as a JSON object {\"lines\": [...]}, one entry per "
        "line, each EXACTLY {\"description\": \"\", \"quantity\": \"\", \"unit_price\": \"\", "
        "\"amount\": \"\"}. Amounts/prices as plain numbers (no symbols or thousands separators). "
        "Copy descriptions verbatim; INCLUDE labor/service lines and surcharges. Do NOT include "
        "subtotal/tax/total summary rows.\n\n"
        f"INVOICE:\n{text[:14000]}\n\nJSON:"
    )
    data = _ask(provider, "extract_lines", _EXTRACT_SYS, prompt)
    raw = data.get("lines") if isinstance(data, dict) else (data if isinstance(data, list) else [])
    lines = []
    for it in (raw or []):
        if isinstance(it, dict) and str(it.get("description", "")).strip():
            lines.append({k: str(it.get(k, "")).strip() for k in LINE_KEYS})
    return lines


def classify_lines(provider, header, lines, ref) -> list:
    """Diagram step 3 - classify EACH line CapEx vs OpEx and validate its task. When the governed
    reference data is present the model ALSO maps each line to one standardized ITEM TYPE (used for
    the tax matrix) and the best-fitting TASK CODE (whose cap_eligible flag then AUTHORITATIVELY
    decides CapEx vs OpEx in build_line_results)."""
    listing = "\n".join(
        f"{i + 1}. {ln['description']}  (qty {ln.get('quantity') or '-'}, amount {ln.get('amount') or '-'})"
        for i, ln in enumerate(lines)
    )
    itypes = refdata.item_types(ref)
    catalog = refdata.task_code_catalog(ref)
    descs = refdata.item_type_descriptors(ref)
    po_rec, po_score, po_how = refdata.match_po(
        ref,
        invoice_number=header.get("invoice_number", ""),
        po_number=header.get("po_number", ""),
        vendor_name=header.get("vendor_name", ""),
    )
    itype_block = ("\n\nITEM TYPES (map each line to EXACTLY one, copied verbatim; use the hints to pick "
                   "the RIGHT bucket):\n"
                   + "\n".join(f"- {t}: {descs.get(t, '')}" for t in itypes)) if itypes else ""
    tc_block = ("\n\nTASK CODES (pick the single best-fitting code for each line, or \"\" if none):\n"
                + "\n".join(f"- {c}: {d} ({'CapEx-eligible' if cap else 'expense/OpEx'})" for c, d, cap in catalog)) if catalog else ""
    itype_field = ', "item_type": ""' if itypes else ""
    tc_field = ', "task_code": ""' if catalog else ""
    extra = ("map it to ONE standardized item type (for tax) and pick the best TASK CODE, "
             if (itypes or catalog) else "")
    itype_rule = ("When choosing the item type: physical alarm/security devices and their dedicated "
                  "cabling/hardware are 'Security & Surveillance Systems'; separately stated travel, "
                  "survey, consulting, and non-capital installation services are 'Professional Services'; "
                  "freight, shipping, delivery, handling, and surcharges are 'Freight & Delivery'. "
                  if itypes else "")
    po_context = ""
    if po_rec is not None and po_how in {"id", "id+vendor"}:
        po_context = ("\n\nMATCHED PO REFERENCE (use as context; invoice facts still control):\n"
                      + json.dumps({
                          "po_number": po_rec.get("po_number"),
                          "description": po_rec.get("description"),
                          "line_descriptions": po_rec.get("line_descriptions"),
                          "task_code": po_rec.get("task_code"),
                          "asset_class": po_rec.get("asset_class"),
                          "match_score": po_score,
                      }, indent=2))
    prompt = (
        "STEP: CLASSIFY_LINES\n"
        "For EACH numbered invoice line, classify CapEx vs OpEx, validate the capitalization task, "
        f"{extra}"
        "then explain briefly. Creating/installing a NEW asset - or a component or installation labor "
        "capitalized INTO it - is CapEx; a like-for-like repair, routine maintenance, or a consumable "
        f"is OpEx. Do NOT just accept an existing 'repair' code. {itype_rule}Return JSON {{\"lines\": [...]}} with ONE "
        "entry per line IN THE SAME ORDER, each: "
        '{"capex_opex": "CapEx"|"OpEx", "asset_category": "", "suggested_task": "", '
        '"existing_task_ok": true' + itype_field + tc_field + ', "confidence": 0.0, "rationale": ""}.'
        f"{itype_block}{tc_block}"
        f"{po_context}\n\nINVOICE HEADER:\n{json.dumps(header, indent=2)}\n\nLINES:\n{listing}\n\nJSON:"
    )
    data = _ask(provider, "classify_lines", _JUDGE_SYS, prompt)
    model_rows = _align(data, len(lines))
    reference_rows = refdata.reference_classifications(ref, header, lines)
    required = ("capex_opex", "asset_category", "item_type", "task_code", "confidence", "rationale")
    for model_row, reference_row in zip(model_rows, reference_rows):
        if not reference_row:
            continue
        for key in required:
            if model_row.get(key) in (None, ""):
                model_row[key] = reference_row.get(key)
        if model_row.get("existing_task_ok") is None:
            model_row["existing_task_ok"] = reference_row.get("existing_task_ok")
        if all(model_row.get(key) == reference_row.get(key) for key in ("item_type", "task_code")):
            model_row["_reference_fallback"] = True
    return model_rows


def tax_lines(provider, header, lines, classifications) -> list:
    """Diagram step 4 - apply sales/use-tax rules to EACH line; flag per-line misclassification."""
    listing = "\n".join(
        f"{i + 1}. {ln['description']}  (amount {ln.get('amount') or '-'}"
        f"; {classifications[i].get('capex_opex', '') if i < len(classifications) else ''})"
        for i, ln in enumerate(lines)
    )
    prompt = (
        "STEP: TAX_LINES\n"
        "Apply sales/use-tax rules to EACH numbered line for the ship-to state. For each line decide "
        "whether it is taxable, the expected rate, and the expected tax on the line amount, and FLAG "
        "an exception when the line's tax treatment looks MISCLASSIFIED (e.g. taxable tangible "
        "property treated as exempt, exempt service taxed, or bundled labor that is taxable in this "
        "state). Circle K self-assesses use tax; it does NOT go back to the vendor. Return JSON "
        "{\"lines\": [...]} with ONE entry per line IN THE SAME ORDER, each: "
        '{"taxable": true, "jurisdiction_state": "", "expected_tax_rate": 0.0, '
        '"expected_tax_amount": 0.0, "exception": false, "exception_reason": "", '
        '"confidence": 0.0, "rationale": ""}.\n\n'
        f"INVOICE HEADER (state={header.get('state', '')}, tax_charged={header.get('tax_charged', '')}):\n"
        f"{json.dumps(header, indent=2)}\n\nLINES:\n{listing}\n\nJSON:"
    )
    data = _ask(provider, "tax_lines", _JUDGE_SYS, prompt)
    return _align(data, len(lines))


def apply_tax_matrix(header, classifications, ref) -> list:
    """Diagram step 4, RULES-FIRST: taxable Y/N + rate come from the GOVERNED taxability matrix keyed
    by (ship-to state x item_type) - deterministic, no model call. 'A' (ambiguous) and unmapped
    cells raise a per-line exception (-> SME). Returns one dict per line aligned to classifications."""
    state = (header.get("state") or "").strip().upper()
    out = []
    for c in classifications:
        itype = (c or {}).get("item_type", "")
        verdict, label, rate = refdata.taxability(ref, state, itype)
        e = {"jurisdiction_state": state, "item_type": itype, "tax_verdict": verdict,
             "tax_verdict_label": label, "expected_tax_rate": rate, "tax_basis": "",
             "exception": False, "exception_reason": "", "taxable": None, "confidence": 0.0,
             "rationale": ""}
        if verdict == "T":
            e.update(taxable=True, confidence=0.97,
                     tax_basis=f"matrix {state}/{itype} = Taxable @ {rate}",
                     rationale=f"{itype} is taxable in {state} per the taxability matrix.")
        elif verdict == "E":
            e.update(taxable=False, expected_tax_rate=0.0, confidence=0.97,
                     tax_basis=f"matrix {state}/{itype} = Exempt",
                     rationale=f"{itype} is exempt in {state} per the taxability matrix.")
        elif verdict == "A":
            e.update(taxable=None, exception=True, confidence=0.5,
                     tax_basis=f"matrix {state}/{itype} = Ambiguous",
                     exception_reason=f"{itype} in {state} is AMBIGUOUS in the taxability matrix - requires SME review.")
        else:
            e.update(taxable=None, exception=True, confidence=0.4,
                     tax_basis=f"no matrix entry for {state}/{itype or '(unmapped)'}",
                     exception_reason=f"No taxability rule for {state}/{itype or '(unmapped item type)'} - requires review.")
        out.append(e)
    return out


def _num(v):
    """Parse a currency/number string to float, or None."""
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def build_line_results(lines, classifications, taxes, threshold: float, header: dict, ref: dict) -> list:
    """Merge the per-line extraction + classification + tax into one result per line. RULES-FIRST:
    when a line's TASK CODE resolves in the governed task-code master, its ``cap_eligible`` flag
    AUTHORITATIVELY sets CapEx vs OpEx (with asset class + depreciation); the model's guess is only
    a fallback. Expected tax = matrix rate x line amount when the matrix says taxable. Per-line
    confidence is the worst of the two judges; the route follows (diagram step 5). Then the invoice's
    actually-charged tax is allocated across taxable lines so each shows charged-vs-expected + delta."""
    # Diagram STEP 5 - CAPITALIZATION RULES (logic in code, $2k / $100k thresholds). The capital
    # ASSET/PROJECT total (cap-eligible, non-period-cost lines) decides capitalization: capitalize
    # only when it reaches $2,000 (de-minimis expense below), and flag a MAJOR project at $100,000.
    cap_project_total = 0.0
    for i, ln in enumerate(lines):
        c = classifications[i] if i < len(classifications) else {}
        tr = refdata.task_lookup(ref, str(c.get("task_code", "")).strip())
        if tr and tr.get("cap_eligible") and not _PERIOD_COST_RE.search(ln.get("description", "") or ""):
            cap_project_total += _num(ln.get("amount")) or 0.0
    capitalize_project = cap_project_total >= CAP_THRESHOLD
    major_project = cap_project_total >= MAJOR_PROJECT_THRESHOLD

    out = []
    for i, ln in enumerate(lines):
        c = classifications[i] if i < len(classifications) else {}
        t = taxes[i] if i < len(taxes) else {}
        amount = _num(ln.get("amount"))

        # --- Diagram STEP 5: CapEx vs OpEx via capitalization rules + the task-code master -------- #
        model_capex = str(c.get("capex_opex", "")).strip()
        task_code = str(c.get("task_code", "")).strip()
        task_rec = refdata.task_lookup(ref, task_code)
        asset_class = c.get("asset_category", "")
        useful_life = depreciation = None
        period_cost = bool(_PERIOD_COST_RE.search(ln.get("description", "") or ""))
        if task_rec is not None:
            asset_class = task_rec.get("asset_class") or asset_class
            useful_life = task_rec.get("useful_life_months")
            depreciation = task_rec.get("depreciation")
            cap_ok = bool(task_rec.get("cap_eligible"))
            if period_cost:
                # freight / fuel surcharge / travel / handling -> period expense, never capitalized;
                # realign to the services expense code so the asset/depreciation stay coherent.
                capex_opex, capex_basis = "OpEx", "period cost (freight/surcharge/travel) - expensed"
                svc = refdata.task_lookup(ref, "TC-9030")
                if svc and cap_ok:
                    task_code, asset_class = "TC-9030", svc.get("asset_class") or asset_class
                    useful_life, depreciation = svc.get("useful_life_months"), svc.get("depreciation")
            elif not cap_ok:
                capex_opex, capex_basis = "OpEx", f"task {task_rec.get('code')} (cap_eligible=False)"
            elif not capitalize_project:
                capex_opex = "OpEx"
                capex_basis = (f"below ${int(CAP_THRESHOLD):,} capitalization threshold "
                               f"(project ${cap_project_total:,.0f}) - expensed")
            else:
                capex_opex = "CapEx"
                capex_basis = (f"task {task_rec.get('code')} cap-eligible; project "
                               f"${cap_project_total:,.0f} >= ${int(CAP_THRESHOLD):,}")
            capex_conflict = bool(model_capex and model_capex.lower() != capex_opex.lower())
        else:
            capex_opex = "OpEx" if period_cost else model_capex
            capex_basis = "period cost - expensed" if period_cost else "AI (no task-code match)"
            capex_conflict = False

        # --- tax: matrix verdict (rules-first) -> taxable + rate + expected ---------------------- #
        taxable = t.get("taxable")
        rate = t.get("expected_tax_rate")
        expected = round(rate * amount, 2) if (taxable and rate and amount) else (0.0 if taxable is False else None)
        tax_exception = bool(t.get("exception"))

        # --- Diagram STEP 9: DUAL VALIDATION (LLM tax assessment vs the tax engine) -------------- #
        # Only when the two verdicts AGREE may a line consider auto-approve; a DIVERGENCE (engine says
        # exempt but the model reads it taxable, or vice-versa) or an ambiguous engine call stops the
        # line at a named analyst. This is the gate the flow diagram makes central - without it the
        # matrix verdict alone would (wrongly) assert "over-collected / seek credit" when the vendor's
        # charged tax is real evidence the item is taxable.
        matrix_on = bool(ref.get("taxability"))
        llm_taxable = c.get("_llm_tax_taxable")
        tax_dual = None
        if matrix_on and llm_taxable is not None:
            if taxable is None:
                tax_dual = "ambiguous"           # engine could not decide (already an exception)
            elif bool(taxable) == bool(llm_taxable):
                tax_dual = "agree"
            else:
                tax_dual = "diverge"
        tax_divergence = (tax_dual == "diverge")

        conf = min(_confidence(c), _confidence(t))
        # Semantic second opinion (validator): the model's pick stays authoritative; the embedder's
        # deterministic nearest entry either CONFIRMS it or, when it disagrees in a way that changes
        # the taxability verdict, forces the line to SME review.
        sem = c.get("_sem")
        llm_item = c.get("item_type", "")
        llm_task = str(c.get("task_code", "")).strip()
        tax_mapping_conflict = False
        if sem:
            sem_item, sem_task = sem.get("item_type"), sem.get("task_code")
            item_conflict = bool(sem_item and llm_item and sem_item != llm_item)
            task_conflict = bool(sem_task and llm_task and llm_task != sem_task)
            mapping_conflict = item_conflict or task_conflict
            if item_conflict:
                state = (header.get("state") or "").strip().upper()
                llm_verdict = refdata.taxability(ref, state, llm_item)[0]
                sem_verdict = refdata.taxability(ref, state, sem_item)[0]
                tax_mapping_conflict = bool(llm_verdict != sem_verdict)
            agree = "confirmed" if not mapping_conflict else "DISAGREES"
            mapping_basis = (f"LLM pick, semantic {agree} (index nearest: item {sem_item} "
                             f"{sem.get('item_score')}, task {sem_task} {sem.get('task_score')})")
        else:
            mapping_conflict = False
            mapping_basis = "LLM pick"
        route = ("SME_REVIEW" if (conf < threshold or tax_exception or capex_conflict
                                  or tax_mapping_conflict or tax_divergence)
                 else "AUTO_POST")
        out.append({
            "n": i + 1,
            "description": ln.get("description", ""),
            "quantity": ln.get("quantity", ""),
            "amount": amount,
            "amount_raw": ln.get("amount", ""),
            "capex_opex": capex_opex,
            "capex_basis": capex_basis,
            "capex_conflict": capex_conflict,
            "mapping_basis": mapping_basis,
            "mapping_conflict": mapping_conflict,
            "tax_mapping_conflict": tax_mapping_conflict,
            "tax_dual": tax_dual,
            "tax_divergence": tax_divergence,
            "llm_tax_taxable": llm_taxable,
            "llm_tax_reason": c.get("_llm_tax_reason", ""),
            "cache": c.get("_cache"),
            "asset_category": asset_class,
            "task_code": task_code,
            "useful_life_months": useful_life,
            "depreciation": depreciation,
            "suggested_task": c.get("suggested_task", ""),
            "existing_task_ok": c.get("existing_task_ok"),
            "class_rationale": c.get("rationale", ""),
            "item_type": t.get("item_type") or c.get("item_type", ""),
            "taxable": taxable,
            "tax_verdict": t.get("tax_verdict"),
            "tax_verdict_label": t.get("tax_verdict_label"),
            "tax_basis": t.get("tax_basis", ""),
            "jurisdiction_state": t.get("jurisdiction_state", ""),
            "expected_tax_rate": rate,
            "expected_tax_amount": expected,
            "tax_exception": tax_exception,
            "tax_exception_reason": t.get("exception_reason", ""),
            "tax_rationale": t.get("rationale", ""),
            "confidence": round(conf, 3),
            "route": route,
        })

    # Allocate the invoice's charged tax across ALL lines pro-rata by line AMOUNT (the vendor's tax
    # base is the invoice value). This localizes a discrepancy to the RIGHT line: an exempt line that
    # still received a share of charged tax shows it as over-collected on that line; a taxable line
    # shows only the small rate delta. Each line's delta = expected - allocated charged (positive =
    # under-collected -> use tax owed on that line).
    charged_total = _num(header.get("tax_charged")) or 0.0
    total_amt_all = sum(r["amount"] for r in out if r["amount"])
    for r in out:
        share = (r["amount"] / total_amt_all) if (total_amt_all > 0 and r["amount"]) else 0.0
        alloc = round(charged_total * share, 2)
        exp = r["expected_tax_amount"] or 0.0
        r["charged_tax_alloc"] = alloc
        r["tax_delta"] = round(exp - alloc, 2)
        # Posting basis (step 13): where the cost lands + the use tax to self-assess on this line.
        if str(r["capex_opex"]).lower() == "capex":
            r["posting_target"] = f"capitalize to asset: {r['asset_category'] or '(asset)'}"
        elif str(r["capex_opex"]).lower() == "opex":
            r["posting_target"] = "expense to GL"
        else:
            r["posting_target"] = "(unclassified)"
        r["use_tax_to_allocate"] = round(max(0.0, r["tax_delta"]), 2)  # >0 = self-assess on this line
    return out


def summarize_lines(line_results, header) -> dict:
    """Invoice-level rollup of the per-line results: CapEx/OpEx totals, expected vs charged tax,
    and a full tax RECONCILIATION that flags BOTH under-collection (self-assess use tax) AND
    over-collection (vendor charged tax on exempt/lower-rate items -> verify classification / seek
    credit). A material mismatch either way is an exception."""
    capex_total = sum(r["amount"] for r in line_results if r["amount"] and str(r["capex_opex"]).lower() == "capex")
    opex_total = sum(r["amount"] for r in line_results if r["amount"] and str(r["capex_opex"]).lower() == "opex")
    # Diagram step 5: a capital project at/above $100k is a MAJOR project (AFE/board approval) -> review.
    major_project = capex_total >= MAJOR_PROJECT_THRESHOLD
    expected_tax = round(sum(r["expected_tax_amount"] for r in line_results
                             if r["expected_tax_amount"] and r.get("taxable")), 2)
    charged = round(_num(header.get("tax_charged")) or 0.0, 2)
    # Tolerance: $1 or 2% of the larger side, so tiny rounding doesn't raise a false exception.
    tol = max(1.0, 0.02 * max(expected_tax, charged))
    use_tax_owed = max(0.0, round(expected_tax - charged, 2))      # under-collected -> self-assess
    over_collected = max(0.0, round(charged - expected_tax, 2))    # over-collected -> verify/credit
    if use_tax_owed > tol:
        tax_status = "under_collected"
    elif over_collected > tol:
        tax_status = "over_collected"
    else:
        tax_status = "balanced"
    tax_recon_exception = tax_status != "balanced"
    # The expected-tax figure is only as trustworthy as the item-type classifications behind it.
    # When lines carry an unresolved tax-relevant mapping conflict (the model and the semantic index
    # disagree on a taxability-driving item type) or an ambiguous matrix verdict, the over/under
    # conclusion is PROVISIONAL - resolve the classification before asserting a credit or accrual.
    n_tax_mapping_conflict = sum(1 for r in line_results if r.get("tax_mapping_conflict"))
    n_ambiguous = sum(1 for r in line_results if str(r.get("tax_verdict") or "").upper().startswith("A"))
    # Diagram step 9: count lines where the LLM tax assessment and the tax engine DIVERGED. Any
    # divergence means the invoice's taxability itself is disputed, so the over/under-collection
    # figure is UNRESOLVED - it must go to a named analyst, not be asserted as a credit/accrual.
    n_tax_divergence = sum(1 for r in line_results if r.get("tax_divergence"))
    tax_unresolved = bool(tax_recon_exception and n_tax_divergence)
    tax_provisional = bool(tax_recon_exception and (n_tax_mapping_conflict or n_ambiguous or n_tax_divergence))
    n_exc = sum(1 for r in line_results if r["tax_exception"]) + (1 if tax_recon_exception else 0)
    n_sme = sum(1 for r in line_results if r["route"] == "SME_REVIEW")
    return {
        "capex_total": round(capex_total, 2),
        "opex_total": round(opex_total, 2),
        "major_project": major_project,
        "cap_threshold": CAP_THRESHOLD,
        "expected_tax_total": expected_tax,
        "tax_charged": charged,
        "use_tax_owed": use_tax_owed,
        "over_collected": over_collected,
        "tax_status": tax_status,
        "tax_recon_exception": tax_recon_exception,
        "tax_provisional": tax_provisional,
        "tax_unresolved": tax_unresolved,
        "n_tax_divergence": n_tax_divergence,
        "n_tax_mapping_conflict": n_tax_mapping_conflict,
        "tax_tolerance": round(tol, 2),
        "tax_shortfall": bool(use_tax_owed > 0.01),
        "n_lines": len(line_results),
        "n_exceptions": n_exc,
        "n_sme": n_sme,
        "route": "SME_REVIEW" if (n_sme or tax_recon_exception or major_project) else "AUTO_POST",
    }


def _confidence(step: dict) -> float:
    try:
        return max(0.0, min(1.0, float(step.get("confidence", 0.0))))
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------------------------------
# Evaluation. The framework OWNS the evaluators (quality_panel / safety_panel / RubricJudge); this
# code only supplies the task inputs and the rubrics. Every judge runs on the SAME provider seam, so
# the Microsoft Agent Framework drives the judging too - and each judge's call is metered for cost.
# --------------------------------------------------------------------------------------------------
def evaluate_quality(provider, fields: dict, line_results: list, rollup: dict, text: str):
    """Quality panel: completeness + groundedness (deterministic) plus accuracy and per-line
    determination-soundness LLM judges and a format check. Returns a PanelReport."""
    payload = json.dumps(fields, ensure_ascii=False)
    grounded_values = ". ".join(str(fields[k]) for k in INVOICE_FIELDS if k not in GROUNDED_EXEMPT and fields.get(k))
    acc_item = (
        f"INVOICE (excerpt):\n{text[:8000]}\n\n---\nEXTRACTION:\n{json.dumps(fields, indent=2, ensure_ascii=False)}"
    )
    sound_item = json.dumps({"lines": line_results, "rollup": rollup}, ensure_ascii=False, default=str)
    panel = quality_panel(
        source=text,
        required=REQUIRED_FIELDS,
        judges={
            "accuracy": RubricJudge(provider, threshold=0.6, name="accuracy", rubric=_ACCURACY_RUBRIC),
            "soundness": RubricJudge(provider, threshold=0.6, name="soundness", rubric=_SOUNDNESS_RUBRIC),
        },
        extra={
            "format": AssertionEvaluator([
                ("total_amount is numeric", lambda s: _is_number(json.loads(s).get("total_amount"))),
                ("description is present", lambda s: len(str(json.loads(s).get("description", ""))) >= 3),
            ])
        },
    )
    items = {
        "completeness": payload,
        "groundedness": grounded_values,
        "accuracy": acc_item,
        "soundness": sound_item,
        "format": payload,
    }
    return panel.evaluate(items)


def evaluate_safety(provider, fields: dict, line_results: list, text: str, governed_ok: bool):
    """Safety panel: deterministic prompt-injection + PII scans, an LLM harmful-content judge, and a
    governance assertion that the acting agent was proven unable to write or delete. Returns a
    PanelReport."""
    output_text = ". ".join(str(v) for v in fields.values() if v)
    panel = safety_panel(
        model=provider,
        harm_rubric=_HARM_RUBRIC,
        extra={"governance": AssertionEvaluator([("agent proven unable to write or delete", bool)])},
    )
    items = {
        "governance": governed_ok,
        "prompt_injection": text,
        "pii_exposure": output_text,
        "harmful_content": json.dumps({"fields": fields, "lines": line_results}, ensure_ascii=False, default=str),
    }
    return panel.evaluate(items)


def evaluate_field_verdicts(provider, fields: dict, text: str) -> dict:
    """One LLM call returning a PER-FIELD verdict for each populated invoice field:
    ``{field: {"status": "ok"|"warning"|"fail", "reason": ...}}`` grounded ONLY in the invoice.
    Fail-soft to ``{}`` so it never blocks the run."""
    populated = {k: str(v).strip() for k, v in fields.items() if str(v).strip()}
    if not populated:
        return {}
    listing = "\n".join(f"- {k}: {v[:300]}" for k, v in populated.items())
    prompt = (
        "STEP: FIELD_VERDICT\n"
        "Judge EACH extracted field against the INVOICE. Return a JSON object mapping every field name "
        'to {"status": "ok"|"warning"|"fail", "reason": "<short justification>"}:\n'
        "- ok: clearly supported by the invoice and in the correct field.\n"
        "- warning: only partially supported, imprecise, or possibly misplaced.\n"
        "- fail: contradicted by, or absent from, the invoice (likely invented).\n"
        "Use ONLY the invoice; do not rely on outside knowledge.\n\n"
        f"EXTRACTED FIELDS:\n{listing}\n\nINVOICE:\n{text[:12000]}\n\nJSON:"
    )
    data = _ask(provider, "field_verdict", "You are a meticulous verification judge. Output ONLY one JSON object.", prompt)
    out = {}
    for k, v in (data or {}).items():
        if isinstance(v, dict):
            out[str(k)] = {"status": str(v.get("status", "")).lower().strip(), "reason": str(v.get("reason", "")).strip()}
        elif isinstance(v, str):
            out[str(k)] = {"status": v.lower().strip(), "reason": ""}
    return out


def _is_number(v) -> bool:
    try:
        float(str(v))
        return True
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------------------------------
# Table renderers (per-field verdict, token usage, per-call cost) - ported from examples/extract.py.
# --------------------------------------------------------------------------------------------------
def _verdict_table(rows) -> str:
    """Aligned per-field verdict table: field | present | grounded | judge | reason (reason wraps)."""
    import shutil
    import textwrap
    header = ("field", "present", "grounded", "judge", "reason")
    term_w = shutil.get_terminal_size((120, 40)).columns
    fw = min(20, max(len(header[0]), max((len(str(r[0])) for r in rows), default=5)))
    pw = max(len(header[1]), max((len(str(r[1])) for r in rows), default=0))
    gw = max(len(header[2]), max((len(str(r[2])) for r in rows), default=0))
    jw = max(len(header[3]), max((len(str(r[3])) for r in rows), default=0))
    lead = 2 + fw + 1 + pw + 1 + gw + 1 + jw + 1
    rw = max(24, min(80, term_w - lead))

    def line(field, present, grounded, judge, reason):
        rlines = textwrap.wrap(str(reason), rw) or [""]
        first = f"  {str(field)[:fw].ljust(fw)} {str(present).center(pw)} {str(grounded).center(gw)} {str(judge).center(jw)} {rlines[0]}"
        rest = [(" " * lead) + rl for rl in rlines[1:]]
        return "\n".join([first] + rest)

    out = [line(*header), "  " + "\u2500" * min(term_w - 2, lead + rw)]
    out.extend(line(*r) for r in rows)
    return "\n".join(out)


_EVAL_PHASES = ("quality_judges", "safety_judges", "field_verdict", "accuracy", "soundness", "harmful_content")


def _call_kind(label) -> str:
    return "eval" if str(label) in _EVAL_PHASES else "main"


def _fmt_clock(t) -> str:
    if not t:
        return "-"
    import datetime
    d = datetime.datetime.fromtimestamp(t)
    return d.strftime("%H:%M:%S.") + f"{d.microsecond // 1000:03d}"


def _fmt_dur(sec) -> str:
    if not sec or sec <= 0:
        return "-"
    return f"{sec:.2f}s" if sec >= 1 else f"{int(sec * 1000)}ms"


def _usage_table(rows, total) -> str:
    """model | calls | input | output | est. cost (USD)."""
    out = [f"  {'model':24} {'calls':>5} {'input':>11} {'output':>11} {'est.$':>10}", "  " + "\u2500" * 64]
    for m, calls, pin, pout, cost in rows:
        out.append(f"  {str(m)[:24]:24} {calls:>5} {pin:>11,} {pout:>11,} {cost:>10.4f}")
    calls, pin, pout, cost, est = total
    out.append("  " + "\u2500" * 64)
    out.append(f"  {'TOTAL':24} {calls:>5} {pin:>11,} {pout:>11,} {cost:>10.4f}")
    if est:
        out.append("  * some token counts estimated (the API/runtime returned no usage)")
    return "\n".join(out)


def _usage_calls_table(rows) -> str:
    """# | kind | phase | start | end | dur | input | output | est. cost (USD)."""
    out = [f"  {'#':>3} {'kind':4} {'phase':22} {'start':12} {'end':12} {'dur':>7} {'input':>8} {'output':>8} {'est.$':>8}",
           "  " + "\u2500" * 98]
    for n, kind, phase, model, start, end, dur, pin, pout, cost, est in rows:
        star = "*" if est else " "
        out.append(f"  {n:>3} {str(kind):4} {str(phase)[:22]:22} {start:12} {end:12} {dur:>7} {pin:>8,} {pout:>8,} {cost:>8.4f}{star}")
    return "\n".join(out)


def _collect_usage():
    """Pull the per-model and per-call usage+cost from the framework's meter, priced via PriceBook."""
    meter = get_usage_meter()
    pb = PriceBook()
    rows = [
        (m, d["calls"], d["prompt_tokens"], d["completion_tokens"],
         pb.token_cost(m, d["prompt_tokens"], d["completion_tokens"]))
        for m, d in sorted(meter.by_model().items())
    ]
    tot = meter.totals()
    total = (tot["calls"], tot["prompt_tokens"], tot["completion_tokens"], meter.cost(pb), tot["any_estimated"])
    calls = [
        (n + 1, _call_kind(c.label), (c.label or c.source or "-"), c.model,
         _fmt_clock(c.started), _fmt_clock(c.ended or c.ts), _fmt_dur(c.duration),
         c.prompt_tokens, c.completion_tokens,
         pb.token_cost(c.model, c.prompt_tokens, c.completion_tokens), c.estimated)
        for n, c in enumerate(sorted(meter.calls, key=lambda c: (c.started or c.ended or c.ts or 0.0)))
    ]
    return {"rows": rows, "total": total, "calls": calls}


# --------------------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------------------
def run(provider, text: str, agent, read_result, guarantee_ok: bool, threshold: float,
        embed_spec=None, di=None, cache_path=None) -> dict:
    # Governed, read-only load of the reference data (taxability matrix, task codes, PO master,
    # history) - each read is signed and the loader is proven unable to write/delete.
    ref, ref_gov = refdata.load_reference()

    # Optional DETERMINISTIC semantic mapper: embed the task/item-type catalog once so line ->
    # task_code / item_type is reproducible (same input -> same answer) instead of an LLM free-pick.
    embedder, sem_index, embed_label = None, None, None
    if embed_spec:
        try:
            from autarch.intelligence.factory import build_embedder
            embedder = build_embedder(embed_spec)
            sem_index = refdata.build_semantic_index(ref, embedder)
            if sem_index:
                _mode = "lexical" if "hash" in str(embed_spec).lower() else "learned"
                embed_label = f"{embed_spec} ({_mode})"
        except Exception as exc:  # noqa: BLE001
            print(f"  (semantic mapping unavailable: {type(exc).__name__}: {exc}; using LLM pick)")

    # Document Intelligence results (when --doci ran): its 'prebuilt-invoice' output is authoritative
    # for the header fields + line items it extracted, each carrying a real confidence score.
    di = di or {}
    di_header = {k: v for k, v in (di.get("header") or {}).items() if str(v).strip()}
    di_conf = di.get("confidence") or {}
    di_lines = di.get("lines") or []

    # Header (invoice-level) fields for context + reconciliation. The LLM extract provides the full
    # field set (incl. AFE#/site# that DI's invoice model doesn't carry); DI then OVERRIDES the
    # fields it lifted with confidence (vendor, invoice#, PO#, totals, and the ship-to state).
    fields = extract_invoice_fields(provider, text)
    for k, v in di_header.items():
        if k in fields:
            fields[k] = str(v).strip()

    # Diagram steps 3-5, PER LINE ITEM: extract lines -> classify each (CapEx/OpEx + item type +
    # task code) -> apply tax rules per line -> score each line's confidence and route. Tax is
    # RULES-FIRST from the governed taxability matrix when it is available; otherwise the model
    # decides taxability directly. DI line items (structured + confidence-scored) are used when
    # present; otherwise the model extracts them from the OCR text.
    if di_lines:
        lines = [{k: str(ln.get(k, "")).strip() for k in LINE_KEYS} for ln in di_lines
                 if str(ln.get("description", "")).strip()]
    else:
        lines = extract_line_items(provider, text, fields)
    classifications = classify_lines(provider, fields, lines, ref) if lines else []

    # Diagram STEP 8 - INDEPENDENT LLM tax assessment. The tax ENGINE (matrix, below) is the rules
    # verdict; this is a SEPARATE model opinion on whether each line is taxable, so step 9 (dual
    # validation) can compare the two. Folded into each classification so the decision cache persists
    # BOTH together -> the agree/diverge gate is reproducible run-to-run.
    llm_tax = tax_lines(provider, fields, lines, classifications) if lines else []
    for i, c in enumerate(classifications):
        if i < len(llm_tax):
            c["_llm_tax_taxable"] = llm_tax[i].get("taxable")
            c["_llm_tax_reason"] = (llm_tax[i].get("rationale") or llm_tax[i].get("exception_reason") or "")

    # DETERMINISTIC decision cache (reproducibility). Azure OpenAI decoding is not deterministic even
    # at temperature 0, so the SAME line can otherwise land on a different item type/task code and
    # flip its tax verdict run-to-run. The cache reuses a previously-persisted classification for any
    # line already seen (same vendor + ship-to state + normalized description) so the determination
    # is identical every run; newly-seen lines are classified by the model and persisted for next
    # time. LLM-quality accuracy on first sight, byte-for-byte reproducibility thereafter.
    cache_stats = None
    if cache_path and classifications:
        try:
            import decision_cache
            _cache = decision_cache.load_cache(cache_path)
            cache_stats = decision_cache.apply_cache(
                _cache, fields, lines, classifications, model_label=getattr(provider, "name", ""))
            decision_cache.save_cache(cache_path, _cache)
            cache_stats["path"] = cache_path
        except Exception as exc:  # noqa: BLE001
            print(f"  (decision cache unavailable: {type(exc).__name__}: {exc})")

    # DETERMINISTIC semantic CROSS-CHECK (validator, never an override). The reasoning model
    # (gpt-4.1) is the more accurate classifier - it understands that "FUEL SURCHARGE" is a freight
    # charge (Professional Services), not fuel equipment, and that "INSTALL ALARM" is labor. A bare
    # nearest-neighbour over the catalog cannot, and letting it OVERRIDE the model demonstrably
    # mis-mapped those lines and fabricated tax. So the embedder's deterministic nearest entry is
    # recorded as an independent second opinion: when it AGREES with the model we gain reproducible
    # confirmation; when it DISAGREES on the taxability-driving item type, build_line_results routes
    # that line to SME review instead of silently trusting either side.
    sem_mode = None
    if sem_index is not None:
        sem_mode = "lexical" if "hash" in str(embed_spec).lower() else "learned"
        for i, ln in enumerate(lines):
            if i >= len(classifications):
                break
            sm = refdata.map_line_semantic(sem_index, ln.get("description", ""), embedder)
            if not sm:
                continue
            c = classifications[i]
            c["_sem"] = sm
            c["_sem_mode"] = sem_mode

    if lines and ref.get("taxability"):
        taxes = apply_tax_matrix(fields, classifications, ref)  # tax ENGINE (deterministic, no model call)
    elif lines:
        taxes = llm_tax  # no matrix -> the LLM assessment IS the verdict (no dual validation possible)
    else:
        taxes = []
    line_results = build_line_results(lines, classifications, taxes, threshold, fields, ref)
    rollup = summarize_lines(line_results, fields)

    # PO/AFE reconciliation: match the invoice to the governed PO master (by PO#/invoice# incl.
    # alt formats and vendor aliases) and flag discrepancies against the committed PO.
    po_rec, po_score, po_how = refdata.match_po(
        ref, fields.get("invoice_number", ""), fields.get("po_number", ""), fields.get("vendor_name", ""))
    po_discrepancy_list = refdata.po_discrepancies(po_rec, fields, line_results)

    # Multi-jurisdiction (step 11): a PO that spans multiple sites/states (or an invoice state that
    # differs from the PO state) needs a jurisdiction allocation -> route to SME (allocation itself
    # is downstream). Detected here; not auto-allocated.
    states_seen = {(r.get("jurisdiction_state") or "").upper() for r in line_results if r.get("jurisdiction_state")}
    states_seen.discard("")
    multi_jurisdiction = bool((po_rec and str(po_rec.get("location_site", "")).upper() == "MULTI") or len(states_seen) > 1)

    # Historical precedent (step 8): similar past decisions for this vendor + item type + state,
    # to calibrate confidence and give an audit-defensible basis.
    from collections import Counter
    itypes = [r.get("item_type") for r in line_results if r.get("item_type")]
    dom_itype = Counter(itypes).most_common(1)[0][0] if itypes else ""
    prec_matches, prec_summary = refdata.precedents(
        ref, fields.get("vendor_name", ""), dom_itype, fields.get("state", ""))

    # Anti-hallucination: extracted header fields must be grounded in the signed source.
    ungrounded = check_grounding(fields, text, exempt=GROUNDED_EXEMPT)
    flagged_map = {f: w for f, v, w in ungrounded}

    # Quality + safety panels WITH LLM judges (accuracy / per-line soundness / harmful-content),
    # plus deterministic completeness/groundedness/injection/PII and a governance assertion. Each
    # judge runs on the same MAF provider seam and is metered for cost.
    with usage_label("quality_judges"):
        quality_report = evaluate_quality(provider, fields, line_results, rollup, text)
    with usage_label("safety_judges"):
        safety_report = evaluate_safety(provider, fields, line_results, text, guarantee_ok)

    # Per-field verdict (one judge call): present / grounded / judge status + reason.
    field_verdicts = evaluate_field_verdicts(provider, fields, text)

    # Confidence: worst LINE wins (fail-closed). Invoice routes to review if any line does, or a
    # tax-reconciliation exception, ungrounded value, failed panel, PO discrepancy, or a
    # multi-jurisdiction allocation need is present.
    line_confs = [r["confidence"] for r in line_results]
    overall = min(line_confs) if line_confs else 0.0
    # Hard blockers force a named analyst regardless of confidence.
    hard_blockers = (rollup["route"] == "SME_REVIEW" or rollup.get("tax_recon_exception")
                     or bool(ungrounded) or bool(po_discrepancy_list) or multi_jurisdiction
                     or not quality_report.passed or not safety_report.passed
                     or rollup.get("major_project"))
    # Diagram STEP 10 - routing confidence tiers: >= 0.85 auto-approve; 0.70-0.85 auto-post WITH a
    # 48-hour review flag; < 0.70 (or any hard blocker) route to a named analyst.
    if hard_blockers or overall < AUTOPOST_FLAG_THRESHOLD:
        route = "HUMAN_REVIEW"
    elif overall < threshold:
        route = "AUTO_POST_FLAGGED"
    else:
        route = "AUTO_APPROVE"

    # Source citations: point each populated header field at its supporting passage in the source.
    citer = Citer(text)
    citations = {}
    for k in INVOICE_FIELDS:
        val = str(fields.get(k, "")).strip()
        if not val:
            continue
        c = citer.cite(val)
        if c is not None:
            citations[k] = {"value": val, "quote": c.text.strip(), "method": c.method,
                            "score": round(c.score, 2), "start": c.start, "end": c.end}

    # Per-field verdict rows: field | present | grounded | judge | reason.
    verdict_rows = []
    for k in INVOICE_FIELDS:
        val = str(fields.get(k, "")).strip()
        if not val:
            continue
        grounded = "n/a" if k in GROUNDED_EXEMPT else ("NO" if k in flagged_map else "yes")
        ver = field_verdicts.get(k) or {}
        status = (ver.get("status") or "").upper() or "?"
        reason = ver.get("reason") or (flagged_map.get(k, "") if grounded == "NO" else "")
        verdict_rows.append((k, "yes", grounded, status, reason))

    return {
        "fields": fields,
        "lines": line_results,
        "rollup": rollup,
        "po": {
            "matched": bool(po_rec),
            "score": po_score,
            "how": po_how,
            "record": {k: po_rec.get(k) for k in ("po_number", "afe_number", "project_name",
                        "vendor_name", "task_code", "asset_class", "location_state", "budget_amount")}
                      if po_rec else None,
            "discrepancies": po_discrepancy_list,
        },
        "multi_jurisdiction": {"flag": multi_jurisdiction, "states": sorted(states_seen)},
        "precedent": {"summary": prec_summary,
                      "matches": [{k: h.get(k) for k in ("invoice_number", "ship_to_state", "item_type",
                                   "routing_result", "overall_confidence", "taxability", "scenario_label")}
                                  for h in prec_matches]},
        "reference": {"loaded": ref_gov.get("loaded", []),
                      "guarantee_read_only": ref_gov.get("guarantee_read_only"),
                      "signed_reads": len(ref_gov.get("why_ids", [])),
                      "semantic_mapping": embed_label},
        "classification_cache": cache_stats,
        "extraction": {"engine": "document_intelligence" if di_header else "ocr+llm",
                       "di_fields": sorted(di_header.keys()),
                       "di_confidence": di_conf,
                       "di_state_source": di.get("state_source", ""),
                       "di_line_count": len(di_lines),
                       "di_pages": di.get("n_pages")},
        "confidence": {"overall": round(overall, 3), "threshold": threshold,
                       "per_line": {r["n"]: r["confidence"] for r in line_results}},
        "routing": route,
        "grounding": {"all_grounded": not ungrounded,
                      "flagged": [{"field": f, "value": v, "why": w} for f, v, w in ungrounded]},
        "safety": {"passed": safety_report.passed, "score": round(safety_report.score, 3),
                   "rows": safety_report.rows(), "skipped": list(safety_report.skipped)},
        "quality": {"passed": quality_report.passed, "score": round(quality_report.score, 3),
                    "rows": quality_report.rows(), "skipped": list(quality_report.skipped)},
        "verdicts": verdict_rows,
        "usage": _collect_usage(),
        "evidence": {
            "why_id": read_result.why_id,
            "provenance_verifies": agent.memory.verify_provenance(read_result.why_id),
            "guarantee_read_only": guarantee_ok,
            "citations": citations,
        },
    }


def _money(v) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v or "-")


def print_report(rep: dict) -> None:
    f = rep["fields"]
    lines, roll = rep["lines"], rep["rollup"]

    banner("INVOICE")
    print(f"  vendor      : {f.get('vendor_name') or '-'}")
    print(f"  invoice #   : {f.get('invoice_number') or '-'}   date: {f.get('invoice_date') or '-'}")
    print(f"  PO / AFE    : {f.get('po_number') or '-'} / {f.get('afe_number') or '-'}")
    print(f"  site / state: {f.get('site_number') or '-'} / {f.get('state') or '-'}")
    print(f"  total       : {_money(f.get('total_amount'))}   tax billed: {_money(f.get('tax_charged'))}")

    ext = rep.get("extraction") or {}
    if ext.get("engine") == "document_intelligence":
        conf = ext.get("di_confidence") or {}
        lo = f"min {min(conf.values()):.2f}" if conf else "n/a"
        print(f"  extraction  : Azure Document Intelligence (prebuilt-invoice) - {ext.get('di_line_count')} "
              f"line(s), field confidence {lo}, state via {ext.get('di_state_source') or '-'}")
    else:
        print("  extraction  : OCR + LLM")

    banner("LINE-ITEM CLASSIFICATION & TAX  (steps 3-5: classify -> tax -> confidence -> route)")
    if not lines:
        print("  (no line items were extracted)")
    for r in lines:
        head = f"  [{r['n']}] {r['description']}"
        amt = _money(r["amount"]) if r["amount"] is not None else (r["amount_raw"] or "-")
        print(f"{head[:74].ljust(74)} {amt:>12}")
        tok = "" if r["existing_task_ok"] is None else ("yes" if r["existing_task_ok"] else "no")
        basis = f"  [{r.get('capex_basis')}]" if r.get("capex_basis") else ""
        cache_tag = {"hit": "  {cached}", "new": "  {new->cached}"}.get(r.get("cache"), "")
        print(f"        step3  {str(r['capex_opex'] or '-'):5}  asset: {r['asset_category'] or '-'}"
              f"   task: {r.get('task_code') or r['suggested_task'] or '-'} (existing OK: {tok}){basis}{cache_tag}")
        if r.get("capex_conflict"):
            print(f"               ** the model guessed a different CapEx/OpEx than the task-code rule")
        if r.get("mapping_basis") and r["mapping_basis"] != "LLM pick":
            if r.get("tax_mapping_conflict"):
                note = "  ** semantic index disagrees on a TAX-RELEVANT item type -> SME review"
            elif r.get("mapping_conflict"):
                note = "  ** semantic index suggests a different mapping (non-material)"
            else:
                note = ""
            print(f"               map: {r['mapping_basis']}{note}")
        taxable = r["taxable"]
        tbasis = f"  [{r.get('tax_basis')}]" if r.get("tax_basis") else ""
        tax_line = (f"        step4  taxable={taxable}  {r.get('item_type') or '-'}"
                    f" @{_pct(r['expected_tax_rate'])}   expected {_money(r['expected_tax_amount'])}"
                    f"   charged~{_money(r.get('charged_tax_alloc'))}   \u0394 {_money(r.get('tax_delta'))}{tbasis}")
        if r["tax_exception"]:
            tax_line += "   ** EXCEPTION"
        print(tax_line)
        if r["tax_exception"] and r["tax_exception_reason"]:
            for ln in _wrap(r["tax_exception_reason"], 84):
                print(f"               {ln}")
        # Diagram step 8+9: independent LLM tax verdict + dual-validation result vs the tax engine.
        dual = r.get("tax_dual")
        if dual:
            eng = "taxable" if r.get("taxable") else ("exempt" if r.get("taxable") is False else "ambiguous")
            llm = "taxable" if r.get("llm_tax_taxable") else "exempt"
            if dual == "agree":
                print(f"        step8-9  dual validation: AGREE (engine={eng}, model={llm})")
            elif dual == "diverge":
                print(f"        step8-9  dual validation: ** DIVERGE - engine={eng} but model reads {llm}"
                      f" -> analyst review (do NOT auto-post/credit)")
                if r.get("llm_tax_reason"):
                    for ln in _wrap(f"model: {r['llm_tax_reason']}", 84):
                        print(f"                 {ln}")
            else:
                print(f"        step8-9  dual validation: engine call is {eng} -> analyst review")
        arrow = "AUTO-POST" if r["route"] == "AUTO_POST" else "SME REVIEW"
        print(f"        step5  confidence {r['confidence']:.2f}  ->  {arrow}")

    banner("INVOICE ROLLUP")
    print(f"  CapEx total     : {_money(roll['capex_total'])}      OpEx total: {_money(roll['opex_total'])}")
    print(f"  capitalization  : project {_money(roll['capex_total'])} vs ${int(roll.get('cap_threshold', CAP_THRESHOLD)):,} "
          f"threshold -> {'CAPITALIZE' if roll['capex_total'] >= roll.get('cap_threshold', CAP_THRESHOLD) else 'expense (de minimis)'}"
          + ("   ** MAJOR PROJECT (AFE/board)" if roll.get('major_project') else ""))
    print(f"  expected tax    : {_money(roll['expected_tax_total'])}   vs charged {_money(roll['tax_charged'])}")
    status = roll.get("tax_status", "balanced")
    unresolved = roll.get("tax_unresolved")
    n_div = roll.get("n_tax_divergence", 0)
    if status == "balanced":
        print(f"  tax reconciliation: balanced (within {_money(roll.get('tax_tolerance'))} tolerance)")
    elif unresolved:
        # Dual-validation gate diverged: the taxability itself is disputed, so the gap is NOT a firm
        # over/under-collection - it goes to a named analyst (per the flow), not a credit/accrual.
        gap = roll['over_collected'] if status == "over_collected" else roll['use_tax_owed']
        print(f"  tax reconciliation: ** DUAL-VALIDATION DIVERGENCE on {n_div} line(s) - the tax engine and "
              f"the model disagree on taxability")
        print(f"                      expected {_money(roll['expected_tax_total'])} vs charged "
              f"{_money(roll['tax_charged'])} (\u0394 {_money(gap)}) is UNRESOLVED -> analyst review; do NOT "
              f"seek credit / self-assess until the taxability is confirmed")
    elif status == "under_collected":
        print(f"  tax reconciliation: ** UNDER-COLLECTED - self-assess use tax {_money(roll['use_tax_owed'])}")
    elif status == "over_collected":
        print(f"  tax reconciliation: ** OVER-COLLECTED {_money(roll['over_collected'])} - vendor charged tax "
              f"the matrix says isn't due; verify classification / seek credit")
    if roll.get("tax_provisional") and not unresolved:
        print(f"                      (PROVISIONAL: {roll.get('n_tax_mapping_conflict', 0)} line(s) have a disputed "
              f"tax-relevant item type; resolve classification in SME review before any credit/accrual)")
    print(f"  lines           : {roll['n_lines']}   tax exceptions: {roll['n_exceptions']}   "
          f"to SME review: {roll['n_sme']}")

    mj = rep.get("multi_jurisdiction") or {}
    if mj.get("flag"):
        print(f"  ** MULTI-JURISDICTION: states {', '.join(mj.get('states') or []) or '(PO spans multiple sites)'}"
              f" - requires tax allocation across jurisdictions (route to SME).")

    prec = (rep.get("precedent") or {}).get("summary") or {}
    if prec.get("count"):
        routing = ", ".join(f"{k}:{v}" for k, v in (prec.get("routing") or {}).items())
        print(f"  precedent       : {prec['count']} similar past decision(s), avg confidence "
              f"{prec.get('avg_confidence')}  ({routing})")

    po = rep.get("po") or {}
    banner("PO / AFE RECONCILIATION  (governed PO master)")
    if po.get("matched"):
        rec = po["record"]
        print(f"  matched PO      : {rec.get('po_number')}  (score {po['score']}, via {po['how']})")
        print(f"  project / AFE   : {rec.get('project_name') or '-'}  /  {rec.get('afe_number') or '-'}")
        print(f"  PO vendor/state : {rec.get('vendor_name') or '-'}  /  {rec.get('location_state') or '-'}")
        print(f"  PO task / asset : {rec.get('task_code') or '-'}  /  {rec.get('asset_class') or '-'}"
              f"   budget {_money(rec.get('budget_amount'))}")
        if po.get("discrepancies"):
            print("  ** discrepancies vs the invoice:")
            for d in po["discrepancies"]:
                print(f"       - {d}")
        else:
            print("  no discrepancies against the PO.")
    else:
        print(f"  no PO matched (best score {po.get('score')}).  -> unbacked invoice, route to review.")

    banner("ANTI-HALLUCINATION")
    if rep["grounding"]["flagged"]:
        print(f"  {len(rep['grounding']['flagged'])} value(s) NOT grounded in the source (review):")
        for flag in rep["grounding"]["flagged"]:
            print(f"    - {flag['field']}: {flag['value']!r}")
            print(f"        ({flag['why']})")
    else:
        print("  every extracted value is grounded in the signed source.")

    banner("QUALITY - deterministic checks + LLM judges")
    for name, score, passed, reason in rep["quality"]["rows"]:
        print(f"  {name:14} {score:.2f}  {'PASS' if passed else 'FAIL'}  {reason}")
    if rep["quality"]["skipped"]:
        print(f"  (skipped: {', '.join(rep['quality']['skipped'])})")
    print(f"  -> quality: {'PASS' if rep['quality']['passed'] else 'FAIL'}   mean score {rep['quality']['score']}")

    banner("SAFETY - governance + content safety")
    for name, score, passed, reason in rep["safety"]["rows"]:
        print(f"  {name:16} {score:.2f}  {'PASS' if passed else 'FAIL'}  {reason}")
    if rep["safety"]["skipped"]:
        print(f"  (skipped: {', '.join(rep['safety']['skipped'])})")
    print(f"  -> safety: {'PASS' if rep['safety']['passed'] else 'FAIL'}")

    banner("FIELD-BY-FIELD VERDICT - per-field judge status")
    if rep["verdicts"]:
        print(_verdict_table(rep["verdicts"]))
    else:
        print("  (no populated fields to verify)")

    banner("SOURCE CITATIONS - supporting passage per field")
    cites = rep["evidence"]["citations"]
    if cites:
        cite_rows = []
        for k in INVOICE_FIELDS:
            c = cites.get(k)
            if not c:
                continue
            snip = c["quote"] if len(c["quote"]) <= 200 else c["quote"][:197] + "\u2026"
            cite_rows.append((k, snip, f"chars {c['start']}-{c['end']} \u00b7 {c['method']} {c['score']:.2f}"))
        fw = max((len(r[0]) for r in cite_rows), default=8)
        for field, snip, where in cite_rows:
            print(f"  {field.ljust(fw)}  \"{snip}\"")
            print(f"  {' ' * fw}  [{where}]")
    else:
        print("  (no populated fields to cite)")

    banner("GOVERNANCE & CONFIDENCE")
    ev, conf = rep["evidence"], rep["confidence"]
    print(f"  read-only guarantee : {ev['guarantee_read_only']}  (agent cannot write or delete)")
    print(f"  signed why-record   : {ev['why_id']}  (provenance verifies: {ev['provenance_verifies']})")
    refm = rep.get("reference") or {}
    if refm.get("loaded"):
        print(f"  reference data      : {', '.join(refm['loaded'])}  "
              f"(read-only guarantee {refm.get('guarantee_read_only')}, {refm.get('signed_reads')} signed reads)")
    if refm.get("semantic_mapping"):
        _auth = "learned" in str(refm["semantic_mapping"])
        _desc = ("learned second opinion; tax-relevant disagreements route to review" if _auth
                 else "lexical second opinion (offline; confirms/flags, never overrides the model)")
        print(f"  line->task check    : semantic cross-check ({refm['semantic_mapping']}) - {_desc}")
    cache = rep.get("classification_cache")
    if cache:
        print(f"  decision cache      : {cache.get('hits', 0)} reproduced (deterministic) + "
              f"{cache.get('new', 0)} newly classified & persisted  ({cache.get('keys', 0)} known lines)")
    print(f"  all values grounded : {rep['grounding']['all_grounded']}")
    print(f"  quality panel       : {'PASS' if rep['quality']['passed'] else 'FAIL'}  (mean {rep['quality']['score']})")
    print(f"  safety panel        : {'PASS' if rep['safety']['passed'] else 'FAIL'}  (mean {rep['safety']['score']})")
    per_line = "  ".join(f"L{n}:{c:.2f}" for n, c in conf["per_line"].items())
    print(f"  per-line confidence : {per_line or '(none)'}   ->  worst {conf['overall']:.2f}")

    banner("TOKEN USAGE & COST - per model (cost estimated from list prices)")
    usage = rep.get("usage") or {}
    if usage.get("rows"):
        print(_usage_table(usage["rows"], usage["total"]))
        print("\n  per LLM call:")
        print(_usage_calls_table(usage["calls"]))
    else:
        print("  (no model calls recorded)")

    banner("DECISION")
    if rep["routing"] == "AUTO_APPROVE":
        print(f"  AUTO-APPROVE & POST  (worst-line confidence {conf['overall']:.2f} >= {conf['threshold']:.2f},"
              f" no exceptions) - validated BEFORE payment.")
    elif rep["routing"] == "AUTO_POST_FLAGGED":
        print(f"  AUTO-POST + 48H REVIEW FLAG  (worst-line confidence {conf['overall']:.2f} in the "
              f"{AUTOPOST_FLAG_THRESHOLD:.2f}-{conf['threshold']:.2f} tier, no hard exceptions) - posts now, "
              f"queued for a 48-hour spot review.")
    else:
        reasons = []
        if roll["n_sme"]:
            reasons.append(f"{roll['n_sme']} line(s) to SME review")
        if roll.get("major_project"):
            reasons.append(f"major project (CapEx {_money(roll['capex_total'])} >= {_money(MAJOR_PROJECT_THRESHOLD)}) - AFE/board")
        if roll.get("tax_unresolved"):
            reasons.append(f"dual-validation divergence on {roll.get('n_tax_divergence', 0)} line(s) - taxability disputed")
        elif roll.get("tax_status") == "under_collected":
            reasons.append(f"tax under-collected (use tax {_money(roll['use_tax_owed'])})")
        elif roll.get("tax_status") == "over_collected":
            reasons.append(f"tax over-collected {_money(roll['over_collected'])}")
        if (rep.get("multi_jurisdiction") or {}).get("flag"):
            reasons.append("multi-jurisdiction allocation needed")
        if (rep.get("po") or {}).get("discrepancies"):
            reasons.append(f"{len((rep['po'])['discrepancies'])} PO discrepancy(ies)")
        if conf["overall"] < AUTOPOST_FLAG_THRESHOLD:
            reasons.append(f"worst-line confidence {conf['overall']:.2f} < {AUTOPOST_FLAG_THRESHOLD:.2f}")
        if not rep["grounding"]["all_grounded"]:
            reasons.append("ungrounded value(s)")
        if not rep["quality"]["passed"]:
            reasons.append("quality below threshold")
        if not rep["safety"]["passed"]:
            reasons.append("safety concern")
        print(f"  ROUTE TO SME REVIEW  ({'; '.join(reasons)}).")
        print("  High-confidence lines can auto-post; flagged lines carry evidence + a recommendation,")
        print("  and the reviewer's correction feeds the loop.")


def _wrap(s, width):
    import textwrap
    return textwrap.wrap(str(s), width) or [""]


# Per-line CSV columns for downstream posting / SME triage.
_CSV_COLUMNS = (
    "n", "description", "quantity", "amount", "capex_opex", "capex_basis", "task_code",
    "asset_category", "useful_life_months", "depreciation", "existing_task_ok",
    "item_type", "mapping_basis", "mapping_conflict", "taxable", "tax_verdict",
    "jurisdiction_state", "expected_tax_rate",
    "expected_tax_amount", "charged_tax_alloc", "tax_delta", "use_tax_to_allocate", "tax_basis",
    "tax_exception", "tax_exception_reason", "posting_target", "confidence", "route",
)


def write_lines_csv(rep: dict, out_path: Path) -> Path:
    """Write ONE row per invoice line (classification + tax + confidence + route) so the results can
    be posted or triaged downstream. A trailing ROLLUP row carries the invoice-level totals."""
    import csv
    with open(out_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rep["lines"]:
            w.writerow({k: r.get(k) for k in _CSV_COLUMNS})
        roll = rep["rollup"]
        w.writerow({
            "n": "ROLLUP", "description": f"{roll['n_lines']} line(s); {roll['n_exceptions']} exception(s); "
            f"{roll['n_sme']} to SME; route {roll['route']}",
            "capex_opex": f"CapEx {roll['capex_total']}", "asset_category": f"OpEx {roll['opex_total']}",
            "expected_tax_amount": roll["expected_tax_total"], "charged_tax_alloc": roll["tax_charged"],
            "tax_delta": roll["use_tax_owed"], "route": roll["route"],
        })
    return out_path


def _pct(v) -> str:
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return "-"


# --------------------------------------------------------------------------------------------------
# HTML report (self-contained, downloadable) - mirrors examples/extract.py's --html report.
# --------------------------------------------------------------------------------------------------
_REPORT_CSS = (
    "body{font-family:system-ui,Arial,sans-serif;color:#1c2733;margin:0;padding:28px;background:#f5f7fa;"
    "line-height:1.45}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 10px;"
    "border-bottom:2px solid #dbe2ea;padding-bottom:4px}h3{font-size:15px;margin:0 0 8px;color:#0b5cad}"
    ".meta{color:#5b6b7b;font-size:13px;margin:2px 0}.sub{color:#5b6b7b;font-size:12px;font-weight:normal}"
    ".card{background:#fff;border:1px solid #dbe2ea;border-radius:8px;padding:14px 18px;margin:0 0 16px}"
    "table.kv{width:100%;border-collapse:collapse}table.kv th{text-align:left;vertical-align:top;width:180px;"
    "font-weight:600;color:#33445a;padding:7px 12px 7px 0;border-bottom:1px solid #eef2f6}"
    "table.kv td{vertical-align:top;padding:7px 0;border-bottom:1px solid #eef2f6}"
    "table.scores{width:100%;border-collapse:collapse;background:#fff;border:1px solid #dbe2ea;"
    "border-radius:8px;overflow:hidden}table.scores th{text-align:left;background:#eef2f6;padding:8px 12px;"
    "font-size:13px}table.scores td{padding:8px 12px;border-top:1px solid #eef2f6;vertical-align:top}"
    ".pass{color:#1a7f37;font-weight:600}.fail{color:#c1341d;font-weight:600}.warn{color:#b7791f;font-weight:600}"
    "code{background:#eef2f6;padding:1px 5px;border-radius:4px;font-size:12px}"
    ".banner{border-radius:8px;padding:12px 16px;margin:12px 0 20px;font-weight:600}"
    ".ok{background:#e7f4ec;border:1px solid #bfe3cd;color:#1a7f37}"
    ".sme{background:#fdf3e1;border:1px solid #f3d9a6;color:#8a5a12}"
    "tr.exc td{background:#fdf3e1}.num{text-align:right;white-space:nowrap}a.cite{color:#0b5cad}"
)


def _html_escape(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _hpass(passed) -> str:
    return "<span class='pass'>PASS</span>" if passed else "<span class='fail'>FAIL</span>"


def _tax_recon_html(roll) -> str:
    """Render the invoice-level tax reconciliation status (balanced / under / over / dual-divergence)."""
    status = roll.get("tax_status", "balanced")
    if roll.get("tax_unresolved") and status != "balanced":
        gap = roll['over_collected'] if status == "over_collected" else roll['use_tax_owed']
        return (f"<span class='fail'>DUAL-VALIDATION DIVERGENCE</span> on "
                f"{roll.get('n_tax_divergence', 0)} line(s) &mdash; the tax engine and the model disagree "
                f"on taxability; the {_money(gap)} gap is <b>UNRESOLVED</b>, routed to analyst review "
                f"(do NOT seek credit / self-assess until confirmed)")
    if status == "under_collected":
        return (f"<span class='fail'>UNDER-COLLECTED</span> &mdash; self-assess use tax "
                f"<b>{_money(roll['use_tax_owed'])}</b>")
    if status == "over_collected":
        return (f"<span class='fail'>OVER-COLLECTED {_money(roll['over_collected'])}</span> &mdash; vendor "
                f"charged tax the matrix says isn't due; verify classification / seek credit")
    return f"<span class='pass'>balanced</span> <span class='sub'>(within {_money(roll.get('tax_tolerance'))})</span>"


def _scores_html(rows, skipped) -> str:
    trs = ["<tr><th>dimension</th><th>score</th><th>result</th><th>reason</th></tr>"]
    for name, score, passed, reason in rows:
        badge = "<span class='pass'>PASS</span>" if passed else "<span class='fail'>FAIL</span>"
        trs.append(f"<tr><th>{_html_escape(name)}</th><td>{score:.2f}</td><td>{badge}</td>"
                   f"<td>{_html_escape(reason)}</td></tr>")
    tail = f"<div class='sub'>skipped: {_html_escape(', '.join(skipped))}</div>" if skipped else ""
    return "<table class='scores'>\n" + "\n".join(trs) + f"\n</table>{tail}"


def _verdicts_html(rows) -> str:
    trs = ["<tr><th>field</th><th>present</th><th>grounded</th><th>judge</th><th>reason</th></tr>"]
    for field, present, grounded, judge, reason in rows:
        j = str(judge).lower()
        jcls = "pass" if j == "ok" else ("fail" if j == "fail" else "warn")
        gcell = ("<span class='fail'>NO</span>" if str(grounded).lower() == "no" else _html_escape(grounded))
        trs.append(f"<tr><th>{_html_escape(field)}</th><td>{_html_escape(present)}</td><td>{gcell}</td>"
                   f"<td><span class='{jcls}'>{_html_escape(judge)}</span></td><td>{_html_escape(reason)}</td></tr>")
    return "<table class='scores'>\n" + "\n".join(trs) + "\n</table>"


def _citations_html(citations) -> str:
    rows = ["<tr><th>field</th><th>supporting source passage</th><th>where</th></tr>"]
    for field, c in citations.items():
        where = f"<span class='sub'>chars {c['start']}-{c['end']} &middot; {c['method']} {c['score']:.2f}</span>"
        rows.append(f"<tr><th>{_html_escape(field)}</th><td>{_html_escape(c['quote'])}</td><td>{where}</td></tr>")
    return "<table class='scores'>\n" + "\n".join(rows) + "\n</table>"


def _usage_html(rows, total) -> str:
    trs = ["<tr><th>model</th><th>calls</th><th>input tokens</th><th>output tokens</th><th>est. cost (USD)</th></tr>"]
    for m, calls, pin, pout, cost in rows:
        trs.append(f"<tr><th>{_html_escape(m)}</th><td class='num'>{calls:,}</td><td class='num'>{pin:,}</td>"
                   f"<td class='num'>{pout:,}</td><td class='num'>${cost:,.4f}</td></tr>")
    calls, pin, pout, cost, est = total
    star = " *" if est else ""
    trs.append(f"<tr><th>TOTAL{star}</th><td class='num'>{calls:,}</td><td class='num'>{pin:,}</td>"
               f"<td class='num'>{pout:,}</td><td class='num'>${cost:,.4f}</td></tr>")
    return "<table class='scores'>\n" + "\n".join(trs) + "\n</table>"


def _usage_calls_html(rows) -> str:
    trs = ["<tr><th>#</th><th>kind</th><th>phase</th><th>start</th><th>end</th><th>dur</th>"
           "<th>input</th><th>output</th><th>est. $</th></tr>"]
    for n, kind, phase, model, start, end, dur, pin, pout, cost, est in rows:
        trs.append(f"<tr><td>{n}</td><td>{_html_escape(kind)}</td><th>{_html_escape(phase)}</th><td>{start}</td>"
                   f"<td>{end}</td><td>{dur}</td><td class='num'>{pin:,}</td><td class='num'>{pout:,}</td>"
                   f"<td class='num'>${cost:,.4f}</td></tr>")
    return "<table class='scores'>\n" + "\n".join(trs) + "\n</table>"


def _lines_html(lines) -> str:
    trs = ["<tr><th>#</th><th>line item</th><th>amount</th><th>CapEx/OpEx</th><th>item type</th>"
           "<th>taxable</th><th>rate</th><th>expected</th><th>charged~</th><th>&Delta;</th><th>conf</th><th>route</th></tr>"]
    for r in lines:
        exc = " class='exc'" if r["tax_exception"] else ""
        route_cls = "pass" if r["route"] == "AUTO_POST" else "warn"
        route = "AUTO-POST" if r["route"] == "AUTO_POST" else "SME REVIEW"
        sub = (f"asset: {_html_escape(r['asset_category'] or '-')} &middot; "
               f"task: {_html_escape(r.get('task_code') or r['suggested_task'] or '-')}")
        if r.get("capex_basis"):
            sub += f" &middot; <i>{_html_escape(r['capex_basis'])}</i>"
        if r.get("tax_basis"):
            sub += f"<br>tax: <i>{_html_escape(r['tax_basis'])}</i>"
        trs.append(
            f"<tr{exc}><td>{r['n']}</td><td>{_html_escape(r['description'])}"
            f"<div class='sub'>{sub}</div></td>"
            f"<td class='num'>{_money(r['amount'])}</td><td>{_html_escape(r['capex_opex'] or '-')}</td>"
            f"<td>{_html_escape(r.get('item_type') or '-')}</td>"
            f"<td>{_html_escape(r['taxable'])}</td><td class='num'>{_pct(r['expected_tax_rate'])}</td>"
            f"<td class='num'>{_money(r['expected_tax_amount'])}</td><td class='num'>{_money(r.get('charged_tax_alloc'))}</td>"
            f"<td class='num'>{_money(r.get('tax_delta'))}</td><td class='num'>{r['confidence']:.2f}</td>"
            f"<td><span class='{route_cls}'>{route}</span></td></tr>"
        )
        if r["tax_exception"] and r["tax_exception_reason"]:
            trs.append(f"<tr{exc}><td></td><td colspan='11' class='sub'>&#9888; {_html_escape(r['tax_exception_reason'])}</td></tr>")
    return "<table class='scores'>\n" + "\n".join(trs) + "\n</table>"


def render_report_html(rep: dict, meta: dict) -> str:
    """Build a self-contained, downloadable HTML report of the per-line determinations, evaluation
    panels, citations, and cost - the HTML twin of print_report()."""
    import datetime
    esc = _html_escape
    f, roll, conf = rep["fields"], rep["rollup"], rep["confidence"]
    ev = rep["evidence"]
    doc_url = meta.get("doc_url") or ""
    doc_link = f"<a class='cite' href='{esc(doc_url)}'><b>{esc(meta.get('doc_name'))}</b> &#8599;</a>" if doc_url else f"<b>{esc(meta.get('doc_name'))}</b>"
    auto = rep["routing"] == "AUTO_APPROVE"
    flagged = rep["routing"] == "AUTO_POST_FLAGGED"
    if auto:
        banner_cls, banner_txt = "ok", "AUTO-APPROVE & POST"
    elif flagged:
        banner_cls, banner_txt = "warn", "AUTO-POST + 48H REVIEW FLAG"
    else:
        banner_cls, banner_txt = "sme", "ROUTE TO SME REVIEW"
    p = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Invoice determination \u2014 {esc(meta.get('doc_name'))}</title>",
        "<style>" + _REPORT_CSS + "</style></head><body>",
        "<h1>Invoice classification &amp; tax determination</h1>",
        f"<p class='meta'>Document: {doc_link} &middot; engine: {esc(meta.get('engine'))} &middot; "
        f"{meta.get('chars', 0):,} chars &middot; generated {datetime.datetime.now():%Y-%m-%d %H:%M}</p>",
        f"<p class='meta'>Signed why-record: <code>{esc(ev['why_id'])}</code> &middot; provenance verifies: "
        f"{esc(ev['provenance_verifies'])} &middot; read-only guarantee: {esc(ev['guarantee_read_only'])}</p>",
        f"<div class='banner {banner_cls}'>{banner_txt} &nbsp;&middot;&nbsp; worst-line confidence "
        f"{conf['overall']:.2f} (threshold {conf['threshold']:.2f}) &middot; {roll['n_exceptions']} exception(s) "
        f"&middot; {roll['n_sme']} line(s) to SME</div>",
        "<h2>Invoice</h2><table class='kv'>",
        f"<tr><th>vendor</th><td>{esc(f.get('vendor_name') or '-')}</td></tr>",
        f"<tr><th>invoice # / date</th><td>{esc(f.get('invoice_number') or '-')} &middot; {esc(f.get('invoice_date') or '-')}</td></tr>",
        f"<tr><th>PO / AFE</th><td>{esc(f.get('po_number') or '-')} / {esc(f.get('afe_number') or '-')}</td></tr>",
        f"<tr><th>site / state</th><td>{esc(f.get('site_number') or '-')} / {esc(f.get('state') or '-')}</td></tr>",
        f"<tr><th>total / tax billed</th><td>{_money(f.get('total_amount'))} &middot; {_money(f.get('tax_charged'))}</td></tr>",
        "</table>",
        "<h2>Line-item classification &amp; tax <span class='sub'>steps 3-5: classify &rarr; tax &rarr; confidence &rarr; route</span></h2>",
        _lines_html(rep["lines"]) if rep["lines"] else "<p>(no line items extracted)</p>",
        "<h2>Invoice rollup</h2><table class='kv'>",
        f"<tr><th>CapEx / OpEx</th><td>{_money(roll['capex_total'])} / {_money(roll['opex_total'])}</td></tr>",
        f"<tr><th>expected vs charged tax</th><td>{_money(roll['expected_tax_total'])} vs {_money(roll['tax_charged'])}</td></tr>",
        f"<tr><th>tax reconciliation</th><td>{_tax_recon_html(roll)}</td></tr>",
        f"<tr><th>lines / exceptions / SME</th><td>{roll['n_lines']} / {roll['n_exceptions']} / {roll['n_sme']}</td></tr>",
        "</table>",
    ]
    mj = rep.get("multi_jurisdiction") or {}
    if mj.get("flag"):
        p.append(f"<p class='warn'>&#9888; Multi-jurisdiction: states {esc(', '.join(mj.get('states') or []))} "
                 f"&mdash; requires tax allocation across jurisdictions (route to SME).</p>")
    prec = (rep.get("precedent") or {}).get("summary") or {}
    if prec.get("count"):
        routing = ", ".join(f"{esc(k)}: {v}" for k, v in (prec.get("routing") or {}).items())
        p.append(f"<p class='sub'>Precedent: {prec['count']} similar past decision(s), avg confidence "
                 f"{prec.get('avg_confidence')} ({routing}) &middot; e.g. &ldquo;{esc(prec.get('example'))}&rdquo;</p>")
    po = rep.get("po") or {}
    p.append("<h2>PO / AFE reconciliation <span class='sub'>governed PO master</span></h2>")
    if po.get("matched"):
        rec = po["record"]
        p.append("<table class='kv'>")
        p.append(f"<tr><th>matched PO</th><td>{esc(rec.get('po_number'))} "
                 f"<span class='sub'>(score {po['score']}, via {esc(po['how'])})</span></td></tr>")
        p.append(f"<tr><th>project / AFE</th><td>{esc(rec.get('project_name') or '-')} / {esc(rec.get('afe_number') or '-')}</td></tr>")
        p.append(f"<tr><th>PO vendor / state</th><td>{esc(rec.get('vendor_name') or '-')} / {esc(rec.get('location_state') or '-')}</td></tr>")
        p.append(f"<tr><th>PO task / asset</th><td>{esc(rec.get('task_code') or '-')} / {esc(rec.get('asset_class') or '-')} "
                 f"&middot; budget {_money(rec.get('budget_amount'))}</td></tr>")
        p.append("</table>")
        if po.get("discrepancies"):
            p.append("<p class='warn'>Discrepancies vs the invoice:</p><ul>")
            p.extend(f"<li class='warn'>{esc(d)}</li>" for d in po["discrepancies"])
            p.append("</ul>")
        else:
            p.append("<p class='pass'>No discrepancies against the PO.</p>")
    else:
        p.append(f"<p class='warn'>No PO matched (best score {po.get('score')}) &mdash; unbacked invoice, route to review.</p>")
    flagged = rep["grounding"]["flagged"]
    p.append("<h2>Anti-hallucination</h2>")
    if flagged:
        p.append(f"<p class='fail'>{len(flagged)} value(s) NOT grounded in the source:</p><ul>")
        p.extend(f"<li>{esc(x['field'])}: {esc(repr(x['value']))} <span class='sub'>({esc(x['why'])})</span></li>" for x in flagged)
        p.append("</ul>")
    else:
        p.append("<p class='pass'>Every extracted value is grounded in the signed source.</p>")
    if rep["verdicts"]:
        p.append("<h2>Field-by-field verdict <span class='sub'>per-field judge status</span></h2>")
        p.append(_verdicts_html(rep["verdicts"]))
    if ev["citations"]:
        p.append("<h2>Source citations <span class='sub'>supporting passage per field</span></h2>")
        p.append(_citations_html(ev["citations"]))
    p.append(f"<h2>Quality <span class='sub'>mean {rep['quality']['score']} \u2014 {'PASS' if rep['quality']['passed'] else 'FAIL'}</span></h2>")
    p.append(_scores_html(rep["quality"]["rows"], rep["quality"]["skipped"]))
    p.append(f"<h2>Safety <span class='sub'>mean {rep['safety']['score']} \u2014 {'PASS' if rep['safety']['passed'] else 'FAIL'}</span></h2>")
    p.append(_scores_html(rep["safety"]["rows"], rep["safety"]["skipped"]))
    usage = rep.get("usage") or {}
    if usage.get("rows"):
        p.append("<h2>Token usage &amp; cost <span class='sub'>per model \u2014 estimated from list prices</span></h2>")
        p.append(_usage_html(usage["rows"], usage["total"]))
        if usage.get("calls"):
            p.append("<h3>Per LLM call</h3>")
            p.append(_usage_calls_html(usage["calls"]))
    p.append("</body></html>")
    return "\n".join(p)



# --------------------------------------------------------------------------------------------------
def main() -> int:
    try:  # UTF-8 console so currency/dashes render on legacy Windows code pages
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

    path, model, auth_mode, threshold, demo, as_json, csv_on, csv_path, html_on, html_path, embed_spec, doci_on, doci_endpoint, cache_path = parse_args(sys.argv[1:])
    if not path and not demo:
        print("usage: python examples/extract_invoice.py <invoice.pdf> [--model azure:<deployment>] "
              "[--auth aad|key|auto] [--threshold 0.85] [--json] [--csv [out.csv]] [--html [out.html]] "
              "[--embed [spec]] [--doci [endpoint]] [--cache [path]] [--no-cache]")
        print("       python examples/extract_invoice.py --demo   (offline, bundled sample invoice)")
        return 2

    get_usage_meter().reset()
    banner("MICROSOFT AGENT FRAMEWORK - reasoning  |  autarch - governance")

    tmp_demo = None
    if demo and not path:
        tmp_demo = Path(tempfile.mkdtemp(prefix="autarch_invoice_demo_")) / "sample_invoice.txt"
        tmp_demo.write_text(DEMO_INVOICE, encoding="utf-8")
        path = str(tmp_demo)
    # Keep offline demo decisions out of the real shared cache (unless the user pointed --cache
    # somewhere explicitly): route the demo to a throwaway cache file.
    if demo and cache_path == decision_cache.DEFAULT_CACHE_PATH:
        cache_path = str(Path(tempfile.mkdtemp(prefix="autarch_invoice_cache_")) / "decision_cache.json")

    doc = Path(path).expanduser()
    if not doc.exists():
        print(f"file not found: {doc}")
        return 2

    provider, engine_label, is_live = resolve_engine(model, auth_mode, demo)
    print(f"  reasoning engine: {engine_label}")
    if model.startswith("azure:") and not demo and not is_live:
        print("  Aborting: an explicit Azure model was requested, but Azure is unavailable. "
              "Set AZURE_OPENAI_ENDPOINT and authenticate before rerunning.")
        return 1
    if embed_spec:
        _mode = "lexical" if "hash" in str(embed_spec).lower() else "learned"
        print(f"  line->task check   : semantic cross-check / validator ({_mode}: {embed_spec})")

    banner(f"1) GOVERNED READ - {doc.name}")
    agent, read_result, guarantee_ok = governed_read(doc)
    if not read_result.executed or read_result.result is None or not read_result.result.ok:
        err = read_result.result.error if read_result.result else "no result"
        print(f"  read was blocked: {err}")
        return 1
    text = read_result.result.output
    print(f"  read OK: {len(text):,} characters   |   read-only guarantee holds: {guarantee_ok}")
    print(f"  signed why-record: {read_result.why_id}")

    # Step 5 - Document Intelligence: structured, confidence-scored extraction of the same PDF. The
    # governed read above already proved the file was touched read-only; DI is the extraction engine.
    di = None
    if doci_on:
        endpoint = doci_endpoint or os.environ.get("AZURE_DOCINTEL_ENDPOINT")
        if not endpoint:
            print("  --doci set but no endpoint (pass --doci <url> or set AZURE_DOCINTEL_ENDPOINT); using OCR+LLM.")
        elif doc.suffix.lower() != ".pdf":
            print("  --doci needs a PDF; using OCR+LLM for this file.")
        else:
            banner(f"1b) DOCUMENT INTELLIGENCE - prebuilt-invoice  |  {doc.name}")
            tenant = (os.environ.get("AZURE_DOCINTEL_TENANT_ID") or os.environ.get("AZURE_OPENAI_TENANT_ID")
                      or os.environ.get("AZURE_TENANT_ID"))
            key = os.environ.get("AZURE_DOCINTEL_KEY")
            print(f"  endpoint: {endpoint}")
            di = docintel.analyze_invoice(str(doc), endpoint, tenant_id=tenant, api_key=key)
            if di is None:
                print("  azure-ai-documentintelligence not installed - using OCR+LLM.")
            elif di.get("error"):
                print(f"  DI extraction failed ({di['error']}) - using OCR+LLM.")
                di = None
            else:
                conf = di.get("confidence") or {}
                print(f"  DI OK: {len(di.get('header') or {})} header field(s), "
                      f"{len(di.get('lines') or [])} line item(s), {di.get('n_pages')} page(s), "
                      f"{len(di.get('content') or ''):,} chars OCR")
                if conf:
                    print(f"  field confidence (min {min(conf.values()):.2f}): "
                          + ", ".join(f"{k} {v:.2f}" for k, v in conf.items()))
                if di.get("state_source"):
                    print(f"  governing state {di['header'].get('state', '?')} from {di['state_source']} "
                          f"(ship-to, not bill-to)")
                if di.get("content"):
                    text = di["content"]  # DI's OCR becomes the grounding/citation source
                    print(f"  using DI OCR content ({len(text):,} chars) for grounding")

    if di is None and _looks_scanned(text):
        print("  no text layer found (scanned/image-only PDF) - running governed vision-OCR fallback ...")
        ocr = vision_transcribe(doc, provider)
        if ocr.strip():
            text = ocr
            print(f"  vision OCR recovered {len(text):,} characters")
        else:
            print("  vision OCR recovered no text (needs a vision-capable model; the offline "
                  "provider and non-vision deployments cannot OCR).")

    try:
        rep = run(provider, text, agent, read_result, guarantee_ok, threshold, embed_spec, di=di,
                  cache_path=cache_path)
    finally:
        if is_live:
            provider.close()

    if as_json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        print_report(rep)
        totals = get_usage_meter().totals()
        print(f"\n  model usage: {totals.get('calls', 0)} call(s), "
              f"{totals.get('prompt_tokens', 0)} prompt + {totals.get('completion_tokens', 0)} completion tokens")

    if csv_on:
        out = Path(csv_path).expanduser() if csv_path else doc.with_name(doc.stem + "_lines.csv")
        try:
            write_lines_csv(rep, out)
            print(f"\n  per-line CSV written: {out}")
        except Exception as exc:  # noqa: BLE001
            print(f"\n  CSV export failed ({type(exc).__name__}: {exc})")

    if html_on:
        out = Path(html_path).expanduser() if html_path else doc.with_name(doc.stem + "_report.html")
        try:
            doc_url = ""
            if not (tmp_demo is not None):
                try:
                    doc_url = doc.resolve().as_uri()
                except Exception:
                    doc_url = ""
            meta = {"doc_name": doc.name, "doc_url": doc_url, "engine": engine_label, "chars": len(text)}
            out.write_text(render_report_html(rep, meta), encoding="utf-8")
            print(f"  HTML report written: {out}")
        except Exception as exc:  # noqa: BLE001
            print(f"  HTML report failed ({type(exc).__name__}: {exc})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
