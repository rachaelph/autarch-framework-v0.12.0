from __future__ import annotations

import pytest

from autarch.capex.demo_v2_normalization import LineDescriptionTranslation, normalize_abbyy_for_demo_v2
from autarch.capex.demo_v2_nuvolo import resolve_nuvolo_work_order
from tests.test_abbyy_adapter import HEADER_FIELDS, valid_header, write_package
from tests.test_capex_demo_v2_task_suggestion import NOW, _line, _package


@pytest.mark.parametrize(("field", "reference", "number", "risk"), [
    ("Customer PO", "FWKD7819574", "7819574", True),
    ("Customer PO Number", "FWKD0001234", "0001234", True),
    ("Purchase Order", "fwkd: 7819574", "7819574", True),
    ("PO Number", "PO reference FWKD-7819574", "7819574", True),
    ("Customer PO", "841305", None, False),
    ("Customer PO", "FFX7819574", None, False),
    ("Customer PO", "XFWKD7819574", None, True),
    ("Customer PO", "FWKD7819574ABC", None, True),
    ("Customer PO", "FWKD7819574-ABC", None, True),
    ("Customer PO", "FWKD7819574.1", None, True),
    ("Customer PO", "FWKD", None, True),
    ("Customer PO", "FWKD" + "1" * 129, None, True),
    ("Work Order Number", "FWKD7819574", None, False),
    ("Invoice Number", "FWKD7819574", None, False),
    ("Description", "Repair for FWKD7819574", None, False),
    ("File Name", "FWKD7819574.pdf", None, False),
])
def test_only_fwkd_in_purchase_order_fields_establishes_nuvolo(field, reference, number, risk):
    context = resolve_nuvolo_work_order({field: reference})
    assert context.work_order_no == number
    assert context.risk_flag is risk
    assert bool(context.review_reasons) is (risk and number is None)


def test_repeated_same_po_is_not_a_conflict_but_distinct_or_malformed_pos_require_review():
    repeated = resolve_nuvolo_work_order({
        "Customer PO": "FWKD0001234 / FWKD0001234",
        "PO Number": "FWKD0001234",
        "Work Order Number": "FWKD9999999",
    })
    assert repeated.work_order_no == "0001234" and not repeated.review_reasons
    for second in ("FWKD9999999", "FWKD12X"):
        context = resolve_nuvolo_work_order({"Customer PO": "FWKD0001234", "PO Number": second})
        assert context.risk_flag and context.work_order_no is None and context.review_reasons


def test_source_labels_descriptions_invoice_numbers_and_translations_are_not_po_evidence():
    line = _line("line-1", "Repair for FWKD7819574").model_copy(update={
        "source_document_number": "FWKD7819574", "source_invoice_number": "FWKD7819574",
        "translation": LineDescriptionTranslation(
            source_language="fr", language_detection_score=1, english_text="FWKD7819574",
        ),
    })
    package = _package([line]).model_copy(update={
        "invoice_number": "FWKD7819574", "po_number": "841305",
        "source_references": {"Work Order Number": "FWKD7819574"},
    })
    package = package.model_copy(update={"source": package.source.model_copy(update={
        "source_system": "NUVOLO", "file_name": "FWKD7819574.zip",
    })})
    assert not package.nuvolo_context.risk_flag
    assert package.nuvolo_context.work_order_no is None


def test_original_line_purchase_orders_are_recognized_without_changing_the_popa_po():
    line = _line("line-1", "Routine service").model_copy(update={"source_purchase_order": "FWKD0001234"})
    package = _package([line]).model_copy(update={"po_number": "841305"})
    assert package.nuvolo_context.work_order_no == "0001234"
    assert package.po_number == "841305"


def test_abbyy_retains_customer_po_but_not_bank_fields_or_child_csv_references(tmp_path):
    header = {**valid_header(), "Customer PO": "FWKD7819574", "Work Order Number": "FWKD9999999"}
    header["Purchase Order"] = "FWKD8121179.csv"
    path = write_package(
        tmp_path / "source.zip", headers=[header], header_fields=[*HEADER_FIELDS, "Customer PO", "Purchase Order"],
    )
    package = normalize_abbyy_for_demo_v2(path, run_id="nuvolo-test", created_at_utc=NOW)
    assert package.nuvolo_context.work_order_no == "7819574"
    assert package.source_references["Customer PO"] == "FWKD7819574"
    assert "Purchase Order" not in package.source_references
    assert not any("Bank" in key for key in package.source_references)
