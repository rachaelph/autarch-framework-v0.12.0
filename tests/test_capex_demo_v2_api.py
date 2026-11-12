from __future__ import annotations

import base64
import copy
import json
import sqlite3
import time
import socket
import threading
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from autarch.capex import demo_v2_mvp as mvp
from autarch.capex.demo_v2_api import EntraVerifier, create_app
from autarch.capex.demo_v2_reference import canonical_sha256
from autarch.capex.demo_v2_remote import RemoteAgent, RemoteServiceError, ServiceClient
from autarch.capex.demo_v2_review import ReviewDecision, ReviewStore, SqlReviewStore
from autarch.capex.demo_v2_store import SqlSession
from autarch.intelligence.vision import ImageRef
from tests.test_capex_demo_v2_mvp import FixtureModel, extracted, manifest, pdf_bytes, run


TENANT = "11111111-1111-4111-8111-111111111111"
AUDIENCE = "22222222-2222-4222-8222-222222222222"
OBJECT = "33333333-3333-4333-8333-333333333333"
ROLES = ["Capex.Agent.Invoke", "Capex.Reference.Read", "Capex.Review.Import",
         "Capex.Review.Read", "Capex.Review.Decide"]


@pytest.fixture
def identity():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = EntraVerifier(TENANT, AUDIENCE)
    verifier.keys.get_signing_key_from_jwt = lambda token: SimpleNamespace(key=key.public_key())

    def token(**changes):
        now = int(time.time())
        claims = {
            "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": AUDIENCE,
            "tid": TENANT, "oid": OBJECT, "iat": now, "nbf": now - 1, "exp": now + 300,
            "roles": ROLES,
        }
        claims.update(changes)
        return jwt.encode(claims, key, algorithm="RS256")
    return verifier, token


def headers(token):
    return {"Authorization": "Bearer " + token}


@pytest.mark.parametrize("changes", [
    {"aud": "wrong"}, {"iss": "https://attacker.invalid"}, {"tid": AUDIENCE},
    {"exp": 1}, {"nbf": int(time.time()) + 3600}, {"oid": "not-an-object-id"}, {"roles": "admin"},
])
def test_entra_rejects_bad_claims(identity, manifest, changes):
    verify, token = identity
    with TestClient(create_app("reference", verifier=verify, reference=manifest)) as client:
        assert client.get("/v1/references", headers=headers(token(**changes))).status_code == 401


def test_entra_signature_and_role_enforcement_and_service_isolation(identity, manifest):
    verify, token = identity
    with TestClient(create_app("reference", verifier=verify, reference=manifest)) as client:
        assert client.get("/v1/references").status_code == 401
        assert client.get("/v1/references", headers=headers(token(roles=["Capex.Review.Read"]))).status_code == 403
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        forged = jwt.encode(jwt.decode(token(), options={"verify_signature": False}), other_key, algorithm="RS256")
        assert client.get("/v1/references", headers=headers(forged)).status_code == 401
        result = client.get("/v1/references", headers=headers(token())).json()
        assert result["existingAssetIdsAvailable"] is False
        assert result["businessApproved"] is False
        assert result["taskMasterSha256"] == canonical_sha256(result["taskMaster"])
        assert client.post("/v1/review/imports", headers=headers(token()), json={}).status_code == 404
        assert client.get("/health/live").status_code == 200
    with pytest.raises(ValueError, match="transactional store"):
        create_app("agent", verifier=verify, provider=FixtureModel(), reference=manifest,
                   store_factory=lambda: ReviewStore(":memory:"))


def test_reference_mismatch_is_not_silently_accepted(identity, manifest):
    verify, token = identity
    with TestClient(create_app("reference", verifier=verify, reference=manifest)) as http:
        client = BridgeClient(http, token())
        client.verify_references(manifest)
        changed = manifest.model_copy(deep=True)
        changed.task_master.snapshot_id = "DIFFERENT-SNAPSHOT"
        with pytest.raises(ValueError, match="differs"):
            client.verify_references(changed)


