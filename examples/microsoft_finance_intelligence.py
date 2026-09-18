"""Governed Microsoft Finance Intelligence — live/cached go-to-market example.

This example discovers Microsoft's latest Form 10-K and recent filing sequence,
retrieves authoritative SEC and Microsoft Investor Relations evidence, adds FRED
macro data, indicative market history, and SEC-derived peers, then runs fifteen
capability-attenuated specialist analyses. All actions are policy/budget gated and
written to a signed, tamper-evident evidence chain. External release remains gated
by a separate durable, digest-bound professional review.

Run from the repository root:
    python examples/microsoft_finance_intelligence.py --mode live
    python examples/microsoft_finance_intelligence.py --mode hybrid
    python examples/microsoft_finance_intelligence.py --mode cache-only

Artifacts are written to sandbox/microsoft_finance_intelligence/outputs/.
This is a controlled pilot, not investment, audit, tax, legal, credit, or
regulatory advice and not a claim of complete enterprise production readiness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import stat
import statistics
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from microsoft_finance_data import (
    DEFAULT_USER_AGENT,
    SourceCache,
    fetch_macro_data,
    fetch_market_data,
    fetch_microsoft_investor_relations,
    fetch_microsoft_sec,
    fetch_peer_data,
    finite,
)
from microsoft_finance_report import render_microsoft_markdown, render_microsoft_report

from autarch import (
    Agent,
    AssertionEvaluator,
    Invariant,
    Policy,
    PolicyEffect,
    capability,
    from_callables,
)
from autarch.review import ReleaseReviewStore, canonical_sha256


WORKSPACE = Path("./sandbox/microsoft_finance_intelligence")
OUTPUTS = WORKSPACE / "outputs"
TICKER = "MSFT"
REVIEW_DB = WORKSPACE / "reviews" / "release_reviews.db"
ANALYSIS_COMPONENTS = (
    "financial_performance",
    "quarterly_change_intelligence",
    "filing_change_detection",
    "segment_economics",
    "cash_flow_capital_intensity",
    "balance_sheet_credit",
    "capital_allocation",
    "valuation_scenarios",
    "market_risk",
    "peer_benchmarking",
    "accounting_quality",
    "audit_assurance",
    "tax_exposure",
    "ai_cloud_strategy",
    "regulatory_operational_risk",
)
AUDIENCES = (
    "investment_committee",
    "cfo_strategy",
    "audit_committee",
    "credit_committee",
    "regulator_risk",
)


def _remove_readonly(func: Callable[..., Any], path: str, _error: Any) -> None:
    last_error: Optional[PermissionError] = None
    for attempt in range(8):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error


def _reset_generated() -> None:
    """Reset ephemeral run outputs while preserving cache and durable reviews."""
    for path in (OUTPUTS, WORKSPACE / ".autarch"):
        if path.exists():
            shutil.rmtree(path, onerror=_remove_readonly)
    OUTPUTS.mkdir(parents=True, exist_ok=True)


def _release_content(
    data: Mapping[str, Any],
    source_manifest: Mapping[str, Mapping[str, Any]],
    analyses: Sequence[Mapping[str, Any]],
    synthesis: Mapping[str, Any],
    reports: Mapping[str, Any],
) -> Dict[str, Any]:
    """Canonical decision content reviewed by accountable owners.

    Volatile retrieval timestamps and generated-at metadata are excluded so an
    unchanged evidence/analysis package recovers the same pending or approved
    review.  Changed source bytes, facts, analysis, synthesis, or persona content
    necessarily produce a new digest and supersede the prior decision.
    """
    stable_synthesis = {
        key: value for key, value in synthesis.items()
        if key not in {"as_of", "evidence_chain"}
    }
    stable_reports = {
        audience: {
            key: value for key, value in report.items()
            if key not in {"evidence_chain", "review_id", "review_status", "release_authorized"}
        }
        for audience, report in reports.items()
    }
    return {
        "schema_version": "autarch.microsoft-finance.release.v1.1",
        "ticker": TICKER,
        "filing": data["sec"].get("filing"),
        "latest_quarter_filing": data["sec"].get("latest_quarter_filing"),
        "sources": {
            source_id: {
                "sha256": source.get("sha256"),
                "url": source.get("url"),
                "evidence_class": source.get("evidence_class"),
                "authoritative": bool(source.get("authoritative")),
            }
            for source_id, source in sorted(source_manifest.items())
        },
        "selected_fact_ids": sorted(data["sec"].get("fact_index", {})),
        "analyses": [item["output"] for item in analyses],
        "synthesis": stable_synthesis,
        "reports": stable_reports,
    }


def _review_status(review: Any, artifact_digest: str) -> Dict[str, Any]:
    if review is None:
        return {
            "id": None,
            "status": "not_submitted",
            "artifact_digest": artifact_digest,
            "artifact_digest_matches": False,
            "content_binding_valid": False,
            "approval_valid_for_digest": False,
            "release_authorized": False,
        }
    value = review.as_dict()
    digest_matches = review.artifact_digest == artifact_digest
    authorization_valid = review.valid_for(artifact_digest)
    value["artifact_digest_matches"] = digest_matches
    value["content_binding_valid"] = digest_matches
    value["approval_valid_for_digest"] = authorization_valid
    value["release_authorized"] = authorization_valid
    value["identity_assurance"] = (
        "Reviewer names are attributed; production deployment must inject authenticated IdP identities."
    )
    return value


def _ratio(numerator: Any, denominator: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        numerator_f = float(numerator)
        denominator_f = float(denominator)
    except (TypeError, ValueError):
        return default
    if not denominator_f or not math.isfinite(numerator_f) or not math.isfinite(denominator_f):
        return default
    return numerator_f / denominator_f


def _growth(current: Any, prior: Any) -> Optional[float]:
    value = _ratio(current, prior)
    return value - 1 if value is not None else None


def _cagr(current: Any, prior: Any, periods: int) -> Optional[float]:
    if periods <= 0:
        return None
    ratio = _ratio(current, prior)
    if ratio is None or ratio < 0:
        return None
    return ratio ** (1 / periods) - 1


def _latest(series: Mapping[str, Any]) -> Tuple[str, float]:
    if not series:
        raise RuntimeError("required financial series is empty")
    year = max(series)
    return year, finite(series[year])


def _value(series: Mapping[str, Any], year: str, default: float = 0.0) -> float:
    return finite(series.get(year), default)


def _annual(sec: Mapping[str, Any], metric: str) -> Mapping[str, float]:
    return sec.get("annual", {}).get(metric, {})


def _balance(sec: Mapping[str, Any], metric: str) -> Mapping[str, float]:
    return sec.get("balance_sheet", {}).get(metric, {})


def _finding(
    headline: str,
    detail: str,
    *,
    severity: str,
    evidence: Sequence[str],
    kind: str = "calculated",
    fact_ids: Sequence[str] = (),
    lineage_applicable: bool = False,
) -> Dict[str, Any]:
    selected_fact_ids = list(dict.fromkeys(str(value) for value in fact_ids if value))
    if lineage_applicable and not selected_fact_ids:
        raise ValueError(f"finding '{headline}' requires at least one selected fact ID")
    return {
        "headline": headline,
        "detail": detail,
        "severity": severity,
        "kind": kind,
        "evidence_refs": list(evidence),
        "fact_ids": selected_fact_ids,
        "fact_lineage_applicable": bool(lineage_applicable or selected_fact_ids),
    }


def _component(
    name: str,
    title: str,
    verdict: str,
    summary: str,
    confidence: float,
    metrics: Mapping[str, Any],
    findings: Sequence[Mapping[str, Any]],
    *,
    evidence: Sequence[str],
    caveats: Sequence[str],
    questions: Sequence[str],
    tables: Optional[Mapping[str, Any]] = None,
    assumptions: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    confidence_value = float(confidence)
    if not math.isfinite(confidence_value) or not 0.0 <= confidence_value <= 1.0:
        raise ValueError(f"component '{name}' confidence must be finite and between zero and one")
    return {
        "component": name,
        "title": title,
        "verdict": verdict,
        "summary": summary,
        "confidence": round(confidence_value, 2),
        "confidence_label": "uncalibrated analytical confidence",
        "metrics": dict(metrics),
        "findings": [dict(item) for item in findings],
        "evidence_refs": list(dict.fromkeys(evidence)),
        "caveats": list(caveats),
        "diligence_questions": list(questions),
        "tables": dict(tables or {}),
        "assumptions": list(assumptions or []),
        "fact_ids": list(dict.fromkeys(
            fact_id
            for finding in findings
            for fact_id in finding.get("fact_ids", [])
        )),
    }


def _fact_id(sec: Mapping[str, Any], section: str, metric: str, period: str) -> str:
    if section == "quarterly":
        collection = sec.get("quarterly_lineage", {})
    elif section == "ttm":
        collection = sec.get("ttm_lineage", {})
    else:
        collection = sec.get("fact_lineage", {}).get(section, {})
    return str(
        collection.get(metric, {})
        .get(period, {})
        .get("fact_id", "")
    )


def _fact_ids(
    sec: Mapping[str, Any], references: Sequence[Tuple[str, str, str]]
) -> List[str]:
    return [
        fact_id for section, metric, period in references
        if (fact_id := _fact_id(sec, section, metric, period))
    ]


def _member_fact_ids(
    sec: Mapping[str, Any], section: str, entity: str, period: str,
    metrics: Sequence[str],
) -> List[str]:
    records = (
        sec.get("fact_lineage", {})
        .get(section, {})
        .get(entity, {})
        .get(period, {})
    )
    return [
        str(records[metric]["fact_id"])
        for metric in metrics
        if metric in records and records[metric].get("fact_id")
    ]


def financial_performance(sec: dict) -> dict:
    revenue = _annual(sec, "revenue")
    years = sorted(revenue)
    if len(years) < 3:
        raise RuntimeError("at least three annual revenue observations are required")
    latest, prior, start = years[-1], years[-2], years[-3]
    operating_income = _annual(sec, "operating_income")
    net_income = _annual(sec, "net_income")
    gross_profit = _annual(sec, "gross_profit")
    equity = _balance(sec, "equity")
    revenue_growth = _growth(revenue[latest], revenue[prior])
    revenue_cagr = _cagr(revenue[latest], revenue[start], 2)
    op_margin = _ratio(_value(operating_income, latest), revenue[latest])
    prior_op_margin = _ratio(_value(operating_income, prior), revenue[prior])
    gross_margin = _ratio(_value(gross_profit, latest), revenue[latest])
    net_margin = _ratio(_value(net_income, latest), revenue[latest])
    avg_equity = (_value(equity, latest) + _value(equity, prior)) / 2
    roe = _ratio(_value(net_income, latest), avg_equity)
    operating_leverage = (_growth(_value(operating_income, latest), _value(operating_income, prior)) or 0) - (revenue_growth or 0)
    evidence = ["S-SEC-COMPANYFACTS", "S-SEC-10K"]
    table = []
    for year in years[-3:]:
        table.append({
            "year": f"FY{year}",
            "revenue_usd_b": revenue[year],
            "gross_margin": _ratio(_value(gross_profit, year), revenue[year]),
            "operating_income_usd_b": _value(operating_income, year),
            "operating_margin": _ratio(_value(operating_income, year), revenue[year]),
            "net_income_usd_b": _value(net_income, year),
        })
    return _component(
        "financial_performance",
        "Financial performance & operating leverage",
        "High-quality double-digit growth",
        f"FY{latest} revenue and operating income expanded while operating margin moved to {op_margin:.1%}.",
        0.96,
        {
            "fiscal_year": f"FY{latest}",
            "revenue_usd_b": revenue[latest],
            "revenue_growth": revenue_growth,
            "two_year_revenue_cagr": revenue_cagr,
            "gross_margin": gross_margin,
            "operating_margin": op_margin,
            "operating_margin_change_pp": (op_margin - prior_op_margin) * 100 if op_margin is not None and prior_op_margin is not None else None,
            "net_margin": net_margin,
            "return_on_average_equity": roe,
            "operating_leverage_spread": operating_leverage,
        },
        [
            _finding(
                "Scale translated into operating leverage",
                f"Revenue grew {revenue_growth:.1%}; operating income grew {_growth(_value(operating_income, latest), _value(operating_income, prior)):.1%}, a {operating_leverage * 100:.1f} percentage-point spread.",
                severity="positive", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("annual", "revenue", latest), ("annual", "revenue", prior),
                    ("annual", "operating_income", latest), ("annual", "operating_income", prior),
                ]), lineage_applicable=True,
            ),
            _finding(
                "The three-year earnings base is materially larger",
                f"Revenue increased from ${revenue[start]:,.1f}B in FY{start} to ${revenue[latest]:,.1f}B in FY{latest}; net income reached ${_value(net_income, latest):,.1f}B.",
                severity="positive", evidence=evidence, kind="reported",
                fact_ids=_fact_ids(sec, [
                    ("annual", "revenue", start), ("annual", "revenue", latest),
                    ("annual", "net_income", latest),
                ]), lineage_applicable=True,
            ),
            _finding(
                "Profitability remains exceptional but is not the whole cash story",
                f"Operating margin was {op_margin:.1%} and net margin was {net_margin:.1%}; the separate cash-flow analysis tests whether AI infrastructure spending converts those earnings into distributable cash.",
                severity="watch", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("annual", "revenue", latest), ("annual", "operating_income", latest),
                    ("annual", "net_income", latest),
                ]), lineage_applicable=True,
            ),
        ],
        evidence=evidence,
        caveats=["Historical GAAP performance does not establish future growth or valuation."],
        questions=[
            "How much of FY2026 growth is durable consumption growth versus capacity catch-up or contract timing?",
            "What revenue contribution and gross-margin profile does management expect from paid Copilot and agent workloads?",
        ],
        tables={"historical_financials": table},
    )


def quarterly_change_intelligence(sec: dict) -> dict:
    quarterly = sec.get("quarterly", {})
    ttm = sec.get("ttm", {})
    ttm_series = sec.get("ttm_series", {})
    latest_period = str(sec.get("ttm_as_of", ""))
    revenue = quarterly.get("revenue", {})
    operating_income = quarterly.get("operating_income", {})
    cfo = quarterly.get("operating_cash_flow", {})
    capex = quarterly.get("capex", {})
    fcf = quarterly.get("free_cash_flow", {})
    periods = sorted(revenue, key=lambda value: (int(value[2:6]), int(value[-1])))
    if len(periods) < 8 or not latest_period:
        raise RuntimeError("at least eight normalized fiscal quarters are required")
    latest = periods[-1]
    prior = periods[-2]
    year_ago = periods[-5]
    latest_revenue = finite(revenue.get(latest))
    revenue_yoy = _growth(latest_revenue, revenue.get(year_ago))
    revenue_sequential = _growth(latest_revenue, revenue.get(prior))
    latest_margin = _ratio(operating_income.get(latest), latest_revenue)
    year_ago_margin = _ratio(operating_income.get(year_ago), revenue.get(year_ago))
    latest_fcf = finite(fcf.get(latest))
    year_ago_fcf = finite(fcf.get(year_ago))
    reconciliation = sec.get("reconciliation", [])
    failed_reconciliations = [item for item in reconciliation if not item.get("within_tolerance")]
    derived = [
        record for records in sec.get("quarterly_lineage", {}).values()
        for record in records.values() if record.get("derived")
    ]
    evidence = ["S-SEC-COMPANYFACTS", "S-SEC-10Q-LATEST", "S-SEC-10K"]
    latest_fact_ids = []
    for metric in ("revenue", "operating_income", "operating_cash_flow", "capex", "free_cash_flow"):
        record = sec.get("quarterly_lineage", {}).get(metric, {}).get(latest, {})
        if record.get("fact_id"):
            latest_fact_ids.append(record["fact_id"])
    ttm_fact_ids = [
        record["fact_id"] for metric in (
            "revenue", "operating_income", "net_income", "operating_cash_flow", "capex", "free_cash_flow"
        ) if (record := sec.get("ttm_lineage", {}).get(metric, {}).get(latest_period))
    ]
    latest_fiscal_year = f"FY{latest[2:6]}"
    reconciliation_fact_ids = list(dict.fromkeys(
        fact_id
        for check in reconciliation
        if check.get("fiscal_year") == latest_fiscal_year
        for fact_id in [check.get("annual_fact_id"), *check.get("quarter_fact_ids", [])]
        if fact_id
    ))
    rows = []
    for period in periods[-12:]:
        period_revenue = finite(revenue.get(period))
        rows.append({
            "period": period,
            "revenue_usd_b": period_revenue,
            "revenue_yoy": _growth(period_revenue, revenue.get(
                f"FY{int(period[2:6]) - 1} Q{period[-1]}"
            )),
            "operating_income_usd_b": finite(operating_income.get(period)),
            "operating_margin": _ratio(operating_income.get(period), period_revenue),
            "operating_cash_flow_usd_b": finite(cfo.get(period)),
            "capex_usd_b": finite(capex.get(period)),
            "free_cash_flow_usd_b": finite(fcf.get(period)),
        })
    return _component(
        "quarterly_change_intelligence",
        "Quarterly, TTM & change intelligence",
        "Growth remains strong; capital intensity governs cash conversion",
        f"{latest} revenue was ${latest_revenue:,.1f}B, {revenue_yoy:.1%} above the year-ago quarter; trailing revenue reached ${finite(ttm.get('revenue')):,.1f}B.",
        0.96,
        {
            "latest_period": latest,
            "quarterly_revenue_usd_b": latest_revenue,
            "quarterly_revenue_yoy": revenue_yoy,
            "quarterly_revenue_sequential": revenue_sequential,
            "quarterly_operating_margin": latest_margin,
            "quarterly_operating_margin_yoy_change_pp": (
                (latest_margin - year_ago_margin) * 100
                if latest_margin is not None and year_ago_margin is not None else None
            ),
            "quarterly_free_cash_flow_usd_b": latest_fcf,
            "quarterly_free_cash_flow_yoy": _growth(latest_fcf, year_ago_fcf),
            "ttm_as_of": latest_period,
            "ttm_revenue_usd_b": ttm.get("revenue"),
            "ttm_operating_income_usd_b": ttm.get("operating_income"),
            "ttm_net_income_usd_b": ttm.get("net_income"),
            "ttm_operating_cash_flow_usd_b": ttm.get("operating_cash_flow"),
            "ttm_capex_usd_b": ttm.get("capex"),
            "ttm_free_cash_flow_usd_b": ttm.get("free_cash_flow"),
            "ttm_operating_margin": ttm.get("operating_margin"),
            "ttm_free_cash_flow_margin": ttm.get("free_cash_flow_margin"),
            "ttm_capex_intensity": ttm.get("capex_intensity"),
            "quarterly_reconciliation_checks": len(reconciliation),
            "failed_reconciliation_checks": len(failed_reconciliations),
            "derived_quarter_fact_count": len(derived),
        },
        [
            _finding(
                "Quarterly growth remains double digit",
                f"Revenue increased {revenue_yoy:.1%} year over year and {revenue_sequential:.1%} sequentially in {latest}; operating margin was {latest_margin:.1%}.",
                severity="positive", evidence=evidence, fact_ids=latest_fact_ids,
                lineage_applicable=True,
            ),
            _finding(
                "TTM scale is paired with exceptional infrastructure spend",
                f"Through {latest_period}, TTM CFO was ${finite(ttm.get('operating_cash_flow')):,.1f}B, capex was ${finite(ttm.get('capex')):,.1f}B, and conventional FCF was ${finite(ttm.get('free_cash_flow')):,.1f}B.",
                severity="high", evidence=evidence, fact_ids=ttm_fact_ids,
                lineage_applicable=True,
            ),
            _finding(
                "Quarter construction reconciles to filed annual facts",
                f"All {len(reconciliation)} available annual-to-quarter checks reconcile within tolerance; {len(derived)} quarter observations are explicitly derived from filed cumulative or annual facts.",
                severity="neutral" if not failed_reconciliations else "high",
                evidence=evidence,
                kind="methodology",
                fact_ids=reconciliation_fact_ids,
                lineage_applicable=True,
            ),
        ],
        evidence=evidence,
        caveats=[
            "TTM values are deterministic sums of four fiscal quarters and are not management guidance.",
            "Q2/Q3 may be derived from cumulative interim facts; Q4 is annual less nine-month YTD only when both filed inputs exist.",
            "Quarterly cash flow is seasonal and should not be annualized from a single period.",
        ],
        questions=[
            "Is year-over-year revenue acceleration converting to FCF after the full capacity-build cycle?",
            "Which quarter-level changes reflect seasonality, investment marks, contract timing, or durable operations?",
            "Do derived quarters reconcile to the face statements and footnotes after every new filing or amendment?",
        ],
        tables={
            "quarterly_history": rows,
            "ttm_history": [
                {"period": period, **{
                    f"{metric}_usd_b": values.get(period)
                    for metric, values in ttm_series.items()
                    if metric in {"revenue", "operating_income", "operating_cash_flow", "capex", "free_cash_flow"}
                }}
                for period in sorted(
                    set().union(*(set(values) for values in ttm_series.values())),
                    key=lambda value: (int(value[2:6]), int(value[-1])),
                )[-9:]
            ],
            "quarter_reconciliation": reconciliation[-16:],
        },
    )


def filing_change_detection(sec: dict) -> dict:
    comparison = sec.get("filing_changes", {})
    changes = comparison.get("changes", [])
    summary = comparison.get("summary", {})
    current = comparison.get("current_filing", {})
    prior = comparison.get("prior_filing", {})
    timeline = sec.get("filing_timeline", [])
    material_events = [item for item in timeline if item.get("material_event")]
    evidence = ["S-SEC-10K", "S-SEC-10K-PRIOR", "S-SEC-SUBMISSIONS"]
    findings = []
    for change in changes[:3]:
        findings.append(_finding(
            f"{change['topic']}: {change['change']}",
            f"The deterministic comparison moved from {change.get('prior_value')} to {change.get('current_value')} on a {change.get('basis')} basis.",
            severity=change.get("severity", "watch"),
            evidence=change.get("evidence_refs", evidence),
            kind="disclosure comparison",
        ))
    while len(findings) < 3:
        fallback = (
            "No additional high-priority topic delta detected"
            if changes else "No deterministic disclosure topic delta detected"
        )
        findings.append(_finding(
            fallback,
            "The absence of a detected keyword or extracted-attribute change is not evidence that the filing is unchanged; professional redline review remains required.",
            severity="neutral", evidence=evidence, kind="methodology",
        ))
    return _component(
        "filing_change_detection",
        "Filing timeline & disclosure-change triage",
        "Machine triage narrows review; it does not replace a legal redline",
        f"Compared the {prior.get('form', 'prior filing')} filed {prior.get('filing_date')} with the {current.get('form', 'current filing')} filed {current.get('filing_date')} and surfaced {len(changes)} deterministic review signals.",
        0.82,
        {
            "current_filing_date": current.get("filing_date"),
            "prior_filing_date": prior.get("filing_date"),
            "changed_signals": summary.get("changed_signals", 0),
            "high_attention_changes": summary.get("high_attention", 0),
            "watch_changes": summary.get("watch", 0),
            "recent_filing_count": len(timeline),
            "recent_material_event_count": len(material_events),
        },
        findings,
        evidence=evidence,
        caveats=comparison.get("limitations", [
            "Deterministic topic detection is not a legal redline or materiality conclusion."
        ]),
        questions=[
            "Which added, removed, or expanded passages survive a paragraph-level professional redline?",
            "Do recent 8-K items alter any thesis, risk, estimate, or disclosure-control conclusion?",
            "Did definitions, segment presentation, accounting policies, or non-GAAP reconciliations change?",
        ],
        tables={"filing_changes": changes, "filing_timeline": timeline},
    )


def segment_economics(sec: dict, investor_relations: dict) -> dict:
    segments = sec.get("segments", {})
    report_year = sec["filing"]["reportDate"][:4]
    prior_year = str(int(report_year) - 1)
    total_revenue = _value(_annual(sec, "revenue"), report_year)
    rows = []
    for name, values in segments.items():
        current = values.get(report_year, {})
        prior = values.get(prior_year, {})
        revenue = finite(current.get("revenue"))
        operating_income = finite(current.get("operating_income"))
        rows.append({
            "segment": name,
            "revenue_usd_b": revenue,
            "revenue_growth": _growth(revenue, prior.get("revenue")),
            "revenue_mix": _ratio(revenue, total_revenue),
            "operating_income_usd_b": operating_income,
            "operating_margin": _ratio(operating_income, revenue),
            "margin_change_pp": ((_ratio(operating_income, revenue) or 0) -
                                 (_ratio(prior.get("operating_income"), prior.get("revenue")) or 0)) * 100,
        })
    rows.sort(key=lambda item: item["revenue_usd_b"], reverse=True)
    products = []
    for name, values in sec.get("products", {}).items():
        current = finite(values.get(report_year, {}).get("revenue"))
        prior = finite(values.get(prior_year, {}).get("revenue"))
        products.append({"offering": name, "revenue_usd_b": current, "growth": _growth(current, prior), "revenue_mix": _ratio(current, total_revenue)})
    products.sort(key=lambda item: item["revenue_usd_b"], reverse=True)
    cloud = next((item for item in rows if item["segment"] == "Intelligent Cloud"), {})
    pbp = next((item for item in rows if item["segment"] == "Productivity and Business Processes"), {})
    mpc = next((item for item in rows if item["segment"] == "More Personal Computing"), {})
    ir_metrics = investor_relations.get("metrics", {})
    evidence = ["S-SEC-10K", "S-SEC-COMPANYFACTS", "S-MSFT-EARNINGS"]
    return _component(
        "segment_economics",
        "Segment & product economics",
        "Cloud-led mix shift; consumer portfolio diverges",
        f"Intelligent Cloud became a co-primary revenue engine in FY{report_year}, while More Personal Computing contracted.",
        0.94,
        {
            "intelligent_cloud_revenue_usd_b": cloud.get("revenue_usd_b"),
            "intelligent_cloud_growth": cloud.get("revenue_growth"),
            "intelligent_cloud_margin": cloud.get("operating_margin"),
            "productivity_growth": pbp.get("revenue_growth"),
            "more_personal_computing_growth": mpc.get("revenue_growth"),
            "azure_growth_q4": ir_metrics.get("azure_growth"),
            "azure_fiscal_revenue_usd_b": ir_metrics.get("azure_revenue_usd_b"),
        },
        [
            _finding(
                "Intelligent Cloud is the incremental growth engine",
                f"Segment revenue reached ${cloud.get('revenue_usd_b', 0):,.1f}B, growing {cloud.get('revenue_growth', 0):.1%} and representing {cloud.get('revenue_mix', 0):.1%} of group revenue.",
                severity="positive", evidence=evidence,
                fact_ids=(
                    _member_fact_ids(sec, "segments", "Intelligent Cloud", report_year, ("revenue",))
                    + _member_fact_ids(sec, "segments", "Intelligent Cloud", prior_year, ("revenue",))
                    + _fact_ids(sec, [("annual", "revenue", report_year)])
                ), lineage_applicable=True,
            ),
            _finding(
                "Productivity remains a high-margin ballast",
                f"Productivity and Business Processes generated ${pbp.get('operating_income_usd_b', 0):,.1f}B of operating income at a {pbp.get('operating_margin', 0):.1%} segment margin.",
                severity="positive", evidence=evidence,
                fact_ids=_member_fact_ids(
                    sec, "segments", "Productivity and Business Processes",
                    report_year, ("revenue", "operating_income"),
                ), lineage_applicable=True,
            ),
            _finding(
                "Portfolio dispersion requires separate underwriting",
                f"More Personal Computing revenue changed {mpc.get('revenue_growth', 0):.1%}; cloud and productivity momentum should not be used to mask weaker gaming, device, or consumer economics.",
                severity="watch", evidence=evidence,
                fact_ids=(
                    _member_fact_ids(sec, "segments", "More Personal Computing", report_year, ("revenue",))
                    + _member_fact_ids(sec, "segments", "More Personal Computing", prior_year, ("revenue",))
                ), lineage_applicable=True,
            ),
        ],
        evidence=evidence,
        caveats=["Microsoft recast prior segment information; comparisons use the presentation in the latest 10-K."],
        questions=[
            "What portion of Intelligent Cloud growth is AI workload demand, and what portion reflects non-AI Azure consumption?",
            "Can More Personal Computing stabilize without structurally lower investment or further portfolio actions?",
            "How should shared AI infrastructure cost be allocated across Azure, Microsoft 365 Copilot, GitHub, and consumer products?",
        ],
        tables={"segments": rows, "products": products[:10]},
    )


def cash_flow_capital_intensity(sec: dict) -> dict:
    revenue = _annual(sec, "revenue")
    cfo = _annual(sec, "operating_cash_flow")
    capex = _annual(sec, "capex")
    net_income = _annual(sec, "net_income")
    sbc = _annual(sec, "stock_compensation")
    years = sorted(set(revenue) & set(cfo) & set(capex))
    latest, prior = years[-1], years[-2]
    fcf = {year: _value(cfo, year) - _value(capex, year) for year in years}
    ppe = _balance(sec, "property_equipment")
    rows = [{
        "year": f"FY{year}",
        "operating_cash_flow_usd_b": _value(cfo, year),
        "capex_usd_b": _value(capex, year),
        "free_cash_flow_usd_b": fcf[year],
        "free_cash_flow_margin": _ratio(fcf[year], _value(revenue, year)),
        "capex_intensity": _ratio(_value(capex, year), _value(revenue, year)),
    } for year in years[-3:]]
    latest_fcf_margin = _ratio(fcf[latest], revenue[latest])
    prior_fcf_margin = _ratio(fcf[prior], revenue[prior])
    evidence = ["S-SEC-COMPANYFACTS", "S-SEC-10K"]
    return _component(
        "cash_flow_capital_intensity",
        "Cash flow & AI capital intensity",
        "Cash generation rose, but capex absorbed the increment",
        f"Operating cash flow reached ${cfo[latest]:,.1f}B, while capex rose to ${capex[latest]:,.1f}B and compressed conventional free cash flow.",
        0.96,
        {
            "operating_cash_flow_usd_b": cfo[latest],
            "operating_cash_flow_growth": _growth(cfo[latest], cfo[prior]),
            "capex_usd_b": capex[latest],
            "capex_growth": _growth(capex[latest], capex[prior]),
            "capex_intensity": _ratio(capex[latest], revenue[latest]),
            "free_cash_flow_usd_b": fcf[latest],
            "free_cash_flow_growth": _growth(fcf[latest], fcf[prior]),
            "free_cash_flow_margin": latest_fcf_margin,
            "free_cash_flow_margin_change_pp": (latest_fcf_margin - prior_fcf_margin) * 100,
            "cash_conversion": _ratio(cfo[latest], _value(net_income, latest)),
            "free_cash_flow_after_sbc_usd_b": fcf[latest] - _value(sbc, latest),
            "property_equipment_growth": _growth(_value(ppe, latest), _value(ppe, prior)),
        },
        [
            _finding(
                "Capex, not operating weakness, drove conventional FCF pressure",
                f"CFO grew {_growth(cfo[latest], cfo[prior]):.1%}, but capex grew {_growth(capex[latest], capex[prior]):.1%}; FCF moved from ${fcf[prior]:,.1f}B to ${fcf[latest]:,.1f}B.",
                severity="watch", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("annual", "operating_cash_flow", latest), ("annual", "operating_cash_flow", prior),
                    ("annual", "capex", latest), ("annual", "capex", prior),
                ]), lineage_applicable=True,
            ),
            _finding(
                "The balance sheet now embeds a much larger infrastructure base",
                f"Net property and equipment increased {_growth(_value(ppe, latest), _value(ppe, prior)):.1%}, making utilization, depreciation lives, energy availability, and hardware obsolescence central underwriting variables.",
                severity="high", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("balance_sheet", "property_equipment", latest),
                    ("balance_sheet", "property_equipment", prior),
                ]), lineage_applicable=True,
            ),
            _finding(
                "Reported CFO still covers accounting earnings",
                f"CFO was {_ratio(cfo[latest], _value(net_income, latest)):.2f}x net income, but stock compensation of ${_value(sbc, latest):,.1f}B is a real ownership cost and is shown separately.",
                severity="neutral", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("annual", "operating_cash_flow", latest), ("annual", "net_income", latest),
                    ("annual", "stock_compensation", latest),
                ]), lineage_applicable=True,
            ),
        ],
        evidence=evidence,
        caveats=[
            "CFO less capex is a non-GAAP analytical measure and does not distinguish growth from maintenance capex.",
            "Subtracting stock compensation from FCF is an ownership-economics lens, not a GAAP measure.",
        ],
        questions=[
            "What is the split between maintenance, contracted growth, and speculative AI capacity capex?",
            "What utilization and token-price assumptions are required for new AI datacenters to earn the cost of capital?",
            "How sensitive are useful lives and depreciation expense to accelerated GPU obsolescence?",
        ],
        tables={"cash_flow_history": rows},
    )


def balance_sheet_credit(sec: dict) -> dict:
    year, assets = _latest(_balance(sec, "total_assets"))
    prior_years = sorted(_balance(sec, "total_assets"))
    prior = prior_years[-2] if len(prior_years) > 1 else year
    cash = _value(_balance(sec, "cash"), year)
    investments = _value(_balance(sec, "short_term_investments"), year)
    debt = _value(_balance(sec, "long_term_debt_current"), year) + _value(_balance(sec, "long_term_debt_noncurrent"), year)
    finance_leases = _value(_balance(sec, "finance_lease_total"), year)
    operating_leases = _value(_balance(sec, "operating_lease_total"), year)
    current_assets = _value(_balance(sec, "current_assets"), year)
    current_liabilities = _value(_balance(sec, "current_liabilities"), year)
    operating_income = _value(_annual(sec, "operating_income"), year)
    interest = _value(_annual(sec, "interest_expense"), year)
    rpo = _value(_balance(sec, "remaining_performance_obligation"), year)
    evidence = ["S-SEC-COMPANYFACTS", "S-SEC-10K"]
    return _component(
        "balance_sheet_credit",
        "Balance sheet, liquidity & credit capacity",
        "Substantial liquidity; leases and build commitments matter",
        f"Microsoft ended FY{year} with ${cash + investments:,.1f}B of cash and short-term investments versus ${debt:,.1f}B of funded debt.",
        0.95,
        {
            "cash_and_short_investments_usd_b": cash + investments,
            "funded_debt_usd_b": debt,
            "net_cash_usd_b": cash + investments - debt,
            "current_ratio": _ratio(current_assets, current_liabilities),
            "debt_to_operating_income": _ratio(debt, operating_income),
            "interest_coverage": _ratio(operating_income, interest),
            "finance_lease_liabilities_usd_b": finance_leases,
            "operating_lease_liabilities_usd_b": operating_leases,
            "assets_growth": _growth(assets, _value(_balance(sec, "total_assets"), prior)),
            "remaining_performance_obligation_usd_b": rpo,
        },
        [
            _finding(
                "Funded-debt liquidity is strong",
                f"Cash and short-term investments exceeded funded debt by ${cash + investments - debt:,.1f}B; debt was {_ratio(debt, operating_income):.2f}x annual operating income.",
                severity="positive", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("balance_sheet", "cash", year), ("balance_sheet", "short_term_investments", year),
                    ("balance_sheet", "long_term_debt_current", year),
                    ("balance_sheet", "long_term_debt_noncurrent", year),
                    ("annual", "operating_income", year),
                ]), lineage_applicable=True,
            ),
            _finding(
                "Lease-adjusted obligations are economically relevant",
                f"Reported finance and operating lease liabilities total approximately ${finance_leases + operating_leases:,.1f}B and should be included in capacity and fixed-charge analysis.",
                severity="watch", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("balance_sheet", "finance_lease_total", year),
                    ("balance_sheet", "operating_lease_total", year),
                ]), lineage_applicable=True,
            ),
            _finding(
                "Backlog visibility is large but not cash in hand",
                f"Total remaining performance obligations were approximately ${rpo:,.1f}B; timing, cancellation rights, cloud consumption, and margin realization determine its economic value.",
                severity="neutral", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("balance_sheet", "remaining_performance_obligation", year),
                ]), lineage_applicable=True,
            ),
        ],
        evidence=evidence,
        caveats=["RPO is not equivalent to contracted cash, recognized revenue, or profit."],
        questions=[
            "How much datacenter capacity is covered by non-cancellable customer commitments of comparable duration?",
            "What are peak annual lease, purchase, power, and construction cash requirements under stress?",
        ],
    )


def capital_allocation(sec: dict) -> dict:
    revenue = _annual(sec, "revenue")
    year, current_revenue = _latest(revenue)
    cfo = _value(_annual(sec, "operating_cash_flow"), year)
    capex = _value(_annual(sec, "capex"), year)
    fcf = cfo - capex
    dividends = _value(_annual(sec, "dividends_paid"), year)
    buybacks = _value(_annual(sec, "share_repurchases"), year)
    sbc = _value(_annual(sec, "stock_compensation"), year)
    rd = _value(_annual(sec, "research_development"), year)
    diluted_shares = _annual(sec, "diluted_shares")
    prior_years = sorted(diluted_shares)
    prior = prior_years[-2] if len(prior_years) > 1 else year
    evidence = ["S-SEC-COMPANYFACTS", "S-SEC-10K"]
    return _component(
        "capital_allocation",
        "Capital allocation & ownership economics",
        "Reinvestment dominates shareholder distributions",
        f"Capex and R&D totaled ${capex + rd:,.1f}B, materially above ${dividends + buybacks:,.1f}B returned through dividends and repurchases.",
        0.95,
        {
            "research_development_usd_b": rd,
            "research_development_pct_revenue": _ratio(rd, current_revenue),
            "capex_plus_rd_usd_b": capex + rd,
            "dividends_paid_usd_b": dividends,
            "share_repurchases_usd_b": buybacks,
            "gross_shareholder_returns_usd_b": dividends + buybacks,
            "gross_shareholder_returns_to_fcf": _ratio(dividends + buybacks, fcf),
            "stock_compensation_usd_b": sbc,
            "net_repurchase_after_sbc_usd_b": buybacks - sbc,
            "diluted_share_change": _growth(_value(diluted_shares, year), _value(diluted_shares, prior)),
        },
        [
            _finding(
                "Capital allocation is an AI reinvestment decision first",
                f"Capex plus R&D represented {_ratio(capex + rd, current_revenue):.1%} of revenue, so future value depends on incremental returns from infrastructure and product investment.",
                severity="high", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("annual", "capex", year), ("annual", "research_development", year),
                    ("annual", "revenue", year),
                ]), lineage_applicable=True,
            ),
            _finding(
                "Buybacks should be assessed net of employee equity issuance",
                f"Cash repurchases were ${buybacks:,.1f}B versus ${sbc:,.1f}B of stock compensation; the net cash repurchase lens was ${buybacks - sbc:,.1f}B.",
                severity="neutral", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("annual", "share_repurchases", year),
                    ("annual", "stock_compensation", year),
                ]), lineage_applicable=True,
            ),
            _finding(
                "Distributions remain affordable but compete with capex",
                f"Dividends plus repurchases consumed {_ratio(dividends + buybacks, fcf):.1%} of conventional FCF in FY{year}.",
                severity="watch", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("annual", "dividends_paid", year), ("annual", "share_repurchases", year),
                    ("annual", "operating_cash_flow", year), ("annual", "capex", year),
                ]), lineage_applicable=True,
            ),
        ],
        evidence=evidence,
        caveats=["Stock compensation expense and repurchase cash outlays are not directly interchangeable period by period."],
        questions=[
            "What return threshold governs incremental AI datacenter commitments versus repurchases?",
            "At what valuation does management view buybacks as accretive after stock-based compensation?",
        ],
    )


def _dcf_value(
    starting_fcf: float,
    growth: float,
    discount_rate: float,
    terminal_growth: float,
    net_cash: float,
    shares: float,
    years: int = 5,
) -> float:
    if discount_rate <= terminal_growth or shares <= 0:
        raise ValueError("DCF requires discount rate above terminal growth and positive shares")
    present_value = 0.0
    cash_flow = starting_fcf
    for period in range(1, years + 1):
        cash_flow *= 1 + growth
        present_value += cash_flow / (1 + discount_rate) ** period
    terminal = cash_flow * (1 + terminal_growth) / (discount_rate - terminal_growth)
    equity_value = present_value + terminal / (1 + discount_rate) ** years + net_cash
    return equity_value / shares


def _reverse_dcf_growth(
    target_price: float,
    starting_fcf: float,
    discount_rate: float,
    terminal_growth: float,
    net_cash: float,
    shares: float,
) -> float:
    low, high = -0.20, 0.60
    for _ in range(100):
        middle = (low + high) / 2
        value = _dcf_value(starting_fcf, middle, discount_rate, terminal_growth, net_cash, shares)
        if value < target_price:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def valuation_scenarios(sec: dict, market: dict, macro: dict) -> dict:
    year, cfo = _latest(_annual(sec, "operating_cash_flow"))
    capex = _value(_annual(sec, "capex"), year)
    prior_year = sorted(_annual(sec, "operating_cash_flow"))[-2]
    current_fcf = cfo - capex
    prior_fcf = _value(_annual(sec, "operating_cash_flow"), prior_year) - _value(_annual(sec, "capex"), prior_year)
    normalized_fcf = (current_fcf + prior_fcf) / 2
    cash = _value(_balance(sec, "cash"), year) + _value(_balance(sec, "short_term_investments"), year)
    debt = _value(_balance(sec, "long_term_debt_current"), year) + _value(_balance(sec, "long_term_debt_noncurrent"), year)
    net_cash = cash - debt
    shares = _value(_balance(sec, "shares_outstanding"), year) or _value(_annual(sec, "diluted_shares"), year)
    price = finite(market["microsoft"]["regular_market_price"])
    scenarios = [
        {"case": "Bear", "fcf_growth": 0.08, "discount_rate": 0.09, "terminal_growth": 0.025},
        {"case": "Base", "fcf_growth": 0.14, "discount_rate": 0.0775, "terminal_growth": 0.0325},
        {"case": "Bull", "fcf_growth": 0.20, "discount_rate": 0.068, "terminal_growth": 0.035},
    ]
    for item in scenarios:
        item["fair_value_per_share"] = round(_dcf_value(
            normalized_fcf, item["fcf_growth"], item["discount_rate"], item["terminal_growth"], net_cash, shares
        ), 2)
        item["upside_downside"] = item["fair_value_per_share"] / price - 1
    ten_year = finite(macro["series"]["ten_year_treasury"]["latest"]) / 100
    base = next(item for item in scenarios if item["case"] == "Base")
    reverse_growth = _reverse_dcf_growth(price, normalized_fcf, base["discount_rate"], base["terminal_growth"], net_cash, shares)
    eps = _value(_annual(sec, "diluted_eps"), year)
    revenue = _value(_annual(sec, "revenue"), year)
    market_cap = price * shares
    enterprise_value = market_cap + debt - cash
    evidence = ["S-SEC-COMPANYFACTS", "S-MARKET-MSFT", "S-FRED-DGS10", "S-FRED-DFII10"]
    position = "above" if price > base["fair_value_per_share"] else "below"
    return _component(
        "valuation_scenarios",
        "Valuation laboratory & reverse DCF",
        "Premium valuation requires sustained FCF acceleration",
        f"The indicative price of ${price:,.2f} sits {position} the illustrative base DCF of ${base['fair_value_per_share']:,.2f}; this is a sensitivity framework, not a target price.",
        0.74,
        {
            "market_price": price,
            "market_as_of": market["microsoft"]["as_of"],
            "shares_outstanding_b": shares,
            "market_cap_usd_b": market_cap,
            "enterprise_value_usd_b": enterprise_value,
            "price_to_earnings": _ratio(price, eps),
            "price_to_free_cash_flow": _ratio(market_cap, current_fcf),
            "enterprise_value_to_sales": _ratio(enterprise_value, revenue),
            "earnings_yield": _ratio(eps, price),
            "ten_year_treasury": ten_year,
            "earnings_yield_spread_to_treasury": (_ratio(eps, price) or 0) - ten_year,
            "normalized_fcf_usd_b": normalized_fcf,
            "reverse_dcf_five_year_fcf_growth": reverse_growth,
            "base_fair_value_per_share": base["fair_value_per_share"],
        },
        [
            _finding(
                "The market capitalizes a long duration of growth",
                f"Indicative P/E is {_ratio(price, eps):.1f}x and EV/revenue is {_ratio(enterprise_value, revenue):.1f}x; the earnings yield is {(_ratio(eps, price) or 0):.1%} versus a {ten_year:.2%} 10-year Treasury yield.",
                severity="watch", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("annual", "diluted_eps", year), ("annual", "revenue", year),
                    ("balance_sheet", "shares_outstanding", year),
                    ("balance_sheet", "cash", year), ("balance_sheet", "short_term_investments", year),
                    ("balance_sheet", "long_term_debt_current", year),
                    ("balance_sheet", "long_term_debt_noncurrent", year),
                ]), lineage_applicable=True,
            ),
            _finding(
                "Reverse DCF makes the embedded requirement explicit",
                f"At the base discount and terminal assumptions, normalized FCF must compound approximately {reverse_growth:.1%} annually for five years to support the indicative price.",
                severity="high", evidence=evidence, kind="model inference",
                fact_ids=_fact_ids(sec, [
                    ("annual", "operating_cash_flow", year), ("annual", "capex", year),
                    ("annual", "operating_cash_flow", prior_year), ("annual", "capex", prior_year),
                    ("balance_sheet", "shares_outstanding", year),
                    ("balance_sheet", "cash", year), ("balance_sheet", "short_term_investments", year),
                    ("balance_sheet", "long_term_debt_current", year),
                    ("balance_sheet", "long_term_debt_noncurrent", year),
                ]), lineage_applicable=True,
            ),
            _finding(
                "Scenario dispersion is the decision, not a single point estimate",
                f"Illustrative values span ${scenarios[0]['fair_value_per_share']:,.2f} to ${scenarios[-1]['fair_value_per_share']:,.2f} per share because AI capex, FCF growth, and discount rates dominate the result.",
                severity="neutral", evidence=evidence, kind="model inference",
                fact_ids=_fact_ids(sec, [
                    ("annual", "operating_cash_flow", year), ("annual", "capex", year),
                    ("annual", "operating_cash_flow", prior_year), ("annual", "capex", prior_year),
                    ("balance_sheet", "shares_outstanding", year),
                    ("balance_sheet", "cash", year), ("balance_sheet", "short_term_investments", year),
                    ("balance_sheet", "long_term_debt_current", year),
                    ("balance_sheet", "long_term_debt_noncurrent", year),
                ]), lineage_applicable=True,
            ),
        ],
        evidence=evidence,
        caveats=[
            "DCF outputs are illustrative and highly sensitive; they are not forecasts or investment recommendations.",
            "Yahoo data is indicative; production decisions require a licensed price source.",
            "Normalized FCF averages two years and does not separate maintenance from growth capex.",
        ],
        questions=[
            "What five-year FCF growth range is supportable under realistic AI pricing, utilization, and depreciation assumptions?",
            "How should OpenAI/Anthropic investment gains and losses be excluded from normalized earnings?",
            "What risk premium is appropriate for a hyperscaler during an unusually capital-intensive platform transition?",
        ],
        tables={"dcf_scenarios": scenarios},
        assumptions=[
            "Five explicit annual periods with constant FCF growth by scenario.",
            "Terminal value uses the Gordon growth model; net cash is added to equity value.",
            "Starting FCF is the average of the latest two fiscal years' CFO less capex.",
        ],
    )


def _returns(observations: Sequence[Mapping[str, Any]]) -> Dict[str, float]:
    output = {}
    previous = None
    for item in observations:
        close = finite(item.get("close"))
        if previous and close:
            output[str(item["date"])] = close / previous - 1
        if close:
            previous = close
    return output


def market_risk(sec: dict, market: dict, macro: dict) -> dict:
    observations = market["microsoft"]["observations"]
    prices = [finite(item["close"]) for item in observations]
    returns = _returns(observations)
    benchmark_returns = _returns(market["benchmark"]["observations"])
    shared = sorted(set(returns) & set(benchmark_returns))
    stock_values = [returns[key] for key in shared]
    benchmark_values = [benchmark_returns[key] for key in shared]
    covariance = statistics.covariance(stock_values, benchmark_values) if len(shared) > 2 else 0
    variance = statistics.variance(benchmark_values) if len(shared) > 2 else 0
    beta = covariance / variance if variance else None
    correlation = statistics.correlation(stock_values, benchmark_values) if len(shared) > 2 else None
    volatility = statistics.stdev(list(returns.values())) * math.sqrt(252) if len(returns) > 2 else None
    peak = prices[0]
    maximum_drawdown = 0.0
    for price in prices:
        peak = max(peak, price)
        maximum_drawdown = min(maximum_drawdown, price / peak - 1)
    current = prices[-1]
    ma50 = statistics.mean(prices[-50:])
    ma200 = statistics.mean(prices[-200:]) if len(prices) >= 200 else statistics.mean(prices)
    one_year_return = current / prices[0] - 1
    thirty_day_return = current / prices[-22] - 1 if len(prices) >= 22 else None
    series = macro["series"]
    evidence = ["S-MARKET-MSFT", "S-MARKET-SP500", "S-FRED-DGS10", "S-FRED-FEDFUNDS", "S-FRED-CPIAUCSL", "S-FRED-GDPC1", "S-FRED-BAA10YM"]
    return _component(
        "market_risk",
        "Market, rate & macro risk",
        "Momentum constructive; discount-rate sensitivity remains material",
        f"MSFT returned {one_year_return:.1%} over the retrieved one-year window with {volatility:.1%} annualized realized volatility.",
        0.86,
        {
            "price": current,
            "as_of": market["microsoft"]["as_of"],
            "one_year_return": one_year_return,
            "thirty_trading_day_return": thirty_day_return,
            "annualized_volatility": volatility,
            "maximum_drawdown": maximum_drawdown,
            "beta_to_sp500": beta,
            "correlation_to_sp500": correlation,
            "fifty_day_moving_average": ma50,
            "two_hundred_day_moving_average": ma200,
            "distance_to_fifty_day_average": current / ma50 - 1,
            "ten_year_treasury": finite(series["ten_year_treasury"]["latest"]) / 100,
            "ten_year_real_yield": finite(series["ten_year_real_yield"]["latest"]) / 100,
            "fed_funds": finite(series["fed_funds"]["latest"]) / 100,
            "cpi_yoy": series["cpi"].get("year_over_year"),
            "real_gdp_yoy": series["real_gdp"].get("year_over_year"),
            "baa_spread": finite(series["baa_spread"]["latest"]) / 100,
        },
        [
            _finding(
                "Equity duration is exposed to real and nominal yields",
                f"The 10-year nominal Treasury yield was {finite(series['ten_year_treasury']['latest']) / 100:.2%} and the real yield was {finite(series['ten_year_real_yield']['latest']) / 100:.2%}; higher yields reduce the present value of long-dated AI cash flows.",
                severity="high", evidence=evidence,
            ),
            _finding(
                "Observed beta does not capture platform-transition risk",
                f"One-year daily beta was {beta:.2f} with {correlation:.2f} correlation to the S&P 500; capacity, regulation, or AI-pricing shocks may not resemble the historical window.",
                severity="watch", evidence=evidence,
            ),
            _finding(
                "Price trend is currently above key moving averages",
                f"The latest adjusted close was {current / ma50 - 1:.1%} from the 50-day average and {current / ma200 - 1:.1%} from the 200-day average.",
                severity="neutral", evidence=evidence,
            ),
        ],
        evidence=evidence,
        caveats=["Technical statistics use approximately one year of indicative adjusted prices and are not predictive."],
        questions=[
            "How should the equity risk premium change if AI capex remains elevated for longer than consensus expects?",
            "What revenue and margin sensitivity should be applied under slower GDP growth or stronger U.S. dollar scenarios?",
        ],
        tables={"price_history": observations},
    )


def peer_benchmarking(sec: dict, peers: dict) -> dict:
    year, revenue = _latest(_annual(sec, "revenue"))
    operating_income = _value(_annual(sec, "operating_income"), year)
    cfo = _value(_annual(sec, "operating_cash_flow"), year)
    capex = _value(_annual(sec, "capex"), year)
    prior_year = sorted(_annual(sec, "revenue"))[-2]
    microsoft = {
        "ticker": "MSFT", "company": "Microsoft", "fiscal_year_end": year,
        "revenue_usd_b": revenue,
        "revenue_growth": _growth(revenue, _value(_annual(sec, "revenue"), prior_year)),
        "operating_margin": _ratio(operating_income, revenue),
        "net_margin": _ratio(_value(_annual(sec, "net_income"), year), revenue),
        "free_cash_flow_margin": _ratio(cfo - capex, revenue),
        "capex_intensity": _ratio(capex, revenue),
    }
    rows = [microsoft] + [item for item in peers.get("peers", []) if "error" not in item]
    peer_rows = rows[1:]
    medians = {}
    for metric in ("revenue_growth", "operating_margin", "net_margin", "free_cash_flow_margin", "capex_intensity"):
        values = [finite(item.get(metric), math.nan) for item in peer_rows]
        values = [item for item in values if math.isfinite(item)]
        medians[metric] = statistics.median(values) if values else None
    margin_rank = 1 + sum(1 for item in peer_rows if finite(item.get("operating_margin"), -1) > microsoft["operating_margin"])
    fcf_rank = 1 + sum(1 for item in peer_rows if finite(item.get("free_cash_flow_margin"), -1) > microsoft["free_cash_flow_margin"])
    evidence = ["S-SEC-COMPANYFACTS"] + list(peers.get("source_ids", []))
    return _component(
        "peer_benchmarking",
        "Peer operating benchmark",
        "Top-tier margins; AI capex lowers current FCF conversion",
        f"Microsoft ranks {margin_rank} of {len(rows)} on operating margin and {fcf_rank} of {len(rows)} on conventional FCF margin in the selected cross-platform peer set.",
        0.88,
        {
            "microsoft_revenue_growth": microsoft["revenue_growth"],
            "microsoft_operating_margin": microsoft["operating_margin"],
            "peer_median_operating_margin": medians["operating_margin"],
            "microsoft_free_cash_flow_margin": microsoft["free_cash_flow_margin"],
            "peer_median_free_cash_flow_margin": medians["free_cash_flow_margin"],
            "microsoft_capex_intensity": microsoft["capex_intensity"],
            "peer_median_capex_intensity": medians["capex_intensity"],
            "operating_margin_rank": f"{margin_rank}/{len(rows)}",
            "free_cash_flow_margin_rank": f"{fcf_rank}/{len(rows)}",
        },
        [
            _finding(
                "Operating profitability compares favorably",
                f"Microsoft's {microsoft['operating_margin']:.1%} operating margin compares with a {medians['operating_margin']:.1%} peer median.",
                severity="positive", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("annual", "revenue", year), ("annual", "operating_income", year),
                ]), lineage_applicable=True,
            ),
            _finding(
                "Current capex intensity changes the peer narrative",
                f"Microsoft invested {microsoft['capex_intensity']:.1%} of revenue in capex versus a {medians['capex_intensity']:.1%} median, depressing current FCF margin relative to accounting margins.",
                severity="watch", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("annual", "revenue", year), ("annual", "capex", year),
                    ("annual", "operating_cash_flow", year),
                ]), lineage_applicable=True,
            ),
            _finding(
                "This is an operating benchmark, not a valuation comp set",
                "Alphabet, Amazon, Meta, Apple, and Oracle have different fiscal calendars, segment mixes, capital structures, and accounting judgments.",
                severity="neutral", evidence=evidence, kind="methodology",
            ),
        ],
        evidence=evidence,
        caveats=[peers.get("methodology_note", "Peer comparability is limited.")],
        questions=[
            "Which peers best match Azure infrastructure economics versus Microsoft 365 software economics?",
            "Should capex be normalized by deployed capacity, revenue, or expected workload demand when comparing hyperscalers?",
        ],
        tables={"peer_benchmark": rows},
    )


def accounting_quality(sec: dict, investor_relations: dict) -> dict:
    year, revenue = _latest(_annual(sec, "revenue"))
    previous = str(int(year) - 1)
    net_income = _value(_annual(sec, "net_income"), year)
    cfo = _value(_annual(sec, "operating_cash_flow"), year)
    capex = _value(_annual(sec, "capex"), year)
    assets = _value(_balance(sec, "total_assets"), year)
    prior_assets = _value(_balance(sec, "total_assets"), previous)
    receivables = _value(_balance(sec, "accounts_receivable"), year)
    prior_receivables = _value(_balance(sec, "accounts_receivable"), previous)
    deferred = _value(_balance(sec, "deferred_revenue_current"), year) + _value(_balance(sec, "deferred_revenue_noncurrent"), year)
    prior_deferred = _value(_balance(sec, "deferred_revenue_current"), previous) + _value(_balance(sec, "deferred_revenue_noncurrent"), previous)
    goodwill = _value(_balance(sec, "goodwill"), year)
    sbc = _value(_annual(sec, "stock_compensation"), year)
    other_income = _value(_annual(sec, "other_income_expense"), year)
    prior_other_income = _value(_annual(sec, "other_income_expense"), previous)
    openai_impact = finite(investor_relations.get("metrics", {}).get("openai_net_income_impact_usd_b"))
    evidence = ["S-SEC-COMPANYFACTS", "S-SEC-10K", "S-MSFT-EARNINGS"]
    return _component(
        "accounting_quality",
        "Accounting quality & earnings normalization",
        "Strong cash conversion with material non-operating noise",
        f"CFO was {_ratio(cfo, net_income):.2f}x net income, but non-operating income and investment marks widened the gap between operating and GAAP net-income growth.",
        0.92,
        {
            "cash_conversion_cfo_to_net_income": _ratio(cfo, net_income),
            "accrual_ratio": _ratio(net_income - cfo, (assets + prior_assets) / 2),
            "days_sales_outstanding": _ratio(receivables, revenue, 0) * 365,
            "receivables_growth": _growth(receivables, prior_receivables),
            "deferred_revenue_usd_b": deferred,
            "deferred_revenue_growth": _growth(deferred, prior_deferred),
            "goodwill_to_assets": _ratio(goodwill, assets),
            "stock_compensation_pct_revenue": _ratio(sbc, revenue),
            "other_income_expense_usd_b": other_income,
            "other_income_swing_usd_b": other_income - prior_other_income,
            "openai_net_income_impact_usd_b": openai_impact,
            "owner_earnings_lens_usd_b": cfo - capex - sbc,
        },
        [
            _finding(
                "Core cash conversion is strong",
                f"CFO exceeded net income by ${cfo - net_income:,.1f}B and the accrual ratio was {_ratio(net_income - cfo, (assets + prior_assets) / 2):.1%} of average assets.",
                severity="positive", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("annual", "operating_cash_flow", year), ("annual", "net_income", year),
                    ("balance_sheet", "total_assets", year),
                    ("balance_sheet", "total_assets", previous),
                ]), lineage_applicable=True,
            ),
            _finding(
                "Investment marks require normalized earnings",
                f"Other income/expense swung by ${other_income - prior_other_income:,.1f}B year over year; Microsoft separately disclosed a ${openai_impact:,.1f}B FY2026 net-income impact from OpenAI investments.",
                severity="high", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("annual", "other_income_expense", year),
                    ("annual", "other_income_expense", previous),
                ]), lineage_applicable=True,
            ),
            _finding(
                "Receivables grew faster than revenue",
                f"Accounts receivable grew {_growth(receivables, prior_receivables):.1%} versus revenue growth of {_growth(revenue, _value(_annual(sec, 'revenue'), previous)):.1%}; DSO was approximately {_ratio(receivables, revenue, 0) * 365:.0f} days at year-end.",
                severity="watch", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("balance_sheet", "accounts_receivable", year),
                    ("balance_sheet", "accounts_receivable", previous),
                    ("annual", "revenue", year), ("annual", "revenue", previous),
                ]), lineage_applicable=True,
            ),
        ],
        evidence=evidence,
        caveats=["Year-end DSO is affected by seasonality and fourth-quarter contract timing."],
        questions=[
            "What portion of receivables growth reflects Q4 seasonality, contract structure, or collection-cycle change?",
            "Which investment gains and losses should be excluded from recurring earnings and valuation multiples?",
            "How sensitive are reported margins to useful-life estimates for rapidly evolving AI infrastructure?",
        ],
    )


def audit_assurance(sec: dict) -> dict:
    audit = sec.get("qualitative", {}).get("audit", {})
    evidence = ["S-SEC-10K"]
    cams = audit.get("critical_audit_matters", [])
    findings = [
        _finding(
            "External audit opinion is unmodified",
            f"{audit.get('auditor', 'The auditor')} reported that the financial statements present fairly in all material respects.",
            severity="positive", evidence=evidence, kind="reported",
        ),
        _finding(
            "Internal control opinion is unmodified",
            "The filing contains an unqualified auditor conclusion on internal control over financial reporting; reasonable assurance is not absolute assurance.",
            severity="positive", evidence=evidence, kind="reported",
        ),
        _finding(
            "Critical audit matters identify the highest-judgment areas",
            f"Detected CAM topics: {', '.join(cams) if cams else 'not reliably extracted'}. These require targeted contract, tax, model, and control testing rather than generic review.",
            severity="watch", evidence=evidence, kind="reported",
        ),
    ]
    return _component(
        "audit_assurance",
        "External audit & controls assurance",
        "Clean opinions; judgment concentration remains",
        "The latest 10-K supports clean financial-statement and ICFR conclusions while identifying revenue recognition and tax as high-judgment areas.",
        0.94,
        {
            "auditor": audit.get("auditor"),
            "financial_statement_opinion_unmodified": audit.get("financial_statement_opinion_unmodified"),
            "icfr_opinion_unmodified": audit.get("icfr_opinion_unmodified"),
            "management_controls_effective": audit.get("management_controls_effective"),
            "critical_audit_matter_count": len(cams),
            "critical_audit_matters": cams,
        },
        findings,
        evidence=evidence,
        caveats=[
            "An unmodified audit opinion is not a guarantee against fraud, future control failure, or forecast error.",
            "This analysis does not reproduce or replace the auditor's report or perform audit procedures.",
        ],
        questions=[
            "How are standalone selling prices and performance obligations controlled for bundled cloud and AI contracts?",
            "What audit procedures address consumption estimates, variable consideration, and free or optional AI services?",
            "How are model governance and change controls evolving for AI-assisted financial processes?",
        ],
    )


def tax_exposure(sec: dict) -> dict:
    year, pretax = _latest(_annual(sec, "pretax_income"))
    tax = _value(_annual(sec, "income_tax"), year)
    cash_tax = _value(_annual(sec, "cash_taxes_paid"), year)
    unrecognized = _value(_balance(sec, "unrecognized_tax_benefits"), year)
    tax_info = sec.get("qualitative", {}).get("tax", {})
    nopa = tax_info.get("irs_nopa_claim_usd_b")
    evidence = ["S-SEC-COMPANYFACTS", "S-SEC-10K"]
    return _component(
        "tax_exposure",
        "Tax rate, controversy & policy exposure",
        "Manageable current rate; concentrated controversy tail",
        f"The FY{year} effective tax rate was {_ratio(tax, pretax):.1%}; uncertain tax positions and the IRS transfer-pricing dispute remain material diligence items.",
        0.93,
        {
            "pretax_income_usd_b": pretax,
            "income_tax_expense_usd_b": tax,
            "effective_tax_rate": _ratio(tax, pretax),
            "cash_taxes_paid_usd_b": cash_tax,
            "cash_tax_rate": _ratio(cash_tax, pretax),
            "unrecognized_tax_benefits_usd_b": unrecognized,
            "irs_nopa_claim_usd_b": nopa,
            "pillar_two_discussed": tax_info.get("pillar_two_discussed"),
            "transfer_pricing_discussed": tax_info.get("transfer_pricing_discussed"),
        },
        [
            _finding(
                "Reported tax rate remains below the U.S. statutory rate",
                f"Tax expense was ${tax:,.1f}B on ${pretax:,.1f}B of pretax income, an effective rate of {_ratio(tax, pretax):.1%}.",
                severity="neutral", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("annual", "income_tax", year), ("annual", "pretax_income", year),
                ]), lineage_applicable=True,
            ),
            _finding(
                "Transfer-pricing controversy is a material tail scenario",
                f"The filing discusses IRS proposed adjustments seeking approximately ${finite(nopa):,.1f}B plus penalties and interest; Microsoft disputes the adjustments.",
                severity="high", evidence=evidence, kind="reported",
            ),
            _finding(
                "Tax reserves require judgment",
                f"Gross unrecognized tax benefits were approximately ${unrecognized:,.1f}B, so settlement timing and measurement can affect future effective rates and cash flows.",
                severity="watch", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("balance_sheet", "unrecognized_tax_benefits", year),
                ]), lineage_applicable=True,
            ),
        ],
        evidence=evidence,
        caveats=["The proposed IRS amount is not an expected loss estimate; outcomes and timing are uncertain."],
        questions=[
            "What probability-weighted cash and rate scenarios should be assigned to the transfer-pricing dispute?",
            "How will OECD Pillar Two, U.S. law changes, and geographic profit mix affect the normalized tax rate?",
            "How much of deferred-tax asset growth depends on future taxable income and R&D capitalization rules?",
        ],
    )


def ai_cloud_strategy(sec: dict, investor_relations: dict) -> dict:
    metrics = investor_relations.get("metrics", {})
    year, revenue = _latest(_annual(sec, "revenue"))
    prior = str(int(year) - 1)
    capex = _value(_annual(sec, "capex"), year)
    ppe = _value(_balance(sec, "property_equipment"), year)
    prior_ppe = _value(_balance(sec, "property_equipment"), prior)
    rpo = _value(_balance(sec, "remaining_performance_obligation"), year)
    cloud_margin = sec.get("qualitative", {}).get("operations", {}).get("microsoft_cloud_gross_margin_pct")
    evidence = ["S-MSFT-EARNINGS", "S-SEC-10K", "S-SEC-COMPANYFACTS"]
    return _component(
        "ai_cloud_strategy",
        "Cloud, Copilot & AI infrastructure economics",
        "Demand proof is strong; return-on-capital proof is still forming",
        f"Azure exceeded ${finite(metrics.get('azure_revenue_usd_b')):,.0f}B of fiscal revenue and Microsoft 365 Copilot passed {finite(metrics.get('copilot_paid_seats_m')):,.0f}M paid seats, while capex reached ${capex:,.1f}B.",
        0.88,
        {
            "azure_fiscal_revenue_usd_b": metrics.get("azure_revenue_usd_b"),
            "azure_growth_q4": metrics.get("azure_growth"),
            "copilot_paid_seats_m": metrics.get("copilot_paid_seats_m"),
            "quarterly_microsoft_cloud_revenue_usd_b": metrics.get("quarterly_cloud_revenue_usd_b"),
            "quarterly_microsoft_cloud_growth": metrics.get("quarterly_cloud_growth"),
            "commercial_rpo_usd_b": metrics.get("commercial_rpo_usd_b"),
            "commercial_rpo_growth": metrics.get("commercial_rpo_growth"),
            "total_rpo_usd_b": rpo,
            "capex_usd_b": capex,
            "capex_pct_revenue": _ratio(capex, revenue),
            "property_equipment_usd_b": ppe,
            "property_equipment_growth": _growth(ppe, prior_ppe),
            "microsoft_cloud_gross_margin": cloud_margin,
        },
        [
            _finding(
                "Commercial demand indicators are unusually strong",
                f"Q4 Azure growth was {finite(metrics.get('azure_growth')):.1%}; commercial RPO reached ${finite(metrics.get('commercial_rpo_usd_b')):,.0f}B and grew {finite(metrics.get('commercial_rpo_growth')):.1%}.",
                severity="positive", evidence=evidence,
            ),
            _finding(
                "Paid Copilot adoption is now measurable",
                f"Microsoft disclosed more than {finite(metrics.get('copilot_paid_seats_m')):,.0f}M Microsoft 365 Copilot paid seats, improving evidence beyond product-launch narratives.",
                severity="positive", evidence=evidence, kind="reported",
            ),
            _finding(
                "Unit economics remain obscured by shared infrastructure",
                f"Capex equaled {_ratio(capex, revenue):.1%} of revenue and PP&E grew {_growth(ppe, prior_ppe):.1%}; segment disclosure does not isolate AI inference revenue, gross margin, utilization, or return on deployed capital.",
                severity="high", evidence=evidence,
                fact_ids=_fact_ids(sec, [
                    ("annual", "capex", year), ("annual", "revenue", year),
                    ("balance_sheet", "property_equipment", year),
                    ("balance_sheet", "property_equipment", prior),
                ]), lineage_applicable=True,
            ),
        ],
        evidence=evidence,
        caveats=[
            "Azure revenue and Copilot seats are management-reported operating metrics and do not disclose standalone profitability.",
            "RPO includes timing and mix effects and should not be treated as immediate AI revenue.",
        ],
        questions=[
            "What are AI infrastructure utilization, power, depreciation, and gross-margin curves by workload cohort?",
            "What portion of Copilot seats are incremental versus bundled, discounted, trial, or substitution revenue?",
            "What customer concentration, model-provider, and GPU-supplier dependencies sit behind RPO growth?",
            "How quickly can software and custom-silicon efficiency offset token-price compression?",
        ],
    )


def regulatory_operational_risk(sec: dict) -> dict:
    detected = {item["topic"]: item for item in sec.get("qualitative", {}).get("risk_topics", []) if item.get("present")}
    evidence = ["S-SEC-10K"]
    risk_definitions = [
        ("AI return-on-capital", "High", "High", "AI investment returns and adoption", "Demand can remain strong while pricing, utilization, depreciation, or capacity timing depress returns."),
        ("Cybersecurity and service resilience", "High", "Medium", "Cybersecurity and service resilience", "Microsoft operates mission-critical identity, cloud, productivity, and security infrastructure at global scale."),
        ("AI/privacy/competition regulation", "High", "Medium", "Regulation and antitrust", "Rules can constrain bundling, data use, model deployment, sovereign-cloud design, or acquisition strategy."),
        ("OpenAI and strategic-partner economics", "High", "Medium", "OpenAI and strategic-partner economics", "Reciprocal economics, capacity rights, model access, and investment accounting affect both operations and reported earnings."),
        ("Tax controversy", "High", "Medium", "Tax controversy and transfer pricing", "The IRS transfer-pricing dispute creates a large but uncertain cash and earnings tail."),
        ("GPU, power, and datacenter supply", "High", "Medium", "Datacenter capacity, energy, and GPUs", "Capacity depends on scarce chips, power, networking, land, permits, construction, and supplier execution."),
        ("Consumer/gaming portfolio execution", "Medium", "Medium", "Competition and open-source substitution", "More Personal Computing is not sharing equally in cloud-led growth and faces distinct platform competition."),
        ("Foreign exchange and geopolitics", "Medium", "Medium", "Foreign exchange and geopolitics", "Global revenue and infrastructure expose results to currency, trade controls, sovereignty, and geopolitical change."),
    ]
    score_map = {"Low": 1, "Medium": 2, "High": 3}
    rows = []
    for risk, impact, likelihood, topic, rationale in risk_definitions:
        rows.append({
            "risk": risk,
            "impact": impact,
            "likelihood": likelihood,
            "score": score_map[impact] * score_map[likelihood],
            "filing_topic_detected": topic in detected,
            "rationale": rationale,
            "kind": "analyst inference",
            "evidence_refs": evidence,
        })
    rows.sort(key=lambda item: item["score"], reverse=True)
    return _component(
        "regulatory_operational_risk",
        "Regulatory, cyber & operational risk register",
        "No single red flag; several enterprise-scale tail risks",
        "The filing supports a concentrated risk register around AI capital returns, cyber resilience, regulation, strategic partners, tax, and infrastructure supply.",
        0.87,
        {
            "risk_topics_detected": len(detected),
            "high_impact_risks": sum(1 for item in rows if item["impact"] == "High"),
            "top_risk_score": rows[0]["score"],
            "datacenter_constraints_disclosed": sec.get("qualitative", {}).get("operations", {}).get("datacenter_resource_constraints_discussed"),
            "ai_margin_pressure_disclosed": sec.get("qualitative", {}).get("operations", {}).get("ai_infrastructure_margin_pressure_discussed"),
        },
        [
            _finding(
                "AI return risk is broader than demand risk",
                "The key failure mode is not merely weak demand; it is strong demand monetized below the fully loaded cost of capacity, energy, depreciation, and model economics.",
                severity="high", evidence=evidence, kind="analyst inference",
            ),
            _finding(
                "Cyber risk is systemic to the franchise",
                "Identity, cloud, security, and productivity products make operational resilience and secure-by-design execution both a customer promise and a financial dependency.",
                severity="high", evidence=evidence, kind="analyst inference",
            ),
            _finding(
                "Regulatory risks interact rather than stand alone",
                "Antitrust, privacy, AI safety, content, sovereignty, export-control, and government-contract requirements can alter product design, cost, and distribution simultaneously.",
                severity="watch", evidence=evidence, kind="analyst inference",
            ),
        ],
        evidence=evidence,
        caveats=["Likelihood and impact labels are analyst judgments, not management guidance or probabilities."],
        questions=[
            "What board-level risk appetite and leading indicators govern AI capacity commitments?",
            "How are Secure Future Initiative outcomes independently tested and linked to executive accountability?",
            "What are quantified revenue and cost sensitivities for major AI, privacy, competition, and sovereignty rules?",
            "What contingency exists for model-provider, GPU, power, or region-specific service disruption?",
        ],
        tables={"risk_register": rows},
    )


def assess_evidence_quality(
    sec: Mapping[str, Any],
    source_manifest: Mapping[str, Mapping[str, Any]],
    analyses: Sequence[Mapping[str, Any]],
    review: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Measure inspectable evidence properties; never describe them as probability."""
    now = datetime.now(timezone.utc)
    ages = []
    for source in source_manifest.values():
        try:
            retrieved = datetime.fromisoformat(str(source.get("retrieved_at")))
            if retrieved.tzinfo is None:
                retrieved = retrieved.replace(tzinfo=timezone.utc)
            ages.append(max(0.0, (now - retrieved).total_seconds() / 86_400))
        except (TypeError, ValueError):
            continue
    authoritative = sum(bool(source.get("authoritative")) for source in source_manifest.values())
    source_count = len(source_manifest)
    findings = [finding for item in analyses for finding in item["output"].get("findings", [])]
    known_sources = set(source_manifest)
    selected_facts = sec.get("fact_index", {})
    known_fact_ids = set(selected_facts)
    source_linked = [
        finding for finding in findings
        if finding.get("evidence_refs")
        and set(finding.get("evidence_refs", [])) <= known_sources
    ]
    fact_applicable = [
        finding for finding in findings if finding.get("fact_lineage_applicable")
    ]
    fact_linked = [
        finding for finding in fact_applicable
        if finding.get("fact_ids")
        and set(finding.get("fact_ids", [])) <= known_fact_ids
    ]
    reconciliations = sec.get("reconciliation", [])
    reconciliation_passes = sum(bool(item.get("within_tolerance")) for item in reconciliations)
    source_authority = authoritative / source_count if source_count else 0.0
    freshness = sum(1.0 if age <= 7 else 0.8 if age <= 30 else 0.5 if age <= 180 else 0.2 for age in ages) / len(ages) if ages else 0.0
    source_coverage = len(source_linked) / len(findings) if findings else 0.0
    valid_fact_records = sum(
        bool(
            record.get("fact_id") == fact_id
            and record.get("metric")
            and record.get("period")
            and record.get("source_id") in known_sources
        )
        for fact_id, record in selected_facts.items()
    )
    fact_inventory_coverage = valid_fact_records / len(selected_facts) if selected_facts else 0.0
    fact_finding_coverage = len(fact_linked) / len(fact_applicable) if fact_applicable else 1.0
    reconciliation_score = reconciliation_passes / len(reconciliations) if reconciliations else 0.0
    calculation_integrity = 1.0 if not any(not item.get("within_tolerance") for item in reconciliations) else 0.0
    review_status = str((review or {}).get("status", "pending"))
    review_authorized = bool((review or {}).get("release_authorized"))
    review_score = 1.0 if review_authorized else 0.0
    dimensions = [
        {"dimension": "Source authority", "score": round(source_authority, 4), "status": "measured", "basis": f"{authoritative}/{source_count} used objects marked authoritative"},
        {"dimension": "Freshness", "score": round(freshness, 4), "status": "measured", "basis": f"Oldest used source age {max(ages, default=0):.1f} days"},
        {"dimension": "Finding-to-source coverage", "score": round(source_coverage, 4), "status": "measured", "basis": f"{len(source_linked)}/{len(findings)} findings declare source IDs"},
        {"dimension": "Selected-fact lineage inventory", "score": round(fact_inventory_coverage, 4), "status": "complete" if fact_inventory_coverage == 1 else "partial", "basis": f"{valid_fact_records}/{len(selected_facts)} selected/calculated facts retain matching IDs, periods, metrics, and resolvable sources"},
        {"dimension": "Applicable finding-to-fact coverage", "score": round(fact_finding_coverage, 4), "status": "complete" if fact_finding_coverage == 1 else "partial", "basis": f"{len(fact_linked)}/{len(fact_applicable)} findings designated as structured-fact claims expose only resolvable selected fact IDs"},
        {"dimension": "Quarter/annual reconciliation", "score": round(reconciliation_score, 4), "status": "measured", "basis": f"{reconciliation_passes}/{len(reconciliations)} checks within tolerance"},
        {"dimension": "Calculation integrity", "score": calculation_integrity, "status": "deterministic", "basis": "TTM and quarterly derivations retain formulas and input fact IDs"},
        {"dimension": "Professional review", "score": review_score, "status": review_status, "basis": "Two-person digest-bound review; release remains unauthorized until quorum, and identity must be supplied by the deployment IdP"},
    ]
    technical_dimensions = [item["score"] for item in dimensions if item["dimension"] != "Professional review"]
    return {
        "label": "evidence quality profile — not a probability",
        "technical_quality_index": round(statistics.mean(technical_dimensions), 4),
        "release_ready": review_authorized and all(item["score"] >= 0.8 for item in dimensions),
        "dimensions": dimensions,
        "review_status": review_status,
        "review_id": (review or {}).get("id"),
        "review_content_binding_valid": bool((review or {}).get("content_binding_valid")),
        "review_authorization_valid": bool((review or {}).get("approval_valid_for_digest")),
        "limitations": [
            "Dimension scores are deterministic completeness/quality indicators, not calibrated probabilities.",
            "A strong source can still contain estimates, errors, omissions, later recasts, or ambiguous disclosure.",
            "Direct fact IDs apply to normalized structured-fact claims; qualitative and external-provider claims resolve at source-object level.",
            "Professional approval establishes accountable review of exact content; it does not guarantee correctness.",
        ],
    }


