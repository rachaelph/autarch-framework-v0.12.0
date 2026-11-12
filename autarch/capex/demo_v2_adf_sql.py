"""Use existing SQL contracts for one atomic ADF result and review handoff."""
from __future__ import annotations

import json
from typing import Callable, Sequence

from .demo_v2_contracts import DemoWorkflowRequest, DemoWorkflowResult
from .demo_v2_asset_classes import AssetClassMaster, is_eu_invoice
from .demo_v2_job import _persist_task_determinations
from .demo_v2_llm_decisions import SqlLlmDecisionWorkflow
from .demo_v2_normalization import NormalizedInvoicePackage
from .demo_v2_persistence import persist_demo_v2_results
from .demo_v2_reference import TaskMasterSnapshot, canonical_sha256
from .demo_v2_review import SqlReviewStore
from .demo_v2_sql_job import _task_master
from .demo_v2_store import SqlSession
from .persistence import DatabaseConnection, DatabaseCursor


_ACCESS_QUERY = """
SELECT object_name, permission_name, column_name FROM (VALUES
    ('pub.v1_TaskMaster', 'SELECT', NULL),
    ('ref.assetClassMaster', 'SELECT', NULL),
    ('task.assetClassSuggestion', 'SELECT', NULL), ('task.assetClassSuggestion', 'INSERT', NULL),
    ('task.TaskSuggestion', 'UPDATE', 'PotentialBundling'),
    ('task.TaskSuggestion', 'UPDATE', 'PotentialTagsJson'),
    ('task.assetClassSuggestion', 'UPDATE', 'PotentialBundling'),
    ('task.assetClassSuggestion', 'UPDATE', 'PotentialTagsJson'),
    ('task.TaskSuggestion', 'SELECT', NULL), ('task.TaskSuggestion', 'INSERT', NULL),
    ('task.TaskSuggestion', 'UPDATE', 'MatchMethod'),
    ('task.TaskSuggestion', 'UPDATE', 'ItemIdentified'),
    ('task.TaskSuggestion', 'UPDATE', 'UnresolvedReason'),
    ('task.TaskSuggestion', 'UPDATE', 'ContractVersion'),
    ('capex.AssessmentRun', 'SELECT', NULL), ('capex.AssessmentRun', 'INSERT', NULL),
    ('capex.Assessment', 'SELECT', NULL), ('capex.Assessment', 'INSERT', NULL),
    ('capex.Assessment', 'UPDATE', 'TaskSuggestionID'),
    ('capex.Assessment', 'UPDATE', 'AssetClassSuggestionID'),
    ('capex.Assessment', 'UPDATE', 'SuggestedAssetClassCode'),
    ('capex.Assessment', 'UPDATE', 'RepairMaintenanceFlag'),
    ('capex.Assessment', 'UPDATE', 'AroFlag'),
    ('capex.Assessment', 'UPDATE', 'PotentialTagsJson'),
    ('capex.Assessment', 'UPDATE', 'CategorySuggestion'),
    ('capex.BundleCandidateGroup', 'SELECT', NULL), ('capex.BundleCandidateGroup', 'INSERT', NULL),
    ('capex.BundleCandidateGroup', 'UPDATE', 'SuggestedAssetClassCode'),
    ('capex.BundleCandidateMember', 'SELECT', NULL), ('capex.BundleCandidateMember', 'INSERT', NULL),
    ('capex.BundleDecision', 'SELECT', NULL), ('capex.BundleDecision', 'INSERT', NULL),
    ('capex.LlmShowcaseRun', 'SELECT', NULL), ('capex.LlmShowcaseRun', 'INSERT', NULL),
    ('mvp_review.imports', 'SELECT', NULL), ('mvp_review.imports', 'INSERT', NULL),
    ('mvp_review.documents', 'SELECT', NULL), ('mvp_review.documents', 'INSERT', NULL)
) AS required(object_name, permission_name, column_name)
WHERE COALESCE(
    CASE WHEN column_name IS NULL
    THEN HAS_PERMS_BY_NAME(object_name, 'OBJECT', permission_name)
    ELSE HAS_PERMS_BY_NAME(object_name, 'OBJECT', permission_name, column_name, 'COLUMN')
    END, 0) <> 1;
"""


