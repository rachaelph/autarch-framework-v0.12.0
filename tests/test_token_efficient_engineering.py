from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from autarch.intelligence.pricing import estimate_tokens
from examples.token_efficient_agentic_engineering.engine import (
    ContextAssembler,
    DeterministicEngineeringProvider,
    KnowledgeItem,
    Skill,
    SkillRegistry,
    TokenBudgetExceeded,
    TokenEfficientEngineering,
    TokenLedger,
    load_knowledge,
)


HERE = Path(__file__).resolve().parents[1] / "examples" / "token_efficient_agentic_engineering"


def test_skill_registry_activates_relevant_capabilities_only():
    registry = SkillRegistry([
        Skill("security", "secure APIs", ("jwt", "authentication"), "fail closed", ("repo.config",)),
        Skill("frontend", "style pages", ("css", "accessibility"), "be accessible", ("repo.ui",)),
    ])
    selected = registry.route("Add JWT authentication")
    assert [skill.name for skill in selected] == ["security"]


def test_context_assembler_honors_hard_serialized_limit_and_skips_oversized():
    skill = Skill("security", "security", ("token",), "safe", ("repo.search",), ("security",))
    items = [
        KnowledgeItem("sec-001", "token " + "x" * 2000, "security"),
        KnowledgeItem("sec-002", "token validation must fail closed", "security"),
    ]
    bundle = ContextAssembler(items).assemble("token", skill, token_limit=20)
    assert estimate_tokens(bundle.text) <= 20
    assert bundle.selected_ids == ("sec-002",)
    assert "sec-001" in bundle.rejected_ids


def test_token_ledger_prevents_parallel_overcommit():
    ledger = TokenLedger(100)

    def try_reserve(_):
        try:
            return ledger.reserve("workers", 30)
        except TokenBudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(try_reserve, range(8)))
    admitted = [reservation for reservation in reservations if reservation is not None]
    assert len(admitted) == 3
    assert ledger.snapshot()["reserved"] == 90
    for reservation in admitted:
        ledger.release(reservation)
    assert ledger.snapshot()["reserved"] == 0


def test_phase_limit_fails_closed():
    ledger = TokenLedger(1000, {"synthesis": 100})
    with pytest.raises(TokenBudgetExceeded, match="synthesis"):
        ledger.reserve("synthesis", 101)


def test_optimized_run_reduces_tokens_and_preserves_quality(tmp_path):
    app = TokenEfficientEngineering(
        DeterministicEngineeringProvider(),
        load_knowledge(HERE / "knowledge.json"),
        workspace=tmp_path,
        max_parallel=3,
    )
    report = app.compare(
        "Add JWT authentication with tests, safe telemetry, and a rotation runbook",
        optimized_budget=12000,
        context_budget=2400,
    )
    assert report.optimized.total_tokens < report.baseline.total_tokens
    assert len(report.optimized.selected_skills) < len(report.baseline.selected_skills)
    assert len(report.optimized.exposed_tools) < len(report.baseline.exposed_tools)
    assert report.optimized.citation_score == 1.0
    assert report.optimized.section_score == 1.0
    assert report.token_reduction_percent > 0
    assert report.to_dict()["improvement"]["quality_preserved"] is True


def test_spawned_specialists_have_attenuated_capabilities(tmp_path):
    app = TokenEfficientEngineering(
        DeterministicEngineeringProvider(),
        load_knowledge(HERE / "knowledge.json"),
        workspace=tmp_path,
    )
    security = next(skill for skill in app.registry.skills if skill.name == "security")
    child = app._provision(security)
    assert {grant.name for grant in child.grants} == set(security.tools)
    assert {grant.name for grant in child.grants} < {grant.name for grant in app.master.grants}
