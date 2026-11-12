from pathlib import Path

from tools.build_capex_demo_v2_request import _load_packages


ROOT = Path(__file__).resolve().parents[1]

def test_frozen_rehearsal_query_is_read_only_and_shows_all_three_model_stages() -> None:
    sql = (ROOT / "sql" / "validation" / "rehearse_capex_sql_llm_workflow.sql").read_text()
    for statement in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE ", "DROP ", "ALTER ", "CREATE ", "MERGE "):
        assert statement not in sql.upper()
    for expected in (
        "friday-sql-gpt-contract-v4-20261105T150000Z",
        "58aceec5-f35a-48a7-8faa-2f1a195af203",
        "Status = 'SUCCEEDED'", "THROW 51220",
        "task.TaskSuggestion", "capex.Assessment", "capex.BundleDecision",
        "line.InvoiceLineId = task.lineId", "line.InvoiceLineId = assessment.LineId",
        "line.InvoiceLineId = decision.InvoiceLineId", "assessment.TaskSuggestionID",
        "ModelTaskRationale", "ModelAccountingRationale", "ModelBundleRationale",
        "ModelBundleDecisionCount", "ProposedBundleGroupCount",
        "Unapproved synthetic demo context",
    ):
        assert expected in sql
    assert sql.count(" AS DemoStep") == 5

def test_native_gpt_migration_preserves_human_approvals_and_scopes_grants() -> None:
    sql = (ROOT / "sql" / "migrations" / "013_store_gpt_bundle_decisions.sql").read_text()
    for expected in (
        "CREATE TABLE capex.BundleDecision", "PRIMARY KEY NONCLUSTERED",
        "InvoiceLineId nvarchar(256)", "REFERENCES capex.Assessment",
        "REFERENCES capex.BundleCandidateGroup", "IsOneAssetSuggestion IS NOT NULL",
        "UsedLlm = 1 AND ModelVersion = N'gpt-5.1'", "GPT_BUNDLE_DECISION_STORE_READY",
        "GRANT UPDATE (MatchMethod, ItemIdentified, UnresolvedReason, ContractVersion)",
        "GRANT UPDATE (TaskSuggestionID)", "ROLLBACK TRANSACTION",
    ):
        assert expected in sql
    for forbidden in ("INSERT INTO review.", "ALTER TABLE review.", "ON SCHEMA::", "db_owner", "DELETE ", "DROP "):
        assert forbidden not in sql
    assert sql.count("CREATE TABLE") == 1


def test_native_gpt_verification_checks_each_line_and_three_actual_model_stages() -> None:
    sql = (ROOT / "sql" / "validation" / "validate_capex_sql_llm_workflow.sql").read_text()
    for statement in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE ", "DROP ", "ALTER ", "CREATE "):
        assert statement not in sql.upper()
    for expected in (
        "GPT_INVOICE_WORKFLOW_VERIFIED", "assessment.UsedLlm <> 1",
        "task.taskSuggestionId = assessment.TaskSuggestionID",
        "decision.InvoiceLineId = line.InvoiceLineId", "LLM_DECISIONS",
        "Stage = 'TASK_SUGGESTION'", "Stage = 'ACCOUNTING_DECISION'", "Stage = 'BUNDLE_DECISION'",
        "LlmCallsAttempted BETWEEN 21 AND 32", "LlmResponsesReceived = evidence.LlmCallsAttempted",
        "Response nvarchar(max)", "ValidationError nvarchar(max)", "NOT BETWEEN 1 AND 2",
        "item.Recommendation = assessment.Recommendation", "item.Decision = decision.ModelDecision",
        "SUM(member.LineAmount)", "DistinctInvoiceCount", "reference.[value] = call.CallId",
        "sql-llm-accounting-v4", "CODE_GATE_ON_MODEL_ACCOUNTING_AND_INPUT_FACTS",
        "accountingMaxAttemptsPerInvoice", "accepted raw model response",
        "bundlingMaxAttempts", "sql-llm-bundling-v2",
    ):
        assert expected in sql
    assert "911.08" not in sql
    assert "BundleMemberCount = 11" not in sql

