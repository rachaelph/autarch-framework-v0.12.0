import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "sql"
MIGRATION = SQL / "migrations" / "009_complete_capex_model_tables.sql"
VALIDATION = SQL / "validation" / "validate_capex_model_schema.sql"


def _created_columns(sql: str) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    sql = sql.replace("[", "").replace("]", "")
    for match in re.finditer(
        r"CREATE TABLE\s+([\w.]+)\s*\((.*?)^\s*\);?",
        sql,
        re.DOTALL | re.MULTILINE | re.IGNORECASE,
    ):
        columns = {
            column.group(1).lower()
            for column in re.finditer(
                r"^\s+(\w+)\s+(?:n?varchar|n?char|decimal|int|bigint|bit|date\b|"
                r"datetime2|datetimeoffset|uniqueidentifier|rowversion)\b",
                match.group(2),
                re.MULTILINE | re.IGNORECASE,
            )
        }
        tables[match.group(1).lower()] = columns
    return tables


def _model_columns() -> dict[str, set[str]]:
    sql = VALIDATION.read_text(encoding="utf-8")
    return {
        f"{schema}.{table}".lower(): {column.lower() for column in columns.split("|")}
        for schema, table, columns in re.findall(
            r"\(N'(\w+)', N'(\w+)', N'([\w|]+)'\)", sql
        )
    }


def test_all_23_model_tables_and_columns_are_physically_covered() -> None:
    physical: dict[str, set[str]] = {}
    for path in sorted((SQL / "migrations").glob("*.sql")):
        contents = path.read_text(encoding="utf-8")
        for table, columns in _created_columns(contents).items():
            physical.setdefault(table, set()).update(columns)
        for schema, table, column in re.findall(
            r"ALTER TABLE\s+\[?(\w+)\]?\.\[?(\w+)\]?\s+ADD\s+\[?(\w+)\]?\s+",
            contents,
            re.IGNORECASE,
        ):
            if column.lower() != "constraint":
                physical.setdefault(f"{schema}.{table}".lower(), set()).add(column.lower())
        for schema, table, column in re.findall(
            r"\(N'(\w+)', N'(\w+)', N'(\w+)', N'[^']+'\)", contents
        ):
            physical.setdefault(f"{schema}.{table}".lower(), set()).add(column.lower())

    model = _model_columns()
    assert len(model) == 23
    missing = {
        table: sorted(columns - physical.get(table, set()))
        for table, columns in model.items()
        if columns - physical.get(table, set())
    }
    assert missing == {}


def test_creates_only_the_16_missing_tables_and_preserves_runtime_shapes() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    created = _created_columns(sql)
    assert len(created) == 16
    existing = {
        "prep.invoicepackage",
        "prep.invoiceline",
        "task.tasksuggestion",
        "ref.taskmaster",
        "capex.assessment",
        "capex.bundlecandidategroup",
        "capex.bundlecandidatemember",
    }
    assert set(created) == set(_model_columns()) - existing
    assert "AssessmentID uniqueidentifier NOT NULL" in sql
    assert "TaskSuggestionID uniqueidentifier NOT NULL" in sql
    assert "WorkflowRunID nvarchar(128) NOT NULL" in sql
    assert "REFERENCES capex.BundleCandidateGroup (WorkflowRunId, BundleGroupId)" in sql
    assert "RowVersion rowversion NOT NULL" in sql
    assert "WITH CHECK ADD CONSTRAINT" in sql
    for forbidden in ("DROP TABLE", "DROP COLUMN", "ALTER COLUMN", "SP_RENAME", "WITH NOCHECK"):
        assert forbidden not in sql.upper()
    assert not re.search(r"\b(?:INSERT INTO|UPDATE|DELETE FROM)\s+(?:prep|task|capex|ref|review|ops)\.", sql, re.I)
    for definition in re.findall(
        r"\(N'\w+', N'\w+', N'\w+', N'([^']+)'\)", sql
    ):
        assert definition.startswith("AS (") or definition.endswith(" NULL")
    assert "ROLLBACK TRANSACTION" in sql
    assert "COL_LENGTH(@Qualified, @Column) IS NULL" in sql


def test_coverage_validation_is_explicitly_not_exact_model_equivalence() -> None:
    sql = VALIDATION.read_text(encoding="utf-8")
    assert "MISSING_TABLE" in sql
    assert "MISSING_COLUMN" in sql
    assert "THROW 51120" in sql
    assert "COLUMN_COVERAGE_PASS" in sql
    assert "ActualPrecision" in sql
    assert "ActualScale" in sql
    assert "IsComputed" in sql
    assert "IsNotTrusted" in sql
    assert "column coverage alone is not model equivalence" in sql


def test_sql_insertion_verification_never_commits_test_data() -> None:
    sql = (SQL / "tests" / "004_verify_capex_model_tables.sql").read_text(encoding="utf-8")
    assert "ROLLBACK TRANSACTION" in sql
    assert "COMMIT" not in sql.upper()
    assert "THROW 51130" in sql
    assert "THROW 51131" in sql
    assert "NOT APPROVED POLICY" in sql
