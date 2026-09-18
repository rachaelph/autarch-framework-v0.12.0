"""Company resolution and disclosure-based financial data for SEC registrants."""
from __future__ import annotations

import calendar
import math
import re
from datetime import date
from typing import Any, Dict, Mapping, Optional
from urllib.parse import quote

from microsoft_finance_data import (
    ANNUAL_METRICS,
    BALANCE_METRICS,
    SourceCache,
    normalize_company_facts_with_lineage,
    visible_text,
)


SEC_PROVIDER = "U.S. Securities and Exchange Commission"
ANNUAL_FORMS = ("10-K", "10-K/A")
FINANCIAL_FORMS = (*ANNUAL_FORMS, "10-Q", "10-Q/A")
DIRECTORY_URL = "https://www.sec.gov/files/company_tickers.json"


def _name_key(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", value.casefold())
    while words and words[-1] in {"inc", "incorporated", "corp", "corporation", "plc", "ltd", "limited", "co", "company"}:
        words.pop()
    return " ".join(words)


def select_company(directory: Mapping[str, Any], query: str, *, by_ticker: bool = False) -> Dict[str, Any]:
    """Resolve one legal registrant; multiple share classes collapse to its CIK."""
    registrants: Dict[str, Dict[str, Any]] = {}
    for row in directory.values():
        if not isinstance(row, Mapping) or not str(row.get("cik_str", "")).isdigit():
            continue
        cik = str(int(row["cik_str"])).zfill(10)
        record = registrants.setdefault(cik, {"cik": cik, "name": row.get("title", ""), "tickers": []})
        symbol = str(row.get("ticker", "")).upper()
        if symbol and symbol not in record["tickers"]:
            record["tickers"].append(symbol)
    text = query.strip()
    if not text:
        raise ValueError("company name or ticker must not be empty")
    matches = [record for record in registrants.values() if text.upper() in record["tickers"]]
    if not matches and not by_ticker:
        key = _name_key(text)
        if not key:
            raise ValueError("provide a company name, ticker, or SEC CIK")
        matches = [record for record in registrants.values() if _name_key(str(record["name"])) == key]
        if not matches and len(key) >= 3:
            matches = [record for record in registrants.values() if key in _name_key(str(record["name"]))]
    if not matches:
        raise ValueError(
            f"No SEC-listed registrant matched {query!r}. Use the legal parent name, an exact ticker, or --cik; "
            "private companies and subsidiary brands may not have standalone public filings."
        )
    if len(matches) != 1:
        candidates = "; ".join(
            f"{record['name']} ({', '.join(sorted(record['tickers']))}; CIK {record['cik']})"
            for record in sorted(matches, key=lambda record: str(record["name"]))[:10]
        )
        raise ValueError(f"Ambiguous company name {query!r}. Use --ticker or --cik. Matches: {candidates}")
    return {**matches[0], "tickers": sorted(matches[0]["tickers"])}


def resolve_company(
    cache: SourceCache, *, company: Optional[str] = None,
    ticker: Optional[str] = None, cik: Optional[str] = None,
) -> Dict[str, Any]:
    if sum(value is not None for value in (company, ticker, cik)) != 1:
        raise ValueError("supply exactly one of --company, --ticker, or --cik")
    if cik is not None:
        if not re.fullmatch(r"\d{1,10}", cik) or int(cik) <= 0:
            raise ValueError("CIK must contain 1 to 10 digits and be positive")
        return {"cik": str(int(cik)).zfill(10), "name": "", "tickers": []}
    directory, _metadata = cache.fetch_json(
        "S-SEC-TICKERS", DIRECTORY_URL, provider=SEC_PROVIDER, authoritative=True,
        description="SEC registrant names, tickers, and CIK identities",
        evidence_class="regulatory filing metadata", ttl_hours=24,
    )
    if not isinstance(directory, dict):
        raise ValueError("SEC company directory has an unsupported schema")
    return select_company(directory, ticker if ticker is not None else str(company), by_ticker=ticker is not None)


def filing_timeline(submissions: Mapping[str, Any], cik: str) -> list:
    recent = submissions.get("filings", {}).get("recent", {})
    records = []
    for index, form in enumerate(recent.get("form", [])):
        if form not in {*FINANCIAL_FORMS, "8-K", "8-K/A", "20-F", "40-F", "6-K"}:
            continue
        record = {
            field: values[index] if index < len(values) else None
            for field in ("form", "filingDate", "reportDate", "accessionNumber", "primaryDocument", "items")
            for values in [recent.get(field, [])]
        }
        accession = str(record.get("accessionNumber", ""))
        document = str(record.get("primaryDocument", ""))
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
            continue
        if not document or "/" in document or "\\" in document or document in {".", ".."}:
            continue
        record["url"] = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{quote(document, safe='')}"
        )
        records.append(record)
    return sorted(records, key=lambda record: (str(record["filingDate"]), str(record["accessionNumber"])), reverse=True)


