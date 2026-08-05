"""The governance gateway — Autarch as infrastructure, not just a library.

A library governs Python that imports it. A *gateway* governs everything: any
agent, in any language, local or remote, routes its intended actions through one
long-running governed process. The gateway runs the full deterministic pipeline
(capability kernel + policy + budget), signs the outcome into the tamper-evident
ledger, and — when a policy requires human ratification — parks the action in the
async approval plane instead of executing it.

Stdlib only (``http.server`` + ``urllib``), loopback by default (secure by
default; pass ``host="0.0.0.0"`` to expose it). Governance decisions are
serialized behind a lock so the shared SQLite ledger stays consistent under the
threading server — correctness over raw throughput, which is the right trade for
a control plane.

Endpoints (all JSON):
    GET  /autarch/health              -> {"ok", "node", "capabilities"}
    GET  /autarch/capabilities        -> granted capabilities + scope descriptions
    POST /autarch/enact               -> govern+execute a known action; may park
                                         for approval. Body: {capability, params,
                                         actor?, wait_seconds?}
    GET  /autarch/approvals           -> pending approvals
    POST /autarch/approvals/ratify    -> {id, by}
    POST /autarch/approvals/overrule  -> {id, by, reason}
    GET  /autarch/prove?why_id=...    -> the signed why-record + verification
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

from . import scoping
from .agent import Agent
from .approval import ApprovalQueue
from .contracts import CapabilityGrant

_MAX_BODY = 4 * 1024 * 1024


class _Handler(BaseHTTPRequestHandler):
    server_version = "AutarchGateway"

    def log_message(self, *args):
        pass

    @property
    def gw(self) -> "GovernanceGateway":
        return self.server.gateway  # type: ignore[attr-defined]

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > _MAX_BODY:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _send(self, code: int, obj: dict):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlparse(self.path)
        if route.path == "/autarch/health":
            self._send(200, self.gw.health())
        elif route.path == "/autarch/capabilities":
            self._send(200, {"capabilities": self.gw.capabilities()})
        elif route.path == "/autarch/approvals":
            self._send(200, {"pending": self.gw.pending_approvals()})
        elif route.path == "/autarch/prove":
            qs = parse_qs(route.query)
            why_id = (qs.get("why_id") or [""])[0]
            result = self.gw.prove(why_id)
            self._send(200 if result.get("found") else 404, result)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        route = urlparse(self.path)
        body = self._read_json()
        if route.path == "/autarch/enact":
            self._send(200, self.gw.enact(body))
        elif route.path == "/autarch/approvals/ratify":
            self._send(200, self.gw.ratify(body.get("id", ""), body.get("by", "autarch")))
        elif route.path == "/autarch/approvals/overrule":
            self._send(200, self.gw.overrule(
                body.get("id", ""), body.get("by", "autarch"), body.get("reason", "")))
        else:
            self._send(404, {"error": "not found"})


class GovernanceGateway:
    """A governed control plane fronting one Agent's kernel/policy/budget/ledger."""

    def __init__(
        self,
        agent: Optional[Agent] = None,
        *,
        grants: Optional[List[CapabilityGrant]] = None,
        adapters=None,
        workspace: str = "./gateway",
        policies=None,
        budget=None,
        approvals: Optional[ApprovalQueue] = None,
    ):
        if agent is None:
            from pathlib import Path as _Path

            from .memory import WhyMemory
            ws = _Path(workspace)
            # The gateway serializes governance behind a lock, so one connection
            # shared across the threading server's request threads is safe.
            shared_memory = WhyMemory(ws / ".autarch" / "why.db", same_thread=False)
            agent = Agent(
                intent="gateway",
                grants=grants or [],
                adapters=adapters,
                workspace=workspace,
                policies=policies,
                budget=budget,
                memory=shared_memory,
                auto_preside=False,  # the gateway never auto-ratifies; humans do
            )
        self.agent = agent
        self.approvals = approvals or ApprovalQueue(
            str(self.agent.workspace / ".autarch" / "approvals.db")
        )
        self._lock = threading.Lock()
        self._httpd: Optional[ThreadingHTTPServer] = None

    # -- governed operations ---------------------------------------------
    def capabilities(self) -> List[dict]:
        out = []
        for g in self.agent.grants:
            out.append({
                "name": g.name,
                "scope": scoping.describe(g.scope, g.limits) or ["(unconstrained)"],
            })
        return out

    def health(self) -> dict:
        return {
            "ok": True,
            "node": self.agent.node_id,
            "capabilities": [g.name for g in self.agent.grants],
            "pending_approvals": len(self.approvals.pending_list()),
        }

    def enact(self, body: dict) -> dict:
        """Govern (and maybe execute) a known action proposed by an external agent."""
        capability = body.get("capability")
        params = body.get("params") or {}
        actor = body.get("actor", "external-agent")
        if not capability:
            return {"error": "missing 'capability'"}

        # Policy pre-check: if the action requires human ratification, park it in
        # the approval plane instead of running it. The kernel/budget still get
        # the final say when it is later enacted.
        from .contracts import Action

        action = Action(capability=capability, params=params)
        policy = self.agent.policy_engine.evaluate(action)
        if policy.requires_ratify:
            appr = self.approvals.request(
                intent_text=body.get("intent", capability),
                capability=capability, params=params,
                rationale=policy.note(), requested_by=actor,
                quorum=int(body.get("quorum", 1)),
                ttl_seconds=body.get("ttl_seconds"),
            )
            wait_s = body.get("wait_seconds")
            if wait_s:
                appr = self.approvals.wait(appr.id, timeout=float(wait_s))
                if appr.ratified:
                    return self._execute(capability, params)
            return {"status": "pending_approval", "approval_id": appr.id,
                    "policy": policy.note()}

        return self._execute(capability, params)

    def _execute(self, capability: str, params: dict) -> dict:
        with self._lock:  # serialize governance for a consistent shared ledger
            result = self.agent.enact(capability, params)
        return {
            "status": "executed" if result.executed else "denied",
            "executed": result.executed,
            "why_id": result.why_id,
            "gate_reason": result.gate.reason if result.gate else "",
            "output": _safe(result.result_output if hasattr(result, "result_output") else None),
        }

    def ratify(self, approval_id: str, by: str) -> dict:
        appr = self.approvals.ratify(approval_id, by=by)
        if appr.ratified:
            outcome = self._execute(appr.capability, appr.params)
            outcome["approval"] = appr.status
            return outcome
        return {"status": appr.status, "approvals": appr.approvals, "quorum": appr.quorum}

    def overrule(self, approval_id: str, by: str, reason: str) -> dict:
        appr = self.approvals.overrule(approval_id, by=by, reason=reason)
        return {"status": appr.status, "reason": appr.decision_reason}

    def pending_approvals(self) -> List[dict]:
        return [
            {"id": a.id, "capability": a.capability, "params": a.params,
             "rationale": a.rationale, "requested_by": a.requested_by,
             "quorum": a.quorum, "approvals": a.approvals}
            for a in self.approvals.pending_list()
        ]

    def prove(self, why_id: str) -> dict:
        rec = self.agent.memory.get(why_id)
        if rec is None:
            return {"found": False, "why_id": why_id}
        return {
            "found": True,
            "why_id": why_id,
            "capability": rec.capability,
            "executed": rec.executed,
            "gate_allowed": rec.gate_allowed,
            "integrity_ok": self.agent.memory.verify(why_id),
            "provenance_ok": self.agent.memory.verify_provenance(why_id),
            "signer": rec.signer,
        }

    # -- server lifecycle -------------------------------------------------
    def serve(self, host: str = "127.0.0.1", port: int = 8799) -> ThreadingHTTPServer:
        httpd = ThreadingHTTPServer((host, port), _Handler)
        httpd.gateway = self  # type: ignore[attr-defined]
        self._httpd = httpd
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd = None


def _safe(value):
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


# --- a thin typed client (proves any HTTP client, in any language, works) ------

class GatewayClient:
    """A stdlib client for the gateway (Python convenience; the wire is plain HTTP)."""

    def __init__(self, base_url: str = "http://127.0.0.1:8799", timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        import urllib.request

        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def health(self) -> dict:
        return self._call("GET", "/autarch/health")

    def capabilities(self) -> dict:
        return self._call("GET", "/autarch/capabilities")

    def enact(self, capability: str, params: Optional[dict] = None, **kw) -> dict:
        return self._call("POST", "/autarch/enact",
                          {"capability": capability, "params": params or {}, **kw})

    def pending(self) -> dict:
        return self._call("GET", "/autarch/approvals")

    def ratify(self, approval_id: str, by: str = "autarch") -> dict:
        return self._call("POST", "/autarch/approvals/ratify", {"id": approval_id, "by": by})

    def overrule(self, approval_id: str, by: str = "autarch", reason: str = "") -> dict:
        return self._call("POST", "/autarch/approvals/overrule",
                          {"id": approval_id, "by": by, "reason": reason})
