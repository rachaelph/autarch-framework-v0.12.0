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
import sys
import tempfile
from pathlib import Path

# Run against THIS repository's autarch (the copy that ships MAFModelProvider), regardless of any
# other autarch install on the path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

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
            {"capex_opex": "CapEx", "asset_category": "Fuel Dispenser",
             "suggested_task": "New Fuel Dispenser Installation (Capital)", "existing_task_ok": False,
             "confidence": 0.92, "rationale": "Brand-new Encore 700S dispenser tied to AFE-2025-IL-0091 - a new capital asset, not a repair."},
            {"capex_opex": "CapEx", "asset_category": "Fuel-Island Equipment",
             "suggested_task": "New Fuel Dispenser Installation (Capital)", "existing_task_ok": True,
             "confidence": 0.90, "rationale": "Sump kit installed with the new dispensers is capitalized into the fuel-island asset."},
            {"capex_opex": "CapEx", "asset_category": "Installation Labor (capitalized)",
             "suggested_task": "New Fuel Dispenser Installation (Capital)", "existing_task_ok": True,
             "confidence": 0.83, "rationale": "Labor to place the new dispensers in service is capitalized into the asset cost, not expensed."},
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
    model, argv = _split_flag(argv, "--model")
    auth, argv = _split_flag(argv, "--auth")
    thr, argv = _split_flag(argv, "--threshold")
    path = argv[0] if argv else None
    try:
        threshold = float(thr) if thr is not None else DEFAULT_THRESHOLD
    except ValueError:
        threshold = DEFAULT_THRESHOLD
    return (path, (model or DEFAULT_MODEL), (auth or "auto").lower(), threshold, demo, as_json,
            csv_on, csv_path, html_on, html_path)


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
        candidate = MAFModelProvider(factory, agent_name="autarch-invoice-agent", model_label=deployment)
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


def classify_lines(provider, header, lines) -> list:
    """Diagram step 3 - classify EACH line CapEx vs OpEx (rules + LLM) and validate its task."""
    listing = "\n".join(
        f"{i + 1}. {ln['description']}  (qty {ln.get('quantity') or '-'}, amount {ln.get('amount') or '-'})"
        for i, ln in enumerate(lines)
    )
    prompt = (
        "STEP: CLASSIFY_LINES\n"
        "For EACH numbered invoice line, classify CapEx vs OpEx and validate the capitalization task. "
        "Creating/installing a NEW asset - or a component or installation labor capitalized INTO it - "
        "is CapEx; a like-for-like repair, routine maintenance, or a consumable is OpEx. Do NOT just "
        "accept an existing 'repair' code; decide the correct task (tasks exist only on capital AFE "
        "items). Return JSON {\"lines\": [...]} with ONE entry per line IN THE SAME ORDER, each: "
        '{"capex_opex": "CapEx"|"OpEx", "asset_category": "", "suggested_task": "", '
        '"existing_task_ok": true, "confidence": 0.0, "rationale": ""}.\n\n'
        f"INVOICE HEADER:\n{json.dumps(header, indent=2)}\n\nLINES:\n{listing}\n\nJSON:"
    )
    data = _ask(provider, "classify_lines", _JUDGE_SYS, prompt)
    return _align(data, len(lines))


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


