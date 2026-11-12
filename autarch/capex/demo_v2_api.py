"""Opt-in Entra-protected service hosts. No public/anonymous demo mode."""
from __future__ import annotations

import argparse
import base64
import binascii
import logging
import os
from dataclasses import dataclass
from typing import Callable, Literal, Optional
from uuid import UUID

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime, ConfigDict, Field, field_validator
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..intelligence.base import ModelProvider
from ..intelligence.vision import ImageRef
from ..errors import ModelError
from .demo_v2_job import _read_bytes
from .demo_v2_contracts import DemoWorkflowResult
from .demo_v2_mvp import (
    MAX_PAGES, MAX_PDF_BYTES, SYSTEM, ExtractedInvoice, MvpDocument, MvpManifest,
    assess_partition, extraction_prompt, normalized_package, taskmaster_route, validate_extraction,
)
from .demo_v2_normalization import NormalizationModel
from .demo_v2_reference import canonical_sha256
from .demo_v2_review import ReviewConflictError, ReviewDecision, ReviewStore, SqlReviewStore
from .sql_connection import connect_azure_sql


LOG = logging.getLogger(__name__)
MAX_BODY = 96 * 1024 * 1024
MAX_DOCUMENTS = 20


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = Headers(scope=scope).get("content-length")
        if length is not None:
            try:
                invalid = not 0 <= int(length) <= MAX_BODY
            except ValueError:
                await Response("Invalid Content-Length.", status_code=400)(scope, receive, send)
                return
            if invalid:
                await Response("Request too large.", status_code=413)(scope, receive, send)
                return
        chunks = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > MAX_BODY:
                await Response("Request too large.", status_code=413)(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        await self.app(scope, replay, secure_send)


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    object_id: str
    roles: frozenset[str]

    @property
    def actor(self) -> str:
        return f"{self.tenant_id}/{self.object_id}"


class EntraVerifier:
    def __init__(self, tenant_id: str, audience: str):
        self.tenant_id = str(UUID(tenant_id))
        self.audience = str(UUID(audience))
        self.issuer = f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"
        self.keys = jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys",
            cache_jwk_set=True, lifespan=300, timeout=10,
        )

    def __call__(self, token: str) -> Principal:
        if len(token) > 16384:
            raise HTTPException(401, "Invalid access token.")
        try:
            key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, key.key, algorithms=["RS256"], audience=self.audience, issuer=self.issuer,
                options={"require": ["exp", "iat", "nbf", "iss", "aud", "tid", "oid"]},
            )
            roles = claims.get("roles", [])
            if claims["tid"] != self.tenant_id or not isinstance(roles, list) \
                    or any(not isinstance(role, str) for role in roles):
                raise jwt.InvalidTokenError("Invalid tenant or role claims.")
            return Principal(self.tenant_id, str(UUID(claims["oid"])), frozenset(roles))
        except jwt.PyJWKClientConnectionError as exc:
            LOG.error("ENTRA_KEYS_UNAVAILABLE: %s", type(exc).__name__)
            raise HTTPException(503, "Identity signing keys are unavailable.") from exc
        except (jwt.InvalidTokenError, jwt.PyJWKClientError, ValueError, TypeError) as exc:
            LOG.warning("TOKEN_REJECTED: %s", type(exc).__name__)
            raise HTTPException(401, "Invalid access token.") from exc


class ExtractionRequest(NormalizationModel):
    pages: list[str] = Field(min_length=1, max_length=MAX_PAGES)


class AssessmentDocument(NormalizationModel):
    document: MvpDocument
    extraction: ExtractedInvoice
    page_count: int = Field(ge=1, le=MAX_PAGES)


class AssessmentRequest(NormalizationModel):
    manifest: MvpManifest
    requested_at_utc: AwareDatetime
    documents: list[AssessmentDocument] = Field(min_length=1, max_length=MAX_DOCUMENTS)


class ImportPdf(NormalizationModel):
    document_id: str = Field(min_length=1, max_length=128)
    content_base64: str = Field(min_length=1, max_length=28 * 1024 * 1024)


class ImportedDocument(AssessmentDocument):
    review_reasons: list[str]
    reference_routing: Optional[dict] = None


