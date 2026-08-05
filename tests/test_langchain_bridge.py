"""LangChain bridge tests — govern their tools, expose ours (both directions)."""
from autarch import capability, from_callables
from autarch.contracts import Action
from autarch.kernel import CapabilityKernel
from autarch.langchain_bridge import (
    as_langchain_tool,
    as_langchain_tools,
    govern_langchain_tools,
)


class FakeInvokeTool:
    name = "weather"
    description = "get the weather"

    def invoke(self, x):
        return f"sunny in {x}"


class FakeRunTool:
    name = "calc"

    def run(self, x):
        return f"={x}"


class FakeStructuredTool:
    name = "book"
    args = {"city": {"type": "string"}, "nights": {"type": "integer"}}

    def invoke(self, payload):
        return f"booked {payload}"


# --- inbound: govern LangChain tools --------------------------------------

def test_govern_invoke_tool():
    adapter = govern_langchain_tools([FakeInvokeTool()])
    assert adapter.capabilities() == ["tool.weather"]
    result = adapter.execute(Action("tool.weather", {"input": "Paris"}))
    assert result.ok is True
    assert result.output == "sunny in Paris"


def test_govern_run_tool():
    adapter = govern_langchain_tools([FakeRunTool()])
    result = adapter.execute(Action("tool.calc", {"input": "2+2"}))
    assert result.output == "=2+2"


def test_structured_tool_schema_surfaced():
    adapter = govern_langchain_tools([FakeStructuredTool()])
    schema = adapter.schema()
    assert schema["tool.book"] == {"city": "string", "nights": "integer"}


def test_governed_langchain_tool_runs_through_kernel():
    adapter = govern_langchain_tools([FakeInvokeTool()])
    kernel = CapabilityKernel([capability("tool.weather")])
    assert kernel.authorize(Action("tool.weather", {"input": "x"})).allowed is True
    denied = CapabilityKernel([])
    assert denied.authorize(Action("tool.weather", {"input": "x"})).allowed is False


# --- outbound: expose Autarch capability as a LangChain tool ------------

def test_as_langchain_tool_invokes_when_granted():
    adapter = from_callables({"search": lambda input: f"hit:{input}"})
    kernel = CapabilityKernel([capability("tool.search")])
    lc = as_langchain_tool("tool.search", kernel, adapter)
    assert lc.name == "tool.search"
    assert lc.invoke({"input": "q"}) == "hit:q"
    assert lc.run({"input": "q"}) == "hit:q"  # older LangChain surface


def test_as_langchain_tool_denied_when_ungranted():
    adapter = from_callables({"danger": lambda: "boom"})
    kernel = CapabilityKernel([])  # nothing granted
    lc = as_langchain_tool("tool.danger", kernel, adapter)
    out = lc.invoke({})
    assert "DENIED by governance" in out


def test_as_langchain_tools_exposes_all():
    adapter = from_callables({"a": lambda: 1, "b": lambda: 2})
    kernel = CapabilityKernel([capability("tool.a"), capability("tool.b")])
    tools = as_langchain_tools(kernel, adapter)
    assert {t.name for t in tools} == {"tool.a", "tool.b"}
