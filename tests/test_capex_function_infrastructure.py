"""Tests for CapEx Function trigger infrastructure."""
from __future__ import annotations

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SERVICE_BUS_BICEP = (
    REPOSITORY_ROOT
    / "infra"
    / "service-bus"
    / "invoice-ready-service-bus.bicep"
)
FUNCTION_BICEP = (
    REPOSITORY_ROOT
    / "infra"
    / "function-apps"
    / "capex-invoice-agent-invocation.bicep"
)


def test_task_determination_subscription_is_durable() -> None:
    source = SERVICE_BUS_BICEP.read_text(encoding="utf-8")

    assert "invoice-agent-invocation" in source
    assert (
        "Microsoft.ServiceBus/namespaces/topics/subscriptions@2024-01-01"
        in source
    )
    assert "deadLetteringOnMessageExpiration: true" in source
    assert "deadLetteringOnFilterEvaluationExceptions: true" in source
    assert "lockDuration: 'PT5M'" in source
    assert "maxDeliveryCount: 10" in source


def test_function_uses_identity_based_service_bus_settings() -> None:
    source = FUNCTION_BICEP.read_text(encoding="utf-8")

    assert "Endpoint=sb://" not in source
    assert "Authentication=ManagedIdentity" not in source
    assert "ServiceBusConnection__fullyQualifiedNamespace" in source
    assert "ServiceBusConnection__credential" in source
    assert "ServiceBusConnection__clientId" in source
    assert "value: 'managedidentity'" in source
    assert "value: userAssignedIdentityClientId" in source


def test_function_uses_identity_based_host_storage() -> None:
    source = FUNCTION_BICEP.read_text(encoding="utf-8")

    assert "functionsStorageAccountKey" not in source
    assert "AccountKey=" not in source
    assert "AzureWebJobsStorage__accountName" in source
    assert "AzureWebJobsStorage__credential" in source
    assert "AzureWebJobsStorage__clientId" in source
    assert "WEBSITE_CONTENTAZUREFILECONNECTIONSTRING" not in source
    assert "APPLICATIONINSIGHTS_CONNECTION_STRING" in source


def test_function_targets_existing_cict_app() -> None:
    source = FUNCTION_BICEP.read_text(encoding="utf-8")

    assert "param functionAppName string = 'cict-ai-cus-func'" in source



def test_function_enables_remote_dependency_build() -> None:
    source = FUNCTION_BICEP.read_text(encoding="utf-8")

    assert "SCM_DO_BUILD_DURING_DEPLOYMENT" in source
    assert "ENABLE_ORYX_BUILD" in source
