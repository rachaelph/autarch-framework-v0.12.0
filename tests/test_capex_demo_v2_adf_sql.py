from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest
from fastapi.testclient import TestClient

from autarch.capex import demo_v2_adf_extraction as extraction
from autarch.capex import demo_v2_adf_sql as sql
from autarch.capex.demo_v2_contracts import PersistenceCounts
from autarch.capex.demo_v2_reference import TaskMasterSnapshot
from autarch.capex import demo_v2_review as review
from autarch.capex.demo_v2_review import ReviewStore, SqlReviewStore, make_handler
from tests.test_capex_demo_v2_adf_job import MASTER, run
from tests.test_capex_demo_v2_api import SqlDialectHarness, create_app, headers, identity
from tests.test_capex_demo_v2_mvp import FixtureModel, extracted, fake_render


@pytest.fixture
def handoff(monkeypatch, request):
    observed = {}
    original_extraction = extraction.extract_adf_pdfs
    with monkeypatch.context() as patch:
        patch.setattr(sql, "load_adf_task_master", lambda connection, expected: expected)
        patch.setattr(sql, "load_adf_asset_class_master", lambda connection, expected: expected)
        patch.setattr(extraction, "extract_adf_pdfs",
                      lambda **kwargs: original_extraction(**kwargs, render=fake_render))

        def capture(connection, **kwargs):
            observed.update(kwargs)
            return {}

        patch.setattr(sql, "persist_adf_results", capture)
        invoices = [extracted(1), extracted(2)]
        if getattr(request, "param", None) == "all_review":
            for invoice in invoices:
                invoice["headerConfidence"] = 0.1
        run(FixtureModel(extractions=invoices), {}, connection=object(), fresh_pdf_extraction=True)
    return observed


class AtomicSqlHarness:
    """Translate only this transaction contract to SQLite; not a SQL Server test."""

    def __init__(self, review_path, *, fail_on=None, readback_delta=0):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("ATTACH DATABASE ':memory:' AS task")
        self.db.execute("ATTACH DATABASE ':memory:' AS capex")
        self.db.execute("ATTACH DATABASE ? AS mvp_review", (str(review_path),))
        self.db.executescript("""
            CREATE TABLE task.TaskSuggestion (
                taskSuggestionId INTEGER PRIMARY KEY, correlationId, packageId, invoiceId, lineId,
                MatchMethod, ItemIdentified, UnresolvedReason, ContractVersion, modelDeployment,
                PotentialBundling, PotentialTagsJson);
            CREATE TABLE capex.Assessment (
                WorkflowRunId, PackageId, InvoiceId, LineId, TaskSuggestionID,
                AssetClassSuggestionID, SuggestedAssetClassCode, UsedLlm, ModelVersion,
                RepairMaintenanceFlag, AroFlag, PotentialTagsJson, CategorySuggestion);
            CREATE TABLE task.assetClassSuggestion (
                assetClassSuggestionId INTEGER PRIMARY KEY, correlationId, packageId, invoiceId,
                runId, lineId, assetClassCode, resultStatus, ItemIdentified, confidence, rationale,
                UnresolvedReason, evidenceRefs, AssetClassMasterSha256, modelDeployment, promptVersion, createdAtUtc,
                PotentialBundling, PotentialTagsJson);
            CREATE TABLE capex.BundleDecision (
                WorkflowRunId, PackageId, InvoiceId, InvoiceLineId, BundleGroupId, ModelDecision,
                IsOneAssetSuggestion, Confidence, Rationale, EvidenceRefs, UsedLlm, ModelVersion,
                PromptVersion, CreatedAtUtc);
            CREATE TABLE capex.LlmShowcaseRun (
                WorkflowRunId PRIMARY KEY, SchemaVersion, Status, ModelDeployment, InputSha256,
                WorkflowResultSha256, EvidenceSha256, LlmCallsAttempted, LlmResponsesReceived,
                AccountingInvoiceCount, AccountingExplanationCount, BundleExplanationCount,
                EvidenceJson, CreatedAtUtc);
        """)
        self.commits = self.rollbacks = 0
        self.fail_on = fail_on
        self.readback_delta = readback_delta
        self.cursors_open = 0
        self.queries = []

    def cursor(self):
        parent = self
        cursor = self.db.cursor()
        parent.cursors_open += 1

        class Cursor:
            closed = False

            def execute(self, query, parameters=()):
                parent.queries.append(query)
                if parent.fail_on and parent.fail_on in query:
                    raise ValueError(f"MOCK SQL failure: {parent.fail_on}")
                if query.lstrip().startswith("SET "):
                    query = "SELECT 1"
                elif "UPDATE assessment SET TaskSuggestionID" in query:
                    query = """
                        UPDATE capex.Assessment SET TaskSuggestionID=(
                            SELECT taskSuggestionId FROM task.TaskSuggestion AS t
                            WHERE t.correlationId=Assessment.WorkflowRunId
                            AND t.packageId=Assessment.PackageId AND t.invoiceId=Assessment.InvoiceId
                            AND t.lineId=Assessment.LineId) WHERE WorkflowRunId=?;
                    """
                elif "UPDATE assessment SET AssetClassSuggestionID" in query:
                    query = """
                        UPDATE capex.Assessment SET
                        AssetClassSuggestionID=(SELECT assetClassSuggestionId FROM task.assetClassSuggestion AS a
                            WHERE a.correlationId=Assessment.WorkflowRunId AND a.packageId=Assessment.PackageId
                            AND a.invoiceId=Assessment.InvoiceId AND a.lineId=Assessment.LineId),
                        SuggestedAssetClassCode=(SELECT assetClassCode FROM task.assetClassSuggestion AS a
                            WHERE a.correlationId=Assessment.WorkflowRunId AND a.packageId=Assessment.PackageId
                            AND a.invoiceId=Assessment.InvoiceId AND a.lineId=Assessment.LineId)
                        WHERE WorkflowRunId=?;
                    """
                query = query.replace("WITH (UPDLOCK, HOLDLOCK)", "").replace("COUNT_BIG", "COUNT")
                parameters = tuple(value.isoformat() if isinstance(value, datetime) else value
                                   for value in parameters)
                cursor.execute(query, parameters)
                return self

            def executemany(self, query, parameters):
                for values in parameters:
                    self.execute(query, values)

            def fetchone(self):
                row = cursor.fetchone()
                if "(SELECT COUNT_BIG(*) FROM task.TaskSuggestion" in parent.queries[-1]:
                    return (row[0] + parent.readback_delta, *row[1:])
                return row

            def fetchall(self):
                return cursor.fetchall()

            def close(self):
                if not self.closed:
                    self.closed = True
                    cursor.close()
                    parent.cursors_open -= 1

        return Cursor()

    def commit(self):
        if self.fail_on == "COMMIT":
            raise ValueError("MOCK SQL failure: COMMIT")
        self.commits += 1
        self.db.commit()

    def rollback(self):
        self.rollbacks += 1
        self.db.rollback()

    def close(self):
        self.db.close()


