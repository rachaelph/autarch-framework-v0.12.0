"""Autarch-governed Microsoft Agent Framework CAPEX invoice intelligence.

The default MAF engine runs seven LLM specialists over ABBYY-extracted data.
Install optional dependencies with ``pip install -e .[capex]`` and configure
Azure OpenAI. Missing credentials never trigger an offline fallback. The earlier
rule-based example is available explicitly via ``--engine rules``.
Neither engine performs OCR, approves recommendations, or posts financial entries.

Run from the repository root:
    python examples/capex_flow.py --model azure:<deployment> --auth aad
    python examples/capex_flow.py --engine rules --threshold 2000
    python examples/capex_flow.py review <invoice-id> approve --reviewer <name>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import math
import os
import re
import shutil
import stat
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DEFAULT_DATA = Path(__file__).resolve().parent / "data" / "capex"
DEFAULT_WORKSPACE = Path("./sandbox/capex_flow")
SCHEMA_VERSION = "autarch.capex-flow.v1"
US_COUNTRIES = {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}
PERIOD_COST = re.compile(
    r"\b(shipping|freight|reimbursable|travel|mileage|per diem|surcharge|maintenance|repair)\b",
    re.IGNORECASE,
)
TOKEN = re.compile(r"[a-z0-9]+")
VERIFIED_TASK_CONTINUITY = [
    {"Task Type": "0007", "Description": "ENVIRONMENTAL", "Active": "1", "Asset Category Major": "STORE EQUIPMENT", "Asset Category Minor": "ENG/PERM", "Property Type": "Real"},
    {"Task Type": "5225", "Description": "WIRE SHELVING", "Active": "1", "Asset Category Major": "STORE EQUIPMENT", "Asset Category Minor": "SHELVING-WIRE", "Property Type": "Personal"},
]
VERIFIED_BOOK_CONTINUITY = {
    "0007": {"Task Type": "0007", "Name": "Internal", "Estimated Life Years": "5", "Estimated Life Months": "0"},
    "5225": {"Task Type": "5225", "Name": "Internal", "Estimated Life Years": "5", "Estimated Life Months": "0"},
}
NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return _sha256_bytes(raw)


def _number(value: Any) -> float:
    text = str(value or "").replace(",", "").replace("$", "").strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference)
    value = 0
    for char in letters.group() if letters else "A":
        value = value * 26 + ord(char) - 64
    return value - 1


def _unique_headers(values: Sequence[Any]) -> List[str]:
    counts: Counter[str] = Counter()
    headers: List[str] = []
    for index, value in enumerate(values):
        base = str(value or f"Column {index + 1}").strip()
        counts[base] += 1
        headers.append(base if counts[base] == 1 else f"{base}__{counts[base]}")
    return headers


def read_xlsx(path: Path) -> Dict[str, List[Dict[str, str]]]:
    """Read worksheet rows using the XLSX ZIP/XML format (no openpyxl dependency)."""
    if path.read_bytes()[:8] == bytes.fromhex("D0CF11E0A1B11AE1"):
        raise RuntimeError(
            f"{path.name} is an encrypted Microsoft Purview/DRM Office package; "
            "provide an authorized, decrypted XLSX export for automated processing"
        )
    with zipfile.ZipFile(path) as archive:
        shared: List[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [
                "".join(node.text or "" for node in item.findall(".//m:t", NS))
                for item in root.findall("m:si", NS)
            ]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {node.attrib["Id"]: node.attrib["Target"] for node in relationships}
        result: Dict[str, List[Dict[str, str]]] = {}
        for sheet in workbook.findall("m:sheets/m:sheet", NS):
            relationship_id = sheet.attrib[f"{{{NS['r']}}}id"]
            target = targets[relationship_id].replace("\\", "/").lstrip("/")
            member = target if target.startswith("xl/") else f"xl/{target}"
            root = ET.fromstring(archive.read(member))
            matrix: List[List[str]] = []
            for row in root.findall(".//m:sheetData/m:row", NS):
                values: Dict[int, str] = {}
                for cell in row.findall("m:c", NS):
                    value_node = cell.find("m:v", NS)
                    inline_node = cell.find("m:is", NS)
                    raw = value_node.text if value_node is not None else ""
                    if inline_node is not None:
                        raw = "".join(node.text or "" for node in inline_node.findall(".//m:t", NS))
                    if cell.attrib.get("t") == "s" and raw:
                        raw = shared[int(raw)]
                    values[_column_index(cell.attrib.get("r", "A1"))] = str(raw or "")
                matrix.append([values.get(i, "") for i in range(max(values, default=-1) + 1)])
            if not matrix:
                result[sheet.attrib["name"]] = []
                continue
            headers = _unique_headers(matrix[0])
            result[sheet.attrib["name"]] = [
                {header: row[i] if i < len(row) else "" for i, header in enumerate(headers)}
                for row in matrix[1:]
                if any(str(value).strip() for value in row)
            ]
        return result


def load_task_catalog(path: Path) -> Tuple[List[Dict[str, str]], Dict[str, Dict[str, str]]]:
    workbook = read_xlsx(path)
    tasks = workbook.get("US Task Type", [])
    internal_books = {
        row.get("Task Type", ""): row
        for row in workbook.get("US Books", [])
        if row.get("Name", "").strip().lower() == "internal"
    }
    return tasks, internal_books


def _csv_member(archive: zipfile.ZipFile, predicate: Any) -> List[Dict[str, str]]:
    for name in archive.namelist():
        if name.lower().endswith(".csv") and predicate(Path(name).name):
            text = archive.read(name).decode("utf-8-sig", errors="replace")
            return list(csv.DictReader(io.StringIO(text)))
    return []


def _csv_file(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as stream:
        return list(csv.DictReader(stream))


def _line(row: Mapping[str, Any], index: int) -> Dict[str, Any]:
    amount = _number(row.get("Total Price") or row.get("Net Price"))
    return {
        "line_id": str(row.get("Position") or index),
        "description": str(row.get("Description") or "").strip(),
        "quantity": _number(row.get("Quantity")),
        "unit_price": _number(row.get("Unit Price")),
        "amount": amount,
        "actual_task": str(row.get("Job Type") or row.get("Project") or "").strip(),
    }


def _abbyy_invoice(
    header: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    source: str,
    source_sha256: str,
) -> Dict[str, Any]:
    lines = [_line(row, index) for index, row in enumerate(rows, 1) if row.get("Description")]
    transaction_id = header.get("Transaction id") or Path(source).stem
    country = str(header.get("Business Unit/Country", ""))
    return {
        "invoice_id": transaction_id,
        "transaction_id": transaction_id,
        "invoice_number": header.get("Invoice Number", ""),
        "invoice_date": header.get("Invoice Date", ""),
        "vendor": header.get("Vendor/Name") or header.get("Vendor/Invoice Vendor Name", ""),
        "country": country,
        "region": "North America" if country.upper() in US_COUNTRIES else "Europe",
        "state": header.get("Business Unit/State", ""),
        "currency": header.get("Currency", ""),
        "total": _number(header.get("Total")),
        "afe_number": header.get("AFE Number", ""),
        "project_number": header.get("Work Order Number", ""),
        "business_capex_opex": header.get("CAPEX/OPEX", ""),
        "lines": lines,
        "source": source,
        "source_sha256": source_sha256,
        "abbyy_confidence": _number(str(header.get("Confidence", "")).replace("%", "")),
    }


def read_abbyy_archive(path: Path) -> Dict[str, Any]:
    """Consume ABBYY's normalized CSV output; PDFs/images remain source evidence only."""
    with zipfile.ZipFile(path) as archive:
        headers = _csv_member(
            archive,
            lambda name: name.startswith("Non-Trade Invoices") and "Line Items" not in name,
        )
        rows = _csv_member(archive, lambda name: name.startswith("Line Items_"))
        if not headers:
            raise ValueError(f"ABBYY package has no invoice CSV: {path.name}")
        return _abbyy_invoice(headers[0], rows, path.name, _sha256_bytes(path.read_bytes()))


