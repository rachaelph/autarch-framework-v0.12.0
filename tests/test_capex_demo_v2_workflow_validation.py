from __future__ import annotations

from tools.validate_capex_demo_v2_workflow import validate_workflow


def test_complete_three_invoice_workflow_reconciles_and_replays() -> None:
    evidence = validate_workflow()

    assert evidence["status"] == "PASS"
    assert evidence["taskEvidence"] == {
        "mode": "REPLAY_OF_PRIOR_GOVERNED_RESULTS",
        "priorModelCalls": 2,
        "modelCallsDuringWorkflowValidation": 0,
        "invalidTaskCodes": [],
    }
    truthful = evidence["truthfulCurrentPolicy"]
    assert len(truthful["taskDeterminations"]) == 23
    assert len(truthful["assessments"]) == 23
    assert truthful["bundleGroups"] == []
    synthetic = evidence["syntheticDemo"]["result"]
    assert len(synthetic["taskDeterminations"]) == 23
    assert len(synthetic["assessments"]) == 23
    assert len(synthetic["bundleGroups"]) == 1
    assert synthetic["bundleGroups"][0]["totalAmount"] == 455.56
    assert {
        member["invoiceId"]
        for member in synthetic["bundleGroups"][0]["members"]
    } == {"135d03ac-3598-498a-83fe-d273d046bfea"}
    assert synthetic["persistence"] == {
        "taskSuggestions": 23,
        "assessments": 23,
        "bundleGroups": 1,
        "bundleMembers": 9,
    }
    assert evidence["replayEvidence"]["additionalInsertCount"] == 0
