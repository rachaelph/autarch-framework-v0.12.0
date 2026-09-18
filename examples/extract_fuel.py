"""Detect FUEL DIVERSIONS and produce a governed, audit-ready PDI diversion + tax-recalculation
determination - the "fuel diversion" use case, end to end, governed by autarch.

Business problem (from the fuel-accounting workshops): a carrier lifts fuel at a terminal for one
state, but actually delivers it to a store in a DIFFERENT state (a state-to-state diversion). The
supplier billed tax for the originally-planned state, so the tax jurisdiction is now wrong. Today
this is caught late (invoice reconciliation), a human flags it in a PDI "diversion" form, hunts the
diversion number in the third-party Fuel Track portal, and re-triggers the tax calculation. The goal
is to shift this LEFT: scan confirmed deliveries daily, detect diversions the moment the data lands,
recommend the correct jurisdiction + tax impact, attach the Fuel Track diversion number (or notify
the carrier when it's missing), and hand a ready-to-post form payload to the RPA writer.

What this program does, per confirmed delivery, governed end to end:

  1. GOVERNED READ (read-only) - loads the PDI reporting views (deliveries, terminals, sites), the
     Fuel Track registry, and the state fuel-tax rates under an agent PROVEN unable to write/delete;
     every read is signed. (PDI has no write API; the form write is a separate downstream RPA step.)
  2. DETECT DIVERSION - resolves ORIGIN (terminal) / PLANNED (BOL destination site = what was billed)
     / ACTUAL (site that confirmed receipt) states via deterministic joins, and flags a STATE-TO-STATE
     diversion (planned != actual). In-state moves are out of scope.
  3. TAX JURISDICTION + RECALC - determines the correct jurisdiction (actual state) and the tax impact
     (credit the planned state, assess the actual state) from the per-gallon fuel-tax rates.
  4. FUEL TRACK MATCH - looks up the authoritative diversion number by FEIN + BOL + lift date; if it
     is missing, flags a carrier notification; if its reported state disagrees, routes to review.
  5. DUAL VALIDATION (optional model) - an independent model verdict cross-checks the rules engine;
     agreement -> confident, divergence -> analyst review. Falls back to deterministic when offline.
  6. PDI FORM PAYLOAD + ROUTE - builds the exact diversion-form update (original state, diverted-to
     state, diversion number, recalc trigger) and routes: AUTO_READY / NOTIFY_CARRIER / SME_REVIEW.
  7. EVIDENCE - per-delivery rationale, the signed why-record, a carrier-notification draft, cost.

The optional REASONING runs on the Microsoft Agent Framework (MAF) + Azure OpenAI, exactly like
examples/extract_invoice.py; without a model the full determination still runs deterministically.

Usage:
    python examples/extract_fuel.py --demo                              # offline, daily PDI scan
    python examples/extract_fuel.py --model azure:gpt-4.1-rp --auth aad # + model dual-validation
    python examples/extract_fuel.py --since 2026-08-12 --html out.html --csv out.csv
    python examples/extract_fuel.py --bol BOL-100234 --json

    # DOCUMENT-DRIVEN (mirrors extract_invoice.py): read a fuel BOL / supplier invoice PDF via Azure
    # Document Intelligence, extract the fuel fields, then JOIN the PDI delivery data by BOL for the
    # actual delivered state and run the diversion determination:
    python examples/extract_fuel.py "C:/path/to/fuel_invoice.pdf" --model azure:gpt-4.1-rp --auth aad --doci "https://<docintel>.cognitiveservices.azure.com/"
    python examples/extract_fuel.py examples/sample_fuel_invoice.txt --model azure:gpt-4.1-rp --auth aad
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from autarch import (  # noqa: E402
    Agent,
    DocumentAdapter,
    Invariant,
    MAFModelProvider,
    capability,
    get_usage_meter,
    usage_label,
)
from autarch.util import extract_json  # noqa: E402
import fueldata  # noqa: E402
import docintel  # noqa: E402  Azure Document Intelligence prebuilt-invoice extraction (optional)

DEFAULT_MODEL = "azure:gpt-4.1-rp"
AUTO_THRESHOLD = 0.85
_SYS = "You are a precise fuel-tax accounting assistant. Output ONLY one JSON object, no prose."


# --------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------
def _split_flag(argv, flag):
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            val = argv[i + 1]
            del argv[i:i + 2]
            return val, argv
        del argv[i]
    return None, argv


def parse_args(argv):
    demo = "--demo" in argv
    as_json = "--json" in argv
    argv = [a for a in argv if a not in ("--demo", "--json")]
    csv_on, csv_path = False, None
    if "--csv" in argv:
        csv_on = True
        i = argv.index("--csv")
        if i + 1 < len(argv) and argv[i + 1].lower().endswith(".csv"):
            csv_path = argv[i + 1]; del argv[i:i + 2]
        else:
            del argv[i]
    html_on, html_path = False, None
    if "--html" in argv:
        html_on = True
        i = argv.index("--html")
        if i + 1 < len(argv) and argv[i + 1].lower().endswith((".html", ".htm")):
            html_path = argv[i + 1]; del argv[i:i + 2]
        else:
            del argv[i]
    # --doci [endpoint]: read a fuel document (BOL / fuel-supplier invoice) via Azure Document
    # Intelligence. Bare --doci uses AZURE_DOCINTEL_ENDPOINT. A document path (positional) switches the
    # tool from the daily PDI scan to the single-document flow.
    doci_endpoint = None
    if "--doci" in argv:
        i = argv.index("--doci")
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if nxt and not nxt.startswith("--") and not nxt.lower().endswith(".pdf"):
            doci_endpoint = nxt; del argv[i:i + 2]
        else:
            doci_endpoint = os.environ.get("AZURE_DOCINTEL_ENDPOINT") or ""; del argv[i]
    model, argv = _split_flag(argv, "--model")
    auth, argv = _split_flag(argv, "--auth")
    since, argv = _split_flag(argv, "--since")
    bol, argv = _split_flag(argv, "--bol")
    doc = next((a for a in argv if not a.startswith("--")), None)  # positional document path
    return (model or DEFAULT_MODEL), (auth or "auto").lower(), demo, as_json, csv_on, csv_path, \
        html_on, html_path, since, bol, doc, doci_endpoint


def banner(title):
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def _money(v):
    return "-" if v is None else f"${v:,.2f}"


# --------------------------------------------------------------------------------------------------
# Microsoft Agent Framework wiring (same proven pattern as extract_invoice.py).
# --------------------------------------------------------------------------------------------------
def _is_auth_error(exc):
    m = str(exc).lower()
    return any(s in m for s in ("authenticationtypedisabled", "key based authentication is disabled",
                                "permissiondenied", "invalid api key", "access denied", "401", "403"))


def _make_client_factory(deployment, endpoint, api_version, use_aad):
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    prefer_aad = use_aad or not api_key

    def _factory():
        from openai import AsyncAzureOpenAI
        from agent_framework.openai import OpenAIChatCompletionClient
        kwargs = dict(azure_endpoint=endpoint, api_version=api_version)
        if prefer_aad:
            from azure.identity import (AzureCliCredential, ChainedTokenCredential,
                                        DefaultAzureCredential, get_bearer_token_provider)
            tenant = os.environ.get("AZURE_OPENAI_TENANT_ID") or os.environ.get("AZURE_TENANT_ID")
            cred = (AzureCliCredential(tenant_id=tenant) if tenant
                    else ChainedTokenCredential(AzureCliCredential(), DefaultAzureCredential()))
            kwargs["azure_ad_token_provider"] = get_bearer_token_provider(
                cred, "https://cognitiveservices.azure.com/.default")
        else:
            kwargs["api_key"] = api_key
        return OpenAIChatCompletionClient(model=deployment, async_client=AsyncAzureOpenAI(**kwargs))

    return _factory, ("Entra ID" if prefer_aad else "api-key")


def connect_model(model, auth_mode, demo):
    """Return ``(provider, label)`` - a MAF provider when Azure is configured, else ``(None, ...)`` so
    the pipeline runs fully deterministic. ``--demo`` forces the deterministic path."""
    if demo:
        return None, "offline (deterministic engine only)"
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if not endpoint:
        return None, "offline (AZURE_OPENAI_ENDPOINT not set)"
    try:
        from agent_framework.openai import OpenAIChatCompletionClient  # noqa: F401
    except Exception:
        return None, "offline (agent-framework not installed)"
    deployment = model.split("azure:", 1)[1] if model.startswith("azure:") else \
        (os.environ.get("AZURE_OPENAI_DEPLOYMENT") or model)
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    modes = {"aad": [True], "key": [False]}.get(auth_mode, [False, True] if os.environ.get("AZURE_OPENAI_API_KEY") else [True])
    for idx, use_aad in enumerate(modes):
        factory, label = _make_client_factory(deployment, endpoint, api_version, use_aad)
        provider = MAFModelProvider(factory, agent_name="autarch-fuel-agent", model_label=deployment,
                                    run_kwargs={"client_kwargs": {"temperature": 0, "seed": 7}})
        try:
            provider.complete("Reply with the single word: OK.")
            return provider, f"Microsoft Agent Framework on '{deployment}' (auth {label})"
        except Exception as exc:  # noqa: BLE001
            provider.close()
            if _is_auth_error(exc) and idx + 1 < len(modes):
                continue
            print(f"  MAF/Azure connection failed ({type(exc).__name__}: {exc}); running deterministic.")
            return None, "offline (model unreachable)"
    return None, "offline"


def _ask(provider, label, prompt):
    with usage_label(label):
        raw = provider.complete(prompt, system=_SYS)
    try:
        return extract_json(raw) or {}
    except Exception:
        return {}


# --------------------------------------------------------------------------------------------------
# Optional model layer: dual validation + carrier-notification drafting.
# --------------------------------------------------------------------------------------------------
def llm_validate(provider, rec, states, tax):
    """Independent model verdict on the diversion + jurisdiction (for dual validation)."""
    prompt = (
        "STEP: VALIDATE_DIVERSION\n"
        "A fuel load was lifted at a terminal for a planned destination state and confirmed delivered "
        "at a site in another state. Decide whether this is a state-to-state diversion and which state "
        "should be the tax jurisdiction, then rate your confidence. Return JSON "
        '{"is_diversion": true, "correct_jurisdiction_state": "", "confidence": 0.0, "rationale": ""}.\n\n'
        f"RECORD:\n{json.dumps({'bol': rec.get('bol_number'), 'product': rec.get('product'), 'gallons': rec.get('gallons'), 'origin_state': states.get('origin_state'), 'planned_state': states.get('planned_state'), 'actual_state': states.get('actual_state'), 'tax_impact': tax}, indent=2)}\n\nJSON:"
    )
    return _ask(provider, "validate_diversion", prompt)


def llm_notification(provider, rec, states, tax):
    """Draft the carrier notification asking them to record the diversion in Fuel Track."""
    prompt = (
        "STEP: DRAFT_CARRIER_NOTICE\n"
        "Write a short, professional notice (3-5 sentences) to a fuel carrier asking them to record a "
        "state-to-state diversion in the Fuel Track portal so the diversion number can be captured for "
        "tax reporting. Include the BOL number, lift date, planned vs actual state, and gallons. "
        'Return JSON {"subject": "", "body": ""}.\n\n'
        f"RECORD:\n{json.dumps({'carrier': rec.get('carrier_name'), 'bol': rec.get('bol_number'), 'lift_date': rec.get('lift_date'), 'planned_state': states.get('planned_state'), 'actual_state': states.get('actual_state'), 'gallons': rec.get('gallons')}, indent=2)}\n\nJSON:"
    )
    return _ask(provider, "draft_notice", prompt)


def _template_notice(rec, states):
    return {
        "subject": f"Action required: record state-to-state fuel diversion for BOL {rec.get('bol_number')}",
        "body": (f"Our records show BOL {rec.get('bol_number')} (lifted {rec.get('lift_date')}, "
                 f"{rec.get('gallons')} gal {rec.get('product')}) was billed to "
                 f"{states.get('planned_state')} but delivered to a site in {states.get('actual_state')}. "
                 f"Please record this diversion in Fuel Track and provide the diversion number so we can "
                 f"correct the tax jurisdiction for reporting."),
    }


# --------------------------------------------------------------------------------------------------
# Document ingestion (Azure Document Intelligence + governed read) - mirrors extract_invoice.py so a
# fuel-supplier invoice / bill-of-lading PDF can DRIVE the pipeline. The document supplies the
# ORIGIN / PLANNED (billed) side + BOL# + gallons + tax charged; the ACTUAL delivered state is joined
# from the PDI delivery-confirmation data by BOL (a document alone can't prove where the truck went).
# --------------------------------------------------------------------------------------------------
def _num(v):
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def governed_read(doc):
    """Run an autarch agent that may ONLY read the document - and prove it (no write/delete)."""
    workspace = tempfile.mkdtemp(prefix="autarch_fuel_doc_")
    agent = Agent(
        intent=f"read the fuel document {doc.name}",
        adapters=[DocumentAdapter(root=str(doc.parent))],
        grants=[capability("doc.read", scope={"path_prefix": "."})],  # read-only by construction
        workspace=workspace,
    )
    report = agent.guarantee([Invariant.forbid("file.write"), Invariant.forbid("file.delete")])
    result = agent.enact("doc.read", {"path": doc.name})
    return agent, result, report.all_hold


_FUEL_EXTRACT_SYS = ("You are a precise fuel bill-of-lading / fuel-invoice extraction assistant. "
                     "Output ONLY one JSON object, no prose.")


def extract_fuel_fields(provider, text):
    """LLM extraction of the fuel-relevant fields from the document OCR/text: BOL#, carrier (+FEIN),
    origin/lift state, planned/billed destination state, gallons, product, lift date, tax charged."""
    prompt = (
        "STEP: EXTRACT_FUEL_DOC\n"
        "Extract the fuel-delivery fields from this document. ORIGIN/LIFT state is where the fuel was "
        "loaded at the terminal; PLANNED/BILLED destination state is the state the load was billed / "
        "destined to at lift; delivered_state is the actual ship-to state IF the document states it; "
        "tax_charged is any fuel/excise tax billed. Use empty string / 0 when a field is absent. "
        "Return JSON: "
        '{"bol_number": "", "carrier_name": "", "carrier_fein": "", "terminal_name": "", '
        '"origin_state": "", "planned_state": "", "delivered_state": "", "product": "", '
        '"gallons": 0, "lift_date": "", "tax_charged": 0.0, "ship_to": ""}.\n\n'
        f"DOCUMENT:\n{(text or '')[:6000]}\n\nJSON:"
    )
    with usage_label("extract_fuel_doc"):
        raw = provider.complete(prompt, system=_FUEL_EXTRACT_SYS)
    try:
        return extract_json(raw) or {}
    except Exception:
        return {}


import re as _re  # noqa: E402

_BOL_RE = _re.compile(r"\bB[O0]L[-\s#:]*?(\d{4,})\b", _re.IGNORECASE)
_GAL_RE = _re.compile(r"([\d,]+(?:\.\d+)?)\s*(?:gal|gallons)\b", _re.IGNORECASE)


def _regex_fuel_fields(text):
    """Best-effort offline extraction (no model) - enough to exercise the flow deterministically."""
    t = text or ""
    bol = _BOL_RE.search(t)
    gal = _GAL_RE.search(t)
    return {
        "bol_number": (f"BOL-{bol.group(1)}" if bol else ""),
        "gallons": (float(gal.group(1).replace(",", "")) if gal else 0),
        "carrier_name": "", "carrier_fein": "", "origin_state": "", "planned_state": "",
        "delivered_state": "", "product": "", "lift_date": "", "tax_charged": 0.0, "ship_to": "",
    }


def _doc_only_result(data, fields, docname, provider):
    """Determination when the document's BOL has NO matching PDI delivery confirmation. We only have
    the planned/billed side from the document - the actual delivery state (and therefore a provable
    diversion) requires the PDI feed, so this routes conservatively."""
    planned = (fields.get("planned_state") or "").strip().upper() or None
    actual = (fields.get("delivered_state") or "").strip().upper() or None
    origin = (fields.get("origin_state") or "").strip().upper() or None
    base = {
        "bol_number": fields.get("bol_number"), "source": "document", "document": docname,
        "carrier_name": fields.get("carrier_name"), "carrier_type": None,
        "carrier_fein": fields.get("carrier_fein"), "product": fields.get("product"),
        "gallons": fields.get("gallons"), "origin_state": origin, "planned_state": planned,
        "actual_state": actual, "is_diversion": None, "tax": None, "fueltrack": None,
        "fueltrack_match": None, "dual_validation": None, "notification": None, "form_payload": None,
        "notes": [], "route": "SME_REVIEW", "confidence": 0.5,
        "doc_tax_charged": _num(fields.get("tax_charged")),
    }
    if not actual:
        base["route"], base["confidence"] = "PENDING", 1.0
        base["notes"].append(
            f"no PDI delivery confirmation found for BOL {fields.get('bol_number')} - the actual delivery "
            "state is unknown, so a diversion cannot be confirmed from the document alone (awaiting the "
            "PDI delivery feed).")
        return base
    det = fueldata.detect_diversion({"planned_state": planned, "actual_state": actual})
    base["is_diversion"] = det.get("is_diversion")
    if det.get("is_diversion"):
        base["tax"] = fueldata.tax_impact(data, planned, actual, fields.get("gallons"))
        base["notes"].append("diversion states came from the DOCUMENT (no PDI join) - verify against PDI")
        base["route"], base["confidence"] = "SME_REVIEW", 0.6
    else:
        base["route"], base["confidence"] = "NO_ACTION", 1.0
    return base


def determine_from_document(data, doc_path, provider, doci_endpoint):
    """Document-driven flow: governed read -> Document Intelligence / text -> LLM fuel-field extraction
    -> join the PDI delivery confirmation by BOL for the ACTUAL state -> run the fuel determination.
    Returns the same report shape as run() with a single result + an ``extraction`` block."""
    doc = Path(doc_path).expanduser()
    if not doc.exists():
        return {"error": f"file not found: {doc}"}
    ext = {"engine": None, "document": doc.name, "fields": {}, "guarantee_read_only": None, "why_id": None}

    # 1) Governed, read-only ingest (provably cannot write/delete).
    agent, read_result, guarantee_ok = governed_read(doc)
    ext["guarantee_read_only"] = guarantee_ok
    ext["why_id"] = getattr(read_result, "why_id", None)
    text = ""
    if read_result.executed and read_result.result is not None and read_result.result.ok:
        text = read_result.result.output or ""

    # 2) Document Intelligence for scanned PDFs (its OCR becomes the source), else direct text.
    di_header = {}
    if doci_endpoint and doc.suffix.lower() == ".pdf":
        tenant = (os.environ.get("AZURE_DOCINTEL_TENANT_ID") or os.environ.get("AZURE_OPENAI_TENANT_ID")
                  or os.environ.get("AZURE_TENANT_ID"))
        di = docintel.analyze_invoice(str(doc), doci_endpoint, tenant_id=tenant,
                                      api_key=os.environ.get("AZURE_DOCINTEL_KEY"))
        if di and not di.get("error"):
            text = di.get("content") or text
            di_header = di.get("header") or {}
            ext["engine"] = "document_intelligence"
    if not text and doc.suffix.lower() in (".txt", ".text"):
        try:
            text = doc.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
    if ext["engine"] is None:
        ext["engine"] = "text" if text else "none"

    # 3) Extract fuel fields (LLM when a model is available, else best-effort regex).
    fields = extract_fuel_fields(provider, text) if (provider is not None and text) else _regex_fuel_fields(text)
    gallons_f = _num(fields.get("gallons")) or 0.0
    if di_header:  # fold in what DI already lifted with confidence
        if not fields.get("carrier_name"):
            fields["carrier_name"] = di_header.get("vendor_name", "")
        if not fields.get("tax_charged"):
            fields["tax_charged"] = di_header.get("tax_charged")
        # only treat the PO field as a BOL for an ACTUAL fuel doc (has gallons) - otherwise a normal
        # (non-fuel) invoice's PO would be mistaken for a bill of lading.
        if not fields.get("bol_number") and gallons_f > 0:
            fields["bol_number"] = di_header.get("po_number", "")
    for k in ("carrier_name", "product", "planned_state", "origin_state", "delivered_state"):
        if isinstance(fields.get(k), str):  # normalize OCR whitespace/newlines
            fields[k] = " ".join(fields[k].split())
    ext["fields"] = fields
    bol = str(fields.get("bol_number", "")).strip()

    gov = {"guarantee_read_only": guarantee_ok,
           "why_ids": [ext["why_id"]] if ext["why_id"] else [], "loaded": ["document"]}

    def _wrap(det):
        return {"results": [det], "extraction": ext, "governance": gov,
                "rollup": {"scanned": 1, "diversions": 1 if det.get("is_diversion") else 0,
                           "net_tax_delta": round((det.get("tax") or {}).get("net_tax_delta") or 0.0, 2),
                           "by_route": {det["route"]: 1}}}

    # 4) Guard: a fuel delivery document is sold BY THE GALLON - no gallons means this isn't one.
    if gallons_f <= 0:
        det = {
            "bol_number": bol or None, "source": "document", "document": doc.name,
            "carrier_name": fields.get("carrier_name"), "carrier_type": None, "product": None,
            "gallons": None, "origin_state": None, "planned_state": None, "actual_state": None,
            "is_diversion": None, "tax": None, "fueltrack": None, "fueltrack_match": None,
            "dual_validation": None, "notification": None, "form_payload": None,
            "route": "NOT_FUEL_DOC", "confidence": 1.0, "doc_tax_charged": _num(fields.get("tax_charged")),
            "notes": ["this does not look like a fuel delivery document (no gallons of fuel) - the "
                      "fuel-diversion flow needs a fuel bill-of-lading or fuel-supplier invoice"],
        }
        return _wrap(det)

    # 5) Join the PDI delivery confirmation by BOL -> the ACTUAL delivered state (source of truth).
    seed = next((r for r in fueldata.deliveries(data) if str(r.get("bol_number", "")).strip() == bol), None)
    if seed is not None:
        det = determine(data, seed, provider)
        det["source"] = "document"
        det["document"] = doc.name
        det["doc_tax_charged"] = _num(fields.get("tax_charged"))
        det["notes"].append(f"extracted from {doc.name} via {ext['engine']}; joined to PDI delivery {bol}")
    else:
        det = _doc_only_result(data, fields, doc.name, provider)
    return _wrap(det)


# --------------------------------------------------------------------------------------------------
# Core determination
# --------------------------------------------------------------------------------------------------
def determine(data, rec, provider):
    """Run the full determination for ONE delivery record. Returns a result dict."""
    states = fueldata.resolve_states(data, rec)
    det = fueldata.detect_diversion(states)
    gallons = rec.get("gallons")
    out = {
        "bol_number": rec.get("bol_number"), "lift_date": rec.get("lift_date"),
        "carrier_name": rec.get("carrier_name"), "carrier_type": rec.get("carrier_type"),
        "carrier_fein": rec.get("carrier_fein"), "product": rec.get("product"), "gallons": gallons,
        "origin_state": states.get("origin_state"),
        "planned_state": states.get("planned_state"), "planned_how": states.get("planned_how"),
        "actual_state": states.get("actual_state"), "actual_how": states.get("actual_how"),
        "confirmation_status": rec.get("confirmation_status"),
        "is_diversion": det.get("is_diversion"), "detect_reason": det.get("reason"),
        "tax": None, "fueltrack": None, "fueltrack_match": None, "llm": None,
        "dual_validation": None, "notification": None, "form_payload": None,
        "route": "NO_ACTION", "confidence": 1.0, "notes": [],
    }

    # Only confirmed deliveries are actionable (once-a-day run over confirmed BOLs).
    if str(rec.get("confirmation_status", "")).lower() != "confirmed":
        out["route"], out["confidence"] = "PENDING", 1.0
        out["notes"].append("delivery not yet confirmed - will be picked up on a later run")
        return out

    if det.get("is_diversion") is None:  # states unresolved / ambiguous site -> review
        out["route"], out["confidence"] = "SME_REVIEW", 0.4
        out["notes"].append(det.get("reason"))
        return out

    if det.get("is_diversion") is False:  # in-state, out of scope
        out["route"], out["confidence"] = "NO_ACTION", 1.0
        return out

    # --- It IS a state-to-state diversion: jurisdiction + tax impact + Fuel Track match ---------- #
    tax = fueldata.tax_impact(data, states["planned_state"], states["actual_state"], gallons)
    out["tax"] = tax
    ft = fueldata.fueltrack_match(data, rec.get("carrier_fein"), rec.get("bol_number"), rec.get("lift_date"))
    out["fueltrack"] = ft

    # Optional independent model verdict (dual validation).
    engine_says = True
    if provider is not None:
        llm = llm_validate(provider, rec, states, tax)
        out["llm"] = llm
        llm_says = bool(llm.get("is_diversion"))
        juris_ok = str(llm.get("correct_jurisdiction_state", "")).strip().upper() in ("", states["actual_state"])
        agree = (llm_says == engine_says) and juris_ok
        out["dual_validation"] = "agree" if agree else "diverge"

    # Route + confidence.
    diverged = out["dual_validation"] == "diverge"
    if ft is None:
        out["route"], out["confidence"] = "NOTIFY_CARRIER", 0.9
        out["notes"].append("no Fuel Track diversion number yet - carrier must record it")
        out["notification"] = (llm_notification(provider, rec, states, tax) if provider is not None
                               else _template_notice(rec, states))
    else:
        reported = str(ft.get("reported_destination_state", "")).strip().upper()
        out["fueltrack_match"] = (reported == states["actual_state"])
        if reported != states["actual_state"]:
            out["route"], out["confidence"] = "SME_REVIEW", 0.5
            out["notes"].append(f"Fuel Track state {reported} != actual delivery state {states['actual_state']}")
        elif diverged:
            out["route"], out["confidence"] = "SME_REVIEW", 0.6
            out["notes"].append("model and rules engine disagree on the diversion - review")
        else:
            out["route"], out["confidence"] = "AUTO_READY", 0.97

    # PDI diversion-form payload (what the downstream RPA writer will post). Read-only recommendation.
    out["form_payload"] = {
        "bol_number": rec.get("bol_number"),
        "action": "flag_state_to_state_diversion",
        "original_destination_state": states["planned_state"],   # off the BOL (what was billed)
        "diverted_to_state": states["actual_state"],             # actual delivery jurisdiction
        "diversion_number": (ft or {}).get("diversion_number"),
        "net_tax_delta": tax.get("net_tax_delta"),
        "trigger_recalculation": True,
    }
    return out


def run(data, gov, provider, since, bol):
    recs = fueldata.deliveries(data)
    if bol:
        recs = [r for r in recs if str(r.get("bol_number", "")).strip() == bol.strip()]
    if since:
        recs = [r for r in recs if str(r.get("confirmation_date") or "") >= since]
    results = [determine(data, r, provider) for r in recs]

    diversions = [r for r in results if r["is_diversion"] is True]
    net = round(sum((r["tax"] or {}).get("net_tax_delta") or 0 for r in diversions
                    if (r["tax"] or {}).get("net_tax_delta") is not None), 2)
    by_route = {}
    for r in results:
        by_route[r["route"]] = by_route.get(r["route"], 0) + 1
    return {
        "results": results,
        "rollup": {
            "scanned": len(results),
            "diversions": len(diversions),
            "net_tax_delta": net,
            "by_route": by_route,
        },
        "governance": gov,
    }


# --------------------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------------------
def print_report(rep, engine_label):
    banner("FUEL DIVERSION DETERMINATION  |  Microsoft Agent Framework + autarch governance")
    print(f"  reasoning engine : {engine_label}")
    gov = rep["governance"]
    print(f"  governed read    : {', '.join(gov.get('loaded', []))}  "
          f"(read-only guarantee {gov.get('guarantee_read_only')}, {len(gov.get('why_ids', []))} signed reads)")
    ext = rep.get("extraction")
    if ext:
        f = ext.get("fields") or {}
        print(f"  document         : {ext.get('document')}  (extraction: {ext.get('engine')}, "
              f"read-only guarantee {ext.get('guarantee_read_only')})")
        print(f"  extracted        : BOL {f.get('bol_number') or '-'} | {f.get('carrier_name') or '-'} | "
              f"{f.get('gallons') or '-'} gal {f.get('product') or ''} | origin {f.get('origin_state') or '-'} "
              f"/ planned {f.get('planned_state') or '-'} | tax charged {_money(_num(f.get('tax_charged')))}")

    banner("CONFIRMED DELIVERIES  (detect -> jurisdiction -> Fuel Track -> route)")
    for r in rep["results"]:
        head = f"  [{r['bol_number']}] {r['carrier_name']} ({r.get('carrier_type')})"
        print(f"{head}   {r.get('gallons')} gal {r.get('product')}")
        route_map = {"AUTO_READY": "AUTO-POST (RPA)", "NOTIFY_CARRIER": "NOTIFY CARRIER",
                     "SME_REVIEW": "SME REVIEW", "NO_ACTION": "no action", "PENDING": "pending",
                     "NOT_FUEL_DOC": "NOT A FUEL DOCUMENT"}
        flow = (f"        origin {r.get('origin_state') or '?'} | planned {r.get('planned_state') or '?'} "
                f"-> actual {r.get('actual_state') or '?'}")
        if r["is_diversion"] is True:
            flow += "   ** DIVERSION"
        elif r["is_diversion"] is False:
            flow += "   (in-state)"
        print(flow)
        if r.get("tax"):
            t = r["tax"]
            print(f"        tax impact: {r['planned_state']} @{t.get('planned_rate')}/gal "
                  f"-> {r['actual_state']} @{t.get('actual_rate')}/gal   net {_money(t.get('net_tax_delta'))}")
        if r.get("doc_tax_charged") is not None:
            print(f"        tax billed on document: {_money(r['doc_tax_charged'])}"
                  + (f" (for planned {r.get('planned_state')})" if r.get('planned_state') else ""))
        if r.get("fueltrack"):
            ft = r["fueltrack"]
            ok = "matches" if r.get("fueltrack_match") else "MISMATCH"
            print(f"        fuel track: {ft.get('diversion_number')} (reported {ft.get('reported_destination_state')}, {ok})")
        elif r["is_diversion"] is True:
            print(f"        fuel track: (none on file - carrier must report)")
        if r.get("dual_validation"):
            print(f"        dual validation: {r['dual_validation'].upper()}"
                  + ("" if r["dual_validation"] == "agree" else "  -> review"))
        for n in r.get("notes", []):
            print(f"        note: {n}")
        if r.get("notification"):
            print(f"        carrier notice drafted: \"{r['notification'].get('subject')}\"")
        print(f"        confidence {r['confidence']:.2f}  ->  {route_map.get(r['route'], r['route'])}")

    roll = rep["rollup"]
    banner("DAILY ROLLUP")
    print(f"  deliveries scanned : {roll['scanned']}   diversions found: {roll['diversions']}")
    print(f"  net tax impact     : {_money(roll['net_tax_delta'])}  (credit planned state / assess actual state)")
    print(f"  routing            : " + ", ".join(f"{k}={v}" for k, v in sorted(roll['by_route'].items())))

    banner("DECISION")
    ready = roll["by_route"].get("AUTO_READY", 0)
    notify = roll["by_route"].get("NOTIFY_CARRIER", 0)
    review = roll["by_route"].get("SME_REVIEW", 0)
    print(f"  {ready} diversion(s) READY to post to the PDI diversion form (RPA writeback), "
          f"{notify} awaiting carrier Fuel Track entry, {review} to SME review.")
    print("  The tool reads + determines under governance; the PDI form write is a separate, signed RPA step.")


# --------------------------------------------------------------------------------------------------
# CSV / HTML
# --------------------------------------------------------------------------------------------------
_CSV_COLS = ("bol_number", "lift_date", "carrier_name", "carrier_type", "product", "gallons",
             "origin_state", "planned_state", "actual_state", "is_diversion", "planned_rate",
             "actual_rate", "net_tax_delta", "diversion_number", "fueltrack_match", "dual_validation",
             "confidence", "route")


def write_csv(rep, path):
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(_CSV_COLS)
        for r in rep["results"]:
            t = r.get("tax") or {}
            ft = r.get("fueltrack") or {}
            w.writerow([r.get("bol_number"), r.get("lift_date"), r.get("carrier_name"), r.get("carrier_type"),
                        r.get("product"), r.get("gallons"), r.get("origin_state"), r.get("planned_state"),
                        r.get("actual_state"), r.get("is_diversion"), t.get("planned_rate"),
                        t.get("actual_rate"), t.get("net_tax_delta"), ft.get("diversion_number"),
                        r.get("fueltrack_match"), r.get("dual_validation"), r.get("confidence"), r.get("route")])


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_html(rep, engine_label):
    roll = rep["rollup"]
    cls = {"AUTO_READY": "ok", "NO_ACTION": "ok", "PENDING": "muted",
           "NOTIFY_CARRIER": "warn", "SME_REVIEW": "sme"}
    rows = []
    for r in rep["results"]:
        t = r.get("tax") or {}
        ft = r.get("fueltrack") or {}
        div = ("<b>YES</b>" if r["is_diversion"] else ("no" if r["is_diversion"] is False else "?"))
        rows.append(
            f"<tr class='{cls.get(r['route'], '')}'><td>{_esc(r['bol_number'])}</td>"
            f"<td>{_esc(r['carrier_name'])}<br><span class='muted'>{_esc(r.get('carrier_type'))}</span></td>"
            f"<td>{_esc(r.get('gallons'))} gal<br><span class='muted'>{_esc(r.get('product'))}</span></td>"
            f"<td>{_esc(r.get('origin_state'))} &rarr; {_esc(r.get('planned_state'))} &rArr; <b>{_esc(r.get('actual_state'))}</b></td>"
            f"<td>{div}</td>"
            f"<td>{_esc(_money(t.get('net_tax_delta')))}</td>"
            f"<td>{_esc(ft.get('diversion_number') or '&mdash;')}</td>"
            f"<td>{_esc((r.get('dual_validation') or '').upper())}</td>"
            f"<td>{r.get('confidence'):.2f}</td>"
            f"<td>{_esc(r['route'])}</td></tr>")
    gov = rep["governance"]
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Fuel Diversion Determination</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;margin:2rem;color:#222}}
h1{{color:#0b6}}table{{border-collapse:collapse;width:100%;margin-top:1rem}}
th,td{{border:1px solid #ddd;padding:.5rem;text-align:left;font-size:.9rem;vertical-align:top}}
th{{background:#0b6;color:#fff}}.muted{{color:#888;font-size:.8rem}}
tr.ok td{{background:#f2fbf5}}tr.warn td{{background:#fff8e6}}tr.sme td{{background:#fdeeee}}
.summary{{margin-top:1rem;padding:1rem;background:#f6f6f6;border-radius:8px}}</style></head><body>
<h1>Fuel Diversion Determination</h1>
<div class='muted'>Reasoning: {_esc(engine_label)} &middot; governed read: {_esc(', '.join(gov.get('loaded', [])))}
(read-only guarantee {gov.get('guarantee_read_only')}, {len(gov.get('why_ids', []))} signed reads)</div>
<div class='summary'><b>{roll['scanned']}</b> deliveries scanned &middot; <b>{roll['diversions']}</b> diversions &middot;
net tax impact <b>{_esc(_money(roll['net_tax_delta']))}</b> &middot; routing {_esc(', '.join(f'{k}={v}' for k,v in sorted(roll['by_route'].items())))}</div>
<table><tr><th>BOL</th><th>Carrier</th><th>Load</th><th>Origin &rarr; Planned &rArr; Actual</th><th>Diversion</th>
<th>Net tax</th><th>Fuel Track #</th><th>Dual val.</th><th>Conf.</th><th>Route</th></tr>
{''.join(rows)}</table>
<p class='muted'>Illustrative seed data - not tax advice. The PDI form write is a separate governed RPA step.</p>
</body></html>"""


