"""LangChain bridge — run their tools under governance, and expose ours to them.

A genuine, bidirectional bridge — with no hard dependency on LangChain (everything
is duck-typed, so it works whether or not LangChain is installed):

  * **`govern_langchain_tools(tools)`** wraps LangChain tools as *governed*
    Autarch capabilities. Each call passes through the capability kernel, gets
    deliberated, and is recorded — governance the tool never had. Structured-tool
    argument schemas are surfaced to the council.

  * **`as_langchain_tool(capability, kernel, adapter)`** exposes a *Autarch-
    governed* capability as a LangChain-compatible tool (an object with `.name`,
    `.description`, `.invoke`, `.run`). A LangChain agent can then call it — and
    the call is still governed by the kernel.

So existing LangChain agents can run inside Autarch's governance, and Autarch's
governed capabilities can be dropped into existing LangChain agents.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from .adapters.tool import ToolAdapter
from .contracts import Action
from .kernel import CapabilityKernel


def _lc_name(tool) -> str:
    name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
    if not name:
        raise ValueError(f"LangChain tool {tool!r} has no usable name")
    return name


def _lc_schema(tool) -> dict:
    """Best-effort param schema from a LangChain StructuredTool's args_schema."""
    args = getattr(tool, "args", None)
    if isinstance(args, dict):
        return {k: (v.get("type", "value") if isinstance(v, dict) else "value") for k, v in args.items()}
    schema = getattr(tool, "args_schema", None)
    if schema is not None:
        fields = getattr(schema, "model_fields", None) or getattr(schema, "__fields__", None)
        if isinstance(fields, dict):
            return {k: "value" for k in fields}
    return {}


def _lc_invoker(tool) -> Callable:
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
        raise TypeError(f"don't know how to invoke LangChain tool {tool!r}")
    return invoke


def govern_langchain_tools(tools, namespace: str = "tool") -> ToolAdapter:
    """Wrap LangChain tools as governed Autarch capabilities (with schemas)."""
    mapping: Dict[str, Callable] = {}
    schemas: Dict[str, dict] = {}
    for tool in tools:
        name = _lc_name(tool)
        mapping[name] = _lc_invoker(tool)
        schema = _lc_schema(tool)
        if schema:
            schemas[name] = schema
    return ToolAdapter(mapping, namespace=namespace, schemas=schemas)


class GovernedLangChainTool:
    """A LangChain-compatible view of a single Autarch-governed capability.

    Duck-types the LangChain ``BaseTool`` surface (`name`, `description`,
    `invoke`, `run`) so it drops into a LangChain agent — but every call is
    authorized by the capability kernel first.
    """

    def __init__(self, capability: str, kernel: CapabilityKernel, adapter, description: str = ""):
        self.name = capability
        self.description = description or f"Governed Autarch capability '{capability}'"
        self._capability = capability
        self._kernel = kernel
        self._adapter = adapter

    def invoke(self, params=None, **kwargs):
        args = params if isinstance(params, dict) else (kwargs or ({} if params is None else {"input": params}))
        action = Action(self._capability, dict(args))
        gate = self._kernel.authorize(action)
        if not gate.allowed:
            return f"DENIED by governance: {gate.reason}"
        result = self._adapter.execute(action)
        return result.output if result.ok else f"ERROR: {result.error}"

    # LangChain's older surface.
    def run(self, params=None, **kwargs):
        return self.invoke(params, **kwargs)

    def __call__(self, params=None, **kwargs):
        return self.invoke(params, **kwargs)


def as_langchain_tool(capability: str, kernel: CapabilityKernel, adapter, description: str = "") -> GovernedLangChainTool:
    """Expose one Autarch-governed capability as a LangChain-compatible tool."""
    return GovernedLangChainTool(capability, kernel, adapter, description)


def as_langchain_tools(kernel: CapabilityKernel, adapter) -> List[GovernedLangChainTool]:
    """Expose every capability of an adapter as LangChain-compatible governed tools."""
    return [as_langchain_tool(cap, kernel, adapter) for cap in adapter.capabilities()]
