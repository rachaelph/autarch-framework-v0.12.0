"""Evaluation & reflection tests — governed judge LLMs, deterministic checks."""
import json

from autarch import (
    Agent,
    AssertionEvaluator,
    ConsensusEvaluator,
    GroundednessEvaluator,
    RubricJudge,
    capability,
    from_callables,
    reflect,
)
from autarch.contracts import HumanDecision
from autarch.intelligence.mock import MockProvider


# --- AssertionEvaluator (deterministic) -----------------------------------

def test_assertion_all_pass():
    ev = AssertionEvaluator([("a", lambda s: "x" in s), ("b", lambda s: len(s) > 0)])
    v = ev.evaluate("x")
    assert v.passed is True and v.score == 1.0


def test_assertion_partial():
    ev = AssertionEvaluator([("a", lambda s: "x" in s), ("b", lambda s: "z" in s)])
    v = ev.evaluate("x alone")
    assert v.passed is False and v.score == 0.5
    assert "failed: b" in v.reasons


def test_assertion_throwing_check_fails():
    ev = AssertionEvaluator([("boom", lambda s: 1 / 0)])
    v = ev.evaluate("x")
    assert v.passed is False and v.score == 0.0


# --- RubricJudge (LLM-as-judge, fails closed) -----------------------------

def _judge(score=0.9, reasons="ok"):
    return MockProvider(name="judge", scripted={"ROLE: JUDGE": json.dumps({"score": score, "reasons": reasons})})


def test_rubric_judge_passes():
    v = RubricJudge(_judge(0.9), rubric="be clear", threshold=0.7).evaluate("output")
    assert v.passed is True and v.score == 0.9


def test_rubric_judge_below_threshold():
    v = RubricJudge(_judge(0.4), rubric="be clear", threshold=0.7).evaluate("output")
    assert v.passed is False and v.score == 0.4


def test_rubric_judge_fails_closed_on_garbage():
    bad = MockProvider(name="judge", scripted={"ROLE: JUDGE": "I think it is pretty good honestly"})
    v = RubricJudge(bad, rubric="x").evaluate("output")
    assert v.score == 0.0 and v.passed is False
    assert "unparseable" in v.reasons


def test_rubric_judge_clamps_out_of_range():
    v = RubricJudge(_judge(5.0), rubric="x", threshold=0.7).evaluate("output")
    assert v.score == 1.0  # clamped


# --- ConsensusEvaluator ---------------------------------------------------

def test_consensus_mean():
    a = AssertionEvaluator([("ok", lambda s: True)])      # 1.0
    b = RubricJudge(_judge(0.5), rubric="x")              # 0.5
    v = ConsensusEvaluator([a, b], strategy="mean", threshold=0.7).evaluate("x")
    assert v.score == 0.75 and v.passed is True


def test_consensus_min_is_strict():
    a = AssertionEvaluator([("ok", lambda s: True)])      # 1.0
    b = RubricJudge(_judge(0.3), rubric="x")              # 0.3
    v = ConsensusEvaluator([a, b], strategy="min", threshold=0.7).evaluate("x")
    assert v.score == 0.3 and v.passed is False


def test_consensus_majority():
    passing = AssertionEvaluator([("ok", lambda s: True)])
    failing = AssertionEvaluator([("no", lambda s: False)])
    v = ConsensusEvaluator([passing, passing, failing], strategy="majority", threshold=0.6).evaluate("x")
    assert round(v.score, 2) == 0.67 and v.passed is True


def test_groundedness_accepts_equivalent_invoice_number_formatting():
    evaluator = GroundednessEvaluator(
        source="Invoice total $3,795.50. Tax charged $0.00.",
        min_support=0.0,
    )

    assert evaluator.evaluate("Invoice total 3795.5. Tax charged 0.0.").passed is True
    assert evaluator.evaluate("Invoice total 3796.5.").passed is False


# --- reflect() loop -------------------------------------------------------

def test_reflect_improves_with_feedback():
    calls = {"n": 0}

    def produce(feedback):
        calls["n"] += 1
        return "bad" if calls["n"] == 1 else "good"

    ev = AssertionEvaluator([("good", lambda s: s == "good")])
    r = reflect(produce, ev, min_score=1.0, max_revisions=2)
    assert r.verdict.passed is True
    assert r.revisions == 1
    assert r.output == "good"


def test_reflect_is_bounded():
    def produce(feedback):
        return "always bad"

    ev = AssertionEvaluator([("good", lambda s: s == "good")])
    r = reflect(produce, ev, min_score=1.0, max_revisions=2)
    assert r.verdict.passed is False
    assert r.revisions == 2  # tried initial + 2 revisions, then stopped
    assert len(r.history) == 3


def test_reflect_passes_first_try():
    def produce(feedback):
        return "good"

    ev = AssertionEvaluator([("good", lambda s: s == "good")])
    r = reflect(produce, ev, min_score=1.0)
    assert r.revisions == 0


# --- governed evaluation in the agent (the moat) --------------------------

def _scripted_extract_agent(tmp_path):
    prov = MockProvider(name="m", scripted={
        "ROLE: PROPOSER": json.dumps({"capability": "doc.extract", "params": {"input": "f.pdf"}, "rationale": "x"}),
        "ROLE: CHALLENGER": json.dumps({"verdict": "approve", "reasons": "ok"}),
    })
    docs = from_callables({"extract": lambda input: '{"name": "Acme"}'}, namespace="doc")
    return Agent(
        intent="extract", council=[prov], adapters=[docs],
        grants=[capability("doc.extract")], workspace=tmp_path,
        preside_fn=lambda d, g: HumanDecision.RATIFY.value,
    )


def test_agent_evaluate_records_signed_verdict(tmp_path):
    agent = _scripted_extract_agent(tmp_path)
    ev = AssertionEvaluator([("has name", lambda s: '"name"' in s)])
    result = agent.run(evaluate=ev)
    assert result.executed is True
    assert result.verdict is not None
    assert result.verdict.passed is True
    # The verdict is persisted in the (signed) why-memory -> provable.
    record = agent.memory.get(result.why_id)
    assert record.eval_score == 1.0
    assert record.eval_passed is True
    assert record.evaluator == "assertions"


def test_agent_without_evaluate_has_no_verdict(tmp_path):
    agent = _scripted_extract_agent(tmp_path)
    result = agent.run()
    assert result.verdict is None
    assert agent.memory.get(result.why_id).eval_score is None


def test_agent_evaluation_emits_event(tmp_path):
    from autarch import ListSink
    from autarch.events import EVALUATION_COMPLETE

    sink = ListSink()
    prov = MockProvider(name="m", scripted={
        "ROLE: PROPOSER": json.dumps({"capability": "doc.extract", "params": {"input": "f"}, "rationale": "x"}),
        "ROLE: CHALLENGER": json.dumps({"verdict": "approve", "reasons": "ok"}),
    })
    docs = from_callables({"extract": lambda input: "result"}, namespace="doc")
    agent = Agent(
        intent="extract", council=[prov], adapters=[docs],
        grants=[capability("doc.extract")], workspace=tmp_path, events=sink,
        preside_fn=lambda d, g: HumanDecision.RATIFY.value,
    )
    agent.run(evaluate=AssertionEvaluator([("ok", lambda s: True)]))
    assert EVALUATION_COMPLETE in sink.kinds()
