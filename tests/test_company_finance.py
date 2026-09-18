"""Offline contracts for company-parameterized finance intelligence."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import pytest


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
if str(EXAMPLES) not in sys.path:
    sys.path.insert(0, str(EXAMPLES))

from company_finance_data import filing_timeline, normalize_registrant, select_company
from company_finance_intelligence import release_content, run_company, validate_package
from autarch.review import ReleaseReviewStore, canonical_sha256


DIRECTORY = {
    "0": {"cik_str": 1652044, "title": "Alphabet Inc.", "ticker": "GOOGL"},
    "1": {"cik_str": 1652044, "title": "Alphabet Inc.", "ticker": "GOOG"},
    "2": {"cik_str": 789019, "title": "MICROSOFT CORP", "ticker": "MSFT"},
    "3": {"cik_str": 101, "title": "Example Finance Inc.", "ticker": "EXFI"},
    "4": {"cik_str": 102, "title": "Example Energy Inc.", "ticker": "EXEN"},
}


def _fixture(currency="USD"):
    facts = {"cik": 1652044, "facts": {"us-gaap": {}}}
    for concept, multiplier in (
        ("RevenueFromContractWithCustomerExcludingAssessedTax", 1),
        ("OperatingIncomeLoss", 0.4), ("NetIncomeLoss", 0.3),
        ("NetCashProvidedByUsedInOperatingActivities", 0.5),
        ("PaymentsToAcquirePropertyPlantAndEquipment", 0.2),
    ):
        rows = []
        for year in (2024, 2025):
            for quarter, end, total in ((1, "03-31", 10), (2, "06-30", 23), (3, "09-30", 39), (4, "12-31", 60)):
                rows.append({
                    "start": f"{year}-01-01", "end": f"{year}-{end}",
                    "val": total * multiplier * 1e9, "fy": year + 1,
                    "form": "10-K" if quarter == 4 else "10-Q",
                    "fp": "FY" if quarter == 4 else f"Q{quarter}",
                    "filed": f"{year + 1}-02-01",
                    "accn": f"0001652044-{str(year)[2:]}-00000{quarter}",
                })
        facts["facts"]["us-gaap"][concept] = {"units": {currency: rows}}
    facts["facts"]["us-gaap"]["Assets"] = {"units": {currency: [
        {"end": "2025-12-31", "val": 200e9, "form": "10-K", "fp": "FY", "filed": "2026-02-01", "accn": "0001652044-26-000001"},
        {"end": "2025-09-30", "val": 150e9, "form": "10-K", "fp": "FY", "filed": "2026-02-01", "accn": "0001652044-26-000001"},
    ]}}
    facts["facts"]["us-gaap"]["SellingAndMarketingExpense"] = {"units": {currency: [{
        "start": "2017-01-01", "end": "2017-12-31", "val": 1e9,
        "form": "10-K", "fp": "FY", "filed": "2018-02-01", "accn": "0001652044-18-000001",
    }]}}
    submissions = {
        "cik": "1652044", "name": "Alphabet Inc.", "tickers": ["GOOG", "GOOGL"], "fiscalYearEnd": "1231",
        "filings": {"recent": {
            "form": ["10-K", "10-K", "10-Q"],
            "reportDate": ["2025-12-31", "2024-12-31", "2025-09-30"],
            "filingDate": ["2026-02-01", "2025-02-01", "2025-10-25"],
            "accessionNumber": ["0001652044-26-000001", "0001652044-25-000001", "0001652044-25-000003"],
            "primaryDocument": ["annual.htm", "prior.htm", "quarter.htm"],
        }},
    }
    return facts, submissions


@pytest.mark.parametrize("query", ["Alphabet", "Alphabet Inc.", "googl", "GOOG"])
def test_resolve_names_and_share_classes_to_one_registrant(query):
    company = select_company(DIRECTORY, query)
    assert company["cik"] == "0001652044"
    assert company["tickers"] == ["GOOG", "GOOGL"]


def test_resolution_rejects_ambiguous_names_and_subsidiary_brands():
    with pytest.raises(ValueError, match="Ambiguous.*EXEN.*EXFI"):
        select_company(DIRECTORY, "Example")
    with pytest.raises(ValueError, match="No SEC-listed registrant"):
        select_company(DIRECTORY, "GitHub")


@pytest.mark.parametrize("currency", ["USD", "EUR"])
def test_normalization_uses_company_calendar_currency_and_year_end(currency):
    facts, submissions = _fixture(currency)
    timeline = filing_timeline(submissions, "0001652044")
    result = normalize_registrant(facts, submissions, timeline)
    assert "/1652044/" in timeline[0]["url"]
    assert result["currency"] == currency
    assert result["quarterly"]["revenue"]["FY2025 Q4"] == 21.0
    assert result["ttm"]["free_cash_flow"] == 18.0
    assert result["balance_sheet"]["total_assets"] == {"2025": 200.0}
    assert all(check["within_tolerance"] for check in result["reconciliation"])
    assert all(record["cik"] == "0001652044" for record in result["fact_index"].values())
    assert all(
        input_fact["fact_id"] in result["fact_index"]
        for fact in result["fact_index"].values() for input_fact in fact.get("inputs", [])
    )


def test_missing_capex_is_not_zero_or_free_cash_flow():
    facts, submissions = _fixture()
    facts["facts"]["us-gaap"].pop("PaymentsToAcquirePropertyPlantAndEquipment")
    result = normalize_registrant(facts, submissions, filing_timeline(submissions, "0001652044"))
    assert result["annual"]["capex"] == {}
    assert result["quarterly"]["free_cash_flow"] == {}
    assert result["ttm"]["free_cash_flow_margin"] is None
    assert any(item["metric"] == "capex" for item in result["coverage"]["missing_metrics"])


def test_direct_quarter_alias_cannot_override_the_annual_accounting_measure():
    facts, submissions = _fixture()
    concepts = facts["facts"]["us-gaap"]
    concepts["ShareBasedCompensation"] = copy.deepcopy(concepts["NetCashProvidedByUsedInOperatingActivities"])
    concepts["AllocatedShareBasedCompensationExpense"] = {"units": {"USD": [{
        "start": "2025-04-01", "end": "2025-06-30", "val": 99e9,
        "form": "10-Q", "fp": "Q2", "filed": "2026-06-01", "accn": "0001652044-26-000009",
    }]}}
    result = normalize_registrant(facts, submissions, filing_timeline(submissions, "0001652044"))
    assert result["quarterly"]["stock_compensation"]["FY2025 Q2"] == 6.5
    assert all(check["within_tolerance"] for check in result["reconciliation"])


def test_cross_company_and_unsupported_forms_fail_explicitly():
    facts, submissions = _fixture()
    wrong_company = copy.deepcopy(facts)
    wrong_company["cik"] = 789019
    with pytest.raises(ValueError, match="different SEC registrants"):
        normalize_registrant(wrong_company, submissions, filing_timeline(submissions, "0001652044"))
    with pytest.raises(ValueError, match="No recent Form 10-K"):
        normalize_registrant(facts, submissions, [{"form": "20-F"}])


def _prime_cache(root, sources):
    (root / "raw").mkdir(parents=True, exist_ok=True)
    index = {}
    for source_id, url, value in sources:
        body = (json.dumps(value) if isinstance(value, dict) else value).encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        relative = f"raw/{source_id}-{digest}.txt"
        (root / relative).write_bytes(body)
        index[source_id] = {
            "source_id": source_id, "url": url, "sha256": digest, "cache_path": relative,
            "retrieved_at": "2026-09-01T00:00:00+00:00", "bytes": len(body),
        }
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")


def _prime_company(root, cik="0001652044", *, changed=False):
    from company_finance_data import DIRECTORY_URL

    _prime_cache(root / "registry", [("S-SEC-TICKERS", DIRECTORY_URL, DIRECTORY)])
    facts, submissions = _fixture()
    facts["cik"] = int(cik)
    submissions["cik"] = cik
    if cik != "0001652044":
        submissions.update(name="MICROSOFT CORP", tickers=["MSFT"])
    if changed:
        for row in facts["facts"]["us-gaap"]["OperatingIncomeLoss"]["units"]["USD"]:
            row["val"] *= 1.1
    timeline = filing_timeline(submissions, cik)
    annuals = [item for item in timeline if item["form"] == "10-K"]
    quarter = next(item for item in timeline if item["form"] == "10-Q")
    _prime_cache(root / f"CIK{cik}" / "cache", [
        ("S-SEC-SUBMISSIONS", f"https://data.sec.gov/submissions/CIK{cik}.json", submissions),
        ("S-SEC-COMPANYFACTS", f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", facts),
        ("S-SEC-10K", annuals[0]["url"], "<html><body>Liquidity and competition. Artificial intelligence.</body></html>"),
        ("S-SEC-10K-PRIOR", annuals[1]["url"], "<html><body>Liquidity and competition.</body></html>"),
        ("S-SEC-10Q-LATEST", quarter["url"], "<html><body>Interim report.</body></html>"),
    ])


class _ReportLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.duplicate_ids = set()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            if attributes["id"] in self.ids:
                self.duplicate_ids.add(attributes["id"])
            self.ids.add(attributes["id"])
        if tag == "a":
            self.links.append(attributes.get("href", ""))


def test_offline_end_to_end_replay_binds_review_and_retains_evidence(tmp_path):
    _prime_company(tmp_path)
    first = run_company(company="Alphabet Inc.", mode="cache-only", workspace=tmp_path)
    second = run_company(ticker="GOOGL", mode="cache-only", workspace=tmp_path)

    assert first["run_id"] != second["run_id"]
    assert first["governance"]["release_digest"] == second["governance"]["release_digest"]
    assert first["governance"]["review"]["id"] == second["governance"]["review"]["id"]
    assert second["governance"]["review"]["status"] == "pending"
    assert second["governance"]["external_release_authorized"] is False
    assert second["governance"]["action_chain"] == {"verified": True, "broken": None, "records": 8}
    assert validate_package(second)["passed"]
    output = tmp_path / "CIK0001652044" / "runs" / second["run_id"] / "outputs"
    parser = _ReportLinks()
    document = (output / "financial_report.html").read_text(encoding="utf-8")
    parser.feed(document)
    assert "<td>2017</td>" not in document
    assert 'data-theme="dark"' in document
    assert 'id="cockpit"' in document
    assert document.count("class='kpi'") == 6
    assert document.count("class='viz'") == 3
    assert 'Rolling trailing-twelve-month results' in document
    assert not parser.duplicate_ids
    assert {"analysisStatus", "analysisCount", "analysisEmpty", "factEmpty", "governance"} <= parser.ids
    assert "No external publication or trading authority is granted." in document
    assert "company-specific valuations, peers, and operating KPIs are not included" in document
    assert all(link[1:] in parser.ids for link in parser.links if link.startswith("#"))
    assert all((output / link).is_file() for link in parser.links if link and not link.startswith(("#", "http")))
    assert hashlib.sha256((output / "release_content.json").read_bytes()).hexdigest() == second["governance"]["release_digest"]
    with zipfile.ZipFile(output / "evidence_bundle.zip") as archive:
        manifest = json.loads(archive.read("bundle_manifest.json"))
        assert set(archive.namelist()) == set(manifest["members"]) | {"bundle_manifest.json"}
        for member, metadata in manifest["members"].items():
            assert hashlib.sha256(archive.read(member)).hexdigest() == metadata["sha256"]
    for name, metadata in json.loads((output / "artifact_manifest.json").read_text()).items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == metadata["sha256"]


def test_company_outputs_are_isolated_and_changed_content_supersedes_review(tmp_path):
    _prime_company(tmp_path)
    alphabet = run_company(ticker="GOOG", mode="cache-only", workspace=tmp_path)
    prior_digest = alphabet["governance"]["release_digest"]
    _prime_company(tmp_path, "0000789019")
    microsoft = run_company(ticker="MSFT", mode="cache-only", workspace=tmp_path)
    assert microsoft["data"]["company"]["cik"] == "0000789019"
    assert microsoft["governance"]["release_digest"] != prior_digest
    assert microsoft["governance"]["review_database"] != alphabet["governance"]["review_database"]
    store = ReleaseReviewStore(alphabet["governance"]["review_database"])
    old_review = alphabet["governance"]["review"]["id"]
    store.approve(old_review, "finance-review-lead")
    store.approve(old_review, "risk-review-lead")
    _prime_company(tmp_path, changed=True)
    changed = run_company(ticker="GOOGL", mode="cache-only", workspace=tmp_path)
    assert changed["governance"]["release_digest"] != prior_digest
    assert changed["governance"]["review"]["status"] == "pending"
    assert store.get(old_review).status == "superseded"
    assert canonical_sha256(release_content(alphabet)) == prior_digest


def test_original_entry_point_routes_company_selection_without_mutating_defaults(monkeypatch):
    import company_finance_intelligence as generic
    import microsoft_finance_intelligence as microsoft

    calls = []
    monkeypatch.setattr(generic, "main", lambda argv: calls.append(argv))
    monkeypatch.setattr(sys, "argv", ["microsoft_finance_intelligence.py", "--company", "Alphabet Inc.", "--mode", "cache-only"])
    microsoft.main()
    assert calls[0][:4] == ["--company", "Alphabet Inc.", "--mode", "cache-only"]
    assert microsoft.TICKER == "MSFT"