def synthesize(ticker: str, analyses: list, source_manifest: dict, filing: dict) -> dict:
    outputs = [item["output"] for item in analyses]
    by_name = {item["component"]: item for item in outputs}
    evidence_chain = [item["evidence_id"] for item in analyses]
    confidence = statistics.mean(item["confidence"] for item in outputs)
    performance = by_name["financial_performance"]["metrics"]
    quarterly = by_name["quarterly_change_intelligence"]["metrics"]
    filing_change = by_name["filing_change_detection"]["metrics"]
    cash = by_name["cash_flow_capital_intensity"]["metrics"]
    valuation = by_name["valuation_scenarios"]["metrics"]
    ai = by_name["ai_cloud_strategy"]["metrics"]
    risk_rows = by_name["regulatory_operational_risk"]["tables"]["risk_register"]
    questions = []
    for output in outputs:
        for question in output.get("diligence_questions", []):
            if question not in questions:
                questions.append(question)
    return {
        "component": "governed_research_synthesis",
        "ticker": ticker,
        "as_of": max(item.get("accessed_at", "") for item in source_manifest.values()),
        "fiscal_period": filing["fiscal_year"],
        "decision_posture": "QUALITY COMPOUNDER / PRICE-SENSITIVE",
        "recommendation": "CONTINUE DILIGENCE — DO NOT RELY ON A SINGLE VALUATION CASE",
        "summary": (
            f"Microsoft's {quarterly['latest_period']} revenue grew "
            f"{quarterly['quarterly_revenue_yoy']:.1%} year over year, extending FY growth of "
            f"{performance['revenue_growth']:.1%}. TTM revenue was "
            f"${quarterly['ttm_revenue_usd_b']:,.1f}B, while ${quarterly['ttm_capex_usd_b']:,.1f}B "
            f"of TTM capex reduced conventional TTM FCF to ${quarterly['ttm_free_cash_flow_usd_b']:,.1f}B. "
            f"All {quarterly['quarterly_reconciliation_checks']} annual-to-quarter checks reconciled, "
            f"and annual filing triage surfaced {filing_change['changed_signals']} review signals. "
            f"The indicative price requires approximately {valuation['reverse_dcf_five_year_fcf_growth']:.1%} "
            "five-year FCF compounding under the disclosed reverse-DCF assumptions."
        ),
        "confidence": round(confidence, 2),
        "confidence_label": "uncalibrated analytical confidence — not a probability",
        "thesis": {
            "bull": [
                f"Latest-quarter revenue growth of {quarterly['quarterly_revenue_yoy']:.1%} and TTM revenue of ${quarterly['ttm_revenue_usd_b']:,.1f}B show that demand remains visible in filed results, not only management KPIs.",
                f"Azure growth of {finite(ai.get('azure_growth_q4')):.1%} and commercial RPO growth of {finite(ai.get('commercial_rpo_growth')):.1%} provide unusually strong forward demand evidence.",
                f"Paid Microsoft 365 Copilot seats exceeded {finite(ai.get('copilot_paid_seats_m')):,.0f}M, creating a measurable application-layer monetization path.",
                "Operating leverage and high-margin productivity software can finance the infrastructure transition without balance-sheet stress.",
            ],
            "base": [
                f"Cloud and AI sustain above-market growth, but TTM capex intensity of {quarterly['ttm_capex_intensity']:.1%} keeps FCF conversion below historical software norms.",
                "Valuation outcomes depend more on realized FCF and discount rates than on headline AI adoption metrics alone.",
                f"The {filing_change['changed_signals']} detected annual disclosure signals are triage inputs; the appropriate workflow is professional redline review and explicit trigger monitoring.",
            ],
            "bear": [
                "Capacity is built ahead of durable demand, token prices fall faster than unit costs, or useful lives shorten, reducing return on invested capital.",
                "Cyber, antitrust, privacy, sovereignty, tax, or model-partner events impose simultaneous revenue, cost, and reputation pressure.",
                f"TTM FCF of ${quarterly['ttm_free_cash_flow_usd_b']:,.1f}B fails to reaccelerate while capacity commitments and higher-for-longer rates pressure long-duration value.",
            ],
        },
        "decision_triggers": [
            {"signal": "Quarterly momentum", "green": "Year-over-year growth and operating margin remain durable across filed quarters", "red": "Revenue growth or margin deteriorates across two consecutive comparable quarters"},
            {"signal": "AI monetization", "green": "Azure/Copilot growth with stable or rising cloud margin", "red": "Growth decelerates while capex intensity remains elevated"},
            {"signal": "Cash conversion", "green": "FCF growth recovers above revenue growth", "red": "CFO-capex remains flat/down for multiple periods"},
            {"signal": "Capital efficiency", "green": "PP&E utilization and depreciation productivity improve", "red": "Impairments, life reductions, or capacity write-downs emerge"},
            {"signal": "Filing change", "green": "Professional redline closes detected deltas without thesis impact", "red": "New or expanded disclosure changes a risk, estimate, control, or commitment conclusion"},
            {"signal": "Data integrity", "green": "Quarter sums reconcile to annual facts and all claim links resolve", "red": "A filing amendment, recast, taxonomy drift, or broken lineage creates an unexplained variance"},
            {"signal": "Valuation", "green": "Price embeds a supportable reverse-DCF requirement", "red": "Embedded FCF growth rises despite slower demand or higher yields"},
            {"signal": "Assurance", "green": "Clean ICFR with transparent AI contract accounting", "red": "Control deficiencies, revenue-recognition exceptions, or reserve surprises"},
        ],
        "top_risks": risk_rows[:6],
        "priority_reviews": [
            "quarterly_change_intelligence", "filing_change_detection",
            "cash_flow_capital_intensity", "valuation_scenarios", "ai_cloud_strategy",
            "accounting_quality", "tax_exposure", "regulatory_operational_risk",
        ],
        "diligence_questions": questions[:20],
        "evidence_chain": evidence_chain,
        "source_coverage": len(source_manifest),
        "reflection": "A deterministic second pass confirmed that every specialist declares resolvable source references, structured-fact claims expose selected fact IDs, annual-to-quarter checks reconcile, and every execution has signed evidence.",
        "disclaimer": "Controlled pilot only; not investment, audit, tax, legal, credit, or regulatory advice.",
    }


