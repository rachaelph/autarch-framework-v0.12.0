"""Quickstart: build a governed agent in a few lines.

Run from the repo root:
    python examples/quickstart.py
"""
import shutil
from pathlib import Path

from autarch import Agent, capability


def main() -> None:
    # Use a dedicated, freshly-reset workspace so the demo is reproducible and
    # independent of any CLI experiments in ./sandbox.
    workspace = Path("./sandbox/_quickstart")
    if workspace.exists():
        shutil.rmtree(workspace)

    agent = Agent(
        intent="create a file called notes.txt that says Hello Autarch",
        council=["mock"],  # swap for ["ollama:llama3"] to use a real local model
        grants=[
            capability("file.write", scope={"path_prefix": "."}),
            capability("file.read"),
            # No file.delete grant -> the agent literally cannot delete. Provable.
        ],
        workspace=workspace,
    )

    result = agent.run()

    print("Proposer :", result.deliberation.proposal.voice, "->", result.action.capability)
    print("Challenger:", result.deliberation.critique.voice, "->", result.deliberation.critique.verdict)
    print("Gate     :", "ALLOWED" if result.gate.allowed else "DENIED", "-", result.gate.reason)
    print("Decision :", result.human_decision)
    print("Executed :", result.executed, "-", (result.result.output if result.result else None))
    print("Why-id   :", result.why_id, " (run: autarch why", result.why_id, ")")


if __name__ == "__main__":
    main()
