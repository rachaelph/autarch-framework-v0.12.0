"""End-to-end Agent SDK tests — the full governed loop."""
from autarch import Agent, Policy, capability
from autarch.contracts import HumanDecision
from autarch.policy import PolicyEffect


def test_create_file_executes_and_records(tmp_path):
    agent = Agent(
        intent="create a file called notes.txt that says Hello Autarch",
        council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."}), capability("file.read")],
        workspace=tmp_path,
    )
    result = agent.run()

    assert result.executed is True
    assert result.human_decision == HumanDecision.RATIFY.value
    assert (tmp_path / "notes.txt").read_text() == "Hello Autarch"
    assert result.why_id is not None

    record = agent.memory.get(result.why_id)
    assert record.executed is True
    assert record.capability == "file.write"


def test_ungranted_capability_is_denied(tmp_path):
    # Intent wants a delete, but no file.delete grant is given -> kernel denies.
    (tmp_path / "victim.txt").write_text("data")
    agent = Agent(
        intent="delete the file victim.txt",
        council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path,
    )
    result = agent.run()

    assert result.gate.allowed is False
    assert result.executed is False
    # The file must still exist — governance prevented the action.
    assert (tmp_path / "victim.txt").exists()


def test_auto_preside_overrules_on_revise(tmp_path):
    # Even if delete were granted, the challenger says 'revise', so auto-preside
    # overrules rather than auto-ratifying a risky action.
    (tmp_path / "victim.txt").write_text("data")
    agent = Agent(
        intent="delete the file victim.txt",
        council=["mock"],
        grants=[capability("file.delete", scope={"path_prefix": "."})],
        workspace=tmp_path,
    )
    result = agent.run()

    assert result.gate.allowed is True  # it WAS granted
    assert result.human_decision == HumanDecision.OVERRULE.value
    assert result.executed is False
    assert (tmp_path / "victim.txt").exists()


def test_preside_fn_can_ratify(tmp_path):
    # A custom presiding function can ratify the risky action explicitly.
    (tmp_path / "victim.txt").write_text("data")
    agent = Agent(
        intent="delete the file victim.txt",
        council=["mock"],
        grants=[capability("file.delete", scope={"path_prefix": "."})],
        workspace=tmp_path,
        preside_fn=lambda delib, gate: HumanDecision.RATIFY.value,
    )
    result = agent.run()

    assert result.executed is True
    assert not (tmp_path / "victim.txt").exists()
    # Undo information was captured for reversibility.
    assert result.result.undo["restore"] == "data"


# --- Phase 2: council plurality, policy, precedent, send-back ---------------

def test_council_disagreement_is_surfaced(tmp_path):
    agent = Agent(
        intent="delete the file victim.txt",
        council=["mock:bold", "mock:cautious"],
        grants=[capability("file.delete", scope={"path_prefix": "."})],
        workspace=tmp_path,
    )
    result = agent.run()
    delib = result.deliberation

    assert set(delib.voices) == {"mock:bold", "mock:cautious"}
    assert delib.tally.get("approve") == 1
    assert delib.tally.get("veto") == 1
    assert delib.recommendation == "veto"
    assert delib.has_disagreement is True
    # A veto in the council means auto-presiding overrules.
    assert result.executed is False


def test_overrule_precedent_applied_next_time(tmp_path):
    grants = [capability("file.move", scope={"path_prefix": "."}), capability("file.read")]
    (tmp_path / "a.txt").write_text("hi")

    # First run: the council approves the move, but the autarch overrules it.
    first = Agent(
        intent="move a.txt to b.txt",
        council=["mock"],
        grants=grants,
        workspace=tmp_path,
        preside_fn=lambda d, g: HumanDecision.OVERRULE.value,
    ).run()
    assert first.executed is False
    assert (tmp_path / "a.txt").exists()  # not moved

    # Second run: auto-presiding. The standing overrule precedent is applied,
    # even though the council would otherwise approve the move.
    second = Agent(
        intent="move a.txt to b.txt",
        council=["mock"],
        grants=grants,
        workspace=tmp_path,
    ).run()
    assert second.precedent is not None
    assert second.precedent.decision == "overrule"
    assert second.human_decision == HumanDecision.OVERRULE.value
    assert second.executed is False
    assert (tmp_path / "a.txt").exists()


def test_policy_require_ratify_blocks_auto_but_allows_explicit(tmp_path):
    big = "x" * 300
    policies = [
        Policy(
            name="big-write",
            effect=PolicyEffect.REQUIRE_RATIFY.value,
            capability="file.write",
            when=lambda p: len(str(p.get("content", ""))) > 280,
            reason="Large write",
        )
    ]
    grants = [capability("file.write", scope={"path_prefix": "."})]

    # Auto mode: require_ratify means auto-presiding may not ratify -> not executed.
    auto = Agent(
        intent=f"create big.txt that says {big}",
        council=["mock"], grants=grants, workspace=tmp_path, policies=policies,
    ).run()
    assert auto.policy.requires_ratify
    assert auto.executed is False
    assert not (tmp_path / "big.txt").exists()

    # Explicit human ratification satisfies the policy -> executed.
    explicit = Agent(
        intent=f"create big.txt that says {big}",
        council=["mock"], grants=grants, workspace=tmp_path, policies=policies,
        preside_fn=lambda d, g: HumanDecision.RATIFY.value,
    ).run()
    assert explicit.executed is True
    assert (tmp_path / "big.txt").read_text() == big


def test_policy_deny_blocks_even_explicit_ratify(tmp_path):
    policies = [Policy("forbid-write", PolicyEffect.DENY.value, "file.write", reason="frozen")]
    result = Agent(
        intent="create x.txt that says hi",
        council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path,
        policies=policies,
        preside_fn=lambda d, g: HumanDecision.RATIFY.value,
    ).run()
    assert result.policy.denies
    assert result.executed is False
    assert not (tmp_path / "x.txt").exists()


def test_send_back_excludes_capability_and_finds_no_alternative(tmp_path):
    calls = {"n": 0}

    def preside(deliberation, gate):
        calls["n"] += 1
        return HumanDecision.SEND_BACK.value

    (tmp_path / "v.txt").write_text("data")
    result = Agent(
        intent="delete the file v.txt",
        council=["mock"],
        grants=[capability("file.delete", scope={"path_prefix": "."})],
        workspace=tmp_path,
        preside_fn=preside,
        max_rounds=2,
    ).run()

    # Round 1 proposes delete -> sent back (delete excluded). Round 2 finds no
    # alternative motion, so nothing executes and the file survives.
    assert result.executed is False
    assert (tmp_path / "v.txt").exists()
    assert result.deliberation.rounds == 2
    assert calls["n"] == 1


def test_send_back_exhausted_becomes_overrule(tmp_path):
    (tmp_path / "v.txt").write_text("d")
    result = Agent(
        intent="delete the file v.txt",
        council=["mock"],
        grants=[capability("file.delete", scope={"path_prefix": "."})],
        workspace=tmp_path,
        preside_fn=lambda d, g: HumanDecision.SEND_BACK.value,
        max_rounds=1,
    ).run()
    assert result.human_decision == HumanDecision.OVERRULE.value
    assert result.executed is False
