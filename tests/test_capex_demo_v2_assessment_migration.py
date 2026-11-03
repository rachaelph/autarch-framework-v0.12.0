from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "sql"
    / "migrations"
    / "007_create_capex_assessment_store.sql"
)


def test_migration_creates_append_only_assessment_store_and_public_view() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE capex.AssessmentRun" in sql
    assert "CREATE TABLE capex.Assessment" in sql
    assert "CREATE TABLE capex.BundleCandidateGroup" in sql
    assert "CREATE TABLE capex.BundleCandidateMember" in sql
    assert "CREATE OR ALTER VIEW pub.v1_Assessment" in sql
    assert "UQ_Assessment_Run_Package_Line" in sql
    assert "UQ_BundleCandidateGroup_Run_Group" in sql
    assert "UQ_BundleCandidateMember_Run_Line" in sql
    assert "FK_BundleCandidateMember_Assessment" in sql
    assert "ResultSha256" in sql
    assert "UsedLlm" in sql
    assert "ModelVersion" in sql
    assert "PolicyCitations" in sql
    assert "bundleMemberCount" in sql


def test_migration_does_not_add_mutating_update_or_delete_paths() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").upper()

    assert "UPDATE CAPEX." not in sql
    assert "DELETE FROM CAPEX." not in sql
    assert "ON DELETE CASCADE" not in sql