class BridgeClient(ServiceClient):
    """Test-only in-process HTTP transport; production still requires HTTPS/Entra."""

    def __init__(self, http, token):
        super().__init__("https://testserver", "api://" + AUDIENCE, lambda scope: token)
        self.http = http

    def request(self, path, payload=None):
        if payload is None:
            response = self.http.get(path, headers=headers(self.token(self.scope)))
        else:
            response = self.http.post(path, headers=headers(self.token(self.scope)), json=payload)
        if response.status_code >= 400:
            raise RemoteServiceError(response.status_code, response.json())
        return response.json()


def test_remote_batch_crosses_two_capabilities_without_store_access(identity, manifest, tmp_path):
    verify, token = identity
    model = FixtureModel()
    app = create_app("agent", verifier=verify, provider=model, reference=manifest)
    state = mvp.BatchState(tmp_path / "remote-batch.sqlite")
    with TestClient(app) as http:
        remote = RemoteAgent(BridgeClient(http, token()), manifest.model_deployment)
        try:
            result = mvp.run_batch(
                manifest, remote, state, read=pdf_bytes,
                render=lambda data: [ImageRef.from_bytes(b"\x89PNG\r\n\x1a\nTEST_ONLY")],
                assessment=remote.assess,
            )
        finally:
            state.close()
        assert result["status"] == "SUCCEEDED"
        assert len(result["partitions"][0]["result"]["bundleGroups"]) == 1
        assert result["llmCallsAttempted"] == 7
        assert model.vision_calls == 2 and model.text_calls == 5
        assert http.get("/v1/review/runs/test", headers=headers(token())).status_code == 404


def test_remote_failed_assessment_evidence_is_retained_by_batch(identity, manifest, tmp_path):
    verify, token = identity
    app = create_app("agent", verifier=verify, provider=FixtureModel(fail=True), reference=manifest)
    state = mvp.BatchState(tmp_path / "failed-remote.sqlite")
    try:
        with TestClient(app) as http:
            remote = RemoteAgent(BridgeClient(http, token()), manifest.model_deployment)
            with pytest.raises(RemoteServiceError):
                mvp.run_batch(manifest, remote, state, read=pdf_bytes,
                              render=lambda data: [ImageRef.from_bytes(b"\x89PNG\r\n\x1a\nTEST_ONLY")],
                              assessment=remote.assess)
        evidence = [item["payload"] for item in state.evidence(manifest.workflow_run_id)
                    if item["status"] == "ASSESSMENT_EVIDENCE"]
        assert len(evidence) == 1
        assert evidence[0]["status"] == "INCOMPLETE"
        assert evidence[0]["llmCallsAttempted"] >= 1
        assert evidence[0]["calls"][0]["error"]
        assert state.connection.execute("SELECT status FROM runs").fetchone()[0] == "FAILED"
    finally:
        state.close()


def test_remote_invalid_extraction_is_audited_before_contract_rejection(identity, manifest, tmp_path):
    class BadExtraction(FixtureModel):
        def complete_vision(self, prompt, images, system=None):
            return "INVALID_MODEL_JSON_TEST_ONLY"

    verify, token = identity
    app = create_app("agent", verifier=verify, provider=BadExtraction(), reference=manifest)
    state = mvp.BatchState(tmp_path / "invalid-extraction.sqlite")
    try:
        with TestClient(app) as http:
            remote = RemoteAgent(BridgeClient(http, token()), manifest.model_deployment)
            with pytest.raises(ValueError):
                mvp.run_batch(manifest, remote, state, read=pdf_bytes,
                              render=lambda data: [ImageRef.from_bytes(b"\x89PNG\r\n\x1a\nTEST_ONLY")],
                              assessment=remote.assess)
        responses = [item["payload"]["response"] for item in state.evidence(manifest.workflow_run_id)
                     if item["status"] == "RESPONSE_RECEIVED"]
        assert responses == ["INVALID_MODEL_JSON_TEST_ONLY"]
        assert state.connection.execute("SELECT status FROM runs").fetchone()[0] == "FAILED"
    finally:
        state.close()


