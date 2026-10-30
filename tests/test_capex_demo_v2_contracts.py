from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from autarch.capex.demo_v2_contracts import (
    BundleCandidateGroup,
    BundleCandidateMember,
    BundlingDisposition,
    CapitalizationAssessment,
    DemoWorkflowRequest,
    DemoWorkflowResult,
    TaskDetermination,
    WorkflowPackageSummary,
    derive_bundle_group_id,
)


NOW = datetime(2026, 10, 30, 16, 0, tzinfo=timezone.utc)
HASH = "a" * 64


def _task(**overrides: object) -> TaskDetermination:
    values = {
        "workflowRunId": "workflow-1",
        "packageId": "package-1",
        "invoiceId": "invoice-1",
        "lineId": "line-1",
        "resolutionStatus": "RESOLVED",
        "suggestedTaskCode": "1200",
        "matchMethod": "LANGCHAIN",
        "confidence": 0.9,
        "rationale": "The description matches the governed task.",
        "taskMasterSha256": HASH,
        "modelDeployment": "gpt-5.1",
        "promptVersion": "task-v2",
        "createdAtUtc": NOW,
    }
    values.update(overrides)
    return TaskDetermination.model_validate(values)


def _assessment(**overrides: object) -> CapitalizationAssessment:
    values = {
        "workflowRunId": "workflow-1",
        "packageId": "package-1",
        "invoiceId": "invoice-1",
        "lineId": "line-1",
        "suggestedTaskCode": "1200",
        "natureResult": "CAPITALIZABLE",
        "repairMaintenanceIndicator": False,
        "neverCapitalizeHit": False,
        "unitCostMeetsThreshold": True,
        "thresholdAmount": 2500.0,
        "thresholdCurrency": "usd",
        "recommendation": "CAPEX",
        "outcomeCategory": "MEETS_UNIT_THRESHOLD",
        "bundlingDisposition": "NOT_ELIGIBLE",
        "rationale": "The unit cost meets the approved threshold.",
        "policyCitations": ["policy:threshold"],
        "policyVersion": "demo-v1",
        "createdAtUtc": NOW,
    }
    values.update(overrides)
    return CapitalizationAssessment.model_validate(values)


def test_request_accepts_camel_case_and_serializes_camel_case() -> None:
    request = DemoWorkflowRequest.model_validate(
        {
            "workflowRunId": "workflow-1",
            "requestedAtUtc": "2026-10-30T16:00:00Z",
            "packages": [
                {
                    "packageId": "package-1",
                    "runId": "prep-run-1",
                    "packageUri": (
                        "https://storage.example/packages/package-1.json"
                    ),
                    "expectedSourceSha256": HASH,
                }
            ],
            "modelDeployment": "gpt-5.1",
            "taskPromptVersion": "task-v2",
            "capitalizationPolicyVersion": "demo-v1",
            "bundlingPolicyVersion": "demo-v1",
        }
    )

    payload = request.model_dump(mode="json", by_alias=True)

    assert payload["schemaVersion"] == "2.0"
    assert payload["workflowRunId"] == "workflow-1"
    assert payload["packages"][0]["packageId"] == "package-1"
    assert "workflow_run_id" not in payload


def test_request_rejects_duplicate_package_ids() -> None:
    package = {
        "packageId": "package-1",
        "runId": "prep-run-1",
        "packageUri": "https://storage.example/package-1.json",
    }

    with pytest.raises(ValidationError, match="duplicate packageId"):
        DemoWorkflowRequest.model_validate(
            {
                "workflowRunId": "workflow-1",
                "requestedAtUtc": NOW,
                "packages": [package, dict(package)],
                "modelDeployment": "gpt-5.1",
                "taskPromptVersion": "task-v2",
            }
        )


def test_request_rejects_non_utc_timestamp() -> None:
    with pytest.raises(ValidationError, match="UTC offset"):
        DemoWorkflowRequest.model_validate(
            {
                "workflowRunId": "workflow-1",
                "requestedAtUtc": "2026-10-30T16:00:00",
                "packages": [
                    {
                        "packageId": "package-1",
                        "runId": "prep-run-1",
                        "packageUri": "file:///package-1.json",
                    }
                ],
                "modelDeployment": "gpt-5.1",
                "taskPromptVersion": "task-v2",
            }
        )


def test_task_determination_separates_task_from_capitalization() -> None:
    task = _task()
    payload = task.model_dump(mode="json", by_alias=True)

    assert payload["suggestedTaskCode"] == "1200"
    assert "recommendation" not in payload
    assert "CAPEX" not in payload.values()
    assert "OPEX" not in payload.values()


def test_resolved_task_requires_governed_task_code() -> None:
    with pytest.raises(ValidationError, match="suggestedTaskCode"):
        _task(suggestedTaskCode=None)


def test_review_task_requires_review_method_and_reason() -> None:
    with pytest.raises(ValidationError, match="unresolvedReason"):
        _task(
            resolutionStatus="REVIEW",
            suggestedTaskCode=None,
            matchMethod="REVIEW",
        )

    task = _task(
        resolutionStatus="REVIEW",
        suggestedTaskCode=None,
        matchMethod="REVIEW",
        unresolvedReason="No governed candidate met the confidence floor.",
    )

    assert task.suggested_task_code is None