@pytest.fixture
def native_helpers(monkeypatch):
    def tasks(*, connection, package, request, determinations):
        cursor = connection.cursor()
        try:
            cursor.executemany("""
                INSERT INTO task.TaskSuggestion
                    (correlationId, packageId, invoiceId, lineId, modelDeployment) VALUES (?,?,?,?,?);
            """, [(request.workflow_run_id, package.package_id, package.invoice_id, item.line_id,
                   request.model_deployment) for item in determinations])
            connection.commit()
        finally:
            cursor.close()
        return len(determinations)

    def assessments(*, connection, assessments, groups, **kwargs):
        assert not groups
        cursor = connection.cursor()
        try:
            cursor.executemany("""
                INSERT INTO capex.Assessment
                    (WorkflowRunId, PackageId, InvoiceId, LineId, UsedLlm, ModelVersion)
                    VALUES (?,?,?,?,?,?);
            """, [(item.workflow_run_id, item.package_id, item.invoice_id, item.line_id,
                   item.used_llm, item.model_version) for item in assessments])
            connection.commit()
        finally:
            cursor.close()
        return PersistenceCounts(assessments=len(assessments))

    monkeypatch.setattr(sql, "_persist_task_determinations", tasks)
    monkeypatch.setattr(sql, "persist_demo_v2_results", assessments)


@pytest.mark.parametrize("fail_on", [None, "INSERT INTO task.assetClassSuggestion",
                                     "UPDATE assessment SET AssetClassSuggestionID"])
