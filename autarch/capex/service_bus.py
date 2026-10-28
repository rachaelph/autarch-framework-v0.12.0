"""Azure Service Bus publisher for InvoiceReady v1 events."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Optional

from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage

from .invoice_ready import validate_invoice_ready


class ServiceBusPublisherConfigurationError(ValueError):
    """Raised when Service Bus publisher configuration is invalid."""


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ServiceBusPublisherConfigurationError(
            f"{field} must be a non-empty string"
        )
    return value.strip()


def build_invoice_ready_message(
    event: Mapping[str, Any],
) -> ServiceBusMessage:
    """Create a broker message from a validated pointer-only event."""
    validate_invoice_ready(event)

    payload = json.dumps(
        dict(event),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )

    return ServiceBusMessage(
        payload,
        message_id=event["eventId"],
        correlation_id=event["correlationId"],
        subject=event["eventType"],
        content_type="application/json",
        application_properties={
            "eventType": event["eventType"],
            "schemaVersion": event["schemaVersion"],
            "packageId": event["packageId"],
            "runId": event["runId"],
        },
    )


class AzureServiceBusInvoiceReadyPublisher:
    """Publish InvoiceReady v1 with Entra-based authentication."""

    def __init__(
        self,
        *,
        fully_qualified_namespace: str,
        topic_name: str,
        credential: Optional[Any] = None,
        client_factory: Callable[..., Any] = ServiceBusClient,
    ) -> None:
        namespace = _required_text(
            fully_qualified_namespace,
            field="fully_qualified_namespace",
        )
        topic = _required_text(
            topic_name,
            field="topic_name",
        )

        if not namespace.endswith(".servicebus.windows.net"):
            raise ServiceBusPublisherConfigurationError(
                "fully_qualified_namespace must end with "
                ".servicebus.windows.net"
            )

        self._topic_name = topic
        self._owns_credential = credential is None
        self._credential = (
            credential
            if credential is not None
            else DefaultAzureCredential()
        )
        self._client = client_factory(
            fully_qualified_namespace=namespace,
            credential=self._credential,
        )
        self._closed = False

    def publish(
        self,
        event: Mapping[str, Any],
    ) -> None:
        """Publish one validated event."""
        if self._closed:
            raise RuntimeError("The Service Bus publisher is closed.")

        message = build_invoice_ready_message(event)
        with self._client.get_topic_sender(
            topic_name=self._topic_name
        ) as sender:
            sender.send_messages(message)

    def close(self) -> None:
        """Close the Service Bus client and owned credential."""
        if self._closed:
            return

        self._client.close()
        if self._owns_credential:
            self._credential.close()
        self._closed = True

    def __enter__(
        self,
    ) -> "AzureServiceBusInvoiceReadyPublisher":
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        self.close()


__all__ = [
    "AzureServiceBusInvoiceReadyPublisher",
    "ServiceBusPublisherConfigurationError",
    "build_invoice_ready_message",
]