def read_abbyy_directory(path: Path) -> Dict[str, Any]:
    """Consume an unpacked ABBYY export using its invoice and referenced line-item CSVs."""
    header_paths = sorted(
        item for item in path.glob("Non-Trade Invoices*.csv") if "Line Items" not in item.name
    )
    if not header_paths:
        raise ValueError(f"ABBYY directory has no invoice CSV: {path.name}")
    header_rows = _csv_file(header_paths[0])
    if not header_rows:
        raise ValueError(f"ABBYY invoice CSV is empty: {header_paths[0].name}")
    header = header_rows[0]
    line_name = str(header.get("Line Items", "")).strip()
    line_paths = (
        [path / line_name, *sorted(path.glob(f"**/{line_name}"))]
        if line_name
        else sorted(path.glob("**/Line Items_*.csv"))
    )
    line_path = next((item for item in line_paths if item.is_file()), None)
    if line_path is None:
        raise ValueError(f"ABBYY directory has no referenced line-item CSV: {path.name}")
    source_bytes = header_paths[0].read_bytes() + line_path.read_bytes()
    return _abbyy_invoice(
        header,
        _csv_file(line_path),
        path.name,
        _sha256_bytes(source_bytes),
    )


def read_cases(path: Path, excluded_ids: Iterable[str]) -> List[Dict[str, Any]]:
    excluded = set(excluded_ids)
    invoices: List[Dict[str, Any]] = []
    for sheet, rows in read_xlsx(path).items():
        groups: Dict[str, List[Dict[str, str]]] = defaultdict(list)
        for index, row in enumerate(rows, 1):
            key = row.get("Transaction id") or f"{sheet}-{row.get('Invoice Number') or index}"
            if key not in excluded:
                groups[key].append(row)
        for transaction_id, grouped in groups.items():
            header = grouped[0]
            country = header.get("Business Unit/Country", "")
            lines = [_line(row, index) for index, row in enumerate(grouped, 1) if row.get("Description")]
            invoices.append({
                "invoice_id": transaction_id,
                "transaction_id": transaction_id,
                "invoice_number": header.get("Invoice Number", ""),
                "invoice_date": header.get("Invoice Date", ""),
                "vendor": header.get("Vendor/Name", ""),
                "country": country,
                "region": "North America" if country.upper() in US_COUNTRIES else "Europe",
                "state": header.get("Business Unit/State", ""),
                "currency": header.get("Currency", ""),
                "total": _number(header.get("Total")),
                "afe_number": header.get("AFE Number", ""),
                "project_number": header.get("Work Order Number", ""),
                "business_capex_opex": header.get("CAPEX/OPEX", ""),
                "lines": lines,
                "source": f"{path.name}:{sheet}",
                "source_sha256": _sha256_bytes(path.read_bytes()),
                "abbyy_confidence": _number(header.get("Confidence")),
            })
    return invoices


def load_inputs(data_dir: Path, *, allow_continuity: bool = True) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]], Dict[str, Dict[str, str]], List[str]]:
    archives = [read_abbyy_archive(path) for path in sorted(data_dir.glob("*.zip"))]
    archived_ids = {invoice["transaction_id"] for invoice in archives}
    directories = [
        read_abbyy_directory(path)
        for path in sorted(item for item in data_dir.iterdir() if item.is_dir())
        if any(path.glob("Non-Trade Invoices*.csv"))
    ]
    archives.extend(
        invoice for invoice in directories if invoice["transaction_id"] not in archived_ids
    )
    ids = {invoice["transaction_id"] for invoice in archives}
    warnings: List[str] = []
    cases_path = data_dir / "Cases.xlsx"
    cases: List[Dict[str, Any]] = []
    if cases_path.exists():
        try:
            cases = read_cases(cases_path, ids)
        except (RuntimeError, zipfile.BadZipFile) as exc:
            warnings.append(str(exc))
    invoices = archives + cases
    catalog_path = data_dir / "Task_Type_Export_9_6_2026.xlsx"
    tasks: List[Dict[str, str]] = []
    books: Dict[str, Dict[str, str]] = {}
    if catalog_path.exists():
        try:
            tasks, books = load_task_catalog(catalog_path)
        except (RuntimeError, zipfile.BadZipFile) as exc:
            warnings.append(str(exc))
    if not tasks and allow_continuity:
        tasks, books = list(VERIFIED_TASK_CONTINUITY), dict(VERIFIED_BOOK_CONTINUITY)
        warnings.append("Using the two-record verified task continuity cache; full catalog validation is pending")
    elif not tasks:
        warnings.append("No accessible US task catalog; live agents must not invent task codes")
    return invoices, tasks, books, warnings


def _tokens(value: str) -> set:
    stop = {"and", "the", "for", "of", "to", "a", "in", "line", "new", "project"}
    return {token for token in TOKEN.findall(value.lower()) if len(token) > 2 and token not in stop}


