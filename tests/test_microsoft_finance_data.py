"""Offline parser and cache tests for the live Microsoft finance example."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
if str(EXAMPLES) not in sys.path:
    sys.path.insert(0, str(EXAMPLES))

from microsoft_finance_data import (  # noqa: E402
    SourceCache,
    annual_series,
    annual_series_with_lineage,
    compare_qualitative_disclosures,
    fetch_microsoft_investor_relations,
    inline_xbrl_member_data,
    instant_series,
    normalize_company_facts_with_lineage,
    normalize_quarterly_facts,
    recent_filing_timeline,
)


def _annual(value: int, start: str, end: str, filed: str, accession: str) -> dict:
    return {
        "start": start,
        "end": end,
        "val": value,
        "form": "10-K",
        "fp": "FY",
        "filed": filed,
        "accn": accession,
    }


def _instant(value: int, end: str, filed: str, accession: str) -> dict:
    return {
        "end": end,
        "val": value,
        "form": "10-K",
        "fp": "FY",
        "filed": filed,
        "accn": accession,
    }


def test_annual_series_merges_taxonomy_aliases_and_uses_newest_recast() -> None:
    facts = {
        "facts": {"us-gaap": {
            "OldRevenue": {"units": {"USD": [
                _annual(100_000_000_000, "2023-01-01", "2023-12-31", "2024-02-01", "old"),
                _annual(110_000_000_000, "2024-01-01", "2024-12-31", "2025-02-01", "old-first"),
            ]}},
            "NewRevenue": {"units": {"USD": [
                _annual(111_000_000_000, "2024-01-01", "2024-12-31", "2026-02-01", "new-recast"),
                _annual(125_000_000_000, "2025-01-01", "2025-12-31", "2026-02-01", "new"),
            ]}},
        }}
    }

    result = annual_series(facts, ("OldRevenue", "NewRevenue"), years=5)

    assert result == {"2023": 100.0, "2024": 111.0, "2025": 125.0}


def test_annual_lineage_exposes_selected_concept_accession_and_recast_chain() -> None:
    facts = {
        "facts": {"us-gaap": {"Revenue": {"units": {"USD": [
            _annual(100_000_000_000, "2024-07-01", "2025-06-30", "2025-07-30", "original"),
            _annual(101_000_000_000, "2024-07-01", "2025-06-30", "2026-07-30", "recast"),
        ]}}}},
    }

    result = annual_series_with_lineage(facts, "revenue", ("Revenue",))
    selected = result["2025"]

    assert selected["namespace"] == "us-gaap"
    assert selected["concept"] == "Revenue"
    assert selected["accession"] == "recast"
    assert selected["value"] == 101.0
    assert selected["value_changed_in_recast_chain"] is True
    assert [item["selected"] for item in selected["recast_chain"]] == [False, True]


def _duration(
    value: int, start: str, end: str, filed: str, accession: str,
    *, form: str = "10-Q", fp: str = "Q1",
) -> dict:
    return {
        "start": start,
        "end": end,
        "val": value,
        "form": form,
        "fp": fp,
        "filed": filed,
        "accn": accession,
    }


def test_quarterly_normalizer_derives_cumulative_periods_q4_and_ttm() -> None:
    facts = {
        "cik": 789019,
        "facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                _duration(10_000_000_000, "2024-07-01", "2024-09-30", "2024-10-20", "q1-original"),
                _duration(11_000_000_000, "2024-07-01", "2024-09-30", "2025-10-20", "q1-recast"),
                _duration(30_000_000_000, "2024-07-01", "2024-12-31", "2025-01-20", "q2-ytd", fp="Q2"),
                _duration(48_000_000_000, "2024-07-01", "2025-03-31", "2025-04-20", "q3-ytd", fp="Q3"),
                _duration(70_000_000_000, "2024-07-01", "2025-06-30", "2025-07-20", "fy", form="10-K", fp="FY"),
            ]}},
        }},
    }

    result = normalize_quarterly_facts(facts, quarters=8)

    assert result["quarterly"]["revenue"] == {
        "FY2025 Q1": 11.0,
        "FY2025 Q2": 19.0,
        "FY2025 Q3": 18.0,
        "FY2025 Q4": 22.0,
    }
    assert result["quarterly_lineage"]["revenue"]["FY2025 Q2"]["derived"] is True
    assert result["quarterly_lineage"]["revenue"]["FY2025 Q4"]["selection_method"] == "derived_q4_annual_less_q3_ytd"
    assert len(result["quarterly_lineage"]["revenue"]["FY2025 Q4"]["inputs"]) == 2
    assert result["ttm"]["revenue"] == 70.0
    assert result["ttm_as_of"] == "FY2025 Q4"
    check = next(item for item in result["reconciliation"] if item["metric"] == "revenue")
    assert check["variance"] == 0.0 and check["within_tolerance"] is True


def test_calendar_year_quarters_do_not_use_microsoft_fiscal_calendar() -> None:
    facts = {"cik": 1652044, "facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            _duration(10_000_000_000, "2025-01-01", "2025-03-31", "2025-04-25", "first"),
            _duration(23_000_000_000, "2025-01-01", "2025-06-30", "2025-07-25", "second", fp="Q2"),
            _duration(39_000_000_000, "2025-01-01", "2025-09-30", "2025-10-25", "third", fp="Q3"),
            _duration(60_000_000_000, "2025-01-01", "2025-12-31", "2026-02-01", "annual", form="10-K", fp="FY"),
        ]}},
    }}}

    result = normalize_quarterly_facts(facts, fiscal_year_end_month=12)

    assert result["quarterly"]["revenue"] == {
        "FY2025 Q1": 10.0, "FY2025 Q2": 13.0,
        "FY2025 Q3": 16.0, "FY2025 Q4": 21.0,
    }
    assert result["ttm_as_of"] == "FY2025 Q4"
    assert result["ttm"]["revenue"] == 60.0
    assert result["reconciliation"][0]["within_tolerance"] is True


def test_recent_filing_timeline_and_disclosure_delta_are_explicit() -> None:
    submissions = {"filings": {"recent": {
        "form": ["8-K", "10-K", "10-Q"],
        "filingDate": ["2026-08-01", "2026-07-29", "2026-04-29"],
        "reportDate": ["2026-08-01", "2026-06-30", "2026-03-31"],
        "acceptanceDateTime": ["", "", ""],
        "accessionNumber": ["0001-26-000003", "0001-26-000002", "0001-26-000001"],
        "primaryDocument": ["event.htm", "annual.htm", "quarter.htm"],
        "isXBRL": [0, 1, 1],
        "items": ["8.01", "", ""],
        "size": [1, 2, 3],
    }}}

    timeline = recent_filing_timeline(submissions)
    current = {
        "risk_topics": [{"topic": "AI capacity", "present": True, "keyword_hits": 4}],
        "audit": {"critical_audit_matters": ["Revenue recognition"]},
        "tax": {"irs_nopa_claim_usd_b": 28.9},
        "operations": {"microsoft_cloud_gross_margin_pct": 0.67},
    }
    prior = {
        "risk_topics": [{"topic": "AI capacity", "present": True, "keyword_hits": 1}],
        "audit": {"critical_audit_matters": ["Revenue recognition"]},
        "tax": {"irs_nopa_claim_usd_b": 28.9},
        "operations": {"microsoft_cloud_gross_margin_pct": 0.69},
    }
    delta = compare_qualitative_disclosures(
        current, prior, current_filing=timeline[1], prior_filing=timeline[2]
    )

    assert [item["form"] for item in timeline] == ["8-K", "10-K", "10-Q"]
    assert timeline[0]["material_event"] is True
    assert delta["comparison_type"].startswith("deterministic topic-level")
    assert any(item["topic"] == "AI capacity" and item["change"] == "expanded signal" for item in delta["changes"])
    assert any(item["topic"] == "Microsoft Cloud gross margin" for item in delta["changes"])


def test_instant_series_filters_microsoft_to_june_year_end() -> None:
    facts = {
        "cik": 789019,
        "facts": {"us-gaap": {"Assets": {"units": {"USD": [
            _instant(500_000_000_000, "2025-03-31", "2025-07-01", "quarter-comparative"),
            _instant(600_000_000_000, "2025-06-30", "2025-07-29", "annual"),
        ]}}}},
    }

    result = instant_series(facts, ("Assets",), years=3)

    assert result == {"2025": 600.0}


def test_inline_xbrl_extracts_segment_and_product_members() -> None:
    document = """
    <html><body>
      <xbrli:context id="segment-context">
        <xbrli:period><xbrli:startDate>2025-07-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period>
        <xbrldi:explicitMember dimension="msft:SegmentAxis">msft:IntelligentCloudMember</xbrldi:explicitMember>
      </xbrli:context>
      <xbrli:context id="product-context">
        <xbrli:period><xbrli:startDate>2025-07-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period>
        <xbrldi:explicitMember dimension="msft:ProductAxis">msft:ServerProductsAndCloudServicesMember</xbrldi:explicitMember>
      </xbrli:context>
      <ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="segment-context" scale="6">137,791</ix:nonFraction>
      <ix:nonFraction name="us-gaap:OperatingIncomeLoss" contextRef="segment-context" scale="6">56,975</ix:nonFraction>
      <ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="product-context" scale="6">129,365</ix:nonFraction>
    </body></html>
    """

    result = inline_xbrl_member_data(document)

    assert result["segments"]["Intelligent Cloud"]["2026"]["revenue"] == 137.791
    assert result["segments"]["Intelligent Cloud"]["2026"]["operating_income"] == 56.975
    assert result["products"]["Server products and cloud services"]["2026"]["revenue"] == 129.365
    selected = result["segment_lineage"]["Intelligent Cloud"]["2026"]["revenue"]
    assert selected["fact_id"].startswith("fact_") and len(selected["fact_id"]) == 69
    assert selected["source_id"] == "S-SEC-10K"
    assert result["inline_fact_index"][selected["fact_id"]] == selected


class _EarningsFixtureCache:
    def fetch_text(self, *_args, **_kwargs):
        return """
        <html><body>
        <h1>Quarter ended June 30, 2026</h1>
        <p>Revenue was $90.0 billion and increased 18%.</p>
        <p>Operating income was $40.6 billion and increased 18%.</p>
        <p>Net income was $35.8 billion and increased 31%.</p>
        <p>Diluted earnings per share was $4.81 and increased 32%.</p>
        <h2>Fiscal Year 2026 Results</h2>
        <p>Revenue was $331.8 billion and increased 18%.</p>
        <p>Operating income was $155.2 billion and increased 21%.</p>
        <p>Net income was $133.7 billion and increased 31%.</p>
        <p>Diluted earnings per share was $17.95 and increased 32%.</p>
        <p>Azure revenue surpassed $100 billion.</p>
        <p>Microsoft 365 Copilot reached over 30 million paid seats.</p>
        <p>Microsoft Cloud revenue was $59.3 billion, up 27%.</p>
        <p>commercial remaining performance obligation increased 84% to $678 billion.</p>
        <p>Azure and other cloud services revenue increased 43%.</p>
        <p>increase in net income and diluted earnings per share of $480 million and $0.07, respectively, in the fourth quarter, and $4,963 million and $0.67, respectively, for the full fiscal year.</p>
        </body></html>
        """, {}


def test_investor_relations_parser_selects_full_year_not_quarter() -> None:
    result = fetch_microsoft_investor_relations(_EarningsFixtureCache(), 2026)

    assert result["metrics"]["fiscal_revenue_usd_b"] == 331.8
    assert result["metrics"]["fiscal_operating_income_usd_b"] == 155.2
    assert result["metrics"]["fiscal_net_income_usd_b"] == 133.7
    assert result["metrics"]["fiscal_diluted_eps"] == 17.95
    assert result["metrics"]["openai_net_income_impact_usd_b"] == 4.963


def test_content_addressed_cache_replays_valid_bytes(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    body = b'{"answer":42}'
    digest = hashlib.sha256(body).hexdigest()
    relative = Path("raw") / f"fixture--{digest[:16]}.json"
    (tmp_path / relative).write_bytes(body)
    (tmp_path / "index.json").write_text(json.dumps({
        "S-FIXTURE": {
            "source_id": "S-FIXTURE",
            "cache_path": relative.as_posix(),
            "sha256": digest,
            "retrieved_at": "2026-01-01T00:00:00+00:00",
            "url": "https://example.invalid/fixture.json",
        }
    }), encoding="utf-8")
    cache = SourceCache(tmp_path, mode="cache-only")

    payload, metadata = cache.fetch_json(
        "S-FIXTURE",
        "https://example.invalid/fixture.json",
        provider="Fixture",
        authoritative=True,
        description="offline fixture",
        evidence_class="test evidence",
    )

    assert payload == {"answer": 42}
    assert metadata["sha256"] == digest
    assert metadata["retrieval_mode"] == "cache"
    assert metadata["evidence_class"] == "test evidence"


def test_content_addressed_cache_rejects_corruption(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    body = b"original"
    digest = hashlib.sha256(body).hexdigest()
    relative = Path("raw") / "fixture.json"
    (tmp_path / relative).write_bytes(b"corrupted")
    (tmp_path / "index.json").write_text(json.dumps({
        "S-FIXTURE": {
            "source_id": "S-FIXTURE",
            "cache_path": relative.as_posix(),
            "sha256": digest,
            "retrieved_at": "2026-01-01T00:00:00+00:00",
        }
    }), encoding="utf-8")
    cache = SourceCache(tmp_path, mode="cache-only")

    with pytest.raises(RuntimeError, match="no valid entry"):
        cache.fetch_json(
            "S-FIXTURE",
            "https://example.invalid/fixture.json",
            provider="Fixture",
            authoritative=True,
            description="offline fixture",
        )


def test_content_addressed_cache_rejects_url_mismatch(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    body = b'{"answer":42}'
    digest = hashlib.sha256(body).hexdigest()
    relative = Path("raw") / "fixture.json"
    (tmp_path / relative).write_bytes(body)
    (tmp_path / "index.json").write_text(json.dumps({
        "S-FIXTURE": {
            "source_id": "S-FIXTURE",
            "cache_path": relative.as_posix(),
            "sha256": digest,
            "retrieved_at": "2026-01-01T00:00:00+00:00",
            "url": "https://example.invalid/original.json",
        }
    }), encoding="utf-8")
    cache = SourceCache(tmp_path, mode="cache-only")

    with pytest.raises(RuntimeError, match="cached URL does not match"):
        cache.fetch_json(
            "S-FIXTURE",
            "https://example.invalid/replacement.json",
            provider="Fixture",
            authoritative=True,
            description="offline fixture",
        )


def test_normalized_fact_inventory_indexes_every_derivation_input() -> None:
    facts = {
        "cik": 789019,
        "facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                _duration(11_000_000_000, "2024-07-01", "2024-09-30", "2024-10-20", "q1"),
                _duration(30_000_000_000, "2024-07-01", "2024-12-31", "2025-01-20", "q2-ytd", fp="Q2"),
                _duration(48_000_000_000, "2024-07-01", "2025-03-31", "2025-04-20", "q3-ytd", fp="Q3"),
                _duration(70_000_000_000, "2024-07-01", "2025-06-30", "2025-07-20", "fy", form="10-K", fp="FY"),
            ]}},
        }},
    }

    normalized = normalize_company_facts_with_lineage(facts, quarters=8)
    index = normalized["fact_index"]

    assert index
    assert all(key == value["fact_id"] and len(key) == 69 for key, value in index.items())
    for record in index.values():
        for input_record in record.get("inputs", []):
            assert input_record["fact_id"] in index
