from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from autarch.capex.demo_v2_reference import load_demo_policy_snapshot


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "validate_capex_demo_v2_bundling.py"
RESULTS_DIR = ROOT / "examples" / "data" / "capex" / "results-v2"
REFERENCE_DIR = (
    ROOT / "examples" / "data" / "capex" / "reference-v2"
)


def _load_tool() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "validate_capex_demo_v2_bundling",
        TOOL_PATH,
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _run_validation(*, synthetic: bool) -> dict[str, object]:
    tool = _load_tool()
    report_path = RESULTS_DIR / (
        "capitalization.synthetic-assessments.json"
        if synthetic
        else "capitalization.assessments.json"
    )
    report, assessments = tool.load_assessment_report(report_path)
    packages = tool.load_demo_packages()
    if synthetic:
        register = json.loads(
            (
                REFERENCE_DIR / "synthetic-input-register-v2.json"
            ).read_text(encoding="utf-8")
        )
        packages = tool.apply_synthetic_line_values(packages, register)
    return tool.validate_demo_bundling(
        packages,
        assessments,
        report,
        load_demo_policy_snapshot(
            REFERENCE_DIR / "policy-config-v2.json"
        ),
        workflow_run_id="bundle-validation-1",
        synthetic=synthetic,
    )


def test_current_policy_bundling_remains_truthful_review_only() -> None:
    report = _run_validation(synthetic=False)

    assert report["validationStatus"] == "PASSED"
    assert report["syntheticDataUsed"] is False
    assert report["groupCount"] == 0
    assert report["eligibleMemberCount"] == 0
    assert report["modelCallCount"] == 0
    assert report["historicalLookbackIncluded"] is False
    assert report["validationIssues"] == []


def test_synthetic_bundling_is_traceable_and_separate() -> None:
    report = _run_validation(synthetic=True)

    assert report["validationStatus"] == "PASSED"
    assert report["syntheticDataUsed"] is True
    assert report["demoOnly"] is True
    assert report["circleKApproved"] is False
    assert report["sourceArtifactsModified"] is False
    assert report["syntheticRegisterId"] == (
        "capex-demo-v2-synthetic-inputs-2026-11-02"
    )
    register = json.loads(
        (REFERENCE_DIR / "synthetic-input-register-v2.json").read_text(encoding="utf-8")
    )
    expected_assumptions = {item["assumptionId"] for item in register["syntheticInputs"]}
    expected_artifact_assumptions = {
        item["assumptionId"] for item in register["syntheticInputs"]
        if item["category"] != "SQL_PREREQUISITE_FIXTURE"
    }
    assert set(report["syntheticInputRefs"]) == expected_artifact_assumptions
    assert {item["assumptionId"] for item in report["openCircleKQuestions"]} == expected_assumptions
    assert report["eligibleMemberCount"] == 11
    assert report["groupedMemberCount"] == 9
    assert report["ungroupedCandidateCount"] == 2
    assert report["groupCount"] == 1
    assert report["thresholdReachedGroupCount"] == 0
    assert report["modelCallCount"] == 0
    groups = report["bundleGroups"]
    assert isinstance(groups, list)
    assert sorted(
        (len(group["members"]), group["totalAmount"])
        for group in groups
    ) == [(9, 455.56)]
    assert all(
        any(
            citation.startswith("synthetic-context:")
            for citation in group["policyCitations"]
        )
        for group in groups
    )
