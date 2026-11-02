from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional

import pytest

from autarch.capex.demo_v2_contracts import (
    TaskMatchMethod,
    TaskResolutionStatus,
)
from autarch.capex.demo_v2_normalization import (
    NormalizationDataQuality,
    NormalizationStatus,
    NormalizedInvoiceLine,
    NormalizedInvoicePackage,
    NormalizedSource,
)
from autarch.capex.demo_v2_reference import (
    ReferenceSource,
    TaskMasterSnapshot,
    TaskReferenceEntry,
)
from autarch.capex.demo_v2_task_suggestion import (
    LangChainTaskSuggestionModel,
    TaskSuggestionResponseError,
    suggest_tasks_for_invoice,
)
from autarch.errors import ModelError
from autarch.intelligence.base import ModelProvider


NOW = datetime(2026, 10, 30, 16, 0, tzinfo=timezone.utc)


def _task(
    task_code: str,
    description: str,
    category: str,
    minor: str,
) -> TaskReferenceEntry:
    return TaskReferenceEntry(
        task_code=task_code,
        description=description,
        asset_category_major=category,
        asset_category_minor=minor,
        property_type="Real",
        search_text=(
            f"{task_code} {description} {category} {minor} real"
        ).lower(),
    )


def _snapshot() -> TaskMasterSnapshot:
    tasks = [
        _task(
            "0007",
            "ENVIRONMENTAL",
            "PROFESSIONAL SERVICES",
            "ASSESSMENT",
        ),
        _task(
            "1000",
            "BUILDING",
            "REAL PROPERTY",
            "STRUCTURE",
        ),
    ]
    return TaskMasterSnapshot(
        snapshot_id="task-master-us-2026-09-06",
        source=ReferenceSource(
            file_name="TaskMaster.xlsx",
            sheet_name="US Task Type",
            source_version="2026-09-06",
            sha256="A" * 64,
        ),
        total_source_rows=len(tasks),
        active_task_count=len(tasks),
        tasks=tasks,
    )


def _line(
    line_id: str,
    description: str,
    *,
    source_task_code: Optional[str] = None,
) -> NormalizedInvoiceLine:
    return NormalizedInvoiceLine(
        line_id=line_id,
        line_number=int(line_id.rsplit("-", 1)[-1]),
        description=description,
        quantity=1,
        unit_cost=100,
        line_amount=100,
        currency="USD",
        source_task_code=source_task_code,
    )


def _package(
    lines: List[NormalizedInvoiceLine],
) -> NormalizedInvoicePackage:
    return NormalizedInvoicePackage(
        package_id="package-1",
        run_id="normalize-1",
        invoice_id="invoice-1",
        vendor_name="Demo Vendor",
        invoice_total=sum(line.line_amount for line in lines),
        currency="USD",
        country_region="North America",
        normalization_status=NormalizationStatus.NORMALIZED,
        lines=lines,
        data_quality=NormalizationDataQuality(),
        source=NormalizedSource(
            file_name="demo.json",
            sha256="a" * 64,
            source_system="TEST",
        ),
        created_at_utc=NOW,
    )


class RecordingProvider(ModelProvider):
    name = "recording"

    def __init__(
        self,
        response: str = "",
        failure: Optional[Exception] = None,
    ):
        self.response = response
        self.failure = failure
        self.calls = []

    def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
    ) -> str:
        self.calls.append((prompt, system))
        if self.failure is not None:
            raise self.failure
        return self.response


def _model_response(lines: List[dict]) -> str:
    return json.dumps({"lines": lines})


def _resolved(
    line_id: str,
    task_code: str,
    item: str,
) -> dict:
    return {
        "lineId": line_id,
        "resolutionStatus": "RESOLVED",
        "taskCode": task_code,
        "confidence": 0.91,
        "itemIdentified": item,
        "rationale": "The description matches the governed candidate.",
        "evidenceRefs": [
            f"line:{line_id}",
            f"task:{task_code}",
        ],
        "unresolvedReason": None,
    }


def test_exact_governed_source_task_skips_gpt() -> None:
    provider = RecordingProvider(
        failure=AssertionError("model must not be called")
    )
    package = _package(
        [_line("line-1", "Unstructured source text", source_task_code="0007")]
    )

    results = suggest_tasks_for_invoice(
        package,
        _snapshot(),
        workflow_run_id="workflow-1",
        model=LangChainTaskSuggestionModel(provider),
        created_at_utc=NOW,
    )

    assert provider.calls == []
    assert results[0].resolution_status == TaskResolutionStatus.RESOLVED
    assert results[0].match_method == TaskMatchMethod.EXACT_TASK
    assert results[0].suggested_task_code == "0007"
    assert results[0].model_deployment is None


