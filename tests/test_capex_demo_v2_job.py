from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from autarch.capex.demo_v2_contracts import (
    DemoWorkflowRequest,
    InvoicePackageReference,
    TaskDetermination,
)
from autarch.capex.demo_v2_job import (
    _package_from_payload,
    _read_bytes,
    run_demo_v2_workflow,
)
from autarch.capex.demo_v2_normalization import NormalizedInvoicePackage
from autarch.capex.demo_v2_reference import (
    load_demo_policy_snapshot,
    load_task_master_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "examples" / "data" / "capex"
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)


def _package() -> NormalizedInvoicePackage:
    return NormalizedInvoicePackage.model_validate_json(
        (DATA / "normalized-v2" / "bruegmann.normalized.json").read_text(
            encoding="utf-8"
        )
    )


def _task_determinations() -> list[TaskDetermination]:
    payload = json.loads(
        (
            DATA
            / "results-v2"
            / "bruegmann.task-suggestions.json"
        ).read_text(encoding="utf-8")
    )
    return [
        TaskDetermination.model_validate(item)
        for item in payload["taskDeterminations"]
    ]


def _request(
    package: NormalizedInvoicePackage,
    *,
    persist_results: bool = False,
    fail_on_review: bool = False,
) -> DemoWorkflowRequest:
    return DemoWorkflowRequest(
        workflowRunId="workflow-job-test",
        requestedAtUtc=NOW,
        packages=[
            InvoicePackageReference(
                packageId=package.package_id,
                runId=package.run_id,
                packageUri="bruegmann.normalized.json",
                expectedInvoiceId=package.invoice_id,
                expectedSourceSha256=package.source.sha256,
            )
        ],
        taskRegion="US",
        modelDeployment="gpt-5.1",
        taskPromptVersion="capex-demo-v2-task-suggestion-v1",
        capitalizationPolicyVersion="circle-k-na-demo-unapproved-2026-10-30",
        bundlingPolicyVersion="circle-k-na-demo-unapproved-2026-10-30",
        persistResults=persist_results,
        failOnReview=fail_on_review,
    )


def _resolver(
    package: NormalizedInvoicePackage,
    request: DemoWorkflowRequest,
) -> Sequence[TaskDetermination]:
    return [
        item.model_copy(
            update={
                "workflow_run_id": request.workflow_run_id,
                "created_at_utc": NOW,
            }
        )
        for item in _task_determinations()
    ]


def test_runs_one_complete_package_without_model_calls() -> None:
    package = _package()
    request = _request(package)

    result = run_demo_v2_workflow(
        request=request,
        packages=[package],
        task_master=load_task_master_snapshot(
            DATA / "reference-v2" / "task-master-us-v2.json"
        ),
        policy=load_demo_policy_snapshot(
            DATA / "reference-v2" / "policy-config-v2.json"
        ),
        task_resolver=_resolver,
        requested_at_utc=NOW,
        completed_at_utc=NOW,
    )

    assert result.status == "SUCCEEDED"
    assert len(result.packages) == 1
    assert result.packages[0].line_count == 12
    assert result.packages[0].resolved_task_count == 9
    assert result.packages[0].review_task_count == 3
    assert result.packages[0].review_assessment_count == 12
    assert len(result.task_determinations) == 12
    assert len(result.assessments) == 12
    assert result.bundle_groups == []
    assert result.errors == []


def test_rejects_package_identity_mismatch_before_processing() -> None:
    package = _package()
    request = _request(package).model_copy(
        update={
            "packages": [
                _request(package).packages[0].model_copy(
                    update={"expected_invoice_id": "wrong-invoice"}
                )
            ]
        }
    )

    result = run_demo_v2_workflow(
        request=request,
        packages=[package],
        task_master=load_task_master_snapshot(
            DATA / "reference-v2" / "task-master-us-v2.json"
        ),
        policy=load_demo_policy_snapshot(
            DATA / "reference-v2" / "policy-config-v2.json"
        ),
        task_resolver=_resolver,
        requested_at_utc=NOW,
        completed_at_utc=NOW,
    )

    assert result.status == "FAILED"
    assert result.task_determinations == []
    assert result.errors[0].code == "INVOICE_ID_MISMATCH"


def test_reports_partial_when_required_persistence_is_not_configured() -> None:
    package = _package()

    result = run_demo_v2_workflow(
        request=_request(package, persist_results=True),
        packages=[package],
        task_master=load_task_master_snapshot(
            DATA / "reference-v2" / "task-master-us-v2.json"
        ),
        policy=load_demo_policy_snapshot(
            DATA / "reference-v2" / "policy-config-v2.json"
        ),
        task_resolver=_resolver,
        requested_at_utc=NOW,
        completed_at_utc=NOW,
    )

    assert result.status == "PARTIAL"
    assert result.errors[0].code == "CONNECTION_NOT_CONFIGURED"


def test_fail_on_review_turns_review_results_into_partial_status() -> None:
    package = _package()

    result = run_demo_v2_workflow(
        request=_request(package, fail_on_review=True),
        packages=[package],
        task_master=load_task_master_snapshot(
            DATA / "reference-v2" / "task-master-us-v2.json"
        ),
        policy=load_demo_policy_snapshot(
            DATA / "reference-v2" / "policy-config-v2.json"
        ),
        task_resolver=_resolver,
        requested_at_utc=NOW,
        completed_at_utc=NOW,
    )

    assert result.status == "PARTIAL"
    assert {error.code for error in result.errors} == {
        "TASK_REVIEW_REQUIRED",
        "ASSESSMENT_REVIEW_REQUIRED",
    }


def test_selects_requested_package_from_jc_workbook_artifact() -> None:
    payload = json.loads(
        (
            DATA / "normalized-v2" / "jc2000a07814.normalized.json"
        ).read_text(encoding="utf-8")
    )
    package_id = payload["packages"][0]["packageId"]

    package = _package_from_payload(payload, package_id=package_id)

    assert package.package_id == package_id
    assert package.invoice_id == payload["packages"][0]["invoiceId"]


def test_reads_native_windows_path_without_treating_drive_as_uri(
    tmp_path: Path,
) -> None:
    path = tmp_path / "input.json"
    path.write_text('{"ok": true}', encoding="utf-8")

    assert _read_bytes(str(path)) == b'{"ok": true}'
