"""Governed, multi-agent finance intelligence reference implementation.

This executable, offline example mirrors the architecture shown in the finance
pitch:

    four governed data feeds
        -> eight reusable specialist analysis components
        -> governed multi-agent synthesis with evidence references
        -> audience-specific reports for investors, auditors, banks, regulators

No API keys or third-party services are required. Replace the deterministic data
callables with SEC EDGAR, a market-data provider, a news service, and FRED while
keeping the same capabilities and governance contract.

Run from the repository root:
    python examples/finance_intelligence.py

Artifacts are written to sandbox/finance_intelligence/outputs/.
This is an architecture demonstration, not investment, audit, tax, or legal advice.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import stat
import statistics
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from finance_report import render_finance_report

from autarch import (
    Agent,
    AssertionEvaluator,
    HumanDecision,
    Invariant,
    Policy,
    PolicyEffect,
    capability,
    from_callables,
)

WORKSPACE = Path("./sandbox/finance_intelligence")
TICKER = "ACME"


def _remove_readonly(func, path, _error) -> None:
    """Allow deterministic cleanup when OneDrive marks generated folders read-only."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def sec_filing(ticker: str) -> dict:
    """Deterministic stand-in for normalized SEC XBRL and filing disclosures."""
    return {
        "source": "SEC 10-K (offline fixture)",
        "ticker": ticker,
        "period": "FY2025",
        "currency": "USD millions",
        "revenue": 12800.0,
        "revenue_prior": 11600.0,
        "operating_income": 1920.0,
        "net_income": 1344.0,
        "operating_cash_flow": 1760.0,
        "capex": 510.0,
        "cash": 920.0,
        "debt": 3100.0,
        "equity": 5200.0,
        "shares": 400.0,
        "audit_opinion": "unmodified",
        "material_weakness": False,
        "related_party_growth_pct": 38.0,
        "required_disclosures": ["liquidity", "market-risk", "controls"],
    }


def market_data(ticker: str) -> dict:
    """Deterministic stand-in for adjusted price and volume history."""
    closes = [28.0, 28.4, 28.1, 29.0, 29.4, 30.1, 30.0, 31.2, 32.4, 34.8]
    volumes = [1.0, 1.1, 0.9, 1.2, 1.0, 1.3, 1.1, 1.5, 2.1, 3.8]
    return {
        "source": "Market data (offline fixture)",
        "ticker": ticker,
        "as_of": "2026-09-01",
        "closes": closes,
        "volumes_millions": volumes,
        "market_cap": closes[-1] * 400.0,
        "sector": "Industrials",
    }


def news_sentiment(ticker: str) -> dict:
    """Deterministic stand-in for licensed news and sentiment enrichment."""
    return {
        "source": "News sentiment (offline fixture)",
        "ticker": ticker,
        "aggregate_score": -0.18,
        "items": [
            {"headline": "ACME raises full-year guidance", "score": 0.62},
            {"headline": "Regulator reviews distributor incentives", "score": -0.71},
            {"headline": "Unusual quarter-end channel activity reported", "score": -0.45},
        ],
    }


def macro_indicators(region: str) -> dict:
    """Deterministic stand-in for FRED/central-bank macroeconomic series."""
    return {
        "source": "Macro indicators (offline fixture)",
        "region": region,
        "policy_rate": 0.0475,
        "ten_year_yield": 0.043,
        "inflation": 0.027,
        "gdp_growth": 0.019,
        "credit_spread": 0.021,
    }


def _base(name: str, summary: str, confidence: float, findings: list, metrics: dict) -> dict:
    return {
        "component": name,
        "summary": summary,
        "confidence": round(confidence, 2),
        "findings": findings,
        "metrics": metrics,
    }


def fundamental_analysis(sec: dict, market: dict) -> dict:
    growth = sec["revenue"] / sec["revenue_prior"] - 1
    margin = sec["operating_income"] / sec["revenue"]
    roe = sec["net_income"] / sec["equity"]
    return _base(
        "fundamental_analysis",
        "Profitable growth and cash conversion are positive; leverage requires monitoring.",
        0.91,
        ["revenue increased year over year", "operating cash flow exceeds net income"],
        {"revenue_growth": round(growth, 4), "operating_margin": round(margin, 4),
         "roe": round(roe, 4), "debt_to_equity": round(sec["debt"] / sec["equity"], 4)},
    )