class ImportedPartition(NormalizationModel):
    site_id: str
    project_id: str
    result: DemoWorkflowResult
    evidence: dict


class CompletedResult(NormalizationModel):
    model_config = ConfigDict(extra="allow")
    schema_version: Literal["1.0-capex-mvp-result"]
    workflow_run_id: str = Field(min_length=1, max_length=128)
    status: Literal["SUCCEEDED", "REVIEW_REQUIRED"]
    synthetic: Literal[True]
    business_approved: Literal[False]
    sql_writes: Literal[0]
    popa_writes: Literal[0]
    documents: list[ImportedDocument] = Field(min_length=1, max_length=MAX_DOCUMENTS)
    partitions: list[ImportedPartition]
    active_task_codes: list[str] = Field(min_length=1)
    policy_snapshots: dict
    exception_document_count: int = Field(ge=0)
    llm_calls_attempted: int = Field(ge=0)


class ImportRequest(NormalizationModel):
    result: dict
    documents: list[ImportPdf] = Field(min_length=1, max_length=MAX_DOCUMENTS)

    @field_validator("result")
    @classmethod
    def completed_result(cls, value: dict) -> dict:
        result = CompletedResult.model_validate(value)
        ids = {doc.document.document_id for doc in result.documents}
        if len(ids) != len(result.documents) or result.exception_document_count != sum(
            bool(doc.review_reasons) for doc in result.documents
        ) or (result.status == "REVIEW_REQUIRED") != bool(result.exception_document_count):
            raise ValueError("Result has duplicate document IDs or inconsistent exception counts/status.")
        for partition in result.partitions:
            if partition.result.workflow_run_id != result.workflow_run_id \
                    or partition.result.status != "SUCCEEDED" \
                    or any(item.invoice_id not in ids for item in partition.result.assessments):
                raise ValueError("Partition does not belong to this completed run.")
        return value


class DecisionRequest(NormalizationModel):
    document_id: str = Field(min_length=1)
    expected_version: int = Field(ge=0)
    action: Literal["CONFIRM", "CORRECT", "DEFER"]
    reason: str = Field(min_length=1, max_length=2000)
    corrections: dict[str, str] = Field(default_factory=dict)
    simulated_popa_completed: bool = False


def _decode(value: str) -> bytes:
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid base64 document.") from exc
    if not data or len(data) > MAX_PDF_BYTES:
        raise ValueError("Document exceeds the allowed size or is empty.")
    return data


