from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import pytest

from autarch.capex import demo_v2_adf_extraction as extraction
from autarch.capex import demo_v2_adf_sql as sql
from autarch.capex.demo_v2_intake import IntakeManifest, IntakeSource
from autarch.capex.demo_v2_contracts import DemoWorkflowRequest, DemoWorkflowResult, InvoicePackageReference
from autarch.capex.demo_v2_llm_decisions import SqlLlmDecisionWorkflow
from autarch.capex.demo_v2_reference import TaskMasterSnapshot
from autarch.capex.demo_v2_review import ReviewDecision, ReviewStore
from tests.test_capex_demo_v2_adf_job import MASTER, run
from tests.test_capex_demo_v2_intake import read_fixture
from tests.test_capex_demo_v2_mvp import FixtureModel, extracted, fake_render
from tests.test_capex_demo_v2_adf_job import ReviewModel
from tests.test_capex_demo_v2_task_suggestion import _line, _package


class ConditionalModel(ReviewModel):
    """Mock decisions test routing/contracts, not real invoice accounting."""

    def supports_vision(self):
        return True

    def complete_vision(self, prompt, images, system=None):
        self.vision_calls = getattr(self, "vision_calls", 0) + 1
        invoice = extracted(self.vision_calls)
        for line in invoice["lines"]:
            line["sourceTaskCode"] = None
        if self.vision_calls == 2:
            invoice["currency"] = None
            invoice["references"] = [ref for ref in invoice["references"] if ref["field"] != "currency"]
        return json.dumps(invoice)

    def complete(self, prompt, system=None):
        if prompt.startswith("Invoice request JSON:"):
            request = json.loads(prompt.split("\n", 1)[1].split("\n\nReturn JSON")[0])
            self.requests.append(request)
            self.calls += 1
            return json.dumps({"lines": [{
                "lineId": line["lineId"], "resolutionStatus": "RESOLVED",
                "taskCode": line["candidates"][0]["taskCode"], "confidence": 0.9,
                "itemIdentified": line["description"], "rationale": "MOCK TEST ONLY: semantic catalog match",
                "evidenceRefs": [f"line:{line['lineId']}", f"task:{line['candidates'][0]['taskCode']}"],
            } for line in request["lines"]]})
        request = json.loads(prompt)
        if request["stage"] == "POTENTIAL_TAGS":
            return json.dumps({"lines": [{
                "packageId": package["packageId"], "lineId": line["lineId"], "potentialTags": [],
            } for package in request["packages"] for line in package["lines"]]})
        if request["stage"] == "ASSET_CLASS_SUGGESTION":
            return json.dumps({"lines": [{
                "lineId": line["lineId"], "assetClassCode": "295",
                "resolutionStatus": "RESOLVED", "itemIdentified": "MOCK TEST ONLY: shop equipment",
                "confidence": 0.9, "rationale": "MOCK TEST ONLY: EU catalog routing.",
                "evidenceRefs": [f"line:{line['lineId']}", "asset-class:295"],
            } for line in request["lines"]]})
        if request["stage"] == "TASK_RETRIEVAL":
            self.requests.append(request)
            self.calls += 1
            return json.dumps({"lines": [{
                "lineId": line["lineId"], "query": "SHELVING",
            } for line in request["invoice"]["lines"]]})
        if request["stage"] != "ACCOUNTING_DECISION":
            return super().complete(prompt, system)
        self.requests.append(request)
        self.calls += 1
        threshold = request["policy"]["capitalization"]["baseThreshold"]
        return json.dumps({"items": [{
            "lineId": constraint["lineId"], "natureResult": "CAPITALIZABLE",
            "recommendation": "CAPEX" if constraint["demoThresholdValue"] >= threshold else "OPEX",
            "repairMaintenanceIndicator": False, "neverCapitalizeHit": False,
            "rationale": "MOCK TEST ONLY: conditional demo threshold, never approval",
            "policyCitations": request["allowedPolicyCitations"], "missingInputs": [], "reviewReasons": [],
        } for constraint in request["lineConstraints"]]})