def test_mixed_eu_us_review_rows_use_distinct_atomic_stores(native_helpers, monkeypatch, tmp_path, fail_on):
    from autarch.capex.demo_v2_contracts import (
        CapitalizationAssessment, DemoWorkflowRequest, DemoWorkflowResult,
        InvoicePackageReference, TaskDetermination, WorkflowPackageSummary,
    )
    from tests.test_capex_demo_v2_task_suggestion import _line, _package

    packages = []
    for label, filename in (("eu", "JC-example.xlsx"), ("us", "Bruegmann.zip")):
        package = _package([_line("line-1", "MOCK TEST ONLY: unknown charge")])
        packages.append(package.model_copy(update={
            "package_id": label, "invoice_id": label,
            "source": package.source.model_copy(update={"file_name": filename}),
        }))
    request = DemoWorkflowRequest(
        workflow_run_id="mock-split-sql", requested_at_utc=packages[0].created_at_utc,
        model_deployment="gpt-5.1", task_prompt_version="mock-split",
        packages=[InvoicePackageReference(
            package_id=package.package_id, run_id=package.run_id, package_uri="memory:test",
            expected_invoice_id=package.invoice_id, expected_source_sha256=package.source.sha256,
        ) for package in packages],
    )
    tasks = [TaskDetermination(
        workflow_run_id=request.workflow_run_id, package_id=package.package_id,
        invoice_id=package.invoice_id, line_id="line-1",
        classification_type="ASSET_CLASS" if package.package_id == "eu" else "TASK",
        resolution_status="REVIEW", match_method="REVIEW", confidence=0,
        unresolved_reason="MOCK TEST ONLY: missing reference context.", rationale="MOCK TEST ONLY",
        task_master_sha256="0" * 64, prompt_version="mock-reference-review",
        created_at_utc=package.created_at_utc,
    ) for package in packages]
    assessments = [CapitalizationAssessment(
        workflow_run_id=request.workflow_run_id, package_id=package.package_id,
        invoice_id=package.invoice_id, line_id="line-1", nature_result="REVIEW",
        repair_maintenance_indicator=False, never_capitalize_hit=False,
        recommendation="REVIEW", outcome_category="MOCK TEST ONLY", bundling_disposition="REVIEW",
        rationale="MOCK TEST ONLY", review_reasons=["MOCK TEST ONLY: missing context"],
        created_at_utc=package.created_at_utc,
    ) for package in packages]
    result = DemoWorkflowResult(
        workflow_run_id=request.workflow_run_id, status="SUCCEEDED",
        started_at_utc=packages[0].created_at_utc, completed_at_utc=packages[0].created_at_utc,
        packages=[WorkflowPackageSummary(
            package_id=package.package_id, invoice_id=package.invoice_id, line_count=1,
            resolved_task_count=0, review_task_count=int(package.package_id == "us"),
            review_asset_class_count=int(package.package_id == "eu"),
            capex_count=0, opex_count=0, review_assessment_count=1,
        ) for package in packages],
        task_determinations=tasks, assessments=assessments,
    )
    connection = AtomicSqlHarness(tmp_path / "mock-review.db", fail_on=fail_on)
    connection.db.executescript("""
        CREATE TABLE mvp_review.documents(run_id);
        CREATE TABLE mvp_review.imports(run_id);
    """)

    class Store:
        def __init__(self, connection):
            self.connection = connection

        def import_result(self, review, read):
            cursor = self.connection.cursor()
            try:
                cursor.executemany("INSERT INTO mvp_review.documents VALUES (?)",
                                   [(request.workflow_run_id,)] * len(packages))
                cursor.execute("INSERT INTO mvp_review.imports VALUES (?)", (request.workflow_run_id,))
            finally:
                cursor.close()

    monkeypatch.setattr(sql, "SqlReviewStore", Store)
    try:
        kwargs = dict(request=request, packages=packages, result=result, model_result=None,
                      workflow=None, review={}, read=lambda uri: b"mock")
        if fail_on:
            with pytest.raises(ValueError, match="MOCK SQL failure"):
                sql.persist_adf_results(connection, **kwargs)
            assert connection.commits == 0 and connection.rollbacks == 1
            assert connection.db.execute("SELECT COUNT(*) FROM task.TaskSuggestion").fetchone()[0] == 0
            assert connection.db.execute("SELECT COUNT(*) FROM task.assetClassSuggestion").fetchone()[0] == 0
        else:
            counts = sql.persist_adf_results(connection, **kwargs)
            assert counts["taskSuggestions"] == counts["assetClassSuggestions"] == 1
            assert connection.commits == 1
            assert connection.db.execute("SELECT invoiceId FROM task.TaskSuggestion").fetchall() == [("us",)]
            assert connection.db.execute("SELECT invoiceId FROM task.assetClassSuggestion").fetchall() == [("eu",)]
            assert connection.db.execute("""
                SELECT InvoiceId, TaskSuggestionID IS NOT NULL, AssetClassSuggestionID IS NOT NULL
                FROM capex.Assessment ORDER BY InvoiceId
            """).fetchall() == [("eu", 0, 1), ("us", 1, 0)]
        assert connection.cursors_open == 0
    finally:
        connection.close()