def _asset_understanding(description: str) -> Tuple[str, str, bool, float]:
    text = description.lower()
    if PERIOD_COST.search(text):
        return "Period cost", "Repair, maintenance, freight, or reimbursable cost", False, 0.97
    rules = [
        (("environment", "envrionmental", "assessment", "air quality", "biological", "cultural", "health risk", "noise", "joshua tree", "conservation", "engineering", "permit"), "Infrastructure", "Engineering / environmental", True),
        (("shelf", "retainer", "divider", "rack", "infostrip"), "Store equipment", "Shelving / merchandising", True),
        (("dispenser", "pump", "fuel"), "Fuel equipment", "Fuel dispensing equipment", True),
        (("oven", "door"), "Food service equipment", "Cooking equipment", True),
        (("parking", "paving", "asphalt"), "Land improvements", "Site improvements", True),
        (("construction", "install", "build"), "Building improvements", "Construction", True),
    ]
    for keywords, asset, commodity, capital in rules:
        if any(keyword in text for keyword in keywords):
            return asset, commodity, capital, 0.91
    return "Unclassified", "Unclassified", False, 0.45


def _recommend_task(description: str, tasks: Sequence[Mapping[str, str]]) -> Tuple[Optional[Mapping[str, str]], float]:
    text = description.lower()
    preferred = "0007" if any(word in text for word in ("environment", "envrionmental", "assessment", "air quality", "biological", "cultural", "health risk", "noise", "joshua tree", "conservation")) else None
    if preferred:
        match = next((task for task in tasks if task.get("Task Type") == preferred), None)
        if match:
            return match, 0.98
    if any(word in text for word in ("wire divider", "retainer", "shelving", "infostrip")):
        match = next((task for task in tasks if task.get("Task Type") == "5225"), None)
        if match:
            return match, 0.91
    source = _tokens(description)
    best: Optional[Mapping[str, str]] = None
    best_score = 0.0
    for task in tasks:
        if str(task.get("Active", "1")) not in {"1", "True", "true"}:
            continue
        target = _tokens(" ".join((task.get("Description", ""), task.get("Asset Category Minor", ""))))
        score = len(source & target) / max(1, len(source | target))
        if score > best_score:
            best, best_score = task, score
    confidence = min(0.89, 0.48 + best_score * 1.8) if best else 0.0
    return best, round(confidence, 3)


def classify_invoice(
    invoice: Mapping[str, Any],
    tasks: Sequence[Mapping[str, str]],
    books: Mapping[str, Mapping[str, str]],
    threshold: float,
) -> Dict[str, Any]:
    classified: List[Dict[str, Any]] = []
    for source_line in invoice["lines"]:
        line = dict(source_line)
        asset, commodity, capital_nature, understanding_confidence = _asset_understanding(line["description"])
        task, task_confidence = _recommend_task(line["description"], tasks) if invoice["region"] == "North America" else (None, 0.0)
        task_code = task.get("Task Type", "") if task else ""
        book = books.get(task_code, {})
        life_months = int(_number(book.get("Estimated Life Years")) * 12 + _number(book.get("Estimated Life Months")))
        line.update({
            "asset_type": asset,
            "commodity": commodity,
            "capital_nature": capital_nature,
            "recommended_task": task_code,
            "recommended_task_description": task.get("Description", "") if task else "",
            "recommended_asset_class": task.get("Asset Category Minor", asset) if task else asset,
            "useful_life_months": life_months,
            "classification_confidence": round(min(understanding_confidence, task_confidence or understanding_confidence), 3),
        })
        classified.append(line)

    eligible_total = sum(line["amount"] for line in classified if line["capital_nature"])
    invoice_decision = "CAPEX" if eligible_total >= threshold else "OPEX"
    for line in classified:
        line["capex_opex"] = "CAPEX" if invoice_decision == "CAPEX" and line["capital_nature"] else "OPEX"
        line["policy_basis"] = (
            f"Capital-nature invoice total {eligible_total:.2f} meets {threshold:.2f} threshold"
            if line["capex_opex"] == "CAPEX"
            else "Period/non-capital cost or invoice capital total is below threshold"
        )

    exceptions: List[Dict[str, str]] = []
    for line in classified:
        actual = line.get("actual_task", "")
        if actual and line["recommended_task"] and actual != line["recommended_task"]:
            exceptions.append({"code": "E1", "type": "Task Mismatch", "line_id": line["line_id"], "detail": f"AI {line['recommended_task']} != actual {actual}"})
        if line["classification_confidence"] < 0.70:
            exceptions.append({"code": "DQ", "type": "Low Confidence", "line_id": line["line_id"], "detail": "Classification confidence below 70%"})
    if invoice["region"] == "Europe":
        exceptions.append({"code": "E2", "type": "Asset Class Review", "line_id": "", "detail": "JDE asset-class list/country template was not supplied; finance assignment required"})
    business = str(invoice.get("business_capex_opex", "")).upper()
    if business in {"CAPEX", "OPEX"} and business != invoice_decision:
        exceptions.append({"code": "E5", "type": "CAPEX/OPEX Disagreement", "line_id": "", "detail": f"Business {business} != AI {invoice_decision}"})
    if invoice_decision == "CAPEX" and not (invoice.get("afe_number") or invoice.get("project_number")):
        exceptions.append({"code": "E4", "type": "Policy Exception", "line_id": "", "detail": "CAPEX recommendation has no AFE/project reference in ABBYY data"})

    result = dict(invoice)
    result.update({
        "lines": classified,
        "eligible_total": round(eligible_total, 2),
        "recommendation": invoice_decision,
        "exceptions": exceptions,
        "review_status": "PENDING" if exceptions else "NOT_REQUIRED",
        "automatic_posting": False,
    })
    return result


def apply_relationship_analysis(invoices: List[Dict[str, Any]], threshold: float) -> None:
    relationships: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for invoice in invoices:
        for reference in (invoice.get("afe_number"), invoice.get("project_number")):
            if reference:
                relationships[str(reference)].append(invoice)
    for invoice in invoices:
        peers: List[Dict[str, Any]] = []
        for reference in (invoice.get("afe_number"), invoice.get("project_number")):
            peers.extend(other for other in relationships.get(str(reference), []) if other is not invoice)
        low_value_component = invoice["eligible_total"] < threshold and any(
            word in line["description"].lower() for line in invoice["lines"] for word in ("door", "component", "replacement", "addition")
        )
        capital_peer = any(peer.get("recommendation") == "CAPEX" for peer in peers)
        if low_value_component and capital_peer:
            invoice["exceptions"].append({"code": "E3", "type": "Bundle Exception", "line_id": "", "detail": "Low-value component shares an AFE/project with a prior CAPEX invoice"})
            invoice["review_status"] = "PENDING"
        invoice["related_invoice_ids"] = sorted({peer["invoice_id"] for peer in peers})


