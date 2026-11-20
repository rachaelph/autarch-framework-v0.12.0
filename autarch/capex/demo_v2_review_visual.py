"""Paused, unvalidated prototype: export an unapproved local analyst visual."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Callable, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .demo_v2_contracts import DemoWorkflowRequest, DemoWorkflowResult
from .demo_v2_intake import IntakeManifest, _validate_base, read_source
from .demo_v2_normalization import NormalizedInvoicePackage


class _TranslatedPackage(BaseModel):
    package_id: str = Field(alias="packageId")
    package_uri: str = Field(alias="packageUri")


class _TranslationReport(BaseModel):
    workflow_run_id: str = Field(alias="workflowRunId")
    status: Literal["SUCCEEDED"]
    stage: Literal["TRANSLATION"]
    engine: Literal["AZURE_TRANSLATOR_NMT"]
    target_language: Literal["en"] = Field(alias="targetLanguage")
    source_descriptions_preserved: Literal[True] = Field(alias="sourceDescriptionsPreserved")
    line_count: int = Field(alias="lineCount", gt=0)
    packages: list[_TranslatedPackage]


def _without_translation(package: NormalizedInvoicePackage) -> dict:
    return package.model_copy(update={"lines": [
        line.model_copy(update={"translation": None}) for line in package.lines
    ]}).model_dump(mode="json", by_alias=True)


def load_review_snapshot(
    intake_uri: str, *, workflow_result_uri: Optional[str] = None,
    translation_report_uri: Optional[str] = None,
    read: Callable[[str], bytes] = read_source,
) -> dict:
    manifest = IntakeManifest.model_validate_json(read(intake_uri))
    request = DemoWorkflowRequest.model_validate_json(read(manifest.request_uri))
    if request.workflow_run_id != manifest.workflow_run_id:
        raise ValueError("Intake and request workflowRunId do not match.")
    packages = []
    for reference in request.packages:
        package = NormalizedInvoicePackage.model_validate_json(read(reference.package_uri))
        if (package.package_id != reference.package_id
                or package.run_id != reference.run_id
                or package.run_id != manifest.workflow_run_id
                or package.invoice_id != reference.expected_invoice_id
                or package.source.sha256 != reference.expected_source_sha256):
            raise ValueError("Package identity does not match its intake reference.")
        if package.context_overlay is not None:
            raise ValueError("This source visual does not accept synthetic context overlays.")
        packages.append(package)
    sources = {package_id: source for source in manifest.sources for package_id in source.package_ids}
    package_ids = [package.package_id for package in packages]
    if (len(set(package_ids)) != len(package_ids)
            or set(package_ids) != set(sources)
            or len(packages) != manifest.package_count
            or sum(len(package.lines) for package in packages) != manifest.line_count):
        raise ValueError("Intake package or line coverage is inconsistent.")
    if any(package.source.sha256 != sources[package.package_id].sha256
           or package.source.file_name != sources[package.package_id].file_name for package in packages):
        raise ValueError("Package source identity does not match the source manifest.")

    if translation_report_uri is not None:
        report = _TranslationReport.model_validate_json(read(translation_report_uri))
        references = {item.package_id: item.package_uri for item in report.packages}
        if (report.workflow_run_id != manifest.workflow_run_id
                or set(references) != set(package_ids)
                or len(references) != len(report.packages)
                or report.line_count != manifest.line_count):
            raise ValueError("Translation report must cover this exact run and every invoice line.")
        translated_packages = []
        for original in packages:
            translated = NormalizedInvoicePackage.model_validate_json(read(references[original.package_id]))
            if (_without_translation(original) != _without_translation(translated)
                    or any(line.translation is None for line in translated.lines)):
                raise ValueError("Translation must preserve every source fact and cover every line.")
            translated_packages.append(translated)
        packages = translated_packages

    result = (DemoWorkflowResult.model_validate_json(read(workflow_result_uri))
              if workflow_result_uri is not None else None)
    expected = {(package.package_id, package.invoice_id, line.line_id)
                for package in packages for line in package.lines}
    if result is not None:
        if (result.workflow_run_id != manifest.workflow_run_id or result.errors
                or result.status.value != "SUCCEEDED"
                or {(item.package_id, item.invoice_id, item.line_id)
                    for item in result.task_determinations} != expected
                or {(item.package_id, item.invoice_id, item.line_id)
                    for item in result.assessments} != expected
                or {(item.package_id, item.invoice_id, item.line_count) for item in result.packages}
                != {(package.package_id, package.invoice_id, len(package.lines)) for package in packages}):
            raise ValueError("Workflow result must be successful and cover this exact run and source lines.")
    tasks = {(item.package_id, item.line_id): item for item in result.task_determinations} if result else {}
    assessments = {(item.package_id, item.line_id): item for item in result.assessments} if result else {}
    rows = []
    for package in packages:
        source = sources[package.package_id]
        _validate_base(source.uri)
        source_url = source.uri if urlparse(source.uri).scheme == "https" else Path(source.uri).resolve().as_uri()
        for line in package.lines:
            key = (package.package_id, line.line_id)
            task, assessment = tasks.get(key), assessments.get(key)
            rows.append({
                "key": json.dumps([manifest.workflow_run_id, package.package_id, line.line_id]),
                "packageId": package.package_id, "invoiceId": package.invoice_id,
                "invoiceNumber": package.invoice_number, "vendor": package.vendor_name,
                "region": package.country_region, "sourceFile": source.file_name,
                "sourceUrl": source_url, "sourceSha256": source.sha256,
                "sourceReferences": package.source_references,
                "poNumber": line.source_purchase_order or package.po_number,
                "lineId": line.line_id, "lineNumber": line.line_number,
                "description": line.description, "amount": line.line_amount, "currency": line.currency,
                "translation": line.translation.model_dump(mode="json", by_alias=True) if line.translation else None,
                "classification": (task.suggested_task_code or task.suggested_asset_class) if task else None,
                "classificationType": task.classification_type if task else None,
                "category": (assessment.category_suggestion or assessment.outcome_category.value) if assessment else "Awaiting workflow",
                "assessment": assessment.model_dump(mode="json", by_alias=True) if assessment else None,
                "task": task.model_dump(mode="json", by_alias=True) if task else None,
                "reviewReasons": package.data_quality.review_reasons,
            })
    payload = {
        "schemaVersion": "1.0-capex-offline-review", "workflowRunId": manifest.workflow_run_id,
        "mode": "RECORDED_WORKFLOW" if result else "SOURCE_PREVIEW",
        "businessApproved": False, "sqlWrites": 0, "popaWrites": 0,
        "excludedRecordCount": manifest.excluded_record_count,
        "intake": manifest.model_dump(mode="json", by_alias=True), "rows": rows,
        "recordedWorkflow": result.model_dump(mode="json", by_alias=True) if result else None,
    }
    payload["snapshotSha256"] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")).hexdigest()
    return payload


def render_review_visual(snapshot: dict) -> str:
    payload = json.dumps(snapshot, ensure_ascii=True).replace("<", "\\u003c").replace("&", "\\u0026")
    template = Path(__file__).with_name("review-visual.html").read_text(encoding="utf-8")
    return template.replace("/*REVIEW_SNAPSHOT*/", payload, 1)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intake", required=True)
    parser.add_argument("--workflow-result")
    parser.add_argument("--translation-report")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    snapshot = load_review_snapshot(
        args.intake, workflow_result_uri=args.workflow_result,
        translation_report_uri=args.translation_report,
    )
    output = Path(args.output)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(render_review_visual(snapshot))
    print(json.dumps({"output": str(output.resolve()), "mode": snapshot["mode"],
                      "lineCount": len(snapshot["rows"]), "sqlWrites": 0, "popaWrites": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
