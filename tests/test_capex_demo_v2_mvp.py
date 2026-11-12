from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from autarch.capex import demo_v2_mvp as mvp
from autarch.capex.demo_v2_reference import DemoPolicySnapshot, TaskMasterSnapshot
from autarch.capex.demo_v2_review import ReviewDecision, ReviewStore, make_handler, review_page
from autarch.errors import ModelError
from autarch.intelligence.base import ModelProvider
from autarch.intelligence.vision import ImageRef


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "examples" / "data" / "capex"


def extracted(number=1):
    return {
        "invoiceNumber": f"TEST-INVOICE-{number}", "invoiceDate": "2026-11-09",
        "vendorName": f"Synthetic test vendor {number}", "currency": "USD",
        "invoiceTotal": "600.00", "subtotal": "600.00", "tax": "0", "freight": "0", "discount": "0",
        "poNumber": f"TEST-PO-{number}", "headerConfidence": 0.99,
        "lines": [{"lineNumber": 1, "description": "Wire shelving fixture WS-1 component; not standalone",
                   "quantity": "1", "unitCost": "600", "amount": "600", "confidence": 0.99,
                   "sourceTaskCode": "5225"}],
        "references": [{"field": field, "page": 1, "quote": "Test-only visible evidence"}
                       for field in ("invoiceNumber", "invoiceDate", "vendorName", "currency", "invoiceTotal",
                                     "lines.1", "lines.1.sourceTaskCode")],
    }


class FixtureModel(ModelProvider):
    name = "MOCK_TEST_ONLY"

    def __init__(self, extractions=None, fail=False, bad_bundle_sum=False):
        self.extractions = extractions or [extracted(1), extracted(2)]
        self.vision_calls = 0
        self.text_calls = 0
        self.fail = fail
        self.bad_bundle_sum = bad_bundle_sum

    def supports_vision(self):
        return True

    def complete_vision(self, prompt, images, system=None):
        assert images
        assert "reference" in prompt.lower()
        answer = self.extractions[self.vision_calls]
        self.vision_calls += 1
        return json.dumps(answer)

    def complete(self, prompt, system=None):
        self.text_calls += 1
        if self.fail:
            raise ModelError("Controlled transport failure.")
        if prompt.startswith("Invoice request JSON:"):
            request = json.loads(prompt.split("\n", 1)[1].split("\n\nReturn JSON")[0])
            return json.dumps({"lines": [{
                "lineId": line["lineId"], "resolutionStatus": "RESOLVED",
                "taskCode": "5225", "confidence": 0.95, "unresolvedReason": None,
                "rationale": "MOCK TEST ONLY: supplied shelving task.",
                "evidenceRefs": [f"line:{line['lineId']}", "task:5225"],
            } for line in request["lines"]]})
        request = json.loads(prompt)
        if request["stage"] == "POTENTIAL_TAGS":
            return json.dumps({"lines": [{
                "packageId": package["packageId"], "lineId": line["lineId"], "potentialTags": [],
            } for package in request["packages"] for line in package["lines"]]})
        if request["stage"] == "ACCOUNTING_DECISION":
            return json.dumps({"items": [{
                "lineId": line["lineId"], "natureResult": "CAPITALIZABLE",
                "recommendation": "OPEX", "repairMaintenanceIndicator": False,
                "neverCapitalizeHit": False, "rationale": "MOCK TEST ONLY: below unit threshold.",
                "policyCitations": request["allowedPolicyCitations"], "missingInputs": [], "reviewReasons": [],
            } for line in request["processingPackage"]["lines"]]})
        members = request["eligibleBundleMembers"]
        groups = []
        if len(members) >= 2:
            groups = [{
                "proposalId": "mock-shared-asset", "members": [{
                    key: member[key] for key in ("packageId", "invoiceId", "lineId")
                } for member in members],
                "combinedValue": sum(member["lineAmount"] for member in members) + int(self.bad_bundle_sum),
                "isOneAssetSuggestion": True, "rationale": "MOCK TEST ONLY: one fixture.",
                "policyCitations": request["assessments"][0]["policyCitations"],
            }]
        return json.dumps({
            "items": [{
                **line, "decision": "BUNDLE" if groups else "NO_BUNDLE",
                "proposalId": "mock-shared-asset" if groups else None, "confidence": 0.95,
                "rationale": "MOCK TEST ONLY: supplied assembly identity.",
                "evidenceRefs": [f"line:{line['packageId']}:{line['lineId']}"],
            } for line in request["decisionLineCatalog"]],
            "groups": groups,
        })


