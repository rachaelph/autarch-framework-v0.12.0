from __future__ import annotations

import copy
import json

import pytest

from autarch.capex.demo_v2_contracts import DemoWorkflowResult, TaskDetermination
from autarch.capex.demo_v2_potential_tags import screen_potential_tags
from tests.test_capex_demo_v2_adf_job import ReviewModel
from tests.test_capex_demo_v2_task_suggestion import _line, _package


@pytest.fixture
def screened_input():
    package = _package([
        _line("line-1", "Tank removal and disposal"),
        _line("line-2", "Temporary fencing and installation labor"),
    ])
    tasks = [TaskDetermination(
        workflow_run_id="mock-tags", package_id=package.package_id, invoice_id=package.invoice_id,
        line_id=line.line_id, resolution_status="REVIEW", match_method="REVIEW", confidence=0.1,
        unresolved_reason="MOCK TEST ONLY: await classification.", rationale="MOCK TEST ONLY.",
        task_master_sha256="0" * 64, prompt_version="mock", created_at_utc=package.created_at_utc,
    ) for line in package.lines]
    result = DemoWorkflowResult(
        workflow_run_id="mock-tags", status="SUCCEEDED", started_at_utc=package.created_at_utc,
        completed_at_utc=package.created_at_utc, packages=[], task_determinations=tasks, assessments=[],
    )
    response = {"lines": [{
        "packageId": package.package_id, "lineId": "line-1", "potentialTags": [],
    }, {
        "packageId": package.package_id, "lineId": "line-2", "potentialTags": [{
            "tag": "POTENTIAL_ARO", "reason": "MOCK TEST ONLY: fencing associated with tank removal.",
            "evidence": [{"lineId": "line-2", "quote": "Temporary fencing"},
                         {"lineId": "line-1", "quote": "Tank removal and disposal"}],
        }, {
            "tag": "POTENTIAL_BUNDLING", "reason": "MOCK TEST ONLY: associated installation labor.",
            "evidence": [{"lineId": "line-2", "quote": "installation labor"}],
        }],
    }]}
    return package, result, response


class TagModel(ReviewModel):
    def __init__(self, response):
        super().__init__()
        self.response = response

    def complete(self, prompt, system=None):
        return json.dumps(self.response)


def test_potential_tags_allow_multiple_signals_and_same_invoice_context_without_changing_decisions(screened_input):
    package, result, response = screened_input
    tagged = screen_potential_tags([package], result, TagModel(response))
    assert tagged.task_determinations[0].potential_tags == []
    assert len(tagged.task_determinations[1].potential_tags) == 2
    assert all(task.potential_tags is None for task in result.task_determinations)
    assert [task.model_dump(exclude={"potential_tags"}) for task in tagged.task_determinations] == [
        task.model_dump(exclude={"potential_tags"}) for task in result.task_determinations
    ]
    assert tagged.assessments == result.assessments and tagged.bundle_groups == result.bundle_groups


@pytest.mark.parametrize("failure", ["invented_quote", "unknown_line", "other_invoice", "no_own_evidence",
                                    "duplicate_tag", "missing_line", "duplicate_line"])
def test_potential_tags_reject_unverifiable_evidence_and_incomplete_coverage(screened_input, failure):
    package, result, response = screened_input
    response = copy.deepcopy(response)
    tag = response["lines"][1]["potentialTags"][0]
    packages = [package]
    if failure == "invented_quote":
        tag["evidence"][0]["quote"] = "Invented asbestos disposal"
    elif failure in {"unknown_line", "other_invoice"}:
        tag["evidence"][1]["lineId"] = "other-3"
        if failure == "other_invoice":
            other = package.model_copy(update={
                "package_id": "other-invoice", "lines": [_line("other-3", "Tank removal and disposal")],
            })
            packages.append(other)
            result = result.model_copy(update={"task_determinations": [
                *result.task_determinations,
                result.task_determinations[0].model_copy(update={
                    "package_id": other.package_id, "line_id": "other-3",
                }),
            ]})
            response["lines"].append({
                "packageId": other.package_id, "lineId": "other-3", "potentialTags": [],
            })
    elif failure == "no_own_evidence":
        tag["evidence"] = [tag["evidence"][1]]
    elif failure == "duplicate_tag":
        response["lines"][1]["potentialTags"].append(tag)
    elif failure == "missing_line":
        response["lines"].pop(0)
    else:
        response["lines"].append(response["lines"][0])
    with pytest.raises(ValueError):
        screen_potential_tags(packages, result, TagModel(response))