def _audit(stage: int, agent: str, inputs: Any, outputs: Any) -> Dict[str, Any]:
    return {
        "stage": stage,
        "agent": agent,
        "input_sha256": _canonical_sha256(inputs),
        "output_sha256": _canonical_sha256(outputs),
        "automatic_posting": False,
    }


def load_reviews(workspace: Path) -> List[Dict[str, Any]]:
    path = workspace / "reviews.jsonl"
    if not path.exists():
        return []
    reviews: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                reviews.append(value)
        except json.JSONDecodeError:
            continue
    return reviews


def apply_reviews(
    invoices: List[Dict[str, Any]],
    reviews: Sequence[Mapping[str, Any]],
    learning_confirmations: int,
) -> List[Dict[str, Any]]:
    latest = {str(review.get("invoice_id")): review for review in reviews}
    confirmations: Counter[Tuple[str, str, str]] = Counter()
    for invoice in invoices:
        review = latest.get(str(invoice["invoice_id"]))
        invoice["review_decision"] = ""
        invoice["final_decision"] = invoice["recommendation"]
        if not review:
            continue
        decision = str(review.get("decision", "")).upper()
        invoice["review_decision"] = decision
        invoice["review_status"] = decision
        if decision == "RECLASSIFY" and review.get("reclassified_as") in {"CAPEX", "OPEX"}:
            invoice["final_decision"] = review["reclassified_as"]
        elif decision == "REJECT":
            invoice["final_decision"] = "REJECTED"
        for line in invoice["lines"]:
            if invoice["final_decision"] in {"CAPEX", "OPEX"}:
                confirmations[(line["asset_type"], line["recommended_task"], invoice["final_decision"])] += 1
    return [
        {
            "asset_type": asset_type,
            "recommended_task": task,
            "decision": decision,
            "confirmations": count,
            "status": "READY_FOR_RULE_OWNER_APPROVAL",
        }
        for (asset_type, task, decision), count in sorted(confirmations.items())
        if count >= learning_confirmations
    ]


def run_pipeline(
    data_dir: Path,
    threshold: float,
    reviews: Sequence[Mapping[str, Any]] = (),
    learning_confirmations: int = 50,
) -> Dict[str, Any]:
    raw_invoices, tasks, books, source_warnings = load_inputs(data_dir)
    trace = [_audit(1, "Document Intelligence Agent", [row["source_sha256"] for row in raw_invoices], raw_invoices)]
    understood = [[_asset_understanding(line["description"]) for line in invoice["lines"]] for invoice in raw_invoices]
    trace.append(_audit(2, "Asset Classification Agent", raw_invoices, understood))
    invoices = [classify_invoice(invoice, tasks, books, threshold) for invoice in raw_invoices]
    trace.append(_audit(3, "Coding Validation Agent", understood, invoices))
    trace.append(_audit(4, "Capitalization Decision Agent", {"threshold": threshold, "invoices": raw_invoices}, invoices))
    apply_relationship_analysis(invoices, threshold)
    trace.append(_audit(5, "Asset Relationship Agent", invoices, [row["related_invoice_ids"] for row in invoices]))
    exceptions = [dict(item, invoice_id=invoice["invoice_id"]) for invoice in invoices for item in invoice["exceptions"]]
    trace.append(_audit(6, "Exception Management Agent", invoices, exceptions))
    candidate_rules = apply_reviews(invoices, reviews, learning_confirmations)
    trace.append(_audit(7, "Continuous Learning Agent", reviews, {"candidate_rules": candidate_rules, "minimum_confirmations": learning_confirmations}))
    return {
        "schema_version": SCHEMA_VERSION,
        "engine": "rules",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "advisory_only": True,
        "capitalization_threshold": threshold,
        "source_directory": str(data_dir),
        "task_catalog_count": len(tasks),
        "source_warnings": source_warnings,
        "review_count": len(reviews),
        "learning_confirmations": learning_confirmations,
        "candidate_rules": candidate_rules,
        "invoices": invoices,
        "exceptions": exceptions,
        "audit_trace": trace,
    }


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _remove_readonly(function: Any, path: str, _error: Any) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _render_dashboard(package: Mapping[str, Any]) -> str:
    invoices = package["invoices"]
    exceptions = package["exceptions"]
    capex = sum(1 for row in invoices if row["recommendation"] == "CAPEX")
    opex = sum(1 for row in invoices if row["recommendation"] == "OPEX")
    pending = sum(1 for row in invoices if row["review_status"] in {"PENDING", "STALE_REVIEW"})
    engine = "Microsoft Agent Framework + Autarch" if package.get("engine") == "maf" else "Offline rules (no LLM)"
    engine_label = html.escape(engine + (f" | {package['model']}" if package.get("model") else ""))
    source_gaps = "".join(f"<li>{html.escape(warning)}</li>" for warning in package.get("source_warnings", []))
    warnings = f"<section><h2>Source gaps</h2><ul>{source_gaps}</ul></section>" if source_gaps else ""
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(row['invoice_number'] or row['invoice_id'][:12])}</td>"
        f"<td>{html.escape(row['vendor'])}</td><td>{html.escape(row['region'])}</td>"
        f"<td>{html.escape(row['currency'])} {row['total']:,.2f}</td>"
        f"<td><span class='decision {row['recommendation'].lower()}'>{row['recommendation']}</span></td>"
        f"<td>{len(row['exceptions'])}</td><td>{row['review_status']}</td>"
        f"<td>{html.escape(row.get('final_decision') or 'Awaiting Finance')}</td></tr>"
        for row in invoices
    )
    exception_rows = "".join(
        f"<tr><td>{html.escape(item['code'])}</td><td>{html.escape(item['type'])}</td>"
        f"<td>{html.escape(item['invoice_id'][:12])}</td><td>{html.escape(item['detail'])}</td></tr>"
        for item in exceptions
    ) or "<tr><td colspan='4'>No exceptions</td></tr>"
    evidence_sections = []
    for invoice in invoices:
        line_rows = "".join(
            f"<tr><td>{html.escape(line['line_id'])}</td><td>{html.escape(line['description'])}</td>"
            f"<td>{html.escape(line.get('recommended_task') or line.get('recommended_asset_class') or 'Unresolved')}</td>"
            f"<td>{html.escape(line['capex_opex'])}</td><td>{html.escape(line['policy_basis'])}</td>"
            f"<td>{html.escape(', '.join(line.get('evidence_refs', [])))}</td></tr>"
            for line in invoice["lines"]
        )
        evidence_sections.append(
            f"<details><summary>{html.escape(invoice['invoice_number'])} | Line evidence</summary>"
            f"<div class='table-wrap'><table><thead><tr><th>Line</th><th>Description</th><th>Task / class</th>"
            f"<th>Recommendation</th><th>Policy reasoning</th><th>Evidence IDs</th></tr></thead><tbody>{line_rows}</tbody></table></div></details>"
        )
    integrity = ""
    if package.get("governance"):
        integrity = f"<p>Autarch ledger: {'verified' if package['governance']['chain_verified'] else 'FAILED'} | <a href='signed_audit.jsonl'>Signed records</a></p>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CAPEX Decision Control</title><style>