@pytest.fixture
def manifest():
    master = TaskMasterSnapshot.model_validate_json(
        (DATA / "reference-v2" / "task-master-us-v2.json").read_text(),
    )
    policy = DemoPolicySnapshot.model_validate_json(
        (DATA / "reference-v2" / "policy-config-v2.json").read_text(),
    )
    documents = [{
        "documentId": f"TEST-{index}", "uri": f"test-{index}.pdf",
        "sha256": hashlib.sha256(pdf_bytes(f"test-{index}.pdf")).hexdigest(), "countryRegion": "US",
        "context": {"overlayVersion": "SYNTHETIC_TEST_ONLY", "targetInvoiceId": f"TEST-{index}",
                    "siteId": "DEMO-SITE-1", "projectId": "DEMO-PROJECT-1",
                    "projectStatus": "OPEN", "bundlingInScope": True},
    } for index in (1, 2)]
    return mvp.MvpManifest(
        workflow_run_id="TEST-MVP-NOT-LIVE", model_deployment="gpt-5.1",
        task_master=master, policies={"USD": policy}, documents=documents,
    )


def pdf_bytes(uri):
    return b"%PDF-1.7\n" + uri.encode()


def fake_render(data):
    return [ImageRef.from_bytes(b"TEST_IMAGE_NOT_REAL")]


def run(manifest, tmp_path, model=None):
    state = mvp.BatchState(tmp_path / "batch.sqlite")
    try:
        return mvp.run_batch(manifest, model or FixtureModel(), state, read=pdf_bytes, render=fake_render)
    finally:
        state.close()


def test_extraction_preserves_missing_values_and_decimal_checks():
    item = mvp.ExtractedInvoice.model_validate(extracted())
    assert mvp.validate_extraction(item, 1) == []
    item = item.model_copy(update={"tax": None})
    assert any("adjustments" in reason for reason in mvp.validate_extraction(item, 1))
    assert item.tax is None


def test_provisional_validation_preserves_missing_service_details_without_inventing_values():
    payload = extracted()
    payload["invoiceDate"] = None
    payload["tax"] = None
    payload["lines"][0]["quantity"] = None
    payload["lines"][0]["unitCost"] = None
    payload["references"] = [ref for ref in payload["references"] if ref["field"] != "invoiceDate"]
    invoice = mvp.ExtractedInvoice.model_validate(payload)
    assert mvp.validate_extraction(invoice, 1)
    assert mvp.validate_extraction(invoice, 1, allow_incomplete_details=True) == []
    assert invoice.invoice_date is invoice.tax is invoice.lines[0].quantity is invoice.lines[0].unit_cost is None


@pytest.mark.parametrize("change,reason", [
    ({"invoiceTotal": "601"}, "invoice total"),
    ({"subtotal": "601"}, "subtotal"),
    ({"invoiceDate": "2026-02-31"}, "date"),
    ({"headerConfidence": 0.5}, "confidence"),
    ({"invoiceNumber": None}, "required header"),
    ({"references": [{"field": "invoiceNumber", "page": 5, "quote": "outside"}]}, "beyond"),
])
def test_extraction_failures_are_explicit_review_reasons(change, reason):
    payload = {**extracted(), **change}
    invoice = mvp.ExtractedInvoice.model_validate(payload)
    assert any(reason in value for value in mvp.validate_extraction(invoice, 1))


@pytest.mark.parametrize("cost", [None, "599"])
def test_missing_or_unreconciled_unit_cost_is_not_filled(cost):
    payload = extracted()
    payload["lines"][0]["unitCost"] = cost
    invoice = mvp.ExtractedInvoice.model_validate(payload)
    assert mvp.validate_extraction(invoice, 1)
    assert invoice.lines[0].unit_cost is None if cost is None else str(invoice.lines[0].unit_cost) == cost


def test_nonfinite_extraction_and_duplicate_line_numbers_are_rejected():
    payload = extracted()
    payload["lines"][0]["quantity"] = "NaN"
    with pytest.raises(ValueError):
        mvp.ExtractedInvoice.model_validate(payload)
    payload = extracted()
    payload["lines"].append(copy.deepcopy(payload["lines"][0]))
    with pytest.raises(ValueError, match="consecutively"):
        mvp.ExtractedInvoice.model_validate(payload)


