import json
import sys
from pathlib import Path


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
sys.path.insert(0, str(EXAMPLES_DIR))

import refdata  # noqa: E402
import extract_invoice  # noqa: E402
from autarch.evaluation import check_grounding  # noqa: E402


def _reference_data():
    reference_dir = EXAMPLES_DIR / "reference"
    return {
        "task_codes": json.loads((reference_dir / "seed-task-codes.json").read_text(encoding="utf-8")),
        "po_records": json.loads((reference_dir / "seed-po-records.json").read_text(encoding="utf-8")),
        "taxability": json.loads((reference_dir / "seed-taxability-matrix.json").read_text(encoding="utf-8")),
    }


def test_reference_classifications_use_po_task_and_expense_overrides():
    data = _reference_data()
    rows = refdata.reference_classifications(
        data,
        {"invoice_number": "R3645629", "po_number": "2543082", "vendor_name": "CBE"},
        [{"description": "BOSCH TRITECH 360 PIR WHT"}, {"description": "FUEL SURCHARGE"}],
    )

    assert [row["task_code"] for row in rows] == ["TC-5020", "TC-9050"]
    assert [row["item_type"] for row in rows] == [
        "Security & Surveillance Systems",
        "Freight & Delivery",
    ]
    assert refdata.taxability(data, "MO", rows[0]["item_type"])[0] == "T"


def test_reference_classifications_split_rental_and_finance_charges():
    data = _reference_data()
    rows = refdata.reference_classifications(
        data,
        {
            "invoice_number": "9023112177",
            "po_number": "3595 S Yosemite St",
            "vendor_name": "WillScot",
        },
        [
            {"description": "PREMIUM DOOR CONTAINER"},
            {"description": "LIABILITY WAIVER"},
            {"description": "INTEREST CHARGE"},
        ],
    )

    assert [row["task_code"] for row in rows] == ["TC-9040", "TC-9040", "TC-9060"]
    assert [row["item_type"] for row in rows] == [
        "Equipment Rental",
        "Equipment Rental",
        "Finance Charges",
    ]
    assert rows[1]["_reference_override"] is True


def test_reference_classifications_override_distinct_cabinet_line():
    data = _reference_data()
    rows = refdata.reference_classifications(
        data,
        {
            "invoice_number": "119806",
            "po_number": "4701224FCBsignage",
            "vendor_name": "Corporate Interiors",
        },
        [{"description": "CUSTOM CABINET"}],
    )

    assert rows[0]["task_code"] == "TC-6020"
    assert rows[0]["item_type"] == "Construction Materials"
    assert rows[0]["_reference_override"] is True


def test_reference_classifications_do_not_guess_without_po_match():
    data = _reference_data()

    assert refdata.reference_classifications(
        data,
        {"invoice_number": "UNKNOWN", "po_number": "UNKNOWN", "vendor_name": "Unknown Vendor"},
        [{"description": "Equipment"}],
    ) == [{}]


def test_classify_lines_fills_empty_model_response_from_reference_data():
    class EmptyProvider:
        def complete(self, prompt, system=None):
            return "{}"

    rows = extract_invoice.classify_lines(
        EmptyProvider(),
        {"invoice_number": "R3645629", "po_number": "2543082", "vendor_name": "CBE"},
        [{"description": "HONEYWELL GLASSBREAK DETECTOR", "quantity": "3", "amount": "267.00"}],
        _reference_data(),
    )

    assert rows[0]["task_code"] == "TC-5020"
    assert rows[0]["item_type"] == "Security & Surveillance Systems"
    assert rows[0]["_reference_fallback"] is True


def test_classify_lines_keeps_task_item_type_consistent():
    class InconsistentProvider:
        def complete(self, prompt, system=None):
            return json.dumps({"lines": [{
                "capex_opex": "CapEx",
                "asset_category": "Post guards",
                "item_type": "Security & Surveillance Systems",
                "task_code": "TC-3100",
                "confidence": 0.95,
                "rationale": "Protective equipment.",
            }]})

    rows = extract_invoice.classify_lines(
        InconsistentProvider(),
        {"invoice_number": "178225", "po_number": "2338185", "vendor_name": "Encore"},
        [{"description": "4.5 POST GUARD COVER", "quantity": "3", "amount": "168.75"}],
        _reference_data(),
    )

    assert rows[0]["task_code"] == "TC-3100"
    assert rows[0]["item_type"] == "Construction Materials"


