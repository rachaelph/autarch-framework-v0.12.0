"""PolicyEngine tests — declarative rules layered on the kernel."""
from autarch.contracts import Action
from autarch.policy import Policy, PolicyEffect, PolicyEngine


def test_no_policies_allows():
    decision = PolicyEngine([]).evaluate(Action("file.write", {"path": "a"}))
    assert decision.effect == PolicyEffect.ALLOW.value
    assert not decision.denies and not decision.requires_ratify
    assert decision.note() == ""


def test_deny_effect():
    engine = PolicyEngine([Policy("no-del", PolicyEffect.DENY.value, "file.delete", reason="never delete")])
    decision = engine.evaluate(Action("file.delete", {"path": "a"}))
    assert decision.denies
    assert "never delete" in decision.note()
    assert "no-del" in decision.applied


def test_require_ratify_with_predicate():
    engine = PolicyEngine([
        Policy("big", PolicyEffect.REQUIRE_RATIFY.value, "file.write",
               when=lambda p: len(str(p.get("content", ""))) > 5, reason="big write")
    ])
    small = engine.evaluate(Action("file.write", {"content": "hi"}))
    big = engine.evaluate(Action("file.write", {"content": "way too long"}))
    assert small.effect == PolicyEffect.ALLOW.value
    assert big.requires_ratify


def test_strictest_effect_wins():
    engine = PolicyEngine([
        Policy("a", PolicyEffect.REQUIRE_RATIFY.value, "file.*"),
        Policy("b", PolicyEffect.DENY.value, "file.delete"),
    ])
    decision = engine.evaluate(Action("file.delete", {"path": "a"}))
    assert decision.denies  # deny outranks require_ratify


def test_wildcard_star_matches_everything():
    engine = PolicyEngine([Policy("all", PolicyEffect.REQUIRE_RATIFY.value, "*")])
    assert engine.evaluate(Action("anything.at.all", {})).requires_ratify


def test_faulty_predicate_does_not_match():
    # A predicate that raises must never crash the kernel; treat as no match.
    engine = PolicyEngine([
        Policy("bad", PolicyEffect.DENY.value, "file.write", when=lambda p: p["missing"])
    ])
    decision = engine.evaluate(Action("file.write", {}))
    assert decision.effect == PolicyEffect.ALLOW.value
