"""Live governed orchestration — a real local model plans and synthesizes, while
the deterministic kernel governs every child agent it spawns.

Same governance as `examples/orchestration.py`, but the master's *reasoning*
(task decomposition) and its *handoff* (final synthesis) are done by a real
Ollama model. The model decides WHAT the subtasks are and writes the unified
answer; the kernel still decides what each child is ALLOWED to do — a hallucinated
or over-reaching subtask is attenuated or denied, never trusted.

Both model seams FAIL CLOSED: if Ollama is unreachable or returns unparseable
output, the master degrades to the deterministic RulePlanner / ConcatSynthesizer
instead of failing the run.

Prerequisites (one-time, free, fully local):
    1. Install Ollama:  https://ollama.com   (or: winget install Ollama.Ollama)
    2. Pull a model:    ollama pull llama3
    3. Ensure it's up:  ollama serve

Run:
    python examples/orchestration_live.py
    python examples/orchestration_live.py --model qwen2.5 \
        --intent "write greeting.txt saying hello then read greeting.txt"
"""
import argparse
import shutil
import sys
from pathlib import Path

from autarch import (
    Agent,
    Invariant,
    ModelPlanner,
    ModelSynthesizer,
    Orchestrator,
    SpecialistRegistry,
    capability,
)
from autarch.intelligence.ollama import OllamaProvider


def preflight(model: str) -> bool:
    """Confirm Ollama is reachable and the model responds; print guidance if not."""
    probe = OllamaProvider(model=model, timeout=15.0)
    try:
        probe.complete('Reply with {"ok": true}', system="Reply with only JSON.")
        return True
    except Exception as exc:  # noqa: BLE001 — surface any failure clearly
        print("Ollama is not ready for a live run yet.\n")
        print(f"  reason: {exc}\n")
        print("To enable it (free, local, offline):")
        print("  1. Install:  https://ollama.com   (or: winget install Ollama.Ollama)")
        print(f"  2. Pull:     ollama pull {model}")
        print("  3. Serve:    ollama serve")
        print("\nThe offline demo needs no setup:  python examples/orchestration.py")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Live governed master-child orchestration.")
    parser.add_argument("--model", default="llama3", help="Ollama model name (default: llama3)")
    parser.add_argument(
        "--intent",
        default=(
            "create a file called report.txt that says quarterly numbers look strong "
            "then read report.txt then delete report.txt"
        ),
    )
    args = parser.parse_args()

    if not preflight(args.model):
        sys.exit(1)

    workspace = Path("./sandbox/_orchestration_live")
    if workspace.exists():
        # ignore_errors: cloud-synced folders (OneDrive) can hold transient locks.
        shutil.rmtree(workspace, ignore_errors=True)

    provider = OllamaProvider(model=args.model)

    # The master (supervisor): may create and read files, but never delete.
    master = Agent(
        intent="coordinate the report workflow",
        grants=[capability("file.write"), capability("file.read")],
        workspace=workspace,
    )

    orchestrator = Orchestrator(
        master,
        planner=ModelPlanner(provider),          # the model decomposes the goal…
        synthesizer=ModelSynthesizer(provider),  # …and writes the final answer
        registry=SpecialistRegistry.defaults(),
        guarantees=[Invariant.forbid("file.delete")],  # proven before any child runs
    )

    print(f"\nModel : ollama:{args.model}")
    print(f"Intent: {args.intent}\n")
    print("=" * 70)
    print("LIVE GOVERNED ORCHESTRATION  (model plans; kernel governs)")
    print("=" * 70)

    result = orchestrator.run(args.intent)

    print("\nModel-decomposed plan:")
    for i, task in enumerate(result.plan.subtasks, 1):
        req = task.grants[0].name if task.grants else "(none)"
        print(f"  {i}. {task.description}   [requests: {req}]")

    print("\nGoverned execution (each child confined to delegated authority):")
    for i, child in enumerate(result.children, 1):
        status = "done   " if child.executed else "BLOCKED"
        dropped = ", ".join(g.name for g in child.dropped_grants)
        note = f"  (governance withheld: {dropped})" if dropped else ""
        print(f"  {status} {i}. {child.subtask.description}{note}")

    print("\nMaster's model-written synthesis:")
    for line in result.synthesis.splitlines():
        print(f"  {line}")

    ok, _ = master.memory.verify_chain()
    print(f"\n{result.executed_count}/{len(result.children)} subtasks executed; "
          f"signed ledger verifies: {ok}")
    print("The model chose the plan; the kernel still refused what wasn't granted.")


if __name__ == "__main__":
    main()
