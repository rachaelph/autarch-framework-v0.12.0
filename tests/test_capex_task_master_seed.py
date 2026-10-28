"""Tests for the governed CapEx TaskMaster seed generator."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = (
    ROOT / "tools" / "generate_capex_task_master_seed.py"
)
WORKBOOK_PATH = (
    ROOT
    / "examples"
    / "data"
    / "capex"
    / "Task_Type_Export_9_6_2026.xlsx"
)


def _load_generator() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "generate_capex_task_master_seed",
        GENERATOR_PATH,
    )
    assert specification is not None
    assert specification.loader is not None

    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_us_task_master_source_contract() -> None:
    generator = _load_generator()

    rows = generator.read_us_task_rows(WORKBOOK_PATH)
    generator.validate_rows(rows)

    assert len(rows) == 761
    assert len({row["Task Type"] for row in rows}) == 761
    assert all(row["Description"] for row in rows)
    assert all(row["Active"] == "1" for row in rows)
    assert rows[0]["Task Type"] == "0000"
    assert rows[0]["Description"] == "ENGINEERING/PERMITS/OVERHEAD"


def test_generated_seed_is_idempotent_and_governed() -> None:
    generator = _load_generator()

    rows = generator.read_us_task_rows(WORKBOOK_PATH)
    sql = generator.render_seed(rows)

    assert generator.EXPECTED_SHA256 in sql
    assert "DECLARE @expectedRowCount INT = 761" in sql
    assert "BEGIN TRANSACTION" in sql
    assert "WITH (HOLDLOCK)" in sql
    assert "WHEN MATCHED THEN" in sql
    assert "WHEN NOT MATCHED THEN" in sql
    assert "[isCurrent] = 0" in sql
    assert "ROLLBACK TRANSACTION" in sql
    assert "N'0000'" in sql
    assert "N'ENGINEERING/PERMITS/OVERHEAD'" in sql


def test_generated_seed_matches_committed_output() -> None:
    generator = _load_generator()

    rows = generator.read_us_task_rows(WORKBOOK_PATH)
    expected = generator.render_seed(rows)
    committed = (
        ROOT / "sql" / "data" / "005_seed_us_task_master.sql"
    ).read_text(encoding="utf-8")

    assert committed == expected