def audience_report(audience: str, synthesis: dict) -> dict:
    profiles = {
        "investment_committee": {
            "title": "Investment committee",
            "decision_use": "Underwrite growth, normalized cash generation, valuation, catalysts, and thesis-break conditions.",
            "focus": ["quarterly_change_intelligence", "financial_performance", "segment_economics", "cash_flow_capital_intensity", "valuation_scenarios", "filing_change_detection"],
        },
        "cfo_strategy": {
            "title": "CFO & corporate strategy",
            "decision_use": "Benchmark operating leverage, AI capital productivity, portfolio mix, and capital allocation.",
            "focus": ["quarterly_change_intelligence", "segment_economics", "cash_flow_capital_intensity", "capital_allocation", "ai_cloud_strategy"],
        },
        "audit_committee": {
            "title": "Audit committee / assurance",
            "decision_use": "Prioritize revenue recognition, tax, infrastructure estimates, controls, and non-operating normalization.",
            "focus": ["filing_change_detection", "accounting_quality", "audit_assurance", "tax_exposure", "regulatory_operational_risk"],
        },
        "credit_committee": {
            "title": "Credit committee / bank",
            "decision_use": "Assess liquidity, fixed commitments, lease-adjusted leverage, cash coverage, and stress capacity.",
            "focus": ["quarterly_change_intelligence", "balance_sheet_credit", "cash_flow_capital_intensity", "market_risk", "regulatory_operational_risk"],
        },
        "regulator_risk": {
            "title": "Regulator, compliance & enterprise risk",
            "decision_use": "Trace disclosures and analyst inferences across AI, cyber, privacy, competition, tax, and resilience.",
            "focus": ["filing_change_detection", "audit_assurance", "tax_exposure", "regulatory_operational_risk", "ai_cloud_strategy"],
        },
    }
    profile = profiles[audience]
    return {
        "audience": audience,
        **profile,
        "ticker": synthesis["ticker"],
        "decision_posture": synthesis["decision_posture"],
        "executive_summary": synthesis["summary"],
        "evidence_chain": synthesis["evidence_chain"],
        "review_status": "PENDING DIGEST-BOUND HUMAN REVIEW",
        "guardrail": "Accountable professional review remains mandatory before any external or consequential use.",
    }


