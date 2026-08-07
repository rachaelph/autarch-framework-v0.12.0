"""Deterministic decision cache for line-item classification.

The reasoning model gives the most accurate CapEx/OpEx + item-type + task-code call, but Azure
OpenAI decoding is not reproducible run-to-run (even at temperature 0 the same line can land on a
different item type, flipping its tax verdict). For an auditable tax determination that must be the
SAME every run, this module memoizes each classification keyed on (vendor, ship-to state, normalized
description): the FIRST time a line is seen the model classifies it and the decision is persisted;
every later run RESTORES that decision verbatim - LLM-quality accuracy, byte-for-byte reproducibility.

The cache is the tool's own working state, stored OUTSIDE the read-only governed reference data, so
it never touches the signed source document or the reference tables the agent is proven unable to
write. Delete the file to re-derive every decision from the model.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

# The classification fields that fully determine the downstream tax + capitalization outcome. Only
# these are cached/restored, so a cache hit reproduces the exact determination. The `_llm_tax_*`
# fields carry the INDEPENDENT LLM tax verdict (diagram step 8) so the dual-validation gate (step 9,
# LLM vs tax engine) is reproducible run-to-run too.
CACHE_FIELDS = (
    "item_type", "task_code", "capex_opex", "asset_category",
    "suggested_task", "existing_task_ok", "confidence", "rationale",
    "_llm_tax_taxable", "_llm_tax_reason",
)

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^a-z0-9 ]+")

DEFAULT_CACHE_PATH = os.path.join(os.path.dirname(__file__), "decision_cache.json")


def normalize_desc(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace - so trivial formatting differences in the
    same line description map to the SAME cache key."""
    s = _PUNCT.sub(" ", str(text or "").lower())
    return _WS.sub(" ", s).strip()


def cache_key(vendor: str, state: str, description: str) -> str:
    """Stable key for one line: same vendor + ship-to state + description -> same decision."""
    return "|".join((
        normalize_desc(vendor),
        (str(state or "").strip().upper()),
        normalize_desc(description),
    ))


def load_cache(path: str) -> dict:
    """Load the cache file (``{key: decision}``); returns an empty dict if it doesn't exist yet."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_cache(path: str, cache: dict) -> None:
    """Persist the cache atomically (write to a temp file, then replace)."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def apply_cache(cache: dict, header: dict, lines: list, classifications: list, model_label: str = "") -> dict:
    """Restore cached decisions onto ``classifications`` (hits) and record freshly-seen lines (misses).

    Mutates ``classifications`` in place: a HIT overwrites the model's fresh pick with the persisted
    one (reproducible); a MISS leaves the fresh pick and stores it for next time. Returns stats
    ``{hits, new, keys}`` and marks each classification with ``_cache`` = 'hit' | 'new'.
    """
    vendor = header.get("vendor_name") or header.get("vendor") or ""
    state = header.get("state") or ""
    hits = new = 0
    for i, ln in enumerate(lines):
        if i >= len(classifications):
            break
        c = classifications[i]
        key = cache_key(vendor, state, ln.get("description", ""))
        entry = cache.get(key)
        if isinstance(entry, dict) and entry.get("decision"):
            for f in CACHE_FIELDS:
                if f in entry["decision"]:
                    c[f] = entry["decision"][f]
            c["_cache"] = "hit"
            hits += 1
        else:
            cache[key] = {
                "decision": {f: c.get(f) for f in CACHE_FIELDS},
                "vendor": vendor,
                "state": (str(state or "").strip().upper()),
                "description": ln.get("description", ""),
                "model": model_label,
                "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            c["_cache"] = "new"
            new += 1
    return {"hits": hits, "new": new, "keys": len(cache)}
