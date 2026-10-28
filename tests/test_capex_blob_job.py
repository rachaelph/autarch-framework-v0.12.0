"""Tests for Blob-staged invoice preparation job."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from autarch.capex import blob_job
from autarch.capex.blob_job import (
    BlobJobError,
    run_blob_preparation_job,
)
from autarch.capex.sql_connection import AzureSqlConfigurationError


HEADER_FIELDS = [
    "Transaction id",
    "Invoice Number",
    "Invoice Date",
    "Business Unit/Country",
    "Business Unit/ID",
    "Business Unit/State",
    "Currency",
    "Total",
    "Vendor/ID",
    "Vendor/Name",
    "Vendor/Invoice Vendor Name",
    "Confidence",
    "Work Order Number",
]

LINE_FIELDS = [
    "Article Number BU",
    "Article Number Vendor",
    "Description",
    "Position",
    "Quantity",
    "Total Price",
    "Net Price",
    "Unit Price",
    "Job Type",
    "Project",
]


def _csv_bytes(
    fieldnames: list[str],
    rows: list[dict[str, Any]],
) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=fieldnames,
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _make_invoice_zip(
    transaction_id: str,
    invoice_number: str,
    amount: float = 10.0,
) -> bytes:
    header = {
        "Transaction id": transaction_id,
        "Invoice Number": invoice_number,
        "Invoice Date": "2026-10-19",
        "Business Unit/Country": "US",
        "Business Unit/ID": "BU-1",
        "Business Unit/State": "MN",
        "Currency": "USD",
        "Total": str(amount),
        "Vendor/ID": "vendor-1",
        "Vendor/Name": "Test Vendor",
        "Vendor/Invoice Vendor Name": "",
        "Confidence": "99%",
        "Work Order Number": "",
    }
    line = {
        "Article Number BU": "PART-1",
        "Article Number Vendor": "",
        "Description": "Test line",
        "Position": "1",
        "Quantity": "1",
        "Total Price": str(amount),
        "Net Price": "",
        "Unit Price": str(amount),
        "Job Type": "",
        "Project": "",
    }

    stream = io.BytesIO()
    with zipfile.ZipFile(
        stream,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(
            "package/Non-Trade Invoices - US.csv",
            _csv_bytes(HEADER_FIELDS, [header]),
        )
        archive.writestr(
            "package/Line Items_1.csv",
            _csv_bytes(LINE_FIELDS, [line]),
        )
    return stream.getvalue()


def test_blob_job_requires_all_configuration() -> None:
    with pytest.raises(BlobJobError, match="required"):
        run_blob_preparation_job(
            blob_account_url="",
            blob_container="test",
        )


def test_blob_job_downloads_prepares_uploads():
    """Test complete flow: download, prepare, upload."""
    invoice_zip = _make_invoice_zip(
        transaction_id="tx-1",
        invoice_number="INV-1",
    )

    mock_blob = MagicMock()
    mock_blob.name = "capex/input/invoice1.zip"

    mock_blob_client = MagicMock()
    mock_blob_client.download_blob.return_value.readinto = (
        lambda f: f.write(invoice_zip)
    )

    mock_container_client = MagicMock()
    mock_container_client.list_blobs.return_value = [mock_blob]
    mock_container_client.get_blob_client.return_value = mock_blob_client

    mock_service_client = MagicMock()
    mock_service_client.get_container_client.return_value = (
        mock_container_client
    )

    with patch(
        "autarch.capex.blob_job.BlobServiceClient",
        return_value=mock_service_client,
    ):
        with patch(
            "autarch.capex.blob_job.ManagedIdentityCredential"
        ):
            summary = run_blob_preparation_job(
                blob_account_url="https://test.blob.core.windows.net",
                blob_container="testcontainer",
                input_prefix="capex/input/",
                output_prefix="capex/output/",
                quarantine_prefix="capex/quarantine/",
                managed_identity_client_id="client-id",
            )

    assert summary is not None
    assert "runId" in summary
    assert summary.get("succeededCount") == 1
    assert summary.get("failedCount") == 0
    assert "blob" in summary
    assert summary["blob"]["downloadedCount"] == 1
    assert summary["blob"]["uploadedCount"] == 1


def test_blob_job_quarantines_failures():
    """Test that failed packages are quarantined."""
    bad_zip = b"not a valid zip"

    mock_blob = MagicMock()
    mock_blob.name = "capex/input/bad.zip"

    mock_blob_client = MagicMock()
    mock_blob_client.download_blob.return_value.readinto = (
        lambda f: f.write(bad_zip)
    )

    mock_container_client = MagicMock()
    mock_container_client.list_blobs.return_value = [mock_blob]
    mock_container_client.get_blob_client.return_value = mock_blob_client

    mock_service_client = MagicMock()
    mock_service_client.get_container_client.return_value = (
        mock_container_client
    )

    with patch(
        "autarch.capex.blob_job.BlobServiceClient",
        return_value=mock_service_client,
    ):
        with patch(
            "autarch.capex.blob_job.ManagedIdentityCredential"
        ):
            summary = run_blob_preparation_job(
                blob_account_url="https://test.blob.core.windows.net",
                blob_container="testcontainer",
            )

    assert summary["failedCount"] == 1
    assert summary["blob"]["downloadedCount"] == 1
    assert summary["blob"]["uploadedCount"] == 0
    assert summary["blob"]["quarantinedCount"] == 1


def test_blob_job_skips_non_zip_blobs():
    """Test that non-.zip blobs are skipped."""
    mock_blob_csv = MagicMock()
    mock_blob_csv.name = "capex/input/readme.txt"

    mock_blob_zip = MagicMock()
    mock_blob_zip.name = "capex/input/invoice.zip"

    invoice_zip = _make_invoice_zip(
        transaction_id="tx-1",
        invoice_number="INV-1",
    )

    mock_blob_client = MagicMock()
    mock_blob_client.download_blob.return_value.readinto = (
        lambda f: f.write(invoice_zip)
    )

    mock_container_client = MagicMock()
    mock_container_client.list_blobs.return_value = [
        mock_blob_csv,
        mock_blob_zip,
    ]
    mock_container_client.get_blob_client.return_value = mock_blob_client

    mock_service_client = MagicMock()
    mock_service_client.get_container_client.return_value = (
        mock_container_client
    )

    with patch(
        "autarch.capex.blob_job.BlobServiceClient",
        return_value=mock_service_client,
    ):
        with patch(
            "autarch.capex.blob_job.ManagedIdentityCredential"
        ):
            summary = run_blob_preparation_job(
                blob_account_url="https://test.blob.core.windows.net",
                blob_container="testcontainer",
            )

    assert summary["blob"]["downloadedCount"] == 1
    assert summary["succeededCount"] == 1


def test_blob_job_download_error_raises():
    """Test that download errors are properly raised."""
    mock_blob = MagicMock()
    mock_blob.name = "capex/input/invoice.zip"

    mock_blob_client = MagicMock()
    mock_blob_client.download_blob.side_effect = Exception("Network error")

    mock_container_client = MagicMock()
    mock_container_client.list_blobs.return_value = [mock_blob]
    mock_container_client.get_blob_client.return_value = mock_blob_client

    mock_service_client = MagicMock()
    mock_service_client.get_container_client.return_value = (
        mock_container_client
    )

    with patch(
        "autarch.capex.blob_job.BlobServiceClient",
        return_value=mock_service_client,
    ):
        with patch(
            "autarch.capex.blob_job.ManagedIdentityCredential"
        ):
            with pytest.raises(BlobJobError, match="failed to download"):
                run_blob_preparation_job(
                    blob_account_url="https://test.blob.core.windows.net",
                    blob_container="testcontainer",
                )


def test_sql_persistence_requires_server_and_database() -> None:
    with pytest.raises(
        AzureSqlConfigurationError,
        match="must be configured together",
    ):
        blob_job._create_sql_persistence(
            sql_server="sql.example.net",
            sql_database=None,
            managed_identity_client_id="client-id",
        )


def test_sql_persistence_is_disabled_when_unconfigured() -> None:
    connection, persist_package = blob_job._create_sql_persistence(
        sql_server=None,
        sql_database=None,
        managed_identity_client_id="client-id",
    )

    assert connection is None
    assert persist_package is None


def test_sql_persistence_uses_managed_identity() -> None:
    connection = Mock()
    package = {"packageId": "pkg-1"}

    with patch(
        "autarch.capex.blob_job.connect_azure_sql",
        return_value=connection,
    ) as connect:
        with patch(
            "autarch.capex.blob_job.persist_invoice_package",
            return_value={"outboxEventId": "event-1"},
        ) as persist:
            actual_connection, persist_package = (
                blob_job._create_sql_persistence(
                    sql_server="sql.example.net",
                    sql_database="capex",
                    managed_identity_client_id="client-id",
                )
            )
            assert persist_package is not None
            result = persist_package(package)

    assert actual_connection is connection
    assert result == {"outboxEventId": "event-1"}
    connect.assert_called_once_with(
        server_fqdn="sql.example.net",
        database_name="capex",
        managed_identity_client_id="client-id",
    )
    persist.assert_called_once_with(connection, package)
