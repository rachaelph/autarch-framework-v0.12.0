"""Formal guarantee tests — sound static proofs over grants + policies."""
from autarch import Agent, capability
from autarch.guarantees import Invariant, prove_guarantees
from autarch.policy import Policy, PolicyEffect


# --- forbid ---------------------------------------------------------------

def test_forbid_holds_when_no_grant():
    grants = [capability("file.read"), capability("file.write")]
    report = prove_guarantees([Invariant.forbid("file.delete")], grants)
    assert report.all_hold is True
    assert "deny by default" in report.proofs[0].reason


def test_forbid_fails_with_matching_grant():
    report = prove_guarantees([Invariant.forbid("file.delete")], [capability("file.delete")])
    assert report.all_hold is False
    assert report.proofs[0].counterexample == "file.delete"


def test_forbid_fails_with_wildcard_grant():
    report = prove_guarantees([Invariant.forbid("file.delete")], [capability("file.*")])
    assert report.all_hold is False
    assert report.proofs[0].counterexample == "file.*"


def test_forbid_holds_with_unconditional_deny_policy():
    grants = [capability("file.*")]
    policies = [Policy("no-del", PolicyEffect.DENY.value, "file.delete")]
    report = prove_guarantees([Invariant.forbid("file.delete")], grants, policies)
    assert report.all_hold is True
    assert "unconditionally denies" in report.proofs[0].reason


def test_forbid_ignores_conditional_deny():
    # A deny policy with a predicate cannot be relied upon -> still fails.
    grants = [capability("file.*")]
    policies = [Policy("cond", PolicyEffect.DENY.value, "file.delete", when=lambda p: p.get("x"))]
    report = prove_guarantees([Invariant.forbid("file.delete")], grants, policies)
    assert report.all_hold is False


def test_forbid_require_ratify_policy_does_not_satisfy():
    # require_ratify still lets a human execute -> forbid (absolute) not satisfied.
    grants = [capability("file.*")]
    policies = [Policy("rat", PolicyEffect.REQUIRE_RATIFY.value, "file.delete")]
    report = prove_guarantees([Invariant.forbid("file.delete")], grants, policies)
    assert report.all_hold is False


# --- require_approval -----------------------------------------------------

def test_require_approval_holds_with_require_ratify_policy():
    grants = [capability("payment.*")]
    policies = [Policy("two-person", PolicyEffect.REQUIRE_RATIFY.value, "payment.send")]
    report = prove_guarantees([Invariant.require_approval("payment.send")], grants, policies)
    assert report.all_hold is True


def test_require_approval_holds_with_deny_policy():
    grants = [capability("payment.*")]
    policies = [Policy("freeze", PolicyEffect.DENY.value, "payment.send")]
    report = prove_guarantees([Invariant.require_approval("payment.send")], grants, policies)
    assert report.all_hold is True


def test_require_approval_fails_without_policy():
    report = prove_guarantees([Invariant.require_approval("payment.send")], [capability("payment.*")])
    assert report.all_hold is False
    assert report.proofs[0].counterexample == "payment.*"


def test_require_approval_holds_when_no_grant():
    report = prove_guarantees([Invariant.require_approval("payment.send")], [capability("file.read")])
    assert report.all_hold is True  # vacuous: can't execute at all


def test_require_approval_ignores_conditional_policy():
    grants = [capability("payment.*")]
    policies = [Policy("cond", PolicyEffect.REQUIRE_RATIFY.value, "payment.send", when=lambda p: p.get("amt", 0) > 100)]
    report = prove_guarantees([Invariant.require_approval("payment.send")], grants, policies)
    assert report.all_hold is False  # conservative: predicate not relied upon


def test_wildcard_policy_satisfies_require_approval():
    grants = [capability("payment.*")]
    policies = [Policy("global", PolicyEffect.REQUIRE_RATIFY.value, "*")]
    report = prove_guarantees([Invariant.require_approval("payment.send")], grants, policies)
    assert report.all_hold is True


# --- confine --------------------------------------------------------------

def test_confine_holds_when_grant_within_prefix():
    grants = [capability("file.write", scope={"path_prefix": "reports/q1"})]
    report = prove_guarantees([Invariant.confine("file.write", "reports")], grants)
    assert report.all_hold is True


def test_confine_holds_exact_prefix():
    grants = [capability("file.write", scope={"path_prefix": "reports"})]
    report = prove_guarantees([Invariant.confine("file.write", "reports")], grants)
    assert report.all_hold is True


def test_confine_fails_when_grant_broader():
    grants = [capability("file.write", scope={"path_prefix": "."})]
    report = prove_guarantees([Invariant.confine("file.write", "reports")], grants)
    assert report.all_hold is False
    assert report.proofs[0].counterexample == "file.write"


def test_confine_fails_when_grant_has_no_scope():
    grants = [capability("file.write")]
    report = prove_guarantees([Invariant.confine("file.write", "reports")], grants)
    assert report.all_hold is False


def test_confine_holds_when_no_grant():
    report = prove_guarantees([Invariant.confine("file.write", "reports")], [capability("file.read")])
    assert report.all_hold is True  # vacuous


def test_confine_holds_when_unconditionally_denied():
    grants = [capability("file.write", scope={"path_prefix": "."})]
    policies = [Policy("freeze", PolicyEffect.DENY.value, "file.write")]
    report = prove_guarantees([Invariant.confine("file.write", "reports")], grants, policies)
    assert report.all_hold is True  # can't act at all -> can't act outside


# --- report + agent + monotonicity ---------------------------------------

def test_report_failures_lists_only_failures():
    grants = [capability("file.read")]
    report = prove_guarantees(
        [Invariant.forbid("file.delete"), Invariant.forbid("file.read")], grants
    )
    # delete forbidden (no grant) holds; read is granted so its forbid fails.
    assert report.all_hold is False
    assert len(report.failures()) == 1
    assert report.failures()[0].invariant.capability == "file.read"


def test_agent_guarantee_method(tmp_path):
    agent = Agent(
        intent="work", council=["mock"],
        grants=[capability("file.read"), capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path,
    )
    report = agent.guarantee([Invariant.forbid("file.delete")])
    assert report.all_hold is True


def test_attenuation_preserves_guarantee(tmp_path):
    # An invariant proven for a parent must also hold for any spawned child,
    # because delegation only narrows authority.
    parent = Agent(
        intent="root", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "reports"})],
        workspace=tmp_path,
    )
    inv = [Invariant.forbid("file.delete"), Invariant.confine("file.write", "reports")]
    assert parent.guarantee(inv).all_hold is True

    child = parent.spawn(intent="leaf", grants=[capability("file.write", scope={"path_prefix": "reports/x"})])
    assert child.guarantee(inv).all_hold is True
