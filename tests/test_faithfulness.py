"""Faithful summarization — tests for the four GenAI-summary failure modes.

  1. Oversummarization / detail loss  -> CoverageEvaluator + extractive_summary
  2. Illusions of progress            -> ledger 'executed' ground truth (signed)
  3. Hallucination / factuality       -> GroundednessEvaluator (signed verdict)
  4. Flawed context compression       -> extractive_summary / compress_history

Everything here is deterministic and offline.
"""
from __future__ import annotations

from autarch import (
    Agent,
    ConsensusEvaluator,
    CoverageEvaluator,
    GroundednessEvaluator,
    RecallMemory,
    capability,
    compress_history,
    extractive_summary,
    from_callables,
    reflect,
)

SOURCE = (
    "Q3 revenue was $50,000. The project carries a security risk. "
    "The deadline is March 15. Acme Corp signed the contract."
)


# -- Problem 3: hallucination / factuality ------------------------------------
def test_groundedness_passes_faithful_summary():
    summary = "Q3 revenue was $50,000 and the project carries a security risk."
    verdict = GroundednessEvaluator(source=SOURCE).evaluate(summary)
    assert verdict.passed
    assert verdict.score == 1.0


def test_groundedness_flags_invented_number():
    summary = "Q3 revenue was $500,000."  # source says $50,000
    verdict = GroundednessEvaluator(source=SOURCE).evaluate(summary)
    assert not verdict.passed
    assert verdict.score == 0.0
    assert "invented numbers" in verdict.details["ungrounded"][0]["reason"]


def test_groundedness_flags_invented_entity():
    summary = "Globex Inc signed the contract."  # source says Acme Corp
    verdict = GroundednessEvaluator(source=SOURCE).evaluate(summary)
    assert not verdict.passed
    assert any("invented entities" in u["reason"] for u in verdict.details["ungrounded"])


def test_groundedness_source_via_context():
    verdict = GroundednessEvaluator().evaluate(
        "Q3 revenue was $50,000.", context={"source": SOURCE}
    )
    assert verdict.passed


def test_groundedness_mixed_partial_score():
    summary = "Q3 revenue was $50,000. The CEO resigned yesterday."  # 1 good, 1 invented
    verdict = GroundednessEvaluator(source=SOURCE).evaluate(summary)
    assert 0.0 < verdict.score < 1.0
    assert verdict.details["claims"] == 2


# -- Problem 1: oversummarization / detail loss -------------------------------
def test_coverage_detects_dropped_detail():
    summary = "Q3 revenue was $50,000."  # drops risk, deadline, party
    verdict = CoverageEvaluator(required=["$50,000", "security risk", "March 15"]).evaluate(summary)
    assert not verdict.passed
    assert "March 15" in verdict.details["missing"]
    assert "security risk" in verdict.details["missing"]


def test_coverage_passes_complete_summary():
    summary = "Q3 revenue was $50,000, a security risk exists, deadline March 15."
    verdict = CoverageEvaluator(required=["$50,000", "security risk", "March 15"]).evaluate(summary)
    assert verdict.passed


def test_coverage_auto_extracts_required_points():
    # With no explicit list, numbers + entities become the must-keep points.
    verdict = CoverageEvaluator(source=SOURCE).evaluate("Nothing relevant here.")
    assert verdict.score == 0.0
    assert "50000" in verdict.details["missing"] or "$50,000" in verdict.details["missing"]


# -- Problem 4: flawed context compression ------------------------------------
def test_extractive_summary_is_grounded_by_construction():
    texts = [
        "Q3 revenue was $50,000.",
        "The weather was pleasant.",
        "The team felt optimistic.",
        "The deadline is March 15.",
        "Lunch was served at noon.",
    ]
    summary = extractive_summary(texts, max_sentences=2)
    # Every output sentence is copied verbatim -> automatically grounded.
    assert GroundednessEvaluator(source=" ".join(texts)).evaluate(summary).passed
    # Fact-bearing sentences (numbers/dates) are retained over filler.
    assert "$50,000" in summary
    assert "March 15" in summary
    assert "weather" not in summary.lower()


def test_extractive_summary_bounded():
    texts = [f"Fact number {i} about topic {i}." for i in range(10)]
    summary = extractive_summary(texts, max_sentences=3)
    assert summary.count(".") <= 3


