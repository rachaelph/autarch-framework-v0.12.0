from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from autarch.capex.demo_v2_reference import (
    load_demo_policy_snapshot,
    load_task_master_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "validate_capex_demo_v2_jc.py"
NORMALIZED_DIR = (
    ROOT / "examples" / "data" / "capex" / "normalized-v2"
)
REFERENCE_DIR = (
    ROOT / "examples" / "data" / "capex" / "reference-v2"
)


def _load_tool() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "validate_capex_demo_v2_jc",
        TOOL_PATH,
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _inputs(module: ModuleType) -> tuple:
    target = module.load_jc_result(
        NORMALIZED_DIR / "jc2000a07814.normalized.json",
        expected_project_id=module.EXPECTED_TARGET_PROJECT,
        expected_source_sha256=module.EXPECTED_TARGET_SOURCE_SHA256,
    )
    companion = module.load_jc_result(
        NORMALIZED_DIR / "jc2000a07465.classification.json",
        expected_project_id=module.EXPECTED_COMPANION_PROJECT,
        expected_source_sha256=module.EXPECTED_COMPANION_SOURCE_SHA256,
    )
    snapshot = load_task_master_snapshot(
        REFERENCE_DIR / "task-master-us-v2.json"
    )
    policy = load_demo_policy_snapshot(
        REFERENCE_DIR / "policy-config-v2.json"
    )
    return target, companion, snapshot, policy


def test_jc_validation_passes_governed_review_results() -> None:
    tool = _load_tool()
    target, companion, snapshot, policy = _inputs(tool)

    report = tool.validate_jc_task_reviews(
        target,
        companion,
        snapshot,
        policy,
        workflow_run_id="workflow-1",
    )

    assert report["validationStatus"] == "PASSED"
    assert report["modelCallCount"] == 0
    assert report["projectCount"] == 2
    assert report["packageCount"] == 8
    assert report["lineCount"] == 8
    assert report["reviewCount"] == 8
    assert report["resolvedCount"] == 0
    assert report["excludedRecordCount"] == 7
    assert report["excludedTransactionCounts"] == {"JP": 6, "OV": 1}
    assert report["validationIssues"] == []


def test_jc_validation_flags_newly_governed_source_code() -> None:
    tool = _load_tool()
    target, companion, snapshot, policy = _inputs(tool)
    mapped_task = snapshot.tasks[0].model_copy(
        update={
            "task_code": "0303",
            "description": "TLG SYSTEM",
            "search_text": "0303 tlg system equipment",
        }
    )
    mapped_snapshot = snapshot.model_copy(
        update={
            "total_source_rows": snapshot.total_source_rows + 1,
            "active_task_count": snapshot.active_task_count + 1,
            "tasks": list(snapshot.tasks) + [mapped_task],
        }
    )

    report = tool.validate_jc_task_reviews(
        target,
        companion,
        mapped_snapshot,
        policy,
        workflow_run_id="workflow-1",
    )

    assert report["validationStatus"] == "REVIEW_REQUIRED"
    assert any(
        "now present in TaskMaster" in issue
        for issue in report["validationIssues"]
    )
    assert any(
        "must remain REVIEW" in issue
        for issue in report["validationIssues"]
    )
