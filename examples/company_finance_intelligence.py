"""Run governed financial intelligence for a supported SEC-reporting company.

python examples/company_finance_intelligence.py --company "Alphabet Inc." --mode hybrid
python examples/company_finance_intelligence.py --ticker MSFT --mode cache-only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from autarch import Agent, AssertionEvaluator, Invariant, Policy, PolicyEffect, capability, from_callables
from autarch.contracts import new_id
from autarch.review import ReleaseReviewStore, canonical_json, canonical_sha256
from company_finance_data import fetch_company_sec, resolve_company
from microsoft_finance_data import DEFAULT_USER_AGENT, SourceCache


WORKSPACE = Path("sandbox/company_finance_intelligence")
SCHEMA_VERSION = "autarch.company-finance.v1"
COMPONENTS = ("performance", "cash_flow", "liquidity", "quarterly", "tax", "filing_changes")


def _ratio(numerator: Any, denominator: Any) -> Optional[float]:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    value = numerator / denominator
    return value if math.isfinite(value) else None


def _record(data: Mapping[str, Any], section: str, metric: str, period: str) -> Mapping[str, Any]:
    if section in {"quarterly", "ttm"}:
        return data.get(f"{section}_lineage", {}).get(metric, {}).get(period, {})
    result = data.get("fact_lineage", {}).get(section, {}).get(metric, {}).get(period, {})
    if period == data["latest_annual_end"][:4] and result.get("end") != data["latest_annual_end"]:
        return {}
    return result


def _observation(
    data: Mapping[str, Any], label: str, references: Sequence[tuple],
    *, operation: str = "reported", unit: Optional[str] = None,
) -> dict:
    records = [_record(data, *reference) for reference in references]
    values = [record.get("value") for record in records]
    complete = bool(values) and all(value is not None for value in values)
    value = None
    if complete:
        if operation == "reported":
            value = values[0]
        elif operation == "difference":
            value = values[0] - values[1]
        elif operation == "sum":
            value = sum(values)
        elif operation == "ratio":
            value = _ratio(values[0], values[1])
        elif operation == "growth":
            ratio = _ratio(values[0], values[1])
            value = ratio - 1 if ratio is not None else None
        else:
            raise ValueError(f"Unsupported calculation: {operation}")
    return {
        "label": label, "value": value,
        "unit": unit or f"{data['currency']} billions",
        "period": references[0][2] if references else None,
        "kind": "reported" if operation == "reported" else "calculated",
        "formula": operation,
        "fact_ids": [record["fact_id"] for record in records if record.get("fact_id")],
        "source_ids": ["S-SEC-COMPANYFACTS"],
        "status": "available" if value is not None else "unavailable",
        "limitation": "" if value is not None else "Not separately available from supported facts or the calculation has no valid denominator; no zero was substituted.",
    }


def analyze_company(data: dict, component: str) -> dict:
    year = data["latest_annual_end"][:4]
    prior = str(int(year) - 1)
    annual = lambda metric, period=year: ("annual", metric, period)
    balance = lambda metric: ("balance_sheet", metric, year)
    findings = []
    titles = {
        "performance": "Performance and margins",
        "cash_flow": "Cash flow and capital allocation",
        "liquidity": "Balance sheet and liquidity",
        "quarterly": "Quarterly and trailing-twelve-month results",
        "tax": "Reported tax measures",
        "filing_changes": "Filing and disclosure changes",
    }
    if component == "performance":
        for label, metric in (("Revenue", "revenue"), ("Operating income", "operating_income"), ("Net income", "net_income")):
            findings.append(_observation(data, label, [annual(metric)]))
        findings.extend([
            _observation(data, "Revenue growth", [annual("revenue"), annual("revenue", prior)], operation="growth", unit="percent"),
            _observation(data, "Operating margin", [annual("operating_income"), annual("revenue")], operation="ratio", unit="percent"),
            _observation(data, "Net margin", [annual("net_income"), annual("revenue")], operation="ratio", unit="percent"),
        ])
    elif component == "cash_flow":
        for label, metric in (("Operating cash flow", "operating_cash_flow"), ("Capital expenditures", "capex"), ("Dividends paid", "dividends_paid"), ("Share repurchases", "share_repurchases"), ("Stock compensation", "stock_compensation")):
            findings.append(_observation(data, label, [annual(metric)]))
        findings.extend([
            _observation(data, "Free cash flow", [annual("operating_cash_flow"), annual("capex")], operation="difference"),
            _observation(data, "Capex / revenue", [annual("capex"), annual("revenue")], operation="ratio", unit="percent"),
            _observation(data, "CFO / net income", [annual("operating_cash_flow"), annual("net_income")], operation="ratio", unit="multiple"),
        ])
    elif component == "liquidity":
        for label, metric in (("Cash and equivalents", "cash"), ("Short-term investments", "short_term_investments"), ("Total assets", "total_assets"), ("Total liabilities", "total_liabilities"), ("Equity", "equity")):
            findings.append(_observation(data, label, [balance(metric)]))
        findings.extend([
            _observation(data, "Current ratio", [balance("current_assets"), balance("current_liabilities")], operation="ratio", unit="multiple"),
            _observation(data, "Reported funded debt", [balance("long_term_debt_current"), balance("long_term_debt_noncurrent")], operation="sum"),
        ])
    elif component == "quarterly":
        period = data.get("ttm_as_of", "")
        for section in ("quarterly", "ttm"):
            for label, metric in (("Revenue", "revenue"), ("Operating cash flow", "operating_cash_flow"), ("Capex", "capex"), ("Free cash flow", "free_cash_flow")):
                findings.append(_observation(data, f"{section.upper()} {label}", [(section, metric, period)]))
    elif component == "tax":
        for label, metric in (("Pretax income", "pretax_income"), ("Income tax expense", "income_tax"), ("Cash taxes paid", "cash_taxes_paid")):
            findings.append(_observation(data, label, [annual(metric)]))
        findings.extend([
            _observation(data, "Effective tax rate", [annual("income_tax"), annual("pretax_income")], operation="ratio", unit="percent"),
            _observation(data, "Cash tax / pretax income", [annual("cash_taxes_paid"), annual("pretax_income")], operation="ratio", unit="percent"),
        ])
    elif component == "filing_changes":
        for row in data["disclosure_changes"]:
            findings.append({
                "label": row["topic"], "value": row["current_hits"] - row["prior_hits"],
                "unit": "phrase-hit change", "period": data["latest_annual_end"],
                "kind": "disclosure triage", "formula": "current annual phrase hits - prior annual phrase hits",
                "fact_ids": [], "source_ids": row["source_ids"], "status": "available",
                "limitation": "Phrase counts are not materiality, sentiment, or a legal redline.",
            })
    else:
        raise ValueError(f"Unknown analysis component: {component}")
    available = sum(finding["value"] is not None for finding in findings)
    return {
        "component": component, "title": titles[component], "findings": findings,
        "status": "available" if findings and available == len(findings) else "partial" if available else "unavailable",
        "method": "Deterministic disclosed-fact analysis; no forecast, rating, or confidence probability assigned.",
        "caveats": [
            "Consolidated facts do not establish standalone subsidiary performance.",
            "FCF is CFO less cash capex, not a GAAP metric; missing data is not zero.",
            "Tax, credit, valuation, and audit conclusions require accountable professional work.",
        ],
    }


def synthesize_company(data: dict, analyses: list) -> dict:
    findings = [finding for analysis in analyses for finding in analysis["output"]["findings"]]
    revenue = _record(data, "annual", "revenue", data["latest_annual_end"][:4]).get("value")
    revenue_text = (
        f"reported {data['currency']} {revenue:,.3f} billion consolidated revenue"
        if revenue is not None else "has no consolidated revenue fact mapped for the latest annual period"
    )
    failed = sum(not check["within_tolerance"] for check in data["reconciliation"])
    return {
        "summary": f"{data['company']['name']} {revenue_text} for the year ended {data['latest_annual_end']}. "
                   f"Latest filing period: {data['latest_report_end']}. "
                   f"{len(data['reconciliation']) - failed}/{len(data['reconciliation'])} available quarter-to-annual checks reconcile.",
        "available_observations": sum(finding["value"] is not None for finding in findings),
        "unavailable_observations": sum(finding["value"] is None for finding in findings),
        "review_focus": [
            "Confirm material figures against filed statements and investigate every reconciliation exception.",
            "Review accounting policy changes, nonrecurring items, tax judgments, and sector-specific measures.",
            "Do not infer subsidiary statements, forecasts, market values, or recommendations from consolidated data.",
        ],
        "evidence_ids": [analysis["evidence_id"] for analysis in analyses],
    }


def release_content(package: Mapping[str, Any]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "data": package["data"],
        "sources": {
            source_id: {key: source.get(key) for key in ("sha256", "url", "provider", "evidence_class", "authoritative")}
            for source_id, source in package["sources"].items()
        },
        "analyses": [analysis["output"] for analysis in package["analyses"]],
        "synthesis": {key: value for key, value in package["synthesis"].items() if key != "evidence_ids"},
    }


def _execute(parent: Agent, adapter: Any, name: str, params: dict, evaluator: Any = None) -> Any:
    child = parent.spawn(
        intent=f"Execute {name}", grants=[capability(name)], adapters=[adapter],
        node_id=name.replace(".", ":"),
    )
    result = child.enact(name, params, actor=name, evaluate=evaluator)
    if not result.executed or result.result is None or not result.result.ok:
        raise RuntimeError(f"{name} failed: {result.result.error if result.result else result.gate.reason}")
    if evaluator is not None and (result.verdict is None or not result.verdict.passed):
        raise RuntimeError(f"{name} failed its deterministic output evaluation")
    return result


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_package(package: Mapping[str, Any]) -> dict:
    data = package["data"]
    facts = data["fact_index"]
    sources = package["sources"]
    governance = package["governance"]
    findings = [finding for analysis in package["analyses"] for finding in analysis["output"]["findings"]]
    digest = canonical_sha256(release_content(package))
    checks = {
        "company identity": all(record.get("cik") == data["company"]["cik"] for record in facts.values()),
        "source hashes": bool(sources) and all(_valid_digest(source.get("sha256")) for source in sources.values()),
        "component contract": tuple(item["output"]["component"] for item in package["analyses"]) == COMPONENTS,
        "fact inventory": bool(facts) and all(record["fact_id"] == fact_id and record.get("source_id") in sources for fact_id, record in facts.items()),
        "derivation inputs": all(input_fact.get("fact_id") in facts for record in facts.values() for input_fact in record.get("inputs", [])),
        "finding references": all(set(finding["fact_ids"]) <= set(facts) and set(finding["source_ids"]) <= set(sources) for finding in findings),
        "numeric finding lineage": all(bool(finding["fact_ids"]) for finding in findings if finding["value"] is not None and finding["kind"] != "disclosure triage"),
        "release digest": digest == governance["release_digest"] == governance["review"]["artifact_digest"],
        "signed action chain": governance["action_chain"]["verified"],
        "review event chain": governance["review_chain"]["verified"],
        "forbidden actions": bool(governance["guarantees"]) and all(item["holds"] for item in governance["guarantees"]),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise RuntimeError("Package integrity checks failed: " + ", ".join(failed))
    return {"passed": True, "checks": checks}


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _write_artifacts(package: dict, output: Path, source_paths: Mapping[str, Path]) -> None:
    from company_finance_report import render_company_report

    output.mkdir(parents=True, exist_ok=True)
    (output / "financial_report.json").write_text(json.dumps(package, indent=2, allow_nan=False), encoding="utf-8")
    (output / "release_content.json").write_text(canonical_json(release_content(package)), encoding="utf-8")
    render_company_report(package, output)
    members = {}
    with zipfile.ZipFile(output / "evidence_bundle.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output.iterdir()):
            if not path.is_file() or path.suffix == ".zip":
                continue
            member = f"outputs/{path.name}"
            archive.write(path, member)
            members[member] = {"bytes": path.stat().st_size, "sha256": _file_hash(path)}
        for source_id, path in source_paths.items():
            if _file_hash(path) != package["sources"][source_id]["sha256"]:
                raise RuntimeError(f"Source bytes changed during packaging: {source_id}")
            member = f"evidence/{source_id}/{path.name}"
            archive.write(path, member)
            members[member] = {"bytes": path.stat().st_size, "sha256": _file_hash(path)}
        archive.writestr("bundle_manifest.json", json.dumps({"release_digest": package["governance"]["release_digest"], "members": members}, indent=2))
    manifest = {
        path.name: {"bytes": path.stat().st_size, "sha256": _file_hash(path)}
        for path in sorted(output.iterdir()) if path.is_file()
    }
    (output / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def run_company(
    *, company: Optional[str] = None, ticker: Optional[str] = None, cik: Optional[str] = None,
    mode: str = "hybrid", user_agent: str = DEFAULT_USER_AGENT,
    ttl_hours: float = 12.0, workspace: Path = WORKSPACE,
) -> dict:
    if not math.isfinite(ttl_hours) or ttl_hours <= 0:
        raise ValueError("cache TTL must be positive and finite")
    root = Path(workspace)
    registry = SourceCache(root / "registry", mode=mode, user_agent=user_agent, ttl_hours=ttl_hours)
    identity = resolve_company(registry, company=company, ticker=ticker, cik=cik)
    company_root = root / f"CIK{identity['cik']}"
    run_id = new_id("run")
    run_root = company_root / "runs" / run_id
    output = run_root / "outputs"
    cache = SourceCache(company_root / "cache", mode=mode, user_agent=user_agent, ttl_hours=ttl_hours)
    ingestion = from_callables({"sec": lambda: fetch_company_sec(cache, identity)}, namespace="ingest")
    analyses_adapter = from_callables({"financial": analyze_company}, namespace="analysis")
    synthesis_adapter = from_callables({"committee": synthesize_company}, namespace="synthesis")
    parent = Agent(
        intent=f"Produce consolidated financial intelligence for SEC CIK {identity['cik']}",
        grants=[capability("ingest.sec"), capability("analysis.financial"), capability("synthesis.committee")],
        adapters=[ingestion, analyses_adapter, synthesis_adapter], workspace=run_root,
        policies=[
            Policy("no_publication", PolicyEffect.DENY.value, capability="external.publish", reason="Professional review is separate from publication authority"),
            Policy("no_trading", PolicyEffect.DENY.value, capability="trade.*", reason="Financial analysis has no trading authority"),
        ], budget={"calls": 10, "risk": 100, "cost": 2.0},
    )
    guarantee = parent.guarantee([Invariant.forbid("external.publish"), Invariant.forbid("trade.execute")])
    if not guarantee.all_hold:
        raise RuntimeError("Static publication/trading guarantees failed")
    print(f"[1/4] SEC CIK {identity['cik']}: collecting {mode} evidence")
    ingest_run = _execute(parent, ingestion, "ingest.sec", {})
    data = ingest_run.result.output
    sources = {**registry.manifest(), **cache.manifest()}
    source_paths = {
        source_id: source_cache.root / metadata["cache_path"]
        for source_cache in (registry, cache) for source_id, metadata in source_cache.manifest().items()
    }
    for source_id, path in source_paths.items():
        if _file_hash(path) != sources[source_id]["sha256"]:
            raise RuntimeError(f"Source hash verification failed: {source_id}")
    evaluator = AssertionEvaluator([
        ("component", lambda value: value.get("component") in COMPONENTS),
        ("caveats", lambda value: bool(value.get("caveats"))),
        ("source references", lambda value: all(set(finding["source_ids"]) <= set(sources) for finding in value.get("findings", []))),
        ("fact references", lambda value: all(set(finding["fact_ids"]) <= set(data["fact_index"]) for finding in value.get("findings", []))),
        ("finite values", lambda value: all(finding["value"] is None or math.isfinite(finding["value"]) for finding in value.get("findings", []))),
    ], name="company_financial_contract")
    print(f"[2/4] {data['company']['name']}: six governed financial analyses")
    analyses = []
    for component in COMPONENTS:
        result = _execute(parent, analyses_adapter, "analysis.financial", {"data": data, "component": component}, evaluator)
        analyses.append({"output": result.result.output, "evidence_id": result.why_id})
    synthesis = _execute(parent, synthesis_adapter, "synthesis.committee", {"data": data, "analyses": analyses})
    package = {
        "schema_version": SCHEMA_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id, "mode": mode, "data": data, "sources": sources,
        "analyses": analyses, "synthesis": synthesis.result.output,
    }
    digest = canonical_sha256(release_content(package))
    print("[3/4] Bind exact content to persistent finance and risk review")
    review_db = company_root / "reviews" / "release_reviews.db"
    store = ReleaseReviewStore(review_db)
    review = store.submit(
        f"CIK{identity['cik']}:consolidated-financial-release", f"{data['company']['name']} consolidated financial report",
        digest, ["finance-review-lead", "risk-review-lead"], quorum=2,
        requested_by="autarch-company-finance", rationale="Review consolidated facts, calculations, source coverage, and intended use.",
        ttl_seconds=30 * 24 * 60 * 60,
    )
    reconciliation_ok = bool(data["reconciliation"]) and all(check["within_tolerance"] for check in data["reconciliation"])
    approval_valid = review.valid_for(digest) and (review.expires_at is None or datetime.now(timezone.utc).timestamp() < review.expires_at)
    action_ok, action_broken = parent.memory.verify_chain()
    review_ok, review_broken = store.verify_chain()
    package["governance"] = {
        "release_digest": digest,
        "review": {**review.as_dict(), "digest_matches": review.artifact_digest == digest, "approval_valid": approval_valid},
        "review_database": review_db.as_posix(),
        "external_release_authorized": False,
        "review_and_reconciliation_passed": approval_valid and reconciliation_ok and not data["coverage"]["retrieval_gaps"],
        "identity_assurance": "CLI names are asserted attribution only; deployment must supply authenticated IdP identities.",
        "action_chain": {"verified": action_ok, "broken": action_broken, "records": parent.memory.count()},
        "review_chain": {"verified": review_ok, "broken": review_broken},
        "guarantees": [{"label": proof.invariant.label(), "holds": proof.holds, "reason": proof.reason} for proof in guarantee.proofs],
        "budget": parent.budget.snapshot(),
        "reconciliation_passed": reconciliation_ok,
    }
    package["governance"]["package_validation"] = validate_package(package)
    output.mkdir(parents=True, exist_ok=True)
    parent.memory.export_audit(output / "signed_audit.jsonl")
    store.export(output / "review_audit.json")
    print("[4/4] Write report, normalized facts, review record, and hashed evidence bundle")
    _write_artifacts(package, output, source_paths)
    pointer = company_root / f"latest-{run_id}.tmp"
    pointer.write_text(json.dumps({"run_id": run_id, "outputs": output.resolve().as_posix(), "release_digest": digest}), encoding="utf-8")
    pointer.replace(company_root / "latest.json")
    print(f"Company: {data['company']['name']} | CIK {identity['cik']} | {data['currency']}")
    print(f"Annual: {data['latest_annual_end']} | latest reported period: {data['latest_report_end']}")
    print(f"Sources: {len(sources)} | indexed facts: {len(data['fact_index'])} | signed actions: {parent.memory.count()}")
    print(f"Reconciliations: {sum(check['within_tolerance'] for check in data['reconciliation'])}/{len(data['reconciliation'])}")
    print(f"Review: {review.status} | {review.id} | digest {digest}")
    print(f"HTML: {(output / 'financial_report.html').resolve()}")
    print(f"JSON: {(output / 'financial_report.json').resolve()}")
    print(f"Review DB: {review_db.resolve()}")
    print("Controlled pilot. Unavailable metrics are not zero; subsidiaries are not separately analyzed. No publication or trading authority.")
    return package


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Run consolidated financial intelligence for a SEC US-GAAP 10-K/10-Q filer")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--company", help="legal company name, for example Alphabet Inc.")
    selection.add_argument("--ticker", help="exact ticker, for example GOOGL or MSFT")
    selection.add_argument("--cik", help="SEC CIK; bypass ticker-directory lookup")
    parser.add_argument("--mode", choices=("live", "hybrid", "cache-only"), default="hybrid")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="SEC-compliant organization/contact identity")
    parser.add_argument("--cache-ttl-hours", type=float, default=12.0)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE, help="root for isolated per-CIK cache, runs, and reviews")
    args = parser.parse_args(argv)
    try:
        run_company(company=args.company, ticker=args.ticker, cik=args.cik, mode=args.mode,
                    user_agent=args.user_agent, ttl_hours=args.cache_ttl_hours, workspace=args.workspace)
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(2, f"Financial report not completed: {error}\n")


if __name__ == "__main__":
    main()