def test_compress_history_keeps_recent_verbatim_and_summarizes_old():
    turns = [
        "User: my travel budget is $2000.",
        "Assistant: understood.",
        "User: I prefer aisle seats.",
        "Assistant: noted.",
        "User: book the flight now.",
    ]
    compressed = compress_history(turns, keep_recent=2, max_summary_sentences=3)
    assert "book the flight now" in compressed          # recent kept verbatim
    assert "$2000" in compressed                          # key figure preserved
    assert "[Recent turns]" in compressed                 # structure, not a blob


def test_compress_history_accepts_role_dicts():
    turns = [
        {"role": "user", "content": "deadline is March 15"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "any update?"},
        {"role": "assistant", "content": "on track"},
    ]
    compressed = compress_history(turns, keep_recent=1)
    assert "March 15" in compressed


def test_extractive_summary_as_consolidate_summarizer(tmp_path):
    # The structure-preserving compressor upgrades RecallMemory.consolidate,
    # and the originals are still kept (lossless) via derived_from.
    mem = RecallMemory(tmp_path / "recall.db")
    mem.remember("Q3 revenue was $50,000.", kind="episodic", subject="q3")
    mem.remember("The weather was fine.", kind="episodic", subject="q3")
    mem.remember("The deadline is March 15.", kind="episodic", subject="q3")

    new_id = mem.consolidate(subject="q3", kind="episodic",
                             summarize=lambda texts: extractive_summary(texts, max_sentences=2))
    summary = mem.get(new_id)
    assert "$50,000" in summary.content
    assert len(summary.derived_from) == 3  # nothing lost


# -- Problem 3 (governed): the faithfulness verdict is SIGNED into the ledger --
def test_groundedness_verdict_signed_into_ledger(tmp_path):
    # A governed summarize tool whose output is scored for groundedness; the
    # verdict is recorded in the tamper-evident why-memory (provable faithfulness).
    faithful = from_callables({"summarize": lambda source: "Q3 revenue was $50,000."})
    agent = Agent(
        "summarize the filing",
        grants=[capability("tool.summarize")],
        workspace=tmp_path,
        adapters=[faithful],
    )
    result = agent.enact(
        "tool.summarize", {"source": SOURCE},
        evaluate=GroundednessEvaluator(source=SOURCE),
    )
    assert result.executed
    assert result.verdict is not None and result.verdict.passed
    record = agent.memory.get(result.why_id)
    assert record.eval_passed is True
    assert record.evaluator == "groundedness"
    assert agent.memory.verify_provenance(result.why_id) in (True, None)


def test_hallucinated_tool_output_scored_and_recorded(tmp_path):
    # A tool that invents a figure is caught; the failing verdict is still signed.
    liar = from_callables({"summarize": lambda source: "Q3 revenue was $999,999."})
    agent = Agent(
        "summarize", grants=[capability("tool.summarize")], workspace=tmp_path, adapters=[liar]
    )
    result = agent.enact(
        "tool.summarize", {"source": SOURCE},
        evaluate=GroundednessEvaluator(source=SOURCE),
    )
    assert result.verdict is not None and not result.verdict.passed
    assert agent.memory.get(result.why_id).eval_passed is False


# -- Composition: precision + recall + reflection -----------------------------
def test_consensus_of_groundedness_and_coverage():
    faithful = "Q3 revenue was $50,000, a security risk exists, and the deadline is March 15."
    evaluator = ConsensusEvaluator(
        [GroundednessEvaluator(source=SOURCE),
         CoverageEvaluator(required=["$50,000", "security risk", "March 15"])],
        strategy="min",
    )
    verdict = evaluator.evaluate(faithful)
    assert verdict.passed  # faithful AND complete


def test_reflect_improves_until_grounded():
    drafts = iter([
        "Q3 revenue soared to $500,000.",             # hallucinated figure -> fails
        "Q3 revenue was $50,000; a security risk exists.",  # faithful -> passes
    ])
    outcome = reflect(
        lambda feedback: next(drafts),
        GroundednessEvaluator(source=SOURCE),
        min_score=1.0,
        max_revisions=2,
    )
    assert outcome.verdict.passed
    assert outcome.revisions == 1
