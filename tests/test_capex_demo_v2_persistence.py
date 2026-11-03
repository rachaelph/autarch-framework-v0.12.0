from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import pytest

from autarch.capex.demo_v2_contracts import (
    BundleCandidateGroup,
    BundleCandidateMember,
    BundlingDisposition,
    CapitalizationAssessment,
    CapitalizationRecommendation,
    NatureResult,
)
from autarch.capex.demo_v2_persistence import (
    DemoV2PersistenceError,
    persist_demo_v2_results,
)


NOW = datetime(2026, 11, 3, 14, 30, tzinfo=timezone.utc)


class RecordingCursor:
    def __init__(self, connection: "RecordingConnection") -> None:
        self.connection = connection
        self.closed = False

    def execute(
        self,
        operation: str,
        parameters: Sequence[Any] = (),
    ) -> "RecordingCursor":
        values = tuple(parameters)
        self.connection.calls.append(("execute", operation, values))
        if (
            self.connection.fail_on is not None
            and self.connection.fail_on in operation
        ):
            raise RuntimeError("simulated database failure")
        return self

    def executemany(
        self,
        operation: str,
        parameters: Sequence[Sequence[Any]],
    ) -> "RecordingCursor":
        rows = tuple(tuple(row) for row in parameters)
        self.connection.calls.append(("executemany", operation, rows))
        if (
            self.connection.fail_on is not None
            and self.connection.fail_on in operation
        ):
            raise RuntimeError("simulated database failure")
        return self

    def fetchone(self) -> Optional[Sequence[Any]]:
        return self.connection.existing

    def close(self) -> None:
        self.closed = True


class RecordingConnection:
    def __init__(
        self,
        *,
        existing: Optional[Sequence[Any]] = None,
        fail_on: Optional[str] = None,
    ) -> None:
        self.existing = existing
        self.fail_on = fail_on
        self.calls: list[tuple[str, str, object]] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.cursor_instance = RecordingCursor(self)

    def cursor(self) -> RecordingCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


def _assessment(
    line_id: str,
    *,
    grouped: bool,
    amount: float,
) -> CapitalizationAssessment:
    return CapitalizationAssessment(
        workflow_run_id="workflow-1",
        package_id="package-1",
        invoice_id="invoice-1",
        line_id=line_id,
        suggested_task_code="5225",
        nature_result=NatureResult.CAPITALIZABLE,
        repair_maintenance_indicator=False,
        never_capitalize_hit=False,
        unit_cost_meets_threshold=False,
        threshold_amount=1000,
        threshold_currency="USD",
        recommendation=CapitalizationRecommendation.OPEX,
        outcome_category="OPEX_POTENTIAL_FUTURE_BUNDLING",
        bundling_disposition=(
            BundlingDisposition.GROUPED
            if grouped
            else BundlingDisposition.CANDIDATE
        ),
        bundle_group_id="bundle-1" if grouped else None,
        rationale=f"Line amount {amount:.2f} is below threshold.",
        policy_citations=["policy:demo", "setting:bundling-key"],
        policy_version="demo-policy-v1",
        created_at_utc=NOW,
    )


def _group() -> BundleCandidateGroup:
    return BundleCandidateGroup(
        workflow_run_id="workflow-1",
        bundle_group_id="bundle-1",
        suggested_task_code="5225",
        site_id="DEMO-SITE-001",
        project_id="DEMO-PROJECT-001",
        currency="USD",
        total_amount=1000,
        threshold_amount=1000,
        disposition=BundlingDisposition.CANDIDATE,
        members=[
            BundleCandidateMember(
                package_id="package-1",
                invoice_id="invoice-1",
                line_id="line-1",
                quantity=1,
                unit_cost=600,
                line_amount=600,
            ),
            BundleCandidateMember(
                package_id="package-1",
                invoice_id="invoice-1",
                line_id="line-2",
                quantity=2,
                unit_cost=200,
                line_amount=400,
            ),
        ],
        rationale="Matching lines reach the threshold.",
        policy_citations=["policy:demo", "setting:bundling-key"],
        policy_version="demo-policy-v1",
        created_at_utc=NOW,
    )


