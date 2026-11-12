"""Fresh, audited PDF extraction from the exact pinned ADF archive bytes."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from time import monotonic
from typing import Callable, Sequence

from ..intelligence.base import ModelProvider
from ..intelligence.vision import ImageRef
from .demo_v2_intake import IntakeManifest, _join, _validate_archive
from .demo_v2_mvp import (
    SYSTEM, ExtractedInvoice, MvpDocument,
    extraction_prompt, normalized_package, render_pdf, validate_extraction,
)
from .demo_v2_normalization import NormalizationDataQuality, NormalizationStatus, NormalizedInvoicePackage


ADF_EXTRACTION_PROMPT_VERSION = "adf-extraction-v5"


@dataclass
class ExtractedBatch:
    packages: list[NormalizedInvoicePackage]
    documents: dict[str, dict]
    calls: list[dict]


def extract_adf_pdfs(
    *, packages: Sequence[NormalizedInvoicePackage], intake: IntakeManifest,
    provider: ModelProvider, run_uri: str, read: Callable[[str], bytes],
    write: Callable[[str, object], None],
    allow_provisional_recommendations: bool = False,
    corroborate_archive_currency: bool = False,
    render: Callable[[bytes], list[ImageRef]] = render_pdf,
) -> ExtractedBatch:
    if not provider.supports_vision():
        raise ValueError("Fresh ADF PDF extraction requires a vision-capable provider.")
    source_by_package = {package_id: source for source in intake.sources for package_id in source.package_ids}
    output = ExtractedBatch([], {}, [])
    for package in packages:
        source = source_by_package[package.package_id]
        if source.format != "ABBYY_ZIP":
            output.packages.append(package)
            continue
        data = read(source.uri)
        if hashlib.sha256(data).hexdigest() != source.sha256:
            raise ValueError("Source archive changed between intake and PDF extraction.")
        _validate_archive(data, source.file_name)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = [member for member in archive.namelist() if member.lower().endswith(".pdf")]
            if len(members) != 1:
                raise ValueError("Each selected ABBYY archive must contain exactly one original PDF.")
            member = members[0]
            pdf = archive.read(member)
        if not pdf.startswith(b"%PDF-"):
            raise ValueError("Archive PDF member does not contain PDF bytes.")
        digest = hashlib.sha256(pdf).hexdigest()
        started = monotonic()
        call = {
            "stage": "EXTRACTION", "packageId": package.package_id,
            "modelDeployment": "gpt-5.1", "promptVersion": ADF_EXTRACTION_PROMPT_VERSION,
            "sourceArchiveSha256": source.sha256, "pdfSha256": digest,
            "archiveMember": member, "pageCount": None,
            "systemPrompt": SYSTEM, "prompt": None, "response": None, "error": None,
        }
        try:
            images = render(pdf)
            if not images:
                raise ValueError("PDF rendering produced no pages.")
            payload = json.loads(extraction_prompt(len(images)))
            payload["promptVersion"] = ADF_EXTRACTION_PROMPT_VERSION
            payload["instructions"] += (
                " Inspect each page header for Date or Invoice Date, not Due Date. "
                "Return invoiceDate explicitly, with its printed quote and page reference; "
                "do not omit a clearly visible, unambiguous date. Return null if ambiguous. "
                "Do not replace missing service quantities, unit prices or header adjustments "
                "with invented values. Preserve each current-charge amount, not contract value "
                "or amounts billed previously."
            )
            payload["responseSchema"]["required"].append("invoiceDate")
            attempt = 1
            while True:
                prompt = json.dumps(payload)
                call.update(pageCount=len(images), prompt=prompt, attempt=attempt)
                raw = provider.complete_vision(prompt, images, system=SYSTEM)
                call["response"] = raw
                invoice = ExtractedInvoice.model_validate_json(raw)
                reasons = validate_extraction(invoice, len(images))
                blocking_reasons = validate_extraction(
                    invoice, len(images), allow_incomplete_details=allow_provisional_recommendations,
                    corroborated_currency=package.currency if corroborate_archive_currency else None,
                )
                call["validationError"] = "; ".join(blocking_reasons) if blocking_reasons else None
                if attempt == 2 or not corroborate_archive_currency or (
                    "Extracted line sum does not reconcile to subtotal." not in blocking_reasons
                ):
                    break
                call["durationSeconds"] = monotonic() - started
                write(_join(run_uri, "extraction", f"{package.package_id}.attempt-1.json"), call)
                output.calls.append(call)
                payload["instructions"] += (
                    " The previous extraction failed arithmetic validation: "
                    + "; ".join(blocking_reasons) + " "
                    "Reread the ORIGINAL PDF and distinguish current charges from contract values, "
                    "cumulative billings and previously billed amounts. Correct only what the "
                    "printed evidence supports. Do not invent or adjust values to make totals balance; "
                    "if the source itself is inconsistent, retain the inconsistency for review. "
                    "Return ONLY fields from responseSchema. Validation feedback is input context, "
                    "not an invoice field; do not echo it or any prompt metadata in the response."
                )
                call = {**call, "response": None, "validationError": None, "error": None}
                started = monotonic()
                attempt += 1
        except Exception as exc:
            call["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            call["durationSeconds"] = monotonic() - started
            write(_join(run_uri, "extraction", f"{package.package_id}.json"), call)
        output.calls.append(call)
        document = {
            "documentId": package.invoice_id, "uri": source.uri,
            "sha256": digest, "mediaType": "application/pdf",
            "archiveSha256": source.sha256, "archiveMember": member,
        }
        output.documents[package.package_id] = {
            "document": document, "extraction": invoice.model_dump(mode="json", by_alias=True),
            "reviewReasons": reasons,
            "blockingReviewReasons": blocking_reasons,
            "provisionalRecommendations": bool(reasons and not blocking_reasons),
        }
        output.documents[package.package_id]["extraction"]["recommendationEligibility"] = {
            "mode": "PROVISIONAL_DEMO" if allow_provisional_recommendations else "STRICT",
            "reviewReasons": reasons, "blockingReviewReasons": blocking_reasons,
            "requiresAnalystReview": True,
        }
        if corroborate_archive_currency:
            output.documents[package.package_id]["extraction"]["archiveCurrencyEvidence"] = {
                "value": package.currency, "source": "ABBYY invoice header Currency field",
                "archiveSha256": source.sha256, "pdfCurrency": invoice.currency,
                "notPdfPageEvidence": True,
            }
        if blocking_reasons:
            # Keep preprocessed facts only as source evidence; invalid extraction must not be assessed.
            quality = package.data_quality.model_copy(update={
                "review_reasons": [*package.data_quality.review_reasons, *reasons],
            })
            output.packages.append(package.model_copy(update={
                "normalization_status": NormalizationStatus.REVIEW_REQUIRED, "data_quality": quality,
            }))
            continue
        effective_invoice = invoice.model_copy(update={"currency": package.currency}) \
            if corroborate_archive_currency and invoice.currency is None else invoice
        if effective_invoice.currency is None:
            raise ValueError("Validated PDF extraction unexpectedly lacks currency.")
        extracted = normalized_package(
            MvpDocument(document_id=package.invoice_id, uri=source.uri,
                        sha256=digest, country_region=package.country_region),
            effective_invoice, package.run_id, package.created_at_utc,
        )
        output.packages.append(extracted.model_copy(update={
            "package_id": package.package_id, "source": package.source,
            "normalization_status": (
                NormalizationStatus.REVIEW_REQUIRED if reasons else NormalizationStatus.NORMALIZED
            ),
            "data_quality": NormalizationDataQuality(
                source_currencies=[effective_invoice.currency], review_reasons=reasons,
                missing_quantity_line_ids=[line.line_id for line in extracted.lines if line.quantity is None],
                missing_unit_cost_line_ids=[line.line_id for line in extracted.lines if line.unit_cost is None],
            ),
        }))
    return output
