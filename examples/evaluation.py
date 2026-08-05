"""Governed evaluation & reflection — judge LLMs and self-improvement, built in.

Developers keep hand-rolling LLM-as-judge scorers and reflect-then-retry loops.
Autarch ships the reusable contract AND makes evaluation *governed*: a verdict
is signed into the tamper-evident why-memory, so you can later PROVE an output was
evaluated, by which judge, and what it scored. No competitor does that.

Run from the repo root:
    python examples/evaluation.py
"""
import json
import shutil
from pathlib import Path

from autarch import (
    Agent,
    AssertionEvaluator,
    ConsensusEvaluator,
    HumanDecision,
    RubricJudge,
    capability,
    from_callables,
    reflect,
)
from autarch.intelligence.mock import MockProvider


def banner(title):
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def main():
    ws = Path("./sandbox/_evaluation")
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)

    banner("1) Deterministic checks (no LLM, no bias) \u2014 prefer these")
    fields = AssertionEvaluator([
        ("valid JSON", lambda s: s.strip().startswith("{")),
        ("has name", lambda s: '"name"' in s),
        ("has location", lambda s: '"location"' in s),
    ])
    good = fields.evaluate('{"name": "Acme", "location": "NY"}')
    bad = fields.evaluate('{"name": "Acme"}')
    print(f"  good output: passed={good.passed} score={good.score:.2f}")
    print(f"  bad output:  passed={bad.passed} score={bad.score:.2f} \u2014 {bad.reasons}")

    banner("2) LLM-as-judge against a rubric (fails closed)")
    judge_model = MockProvider(name="judge", scripted={
        "ROLE: JUDGE": json.dumps({"score": 0.85, "reasons": "accurate and concise"})
    })
    judge = RubricJudge(judge_model, rubric="extraction must be accurate and concise", threshold=0.7)
    v = judge.evaluate('{"name": "Acme"}')
    print(f"  judge score: {v.score:.2f} passed={v.passed} \u2014 {v.reasons}")

    banner("3) Consensus across judges (mitigates single-judge bias)")
    consensus = ConsensusEvaluator([fields, judge], strategy="mean", threshold=0.7)
    cv = consensus.evaluate('{"name": "Acme", "location": "NY"}')
    print(f"  consensus: score={cv.score:.2f} passed={cv.passed} \u2014 {cv.reasons}")

    banner("4) Reflection \u2014 produce, evaluate, improve (bounded)")
    attempts = {"n": 0}

    def produce(feedback):
        attempts["n"] += 1
        if feedback:
            print(f"    retry after feedback: {feedback}")
        return '{"name": "Acme"}' if attempts["n"] == 1 else '{"name": "Acme", "location": "NY"}'

    result = reflect(produce, fields, min_score=1.0, max_revisions=2)
    print(f"  final output: {result.output}")
    print(f"  revisions: {result.revisions}  passed: {result.verdict.passed}")

    banner("5) GOVERNED evaluation \u2014 the verdict is signed & provable")
    prov = MockProvider(name="m", scripted={
        "ROLE: PROPOSER": json.dumps({"capability": "doc.extract", "params": {"input": "project.pdf"}, "rationale": "extract"}),
        "ROLE: CHALLENGER": json.dumps({"verdict": "approve", "reasons": "ok"}),
    })
    docs = from_callables({"extract": lambda input: '{"name": "Riverside Bridge", "location": "Portland"}'}, namespace="doc")
    agent = Agent(
        intent="extract project fields from project.pdf",
        council=[prov], adapters=[docs], grants=[capability("doc.extract")],
        workspace=ws, preside_fn=lambda d, g: HumanDecision.RATIFY.value,
    )
    run = agent.run(evaluate=fields)
    print(f"  output:   {run.result.output}")
    print(f"  verdict:  passed={run.verdict.passed} score={run.verdict.score:.2f} by {run.verdict.evaluator}")
    rec = agent.memory.get(run.why_id)
    print(f"  recorded in ledger: eval_score={rec.eval_score} eval_passed={rec.eval_passed}")
    print(f"  provenance verifies (verdict is in the signed payload): {agent.memory.verify_provenance(run.why_id)}")
    print("\n  You can PROVE this output was evaluated and what it scored \u2014 nobody else can.")


if __name__ == "__main__":
    main()
