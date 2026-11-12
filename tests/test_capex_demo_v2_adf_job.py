from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from autarch.capex import demo_v2_adf_job as job
from autarch.errors import ModelError
from autarch.intelligence.base import ModelProvider
from tests.test_capex_demo_v2_intake import INPUT, OUTPUT, read_fixture


DATA = Path(__file__).resolve().parents[1] / "examples" / "data" / "capex"
MASTER = DATA / "reference-v2" / "task-master-us-v2.json"
POLICY = DATA / "reference-v2" / "policy-config-v2.json"


class ReviewModel(ModelProvider):
    name = "MOCK_TEST_ONLY"

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0
        self.requests = []

    def complete(self, prompt, system=None):
        self.calls += 1
        if self.fail:
            raise ModelError("Controlled mock-only provider failure")
        if prompt.startswith("Invoice request JSON:"):
            request = json.loads(prompt.split("\n", 1)[1].split("\n\nReturn JSON")[0])
            self.requests.append(request)
            return json.dumps({"lines": [{
                "lineId": line["lineId"], "resolutionStatus": "REVIEW", "taskCode": None,
                "confidence": 0.1, "unresolvedReason": "MOCK TEST ONLY: insufficient evidence",
                "rationale": "MOCK TEST ONLY: retain human review",
                "evidenceRefs": [f"line:{line['lineId']}"],
            } for line in request["lines"]]})
        request = json.loads(prompt)
        self.requests.append(request)
        if request["stage"] == "POTENTIAL_TAGS":
            return json.dumps({"lines": [{
                "packageId": package["packageId"], "lineId": line["lineId"], "potentialTags": [],
            } for package in request["packages"] for line in package["lines"]]})
        if request["stage"] == "ACCOUNTING_DECISION":
            return json.dumps({"items": [{
                "lineId": line["lineId"], "natureResult": "REVIEW", "recommendation": "REVIEW",
                "repairMaintenanceIndicator": False, "neverCapitalizeHit": False,
                "rationale": "MOCK TEST ONLY: insufficient source facts",
                "policyCitations": request["allowedPolicyCitations"],
                "missingInputs": ["MOCK TEST ONLY: analyst review"],
                "reviewReasons": ["MOCK TEST ONLY: task unresolved"],
            } for line in request["processingPackage"]["lines"]]})
        assert request["stage"] == "BUNDLE_DECISION"
        return json.dumps({"items": [{
            **identity, "decision": "REVIEW", "proposalId": None,
            "confidence": 0.1, "rationale": "MOCK TEST ONLY: unresolved inputs",
            "evidenceRefs": [f"line:{identity['packageId']}:{identity['lineId']}"],
        } for identity in request["decisionLineCatalog"]], "groups": []})


def run(provider, written, **overrides):
    def read(uri):
        if uri in (str(MASTER), str(POLICY)):
            return Path(uri).read_bytes()
        return read_fixture(uri)

    def write(uri, document):
        if uri in written:
            raise FileExistsError(uri)
        written[uri] = document

    args = dict(
        input_base_uri=INPUT, output_base_uri=OUTPUT, workflow_run_id="adf-worker-test",
        task_master_uri=str(MASTER), task_master_sha256=hashlib.sha256(MASTER.read_bytes()).hexdigest(),
        policy_uri=str(POLICY), policy_sha256=hashlib.sha256(POLICY.read_bytes()).hexdigest(),
        provider=provider, read=read, write=write,
    )
    args.update(overrides)
    return job.run_adf_batch(**args)


def test_all_sources_are_accounted_for_with_explicit_reference_exceptions():
    written = {}
    model = ReviewModel()
    summary = run(model, written)
    assert summary["status"] == "REVIEW_REQUIRED"
    assert summary["sourceCount"] == 4
    assert summary["packageCount"] == 10
    assert summary["lineCount"] == 30
    assert summary["excludedRecordCount"] == 7
    assert summary["modelPackageCount"] == 2
    assert summary["referenceExceptionPackageCount"] == summary["referenceExceptionLineCount"] == 8
    assert summary["taskSuggestionCount"] == summary["assessmentCount"] == summary["bundleDecisionCount"] == 22
    assert summary["bundleGroupCount"] == 0
    assert summary["modelCallsAttempted"] == summary["modelResponsesReceived"] == model.calls == 6
    assert summary["sqlWrites"] == summary["erpWrites"] == 0
    assert not summary["freshPdfExtraction"]
    assert not summary["humanApproval"]
    assert list(written)[-1].endswith("/batch.json")
    evidence = written[summary["evidenceUri"]]
    assert evidence["status"] == "SUCCEEDED"
    assert evidence["provenance"].startswith("ORIGINAL_SOURCE_DEMO_WITH_UNAPPROVED_POLICY:")
    assert evidence["modelRequestUri"] == summary["modelRequestUri"]
    assert evidence["referenceExceptionsUri"] == summary["referenceExceptionsUri"]
    assert {call["stage"] for call in evidence["calls"]} == {
        "TASK_SUGGESTION", "ACCOUNTING_DECISION", "BUNDLE_DECISION", "POTENTIAL_TAGS",
    }
    exceptions = written[summary["referenceExceptionsUri"]]["packages"]
    assert len(exceptions) == 8
    assert {item["currency"] for item in exceptions} == {"EUR", "DKK"}
    assert all(item["modelCalls"] == 0 and item["route"] == "REVIEW_REQUIRED" for item in exceptions)
    assert all(item["reasons"] == [
        "No governed native-currency policy is supplied; no FX conversion is authorized.",
    ] for item in exceptions)
    assert all(item["normalizationReviewReasons"] for item in exceptions)
    intake_request = written[summary["requestUri"]]
    model_request = written[summary["modelRequestUri"]]
    all_ids = {item["packageId"] for item in intake_request["packages"]}
    model_ids = {item["packageId"] for item in model_request["packages"]}
    exception_ids = {item["packageId"] for item in exceptions}
    assert not model_ids & exception_ids
    assert model_ids | exception_ids == all_ids
    assert sum(len(written[item["packageUri"]]["lines"]) for item in model_request["packages"]) + sum(
        len(item["lineIds"]) for item in exceptions
    ) == summary["lineCount"]
    for item in exceptions:
        package = written[item["packageUri"]]
        assert item["sourceSha256"] == package["source"]["sha256"]
        assert item["lineIds"] == [line["lineId"] for line in package["lines"]]
    accounting = [item for item in model.requests if item.get("stage") == "ACCOUNTING_DECISION"]
    assert len(accounting) == 2
    assert all(item["processingPackage"]["currency"] == "USD" for item in accounting)
    assert not any(package_id in json.dumps(model.requests) for package_id in exception_ids)