@pytest.mark.parametrize("fail_on", [
    None, "UPDATE task.assetClassSuggestion SET PotentialBundling=",
    "UPDATE capex.Assessment SET RepairMaintenanceFlag=", "TAG_READBACK_MISMATCH",
])
@pytest.mark.parametrize("aro_signal", [False, True])
def test_full_worker_routes_eu_model_result_into_linked_asset_store(
    monkeypatch, native_helpers, tmp_path, fail_on, aro_signal,
):
    from autarch.capex import demo_v2_adf_job as job
    from autarch.capex.demo_v2_contracts import DemoWorkflowRequest, InvoicePackageReference
    from autarch.capex.demo_v2_intake import IntakeManifest, IntakeSource
    from tests.test_capex_demo_v2_asset_classes import ClassificationModel
    from tests.test_capex_demo_v2_task_suggestion import _line, _package

    packages = []
    for label, filename, currency in (("eu", "JC-example.xlsx", "EUR"), ("us", "US-example.zip", "USD")):
        description = "Routine cleaning of shop furniture" if label == "eu" else (
            "Tank removal and disposal" if aro_signal else "Shelving"
        )
        package = _package([_line("line-1", description)])
        packages.append(package.model_copy(update={
            "package_id": label, "invoice_id": label, "currency": currency,
            "lines": [line.model_copy(update={"currency": currency}) for line in package.lines],
            "source": package.source.model_copy(update={"file_name": filename}),
        }))

    def prepare(**kwargs):
        request = DemoWorkflowRequest(
            workflow_run_id=kwargs["workflow_run_id"], requested_at_utc=packages[0].created_at_utc,
            model_deployment="gpt-5.1", task_prompt_version="mock-full-worker",
            persist_results=False,
            packages=[InvoicePackageReference(
                package_id=p.package_id, run_id=p.run_id, package_uri=f"memory:{p.package_id}",
                expected_invoice_id=p.invoice_id, expected_source_sha256=p.source.sha256,
            ) for p in packages],
        )
        for ref, package in zip(request.packages, packages):
            kwargs["write"](ref.package_uri, package.model_dump(mode="json", by_alias=True))
        kwargs["write"]("memory:request", request.model_dump(mode="json", by_alias=True))
        return IntakeManifest(
            workflow_run_id=request.workflow_run_id, created_at_utc=request.requested_at_utc,
            sources=[IntakeSource(
                file_name=p.source.file_name, uri=f"https://example.invalid/{p.source.file_name}",
                sha256=p.source.sha256, byte_count=4,
                format="JC_WORKBOOK" if p.source.file_name.endswith(".xlsx") else "ABBYY_ZIP",
                package_ids=[p.package_id],
            ) for p in packages],
            request_uri="memory:request", package_count=2, line_count=2,
            excluded_record_count=0, review_required_package_count=0,
        )

    observed = {}
    class TaggedClassificationModel(ClassificationModel):
        def complete(self, prompt, system=None):
            if not prompt.startswith("Invoice request JSON:"):
                payload = json.loads(prompt)
                if payload["stage"] == "POTENTIAL_TAGS":
                    return json.dumps({"lines": [{
                        "packageId": p.package_id, "lineId": line.line_id,
                        "potentialTags": [{
                            "tag": "POTENTIAL_REPAIR_MAINTENANCE_OPEX",
                            "reason": "MOCK TEST ONLY: routine cleaning signal, not an accounting decision.",
                            "evidence": [{"lineId": line.line_id, "quote": "Routine cleaning"}],
                        }] if p.package_id == "eu" else [{
                            "tag": "POTENTIAL_ARO",
                            "reason": "MOCK TEST ONLY: supported tank retirement signal.",
                            "evidence": [{"lineId": line.line_id, "quote": "Tank removal"}],
                        }] if aro_signal else [],
                    } for p in packages for line in p.lines]})
            return super().complete(prompt, system)

    with monkeypatch.context() as patch:
        patch.setattr(job, "prepare_intake", prepare)
        patch.setattr(extraction, "extract_adf_pdfs", lambda **kwargs: extraction.ExtractedBatch(
            packages=list(kwargs["packages"]), documents={}, calls=[],
        ))
        patch.setattr(sql, "load_adf_task_master", lambda connection, expected: expected)
        patch.setattr(sql, "load_adf_asset_class_master", lambda connection, expected: expected)

        def capture(connection, **kwargs):
            observed.update(kwargs)
            return {}

        patch.setattr(sql, "persist_adf_results", capture)
        summary = run(TaggedClassificationModel(), {}, connection=object(), fresh_pdf_extraction=True,
                      allow_provisional_pdf_recommendations=True, demo_recommendations=True)
    assert summary["taskSuggestionCount"] == summary["assetClassSuggestionCount"] == 1
    assert summary["potentialTagScreenedLineCount"] == 2
    result = observed["result"]
    eu_summary = next(p for p in result.packages if p.package_id == "eu")
    assert eu_summary.resolved_task_count == 0 and eu_summary.resolved_asset_class_count == 1
    connection = AtomicSqlHarness(tmp_path / "model-review.db", fail_on=fail_on)
    connection.db.executescript("""
        CREATE TABLE mvp_review.documents(run_id);
        CREATE TABLE mvp_review.imports(run_id);
    """)
    if fail_on == "TAG_READBACK_MISMATCH":
        connection.db.executescript("""
            CREATE TRIGGER capex.corrupt_aro AFTER UPDATE OF AroFlag ON Assessment
            BEGIN
                UPDATE Assessment SET AroFlag=CASE WHEN NEW.AroFlag=1 THEN 0 ELSE 1 END
                WHERE rowid=NEW.rowid;
            END;
        """)

    class Store:
        def __init__(self, connection):
            self.connection = connection

        def import_result(self, envelope, read):
            cursor = self.connection.cursor()
            try:
                cursor.executemany("INSERT INTO mvp_review.documents VALUES (?)",
                                   [(result.workflow_run_id,)] * len(packages))
                cursor.execute("INSERT INTO mvp_review.imports VALUES (?)", (result.workflow_run_id,))
            finally:
                cursor.close()

    monkeypatch.setattr(sql, "SqlReviewStore", Store)
    try:
        if fail_on:
            message = "potential review tags" if fail_on == "TAG_READBACK_MISMATCH" else "MOCK SQL failure"
            with pytest.raises(ValueError, match=message):
                sql.persist_adf_results(connection, **observed)
            assert connection.rollbacks == 1 and connection.commits == 0
            for table in ("task.TaskSuggestion", "task.assetClassSuggestion", "capex.Assessment",
                          "capex.BundleDecision", "capex.LlmShowcaseRun", "mvp_review.imports"):
                assert connection.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            assert connection.cursors_open == 0
            return
        counts = sql.persist_adf_results(connection, **observed)
        assert counts["taskSuggestions"] == counts["assetClassSuggestions"] == 1
        assert counts["modelBundleDecisions"] == 2 and connection.commits == 1
        assert connection.db.execute("""
            SELECT InvoiceId, TaskSuggestionID IS NULL, AssetClassSuggestionID IS NOT NULL, SuggestedAssetClassCode
            FROM capex.Assessment WHERE InvoiceId='eu'
        """).fetchone() == ("eu", 1, 1, "295")
        assert connection.db.execute("SELECT COUNT(*) FROM task.TaskSuggestion WHERE invoiceId='eu'").fetchone()[0] == 0
        assert connection.db.execute("SELECT COUNT(*) FROM capex.LlmShowcaseRun").fetchone()[0] == 1
        assert connection.db.execute("""
            SELECT RepairMaintenanceFlag, AroFlag
            FROM capex.Assessment WHERE InvoiceId='eu'
        """).fetchone() == (1, 0)
        assert json.loads(connection.db.execute(
            "SELECT PotentialTagsJson FROM capex.Assessment WHERE InvoiceId='eu'"
        ).fetchone()[0])[0]["evidence"][0]["quote"] == "Routine cleaning"
        us_flags = connection.db.execute("""
            SELECT RepairMaintenanceFlag, AroFlag, PotentialTagsJson
            FROM capex.Assessment WHERE InvoiceId='us'
        """).fetchone()
        assert us_flags[:2] == (0, int(aro_signal))
        us_evidence = json.loads(us_flags[2])
        if aro_signal:
            assert us_evidence[0]["tag"] == "POTENTIAL_ARO"
            assert us_evidence[0]["evidence"][0]["quote"] == "Tank removal"
        else:
            assert us_evidence == []
        assert connection.cursors_open == 0
    finally:
        connection.close()


