"""Autarch as a GOVERNED MCP server — the ecosystem absorbs governance.

MCP (Model Context Protocol) is how models and tools increasingly connect. Here
Autarch exposes its capabilities *as* an MCP server: any MCP client (an IDE,
Claude Desktop, another agent) can list and call the tools — but every call is
authorized by the capability kernel first. The client never had governance; now
it does, for free.

No third-party packages: the JSON-RPC protocol is spoken directly.

Run from the repo root:
    python examples/mcp.py
"""
from autarch import MCPServer, capability, from_callables
from autarch.kernel import CapabilityKernel


def banner(title):
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def main():
    # A toolbox: a safe search tool and a dangerous one.
    adapter = from_callables({
        "search": lambda input: f"[results for '{input}']",
        "wipe_database": lambda: "ALL DATA DELETED",
    })

    # Autarch is granted ONLY search. wipe_database is ungranted -> deny by default.
    kernel = CapabilityKernel([capability("tool.search")])
    server = MCPServer([adapter], kernel, name="autarch-demo")

    def call(method, **params):
        return server.handle_request({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})

    banner("1) An MCP client connects and lists tools")
    init = call("initialize")["result"]
    print(f"  server: {init['serverInfo']['name']} (protocol {init['protocolVersion']})")
    tools = call("tools/list")["result"]["tools"]
    for t in tools:
        print(f"  tool: {t['name']} - {t['description']}")

    banner("2) Client calls the GRANTED tool -> governed, runs")
    r = call("tools/call", name="tool.search", arguments={"input": "autarch ai"})
    print(f"  result: {r['result']['content'][0]['text']}")

    banner("3) Client calls the UNGRANTED tool -> the kernel refuses")
    r = call("tools/call", name="tool.wipe_database", arguments={})
    print(f"  error: {r['error']['message']}")
    print("\n  The MCP client was never built with governance. Autarch added it underneath.")


if __name__ == "__main__":
    main()
