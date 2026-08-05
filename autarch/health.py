"""Health & readiness — operational checks for containerized deployment.

A production process needs a fast, dependency-free way to answer "am I healthy?"
for liveness/readiness probes. `health_check` inspects a workspace and reports a
structured status: storage reachable, ledger intact, signing identity present,
crypto availability, and the running version.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_ERROR = "error"


def health_check(workspace="./sandbox") -> Dict[str, Any]:
    """Return a structured health report for a workspace (safe, read-only)."""
    from . import __version__
    from .memory import WhyMemory
    from .provenance import NodeIdentity, available

    checks: Dict[str, Any] = {}
    ws = Path(workspace)

    # Storage + ledger integrity.
    db = ws / ".autarch" / "why.db"
    if db.exists():
        try:
            mem = WhyMemory(db)
            ok, broken = mem.verify_chain()
            checks["storage"] = {"ok": True, "records": mem.count()}
            checks["ledger"] = {"ok": ok, "broken_at": broken}
            mem.close()
        except Exception as exc:  # noqa: BLE001
            checks["storage"] = {"ok": False, "error": str(exc)}
            checks["ledger"] = {"ok": False}
    else:
        checks["storage"] = {"ok": True, "records": 0, "note": "no ledger yet"}
        checks["ledger"] = {"ok": True}

    # Signing identity + crypto.
    identity = NodeIdentity.load(ws) if (ws / ".autarch" / "identity.json").exists() else None
    checks["identity"] = {
        "present": identity is not None,
        "encrypted_at_rest": (identity.is_encrypted_at_rest(ws) if identity else False),
    }
    checks["crypto"] = {"available": available()}

    # Overall status: error if storage/ledger broken, degraded if crypto missing.
    failed = (not checks["storage"].get("ok", False)) or (not checks["ledger"].get("ok", True))
    if failed:
        status = STATUS_ERROR
    elif not checks["crypto"]["available"]:
        status = STATUS_DEGRADED
    else:
        status = STATUS_OK

    return {
        "status": status,
        "version": __version__,
        "workspace": str(ws),
        "ts": time.time(),
        "checks": checks,
    }
