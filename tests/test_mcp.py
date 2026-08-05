"""MCP tests — Autarch as a governed MCP server (transport-free protocol checks)."""
from autarch import MCPServer, capability, from_callables
from autarch.kernel import CapabilityKernel


def _server(granted=("tool.search",)):
    adapter = from_callables({
        "search": lambda input: f"results for {input}",
        "delete_db": lambda: "DROPPED",
    })
    kernel = CapabilityKernel([capability(g) for g in granted])
    return MCPServer([adapter], kernel)


def _req(method, **params):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def test_initialize():
    resp = _server().handle_request(_req("initialize"))
    assert resp["result"]["serverInfo"]["name"] == "autarch"
    assert "tools" in resp["result"]["capabilities"]


def test_tools_list_exposes_capabilities():
    tools = _server().handle_request(_req("tools/list"))["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {"tool.search", "tool.delete_db"}
    # Each tool advertises a JSON-schema input object.
    for t in tools:
        assert t["inputSchema"]["type"] == "object"


def test_granted_tool_call_runs():
    resp = _server().handle_request(
        _req("tools/call", name="tool.search", arguments={"input": "x"})
    )
    assert resp["result"]["content"][0]["text"] == "results for x"
    assert resp["result"]["isError"] is False


def test_ungranted_tool_call_is_denied_by_kernel():
    # The killer property: an MCP client cannot call what Autarch wasn't granted.
    resp = _server(granted=("tool.search",)).handle_request(
        _req("tools/call", name="tool.delete_db")
    )
    assert "error" in resp
    assert "denied by governance" in resp["error"]["message"]


def test_unknown_tool_is_method_error():
    resp = _server().handle_request(_req("tools/call", name="tool.missing"))
    assert resp["error"]["code"] == -32601


def test_unknown_method():
    resp = _server().handle_request(_req("frobnicate"))
    assert resp["error"]["code"] == -32601


def test_serve_stdio_roundtrip():
    # Drive the real stdio loop with in-memory streams.
    import io
    import json

    server = _server()
    requests = "\n".join([
        json.dumps(_req("tools/list")),
        json.dumps(_req("tools/call", name="tool.search", arguments={"input": "hi"})),
    ]) + "\n"
    out = io.StringIO()
    server.serve_stdio(stream_in=io.StringIO(requests), stream_out=out)
    lines = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    assert lines[0]["result"]["tools"]
    assert lines[1]["result"]["content"][0]["text"] == "results for hi"
