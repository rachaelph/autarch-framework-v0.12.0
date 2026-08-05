"""The five classic intelligent-agent types — built on Autarch, and governed.

The Russell & Norvig taxonomy (simple-reflex, model-based-reflex, goal-based,
utility-based, learning) isn't a set of new primitives — it's a set of *patterns*.
Autarch already has every building block, so each archetype is a few lines of
composition. The twist: in Autarch every one of them is **governed** — a reflex,
a planner, or a utility-maximizer still cannot exceed its capability grants, and
every action it takes is signed into the tamper-evident ledger.

    python examples/agent_types.py

Fully offline (deterministic mock council + rule planner; no network).

Mapping:
  1. Simple Reflex       -> enact() with if-then percept rules      (kernel gates it)
  2. Model-Based Reflex  -> RecallMemory as the internal world-model
  3. Goal-Based          -> Orchestrator + Planner (plan to a goal)
  4. Utility-Based       -> CostModel risk vectors + argmax over a utility score
  5. Learning            -> PrecedentStore (rulings) + reflect() (evaluator feedback)
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from autarch import (
    Action,
    Agent,
    AssertionEvaluator,
    CostModel,
    Orchestrator,
    capability,
    reflect,
)


def _ws(name: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"autarch_{name}_"))


# 1. SIMPLE REFLEX -----------------------------------------------------------
# Fixed condition -> action rules. No memory of the past, no model of the future.
# But unlike a toaster, the kernel still governs what the reflex is allowed to do.
def simple_reflex() -> None:
    agent = Agent(
        "reflex", grants=[capability("file.write")], workspace=_ws("reflex")
    )
    rules = {
        "toast_done": Action("file.write", {"path": "toast.txt", "content": "pop!"}),
        "purge_all": Action("file.delete", {"path": "toast.txt"}),  # ungranted
    }
    print("\n[1] SIMPLE REFLEX  (if-this-then-that; governed)")
    for percept in ("toast_done", "purge_all"):
        result = agent.enact(rules[percept])
        verb = "fired" if result.executed else "BLOCKED by kernel"
        print(f"    percept '{percept}' -> {rules[percept].capability}: {verb}")


# 2. MODEL-BASED REFLEX ------------------------------------------------------
# Keeps an internal model of the world (RecallMemory) so it can act on state it
# can't currently observe — the classic "window is open but I can't see it now".
def model_based_reflex() -> None:
    agent = Agent(
        "thermostat", grants=[capability("file.write")], workspace=_ws("model")
    )
    # A past percept updated the internal model with a hidden fact.
    agent.remember("a window is open in the living room", subject="window", tags=["state"])

    # The current percept shows only temperature; the window isn't observable now.
    percept = {"temperature_c": 18}
    hidden = agent.recall("is a window open", subject="window")  # consult the model

    if hidden:
        action = Action("file.write", {"path": "hvac.txt", "content": "close window, then heat"})
        reason = f"model recalled hidden state: {hidden[0].content!r}"
    else:
        action = Action("file.write", {"path": "hvac.txt", "content": "heat on"})
        reason = "no hidden state; react to percept only"

    agent.enact(action)
    print("\n[2] MODEL-BASED REFLEX  (acts on unobserved state via world-model)")
    print(f"    percept {percept} + {reason}")
    print(f"    -> decided: {action.params['content']!r}")


# 3. GOAL-BASED --------------------------------------------------------------
# Has a target and PLANS the steps to reach it. This is exactly the Orchestrator:
# a planner decomposes the goal, governed children execute, results are synthesized.
def goal_based() -> None:
    master = Agent(
        "goal", grants=[capability("file.write"), capability("file.read")], workspace=_ws("goal")
    )
    goal = "create plan.txt that says ship the release then read plan.txt"
    result = Orchestrator(master).run(goal)
    print("\n[3] GOAL-BASED  (plan a sequence of steps toward a goal)")
    print(f"    goal: {goal}")
    for i, task in enumerate(result.plan.subtasks, 1):
        print(f"      step {i}: {task.description}")
    print(f"    -> {result.executed_count}/{len(result.children)} steps executed")


# 4. UTILITY-BASED -----------------------------------------------------------
# Doesn't just reach a goal — scores candidate actions on a utility function and
# picks the best. Risk comes straight from Autarch's CostModel; benefit is yours.
def utility_based() -> None:
    agent = Agent(
        "utility",
        grants=[capability("file.read"), capability("file.write"), capability("file.move")],
        workspace=_ws("utility"),
    )
    cost = CostModel()
    RISK_WEIGHT = 0.1
    candidates = [
        ("reuse existing", Action("file.read", {"path": "data.txt"}), 0.30),
        ("write fresh", Action("file.write", {"path": "data.txt", "content": "value"}), 0.90),
        ("archive old", Action("file.move", {"path": "data.txt", "dest": "old.txt"}), 0.50),
    ]

    scored = []
    for label, action, benefit in candidates:
        risk = cost.estimate(action).get("risk", 0.0)
        utility = benefit - RISK_WEIGHT * risk
        scored.append((utility, label, action, benefit, risk))
    scored.sort(key=lambda t: t[0], reverse=True)

    print("\n[4] UTILITY-BASED  (maximize a utility score; balance benefit vs risk)")
    for utility, label, _action, benefit, risk in scored:
        print(f"    {label:16s} benefit={benefit:.2f} risk={risk:.0f} -> utility={utility:.2f}")
    best = scored[0]
    result = agent.enact(best[2])  # enact the argmax
    print(f"    -> chose '{best[1]}' (utility {best[0]:.2f}); executed: {result.executed}")


# 5. LEARNING ----------------------------------------------------------------
# Improves over time from feedback. Two governed mechanisms:
#   (a) reflect()      — learn from an evaluator's feedback (bounded retry loop).
#   (b) PrecedentStore — learn your rulings and apply them next time.
def learning() -> None:
    agent = Agent("learner", grants=[capability("file.write")], workspace=_ws("learn"))

    # (a) Reflection: keep improving a draft until it satisfies the evaluator.
    evaluator = AssertionEvaluator([
        ("mentions revenue", lambda s: "revenue" in s.lower()),
        ("mentions risk", lambda s: "risk" in s.lower()),
    ])
    drafts = iter([
        "Quarterly summary: revenue is up.",                     # missing 'risk' -> 0.5
        "Quarterly summary: revenue is up; a key risk remains.",  # both -> 1.0, passes
    ])

    def produce(feedback):
        return next(drafts, "Quarterly summary: revenue is up; a key risk remains.")

    outcome = reflect(produce, evaluator, min_score=1.0, max_revisions=2)

    print("\n[5] LEARNING  (improve from feedback + remember rulings)")
    print(f"    (a) reflection converged after {outcome.revisions} revision(s); "
          f"passed={outcome.verdict.passed}")

    # (b) Precedent: your overrule of a risky action is remembered and applied.
    agent.precedents.record("file.delete", "overrule", "never delete production files")
    agent.precedents.record("file.delete", "overrule", "again: no prod deletes")
    prec = agent.precedents.lookup("file.delete")
    print(f"    (b) {prec.note()} -> auto-applied to future file.delete motions")


def main() -> None:
    print("=" * 70)
    print("THE FIVE INTELLIGENT-AGENT TYPES, BUILT ON AUTARCH (and governed)")
    print("=" * 70)
    simple_reflex()
    model_based_reflex()
    goal_based()
    utility_based()
    learning()
    print("\n" + "=" * 70)
    print("All five archetypes = composition of existing primitives. Every action")
    print("still passes the kernel and is signed into the ledger — intelligence is")
    print("unlimited; consequences are governed.")
    print("=" * 70)


if __name__ == "__main__":
    main()
