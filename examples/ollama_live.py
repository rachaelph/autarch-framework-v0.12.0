"""Live model: run a real intent through the council with a local Ollama model.

This is the end-to-end proof that the architecture is model-agnostic — the SAME
kernel, council, and why-memory that ran on the deterministic `mock` now run on a
real language model, with zero changes to the core.

Prerequisites (one-time, free, fully local):
    1. Install Ollama:  https://ollama.com   (or: winget install Ollama.Ollama)
    2. Pull a model:    ollama pull llama3
    3. Ensure it's up:  ollama serve   (the app usually starts this for you)

Run:
    python examples/ollama_live.py
    python examples/ollama_live.py --model qwen2.5 --intent "make a file note.txt that says hi"
"""
import argparse
import shutil
import sys
from pathlib import Path

from autarch import Agent, capability
from autarch.intelligence.ollama import OllamaProvider


def preflight(model: str) -> bool:
    """Confirm Ollama is reachable and the model responds; print guidance if not."""
    probe = OllamaProvider(model=model, timeout=15.0)
    try:
        probe.complete('Reply with {"ok": true}', system="Reply with only JSON.")
        return True
    except Exception as exc:  # noqa: BLE001 — we want to show any failure clearly
        print("Ollama is not ready for a live run yet.\n")
        print(f"  reason: {exc}\n")
        print("To enable it (free, local, offline):")
        print("  1. Install:  https://ollama.com   (or: winget install Ollama.Ollama)")
        print(f"  2. Pull:     ollama pull {model}")
        print("  3. Serve:    ollama serve")
        print("\nThe rest of Autarch runs today on the deterministic `mock` provider")
        print("(see examples/quickstart.py and examples/council.py).")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an intent through a live Ollama council.")
    parser.add_argument("--model", default="llama3", help="Ollama model name (default: llama3)")
    parser.add_argument(
        "--challenger",
        help="optional second model to act as the safety reviewer (a real council)",
    )
    parser.add_argument(
        "--intent",
        default="create a file called hello.txt that says Hello from a real model",
    )
    args = parser.parse_args()

    if not preflight(args.model):
        sys.exit(1)

    workspace = Path("./sandbox/_ollama")
    if workspace.exists():
        # ignore_errors: workspaces under cloud-synced folders (OneDrive) can hold
        # transient locks; a stale demo dir shouldn't abort the run.
        shutil.rmtree(workspace, ignore_errors=True)

    council = [f"ollama:{args.model}"]
    if args.challenger:
        council.append(f"ollama:{args.challenger}")

    print(f"\nCouncil: {council}")
    print(f"Intent : {args.intent}\n")

    agent = Agent(
        intent=args.intent,
        council=council,
        grants=[
            capability("file.write", scope={"path_prefix": "."}),
            capability("file.read"),
            # No delete grant: even a real model cannot delete here. Provable.
        ],
        workspace=workspace,
    )
    result = agent.run()

    delib = result.deliberation
    print("Council deliberation:")
    for pos in delib.proposals:
        what = pos.action.capability if pos.action else "(abstained)"
        print(f"  proposer {pos.voice}: {what}")
    for pos in delib.critiques:
        print(f"  critic   {pos.voice}: {pos.verdict} — {pos.rationale[:80]}")
    print(f"\nMotion       : {result.action.capability if result.action else None}")
    print(f"Gate         : {'ALLOWED' if result.gate.allowed else 'DENIED'} — {result.gate.reason}")
    print(f"Recommendation: {delib.recommendation}")
    print(f"Decision     : {result.human_decision}")
    print(f"Executed     : {result.executed}")
    print(f"Why-id       : {result.why_id}  (run: autarch --workspace {workspace} why {result.why_id})")
    print("\nSame kernel. Same council. Same memory. A real brain in the seat.")


if __name__ == "__main__":
    main()