@pytest.mark.parametrize("field", ["task_master_sha256", "policy_sha256"])
def test_reference_drift_prevents_all_output_and_model_calls(field):
    written = {}
    model = ReviewModel()
    with pytest.raises(ValueError, match="Reference bytes changed"):
        run(model, written, **{field: "0" * 64})
    assert written == {}
    assert model.calls == 0


@pytest.mark.parametrize("unsupported", ["region", "currency"])
def test_unsupported_policy_never_calls_model_or_marks_batch_complete(unsupported):
    policy = json.loads(POLICY.read_bytes())
    if unsupported == "region":
        policy["region"] = "EUROPE"
    else:
        policy["capitalization"]["currency"] = "GBP"
    policy_bytes = json.dumps(policy).encode()

    def read(uri):
        if uri == str(POLICY):
            return policy_bytes
        if uri == str(MASTER):
            return MASTER.read_bytes()
        return read_fixture(uri)

    written = {}
    model = ReviewModel()
    expected_error = "North American policy" if unsupported == "region" else "No invoice"
    with pytest.raises(ValueError, match=expected_error):
        run(model, written, read=read, policy_sha256=hashlib.sha256(policy_bytes).hexdigest())
    assert model.calls == 0
    assert not any(uri.endswith("/batch.json") for uri in written)
    if unsupported == "region":
        assert not written
    else:
        exceptions = next(
            value for uri, value in written.items() if uri.endswith("/reference-exceptions.json")
        )
        assert len(exceptions["packages"]) == 10


def test_model_failure_records_evidence_and_never_publishes_completion_marker():
    written = {}
    with pytest.raises(ValueError, match="complete three-stage"):
        run(ReviewModel(fail=True), written)
    assert not any(uri.endswith("/batch.json") for uri in written)
    evidence = next(value for uri, value in written.items() if uri.endswith("/llm-evidence.json"))
    assert evidence["status"] == "INCOMPLETE"
    assert evidence["calls"]
    assert all(call["error"] for call in evidence["calls"])


def test_repeat_run_stops_before_new_model_calls():
    written = {}
    run(ReviewModel(), written)
    second_model = ReviewModel()
    with pytest.raises(FileExistsError):
        run(second_model, written)
    assert second_model.calls == 0


def test_result_upload_failure_never_publishes_completion_marker():
    written = {}

    def write(uri, value):
        if uri.endswith("/result.json"):
            raise OSError("Controlled result upload failure")
        written[uri] = value

    with pytest.raises(OSError, match="result upload failure"):
        run(ReviewModel(), written, write=write)
    assert any(uri.endswith("/llm-evidence.json") for uri in written)
    assert not any(uri.endswith("/batch.json") for uri in written)


def test_failed_model_and_result_upload_still_retain_failure_evidence():
    written = {}

    def write(uri, value):
        if uri.endswith("/result.json"):
            raise OSError("Controlled result upload failure")
        written[uri] = value

    with pytest.raises(OSError, match="result upload failure"):
        run(ReviewModel(fail=True), written, write=write)
    evidence = next(value for uri, value in written.items() if uri.endswith("/llm-evidence.json"))
    assert evidence["status"] == "INCOMPLETE"
    assert evidence["error"]
    assert not any(uri.endswith("/batch.json") for uri in written)


def test_local_worker_persists_consistent_json_bundle(tmp_path):
    summary = job.run_adf_batch(
        input_base_uri=str(DATA), output_base_uri=str(tmp_path),
        workflow_run_id="local-persistence-test",
        task_master_uri=str(MASTER),
        task_master_sha256=hashlib.sha256(MASTER.read_bytes()).hexdigest(),
        policy_uri=str(POLICY),
        policy_sha256=hashlib.sha256(POLICY.read_bytes()).hexdigest(),
        provider=ReviewModel(),
    )
    saved = json.loads((tmp_path / "local-persistence-test" / "batch.json").read_bytes())
    assert saved == summary
    result = json.loads(Path(saved["resultUri"]).read_bytes())
    evidence = json.loads(Path(saved["evidenceUri"]).read_bytes())
    exceptions = json.loads(Path(saved["referenceExceptionsUri"]).read_bytes())
    assert evidence["workflowResultSha256"] == job.canonical_sha256(result)
    assert evidence["status"] == "SUCCEEDED"
    assert len(exceptions["packages"]) == 8
    assert saved["status"] == "REVIEW_REQUIRED"


def test_cli_requires_explicit_model_execution_opt_in():
    with pytest.raises(SystemExit) as exc:
        job.main([])
    assert exc.value.code == 2
