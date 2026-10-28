"""Tests for the immutable CICT-190 live proof contract."""

from __future__ import annotations

import pytest

from tools.prove_cict_190_live import (
    CORRELATION_ID,
    EXPECTED_LINE_COUNT,
    INVOICE_ID,
    MODEL_DEPLOYMENT,
    PACKAGE_ID,
    PROMPT_VERSION,
    ProofError,
    RUN_ID,
    TASK_MASTER_SHA256,
    build_event,
    validate_rows,
)


def _rows() -> list[dict[str, object]]:
    return [
        {
            "lineId": f"line-{index:02d}",
            "resultStatus": "SUGGESTED",
            "recommendation": "CAPEX",
            "taskCode": f"TASK-{index:02d}",
            "packageId": PACKAGE_ID,
            "invoiceId": INVOICE_ID,
            "runId": RUN_ID,
            "correlationId": CORRELATION_ID,
            "modelDeployment": MODEL_DEPLOYMENT,
            "promptVersion": PROMPT_VERSION,
            "taskMasterSha256": TASK_MASTER_SHA256,
            "createdAtUtc": "2026-10-28T18:00:00",
        }
        for index in range(EXPECTED_LINE_COUNT)
    ]


def test_build_event_is_stable_and_pointer_only() -> None:
    event = build_event()

    assert event["eventId"] == (
        "evt-06d9fc90-73ce-5e2f-ac87-32b9fbc72841"
    )
    assert event["packageId"] == PACKAGE_ID
    assert event["packageUri"].endswith(
        f"/capex/output/{RUN_ID}/{PACKAGE_ID}.json"
    )
    assert set(event) == {
        "eventId",
        "eventType",
        "schemaVersion",
        "invoiceId",
        "packageId",
        "runId",
        "packageUri",
        "occurredAtUtc",
        "correlationId",
    }


def test_validate_rows_proves_governance_and_provenance() -> None:
    rows = _rows()

    result = validate_rows(
        rows,
        governed_codes={
            str(row["taskCode"])
            for row in rows
        },
    )

    assert result["rowCount"] == EXPECTED_LINE_COUNT
    assert result["distinctLineCount"] == EXPECTED_LINE_COUNT
    assert result["duplicateEventLineGroups"] == 0
    assert result["unresolvedLineCount"] == 0
    assert result["ungovernedTaskCodeCount"] == 0
    assert len(result["durableRowsSha256"]) == 64


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        (
            lambda rows: rows[1].update(
                lineId=rows[0]["lineId"]
            ),
            "duplicate line IDs",
        ),
        (
            lambda rows: rows[0].update(
                taskCode="NOT-GOVERNED"
            ),
            "Ungoverned task codes",
        ),
        (
            lambda rows: rows[0].update(
                packageId="wrong-package"
            ),
            "Unexpected packageId provenance",
        ),
    ],
)
def test_validate_rows_rejects_invalid_durable_results(
    mutation: object,
    expected_error: str,
) -> None:
    rows = _rows()
    mutation(rows)

    with pytest.raises(ProofError, match=expected_error):
        validate_rows(
            rows,
            governed_codes={
                f"TASK-{index:02d}"
                for index in range(EXPECTED_LINE_COUNT)
            },
        )
