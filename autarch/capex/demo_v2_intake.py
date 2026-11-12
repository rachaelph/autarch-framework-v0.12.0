"""Prepare the explicitly selected ADF demo sources; no model or SQL calls."""
from __future__ import annotations

import argparse
import hashlib
import io
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Literal, Optional
from urllib.parse import quote, urlparse

from pydantic import Field

from .demo_v2_contracts import DemoWorkflowRequest, InvoicePackageReference
from .demo_v2_job import _credential, _is_windows_path, _write_json
from .demo_v2_normalization import (
    NormalizationModel, NormalizationStatus, NormalizedInvoicePackage,
    normalize_abbyy_for_demo_v2, normalize_jc_workbook,
)


SOURCE_FILES = (
    "Bruegmann  - INV1262027.pdf-135d03ac-3598-498a-83fe-d273d046bfea.zip",
    "Kimley Hor - 36764068GRP005.pdf-58aceec5-f35a-48a7-8faa-2f1a195af203.zip",
    "JC2000A07465-Ariba.xlsx",
    "JC2000A07814 - Ariba+Proj.tool.xlsx",
)
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 512


class IntakeSource(NormalizationModel):
    file_name: str
    uri: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_count: int = Field(gt=0)
    format: Literal["ABBYY_ZIP", "JC_WORKBOOK"]
    package_ids: list[str]
    classification_uri: Optional[str] = None


class IntakeManifest(NormalizationModel):
    schema_version: Literal["1.0-capex-adf-intake"] = "1.0-capex-adf-intake"
    stage: Literal["INPUTS_PREPARED"] = "INPUTS_PREPARED"
    workflow_run_id: str
    created_at_utc: datetime
    sources: list[IntakeSource]
    request_uri: str
    package_count: int
    line_count: int
    excluded_record_count: int
    review_required_package_count: int
    model_calls: Literal[0] = 0
    sql_writes: Literal[0] = 0
    business_approved: Literal[False] = False


def _validate_base(base: str) -> None:
    if _is_windows_path(base):
        return
    parsed = urlparse(base)
    if not parsed.scheme:
        return
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".blob.core.windows.net")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.strip("/")
    ):
        raise ValueError("Use a local directory or an HTTPS Azure Blob base URI without SAS credentials.")


def _join(base: str, *parts: str) -> str:
    if not _is_windows_path(base) and urlparse(base).scheme == "https":
        return base.rstrip("/") + "/" + "/".join(quote(part, safe="") for part in parts)
    return str(Path(base).joinpath(*parts))


def read_source(uri: str) -> bytes:
    if not _is_windows_path(uri) and urlparse(uri).scheme == "https":
        from azure.core import MatchConditions
        from azure.storage.blob import BlobClient

        with BlobClient.from_blob_url(uri, credential=_credential()) as blob:
            properties = blob.get_blob_properties()
            if not 0 < properties.size <= MAX_SOURCE_BYTES:
                raise ValueError(f"Source must be between 1 byte and {MAX_SOURCE_BYTES} bytes: {uri}")
            return blob.download_blob(
                etag=properties.etag, match_condition=MatchConditions.IfNotModified,
            ).readall()
    path = Path(uri)
    if not 0 < path.stat().st_size <= MAX_SOURCE_BYTES:
        raise ValueError(f"Source must be between 1 byte and {MAX_SOURCE_BYTES} bytes: {uri}")
    return path.read_bytes()


def _validate_archive(data: bytes, name: str) -> None:
    if not 0 < len(data) <= MAX_SOURCE_BYTES:
        raise ValueError(f"Source exceeds the size limit or is empty: {name}")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if not members or len(members) > MAX_ARCHIVE_MEMBERS:
                raise ValueError(f"Invalid archive member count: {name}")
            if sum(member.file_size for member in members) > MAX_ARCHIVE_BYTES:
                raise ValueError(f"Archive exceeds the uncompressed size limit: {name}")
            names = [member.filename.replace("\\", "/") for member in members]
            if len(names) != len(set(names)):
                raise ValueError(f"Archive contains duplicate member names: {name}")
            for member, member_name in zip(members, names):
                path = PurePosixPath(member_name)
                if path.is_absolute() or ".." in path.parts or ":" in member_name or member.flag_bits & 1:
                    raise ValueError(f"Unsafe or encrypted archive member in {name}")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Source is not a valid unencrypted ZIP/XLSX archive: {name}") from exc


def _write_artifact(uri: str, document: object) -> None:
    _write_json(uri, document, exclusive=True)