def _reporting_currency(company_facts: Mapping[str, Any], annual_end: str) -> str:
    concepts = company_facts.get("facts", {}).get("us-gaap", {})
    for metric in ("revenue", "operating_cash_flow", "total_assets"):
        specs = ANNUAL_METRICS if metric in ANNUAL_METRICS else BALANCE_METRICS
        currencies = set()
        for concept in specs[metric][0]:
            for unit, rows in concepts.get(concept, {}).get("units", {}).items():
                if re.fullmatch(r"[A-Z]{3}", unit) and any(
                    row.get("form") in ANNUAL_FORMS and row.get("end") == annual_end
                    for row in rows
                ):
                    currencies.add(unit)
        if len(currencies) == 1:
            return next(iter(currencies))
        if len(currencies) > 1:
            raise ValueError("Multiple reporting currencies found; currency-specific normalization is required")
    raise ValueError("No supported annual US-GAAP monetary facts found; custom taxonomy or IFRS mapping is required")


def normalize_registrant(company_facts: Mapping[str, Any], submissions: Mapping[str, Any], filings: list) -> Dict[str, Any]:
    """Normalize supported consolidated facts without guessing unavailable values."""
    annual_filings = [filing for filing in filings if filing["form"] == "10-K"]
    if not annual_filings:
        forms = ", ".join(sorted({filing["form"] for filing in filings})) or "none"
        raise ValueError(f"No recent Form 10-K found (forms: {forms}). This connector supports US-GAAP 10-K/10-Q filers, not 20-F/IFRS or private companies.")
    if str(int(company_facts.get("cik", 0))) != str(int(submissions.get("cik", 0))):
        raise ValueError("Company Facts and submissions refer to different SEC registrants")
    annual_filing = annual_filings[0]
    annual_end = str(annual_filing["reportDate"])
    latest_report_date = max(str(filing["reportDate"]) for filing in filings if filing["form"] in FINANCIAL_FORMS)
    currency = _reporting_currency(company_facts, annual_end)
    year_end = str(submissions.get("fiscalYearEnd") or annual_end[5:].replace("-", ""))
    if not re.fullmatch(r"\d{4}", year_end):
        raise ValueError("No usable nominal fiscal year-end in SEC submissions")
    fiscal_month = int(year_end[:2])
    date(2000, fiscal_month, int(year_end[2:]))
    nominal_day = calendar.monthrange(2000, fiscal_month)[1]
    if abs(int(year_end[2:]) - nominal_day) > 7:
        raise ValueError("Mid-month fiscal calendars need a dedicated period mapping; no quarters were guessed")
    namespaces = {}
    for namespace in ("us-gaap", "dei"):
        concepts = {}
        for name, concept in company_facts.get("facts", {}).get(namespace, {}).items():
            units = {}
            for unit, rows in concept.get("units", {}).items():
                if unit not in {currency, f"{currency}/shares", "shares"}:
                    continue
                selected = []
                for row in rows:
                    try:
                        valid_number = math.isfinite(float(row.get("val")))
                    except (TypeError, ValueError):
                        valid_number = False
                    if valid_number and row.get("form") in FINANCIAL_FORMS and str(row.get("end", "")) <= latest_report_date:
                        selected.append(row)
                units[unit] = selected
            concepts[name] = {**concept, "units": units}
        namespaces[namespace] = concepts
    filtered = {"cik": company_facts.get("cik"), "facts": namespaces}
    year_ends = {str(filing["reportDate"]) for filing in annual_filings}
    for metric in ("revenue", "operating_cash_flow", "net_income"):
        for concept in ANNUAL_METRICS[metric][0]:
            for row in namespaces["us-gaap"].get(concept, {}).get("units", {}).get(currency, []):
                if row.get("form") not in ANNUAL_FORMS or row.get("fp") != "FY":
                    continue
                try:
                    elapsed = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days
                except (KeyError, ValueError):
                    continue
                if 350 <= elapsed <= 380:
                    year_ends.add(row["end"])
    normalized = normalize_company_facts_with_lineage(
        filtered, fiscal_year_end_month=fiscal_month, currency=currency,
        year_end_dates=sorted(year_ends), week_based=True, annual_forms=ANNUAL_FORMS,
        consistent_concepts=True,
    )
    revenue = normalized["ttm"].get("revenue")
    for ratio, metric in (
        ("operating_margin", "operating_income"), ("net_margin", "net_income"),
        ("free_cash_flow_margin", "free_cash_flow"), ("capex_intensity", "capex"),
    ):
        numerator = normalized["ttm"].get(metric)
        normalized["ttm"][ratio] = numerator / revenue if numerator is not None and revenue else None
    for record in normalized["fact_index"].values():
        record["cik"] = str(int(company_facts["cik"])).zfill(10)
        if "normalized_unit" not in record:
            record["normalized_unit"] = f"{currency} billions" if record.get("unit") == currency else record.get("unit")
    missing = []
    for section, metrics in (("annual", ANNUAL_METRICS), ("balance_sheet", BALANCE_METRICS)):
        for metric in metrics:
            selected = normalized["fact_lineage"][section][metric].get(annual_end[:4])
            if not selected or selected.get("end") != annual_end:
                missing.append({"section": section, "metric": metric, "reason": "No supported fact for the latest annual period; not assumed to be zero"})
    return {
        **normalized,
        "currency": currency,
        "fiscal_year_end": year_end,
        "latest_annual_end": annual_end,
        "latest_report_end": latest_report_date,
        "coverage": {
            "profile": "SEC US-GAAP consolidated financials",
            "missing_metrics": missing,
            "limitations": [
                "Public consolidated statements are not standalone statements for every subsidiary.",
                "Subsidiary discovery, segment mappings, company-specific KPIs, market prices, peer selection, and valuation models are not enabled in this general profile.",
                "Banks, insurers, and other specialized industries may require additional taxonomy mappings and professional interpretation.",
                "Quarter labels use the nominal fiscal year-end with a seven-day week-calendar tolerance; reported dates and derivation inputs remain visible.",
                "Quarter construction uses one concept per metric and fiscal year, preferring the selected annual concept, to avoid mixing different accounting measures.",
                "Fiscal calendar changes, recasts, and incomplete quarters can prevent reconciliation; exceptions are not hidden.",
                "Non-USD amounts remain in reporting currency; no currency conversion is performed.",
            ],
        },
    }


