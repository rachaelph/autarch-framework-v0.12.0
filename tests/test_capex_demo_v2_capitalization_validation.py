from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from autarch.capex.demo_v2_reference import load_demo_policy_snapshot


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = (
    ROOT / "tools" / "validate_capex_demo_v2_capitalization.py"
)
POLICY_PATH = (
    ROOT
    / "examples"
    / "data"
    / "capex"
    / "reference-v2"
    / "policy-config-v2.json"
)


def _load_tool() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "validate_capex_demo_v2_capitalization",
        TOOL_PATH,
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_current_demo_capitalization_is_truthful_review_output() -> None:
    tool = _load_tool()

    report = tool.validate_current_demo_capitalization(
        tool.load_demo_targets(),
        load_demo_policy_snapshot(POLICY_PATH),
        workflow_run_id="workflow-1",
    )

    assert report["validationStatus"] == "PASSED"
    assert report["usedLlm"] is False
    assert report["modelCallCount"] == 0
    assert report["packageCount"] == 3
    assert report["lineCount"] == 23
    assert report["reviewCount"] == 23
    assert report["capexCount"] == 0
    assert report["opexCount"] == 0
    assert report["thresholdEvaluatedCount"] == 11
    assert report["validationIssues"] == []
