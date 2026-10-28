from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from autarch.capex.invoice_ready import build_invoice_ready_event
from autarch.capex.service_bus import (
    AzureServiceBusInvoiceReadyPublisher,
    ServiceBusPublisherConfigurationError,
    build_invoice_ready_message,
)


ROOT = Path(__file__).resolve().parents[1]


def _event() -> dict[str, Any]:
    package = json.loads(
        (
            ROOT
            / "contracts"
            / "examples"
            / "invoice-package-v1-bruegmann.json"
        ).read_text(encoding="utf-8")
    )
    return build_invoice_ready_event(
        package,
        occurred_at_utc=datetime(
            2026,
            10,
            19,
            19,
            0,
            tzinfo=timezone.utc,
        ),
    )


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[Any] = []
        self.entered = False
        self.exited = False

    def __enter__(self) -> "FakeSender":
        self.entered = True
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        self.exited = True

    def send_messages(self, message: Any) -> None:
        self.messages.append(message)


class FakeClient:
    def __init__(self) -> None:
        self.sender = FakeSender()
        self.topic_names: list[str] = []
        self.closed = False

    def get_topic_sender(self, *, topic_name: str) -> FakeSender:
        self.topic_names.append(topic_name)
        return self.sender

    def close(self) -> None:
        self.closed = True


class FakeCredential:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_message_uses_contract_identifiers_as_broker_metadata() -> None:
    event = _event()

    message = build_invoice_ready_message(event)

    assert message.message_id == event["eventId"]
    assert message.correlation_id == event["correlationId"]
    assert message.subject == "InvoiceReady"
    assert message.content_type == "application/json"

    payload = json.loads(b"".join(message.body).decode("utf-8"))
    assert payload == event
    assert "invoice" not in payload
    assert "lines" not in payload
    assert "source" not in payload


def test_publisher_sends_to_configured_topic() -> None:
    fake_client = FakeClient()
    credential = FakeCredential()
    factory_calls: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> FakeClient:
        factory_calls.append(kwargs)
        return fake_client

    publisher = AzureServiceBusInvoiceReadyPublisher(
        fully_qualified_namespace=(
            "cict-ai-cus-bus.servicebus.windows.net"
        ),
        topic_name="capex-invoice-ready-v1",
        credential=credential,
        client_factory=factory,
    )

    publisher.publish(_event())
    publisher.close()
    publisher.close()

    assert factory_calls == [
        {
            "fully_qualified_namespace": (
                "cict-ai-cus-bus.servicebus.windows.net"
            ),
            "credential": credential,
        }
    ]
    assert fake_client.topic_names == [
        "capex-invoice-ready-v1"
    ]
    assert len(fake_client.sender.messages) == 1
    assert fake_client.sender.entered
    assert fake_client.sender.exited
    assert fake_client.closed
    assert not credential.closed


def test_publisher_rejects_invalid_namespace() -> None:
    with pytest.raises(
        ServiceBusPublisherConfigurationError,
        match=r"\.servicebus\.windows\.net",
    ):
        AzureServiceBusInvoiceReadyPublisher(
            fully_qualified_namespace="not-a-namespace",
            topic_name="capex-invoice-ready-v1",
            credential=FakeCredential(),
        )


def test_closed_publisher_rejects_publish() -> None:
    fake_client = FakeClient()

    publisher = AzureServiceBusInvoiceReadyPublisher(
        fully_qualified_namespace=(
            "cict-ai-cus-bus.servicebus.windows.net"
        ),
        topic_name="capex-invoice-ready-v1",
        credential=FakeCredential(),
        client_factory=lambda **_: fake_client,
    )
    publisher.close()

    with pytest.raises(RuntimeError, match="closed"):
        publisher.publish(_event())


def test_service_bus_bicep_configures_invoice_ready_delivery() -> None:
    bicep = (
        ROOT
        / "infra"
        / "service-bus"
        / "invoice-ready-service-bus.bicep"
    ).read_text(encoding="utf-8-sig")

    required_fragments = [
        "br/public:avm/res/service-bus/namespace/topic:0.1.1",
        "cict-ai-cus-bus",
        "capex-invoice-ready-v1",
        "requiresDuplicateDetection: true",
        "duplicateDetectionHistoryTimeWindow: 'PT1H'",
        "defaultMessageTimeToLive: 'P14D'",
        "invoice-agent-invocation",
        "Microsoft.ServiceBus/namespaces/topics/subscriptions@2024-01-01",
        "deadLetteringOnMessageExpiration: true",
        "deadLetteringOnFilterEvaluationExceptions: true",
        "lockDuration: 'PT5M'",
        "maxDeliveryCount: 10",
    ]
    for fragment in required_fragments:
        assert fragment in bicep

    forbidden_fragments = [
        "br/public:avm/res/service-bus/namespace/topic/subscription",
        "capex-determination-v1",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in bicep

    lowered = bicep.lower()
    assert "service-bus/namespace:" not in lowered
    assert "connectionstring" not in lowered
    assert "sharedaccesskey" not in lowered
    assert "authorizationrules" not in lowered


def test_capex_dependencies_include_service_bus_sdk() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(
        encoding="utf-8-sig"
    )

    assert '"azure-servicebus>=7.14,<8"' in pyproject
