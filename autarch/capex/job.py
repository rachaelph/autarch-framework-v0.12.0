"""Finite nightly Invoice Preparation Job for Azure Container Apps."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional
from uuid import uuid4

from autarch.adapters import ABBYYInvoiceAdapter
from autarch.contracts import Action

from .invoice_package import (
    InvoicePackageAssemblyError,
    build_invoice_package,
)
from .persistence import (
    InvoicePersistenceError,
    persist_invoice_package,
)
from .sql_connection import (
    AzureSqlConfigurationError,
    AzureSqlConnectionError,
    connect_azure_sql,
)


PersistPackage = Callable[
    [Mapping[str, Any]],
    Mapping[str, Any],
]


class InvoicePreparationJobError(RuntimeError):
    """Raised when the preparation job cannot run safely."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _default_run_id(started_at: datetime) -> str:
    timestamp = (
        started_at.astimezone(timezone.utc)
        .strftime("%Y%m%dT%H%M%SZ")
    )
    return f"run-{timestamp}-{uuid4().hex[:8]}"


def _write_json_atomic(
    path: Path,
    value: Dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{uuid4().hex}.tmp"
    )

    try:
        temporary.write_text(
            json.dumps(
                value,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _log(event: str, **fields: Any) -> None:
    print(
        json.dumps(
            {
                "event": event,
                **fields,
            },
            sort_keys=True,
        ),
        flush=True,
    )


def run_preparation_job(
    *,
    input_dir: Path,
    output_dir: Path,
    run_id: Optional[str] = None,
    now: Callable[[], datetime] = _utc_now,
    persist_package: Optional[PersistPackage] = None,
) -> Dict[str, Any]:
    """Parse, assemble, validate, and stage every ABBYY ZIP in one run."""
    resolved_input = Path(input_dir).resolve()
    resolved_output = Path(output_dir).resolve()

    if not resolved_input.is_dir():
        raise InvoicePreparationJobError(
            f"input directory does not exist: {resolved_input}"
        )
    if resolved_input == resolved_output:
        raise InvoicePreparationJobError(
            "input and output directories must be different"
        )

    resolved_output.mkdir(parents=True, exist_ok=True)

    started = now()
    started_at = _utc_text(started)
    effective_run_id = (
        str(run_id).strip()
        if run_id is not None
        else _default_run_id(started)
    )
    if not effective_run_id:
        raise InvoicePreparationJobError(
            "run_id cannot be empty"
        )

    adapter = ABBYYInvoiceAdapter(
        str(resolved_input)
    )
    listed = adapter.execute(
        Action("invoice.list_abbyy")
    )
    if not listed.ok:
        raise InvoicePreparationJobError(
            listed.error or "failed to list ABBYY inputs"
        )

    package_paths = list(
        listed.output.get("packages", [])
    )

    _log(
        "invoice_preparation_started",
        runId=effective_run_id,
        startedAtUtc=started_at,
        discoveredCount=len(package_paths),
    )

    successes: List[Dict[str, Any]] = []
    failures: List[Dict[str, str]] = []
    seen_package_ids = set()

    for relative_path in package_paths:
        read_result = adapter.execute(
            Action(
                "invoice.read_abbyy",
                {"path": relative_path},
            )
        )

        if not read_result.ok:
            error = (
                read_result.error
                or "ABBYY adapter returned no error"
            )
            failures.append(
                {
                    "source": relative_path,
                    "error": error,
                }
            )
            _log(
                "invoice_preparation_failed",
                runId=effective_run_id,
                source=relative_path,
                error=error,
            )
            continue

        try:
            package = build_invoice_package(
                read_result.output,
                run_id=effective_run_id,
                created_at_utc=started_at,
            )
            package_id = package["packageId"]

            if package_id in seen_package_ids:
                raise InvoicePreparationJobError(
                    f"duplicate package ID in run: {package_id}"
                )
            seen_package_ids.add(package_id)

            output_name = f"{package_id}.json"
            output_path = resolved_output / output_name

            _write_json_atomic(
                output_path,
                package,
            )

            if persist_package is not None:
                persist_package(package)

            success = {
                "source": relative_path,
                "invoiceId": package["invoice"][
                    "invoiceId"
                ],
                "packageId": package_id,
                "output": output_name,
            }
            successes.append(success)

            _log(
                "invoice_preparation_succeeded",
                runId=effective_run_id,
                **success,
            )
        except (
            InvoicePackageAssemblyError,
            InvoicePreparationJobError,
            InvoicePersistenceError,
            OSError,
        ) as exc:
            error = f"{type(exc).__name__}: {exc}"
            failures.append(
                {
                    "source": relative_path,
                    "error": error,
                }
            )
            _log(
                "invoice_preparation_failed",
                runId=effective_run_id,
                source=relative_path,
                error=error,
            )

    completed_at = _utc_text(now())
    summary: Dict[str, Any] = {
        "runId": effective_run_id,
        "startedAtUtc": started_at,
        "completedAtUtc": completed_at,
        "discoveredCount": len(package_paths),
        "succeededCount": len(successes),
        "failedCount": len(failures),
        "packages": successes,
        "failures": failures,
    }

    _write_json_atomic(
        resolved_output / "_run-summary.json",
        summary,
    )

    _log(
        "invoice_preparation_completed",
        runId=effective_run_id,
        completedAtUtc=completed_at,
        discoveredCount=len(package_paths),
        succeededCount=len(successes),
        failedCount=len(failures),
    )

    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare validated InvoicePackage v1 JSON files "
            "from ABBYY Vantage ZIP packages."
        )
    )
    parser.add_argument(
        "--input-dir",
        default=os.environ.get(
            "CAPEX_INPUT_DIR",
            "/input",
        ),
        help=(
            "ABBYY ZIP input directory "
            "(default: CAPEX_INPUT_DIR or /input)"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get(
            "CAPEX_OUTPUT_DIR",
            "/output",
        ),
        help=(
            "Validated package output directory "
            "(default: CAPEX_OUTPUT_DIR or /output)"
        ),
    )
    parser.add_argument(
        "--run-id",
        default=os.environ.get("CAPEX_RUN_ID"),
        help=(
            "Stable execution ID for retries "
            "(default: CAPEX_RUN_ID or generated)"
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
    connection = None

    try:
        has_server = bool(
            str(args.sql_server).strip()
            if args.sql_server is not None
            else ""
        )
        has_database = bool(
            str(args.sql_database).strip()
            if args.sql_database is not None
            else ""
        )
        if has_server != has_database:
            raise AzureSqlConfigurationError(
                "CAPEX_SQL_SERVER and CAPEX_SQL_DATABASE "
                "must be configured together."
            )

        persist_package = None
        if has_server:
            connection = connect_azure_sql(
                server_fqdn=args.sql_server,
                database_name=args.sql_database,
                managed_identity_client_id=(
                    args.managed_identity_client_id
                ),
            )
            persist_package = lambda package: (
                persist_invoice_package(
                    connection,
                    package,
                )
            )

        summary = run_preparation_job(
            input_dir=Path(args.input_dir),
            output_dir=Path(args.output_dir),
            run_id=args.run_id,
            persist_package=persist_package,
        )
    except (
        AzureSqlConfigurationError,
        AzureSqlConnectionError,
        InvoicePreparationJobError,
        OSError,
    ) as exc:
        _log(
            "invoice_preparation_job_error",
            error=f"{type(exc).__name__}: {exc}",
        )
        return 2
    finally:
        if connection is not None:
            close = getattr(connection, "close", None)
            if callable(close):
                close()

    return 1 if summary["failedCount"] else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "InvoicePreparationJobError",
    "main",
    "run_preparation_job",
]
