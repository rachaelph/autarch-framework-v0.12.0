"""Regression tests for the deployed CapEx Function contract boundary."""

from __future__ import annotations

import json

import pytest

from azure_function.function_app import (
    _parse_invoice_ready_message,
)


def test_parse_invoice_ready_message_accepts_v1_camel_case_contract() -> None:
    message = _parse_invoice_ready_message(
        json.dumps(
            {
                "eventId": "evt-1",
                "eventType": "InvoiceReady",
                "schemaVersion": "1.0",
                "invoiceId": "invoice-1",
                "packageId": "package-1",
                "runId": "run-1",
                "packageUri": "urn:package:1",
                "occurredAtUtc": "2026-10-28T18:00:00Z",
                "correlationId": "correlation-1",
            }
        ).encode("utf-8")
    )

    assert message.event_id == "evt-1"
    assert message.invoice_id == "invoice-1"
    assert message.package_id == "package-1"
    assert message.correlation_id == "correlation-1"


def test_parse_invoice_ready_message_rejects_non_object_json() -> None:
    with pytest.raises(
        ValueError,
        match="must be a JSON object",
    ):
        _parse_invoice_ready_message(b"[]")
