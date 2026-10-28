"""Fetch invoice packages from Blob Storage for agent processing."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient

logger = logging.getLogger(__name__)


class BlobPackageFetchError(RuntimeError):
    """Raised when fetching from Blob Storage fails."""


def fetch_invoice_package_from_blob(
    *,
    blob_account_url: str,
    blob_container: str,
    package_id: str,
    run_id: str,
    managed_identity_client_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch a prepared invoice package JSON from Blob Storage."""

    account_url = str(blob_account_url).strip()
    container = str(blob_container).strip()

    if not all([account_url, container, package_id, run_id]):
        raise BlobPackageFetchError(
            "blob_account_url, blob_container, package_id, and run_id are required"
        )

    credential = (
        ManagedIdentityCredential(client_id=managed_identity_client_id)
        if managed_identity_client_id
        else ManagedIdentityCredential()
    )

    try:
        client = BlobServiceClient(
            account_url=account_url,
            credential=credential,
        )
        container_client = client.get_container_client(container)

        # Try standard output path first: capex/output/<run-id>/<package-id>.json
        blob_name = f"capex/output/{run_id}/{package_id}.json"
        blob_client = container_client.get_blob_client(blob_name)

        try:
            download = blob_client.download_blob()
            package_json = download.readall().decode("utf-8")
            package = json.loads(package_json)

            logger.info(
                "invoice_package_fetched",
                extra={
                    "blob": blob_name,
                    "packageId": package_id,
                    "runId": run_id,
                },
            )

            return package

        except Exception as e:
            logger.warning(
                "blob_not_found_at_standard_path",
                extra={
                    "blob": blob_name,
                    "error": str(e),
                },
            )
            # Could implement fallback logic here (e.g., list all outputs for run_id)
            raise BlobPackageFetchError(
                f"Invoice package not found at {blob_name}: {e}"
            ) from e

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.error(
            "blob_fetch_error",
            extra={
                "packageId": package_id,
                "runId": run_id,
                "error": error_msg,
            },
        )
        raise BlobPackageFetchError(
            f"Failed to fetch invoice package: {error_msg}"
        ) from exc


__all__ = [
    "BlobPackageFetchError",
    "fetch_invoice_package_from_blob",
]
