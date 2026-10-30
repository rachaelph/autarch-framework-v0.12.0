"""Tests for Invoice Agent Invocation components."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from autarch.capex.agent_invocation_models import (
    InvoiceReadyMessage,
    TaskSuggestion,
    TaskSuggestionLine,
)
from autarch.capex.agent_invocation_blob import (
    BlobPackageFetchError,
    fetch_invoice_package_from_blob,
)
from autarch.capex.agent_invocation_handler import (
    FoundryAgentError,
    invoke_task_determination_agent,
)


def test_invoice_ready_message_parsing():
    """Test parsing of InvoiceReady v1 message."""
    props = {
        "eventId": "evt-12345",
        "eventType": "InvoiceReady",
        "schemaVersion": "1.0",
        "invoiceId": "INV-001",
        "packageId": "pkg-001",
        "runId": "run-001",
        "packageUri": "urn:circlek:capex:invoice-package:pkg-001",
        "occurredAtUtc": "2026-10-21T12:00:00Z",
        "correlationId": "corr-001",
    }

    message = InvoiceReadyMessage.from_properties(props)

    assert message.event_id == "evt-12345"
    assert message.invoice_id == "INV-001"
    assert message.package_id == "pkg-001"
    assert message.correlation_id == "corr-001"


def test_task_suggestion_creation():
    """Test creating a task suggestion."""
    lines = [
        TaskSuggestionLine(
            line_id="1",
            recommendation="CAPEX",
            confidence=0.85,
            rationale="Equipment purchase",
            evidence_refs=["invoice:line1"],
            policy_ids=["policy:asset"],
        )
    ]

    suggestion = TaskSuggestion(
        package_id="pkg-001",
        invoice_id="INV-001",
        run_id="run-001",
        correlation_id="corr-001",
        lines=lines,
        overall_recommendation="CAPEX",
    )

    assert suggestion.package_id == "pkg-001"
    assert len(suggestion.lines) == 1
    assert suggestion.overall_recommendation == "CAPEX"
    assert suggestion.status == "PENDING"


def test_blob_fetch_requires_configuration():
    """Test that blob fetch requires all configuration."""
    with pytest.raises(BlobPackageFetchError, match="required"):
        fetch_invoice_package_from_blob(
            blob_account_url="",
            blob_container="test",
            package_id="pkg-001",
            run_id="run-001",
        )


def test_blob_fetch_success():
    """Test successful package fetch from Blob Storage."""
    invoice_package = {
        "packageId": "pkg-001",
        "invoice": {"invoiceId": "INV-001"},
        "lines": [{"lineId": "1", "description": "Test item"}],
    }

    mock_blob_client = MagicMock()
    mock_download = MagicMock()
    mock_download.readall.return_value = json.dumps(invoice_package).encode("utf-8")
    mock_blob_client.download_blob.return_value = mock_download

    mock_container_client = MagicMock()
    mock_container_client.get_blob_client.return_value = mock_blob_client

    mock_service_client = MagicMock()
    mock_service_client.get_container_client.return_value = mock_container_client

    with patch(
        "autarch.capex.agent_invocation_blob.BlobServiceClient",
        return_value=mock_service_client,
    ):
        with patch(
            "autarch.capex.agent_invocation_blob.ManagedIdentityCredential"
        ):
            result = fetch_invoice_package_from_blob(
                blob_account_url="https://test.blob.core.windows.net",
                blob_container="test",
                package_id="pkg-001",
                run_id="run-001",
            )

    assert result["packageId"] == "pkg-001"
    assert result["invoice"]["invoiceId"] == "INV-001"


def test_blob_fetch_not_found():
    """Test blob fetch when package not found."""
    mock_blob_client = MagicMock()
    mock_blob_client.download_blob.side_effect = Exception("Blob not found")

    mock_container_client = MagicMock()
    mock_container_client.get_blob_client.return_value = mock_blob_client

    mock_service_client = MagicMock()
    mock_service_client.get_container_client.return_value = mock_container_client

    with patch(
        "autarch.capex.agent_invocation_blob.BlobServiceClient",
        return_value=mock_service_client,
    ):
        with patch(
            "autarch.capex.agent_invocation_blob.ManagedIdentityCredential"
        ):
            with pytest.raises(BlobPackageFetchError, match="not found"):
                fetch_invoice_package_from_blob(
                    blob_account_url="https://test.blob.core.windows.net",
                    blob_container="test",
                    package_id="pkg-001",
                    run_id="run-001",
                )


def _task_catalog():
    return [
        {
            "task_code": "2000.10",
            "description": "BUILDING",
        }
    ]


def _invoice_package():
    return {
        "invoice": {
            "invoiceId": "INV-001",
            "vendorName": "Vendor",
        },
        "lines": [
            {
                "invoiceLineId": "line-001",
                "description": "Building work",
                "quantity": "1",
                "unitCost": "100",
            }
        ],
    }


def test_agent_invocation_requires_sdk():
    with patch(
        "autarch.capex.agent_invocation_handler."
        "AGENT_FRAMEWORK_AVAILABLE",
        False,
    ):
        with pytest.raises(
            FoundryAgentError,
            match="agent-framework",
        ):
            invoke_task_determination_agent(
                invoice_package=_invoice_package(),
                correlation_id="corr-001",
                package_id="pkg-001",
                run_id="run-001",
                invoice_id="INV-001",
                foundry_endpoint="https://test.openai.azure.com/",
                managed_identity_client_id="client-001",
                task_catalog=_task_catalog(),
            )


def test_agent_invocation_success():
    agent_response = {
        "lines": [
            {
                "line_id": "line-001",
                "recommendation": "REVIEW",
                "task_code": "2000.10",
                "asset_class_code": None,
                "confidence": 0.85,
                "rationale": "Matches governed BUILDING task.",
                "evidence_refs": [],
                "policy_ids": [],
            }
        ],
        "overall_recommendation": "REVIEW",
    }

    response = MagicMock()
    response.text = json.dumps(agent_response)
    response.response_id = "exec-001"
    response.conversation_id = "session-001"

    chat_client = MagicMock()
    chat_client.get_response = AsyncMock(
        return_value=response,
    )

    credential = MagicMock()

    with patch(
        "autarch.capex.agent_invocation_handler."
        "OpenAIChatClient",
        return_value=chat_client,
    ), patch(
        "autarch.capex.agent_invocation_handler."
        "ManagedIdentityCredential",
        return_value=credential,
    ):
        suggestion = invoke_task_determination_agent(
            invoice_package=_invoice_package(),
            correlation_id="corr-001",
            package_id="pkg-001",
            run_id="run-001",
            invoice_id="INV-001",
            foundry_endpoint="https://test.openai.azure.com/",
            managed_identity_client_id="client-001",
            task_catalog=_task_catalog(),
            deployment_name="gpt-5.1",
        )

    assert len(suggestion.lines) == 1
    assert suggestion.lines[0].task_code == "2000.10"
    assert suggestion.agent_execution_id == "exec-001"
    assert suggestion.agent_session_id == "session-001"
    chat_client.get_response.assert_awaited_once()
    credential.close.assert_called_once()


def test_agent_invocation_rejects_invalid_json():
    response = MagicMock()
    response.text = "not json"
    response.response_id = "exec-001"
    response.conversation_id = None

    chat_client = MagicMock()
    chat_client.get_response = AsyncMock(
        return_value=response,
    )

    with patch(
        "autarch.capex.agent_invocation_handler."
        "OpenAIChatClient",
        return_value=chat_client,
    ), patch(
        "autarch.capex.agent_invocation_handler."
        "ManagedIdentityCredential",
    ):
        with pytest.raises(
            FoundryAgentError,
            match="invalid JSON",
        ):
            invoke_task_determination_agent(
                invoice_package=_invoice_package(),
                correlation_id="corr-001",
                package_id="pkg-001",
                run_id="run-001",
                invoice_id="INV-001",
                foundry_endpoint="https://test.openai.azure.com/",
                managed_identity_client_id="client-001",
                task_catalog=_task_catalog(),
            )


def test_agent_invocation_rejects_unknown_task_code():
    agent_response = {
        "lines": [
            {
                "line_id": "line-001",
                "recommendation": "REVIEW",
                "task_code": "NOT-ALLOWED",
                "confidence": 0.5,
                "rationale": "Unsupported.",
            }
        ],
        "overall_recommendation": "REVIEW",
    }

    response = MagicMock()
    response.text = json.dumps(agent_response)
    response.response_id = "exec-001"
    response.conversation_id = None

    chat_client = MagicMock()
    chat_client.get_response = AsyncMock(
        return_value=response,
    )

    with patch(
        "autarch.capex.agent_invocation_handler."
        "OpenAIChatClient",
        return_value=chat_client,
    ), patch(
        "autarch.capex.agent_invocation_handler."
        "ManagedIdentityCredential",
    ):
        with pytest.raises(
            FoundryAgentError,
            match="outside TaskMaster",
        ):
            invoke_task_determination_agent(
                invoice_package=_invoice_package(),
                correlation_id="corr-001",
                package_id="pkg-001",
                run_id="run-001",
                invoice_id="INV-001",
                foundry_endpoint="https://test.openai.azure.com/",
                managed_identity_client_id="client-001",
                task_catalog=_task_catalog(),
            )