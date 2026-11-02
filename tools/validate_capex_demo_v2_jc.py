"""Validate governed JC task review behavior through the Demo V2 path."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

from autarch.capex.demo_v2_contracts import TaskResolutionStatus
from autarch.capex.demo_v2_normalization import (
    JCWorkbookNormalizationResult,
    NormalizedInvoicePackage,
)
from autarch.capex.demo_v2_reference import (
    DemoPolicySnapshot,
    TaskMasterSnapshot,
    load_demo_policy_snapshot,
    load_task_master_snapshot,
)
from autarch.capex.demo_v2_task_suggestion import (
    DEFAULT_TASK_PROMPT_VERSION,
    ModelTaskSuggestionBatch,
    suggest_tasks_for_invoice,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET_PATH = (
    ROOT
    / "examples"
    / "data"
    / "capex"
    / "normalized-v2"
    / "jc2000a07814.normalized.json"
)
DEFAULT_COMPANION_PATH = (
    ROOT
    / "examples"
    / "data"
    / "capex"
    / "normalized-v2"
    / "jc2000a07465.classification.json"
)
DEFAULT_TASK_MASTER_PATH = (
    ROOT
    / "examples"
    / "data"
    / "capex"
    / "reference-v2"
    / "task-master-us-v2.json"
)
DEFAULT_POLICY_PATH = (
    ROOT
    / "examples"
    / "data"
    / "capex"
    / "reference-v2"
    / "policy-config-v2.json"
)
EXPECTED_TARGET_SOURCE_SHA256 = (
    "bdb5a904f1d661a04ca24db2f365c9de4c06916579b0f250a519151a22227510"
)
EXPECTED_COMPANION_SOURCE_SHA256 = (
    "6cdb3b8cc42e0a252e4f2716feb4cfa94de7037f537d0b3ca4585092f7ae2e42"
)
EXPECTED_TARGET_PROJECT = "JC2000A07814"
EXPECTED_COMPANION_PROJECT = "JC2000A07465"
EXPECTED_TARGET_TASK_COUNTS = {"0303": 1}
EXPECTED_COMPANION_TASK_COUNTS = {
    "0601": 3,
    "0604": 2,
    "1201": 1,
    "3307": 1,
}


class _RejectingModel:
    def __init__(self) -> None:
        self.call_count = 0

    def suggest(
        self,
        request: Mapping[str, object],
    ) -> ModelTaskSuggestionBatch:
        del request
        self.call_count += 1
        raise AssertionError(
            "JC lines with ungoverned source task codes must not call GPT-5.1"
        )


def load_jc_result(
    path: Path,
    *,
    expected_project_id: str,
    expected_source_sha256: str,
) -> JCWorkbookNormalizationResult:
    """Load and verify one committed JC normalization artifact."""
    result = JCWorkbookNormalizationResult.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )
    if result.project_id != expected_project_id:
        raise ValueError(
            f"Expected JC project {expected_project_id}, "
            f"received {result.project_id}"
        )
    if result.source.sha256 != expected_source_sha256:
        raise ValueError(
            f"{expected_project_id} source SHA-256 does not match the "
            "approved demo input"
        )
    return result


def _validate_packages(
    packages: Sequence[NormalizedInvoicePackage],
    snapshot: TaskMasterSnapshot,
    policy: DemoPolicySnapshot,
    *,
    workflow_run_id: str,
    issues: List[str],
) -> tuple[List[Dict[str, object]], int]:
    governed_codes = {task.task_code for task in snapshot.tasks}
    configured_codes = set(
        policy.jc_reference_handling.source_task_codes
    )
    model = _RejectingModel()
    package_reports: List[Dict[str, object]] = []
    for package in packages:
        determinations = suggest_tasks_for_invoice(
            package,
            snapshot,
            workflow_run_id=workflow_run_id,
            model=model,
        )
        for line, determination in zip(package.lines, determinations):
            source_code = line.source_task_code
            if source_code not in configured_codes:
                issues.append(
                    f"Line {line.line_id} uses JC source task code "
                    f"{source_code!r}, which is absent from the governed "
                    "JC review configuration."
                )
            if source_code in governed_codes:
                issues.append(
                    f"JC source task code {source_code} is now present in "
                    "TaskMaster; an approved mapping review is required."
                )
            if (
                determination.resolution_status
                != TaskResolutionStatus.REVIEW
            ):
                issues.append(
                    f"Line {line.line_id} must remain REVIEW without an "
                    "approved European TaskMaster mapping."
                )
            if determination.suggested_task_code is not None:
                issues.append(
                    f"Line {line.line_id} received unexpected governed task "
                    f"{determination.suggested_task_code}."
                )
            if determination.model_deployment is not None:
                issues.append(
                    f"Line {line.line_id} was unexpectedly sent to a model."
                )
        package_reports.append(
            {
                "packageId": package.package_id,
                "invoiceId": package.invoice_id,
                "invoiceNumber": package.invoice_number,
                "currency": package.currency,
                "sourceTaskCodes": sorted(
                    {
                        line.source_task_code
                        for line in package.lines
                        if line.source_task_code
                    }
                ),
                "taskDeterminations": [
                    determination.model_dump(
                        mode="json",
                        by_alias=True,
                    )
                    for determination in determinations
                ],
            }
        )
    return package_reports, model.call_count


def _validate_excluded_records(
    result: JCWorkbookNormalizationResult,
    policy: DemoPolicySnapshot,
    *,
    expected_type: str,
    expected_count: int,
    issues: List[str],
) -> None:
    excluded = result.excluded_records
    actual_types = Counter(record.transaction_type for record in excluded)
    if len(excluded) != expected_count:
        issues.append(
            f"{result.project_id} must retain exactly {expected_count} "
            f"excluded ledger row(s); received {len(excluded)}."
        )
    if set(actual_types) != {expected_type}:
        issues.append(
            f"{result.project_id} excluded transaction types must be "
            f"{expected_type}; received {sorted(actual_types)}."
        )
    configured_types = (
        policy.jc_reference_handling.excluded_transaction_types
    )
    if expected_type not in configured_types:
        issues.append(
            f"Excluded transaction type {expected_type} is absent from "
            "the governed JC review configuration."
        )
    included_types = {
        line.source_transaction_type
        for package in result.packages
        for line in package.lines
    }
    if included_types != {"AI"}:
        issues.append(
            f"{result.project_id} included transaction types must be AI; "
            f"received {sorted(str(value) for value in included_types)}."
        )


def _validate_project_shape(
    result: JCWorkbookNormalizationResult,
    *,
    expected_package_count: int,
    expected_currencies: Sequence[str],
    expected_task_counts: Mapping[str, int],
    issues: List[str],
) -> None:
    if len(result.packages) != expected_package_count:
        issues.append(
            f"{result.project_id} must contain {expected_package_count} "
            f"included package(s); received {len(result.packages)}."
        )
    if result.source_currencies != list(expected_currencies):
        issues.append(
            f"{result.project_id} source currencies must be "
            f"{list(expected_currencies)}; received "
            f"{result.source_currencies}."
        )
    actual_task_counts = Counter(
        line.source_task_code
        for package in result.packages
        for line in package.lines
    )
    if dict(actual_task_counts) != dict(expected_task_counts):
        issues.append(
            f"{result.project_id} source task counts must be "
            f"{dict(expected_task_counts)}; received "
            f"{dict(actual_task_counts)}."
        )


def validate_jc_task_reviews(
    target: JCWorkbookNormalizationResult,
    companion: JCWorkbookNormalizationResult,
    snapshot: TaskMasterSnapshot,
    policy: DemoPolicySnapshot,
    *,
    workflow_run_id: str,
    prompt_version: str = DEFAULT_TASK_PROMPT_VERSION,
) -> Dict[str, object]:
    """Validate target and companion JC rows as governed review evidence."""
    issues: List[str] = []
    handling = policy.jc_reference_handling
    if handling.governed_mapping_available:
        issues.append(
            "The policy unexpectedly claims a governed JC mapping is "
            "available."
        )
    if handling.result_without_mapping.value != "REVIEW":
        issues.append(
            "The policy must require REVIEW when no governed mapping exists."
        )
    if handling.included_transaction_types != ["AI"]:
        issues.append(
            "The policy must include only AI transactions for JC packages."
        )

    _validate_project_shape(
        target,
        expected_package_count=1,
        expected_currencies=["DKK", "EUR"],
        expected_task_counts=EXPECTED_TARGET_TASK_COUNTS,
        issues=issues,
    )
    _validate_project_shape(
        companion,
        expected_package_count=7,
        expected_currencies=["DKK"],
        expected_task_counts=EXPECTED_COMPANION_TASK_COUNTS,
        issues=issues,
    )
    _validate_excluded_records(
        target,
        policy,
        expected_type="OV",
        expected_count=1,
        issues=issues,
    )
    _validate_excluded_records(
        companion,
        policy,
        expected_type="JP",
        expected_count=6,
        issues=issues,
    )

    target_reports, target_calls = _validate_packages(
        target.packages,
        snapshot,
        policy,
        workflow_run_id=workflow_run_id,
        issues=issues,
    )
    companion_reports, companion_calls = _validate_packages(
        companion.packages,
        snapshot,
        policy,
        workflow_run_id=workflow_run_id,
        issues=issues,
    )
    model_call_count = target_calls + companion_calls
    if model_call_count:
        issues.append(
            f"JC review validation made {model_call_count} unexpected "
            "model call(s)."
        )

    package_count = len(target.packages) + len(companion.packages)
    line_count = sum(
        len(package.lines)
        for package in list(target.packages) + list(companion.packages)
    )
    excluded_counts = Counter(
        record.transaction_type
        for record in (
            list(target.excluded_records)
            + list(companion.excluded_records)
        )
    )
    return {
        "schemaVersion": "2.0-jc-task-validation",
        "validationStatus": "PASSED" if not issues else "REVIEW_REQUIRED",
        "workflowRunId": workflow_run_id,
        "taskMasterVersion": snapshot.source.source_version,
        "policyId": policy.policy_id,
        "promptVersion": prompt_version,
        "modelCallCount": model_call_count,
        "projectCount": 2,
        "packageCount": package_count,
        "lineCount": line_count,
        "reviewCount": line_count,
        "resolvedCount": 0,
        "excludedRecordCount": sum(excluded_counts.values()),
        "excludedTransactionCounts": dict(sorted(excluded_counts.items())),
        "validationIssues": issues,
        "target": {
            "projectId": target.project_id,
            "sourceSha256": target.source.sha256,
            "sourceCurrencies": target.source_currencies,
            "reviewReasons": target.review_reasons,
            "packages": target_reports,
        },
        "companion": {
            "projectId": companion.project_id,
            "sourceSha256": companion.source.sha256,
            "sourceCurrencies": companion.source_currencies,
            "reviewReasons": companion.review_reasons,
            "packages": companion_reports,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the approved JC target and companion accounting "
            "artifacts through the Demo V2 governed task-review path."
        )
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_TARGET_PATH,
    )
    parser.add_argument(
        "--companion",
        type=Path,
        default=DEFAULT_COMPANION_PATH,
    )
    parser.add_argument(
        "--task-master",
        type=Path,
        default=DEFAULT_TASK_MASTER_PATH,
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY_PATH,
    )
    parser.add_argument(
        "--workflow-run-id",
        default="cict-236-jc-task-review-validation",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    target = load_jc_result(
        args.target,
        expected_project_id=EXPECTED_TARGET_PROJECT,
        expected_source_sha256=EXPECTED_TARGET_SOURCE_SHA256,
    )
    companion = load_jc_result(
        args.companion,
        expected_project_id=EXPECTED_COMPANION_PROJECT,
        expected_source_sha256=EXPECTED_COMPANION_SOURCE_SHA256,
    )
    report = validate_jc_task_reviews(
        target,
        companion,
        load_task_master_snapshot(args.task_master),
        load_demo_policy_snapshot(args.policy),
        workflow_run_id=args.workflow_run_id,
    )
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