def test_llm_sql_verification_is_read_only_and_does_not_pin_model_outcomes() -> None:
    sql = (ROOT / "sql" / "validation" / "validate_capex_sql_llm_demo_run.sql").read_text()
    for statement in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE ", "DROP ", "ALTER ", "CREATE "):
        assert statement not in sql.upper()
    for expected in (
        "REPLACE_WITH_NEW_LLM_WORKFLOW_RUN_ID", "SQL_LLM_RESULTS_VERIFIED",
        "task.lineId = line.InvoiceLineId", "assessment.LineId = line.InvoiceLineId",
        "task.taskCode = assessment.SuggestedTaskCode", "OPENJSON(task.evidenceRefs)",
        "LIKE N'llm-call:%'", "assessment.UsedLlm <> 0", "SUM(member.LineAmount)",
    ):
        assert expected in sql
    assert "911.08" not in sql
    assert "BundleMemberCount = 11" not in sql
    assert sql.count("THROW ") == 10
    for expected in (
        "capex.LlmShowcaseRun", "evidence.AccountingExplanationCount = 30",
        "OPENJSON(evidence.EvidenceJson, '$.calls')", "Prompt nvarchar(max)",
        "Response nvarchar(max)", "Explanation nvarchar(max)", "HASHBYTES('SHA2_256'",
    ):
        assert expected in sql
    template = (ROOT / "infra" / "container-apps" / "capex-demo-v2-sql-job.bicep").read_text()
    assert "param azureOpenAIEndpoint string = ''" in template
    assert "name: 'AZURE_OPENAI_ENDPOINT'" in template
    assert "API_KEY" not in template


def test_llm_output_migration_is_additive_and_object_scoped() -> None:
    sql = (ROOT / "sql" / "migrations" / "012_store_capex_llm_showcase_outputs.sql").read_text()
    for expected in (
        "CREATE TABLE capex.LlmShowcaseRun", "PRIMARY KEY (WorkflowRunId)",
        "EvidenceJson", "ISJSON(EvidenceJson)", "EvidenceSha256",
        "AccountingExplanationCount", "BundleExplanationCount",
        "Status IN ('SUCCEEDED', 'INCOMPLETE')", "CAPEX_LLM_SQL_OUTPUT_STORE_READY",
        "GRANT SELECT, INSERT ON OBJECT::capex.LlmShowcaseRun",
        "BEGIN TRANSACTION", "COMMIT TRANSACTION", "ROLLBACK TRANSACTION",
    ):
        assert expected in sql
    for forbidden in ("DELETE ", "TRUNCATE ", "DROP ", "ALTER TABLE", "ON SCHEMA::", "db_owner"):
        assert forbidden not in sql
    assert sql.count("CREATE TABLE") == 1

def test_expanded_sql_run_reconciliation_is_read_only_and_line_scoped() -> None:
    sql = (ROOT / "sql" / "validation" / "validate_capex_sql_demo_run.sql").read_text(encoding="utf-8")
    for statement in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE ", "DROP ", "ALTER ", "CREATE "):
        assert statement not in sql.upper()
    for expected in (
        "friday-sql-20261105T010058Z", "FRIDAY-DEMO-2026-11-06-V1",
        "task.lineId = line.InvoiceLineId", "assessment.LineId = line.InvoiceLineId",
        "task.correlationId = assessment.WorkflowRunId", "SUM(member.LineAmount)",
        "BundleMemberCount = 11", "AssessmentCount = 30", "bundle.TotalAmount <> 911.08",
        "UsedLlm <> 0", "SQL_DEMO_RUN_VERIFIED",
    ):
        assert expected in sql
    assert sql.count("THROW ") == 8


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
