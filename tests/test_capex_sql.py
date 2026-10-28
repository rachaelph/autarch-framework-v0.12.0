from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "sql"
MIGRATIONS = SQL / "migrations"
SQL_TESTS = SQL / "tests"
CONTRACTS = ROOT / "contracts"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_sql_assets_use_ordered_migrations() -> None:
    assert [
        path.name
        for path in sorted(MIGRATIONS.glob("*.sql"))
    ] == [
        "001_create_invoice_operational_store.sql",
        "002_create_published_invoice_package_view.sql",
        "003_create_invoice_outbox.sql",
        "004_create_task_suggestion_table.sql",
        "005_create_task_master.sql",
        "006_align_task_suggestion.sql",
    ]

    assert (
        SQL_TESTS / "001_verify_invoice_operational_store.sql"
    ).is_file()


def test_operational_store_migration_defines_required_model() -> None:
    migration = read_text(
        MIGRATIONS / "001_create_invoice_operational_store.sql"
    )

    required_fragments = [
        "CREATE SCHEMA prep",
        "CREATE SCHEMA pub",
        "CREATE TABLE prep.InvoicePackage",
        "CREATE TABLE prep.InvoiceLine",
        "CREATE TABLE prep.InvoiceDataQualityWarning",
        "PRIMARY KEY (PackageId)",
        "UNIQUE (RunId, InvoiceId)",
        "REFERENCES prep.InvoicePackage (PackageId)",
        "ON DELETE CASCADE",
        "CHECK (SchemaVersion = '1.0')",
        "PackageStatus IN",
        "SourceSha256",
        "CreatedAtUtc",
        "BEGIN TRANSACTION",
        "COMMIT TRANSACTION",
        "ROLLBACK TRANSACTION",
        "THROW",
    ]

    for fragment in required_fragments:
        assert fragment in migration

    assert not re.search(r"\bDROP\s+(TABLE|SCHEMA)\b", migration, re.I)


def test_published_view_covers_invoice_package_contract() -> None:
    view_sql = read_text(
        MIGRATIONS
        / "002_create_published_invoice_package_view.sql"
    )
    schema = json.loads(
        (
            CONTRACTS / "invoice-package-v1.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert "CREATE OR ALTER VIEW pub.v1_InvoicePackage" in view_sql

    for property_name in schema["properties"]:
        assert re.search(
            rf"\bAS\s+{re.escape(property_name)}\b",
            view_sql,
            re.I,
        ), f"Published view is missing {property_name}"

    nested_properties = set()

    for section in (
        "invoice",
        "context",
        "dataQuality",
        "source",
    ):
        nested_properties.update(
            schema["properties"][section]["properties"]
        )

    nested_properties.update(
        schema["properties"]["lines"]["items"]["properties"]
    )

    for property_name in nested_properties:
        assert re.search(
            rf"\bAS\s+{re.escape(property_name)}\b",
            view_sql,
            re.I,
        ), f"Published view is missing nested field {property_name}"

    assert "p.PackageStatus = 'READY'" in view_sql
    assert "EXISTS" in view_sql
    assert "ORDER BY line.LineNumber" in view_sql
    assert "ORDER BY warning.WarningOrdinal" in view_sql
    assert "INCLUDE_NULL_VALUES" in view_sql
    assert "STRING_ESCAPE" in view_sql


def test_sql_verification_is_transactional_and_non_destructive() -> None:
    verification = read_text(
        SQL_TESTS / "001_verify_invoice_operational_store.sql"
    )

    required_fragments = [
        "BEGIN TRANSACTION",
        "PackageStatus = 'READY'",
        "FROM pub.v1_InvoicePackage",
        "OPENJSON(@Lines)",
        "JSON_QUERY(@DataQuality, '$.warnings')",
        "ROLLBACK TRANSACTION",
        "Invoice Operational Store verification passed.",
    ]

    for fragment in required_fragments:
        assert fragment in verification

    assert "COMMIT TRANSACTION" not in verification
    assert not re.search(
        r"\bDROP\s+(TABLE|SCHEMA|DATABASE)\b",
        verification,
        re.I,
    )


def test_sql_readme_documents_contract_boundary() -> None:
    readme = read_text(SQL / "README.md")

    assert "`prep` is the durable operational schema" in readme
    assert "`pub` exposes versioned, read-only contracts" in readme
    assert "invoice-package-v1.schema.json" in readme
    assert "Raw ABBYY documents are not stored" in readme



def test_outbox_migration_defines_durable_dispatch_contract() -> None:
    migration = read_text(
        MIGRATIONS / "003_create_invoice_outbox.sql"
    )

    required_fragments = [
        "CREATE TABLE prep.InvoiceOutbox",
        "PRIMARY KEY (EventId)",
        "UNIQUE",
        "PackageId",
        "EventType",
        "EventVersion",
        "CorrelationId",
        "Payload",
        "ISJSON(Payload) = 1",
        "OutboxStatus",
        "'PENDING'",
        "'PUBLISHING'",
        "'PUBLISHED'",
        "'FAILED'",
        "AttemptCount",
        "AvailableAtUtc",
        "PublishedAtUtc",
        "REFERENCES prep.InvoicePackage (PackageId)",
        "ON DELETE CASCADE",
        "CREATE INDEX IX_InvoiceOutbox_Dispatch",
        "BEGIN TRANSACTION",
        "COMMIT TRANSACTION",
        "ROLLBACK TRANSACTION",
        "THROW",
    ]

    for fragment in required_fragments:
        assert fragment in migration

    assert not re.search(
        r"\bDROP\s+(TABLE|SCHEMA|DATABASE)\b",
        migration,
        re.I,
    )
    assert "connectionstring" not in migration.lower()
    assert "password" not in migration.lower()
    assert "accountkey" not in migration.lower()


def test_outbox_verification_is_transactional_and_non_destructive() -> None:
    verification = read_text(
        SQL_TESTS / "002_verify_invoice_outbox.sql"
    )

    required_fragments = [
        "BEGIN TRANSACTION",
        "INSERT INTO prep.InvoicePackage",
        "INSERT INTO prep.InvoiceLine",
        "INSERT INTO prep.InvoiceOutbox",
        "OutboxStatus = 'PENDING'",
        "JSON_VALUE(Payload, '$.eventType')",
        "PackageStatus = 'READY'",
        "FROM pub.v1_InvoicePackage",
        "ROLLBACK TRANSACTION",
        "Invoice outbox verification passed.",
    ]

    for fragment in required_fragments:
        assert fragment in verification

    assert "COMMIT TRANSACTION" not in verification
    assert not re.search(
        r"\bDROP\s+(TABLE|SCHEMA|DATABASE)\b",
        verification,
        re.I,
    )
