from __future__ import annotations

import struct
from typing import Any
from unittest.mock import MagicMock

import pytest

from autarch.capex.sql_connection import (
    AzureSqlConfigurationError,
    AzureSqlConnectionError,
    build_managed_identity_connection_string,
    connect_azure_sql,
)


@pytest.fixture(autouse=True)
def clear_hosted_identity_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IDENTITY_ENDPOINT", raising=False)
    monkeypatch.delenv("IDENTITY_HEADER", raising=False)


def test_builds_encrypted_managed_identity_connection_string() -> None:
    value = build_managed_identity_connection_string(
        server_fqdn="cict-ai-cus-sql.database.windows.net",
        database_name="cict-ai-cus-sqldb",
    )

    assert "ODBC Driver 18 for SQL Server" in value
    assert (
        "Server=tcp:cict-ai-cus-sql.database.windows.net,1433"
        in value
    )
    assert "Database=cict-ai-cus-sqldb" in value
    assert "Authentication=ActiveDirectoryMsi" in value
    assert "Encrypt=yes" in value
    assert "TrustServerCertificate=no" in value
    assert "Password=" not in value


@pytest.mark.parametrize(
    ("server", "database"),
    [
        ("", "database"),
        ("server;Trusted_Connection=yes", "database"),
        ("server.database.windows.net", ""),
        ("server.database.windows.net", "database;Encrypt=no"),
    ],
)
def test_rejects_invalid_connection_values(
    server: str,
    database: str,
) -> None:
    with pytest.raises(AzureSqlConfigurationError):
        build_managed_identity_connection_string(
            server_fqdn=server,
            database_name=database,
        )


def test_connects_with_transactions_enabled() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    expected = object()

    def connector(
        connection_string: str,
        **kwargs: Any,
    ) -> object:
        calls.append((connection_string, kwargs))
        return expected

    actual = connect_azure_sql(
        server_fqdn="sql.database.windows.net",
        database_name="capex",
        connector=connector,
    )

    assert actual is expected
    assert calls[0][1] == {"autocommit": False}


def test_wraps_driver_connection_failures() -> None:
    def connector(
        connection_string: str,
        **kwargs: Any,
    ) -> object:
        raise RuntimeError("driver failed")

    with pytest.raises(
        AzureSqlConnectionError,
        match="managed identity",
    ):
        connect_azure_sql(
            server_fqdn="sql.database.windows.net",
            database_name="capex",
            connector=connector,
        )

def test_user_assigned_identity_adds_client_id() -> None:
    client_id = "11111111-2222-3333-4444-555555555555"

    value = build_managed_identity_connection_string(
        server_fqdn="sql.database.windows.net",
        database_name="capex",
        managed_identity_client_id=client_id,
    )

    assert f"UID={client_id}" in value


def test_rejects_invalid_managed_identity_client_id() -> None:
    with pytest.raises(
        AzureSqlConfigurationError,
        match="UUID",
    ):
        build_managed_identity_connection_string(
            server_fqdn="sql.database.windows.net",
            database_name="capex",
            managed_identity_client_id="not-a-client-id",
        )


@pytest.mark.parametrize("client_id", [None, "11111111-2222-3333-4444-555555555555"])
def test_hosted_identity_uses_sdk_token_without_odbc_auth_fields(
    monkeypatch: pytest.MonkeyPatch, client_id: str | None,
) -> None:
    from azure.core.credentials import AccessToken

    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://127.0.0.1/identity")
    monkeypatch.setenv("IDENTITY_HEADER", "test-header")
    credential = MagicMock()
    credential.__enter__.return_value = credential
    credential.get_token.return_value = AccessToken("test-access-token", 2000000000)
    factory = MagicMock(return_value=credential)
    monkeypatch.setattr("azure.identity.ManagedIdentityCredential", factory)
    connector = MagicMock()

    actual = connect_azure_sql(
        server_fqdn="sql.database.windows.net", database_name="capex",
        managed_identity_client_id=client_id, connector=connector,
    )

    assert actual is connector.return_value
    factory.assert_called_once_with(client_id=client_id)
    credential.get_token.assert_called_once_with("https://database.windows.net/.default")
    credential.__exit__.assert_called_once()
    connection_string = connector.call_args.args[0]
    assert "Authentication=" not in connection_string
    assert "UID=" not in connection_string
    assert "Encrypt=yes;" in connection_string
    assert "TrustServerCertificate=no;" in connection_string
    assert "test-access-token" not in connection_string
    encoded = "test-access-token".encode("utf-16-le")
    assert connector.call_args.kwargs == {
        "autocommit": False,
        "attrs_before": {1256: struct.pack("<I", len(encoded)) + encoded},
    }