def test_service_bounds_actual_body_not_just_content_length(identity, manifest, monkeypatch):
    from autarch.capex import demo_v2_api
    verify, token = identity
    monkeypatch.setattr(demo_v2_api, "MAX_BODY", 128)
    with TestClient(create_app("reference", verifier=verify, reference=manifest)) as http:
        assert http.post("/v1/references", content=b"x" * 129,
                         headers={**headers(token()), "Content-Length": "0"}).status_code == 413
        assert http.post("/v1/references", content=b"x",
                         headers={**headers(token()), "Content-Length": "invalid"}).status_code == 400


def test_live_socket_host_checks_signed_token_and_shuts_down(identity, manifest):
    import uvicorn
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

    verify, token = identity
    app = create_app("reference", verifier=verify, reference=manifest)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", proxy_headers=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        for _ in range(500):
            if server.started:
                break
            time.sleep(0.01)
        assert server.started
        origin = f"http://127.0.0.1:{port}"
        with urlopen(origin + "/health/live", timeout=5) as response:
            assert json.load(response)["status"] == "alive"
        with pytest.raises(HTTPError) as error:
            urlopen(origin + "/v1/references", timeout=5)
        assert error.value.code == 401
        with urlopen(Request(origin + "/v1/references", headers=headers(token())), timeout=5) as response:
            assert json.load(response)["existingAssetIdsAvailable"] is False
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()


def test_agent_rejects_external_page_urls_pinned_reference_drift_and_mixed_partition(identity, manifest, tmp_path):
    verify, token = identity
    model = FixtureModel()
    with TestClient(create_app("agent", verifier=verify, provider=model, reference=manifest)) as http:
        assert http.post("/v1/extractions", headers=headers(token()),
                         json={"pages": ["http://169.254.169.254/metadata"]}).status_code == 422
        body = {"manifest": manifest.model_dump(mode="json", by_alias=True),
                "requestedAtUtc": "2026-11-09T19:00:00Z",
                "documents": [{"document": doc.model_dump(mode="json", by_alias=True),
                               "extraction": extracted(index), "pageCount": 1}
                              for index, doc in enumerate(manifest.documents, 1)]}
        body["manifest"]["taskMaster"]["snapshotId"] = "DRIFT"
        assert http.post("/v1/assessments", headers=headers(token()), json=body).status_code == 422
        body["manifest"] = manifest.model_dump(mode="json", by_alias=True)
        body["manifest"]["documents"][1]["context"]["projectId"] = "OTHER"
        body["documents"][1]["document"] = body["manifest"]["documents"][1]
        assert http.post("/v1/assessments", headers=headers(token()), json=body).status_code == 422
        assert model.vision_calls == model.text_calls == 0


def test_exact_printed_task_bypass_is_opt_in_and_does_not_bypass_accounting(manifest, tmp_path):
    opted = manifest.model_copy(update={"taskmaster_bypass": True})
    model = FixtureModel()
    result = run(opted, tmp_path, model)
    assert result["llmCallsAttempted"] == 5
    assert model.text_calls == 3  # Two accounting calls and one bundling call remain.
    partition = result["partitions"][0]
    assert len(partition["evidence"]["taskBypass"]["packageIds"]) == 2
    assert all(t["matchMethod"] == "EXACT_TASK" and t["modelDeployment"] is None
               for t in partition["result"]["taskDeterminations"])
    assert all(item["referenceRouting"]["route"] == "EXACT_TASK_BYPASS_ACCOUNTING_MODEL_REQUIRED"
               for item in result["documents"])
    assert all(match["existingAssetId"] is None for item in result["documents"]
               for match in item["referenceRouting"]["matches"])
    assert result["businessApproved"] is False and result["popaWrites"] == 0


