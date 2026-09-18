"""Focused contracts for Microsoft Quarterly & Change Intelligence v1.1."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
if str(EXAMPLES) not in sys.path:
    sys.path.insert(0, str(EXAMPLES))

from autarch.review import ReleaseReviewStore, canonical_sha256  # noqa: E402
from microsoft_finance_intelligence import (  # noqa: E402
    ANALYSIS_COMPONENTS,
    AUDIENCES,
    _component,
    _release_content,
    _review_status,
    _validate_package,
    filing_change_detection,
    quarterly_change_intelligence,
)
from microsoft_finance_report import _fact_lineage, _review_panel  # noqa: E402


def _release_fixture():
    data = {
        "sec": {
            "filing": {"accessionNumber": "annual", "fiscal_year": "FY2026"},
            "latest_quarter_filing": {"accessionNumber": "quarter"},
            "fact_index": {"fact_1": {"value": 1}},
        }
    }
    sources = {
        "S-ONE": {
            "sha256": "a" * 64,
            "url": "https://example.invalid/one",
            "evidence_class": "regulatory XBRL facts",
            "authoritative": True,
            "retrieved_at": "2026-01-01T00:00:00+00:00",
            "accessed_at": "2026-01-01T00:00:00+00:00",
        }
    }
    analyses = [{"output": {"component": "one", "value": 7}, "evidence_id": "why_1"}]
    synthesis = {"summary": "stable", "as_of": "first", "evidence_chain": ["why_1"]}
    reports = {
        "owner": {
            "decision": "review",
            "evidence_chain": ["why_1"],
            "review_id": "review_1",
            "review_status": "pending",
            "release_authorized": False,
        }
    }
    return data, sources, analyses, synthesis, reports


def test_release_digest_ignores_volatile_metadata_but_binds_source_url() -> None:
    data, sources, analyses, synthesis, reports = _release_fixture()
    first = canonical_sha256(_release_content(data, sources, analyses, synthesis, reports))

    sources["S-ONE"]["retrieved_at"] = "2026-02-01T00:00:00+00:00"
    sources["S-ONE"]["accessed_at"] = "2026-02-02T00:00:00+00:00"
    synthesis["as_of"] = "second"
    synthesis["evidence_chain"] = ["why_2"]
    reports["owner"].update(
        review_id="review_2", review_status="approved", release_authorized=True
    )
    second = canonical_sha256(_release_content(data, sources, analyses, synthesis, reports))

    assert second == first
    sources["S-ONE"]["url"] = "https://example.invalid/replacement"
    assert canonical_sha256(_release_content(data, sources, analyses, synthesis, reports)) != first


def test_review_state_separates_digest_match_from_authorization(tmp_path: Path) -> None:
    digest = canonical_sha256({"release": 1})
    store = ReleaseReviewStore(tmp_path / "reviews.db")
    review = store.submit("release", "Release", digest, ["owner"])

    pending = _review_status(review, digest)
    assert pending["artifact_digest_matches"] is True
    assert pending["content_binding_valid"] is True
    assert pending["approval_valid_for_digest"] is False
    assert pending["release_authorized"] is False

    mismatch = _review_status(review, canonical_sha256({"release": 2}))
    assert mismatch["artifact_digest_matches"] is False
    assert mismatch["content_binding_valid"] is False
    assert mismatch["release_authorized"] is False

    approved = _review_status(store.approve(review.id, "owner"), digest)
    assert approved["approval_valid_for_digest"] is True
    assert approved["release_authorized"] is True


def _quarterly_sec_fixture() -> dict:
    periods = [f"FY{year} Q{quarter}" for year in (2025, 2026) for quarter in range(1, 5)]
    values = {
        "revenue": {period: 50.0 + index * 4 for index, period in enumerate(periods)},
        "operating_income": {period: 20.0 + index * 2 for index, period in enumerate(periods)},
        "operating_cash_flow": {period: 22.0 + index * 2 for index, period in enumerate(periods)},
        "capex": {period: 8.0 + index for index, period in enumerate(periods)},
    }
    values["free_cash_flow"] = {
        period: values["operating_cash_flow"][period] - values["capex"][period]
        for period in periods
    }
    lineage = {
        metric: {
            period: {
                "fact_id": f"fact_{metric}_{period.replace(' ', '_')}",
                "derived": period.endswith("Q4"),
            }
            for period in periods
        }
        for metric in values
    }
    latest = periods[-1]
    ttm_values = {
        "revenue": 290.0,
        "operating_income": 130.0,
        "net_income": 110.0,
        "operating_cash_flow": 145.0,
        "capex": 58.0,
        "free_cash_flow": 87.0,
        "operating_margin": 130.0 / 290.0,
        "free_cash_flow_margin": 87.0 / 290.0,
        "capex_intensity": 58.0 / 290.0,
    }
    ttm_lineage = {
        metric: {latest: {"fact_id": f"fact_ttm_{metric}"}}
        for metric in (
            "revenue", "operating_income", "net_income", "operating_cash_flow",
            "capex", "free_cash_flow",
        )
    }
    quarter_ids = [lineage["revenue"][period]["fact_id"] for period in periods[-4:]]
    return {
        "quarterly": values,
        "quarterly_lineage": lineage,
        "ttm": ttm_values,
        "ttm_series": {metric: {latest: value} for metric, value in ttm_values.items()},
        "ttm_lineage": ttm_lineage,
        "ttm_as_of": latest,
        "reconciliation": [{
            "metric": "revenue",
            "fiscal_year": "FY2026",
            "within_tolerance": True,
            "annual_fact_id": "fact_annual_revenue_2026",
            "quarter_fact_ids": quarter_ids,
        }],
    }


def test_new_specialists_emit_reviewable_contracts() -> None:
    quarterly = quarterly_change_intelligence(_quarterly_sec_fixture())
    assert quarterly["component"] == "quarterly_change_intelligence"
    assert quarterly["metrics"]["latest_period"] == "FY2026 Q4"
    assert len(quarterly["findings"]) == 3
    assert all(finding["fact_ids"] for finding in quarterly["findings"])

    filing = filing_change_detection({
        "filing_changes": {
            "current_filing": {"form": "10-K", "filing_date": "2026-07-29"},
            "prior_filing": {"form": "10-K", "filing_date": "2025-07-30"},
            "summary": {"changed_signals": 1, "high_attention": 0, "watch": 1},
            "changes": [{
                "topic": "Cloud margin",
                "change": "reported metric changed",
                "prior_value": 0.69,
                "current_value": 0.66,
                "basis": "deterministic extraction",
                "severity": "watch",
                "evidence_refs": ["S-SEC-10K", "S-SEC-10K-PRIOR"],
            }],
            "limitations": ["Professional redline required."],
        },
        "filing_timeline": [],
    })
    assert filing["component"] == "filing_change_detection"
    assert len(filing["findings"]) == 3
    assert filing["findings"][0]["kind"] == "disclosure comparison"


def test_component_rejects_invalid_confidence() -> None:
    with pytest.raises(ValueError, match="confidence"):
        _component(
            "bad", "Bad", "Bad", "Bad", 1.1, {},
            [{"fact_ids": []}], evidence=["S"], caveats=["c"], questions=["q"],
        )


def _package_fixture() -> dict:
    source_id = "S-ONE"
    fact_id = "fact_" + "b" * 64
    sources = {
        source_id: {
            "source_id": source_id,
            "sha256": "a" * 64,
            "url": "https://example.invalid/source",
            "evidence_class": "regulatory XBRL facts",
            "authoritative": True,
        }
    }
    finding = {
        "headline": "Traceable",
        "detail": "Traceable",
        "severity": "neutral",
        "kind": "reported",
        "evidence_refs": [source_id],
        "fact_ids": [fact_id],
        "fact_lineage_applicable": True,
    }
    analyses = [
        {
            "output": {
                "component": component,
                "evidence_refs": [source_id],
                "findings": [copy.deepcopy(finding)],
            },
            "evidence_id": f"why_{index}",
        }
        for index, component in enumerate(ANALYSIS_COMPONENTS)
    ]
    reports = {audience: {"audience": audience} for audience in AUDIENCES}
    source_data = {
        "sec": {
            "filing": {"accessionNumber": "annual"},
            "latest_quarter_filing": {"accessionNumber": "quarter"},
            "fact_index": {
                fact_id: {
                    "fact_id": fact_id,
                    "metric": "revenue",
                    "period": "FY2026",
                    "source_id": source_id,
                }
            },
            "reconciliation": [{"within_tolerance": True}],
        }
    }
    package = {
        "source_data": source_data,
        "sources": sources,
        "analyses": analyses,
        "synthesis": {"summary": "Synthesis"},
        "reports": reports,
        "governance": {
            "integrity": {"verified": True, "record_count": 22},
            "review_integrity": {"verified": True},
            "guarantees": [{"holds": True}],
        },
    }
    digest = canonical_sha256(
        _release_content(source_data, sources, analyses, package["synthesis"], reports)
    )
    package["governance"]["release"] = {"content_digest": digest}
    package["governance"]["review"] = {
        "artifact_digest": digest,
        "status": "pending",
        "content_binding_valid": True,
        "approval_valid_for_digest": False,
        "release_authorized": False,
    }
    return package


def test_package_acceptance_gate_passes_and_fails_closed() -> None:
    package = _package_fixture()
    result = _validate_package(package)
    assert result["passed"] is True and result["check_count"] >= 15

    package["analyses"][0]["output"]["findings"][0]["evidence_refs"] = ["S-MISSING"]
    with pytest.raises(RuntimeError, match="finding source references"):
        _validate_package(package)


def test_fact_and_review_renderers_expose_auditable_state() -> None:
    source_fact = "fact_" + "1" * 64
    derived_fact = "fact_" + "2" * 64
    rendered = _fact_lineage({
        source_fact: {
            "fact_id": source_fact,
            "metric": "revenue",
            "period": "FY2026 Q4",
            "source_id": "S-SEC-COMPANYFACTS",
            "value": 10,
        },
        derived_fact: {
            "fact_id": derived_fact,
            "metric": "free_cash_flow",
            "period": "FY2026 Q4",
            "source_id": "S-SEC-COMPANYFACTS",
            "value": 8,
            "inputs": [{"fact_id": source_fact}],
        },
    })
    assert f'href="#fact-{"1" * 64}"' in rendered
    assert f'id="fact-{"1" * 64}"' in rendered

    review = _review_panel({
        "id": "review_1",
        "status": "pending",
        "artifact_digest": "a" * 64,
        "artifact_digest_matches": True,
        "approval_valid_for_digest": False,
        "release_authorized": False,
        "required_reviewers": ["finance-lead", "risk-lead"],
        "votes": [],
        "comments": [],
    }, {"verified": True})
    assert "Digest match: <b>VERIFIED</b>" in review
    assert "EXTERNAL / CONSEQUENTIAL RELEASE NOT AUTHORIZED" in review