@pytest.mark.parametrize(
    ("description", "expected_code"),
    [("Electrical Installation", None), ("EV charging electrical installation", "6805")],
)
def test_conditional_electrical_candidates_require_ev_evidence(description, expected_code):
    class ElectricalModel(ReviewModel):
        def complete(self, prompt, system=None):
            if not prompt.startswith("Invoice request JSON:"):
                request = json.loads(prompt)
                return json.dumps({"lines": [
                    {"lineId": line["lineId"], "query": "ELECTRICAL"}
                    for line in request["invoice"]["lines"]
                ]})
            request = json.loads(prompt.split("\n", 1)[1].split("\n\nReturn JSON")[0])
            self.requests.append(request)
            line = request["lines"][0]
            code = line["candidates"][0]["taskCode"] if line["candidates"] else None
            return json.dumps({"lines": [{
                "lineId": line["lineId"], "taskCode": code,
                "resolutionStatus": "RESOLVED" if code else "REVIEW",
                "confidence": 0.9 if code else 0.1,
                "unresolvedReason": None if code else "MOCK TEST ONLY: EV context is not evidenced.",
                "rationale": "MOCK TEST ONLY: select only an eligible candidate.",
                "evidenceRefs": [f"line:{line['lineId']}"] + ([f"task:{code}"] if code else []),
            }]})

    package = _package([_line("line-1", description)])
    request = DemoWorkflowRequest(
        workflow_run_id="mock-electrical", requested_at_utc=package.created_at_utc,
        packages=[InvoicePackageReference(
            package_id=package.package_id, run_id=package.run_id, package_uri="memory:test",
        )],
        model_deployment="MOCK_TEST_ONLY", task_prompt_version="mock-electrical-context",
    )
    model = ElectricalModel()
    workflow = SqlLlmDecisionWorkflow(
        sources=[package], provider=model, deployment="MOCK_TEST_ONLY",
        task_master=TaskMasterSnapshot.model_validate_json(MASTER.read_bytes()),
        fixture_id="mock-electrical-context", input_sha256="0" * 64, demo_recommendations=True,
    )
    task = workflow.resolve(package, request)[0]
    assert task.suggested_task_code == expected_code
    assert (task.suggested_asset_class is not None) == (expected_code is not None)
    offered = {candidate["taskCode"] for candidate in model.requests[0]["lines"][0]["candidates"]}
    assert ("6805" in offered) == (expected_code is not None)


def test_conditional_demo_runs_every_invoice_without_inventing_missing_unit_values(monkeypatch):
    use_mock_render(monkeypatch)
    model = ConditionalModel()
    written = {}
    summary = run(
        model, written, fresh_pdf_extraction=True,
        allow_provisional_pdf_recommendations=True, demo_recommendations=True,
    )
    assert summary["modelPackageCount"] == 10 and summary["referenceExceptionPackageCount"] == 0
    assert summary["resolvedTaskCount"] == 2
    assert summary["resolvedAssetClassCount"] == 8
    assert summary["bundleDecisionCount"] == 10
    assert summary["capexCount"] > 0 and summary["opexCount"] > 0
    evidence = written[summary["evidenceUri"]]
    assert evidence["demoAssumptions"]["nativeThresholds"] == {"USD": 1000, "EUR": 1000, "DKK": 7500}
    assert not evidence["demoAssumptions"]["sourceValuesOverwritten"]
    retrievals = [request for request in model.requests if request.get("stage") == "TASK_RETRIEVAL"]
    assert len(retrievals) == 2
    for request in retrievals:
        assert set(request["categoryDescriptions"]) == set(request["categoryNames"])
        assert all(request["categoryDescriptions"].values())
        assert "SHELVING" in request["categoryDescriptions"]["SHELVING"]
    tasks = [request for request in model.requests if "taskMasterVersion" in request]
    assert len(tasks) == 2
    assert "5220A" in {task["taskCode"] for task in tasks[0]["lines"][0]["candidates"]}
    assert not {"5225", "5225A", "5225B"} & {
        task["taskCode"] for task in tasks[0]["lines"][0]["candidates"]
    }
    accounts = [request for request in model.requests if request.get("stage") == "ACCOUNTING_DECISION"]
    assert len(accounts) == 10
    workbook_accounts = [request for request in accounts if request["sourcePackage"]["invoiceId"].startswith("JC")]
    assert len(workbook_accounts) == 8
    for request in workbook_accounts:
        line = request["processingPackage"]["lines"][0]
        assert line["unitCost"] is None and line["quantity"] is None
        assert request["lineConstraints"][0]["demoThresholdBasis"] == "LINE_AMOUNT_PROXY"
    pdfs = [value for uri, value in written.items() if "/extraction/" in uri]
    assert json.loads(pdfs[1]["response"])["currency"] is None
    result = written[summary["resultUri"]]
    assert all(item["outcomeCategory"].startswith("CONDITIONAL_DEMO_") for item in result["assessments"])
    assert all("not finance approval" in item["rationale"] for item in result["assessments"])