def test_bad_printed_task_routes_review_and_skips_assessment(manifest, tmp_path):
    opted = manifest.model_copy(update={"taskmaster_bypass": True})
    items = [extracted(1), extracted(2)]
    for item in items:
        item["lines"][0]["sourceTaskCode"] = "NOT-A-TASK"
    model = FixtureModel(items)
    result = run(opted, tmp_path, model)
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["partitions"] == []
    assert model.text_calls == 0
    assert result["llmCallsAttempted"] == 2
    assert all(item["referenceRouting"]["route"] == "REVIEW_NO_ASSESSMENT" for item in result["documents"])


def test_old_manifest_digest_is_preserved_when_bypass_disabled(manifest):
    old = manifest.model_dump(mode="json", by_alias=True)
    old.pop("taskmasterBypass")
    assert mvp.manifest_sha256(manifest) == canonical_sha256(old)
    assert mvp.manifest_sha256(manifest.model_copy(update={"taskmaster_bypass": True})) != canonical_sha256(old)


def import_body(result):
    return {"result": result, "documents": [
        {"documentId": item["document"]["documentId"],
         "contentBase64": base64.b64encode(pdf_bytes(item["document"]["uri"])).decode()}
        for item in result["documents"]
    ]}


def test_review_authenticated_import_replay_decision_conflict_and_reopen(identity, manifest, tmp_path):
    verify, token = identity
    result = run(manifest, tmp_path)
    path = tmp_path / "review.sqlite"
    app = create_app("review", verifier=verify, store_factory=lambda: ReviewStore(path))
    with TestClient(app) as http:
        body = import_body(result)
        assert http.post("/v1/review/imports", headers=headers(token(roles=["Capex.Agent.Invoke"])),
                         json=body).status_code == 403
        for expected in (True, False):
            response = http.post("/v1/review/imports", headers=headers(token()), json=body)
            assert response.status_code == 200, response.text
            assert response.json()["imported"] is expected
        changed = copy.deepcopy(body)
        changed["result"]["modelDeployment"] = "CONFLICT"
        assert http.post("/v1/review/imports", headers=headers(token()), json=changed).status_code == 409
        route = f"/v1/review/runs/{manifest.workflow_run_id}"
        decision = {"documentId": "TEST-1", "expectedVersion": 0, "action": "DEFER",
                    "reason": "TEST ONLY: need finance review"}
        spoof = {**decision, "actor": "invented administrator"}
        assert http.post(route + "/decisions", headers=headers(token()), json=spoof).status_code == 422
        response = http.post(route + "/decisions", headers=headers(token()), json=decision)
        assert response.status_code == 200, response.text
        assert http.post(route + "/decisions", headers=headers(token()), json=decision).status_code == 409
        assert http.get(route + "/documents/TEST-1", headers=headers(token())).content == pdf_bytes("test-1.pdf")
        assert http.get("/health/ready").status_code == 200
    with TestClient(create_app("review", verifier=verify, store_factory=lambda: ReviewStore(path))) as http:
        saved = http.get(route, headers=headers(token())).json()
        audit = saved["analystAudit"]["TEST-1"]
        assert audit[0]["decision"]["actor"] == TENANT + "/" + OBJECT
        assert audit[0]["decision"]["action"] == "DEFER"
        assert saved["result"] == result


def test_review_import_rejects_missing_shape_and_changed_pdf_before_writing(identity, manifest, tmp_path):
    verify, token = identity
    path = tmp_path / "review-invalid.sqlite"
    result = run(manifest, tmp_path)
    with TestClient(create_app("review", verifier=verify, store_factory=lambda: ReviewStore(path))) as http:
        body = import_body(result)
        assert http.post("/v1/review/imports", headers=headers(token()),
                         json={**body, "result": {}}).status_code == 422
        body["documents"][0]["contentBase64"] = base64.b64encode(b"%PDF-1.7\nTAMPERED").decode()
        assert http.post("/v1/review/imports", headers=headers(token()), json=body).status_code == 422
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 0


