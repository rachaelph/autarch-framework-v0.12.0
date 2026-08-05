"""Tests for the declarative policy DSL (compile / simulate / diff)."""
from autarch.contracts import Action
from autarch.policydsl import (compile_condition, compile_policies, diff,
                               simulate)


def test_leaf_ops():
    assert compile_condition({"param": "n", "op": "gt", "value": 5})({"n": 6})
    assert not compile_condition({"param": "n", "op": "gt", "value": 5})({"n": 5})
    assert compile_condition({"param": "s", "op": "matches", "value": r"a+"})({"s": "aaa"})
    assert compile_condition({"param": "c", "op": "in", "value": ["BTC"]})({"c": "BTC"})


def test_missing_param_is_deny_safe():
    # a condition on an absent param must be False, never raise
    assert compile_condition({"param": "x", "op": "eq", "value": 1})({}) is False


def test_combinators():
    cond = {"all": [{"param": "a", "op": "eq", "value": 1},
                    {"any": [{"param": "b", "op": "eq", "value": 2},
                             {"not": {"param": "c", "op": "eq", "value": 3}}]}]}
    fn = compile_condition(cond)
    assert fn({"a": 1, "b": 2, "c": 9})
    assert fn({"a": 1, "b": 9, "c": 9})       # via the not-branch
    assert not fn({"a": 0, "b": 2, "c": 9})   # first clause fails


def test_simulate_reports_effects():
    pols = compile_policies([
        {"name": "big", "effect": "require_ratify", "capability": "payment.*",
         "when": {"param": "amount", "op": "gt", "value": 1000}},
    ])
    rows = simulate(pols, [Action("payment.send", {"amount": 5000}),
                          Action("payment.send", {"amount": 5})])
    assert rows[0]["effect"] == "require_ratify"
    assert rows[1]["effect"] == "allow"


def test_diff_reports_only_changes():
    before = compile_policies([])
    after = compile_policies([
        {"name": "no-crypto", "effect": "deny", "capability": "payment.*",
         "when": {"param": "currency", "op": "in", "value": ["BTC"]}},
    ])
    samples = [Action("payment.send", {"currency": "USD"}),
               Action("payment.send", {"currency": "BTC"})]
    changes = diff(before, after, samples)
    assert len(changes) == 1
    assert changes[0]["before"] == "allow" and changes[0]["after"] == "deny"
