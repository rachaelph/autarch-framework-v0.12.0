"""Validate Kimley-Horn task suggestions through the Demo V2 path."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Mapping

from autarch.capex.demo_v2_contracts import TaskResolutionStatus
from autarch.capex.demo_v2_normalization import NormalizedInvoicePackage
from autarch.capex.demo_v2_reference import (
    TaskMasterSnapshot,
    load_task_master_snapshot,
)
from autarch.capex.demo_v2_task_suggestion import (
    DEFAULT_MODEL_DEPLOYMENT,
    DEFAULT_TASK_PROMPT_VERSION,
    ModelTaskSuggestionBatch,
    TaskSuggestionModel,
    build_azure_task_suggestion_model,
    suggest_tasks_for_invoice,
)
from autarch.errors import ModelError


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE_PATH = (
    ROOT
    / "examples"
    / "data"
    / "capex"
    / "normalized-v2"
    / "kimley-horn.normalized.json"
)
DEFAULT_TASK_MASTER_PATH = (
    ROOT
    / "examples"
    / "data"
    / "capex"
    / "reference-v2"
    / "task-master-us-v2.json"
)
EXPECTED_SOURCE_SHA256 = (
    "ede8b96961483673d0e06d8d73f684a835f05d9a061f67a8bcb84b98be445292"
)
EXPECTED_LINE_COUNT = 10
EXPECTED_ENVIRONMENTAL_TASK_CODE = "0007"


class _CountingModel:
    def __init__(self, inner: TaskSuggestionModel):
        self.inner = inner
        self.call_count = 0

    def suggest(
        self,
        request: Mapping[str, object],
    ) -> ModelTaskSuggestionBatch:
        self.call_count += 1
        return self.inner.suggest(request)


def load_kimley_horn_package(path: Path) -> NormalizedInvoicePackage:
    """Load and verify the committed Kimley-Horn normalized package."""
    package = NormalizedInvoicePackage.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )
    if package.source.sha256 != EXPECTED_SOURCE_SHA256:
        raise ValueError(
            "Kimley-Horn source SHA-256 does not match the approved demo input"
        )
    if len(package.lines) != EXPECTED_LINE_COUNT:
        raise ValueError(
            "Kimley-Horn package must contain exactly 10 invoice lines"
        )
    return package


def validate_kimley_horn_task_suggestions(
    package: NormalizedInvoicePackage,
    snapshot: TaskMasterSnapshot,
    model: TaskSuggestionModel,
    *,
    workflow_run_id: str,
    deployment: str = DEFAULT_MODEL_DEPLOYMENT,
    prompt_version: str = DEFAULT_TASK_PROMPT_VERSION,
) -> Dict[str, object]:
    """Run V2 and return structural and scenario-level validation evidence."""
    counting_model = _CountingModel(model)
    determinations = suggest_tasks_for_invoice(
        package,
        snapshot,
        workflow_run_id=workflow_run_id,
        model=counting_model,
        model_deployment=deployment,
        prompt_version=prompt_version,
    )
    if len(determinations) != len(package.lines):
        raise ValueError(
            "Kimley-Horn validation did not return one result per line"
        )
    if counting_model.call_count != 1:
        raise ValueError(
            "Kimley-Horn validation must use exactly one model call"
        )

    tasks_by_code = {task.task_code: task for task in snapshot.tasks}
    issues: List[str] = []
    for line, result in zip(package.lines[:-1], determinations[:-1]):
        if result.resolution_status != TaskResolutionStatus.RESOLVED:
            issues.append(
                f"Line {line.line_number} must resolve to governed "
                f"environmental task {EXPECTED_ENVIRONMENTAL_TASK_CODE}; "
                f"received {result.resolution_status.value}."
            )
            continue
        if result.suggested_task_code != EXPECTED_ENVIRONMENTAL_TASK_CODE:
            task = tasks_by_code[result.suggested_task_code or ""]
            issues.append(
                f"Line {line.line_number} resolved to {task.task_code} "
                f"({task.description}) instead of governed environmental "
                f"task {EXPECTED_ENVIRONMENTAL_TASK_CODE}."
            )

    expense_line = package.lines[-1]
    expense_result = determinations[-1]
    if "reimbursable" not in expense_line.description.lower():
        issues.append("Expected line 10 to be the reimbursable-expense line.")
    if expense_result.resolution_status != TaskResolutionStatus.REVIEW:
        issues.append(
            "Reimbursable expenses must remain REVIEW because no approved "
            "allocation rule maps them to a governed project task."
        )
    if expense_result.model_deployment is not None:
        issues.append(
            "Reimbursable expenses must be reviewed deterministically "
            "without being sent to GPT-5.1."
        )

    resolved_count = sum(
        result.resolution_status == TaskResolutionStatus.RESOLVED
        for result in determinations
    )
    review_count = len(determinations) - resolved_count
    return {
        "schemaVersion": "2.0-kimley-horn-task-validation",
        "validationStatus": "PASSED" if not issues else "REVIEW_REQUIRED",
        "workflowRunId": workflow_run_id,
        "packageId": package.package_id,
        "invoiceId": package.invoice_id,
        "invoiceNumber": package.invoice_number,
        "sourceSha256": package.source.sha256,
        "taskMasterVersion": snapshot.source.source_version,
        "modelDeployment": deployment,
        "promptVersion": prompt_version,
        "modelCallCount": counting_model.call_count,
        "lineCount": len(determinations),
        "resolvedCount": resolved_count,
        "reviewCount": review_count,
        "validationIssues": issues,
        "taskDeterminations": [
            result.model_dump(mode="json", by_alias=True)
            for result in determinations
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the approved Kimley-Horn package through the Demo V2 "
            "LangChain/GPT-5.1 task stage."
        )
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("AZURE_OPENAI_ENDPOINT"),
        help=(
            "Foundry OpenAI v1 Responses endpoint. Defaults to "
            "AZURE_OPENAI_ENDPOINT."
        ),
    )
    parser.add_argument(
        "--deployment",
        default=os.environ.get(
            "AZURE_OPENAI_DEPLOYMENT",
            DEFAULT_MODEL_DEPLOYMENT,
        ),
    )
    parser.add_argument(
        "--package",
        type=Path,
        default=DEFAULT_PACKAGE_PATH,
    )
    parser.add_argument(
        "--task-master",
        type=Path,
        default=DEFAULT_TASK_MASTER_PATH,
    )
    parser.add_argument(
        "--workflow-run-id",
        default="cict-235-kimley-horn-validation",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if not args.endpoint:
        parser.error(
            "--endpoint or AZURE_OPENAI_ENDPOINT is required"
        )

    package = load_kimley_horn_package(args.package)
    snapshot = load_task_master_snapshot(args.task_master)
    model = build_azure_task_suggestion_model(
        deployment=args.deployment,
        endpoint=args.endpoint,
        managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID"),
    )
    try:
        report = validate_kimley_horn_task_suggestions(
            package,
            snapshot,
            model,
            workflow_run_id=args.workflow_run_id,
            deployment=args.deployment,
        )
    except ModelError as exc:
        print(
            f"Live Kimley-Horn validation failed: {exc}",
            file=sys.stderr,
        )
        return 1
    payload = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(args.output)
    else:
        print(payload, end="")
    return 0 if report["validationStatus"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