def _num(v):
    """Parse a currency/number string to float, or None."""
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def build_line_results(lines, classifications, taxes, threshold: float, header: dict) -> list:
    """Merge the per-line extraction + classification + tax into one result per line, with a
    per-line confidence (worst of the two judges) and a route (diagram step 5). Then allocate the
    invoice's actually-charged tax across the taxable lines (pro-rata by expected tax, else by
    amount) so each line shows charged-vs-expected and its own tax delta / self-assessed shortfall."""
    out = []
    for i, ln in enumerate(lines):
        c = classifications[i] if i < len(classifications) else {}
        t = taxes[i] if i < len(taxes) else {}
        conf = min(_confidence(c), _confidence(t))
        exception = bool(t.get("exception"))
        route = "SME_REVIEW" if (conf < threshold or exception) else "AUTO_POST"
        out.append({
            "n": i + 1,
            "description": ln.get("description", ""),
            "quantity": ln.get("quantity", ""),
            "amount": _num(ln.get("amount")),
            "amount_raw": ln.get("amount", ""),
            "capex_opex": c.get("capex_opex", ""),
            "asset_category": c.get("asset_category", ""),
            "suggested_task": c.get("suggested_task", ""),
            "existing_task_ok": c.get("existing_task_ok"),
            "class_rationale": c.get("rationale", ""),
            "taxable": t.get("taxable"),
            "jurisdiction_state": t.get("jurisdiction_state", ""),
            "expected_tax_rate": t.get("expected_tax_rate"),
            "expected_tax_amount": _num(t.get("expected_tax_amount")),
            "tax_exception": exception,
            "tax_exception_reason": t.get("exception_reason", ""),
            "tax_rationale": t.get("rationale", ""),
            "confidence": round(conf, 3),
            "route": route,
        })

    # Allocate the invoice's charged tax across taxable lines: pro-rata by expected tax (falls back
    # to line amount when no expected tax resolved). Each line's delta = expected - allocated charged
    # (positive = under-collected -> use tax owed on that line).
    charged_total = _num(header.get("tax_charged")) or 0.0
    total_expected = sum(r["expected_tax_amount"] for r in out if r["taxable"] and r["expected_tax_amount"])
    total_amt = sum(r["amount"] for r in out if r["taxable"] and r["amount"])
    for r in out:
        share = 0.0
        if r["taxable"]:
            if total_expected > 0 and r["expected_tax_amount"]:
                share = r["expected_tax_amount"] / total_expected
            elif total_amt > 0 and r["amount"]:
                share = r["amount"] / total_amt
        alloc = round(charged_total * share, 2)
        exp = r["expected_tax_amount"] or 0.0
        r["charged_tax_alloc"] = alloc
        r["tax_delta"] = round(exp - alloc, 2)
    return out


