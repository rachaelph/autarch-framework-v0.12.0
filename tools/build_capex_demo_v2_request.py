"""Build a validated three-invoice Demo V2 workflow request."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from autarch.capex.demo_v2_contracts import (
    DemoWorkflowRequest,
    InvoicePackageReference,
)
from autarch.capex.demo_v2_normalization import (
    JCWorkbookNormalizationResult,
    NormalizedInvoicePackage,
)


ROOT = Path(__file__).resolve().parents[1]
NORMALIZED = ROOT / "examples" / "data" / "capex" / "normalized-v2"
FILES = (
    "bruegmann.normalized.json",
    "kimley-horn.normalized.json",
    "jc2000a07814.normalized.json",
)


def _load_packages() -> list[tuple[str, NormalizedInvoicePackage]]:
    packages: list[tuple[str, NormalizedInvoicePackage]] = []
    for file_name in FILES:
        path = NORMALIZED / file_name
        if file_name.startswith("jc"):
            package = JCWorkbookNormalizationResult.model_validate_json(
                path.read_text(encoding="utf-8")
            ).packages[0]
        else:
            package = NormalizedInvoicePackage.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        packages.append((file_name, package))
    return packages


def _artifact_uri(
    *,
    file_name: str,
    artifact_base_uri: Optional[str],
) -> str:
    if artifact_base_uri:
        return f"{artifact_base_uri.rstrip('/')}/{file_name}"
    return (NORMALIZED / file_name).resolve().as_uri()


def build_request(
    *,
    workflow_run_id: str,
    artifact_base_uri: Optional[str],
    allow_synthetic_inputs: bool,
    persist_results: bool,
    requested_at_utc: datetime,
) -> DemoWorkflowRequest:
    """Build the canonical three-invoice direct-batch request."""
    return DemoWorkflowRequest(
        workflowRunId=workflow_run_id,
        requestedAtUtc=requested_at_utc,
        packages=[
            InvoicePackageReference(
                packageId=package.package_id,
                runId=package.run_id,
                packageUri=_artifact_uri(
                    file_name=file_name,
                    artifact_base_uri=artifact_base_uri,
                ),
                expectedInvoiceId=package.invoice_id,
                expectedSourceSha256=package.source.sha256,
            )
            for file_name, package in _load_packages()
        ],
        taskRegion="US",
        modelDeployment="gpt-5.1",
        taskPromptVersion="capex-demo-v2-task-suggestion-v1",
        capitalizationPolicyVersion="circle-k-na-demo-unapproved-2026-10-30",
        bundlingPolicyVersion="circle-k-na-demo-unapproved-2026-10-30",
        persistResults=persist_results,
        failOnReview=False,
        allowSyntheticInputs=allow_synthetic_inputs,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-base-uri")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--persist-results", action="store_true")
    parser.add_argument(
        "--requested-at-utc",
        help="UTC ISO-8601 timestamp; defaults to the current time.",
    )
    args = parser.parse_args()
    requested_at = (
        datetime.fromisoformat(args.requested_at_utc.replace("Z", "+00:00"))
        if args.requested_at_utc
        else datetime.now(timezone.utc)
    )
    request = build_request(
        workflow_run_id=args.workflow_run_id,
        artifact_base_uri=args.artifact_base_uri,
        allow_synthetic_inputs=args.synthetic,
        persist_results=args.persist_results,
        requested_at_utc=requested_at,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        request.model_dump_json(by_alias=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
