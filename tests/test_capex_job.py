from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from autarch.capex import validate_invoice_package
from autarch.capex.persistence import InvoicePersistenceError
from autarch.capex.job import (
    InvoicePreparationJobError,
    main,
    run_preparation_job,
)


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


def _write_package(
    path: Path,
    *,
    transaction_id: str,
    invoice_number: str,
    amount: float = 10.0,
) -> Path:
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
        "Vendor/Name": "Job Test Vendor",
        "Vendor/Invoice Vendor Name": "",
        "Confidence": "99%",
        "Work Order Number": "",
    }
    line = {
        "Article Number BU": "PART-1",
        "Article Number Vendor": "",
        "Description": "Job test line",
        "Position": "1",
        "Quantity": "1",
        "Total Price": str(amount),
        "Net Price": "",
        "Unit Price": str(amount),
        "Job Type": "",
        "Project": "",
    }

    with zipfile.ZipFile(
        path,
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

    return path


def _fixed_now() -> datetime:
    return datetime(
        2026,
        10,
        19,
        2,
        0,
        0,
        tzinfo=timezone.utc,
    )


def test_job_prepares_every_valid_package(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    _write_package(
        input_dir / "first.zip",
        transaction_id="invoice-1",
        invoice_number="INV-1",
    )
    _write_package(
        input_dir / "second.zip",
        transaction_id="invoice-2",
        invoice_number="INV-2",
        amount=20.0,
    )

    summary = run_preparation_job(
        input_dir=input_dir,
        output_dir=output_dir,
        run_id="nightly-20261019",
        now=_fixed_now,
    )

    assert summary["discoveredCount"] == 2
    assert summary["succeededCount"] == 2
    assert summary["failedCount"] == 0
    assert summary["failures"] == []

    package_files = sorted(
        path
        for path in output_dir.glob("*.json")
        if path.name != "_run-summary.json"
    )
    assert len(package_files) == 2

    packages = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in package_files
    ]
    for package in packages:
        validate_invoice_package(package)
        assert package["runId"] == "nightly-20261019"
        assert package["createdAtUtc"] == (
            "2026-10-19T02:00:00.000Z"
        )

    persisted_summary = json.loads(
        (output_dir / "_run-summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted_summary == summary


def test_job_succeeds_when_no_inputs_are_available(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    summary = run_preparation_job(
        input_dir=input_dir,
        output_dir=output_dir,
        run_id="nightly-empty",
        now=_fixed_now,
    )

    assert summary["discoveredCount"] == 0
    assert summary["succeededCount"] == 0
    assert summary["failedCount"] == 0
    assert (
        output_dir / "_run-summary.json"
    ).is_file()


def test_job_isolates_bad_package_and_processes_good_package(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    (input_dir / "bad.zip").write_bytes(
        b"not-a-zip"
    )
    _write_package(
        input_dir / "good.zip",
        transaction_id="invoice-good",
        invoice_number="INV-GOOD",
    )

    summary = run_preparation_job(
        input_dir=input_dir,
        output_dir=output_dir,
        run_id="nightly-partial",
        now=_fixed_now,
    )

    assert summary["discoveredCount"] == 2
    assert summary["succeededCount"] == 1
    assert summary["failedCount"] == 1
    assert summary["failures"][0]["source"] == "bad.zip"
    assert "valid ZIP" in summary["failures"][0]["error"]


def test_cli_exit_codes_distinguish_configuration_and_package_failures(
    tmp_path: Path,
) -> None:
    missing_input = tmp_path / "missing"
    output_dir = tmp_path / "output"

    assert main(
        [
            "--input-dir",
            str(missing_input),
            "--output-dir",
            str(output_dir),
        ]
    ) == 2

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "bad.zip").write_bytes(
        b"not-a-zip"
    )

    assert main(
        [
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--run-id",
            "nightly-failed",
        ]
    ) == 1


def test_job_rejects_same_input_and_output_directory(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        InvoicePreparationJobError,
        match="must be different",
    ):
        run_preparation_job(
            input_dir=tmp_path,
            output_dir=tmp_path,
            run_id="nightly-invalid",
            now=_fixed_now,
        )


def test_capex_dockerfile_runs_the_finite_production_job() -> None:
    dockerfile = (
        Path(__file__).parents[1] / "Dockerfile.capex"
    ).read_text(encoding="utf-8-sig")

    assert "FROM python:3.12-slim" in dockerfile
    assert "ENV CAPEX_INPUT_DIR=/input" in dockerfile
    assert "ENV CAPEX_OUTPUT_DIR=/output" in dockerfile
    assert "msodbcsql18" in dockerfile
    assert "USER capex" in dockerfile
    assert 'ENTRYPOINT ["capex-prepare-blob"]' in dockerfile
    assert "examples/data/capex" not in dockerfile
    assert "examples/capex_flow.py" not in dockerfile
    assert "`" not in dockerfile


def test_container_apps_job_has_governed_nightly_configuration() -> None:
    bicep = (
        Path(__file__).parents[1]
        / "infra"
        / "container-apps"
        / "capex-invoice-preparation-job.bicep"
    ).read_text(encoding="utf-8-sig")

    required_fragments = [
        "Microsoft.App/jobs@2025-01-01",
        "triggerType: scheduleEnabled ? 'Schedule' : 'Manual'",
        "param scheduleEnabled bool = true",
        "cronExpression string = '0 2 * * *'",
        "parallelism: 1",
        "replicaCompletionCount: 1",
        "replicaRetryLimit int = 2",
        "replicaTimeoutSeconds int = 1800",
        "type: 'UserAssigned'",
        "param userAssignedIdentityResourceId string",
        "param userAssignedIdentityClientId string",
        "identity: userAssignedIdentityResourceId",
        "name: 'AZURE_CLIENT_ID'",
        "name: 'CAPEX_INPUT_DIR'",
        "value: '/input'",
        "name: 'CAPEX_OUTPUT_DIR'",
        "name: 'CAPEX_SQL_SERVER'",
        "name: 'CAPEX_SQL_DATABASE'",
        "value: '/output'",
    ]
    for fragment in required_fragments:
        assert fragment in bicep

    lowered = bicep.lower()
    assert "connectionstring" not in lowered
    assert "accountkey" not in lowered
    assert "password" not in lowered
    assert "systemassigned" not in lowered


def test_docker_build_context_excludes_private_capex_inputs() -> None:
    dockerignore = (
        Path(__file__).parents[1] / ".dockerignore"
    ).read_text(encoding="utf-8-sig").splitlines()

    rules = {
        line.strip().rstrip("/")
        for line in dockerignore
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "examples/data/capex" in rules


def test_job_persists_each_successful_package(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    _write_package(
        input_dir / "invoice.zip",
        transaction_id="invoice-persisted",
        invoice_number="INV-PERSISTED",
    )
    persisted: list[dict[str, Any]] = []

    def persist(
        package: dict[str, Any],
    ) -> dict[str, Any]:
        persisted.append(package)
        return {"eventId": "event-1"}

    summary = run_preparation_job(
        input_dir=input_dir,
        output_dir=output_dir,
        run_id="nightly-persisted",
        now=_fixed_now,
        persist_package=persist,
    )

    assert summary["succeededCount"] == 1
    assert summary["failedCount"] == 0
    assert len(persisted) == 1
    assert persisted[0]["runId"] == "nightly-persisted"


def test_job_records_persistence_failure_per_package(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    _write_package(
        input_dir / "invoice.zip",
        transaction_id="invoice-failed",
        invoice_number="INV-FAILED",
    )

    def persist(
        package: dict[str, Any],
    ) -> dict[str, Any]:
        raise InvoicePersistenceError(
            "database unavailable"
        )

    summary = run_preparation_job(
        input_dir=input_dir,
        output_dir=output_dir,
        run_id="nightly-persistence-failure",
        now=_fixed_now,
        persist_package=persist,
    )

    assert summary["succeededCount"] == 0
    assert summary["failedCount"] == 1
    assert "database unavailable" in (
        summary["failures"][0]["error"]
    )