def test_dakota_ocr_motor_typo_uses_po_repair_task():
    class RefrigerationProvider:
        def complete(self, prompt, system=None):
            return json.dumps({"lines": [{
                "capex_opex": "OpEx",
                "asset_category": "Refrigeration Equipment",
                "item_type": "HVAC & Mechanical",
                "task_code": "TC-1060",
                "confidence": 0.9,
                "rationale": "Motor equipment.",
            }]})

    rows = extract_invoice.classify_lines(
        RefrigerationProvider(),
        {
            "invoice_number": "30938",
            "po_number": "FWKD-255-8156",
            "vendor_name": "Dakota Car Wash & Equipment, Inc.",
        },
        [{"description": "10 HP RYKO DRYER MOTOER RYKO", "quantity": "1", "amount": "1042.18"}],
        _reference_data(),
    )

    assert rows[0]["task_code"] == "TC-3020"
    assert rows[0]["asset_category"] == "CONSTRUCTION CONTRACTS"
    assert rows[0]["item_type"] == "MATERIAL / PARTS / TOOLS / EQUIPMENT FOR REPAIRING"
    assert rows[0]["_reference_override"] is True


def test_installation_keeps_asset_task_but_uses_service_tax_type():
    data = _reference_data()
    rows = refdata.reference_classifications(
        data,
        {"invoice_number": "R3645629", "po_number": "2543082", "vendor_name": "CBE"},
        [{"description": "INSTALL ALARM"}],
    )

    assert rows[0]["task_code"] == "TC-5030"
    assert rows[0]["capex_opex"] == "CapEx"
    assert rows[0]["item_type"] == "TANGIBLE PERSONAL PROPERTY LABOR: INSTALLATION"
    assert rows[0]["_reference_override"] is True


def test_loris_invoice_descriptions_use_deterministic_po_overrides():
    data = _reference_data()
    cases = [
        (
            {"invoice_number": "108007", "po_number": "FWKD3152143", "vendor_name": "FOX GLASS ORLANDO, INC."},
            ["Labor", "Trip", "3-0 x 7-0 Steel door"],
            [("TC-9030", "Professional Services"), ("TC-9030", "Professional Services"),
             ("TC-9010", "Construction Materials")],
        ),
        (
            {"invoice_number": "80530", "po_number": "646449", "vendor_name": "MASONWAYS"},
            ["TRACKING # 545785834B", "SKID CHARGE"],
            [("TC-9050", "Freight & Delivery"), ("TC-9050", "Freight & Delivery")],
        ),
        (
            {"invoice_number": "121767T", "po_number": "FWKD2660328", "vendor_name": "PETRO TOWERY"},
            ["VERIFONE CARD READER - UX300", "DOCUMENT PROCESS FEE CK", "Environmental Fee", "CONSUMABLES FEE"],
            [("TC-5030", "IT & Electronics"), ("TC-9060", "Finance Charges"),
             ("TC-9010", "Construction Materials"), ("TC-9010", "Construction Materials")],
        ),
    ]

    for header, descriptions, expected in cases:
        rows = refdata.reference_classifications(
            data, header, [{"description": description} for description in descriptions]
        )
        assert [(row["task_code"], row["item_type"]) for row in rows] == expected


def test_normalize_extraction_rejects_subtotal_as_tax_and_prefers_reconciled_lines():
    header = {"total_amount": "267.15", "tax_charged": "212.95", "subtotal": "212.95"}
    di_lines = [{"description": "Rental", "amount": "212.95"}]
    model_lines = [
        {"description": "Rental", "amount": "212.95", "tax_status": "T"},
        {"description": "Late Payment Fee", "amount": "35.00", "tax_status": "N"},
        {"description": "Other Fees", "amount": "19.20", "tax_status": "N"},
    ]

    normalized_header, lines, issues = extract_invoice.normalize_extraction(
        header, di_lines, model_lines
    )

    assert normalized_header["tax_charged"] == ""
    assert [line["description"] for line in lines] == [
        "Rental", "Late Payment Fee", "Other Fees",
    ]
    assert lines[0]["tax_status"] == "T"
    assert "rejected_tax_equal_to_subtotal" in issues
    assert "used_reconciled_model_lines" in issues


