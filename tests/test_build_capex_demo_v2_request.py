from __future__ import annotations

from datetime import datetime, timezone

from tools.build_capex_demo_v2_request import build_request


def test_builds_canonical_three_invoice_blob_request() -> None:
    request = build_request(
        workflow_run_id="workflow-request-test",
        artifact_base_uri=(
            "https://example.blob.core.windows.net/landing/"
            "capex/demo-v2/normalized"
        ),
        allow_synthetic_inputs=True,
        persist_results=True,
        requested_at_utc=datetime(
            2026,
            11,
            4,
            tzinfo=timezone.utc,
        ),
    )

    assert len(request.packages) == 3
    assert request.persist_results
    assert request.allow_synthetic_inputs
    assert all(
        package.package_uri.startswith(
            "https://example.blob.core.windows.net/"
        )
        for package in request.packages
    )
    assert all(
        package.expected_source_sha256 is not None
        for package in request.packages
    )