def load_adf_asset_class_master(
    connection: DatabaseConnection, expected: AssetClassMaster,
) -> AssetClassMaster:
    cursor = connection.cursor()
    try:
        cursor.execute("""
            SELECT AssetClassCode AS assetClassCode, AssetClassName AS assetClassName,
                AccountGroup AS accountGroup, JSON_QUERY(AccountMappingsJson) AS accountMappings,
                SourceFile AS sourceFile, SourceSheet AS sourceSheet, SourceSha256 AS sourceSha256
            FROM ref.assetClassMaster ORDER BY AssetClassCode FOR JSON PATH, INCLUDE_NULL_VALUES;
        """)
        rows = json.loads("".join(str(row[0]) for row in cursor.fetchall()))
        if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
            raise ValueError("SQL EU assetClassMaster is empty or invalid; apply migration 015 and seed 006.")
        metadata = {key: rows[0][key] for key in ("sourceFile", "sourceSheet", "sourceSha256")}
        if any(any(row[key] != value for key, value in metadata.items()) for row in rows):
            raise ValueError("EU SQL catalog contains mixed source-workbook provenance.")
        actual = AssetClassMaster.model_validate({
            **metadata,
            "classes": [{key: value for key, value in row.items() if key not in metadata} for row in rows],
        })
        if actual != expected:
            raise ValueError("SQL EU assetClassMaster differs from the pinned workbook snapshot.")
        return actual
    finally:
        try:
            connection.rollback()
        finally:
            cursor.close()


def _persist_asset_class_determinations(
    connection: DatabaseConnection, package: NormalizedInvoicePackage, result: DemoWorkflowResult,
) -> int:
    tasks = [item for item in result.task_determinations if item.package_id == package.package_id]
    cursor = connection.cursor()
    try:
        cursor.executemany("""
            INSERT INTO task.assetClassSuggestion
                (correlationId, packageId, invoiceId, runId, lineId, assetClassCode, resultStatus,
                 ItemIdentified, confidence, rationale, UnresolvedReason, evidenceRefs,
                 AssetClassMasterSha256, modelDeployment, promptVersion, createdAtUtc)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
        """, [
            (result.workflow_run_id, package.package_id, package.invoice_id, package.run_id, item.line_id,
             item.suggested_asset_class, item.resolution_status.value, item.item_identified,
             item.confidence, item.rationale, item.unresolved_reason, json.dumps(item.evidence_refs),
             item.task_master_sha256, item.model_deployment, item.prompt_version,
             item.created_at_utc.replace(tzinfo=None))
            for item in tasks
        ])
    finally:
        cursor.close()
    return len(tasks)


def load_adf_task_master(
    connection: DatabaseConnection, expected: TaskMasterSnapshot,
) -> TaskMasterSnapshot:
    """Read the real published catalog and reject drift from reviewed reference data."""
    cursor = connection.cursor()
    try:
        cursor.execute(_ACCESS_QUERY)
        missing = cursor.fetchall()
        if missing:
            raise ValueError("ADF SQL access is missing before inference: " + "; ".join(
                f"{row[0]} {row[1]}" + (f"({row[2]})" if row[2] else "") for row in missing
            ))
        cursor.execute(
            "SELECT * FROM pub.v1_TaskMaster WHERE regionCode='US' FOR JSON PATH, INCLUDE_NULL_VALUES;",
        )
        rows = json.loads("".join(str(row[0]) for row in cursor.fetchall()))
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("SQL TaskMaster did not return a JSON row array.")
        actual = _task_master(rows)
        expected_tasks = sorted(
            (task.model_dump(mode="json", by_alias=True, exclude={"search_text"}) for task in expected.tasks),
            key=lambda task: str(task["taskCode"]),
        )
        actual_tasks = sorted(
            (task.model_dump(mode="json", by_alias=True, exclude={"search_text"}) for task in actual.tasks),
            key=lambda task: str(task["taskCode"]),
        )
        if actual.source != expected.source or canonical_sha256({"tasks": actual_tasks}) != canonical_sha256({"tasks": expected_tasks}):
            raise ValueError("SQL TaskMaster drifted from the pinned reference; review the catalog before inference.")
        return actual
    finally:
        try:
            connection.rollback()
        finally:
            cursor.close()