def test_native_transaction_commits_once_with_model_evidence_and_review(
    handoff, native_helpers, tmp_path,
):
    path = tmp_path / "review.db"
    ReviewStore(path).close()
    connection = AtomicSqlHarness(path)
    try:
        counts = sql.persist_adf_results(connection, **handoff)
        assert connection.commits == 1 and connection.rollbacks == 0
        assert connection.cursors_open == 0
        assert counts["taskSuggestions"] == 2
        assert counts["assetClassSuggestions"] == 8
        assert counts["assessments"] == 10
        assert handoff["result"].persistence.task_suggestions == 2
        assert handoff["result"].persistence.asset_class_suggestions == 8
        assert handoff["result"].persistence.assessments == 10
        assert counts["modelBundleDecisions"] == 2
        assert counts["reviewDocuments"] == 10
        assert connection.db.execute("""
            SELECT COUNT(*) FROM task.TaskSuggestion
            WHERE modelDeployment='NOT_CALLED_REFERENCE_REVIEW' AND MatchMethod='REVIEW';
        """).fetchone()[0] == 0
        assert connection.db.execute("""
            SELECT COUNT(*) FROM capex.Assessment
            WHERE TaskSuggestionID IS NOT NULL OR AssetClassSuggestionID IS NOT NULL;
        """).fetchone()[0] == 10
        evidence = json.loads(connection.db.execute("SELECT EvidenceJson FROM capex.LlmShowcaseRun").fetchone()[0])
        assert evidence["provenance"].startswith("ORIGINAL_SOURCE")
        assert len(evidence["extractionCalls"]) == 2
        assert evidence["completeResultSha256"]
        assert evidence["taskMasterSource"] == "pub.v1_TaskMaster"
    finally:
        connection.close()