def test_archive_currency_evidence_never_overrides_conflicting_pdf_currency(monkeypatch):
    use_mock_render(monkeypatch)
    model = ConditionalModel()
    original = model.complete_vision

    def conflicting(prompt, images, system=None):
        invoice = json.loads(original(prompt, images, system))
        if model.vision_calls == 1:
            invoice["currency"] = "EUR"
        return json.dumps(invoice)

    monkeypatch.setattr(model, "complete_vision", conflicting)
    written = {}
    summary = run(
        model, written, fresh_pdf_extraction=True,
        allow_provisional_pdf_recommendations=True, demo_recommendations=True,
    )
    exceptions = written[summary["referenceExceptionsUri"]]["packages"]
    assert len(exceptions) == 1
    assert "PDF currency conflicts with the pinned archive header." in exceptions[0]["reasons"]


@pytest.mark.parametrize("correct_on_retry", [True, False])
def test_conditional_demo_rechecks_pdf_arithmetic_once_without_changing_source_values(
    monkeypatch, correct_on_retry,
):
    use_mock_render(monkeypatch)
    model = ConditionalModel()
    complete_vision = model.complete_vision
    valid_response = None
    vision_calls = 0

    def inconsistent_subtotal(prompt, images, system=None):
        nonlocal valid_response, vision_calls
        vision_calls += 1
        payload = json.loads(prompt)
        retrying = valid_response is not None
        if retrying:
            invoice = json.loads(valid_response)
        else:
            raw = complete_vision(prompt, images, system)
            if model.vision_calls == 1:
                return raw
            valid_response = raw
            invoice = json.loads(raw)
        if not retrying or not correct_on_retry:
            invoice["subtotal"] = str(sum(Decimal(line["amount"]) for line in invoice["lines"]) + 1)
            invoice["invoiceTotal"] = invoice["subtotal"]
        if "validationFeedback" in payload:
            invoice["validationFeedback"] = payload["validationFeedback"]
        return json.dumps(invoice)

    monkeypatch.setattr(model, "complete_vision", inconsistent_subtotal)
    written = {}
    summary = run(
        model, written, fresh_pdf_extraction=True,
        allow_provisional_pdf_recommendations=True, demo_recommendations=True,
    )
    assert vision_calls == 3
    assert summary["modelPackageCount"] == (10 if correct_on_retry else 9)
    evidence = written[summary["evidenceUri"]]
    assert len(evidence["extractionCalls"]) == 3
    failed = next(call for call in evidence["extractionCalls"] if call.get("validationError"))
    assert failed["validationError"] == "Extracted line sum does not reconcile to subtotal."
    assert any(uri.endswith(".attempt-1.json") for uri in written)
    final = [call for call in evidence["extractionCalls"] if call["packageId"] == failed["packageId"]][-1]
    assert final["attempt"] == 2
    if correct_on_retry:
        assert json.loads(final["response"]) == json.loads(valid_response)
    else:
        exceptions = written[summary["referenceExceptionsUri"]]["packages"]
        assert exceptions[0]["reasons"] == ["Extracted line sum does not reconcile to subtotal."]


