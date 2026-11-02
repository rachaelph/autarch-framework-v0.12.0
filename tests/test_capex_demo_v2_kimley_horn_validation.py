from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Mapping

from autarch.capex.demo_v2_reference import load_task_master_snapshot
from autarch.capex.demo_v2_task_suggestion import (
    ModelTaskSuggestion,
    ModelTaskSuggestionBatch,
)


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "validate_capex_demo_v2_kimley_horn.py"
REFERENCE_PATH = (
    ROOT
    / "examples"
    / "data"
    / "capex"
    / "reference-v2"
    / "task-master-us-v2.json"
)
PACKAGE_PATH = (
    ROOT
    / "examples"
    / "data"
    / "capex"
    / "normalized-v2"
    / "kimley-horn.normalized.json"
)


def _load_tool() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "validate_capex_demo_v2_kimley_horn",
        TOOL_PATH,
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class KimleyHornModel:
    def __init__(
        self,
        *,
        unrelated_task_code: str = "",
    ):
        self.unrelated_task_code = unrelated_task_code

    def suggest(
        self,
        request: Mapping[str, object],
    ) -> ModelTaskSuggestionBatch:
        request_lines = request["lines"]
        assert isinstance(request_lines, list)
        assert len(request_lines) == 9
        outputs = []
        for line in request_lines:
            assert isinstance(line, dict)
            line_id = line["lineId"]
            candidates = line["candidates"]
            assert isinstance(line_id, str)
            assert isinstance(candidates, list)
            candidate_codes = {
                candidate["taskCode"] for candidate in candidates
            }
            task_code = "0007"
            if (
                self.unrelated_task_code
                and self.unrelated_task_code in candidate_codes
            ):
                task_code = self.unrelated_task_code
                self.unrelated_task_code = ""
            assert task_code in candidate_codes
            outputs.append(
                ModelTaskSuggestion(
                    line_id=line_id,
                    resolution_status="RESOLVED",
                    task_code=task_code,
                    confidence=0.9,
                    item_identified="Environmental professional service",
                    rationale=(
                        "The service matches the governed environmental task."
                    ),
                    evidence_refs=[
                        f"line:{line_id}",
                        f"task:{task_code}",
                    ],
                )
            )
        return ModelTaskSuggestionBatch(lines=outputs)


def test_kimley_horn_validation_passes_environmental_results() -> None:
    tool = _load_tool()
    package = tool.load_kimley_horn_package(PACKAGE_PATH)
    snapshot = load_task_master_snapshot(REFERENCE_PATH)

    report = tool.validate_kimley_horn_task_suggestions(
        package,
        snapshot,
        KimleyHornModel(),
        workflow_run_id="workflow-1",
    )

    assert report["validationStatus"] == "PASSED"
    assert report["modelCallCount"] == 1
    assert report["lineCount"] == 10
    assert report["resolvedCount"] == 9
    assert report["reviewCount"] == 1
    assert report["validationIssues"] == []


def test_kimley_horn_validation_flags_unrelated_resolved_task() -> None:
    tool = _load_tool()
    package = tool.load_kimley_horn_package(PACKAGE_PATH)
    snapshot = load_task_master_snapshot(REFERENCE_PATH)

    report = tool.validate_kimley_horn_task_suggestions(
        package,
        snapshot,
        KimleyHornModel(unrelated_task_code="6015"),
        workflow_run_id="workflow-1",
    )

    assert report["validationStatus"] == "REVIEW_REQUIRED"
    assert report["validationIssues"]
    assert "AIR/WATER" in report["validationIssues"][0]
