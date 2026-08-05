"""ToolAdapter — turn any callable, LangChain tool, or MCP tool into a governed
Autarch capability.

This is the ecosystem play *and* the absorb-then-replace move: an existing tool
runs **inside** the kernel, so every call is gated by a capability grant,
deliberated by the council, and recorded in the why-memory. The tool gains
governance it never had — without changing its code.

Tools are exposed as `tool.<name>` capabilities (or a custom namespace). Tool
calls return output but declare no automatic undo (a generic side effect cannot
be assumed reversible).

No third-party packages are imported: LangChain/MCP tools are recognized by duck
typing, so this works whether or not those libraries are installed.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from ..contracts import Action, ActionResult
from .base import Adapter


class ToolAdapter(Adapter):
    name = "tool"

    def __init__(self, tools: Dict[str, Callable], namespace: str = "tool", schemas: Optional[Dict[str, dict]] = None):
        self.namespace = namespace
        self._tools: Dict[str, Callable] = dict(tools)
        # Optional per-tool parameter schema, surfaced to the council so models
        # know the exact arguments a wrapped tool expects.
        self._schemas: Dict[str, dict] = dict(schemas or {})

    def capabilities(self) -> List[str]:
        return [f"{self.namespace}.{name}" for name in self._tools]

    def schema(self) -> Dict[str, Dict[str, str]]:
        return {f"{self.namespace}.{name}": spec for name, spec in self._schemas.items()}

    def execute(self, action: Action) -> ActionResult:
        _, _, name = action.capability.partition(".")
        fn = self._tools.get(name)
        if fn is None:
            return ActionResult(False, error=f"unknown tool '{action.capability}'")
        try:
            output = self._invoke(fn, action.params)
            return ActionResult(True, output=output)
        except Exception as exc:  # surface, never crash the kernel
            return ActionResult(False, error=f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _invoke(fn: Callable, params: dict):
        """Call a tool flexibly: kwargs, single positional, or no args."""
        if not params:
            return fn()
        # A single 'input'/'query' arg is the common single-string tool shape.
        for key in ("input", "query", "text"):
            if set(params.keys()) == {key}:
                return fn(params[key])
        try:
            return fn(**params)
        except TypeError:
            return fn(params)


def from_callables(mapping: Dict[str, Callable], namespace: str = "tool") -> ToolAdapter:
    """Wrap a {name: callable} mapping as a governed ToolAdapter."""
    return ToolAdapter(mapping, namespace=namespace)


def from_langchain_tools(tools, namespace: str = "tool") -> ToolAdapter:
    """Wrap LangChain-style tools (duck-typed: each has `.name` and is invocable).

    Recognizes `.invoke(x)`, `.run(x)`, `.func(**kwargs)`, or a plain callable.
    """
    mapping: Dict[str, Callable] = {}
    for tool in tools:
        tool_name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        if not tool_name:
            raise ValueError(f"tool {tool!r} has no usable name")
        mapping[tool_name] = _langchain_invoker(tool)
    return ToolAdapter(mapping, namespace=namespace)


def _langchain_invoker(tool) -> Callable:
    def invoke(*args, **kwargs):
        payload = args[0] if (args and not kwargs) else (kwargs or (args[0] if args else {}))
        if hasattr(tool, "invoke"):
            return tool.invoke(payload)
        if hasattr(tool, "run"):
            return tool.run(payload)
        if hasattr(tool, "func") and callable(tool.func):
            return tool.func(**kwargs) if kwargs else tool.func(payload)
        if callable(tool):
            return tool(payload)
        raise TypeError(f"don't know how to invoke tool {tool!r}")

    return invoke


def from_mcp_tools(tools, call: Callable[[str, dict], object], namespace: str = "mcp") -> ToolAdapter:
    """Wrap MCP-style tools.

    `tools` is an iterable of objects/dicts exposing a `name`; `call(name, args)`
    is the MCP client invocation. Each tool becomes `mcp.<name>`.
    """
    mapping: Dict[str, Callable] = {}
    for tool in tools:
        tool_name = tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)
        if not tool_name:
            raise ValueError(f"MCP tool {tool!r} has no name")
        mapping[tool_name] = _mcp_invoker(call, tool_name)
    return ToolAdapter(mapping, namespace=namespace)


def _mcp_invoker(call: Callable[[str, dict], object], tool_name: str) -> Callable:
    def invoke(*args, **kwargs):
        params = kwargs or (args[0] if args and isinstance(args[0], dict) else {})
        return call(tool_name, params)

    return invoke