def test_missing_assessment_input_forces_review() -> None:
    with pytest.raises(ValidationError, match="must recommend REVIEW"):
        _assessment(missingInputs=["siteId"])

    assessment = _assessment(
        natureResult="REVIEW",
        recommendation="REVIEW",
        outcomeCategory="MISSING_CONTEXT",
        bundlingDisposition="REVIEW",
        missingInputs=["siteId"],
        reviewReasons=["Bundling requires an approved site identifier."],
        unitCostMeetsThreshold=None,
    )

    assert assessment.recommendation.value == "REVIEW"


def test_currency_is_normalized_and_threshold_fields_are_paired() -> None:
    assessment = _assessment()

    assert assessment.threshold_currency == "USD"

    with pytest.raises(ValidationError, match="provided together"):
        _assessment(thresholdCurrency=None)


def test_bundle_group_requires_unique_members_and_context() -> None:
    member = {
        "packageId": "package-1",
        "invoiceId": "invoice-1",
        "lineId": "line-1",
        "quantity": 1,
        "unitCost": 1000,
        "lineAmount": 1000,
    }

    with pytest.raises(ValidationError, match="duplicate"):
        BundleCandidateGroup.model_validate(
            {
                "workflowRunId": "workflow-1",
                "bundleGroupId": "bundle-1",
                "suggestedTaskCode": "1200",
                "siteId": "site-1",
                "projectId": "project-1",
                "currency": "USD",
                "totalAmount": 2000,
                "thresholdAmount": 2500,
                "disposition": "CANDIDATE",
                "members": [member, dict(member)],
                "rationale": "Common task, site, and project.",
                "createdAtUtc": NOW,
            }
        )

    review_group = BundleCandidateGroup.model_validate(
        {
            "workflowRunId": "workflow-1",
            "bundleGroupId": "bundle-1",
            "suggestedTaskCode": "1200",
            "currency": "USD",
            "totalAmount": 2000,
            "disposition": "REVIEW",
            "members": [
                member,
                dict(member, lineId="line-2"),
            ],
            "missingInputs": ["siteId", "projectId"],
            "rationale": "Required grouping context is missing.",
            "createdAtUtc": NOW,
        }
    )

    assert review_group.site_id is None


def test_bundle_group_id_is_stable_and_uses_full_grouping_key() -> None:
    first = derive_bundle_group_id(
        "workflow-1", "1200", "site-1", "project-1", "usd"
    )
    second = derive_bundle_group_id(
        "workflow-1", "1200", "site-1", "project-1", "USD"
    )
    different = derive_bundle_group_id(
        "workflow-1", "1200", "site-2", "project-1", "USD"
    )

    assert first == second
    assert first.startswith("bundle-")
    assert first != different


def test_complete_result_validates_cross_stage_references() -> None:
    result = DemoWorkflowResult(
        workflow_run_id="workflow-1",
        status="SUCCEEDED",
        started_at_utc=NOW,
        completed_at_utc=NOW,
        packages=[
            WorkflowPackageSummary(
                package_id="package-1",
                invoice_id="invoice-1",
                line_count=1,
                resolved_task_count=1,
                review_task_count=0,
                capex_count=1,
                opex_count=0,
                review_assessment_count=0,
            )
        ],
        task_determinations=[_task()],
        assessments=[_assessment()],
    )

    payload = result.model_dump(mode="json", by_alias=True)

    assert payload["status"] == "SUCCEEDED"
    assert payload["packages"][0]["capexCount"] == 1
    assert payload["persistence"]["assessments"] == 0


def test_result_rejects_bundle_member_without_assessment() -> None:
    group = BundleCandidateGroup(
        workflow_run_id="workflow-1",
        bundle_group_id="bundle-1",
        suggested_task_code="1200",
        site_id="site-1",
        project_id="project-1",
        currency="USD",
        total_amount=3000,
        threshold_amount=2500,
        disposition=BundlingDisposition.CANDIDATE,
        members=[
            BundleCandidateMember(
                package_id="package-1",
                invoice_id="invoice-1",
                line_id="line-1",
                quantity=1,
                unit_cost=1500,
                line_amount=1500,
            ),
            BundleCandidateMember(
                package_id="package-1",
                invoice_id="invoice-1",
                line_id="line-2",
                quantity=1,
                unit_cost=1500,
                line_amount=1500,
            ),
        ],
        rationale="Common task, site, and project.",
        created_at_utc=NOW,
    )

    with pytest.raises(ValidationError, match="reference an assessment"):
        DemoWorkflowResult(
            workflow_run_id="workflow-1",
            status="SUCCEEDED",
            started_at_utc=NOW,
            completed_at_utc=NOW,
            packages=[
                WorkflowPackageSummary(
                    package_id="package-1",
                    invoice_id="invoice-1",
                    line_count=1,
                    resolved_task_count=1,
                    review_task_count=0,
                    capex_count=1,
                    opex_count=0,
                    review_assessment_count=0,
                )
            ],
            task_determinations=[_task()],
            assessments=[_assessment()],
            bundle_groups=[group],
        )


def test_generated_schemas_are_strict_and_camel_case() -> None:
    request_schema = DemoWorkflowRequest.model_json_schema(by_alias=True)
    result_schema = DemoWorkflowResult.model_json_schema(by_alias=True)

    assert request_schema["additionalProperties"] is False
    assert "workflowRunId" in request_schema["properties"]
    assert "workflow_run_id" not in request_schema["properties"]
    assert result_schema["additionalProperties"] is False
    assert "taskDeterminations" in result_schema["properties"]