def fetch_company_sec(cache: SourceCache, identity: Mapping[str, Any]) -> Dict[str, Any]:
    cik = str(identity["cik"])
    submissions, _metadata = cache.fetch_json(
        "S-SEC-SUBMISSIONS", f"https://data.sec.gov/submissions/CIK{cik}.json",
        provider=SEC_PROVIDER, authoritative=True, description="Registrant identity and recent filing timeline",
        evidence_class="regulatory filing metadata", ttl_hours=6,
    )
    if str(int(submissions.get("cik", 0))).zfill(10) != cik:
        raise ValueError("Resolved CIK does not match SEC submissions")
    filings = filing_timeline(submissions, cik)
    if not any(filing["form"] == "10-K" for filing in filings):
        raise ValueError("No recent Form 10-K found. Non-US/IFRS and private-company connectors are not supported by this profile")
    facts, _metadata = cache.fetch_json(
        "S-SEC-COMPANYFACTS", f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
        provider=SEC_PROVIDER, authoritative=True, description="Consolidated US-GAAP XBRL company facts",
        evidence_class="regulatory XBRL facts", ttl_hours=6,
    )
    normalized = normalize_registrant(facts, submissions, filings)
    annuals = [filing for filing in filings if filing["form"] == "10-K"]
    quarters = [filing for filing in filings if filing["form"] == "10-Q"]
    texts = {}
    retrieval_gaps = []
    selected = [("S-SEC-10K", annuals[0])]
    if len(annuals) > 1:
        selected.append(("S-SEC-10K-PRIOR", annuals[1]))
    if quarters:
        selected.append(("S-SEC-10Q-LATEST", quarters[0]))
    for source_id, filing in selected:
        try:
            document, _metadata = cache.fetch_text(
                source_id, filing["url"], provider=SEC_PROVIDER, authoritative=True,
                description=f"{submissions['name']} {filing['form']}, period ended {filing['reportDate']}",
                evidence_class="filed annual report" if filing["form"] == "10-K" else "filed interim report",
                ttl_hours=168,
            )
            texts[source_id] = visible_text(document).casefold()
        except RuntimeError as error:
            if cache.mode == "cache-only":
                raise
            retrieval_gaps.append({"source_id": source_id, "reason": str(error)})
    topics = {
        "Liquidity": ("liquidity", "cash flows"),
        "Cybersecurity": ("cybersecurity", "cyberattacks"),
        "Competition": ("competition", "competitive"),
        "Tax": ("income taxes", "tax positions"),
        "Regulation": ("regulatory", "antitrust"),
        "Artificial intelligence": ("artificial intelligence",),
    }
    comparison_available = "S-SEC-10K" in texts and "S-SEC-10K-PRIOR" in texts
    disclosure_changes = [
        {
            "topic": topic,
            "prior_hits": sum(texts["S-SEC-10K-PRIOR"].count(phrase) for phrase in phrases),
            "current_hits": sum(texts["S-SEC-10K"].count(phrase) for phrase in phrases),
            "source_ids": ["S-SEC-10K-PRIOR", "S-SEC-10K"],
        }
        for topic, phrases in topics.items()
    ] if comparison_available else []
    normalized["coverage"]["retrieval_gaps"] = retrieval_gaps
    normalized["coverage"]["disclosure_comparison_available"] = comparison_available
    return {
        "company": {"cik": cik, "name": submissions["name"], "tickers": sorted(submissions.get("tickers", []))},
        "filing": annuals[0],
        "latest_interim_filing": quarters[0] if quarters else None,
        "filing_timeline": filings[:30],
        "disclosure_changes": disclosure_changes,
        **normalized,
    }