def _grouped_assessments() -> list[CapitalizationAssessment]:
    return [
        _assessment("line-1", grouped=True, amount=600),
        _assessment("line-2", grouped=True, amount=400),
    ]


def _operation_indexes(
    connection: RecordingConnection,
    fragment: str,
) -> list[int]:
    return [
        index
        for index, call in enumerate(connection.calls)
        if fragment in call[1]
    ]


def test_persists_assessments_groups_and_members_atomically() -> None:
    connection = RecordingConnection()

    counts = persist_demo_v2_results(
        connection,
        _grouped_assessments(),
        [_group()],
        workflow_run_id="workflow-1",
        created_at_utc=NOW,
    )

    assert counts.assessments == 2
    assert counts.bundle_groups == 1
    assert counts.bundle_members == 2
    assert counts.task_suggestions == 0
    assert connection.commit_count == 1
    assert connection.rollback_count == 0
    assert connection.cursor_instance.closed
    run_insert = _operation_indexes(
        connection,
        "INSERT INTO [capex].[AssessmentRun]",
    )[0]
    assessment_insert = _operation_indexes(
        connection,
        "INSERT INTO [capex].[Assessment]",
    )[0]
    group_insert = _operation_indexes(
        connection,
        "INSERT INTO [capex].[BundleCandidateGroup]",
    )[0]
    member_insert = _operation_indexes(
        connection,
        "INSERT INTO [capex].[BundleCandidateMember]",
    )[0]
    assert run_insert < assessment_insert < group_insert < member_insert
    assert len(connection.calls[assessment_insert][2]) == 2
    assert len(connection.calls[group_insert][2]) == 1
    assert len(connection.calls[member_insert][2]) == 2


def test_persists_review_only_run_without_bundle_rows() -> None:
    connection = RecordingConnection()
    assessment = CapitalizationAssessment(
        workflow_run_id="workflow-1",
        package_id="package-1",
        invoice_id="invoice-1",
        line_id="line-1",
        nature_result=NatureResult.REVIEW,
        repair_maintenance_indicator=False,
        never_capitalize_hit=False,
        recommendation=CapitalizationRecommendation.REVIEW,
        outcome_category="REVIEW_NATURE_UNRESOLVED",
        bundling_disposition=BundlingDisposition.REVIEW,
        missing_inputs=["approvedNatureRule"],
        review_reasons=["No approved nature rule matched."],
        rationale="The line remains unresolved.",
        policy_citations=["policy:demo"],
        policy_version="demo-policy-v1",
        created_at_utc=NOW,
    )

    counts = persist_demo_v2_results(
        connection,
        [assessment],
        [],
        workflow_run_id="workflow-1",
        created_at_utc=NOW,
    )

    assert counts.assessments == 1
    assert counts.bundle_groups == 0
    assert counts.bundle_members == 0
    assert not _operation_indexes(
        connection,
        "INSERT INTO [capex].[BundleCandidateGroup]",
    )
    assert not _operation_indexes(
        connection,
        "INSERT INTO [capex].[BundleCandidateMember]",
    )


def test_persists_review_bundle_without_mutating_assessment_reference() -> None:
    connection = RecordingConnection()
    assessments = [
        CapitalizationAssessment(
            workflow_run_id="workflow-1",
            package_id="package-1",
            invoice_id="invoice-1",
            line_id=line_id,
            suggested_task_code="5225",
            nature_result=NatureResult.REVIEW,
            repair_maintenance_indicator=False,
            never_capitalize_hit=False,
            recommendation=CapitalizationRecommendation.REVIEW,
            outcome_category="REVIEW_BUNDLING_CONTEXT",
            bundling_disposition=BundlingDisposition.REVIEW,
            missing_inputs=["siteId"],
            review_reasons=["Site context is missing."],
            rationale="The line cannot be grouped safely.",
            policy_citations=["policy:demo"],
            policy_version="demo-policy-v1",
            created_at_utc=NOW,
        )
        for line_id in ("line-1", "line-2")
    ]
    group = BundleCandidateGroup(
        workflow_run_id="workflow-1",
        bundle_group_id="bundle-review-1",
        suggested_task_code="5225",
        currency="USD",
        total_amount=1000,
        threshold_amount=1000,
        disposition=BundlingDisposition.REVIEW,
        members=[
            BundleCandidateMember(
                package_id="package-1",
                invoice_id="invoice-1",
                line_id="line-1",
                quantity=1,
                unit_cost=600,
                line_amount=600,
            ),
            BundleCandidateMember(
                package_id="package-1",
                invoice_id="invoice-1",
                line_id="line-2",
                quantity=1,
                unit_cost=400,
                line_amount=400,
            ),
        ],
        missing_inputs=["siteId"],
        rationale="Common task and currency, but site context is missing.",
        policy_citations=["policy:demo"],
        policy_version="demo-policy-v1",
        created_at_utc=NOW,
    )

    counts = persist_demo_v2_results(
        connection,
        assessments,
        [group],
        workflow_run_id="workflow-1",
        created_at_utc=NOW,
    )

    assert counts.assessments == 2
    assert counts.bundle_groups == 1
    assert counts.bundle_members == 2
    assert all(item.bundle_group_id is None for item in assessments)


