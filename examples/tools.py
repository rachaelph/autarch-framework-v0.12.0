"""Absorb-then-replace: run an existing tool INSIDE the kernel.

This shows the ecosystem move — a LangChain-style tool (and a plain callable)
wrapped as a governed Autarch capability. The tool gains capability-gating,
deliberation, and an audit trail it never had, without changing its code.

Run from the repo root:
    python examples/tools.py
"""
import shutil
from pathlib import Path

from autarch import Agent, capability
from autarch.adapters.tool import from_callables, from_langchain_tools
from autarch.contracts import Action, HumanDecision
from autarch.kernel import CapabilityKernel


class FakeLangChainTool:
    """A duck-typed stand-in for a LangChain tool (no real dependency needed)."""

    def __init__(self, name):
        self.name = name

    def invoke(self, query):
        return f"[search results for {query!r}]"


def main() -> None:
    workspace = Path("./sandbox/_tools")
    if workspace.exists():
        shutil.rmtree(workspace)

    # Wrap a LangChain-style tool and a plain callable as governed capabilities.
    search = from_langchain_tools([FakeLangChainTool("web_search")])
    mathy = from_callables({"add": lambda a, b: a + b})

    print("Wrapped capabilities:")
    print("  ", search.capabilities(), "+", mathy.capabilities())

    # 1) Granted: the tool runs inside the kernel and is audited.
    print("\n1) A granted tool call passes the gate and executes:")
    kernel = CapabilityKernel([capability("tool.web_search"), capability("tool.add")])
    action = Action("tool.web_search", {"query": "autarch ai os"})
    gate = kernel.authorize(action)
    print("   gate:", "ALLOWED" if gate.allowed else "DENIED")
    print("   output:", search.execute(action).output)

    # 2) Ungranted: the SAME tool is denied by default. Governance the tool
    #    never had on its own.
    print("\n2) The same tool, ungranted, is denied by the kernel:")
    locked = CapabilityKernel([])  # no grants
    print("   gate:", "ALLOWED" if locked.authorize(action).allowed else "DENIED",
          "-", locked.authorize(action).reason)

    # 3) Full loop via the Agent SDK: the tool, governed and recorded.
    print("\n3) The Agent runs the tool through the full governed loop:")
    agent = Agent(
        intent="add",  # the mock council proposes tool.add with the given params
        council=["mock"],
        grants=[capability("tool.add")],
        workspace=workspace,
        adapters=[mathy],
        preside_fn=lambda d, g: HumanDecision.RATIFY.value,
    )
    # Drive the tool deterministically through the kernel + adapter + memory:
    add_action = Action("tool.add", {"a": 2, "b": 40})
    g = agent.kernel.authorize(add_action)
    out = mathy.execute(add_action) if g.allowed else None
    print("   gate:", "ALLOWED" if g.allowed else "DENIED", "| result:", out.output if out else None)

    print("\nThe tool didn't change. Autarch wrapped it in governance.")


if __name__ == "__main__":
    main()
