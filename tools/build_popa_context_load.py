"""Prepare, but never execute, a four-table North American POPA context load."""

from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from tools.build_capex_source_invoice_load import BEGIN_PAYLOAD, END_PAYLOAD, render_sql


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "sql" / "data" / "014_load_popa_na_context.sql"
SITE_PO_TEMPLATE = ROOT / "sql" / "data" / "015_load_popa_site_po_headers.sql"
PROJECT_LINE_TEMPLATE = ROOT / "sql" / "data" / "017_load_popa_project_po_line_core.sql"
NULLABLE_SCHEMA = ROOT / "sql" / "migrations" / "021_allow_unknown_popa_project_line_context.sql"
MAPPING = {
    "Project_Number": ("AFEProjectID", "prep.Project"),
    "Project_Name": ("ProjectDescription", "prep.Project"),
    "Project_Type": ("ProjectType", "prep.Project"),
    "Project_Status": ("ProjectStatus", "prep.Project"),
    "Project_Start_Date": ("ProjectStartDate", "prep.Project"),
    "Total_Budget": ("ProjectTotalBudget", "prep.Project"),
    "Site_Id": ("SiteID", "prep.Site"),
    "PO_Number": ("PONumber", "prep.PurchaseOrder"),
    "PO_Line_Number": ("POLineNumber", "prep.PurchaseOrderLine"),
    "PO_Description": ("PODescription", "prep.PurchaseOrderLine"),
    "Task_Type": ("POTaskCode", "prep.PurchaseOrderLine"),
    "Bus_Entity": ("BusinessUnit", "prep.PurchaseOrderLine"),
}
REQUIRED = {"System_Key", "System_Name", "Type_Order", "Supplier_Name", *MAPPING}
LIMITS = {
    "AFEProjectID": 128, "ProjectDescription": 2000, "ProjectStatus": 100,
    "ProjectType": 100, "SiteID": 128, "PONumber": 128,
    "PODescription": 2000, "POTaskCode": 256, "BusinessUnit": 100,
}
PROJECT_FIELDS = {
    target: source for source, (target, table) in MAPPING.items()
    if table == "prep.Project"
}
LINE_FIELDS = {
    "AFEProjectID": "Project_Number", "SiteID": "Site_Id", "POTaskCode": "Task_Type",
    "BusinessUnit": "Bus_Entity", "PODescription": "PO_Description",
    "SupplierName": "Supplier_Name",
}
SQL_FIELDS = {
    "projects": ("AFEProjectID", "ProjectDescription", "ProjectTotalBudget", "ProjectStatus",
                 "ProjectType", "CountryRegion", "ProjectStartDate"),
    "sites": ("SiteID", "CountryRegion"),
    "purchaseOrders": ("PONumber", "CountryRegion"),
    "purchaseOrderLines": ("PONumber", "POLineNumber", "PODescription", "AFEProjectID",
                          "SiteID", "POTaskCode", "BusinessUnit", "SupplierName",
                          "CountryRegion", "SourceSystem"),
}
OUTPUT_NAMES = (
    "popa-context.json", "popa-context-load.sql", "popa-context-data.sql",
    "popa-context-engine.sql", "popa-context-review.csv",
    "popa-field-disposition.csv", "popa-context-portal-preview.sql",
    "popa-context-site-po-preview.sql",
    "popa-context-project-line-preview.sql", "popa-context-nullable-schema-preview.sql",
    "popa-context-handoff.zip",
)


def _compressed_payload(sql_payload: dict[str, Any]) -> str:
    encoded = base64.b64encode(gzip.compress(
        json.dumps(sql_payload, ensure_ascii=True, separators=(",", ":")).encode("ascii"),
        mtime=0,
    )).decode("ascii")
    return (
        BEGIN_PAYLOAD + "\n"
        "-- ASCII JSON preserves Unicode through JSON escapes; GZIP is lossless.\n"
        "DECLARE @PopaBase64 nvarchar(max) = CAST(N'' AS nvarchar(max))\n"
        + "".join(f"    + N'{encoded[start:start + 4000]}'\n"
                  for start in range(0, len(encoded), 4000))
        + ";\n"
        "DECLARE @PopaCompressed varbinary(max) = CAST(N'' AS xml).value(\n"
        "    'xs:base64Binary(sql:variable(\"@PopaBase64\"))', 'varbinary(max)');\n"
        "DECLARE @Payload nvarchar(max) = CONVERT(nvarchar(max),\n"
        "    CONVERT(varchar(max), DECOMPRESS(@PopaCompressed)));\n"
        + END_PAYLOAD
    )


