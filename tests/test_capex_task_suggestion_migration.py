"""Regression tests for TaskSuggestion migration 006."""
from __future__ import annotations

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    REPOSITORY_ROOT
    / "sql"
    / "migrations"
    / "006_align_task_suggestion.sql"
)


def test_constraint_drop_uses_materialized_dynamic_sql() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert (
        "DECLARE @dropRecommendationConstraintSql NVARCHAR(MAX)"
        in source
    )
    assert (
        "SET @dropRecommendationConstraintSql = CONCAT"
        in source
    )
    assert "QUOTENAME(@recommendationConstraint)" in source
    assert (
        "EXEC sp_executesql @dropRecommendationConstraintSql"
        in source
    )


def test_constraint_drop_does_not_execute_expression_directly() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    unsafe_fragment = """    EXEC
    (
        N'ALTER TABLE [task].[TaskSuggestion] DROP CONSTRAINT '
        + QUOTENAME(@recommendationConstraint)
    )
"""
    assert unsafe_fragment not in source