@pytest.mark.parametrize("correct_on_retry", [True, False])
def test_pdf_arithmetic_retry_keeps_feedback_out_of_invoice_fields(monkeypatch, correct_on_retry):
    package = _package([_line("line-1", "Shelving")])
    filename = "Bruegmann  - INV1262027.pdf-135d03ac-3598-498a-83fe-d273d046bfea.zip"
    data = read_fixture(filename)
    intake = IntakeManifest(
        workflow_run_id=package.run_id, created_at_utc=package.created_at_utc,
        sources=[IntakeSource(
            file_name=filename, uri="memory:pdf-source",
            sha256=hashlib.sha256(data).hexdigest(), byte_count=len(data),
            format="ABBYY_ZIP", package_ids=[package.package_id],
        )],
        request_uri="memory:request", package_count=1, line_count=1,
        excluded_record_count=0, review_required_package_count=0,
    )
    prompts = []
    model = FixtureModel()

    def echo_context(prompt, images, system=None):
        payload = json.loads(prompt)
        prompts.append(payload)
        invoice = extracted()
        if len(prompts) == 1 or not correct_on_retry:
            invoice["subtotal"] = invoice["invoiceTotal"] = "601.00"
        if "validationFeedback" in payload:
            invoice["validationFeedback"] = payload["validationFeedback"]
        return json.dumps(invoice)

    monkeypatch.setattr(model, "complete_vision", echo_context)
    written = {}
    batch = extraction.extract_adf_pdfs(
        packages=[package], intake=intake, provider=model, run_uri="memory:run",
        read=lambda uri: data, write=lambda uri, value: written.update({uri: value}),
        allow_provisional_recommendations=True, corroborate_archive_currency=True,
        render=fake_render,
    )
    assert len(prompts) == len(batch.calls) == 2
    assert batch.calls[0]["validationError"] == "Extracted line sum does not reconcile to subtotal."
    assert bool(batch.documents[package.package_id]["blockingReviewReasons"]) != correct_on_retry
    assert json.loads(batch.calls[-1]["response"])["subtotal"] == (
        "600.00" if correct_on_retry else "601.00"
    )


def test_conditional_demo_rejects_below_threshold_capex_without_publishing_success(monkeypatch):
    use_mock_render(monkeypatch)
    model = ConditionalModel()
    complete = model.complete

    def invalid_capex(prompt, system=None):
        response = json.loads(complete(prompt, system))
        if not prompt.startswith("Invoice request JSON:") and json.loads(prompt)["stage"] == "ACCOUNTING_DECISION":
            response["items"][0]["recommendation"] = "CAPEX"
        return json.dumps(response)

    monkeypatch.setattr(model, "complete", invalid_capex)
    written = {}
    with pytest.raises(ValueError, match="complete three-stage"):
        run(
            model, written, fresh_pdf_extraction=True,
            allow_provisional_pdf_recommendations=True, demo_recommendations=True,
        )
    assert not any(uri.endswith("/batch.json") for uri in written)
    evidence = next(value for uri, value in written.items() if uri.endswith("/llm-evidence.json"))
    assert any("CAPEX violates" in (call["validationError"] or "") for call in evidence["calls"])


def test_conditional_demo_requires_explicit_pdf_opt_in_before_reading_sources():
    written = {}
    with pytest.raises(ValueError, match="fresh extraction and provisional-demo"):
        run(ConditionalModel(), written, fresh_pdf_extraction=True, demo_recommendations=True)
    assert not written


def use_mock_render(monkeypatch):
    original = extraction.extract_adf_pdfs

    def extract(**kwargs):
        return original(**kwargs, render=fake_render)

    monkeypatch.setattr(extraction, "extract_adf_pdfs", extract)


def test_fresh_pdf_extraction_retains_every_source_line_and_review(tmp_path, monkeypatch):
    use_mock_render(monkeypatch)
    written = {}
    summary = run(FixtureModel(), written, fresh_pdf_extraction=True)
    assert summary["freshPdfExtraction"]
    assert summary["lineCount"] == 10
    assert summary["preprocessedLineCount"] == 30
    assert summary["taskSuggestionCount"] == 2
    assert summary["assetClassSuggestionCount"] == 8
    assert summary["assessmentCount"] == 10
    assert summary["modelCallsAttempted"] == 8
    result = DemoWorkflowResult.model_validate(written[summary["resultUri"]])
    assert len(result.packages) == 10
    assert len([a for a in result.assessments if not a.used_llm]) == 8
    assert {a.bundling_disposition.value for a in result.assessments if not a.used_llm} == {"REVIEW"}
    assert len([uri for uri in written if "/extraction/" in uri]) == 2
    assert list(written)[-1].endswith("/batch.json")


