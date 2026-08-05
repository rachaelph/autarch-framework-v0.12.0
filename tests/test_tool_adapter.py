"""ToolAdapter tests — wrapping callables / LangChain / MCP tools, governed."""
from autarch.adapters.tool import (
    ToolAdapter,
    from_callables,
    from_langchain_tools,
    from_mcp_tools,
)
from autarch.contracts import Action, CapabilityGrant
from autarch.kernel import CapabilityKernel


def test_callable_tool_executes():
    adapter = from_callables({"upper": lambda input: input.upper()})
    assert adapter.capabilities() == ["tool.upper"]
    result = adapter.execute(Action("tool.upper", {"input": "hi"}))
    assert result.ok is True
    assert result.output == "HI"


def test_kwargs_tool():
    adapter = from_callables({"add": lambda a, b: a + b})
    result = adapter.execute(Action("tool.add", {"a": 2, "b": 3}))
    assert result.ok is True
    assert result.output == 5


def test_unknown_tool_errors():
    adapter = ToolAdapter({"x": lambda: 1})
    result = adapter.execute(Action("tool.nope", {}))
    assert result.ok is False
    assert "unknown tool" in result.error


def test_tool_exception_is_surfaced_not_raised():
    def boom(input):
        raise ValueError("kaboom")

    adapter = from_callables({"boom": boom})
    result = adapter.execute(Action("tool.boom", {"input": "x"}))
    assert result.ok is False
    assert "kaboom" in result.error


class _FakeLangChainTool:
    """Duck-typed stand-in for a LangChain tool (no dependency)."""

    def __init__(self, name):
        self.name = name

    def invoke(self, x):
        return f"searched: {x}"


def test_langchain_tool_wrapped():
    adapter = from_langchain_tools([_FakeLangChainTool("search")])
    assert adapter.capabilities() == ["tool.search"]
    result = adapter.execute(Action("tool.search", {"input": "weather"}))
    assert result.ok is True
    assert result.output == "searched: weather"


def test_mcp_tool_wrapped():
    calls = []

    def client_call(name, args):
        calls.append((name, args))
        return {"ok": True}

    adapter = from_mcp_tools([{"name": "fetch"}], call=client_call)
    assert adapter.capabilities() == ["mcp.fetch"]
    result = adapter.execute(Action("mcp.fetch", {"url": "x"}))
    assert result.ok is True
    assert calls == [("fetch", {"url": "x"})]


def test_tool_runs_under_governance():
    # A wrapped tool runs INSIDE the kernel: granted -> allowed; ungranted -> denied.
    adapter = from_callables({"echo": lambda input: input})
    action = Action("tool.echo", {"input": "hi"})

    granted = CapabilityKernel([CapabilityGrant("tool.echo")])
    assert granted.authorize(action).allowed is True
    # And once allowed, the adapter actually performs the work.
    assert adapter.execute(action).output == "hi"

    denied = CapabilityKernel([])
    gate = denied.authorize(action)
    assert gate.allowed is False
    assert "deny by default" in gate.reason


def test_tool_wildcard_grant():
    # A 'tool.*' grant authorizes any wrapped tool — handy for trusted bundles.
    kernel = CapabilityKernel([CapabilityGrant("tool.*")])
    assert kernel.authorize(Action("tool.search", {"input": "x"})).allowed is True
    assert kernel.authorize(Action("tool.translate", {"input": "y"})).allowed is True
