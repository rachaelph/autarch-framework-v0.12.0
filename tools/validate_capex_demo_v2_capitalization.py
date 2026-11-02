"""Validate deterministic capitalization behavior for Demo V2 targets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

from autarch.capex.demo_v2_capitalization import (
    assess_capitalization_for_invoice,
)
from autarch.capex.demo_v2_contracts import (
    CapitalizationAssessment,
    CapitalizationRecommendation,
    NatureResult,
    TaskDetermination,
)
from autarch.capex.demo_v2_normalization import (
    JCWorkbookNormalizationResult,
    NormalizedInvoicePackage,
)
from autarch.capex.demo_v2_reference import (
    DemoPolicySnapshot,
    load_demo_policy_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
NORMALIZED_DIR = (
    ROOT / "examples" / "data" / "capex" / "normalized-v2"
)
RESULTS_DIR = ROOT / "examples" / "data" / "capex" / "results-v2"
DEFAULT_POLICY_PATH = (
    ROOT
    / "examples"
    / "data"
    / "capex"
    / "reference-v2"
    / "policy-config-v2.json"
)


def _load_task_determinations(
    path: Path,
) -> List[TaskDetermination]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        TaskDetermination.model_validate(item)
        for item in payload["taskDeterminations"]
    ]


def load_demo_targets() -> List[
    Tuple[str, NormalizedInvoicePackage, List[TaskDetermination]]
]:
    """Load the three approved target packages and task-stage evidence."""
    bruegmann = NormalizedInvoicePackage.model_validate_json(
        (NORMALIZED_DIR / "bruegmann.normalized.json").read_text(
            encoding="utf-8"
        )
    )
    kimley_horn = NormalizedInvoicePackage.model_validate_json(
        (NORMALIZED_DIR / "kimley-horn.normalized.json").read_text(
            encoding="utf-8"
        )
    )
    jc_result = JCWorkbookNormalizationResult.model_validate_json(
        (NORMALIZED_DIR / "jc2000a07814.normalized.json").read_text(
            encoding="utf-8"
        )
    )
    jc_payload = json.loads(
        (RESULTS_DIR / "jc.task-reviews.json").read_text(
            encoding="utf-8"
        )
    )
    jc_tasks = [
        TaskDetermination.model_validate(item)
        for item in jc_payload["target"]["packages"][0][
            "taskDeterminations"
        ]
    ]
    return [
        (
            "bruegmann",
            bruegmann,
            _load_task_determinations(
                RESULTS_DIR / "bruegmann.task-suggestions.json"
            ),
        ),
        (
            "kimley-horn",
            kimley_horn,
            _load_task_determinations(
                RESULTS_DIR / "kimley-horn.task-suggestions.json"
            ),
        ),
        ("jc2000a07814", jc_result.packages[0], jc_tasks),
    ]


def validate_current_demo_capitalization(
    targets: Sequence[
        Tuple[str, NormalizedInvoicePackage, Sequence[TaskDetermination]]
    ],
    policy: DemoPolicySnapshot,
    *,
    workflow_run_id: str,
) -> Dict[str, object]:
    """Run the current unapproved snapshot and validate honest review output."""
    issues: List[str] = []
    package_reports: List[Dict[str, object]] = []
    all_assessments: List[CapitalizationAssessment] = []
    for name, package, determinations in targets:
        assessments = assess_capitalization_for_invoice(
            package,
            determinations,
            policy,
            workflow_run_id=workflow_run_id,
        )
        if len(assessments) != len(package.lines):
            issues.append(
                f"{name} did not produce one capitalization assessment "
                "per line."
            )
        for assessment in assessments:
            if (
                assessment.recommendation
                != CapitalizationRecommendation.REVIEW
            ):
                issues.append(
                    f"{assessment.line_id} received "
                    f"{assessment.recommendation.value} while the current "
                    "nature-rule library remains unapproved."
                )
            if assessment.nature_result != NatureResult.REVIEW:
                issues.append(
                    f"{assessment.line_id} received an approved nature "
                    "result that is unavailable in the current policy."
                )
            if assessment.used_llm:
                issues.append(
                    f"{assessment.line_id} used a model during deterministic "
                    "capitalization."
                )
            if assessment.policy_version != policy.policy_id:
                issues.append(
                    f"{assessment.line_id} did not retain the policy version."
                )
        all_assessments.extend(assessments)
        package_reports.append(
            {
                "name": name,
                "packageId": package.package_id,
                "invoiceId": package.invoice_id,
                "invoiceNumber": package.invoice_number,
                "assessmentCount": len(assessments),
                "assessments": [
                    assessment.model_dump(mode="json", by_alias=True)
                    for assessment in assessments
                ],
            }
        )

    threshold_evaluated_count = sum(
        assessment.unit_cost_meets_threshold is not None
        for assessment in all_assessments
    )
    return {
        "schemaVersion": "2.0-capitalization-validation",
        "validationStatus": "PASSED" if not issues else "REVIEW_REQUIRED",
        "workflowRunId": workflow_run_id,
        "policyId": policy.policy_id,
        "policyApprovalStatus": policy.approval_status.value,
        "usedLlm": False,
        "modelCallCount": 0,
        "packageCount": len(targets),
        "lineCount": len(all_assessments),
        "reviewCount": sum(
            assessment.recommendation
            == CapitalizationRecommendation.REVIEW
            for assessment in all_assessments
        ),
        "capexCount": sum(
            assessment.recommendation
            == CapitalizationRecommendation.CAPEX
            for assessment in all_assessments
        ),
        "opexCount": sum(
            assessment.recommendation
            == CapitalizationRecommendation.OPEX
            for assessment in all_assessments
        ),
        "thresholdEvaluatedCount": threshold_evaluated_count,
        "validationIssues": issues,
        "packages": package_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate deterministic capitalization for the three approved "
            "Demo V2 target packages."
        )
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY_PATH,
    )
    parser.add_argument(
        "--workflow-run-id",
        default="cict-237-capitalization-validation",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = validate_current_demo_capitalization(
        load_demo_targets(),
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