def test_primary_partition_is_site_project_not_po_vendor_or_task(manifest, tmp_path):
    result = run(manifest, tmp_path)
    assert result["status"] == "SUCCEEDED"
    assert result["primaryPartitionKey"] == ["SITE_ID", "PROJECT_ID"]
    assert len(result["partitions"]) == 1
    partition = result["partitions"][0]
    assert len(partition["result"]["taskDeterminations"]) == 2
    assert len(partition["result"]["assessments"]) == 2
    assert len(partition["result"]["bundleGroups"]) == 1
    assert partition["result"]["bundleGroups"][0]["totalAmount"] == 1200
    assert result["sqlWrites"] == result["popaWrites"] == 0
    assert result["llmCallsAttempted"] == 7
    assert result["candidateAssetStatus"] == "NOT_AVAILABLE_NO_ASSET_REGISTER_SUPPLIED"


def test_distinct_site_projects_never_share_an_assessment_partition(manifest, tmp_path):
    payload = manifest.model_dump(mode="json", by_alias=True)
    payload["documents"][1]["context"]["projectId"] = "DEMO-PROJECT-2"
    result = run(mvp.MvpManifest.model_validate(payload), tmp_path)
    assert len(result["partitions"]) == 2
    assert all(not partition["result"]["bundleGroups"] for partition in result["partitions"])


def test_extraction_exception_is_saved_without_assessment_calls(manifest, tmp_path):
    first, second = extracted(1), extracted(2)
    first["headerConfidence"] = second["headerConfidence"] = 0.2
    model = FixtureModel([first, second])
    result = run(manifest, tmp_path, model)
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["exceptionDocumentCount"] == 2
    assert result["partitions"] == []
    assert model.text_calls == 0


def test_duplicate_invoice_identity_quarantines_all_copies(manifest, tmp_path):
    result = run(manifest, tmp_path, FixtureModel([extracted(1), extracted(1)]))
    assert result["exceptionDocumentCount"] == 2
    assert result["partitions"] == []
    assert all(any("Duplicate" in reason for reason in doc["reviewReasons"]) for doc in result["documents"])


def test_no_site_or_project_is_not_guessed(manifest, tmp_path):
    payload = manifest.model_dump(mode="json", by_alias=True)
    payload["documents"][0]["context"] = None
    result = run(mvp.MvpManifest.model_validate(payload), tmp_path)
    assert result["exceptionDocumentCount"] == 1
    assert len(result["partitions"][0]["result"]["assessments"]) == 1


def test_source_hash_mismatch_fails_before_model_call(manifest, tmp_path):
    model = FixtureModel()
    state = mvp.BatchState(tmp_path / "batch.sqlite")
    try:
        with pytest.raises(ValueError, match="SHA-256"):
            mvp.run_batch(manifest, model, state, read=lambda _: b"changed", render=fake_render)
        assert model.vision_calls == 0
        assert state.connection.execute("SELECT status FROM runs").fetchone()[0] == "FAILED"
    finally:
        state.close()


def test_resume_reuses_successful_extraction_and_full_partition_checkpoints(manifest, tmp_path):
    state = mvp.BatchState(tmp_path / "batch.sqlite")
    try:
        with pytest.raises(ValueError, match="partition failed"):
            mvp.run_batch(manifest, FixtureModel(fail=True), state, read=pdf_bytes, render=fake_render)
        recovered = FixtureModel()
        result = mvp.run_batch(manifest, recovered, state, resume=True, read=pdf_bytes, render=fake_render)
        assert recovered.vision_calls == 0
        assert result["status"] == "SUCCEEDED"
        no_calls = FixtureModel(fail=True)
        replay = mvp.run_batch(manifest, no_calls, state, resume=True, read=pdf_bytes, render=fake_render)
        assert no_calls.vision_calls == no_calls.text_calls == 0
        assert replay == result
        with pytest.raises(ValueError, match="exact original"):
            changed = manifest.model_copy(update={"model_deployment": "changed"})
            mvp.run_batch(changed, no_calls, state, resume=True, read=pdf_bytes, render=fake_render)
    finally:
        state.close()


def test_invalid_bundle_math_is_rejected_not_silently_recalculated(manifest, tmp_path):
    with pytest.raises(ValueError, match="partition failed"):
        run(manifest, tmp_path, FixtureModel(bad_bundle_sum=True))


