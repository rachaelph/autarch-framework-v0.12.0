from pathlib import Path

from tools.build_capex_demo_v2_request import _load_packages


ROOT = Path(__file__).resolve().parents[1]


def test_guided_demo_starts_with_read_only_dependency_inventory() -> None:
    sql = (
        ROOT / "sql" / "validation" / "validate_capex_demo_start.sql"
    ).read_text(encoding="utf-8")

    assert "ExistingTaskSuggestions" in sql
    assert "AssessmentsReferencingTasks" in sql
    assert "HumanReviewDecisions" in sql
    assert "fk.referenced_object_id = OBJECT_ID(N'task.TaskSuggestion')" in sql
    assert "PreparedLineCount" in sql
    assert "CurrentActiveTaskCount" in sql
    for statement in (
        "DELETE FROM",
        "TRUNCATE TABLE",
        "UPDATE ",
        "INSERT INTO",
        "ALTER TABLE",
        "DROP TABLE",
    ):
        assert statement not in sql.upper()


def test_operator_access_matches_colleague_schema_control_pattern() -> None:
    sql = (
        ROOT / "sql" / "admin" / "grant_capex_demo_operator_access.sql"
    ).read_text(encoding="utf-8")

    for permission in (
        "CREATE TABLE",
        "CREATE VIEW",
        "CREATE PROCEDURE",
        "CREATE FUNCTION",
        "CREATE TYPE",
        "CREATE SYNONYM",
    ):
        assert permission in sql
    for schema in ("prep", "task", "capex", "review", "ops", "ref", "pub"):
        assert f"CREATE SCHEMA [{schema}] AUTHORIZATION [dbo];" in sql
        assert (
            f"GRANT CONTROL ON SCHEMA::[{schema}]\nTO [v-rphillips@circlek.com];"
            in sql
        )
    assert sql.count("TO [v-rphillips@circlek.com];") == 8
    assert sql.count("GRANT ") == 8
    assert "CREATE USER" not in sql.upper()
    assert "WITH GRANT OPTION" not in sql.upper()
    assert "GRANT CONNECT" not in sql.upper()
    assert "VIEW DEFINITION" not in sql.upper()
    assert "DECLARE " not in sql.upper()
    assert "IF " not in sql.upper()
    assert "EXEC" not in sql.upper()
    assert sql.count("CREATE SCHEMA ") == 7
    assert "ALTER ROLE" not in sql.upper()
    assert "DB_OWNER" not in sql.upper()
    assert "ALTER ANY USER" not in sql.upper()
    assert "GRANT CONTROL TO" not in sql.upper()
    assert "GRANT CONTROL ON DATABASE::" not in sql.upper()


def test_runtime_grants_are_object_scoped_and_append_only() -> None:
    sql = (
        ROOT / "sql" / "migrations" / "008_grant_capex_demo_v2_access.sql"
    ).read_text(encoding="utf-8")

    assert "DATABASE_PRINCIPAL_ID(N'cict-ai-cus-capex-id')" in sql
    for table in (
        "task.TaskSuggestion",
        "capex.AssessmentRun",
        "capex.Assessment",
        "capex.BundleCandidateGroup",
        "capex.BundleCandidateMember",
    ):
        assert (
            f"GRANT SELECT, INSERT ON OBJECT::{table} TO [cict-ai-cus-capex-id];"
            in sql
        )
    assert "GRANT SELECT ON OBJECT::pub.v1_TaskMaster" in sql
    assert "GRANT SELECT ON OBJECT::pub.v1_Assessment" in sql
    for permission in ("UPDATE", "DELETE", "ALTER", "CONTROL", "EXECUTE"):
        assert f"GRANT {permission}" not in sql.upper()
    assert "ON SCHEMA::" not in sql.upper()
    assert "ALTER ROLE" not in sql.upper()


def test_readiness_query_covers_every_invoice_and_fails_closed() -> None:
    sql = (
        ROOT / "sql" / "validation" / "validate_capex_demo_v2_readiness.sql"
    ).read_text(encoding="utf-8")

    for invoice in (
        "135d03ac-3598-498a-83fe-d273d046bfea",
        "58aceec5-f35a-48a7-8faa-2f1a195af203",
        "JC2000A07814:20294",
    ):
        assert invoice in sql
    for _, package in _load_packages():
        assert package.package_id in sql
        assert package.invoice_id in sql
        assert f"N'{package.invoice_id}', {len(package.lines)})" in sql
    assert "DECLARE @Synthetic bit" in sql
    assert "task.correlationId = assessment.WorkflowRunId" in sql
    assert "task.lineId = assessment.LineId" in sql
    assert "HAVING COUNT_BIG(*) <> 1" in sql
    assert "bundle.TotalAmount <= 0" in sql
    assert "SUM(member.LineAmount)" in sql
    assert "member.BundleGroupId = assessment.BundleGroupId" in sql
    assert "UNAPPROVED SYNTHETIC DEMO" in sql
    assert "THROW 51085" in sql
    assert "THROW 51093" in sql
    assert "INSERT INTO @Expected" in sql
    assert "INSERT INTO capex." not in sql
    assert "INSERT INTO task." not in sql