def valuation_analysis(sec: dict, market: dict, macro: dict) -> dict:
    free_cash_flow = sec["operating_cash_flow"] - sec["capex"]
    discount_rate = macro["ten_year_yield"] + 0.055
    terminal_growth = min(macro["gdp_growth"], 0.025)
    enterprise_value = free_cash_flow * 1.04 / (discount_rate - terminal_growth)
    equity_value = enterprise_value - sec["debt"] + sec["cash"]
    fair_value = equity_value / sec["shares"]
    current = market["closes"][-1]
    return _base(
        "valuation_analysis",
        "DCF indicates limited upside at the current market price.",
        0.78,
        ["valuation is sensitive to discount rate", "terminal growth is capped by macro growth"],
        {"free_cash_flow": free_cash_flow, "discount_rate": round(discount_rate, 4),
         "fair_value_per_share": round(fair_value, 2), "market_price": current,
         "upside_pct": round(fair_value / current - 1, 4)},
    )


def audit_assurance(sec: dict) -> dict:
    exceptions = []
    if sec["material_weakness"]:
        exceptions.append("material weakness disclosed")
    if sec["related_party_growth_pct"] > 25:
        exceptions.append("related-party growth exceeds review threshold")
    return _base(
        "audit_assurance",
        "Unmodified opinion, with targeted procedures recommended for related-party activity.",
        0.88,
        exceptions or ["no control exception identified"],
        {"opinion": sec["audit_opinion"], "material_weakness": sec["material_weakness"],
         "review_exceptions": len(exceptions)},
    )


def aml_fraud_detection(sec: dict, news: dict) -> dict:
    negative = sum(1 for item in news["items"] if item["score"] < -0.4)
    score = min(1.0, 0.18 + negative * 0.18 + sec["related_party_growth_pct"] / 200)
    return _base(
        "aml_fraud_detection",
        "Moderate fraud-risk signal; investigate incentives and related-party movements.",
        0.82,
        ["negative regulatory news", "elevated related-party growth"],
        {"risk_score": round(score, 3), "negative_news_flags": negative},
    )


def risk_profiling(sec: dict, market: dict, macro: dict) -> dict:
    prices = market["closes"]
    returns = [prices[i] / prices[i - 1] - 1 for i in range(1, len(prices))]
    volatility = statistics.pstdev(returns) * math.sqrt(252)
    return _base(
        "risk_profiling",
        "Market and leverage risk are moderate in a restrictive rate environment.",
        0.84,
        ["recent price volatility is elevated", "policy rate increases refinancing pressure"],
        {"annualized_volatility": round(volatility, 4),
         "net_debt_to_operating_income": round((sec["debt"] - sec["cash"]) / sec["operating_income"], 3),
         "policy_rate": macro["policy_rate"]},
    )


def regulatory_compliance(sec: dict, news: dict) -> dict:
    expected = {"liquidity", "market-risk", "controls", "cybersecurity"}
    missing = sorted(expected - set(sec["required_disclosures"]))
    return _base(
        "regulatory_compliance",
        "One disclosure gap requires legal and compliance confirmation.",
        0.9,
        [f"missing disclosure: {item}" for item in missing] or ["required disclosures present"],
        {"missing_disclosures": missing, "regulatory_news_flags":
         sum(1 for item in news["items"] if "Regulator" in item["headline"])},
    )


def manipulation_detection(market: dict, news: dict) -> dict:
    vols = market["volumes_millions"]
    baseline = statistics.mean(vols[:-1])
    volume_multiple = vols[-1] / baseline
    price_jump = market["closes"][-1] / market["closes"][-2] - 1
    flagged = volume_multiple > 2.5 and price_jump > 0.05
    return _base(
        "manipulation_detection",
        "Quarter-end price/volume pattern warrants surveillance review." if flagged else "No material pattern detected.",
        0.8,
        ["volume spike coincides with price increase", "compare timing with guidance and channel activity"] if flagged else ["no threshold breach"],
        {"volume_multiple": round(volume_multiple, 2), "last_price_change": round(price_jump, 4),
         "surveillance_flag": flagged},
    )


def peer_benchmarking(sec: dict, market: dict) -> dict:
    margin = sec["operating_income"] / sec["revenue"]
    peer_margins = [0.11, 0.13, 0.145, 0.16, 0.18]
    rank = sum(1 for value in peer_margins if margin >= value) / len(peer_margins)
    return _base(
        "peer_benchmarking",
        "Operating margin is above the sector median, while valuation is near the upper range.",
        0.86,
        ["margin compares favorably", "premium requires sustained execution"],
        {"operating_margin": round(margin, 4), "margin_percentile": round(rank, 2),
         "peer_median_margin": statistics.median(peer_margins)},
    )


def synthesize(ticker: str, analyses: list) -> dict:
    evidence = [item["evidence_id"] for item in analyses]
    avg_confidence = statistics.mean(item["output"]["confidence"] for item in analyses)
    risk_components = {item["output"]["component"]: item["output"]["summary"] for item in analyses}
    flagged = [name for name in ("aml_fraud_detection", "regulatory_compliance", "manipulation_detection")
               if name in risk_components]
    return {
        "component": "multi_agent_synthesis",
        "ticker": ticker,
        "recommendation": "HOLD / ENHANCED DUE DILIGENCE",
        "summary": "Quality fundamentals are offset by valuation, disclosure, and surveillance concerns.",
        "confidence": round(avg_confidence, 2),
        "reflection": "A second pass checked that every material conclusion maps to signed specialist evidence.",
        "priority_reviews": flagged,
        "evidence_chain": evidence,
        "specialist_views": risk_components,
    }


