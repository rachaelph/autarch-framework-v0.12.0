import json
import re
from pathlib import Path

from autarch.capex.demo_v2_synthetic import validate_synthetic_register
from tools.build_capex_demo_v2_request import _load_packages


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "sql" / "admin" / "seed_capex_demo_synthetic_prerequisites.sql"
REFERENCES = ROOT / "examples" / "data" / "capex" / "reference-v2"


def test_seed_context_matches_all_three_normalized_overlays() -> None:
    sql = SEED.read_text(encoding="utf-8")
    contexts = re.findall(
        r"\(N'(DEMO-PROJECT-[^']+)', N'(DEMO-SITE-[^']+)', N'([^']+)', N'[^']+'\)",
        sql,
    )
    expected = set()
    for _, package in _load_packages():
        context = package.context_overlay
        assert context is not None
        assert context.project_status == "OPEN"
        assert context.bundling_in_scope is True
        assert context.synthetic is True
        expected.add((context.project_id, context.site_id, package.country_region))
    assert set(contexts) == expected
    assert len(contexts) == 3


def test_seed_threshold_and_traceability_match_runtime_artifacts() -> None:
    sql = SEED.read_text(encoding="utf-8")
    policy = json.loads((REFERENCES / "policy-config-v2.json").read_text())
    register = validate_synthetic_register(
        json.loads((REFERENCES / "synthetic-input-register-v2.json").read_text())
    )
    amount = re.search(
        r"DECLARE @ThresholdAmount decimal\(19,4\) = ([\d.]+);", sql
    )
    assert amount is not None
    assert float(amount.group(1)) == policy["capitalization"]["baseThreshold"]
    assert policy["capitalization"]["currency"] == "USD"
    assert policy["capitalization"]["thresholdBasis"] == "UNIT_COST"
    assert policy["capitalization"]["thresholdComparison"] == "GREATER_THAN_OR_EQUAL"
    assert policy["capitalization"]["belowThresholdExceptionEnabled"] is False
    assert "SYNTHETIC_DEMO_UNIT_COST_GTE" in sql
    assumptions = {
        item["assumptionId"]: item for item in register["syntheticInputs"]
    }
    for assumption in (
        "SYN-SQL-001",
        "SYN-SQL-002",
        "SYN-CTX-001",
        "SYN-CAP-003",
        "SYN-CAP-004",
    ):
        assert assumption in sql
        assert assumptions[assumption]["status"] == "AWAITING_CIRCLE_K"
    assert "UNAPPROVED SYNTHETIC DEMO ONLY" in sql
    assert "budget 0 is a placeholder, not an actual budget." in sql
    assert "EffectiveFrom is a fixture date, not a policy approval date." in sql
    assert "N'Europe', 'EUR'" not in sql


def test_seed_only_inserts_prerequisites_and_never_overwrites_data() -> None:
    sql = SEED.read_text(encoding="utf-8")
    writes = set(
        re.findall(r"\bINSERT INTO\s+(\w+\.\w+)", sql, re.IGNORECASE)
    )
    assert writes == {
        "prep.Project",
        "prep.Site",
        "ref.CapitalizationThreshold",
    }
    assert not re.search(
        r"\b(?:UPDATE|DELETE FROM|TRUNCATE TABLE|ALTER TABLE|DROP TABLE)"
        r"\s+(?:prep|task|capex|ref|review|ops|pub)\.",
        sql,
        re.IGNORECASE,
    )
    assert sql.count("EXCEPT") >= 5
    assert "No rows were overwritten." in sql
    assert "WHERE NOT EXISTS" in sql
    assert "IF NOT EXISTS" in sql
    assert "WITH (TABLOCKX, HOLDLOCK)" in sql
    assert "BEGIN TRANSACTION;" in sql
    assert "IF XACT_STATE() <> 0" in sql
    assert "ROLLBACK TRANSACTION;" in sql
    assert sql.rstrip().endswith("THROW;\nEND CATCH;")


def test_seed_checks_entire_clean_state_and_preserves_task_master() -> None:
    sql = SEED.read_text(encoding="utf-8")
    counts = {
        f"{schema}.{table}": int(count)
        for schema, table, count in re.findall(
            r"\(N'(\w+)', N'(\w+)', (\d+)\)", sql
        )
    }
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
    assert set(counts) == expected
    assert {name: count for name, count in counts.items() if count} == {
        "prep.Project": 3,
        "prep.Site": 3,
        "ref.CapitalizationThreshold": 1,
    }
    assert "ActualRows <> ExpectedRows" in sql
    assert "EXCEPT SELECT * FROM ref.TaskMaster" in sql
    assert "EXCEPT SELECT * FROM #TaskMasterBeforeSeed" in sql
    assert sql.index("TaskMaster preservation check failed") < sql.index(
        "COMMIT TRANSACTION;"
    )
    assert "SYNTHETIC_PREREQUISITES_READY" in sql
    assert "PRESERVED_UNCHANGED" in sql
    assert "DB_NAME() <> N'cict-ai-cus-sqldb'" in sql
    assert "IF @@TRANCOUNT <> 0" in sql
