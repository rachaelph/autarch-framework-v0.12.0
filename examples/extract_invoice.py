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
     tamper-evident ledger (you can prove which file was read, when, and by whom).
  2. INTAKE / EXTRACTION (as-is steps 3-5) — vendor, amount, PO#, AFE#, state, site, line items,
     tax charged, description are pulled into structured fields.
  3. ASSET CATEGORY (step 7) — what asset is actually being created (dispenser, canopy, tank, ...).
  4. TASK VALIDATION (step 8) — the biggest pain: an invoice coded 'repair' may really be a new
     capital asset. The agent VALIDATES the task against the description + project, not just matches
     it, and recommends a re-task when needed.
  5. CAPEX vs OPEX (step 9) — capitalize vs expense, with the depreciation consequence noted.
  6. TAX DETERMINATION (steps 10-12) — taxable?, jurisdiction, expected rate/amount, use-tax owed,
     and an EXCEPTION flag when tax was under/over-collected (Circle K self-assesses use tax).
  7. GROUNDING + PANELS — every extracted value is checked against the signed source (anti-
     hallucination); a deterministic safety panel scans the untrusted invoice text for prompt-
     injection and the output for PII.
  8. CONFIDENCE ROUTING — the overall confidence is the worst step's confidence (fail-closed).
     Below the threshold (default 0.85) OR any tax exception -> route to a human reviewer with a
     feedback loop; otherwise auto-approve. This is the "~85% confidence -> human" design.
  9. EVIDENCE PACKAGE (step 15) — a decision, the rationale for each call, source citations, the
     signed why-record id, and the guarantee/panel results — the audit trail, produced automatically.

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
    Citer,
    DocumentAdapter,
    Invariant,
    MAFModelProvider,
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
    "STEP: ASSET_CATEGORY": json.dumps({
        "asset_category": "Fuel Dispenser / Fuel-Island Equipment",
        "confidence": 0.93,
        "rationale": ("Two brand-new Encore 700S dispensers with sumps and flex connectors are "
                      "forecourt fueling equipment being installed, not a repair part."),
    }),
    "STEP: TASK_VALIDATION": json.dumps({
        "suggested_task": "New Fuel Dispenser Installation (Capital)",
        "existing_task_ok": False,
        "confidence": 0.88,
        "rationale": ("The invoice supplies and installs NEW dispensers tied to AFE-2025-IL-0091 - "
                      "a new capital asset, not repair/maintenance. If coded 'repair', re-task to the "
                      "capital fuel-island task in POPA to avoid CIP aging."),
    }),
    "STEP: CAPEX_OPEX": json.dumps({
        "classification": "CapEx",
        "confidence": 0.90,
        "rationale": ("Installing entirely new fuel-island dispensers creates a new asset (not a "
                      "like-for-like part replacement), so it is capitalized under AFE-2025-IL-0091."),
        "depreciation_note": ("Begins depreciating per the AFE's CIP rules once placed in service; "
                              "capitalize to the fuel-dispenser asset class."),
    }),
    "STEP: TAX_DETERMINATION": json.dumps({
        "taxable": True,
        "jurisdiction_state": "IL",
        "expected_tax_rate": 0.10,
        "expected_tax_amount": 2450.00,
        "tax_charged_amount": 196.00,
        "use_tax_owed": 2254.00,
        "exception": True,
        "exception_reason": ("Illinois tax on tangible fuel-island equipment should be ~10% "
                             "($2,450.00) on the $24,500.00 taxable base; the invoice billed only "
                             "$196.00. Circle K self-assesses and owes ~$2,254.00 use tax."),
        "confidence": 0.86,
        "rationale": ("Tangible personal property (dispensers, sumps) installed in IL is taxable; "
                      "under-collected tax becomes a self-assessed use-tax exception."),
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
    model, argv = _split_flag(argv, "--model")
    auth, argv = _split_flag(argv, "--auth")
    thr, argv = _split_flag(argv, "--threshold")
    path = argv[0] if argv else None
    try:
        threshold = float(thr) if thr is not None else DEFAULT_THRESHOLD
    except ValueError:
        threshold = DEFAULT_THRESHOLD
    return path, (model or DEFAULT_MODEL), (auth or "auto").lower(), threshold, demo, as_json


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
        from agent_framework.openai import OpenAIChatClient

        kwargs = dict(azure_endpoint=endpoint, api_version=api_version)
        if prefer_aad:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider

            kwargs["azure_ad_token_provider"] = get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
            )
        else:
            kwargs["api_key"] = api_key
        return OpenAIChatClient(model=deployment, async_client=AsyncAzureOpenAI(**kwargs))

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


def validate_task(provider, fields, asset) -> dict:
    prompt = (
        "STEP: TASK_VALIDATION\n"
        "Validate the CAPITALIZATION TASK for this invoice - do NOT just accept the existing code. "
        "An invoice coded 'repair' may really be a NEW capital asset (tasks exist only on capital "
        "AFE items; expenses carry a GL code only). Decide the correct task from the description, "
        "PO/AFE, and asset, and whether an existing 'repair'-style coding would be wrong. Return "
        'JSON: {"suggested_task": "", "existing_task_ok": true, "confidence": 0.0, "rationale": ""}.\n\n'
        f"INVOICE FIELDS:\n{json.dumps(fields, indent=2)}\n"
        f"ASSET CATEGORY: {asset.get('asset_category', '')}\n\nJSON:"
    )
    return _ask(provider, "task_validation", _JUDGE_SYS, prompt)


def classify_capex_opex(provider, fields, asset) -> dict:
    prompt = (
        "STEP: CAPEX_OPEX\n"
        "Decide CapEx vs OpEx. Installing an ENTIRELY NEW asset (e.g. a new fuel island) is CapEx; "
        "replacing a like-for-like part may be OpEx. Capitalization drives when depreciation begins "
        "(CIP rules from the AFE). Return JSON: "
        '{"classification": "CapEx", "confidence": 0.0, "rationale": "", "depreciation_note": ""}.\n\n'
        f"INVOICE FIELDS:\n{json.dumps(fields, indent=2)}\n"
        f"ASSET CATEGORY: {asset.get('asset_category', '')}\n\nJSON:"
    )
    return _ask(provider, "capex_opex", _JUDGE_SYS, prompt)


def determine_tax(provider, fields, capex) -> dict:
    prompt = (
        "STEP: TAX_DETERMINATION\n"
        "Determine the sales/use-tax treatment. Decide if the work is taxable, the state that should "
        "receive the tax, the expected rate and amount on the taxable base, the tax actually charged, "
        "and the use tax Circle K must self-assess (Circle K does NOT go back to vendors; it pays use "
        "tax). Flag an EXCEPTION when the charged tax is materially wrong. Return JSON: "
        '{"taxable": true, "jurisdiction_state": "", "expected_tax_rate": 0.0, '
        '"expected_tax_amount": 0.0, "tax_charged_amount": 0.0, "use_tax_owed": 0.0, '
        '"exception": false, "exception_reason": "", "confidence": 0.0, "rationale": ""}.\n\n'
        f"INVOICE FIELDS:\n{json.dumps(fields, indent=2)}\n"
        f"CAPITALIZATION: {capex.get('classification', '')}\n\nJSON:"
    )
    return _ask(provider, "tax_determination", _JUDGE_SYS, prompt)


def _confidence(step: dict) -> float:
    try:
        return max(0.0, min(1.0, float(step.get("confidence", 0.0))))
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------------------
def run(provider, text: str, agent, read_result, guarantee_ok: bool, threshold: float) -> dict:
    fields = extract_invoice_fields(provider, text)
    asset = classify_asset(provider, fields)
    task = validate_task(provider, fields, asset)
    capex = classify_capex_opex(provider, fields, asset)
    tax = determine_tax(provider, fields, capex)

    # Anti-hallucination: extracted fields must be grounded in the signed source (derived
    # determinations are reasoned, so they carry confidence instead of a verbatim check).
    ungrounded = check_grounding(fields, text, exempt=("currency",))

    # Deterministic guardrails: scan the UNTRUSTED invoice text for prompt-injection, the output
    # for PII. No model needed - these always run.
    safety = safety_panel(injection=True, pii=True)
    safety_report = safety.evaluate({
        "prompt_injection": text,
        "pii_exposure": json.dumps({**fields, **tax}),
    })
    quality = quality_panel(source=text, required=REQUIRED_FIELDS)
    quality_report = quality.evaluate({
        "completeness": json.dumps(fields),
        "groundedness": " ".join(v for v in fields.values() if v),
    })

    # Confidence routing: worst step wins (fail-closed); a tax exception always escalates.
    confidences = {
        "asset": _confidence(asset), "task": _confidence(task),
        "capex": _confidence(capex), "tax": _confidence(tax),
    }
    overall = min(confidences.values()) if confidences else 0.0
    has_exception = bool(tax.get("exception"))
    route = "HUMAN_REVIEW" if (overall < threshold or has_exception or ungrounded) else "AUTO_APPROVE"

    # Citations: point each key determination at the sentence in the source that supports it.
    citer = Citer(text)
    citations = {}
    for name, value in (
        ("asset", asset.get("asset_category")),
        ("task", task.get("suggested_task")),
        ("state", fields.get("state")),
        ("total_amount", fields.get("total_amount")),
    ):
        c = citer.cite(value) if value else None
        if c is not None:
            citations[name] = {"value": value, "quote": c.text.strip(), "method": c.method}

    return {
        "fields": fields,
        "determinations": {"asset": asset, "task": task, "capex_opex": capex, "tax": tax},
        "confidence": {**confidences, "overall": round(overall, 3), "threshold": threshold},
        "routing": route,
        "grounding": {"all_grounded": not ungrounded,
                      "flagged": [{"field": f, "value": v, "why": w} for f, v, w in ungrounded]},
        "safety": {"passed": safety_report.passed, "rows": safety_report.rows()},
        "quality": {"passed": quality_report.passed, "score": round(quality_report.score, 3),
                    "rows": quality_report.rows()},
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
    f, d = rep["fields"], rep["determinations"]
    tax = d["tax"]

    banner("INVOICE")
    print(f"  vendor      : {f.get('vendor_name') or '-'}")
    print(f"  invoice #   : {f.get('invoice_number') or '-'}   date: {f.get('invoice_date') or '-'}")
    print(f"  PO / AFE    : {f.get('po_number') or '-'} / {f.get('afe_number') or '-'}")
    print(f"  site / state: {f.get('site_number') or '-'} / {f.get('state') or '-'}")
    print(f"  total       : {_money(f.get('total_amount'))}   tax billed: {_money(f.get('tax_charged'))}")
    print(f"  description : {f.get('description') or '-'}")

    banner("DETERMINATIONS")
    print(f"  asset category : {d['asset'].get('asset_category') or '-'}")
    ok = d["task"].get("existing_task_ok")
    print(f"  task           : {d['task'].get('suggested_task') or '-'}"
          f"   (existing coding OK: {ok})")
    if ok is False:
        print(f"      -> RE-TASK recommended: {d['task'].get('rationale', '')}")
    print(f"  capex/opex     : {d['capex_opex'].get('classification') or '-'}"
          f"   - {d['capex_opex'].get('depreciation_note', '')}")
    print(f"  taxable        : {tax.get('taxable')}  jurisdiction: {tax.get('jurisdiction_state') or '-'}")
    print(f"  expected tax   : {_money(tax.get('expected_tax_amount'))}"
          f"  ({_pct(tax.get('expected_tax_rate'))})")
    print(f"  tax charged    : {_money(tax.get('tax_charged_amount'))}")
    print(f"  use tax owed   : {_money(tax.get('use_tax_owed'))}")
    if tax.get("exception"):
        print(f"  ** TAX EXCEPTION: {tax.get('exception_reason', '')}")

    banner("GOVERNANCE & CONFIDENCE")
    ev, conf = rep["evidence"], rep["confidence"]
    print(f"  read-only guarantee : {ev['guarantee_read_only']}  (agent cannot write or delete)")
    print(f"  signed why-record   : {ev['why_id']}  (provenance verifies: {ev['provenance_verifies']})")
    print(f"  all values grounded : {rep['grounding']['all_grounded']}")
    for flag in rep["grounding"]["flagged"]:
        print(f"      ! ungrounded: {flag['field']} = {flag['value']!r} ({flag['why']})")
    print(f"  safety panel        : {'PASS' if rep['safety']['passed'] else 'FAIL'}"
          f"  (prompt-injection + PII scan of the invoice)")
    for name, score, passed, reasons in rep["safety"]["rows"]:
        if not passed:
            print(f"      ! {name}: {reasons}")
    print(f"  quality panel       : {'PASS' if rep['quality']['passed'] else 'FAIL'}"
          f"  (score {rep['quality']['score']})")
    print(f"  confidence          : asset {conf['asset']:.2f} | task {conf['task']:.2f} | "
          f"capex {conf['capex']:.2f} | tax {conf['tax']:.2f}  ->  overall {conf['overall']:.2f}")

    banner("DECISION")
    if rep["routing"] == "AUTO_APPROVE":
        print(f"  AUTO-APPROVE  (overall confidence {conf['overall']:.2f} >= {conf['threshold']:.2f},"
              f" no exceptions) - validated BEFORE payment.")
    else:
        reasons = []
        if conf["overall"] < conf["threshold"]:
            reasons.append(f"confidence {conf['overall']:.2f} < {conf['threshold']:.2f}")
        if tax.get("exception"):
            reasons.append("tax exception")
        if not rep["grounding"]["all_grounded"]:
            reasons.append("ungrounded value(s)")
        print(f"  ROUTE TO HUMAN REVIEW  ({'; '.join(reasons)}).")
        print("  Evidence package + recommendations attached; reviewer's correction feeds the loop.")


def _pct(v) -> str:
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return "-"


# --------------------------------------------------------------------------------------------------
def main() -> int:
    try:  # UTF-8 console so currency/dashes render on legacy Windows code pages
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

    path, model, auth_mode, threshold, demo, as_json = parse_args(sys.argv[1:])
    if not path and not demo:
        print("usage: python examples/extract_invoice.py <invoice.pdf> [--model azure:<deployment>] "
              "[--auth aad|key|auto] [--threshold 0.85] [--json]")
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
