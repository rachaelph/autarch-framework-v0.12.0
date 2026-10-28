from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autarch.capex.invoice_ready import (
    InvoiceReadyAssemblyError,
    build_invoice_ready_event,
    derive_invoice_correlation_id,
    derive_invoice_ready_event_id,
    validate_invoice_ready,
)


ROOT = Path(__file__).resolve().parents[1]


def _package() -> dict:
    return json.loads(
        (
            ROOT
            / "contracts"
            / "examples"
            / "invoice-package-v1-bruegmann.json"
        ).read_text(encoding="utf-8")
    )


def test_build_invoice_ready_event_is_pointer_only() -> None:
    package = _package()

    event = build_invoice_ready_event(
        package,
        package_uri=(
            "urn:circlek:capex:invoice-package:"
            f"{package['packageId']}"
        ),
        occurred_at_utc=datetime(
            2026,
            10,
            19,
            19,
            0,
            tzinfo=timezone.utc,
        ),
    )

    validate_invoice_ready(event)

    assert event["eventType"] == "InvoiceReady"
    assert event["schemaVersion"] == "1.0"
    assert event["invoiceId"] == package["invoice"]["invoiceId"]
    assert event["packageId"] == package["packageId"]
    assert event["runId"] == package["runId"]
    assert event["occurredAtUtc"] == "2026-10-19T19:00:00.000Z"
    assert "invoice" not in event
    assert "lines" not in event
    assert "source" not in event
    assert "dataQuality" not in event


def test_invoice_ready_identity_is_stable() -> None:
    package = _package()
    package_id = package["packageId"]
    invoice_id = package["invoice"]["invoiceId"]
    run_id = package["runId"]

    assert derive_invoice_ready_event_id(
        package_id
    ) == derive_invoice_ready_event_id(package_id)

    assert derive_invoice_correlation_id(
        run_id,
        invoice_id,
    ) == derive_invoice_correlation_id(
        run_id,
        invoice_id,
    )


def test_invoice_ready_rejects_naive_timestamp() -> None:
    with pytest.raises(
        InvoiceReadyAssemblyError,
        match="UTC offset",
    ):
        build_invoice_ready_event(
            _package(),
            occurred_at_utc=datetime(2026, 10, 19, 19, 0),
        )


def test_packaged_invoice_ready_schema_matches_contract() -> None:
    canonical = (
        ROOT / "contracts" / "invoice-ready-v1.schema.json"
    ).read_bytes()
    packaged = (
        ROOT
        / "autarch"
        / "capex"
        / "schemas"
        / "invoice-ready-v1.schema.json"
    ).read_bytes()

    assert packaged == canonical