def test_langchain_batches_all_unresolved_lines_in_one_call() -> None:
    provider = RecordingProvider(
        _model_response(
            [
                _resolved("line-1", "0007", "Environmental assessment"),
                _resolved("line-2", "1000", "Building structure"),
            ]
        )
    )
    package = _package(
        [
            _line("line-1", "Environmental assessment"),
            _line("line-2", "Building structure"),
        ]
    )

    results = suggest_tasks_for_invoice(
        package,
        _snapshot(),
        workflow_run_id="workflow-1",
        model=LangChainTaskSuggestionModel(provider),
        created_at_utc=NOW,
    )

    assert len(provider.calls) == 1
    assert "line-1" in provider.calls[0][0]
    assert "line-2" in provider.calls[0][0]
    assert [result.suggested_task_code for result in results] == [
        "0007",
        "1000",
    ]
    assert all(
        result.match_method == TaskMatchMethod.LANGCHAIN
        for result in results
    )
    assert all(result.model_deployment == "gpt-5.1" for result in results)
    assert all(result.task_master_sha256 for result in results)


def test_invoice_context_supplements_line_candidates() -> None:
    snapshot = TaskMasterSnapshot(
        snapshot_id="task-master-us-2026-09-06",
        source=ReferenceSource(
            file_name="TaskMaster.xlsx",
            sheet_name="US Task Type",
            source_version="2026-09-06",
            sha256="A" * 64,
        ),
        total_source_rows=1,
        active_task_count=1,
        tasks=[
            _task(
                "5225",
                "WIRE SHELVING",
                "STORE EQUIPMENT",
                "SHELVING-WIRE",
            )
        ],
    )
    provider = RecordingProvider(
        _model_response(
            [
                _resolved("line-1", "5225", "Wire shelving"),
                _resolved("line-2", "5225", "Shelf retainer"),
            ]
        )
    )

    results = suggest_tasks_for_invoice(
        _package(
            [
                _line("line-1", "Wire shelving divider"),
                _line("line-2", "FlexRoller clear retainer"),
            ]
        ),
        snapshot,
        workflow_run_id="workflow-1",
        model=LangChainTaskSuggestionModel(provider),
        created_at_utc=NOW,
    )

    assert len(provider.calls) == 1
    assert [result.suggested_task_code for result in results] == [
        "5225",
        "5225",
    ]


def test_shipping_is_reviewed_without_being_sent_to_gpt() -> None:
    snapshot = TaskMasterSnapshot(
        snapshot_id="task-master-us-2026-09-06",
        source=ReferenceSource(
            file_name="TaskMaster.xlsx",
            sheet_name="US Task Type",
            source_version="2026-09-06",
            sha256="A" * 64,
        ),
        total_source_rows=1,
        active_task_count=1,
        tasks=[
            _task(
                "5225",
                "WIRE SHELVING",
                "STORE EQUIPMENT",
                "SHELVING-WIRE",
            )
        ],
    )
    provider = RecordingProvider(
        _model_response(
            [_resolved("line-1", "5225", "Wire shelving")]
        )
    )

    results = suggest_tasks_for_invoice(
        _package(
            [
                _line("line-1", "Wire shelving divider"),
                _line("line-2", "Shipping"),
            ]
        ),
        snapshot,
        workflow_run_id="workflow-1",
        model=LangChainTaskSuggestionModel(provider),
        created_at_utc=NOW,
    )

    assert len(provider.calls) == 1
    assert results[0].suggested_task_code == "5225"
    assert results[1].resolution_status == TaskResolutionStatus.REVIEW
    assert results[1].suggested_task_code is None
    assert "shipping" in (results[1].unresolved_reason or "").lower()


def test_reimbursable_expense_is_reviewed_without_being_sent_to_gpt() -> None:
    provider = RecordingProvider(
        _model_response(
            [_resolved("line-1", "0007", "Environmental assessment")]
        )
    )

    results = suggest_tasks_for_invoice(
        _package(
            [
                _line("line-1", "Environmental assessment"),
                _line("line-2", "Line 10 level reimbursable expenses"),
            ]
        ),
        _snapshot(),
        workflow_run_id="workflow-1",
        model=LangChainTaskSuggestionModel(provider),
        created_at_utc=NOW,
    )

    assert len(provider.calls) == 1
    assert "line-2" not in provider.calls[0][0]
    assert results[1].resolution_status == TaskResolutionStatus.REVIEW
    assert results[1].suggested_task_code is None
    assert "reimbursable" in (results[1].unresolved_reason or "").lower()


