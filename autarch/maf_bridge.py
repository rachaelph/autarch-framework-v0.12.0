"""Microsoft Agent Framework bridge — run their tools under governance, and expose ours to them.

A genuine, bidirectional bridge with **no hard dependency** on the Microsoft Agent Framework
(``agent_framework``): this module imports cleanly whether or not MAF is installed, and only
touches it when you actually build a MAF-facing object (imported lazily inside those functions).
So ``import autarch`` never requires MAF — but the moment you want it, it's one import away.

  * **``govern_maf_tools(tools)``** wraps MAF tools (``FunctionTool`` objects or plain callables)
    as *governed* Autarch capabilities (a :class:`ToolAdapter`). Each call then passes through the
    capability kernel and is recorded — governance the tool never had.

  * **``as_maf_tool(capability, kernel, adapter)``** exposes a *Autarch-governed* capability as a
    native MAF ``FunctionTool``. A MAF ``Agent`` can call it — and the call is authorized by the
    kernel first.

  * **``governed_function_middleware(kernel, ...)``** returns a MAF *function middleware* that
    authorizes EVERY tool call a MAF agent makes through the Autarch kernel: deny short-circuits the
    call (the tool never runs) with a reason; allow proceeds. This is the cleanest way to drop
    governance onto an existing MAF agent — the analogue of ``govern_langchain_tools`` for MAF.

So an existing MAF agent can run inside Autarch's governance, and Autarch's governed capabilities
can be dropped into a MAF agent.

Finally, **``MAFModelProvider``** turns the direction around one more way: it is a normal Autarch
``ModelProvider`` whose completions are produced by a Microsoft Agent Framework ``Agent``. Because
every Autarch flow (councils, extraction, evaluation) talks to the *single* seam
``ModelProvider.complete(prompt, system)``, this lets the Microsoft Agent Framework drive the
reasoning of ANY Autarch pipeline while Autarch keeps governing — e.g. ``examples/extract_maf.py``
is ``examples/extract.py`` with this one provider injected.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Awaitable, Callable, Dict, List, Optional, Union

from .adapters.tool import ToolAdapter
from .contracts import Action
from .errors import RateLimited
from .intelligence.base import ModelProvider
from .kernel import CapabilityKernel


def _maf_name(tool) -> str:
    name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
    if not name:
        raise ValueError(f"MAF tool {tool!r} has no usable name")
    return name


def _maf_schema(tool) -> dict:
    """Best-effort param schema from a MAF ``FunctionTool`` (its JSON-schema ``parameters()``)."""
    try:
        params = tool.parameters() if callable(getattr(tool, "parameters", None)) else None
        props = params.get("properties") if isinstance(params, dict) else None
        if isinstance(props, dict):
            return {k: (v.get("type", "value") if isinstance(v, dict) else "value") for k, v in props.items()}
    except Exception:
        pass
    return {}


def _maf_invoker(tool) -> Callable:
    """A SYNC callable that runs a MAF tool from a dict of params (for the ``ToolAdapter``)."""
    func = getattr(tool, "func", None)  # the original python function behind a FunctionTool

    def invoke(params=None, **kwargs):
        args = params if isinstance(params, dict) else (kwargs or ({} if params is None else {"input": params}))
        if callable(func):
            return func(**args)
        if callable(tool):
            return tool(**args)
        raise TypeError(f"don't know how to invoke MAF tool {tool!r}")

    return invoke


def govern_maf_tools(tools, namespace: str = "tool") -> ToolAdapter:
    """Wrap Microsoft Agent Framework tools as governed Autarch capabilities (with schemas)."""
    mapping: Dict[str, Callable] = {}
    schemas: Dict[str, dict] = {}
    for tool in tools:
        name = _maf_name(tool)
        mapping[name] = _maf_invoker(tool)
        schema = _maf_schema(tool)
        if schema:
            schemas[name] = schema
    return ToolAdapter(mapping, namespace=namespace, schemas=schemas)


def _governed_runner(capability: str, kernel: CapabilityKernel, adapter) -> Callable:
    """A plain callable that authorizes ``capability`` via the kernel, then runs it via the adapter."""
    def _run(**kwargs):
        action = Action(capability, dict(kwargs))
        gate = kernel.authorize(action)
        if not gate.allowed:
            return f"DENIED by governance: {gate.reason}"
        result = adapter.execute(action)
        return result.output if result.ok else f"ERROR: {result.error}"

    _run.__name__ = str(capability).replace(".", "_")
    _run.__doc__ = f"Governed Autarch capability '{capability}'."
    return _run


def as_maf_tool(capability: str, kernel: CapabilityKernel, adapter, description: str = ""):
    """Expose one Autarch-governed capability as a native MAF ``FunctionTool``.

    The Microsoft Agent Framework is imported lazily, so this only requires it to be installed
    when you actually build a tool (``pip install agent-framework``)."""
    try:
        from agent_framework import tool as _maf_tool
    except Exception as exc:  # pragma: no cover - only hit when MAF is absent
        raise RuntimeError(
            "Microsoft Agent Framework is not installed: pip install agent-framework"
        ) from exc
    runner = _governed_runner(capability, kernel, adapter)
    return _maf_tool(
        runner,
        name=capability,
        description=description or f"Governed Autarch capability '{capability}'",
    )


def as_maf_tools(kernel: CapabilityKernel, adapter) -> List:
    """Expose every capability of an adapter as MAF governed tools."""
    return [as_maf_tool(cap, kernel, adapter) for cap in adapter.capabilities()]


def governed_function_middleware(
    kernel: CapabilityKernel,
    *,
    capability_for: Optional[Callable[[str], str]] = None,
    on_deny: Optional[Callable] = None,
):
    """Return a MAF *function middleware* that authorizes EVERY tool call through the Autarch kernel.

    Drop it onto a MAF ``Agent`` (``middleware=[governed_function_middleware(kernel)]``) and every
    tool the agent calls is gated by the capability kernel first:

      * ``capability_for(tool_name) -> capability`` maps a MAF tool name to an Autarch capability
        (defaults to identity — the tool name *is* the capability).
      * ``on_deny(tool_name, gate) -> result`` customises the denied response; by default the call
        is short-circuited with a human-readable reason and the tool never executes.

    The Microsoft Agent Framework is imported lazily (only needed when you build the middleware)."""
    try:
        from agent_framework import function_middleware
    except Exception as exc:  # pragma: no cover - only hit when MAF is absent
        raise RuntimeError(
            "Microsoft Agent Framework is not installed: pip install agent-framework"
        ) from exc
    cap_of = capability_for or (lambda name: name)

    @function_middleware
    async def _governed(context, next):
        fn = getattr(context, "function", None)
        name = getattr(fn, "name", "") or ""
        args = getattr(context, "arguments", {})
        if hasattr(args, "model_dump"):
            try:
                args = args.model_dump()
            except Exception:
                args = {}
        elif not isinstance(args, dict):
            try:
                args = dict(args)
            except Exception:
                args = {}
        gate = kernel.authorize(Action(cap_of(name), dict(args)))
        if not gate.allowed:
            context.result = on_deny(name, gate) if on_deny else f"DENIED by autarch governance: {gate.reason}"
            return  # short-circuit — the governed tool never executes
        await next(context)

    return _governed


# ---------------------------------------------------------------------------
# MAF as the reasoning engine: a ModelProvider backed by a MAF Agent.
# ---------------------------------------------------------------------------

def _isawaitable(obj) -> bool:
    return asyncio.iscoroutine(obj) or asyncio.isfuture(obj) or hasattr(obj, "__await__")


def _maf_response_text(resp) -> str:
    """Pull the text out of whatever ``agent_framework.Agent.run`` returned."""
    for attr in ("text", "output_text", "content"):
        val = getattr(resp, attr, None)
        if isinstance(val, str) and val.strip():
            return val
    # Some responses expose their content through a list of messages.
    messages = getattr(resp, "messages", None)
    if isinstance(messages, (list, tuple)) and messages:
        for msg in reversed(messages):
            txt = getattr(msg, "text", None) or getattr(msg, "content", None)
            if isinstance(txt, str) and txt.strip():
                return txt
    return str(resp)


def _maf_usage(resp):
    """Best-effort ``(prompt_tokens, completion_tokens)`` from a MAF response, else ``(None, None)``."""
    def pick(obj, names):
        for n in names:
            v = getattr(obj, n, None)
            if v is None and isinstance(obj, dict):
                v = obj.get(n)
            if isinstance(v, (int, float)):
                return int(v)
        return None

    ins = ("prompt_tokens", "input_token_count", "input_tokens", "prompt_token_count")
    outs = ("completion_tokens", "output_token_count", "output_tokens", "completion_token_count")
    candidates = [getattr(resp, a, None) for a in ("usage", "usage_details", "token_usage")]
    raw = getattr(resp, "raw_representation", None) or getattr(resp, "raw", None)
    if raw is not None:
        candidates.append(getattr(raw, "usage", None))
    for u in candidates:
        if u is None:
            continue
        pt, ct = pick(u, ins), pick(u, outs)
        if pt is not None or ct is not None:
            return pt, ct
    return None, None


def _record_maf_usage(resp, model_label, prompt, system, text, label="", started=0.0, ended=0.0):
    """Record a MAF call's token usage into the global meter (real if exposed, else estimated)."""
    try:
        from .intelligence.usage import record_usage
        from .intelligence.pricing import estimate_tokens

        pt, ct = _maf_usage(resp)
        if pt is None and ct is None:
            record_usage(model_label or "maf", estimate_tokens((system or "") + (prompt or "")),
                         estimate_tokens(text or ""), estimated=True, source="maf", label=label, started=started, ended=ended)
        else:
            record_usage(model_label or "maf", pt or 0, ct or 0, estimated=False, source="maf", label=label, started=started, ended=ended)
    except Exception:
        pass