def remaining_table_payload(payload: dict[str, Any]) -> dict[str, Any]:
    core_fields = {"PONumber", "POLineNumber", "PODescription", "BusinessUnit",
                   "SourceCountry/System_Key/System_Name"}
    issues = [issue for issue in payload["issues"] if issue["TableName"] == "prep.Project"]
    for row in payload["review"]:
        if row["table"] == "prep.PurchaseOrderLine":
            conflicts = set(row["conflictingFields"].split("|")) & core_fields
            if conflicts:
                issues.append({
                    "TableName": "prep.PurchaseOrderLine", "RecordKey": row["recordKey"],
                    "Reason": "Conflicting PO-line core/source identity: " + ", ".join(sorted(conflicts)),
                })
    return {
        "schemaVersion": payload["schemaVersion"], "sourceFiles": payload["sourceFiles"],
        "projects": [{field: row[field] for field in SQL_FIELDS["projects"]} for row in payload["projects"]],
        "purchaseOrderLines": [
            {field: row[field] for field in
             ("PONumber", "POLineNumber", "PODescription", "BusinessUnit", "CountryRegion", "SourceSystem")}
            for row in payload["purchaseOrderLines"]
        ],
        "issues": issues, "allocationContextImported": False,
        "sourceAllocationCount": len(payload["sourceAllocations"]),
    }


def render_handoff(payload: dict[str, Any], directory: Path) -> dict[str, str]:
    sql_payload = {key: payload[key] for key in ("schemaVersion", "sourceFiles", "issues")}
    for key, fields in SQL_FIELDS.items():
        sql_payload[key] = [{field: row[field] for field in fields} for row in payload[key]]
    script = render_sql(TEMPLATE.read_text(encoding="utf-8"), sql_payload)
    match = re.search(re.escape(BEGIN_PAYLOAD) + r".*?" + re.escape(END_PAYLOAD),
                      script, flags=re.DOTALL)
    if match is None:
        raise ValueError("SQL handoff requires a source payload block")
    portal = (
        "-- Azure portal Query Editor: run this entire standalone query.\n"
        "-- Preview only; no SQLCMD Mode, local includes or separate data files needed.\n"
        + script[:match.start()] + _compressed_payload(sql_payload) + script[match.end():]
    )
    partial_payload = {
        "schemaVersion": payload["schemaVersion"], "sourceFiles": payload["sourceFiles"],
        "sites": sql_payload["sites"], "purchaseOrders": sql_payload["purchaseOrders"],
        "issues": [issue for issue in payload["issues"]
                   if issue["TableName"] in {"prep.Site", "prep.PurchaseOrder"}],
        "pendingProjects": len(payload["projects"]),
        "pendingPurchaseOrderLines": len(payload["purchaseOrderLines"]),
    }
    partial_script = render_sql(SITE_PO_TEMPLATE.read_text(encoding="utf-8"), partial_payload)
    partial = re.sub(re.escape(BEGIN_PAYLOAD) + r".*?" + re.escape(END_PAYLOAD),
                     lambda _: _compressed_payload(partial_payload), partial_script,
                     count=1, flags=re.DOTALL)
    remaining_script = render_sql(PROJECT_LINE_TEMPLATE.read_text(encoding="utf-8"),
                                  remaining_table_payload(payload))
    remaining = re.sub(re.escape(BEGIN_PAYLOAD) + r".*?" + re.escape(END_PAYLOAD),
                       lambda _: _compressed_payload(remaining_table_payload(payload)),
                       remaining_script, count=1, flags=re.DOTALL)
    data = match[0].replace("$", r"\u0024") + "\n"
    engine = script[:match.start()] + "-- @Payload is supplied by the data include.\n" + script[match.end():]
    declaration = "DECLARE @ApplyChanges bit = 0;"
    if engine.count(declaration) != 1:
        raise ValueError("SQL handoff requires exactly one preview control")
    engine = engine.replace(declaration, "-- @ApplyChanges is supplied by the launcher.")
    folder = str(directory.resolve())
    if any(char in folder for char in ('"', "\r", "\n")):
        raise ValueError("SQLCMD handoff directory cannot contain quotes or line breaks")
    launcher = (
        "-- SSMS: connect to the target database and enable Query > SQLCMD Mode.\n"
        "-- Keep all three SQL files together; update HandoffDirectory if moved.\n"
        "-- Preview only. Resolve/review blockers before considering ApplyChanges=1.\n"
        f':setvar HandoffDirectory "{folder}"\n'
        ":On Error exit\n"
        "DECLARE @ApplyChanges bit = 0;\n"
        ':r "$(HandoffDirectory)\\popa-context-data.sql"\n'
        ':r "$(HandoffDirectory)\\popa-context-engine.sql"\n'
    )
    return {
        "popa-context-load.sql": launcher,
        "popa-context-data.sql": data,
        "popa-context-engine.sql": engine,
        "popa-context-portal-preview.sql": portal,
        "popa-context-site-po-preview.sql": partial,
        "popa-context-project-line-preview.sql": remaining,
        "popa-context-nullable-schema-preview.sql": NULLABLE_SCHEMA.read_text(encoding="utf-8"),
    }


