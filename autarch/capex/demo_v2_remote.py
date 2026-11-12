"""Batch-owned authenticated clients; never retry a billable model call implicitly."""
from __future__ import annotations

import base64
import argparse
import json
import os
from datetime import datetime
from typing import Callable, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request

from ..errors import ModelError
from ..intelligence.base import ModelProvider
from ..intelligence.vision import ImageRef
from .demo_v2_contracts import DemoWorkflowResult
from .demo_v2_mvp import SYSTEM, MvpManifest, extraction_prompt, manifest_sha256
from .demo_v2_normalization import NormalizedInvoicePackage
from .demo_v2_reference import canonical_sha256


class RemoteServiceError(ModelError):
    def __init__(self, status: int, payload: dict):
        super().__init__(f"Service request failed with HTTP {status}; inspect recorded evidence/service logs.")
        self.payload = payload


class ServiceClient:
    def __init__(self, url: str, audience: str, token: Callable[[str], str]):
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password \
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("Service URL must be an HTTPS origin without credentials, path, query or fragment.")
        if not audience.strip():
            raise ValueError("An explicit API audience is required.")
        self.url = url.rstrip("/")
        self.scope = audience.rstrip("/") + "/.default"
        self.token = token

    def request(self, path: str, payload: Optional[dict] = None) -> dict:
        body = json.dumps(payload, allow_nan=False).encode() if payload is not None else None
        request = Request(self.url + path, data=body, headers={
            "Authorization": "Bearer " + self.token(self.scope), "Content-Type": "application/json",
        })
        try:
            # Do not follow redirects with a bearer credential.
            from urllib.request import HTTPRedirectHandler, build_opener

            class RejectRedirect(HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    raise ModelError("Service redirects are forbidden.")

            with build_opener(RejectRedirect()).open(request, timeout=600) as response:
                data = response.read(96 * 1024 * 1024 + 1)
                if len(data) > 96 * 1024 * 1024:
                    raise ModelError("Service response exceeds the allowed size.")
                decoded = json.loads(data)
                if not isinstance(decoded, dict):
                    raise ModelError("Service returned a non-object response.")
                return decoded
        except HTTPError as exc:
            data = exc.read(96 * 1024 * 1024 + 1)
            if len(data) > 96 * 1024 * 1024:
                raise ModelError("Service error response exceeds the allowed size.") from exc
            try:
                payload = json.loads(data)
            except (json.JSONDecodeError, UnicodeError):
                payload = {"detail": "Non-JSON error; inspect service logs."}
            if not isinstance(payload, dict):
                payload = {"detail": "Non-object error; inspect service logs."}
            raise RemoteServiceError(exc.code, payload) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ModelError(f"Service request failed: {type(exc).__name__}; no automatic retry.") from exc

    def verify_references(self, manifest: MvpManifest) -> None:
        response = self.request("/v1/references")
        expected = manifest.task_master.model_dump(mode="json", by_alias=True)
        policies = {key: value.model_dump(mode="json", by_alias=True) for key, value in manifest.policies.items()}
        if response.get("taskMasterSha256") != canonical_sha256(expected) \
                or response.get("taskMaster") != expected or response.get("policies") != policies \
                or response.get("businessApproved") is not False \
                or response.get("existingAssetIdsAvailable") is not False:
            raise ValueError("Reference service differs from the batch-pinned TaskMaster/policy snapshot.")

    def import_review(self, result: dict, read: Callable[[str], bytes]) -> dict:
        documents = []
        for item in result["documents"]:
            doc = item["document"]
            documents.append({"documentId": doc["documentId"],
                              "contentBase64": base64.b64encode(read(doc["uri"])).decode("ascii")})
        response = self.request("/v1/review/imports", {"result": result, "documents": documents})
        if response.get("workflowRunId") != result["workflowRunId"] \
                or not isinstance(response.get("imported"), bool) \
                or response.get("replayedWithoutChanges") is not (not response["imported"]):
            raise ValueError("Review import acknowledgement is inconsistent with the submitted run.")
        return response


class RemoteAgent(ModelProvider):
    name = "ENTRA_CAPEX_AGENT_API"

    def __init__(self, client: ServiceClient, deployment: str):
        self.client = client
        self.deployment = deployment

    def supports_vision(self) -> bool:
        return True

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        raise ModelError("Remote agent accepts extraction and partition-assessment contracts, not arbitrary prompts.")

    def complete_vision(self, prompt: str, images: Sequence[ImageRef], system: Optional[str] = None) -> str:
        if prompt != extraction_prompt(len(images)) or system != SYSTEM:
            raise ValueError("Extraction prompt does not match the pinned extraction capability.")
        response = self.client.request("/v1/extractions", {"pages": [image.to_data_uri() for image in images]})
        if response.get("modelDeployment") != self.deployment or not isinstance(response.get("response"), str):
            raise ValueError("Agent returned an unexpected model deployment or extraction shape.")
        return response["response"]

    def assess(
        self, manifest: MvpManifest, packages: Sequence[NormalizedInvoicePackage], provider: ModelProvider,
        now: datetime, audit: Callable[[dict], None], documents: Sequence[dict],
    ) -> dict:
        ids = {package.invoice_id for package in packages}
        request = {
            "manifest": manifest.model_dump(mode="json", by_alias=True), "requestedAtUtc": now.isoformat(),
            "documents": [{"document": item["document"], "extraction": item["extraction"],
                           "pageCount": item["pageCount"]} for item in documents
                          if item["document"]["documentId"] in ids],
        }
        try:
            response = self.client.request("/v1/assessments", request)
        except RemoteServiceError as exc:
            detail = exc.payload.get("detail")
            evidence = detail.get("evidence") if isinstance(detail, dict) else None
            if isinstance(evidence, dict) and evidence.get("workflowRunId") == manifest.workflow_run_id \
                    and evidence.get("inputSha256") == manifest_sha256(manifest):
                audit(evidence)
            raise
        result = DemoWorkflowResult.model_validate(response["result"])
        expected = {(p.package_id, p.invoice_id, line.line_id) for p in packages for line in p.lines}
        if result.workflow_run_id != manifest.workflow_run_id or result.status != "SUCCEEDED" \
                or {(a.package_id, a.invoice_id, a.line_id) for a in result.assessments} != expected \
                or {(t.package_id, t.invoice_id, t.line_id) for t in result.task_determinations} != expected:
            raise ValueError("Agent response does not cover the exact requested assessment partition.")
        if response["evidence"].get("workflowRunId") != manifest.workflow_run_id \
                or response["evidence"].get("inputSha256") != manifest_sha256(manifest) \
                or response["evidence"].get("status") != "SUCCEEDED":
            raise ValueError("Agent evidence does not belong to this completed input manifest.")
        audit(response["evidence"])
        return response


def main(argv: Optional[Sequence[str]] = None) -> int:
    from azure.identity import AzureCliCredential, ManagedIdentityCredential
    from .demo_v2_job import _read_bytes, _write_json

    parser = argparse.ArgumentParser(description="Authenticated VM/operator client; no token is printed.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--audience", required=True, help="Entra application ID URI, for example api://<client-id>")
    parser.add_argument("--credential", choices=("managed-identity", "azure-cli"), default="azure-cli")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("references")
    check.add_argument("--manifest-uri", required=True)
    importer = commands.add_parser("import")
    importer.add_argument("--result-uri", required=True)
    read = commands.add_parser("read")
    read.add_argument("--run-id", required=True)
    read.add_argument("--output-uri", required=True)
    decide = commands.add_parser("decide")
    decide.add_argument("--run-id", required=True)
    decide.add_argument("--request-uri", required=True)
    args = parser.parse_args(argv)
    credential = (
        AzureCliCredential(tenant_id=os.environ["CAPEX_TENANT_ID"]) if args.credential == "azure-cli"
        else ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"])
    )
    try:
        client = ServiceClient(args.url, args.audience, lambda scope: credential.get_token(scope).token)
        if args.command == "references":
            client.verify_references(MvpManifest.model_validate_json(_read_bytes(args.manifest_uri)))
            print("PINNED_REFERENCES_VERIFIED")
        elif args.command == "import":
            result = json.loads(_read_bytes(args.result_uri))
            print(json.dumps(client.import_review(result, _read_bytes)))
        elif args.command == "read":
            response = client.request("/v1/review/runs/" + quote(args.run_id, safe=""))
            _write_json(args.output_uri, response, exclusive=True)
            print("REVIEW_AUDIT_SAVED")
        else:
            response = client.request(
                "/v1/review/runs/" + quote(args.run_id, safe="") + "/decisions",
                json.loads(_read_bytes(args.request_uri)),
            )
            print(json.dumps(response))
    finally:
        credential.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
