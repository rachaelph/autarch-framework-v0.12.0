"""Model Context Protocol (MCP) — speak it natively, govern it fully.

MCP is JSON-RPC 2.0 (typically over stdio). It is becoming the standard way models
and tools connect. Autarch speaks it **both directions**, with zero third-party
dependencies — it implements the wire protocol directly with stdlib `subprocess`
and `json`:

  * **`from_mcp_server(command, ...)`** connects to an external MCP server and
    wraps every tool it exposes as a *governed* Autarch capability — so an
    existing MCP tool instantly runs inside the capability kernel.

  * **`MCPServer`** exposes Autarch's governed capabilities *as* an MCP server.
    Any MCP client (Claude Desktop, an IDE, another agent) that calls it gets
    tools whose every invocation passes through the capability kernel first. This
    is the absorb-then-govern move: the ecosystem's clients get governance for free.

The protocol surface used: ``initialize``, ``tools/list``, ``tools/call``.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from typing import Callable, Dict, List, Optional

from .adapters.tool import ToolAdapter
from .contracts import Action
from .errors import AdapterError, ModelError
from .kernel import CapabilityKernel

_PROTOCOL_VERSION = "2024-11-05"


# --------------------------------------------------------------------------- #
# Client — connect to an external MCP server and govern its tools
# --------------------------------------------------------------------------- #
class MCPClient:
    """A minimal JSON-RPC MCP client over a subprocess's stdio (stdlib only)."""

    def __init__(self, command: List[str], timeout: float = 30.0):
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._timeout = timeout
        self._id = 0
        self._lock = threading.Lock()
        self._initialize()

    def _rpc(self, method: str, params: Optional[dict] = None) -> dict:
        with self._lock:
            self._id += 1
            request = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
            if self._proc.stdin is None or self._proc.stdout is None:
                raise ModelError("MCP server process has no stdio")
            try:
                self._proc.stdin.write(json.dumps(request) + "\n")
                self._proc.stdin.flush()
                line = self._proc.stdout.readline()
            except (BrokenPipeError, OSError) as exc:
                raise ModelError(f"MCP transport failed: {exc}") from exc
            if not line:
                raise ModelError("MCP server closed the connection")
            response = json.loads(line)
            if "error" in response:
                raise AdapterError(f"MCP error: {response['error']}", context={"method": method})
            return response.get("result", {})

    def _initialize(self) -> None:
        self._rpc("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "autarch", "version": "0.4.0"},
        })

    def list_tools(self) -> List[dict]:
        return self._rpc("tools/list").get("tools", [])

    def call_tool(self, name: str, arguments: Optional[dict] = None):
        return self._rpc("tools/call", {"name": name, "arguments": arguments or {}})

    def close(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()


def _mcp_schema_to_params(input_schema: dict) -> dict:
    """Translate an MCP/JSON-schema ``inputSchema`` into council param hints."""
    props = (input_schema or {}).get("properties", {})
    return {key: (spec.get("type", "value")) for key, spec in props.items()}


def from_mcp_server(command: List[str], namespace: str = "mcp", timeout: float = 30.0) -> ToolAdapter:
    """Connect to an external MCP server and wrap its tools as governed capabilities."""
    client = MCPClient(command, timeout=timeout)
    tools = client.list_tools()
    mapping: Dict[str, Callable] = {}
    schemas: Dict[str, dict] = {}
    for tool in tools:
        tname = tool.get("name")
        if not tname:
            continue
        mapping[tname] = _make_mcp_caller(client, tname)
        schemas[tname] = _mcp_schema_to_params(tool.get("inputSchema", {}))
    return ToolAdapter(mapping, namespace=namespace, schemas=schemas)


def _make_mcp_caller(client: MCPClient, tool_name: str) -> Callable:
    def call(*args, **kwargs):
        params = kwargs or (args[0] if args and isinstance(args[0], dict) else {})
        return client.call_tool(tool_name, params)
    return call


# --------------------------------------------------------------------------- #
# Server — expose Autarch's GOVERNED capabilities as an MCP server
# --------------------------------------------------------------------------- #
class MCPServer:
    """Serve Autarch capabilities to any MCP client, governed by the kernel.

    Every ``tools/call`` is authorized by the capability kernel before the adapter
    runs — so an MCP client that was never built with governance in mind suddenly
    cannot exceed the grants Autarch holds. `handle_request` is transport-free
    (great for tests and embedding); `serve_stdio` runs the real JSON-RPC loop.
    """

    def __init__(self, adapters, kernel: CapabilityKernel, name: str = "autarch", descriptions=None):
        self.adapters = list(adapters)
        self.kernel = kernel
        self.name = name
        self._descriptions = dict(descriptions or {})
        self._by_capability = {}
        for adapter in self.adapters:
            for cap in adapter.capabilities():
                self._by_capability[cap] = adapter

    def _tool_list(self) -> List[dict]:
        tools = []
        for cap, adapter in self._by_capability.items():
            schema = adapter.schema().get(cap, {})
            properties = {k: {"type": "string", "description": str(v)} for k, v in schema.items()}
            tools.append({
                "name": cap,
                "description": self._descriptions.get(cap, f"Governed Autarch capability '{cap}'"),
                "inputSchema": {"type": "object", "properties": properties},
            })
        return tools

    def handle_request(self, request: dict) -> dict:
        """Process one JSON-RPC request and return the JSON-RPC response."""
        rid = request.get("id")
        method = request.get("method")
        params = request.get("params", {}) or {}

        def ok(result):
            return {"jsonrpc": "2.0", "id": rid, "result": result}

        def err(code, message):
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}

        if method == "initialize":
            return ok({
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": self.name, "version": "0.4.0"},
            })
        if method == "tools/list":
            return ok({"tools": self._tool_list()})
        if method == "tools/call":
            cap = params.get("name", "")
            arguments = params.get("arguments", {}) or {}
            adapter = self._by_capability.get(cap)
            if adapter is None:
                return err(-32601, f"unknown tool '{cap}'")
            # GOVERNANCE: the kernel authorizes before the adapter ever runs.
            action = Action(cap, dict(arguments))
            gate = self.kernel.authorize(action)
            if not gate.allowed:
                return err(-32000, f"denied by governance: {gate.reason}")
            result = adapter.execute(action)
            if not result.ok:
                return err(-32000, result.error or "tool failed")
            return ok({"content": [{"type": "text", "text": str(result.output)}], "isError": False})
        return err(-32601, f"method not found: {method}")

    def serve_stdio(self, stream_in=None, stream_out=None) -> None:
        """Run the JSON-RPC loop over stdio until the input closes (the real server)."""
        stream_in = stream_in or sys.stdin
        stream_out = stream_out or sys.stdout
        for line in stream_in:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue
            response = self.handle_request(request)
            stream_out.write(json.dumps(response) + "\n")
            stream_out.flush()