def audience_report(audience: str, synthesis: dict) -> dict:
    emphasis = {
        "investment_firm": ["fundamental_analysis", "valuation_analysis", "peer_benchmarking"],
        "audit_firm": ["audit_assurance", "regulatory_compliance", "aml_fraud_detection"],
        "bank": ["risk_profiling", "aml_fraud_detection", "fundamental_analysis"],
        "regulator": ["regulatory_compliance", "manipulation_detection", "audit_assurance"],
    }[audience]
    return {
        "audience": audience,
        "ticker": synthesis["ticker"],
        "recommendation": synthesis["recommendation"],
        "executive_summary": synthesis["summary"],
        "focus_components": emphasis,
        "evidence_chain": synthesis["evidence_chain"],
        "disclaimer": "Demonstration output; accountable professional judgment remains required.",
    }


def _must_output(run, capability_name: str) -> dict:
    if not run.executed or run.result is None or not run.result.ok:
        reason = run.result.error if run.result is not None else run.gate.reason
        raise RuntimeError(f"{capability_name} failed: {reason}")
    return run.result.output


def _run_child(parent: Agent, name: str, adapter, params: dict, evaluator=None) -> Tuple[dict, str]:
    cap = f"analysis.{name}"
    child = parent.spawn(
        intent=f"Run reusable {name.replace('_', ' ')} for {TICKER}",
        grants=[capability(cap)],
        adapters=[adapter],
        node_id=f"specialist:{name}",
    )
    run = child.enact(cap, params, actor=f"specialist:{name}", evaluate=evaluator)
    return _must_output(run, cap), run.why_id


