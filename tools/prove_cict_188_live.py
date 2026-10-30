"""Live managed-identity proof for the CapEx Task Determination agent."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from collections.abc import Mapping, Sequence
from typing import Any

from autarch.capex.agent_invocation_blob import fetch_invoice_package_from_blob
from autarch.capex.agent_invocation_handler import invoke_task_determination_agent
from autarch.capex.agent_invocation_persistence import load_task_catalog
from autarch.capex.sql_connection import (
    AzureSqlConnectionError,
    connect_azure_sql,
)

DEFAULT_PACKAGE_ID = "pkg-2a26689e-5097-5cfe-b18c-10d34d75c0da"
DEFAULT_INVOICE_ID = "135d03ac-3598-498a-83fe-d273d046bfea"
DEFAULT_RUN_ID = "run-20261022T031623Z-eee6efc5"
DEFAULT_CORRELATION_ID = "corr-82167cbd-60d4-5c1d-b854-f6e335fd7b6a"
DEFAULT_DEPLOYMENT = "gpt-5.1"
DEFAULT_REGION = "US"
DEFAULT_EXPECTED_LINE_COUNT = 12
DEFAULT_SQL_CONNECT_ATTEMPTS = 4
DEFAULT_SQL_RETRY_DELAY_SECONDS = 30
DEFAULT_PROMPT_VERSION = "task-determination-v1"
DEFAULT_TASK_MASTER_SHA256 = (
    "CC41B492A5DE454EFD7405066EF1EAA6CDE1FF050C8E71ED180831197B6EBA13"
)


class ConfigurationError(RuntimeError):
    """Raised when the proof environment is incomplete or unsafe."""


def required_setting(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"Required environment setting is missing: {name}")
    return value


def assert_managed_identity_runtime() -> None:
    if not (os.getenv("IDENTITY_ENDPOINT") or os.getenv("MSI_ENDPOINT")):
        raise ConfigurationError(
            "This proof must run in an Azure managed-identity runtime. "
            "Workstation credentials are intentionally rejected."
        )


def catalog_codes(catalog: Sequence[Mapping[str, Any]]) -> set[str]:
    codes = {
        str(row.get("task_code", "")).strip()
        for row in catalog
        if str(row.get("task_code", "")).strip()
    }
    if not codes:
        raise ConfigurationError("The governed TaskMaster catalog is empty.")
    return codes



def sql_network_evidence(server_fqdn: str) -> dict[str, Any]:
    addresses = sorted(
        {
            entry[4][0]
            for entry in socket.getaddrinfo(
                server_fqdn,
                1433,
                type=socket.SOCK_STREAM,
            )
        }
    )
    if not addresses:
        raise ConfigurationError(
            f"DNS returned no addresses for Azure SQL host {server_fqdn}."
        )

    tcp_started = time.monotonic()
    with socket.create_connection((server_fqdn, 1433), timeout=10):
        pass

    return {
        "event": "sql-network-preflight",
        "server": server_fqdn,
        "resolvedAddresses": addresses,
        "tcpPort": 1433,
        "tcpConnectMilliseconds": round(
            (time.monotonic() - tcp_started) * 1000
        ),
    }


def connect_azure_sql_with_retry(
    *,
    server_fqdn: str,
    database_name: str,
    managed_identity_client_id: str,
    attempts: int = DEFAULT_SQL_CONNECT_ATTEMPTS,
    retry_delay_seconds: int = DEFAULT_SQL_RETRY_DELAY_SECONDS,
) -> Any:
    if attempts < 1:
        raise ConfigurationError("SQL connection attempts must be at least one.")

    for attempt in range(1, attempts + 1):
        try:
            connection = connect_azure_sql(
                server_fqdn=server_fqdn,
                database_name=database_name,
                managed_identity_client_id=managed_identity_client_id,
            )
            print(
                json.dumps(
                    {
                        "event": "sql-connected",
                        "attempt": attempt,
                        "maximumAttempts": attempts,
                    },
                    sort_keys=True,
                )
            )
            return connection
        except AzureSqlConnectionError:
            print(
                json.dumps(
                    {
                        "event": "sql-connect-failed",
                        "attempt": attempt,
                        "maximumAttempts": attempts,
                        "willRetry": attempt < attempts,
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            if attempt == attempts:
                raise
            time.sleep(retry_delay_seconds)

    raise AssertionError("SQL connection retry loop ended unexpectedly.")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prove a live UAMI-backed Foundry Task Determination call."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Required safety gate for the billable live model invocation.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.execute:
        print(
            "Refusing to invoke the model without --execute.",
            file=sys.stderr,
        )
        return 2

    assert_managed_identity_runtime()

    managed_identity_client_id = required_setting("AZURE_CLIENT_ID")
    blob_account_url = required_setting("CAPEX_BLOB_ACCOUNT_URL")
    blob_container = required_setting("CAPEX_BLOB_CONTAINER")
    sql_server = required_setting("CAPEX_SQL_SERVER")
    sql_database = required_setting("CAPEX_SQL_DATABASE")
    foundry_endpoint = required_setting("FOUNDRY_ENDPOINT")

    package_id = os.getenv("CAPEX_PROOF_PACKAGE_ID", DEFAULT_PACKAGE_ID)
    invoice_id = os.getenv("CAPEX_PROOF_INVOICE_ID", DEFAULT_INVOICE_ID)
    run_id = os.getenv("CAPEX_PROOF_RUN_ID", DEFAULT_RUN_ID)
    correlation_id = os.getenv(
        "CAPEX_PROOF_CORRELATION_ID",
        DEFAULT_CORRELATION_ID,
    )
    deployment = os.getenv("FOUNDRY_DEPLOYMENT", DEFAULT_DEPLOYMENT)
    region = os.getenv("CAPEX_TASK_REGION", DEFAULT_REGION)
    prompt_version = os.getenv(
        "CAPEX_TASK_PROMPT_VERSION",
        DEFAULT_PROMPT_VERSION,
    )
    task_master_sha256 = os.getenv(
        "CAPEX_TASK_MASTER_SHA256",
        DEFAULT_TASK_MASTER_SHA256,
    )
    expected_line_count = int(
        os.getenv(
            "CAPEX_PROOF_EXPECTED_LINE_COUNT",
            str(DEFAULT_EXPECTED_LINE_COUNT),
        )
    )

    invoice_package = fetch_invoice_package_from_blob(
        blob_account_url=blob_account_url,
        blob_container=blob_container,
        package_id=package_id,
        run_id=run_id,
        managed_identity_client_id=managed_identity_client_id,
    )

    print(
        json.dumps(
            sql_network_evidence(sql_server),
            sort_keys=True,
        )
    )

    connection = connect_azure_sql_with_retry(
        server_fqdn=sql_server,
        database_name=sql_database,
        managed_identity_client_id=managed_identity_client_id,
    )
    try:
        task_catalog = load_task_catalog(connection, region_code=region)
    finally:
        connection.close()

    governed_codes = catalog_codes(task_catalog)
    suggestion = invoke_task_determination_agent(
        invoice_package=invoice_package,
        correlation_id=correlation_id,
        package_id=package_id,
        run_id=run_id,
        invoice_id=invoice_id,
        foundry_endpoint=foundry_endpoint,
        managed_identity_client_id=managed_identity_client_id,
        task_catalog=task_catalog,
        deployment_name=deployment,
    )

    suggested_lines = [
        line for line in suggestion.lines if line.task_code is not None
    ]
    unresolved_lines = [
        line for line in suggestion.lines if line.task_code is None
    ]
    ungoverned_codes = sorted(
        {
            str(line.task_code)
            for line in suggested_lines
            if str(line.task_code) not in governed_codes
        }
    )
    if ungoverned_codes:
        raise RuntimeError(
            "The model returned task codes outside the governed catalog: "
            + ", ".join(ungoverned_codes)
        )

    line_ids = [str(line.line_id) for line in suggestion.lines]
    if len(line_ids) != len(set(line_ids)):
        raise RuntimeError("The result contains duplicate invoice line IDs.")
    if len(line_ids) != expected_line_count:
        raise RuntimeError(
            "The result line count does not match the controlled package: "
            f"expected {expected_line_count}, received {len(line_ids)}."
        )
    returned_identity = (
        suggestion.package_id,
        suggestion.invoice_id,
        suggestion.run_id,
        suggestion.correlation_id,
    )
    expected_identity = (
        package_id,
        invoice_id,
        run_id,
        correlation_id,
    )
    if returned_identity != expected_identity:
        raise RuntimeError("The result identity does not match the invocation.")

    evidence = {
        "result": "PASS",
        "authentication": "user-assigned-managed-identity",
        "packageId": suggestion.package_id,
        "invoiceId": suggestion.invoice_id,
        "runId": suggestion.run_id,
        "correlationId": suggestion.correlation_id,
        "agentExecutionId": suggestion.agent_execution_id,
        "agentSessionId": suggestion.agent_session_id,
        "modelDeployment": deployment,
        "promptVersion": prompt_version,
        "taskMasterSha256": task_master_sha256,
        "taskRegion": region,
        "taskCatalogCount": len(governed_codes),
        "resultLineCount": len(suggestion.lines),
        "expectedLineCount": expected_line_count,
        "distinctLineCount": len(set(line_ids)),
        "suggestedLineCount": len(suggested_lines),
        "unresolvedLineCount": len(unresolved_lines),
        "ungovernedTaskCodeCount": len(ungoverned_codes),
        "status": suggestion.status,
        "createdAtUtc": suggestion.created_at_utc.isoformat(),
    }
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {
                    "result": "FAIL",
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise