"""Validate Bruegmann task suggestions through the Demo V2 path."""
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
    / "bruegmann.normalized.json"
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
    "974d61a406edf85e3cadfa75a5177795481d3a82ce962dc414e9d276b885ee5b"
)
EXPECTED_LINE_COUNT = 12


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


def load_bruegmann_package(path: Path) -> NormalizedInvoicePackage:
    """Load and verify the committed Bruegmann normalized package."""
    package = NormalizedInvoicePackage.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )
    if package.source.sha256 != EXPECTED_SOURCE_SHA256:
        raise ValueError(
            "Bruegmann source SHA-256 does not match the approved demo input"
        )
    if len(package.lines) != EXPECTED_LINE_COUNT:
        raise ValueError(
            "Bruegmann package must contain exactly 12 invoice lines"
        )
    return package


def validate_bruegmann_task_suggestions(
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
            "Bruegmann validation did not return one result per line"
        )
    if counting_model.call_count != 1:
        raise ValueError(
            "Bruegmann validation must use exactly one model call"
        )

    tasks_by_code = {task.task_code: task for task in snapshot.tasks}
    issues: List[str] = []
    shipping_line = package.lines[-1]
    shipping_result = determinations[-1]
    if shipping_line.description.strip().lower() != "shipping":
        issues.append("Expected line 12 to be the shipping line.")
    if shipping_result.resolution_status != TaskResolutionStatus.REVIEW:
        issues.append(
            "Shipping must remain REVIEW because the governed TaskMaster "
            "contains no shipping or freight task."
        )

    for line, result in zip(package.lines[:-1], determinations[:-1]):
        if result.resolution_status != TaskResolutionStatus.RESOLVED:
            continue
        task = tasks_by_code[result.suggested_task_code or ""]
        task_context = " ".join(
            (
                task.description,
                task.asset_category_major,
                task.asset_category_minor,
            )
        ).lower()
        if "shelv" not in task_context:
            issues.append(
                f"Line {line.line_number} resolved to "
                f"{task.task_code} ({task.description}), which is not a "
                "shelving task and requires business review."
            )

    resolved_count = sum(
        result.resolution_status == TaskResolutionStatus.RESOLVED
        for result in determinations
    )
    review_count = len(determinations) - resolved_count
    return {
        "schemaVersion": "2.0-bruegmann-task-validation",
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
            "Run the approved Bruegmann package through the Demo V2 "
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
        default="cict-234-bruegmann-validation",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if not args.endpoint:
        parser.error(
            "--endpoint or AZURE_OPENAI_ENDPOINT is required"
        )

    package = load_bruegmann_package(args.package)
    snapshot = load_task_master_snapshot(args.task_master)
    model = build_azure_task_suggestion_model(
        deployment=args.deployment,
        endpoint=args.endpoint,
        managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID"),
    )
    try:
        report = validate_bruegmann_task_suggestions(
            package,
            snapshot,
            model,
            workflow_run_id=args.workflow_run_id,
            deployment=args.deployment,
        )
    except ModelError as exc:
        print(
            f"Live Bruegmann validation failed: {exc}",
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
