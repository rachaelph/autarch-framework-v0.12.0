import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESET = ROOT / "sql" / "admin" / "reset_capex_demo_all_tables.sql"


def _delete_order() -> dict[str, int]:
    sql = RESET.read_text(encoding="utf-8")
    return {
        f"{schema}.{table}": int(order)
        for order, schema, table in re.findall(
            r"\((\d+), N'(\w+)', N'(\w+)'\)", sql
        )
    }


def test_reset_covers_all_model_and_runtime_tables_except_task_master() -> None:
    model_sql = (
        ROOT / "sql" / "validation" / "validate_capex_model_schema.sql"
    ).read_text(encoding="utf-8")
    expected = {
        f"{schema}.{table}"
        for schema, table in re.findall(r"\(N'(\w+)', N'(\w+)', N'", model_sql)
    }
    expected.remove("ref.TaskMaster")
    expected.update(
        {
            "prep.InvoiceDataQualityWarning",
            "prep.InvoiceOutbox",
            "capex.AssessmentRun",
        }
    )
    order = _delete_order()
    assert len(order) == 25
    assert set(order) == expected
    assert len(set(order.values())) == 25


def test_reset_order_respects_committed_migration_foreign_keys() -> None:
    order = _delete_order()
    checked = 0
    for migration in (ROOT / "sql" / "migrations").glob("*.sql"):
        sql = migration.read_text(encoding="utf-8")
        for table, body in re.findall(
            r"CREATE TABLE (\w+\.\w+)\s*\((.*?)\n\s*\);", sql, re.DOTALL
        ):
            for parent in re.findall(r"REFERENCES (\w+\.\w+)", body):
                if table in order and parent in order:
                    assert order[table] < order[parent], (table, parent)
                    checked += 1
        for table, parent in re.findall(
            r"ALTER TABLE (\w+\.\w+) WITH CHECK ADD CONSTRAINT\s+\w+"
            r"\s+FOREIGN KEY\s*\([^)]*\)\s+REFERENCES (\w+\.\w+)",
            sql,
        ):
            if table in order and parent in order:
                assert order[table] < order[parent], (table, parent)
                checked += 1
    assert checked >= 25


def test_reset_defaults_to_preview_and_reconciles_every_table() -> None:
    sql = RESET.read_text(encoding="utf-8")
    assert "DECLARE @CommitReset bit = 0;" in sql
    assert "DECLARE @WritersPaused bit = 0;" in sql
    assert "@CommitReset = 1 AND @WritersPaused = 0" in sql
    assert "DB_NAME() <> N'cict-ai-cus-sqldb'" in sql
    assert "@@TRANCOUNT <> 0" in sql
    assert sql.index("'PREVIEW_ONLY'") < sql.index("N'DELETE FROM '")
    assert "BeforeRows > 0" in sql
    assert sql.index("Backup reconciliation failed") < sql.index("N'DELETE FROM '")
    assert "ArchivedRows <> BeforeRows" in sql
    assert "DeletedRows <> BeforeRows OR RemainingRows <> 0" in sql
    assert "WITH (TABLOCKX, HOLDLOCK)" in sql
    assert "child.ObjectId IS NULL OR child.DeleteOrder >= parent.DeleteOrder" in sql
    assert "fk.is_disabled = 1 OR fk.is_not_trusted = 1" in sql
    assert "tr.is_disabled = 0" in sql
    assert "Unexpected tables found" in sql
    assert "IF XACT_STATE() <> 0" in sql
    assert sql.rstrip().endswith("THROW;\nEND CATCH;")


def test_reset_preserves_master_values_backups_and_schema() -> None:
    sql = RESET.read_text(encoding="utf-8")
    assert "EXCEPT SELECT * FROM ref.TaskMaster" in sql
    assert "EXCEPT SELECT * FROM #TaskMasterBefore" in sql
    assert "@MasterRows <> (SELECT COUNT_BIG(*) FROM ref.TaskMaster)" in sql
    assert sql.index("TaskMaster preservation check failed") < sql.index(
        "COMMIT TRANSACTION;"
    )
    assert "N'CapexDemoBackup_' + @ResetId" in sql
    assert "N'CapexDemoBackup[_]%'" in sql
    assert "N'TaskSuggestionDemoBackup[_]%'" in sql
    assert "RetainedBackupTable" in sql
    for statement in (
        "TRUNCATE TABLE",
        "NOCHECK",
        "DISABLE TRIGGER",
        "DBCC CHECKIDENT",
        "DELETE FROM REF.TASKMASTER",
        "UPDATE REF.TASKMASTER",
        "DROP TABLE REF.",
        "DROP TABLE OPS.",
    ):
        assert statement not in sql.upper()
