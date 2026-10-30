from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from autarch.capex.demo_v2_normalization import (
    DemoContextOverlay,
    JCWorkbookNormalizationResult,
    NormalizationStatus,
    NormalizedInvoicePackage,
    normalize_abbyy_for_demo_v2,
    normalize_jc_workbook,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "examples" / "data" / "capex"
OUTPUT_DIR = SOURCE_DIR / "normalized-v2"
CREATED_AT = datetime(2026, 10, 30, tzinfo=timezone.utc)
RUN_ID = "capex-demo-v2-normalization-2026-10-30"


def _source(pattern: str) -> Path:
    matches = list(SOURCE_DIR.glob(pattern))
    assert len(matches) == 1
    return matches[0]


def _overlay(invoice_id: str) -> DemoContextOverlay:
    return DemoContextOverlay(
        overlay_version="test-overlay",
        target_invoice_id=invoice_id,
        project_id="DEMO-PROJECT-001",
        site_id="DEMO-SITE-001",
        project_status="OPEN",
        bundling_in_scope=True,
    )


def test_bruegmann_missing_shipping_values_remain_null() -> None:
    invoice_id = "135d03ac-3598-498a-83fe-d273d046bfea"

    package = normalize_abbyy_for_demo_v2(
        _source("Bruegmann*.zip"),
        run_id=RUN_ID,
        created_at_utc=CREATED_AT,
        context_overlay=_overlay(invoice_id),
    )

    assert package.normalization_status == (
        NormalizationStatus.REVIEW_REQUIRED
    )
    assert len(package.lines) == 12
    shipping = package.lines[-1]
    assert shipping.line_amount == pytest.approx(617.09)
    assert shipping.quantity is None
    assert shipping.unit_cost is None
    assert package.context_overlay is not None
    assert package.context_overlay.synthetic is True
    assert package.source.sha256 == (
        "974d61a406edf85e3cadfa75a5177795481d3a82ce962dc414e9d276b885ee5b"
    )


def test_kimley_missing_values_are_not_zero_placeholders() -> None:
    invoice_id = "58aceec5-f35a-48a7-8faa-2f1a195af203"

    package = normalize_abbyy_for_demo_v2(
        _source("Kimley*.zip"),
        run_id=RUN_ID,
        created_at_utc=CREATED_AT,
        context_overlay=_overlay(invoice_id),
    )

    assert len(package.lines) == 10
    assert all(line.quantity is None for line in package.lines)
    assert all(line.unit_cost is None for line in package.lines)
    assert sum(line.line_amount for line in package.lines) == (
        pytest.approx(28805.0)
    )
    assert len(
        package.data_quality.missing_unit_cost_line_ids
    ) == 10


def test_jc_target_excludes_unapproved_type_and_mixed_currency() -> None:
    result = normalize_jc_workbook(
        _source("JC2000A07814*.xlsx"),
        run_id=RUN_ID,
        created_at_utc=CREATED_AT,
        context_overlays={
            "JC2000A07814:20294": _overlay(
                "JC2000A07814:20294"
            )
        },
    )

    assert result.project_id == "JC2000A07814"
    assert result.source_currencies == ["DKK", "EUR"]
    assert len(result.packages) == 1
    assert result.packages[0].invoice_number == "20294"
    assert result.packages[0].currency == "EUR"
    assert result.packages[0].invoice_total == pytest.approx(
        16123.79
    )
    assert len(result.excluded_records) == 1
    assert result.excluded_records[0].transaction_type == "OV"
    assert result.excluded_records[0].currency == "DKK"
    assert "not approved" in result.excluded_records[0].reason
    assert any(
        "Mixed source currencies" in reason
        for reason in result.review_reasons
    )


def test_jc_companion_excludes_reversals_from_packages() -> None:
    result = normalize_jc_workbook(
        _source("JC2000A07465*.xlsx"),
        run_id=RUN_ID,
        created_at_utc=CREATED_AT,
    )

    assert len(result.packages) == 7
    assert len(result.excluded_records) == 6
    assert {
        record.transaction_type
        for record in result.excluded_records
    } == {"JP"}
    assert all(
        "double counting" in record.reason
        for record in result.excluded_records
    )


def test_context_overlay_cannot_masquerade_as_source_data() -> None:
    with pytest.raises(ValidationError, match="DEMO-"):
        DemoContextOverlay(
            overlay_version="test-overlay",
            target_invoice_id="invoice-1",
            project_id="REAL-PROJECT",
            site_id="DEMO-SITE-001",
            project_status="OPEN",
            bundling_in_scope=True,
        )


@pytest.mark.parametrize(
    "file_name,model_type",
    [
        ("bruegmann.normalized.json", NormalizedInvoicePackage),
        ("kimley-horn.normalized.json", NormalizedInvoicePackage),
        (
            "jc2000a07814.normalized.json",
            JCWorkbookNormalizationResult,
        ),
        (
            "jc2000a07465.classification.json",
            JCWorkbookNormalizationResult,
        ),
    ],
)
def test_committed_normalization_artifacts_validate(
    file_name: str,
    model_type: type,
) -> None:
    payload = json.loads(
        (OUTPUT_DIR / file_name).read_text(encoding="utf-8")
    )

    model_type.model_validate(payload)
