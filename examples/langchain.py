"""LangChain bridge — run their tools governed, expose ours to them.

A bidirectional bridge with no LangChain dependency (everything duck-typed):
  - wrap existing LangChain tools as governed Autarch capabilities, and
  - expose Autarch-governed capabilities as LangChain-compatible tools.

Run from the repo root:
    python examples/langchain.py
"""
from autarch import (
    as_langchain_tool,
    capability,
    from_callables,
    govern_langchain_tools,
)
from autarch.contracts import Action
from autarch.kernel import CapabilityKernel


def banner(title):
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


# A stand-in for a real LangChain StructuredTool (duck-typed; no dependency).
class FakeLangChainSearch:
    name = "web_search"
    description = "Search the web"
    args = {"query": {"type": "string"}}

    def invoke(self, payload):
        q = payload.get("query") if isinstance(payload, dict) else payload
        return f"[web results for '{q}']"


def main():
    banner("1) Govern an existing LangChain tool")
    adapter = govern_langchain_tools([FakeLangChainSearch()])
    print(f"  capability: {adapter.capabilities()}")
    print(f"  schema surfaced to the council: {adapter.schema()}")
    # It runs inside the kernel now.
    kernel = CapabilityKernel([capability("tool.web_search")])
    action = Action("tool.web_search", {"query": "autarch"})
    print(f"  gate: {'ALLOWED' if kernel.authorize(action).allowed else 'DENIED'}")
    print(f"  output: {adapter.execute(action).output}")

    banner("2) Expose a Autarch-governed capability AS a LangChain tool")
    native = from_callables({"price_lookup": lambda input: f"${len(str(input)) * 10}"})
    granted = CapabilityKernel([capability("tool.price_lookup")])
    lc_tool = as_langchain_tool("tool.price_lookup", granted, native, description="Look up a price")
    print(f"  LangChain-compatible tool: name={lc_tool.name!r}")
    print(f"  a LangChain agent calls .invoke(...): {lc_tool.invoke({'input': 'widget'})}")

    banner("3) Governance still applies when LangChain calls it")
    ungranted = CapabilityKernel([])  # nothing granted
    risky = as_langchain_tool("tool.price_lookup", ungranted, native)
    print(f"  ungranted call via LangChain: {risky.invoke({'input': 'x'})}")
    print("\n  Their tools gain governance; our governed tools drop into their agents.")


if __name__ == "__main__":
    main()