def create_app(
    service: Literal["agent", "review", "reference"], *,
    verifier: Callable[[str], Principal],
    provider: Optional[ModelProvider] = None,
    reference: Optional[MvpManifest] = None,
    store_factory: Optional[Callable[[], ReviewStore]] = None,
) -> FastAPI:
    if service not in {"agent", "review", "reference"}:
        raise ValueError("Unknown service.")
    if service == "review":
        if store_factory is None or provider is not None or reference is not None:
            raise ValueError("Review requires its own store, with no model or reference manifest.")
    elif reference is None or store_factory is not None or (service == "agent" and provider is None):
        raise ValueError("Agent/reference requires pinned references and no transactional store.")
    if service == "reference" and provider is not None:
        raise ValueError("Reference service cannot receive a model provider.")
    app = FastAPI(
        title=f"CapEx MVP {service}", version="1.0",
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    app.add_middleware(BodyLimitMiddleware)
    bearer = HTTPBearer(auto_error=False)

    def require(role: str):
        def dependency(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> Principal:
            if credentials is None or credentials.scheme.lower() != "bearer":
                raise HTTPException(401, "Bearer access token required.", headers={"WWW-Authenticate": "Bearer"})
            principal = verifier(credentials.credentials)
            if role not in principal.roles:
                LOG.warning("ROLE_REJECTED: required=%s", role)
                raise HTTPException(403, "Caller does not have the required application role.")
            return principal
        return dependency

    @app.exception_handler(ValueError)
    async def invalid_input(request: Request, exc: ValueError):
        LOG.warning("REQUEST_REJECTED: %s", str(exc))
        return Response("Request failed input or workflow validation; inspect service logs.", status_code=422)

    @app.exception_handler(ReviewConflictError)
    async def conflict(request: Request, exc: ReviewConflictError):
        LOG.warning("REVIEW_CONFLICT: %s", str(exc))
        return Response("Immutable import or review version conflict; reload before retrying.", status_code=409)

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready():
        if store_factory is not None:
            store = store_factory()
            try:
                for table in (store.imports_table, store.documents_table, store.decisions_table):
                    store.connection.execute(f"SELECT run_id FROM {table} WHERE 1=0").fetchall()
            finally:
                store.close()
        return {"status": "configured", "service": service, "modelConnectivityProbed": False}

    if service == "agent":
        assert provider is not None and reference is not None
        model = provider
        pinned = reference

        @app.post("/v1/extractions")
        def extract(body: ExtractionRequest, caller: Principal = Depends(require("Capex.Agent.Invoke"))):
            images = []
            for page in body.pages:
                prefix = "data:image/png;base64,"
                if not page.startswith(prefix):
                    raise ValueError("Only embedded PNG pages are accepted; URLs and local paths are forbidden.")
                data = _decode(page[len(prefix):])
                if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("Invalid PNG page.")
                images.append(ImageRef.from_bytes(data))
            raw = model.complete_vision(extraction_prompt(len(images)), images, system=SYSTEM)
            # Batch records the raw response before validating the extraction contract.
            return {"response": raw, "modelDeployment": pinned.model_deployment}

        @app.post("/v1/assessments")
        def assess(body: AssessmentRequest, caller: Principal = Depends(require("Capex.Agent.Invoke"))):
            manifest = body.manifest
            expected = lambda m: canonical_sha256({
                "taskMaster": m.task_master.model_dump(mode="json", by_alias=True),
                "policies": {key: value.model_dump(mode="json", by_alias=True) for key, value in m.policies.items()},
                "model": m.model_deployment, "taskmasterBypass": m.taskmaster_bypass,
            })
            if expected(manifest) != expected(pinned):
                raise ValueError("Request references, rule mode or model differ from server-pinned configuration.")
            catalog = {doc.document_id: doc for doc in manifest.documents}
            packages = []
            documents = []
            identities = set()
            for item in body.documents:
                doc = item.document
                if catalog.get(doc.document_id) != doc or doc.context is None:
                    raise ValueError("Assessment document differs from its manifest or has no Site/Project.")
                if doc.country_region not in {"US", "USA", "United States", "North America"}:
                    raise ValueError("No governed non-US TaskMaster mapping is supplied.")
                reasons = validate_extraction(item.extraction, item.page_count)
                if manifest.taskmaster_bypass:
                    taskmaster_route(manifest, item.extraction, reasons)
                if reasons or item.extraction.currency not in manifest.policies:
                    raise ValueError("Extraction exceptions must be routed to review, not assessment.")
                identity = ((item.extraction.vendor_name or "").casefold(),
                            item.extraction.invoice_number, item.extraction.currency)
                if identity in identities:
                    raise ValueError("Duplicate invoice identity in assessment request.")
                identities.add(identity)
                packages.append(normalized_package(doc, item.extraction, manifest.workflow_run_id, body.requested_at_utc))
                documents.append(item.model_dump(mode="json", by_alias=True))
            if len({p.invoice_id for p in packages}) != len(packages) or len({
                (doc.document.context.site_id, doc.document.context.project_id)
                for doc in body.documents if doc.document.context is not None
            }) != 1:
                raise ValueError("Assessment must contain unique invoices in exactly one Site/Project partition.")
            evidence = []
            try:
                return assess_partition(manifest, packages, model, body.requested_at_utc, evidence.append, documents)
            except (ValueError, ModelError) as exc:
                LOG.error("ASSESSMENT_FAILED: %s", type(exc).__name__)
                raise HTTPException(422, detail={
                    "code": "ASSESSMENT_FAILED",
                    "evidence": evidence[-1] if evidence else None,
                }) from exc

    if service == "reference":
        assert reference is not None
        snapshot = reference

        @app.get("/v1/references")
        def references(caller: Principal = Depends(require("Capex.Reference.Read"))):
            return {
                "taskMaster": snapshot.task_master.model_dump(mode="json", by_alias=True),
                "taskMasterSha256": canonical_sha256(snapshot.task_master.model_dump(mode="json", by_alias=True)),
                "policies": {key: value.model_dump(mode="json", by_alias=True) for key, value in snapshot.policies.items()},
                "referenceKind": "TASKMASTER_ASSET_TASK_CATALOG_NOT_PHYSICAL_REGISTER",
                "existingAssetIdsAvailable": False, "businessApproved": False,
            }

    if service == "review":
        assert store_factory is not None
        open_store = store_factory

        @app.post("/v1/review/imports")
        def import_result(body: ImportRequest, caller: Principal = Depends(require("Capex.Review.Import"))):
            content = {item.document_id: _decode(item.content_base64) for item in body.documents}
            if len(content) != len(body.documents) or sum(map(len, content.values())) > 64 * 1024 * 1024:
                raise ValueError("Duplicate documents or aggregate PDF size exceeds 64 MiB.")
            result = body.result
            expected = {item["document"]["documentId"]: item["document"]["uri"] for item in result["documents"]}
            if set(content) != set(expected) or len(set(expected.values())) != len(expected):
                raise ValueError("Supplied PDF identities must exactly cover unique imported documents.")
            by_uri = {expected[key]: value for key, value in content.items()}
            # Never fetch caller-supplied paths or URLs on the review service.
            store = open_store()
            try:
                inserted = store.import_result(result, read=by_uri.__getitem__)
                return {"workflowRunId": result["workflowRunId"], "imported": inserted,
                        "replayedWithoutChanges": not inserted, "importedBy": caller.actor}
            finally:
                store.close()

        @app.get("/v1/review/runs/{run_id}")
        def read_result(run_id: str, caller: Principal = Depends(require("Capex.Review.Read"))):
            store = open_store()
            try:
                result = store.result(run_id)
                return {"result": result, "analystAudit": {
                    item["document"]["documentId"]: store.history(run_id, item["document"]["documentId"])
                    for item in result["documents"]
                }, "demoOnly": True}
            finally:
                store.close()

        @app.get("/v1/review/runs/{run_id}/documents/{document_id}")
        def read_document(run_id: str, document_id: str,
                          caller: Principal = Depends(require("Capex.Review.Read"))):
            store = open_store()
            try:
                data = store.document(run_id, document_id)
                if data is None:
                    raise HTTPException(404, "Unknown document.")
                return Response(
                    data, media_type="application/pdf" if data.startswith(b"%PDF-") else "application/octet-stream",
                )
            finally:
                store.close()

        @app.post("/v1/review/runs/{run_id}/decisions")
        def decide(run_id: str, body: DecisionRequest,
                   caller: Principal = Depends(require("Capex.Review.Decide"))):
            decision = ReviewDecision.model_validate({**body.model_dump(), "actor": caller.actor})
            store = open_store()
            try:
                return store.decide(run_id, decision)
            finally:
                store.close()

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", choices=("agent", "review", "reference"), required=True)
    parser.add_argument("--manifest-uri")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    verify = EntraVerifier(os.environ["CAPEX_TENANT_ID"], os.environ["CAPEX_API_AUDIENCE"])
    if args.service == "review":
        if args.manifest_uri:
            parser.error("Review service does not accept a manifest or model configuration.")
        def store() -> ReviewStore:
            return SqlReviewStore(connect_azure_sql(
                server_fqdn=os.environ["CAPEX_SQL_SERVER"], database_name=os.environ["CAPEX_SQL_DATABASE"],
                managed_identity_client_id=os.environ["AZURE_CLIENT_ID"],
            ))
        app = create_app("review", verifier=verify, store_factory=store)
    else:
        if not args.manifest_uri:
            parser.error("Agent/reference service requires --manifest-uri.")
        reference = MvpManifest.model_validate_json(_read_bytes(args.manifest_uri))
        model = None
        if args.service == "agent":
            from ..intelligence.azure_openai import AzureOpenAIProvider
            model = AzureOpenAIProvider(
                deployment=reference.model_deployment, endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                managed_identity_client_id=os.environ["AZURE_CLIENT_ID"],
            )
        app = create_app(args.service, verifier=verify, provider=model, reference=reference)
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=args.port, proxy_headers=False)


if __name__ == "__main__":
    main()