def _text(value: str | None) -> str | None:
    if value is None or value.strip().upper() in {"", "NULL"}:
        return None
    return value.strip()


def _budget(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        amount = Decimal(value)
        if not amount.is_finite() or abs(amount) >= Decimal("1000000000000000"):
            raise ValueError(f"Budget exceeds decimal(19,4): {value}")
        if amount != amount.quantize(Decimal("0.0001")):
            raise ValueError(f"Budget loses decimal(19,4) precision: {value}")
        return format(amount, ".4f")
    except InvalidOperation as exc:
        raise ValueError(f"Invalid source budget: {value}") from exc


def _validate(field: str, value: str | None) -> str | None:
    if value is None:
        return None
    if field in LIMITS and len(value.encode("utf-16-le")) // 2 > LIMITS[field]:
        raise ValueError(f"{field} exceeds SQL storage length")
    if field in {"ProjectStatus", "ProjectType"}:
        try:
            value.encode("cp1252")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{field} cannot fit the supplied SQL varchar collation") from exc
    if field == "ProjectTotalBudget":
        return _budget(value)
    if field == "ProjectStartDate":
        return date.fromisoformat(value).isoformat()
    return value


def build_payload(sources: list[tuple[str, Path]]) -> dict[str, Any]:
    if not sources or len({country for country, _ in sources}) != len(sources):
        raise ValueError("Provide at most one US and one CA source snapshot; do not mix environments")
    rows: list[dict[str, Any]] = []
    files = []
    excluded = []
    for country, path in sources:
        if country not in {"US", "CA"}:
            raise ValueError("POPA context sources must be explicitly US or CA, never EU")
        source_bytes = path.read_bytes()
        with io.StringIO(source_bytes.decode("utf-8-sig"), newline="") as stream:
            reader = csv.DictReader(stream)
            fields = reader.fieldnames or []
            if len(fields) != len(set(fields)) or not REQUIRED <= set(fields):
                raise ValueError(f"{path.name}: missing/duplicate POPA headers; missing {sorted(REQUIRED - set(fields))}")
            count = 0
            types: Counter[str] = Counter()
            for number, raw in enumerate(reader, 2):
                if None in raw or any(value is None for value in raw.values()):
                    raise ValueError(f"{path.name}:{number}: malformed CSV row")
                values = {key: _text(value) for key, value in raw.items()}
                if values["Type_Order"] not in {"1", "3", "4"}:
                    raise ValueError(f"{path.name}:{number}: unknown Type_Order")
                if not values["System_Key"] or not values["System_Name"]:
                    raise ValueError(f"{path.name}:{number}: missing source-system scope")
                for source, (target, _) in MAPPING.items():
                    try:
                        values[source] = _validate(target, values[source])
                    except ValueError as exc:
                        raise ValueError(f"{path.name}:{number}:{source}: {exc}") from exc
                rows.append(dict(values, CountryRegion=country, SourceFile=path.name, SourceRow=number))
                count += 1
                types[str(values["Type_Order"])] += 1
        files.append({
            "country": country, "fileName": path.name,
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "rowCount": count, "typeCounts": dict(types),
        })
        for field in fields:
            if field in MAPPING:
                use = "Confirmed four-table context mapping"
            elif field in {"System_Key", "System_Name", "Type_Order"}:
                use = "Source scope/filter only; no permanent target column"
            elif field == "Supplier_Name":
                use = "Review/approved crosswalk only; NOT VendorID and not loaded as VendorName"
            else:
                use = "Excluded from this four-table load; retained in original source only"
            excluded.append({"fileName": path.name, "sourceColumn": field, "disposition": use})

    issues: list[dict[str, str]] = []
    review: list[dict[str, Any]] = []

    def candidates(table: str, eligible: list[dict[str, Any]], keys: dict[str, str],
                   fields: dict[str, str]) -> list[dict[str, Any]]:
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in eligible:
            if any(row[source] is None for source in keys.values()):
                issues.append({
                    "TableName": table, "RecordKey": f"{row['SourceFile']}:{row['SourceRow']}",
                    "Reason": "Missing source target key; no record manufactured",
                })
                continue
            normalized = []
            for target, source in keys.items():
                value = str(row[source])
                if target == "POLineNumber":
                    if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 2147483647:
                        raise ValueError(f"{row['SourceFile']}:{row['SourceRow']}: invalid SQL PO line number")
                    value = str(int(value))
                normalized.append(value.casefold())
            groups[tuple(normalized)].append(row)
        result = []
        for key in sorted(groups):
            members = groups[key]
            scopes = {(r["CountryRegion"], r["System_Key"], r["System_Name"]) for r in members}
            conflict = []
            entry: dict[str, Any] = {
                "CountryRegion": next(iter({r["CountryRegion"] for r in members}))
                if len({r["CountryRegion"] for r in members}) == 1 else None,
                "SourceSystem": next(iter({r["System_Name"] for r in members}))
                if len({r["System_Name"] for r in members}) == 1 else None,
            }
            for target, source in {**keys, **fields}.items():
                options = sorted({str(r[source]) for r in members if r[source] is not None})
                if target == "POLineNumber":
                    options = sorted({str(int(value)) for value in options})
                if len(options) > 1:
                    conflict.append(target)
                entry[target] = options[0] if len(options) == 1 else None
                if target == "POLineNumber" and entry[target] is not None:
                    entry[target] = int(entry[target])
            if len(scopes) > 1:
                conflict.append("SourceCountry/System_Key/System_Name")
            record_key = " / ".join(str(entry.get(field) or "?") for field in keys)
            if conflict:
                issues.append({
                    "TableName": table, "RecordKey": record_key,
                    "Reason": "Conflicting source values: " + ", ".join(conflict) + "; no first-row selection",
                })
            entry["SourceRowCount"] = len(members)
            result.append(entry)
            prerequisites = []
            if table == "prep.Project":
                prerequisites.append("New project requires approved BundlingInScope")
                if entry["ProjectTotalBudget"] is None:
                    prerequisites.append("New project requires authoritative ProjectTotalBudget")
            elif table == "prep.PurchaseOrderLine":
                prerequisites.append("New PO line requires authoritative VendorID and valid project/site references")
            review.append({
                "table": table, "recordKey": record_key, "country": entry["CountryRegion"],
                "sourceSystem": entry["SourceSystem"], "sourceRowCount": len(members),
                "conflictingFields": "|".join(conflict),
                "missingSourceFields": "|".join(field for field in fields if entry[field] is None),
                "newRowPrerequisites": "|".join(prerequisites),
                "recordStatus": "SOURCE_CONFLICT_BLOCKS_BATCH" if conflict else "REQUIRES_TARGET_PREFLIGHT",
                "sourceRows": "|".join(f"{r['SourceFile']}:{r['SourceRow']}" for r in members),
            })
        return result

    projects = candidates("prep.Project", rows, {"AFEProjectID": "Project_Number"}, {
        target: source for target, source in PROJECT_FIELDS.items() if target != "AFEProjectID"
    })
    sites = candidates("prep.Site", [r for r in rows if r["Site_Id"] is not None],
                       {"SiteID": "Site_Id"}, {})
    po_rows = [r for r in rows if r["Type_Order"] == "4"]
    orders = candidates("prep.PurchaseOrder", po_rows, {"PONumber": "PO_Number"}, {})
    lines = candidates("prep.PurchaseOrderLine", po_rows, {
        "PONumber": "PO_Number", "POLineNumber": "PO_Line_Number",
    }, LINE_FIELDS)
    return {
        "schemaVersion": 1, "sourceFiles": files, "projects": projects, "sites": sites,
        "purchaseOrders": orders, "purchaseOrderLines": lines, "issues": issues,
        "review": review, "fieldDisposition": excluded,
        "sourceAllocations": [
            {field: row.get(field) for field in
             ("SourceFile", "SourceRow", "CountryRegion", "System_Key", "System_Name",
              "PO_Number", "PO_Line_Number", "PO_Invoice_Distribution_Number",
              "Project_Number", "Site_Id", "Task_Type", "Bus_Entity")}
            for row in po_rows
        ],
        "boundaries": [
            "NA POPA only; no EU data or invoice/line/review/reference writes",
            "Project/site context may occur on Type_Order 1, 3 or 4; PO context only on 4",
            "Budgets are not qualifying actual expenditure; no financial amounts are summed",
            "Missing budgets, BundlingInScope and VendorID are not synthesized",
            "Existing nonmatching records block rather than being overwritten",
            "Country comes from explicitly assigned source files, not Site_Bus_Entity",
            "Project/PO/Site keys are collision-checked within this supplied batch; global uniqueness requires source-owner confirmation",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--us-csv", type=Path)
    parser.add_argument("--ca-csv", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir
    if any((output / name).exists() for name in OUTPUT_NAMES):
        raise FileExistsError("Use a fresh output directory; do not overwrite reviewed SQL/approval edits")
    sources = [(country, path) for country, path in (("US", args.us_csv), ("CA", args.ca_csv)) if path]
    payload = build_payload(sources)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "popa-context.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")
    for name, text in render_handoff(payload, output).items():
        with (output / name).open("x", encoding="utf-8") as stream:
            stream.write(text)
    for name, records in (("popa-context-review.csv", payload["review"]),
                          ("popa-field-disposition.csv", payload["fieldDisposition"])):
        if records:
            with (output / name).open("x", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(records[0]))
                writer.writeheader()
                writer.writerows(records)
    with (output / "popa-context-handoff.zip").open("xb") as stream:
        with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
            for name in OUTPUT_NAMES:
                path = output / name
                if (path.suffix in {".sql", ".csv"} or name == "popa-context.json") and path.exists():
                    archive.write(path, arcname=name)
    print(json.dumps({
        "sourceFiles": payload["sourceFiles"],
        "candidateCounts": {table: len(payload[key]) for table, key in (
            ("prep.Project", "projects"), ("prep.Site", "sites"),
            ("prep.PurchaseOrder", "purchaseOrders"), ("prep.PurchaseOrderLine", "purchaseOrderLines"),
        )},
        "sourceConflicts": len(payload["issues"]),
        "remainingTableBlockers": len(remaining_table_payload(payload)["issues"]),
        "projectsWithoutBudget": sum(r["ProjectTotalBudget"] is None for r in payload["projects"]),
        "newProjectScopeAndVendorMappings": "Remaining-table loader leaves unknown scope/vendor NULL; legacy full loader remains strict",
        "outputDirectory": str(output), "sqlExecuted": False,
        "queryClient": "Azure portal: popa-context-portal-preview.sql; SSMS SQLCMD: popa-context-load.sql",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
