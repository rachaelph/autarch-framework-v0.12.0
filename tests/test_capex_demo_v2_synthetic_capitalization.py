from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from autarch.capex.demo_v2_capitalization import CapitalizationRuleSet
from autarch.capex.demo_v2_reference import load_demo_policy_snapshot


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = (
    ROOT
    / "tools"
    / "validate_capex_demo_v2_synthetic_capitalization.py"
)
REFERENCE_DIR = (
    ROOT / "examples" / "data" / "capex" / "reference-v2"
)


def _load_tool() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "validate_capex_demo_v2_synthetic_capitalization",
        TOOL_PATH,
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_synthetic_demo_is_labeled_tracked_and_separate() -> None:
    tool = _load_tool()
    register = tool.load_synthetic_register(
        REFERENCE_DIR / "synthetic-input-register-v2.json"
    )
    rule_set = CapitalizationRuleSet.model_validate_json(
        (
            REFERENCE_DIR
            / "synthetic-capitalization-rules-v2.json"
        ).read_text(encoding="utf-8")
    )

    report = tool.validate_synthetic_demo_capitalization(
        tool.load_demo_targets(),
        load_demo_policy_snapshot(
            REFERENCE_DIR / "policy-config-v2.json"
        ),
        rule_set,
        register,
        workflow_run_id="workflow-1",
    )

    assert report["validationStatus"] == "PASSED"
    assert report["syntheticDataUsed"] is True
    assert report["demoOnly"] is True
    assert report["circleKApproved"] is False
    assert report["sourceArtifactsModified"] is False
    assert report["modelCallCount"] == 0
    assert report["lineCount"] == 23
    assert report["capexCount"] == 7
    assert report["opexCount"] == 11
    assert report["reviewCount"] == 5
    assert {item["assumptionId"] for item in report["openCircleKQuestions"]} == {
        item["assumptionId"] for item in register["syntheticInputs"]
    }
    assert report["validationIssues"] == []


def test_every_synthetic_input_has_replacement_question() -> None:
    tool = _load_tool()
    register = tool.load_synthetic_register(
        REFERENCE_DIR / "synthetic-input-register-v2.json"
    )

    assumptions = register["syntheticInputs"]
    assert isinstance(assumptions, list)
    assert all(item["circleKQuestion"] for item in assumptions)
    assert all(item["replacementData"] for item in assumptions)
    assert all(item["status"] == "AWAITING_CIRCLE_K" for item in assumptions)
