"""Validate deterministic bundle-candidate evaluation for Demo V2."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

from autarch.capex.demo_v2_bundling import evaluate_bundle_candidates
from autarch.capex.demo_v2_contracts import (
    BundlingDisposition,
    CapitalizationAssessment,
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
REFERENCE_DIR = ROOT / "examples" / "data" / "capex" / "reference-v2"
DEFAULT_POLICY_PATH = REFERENCE_DIR / "policy-config-v2.json"
DEFAULT_CURRENT_ASSESSMENTS_PATH = (
    RESULTS_DIR / "capitalization.assessments.json"
)
DEFAULT_SYNTHETIC_ASSESSMENTS_PATH = (
    RESULTS_DIR / "capitalization.synthetic-assessments.json"
)
DEFAULT_SYNTHETIC_REGISTER_PATH = (
    REFERENCE_DIR / "synthetic-input-register-v2.json"
)


def load_demo_packages() -> List[Tuple[str, NormalizedInvoicePackage]]:
    """Load source-faithful packages used by the current demo targets."""
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
    return [
        ("bruegmann", bruegmann),
        ("kimley-horn", kimley_horn),
        ("jc2000a07814", jc_result.packages[0]),
    ]


def load_assessment_report(
    path: Path,
) -> Tuple[Dict[str, object], List[CapitalizationAssessment]]:
    """Load one CICT-237 report and flatten its package assessments."""
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    packages = report.get("packages")
    if not isinstance(packages, list):
        raise ValueError("Assessment report must contain packages")
    assessments: List[CapitalizationAssessment] = []
    for package in packages:
        if not isinstance(package, dict):
            raise ValueError("Assessment package entries must be objects")
        values = package.get("assessments")
        if not isinstance(values, list):
            raise ValueError(
                "Assessment package entries must contain assessments"
            )
        assessments.extend(
            CapitalizationAssessment.model_validate(value)
            for value in values
        )
    return report, assessments


def apply_synthetic_line_values(
    packages: Sequence[Tuple[str, NormalizedInvoicePackage]],
    register: Mapping[str, object],
) -> List[Tuple[str, NormalizedInvoicePackage]]:
    """Apply registered demo-only line values without changing source files."""
    inputs = register.get("syntheticInputs")
    if not isinstance(inputs, list):
        raise ValueError("Synthetic register must contain syntheticInputs")
    data_override = next(
        (
            value
            for value in inputs
            if isinstance(value, dict)
            and value.get("assumptionId") == "SYN-DATA-001"
        ),
        None,
    )
    if data_override is None:
        return list(packages)
    affected = data_override.get("affectedRecords")
    if not isinstance(affected, list):
        raise ValueError("SYN-DATA-001 requires affectedRecords")
    affected_ids = {str(value) for value in affected}
    result: List[Tuple[str, NormalizedInvoicePackage]] = []
    for name, package in packages:
        lines = [
            line.model_copy(
                update={
                    "quantity": 1.0,
                    "unit_cost": line.line_amount,
                }
            )
            if line.line_id in affected_ids
            else line
            for line in package.lines
        ]
        result.append((name, package.model_copy(update={"lines": lines})))
    return result


def validate_demo_bundling(
    named_packages: Sequence[Tuple[str, NormalizedInvoicePackage]],
    assessments: Sequence[CapitalizationAssessment],
    assessment_report: Mapping[str, object],
    policy: DemoPolicySnapshot,
    *,
    workflow_run_id: str,
    synthetic: bool,
) -> Dict[str, object]:
    """Evaluate bundles and validate current-policy or synthetic expectations."""
    packages = [package for _, package in named_packages]
    evaluation = evaluate_bundle_candidates(
        packages,
        assessments,
        policy,
        workflow_run_id=workflow_run_id,
    )
    issues: List[str] = []
    if any(assessment.used_llm for assessment in assessments):
        issues.append("Bundling input contains a model-assisted assessment.")
    member_keys = {
        (member.package_id, member.line_id)
        for group in evaluation.groups
        for member in group.members
    }
    grouped_assessment_keys = {
        (assessment.package_id, assessment.line_id)
        for assessment in evaluation.assessments
        if assessment.bundling_disposition == BundlingDisposition.GROUPED
    }
    if member_keys != grouped_assessment_keys:
        issues.append(
            "Grouped assessment references do not match bundle members."
        )
    for group in evaluation.groups:
        member_total = sum(member.line_amount for member in group.members)
        if abs(member_total - group.total_amount) > 0.000001:
            issues.append(
                f"{group.bundle_group_id} does not reconcile to its members."
            )

    expected = (
        {
            "eligibleMemberCount": 11,
            "groupedMemberCount": 9,
            "ungroupedCandidateCount": 2,
            "groupCount": 1,
        }
        if synthetic
        else {
            "eligibleMemberCount": 0,
            "groupedMemberCount": 0,
            "ungroupedCandidateCount": 0,
            "groupCount": 0,
        }
    )
    actual = {
        "eligibleMemberCount": evaluation.eligible_member_count,
        "groupedMemberCount": evaluation.grouped_member_count,
        "ungroupedCandidateCount": (
            evaluation.ungrouped_candidate_count
        ),
        "groupCount": len(evaluation.groups),
    }
    if actual != expected:
        issues.append(
            f"Bundling counts changed: expected {expected}, received {actual}."
        )
    if synthetic:
        group_summary = sorted(
            (len(group.members), group.total_amount)
            for group in evaluation.groups
        )
        if group_summary != [(9, 455.56)]:
            issues.append(
                "Synthetic group membership or totals changed: "
                f"received {group_summary}."
            )

    source_workflow_run_id = assessment_report.get("workflowRunId")
    synthetic_questions = (
        assessment_report.get("openCircleKQuestions", [])
        if synthetic
        else []
    )
    synthetic_refs = (
        sorted(
            str(question["assumptionId"])
            for question in synthetic_questions
            if isinstance(question, dict)
            and question.get("assumptionId")
        )
        if isinstance(synthetic_questions, list)
        else []
    )
    return {
        "schemaVersion": "2.0-bundling-validation",
        "validationStatus": "PASSED" if not issues else "REVIEW_REQUIRED",
        "workflowRunId": workflow_run_id,
        "sourceAssessmentWorkflowId": source_workflow_run_id,
        "policyId": policy.policy_id,
        "policyApprovalStatus": policy.approval_status.value,
        "syntheticDataUsed": synthetic,
        "demoOnly": synthetic,
        "circleKApproved": False,
        "sourceArtifactsModified": False,
        "syntheticRegisterId": (
            assessment_report.get("syntheticRegisterId")
            if synthetic
            else None
        ),
        "syntheticInputRefs": synthetic_refs,
        "openCircleKQuestions": synthetic_questions,
        "currentRunOnly": True,
        "historicalLookbackIncluded": False,
        "usedLlm": False,
        "modelCallCount": 0,
        **actual,
        "thresholdReachedGroupCount": sum(
            group.threshold_amount is not None
            and group.total_amount >= group.threshold_amount
            for group in evaluation.groups
        ),
        "validationIssues": issues,
        "bundleGroups": [
            group.model_dump(mode="json", by_alias=True)
            for group in evaluation.groups
        ],
        "assessments": [
            assessment.model_dump(mode="json", by_alias=True)
            for assessment in evaluation.assessments
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate deterministic current-run bundle candidates for "
            "CapEx Demo V2."
        )
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY_PATH,
    )
    parser.add_argument(
        "--assessment-report",
        type=Path,
    )
    parser.add_argument(
        "--synthetic-register",
        type=Path,
        default=DEFAULT_SYNTHETIC_REGISTER_PATH,
    )
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument(
        "--workflow-run-id",
        default="cict-238-bundling-validation",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    assessment_path = args.assessment_report or (
        DEFAULT_SYNTHETIC_ASSESSMENTS_PATH
        if args.synthetic
        else DEFAULT_CURRENT_ASSESSMENTS_PATH
    )
    assessment_report, assessments = load_assessment_report(
        assessment_path
    )
    packages = load_demo_packages()
    if args.synthetic:
        register = json.loads(
            args.synthetic_register.read_text(encoding="utf-8")
        )
        packages = apply_synthetic_line_values(packages, register)
    report = validate_demo_bundling(
        packages,
        assessments,
        assessment_report,
        load_demo_policy_snapshot(args.policy),
        workflow_run_id=args.workflow_run_id,
        synthetic=args.synthetic,
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