@pytest.mark.parametrize("handoff", ["all_review"], indirect=True)
def test_all_exception_batch_commits_review_without_fabricated_model_rows(
    handoff, native_helpers, tmp_path,
):
    path = tmp_path / "review.db"
    ReviewStore(path).close()
    connection = AtomicSqlHarness(path)
    try:
        counts = sql.persist_adf_results(connection, **handoff)
        assert counts["taskSuggestions"] == 22
        assert counts["assetClassSuggestions"] == 8
        assert counts["assessments"] == 30
        assert handoff["result"].persistence.assessments == 30
        assert counts["modelBundleDecisions"] == 0
        assert connection.commits == 1
        assert connection.db.execute("SELECT COUNT(*) FROM capex.LlmShowcaseRun").fetchone()[0] == 0
        assert connection.db.execute("SELECT COUNT(*) FROM capex.BundleDecision").fetchone()[0] == 0
        payload = json.loads(connection.db.execute("SELECT payload FROM mvp_review.imports").fetchone()[0])
        assert payload["partitions"][0]["evidence"]["status"] == "TAG_SCREENING_ONLY_REFERENCE_REVIEW"
        assert payload["llmCallsAttempted"] == 3
    finally:
        connection.close()


@pytest.mark.parametrize("fail_on", [
    "INSERT INTO task.TaskSuggestion", "INSERT INTO capex.Assessment",
    "INSERT INTO capex.LlmShowcaseRun", "INSERT INTO mvp_review.documents",
    "(SELECT COUNT_BIG(*) FROM task.TaskSuggestion", "COMMIT",
])
def test_every_sink_failure_rolls_back_all_prior_stages(
    handoff, native_helpers, tmp_path, fail_on,
):
    path = tmp_path / "review.db"
    ReviewStore(path).close()
    connection = AtomicSqlHarness(path, fail_on=fail_on)
    try:
        with pytest.raises(ValueError, match="MOCK SQL failure"):
            sql.persist_adf_results(connection, **handoff)
        assert connection.commits == 0 and connection.rollbacks >= 1
        assert connection.cursors_open == 0
        for table in (
            "task.TaskSuggestion", "capex.Assessment", "capex.BundleDecision",
            "capex.LlmShowcaseRun", "mvp_review.documents", "mvp_review.imports",
        ):
            assert connection.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        connection.close()


def test_readback_mismatch_rolls_back_every_stage(handoff, native_helpers, tmp_path):
    path = tmp_path / "review.db"
    ReviewStore(path).close()
    connection = AtomicSqlHarness(path, readback_delta=-1)
    try:
        with pytest.raises(ValueError, match="SQL read-back"):
            sql.persist_adf_results(connection, **handoff)
        assert connection.commits == 0 and connection.rollbacks == 1
        assert connection.db.execute("SELECT COUNT(*) FROM task.TaskSuggestion").fetchone()[0] == 0
        assert connection.db.execute("SELECT COUNT(*) FROM mvp_review.imports").fetchone()[0] == 0
        assert connection.cursors_open == 0
    finally:
        connection.close()


