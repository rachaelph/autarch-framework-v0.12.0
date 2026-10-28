from __future__ import annotations

from typing import Any

import pytest

from autarch.capex.sql_connection import (
    AzureSqlConfigurationError,
    AzureSqlConnectionError,
    build_managed_identity_connection_string,
    connect_azure_sql,
)


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