def test_sql_commit_is_required_before_completion_and_original_review_imports(tmp_path, monkeypatch):
    use_mock_render(monkeypatch)
    monkeypatch.setattr(sql, "load_adf_task_master", lambda connection, expected: expected)
    monkeypatch.setattr(sql, "load_adf_asset_class_master", lambda connection, expected: expected)
    observed = {}

    def persist(connection, **kwargs):
        assert not any(uri.endswith("/batch.json") for uri in written)
        store = ReviewStore(tmp_path / "review.db")
        try:
            assert store.import_result(kwargs["review"], read=read_fixture)
            assert not store.import_result(kwargs["review"], read=read_fixture)
            saved = store.result(kwargs["result"].workflow_run_id)
            assert not saved["synthetic"]
            assert len(saved["documents"]) == 10
            assert sum(bool(doc["document"].get("archiveMember")) for doc in saved["documents"]) == 2
            first = saved["documents"][0]["document"]["documentId"]
            assert store.document(saved["workflowRunId"], first)
            decision = ReviewDecision.model_validate({
                "documentId": first, "action": "DEFER", "expectedVersion": 0,
                "actor": "mock-analyst", "reason": "Mock-only review test",
            })
            assert store.decide(saved["workflowRunId"], decision)["status"] == "DEFERRED"
        finally:
            store.close()
        observed.update(kwargs)
        return {"taskSuggestionRows": 10, "assessmentRows": 10, "reviewImports": 1}

    monkeypatch.setattr(sql, "persist_adf_results", persist)
    written = {}
    summary = run(FixtureModel(), written, fresh_pdf_extraction=True, connection=object())
    assert summary["reviewStatus"] == "AWAITING_ANALYST"
    assert summary["taskMasterSource"] == "pub.v1_TaskMaster"
    assert summary["sqlWrites"] == 21
    assert observed["workflow"].evidence(observed["model_result"])["provenance"].startswith("ORIGINAL_SOURCE")


def test_sql_failure_never_publishes_completion(monkeypatch):
    use_mock_render(monkeypatch)
    monkeypatch.setattr(sql, "load_adf_task_master", lambda connection, expected: expected)
    monkeypatch.setattr(sql, "load_adf_asset_class_master", lambda connection, expected: expected)

    def fail(*args, **kwargs):
        raise ValueError("MOCK SQL rollback")

    monkeypatch.setattr(sql, "persist_adf_results", fail)
    written = {}
    with pytest.raises(ValueError, match="MOCK SQL rollback"):
        run(FixtureModel(), written, fresh_pdf_extraction=True, connection=object())
    assert any(uri.endswith("/sql-failure.json") for uri in written)
    assert not any(uri.endswith("/batch.json") for uri in written)


def test_invalid_extractions_route_all_lines_to_review_without_accounting_calls(monkeypatch):
    use_mock_render(monkeypatch)
    invoices = [extracted(1), extracted(2)]
    for invoice in invoices:
        invoice["headerConfidence"] = 0.1
    model = FixtureModel(extractions=invoices)
    written = {}
    summary = run(model, written, fresh_pdf_extraction=True)
    assert model.text_calls == 1
    assert {call["stage"] for call in written[summary["evidenceUri"]]["calls"]} == {"POTENTIAL_TAGS"}
    assert summary["modelPackageCount"] == 0
    assert summary["referenceExceptionPackageCount"] == 10
    assert summary["assessmentCount"] == 30
    result = written[summary["resultUri"]]
    assert all(not a["usedLlm"] and a["recommendation"] == "REVIEW" for a in result["assessments"])


@pytest.mark.parametrize("allow_provisional", [False, True])
def test_incomplete_details_need_explicit_opt_in_and_remain_in_analyst_handoff(monkeypatch, allow_provisional):
    use_mock_render(monkeypatch)
    monkeypatch.setattr(sql, "load_adf_task_master", lambda connection, expected: expected)
    monkeypatch.setattr(sql, "load_adf_asset_class_master", lambda connection, expected: expected)
    captured = {}

    def persist(connection, **kwargs):
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(sql, "persist_adf_results", persist)
    invoices = [extracted(1), extracted(2)]
    for invoice in invoices:
        invoice["invoiceDate"] = None
        invoice["freight"] = None
        invoice["references"] = [ref for ref in invoice["references"] if ref["field"] != "invoiceDate"]
    model = FixtureModel(extractions=invoices)
    written = {}
    summary = run(
        model, written, connection=object(), fresh_pdf_extraction=True,
        allow_provisional_pdf_recommendations=allow_provisional,
    )
    assert summary["modelPackageCount"] == (2 if allow_provisional else 0)
    assert model.text_calls == (6 if allow_provisional else 1)
    assert summary["provisionalPackageCount"] == (2 if allow_provisional else 0)
    assert summary["status"] == "REVIEW_REQUIRED"
    assert not summary["humanApproval"] and summary["erpWrites"] == 0
    review = captured["review"]
    assert review["reviewStatus"] == "AWAITING_ANALYST" and not review["businessApproved"]
    pdf_docs = [doc for doc in review["documents"] if doc["document"]["mediaType"] == "application/pdf"]
    assert len(pdf_docs) == 2
    for document in pdf_docs:
        assert document["extraction"]["invoiceDate"] is None
        assert document["extraction"]["freight"] is None
        assert any("invoice_date" in reason for reason in document["reviewReasons"])
        eligibility = document["extraction"]["recommendationEligibility"]
        assert eligibility["requiresAnalystReview"]
        assert bool(eligibility["blockingReviewReasons"]) is not allow_provisional
    if allow_provisional:
        evidence = written[summary["evidenceUri"]]
        assert {call["stage"] for call in evidence["calls"]} == {
            "TASK_SUGGESTION", "ACCOUNTING_DECISION", "BUNDLE_DECISION", "POTENTIAL_TAGS",
        }


