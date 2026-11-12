from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from autarch.capex import demo_v2_intake as intake
from autarch.capex.demo_v2_contracts import DemoWorkflowRequest
from autarch.capex.demo_v2_job import _package_from_payload
from autarch.capex.demo_v2_normalization import JCWorkbookNormalizationResult


DATA = Path(__file__).resolve().parents[1] / "examples" / "data" / "capex"
CREATED = datetime(2026, 11, 10, tzinfo=timezone.utc)
INPUT = "https://cictaicusst.blob.core.windows.net/landing/capex/input"
OUTPUT = "https://cictaicusst.blob.core.windows.net/processed/capex/thursday"


def read_fixture(uri: str) -> bytes:
    return (DATA / unquote(urlparse(uri).path.rsplit("/", 1)[-1])).read_bytes()


def prepare(**overrides):
    written = {}

    def write(uri, value):
        if uri in written:
            raise FileExistsError(uri)
        written[uri] = value

    args = dict(
        input_base_uri=INPUT, output_base_uri=OUTPUT, workflow_run_id="adf-intake-test",
        policy_version="test-unapproved-policy", created_at_utc=CREATED,
        read=read_fixture, write=write,
    )
    args.update(overrides)
    result = intake.prepare_intake(**args)
    return result, written


def test_all_four_original_sources_produce_ten_packages_thirty_lines_and_seven_exclusions():
    manifest, written = prepare()
    assert manifest.package_count == 10
    assert manifest.line_count == 30
    assert manifest.excluded_record_count == 7
    assert manifest.review_required_package_count == 10
    assert manifest.model_calls == manifest.sql_writes == 0
    assert manifest.business_approved is False
    assert [source.file_name for source in manifest.sources] == list(intake.SOURCE_FILES)
    assert all(
        source.sha256 == hashlib.sha256((DATA / source.file_name).read_bytes()).hexdigest()
        for source in manifest.sources
    )
    request = DemoWorkflowRequest.model_validate(written[manifest.request_uri])
    assert not request.persist_results
    assert not request.allow_synthetic_inputs
    assert request.workflow_run_id == manifest.workflow_run_id
    assert request.capitalization_policy_version == "test-unapproved-policy"
    packages = [
        _package_from_payload(written[ref.package_uri], package_id=ref.package_id)
        for ref in request.packages
    ]
    assert len({package.package_id for package in packages}) == 10
    assert all(package.run_id == request.workflow_run_id for package in packages)
    assert all(package.context_overlay is None for package in packages)
    assert all(
        ref.expected_source_sha256 == package.source.sha256
        and ref.expected_invoice_id == package.invoice_id
        for ref, package in zip(request.packages, packages)
    )
    assert {line.currency for package in packages for line in package.lines} == {"USD", "EUR", "DKK"}
    assert all(
        line.source_transaction_type in (None, "AI")
        for package in packages for line in package.lines
    )
    for source in manifest.sources:
        if source.classification_uri:
            ledger = JCWorkbookNormalizationResult.model_validate(written[source.classification_uri])
            assert {row.transaction_type for row in ledger.excluded_records} <= {"JP", "OV"}
    assert list(written)[-1] == f"{OUTPUT}/adf-intake-test/intake.json"


def test_missing_source_stops_before_publishing_any_artifacts():
    written = []

    def read(uri):
        if "JC2000A07814" in uri:
            raise FileNotFoundError(uri)
        return read_fixture(uri)

    with pytest.raises(FileNotFoundError):
        prepare(read=read, write=lambda uri, value: written.append(uri))
    assert written == []


def test_partial_upload_does_not_publish_ready_manifest():
    written = []

    def write(uri, value):
        if len(written) == 2:
            raise OSError("Controlled upload failure")
        written.append(uri)

    with pytest.raises(OSError, match="upload failure"):
        prepare(write=write)
    assert len(written) == 2
    assert not any(uri.endswith("/intake.json") for uri in written)


@pytest.mark.parametrize("run_id", ["../escape", "a/b", "a\\b", "", "a" * 129])
def test_invalid_run_identity_is_rejected_before_io(run_id):
    def read(uri):
        pytest.fail("No source should be accessed")

    with pytest.raises(ValueError, match="workflowRunId"):
        prepare(workflow_run_id=run_id, read=read)


@pytest.mark.parametrize("uri", [
    "http://cictaicusst.blob.core.windows.net/landing",
    "https://cictaicusst.blob.core.windows.net/landing?sig=secret",
    "https://user:password@cictaicusst.blob.core.windows.net/landing",
    "https://unrelated.example/landing",
    "https://cictaicusst.blob.core.windows.net",
])
def test_invalid_source_bases_are_rejected(uri):
    with pytest.raises(ValueError, match="base URI"):
        prepare(input_base_uri=uri)


@pytest.mark.parametrize("member", ["../escape.csv", "/absolute.csv", "C:\\escape.csv"])
def test_unsafe_archive_members_are_rejected(member):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(member, "test")
    with pytest.raises(ValueError, match="Unsafe"):
        intake._validate_archive(stream.getvalue(), "test.zip")


def test_expanded_archive_limit_and_invalid_archive(monkeypatch):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("test.csv", "x" * 100)
    monkeypatch.setattr(intake, "MAX_ARCHIVE_BYTES", 50)
    with pytest.raises(ValueError, match="uncompressed"):
        intake._validate_archive(stream.getvalue(), "test.zip")
    with pytest.raises(ValueError, match="not a valid"):
        intake._validate_archive(b"not an archive", "test.xlsx")


def test_local_cli_writes_new_run_and_refuses_overwrite(tmp_path, capsys):
    arguments = [
        "--input-base-uri", str(DATA), "--output-base-uri", str(tmp_path),
        "--workflow-run-id", "local-test", "--policy-version", "test-unapproved-policy",
    ]
    assert intake.main(arguments) == 0
    summary = json.loads(capsys.readouterr().out)
    manifest = tmp_path / "local-test" / "intake.json"
    assert summary["stage"] == "INPUTS_PREPARED"
    assert json.loads(manifest.read_text(encoding="utf-8"))["packageCount"] == 10
    original = manifest.read_bytes()
    with pytest.raises(FileExistsError):
        intake.main(arguments)
    assert manifest.read_bytes() == original


def test_blob_download_is_size_checked_and_etag_pinned(monkeypatch):
    from azure.core import MatchConditions
    from azure.storage.blob import BlobClient
    from types import SimpleNamespace

    calls = {}

    class Blob:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get_blob_properties(self):
            return SimpleNamespace(size=10, etag="pinned-etag")

        def download_blob(self, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(readall=lambda: b"test-bytes")

    monkeypatch.setattr(intake, "_credential", lambda: object())
    monkeypatch.setattr(BlobClient, "from_blob_url", lambda *args, **kwargs: Blob())
    assert intake.read_source(INPUT + "/test.zip") == b"test-bytes"
    assert calls == {"etag": "pinned-etag", "match_condition": MatchConditions.IfNotModified}
    calls.clear()
    monkeypatch.setattr(intake, "MAX_SOURCE_BYTES", 5)
    with pytest.raises(ValueError, match="Source must be"):
        intake.read_source(INPUT + "/test.zip")
    assert calls == {}