def test_langchain_can_return_explicit_review() -> None:
    response = {
        "lineId": "line-1",
        "resolutionStatus": "REVIEW",
        "taskCode": None,
        "confidence": 0.35,
        "itemIdentified": "Environmental service",
        "rationale": "The line is not specific enough to select a task.",
        "evidenceRefs": ["line:line-1", "task:0007"],
        "unresolvedReason": "Multiple interpretations remain.",
    }
    provider = RecordingProvider(_model_response([response]))

    results = suggest_tasks_for_invoice(
        _package([_line("line-1", "Environmental assessment service")]),
        _snapshot(),
        workflow_run_id="workflow-1",
        model=LangChainTaskSuggestionModel(provider),
        created_at_utc=NOW,
    )

    assert results[0].resolution_status == TaskResolutionStatus.REVIEW
    assert results[0].match_method == TaskMatchMethod.REVIEW
    assert results[0].suggested_task_code is None
    assert results[0].unresolved_reason == (
        "Multiple interpretations remain."
    )


def test_response_rejects_unknown_task_code() -> None:
    provider = RecordingProvider(
        _model_response([_resolved("line-1", "9999", "Unknown")])
    )

    with pytest.raises(
        TaskSuggestionResponseError,
        match="outside TaskMaster",
    ):
        suggest_tasks_for_invoice(
            _package([_line("line-1", "Environmental assessment")]),
            _snapshot(),
            workflow_run_id="workflow-1",
            model=LangChainTaskSuggestionModel(provider),
            created_at_utc=NOW,
        )


def test_response_rejects_governed_code_outside_line_candidates() -> None:
    provider = RecordingProvider(
        _model_response([_resolved("line-1", "1000", "Building")])
    )

    with pytest.raises(
        TaskSuggestionResponseError,
        match="outside the retrieved candidate set",
    ):
        suggest_tasks_for_invoice(
            _package([_line("line-1", "Environmental assessment")]),
            _snapshot(),
            workflow_run_id="workflow-1",
            model=LangChainTaskSuggestionModel(provider),
            created_at_utc=NOW,
        )


@pytest.mark.parametrize(
    "lines, expected",
    [
        ([], "exactly one result"),
        (
            [
                _resolved("line-1", "0007", "Environmental"),
                _resolved("line-1", "0007", "Environmental"),
            ],
            "duplicate invoice line",
        ),
    ],
)
def test_response_rejects_missing_or_duplicate_lines(
    lines: List[dict],
    expected: str,
) -> None:
    provider = RecordingProvider(_model_response(lines))

    with pytest.raises(TaskSuggestionResponseError, match=expected):
        suggest_tasks_for_invoice(
            _package([_line("line-1", "Environmental assessment")]),
            _snapshot(),
            workflow_run_id="workflow-1",
            model=LangChainTaskSuggestionModel(provider),
            created_at_utc=NOW,
        )


def test_invalid_structured_output_is_an_explicit_error() -> None:
    provider = RecordingProvider("not-json")

    with pytest.raises(
        TaskSuggestionResponseError,
        match="invalid task-suggestion JSON",
    ):
        suggest_tasks_for_invoice(
            _package([_line("line-1", "Environmental assessment")]),
            _snapshot(),
            workflow_run_id="workflow-1",
            model=LangChainTaskSuggestionModel(provider),
            created_at_utc=NOW,
        )


def test_model_failure_propagates_without_success_fallback() -> None:
    provider = RecordingProvider(
        failure=ModelError("synthetic model failure")
    )

    with pytest.raises(ModelError, match="synthetic model failure"):
        suggest_tasks_for_invoice(
            _package([_line("line-1", "Environmental assessment")]),
            _snapshot(),
            workflow_run_id="workflow-1",
            model=LangChainTaskSuggestionModel(provider),
            created_at_utc=NOW,
        )


def test_unknown_jc_task_code_returns_review_without_gpt() -> None:
    package = _package(
        [_line("line-1", "TLG system", source_task_code="0303")]
    )

    results = suggest_tasks_for_invoice(
        package,
        _snapshot(),
        workflow_run_id="workflow-1",
        created_at_utc=NOW,
    )

    assert results[0].resolution_status == TaskResolutionStatus.REVIEW
    assert results[0].match_method == TaskMatchMethod.REVIEW
    assert results[0].suggested_task_code is None
    assert "0303" in (results[0].unresolved_reason or "")