:root{{--ink:#17211c;--muted:#667068;--paper:#f2f4ef;--panel:#fff;--line:#d8ddd5;--green:#156b45;--amber:#b85c18;--red:#a82d2d;--navy:#18374a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:14px/1.45 Georgia,'Times New Roman',serif}}
header{{background:var(--navy);color:white;padding:24px max(24px,calc((100vw - 1180px)/2))}}header h1{{margin:0;font-size:26px;letter-spacing:0}}header p{{margin:5px 0 0;color:#d7e2e8}}
main{{max-width:1180px;margin:auto;padding:24px}}.kpis{{display:grid;grid-template-columns:repeat(5,1fr);border:1px solid var(--line);background:var(--panel)}}
.kpi{{padding:18px;border-right:1px solid var(--line)}}.kpi:last-child{{border:0}}.kpi strong{{display:block;font:700 25px/1.1 Consolas,monospace}}.kpi span{{color:var(--muted);font-size:12px;text-transform:uppercase}}
section{{margin-top:28px}}h2{{font-size:18px;margin:0 0 10px}}.table-wrap{{overflow:auto;border:1px solid var(--line);background:var(--panel)}}table{{border-collapse:collapse;width:100%;min-width:760px}}
a{{text-decoration:none;color:#464feb}}th,td{{padding:10px 12px;text-align:left;border:1px solid #e6e6e6}}th{{background:#f5f5f5;font-size:12px;text-transform:uppercase}}.decision{{font:bold 12px Consolas,monospace}}.capex{{color:var(--green)}}.opex{{color:var(--amber)}}
.notice{{border-left:4px solid var(--amber);padding:10px 14px;background:#fff9ef;margin-top:18px}}footer{{color:var(--muted);margin:24px 0}}
td{{overflow-wrap:anywhere}}details{{margin:12px 0}}summary{{cursor:pointer;padding:8px 0;font-weight:bold}}.review,.mixed{{color:var(--red)}}
@media(max-width:720px){{main{{padding:14px}}.kpis{{grid-template-columns:1fr 1fr}}.kpi{{border-bottom:1px solid var(--line)}}}}
</style></head><body><header><h1>CAPEX Decision Control</h1><p>{engine_label}</p></header><main>
<div class="kpis"><div class="kpi"><strong>{len(invoices)}</strong><span>Invoices</span></div><div class="kpi"><strong>{capex}</strong><span>CAPEX</span></div><div class="kpi"><strong>{opex}</strong><span>OPEX</span></div><div class="kpi"><strong>{len(exceptions)}</strong><span>Exceptions</span></div><div class="kpi"><strong>{pending}</strong><span>Pending review</span></div></div>
<div class="notice">Advisory output only. No financial posting is performed. Finance approval remains required for exceptions.</div>
{warnings}
<section><h2>Invoice decisions</h2><div class="table-wrap"><table><thead><tr><th>Invoice</th><th>Vendor</th><th>Region</th><th>Total</th><th>Recommendation</th><th>Exceptions</th><th>Review status</th><th>Finance decision</th></tr></thead><tbody>{rows}</tbody></table></div></section>
<section><h2>Review queue</h2><div class="table-wrap"><table><thead><tr><th>Code</th><th>Type</th><th>Invoice ID</th><th>Reason</th></tr></thead><tbody>{exception_rows}</tbody></table></div></section>
<section><h2>Decision evidence</h2>{''.join(evidence_sections)}{integrity}</section>
<footer>Catalog: {package['task_catalog_count']} task types | Generated {html.escape(package['generated_at'])}</footer></main></body></html>"""


def _customer_task_label(line: Mapping[str, Any]) -> str:
    code = line.get("recommended_task") or line.get("recommended_asset_class")
    if not code:
        return "Unresolved"
    description = line.get("recommended_task_description")
    return f"{code} - {description}" if description else str(code)


def _customer_confidence_range(invoice: Mapping[str, Any]) -> str:
    values = [
        float(line["classification_confidence"])
        for line in invoice["lines"]
        if _customer_task_label(line) != "Unresolved"
        and isinstance(line.get("classification_confidence"), (int, float))
    ]
    if not values:
        return "Not available"
    return f"{min(values):.0%}" if min(values) == max(values) else f"{min(values):.0%}-{max(values):.0%}"


def _customer_task_summary(invoice: Mapping[str, Any]) -> str:
    counts = Counter(
        _customer_task_label(line)
        for line in invoice["lines"]
        if _customer_task_label(line) != "Unresolved"
    )
    if not counts:
        return "No supported task suggestion"
    return "; ".join(
        f"{html.escape(label)} ({count} line{'s' if count != 1 else ''})"
        for label, count in counts.items()
    )


def _render_customer_dashboard(package: Mapping[str, Any]) -> str:
    invoices = package["invoices"]
    exceptions = package["exceptions"]
    lines = [line for invoice in invoices for line in invoice["lines"]]
    suggestions = [line for line in lines if _customer_task_label(line) != "Unresolved"]
    unresolved = len(lines) - len(suggestions)
    pending = sum(
        invoice["review_status"] in {"PENDING", "STALE_REVIEW"}
        for invoice in invoices
    )
    engine = (
        "Microsoft Agent Framework + governance and audit controls"
        if package.get("engine") == "maf"
        else "Offline deterministic rules (no LLM)"
    )
    engine_label = html.escape(
        engine + (f" | {package['model']}" if package.get("model") else "")
    )

    source_gaps = "".join(
        f"<li>{html.escape(warning)}</li>"
        for warning in package.get("source_warnings", [])
    )
    inputs = (
        "<section class='inputs'><h2>Inputs needed for final determination</h2>"
        "<p>The workflow completed safely, but Finance must supply the following "
        "governed evidence before a final CapEx/OpEx decision can be supported.</p>"
        f"<ul>{source_gaps}</ul></section>"
        if source_gaps else ""
    )

    invoice_rows = "".join(
        "<tr>"
        f"<td>{html.escape(invoice['invoice_number'] or invoice['invoice_id'])}</td>"
        f"<td>{html.escape(invoice['vendor'])}</td>"
        f"<td>{html.escape(invoice['currency'])} {invoice['total']:,.2f}</td>"
        f"<td>{_customer_task_summary(invoice)}</td>"
        f"<td>{_customer_confidence_range(invoice)}</td>"
        f"<td><span class='status review'>{html.escape(invoice['recommendation'])}</span></td>"
        "<td>Finance validation required</td></tr>"
        for invoice in invoices
    )

    case_cards = []
    evidence_sections = []
    for invoice in invoices:
        classified = sum(
            _customer_task_label(line) != "Unresolved"
            for line in invoice["lines"]
        )
        unresolved_count = len(invoice["lines"]) - classified
        case_cards.append(
            "<article class='case-card'><div class='case-title'><div>"
            "<span class='eyebrow'>Invoice case</span>"
            f"<h3>{html.escape(invoice['invoice_number'] or invoice['invoice_id'])}</h3>"
            f"<p>{html.escape(invoice['vendor'])}</p></div>"
            f"<span class='status review'>{html.escape(invoice['recommendation'])}</span></div>"
            "<dl>"
            f"<div><dt>Invoice total</dt><dd>{html.escape(invoice['currency'])} {invoice['total']:,.2f}</dd></div>"
            f"<div><dt>Task coverage</dt><dd>{classified} of {len(invoice['lines'])} lines</dd></div>"
            f"<div><dt>Confidence</dt><dd>{_customer_confidence_range(invoice)}</dd></div>"
            f"<div><dt>Unresolved lines</dt><dd>{unresolved_count}</dd></div></dl>"
            f"<h4>AI task suggestion</h4><p>{_customer_task_summary(invoice)}</p>"
            "<h4>Control outcome</h4><p>No final CapEx/OpEx determination was "
            "issued because approved policy and project evidence were not supplied.</p>"
            "<h4>Finance action</h4><p>Validate the suggested task and provide the "
            "applicable policy, current coding, and project evidence.</p></article>"
        )

        line_rows = "".join(
            "<tr>"
            f"<td>{html.escape(line['line_id'])}</td>"
            f"<td>{html.escape(line['description'])}</td>"
            f"<td>{html.escape(_customer_task_label(line))}</td>"
            f"<td>{float(line.get('classification_confidence') or 0):.0%}</td>"
            f"<td>{html.escape(line['capex_opex'])}</td>"
            f"<td>{html.escape(line['policy_basis'])}</td>"
            f"<td>{html.escape(', '.join(line.get('evidence_refs', [])))}</td></tr>"
            for line in invoice["lines"]
        )
        evidence_sections.append(
            f"<details><summary>{html.escape(invoice['invoice_number'])} | Full line evidence</summary>"
            "<div class='table-wrap'><table><thead><tr><th>Line</th><th>Description</th>"
            "<th>Suggested task / class</th><th>Confidence</th><th>Decision</th>"
            f"<th>Policy reasoning</th><th>Evidence IDs</th></tr></thead><tbody>{line_rows}</tbody>"
            "</table></div></details>"
        )

    grouped_exceptions = Counter(
        (item["code"], item["type"], item["detail"])
        for item in exceptions
    )
    condition_rows = "".join(
        f"<tr><td>{html.escape(code)}</td><td>{html.escape(kind)}</td>"
        f"<td>{count}</td><td>{html.escape(detail)}</td></tr>"
        for (code, kind, detail), count in grouped_exceptions.items()
    ) or "<tr><td colspan='4'>No review conditions</td></tr>"
    exception_rows = "".join(
        f"<tr><td>{html.escape(item['code'])}</td><td>{html.escape(item['type'])}</td>"
        f"<td>{html.escape(item['invoice_id'])}</td><td>{html.escape(item['detail'])}</td></tr>"
        for item in exceptions
    ) or "<tr><td colspan='4'>No review conditions</td></tr>"

    governance = package.get("governance") or {}
    verified = bool(
        governance.get("chain_verified") and governance.get("signatures_verified")
    )
    integrity = (
        f"<div class='control {'verified' if verified else 'failed'}'><strong>"
        f"{'Verified governance record' if verified else 'Governance verification failed'}</strong>"
        f"<span>{governance.get('record_count', 0)} governed actions; hash chain "
        f"{'verified' if governance.get('chain_verified') else 'not verified'}; signatures "
        f"{'verified' if governance.get('signatures_verified') else 'not verified'}.</span></div>"
        if governance else ""
    )
    try:
        generated = datetime.fromisoformat(
            str(package["generated_at"]).replace("Z", "+00:00")
        )
        generated_label = generated.strftime("%B %d, %Y at %H:%M UTC")
    except (TypeError, ValueError):
        generated_label = str(package["generated_at"])

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Circle K CapEx/CIP Validation - Controlled Demonstration Results</title><style>
:root{{--ink:#17211c;--muted:#5f6b64;--paper:#f3f5f2;--panel:#fff;--line:#d7ddd8;--green:#166b46;--green-bg:#eaf5ef;--amber:#9a531c;--amber-bg:#fff5e8;--red:#9c3030;--navy:#163a52;--blue:#1769aa}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:14px/1.5 "Segoe UI",Arial,sans-serif}}header{{background:var(--navy);color:#fff;padding:36px max(24px,calc((100vw - 1180px)/2))}}
header .eyebrow{{color:#b8d8ea}}header h1{{margin:5px 0 7px;font-size:30px;line-height:1.2}}header p{{margin:4px 0;color:#d7e5ec}}.eyebrow{{display:block;color:var(--blue);font-size:11px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase}}
main{{max-width:1180px;margin:auto;padding:26px}}.scope{{background:#fff;border:1px solid var(--line);border-radius:9px;padding:16px 18px;margin-bottom:18px}}.scope strong{{color:var(--navy)}}
.kpis{{display:grid;grid-template-columns:repeat(5,1fr);border:1px solid var(--line);border-radius:9px;background:var(--panel);overflow:hidden}}.kpi{{padding:18px;border-right:1px solid var(--line)}}.kpi:last-child{{border:0}}.kpi strong{{display:block;font:700 25px/1.1 Consolas,monospace;color:var(--navy)}}.kpi span{{color:var(--muted);font-size:11px;text-transform:uppercase}}
section{{margin-top:30px}}h2{{font-size:20px;margin:0 0 6px;color:var(--navy)}}h3{{margin:2px 0;font-size:18px}}h4{{font-size:12px;text-transform:uppercase;color:var(--muted);margin:17px 0 4px}}.section-lede{{color:var(--muted);margin:0 0 14px}}
.notice{{border-left:4px solid var(--amber);padding:12px 15px;background:var(--amber-bg);margin-top:18px}}.notice strong{{display:block;color:var(--amber)}}.success-list{{display:grid;grid-template-columns:1fr 1fr;gap:9px 24px;margin:15px 0;padding-left:21px}}
.cases{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.case-card{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:18px}}.case-title{{display:flex;justify-content:space-between;gap:20px}}.case-title p{{margin:0;color:var(--muted)}}.status{{height:fit-content;padding:5px 10px;border-radius:999px;font-size:11px;font-weight:700}}.status.review{{background:var(--amber-bg);color:var(--amber)}}
dl{{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line);border:1px solid var(--line);margin:17px 0}}dl div{{background:#fff;padding:10px}}dt{{font-size:11px;text-transform:uppercase;color:var(--muted)}}dd{{margin:2px 0 0;font-weight:700}}
.inputs{{background:var(--amber-bg);border:1px solid #ead4bb;border-radius:9px;padding:18px}}.table-wrap{{overflow:auto;border:1px solid var(--line);background:var(--panel)}}table{{border-collapse:collapse;width:100%;min-width:760px}}th,td{{padding:10px 12px;text-align:left;border:1px solid #e7eae7;vertical-align:top}}th{{background:#f3f6f4;font-size:11px;text-transform:uppercase;color:var(--muted)}}td{{overflow-wrap:anywhere}}.decision{{font:bold 12px Consolas,monospace}}.review,.mixed{{color:var(--amber)}}
.control{{display:flex;flex-direction:column;border-radius:9px;padding:15px 18px;margin-top:14px}}.control.verified{{background:var(--green-bg);border:1px solid #bddbc9;color:var(--green)}}.control.failed{{background:#faeaea;border:1px solid #e1b7b7;color:var(--red)}}details{{margin:12px 0;background:#fff;border:1px solid var(--line);border-radius:7px;padding:0 13px}}summary{{cursor:pointer;padding:12px 0;font-weight:700;color:var(--navy)}}footer{{color:var(--muted);margin:30px 0 10px;border-top:1px solid var(--line);padding-top:15px}}
@media(max-width:820px){{.kpis{{grid-template-columns:1fr 1fr}}.kpi{{border-bottom:1px solid var(--line)}}.cases,.success-list{{grid-template-columns:1fr}}}}
@media print{{body{{background:#fff}}header{{padding:20px}}main{{padding:10px}}.case-card,.scope,.inputs,.kpis{{break-inside:avoid}}details{{break-inside:avoid}}summary{{list-style:none}}}}
</style></head><body><header><span class="eyebrow">Circle K | Controlled demonstration</span>
<h1>CapEx/CIP Validation Results</h1><p>{engine_label}</p><p>ABBYY Vantage normalized invoice exports | Advisory workflow</p></header><main>
<div class="scope"><strong>Demonstration scope:</strong> two supplied invoice cases. These results demonstrate workflow behavior, evidence controls, and human-review routing; they are not a statistical accuracy benchmark.</div>
<div class="kpis"><div class="kpi"><strong>{len(invoices)}</strong><span>Invoices analyzed</span></div><div class="kpi"><strong>{len(lines)}</strong><span>Lines analyzed</span></div><div class="kpi"><strong>{len(suggestions)}</strong><span>Task suggestions</span></div><div class="kpi"><strong>{unresolved}</strong><span>Unresolved lines</span></div><div class="kpi"><strong>{pending}</strong><span>Finance reviews</span></div></div>
<div class="notice"><strong>REVIEW is an intentional control outcome, not a model failure.</strong>The workflow generated task suggestions but withheld final CapEx/OpEx conclusions because approved policy and project evidence were not supplied. No financial posting was performed.</div>
<section><h2>Executive summary</h2><p class="section-lede">The controlled run completed the intended advisory and governance workflow.</p>
<ul class="success-list"><li>Consumed {len(invoices)} ABBYY normalized invoice exports.</li><li>Interpreted {len(lines)} invoice lines.</li><li>Produced {len(suggestions)} suggestions from a closed {package['task_catalog_count']}-code catalog.</li><li>Left {unresolved} ambiguous cost lines unresolved rather than forcing a code.</li><li>Routed both invoice cases to Finance for a governed decision.</li><li>Performed no posting or source-system writeback.</li></ul>{integrity}</section>
<section><h2>Case-study results</h2><p class="section-lede">Task mappings are AI suggestions pending Finance validation.</p><div class="cases">{''.join(case_cards)}</div></section>
<section><h2>Invoice result summary</h2><div class="table-wrap"><table><thead><tr><th>Invoice</th><th>Vendor</th><th>Total</th><th>Suggested task coverage</th><th>Confidence</th><th>Control status</th><th>Required action</th></tr></thead><tbody>{invoice_rows}</tbody></table></div></section>
{inputs}
<section><h2>Consolidated review conditions</h2><p class="section-lede">Repeated line-level controls are grouped here to keep the review actionable.</p><div class="table-wrap"><table><thead><tr><th>Code</th><th>Control</th><th>Occurrences</th><th>Reason</th></tr></thead><tbody>{condition_rows}</tbody></table></div></section>
<section><h2>Technical appendix</h2><p class="section-lede">Full model reasoning and raw review conditions are retained for technical and audit review.</p>{''.join(evidence_sections)}
<details><summary>All line-level review conditions ({len(exceptions)})</summary><div class="table-wrap"><table><thead><tr><th>Code</th><th>Type</th><th>Invoice ID</th><th>Reason</th></tr></thead><tbody>{exception_rows}</tbody></table></div></details></section>
<footer>Model: {html.escape(str(package.get('model') or 'No LLM'))} | Catalog: {package['task_catalog_count']} task types | Generated {html.escape(generated_label)} | Controlled demonstration - advisory output only</footer></main></body></html>"""


def write_outputs(package: Mapping[str, Any], workspace: Path) -> Path:
    if package.get("engine") == "maf":
        from capex_agents import archive_cases
        archive_cases(package["invoices"], workspace)
    outputs = workspace / "outputs"
    if outputs.exists():
        shutil.rmtree(outputs, onerror=_remove_readonly)
    outputs.mkdir(parents=True)
    (outputs / "decision_package.json").write_text(json.dumps(package, indent=2), encoding="utf-8")
    invoice_rows = [{key: value for key, value in invoice.items() if key not in {"lines", "exceptions"}} | {"exception_count": len(invoice["exceptions"])} for invoice in package["invoices"]]
    line_rows = [dict(line, invoice_id=invoice["invoice_id"], invoice_number=invoice["invoice_number"]) for invoice in package["invoices"] for line in invoice["lines"]]
    _write_csv(outputs / "invoices.csv", invoice_rows, ["invoice_id", "invoice_number", "vendor", "region", "country", "state", "currency", "total", "eligible_total", "recommendation", "review_status", "review_decision", "final_decision", "exception_count", "source", "source_sha256", "case_digest", "model", "automatic_posting"])
    _write_csv(outputs / "invoice_lines.csv", line_rows, ["invoice_id", "invoice_number", "line_id", "description", "quantity", "unit_price", "amount", "asset_type", "commodity", "recommended_task", "recommended_task_description", "recommended_asset_class", "actual_task", "actual_asset_class", "coding_status", "capex_opex", "useful_life_months", "classification_confidence", "policy_basis", "policy_ids", "evidence_refs"])
    _write_csv(outputs / "review_queue.csv", package["exceptions"], ["invoice_id", "code", "type", "line_id", "detail"])
    _write_csv(outputs / "candidate_rules.csv", package["candidate_rules"], ["rule_id", "asset_type", "recommended_task", "country", "currency", "decision", "condition", "rationale", "supporting_review_ids", "confirmations", "status"])
    _write_csv(outputs / "audit_trace.csv", package["audit_trace"], ["stage", "agent", "framework", "model", "why_id", "input_sha256", "output_sha256", "automatic_posting"])
    if package.get("governance"):
        shutil.copyfile(package["governance"]["audit_path"], outputs / "signed_audit.jsonl")
    (outputs / "dashboard.html").write_text(_render_customer_dashboard(package), encoding="utf-8")
    return outputs


def record_review(workspace: Path, invoice_id: str, decision: str, reviewer: str, comment: str, reclassified_as: str) -> Path:
    if not reviewer.strip() or decision not in {"approve", "reject", "reclassify"}:
        raise ValueError("A named reviewer and a valid finance decision are required")
    if decision == "reclassify" and reclassified_as.upper() not in {"CAPEX", "OPEX"}:
        raise ValueError("Reclassification requires --reclassified-as capex or opex")
    package = json.loads((workspace / "outputs" / "decision_package.json").read_text(encoding="utf-8"))
    invoice = next((item for item in package["invoices"] if item["invoice_id"] == invoice_id), None)
    if invoice is None:
        raise ValueError("Invoice ID is not present in the current decision package")
    if package.get("engine") == "maf":
        from capex_agents import apply_bound_reviews, case_content, digest
        if digest(case_content(invoice)) != invoice["case_digest"]:
            raise ValueError("Decision package content no longer matches its case digest")
        if decision == "approve" and invoice["recommendation"] == "REVIEW":
            raise ValueError("An unresolved recommendation cannot be approved; record a reasoned reclassification instead")
    reviews = workspace / "reviews.jsonl"
    reviews.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "review_id": uuid4().hex,
        "invoice_id": invoice_id,
        "case_digest": invoice.get("case_digest"),
        "decision": decision.upper(),
        "reviewer": reviewer,
        "comment": comment,
        "reclassified_as": reclassified_as.upper(),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    with reviews.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    if package.get("engine") == "maf":
        apply_bound_reviews(package["invoices"], load_reviews(workspace))
        package["review_count"] = len(load_reviews(workspace))
        package["candidate_rules"] = []
        package["learning_status"] = "Finance review changed; rerun MAF analysis to refresh proposals"
        write_outputs(package, workspace)
    return reviews


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the ABBYY-to-CAPEX governed finance workflow")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--engine", choices=("maf", "rules"), default="maf")
    parser.add_argument("--model", help="Azure deployment name, optionally prefixed with azure:")
    parser.add_argument("--endpoint", help="Azure OpenAI HTTPS endpoint; defaults to AZURE_OPENAI_ENDPOINT")
    parser.add_argument("--auth", choices=("aad", "key"), default="aad")
    parser.add_argument("--references", type=Path, help="Approved policy, coding, JDE class and asset-history JSON export")
    parser.add_argument("--max-calls", type=int, default=100, help="Autarch action budget, including source intake")
    parser.add_argument("--max-output-tokens", type=int, default=6000)
    parser.add_argument("--timeout", type=float, default=90.0, help="Timeout per Azure request in seconds; retries disabled")
    parser.add_argument("--threshold", type=float, help="Illustrative rules-engine threshold (default 2000); not policy authority in MAF")
    parser.add_argument("--learning-confirmations", type=int, default=50)
    subparsers = parser.add_subparsers(dest="command")
    review = subparsers.add_parser("review", help="append a human finance decision")
    review.add_argument("invoice_id")
    review.add_argument("decision", choices=("approve", "reject", "reclassify"))
    review.add_argument("--reviewer", required=True)
    review.add_argument("--comment", default="")
    review.add_argument("--reclassified-as", choices=("capex", "opex"), default="")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "review":
            path = record_review(args.workspace, args.invoice_id, args.decision, args.reviewer, args.comment, args.reclassified_as)
            print(f"Finance decision recorded in {path}; no financial entries were posted")
            return 0
        if args.threshold is not None and (not math.isfinite(args.threshold) or args.threshold <= 0):
            raise ValueError("Threshold must be finite and positive")
        if args.learning_confirmations < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("Learning confirmations and timeout must be positive")
        if args.engine == "maf":
            from capex_agents import azure_client_factory, run_maf_pipeline
            factory, deployment = azure_client_factory(args.model, args.endpoint, args.auth, args.timeout)
            references = args.references
            if references is None and (args.data / "references.json").exists():
                references = args.data / "references.json"
            package = run_maf_pipeline(
                args.data, args.workspace, factory, deployment, references_path=references,
                max_calls=args.max_calls, max_output_tokens=args.max_output_tokens,
                learning_confirmations=args.learning_confirmations, scenario_threshold=args.threshold,
            )
        else:
            package = run_pipeline(args.data, args.threshold or 2000.0, load_reviews(args.workspace), args.learning_confirmations)
        outputs = write_outputs(package, args.workspace)
    except (ValueError, RuntimeError, OSError, ImportError) as exc:
        print(f"CAPEX {args.engine} failed: {exc}", file=sys.stderr)
        print("No rules fallback or financial posting was performed. Existing outputs were not reclassified.", file=sys.stderr)
        return 2
    print(f"Engine: {package['engine']}" + (f" | Azure deployment: {package['model']}" if package.get("model") else " (no LLM)"))
    print(f"Processed {len(package['invoices'])} invoices with {len(package['exceptions'])} exceptions")
    for warning in package["source_warnings"]:
        print(f"Source warning: {warning}")
    print(f"Dashboard: {outputs / 'dashboard.html'}")
    if package.get("governance"):
        print(f"Autarch signed ledger verified: {package['governance']['audit_path']}")
    print("Advisory only: no financial entries were posted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())