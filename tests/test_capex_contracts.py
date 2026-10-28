from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
EXAMPLES = CONTRACTS / "examples"
EXAMPLE_CODE = ROOT / "examples"

if str(EXAMPLE_CODE) not in sys.path:
    sys.path.insert(0, str(EXAMPLE_CODE))

from autarch.capex import (  # noqa: E402
    InvoicePackageAssemblyError,
    build_invoice_package,
    derive_package_id,
    validate_invoice_package,
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validator(schema_name: str) -> Draft202012Validator:
    schema = load_json(CONTRACTS / schema_name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )


PACKAGE_FILES = [
    "invoice-package-v1-bruegmann.json",
    "invoice-package-v1-kimley-horn.json",
]

EVENT_PACKAGE_PAIRS = [
    (
        "invoice-ready-v1-bruegmann.json",
        "invoice-package-v1-bruegmann.json",
    ),
    (
        "invoice-ready-v1-kimley-horn.json",
        "invoice-package-v1-kimley-horn.json",
    ),
]


@pytest.mark.parametrize("filename", PACKAGE_FILES)
def test_invoice_package_examples_validate(filename: str) -> None:
    validator("invoice-package-v1.schema.json").validate(
        load_json(EXAMPLES / filename)
    )


def test_packages_preserve_both_invoices_and_all_lines() -> None:
    packages = [
        load_json(EXAMPLES / filename)
        for filename in PACKAGE_FILES
    ]

    assert {
        package["invoice"]["invoiceNumber"]
        for package in packages
    } == {
        "INV12-62027",
        "36764068-GRP005",
    }

    assert sum(len(package["lines"]) for package in packages) == 22


@pytest.mark.parametrize(
    "event_name,package_name",
    EVENT_PACKAGE_PAIRS,
)
def test_events_validate_and_reference_their_packages(
    event_name: str,
    package_name: str,
) -> None:
    event = load_json(EXAMPLES / event_name)
    package = load_json(EXAMPLES / package_name)

    validator("invoice-ready-v1.schema.json").validate(event)

    assert event["invoiceId"] == package["invoice"]["invoiceId"]
    assert event["packageId"] == package["packageId"]
    assert event["runId"] == package["runId"]
    assert event["packageUri"].endswith(package["packageId"])
    assert "lines" not in event
    assert "invoice" not in event


@pytest.mark.parametrize(
    "schema_name,example_name",
    [
        (
            "invoice-package-v1.schema.json",
            "invoice-package-v1-bruegmann.json",
        ),
        (
            "invoice-ready-v1.schema.json",
            "invoice-ready-v1-bruegmann.json",
        ),
    ],
)
def test_unknown_schema_version_is_rejected(
    schema_name: str,
    example_name: str,
) -> None:
    payload = load_json(EXAMPLES / example_name)
    payload["schemaVersion"] = "2.0"

    with pytest.raises(ValidationError):
        validator(schema_name).validate(payload)


def test_missing_required_property_is_rejected() -> None:
    payload = load_json(
        EXAMPLES / "invoice-package-v1-bruegmann.json"
    )
    del payload["packageId"]

    with pytest.raises(ValidationError):
        validator("invoice-package-v1.schema.json").validate(payload)


@pytest.mark.parametrize(
    "example_name,path,value",
    [
        (
            "invoice-package-v1-bruegmann.json",
            ("source", "sourceSha256"),
            "not-a-sha256",
        ),
        (
            "invoice-package-v1-bruegmann.json",
            ("createdAtUtc",),
            "not-a-timestamp",
        ),
        (
            "invoice-ready-v1-bruegmann.json",
            ("occurredAtUtc",),
            "not-a-timestamp",
        ),
    ],
)
def test_invalid_hash_and_timestamps_are_rejected(
    example_name: str,
    path: tuple[str, ...],
    value: str,
) -> None:
    payload = copy.deepcopy(load_json(EXAMPLES / example_name))
    target: dict[str, Any] = payload

    for key in path[:-1]:
        target = target[key]

    target[path[-1]] = value

    schema_name = (
        "invoice-ready-v1.schema.json"
        if example_name.startswith("invoice-ready")
        else "invoice-package-v1.schema.json"
    )

    with pytest.raises(ValidationError):
        validator(schema_name).validate(payload)


def test_adapter_does_not_fabricate_missing_context() -> None:
    invoice = {
        "invoice_id": "invoice-1",
        "invoice_number": "TEST-1",
        "invoice_date": "2026-10-16",
        "vendor": "Test Vendor",
        "country": "US",
        "region": "North America",
        "currency": "USD",
        "total": 10.0,
        "afe_number": "",
        "project_number": "",
        "source": "test.zip",
        "source_sha256": "a" * 64,
        "lines": [
            {
                "line_id": "1",
                "description": "Test line",
                "quantity": 1.0,
                "unit_price": 10.0,
                "amount": 10.0,
                "actual_task": "",
            }
        ],
    }

    package = build_invoice_package(
        invoice,
        package_id="package-1",
        run_id="run-1",
        created_at_utc="2026-10-16T19:00:00Z",
    )

    validator("invoice-package-v1.schema.json").validate(package)

    assert package["invoice"]["poNumber"] is None
    assert package["lines"][0]["afeProjectId"] is None
    assert package["lines"][0]["siteId"] is None
    assert package["dataQuality"]["missingPo"] is True
    assert package["dataQuality"]["missingProject"] is True
    assert package["dataQuality"]["missingSite"] is True


def _normalized_invoice() -> dict[str, Any]:
    return {
        "invoice_id": "invoice-assembly-1",
        "invoice_number": "ASSEMBLY-1",
        "invoice_date": "2026-10-19",
        "vendor_id": "vendor-1",
        "vendor": "Assembly Vendor",
        "country": "US",
        "region": "North America",
        "currency": "USD",
        "total": 10.0,
        "po_number": "",
        "afe_number": "",
        "project_number": "",
        "source": "assembly-test.zip",
        "source_sha256": "b" * 64,
        "lines": [
            {
                "line_id": "1",
                "description": "Assembly test line",
                "part_number": "PART-1",
                "quantity": 1.0,
                "unit_price": 10.0,
                "amount": 10.0,
                "actual_task": "",
            }
        ],
    }


def test_packaged_schema_matches_canonical_contract() -> None:
    from importlib import resources

    canonical = load_json(
        CONTRACTS / "invoice-package-v1.schema.json"
    )
    packaged = json.loads(
        resources.files("autarch.capex.schemas")
        .joinpath("invoice-package-v1.schema.json")
        .read_text(encoding="utf-8")
    )

    assert packaged == canonical


def test_derived_package_id_is_stable_within_run() -> None:
    invoice = _normalized_invoice()

    first = build_invoice_package(
        invoice,
        run_id="run-1",
        created_at_utc="2026-10-19T13:00:00Z",
    )
    second = build_invoice_package(
        invoice,
        run_id="run-1",
        created_at_utc="2026-10-19T13:00:00Z",
    )

    assert first["packageId"] == second["packageId"]
    assert first["packageId"] == derive_package_id(
        "run-1",
        "invoice-assembly-1",
    )
    assert first["packageId"] != derive_package_id(
        "run-2",
        "invoice-assembly-1",
    )


def test_assembly_maps_explicit_business_context() -> None:
    invoice = _normalized_invoice()
    invoice.update(
        {
            "po_number": "PO-1",
            "afe_number": "AFE-1",
            "site_id": "SITE-1",
            "business_unit_id": "BU-1",
        }
    )

    package = build_invoice_package(
        invoice,
        run_id="run-context",
        created_at_utc="2026-10-19T13:00:00Z",
        project_status="Approved",
        project_type="New Store",
        bundling_in_scope=True,
    )

    assert package["context"] == {
        "businessUnit": "BU-1",
        "projectStatus": "Approved",
        "projectType": "New Store",
        "bundlingInScope": True,
    }
    assert package["lines"][0]["afeProjectId"] == "AFE-1"
    assert package["lines"][0]["siteId"] == "SITE-1"
    assert package["dataQuality"]["missingPo"] is False
    assert package["dataQuality"]["missingProject"] is False
    assert package["dataQuality"]["missingSite"] is False
    assert package["dataQuality"]["warnings"] == []


def test_assembly_requires_utc_created_timestamp() -> None:
    with pytest.raises(
        InvoicePackageAssemblyError,
        match="must include a UTC offset",
    ):
        build_invoice_package(
            _normalized_invoice(),
            run_id="run-non-utc",
            created_at_utc="2026-10-19T14:00:00+01:00",
        )


def test_validation_error_reports_contract_path() -> None:
    package = build_invoice_package(
        _normalized_invoice(),
        run_id="run-validation",
        created_at_utc="2026-10-19T13:00:00Z",
    )
    package["source"]["sourceSha256"] = "invalid"

    with pytest.raises(
        InvoicePackageAssemblyError,
        match=r"source\.sourceSha256",
    ):
        validate_invoice_package(package)


def test_assembly_rejects_invoice_without_lines() -> None:
    invoice = _normalized_invoice()
    invoice["lines"] = []

    with pytest.raises(
        InvoicePackageAssemblyError,
        match="at least one line",
    ):
        build_invoice_package(
            invoice,
            run_id="run-no-lines",
            created_at_utc="2026-10-19T13:00:00Z",
        )


def test_assembly_rejects_duplicate_source_line_ids() -> None:
    invoice = _normalized_invoice()
    invoice["lines"].append(
        dict(invoice["lines"][0])
    )

    with pytest.raises(
        InvoicePackageAssemblyError,
        match="duplicate invoice line identifier",
    ):
        build_invoice_package(
            invoice,
            run_id="run-duplicate-lines",
            created_at_utc="2026-10-19T13:00:00Z",
        )