def test_exact_replay_returns_counts_without_inserting() -> None:
    first = RecordingConnection()
    assessments = _grouped_assessments()
    groups = [_group()]
    persist_demo_v2_results(
        first,
        assessments,
        groups,
        workflow_run_id="workflow-1",
        created_at_utc=NOW,
    )
    run_insert = next(
        call
        for call in first.calls
        if "INSERT INTO [capex].[AssessmentRun]" in call[1]
    )
    run_values = run_insert[2]
    assert isinstance(run_values, tuple)
    replay = RecordingConnection(
        existing=(
            run_values[1],
            run_values[2],
            run_values[3],
            run_values[4],
        )
    )

    counts = persist_demo_v2_results(
        replay,
        list(reversed(assessments)),
        groups,
        workflow_run_id="workflow-1",
        created_at_utc=NOW,
    )

    assert counts.assessments == 2
    assert replay.commit_count == 1
    assert replay.rollback_count == 0
    assert len(_operation_indexes(replay, "INSERT INTO")) == 0


def test_changed_replay_is_rejected() -> None:
    connection = RecordingConnection(
        existing=("0" * 64, 2, 1, 2)
    )

    with pytest.raises(
        DemoV2PersistenceError,
        match="different assessment or bundle results",
    ):
        persist_demo_v2_results(
            connection,
            _grouped_assessments(),
            [_group()],
            workflow_run_id="workflow-1",
            created_at_utc=NOW,
        )

    assert connection.commit_count == 0
    assert connection.rollback_count == 1
    assert len(_operation_indexes(connection, "INSERT INTO")) == 0


def test_rolls_back_when_bundle_insert_fails() -> None:
    connection = RecordingConnection(
        fail_on="INSERT INTO [capex].[BundleCandidateGroup]",
    )

    with pytest.raises(
        DemoV2PersistenceError,
        match="simulated database failure",
    ):
        persist_demo_v2_results(
            connection,
            _grouped_assessments(),
            [_group()],
            workflow_run_id="workflow-1",
            created_at_utc=NOW,
        )

    assert connection.commit_count == 0
    assert connection.rollback_count == 1
    assert connection.cursor_instance.closed


def test_rejects_inconsistent_group_membership_before_database_work() -> None:
    connection = RecordingConnection()
    assessments = _grouped_assessments()
    assessments[0] = assessments[0].model_copy(
        update={"bundle_group_id": "bundle-other"}
    )

    with pytest.raises(
        DemoV2PersistenceError,
        match="group references must match",
    ):
        persist_demo_v2_results(
            connection,
            assessments,
            [_group()],
            workflow_run_id="workflow-1",
            created_at_utc=NOW,
        )

    assert connection.calls == []


def test_rejects_bundle_total_that_does_not_reconcile() -> None:
    connection = RecordingConnection()
    group = _group().model_copy(update={"total_amount": 999})

    with pytest.raises(
        DemoV2PersistenceError,
        match="does not reconcile",
    ):
        persist_demo_v2_results(
            connection,
            _grouped_assessments(),
            [group],
            workflow_run_id="workflow-1",
            created_at_utc=NOW,
        )

    assert connection.calls == []