# --------------------------------------------------------------------------------------------------
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    model, auth_mode, demo, as_json, csv_on, csv_path, html_on, html_path, since, bol, doc, doci_endpoint = parse_args(sys.argv[1:])

    get_usage_meter().reset()
    provider, engine_label = connect_model(model, auth_mode, demo)
    try:
        data, gov = fueldata.load_reference()
        if not data.get("deliveries"):
            print("No fuel delivery data found under examples/reference/. Nothing to do.")
            return 2
        if doc:  # document-driven: read a fuel BOL / supplier invoice via Document Intelligence
            rep = determine_from_document(data, doc, provider, doci_endpoint)
            if rep.get("error"):
                print(rep["error"])
                return 2
        else:    # daily scan over the PDI delivery data
            rep = run(data, gov, provider, since, bol)
    finally:
        if provider is not None:
            provider.close()

    if as_json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        print_report(rep, engine_label)
        totals = get_usage_meter().totals()
        if totals.get("calls"):
            print(f"\n  model usage: {totals.get('calls', 0)} call(s), "
                  f"{totals.get('prompt_tokens', 0)} prompt + {totals.get('completion_tokens', 0)} completion tokens")

    if csv_on:
        out = Path(csv_path).expanduser() if csv_path else Path("fuel_diversions.csv")
        try:
            write_csv(rep, out)
            print(f"\n  CSV written: {out}")
        except Exception as exc:  # noqa: BLE001
            print(f"  (CSV failed: {exc})")
    if html_on:
        out = Path(html_path).expanduser() if html_path else Path("fuel_diversions.html")
        try:
            out.write_text(render_html(rep, engine_label), encoding="utf-8")
            print(f"  HTML written: {out}")
        except Exception as exc:  # noqa: BLE001
            print(f"  (HTML failed: {exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