@pytest.mark.parametrize("missing", [False, True])
def test_opt_in_policy_review_can_propose_bundle_but_missing_facts_cannot(manifest, tmp_path, missing):
    class PolicyReviewModel(FixtureModel):
        def complete(self, prompt, system=None):
            response = super().complete(prompt, system)
            if not prompt.startswith("Invoice request JSON:"):
                request = json.loads(prompt)
                if request["stage"] == "ACCOUNTING_DECISION":
                    payload = json.loads(response)
                    for item in payload["items"]:
                        item["recommendation"] = "REVIEW"
                        item["reviewReasons"] = ["Unapproved policy requires analyst review."]
                        item["missingInputs"] = ["asset attribution"] if missing else []
                    return json.dumps(payload)
            return response

    result = run(manifest, tmp_path, PolicyReviewModel())
    partition = result["partitions"][0]
    assert len(partition["result"]["bundleGroups"]) == (0 if missing else 1)
    assert all(item["recommendation"] == "REVIEW" for item in partition["result"]["assessments"])
    assert all(item["reviewReasons"] for item in partition["result"]["assessments"])


def test_review_import_is_separate_idempotent_and_rejects_mutated_run(manifest, tmp_path):
    result = run(manifest, tmp_path)
    store = ReviewStore(tmp_path / "review.sqlite")
    try:
        assert store.import_result(result, read=pdf_bytes)
        assert not store.import_result(result, read=pdf_bytes)
        changed = copy.deepcopy(result)
        changed["status"] = "REVIEW_REQUIRED"
        with pytest.raises(ValueError, match="different payload"):
            store.import_result(changed, read=pdf_bytes)
        assert store.connection.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 1
    finally:
        store.close()


def test_analyst_correction_is_versioned_never_rewrites_recommendation_or_trains_model(manifest, tmp_path):
    result = run(manifest, tmp_path)
    store = ReviewStore(tmp_path / "review.sqlite")
    try:
        store.import_result(result, read=pdf_bytes)
        correction = ReviewDecision(
            document_id="TEST-1", expected_version=0, actor="Synthetic demo analyst",
            action="CORRECT", reason="Controlled test correction.", corrections={"invoiceNumber": "TEST-CORRECTED"},
        )
        outcome = store.decide(manifest.workflow_run_id, correction)
        assert outcome["status"] == "NEEDS_REPROCESSING"
        with pytest.raises(ValueError, match="Stale review"):
            store.decide(manifest.workflow_run_id, correction)
        defer = correction.model_copy(update={"action": "DEFER", "corrections": {}, "expected_version": 1})
        store.decide(manifest.workflow_run_id, defer)
        history = store.history(manifest.workflow_run_id, "TEST-1")
        assert history[1]["previousSha256"] == history[0]["sha256"]
        assert store.result(manifest.workflow_run_id) == result
        assert store.result(manifest.workflow_run_id)["historyUsedForInference"] is False
        confirm = correction.model_copy(update={
            "action": "CONFIRM", "corrections": {}, "expected_version": 2, "simulated_popa_completed": True,
        })
        with pytest.raises(ValueError, match="new processing run"):
            store.decide(manifest.workflow_run_id, confirm)
    finally:
        store.close()


def test_confirmation_requires_explicit_popa_simulation_and_valid_recommendations(manifest, tmp_path):
    with pytest.raises(ValueError, match="POPA"):
        ReviewDecision(document_id="TEST-1", expected_version=0, actor="Test",
                       action="CONFIRM", reason="Testing.")
    result = run(manifest, tmp_path)
    store = ReviewStore(tmp_path / "review.sqlite")
    try:
        store.import_result(result, read=pdf_bytes)
        outcome = store.decide(manifest.workflow_run_id, ReviewDecision(
            document_id="TEST-1", expected_version=0, actor="Test", action="CONFIRM",
            reason="Synthetic demonstration only.", simulated_popa_completed=True,
        ))
        assert outcome["status"] == "CONFIRMED_DEMO_ONLY"
    finally:
        store.close()


def test_unknown_task_corrections_and_exception_confirmations_fail_closed(manifest, tmp_path):
    first, second = extracted(1), extracted(2)
    first["headerConfidence"] = 0.2
    result = run(manifest, tmp_path, FixtureModel([first, second]))
    store = ReviewStore(tmp_path / "review.sqlite")
    try:
        store.import_result(result, read=pdf_bytes)
        with pytest.raises(ValueError, match="exceptions"):
            store.decide(manifest.workflow_run_id, ReviewDecision(
                document_id="TEST-1", expected_version=0, actor="Test", action="CONFIRM",
                reason="Synthetic test.", simulated_popa_completed=True,
            ))
        with pytest.raises(ValueError, match="active TaskMaster"):
            store.decide(manifest.workflow_run_id, ReviewDecision(
                document_id="TEST-1", expected_version=0, actor="Test", action="CORRECT",
                reason="Synthetic test.", corrections={"analystTaskCode": "INVENTED"},
            ))
    finally:
        store.close()