def test_normalize_extraction_enriches_di_lines_with_printed_tax_status():
    header = {"total_amount": "100.00", "tax_charged": ""}
    di_lines = [{"description": "Tariff Surcharge", "amount": "100.00"}]
    model_lines = [{"description": "Tariff Surcharge", "amount": "100.00", "tax_status": "N"}]

    _, lines, _ = extract_invoice.normalize_extraction(header, di_lines, model_lines)

    assert lines[0]["tax_status"] == "N"


def test_normalize_extraction_clears_model_tax_absent_from_authoritative_header():
    header = {"total_amount": "267.15", "tax_charged": "54.87"}

    normalized, _, issues = extract_invoice.normalize_extraction(
        header,
        [{"description": "Rental", "amount": "212.95"}],
        [],
        authoritative_header={"total_amount": 267.15, "subtotal": 212.95},
    )

    assert normalized["tax_charged"] == ""
    assert "cleared_unverified_model_tax" in issues


def test_printed_tax_marker_conflict_routes_to_review_without_overriding_matrix():
    data = _reference_data()
    lines = [{"description": "Tariff Surcharge", "amount": "100.00", "tax_status": "N"}]
    classifications = [{
        "capex_opex": "OpEx",
        "asset_category": "Freight and delivery",
        "item_type": "Freight & Delivery",
        "task_code": "TC-9050",
        "confidence": 0.95,
    }]
    taxes = extract_invoice.apply_tax_matrix({"state": "OH"}, classifications, data)

    rows = extract_invoice.build_line_results(
        lines, classifications, taxes, 0.8, {"state": "OH", "tax_charged": ""}, data
    )

    assert rows[0]["taxable"] is True
    assert rows[0]["tax_status"] == "N"
    assert rows[0]["vendor_tax_conflict"] is True
    assert rows[0]["tax_exception"] is True
    assert rows[0]["route"] == "SME_REVIEW"

    rollup = extract_invoice.summarize_lines(rows, {"tax_charged": ""})
    assert rows[0]["tax_rate_scope"] == "state_base_only"
    assert rollup["tax_provisional"] is True
    assert rollup["n_state_base_rates"] == 1


def test_ambiguous_taxability_is_unresolved_instead_of_balanced():
    data = _reference_data()
    lines = [{"description": "SHOP SUPPLIES", "amount": "20.00", "tax_status": "T"}]
    classifications = [{
        "capex_opex": "OpEx",
        "asset_category": "Maintenance and Repair",
        "item_type": "Construction Materials",
        "task_code": "TC-9010",
        "confidence": 0.9,
    }]
    taxes = extract_invoice.apply_tax_matrix({"state": "ND"}, classifications, data)
    rows = extract_invoice.build_line_results(
        lines, classifications, taxes, 0.85, {"state": "ND", "tax_charged": ""}, data
    )

    rollup = extract_invoice.summarize_lines(rows, {"tax_charged": ""})

    assert rows[0]["expected_tax_amount"] is None
    assert rows[0]["tax_delta"] is None
    assert rows[0]["use_tax_to_allocate"] is None
    assert rollup["expected_tax_total"] is None
    assert rollup["tax_status"] == "unresolved"
    assert rollup["tax_recon_exception"] is True
    assert "UNRESOLVED" in extract_invoice._tax_recon_html(rollup)


def test_source_validation_warning_detects_project_date_error():
    text = (
        "Invalid project based on PO or Invoice date. Date must fall within project "
        "start and end dates. in Line #1 Dist #1"
    )

    warnings = extract_invoice.detect_source_warnings(text)

    assert warnings == [{
        "code": "project_date_invalid",
        "message": text,
        "blocking": True,
    }]


def test_source_validation_warning_detects_invalid_project_status_or_task():
    text = "Error - review project for open status or invalid task."

    warnings = extract_invoice.detect_source_warnings(text)

    assert warnings == [{
        "code": "project_status_or_task_invalid",
        "message": text,
        "blocking": True,
    }]


def test_merge_source_text_preserves_warning_omitted_by_ocr():
    ocr = "Dakota Car Wash Invoice 30938"
    embedded = "Invalid project based on PO or Invoice date. Date must fall within project start and end dates. in Line #1 Dist #1"

    merged = extract_invoice.merge_source_text(ocr, embedded)

    assert ocr in merged
    assert extract_invoice.detect_source_warnings(merged)[0]["code"] == "project_date_invalid"


