"""Secretless Azure SQL connection support for CapEx workloads."""

from __future__ import annotations

import re
from typing import Any, Callable, Optional
from uuid import UUID

from .persistence import DatabaseConnection


_SERVER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*$")
_DATABASE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class AzureSqlConfigurationError(ValueError):
    """Raised when Azure SQL configuration is invalid."""


class AzureSqlConnectionError(RuntimeError):
    """Raised when an Azure SQL connection cannot be opened."""


def _validated_name(
    value: object,
    *,
    field: str,
    pattern: re.Pattern[str],
) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise AzureSqlConfigurationError(
            f"{field} is required."
        )
    if not pattern.fullmatch(text):
        raise AzureSqlConfigurationError(
            f"{field} contains unsupported characters."
        )
    return text


def _validated_client_id(
    value: Optional[str],
) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    if not text:
        return None

    try:
        return str(UUID(text))
    except ValueError as exc:
        raise AzureSqlConfigurationError(
            "managed_identity_client_id must be a UUID."
        ) from exc


def build_managed_identity_connection_string(
    *,
    server_fqdn: str,
    database_name: str,
    managed_identity_client_id: Optional[str] = None,
) -> str:
    """Build an encrypted managed-identity ODBC connection string."""
    server = _validated_name(
        server_fqdn,
        field="server_fqdn",
        pattern=_SERVER_PATTERN,
    )
    database = _validated_name(
        database_name,
        field="database_name",
        pattern=_DATABASE_PATTERN,
    )
    client_id = _validated_client_id(
        managed_identity_client_id
    )

    identity = "Authentication=ActiveDirectoryMsi;"
    if client_id is not None:
        identity += f"UID={client_id};"

    return (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server=tcp:{server},1433;"
        f"Database={database};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        f"{identity}"
        "Connection Timeout=30;"
    )


def connect_azure_sql(
    *,
    server_fqdn: str,
    database_name: str,
    managed_identity_client_id: Optional[str] = None,
    connector: Optional[Callable[..., Any]] = None,
) -> DatabaseConnection:
    """Open Azure SQL using a system- or user-assigned identity."""
    connection_string = (
        build_managed_identity_connection_string(
            server_fqdn=server_fqdn,
            database_name=database_name,
            managed_identity_client_id=(
                managed_identity_client_id
            ),
        )
    )

    if connector is None:
        try:
            import pyodbc
        except ImportError as exc:
            raise AzureSqlConnectionError(
                "pyodbc is required for Azure SQL connectivity."
            ) from exc
        connector = pyodbc.connect

    try:
        return connector(
            connection_string,
            autocommit=False,
        )
    except Exception as exc:
        raise AzureSqlConnectionError(
            "Could not connect to Azure SQL using the "
            "workload managed identity."
        ) from exc


__all__ = [
    "AzureSqlConfigurationError",
    "AzureSqlConnectionError",
    "build_managed_identity_connection_string",
    "connect_azure_sql",
]
