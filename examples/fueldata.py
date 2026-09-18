"""Governed reference/source data for ``extract_fuel.py`` - the fuel-diversion determination.

Five files live in ``examples/reference/`` (illustrative *seed* data - NOT production; in a real
deployment these are the PDI reporting database views + the Fuel Track portal + the state fuel-tax
rates):

  * ``seed-fuel-deliveries.json``  - confirmed bill-of-lading / delivery view (one row per load).
  * ``seed-fuel-terminals.json``   - terminal_id -> ORIGIN state (where the load was lifted).
  * ``seed-fuel-sites.json``       - site_id -> ship-to state (planned + actual destinations).
  * ``seed-fueltrack.json``        - Fuel Track diversion registry (the authoritative diversion #).
  * ``seed-fuel-tax-rates.json``   - state motor-fuel excise per gallon (tax-impact math).

All read ONCE, under an autarch agent granted only ``file.read`` and PROVEN unable to write or
delete - so every read is signed and auditable. This mirrors the "read from the replicated PDI
reporting DB; never write directly (PDI has no write API)" constraint from the workshops: the write
back to the PDI diversion form is a SEPARATE, downstream (RPA) step - this module only reads.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from autarch import Agent, Invariant, capability
from autarch.adapters.filesystem import FileSystemAdapter

_REF_DIR = Path(__file__).resolve().parent / "reference"
_FILES = {
    "deliveries": "seed-fuel-deliveries.json",
    "terminals": "seed-fuel-terminals.json",
    "sites": "seed-fuel-sites.json",
    "fueltrack": "seed-fueltrack.json",
    "tax_rates": "seed-fuel-tax-rates.json",
}


def load_reference():
    """GOVERNED, read-only load of the five source files. Returns ``(data, gov)`` where ``data`` maps
    each key to its parsed JSON and ``gov`` records the read-only guarantee + the signed why-record id
    of each read. Never raises (missing/broken files are simply absent)."""
    data, why_ids = {}, []
    guarantee_ok = None
    ws = tempfile.mkdtemp(prefix="autarch_fueldata_")
    try:
        agent = Agent(
            intent="read fuel-diversion source data (deliveries, terminals, sites, fuel track, tax rates)",
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
                pass  # missing/broken file -> caller degrades gracefully
    except Exception:
        pass
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    return data, {"guarantee_read_only": guarantee_ok, "why_ids": why_ids, "loaded": sorted(data)}


# --------------------------------------------------------------------------------------------------
# Deterministic lookups + detection. The rules ENGINE is authoritative for whether a diversion
# occurred and which jurisdiction applies; a model (when supplied) only cross-checks + narrates.
# --------------------------------------------------------------------------------------------------
def deliveries(data) -> list:
    return list((data.get("deliveries") or {}).get("deliveries") or [])


def _sites(data) -> dict:
    return (data.get("sites") or {}).get("sites") or {}


def _terminals(data) -> dict:
    return (data.get("terminals") or {}).get("terminals") or {}


def site_record(data, site_id):
    """Look up a site by id. Circle K site ids are 270-prefixed (274 for some acquired CST sites);
    the last four digits alone are NOT unique across acquisitions, so a 4-digit-only value is
    reported as AMBIGUOUS (must be kicked out for manual review), never silently guessed."""
    sites = _sites(data)
    sid = str(site_id or "").strip()
    if not sid:
        return None, "missing site id"
    if sid in sites:
        return sites[sid], "exact"
    if len(sid) == 4:  # only the last four digits were captured (ABBYY often does this)
        hits = [k for k in sites if k.endswith(sid)]
        if len(hits) == 1:
            return sites[hits[0]], f"matched 4-digit to {hits[0]}"
        return None, f"AMBIGUOUS 4-digit site '{sid}' ({len(hits)} candidates) - kick out for review"
    return None, f"no site record for '{sid}'"


def site_state(data, site_id):
    rec, how = site_record(data, site_id)
    return (rec.get("state") if rec else None), how


def terminal_state(data, terminal_id):
    t = _terminals(data).get(str(terminal_id or "").strip())
    return (t.get("state") if t else None)


def resolve_states(data, rec) -> dict:
    """Resolve the three states for one delivery: ORIGIN (terminal), PLANNED (BOL destination site =
    what the supplier billed), and ACTUAL (the site that confirmed receipt)."""
    origin = terminal_state(data, rec.get("terminal_id"))
    planned, planned_how = site_state(data, rec.get("planned_site_id"))
    actual, actual_how = site_state(data, rec.get("delivered_site_id"))
    return {
        "origin_state": origin,
        "planned_state": planned, "planned_how": planned_how,
        "actual_state": actual, "actual_how": actual_how,
    }


def detect_diversion(states: dict) -> dict:
    """A reportable diversion (in scope) is a STATE-TO-STATE change: the planned (billed) destination
    state differs from the actual delivery state. Same-state (in-state) moves are OUT of scope."""
    planned, actual = states.get("planned_state"), states.get("actual_state")
    if not planned or not actual:
        return {"is_diversion": None, "reason": "could not resolve planned/actual state - review",
                "resolvable": False}
    if planned == actual:
        return {"is_diversion": False, "reason": f"in-state ({planned}) - not a state-to-state diversion",
                "resolvable": True}
    return {"is_diversion": True, "reason": f"state-to-state diversion {planned} -> {actual}",
            "resolvable": True}


def fueltrack_match(data, carrier_fein, bol_number, lift_date):
    """Find the Fuel Track diversion record (the authoritative diversion number) by FEIN + BOL + lift
    date. Returns the record or ``None`` (meaning the carrier has not yet reported it)."""
    fein = str(carrier_fein or "").strip()
    bol = str(bol_number or "").strip()
    lift = str(lift_date or "").strip()
    for d in (data.get("fueltrack") or {}).get("diversions") or []:
        if (str(d.get("carrier_fein", "")).strip() == fein
                and str(d.get("bol_number", "")).strip() == bol
                and str(d.get("lift_date", "")).strip() == lift):
            return d
    return None


def fuel_tax_rate(data, state):
    rates = (data.get("tax_rates") or {}).get("motor_fuel_tax") or {}
    return rates.get((state or "").strip().upper())


def tax_impact(data, planned_state, actual_state, gallons) -> dict:
    """Tax recalculation impact of the jurisdiction change: credit the planned (billed) state and
    assess the actual delivery state. Net = actual_tax - planned_tax (positive = additional tax owed
    to the actual state; negative = net credit)."""
    try:
        g = float(gallons or 0)
    except (TypeError, ValueError):
        g = 0.0
    rp = fuel_tax_rate(data, planned_state)
    ra = fuel_tax_rate(data, actual_state)
    planned_tax = round(g * rp, 2) if rp is not None else None
    actual_tax = round(g * ra, 2) if ra is not None else None
    net = (round((actual_tax or 0) - (planned_tax or 0), 2)
           if (planned_tax is not None and actual_tax is not None) else None)
    return {"gallons": g, "planned_rate": rp, "actual_rate": ra,
            "planned_tax": planned_tax, "actual_tax": actual_tax, "net_tax_delta": net}