def _must_output(run: Any, capability_name: str) -> dict:
    if not run.executed or run.result is None or not run.result.ok:
        reason = run.result.error if run.result is not None else run.gate.reason
        raise RuntimeError(f"{capability_name} failed: {reason}")
    return run.result.output


def _run_child(parent: Agent, name: str, adapter: Any, params: dict, evaluator: Any) -> Tuple[dict, str]:
    capability_name = f"analysis.{name}"
    child = parent.spawn(
        intent=f"Execute Microsoft {name.replace('_', ' ')}",
        grants=[capability(capability_name)],
        adapters=[adapter],
        node_id=f"specialist:{name}",
    )
    run = child.enact(capability_name, params, actor=f"specialist:{name}", evaluate=evaluator)
    return _must_output(run, capability_name), run.why_id


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_evidence_bundle(cache: SourceCache, outputs_dir: Path, manifest: dict) -> Path:
    bundle_path = outputs_dir / "microsoft_evidence_bundle.zip"
    selected = [
        "microsoft_finance_intelligence.html",
        "microsoft_finance_intelligence.md",
        "microsoft_finance_intelligence.json",
        "evidence_manifest.json",
        "signed_audit.jsonl",
        "review_audit.json",
        "METHODOLOGY.txt",
        "ARCHITECTURE.txt",
    ]
    members: Dict[str, Dict[str, Any]] = {}
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in selected:
            path = outputs_dir / name
            if not path.is_file():
                raise RuntimeError(f"required evidence-bundle artifact is missing: {path}")
            archive_name = f"outputs/{name}"
            archive.write(path, archive_name)
            members[archive_name] = {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        for source in manifest["sources"].values():
            path = cache.root / source["cache_path"]
            if not path.is_file():
                raise RuntimeError(f"required cached evidence is missing: {path}")
            archive_name = f"evidence/{source['cache_path']}"
            if archive_name in members:
                continue
            archive.write(path, archive_name)
            members[archive_name] = {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        bundle_manifest = {
            "schema_version": "autarch.evidence-bundle.v1",
            "release": manifest.get("release", {}),
            "member_count": len(members),
            "members": members,
            "note": "The member manifest intentionally excludes itself.",
        }
        archive.writestr(
            "bundle_manifest.json",
            json.dumps(bundle_manifest, indent=2, sort_keys=True).encode("utf-8"),
        )
    return bundle_path


def _validate_package(package: Mapping[str, Any]) -> Dict[str, Any]:
    """Fail closed unless the assembled v1.1 package satisfies its public contract."""
    sources = package.get("sources", {})
    sec = package.get("source_data", {}).get("sec", {})
    analyses = package.get("analyses", [])
    reports = package.get("reports", {})
    governance = package.get("governance", {})
    facts = sec.get("fact_index", {})
    source_ids = set(sources)
    fact_ids = set(facts)
    outputs = [item.get("output", {}) for item in analyses]
    findings = [finding for output in outputs for finding in output.get("findings", [])]
    expected_record_count = len(package.get("source_data", {})) + len(ANALYSIS_COMPONENTS) + 1 + len(AUDIENCES)
    recomputed_digest = canonical_sha256(_release_content(
        package["source_data"], sources, analyses, package["synthesis"], reports
    ))
    release_digest = governance.get("release", {}).get("content_digest")
    review = governance.get("review", {})

    checks = [
        ("component registry", tuple(output.get("component") for output in outputs) == ANALYSIS_COMPONENTS,
         f"expected {len(ANALYSIS_COMPONENTS)} ordered specialists"),
        ("audience registry", tuple(reports) == AUDIENCES,
         f"expected {len(AUDIENCES)} ordered professional views"),
        ("source hashes", bool(sources) and all(
            source.get("source_id") == source_id
            and len(str(source.get("sha256", ""))) == 64
            for source_id, source in sources.items()
        ), f"{len(sources)} source objects"),
        ("component source references", all(
            bool(output.get("evidence_refs"))
            and set(output.get("evidence_refs", [])) <= source_ids
            for output in outputs
        ), "all component source IDs must resolve"),
        ("finding source references", all(
            bool(finding.get("evidence_refs"))
            and set(finding.get("evidence_refs", [])) <= source_ids
            for finding in findings
        ), f"{len(findings)} findings checked"),
        ("fact inventory", bool(facts) and all(
            record.get("fact_id") == fact_id
            and record.get("source_id") in source_ids
            for fact_id, record in facts.items()
        ), f"{len(facts)} selected/calculated facts checked"),
        ("finding fact references", all(
            set(finding.get("fact_ids", [])) <= fact_ids for finding in findings
        ), "all direct fact links must resolve"),
        ("applicable fact coverage", all(
            bool(finding.get("fact_ids"))
            for finding in findings if finding.get("fact_lineage_applicable")
        ), "every structured-fact claim must expose a direct fact link"),
        ("quarter reconciliation", bool(sec.get("reconciliation")) and all(
            item.get("within_tolerance") for item in sec.get("reconciliation", [])
        ), f"{len(sec.get('reconciliation', []))} checks"),
        ("canonical release digest", recomputed_digest == release_digest,
         f"SHA-256 {str(release_digest)[:16]}…"),
        ("review content binding", review.get("artifact_digest") == release_digest
         and bool(review.get("content_binding_valid")), "review request matches exact release content"),
        ("authorization consistency", bool(review.get("release_authorized")) == (
            review.get("status") == "approved" and bool(review.get("approval_valid_for_digest"))
        ), "approval, digest, and release gate agree"),
        ("action-chain integrity", bool(governance.get("integrity", {}).get("verified"))
         and governance.get("integrity", {}).get("record_count") == expected_record_count,
         f"expected {expected_record_count} signed actions"),
        ("review-chain integrity", bool(governance.get("review_integrity", {}).get("verified")),
         "review event chain must verify"),
        ("static guarantees", all(item.get("holds") for item in governance.get("guarantees", [])),
         "publication and trading invariants must hold"),
    ]
    failed = [name for name, passed, _detail in checks if not passed]
    if failed:
        raise RuntimeError("package validation failed: " + ", ".join(failed))
    return {
        "schema_version": "autarch.microsoft-finance.validation.v1",
        "passed": True,
        "check_count": len(checks),
        "checks": [
            {"name": name, "passed": bool(passed), "detail": detail}
            for name, passed, detail in checks
        ],
    }


def run_product(mode: str, user_agent: str, ttl_hours: float) -> Dict[str, Any]:
    _reset_generated()
    cache = SourceCache(WORKSPACE / "cache", mode=mode, user_agent=user_agent, ttl_hours=ttl_hours)

    ingestion = from_callables(
        {
            "sec": lambda: fetch_microsoft_sec(cache),
            "investor_relations": lambda fiscal_year: fetch_microsoft_investor_relations(cache, fiscal_year),
            "market": lambda: fetch_market_data(cache),
            "macro": lambda: fetch_macro_data(cache),
            "peers": lambda: fetch_peer_data(cache),
        },
        namespace="ingest",
    )
    analysis_functions: Dict[str, Callable[..., dict]] = {
        "financial_performance": financial_performance,
        "quarterly_change_intelligence": quarterly_change_intelligence,
        "filing_change_detection": filing_change_detection,
        "segment_economics": segment_economics,
        "cash_flow_capital_intensity": cash_flow_capital_intensity,
        "balance_sheet_credit": balance_sheet_credit,
        "capital_allocation": capital_allocation,
        "valuation_scenarios": valuation_scenarios,
        "market_risk": market_risk,
        "peer_benchmarking": peer_benchmarking,
        "accounting_quality": accounting_quality,
        "audit_assurance": audit_assurance,
        "tax_exposure": tax_exposure,
        "ai_cloud_strategy": ai_cloud_strategy,
        "regulatory_operational_risk": regulatory_operational_risk,
    }
    if tuple(analysis_functions) != ANALYSIS_COMPONENTS:
        raise RuntimeError("analysis component registry does not match the v1.1 contract")
    analysis_adapter = from_callables(analysis_functions, namespace="analysis")
    synthesis_adapter = from_callables({"committee": synthesize}, namespace="synthesis")
    consumers = from_callables(
        {name: (lambda synthesis, audience=name: audience_report(audience, synthesis)) for name in AUDIENCES},
        namespace="consumer",
    )

    policies = [
        Policy(
            "no_external_publication",
            PolicyEffect.DENY.value,
            capability="external.publish",
            reason="research agents cannot publish or transact",
        ),
        Policy(
            "no_trade_execution",
            PolicyEffect.DENY.value,
            capability="trade.*",
            reason="analysis has no trading authority",
        ),
    ]
    parent = Agent(
        intent="Produce governed, source-backed Microsoft decision intelligence",
        grants=[capability("ingest.*"), capability("analysis.*"), capability("synthesis.*"), capability("consumer.*")],
        adapters=[ingestion, analysis_adapter, synthesis_adapter, consumers],
        policies=policies,
        workspace=WORKSPACE,
        budget={"calls": 36, "risk": 120, "cost": 2.0},
    )
    guarantee = parent.guarantee([
        Invariant.forbid("external.publish", "research cannot self-publish"),
        Invariant.forbid("trade.execute", "research cannot place trades"),
        Invariant.require_approval("external.publish", "external release remains accountable"),
    ])
    if not guarantee.all_hold:
        raise RuntimeError("static governance guarantees did not hold")

    print("\n[1/5] Governed live/cached source ingestion")
    data: Dict[str, Any] = {}
    ingest_evidence: Dict[str, str] = {}

    sec_child = parent.spawn("Ingest latest Microsoft SEC evidence", grants=[capability("ingest.sec")], adapters=[ingestion])
    sec_run = sec_child.enact("ingest.sec", {}, actor="ingestion:sec")
    data["sec"] = _must_output(sec_run, "ingest.sec")
    for source_id in data["sec"]["source_ids"]:
        ingest_evidence[source_id] = sec_run.why_id
    fiscal_year = int(data["sec"]["filing"]["reportDate"][:4])
    print(f"  SEC latest 10-K: FY{fiscal_year} accession={data['sec']['filing']['accessionNumber']}")

    source_jobs = (
        ("investor_relations", {"fiscal_year": fiscal_year}),
        ("market", {}),
        ("macro", {}),
        ("peers", {}),
    )
    for name, params in source_jobs:
        child = parent.spawn(f"Ingest {name}", grants=[capability(f"ingest.{name}")], adapters=[ingestion])
        run = child.enact(f"ingest.{name}", params, actor=f"ingestion:{name}")
        data[name] = _must_output(run, f"ingest.{name}")
        for source_id in data[name]["source_ids"]:
            ingest_evidence[source_id] = run.why_id
        print(f"  {name:22} sources={len(data[name]['source_ids'])} evidence={run.why_id}")

    source_manifest = cache.manifest()
    for source_id, metadata in source_manifest.items():
        metadata["signed_ingest_evidence"] = ingest_evidence.get(source_id)

    print("\n[2/5] Fifteen capability-attenuated specialist analyses")
    jobs: Dict[str, dict] = {
        "financial_performance": {"sec": data["sec"]},
        "quarterly_change_intelligence": {"sec": data["sec"]},
        "filing_change_detection": {"sec": data["sec"]},
        "segment_economics": {"sec": data["sec"], "investor_relations": data["investor_relations"]},
        "cash_flow_capital_intensity": {"sec": data["sec"]},
        "balance_sheet_credit": {"sec": data["sec"]},
        "capital_allocation": {"sec": data["sec"]},
        "valuation_scenarios": {"sec": data["sec"], "market": data["market"], "macro": data["macro"]},
        "market_risk": {"sec": data["sec"], "market": data["market"], "macro": data["macro"]},
        "peer_benchmarking": {"sec": data["sec"], "peers": data["peers"]},
        "accounting_quality": {"sec": data["sec"], "investor_relations": data["investor_relations"]},
        "audit_assurance": {"sec": data["sec"]},
        "tax_exposure": {"sec": data["sec"]},
        "ai_cloud_strategy": {"sec": data["sec"], "investor_relations": data["investor_relations"]},
        "regulatory_operational_risk": {"sec": data["sec"]},
    }
    known_sources = set(source_manifest)
    known_fact_ids = set(data["sec"].get("fact_index", {}))
    quality = AssertionEvaluator([
        ("structured component", lambda output: bool(output.get("component") and output.get("title"))),
        ("bounded confidence", lambda output: 0 <= output.get("confidence", -1) <= 1),
        ("has substantive findings", lambda output: len(output.get("findings", [])) >= 3),
        ("declares caveats", lambda output: bool(output.get("caveats"))),
        ("declares diligence questions", lambda output: bool(output.get("diligence_questions"))),
        ("source references resolve", lambda output: bool(output.get("evidence_refs")) and set(output["evidence_refs"]) <= known_sources),
        ("finding source references resolve", lambda output: all(
            bool(finding.get("evidence_refs"))
            and set(finding.get("evidence_refs", [])) <= known_sources
            for finding in output.get("findings", [])
        )),
        ("confidence is labeled uncalibrated", lambda output: output.get("confidence_label") == "uncalibrated analytical confidence"),
        ("finding fact links resolve", lambda output: all(
            set(finding.get("fact_ids", [])) <= known_fact_ids
            for finding in output.get("findings", [])
        )),
        ("applicable findings expose fact links", lambda output: all(
            bool(finding.get("fact_ids"))
            for finding in output.get("findings", [])
            if finding.get("fact_lineage_applicable")
        )),
    ], name="institutional_finance_component_quality")
    specialist_results = []
    for name, params in jobs.items():
        output, why_id = _run_child(parent, name, analysis_adapter, params, quality)
        specialist_results.append({"output": output, "evidence_id": why_id})
        print(f"  {name:34} {output['confidence']:.0%} evidence={why_id}")

    print("\n[3/5] Governed committee synthesis")
    committee = parent.spawn(
        "Synthesize the source-backed specialist record and expose disagreements",
        grants=[capability("synthesis.committee")], adapters=[synthesis_adapter], node_id="committee",
    )
    synthesis_run = committee.enact(
        "synthesis.committee",
        {"ticker": TICKER, "analyses": specialist_results, "source_manifest": source_manifest, "filing": data["sec"]["filing"]},
        actor="research_committee",
    )
    synthesis_output = _must_output(synthesis_run, "synthesis.committee")
    print(f"  posture: {synthesis_output['decision_posture']}")
    print(f"  evidence links: {len(synthesis_output['evidence_chain'])}")

    print("\n[4/5] Persona-specific decision views")
    reports = {}
    report_evidence = {}
    for audience in AUDIENCES:
        reporter = parent.spawn(
            f"Prepare {audience} decision view", grants=[capability(f"consumer.{audience}")], adapters=[consumers]
        )
        run = reporter.enact(f"consumer.{audience}", {"synthesis": synthesis_output}, actor=f"owner:{audience}")
        reports[audience] = _must_output(run, f"consumer.{audience}")
        report_evidence[audience] = run.why_id
        print(f"  {audience:24} {reports[audience]['review_status']}")

    release_content = _release_content(data, source_manifest, specialist_results, synthesis_output, reports)
    artifact_digest = canonical_sha256(release_content)
    release_key = f"{TICKER}:{data['sec']['filing']['accessionNumber']}:institutional-release"
    review_store = ReleaseReviewStore(REVIEW_DB)
    review = review_store.submit(
        release_key,
        f"Microsoft institutional intelligence release — {data['sec']['filing']['fiscal_year']}",
        artifact_digest,
        ["finance-review-lead", "risk-review-lead"],
        quorum=2,
        requested_by="autarch-finance-intelligence",
        rationale="Accountable release requires finance and risk review of exact source, fact, analysis, and persona content.",
        ttl_seconds=30 * 24 * 60 * 60,
    )
    review_state = _review_status(review, artifact_digest)
    for report in reports.values():
        report["review_status"] = review_state["status"].upper()
        report["review_id"] = review_state["id"]
        report["release_authorized"] = review_state["release_authorized"]
    review_chain_ok, review_chain_broken = review_store.verify_chain()
    review_store.export(OUTPUTS / "review_audit.json")
    evidence_quality = assess_evidence_quality(
        data["sec"], source_manifest, specialist_results, review_state
    )
    print(
        f"  durable review: {review_state['status']} id={review_state['id']} "
        f"digest={artifact_digest[:16]}…"
    )

    print("\n[5/5] Evidence packaging and product report")
    parent.memory.export_audit(OUTPUTS / "signed_audit.jsonl")
    chain_ok, broken = parent.memory.verify_chain()
    retrieved = [item["retrieved_at"] for item in source_manifest.values()]
    package = {
        "product": {
            "name": "Autarch Finance Intelligence",
            "edition": "Microsoft Quarterly & Change Intelligence",
            "version": "1.1-pilot",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_mode": mode,
            "freshness": (
                "VERIFIED CACHE REPLAY" if mode == "cache-only"
                else "LIVE / FRESH CACHE" if all(item.get("freshness") == "fresh" for item in source_manifest.values())
                else "MIXED / STALE FALLBACK"
            ),
            "classification": (
                "Controlled pilot — digest-bound professional review approved"
                if review_state["release_authorized"]
                else "Controlled pilot — release pending digest-bound professional review"
            ),
            "buyers": ["Investment research", "CFO/strategy", "Audit and assurance", "Credit risk", "Regulatory and enterprise risk"],
        },
        "architecture": {
            "ingestion": list(data),
            "analysis_components": list(jobs),
            "synthesis": "governed_research_synthesis",
            "consumers": list(reports),
        },
        "source_data": data,
        "sources": source_manifest,
        "analyses": specialist_results,
        "synthesis": synthesis_output,
        "reports": reports,
        "governance": {
            "guarantees": [{"invariant": proof.invariant.label(), "holds": proof.holds, "reason": proof.reason} for proof in guarantee.proofs],
            "budget": parent.budget.snapshot(),
            "integrity": {"verified": chain_ok, "broken_record": broken, "record_count": parent.memory.count()},
            "review": review_state,
            "review_integrity": {
                "verified": review_chain_ok,
                "broken_sequence": review_chain_broken,
                "database": REVIEW_DB.as_posix(),
            },
            "release": {
                "release_key": release_key,
                "content_digest": artifact_digest,
                "digest_algorithm": "SHA-256 over canonical release JSON",
                "content_schema": "autarch.microsoft-finance.release.v1.1",
            },
            "evidence_quality": evidence_quality,
            "synthesis_evidence": synthesis_run.why_id,
            "report_evidence": report_evidence,
            "source_ingest_evidence": ingest_evidence,
        },
        "methodology": {
            "data_cutoff": max(retrieved),
            "historical_basis": "Latest SEC 10-K plus recent 10-Q/8-K timeline discovered from submissions metadata; annual and quarterly periods retain selected facts and recast chains.",
            "market_basis": data["market"]["rights_note"],
            "peer_basis": data["peers"]["methodology_note"],
            "valuation_basis": "Illustrative five-year levered-FCF DCF and reverse DCF; assumptions are disclosed, not predicted.",
            "quality_controls": ["source hashing", "URL-bound content-addressed cache", "fact-level lineage", "quarter/annual reconciliation", "deterministic TTM calculations", "filing-change triage", "signed specialist evidence", "static guarantees", "persistent digest-bound named review"],
            "limitations": [
                "Public information can be incomplete, delayed, restated, or differently classified.",
                "This workflow does not access management forecasts, customer cohorts, private contracts, channel checks, or licensed consensus estimates.",
                "No model output or computed confidence replaces accountable professional judgment.",
                "Analytical confidence is uncalibrated and is not a probability; evidence-quality dimensions are reported separately.",
                "Reviewer names are attribution fields until a deployment maps authenticated identities from an enterprise identity provider.",
                "The review event hash chain detects isolated mutation but requires protected storage or external signature anchoring against a privileged full-database rewrite.",
            ],
        },
    }
    package["governance"]["package_validation"] = _validate_package(package)
    json_path = OUTPUTS / "microsoft_finance_intelligence.json"
    json_path.write_text(json.dumps(package, indent=2), encoding="utf-8")
    html_path = render_microsoft_report(package, OUTPUTS / "microsoft_finance_intelligence.html")
    markdown_path = render_microsoft_markdown(package, OUTPUTS / "microsoft_finance_intelligence.md")
    evidence_manifest = {
        "generated_at": package["product"]["generated_at"],
        "ticker": TICKER,
        "filing": data["sec"]["filing"],
        "sources": source_manifest,
        "specialist_evidence": {item["output"]["component"]: item["evidence_id"] for item in specialist_results},
        "synthesis_evidence": synthesis_run.why_id,
        "release": package["governance"]["release"],
        "review": review_state,
        "evidence_quality": evidence_quality,
        "package_validation": package["governance"]["package_validation"],
        "ledger": {"verified": chain_ok, "broken_record": broken, "records": parent.memory.count()},
        "review_ledger": package["governance"]["review_integrity"],
    }
    (OUTPUTS / "evidence_manifest.json").write_text(json.dumps(evidence_manifest, indent=2), encoding="utf-8")
    (OUTPUTS / "METHODOLOGY.txt").write_text(
        "AUTARCH FINANCE INTELLIGENCE — METHODOLOGY\n\n"
        "1. Discover latest Microsoft 10-K from SEC submissions metadata.\n"
        "2. Discover recent 10-Q/8-K filings and retrieve the prior 10-K for change triage.\n"
        "3. Hash and URL-bind every raw response; preserve retrieval metadata.\n"
        "4. Normalize annual, quarterly, TTM, balance, segment, and product facts with selected-fact lineage.\n"
        "5. Derive quarter values only from defensible cumulative/annual arithmetic and reconcile to annual facts.\n"
        "6. Add Microsoft IR, FRED, indicative market history, and SEC peer facts.\n"
        "7. Run fifteen deterministic specialists under attenuated capabilities.\n"
        "8. Bind synthesis to signed why-records and submit exact release content for two-person review.\n"
        "9. Preserve pending/approved/rejected/expired/superseded state in a verified review event chain.\n"
        "10. Prohibit external publication and trading; approval does not itself publish.\n\n"
        "Valuation is illustrative. Controlled pilot only. Professional review is required.\n",
        encoding="utf-8",
    )
    (OUTPUTS / "ARCHITECTURE.txt").write_text(
        "PUBLIC SOURCES\n"
        "  SEC 10-K/10-Q/8-K + XBRL | Microsoft IR | FRED | Market | Peers\n"
        "                         |\n"
        "                         v\n"
        "GOVERNED INGESTION + CONTENT-ADDRESSED EVIDENCE CACHE\n"
        "                         |\n"
        "                         v\n"
        "15 CAPABILITY-ATTENUATED SPECIALISTS\n"
        "  Quarterly + TTM | Filing Changes | Evidence-linked Core Analysis\n"
        "  Performance | Segments | Cash/Capex | Credit | Capital Allocation\n"
        "  Valuation | Market Risk | Peers | Accounting | Audit | Tax | AI | Risk\n"
        "                         |\n"
        "                         v\n"
        "SIGNED SYNTHESIS + DIGEST-BOUND NAMED REVIEW + PROFESSIONAL VIEWS\n",
        encoding="utf-8",
    )
    bundle_path = _write_evidence_bundle(cache, OUTPUTS, evidence_manifest)
    artifact_paths = [
        json_path, html_path, markdown_path, OUTPUTS / "evidence_manifest.json",
        OUTPUTS / "signed_audit.jsonl", OUTPUTS / "review_audit.json",
        OUTPUTS / "METHODOLOGY.txt", OUTPUTS / "ARCHITECTURE.txt", bundle_path,
    ]
    artifact_manifest = {path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)} for path in artifact_paths}
    (OUTPUTS / "artifact_manifest.json").write_text(json.dumps(artifact_manifest, indent=2), encoding="utf-8")

    print(f"  latest filing: {data['sec']['filing']['fiscal_year']} ({data['sec']['filing']['filingDate']})")
    print(f"  source objects: {len(source_manifest)}; signed actions: {parent.memory.count()}")
    print(f"  ledger verifies: {chain_ok} (broken={broken})")
    print(f"  package validation: {package['governance']['package_validation']['check_count']} checks passed")
    print(f"  HTML: {html_path.resolve()}")
    print(f"  Detailed report: {markdown_path.resolve()}")
    print(f"  Evidence bundle: {bundle_path.resolve()}")
    return package


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run governed Microsoft public-company intelligence")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--company", help="use the general SEC financial profile for this legal company name")
    selection.add_argument("--ticker", help="use the general SEC financial profile for this ticker, for example GOOGL")
    selection.add_argument("--cik", help="use the general SEC financial profile for this registrant CIK")
    parser.add_argument("--mode", choices=("live", "hybrid", "cache-only"), default="hybrid", help="source retrieval mode")
    parser.add_argument("--cache-ttl-hours", type=float, default=12.0, help="fresh-cache lifetime in hybrid mode")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="descriptive SEC-compliant HTTP user agent")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if any(value is not None for value in (args.company, args.ticker, args.cik)):
        from company_finance_intelligence import main as company_main

        option, value = next(
            (option, value) for option, value in (
                ("--company", args.company), ("--ticker", args.ticker), ("--cik", args.cik)
            ) if value is not None
        )
        company_main([
            option, value, "--mode", args.mode, "--user-agent", args.user_agent,
            "--cache-ttl-hours", str(args.cache_ttl_hours),
        ])
        return
    run_product(args.mode, args.user_agent, args.cache_ttl_hours)
    print("\nControlled pilot only; accountable professional judgment and data entitlements remain required.")


if __name__ == "__main__":
    main()
