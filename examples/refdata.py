"""Governed reference-data for ``extract_invoice.py`` — the master data that makes the tax and
capitalization calls RULES-FIRST instead of LLM-only.

Four reference files live in ``examples/reference/`` (illustrative *seed* data — NOT authoritative
tax advice; in production these come from Circle K's tax-dept taxability matrix, Avalara for live
rates, and the ERP task/PO masters):

  * ``seed-taxability-matrix.json`` — ``matrix[state][item_type] -> "T"|"E"|"A"`` (Taxable / Exempt /
    Ambiguous-review) plus ``tax_rates[state]``. Drives step 4 (tax). ``A`` routes to SME by design.
  * ``seed-task-codes.json``       — 50 task codes with ``cap_eligible`` (CapEx vs OpEx), ``asset_class``,
    ``useful_life_months``, ``depreciation``. Drives step 3 (classify).
  * ``seed-po-records.json``       — PO master with ``alt_po_numbers`` + ``vendor_aliases`` for fuzzy
    matching. Drives PO/AFE reconciliation (validate the invoice against the committed PO).
  * ``seed-history.json``          — past routing decisions (precedent / threshold calibration).

The files are read ONCE, under an autarch agent granted only ``file.read`` and PROVEN unable to write
or delete — so every reference read is signed and auditable, exactly like ``extract.py`` reads its
governed ``ref.*`` tables. Everything degrades gracefully: if the data is missing the caller simply
falls back to the model-only path.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

from autarch import Agent, Invariant, capability
from autarch.adapters.filesystem import FileSystemAdapter

_REF_DIR = Path(__file__).resolve().parent / "reference"
_FILES = {
    "taxability": "seed-taxability-matrix.json",
    "task_codes": "seed-task-codes.json",
    "po_records": "seed-po-records.json",
    "history": "seed-history.json",
}


def load_reference():
    """GOVERNED, read-only load of the four reference files. Returns ``(data, gov)`` where ``data``
    maps each key to its parsed JSON (missing/broken files are simply absent) and ``gov`` records the
    read-only guarantee result + the signed why-record id of each read. Never raises."""
    data, why_ids = {}, []
    guarantee_ok = None
    ws = tempfile.mkdtemp(prefix="autarch_refdata_")
    try:
        agent = Agent(
            intent="read invoice reference data (taxability, task codes, PO master, history)",
            adapters=[FileSystemAdapter(root=str(_REF_DIR))],
            grants=[capability("file.read", scope={"path_prefix": "."})],  # read-only by construction
            workspace=ws,
        )
        report = agent.guarantee([Invariant.forbid("file.write"), Invariant.forbid("file.delete")])
        guarantee_ok = report.all_hold
        for key, fname in _FILES.items():
            try:
                res = agent.enact("file.read", {"path": fname})
                if res.executed and res.result is not None and res.result.ok:
                    data[key] = json.loads(res.result.output)
                    why_ids.append(res.why_id)
            except Exception:
                pass  # missing/broken file -> caller falls back to the model-only path
    except Exception:
        pass
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    return data, {"guarantee_read_only": guarantee_ok, "why_ids": why_ids, "loaded": sorted(data)}


# --------------------------------------------------------------------------------------------------
# Lookups. Deterministic; the model only supplies the fuzzy MAPPING (line -> item_type / task code /
# PO), and these functions return the AUTHORITATIVE reference value.
# --------------------------------------------------------------------------------------------------
_LEGEND = {"T": "Taxable", "E": "Exempt", "A": "Ambiguous - requires review"}


def item_types(data) -> list:
    """The list of item-type categories the taxability matrix is keyed by (for the mapping prompt)."""
    tx = data.get("taxability") or {}
    return list(tx.get("item_types") or [])


def task_code_catalog(data) -> list:
    """``[(code, description, cap_eligible)]`` for the task-code mapping prompt (compact)."""
    out = []
    for t in (data.get("task_codes") or []):
        out.append((t.get("code", ""), t.get("description", ""), bool(t.get("cap_eligible", False))))
    return out


def taxability(data, state, item_type):
    """Look up ``(verdict, verdict_label, rate)`` for a ship-to state + item type. ``verdict`` is
    ``T``/``E``/``A`` (or ``None`` when unknown); ``rate`` is the state rate plus a configured local
    add-on. Callers must treat it as a state-base estimate when no local rate is configured."""
    tx = data.get("taxability") or {}
    matrix = tx.get("matrix") or {}
    rates = tx.get("tax_rates") or {}
    local_rates = tx.get("local_rates") or {}
    st = (state or "").strip().upper()
    verdict = (matrix.get(st) or {}).get(item_type)
    state_rate = rates.get(st)
    local = local_rates.get(st)
    local = local if isinstance(local, (int, float)) else 0.0
    rate = None if state_rate is None else round(state_rate + local, 5)
    return verdict, _LEGEND.get(verdict), rate


def item_type_descriptors(data) -> dict:
    """``{item_type: short keyword descriptor}`` to steer the classification prompt so each line lands
    in the right taxability bucket (alarm gear -> Security & Surveillance Systems; install labor /
    travel / freight -> Professional Services). Sourced from the hints the semantic index embeds."""
    return {it: _ITEM_TYPE_HINTS.get(it, "") for it in item_types(data)}


def task_lookup(data, code):
    """Return the task-code record for ``code`` (case-insensitive), or ``None``."""
    code = (code or "").strip().lower()
    if not code:
        return None
    for t in (data.get("task_codes") or []):
        if str(t.get("code", "")).strip().lower() == code:
            return t
    return None


_FREIGHT_RE = re.compile(r"\b(freight|shipping|delivery fee|handling|fuel surcharge|tariff surcharge)\b", re.I)
_SERVICE_RE = re.compile(r"\b(travel|mileage|site survey|consulting|engineering|design fee)\b", re.I)
_FINANCE_RE = re.compile(r"\b(interest|late[- ]?payment|administrative fee)\b", re.I)
_FIXTURE_RE = re.compile(r"\b(cabinet|cabinetry|store fixture|casework)\b", re.I)
_INSTALL_RE = re.compile(r"\b(install|installation)\b", re.I)
_RENTAL_CHARGE_RE = re.compile(r"\b(liability waiver|personal property expense)\b", re.I)


def reference_classifications(data, header, lines) -> list:
    """Classify lines from an exact PO match when model classifications are unavailable.

    Explicit freight, service, and finance lines use their dedicated task codes. All other lines
    inherit the matched PO's primary task. Records without a valid task/item-type pair remain empty
    so callers route them to review instead of inventing a determination.
    """
    rec, score, how = match_po(
        data,
        invoice_number=header.get("invoice_number", ""),
        po_number=header.get("po_number", ""),
        vendor_name=header.get("vendor_name", ""),
    )
    if rec is None or how not in {"id", "id+vendor"}:
        return [{} for _ in lines]

    primary_code = str(rec.get("task_code") or "").strip()
    out = []
    for line in lines:
        description = str(line.get("description") or "")
        override = True
        if _FINANCE_RE.search(description):
            code = "TC-9060"
        elif _FREIGHT_RE.search(description):
            code = "TC-9050"
        elif _SERVICE_RE.search(description):
            code = "TC-9030"
        elif _FIXTURE_RE.search(description):
            code = "TC-6020"
        elif _INSTALL_RE.search(description):
            code = primary_code
        elif _RENTAL_CHARGE_RE.search(description):
            code = primary_code
        else:
            code = primary_code
            override = False
        task = task_lookup(data, code)
        item_type = ("Professional Services" if _INSTALL_RE.search(description)
                     else str((task or {}).get("item_type") or "").strip())
        if task is None or not item_type:
            out.append({})
            continue
        capex = "CapEx" if task.get("cap_eligible") else "OpEx"
        out.append({
            "capex_opex": capex,
            "asset_category": task.get("asset_class", ""),
            "suggested_task": task.get("description", ""),
            "existing_task_ok": code == primary_code,
            "item_type": item_type,
            "task_code": code,
            "confidence": round(0.9 if score >= 0.9 else 0.85, 2),
            "rationale": f"Reference fallback from matched PO {rec.get('po_number')} and task {code}.",
            "_reference_fallback": True,
            "_reference_override": override,
        })
    return out


def _sim(a, b) -> float:
    """Fuzzy similarity in [0,1] with a containment boost — for PO ids and vendor names."""
    a, b = str(a).lower().strip(), str(b).lower().strip()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


def match_po(data, invoice_number="", po_number="", vendor_name="", min_score=0.55):
    """Fuzzy-match an invoice to the PO master by PO#/invoice# (incl. ``alt_po_numbers``) and vendor
    (incl. ``vendor_aliases``). Returns ``(record, score, how)`` or ``(None, score, how)`` below
    ``min_score``. ``how`` describes which signals matched (id / vendor / id+vendor)."""
    records = data.get("po_records") or []
    ids = [s for s in (str(po_number).strip(), str(invoice_number).strip()) if s]
    best, best_score, how = None, 0.0, "none"
    for rec in records:
        rec_ids = [str(rec.get("po_number", ""))] + [str(x) for x in (rec.get("alt_po_numbers") or [])]
        id_score = max((_sim(k, rid) for k in ids for rid in rec_ids), default=0.0)
        names = [str(rec.get("vendor_name", ""))] + [str(x) for x in (rec.get("vendor_aliases") or [])]
        v_score = max((_sim(vendor_name, n) for n in names), default=0.0) if vendor_name else 0.0
        # weight ids heavily; vendor is a corroborating signal
        score = (id_score * 0.7 + v_score * 0.3) if id_score else (v_score * 0.5)
        if score > best_score:
            best, best_score = rec, score
            how = "id+vendor" if (id_score >= 0.6 and v_score >= 0.6) else ("id" if id_score >= 0.6 else "vendor")
    if best is not None and best_score >= min_score:
        return best, round(best_score, 3), how
    return None, round(best_score, 3), how


def po_discrepancies(rec, header, line_results):
    """Compare a matched PO record against the extracted invoice header + line classifications;
    return a list of human-readable discrepancy strings (empty when everything lines up)."""
    if not rec:
        return []
    out = []
    inv_state = (header.get("state") or "").strip().upper()
    po_state = (rec.get("location_state") or "").strip().upper()
    if inv_state and po_state and inv_state != po_state:
        out.append(f"ship-to state {inv_state} != PO state {po_state}")
    po_task = str(rec.get("task_code") or "").strip()
    if po_task and line_results:
        used = {str(r.get("task_code") or "").strip() for r in line_results if r.get("task_code")}
        if used and po_task not in used:
            out.append(f"no line matches PO task {po_task} ({rec.get('asset_class', '')}); lines use {sorted(used)}")
    budget = rec.get("budget_amount")
    total = header.get("total_amount")
    try:
        if budget and total and float(str(total).replace(",", "")) > float(budget):
            out.append(f"invoice total {total} exceeds PO budget {budget}")
    except (TypeError, ValueError):
        pass
    return out


def precedents(data, vendor_name="", item_type="", state="", limit=5):
    """Find similar PAST decisions in the processing history (step 8: 'validate against historical
    decisions'). Scores each history row by vendor + item type + ship-to state and returns the top
    matches plus a summary (count, avg confidence, routing distribution). Empty when none match."""
    hist = data.get("history") or []
    v = (vendor_name or "").lower().strip()
    it = (item_type or "").lower().strip()
    st = (state or "").upper().strip()
    scored = []
    for h in hist:
        s = 0.0
        if v and _sim(v, h.get("vendor_name", "")) >= 0.6:
            s += 2.0
        if it and it == str(h.get("item_type", "")).lower():
            s += 1.5
        if st and st == str(h.get("ship_to_state", "")).upper():
            s += 1.0
        if s > 0:
            scored.append((s, h))
    scored.sort(key=lambda x: -x[0])
    matches = [h for _, h in scored[:limit]]
    if not matches:
        return [], {"count": 0}
    confs = [float(h.get("overall_confidence") or 0) for h in matches]
    routing = {}
    for h in matches:
        r = h.get("routing_result", "?")
        routing[r] = routing.get(r, 0) + 1
    taxab = {}
    for h in matches:
        tx = h.get("taxability", "?")
        taxab[tx] = taxab.get(tx, 0) + 1
    summary = {
        "count": len(matches),
        "avg_confidence": round(sum(confs) / len(confs), 3) if confs else 0.0,
        "routing": routing,
        "taxability": taxab,
        "example": matches[0].get("scenario_label", ""),
    }
    return matches, summary


# --------------------------------------------------------------------------------------------------
# Semantic mapping (embedding-based, DETERMINISTIC). Instead of letting the model free-choose an item
# type / task code every run (non-deterministic, sometimes wrong), we embed the task-code catalog +
# item-type definitions ONCE and map each invoice line to its NEAREST entry by cosine similarity -
# same input -> same answer, every run. Embedder-agnostic: use the offline HashingEmbedder ('hash',
# lexical + deterministic, no deps) or plug a learned one ('ollama:nomic-embed-text', an Azure/OpenAI
# text-embedding deployment) for richer meaning via the same interface.
# --------------------------------------------------------------------------------------------------
# Keyword-rich definitions per item type so even a lexical embedder disambiguates well, and so the
# classification prompt steers each line to the right taxability bucket. Security/alarm gear is its
# OWN taxable category (tangible personal property) - distinct from genuinely-exempt life-safety
# "Safety Equipment" and from POS/network "IT & Electronics". Install labor / travel / freight /
# surcharges are Professional Services (services), even when they relate to a security system.
_ITEM_TYPE_HINTS = {
    "Fuel Equipment": "fuel dispenser pump nozzle hose tank dispensing fuel island DEF gauging pipeline",
    "Construction Materials": "construction materials lumber steel concrete paving asphalt roofing drywall "
                              "insulation fasteners sealant adhesive fixtures shelving countertop",
    "Safety Equipment": "fire protection fire extinguisher fire suppression sprinkler spill containment "
                        "eyewash first aid ppe personal protective bollard guard rail life-safety",
    "HVAC & Mechanical": "hvac heating ventilation air conditioning refrigeration walk-in cooler freezer "
                          "compressor ductwork thermostat rooftop unit mechanical",
    "IT & Electronics": "point of sale pos register network switch router firewall server computer monitor "
                        "printer barcode scanner card reader payment terminal back-office electronics",
    "Security & Surveillance Systems": "security burglar intrusion alarm system control panel motion sensor "
                        "pir glassbreak detector door window contact siren strobe horn keypad camera cctv "
                        "surveillance access control low-voltage security cabling wire mounting hardware "
                        "bracket enclosure for the alarm system",
    "Vehicles & Heavy Equipment": "vehicle truck fleet forklift excavator loader machinery heavy equipment "
                                  "generator compressor air system",
    "Professional Services": "installation labor install wiring labor service consulting engineering design "
                            "freight shipping delivery handling travel mileage trip charge fuel surcharge fee permit",
    "Environmental": "environmental remediation compliance regulatory permit spill monitoring assessment",
    "Real Property Repair & Installation": "canopy site improvement installed real property repair erection labor "
                                             "demolition fascia construction installation",
    "Signage & Display": "sign signage illuminated cabinet pricer LED display shroud wordmark price sign",
    "Equipment Rental": "rental rent portable storage container leased equipment container guard",
    "Freight & Delivery": "freight shipping delivery handling fuel surcharge tariff surcharge retail delivery fee",
    "Finance Charges": "interest late payment liability waiver administrative fee finance charge",
}


def build_semantic_index(data, embedder):
    """Embed the task-code catalog + item-type definitions ONCE for deterministic nearest-neighbour
    mapping. Returns ``{"tasks": [(rec, vec)], "items": [(item_type, vec)]}`` or None on failure."""
    try:
        tasks = []
        for t in (data.get("task_codes") or []):
            text = " ".join(str(t.get(k, "")) for k in ("description", "category", "asset_class"))
            tasks.append((t, embedder.embed(text)))
        itypes = []
        for it in item_types(data):
            itypes.append((it, embedder.embed(f"{it} {_ITEM_TYPE_HINTS.get(it, '')}")))
        if not tasks or not itypes:
            return None
        return {"tasks": tasks, "items": itypes}
    except Exception:
        return None


def map_line_semantic(index, line_desc, embedder):
    """Deterministic nearest task_code + item_type for a line description (cosine over the pre-built
    index). Returns ``{task_code, task_desc, task_score, item_type, item_score}`` or None."""
    if not index or not line_desc:
        return None
    try:
        from autarch.intelligence.embedding import cosine
        v = embedder.embed(str(line_desc))
        best_t, best_ts = None, -1.0
        for rec, tv in index["tasks"]:
            s = cosine(v, tv)
            if s > best_ts:
                best_ts, best_t = s, rec
        best_i, best_is = None, -1.0
        for it, iv in index["items"]:
            s = cosine(v, iv)
            if s > best_is:
                best_is, best_i = s, it
        if best_t is None or best_i is None:
            return None
        return {"task_code": best_t.get("code"), "task_desc": best_t.get("description"),
                "task_score": round(best_ts, 3), "item_type": best_i, "item_score": round(best_is, 3)}
    except Exception:
        return None
