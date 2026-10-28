"""Blob-staged invoice preparation job wrapper with managed identity."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient

from .job import (
    InvoicePreparationJobError,
    PersistPackage,
    _log,
    run_preparation_job,
)
from .persistence import persist_invoice_package
from .sql_connection import (
    AzureSqlConfigurationError,
    AzureSqlConnectionError,
    connect_azure_sql,
)


class BlobJobError(RuntimeError):
    """Raised when Blob-staged job cannot run safely."""


def _create_sql_persistence(
    *,
    sql_server: Optional[str],
    sql_database: Optional[str],
    managed_identity_client_id: Optional[str],
) -> Tuple[Optional[Any], Optional[PersistPackage]]:
    server = str(sql_server or "").strip()
    database = str(sql_database or "").strip()
    if bool(server) != bool(database):
        raise AzureSqlConfigurationError(
            "CAPEX_SQL_SERVER and CAPEX_SQL_DATABASE "
            "must be configured together."
        )
    if not server:
        return None, None

    connection = connect_azure_sql(
        server_fqdn=server,
        database_name=database,
        managed_identity_client_id=managed_identity_client_id,
    )

    def persist_package(package):
        return persist_invoice_package(connection, package)

    return connection, persist_package


def run_blob_preparation_job(
    *,
    blob_account_url: str,
    blob_container: str,
    input_prefix: str = "capex/input/",
    output_prefix: str = "capex/output/",
    quarantine_prefix: str = "capex/quarantine/",
    managed_identity_client_id: Optional[str] = None,
    sql_server: Optional[str] = None,
    sql_database: Optional[str] = None,
) -> Dict[str, Any]:
    """Download invoices from Blob, prepare, and upload results with quarantine."""

    account_url = str(blob_account_url).strip()
    container = str(blob_container).strip()
    in_prefix = str(input_prefix).strip()
    out_prefix = str(output_prefix).strip()
    quar_prefix = str(quarantine_prefix).strip()

    if not all([account_url, container, in_prefix, out_prefix, quar_prefix]):
        raise BlobJobError(
            "blob_account_url, blob_container, input_prefix, "
            "output_prefix, and quarantine_prefix are required"
        )

    credential = ManagedIdentityCredential(
        client_id=managed_identity_client_id
    ) if managed_identity_client_id else ManagedIdentityCredential()

    client = BlobServiceClient(
        account_url=account_url,
        credential=credential,
    )
    container_client = client.get_container_client(container)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_base = Path(temp_dir)
        temp_input = temp_base / "input"
        temp_output = temp_base / "output"
        temp_input.mkdir(parents=True, exist_ok=True)
        temp_output.mkdir(parents=True, exist_ok=True)

        _log("blob_job_started")

        downloaded_count = 0
        for blob in container_client.list_blobs(name_starts_with=in_prefix):
            if blob.name.endswith(".zip"):
                blob_client = container_client.get_blob_client(blob.name)
                local_name = Path(blob.name).name
                local_path = temp_input / local_name

                try:
                    with open(local_path, "wb") as f:
                        download = blob_client.download_blob()
                        download.readinto(f)
                    downloaded_count += 1
                    _log(
                        "blob_downloaded",
                        blob=blob.name,
                        localPath=str(local_path),
                    )
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    _log(
                        "blob_download_error",
                        blob=blob.name,
                        error=error,
                    )
                    raise BlobJobError(
                        f"failed to download {blob.name}: {error}"
                    )

        _log(
            "blob_inputs_downloaded",
            count=downloaded_count,
        )

        connection, persist_package = _create_sql_persistence(
            sql_server=sql_server,
            sql_database=sql_database,
            managed_identity_client_id=managed_identity_client_id,
        )
        try:
            summary = run_preparation_job(
                input_dir=temp_input,
                output_dir=temp_output,
                persist_package=persist_package,
            )
        finally:
            if connection is not None:
                connection.close()

        run_id = summary.get("runId", "unknown")
        succeeded_count = 0
        failed_count = 0

        for success in summary.get("packages", []):
            output_file = success.get("output", "")
            if output_file:
                local_output_path = temp_output / output_file
                if local_output_path.exists():
                    remote_name = f"{out_prefix}{run_id}/{output_file}"
                    blob_client = container_client.get_blob_client(remote_name)
                    try:
                        with open(local_output_path, "rb") as f:
                            blob_client.upload_blob(f, overwrite=True)
                        succeeded_count += 1
                        _log(
                            "blob_output_uploaded",
                            blob=remote_name,
                            localPath=str(local_output_path),
                        )
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {exc}"
                        _log(
                            "blob_upload_error",
                            blob=remote_name,
                            error=error,
                        )
                        raise BlobJobError(
                            f"failed to upload {remote_name}: {error}"
                        )

        summary_path = temp_output / "_run-summary.json"
        if summary_path.exists():
            remote_summary = f"{out_prefix}{run_id}/_run-summary.json"
            blob_client = container_client.get_blob_client(remote_summary)
            try:
                with open(summary_path, "rb") as f:
                    blob_client.upload_blob(f, overwrite=True)
                _log(
                    "blob_summary_uploaded",
                    blob=remote_summary,
                )
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                _log(
                    "blob_summary_upload_error",
                    blob=remote_summary,
                    error=error,
                )
                raise BlobJobError(
                    f"failed to upload summary {remote_summary}: {error}"
                )

        for failure in summary.get("failures", []):
            source = failure.get("source", "")
            if source:
                source_path = temp_input / Path(source).name
                if source_path.exists():
                    remote_quarantine = (
                        f"{quar_prefix}{run_id}/{Path(source).name}"
                    )
                    blob_client = container_client.get_blob_client(
                        remote_quarantine
                    )
                    try:
                        with open(source_path, "rb") as f:
                            blob_client.upload_blob(f, overwrite=True)
                        failed_count += 1
                        _log(
                            "blob_quarantine_uploaded",
                            blob=remote_quarantine,
                            source=source,
                        )
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {exc}"
                        _log(
                            "blob_quarantine_error",
                            blob=remote_quarantine,
                            error=error,
                        )
                        raise BlobJobError(
                            f"failed to quarantine {remote_quarantine}: {error}"
                        )

        _log(
            "blob_job_completed",
            runId=run_id,
            downloadedCount=downloaded_count,
            uploadedCount=succeeded_count,
            quarantinedCount=failed_count,
        )

        summary["blob"] = {
            "downloadedCount": downloaded_count,
            "uploadedCount": succeeded_count,
            "quarantinedCount": failed_count,
            "outputPrefix": f"{out_prefix}{run_id}/",
            "quarantinePrefix": f"{quar_prefix}{run_id}/",
        }

        return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare validated InvoicePackage v1 JSON files "
            "from ABBYY Vantage ZIP packages in Azure Blob Storage."
        )
    )
    parser.add_argument(
        "--blob-account-url",
        default=os.environ.get("CAPEX_BLOB_ACCOUNT_URL"),
        required=True,
        help=(
            "Azure Blob Storage account URL "
            "(e.g., https://account.blob.core.windows.net). "
            "Required. Set CAPEX_BLOB_ACCOUNT_URL or use --blob-account-url."
        ),
    )
    parser.add_argument(
        "--blob-container",
        default=os.environ.get(
            "CAPEX_BLOB_CONTAINER",
            "test",
        ),
        help=(
            "Blob Storage container name "
            "(default: CAPEX_BLOB_CONTAINER or 'test')"
        ),
    )
    parser.add_argument(
        "--blob-input-prefix",
        default=os.environ.get(
            "CAPEX_BLOB_INPUT_PREFIX",
            "capex/input/",
        ),
        help=(
            "Input prefix for invoices in Blob Storage "
            "(default: CAPEX_BLOB_INPUT_PREFIX or 'capex/input/')"
        ),
    )
    parser.add_argument(
        "--blob-output-prefix",
        default=os.environ.get(
            "CAPEX_BLOB_OUTPUT_PREFIX",
            "capex/output/",
        ),
        help=(
            "Output prefix for prepared packages in Blob Storage "
            "(default: CAPEX_BLOB_OUTPUT_PREFIX or 'capex/output/')"
        ),
    )
    parser.add_argument(
        "--blob-quarantine-prefix",
        default=os.environ.get(
            "CAPEX_BLOB_QUARANTINE_PREFIX",
            "capex/quarantine/",
        ),
        help=(
            "Quarantine prefix for failed packages in Blob Storage "
            "(default: CAPEX_BLOB_QUARANTINE_PREFIX or 'capex/quarantine/')"
        ),
    )
    parser.add_argument(
        "--sql-server",
        default=os.environ.get("CAPEX_SQL_SERVER"),
        help=(
            "Azure SQL server FQDN. Must be provided with "
            "--sql-database to enable transactional persistence."
        ),
    )
    parser.add_argument(
        "--sql-database",
        default=os.environ.get("CAPEX_SQL_DATABASE"),
        help=(
            "Azure SQL database name. Must be provided with "
            "--sql-server to enable transactional persistence."
        ),
    )
    parser.add_argument(
        "--managed-identity-client-id",
        default=os.environ.get("AZURE_CLIENT_ID"),
        help=(
            "Client ID of the user-assigned managed identity "
            "(default: AZURE_CLIENT_ID)."
        ),
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)

    try:
        summary = run_blob_preparation_job(
            blob_account_url=args.blob_account_url,
            blob_container=args.blob_container,
            input_prefix=args.blob_input_prefix,
            output_prefix=args.blob_output_prefix,
            quarantine_prefix=args.blob_quarantine_prefix,
            managed_identity_client_id=(
                args.managed_identity_client_id
            ),
            sql_server=args.sql_server,
            sql_database=args.sql_database,
        )
    except (
        AzureSqlConfigurationError,
        AzureSqlConnectionError,
        BlobJobError,
        InvoicePreparationJobError,
        OSError,
    ) as exc:
        _log(
            "blob_preparation_job_error",
            error=f"{type(exc).__name__}: {exc}",
        )
        return 2

    return 1 if summary["failedCount"] else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BlobJobError",
    "main",
    "run_blob_preparation_job",
]