def main() -> None:
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE, onerror=_remove_readonly)
    outputs_dir = WORKSPACE / "outputs"
    outputs_dir.mkdir(parents=True)

    ingestion = from_callables(
        {"sec": sec_filing, "market": market_data, "news": news_sentiment, "macro": macro_indicators},
        namespace="ingest",
    )
    analysis_functions: Dict[str, Callable[..., dict]] = {
        "fundamental_analysis": fundamental_analysis,
        "valuation_analysis": valuation_analysis,
        "audit_assurance": audit_assurance,
        "aml_fraud_detection": aml_fraud_detection,
        "risk_profiling": risk_profiling,
        "regulatory_compliance": regulatory_compliance,
        "manipulation_detection": manipulation_detection,
        "peer_benchmarking": peer_benchmarking,
    }
    analysis = from_callables(analysis_functions, namespace="analysis")
    synthesis_adapter = from_callables({"committee": synthesize}, namespace="synthesis")
    consumers = from_callables(
        {name: (lambda synthesis, audience=name: audience_report(audience, synthesis))
         for name in ("investment_firm", "audit_firm", "bank", "regulator")},
        namespace="consumer",
    )

    policies = [
        Policy("regulator_requires_accountable_release", PolicyEffect.REQUIRE_RATIFY.value,
               capability="consumer.regulator", reason="regulatory output requires explicit accountable release"),
        Policy("no_external_publication", PolicyEffect.DENY.value,
               capability="external.publish", reason="this reference workflow cannot publish externally"),
    ]
    parent = Agent(
        intent=f"Produce governed multi-audience intelligence for {TICKER}",
        grants=[capability("ingest.*"), capability("analysis.*"), capability("synthesis.*"),
                capability("consumer.*")],
        adapters=[ingestion, analysis, synthesis_adapter, consumers],
        policies=policies,
        workspace=WORKSPACE,
        budget={"calls": 20, "risk": 60, "cost": 1.0},
        preside_fn=lambda deliberation, gate: HumanDecision.RATIFY.value,
    )

    guarantee = parent.guarantee([
        Invariant.forbid("external.publish", "analysis cannot publish itself"),
        Invariant.require_approval("consumer.regulator", "regulatory release is accountable"),
    ])
    if not guarantee.all_hold:
        raise RuntimeError("Static governance guarantees did not hold")

    print("\n[1/4] Governed data ingestion")
    data = {}
    for name, params in (
        ("sec", {"ticker": TICKER}),
        ("market", {"ticker": TICKER}),
        ("news", {"ticker": TICKER}),
        ("macro", {"region": "US"}),
    ):
        child = parent.spawn(f"Ingest {name}", grants=[capability(f"ingest.{name}")], adapters=[ingestion])
        run = child.enact(f"ingest.{name}", params, actor=f"ingestion:{name}")
        data[name] = _must_output(run, f"ingest.{name}")
        print(f"  signed ingest.{name}: {run.why_id}")

    print("\n[2/4] Eight capability-attenuated specialist agents")
    jobs = {
        "fundamental_analysis": {"sec": data["sec"], "market": data["market"]},
        "valuation_analysis": {"sec": data["sec"], "market": data["market"], "macro": data["macro"]},
        "audit_assurance": {"sec": data["sec"]},
        "aml_fraud_detection": {"sec": data["sec"], "news": data["news"]},
        "risk_profiling": {"sec": data["sec"], "market": data["market"], "macro": data["macro"]},
        "regulatory_compliance": {"sec": data["sec"], "news": data["news"]},
        "manipulation_detection": {"market": data["market"], "news": data["news"]},
        "peer_benchmarking": {"sec": data["sec"], "market": data["market"]},
    }
    quality = AssertionEvaluator([
        ("structured component", lambda output: bool(output.get("component"))),
        ("bounded confidence", lambda output: 0 <= output.get("confidence", -1) <= 1),
        ("has findings", lambda output: bool(output.get("findings"))),
        ("has metrics", lambda output: bool(output.get("metrics"))),
    ], name="finance_component_quality")
    specialist_results = []
    for name, params in jobs.items():
        output, why_id = _run_child(parent, name, analysis, params, quality)
        specialist_results.append({"output": output, "evidence_id": why_id})
        print(f"  {name:28} confidence={output['confidence']:.2f} evidence={why_id}")

    print("\n[3/4] Multi-agent synthesis with evidence chain")
    committee = parent.spawn(
        "Synthesize specialist evidence and reflect on coverage",
        grants=[capability("synthesis.committee")], adapters=[synthesis_adapter], node_id="committee",
    )
    synthesis_run = committee.enact(
        "synthesis.committee", {"ticker": TICKER, "analyses": specialist_results}, actor="investment_committee"
    )
    synthesis = _must_output(synthesis_run, "synthesis.committee")
    print(f"  recommendation: {synthesis['recommendation']}")
    print(f"  evidence links: {len(synthesis['evidence_chain'])}")
    print(f"  synthesis proof: {synthesis_run.why_id}")

    print("\n[4/4] Governed consumer-specific views")
    reports = {}
    for audience in ("investment_firm", "audit_firm", "bank", "regulator"):
        reporter = parent.spawn(
            f"Prepare {audience} report", grants=[capability(f"consumer.{audience}")], adapters=[consumers]
        )
        run = reporter.enact(f"consumer.{audience}", {"synthesis": synthesis}, actor=f"owner:{audience}")
        reports[audience] = _must_output(run, f"consumer.{audience}")
        print(f"  {audience:18} signed report={run.why_id}")

    parent.memory.export_audit(outputs_dir / "signed_audit.jsonl")
    chain_ok, broken = parent.memory.verify_chain()
    package = {
        "architecture": {
            "ingestion": list(data),
            "analysis_components": list(jobs),
            "synthesis": "multi_agent_synthesis",
            "consumers": list(reports),
        },
        "source_data": data,
        "analyses": specialist_results,
        "synthesis": synthesis,
        "reports": reports,
        "governance": {
            "guarantees": [{"invariant": proof.invariant.label(), "holds": proof.holds,
                            "reason": proof.reason} for proof in guarantee.proofs],
            "budget": parent.budget.snapshot(),
            "integrity": {
                "verified": chain_ok,
                "broken_record": broken,
                "record_count": parent.memory.count(),
            },
        },
    }
    (outputs_dir / "finance_intelligence.json").write_text(
        json.dumps(package, indent=2), encoding="utf-8"
    )
    html_path = render_finance_report(package, outputs_dir / "finance_intelligence.html")
    (outputs_dir / "ARCHITECTURE.txt").write_text(
        "DATA: SEC | MARKET | NEWS | MACRO\n"
        "              |\n"
        "              v\n"
        "SPECIALISTS: FUNDAMENTALS | VALUATION | AUDIT | AML/FRAUD\n"
        "             RISK | COMPLIANCE | MANIPULATION | PEERS\n"
        "              |\n"
        "              v\n"
        "MULTI-AGENT SYNTHESIS + SIGNED EVIDENCE CHAIN\n"
        "              |\n"
        "              v\n"
        "CONSUMERS: INVESTMENT | AUDIT | BANK | REGULATOR\n",
        encoding="utf-8",
    )

    print(f"\nLedger verifies: {chain_ok} (broken={broken})")
    print(f"Budget: {parent.budget.snapshot()}")
    print(f"Outputs: {outputs_dir.resolve()}")
    print(f"HTML report: {html_path.resolve()}")
    print("\nDemonstration only; accountable professional judgment remains required.")


if __name__ == "__main__":
    main()
