"""Faithful summarization — the four GenAI-summary failure modes, governed.

GenAI agents are great at condensing text and terrible at telling you when they
quietly dropped a figure, invented a claim, or overstated what they did. Autarch
treats a summary like any other action: it is *evaluated*, and the verdict is
*signed into the tamper-evident ledger* — so faithfulness is provable, not
promised. This runs fully offline (deterministic evaluators; no model, no network).

    python examples/faithfulness.py

  1. Oversummarization / detail loss -> CoverageEvaluator (nothing critical dropped)
  2. Illusions of progress           -> ledger 'executed' ground truth
  3. Hallucination / factuality      -> GroundednessEvaluator (nothing invented)
  4. Flawed context compression      -> extractive_summary / compress_history
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from autarch import (
    Agent,
    CoverageEvaluator,
    GroundednessEvaluator,
    capability,
    compress_history,
    extractive_summary,
    from_callables,
)

SOURCE = (
    "Q3 revenue was $50,000. The project carries a security risk. "
    "The deadline is March 15. Acme Corp signed the contract."
)


def problem_3_hallucination() -> None:
    print("\n[3] HALLUCINATION / FACTUALITY  (each claim must be in the source)")
    grounded = GroundednessEvaluator(source=SOURCE)
    faithful = "Q3 revenue was $50,000 and the project carries a security risk."
    invented = "Q3 revenue was $500,000 and the CEO resigned."
    for label, text in (("faithful", faithful), ("invented", invented)):
        v = grounded.evaluate(text)
        print(f"    {label:9s} score={v.score:.2f} passed={v.passed}")
        for u in v.details["ungrounded"]:
            print(f"        - flagged: {u['claim']!r} ({u['reason']})")


def problem_1_detail_loss() -> None:
    print("\n[1] OVERSUMMARIZATION / DETAIL LOSS  (nothing critical may be dropped)")
    cov = CoverageEvaluator(required=["$50,000", "security risk", "March 15", "Acme Corp"])
    flattened = "Q3 revenue was $50,000."
    complete = "Q3 revenue was $50,000; a security risk exists; deadline March 15; Acme Corp signed."
    for label, text in (("flattened", flattened), ("complete", complete)):
        v = cov.evaluate(text)
        print(f"    {label:9s} score={v.score:.2f} passed={v.passed}  missing={v.details['missing']}")


def problem_4_compression() -> None:
    print("\n[4] FLAWED CONTEXT COMPRESSION  (structure + facts preserved)")
    turns = [
        "User: my travel budget is $2000.",
        "Assistant: understood.",
        "User: I prefer aisle seats.",
        "Assistant: noted.",
        "User: what are my options?",
        "Assistant: three flights match.",
        "User: book the cheapest one.",
    ]
    compressed = compress_history(turns, keep_recent=2, max_summary_sentences=3)
    print("    compressed conversation (older summarized, recent verbatim):")
    for line in compressed.splitlines():
        print(f"      {line}")
    print("    note: the $2000 budget and aisle preference survive compression.")

    print("\n    extractive_summary is grounded by construction (copies verbatim):")
    summary = extractive_summary(
        ["Q3 revenue was $50,000.", "The weather was fine.", "The deadline is March 15."],
        max_sentences=2,
    )
    print(f"      {summary}")
    print(f"      grounded? {GroundednessEvaluator(source=SOURCE).evaluate(summary).passed}")


def problem_2_illusion_of_progress() -> None:
    print("\n[2] ILLUSION OF PROGRESS  (the ledger is ground truth, not the summary)")
    workspace = Path(tempfile.mkdtemp(prefix="autarch_faith_"))
    # This agent may summarize but was NOT granted file.write.
    tool = from_callables({"summarize": lambda source: "All tasks completed and saved!"})
    agent = Agent(
        "summarize and save",
        grants=[capability("tool.summarize")],  # note: no file.write
        workspace=workspace,
        adapters=[tool],
    )
    said = agent.enact("tool.summarize", {"source": SOURCE})
    print(f"    the model's summary claims: {said.result.output!r}")
    saved = agent.enact("file.write", {"path": "out.txt", "content": "done"})
    print(f"    but the LEDGER shows the save actually executed: {saved.executed}")
    print(f"    reason: {saved.result.error}")
    print("    -> a polished 'all done!' cannot fake work the kernel never authorized.")


def main() -> None:
    print("=" * 70)
    print("FAITHFUL SUMMARIZATION — evaluated, and signed into the ledger")
    print("=" * 70)
    print(f"\nSource of truth:\n  {SOURCE}")
    problem_1_detail_loss()
    problem_2_illusion_of_progress()
    problem_3_hallucination()
    problem_4_compression()

    # Governed: the faithfulness verdict is signed into the tamper-evident ledger.
    print("\n[SIGNED] the groundedness verdict is recorded in the why-memory:")
    workspace = Path(tempfile.mkdtemp(prefix="autarch_faith2_"))
    tool = from_callables({"summarize": lambda source: "Q3 revenue was $50,000."})
    agent = Agent("summarize", grants=[capability("tool.summarize")],
                  workspace=workspace, adapters=[tool])
    result = agent.enact("tool.summarize", {"source": SOURCE},
                         evaluate=GroundednessEvaluator(source=SOURCE))
    rec = agent.memory.get(result.why_id)
    print(f"    why_id={result.why_id}  evaluator={rec.evaluator}  "
          f"passed={rec.eval_passed}  provenance_ok={agent.memory.verify_provenance(result.why_id)}")

    print("\n" + "=" * 70)
    print("Summaries are governed like any action: no invented facts, no dropped")
    print("detail, no faked progress, no lossy compression — and it's all provable.")
    print("=" * 70)


if __name__ == "__main__":
    main()