@pytest.mark.parametrize("failure", ["confidence", "currency", "amount", "page", "date"])
def test_provisional_mode_does_not_bypass_hard_extraction_failures(monkeypatch, failure):
    use_mock_render(monkeypatch)
    invoices = [extracted(1), extracted(2)]
    for invoice in invoices:
        if failure == "confidence":
            invoice["headerConfidence"] = 0.1
        elif failure == "currency":
            invoice["currency"] = None
        elif failure == "amount":
            invoice["lines"][0]["amount"] = "599"
        elif failure == "page":
            invoice["references"][0]["page"] = 2
        else:
            invoice["invoiceDate"] = "2026-99-99"
    model = FixtureModel(extractions=invoices)
    written = {}
    summary = run(
        model, written, fresh_pdf_extraction=True, allow_provisional_pdf_recommendations=True,
    )
    assert model.text_calls == 1
    assert summary["modelPackageCount"] == summary["provisionalPackageCount"] == 0
    assert {call["stage"] for call in written[summary["evidenceUri"]]["calls"]} == {"POTENTIAL_TAGS"}
    assert all(not item["usedLlm"] for item in written[summary["resultUri"]]["assessments"])


def test_provisional_mode_requires_fresh_pdf_extraction_before_reading_sources():
    model = FixtureModel()
    written = {}
    with pytest.raises(ValueError, match="require fresh PDF extraction"):
        run(model, written, allow_provisional_pdf_recommendations=True)
    assert not written and model.text_calls == model.vision_calls == 0


def test_malformed_extraction_is_audited_and_stops_before_completion(monkeypatch):
    use_mock_render(monkeypatch)
    model = FixtureModel()
    monkeypatch.setattr(model, "complete_vision", lambda *args, **kwargs: json.dumps({"invalid": True}))
    written = {}
    with pytest.raises(ValueError):
        run(model, written, fresh_pdf_extraction=True)
    calls = [value for uri, value in written.items() if "/extraction/" in uri]
    assert len(calls) == 1 and calls[0]["error"]
    assert not any(uri.endswith("/batch.json") for uri in written)


def test_pdf_render_failure_is_audited_without_any_model_calls(monkeypatch):
    original = extraction.extract_adf_pdfs

    def fail_render(data):
        raise RuntimeError("MOCK_RENDER_FAILURE")

    monkeypatch.setattr(extraction, "extract_adf_pdfs",
                        lambda **kwargs: original(**kwargs, render=fail_render))
    model = FixtureModel()
    written = {}
    with pytest.raises(RuntimeError, match="MOCK_RENDER_FAILURE"):
        run(model, written, fresh_pdf_extraction=True)
    calls = [value for uri, value in written.items() if "/extraction/" in uri]
    assert len(calls) == 1 and calls[0]["error"] == "RuntimeError: MOCK_RENDER_FAILURE"
    assert model.vision_calls == model.text_calls == 0
    assert not any(uri.endswith("/batch.json") for uri in written)


def test_real_source_pdf_members_render_locally_before_mock_model_extraction():
    model = FixtureModel()
    written = {}
    summary = run(model, written, fresh_pdf_extraction=True)
    calls = [value for uri, value in written.items() if "/extraction/" in uri]
    assert len(calls) == model.vision_calls == 2
    assert all(call["pageCount"] >= 1 and call["archiveMember"].endswith(".pdf") for call in calls)
    assert summary["modelCallsAttempted"] == 8