def test_pdf_text_layer_recovers_dakota_project_warning():
    pdf = EXAMPLES_DIR.parent / "testing" / "invoices" / "Loris_Invoices" / "Dakota Car - 30938.pdf"

    text = extract_invoice.pdf_text_layer(pdf)

    assert "Invalid project based on PO or Invoice date" in text


def test_ask_retries_transient_provider_failure():
    class FlakyProvider:
        calls = 0

        def complete(self, prompt, system=None):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("temporary connection error")
            return '{"ok": true}'

    provider = FlakyProvider()

    assert extract_invoice._ask(provider, "test", "system", "prompt") == {"ok": True}
    assert provider.calls == 2


def test_precedents_exclude_the_invoice_currently_being_processed():
    data = {
        "history": [
            {"invoice_number": "30938", "vendor_name": "Dakota Car Wash", "ship_to_state": "ND",
             "item_type": "Construction Materials", "overall_confidence": 0.4,
             "routing_result": "human_review", "taxability": "Pending"},
            {"invoice_number": "30901", "vendor_name": "Dakota Car Wash", "ship_to_state": "ND",
             "item_type": "Construction Materials", "overall_confidence": 0.8,
             "routing_result": "auto_approved", "taxability": "Taxable"},
        ]
    }

    matches, summary = refdata.precedents(
        data, "Dakota Car Wash", "Construction Materials", "ND", exclude_invoice_number="30938"
    )

    assert [match["invoice_number"] for match in matches] == ["30901"]
    assert summary["count"] == 1


def test_precedents_reject_shared_item_type_without_vendor_or_state_match():
    data = {"history": [{
        "invoice_number": "118500", "vendor_name": "Corporate Interiors", "ship_to_state": "NC",
        "item_type": "Professional Services", "overall_confidence": 0.933,
        "routing_result": "auto_approved", "taxability": "Exempt",
    }]}

    matches, summary = refdata.precedents(
        data, "Dakota Car Wash", "Professional Services", "ND", exclude_invoice_number="30938"
    )

    assert matches == []
    assert summary == {"count": 0}


def test_state_base_only_tax_estimate_cannot_auto_post():
    data = _reference_data()
    lines = [{"description": "VERIFONE CARD READER - UX300", "amount": "1210.59"}]
    classifications = [{
        "capex_opex": "OpEx",
        "asset_category": "Point-of-Sale Equipment",
        "item_type": "IT & Electronics",
        "task_code": "TC-5030",
        "confidence": 0.97,
    }]
    taxes = extract_invoice.apply_tax_matrix({"state": "OH"}, classifications, data)

    rows = extract_invoice.build_line_results(
        lines, classifications, taxes, 0.85, {"state": "OH", "tax_charged": ""}, data
    )

    assert rows[0]["tax_rate_scope"] == "state_base_only"
    assert rows[0]["state_base_estimate"] is True
    assert rows[0]["route"] == "SME_REVIEW"


def test_governed_description_override_is_not_blocked_by_semantic_neighbor():
    data = _reference_data()
    lines = [{"description": "DOCUMENT PROCESS FEE CK", "amount": "1.95"}]
    classifications = [{
        "capex_opex": "OpEx",
        "asset_category": "Finance and Administrative Charges",
        "item_type": "Finance Charges",
        "task_code": "TC-9060",
        "confidence": 0.97,
        "_reference_override": True,
        "_sem": {
            "item_type": "Construction Materials",
            "task_code": "TC-9010",
            "item_score": 0.7,
            "task_score": 0.7,
        },
    }]
    taxes = extract_invoice.apply_tax_matrix({"state": "OH"}, classifications, data)

    rows = extract_invoice.build_line_results(
        lines, classifications, taxes, 0.85, {"state": "OH", "tax_charged": ""}, data
    )

    assert rows[0]["mapping_conflict"] is True
    assert rows[0]["tax_exception"] is False
    assert rows[0]["route"] == "AUTO_POST"


def test_grounding_accepts_equivalent_date_format_only():
    source = "Invoice Date 2/18/2025"

    assert check_grounding({"invoice_date": "2025-02-18"}, source) == []
    assert check_grounding({"invoice_date": "2025-02-19"}, source)