def summarize_lines(line_results, header) -> dict:
    """Invoice-level rollup of the per-line results: CapEx/OpEx totals, expected vs charged tax,
    self-assessed use tax owed, and exception / SME-review counts."""
    capex_total = sum(r["amount"] for r in line_results if r["amount"] and str(r["capex_opex"]).lower() == "capex")
    opex_total = sum(r["amount"] for r in line_results if r["amount"] and str(r["capex_opex"]).lower() == "opex")
    expected_tax = sum(r["expected_tax_amount"] for r in line_results if r["expected_tax_amount"] and r.get("taxable"))
    charged = _num(header.get("tax_charged")) or 0.0
    use_tax_owed = max(0.0, round(expected_tax - charged, 2))
    n_exc = sum(1 for r in line_results if r["tax_exception"])
    n_sme = sum(1 for r in line_results if r["route"] == "SME_REVIEW")
    return {
        "capex_total": round(capex_total, 2),
        "opex_total": round(opex_total, 2),
        "expected_tax_total": round(expected_tax, 2),
        "tax_charged": round(charged, 2),
        "use_tax_owed": use_tax_owed,
        "tax_shortfall": bool(use_tax_owed > 0.01),
        "n_lines": len(line_results),
        "n_exceptions": n_exc,
        "n_sme": n_sme,
        "route": "SME_REVIEW" if n_sme else "AUTO_POST",
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
def run(provider, text: str, agent, read_result, guarantee_ok: bool, threshold: float) -> dict:
    # Header (invoice-level) fields for context + reconciliation.
    fields = extract_invoice_fields(provider, text)

    # Diagram steps 3-5, PER LINE ITEM: extract lines -> classify each CapEx/OpEx -> apply tax rules
    # per line -> score each line's confidence and route (high auto-posts, low/exception -> SME).
    lines = extract_line_items(provider, text, fields)
    classifications = classify_lines(provider, fields, lines) if lines else []
    taxes = tax_lines(provider, fields, lines, classifications) if lines else []
    line_results = build_line_results(lines, classifications, taxes, threshold, fields)
    rollup = summarize_lines(line_results, fields)

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

    # Confidence: worst LINE wins (fail-closed). Invoice routes to review if any line does, or an
    # exception/ungrounded value/failed panel is present.
    line_confs = [r["confidence"] for r in line_results]
    overall = min(line_confs) if line_confs else 0.0
    route = "HUMAN_REVIEW" if (rollup["route"] == "SME_REVIEW" or overall < threshold or ungrounded
                               or not quality_report.passed or not safety_report.passed) else "AUTO_APPROVE"

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

    banner("LINE-ITEM CLASSIFICATION & TAX  (steps 3-5: classify -> tax -> confidence -> route)")
    if not lines:
        print("  (no line items were extracted)")
    for r in lines:
        head = f"  [{r['n']}] {r['description']}"
        amt = _money(r["amount"]) if r["amount"] is not None else (r["amount_raw"] or "-")
        print(f"{head[:74].ljust(74)} {amt:>12}")
        tok = "" if r["existing_task_ok"] is None else ("yes" if r["existing_task_ok"] else "no")
        print(f"        step3  {str(r['capex_opex'] or '-'):5}  asset: {r['asset_category'] or '-'}"
              f"   task: {r['suggested_task'] or '-'} (existing OK: {tok})")
        taxable = r["taxable"]
        tax_line = (f"        step4  taxable={taxable}  {r['jurisdiction_state'] or '-'}"
                    f" @{_pct(r['expected_tax_rate'])}   expected {_money(r['expected_tax_amount'])}"
                    f"   charged~{_money(r.get('charged_tax_alloc'))}   \u0394 {_money(r.get('tax_delta'))}")
        if r["tax_exception"]:
            tax_line += "   ** EXCEPTION"
        print(tax_line)
        if r["tax_exception"] and r["tax_exception_reason"]:
            for ln in _wrap(r["tax_exception_reason"], 84):
                print(f"               {ln}")
        arrow = "AUTO-POST" if r["route"] == "AUTO_POST" else "SME REVIEW"
        print(f"        step5  confidence {r['confidence']:.2f}  ->  {arrow}")

    banner("INVOICE ROLLUP")
    print(f"  CapEx total     : {_money(roll['capex_total'])}      OpEx total: {_money(roll['opex_total'])}")
    print(f"  expected tax    : {_money(roll['expected_tax_total'])}   vs charged {_money(roll['tax_charged'])}"
          f"  ->  use tax owed {_money(roll['use_tax_owed'])}")
    print(f"  lines           : {roll['n_lines']}   tax exceptions: {roll['n_exceptions']}   "
          f"to SME review: {roll['n_sme']}")

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
    else:
        reasons = []
        if roll["n_sme"]:
            reasons.append(f"{roll['n_sme']} line(s) to SME review")
        if roll["n_exceptions"]:
            reasons.append(f"{roll['n_exceptions']} tax exception(s)")
        if conf["overall"] < conf["threshold"]:
            reasons.append(f"worst-line confidence {conf['overall']:.2f} < {conf['threshold']:.2f}")
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
    "n", "description", "quantity", "amount", "capex_opex", "asset_category", "suggested_task",
    "existing_task_ok", "taxable", "jurisdiction_state", "expected_tax_rate", "expected_tax_amount",
    "charged_tax_alloc", "tax_delta", "tax_exception", "tax_exception_reason", "confidence", "route",
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
    trs = ["<tr><th>#</th><th>line item</th><th>amount</th><th>CapEx/OpEx</th><th>taxable</th>"
           "<th>rate</th><th>expected</th><th>charged~</th><th>&Delta;</th><th>conf</th><th>route</th></tr>"]
    for r in lines:
        exc = " class='exc'" if r["tax_exception"] else ""
        route_cls = "pass" if r["route"] == "AUTO_POST" else "warn"
        route = "AUTO-POST" if r["route"] == "AUTO_POST" else "SME REVIEW"
        trs.append(
            f"<tr{exc}><td>{r['n']}</td><td>{_html_escape(r['description'])}"
            f"<div class='sub'>asset: {_html_escape(r['asset_category'] or '-')} &middot; task: "
            f"{_html_escape(r['suggested_task'] or '-')}</div></td>"
            f"<td class='num'>{_money(r['amount'])}</td><td>{_html_escape(r['capex_opex'] or '-')}</td>"
            f"<td>{_html_escape(r['taxable'])}</td><td class='num'>{_pct(r['expected_tax_rate'])}</td>"
            f"<td class='num'>{_money(r['expected_tax_amount'])}</td><td class='num'>{_money(r.get('charged_tax_alloc'))}</td>"
            f"<td class='num'>{_money(r.get('tax_delta'))}</td><td class='num'>{r['confidence']:.2f}</td>"
            f"<td><span class='{route_cls}'>{route}</span></td></tr>"
        )
        if r["tax_exception"] and r["tax_exception_reason"]:
            trs.append(f"<tr{exc}><td></td><td colspan='10' class='sub'>&#9888; {_html_escape(r['tax_exception_reason'])}</td></tr>")
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
    banner_cls, banner_txt = ("ok", "AUTO-APPROVE & POST") if auto else ("sme", "ROUTE TO SME REVIEW")
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
        f"<tr><th>expected vs charged tax</th><td>{_money(roll['expected_tax_total'])} vs {_money(roll['tax_charged'])} "
        f"&rarr; use tax owed <b>{_money(roll['use_tax_owed'])}</b></td></tr>",
        f"<tr><th>lines / exceptions / SME</th><td>{roll['n_lines']} / {roll['n_exceptions']} / {roll['n_sme']}</td></tr>",
        "</table>",
    ]
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

    path, model, auth_mode, threshold, demo, as_json, csv_on, csv_path, html_on, html_path = parse_args(sys.argv[1:])
    if not path and not demo:
        print("usage: python examples/extract_invoice.py <invoice.pdf> [--model azure:<deployment>] "
              "[--auth aad|key|auto] [--threshold 0.85] [--json] [--csv [out.csv]] [--html [out.html]]")
        print("       python examples/extract_invoice.py --demo   (offline, bundled sample invoice)")
        return 2

    get_usage_meter().reset()
    banner("MICROSOFT AGENT FRAMEWORK - reasoning  |  autarch - governance")

    tmp_demo = None
    if demo and not path:
        tmp_demo = Path(tempfile.mkdtemp(prefix="autarch_invoice_demo_")) / "sample_invoice.txt"
        tmp_demo.write_text(DEMO_INVOICE, encoding="utf-8")
        path = str(tmp_demo)

    doc = Path(path).expanduser()
    if not doc.exists():
        print(f"file not found: {doc}")
        return 2

    provider, engine_label, is_live = resolve_engine(model, auth_mode, demo)
    print(f"  reasoning engine: {engine_label}")

    banner(f"1) GOVERNED READ - {doc.name}")
    agent, read_result, guarantee_ok = governed_read(doc)
    if not read_result.executed or read_result.result is None or not read_result.result.ok:
        err = read_result.result.error if read_result.result else "no result"
        print(f"  read was blocked: {err}")
        return 1
    text = read_result.result.output
    print(f"  read OK: {len(text):,} characters   |   read-only guarantee holds: {guarantee_ok}")
    print(f"  signed why-record: {read_result.why_id}")

    if _looks_scanned(text):
        print("  no text layer found (scanned/image-only PDF) - running governed vision-OCR fallback ...")
        ocr = vision_transcribe(doc, provider)
        if ocr.strip():
            text = ocr
            print(f"  vision OCR recovered {len(text):,} characters")
        else:
            print("  vision OCR recovered no text (needs a vision-capable model; the offline "
                  "provider and non-vision deployments cannot OCR).")

    try:
        rep = run(provider, text, agent, read_result, guarantee_ok, threshold)
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