def test_review_page_discloses_prototype_and_escapes_invoice_content(manifest, tmp_path):
    payload = extracted(1)
    payload["vendorName"] = "<script>alert('test')</script>"
    result = run(manifest, tmp_path, FixtureModel([payload, extracted(2)]))
    store = ReviewStore(tmp_path / "review.sqlite")
    try:
        store.import_result(result, read=pdf_bytes)
        page = review_page(store, manifest.workflow_run_id, "TEST-CSRF-TOKEN")
        assert "<script>" not in page
        assert "&lt;script&gt;" in page
        assert "LOCAL DEMO PROTOTYPE" in page
        assert "not authenticated" in page
        assert "TEST-CSRF-TOKEN" in page
        assert "not a physical asset inventory" in page
    finally:
        store.close()


def test_real_source_manifest_and_rendering_do_not_make_model_calls():
    manifest = mvp.prepare_manifest(DATA, "TEST-PREPARE-ONLY", "gpt-5.1")
    assert len(manifest.documents) == 2
    for doc in manifest.documents:
        data = Path(doc.uri).read_bytes()
        assert hashlib.sha256(data).hexdigest() == doc.sha256
        pages = mvp.render_pdf(data)
        assert pages
        assert all(image.data.startswith(b"\x89PNG") for image in pages)


def test_synthetic_positive_fixture_is_explicit_separate_and_never_overwritten(tmp_path):
    manifest = mvp.prepare_synthetic_case(DATA, tmp_path, "TEST-POSITIVE-CONTROL", "gpt-5.1")
    assert all(doc.document_id.startswith("SYNTHETIC-") for doc in manifest.documents)
    assert len({doc.context.project_id for doc in manifest.documents}) == 1
    assert all(mvp.render_pdf(Path(doc.uri).read_bytes()) for doc in manifest.documents)
    with pytest.raises(FileExistsError):
        mvp.prepare_synthetic_case(DATA, tmp_path, "DIFFERENT-RUN", "gpt-5.1")


def test_review_http_form_submission_source_and_cross_origin_rejection(manifest, tmp_path):
    result = run(manifest, tmp_path)
    store = ReviewStore(tmp_path / "review.sqlite")
    store.import_result(result, read=pdf_bytes)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store, manifest.workflow_run_id, 0))
    port = server.server_address[1]
    server.RequestHandlerClass = make_handler(store, manifest.workflow_run_id, port)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    try:
        with urlopen(url) as response:
            page = response.read().decode()
            assert response.headers["Referrer-Policy"] == "same-origin"
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        form = {
            "csrf": token, "run": manifest.workflow_run_id, "document_id": "TEST-1",
            "expected_version": 0, "actor": "Synthetic HTTP test analyst",
            "reason": "Controlled UI test.", "action": "CORRECT",
            "correction_field": "invoiceNumber", "correction_value": "TEST-CORRECTED",
        }
        body = urlencode(form).encode()
        request = Request(url + "/decision", data=body, headers={"Origin": url})
        with urlopen(request) as response:
            assert response.status == 200
            assert b"NEEDS_REPROCESSING" in response.read()
        assert len(store.history(manifest.workflow_run_id, "TEST-1")) == 1
        with urlopen(url) as response:
            assert b"TEST-CORRECTED" in response.read()
        with pytest.raises(HTTPError) as stale:
            urlopen(request)
        assert stale.value.code == 400
        cross_origin = Request(url + "/decision", data=body, headers={"Origin": "https://example.invalid"})
        with pytest.raises(HTTPError) as forbidden:
            urlopen(cross_origin)
        assert forbidden.value.code == 403
        invalid_token = Request(url + "/decision", data=urlencode({**form, "csrf": "invalid"}).encode(),
                                headers={"Origin": url})
        with pytest.raises(HTTPError) as bad_token:
            urlopen(invalid_token)
        assert bad_token.value.code == 403
        with urlopen(url + "/document?" + urlencode({"run": manifest.workflow_run_id, "id": "TEST-1"})) as response:
            assert response.headers["Content-Type"] == "application/pdf"
            assert response.read() == pdf_bytes("test-1.pdf")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        store.close()