@pytest.mark.parametrize("change", ["invoice", "source_hash", "missing_task", "missing_model", "model_result"])
def test_invalid_coverage_is_rejected_before_starting_a_transaction(handoff, tmp_path, change):
    if change == "invoice":
        tasks = list(handoff["result"].task_determinations)
        tasks[0] = tasks[0].model_copy(update={"invoice_id": "WRONG_INVOICE"})
        handoff["result"] = handoff["result"].model_copy(update={"task_determinations": tasks})
    elif change == "source_hash":
        refs = list(handoff["request"].packages)
        refs[0] = refs[0].model_copy(update={"expected_source_sha256": "0" * 64})
        handoff["request"] = handoff["request"].model_copy(update={"packages": refs})
    elif change == "missing_task":
        handoff["result"] = handoff["result"].model_copy(update={
            "task_determinations": handoff["result"].task_determinations[1:],
        })
    elif change == "missing_model":
        handoff["workflow"] = None
    else:
        handoff["model_result"] = handoff["model_result"].model_copy(update={"assessments": []})
    path = tmp_path / "review.db"
    ReviewStore(path).close()
    connection = AtomicSqlHarness(path)
    try:
        with pytest.raises(ValueError):
            sql.persist_adf_results(connection, **handoff)
        assert not connection.queries
        assert connection.commits == 0
    finally:
        connection.close()


@pytest.mark.parametrize("change", [
    "approved", "missing_documents", "missing_partition", "unknown_source", "wrong_manifest",
    "wrong_media", "archive_hash", "missing_member", "wrong_count",
])
def test_adf_review_rejects_malformed_or_unpinned_handoffs(handoff, tmp_path, change):
    payload = json.loads(json.dumps(handoff["review"]))
    if change == "approved":
        payload["businessApproved"] = True
    elif change == "missing_documents":
        del payload["documents"]
    elif change == "missing_partition":
        payload["partitions"] = []
    elif change == "unknown_source":
        payload["documents"][0]["document"]["uri"] += ".wrong"
    elif change == "wrong_manifest":
        payload["sourceManifest"]["workflowRunId"] = "ANOTHER_RUN"
    elif change == "wrong_media":
        payload["documents"][0]["document"]["mediaType"] = "application/zip"
    elif change == "archive_hash":
        payload["documents"][0]["document"]["archiveSha256"] = "0" * 64
    elif change == "missing_member":
        payload["documents"][0]["document"]["archiveMember"] = "missing.pdf"
    else:
        payload["sqlWrites"] -= 1
    store = ReviewStore(tmp_path / "review.db")
    try:
        with pytest.raises(ValueError):
            store.import_result(payload, read=handoff["read"])
        assert store.connection.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 0
    finally:
        store.close()


def test_loopback_review_reads_sql_documents_and_persists_analyst_decisions(handoff, tmp_path):
    path = tmp_path / "review.db"
    ReviewStore(path).close()
    connections = []

    def factory():
        connection = SqlDialectHarness(tmp_path / "batch.db", path)
        connections.append(connection)
        return connection

    store = SqlReviewStore(factory(), connection_factory=factory)
    store.import_result(handoff["review"], read=handoff["read"])
    run_id = handoff["result"].workflow_run_id
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store, run_id, 0))
    port = server.server_address[1]
    server.RequestHandlerClass = make_handler(store, run_id, port)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    try:
        with urlopen(url, timeout=5) as response:
            page = response.read().decode()
            assert response.status == 200 and "LOCAL DEMO PROTOTYPE" in page
        match = re.search(r'name="csrf" value="([^"]+)"', page)
        assert match is not None
        documents = handoff["review"]["documents"]
        pdf_id = documents[0]["document"]["documentId"]
        workbook_id = next(item["document"]["documentId"] for item in documents
                           if item["document"]["mediaType"].endswith("spreadsheetml.sheet"))
        for doc_id, media, prefix in ((pdf_id, "application/pdf", b"%PDF-"),
                                      (workbook_id, "application/octet-stream", b"PK")):
            with urlopen(url + "/document?" + urlencode({"run": run_id, "id": doc_id}), timeout=5) as response:
                assert response.headers["Content-Type"] == media
                assert response.read().startswith(prefix)
        form = {
            "csrf": match.group(1), "run": run_id, "document_id": pdf_id,
            "expected_version": 0, "actor": "MOCK_SQL_ANALYST", "reason": "MOCK_ONLY",
            "action": "DEFER", "correction_field": "", "correction_value": "",
        }
        request = Request(url + "/decision", data=urlencode(form).encode(), headers={"Origin": url})
        with urlopen(request, timeout=5) as response:
            assert b"DEFERRED" in response.read()
        assert store.history(run_id, pdf_id)[0]["decision"]["actor"] == "MOCK_SQL_ANALYST"
        assert len(connections) >= 5
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        store.close()


