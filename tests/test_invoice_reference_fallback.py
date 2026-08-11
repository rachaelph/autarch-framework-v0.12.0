import json
import sys
from pathlib import Path


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
sys.path.insert(0, str(EXAMPLES_DIR))

import refdata  # noqa: E402
import extract_invoice  # noqa: E402


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

    assert [row["task_code"] for row in rows] == ["TC-9040", "TC-9060", "TC-9060"]
    assert [row["item_type"] for row in rows] == [
        "Equipment Rental",
        "Finance Charges",
        "Finance Charges",
    ]


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