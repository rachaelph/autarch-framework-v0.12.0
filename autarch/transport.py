"""Network transport for the mesh — self-contained, stdlib-only node sync.

Replaces (or complements) file-bundle exchange with direct node-to-node HTTP.
There is no broker and no external dependency: the server is Python's stdlib
``http.server`` and the client is ``urllib``.

Security rides on the **realm key**, not on the transport. Every bundle is
AEAD-encrypted and authenticated end-to-end (see ``mesh.py``), so even over plain
HTTP a non-member can only see ciphertext and cannot forge a record. The server
binds to **loopback (127.0.0.1) by default** — secure by default; pass
``host="0.0.0.0"`` to opt into cross-machine sync. (You may still front it with
TLS on hostile networks, but the ledger's confidentiality and integrity do not
depend on it.)

Endpoints:
    GET  /autarch/health  -> {"ok": true, "node": "<node_id>"}
    GET  /autarch/bundle  -> the encrypted bundle of this node's ledger
    POST /autarch/bundle  -> merge a peer's bundle; returns a JSON merge report
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Tuple

from .mesh import MergeReport, Realm, export_bundle, import_bundle
from .memory import WhyMemory

_BUNDLE_PATH = "/autarch/bundle"
_HEALTH_PATH = "/autarch/health"
_MAX_BODY = 64 * 1024 * 1024  # 64 MB cap on an incoming bundle


class _Handler(BaseHTTPRequestHandler):
    server_version = "Autarch"

    def log_message(self, *args):  # silence default stderr logging
        pass

    def _cfg(self) -> dict:
        return self.server.autarch  # type: ignore[attr-defined]

    def _memory(self) -> WhyMemory:
        cfg = self._cfg()
        # A fresh, short-lived connection per request: SQLite connections are not
        # safe to share across threads, and WAL makes concurrent file access fine.
        return WhyMemory(cfg["db_path"], node_id=cfg["realm"].node_id)

    def do_GET(self):
        cfg = self._cfg()
        if self.path == _HEALTH_PATH:
            self._send_json(200, {"ok": True, "node": cfg["realm"].node_id})
            return
        if self.path == _BUNDLE_PATH:
            memory = self._memory()
            try:
                blob = export_bundle(memory, cfg["realm"])
            finally:
                memory.close()
            self._send_bytes(200, blob)
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        cfg = self._cfg()
        if self.path != _BUNDLE_PATH:
            self._send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > _MAX_BODY:
            self._send_json(400, {"error": "missing or oversized body"})
            return
        blob = self.rfile.read(length)
        memory = self._memory()
        try:
            report = import_bundle(memory, cfg["realm"], blob)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        finally:
            memory.close()
        if report.policies_added and cfg.get("workspace"):
            cfg["realm"].save(cfg["workspace"])
        self._send_json(200, {
            "added": report.added,
            "skipped": report.skipped,
            "rejected": report.rejected,
            "policies_added": report.policies_added,
            "from_node": report.from_node,
        })

    # -- helpers ----------------------------------------------------------
    def _send_bytes(self, code: int, body: bytes, content_type="application/octet-stream"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, obj: dict):
        self._send_bytes(code, json.dumps(obj).encode("utf-8"), "application/json")


class MeshServer:
    """Serve and receive encrypted ledger bundles over stdlib HTTP."""

    def __init__(self, realm: Realm, db_path, workspace=None, host: str = "127.0.0.1", port: int = 0):
        self.realm = realm
        self._httpd = ThreadingHTTPServer((host, port), _Handler)
        self._httpd.autarch = {  # type: ignore[attr-defined]
            "realm": realm,
            "db_path": str(db_path),
            "workspace": str(workspace) if workspace else None,
        }
        self._thread: Optional[threading.Thread] = None

    @property
    def address(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> str:
        """Start serving in a background daemon thread; return the base URL."""
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.address

    def serve_forever(self) -> None:
        """Serve in the foreground until interrupted (for the CLI)."""
        try:
            self._httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self._httpd.server_close()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
def _request(url: str, data: Optional[bytes] = None, timeout: float = 10.0) -> bytes:
    method = "POST" if data is not None else "GET"
    request = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"peer returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"could not reach peer at {url}: {exc}") from exc


def health(base_url: str, timeout: float = 5.0) -> dict:
    raw = _request(base_url.rstrip("/") + _HEALTH_PATH, timeout=timeout)
    return json.loads(raw.decode("utf-8"))


def pull(base_url: str, memory: WhyMemory, realm: Realm, timeout: float = 10.0) -> MergeReport:
    """Fetch a peer's ledger and merge it into `memory`."""
    blob = _request(base_url.rstrip("/") + _BUNDLE_PATH, timeout=timeout)
    return import_bundle(memory, realm, blob)


def push(base_url: str, memory: WhyMemory, realm: Realm, timeout: float = 10.0) -> dict:
    """Send this node's ledger to a peer; return the peer's merge report."""
    blob = export_bundle(memory, realm)
    raw = _request(base_url.rstrip("/") + _BUNDLE_PATH, data=blob, timeout=timeout)
    return json.loads(raw.decode("utf-8"))


def sync(base_url: str, memory: WhyMemory, realm: Realm, timeout: float = 10.0) -> Tuple[dict, MergeReport]:
    """Bidirectional sync: push our ledger, then pull the peer's. Idempotent."""
    pushed = push(base_url, memory, realm, timeout=timeout)
    pulled = pull(base_url, memory, realm, timeout=timeout)
    return pushed, pulled


# --------------------------------------------------------------------------- #
# Gossip — N-node epidemic convergence
# --------------------------------------------------------------------------- #
@dataclass
class GossipReport:
    """The result of one gossip round across a set of peers."""

    peers: list = field(default_factory=list)  # [{"url","ok","pulled","pushed","error"}]

    @property
    def total_pulled(self) -> int:
        return sum(p.get("pulled", 0) for p in self.peers)

    @property
    def total_pushed(self) -> int:
        return sum(p.get("pushed", 0) for p in self.peers)

    @property
    def total_propagated(self) -> int:
        """Records moved in either direction this round (the convergence signal)."""
        return self.total_pulled + self.total_pushed

    @property
    def reached(self) -> int:
        return sum(1 for p in self.peers if p.get("ok"))

    def summary(self) -> str:
        return (
            f"gossiped {len(self.peers)} peer(s): {self.reached} reached, "
            f"{self.total_propagated} record(s) propagated "
            f"(+{self.total_pulled} in, {self.total_pushed} out)"
        )


def gossip(peers, memory: WhyMemory, realm: Realm, timeout: float = 10.0) -> GossipReport:
    """Sync with every known peer in one round; tolerate unreachable ones.

    Because the ledger is a grow-only set merged by id, gossip converges
    *epidemically*: records reach the whole mesh transitively (A→B, B→C) without
    every pair ever syncing directly. One dead peer never aborts the round.
    """
    report = GossipReport()
    for url in peers:
        entry = {"url": url, "ok": False, "pulled": 0, "pushed": 0, "error": ""}
        try:
            pushed, pulled = sync(url, memory, realm, timeout=timeout)
            entry["ok"] = True
            entry["pulled"] = pulled.added
            entry["pushed"] = pushed.get("added", 0)
        except (RuntimeError, ValueError) as exc:
            entry["error"] = str(exc)
        report.peers.append(entry)
    return report