@pytest.mark.parametrize("url", [
    "http://example.com", "https://user:password@example.com", "https://example.com/path",
    "https://example.com?token=secret", "https://example.com#fragment",
])
def test_service_client_requires_https_origin(url):
    with pytest.raises(ValueError):
        ServiceClient(url, "api://audience", lambda scope: "TEST_TOKEN")


class SqlDialectHarness:
    """DB-API adapter contract test only; not an Azure SQL integration test."""

    def __init__(self, batch_path, review_path):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("ATTACH DATABASE ? AS mvp_batch", (str(batch_path),))
        self.db.execute("ATTACH DATABASE ? AS mvp_review", (str(review_path),))
        self.queries = []

    def cursor(self):
        parent = self
        cursor = self.db.cursor()

        class Cursor:
            def execute(self, query, parameters=()):
                parent.queries.append(query)
                if query.startswith("SET "):
                    query = "SELECT 1"
                query = query.replace("WITH (UPDLOCK, HOLDLOCK)", "")
                cursor.execute(query, parameters)
                return self
            def executemany(self, query, parameters):
                parent.queries.append(query)
                cursor.executemany(query, parameters)
            def fetchone(self):
                return cursor.fetchone()
            def fetchall(self):
                return cursor.fetchall()
            def close(self):
                cursor.close()
        return Cursor()

    def commit(self):
        self.db.commit()
    def rollback(self):
        self.db.rollback()
    def close(self):
        self.db.close()


def test_sql_store_adapter_uses_isolated_tables_and_preserves_audit_contract(manifest, tmp_path):
    batch_path, review_path = tmp_path / "sql-batch.sqlite", tmp_path / "sql-review.sqlite"
    mvp.BatchState(batch_path).close()
    ReviewStore(review_path).close()
    batch_db = SqlDialectHarness(batch_path, review_path)
    state = mvp.SqlBatchState(batch_db)
    try:
        result = mvp.run_batch(manifest, FixtureModel(), state, read=pdf_bytes,
                               render=lambda data: [ImageRef.from_bytes(b"TEST_IMAGE")])
        assert result["batchStateStore"] == "AZURE_SQL"
        assert state.get(manifest.workflow_run_id, "extraction:TEST-1") is not None
        resumed = mvp.run_batch(manifest, FixtureModel(), state, resume=True, read=pdf_bytes)
        assert resumed["llmCallsAttempted"] == 7
        assert any("mvp_batch.checkpoints" in query for query in batch_db.queries)
    finally:
        state.close()
    review_db = SqlDialectHarness(batch_path, review_path)
    store = SqlReviewStore(review_db)
    try:
        assert store.import_result(result, read=pdf_bytes) is True
        assert store.import_result(result, read=pdf_bytes) is False
        decision = ReviewDecision(document_id="TEST-1", expected_version=0, actor="TEST_ONLY",
                                  action="DEFER", reason="TEST ONLY")
        assert store.decide(manifest.workflow_run_id, decision)["version"] == 1
        with pytest.raises(ValueError, match="Stale"):
            store.decide(manifest.workflow_run_id, decision)
        assert store.history(manifest.workflow_run_id, "TEST-1")[0]["decision"]["actor"] == "TEST_ONLY"
        assert any("mvp_review.decisions" in query for query in review_db.queries)
    finally:
        store.close()


def test_sql_session_rolls_back_and_closes_on_exception(tmp_path):
    mvp.BatchState(tmp_path / "b.sqlite").close()
    ReviewStore(tmp_path / "r.sqlite").close()
    connection = SqlDialectHarness(tmp_path / "b.sqlite", tmp_path / "r.sqlite")
    session = SqlSession(connection)
    with pytest.raises(ValueError):
        with session:
            session.execute("INSERT INTO mvp_batch.runs VALUES (?,?,?,?,?)",
                            ("ROLLED_BACK", "0" * 64, "RUNNING", "TEST", None))
            raise ValueError("TEST_ONLY")
    assert connection.db.execute("SELECT COUNT(*) FROM mvp_batch.runs").fetchone()[0] == 0
    session.close()