def test_hosted_identity_token_failure_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://127.0.0.1/identity")
    monkeypatch.setenv("IDENTITY_HEADER", "test-header")
    credential = MagicMock()
    credential.__enter__.return_value = credential
    credential.get_token.side_effect = RuntimeError("identity unavailable")
    monkeypatch.setattr("azure.identity.ManagedIdentityCredential", MagicMock(return_value=credential))
    connector = MagicMock()

    with pytest.raises(AzureSqlConnectionError, match="managed identity") as failure:
        connect_azure_sql(
            server_fqdn="sql.database.windows.net", database_name="capex", connector=connector,
        )

    assert isinstance(failure.value.__cause__, RuntimeError)
    connector.assert_not_called()
    credential.__exit__.assert_called_once()


@pytest.mark.parametrize("hosted", [False, True])
def test_explicit_azure_cli_auth_uses_only_cli_credential(monkeypatch, hosted):
    from azure.core.credentials import AccessToken

    if hosted:
        monkeypatch.setenv("IDENTITY_ENDPOINT", "http://127.0.0.1/identity")
    credential = MagicMock()
    credential.__enter__.return_value = credential
    credential.get_token.return_value = AccessToken("cli-test-token", 2000000000)
    cli_factory = MagicMock(return_value=credential)
    identity_factory = MagicMock()
    monkeypatch.setattr("azure.identity.AzureCliCredential", cli_factory)
    monkeypatch.setattr("azure.identity.ManagedIdentityCredential", identity_factory)
    connector = MagicMock()

    result = connect_azure_sql(
        server_fqdn="sql.database.windows.net", database_name="capex",
        authentication="azure-cli", connector=connector,
    )

    assert result is connector.return_value
    cli_factory.assert_called_once_with(process_timeout=30)
    identity_factory.assert_not_called()
    credential.get_token.assert_called_once_with("https://database.windows.net/.default")
    credential.__exit__.assert_called_once()
    connection_string = connector.call_args.args[0]
    assert "Authentication=" not in connection_string
    assert "UID=" not in connection_string
    assert "cli-test-token" not in connection_string
    assert "Encrypt=yes;" in connection_string
    assert "TrustServerCertificate=no;" in connection_string
    encoded = "cli-test-token".encode("utf-16-le")
    assert connector.call_args.kwargs == {
        "autocommit": False,
        "attrs_before": {1256: struct.pack("<I", len(encoded)) + encoded},
    }


def test_azure_cli_failure_does_not_fall_back(monkeypatch):
    credential = MagicMock()
    credential.__enter__.return_value = credential
    credential.get_token.side_effect = RuntimeError("CLI sign-in required")
    monkeypatch.setattr("azure.identity.AzureCliCredential", MagicMock(return_value=credential))
    identity_factory = MagicMock()
    monkeypatch.setattr("azure.identity.ManagedIdentityCredential", identity_factory)
    connector = MagicMock()
    with pytest.raises(AzureSqlConnectionError, match="Azure CLI") as failure:
        connect_azure_sql(
            server_fqdn="sql.database.windows.net", database_name="capex",
            authentication="azure-cli", connector=connector,
        )
    assert isinstance(failure.value.__cause__, RuntimeError)
    connector.assert_not_called()
    identity_factory.assert_not_called()
    credential.__exit__.assert_called_once()


def test_azure_cli_auth_rejects_managed_identity_selection():
    connector = MagicMock()
    with pytest.raises(AzureSqlConfigurationError, match="managed_identity_client_id"):
        connect_azure_sql(
            server_fqdn="sql.database.windows.net", database_name="capex",
            authentication="azure-cli",
            managed_identity_client_id="11111111-2222-3333-4444-555555555555",
            connector=connector,
        )
    connector.assert_not_called()