def prepare_intake(
    *,
    input_base_uri: str,
    output_base_uri: str,
    workflow_run_id: str,
    policy_version: str,
    model_deployment: str = "gpt-5.1",
    created_at_utc: Optional[datetime] = None,
    read: Callable[[str], bytes] = read_source,
    write: Callable[[str, object], None] = _write_artifact,
) -> IntakeManifest:
    """Normalize four sources and publish the ready manifest only after every artifact."""
    for base in (input_base_uri, output_base_uri):
        _validate_base(base)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", workflow_run_id):
        raise ValueError("workflowRunId must be a safe, unique run-directory name of at most 128 characters.")
    created = created_at_utc or datetime.now(timezone.utc)
    if created.tzinfo is None or created.utcoffset() != timezone.utc.utcoffset(created):
        raise ValueError("createdAtUtc must include a UTC offset.")
    run_uri = _join(output_base_uri, workflow_run_id)
    packages: list[NormalizedInvoicePackage] = []
    sources: list[IntakeSource] = []
    artifacts: list[tuple[str, object]] = []
    excluded_count = 0
    with tempfile.TemporaryDirectory(prefix="capex-intake-") as directory:
        for index, name in enumerate(SOURCE_FILES, start=1):
            uri = _join(input_base_uri, name)
            data = read(uri)
            _validate_archive(data, name)
            digest = hashlib.sha256(data).hexdigest()
            if digest in {source.sha256 for source in sources}:
                raise ValueError(f"Duplicate source bytes: {name}")
            path = Path(directory) / name
            path.write_bytes(data)
            classification_uri = None
            if name.endswith(".zip"):
                included = [normalize_abbyy_for_demo_v2(
                    path, run_id=workflow_run_id, created_at_utc=created,
                )]
            else:
                classified = normalize_jc_workbook(
                    path, run_id=workflow_run_id, created_at_utc=created,
                )
                included = classified.packages
                excluded_count += len(classified.excluded_records)
                classification_uri = _join(run_uri, "ledgers", f"{index:02d}.json")
                artifacts.append((classification_uri, classified.model_dump(mode="json", by_alias=True)))
            packages.extend(included)
            sources.append(IntakeSource(
                file_name=name, uri=uri, sha256=digest, byte_count=len(data),
                format="ABBYY_ZIP" if name.endswith(".zip") else "JC_WORKBOOK",
                package_ids=[package.package_id for package in included],
                classification_uri=classification_uri,
            ))
    references = []
    for index, package in enumerate(packages, start=1):
        uri = _join(run_uri, "packages", f"{index:03d}.json")
        artifacts.append((uri, package.model_dump(mode="json", by_alias=True)))
        references.append(InvoicePackageReference(
            package_id=package.package_id, run_id=package.run_id, package_uri=uri,
            expected_invoice_id=package.invoice_id, expected_source_sha256=package.source.sha256,
        ))
    request = DemoWorkflowRequest(
        workflow_run_id=workflow_run_id, requested_at_utc=created, packages=references,
        model_deployment=model_deployment, task_prompt_version="capex-demo-v2-task-suggestion-v1",
        capitalization_policy_version=policy_version, bundling_policy_version=policy_version,
        persist_results=False, allow_synthetic_inputs=False, fail_on_review=False,
    )
    request_uri = _join(run_uri, "request.json")
    manifest = IntakeManifest(
        workflow_run_id=workflow_run_id, created_at_utc=created, sources=sources,
        request_uri=request_uri, package_count=len(packages),
        line_count=sum(len(package.lines) for package in packages),
        excluded_record_count=excluded_count,
        review_required_package_count=sum(
            package.normalization_status == NormalizationStatus.REVIEW_REQUIRED for package in packages
        ),
    )
    artifacts.append((request_uri, request.model_dump(mode="json", by_alias=True)))
    for uri, document in artifacts:
        write(uri, document)
    write(_join(run_uri, "intake.json"), manifest.model_dump(mode="json", by_alias=True))
    return manifest


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-base-uri", required=True)
    parser.add_argument("--output-base-uri", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--policy-version", required=True)
    parser.add_argument("--model-deployment", default="gpt-5.1")
    args = parser.parse_args(argv)
    manifest = prepare_intake(
        input_base_uri=args.input_base_uri, output_base_uri=args.output_base_uri,
        workflow_run_id=args.workflow_run_id, policy_version=args.policy_version,
        model_deployment=args.model_deployment,
    )
    print(manifest.model_dump_json(by_alias=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