def _looks_like_rate_limit(exc: BaseException) -> bool:
    if type(exc).__name__ in ("RateLimitError", "RateLimited"):
        return True
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if code == 429:
        return True
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "too many requests" in msg


def _retry_after_of(exc: BaseException) -> Optional[float]:
    for attr in ("retry_after", "retry_after_seconds"):
        val = getattr(exc, attr, None)
        if isinstance(val, (int, float)):
            return float(val)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            raw = headers.get("retry-after") or headers.get("Retry-After")
            if raw is not None:
                return float(raw)
        except (TypeError, ValueError):
            pass
    return None


class _LoopRunner:
    """One private event loop on a daemon thread, so synchronous (and thread-pooled) callers can
    run coroutines against a single, stable loop. This keeps a shared async HTTP client bound to
    exactly one loop — the safe way to reuse a MAF/OpenAI client across Autarch's ThreadPool
    workers (``AdaptiveExecutor``)."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._serve, name="autarch-maf-loop", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro: Awaitable):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)


class MAFModelProvider(ModelProvider):
    """An Autarch :class:`ModelProvider` whose completions run through a Microsoft Agent Framework agent.

    Autarch's whole stack speaks to intelligence through one method — ``complete(prompt, system)``.
    This implementation runs each completion as an ``agent_framework.Agent`` turn on a chat client
    you supply, so the Microsoft Agent Framework can be the reasoning engine of ANY Autarch pipeline
    (extraction, councils, evaluation) while Autarch keeps governing, signing, and grounding.

    ``client_factory`` returns a MAF chat client (e.g. ``agent_framework.openai.OpenAIChatClient``).
    It is called once, lazily, on a dedicated event loop; the resulting client is reused for every
    completion and always used on that one loop — so a single provider instance is safe to share
    across Autarch's thread-pool workers. Azure/OpenAI 429s are surfaced as Autarch ``RateLimited``
    so ``make_resilient`` and ``AdaptiveExecutor`` self-tuning keep working unchanged.

    The Microsoft Agent Framework is imported lazily (only when the first completion runs), so this
    class — and ``import autarch`` — never require ``agent_framework`` to be installed.
    """

    name = "maf"

    def __init__(
        self,
        client_factory: Callable[[], Union["object", Awaitable]],
        *,
        instructions_default: str = "You are a precise assistant. Follow the instructions exactly.",
        agent_name: str = "autarch-maf",
        agent_kwargs: Optional[dict] = None,
        run_kwargs: Optional[dict] = None,
        model_label: str = "maf",
    ) -> None:
        self._client_factory = client_factory
        self._instructions_default = instructions_default
        self._agent_name = agent_name
        self._agent_kwargs = dict(agent_kwargs or {})
        # Options forwarded to every ``agent.run(...)`` call, e.g.
        # ``{"client_kwargs": {"temperature": 0, "seed": 7}}`` for deterministic decoding.
        self._run_kwargs = dict(run_kwargs or {})
        self._model_label = model_label
        self._runner: Optional[_LoopRunner] = None
        self._client = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._runner is None:
            with self._lock:
                if self._runner is None:
                    runner = _LoopRunner()
                    self._client = runner.run(self._build_client())
                    self._runner = runner  # publish last: other threads see a ready provider
        return self._runner, self._client

    async def _build_client(self):
        made = self._client_factory()
        return await made if _isawaitable(made) else made

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        runner, client = self._ensure()
        from .intelligence.usage import current_label

        label = current_label()  # capture on the CALLING thread; the loop thread won't have it
        try:
            return runner.run(self._acomplete(client, prompt, system, label))
        except RateLimited:
            raise
        except BaseException as exc:  # noqa: BLE001 - classify, then re-raise (as RateLimited if 429)
            if _looks_like_rate_limit(exc):
                raise RateLimited(str(exc), retry_after=_retry_after_of(exc)) from exc
            raise

    async def _acomplete(self, client, prompt: str, system: Optional[str], label: str = "") -> str:
        import agent_framework as af
        import time

        agent = af.Agent(
            client,
            instructions=system or self._instructions_default,
            name=self._agent_name,
            **self._agent_kwargs,
        )
        _t0 = time.time()
        resp = await agent.run(prompt, **self._run_kwargs)
        _t1 = time.time()
        text = _maf_response_text(resp)
        _record_maf_usage(resp, self._model_label, prompt, system, text, label, _t0, _t1)
        return text
    def supports_vision(self) -> bool:
        return True

    def complete_vision(self, prompt, images, system=None) -> str:
        runner, client = self._ensure()
        from .intelligence.usage import current_label

        label = current_label()  # capture on the CALLING thread (the loop thread won't have it)
        try:
            return runner.run(self._acomplete_vision(client, prompt, images, system, label))
        except RateLimited:
            raise
        except BaseException as exc:  # noqa: BLE001
            if _looks_like_rate_limit(exc):
                raise RateLimited(str(exc), retry_after=_retry_after_of(exc)) from exc
            raise

    async def _acomplete_vision(self, client, prompt, images, system, label=""):
        import time

        from .intelligence.vision import openai_vision_content

        # Vision goes through the underlying OpenAI/Azure async client the MAF chat client wraps
        # (the documented image content-parts format); degrade to text if it isn't reachable.
        inner = getattr(client, "async_client", None) or getattr(client, "client", None)
        if inner is None or not hasattr(inner, "chat"):
            return await self._acomplete(client, prompt, system, label)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": openai_vision_content(prompt, images)})
        _t0 = time.time()
        resp = await inner.chat.completions.create(
            model=self._model_label, messages=messages, **(self._run_kwargs.get("client_kwargs") or {})
        )
        _t1 = time.time()
        choices = getattr(resp, "choices", None) or []
        text = (getattr(getattr(choices[0], "message", None), "content", "") or "") if choices else ""
        try:
            from .intelligence.usage import record_usage
            from .intelligence.pricing import estimate_tokens

            u = getattr(resp, "usage", None)
            pt = getattr(u, "prompt_tokens", None) if u is not None else None
            ct = getattr(u, "completion_tokens", None) if u is not None else None
            if pt is None and ct is None:
                record_usage(self._model_label, estimate_tokens((system or "") + (prompt or "")),
                             estimate_tokens(text), estimated=True, source="maf", label=label, started=_t0, ended=_t1)
            else:
                record_usage(self._model_label, pt or 0, ct or 0, estimated=False, source="maf", label=label, started=_t0, ended=_t1)
        except Exception:
            pass
        return text
    def close(self) -> None:
        """Best-effort shutdown of the underlying async client and the private loop."""
        runner, client = self._runner, self._client
        if runner is None:
            return

        async def _shutdown():
            for target in (client, getattr(client, "async_client", None), getattr(client, "client", None)):
                if target is None:
                    continue
                for attr in ("aclose", "close"):
                    fn = getattr(target, attr, None)
                    if callable(fn):
                        try:
                            res = fn()
                            if _isawaitable(res):
                                await res
                        except Exception:
                            pass
                        break

        try:
            runner.run(_shutdown())
        except Exception:
            pass
        finally:
            runner.close()
            self._runner = None
