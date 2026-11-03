"""Generate explicitly synthetic capitalization results for Demo V2."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

from autarch.capex.demo_v2_capitalization import (
    CapitalizationRuleSet,
    assess_capitalization_for_invoice,
)
from autarch.capex.demo_v2_contracts import (
    CapitalizationAssessment,
    CapitalizationRecommendation,
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
from autarch.capex.demo_v2_synthetic import (
    apply_synthetic_line_values as _apply_synthetic_line_values,
    validate_synthetic_register,
)


ROOT = Path(__file__).resolve().parents[1]
NORMALIZED_DIR = (
    ROOT / "examples" / "data" / "capex" / "normalized-v2"
)
RESULTS_DIR = ROOT / "examples" / "data" / "capex" / "results-v2"
REFERENCE_DIR = ROOT / "examples" / "data" / "capex" / "reference-v2"
DEFAULT_POLICY_PATH = REFERENCE_DIR / "policy-config-v2.json"
DEFAULT_RULE_SET_PATH = (
    REFERENCE_DIR / "synthetic-capitalization-rules-v2.json"
)
DEFAULT_REGISTER_PATH = (
    REFERENCE_DIR / "synthetic-input-register-v2.json"
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
    """Load source-faithful packages and prior governed task results."""
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


def load_synthetic_register(path: Path) -> Dict[str, object]:
    """Load and validate the machine-readable assumption register."""
    return validate_synthetic_register(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )


def validate_synthetic_demo_capitalization(
    targets: Sequence[
        Tuple[str, NormalizedInvoicePackage, Sequence[TaskDetermination]]
    ],
    policy: DemoPolicySnapshot,
    rule_set: CapitalizationRuleSet,
    register: Mapping[str, object],
    *,
    workflow_run_id: str,
) -> Dict[str, object]:
    """Run an opt-in synthetic demo while retaining replacement evidence."""
    if not rule_set.synthetic or rule_set.approved:
        raise ValueError(
            "Synthetic demo validation requires an unapproved synthetic "
            "rule set"
        )
    setting_ids = register["syntheticSettingIds"]
    if not isinstance(setting_ids, list):
        raise ValueError("syntheticSettingIds must be a list")
    issues: List[str] = []
    package_reports: List[Dict[str, object]] = []
    all_assessments: List[CapitalizationAssessment] = []
    for name, source_package, determinations in targets:
        package, input_refs = _apply_synthetic_line_values(
            source_package,
            register,
        )
        if package.context_overlay is not None:
            input_refs.append("SYN-CTX-001")
        assessments = assess_capitalization_for_invoice(
            package,
            determinations,
            policy,
            workflow_run_id=workflow_run_id,
            rule_set=rule_set,
            allow_synthetic=True,
            synthetic_setting_ids=[
                str(value) for value in setting_ids
            ],
        )
        if any(assessment.used_llm for assessment in assessments):
            issues.append(
                f"{name} used a model during synthetic capitalization."
            )
        all_assessments.extend(assessments)
        package_reports.append(
            {
                "name": name,
                "packageId": package.package_id,
                "invoiceId": package.invoice_id,
                "invoiceNumber": package.invoice_number,
                "syntheticInputRefs": sorted(set(input_refs)),
                "assessmentCount": len(assessments),
                "assessments": [
                    assessment.model_dump(mode="json", by_alias=True)
                    for assessment in assessments
                ],
            }
        )

    actual_counts = {
        "lineCount": len(all_assessments),
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
        "reviewCount": sum(
            assessment.recommendation
            == CapitalizationRecommendation.REVIEW
            for assessment in all_assessments
        ),
    }
    expected_counts = register["expectedSyntheticOutcomeCounts"]
    if actual_counts != expected_counts:
        issues.append(
            "Synthetic outcome counts changed: expected "
            f"{expected_counts}, received {actual_counts}."
        )
    inputs = register["syntheticInputs"]
    assert isinstance(inputs, list)
    open_questions = [
        {
            "assumptionId": item["assumptionId"],
            "question": item["circleKQuestion"],
            "replacementData": item["replacementData"],
        }
        for item in inputs
        if isinstance(item, dict)
    ]
    return {
        "schemaVersion": "2.0-synthetic-capitalization-validation",
        "validationStatus": "PASSED" if not issues else "REVIEW_REQUIRED",
        "workflowRunId": workflow_run_id,
        "syntheticDataUsed": True,
        "demoOnly": True,
        "circleKApproved": False,
        "sourceArtifactsModified": False,
        "syntheticRegisterId": register["registerId"],
        "syntheticRuleSetId": rule_set.rule_set_id,
        "basePolicyId": policy.policy_id,
        "usedLlm": False,
        "modelCallCount": 0,
        **actual_counts,
        "validationIssues": issues,
        "openCircleKQuestions": open_questions,
        "packages": package_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate clearly labeled synthetic capitalization outcomes "
            "without modifying source-normalized packages."
        )
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY_PATH,
    )
    parser.add_argument(
        "--rule-set",
        type=Path,
        default=DEFAULT_RULE_SET_PATH,
    )
    parser.add_argument(
        "--register",
        type=Path,
        default=DEFAULT_REGISTER_PATH,
    )
    parser.add_argument(
        "--workflow-run-id",
        default="cict-237-synthetic-capitalization-demo",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = validate_synthetic_demo_capitalization(
        load_demo_targets(),
        load_demo_policy_snapshot(args.policy),
        CapitalizationRuleSet.model_validate_json(
            args.rule_set.read_text(encoding="utf-8")
        ),
        load_synthetic_register(args.register),
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