@pytest.mark.parametrize("authentication", [None, "managed-identity", "azure-cli"])
def test_sql_review_cli_exports_persisted_batch_without_blob_access(
    handoff, tmp_path, monkeypatch, authentication,
):
    from autarch.capex import sql_connection

    path = tmp_path / "review.db"
    ReviewStore(path).close()

    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return SqlDialectHarness(tmp_path / "batch.db", path)

    store = SqlReviewStore(factory())
    store.import_result(handoff["review"], read=handoff["read"])
    store.close()
    calls.clear()
    monkeypatch.setenv("AZURE_CLIENT_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setattr(sql_connection, "connect_azure_sql", factory)
    output = tmp_path / "analyst-audit.json"
    authentication_args = ["--sql-auth", authentication] if authentication else []
    assert review.main([
        "--store", "azure-sql", *authentication_args,
        "export", "--workflow-run-id", handoff["result"].workflow_run_id,
        "--output", str(output),
    ]) == 0
    assert json.loads(output.read_text())["result"]["workflowRunId"] == handoff["result"].workflow_run_id
    assert calls[0]["authentication"] == (authentication or "managed-identity")
    assert calls[0]["managed_identity_client_id"] == (
        None if authentication == "azure-cli" else "11111111-2222-3333-4444-555555555555"
    )


def test_review_cli_rejects_cli_auth_with_sqlite(tmp_path):
    with pytest.raises(SystemExit) as failure:
        review.main([
            "--db", str(tmp_path / "review.db"), "--sql-auth", "azure-cli",
            "serve", "--workflow-run-id", "demo",
        ])
    assert failure.value.code == 2
    assert not (tmp_path / "review.db").exists()


def test_authenticated_review_reads_already_imported_adf_sources_without_pdf_mislabeling(
    handoff, identity, tmp_path,
):
    verify, token = identity
    path = tmp_path / "review.db"
    store = ReviewStore(path)
    store.import_result(handoff["review"], read=handoff["read"])
    store.close()
    run_id = handoff["result"].workflow_run_id
    with TestClient(create_app("review", verifier=verify, store_factory=lambda: ReviewStore(path))) as http:
        response = http.get(f"/v1/review/runs/{run_id}", headers=headers(token()))
        assert response.status_code == 200 and not response.json()["result"]["synthetic"]
        for item in handoff["review"]["documents"]:
            doc = item["document"]
            response = http.get(
                f"/v1/review/runs/{run_id}/documents/{doc['documentId']}", headers=headers(token()),
            )
            assert response.status_code == 200
            expected_media = "application/pdf" if doc["mediaType"] == "application/pdf" else "application/octet-stream"
            assert response.headers["Content-Type"] == expected_media


class CatalogConnection:
    def __init__(self, *, missing=(), rows=None):
        self.missing = missing
        self.rows = rows
        self.query = ""
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self

    def execute(self, query):
        self.query = query

    def fetchall(self):
        if self.query == sql._ACCESS_QUERY:
            return self.missing
        payload = json.dumps(self.rows)
        return [(payload[:len(payload) // 2],), (payload[len(payload) // 2:],)]

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


@pytest.mark.parametrize("change", ["none", "task", "source", "empty", "shape", "permission"])
def test_sql_catalog_requires_effective_permissions_and_exact_reference(change):
    master = TaskMasterSnapshot.model_validate_json(MASTER.read_bytes())
    catalog = json.loads(MASTER.read_bytes())
    rows = [{
        **task, "regionCode": "US", "sourceFile": catalog["source"]["fileName"],
        "sourceSheet": catalog["source"]["sheetName"], "sourceVersion": catalog["source"]["sourceVersion"],
        "sourceSha256": catalog["source"]["sha256"],
    } for task in catalog["tasks"]]
    if change == "task":
        rows[0]["description"] = "DRIFTED TEST TASK"
    elif change == "source":
        for row in rows:
            row["sourceSha256"] = "0" * 64
    elif change == "empty":
        rows = []
    elif change == "shape":
        rows = {"invalid": True}
    connection = CatalogConnection(
        rows=rows, missing=[("mvp_review.imports", "INSERT", None)] if change == "permission" else (),
    )
    if change == "none":
        actual = sql.load_adf_task_master(connection, master)
        assert {task.task_code: task.model_dump(exclude={"search_text"}) for task in actual.tasks} == {
            task.task_code: task.model_dump(exclude={"search_text"}) for task in master.tasks
        }
        assert actual.snapshot_id.startswith("sql-us-")
    else:
        with pytest.raises(ValueError):
            sql.load_adf_task_master(connection, master)
    assert connection.closed and connection.rollbacks == 1