class _TransactionConnection:
    """Keep legacy helper commits inside this caller-owned transaction."""

    def __init__(self, connection: DatabaseConnection):
        self.connection = connection
        self.failed = False

    def cursor(self) -> DatabaseCursor:
        if self.failed:
            raise ValueError("The ADF SQL transaction has already failed.")
        return self.connection.cursor()

    def commit(self) -> None:
        if self.failed:
            raise ValueError("Cannot commit a failed ADF SQL transaction.")

    def rollback(self) -> None:
        self.failed = True
        self.connection.rollback()

    def close(self) -> None:
        raise ValueError("Only the ADF SQL transaction owner may close the connection.")


def persist_adf_results(
    connection: DatabaseConnection, *, request: DemoWorkflowRequest,
    packages: Sequence[NormalizedInvoicePackage], result: DemoWorkflowResult,
    model_result: DemoWorkflowResult | None, workflow: SqlLlmDecisionWorkflow | None,
    review: dict, read: Callable[[str], bytes],
) -> dict[str, int]:
    """Commit task, assessment, bundle, evidence and review rows together or roll back."""
    expected = {(package.package_id, line.line_id, package.invoice_id)
                for package in packages for line in package.lines}
    if expected != {(task.package_id, task.line_id, task.invoice_id) for task in result.task_determinations} \
            or expected != {(assessment.package_id, assessment.line_id, assessment.invoice_id)
                            for assessment in result.assessments} \
            or len(expected) != len(result.task_determinations) or len(expected) != len(result.assessments):
        raise ValueError("SQL persistence requires exact, unique task/assessment coverage of every source line.")
    if result.status != "SUCCEEDED" or result.errors or result.workflow_run_id != request.workflow_run_id:
        raise ValueError("Only a complete validated ADF workflow may be persisted.")
    if len(packages) != len({package.package_id for package in packages}) \
            or {(ref.package_id, ref.expected_invoice_id, ref.expected_source_sha256)
                for ref in request.packages} != {
                    (package.package_id, package.invoice_id, package.source.sha256) for package in packages
                }:
        raise ValueError("SQL source packages must match the pinned workflow request.")
    model_tasks = [task for task in result.task_determinations if task.model_deployment is not None]
    if (model_result is None) != (workflow is None) or bool(model_tasks) != (model_result is not None):
        raise ValueError("SQL model evidence must cover exactly the model-backed recommendations.")
    if model_result is not None:
        model_keys = {(task.package_id, task.line_id) for task in model_tasks}
        if model_result.workflow_run_id != result.workflow_run_id or model_result.status != "SUCCEEDED" \
                or set((task.package_id, task.line_id) for task in model_result.task_determinations) != model_keys \
                or sorted(model_result.task_determinations, key=lambda task: (task.package_id, task.line_id)) != sorted(
                    model_tasks, key=lambda task: (task.package_id, task.line_id),
                ) or sorted(model_result.assessments, key=lambda item: (item.package_id, item.line_id)) != sorted(
                    (item for item in result.assessments if (item.package_id, item.line_id) in model_keys),
                    key=lambda item: (item.package_id, item.line_id),
                ):
            raise ValueError("SQL model evidence differs from the complete batch recommendations.")
    transaction = _TransactionConnection(connection)
    tasks_by_package = {
        package.package_id: [task for task in result.task_determinations if task.package_id == package.package_id]
        for package in packages
    }
    if any(item.classification_type != ("ASSET_CLASS" if is_eu_invoice(package) else "TASK")
           for package in packages for item in tasks_by_package[package.package_id]):
        raise ValueError("EU XLSX invoices require asset-class suggestions; NA ZIP invoices require task suggestions.")
    try:
        cursor = transaction.cursor()
        try:
            cursor.execute("SET XACT_ABORT ON; SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;")
        finally:
            cursor.close()
        task_count = sum(_persist_task_determinations(
            connection=transaction, package=package, request=request if any(
                task.model_deployment is not None for task in tasks_by_package[package.package_id]
            ) else request.model_copy(update={
                "model_deployment": "NOT_CALLED_REFERENCE_REVIEW",
                "task_prompt_version": "adf-reference-review-v1",
            }),
            determinations=tasks_by_package[package.package_id],
        ) for package in packages if not is_eu_invoice(package))
        asset_class_count = sum(
            _persist_asset_class_determinations(transaction, package, result)
            for package in packages if is_eu_invoice(package)
        )
        counts = persist_demo_v2_results(
            connection=transaction, assessments=result.assessments, groups=result.bundle_groups,
            workflow_run_id=result.workflow_run_id, created_at_utc=result.completed_at_utc,
        )
        if any(item.category_suggestion is not None for item in result.assessments):
            cursor = transaction.cursor()
            try:
                cursor.executemany("""
                    UPDATE capex.Assessment SET CategorySuggestion=?
                    WHERE WorkflowRunId=? AND PackageId=? AND InvoiceId=? AND LineId=?;
                """, [
                    (item.category_suggestion, result.workflow_run_id, item.package_id, item.invoice_id, item.line_id)
                    for item in result.assessments
                ])
                cursor.execute("""
                    SELECT PackageId, InvoiceId, LineId, CategorySuggestion
                    FROM capex.Assessment WHERE WorkflowRunId=?;
                """, (result.workflow_run_id,))
                saved_categories = [tuple(row) for row in cursor.fetchall()]
                expected_categories = [
                    (item.package_id, item.invoice_id, item.line_id, item.category_suggestion)
                    for item in result.assessments
                ]
                if len(saved_categories) != len(expected_categories) or set(saved_categories) != set(expected_categories):
                    raise ValueError("SQL did not confirm every assessment's exact category suggestion.")
            finally:
                cursor.close()
        asset_groups = [group for group in result.bundle_groups if group.suggested_asset_class_code is not None]
        if asset_groups:
            cursor = transaction.cursor()
            try:
                cursor.executemany("""
                    UPDATE capex.BundleCandidateGroup SET SuggestedAssetClassCode=?
                    WHERE WorkflowRunId=? AND BundleGroupId=?;
                """, [(group.suggested_asset_class_code, result.workflow_run_id, group.bundle_group_id)
                      for group in asset_groups])
            finally:
                cursor.close()
        # Non-model exceptions are explicitly marked, never attributed to GPT.
        exception_tasks = [task for task in result.task_determinations
                           if task.model_deployment is None and task.classification_type == "TASK"]
        cursor = transaction.cursor()
        try:
            if exception_tasks:
                cursor.executemany("""
                    UPDATE task.TaskSuggestion SET MatchMethod='REVIEW', ItemIdentified=NULL,
                        UnresolvedReason=?, ContractVersion='2.0'
                    WHERE correlationId=? AND packageId=? AND lineId=?;
                """, [(task.unresolved_reason, result.workflow_run_id, task.package_id, task.line_id)
                      for task in exception_tasks])
            cursor.execute("""
                UPDATE assessment SET TaskSuggestionID=task.taskSuggestionId
                FROM capex.Assessment assessment JOIN task.TaskSuggestion task
                ON task.correlationId=assessment.WorkflowRunId AND task.packageId=assessment.PackageId
                AND task.lineId=assessment.LineId AND task.invoiceId=assessment.InvoiceId
                WHERE assessment.WorkflowRunId=?;
            """, (result.workflow_run_id,))
            cursor.execute("""
                UPDATE assessment SET AssetClassSuggestionID=asset.assetClassSuggestionId,
                    SuggestedAssetClassCode=asset.assetClassCode
                FROM capex.Assessment assessment JOIN task.assetClassSuggestion asset
                ON asset.correlationId=assessment.WorkflowRunId AND asset.packageId=assessment.PackageId
                AND asset.lineId=assessment.LineId AND asset.invoiceId=assessment.InvoiceId
                WHERE assessment.WorkflowRunId=?;
            """, (result.workflow_run_id,))
        finally:
            cursor.close()
        if workflow is not None and model_result is not None:
            workflow.persist(transaction, model_result)
        tagged_tasks = [item for item in result.task_determinations if item.potential_tags is not None]
        if tagged_tasks:
            cursor = transaction.cursor()
            try:
                expected_tag_rows = []
                for kind, table in (("TASK", "TaskSuggestion"), ("ASSET_CLASS", "assetClassSuggestion")):
                    values = []
                    assessment_values = []
                    for task in tagged_tasks:
                        if task.classification_type != kind:
                            continue
                        tags = {tag.tag for tag in task.potential_tags or []}
                        repair = "POTENTIAL_REPAIR_MAINTENANCE_OPEX" in tags
                        aro = "POTENTIAL_ARO" in tags
                        evidence = json.dumps([
                            tag.model_dump(mode="json", by_alias=True) for tag in task.potential_tags or []
                        ])
                        assessment_values.append((
                            repair, aro, evidence, result.workflow_run_id,
                            task.package_id, task.invoice_id, task.line_id,
                        ))
                        expected_tag_rows.append((
                            task.package_id, task.invoice_id, task.line_id, repair, aro, evidence,
                        ))
                        values.append((
                            "POTENTIAL_BUNDLING" in tags, evidence,
                            result.workflow_run_id, task.package_id, task.invoice_id, task.line_id,
                        ))
                    if values:
                        cursor.executemany("""
                            UPDATE capex.Assessment SET RepairMaintenanceFlag=?, AroFlag=?, PotentialTagsJson=?
                            WHERE WorkflowRunId=? AND PackageId=? AND InvoiceId=? AND LineId=?;
                        """, assessment_values)
                        cursor.executemany(f"""
                            UPDATE task.{table} SET PotentialBundling=?, PotentialTagsJson=?
                            WHERE correlationId=? AND packageId=? AND invoiceId=? AND lineId=?;
                        """, values)
                cursor.execute("""
                    SELECT PackageId, InvoiceId, LineId, RepairMaintenanceFlag, AroFlag, PotentialTagsJson
                    FROM capex.Assessment WHERE WorkflowRunId=? AND PotentialTagsJson IS NOT NULL;
                """, (result.workflow_run_id,))
                saved_tags = [tuple(row) for row in cursor.fetchall()]
                if len(saved_tags) != len(expected_tag_rows) or set(saved_tags) != set(expected_tag_rows):
                    raise ValueError("SQL did not confirm every screened line's potential review tags.")
            finally:
                cursor.close()
        review_store = SqlReviewStore(transaction)
        try:
            review_store.import_result(review, read=read)
        finally:
            # Do not close the shared caller-owned connection through SqlSession.
            if isinstance(review_store.connection, SqlSession):
                review_store.connection._close_cursors()
        cursor = transaction.cursor()
        try:
            cursor.execute("""
                SELECT
                  (SELECT COUNT_BIG(*) FROM task.TaskSuggestion WHERE correlationId=?),
                  (SELECT COUNT_BIG(*) FROM task.assetClassSuggestion WHERE correlationId=?),
                  (SELECT COUNT_BIG(*) FROM capex.Assessment WHERE WorkflowRunId=? AND
                    (TaskSuggestionID IS NOT NULL OR AssetClassSuggestionID IS NOT NULL)),
                  (SELECT COUNT_BIG(*) FROM capex.BundleDecision WHERE WorkflowRunId=?),
                  (SELECT COUNT_BIG(*) FROM mvp_review.documents WHERE run_id=?),
                  (SELECT COUNT_BIG(*) FROM mvp_review.imports WHERE run_id=?);
            """, (result.workflow_run_id,) * 6)
            saved = cursor.fetchone()
            model_bundle_count = len(workflow.bundle_decisions.items) \
                if workflow is not None and workflow.bundle_decisions is not None else 0
            expected_counts = (task_count, asset_class_count, len(expected), model_bundle_count, len(packages), 1)
            if saved is None or tuple(int(value) for value in saved) != expected_counts:
                raise ValueError("SQL read-back did not confirm complete linked results and analyst-review handoff.")
        finally:
            cursor.close()
        if transaction.failed:
            raise ValueError("The ADF SQL transaction was rolled back by a persistence helper.")
        connection.commit()
        return {
            "taskSuggestions": task_count, "assetClassSuggestions": asset_class_count,
            "assessments": counts.assessments,
            "bundleGroups": counts.bundle_groups, "bundleMembers": counts.bundle_members,
            "modelBundleDecisions": model_bundle_count,
            "reviewDocuments": len(packages), "reviewImports": 1,
        }
    except Exception:
        connection.rollback()
        raise
