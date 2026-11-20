"""Prepare a preview-first Canadian TaskMaster DBA handoff; never execute SQL."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from tools.build_popa_context_load import _compressed_payload
from tools.generate_capex_task_master_seed import EXPECTED_HEADERS, STAGE_COLUMNS, read_task_rows


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILE = "Task List CAN.xlsx"
EXPECTED_SHA256 = "A9DCD3791653AD62271B757E7B830DB71453EE50FC3DD982473F2996C879AB38"
SHEETS = {"Task Type - MACS": 1434, "Task Type - CTI": 3324}
LIMITS = (64, 512, 1, *([32] * 8), 256, 256, 64)
TEMPLATE = ROOT / "sql" / "data" / "016_import_canada_task_master.sql"


def build_payload(workbook: Path, *, expected_sha256: str = EXPECTED_SHA256) -> dict:
    digest = hashlib.sha256(workbook.read_bytes()).hexdigest().upper()
    if workbook.name != SOURCE_FILE or digest != expected_sha256:
        raise ValueError("Canadian workbook filename/hash differs from the reviewed source.")
    records = []
    codes_by_sheet = {}
    for sheet, expected_count in SHEETS.items():
        rows = read_task_rows(workbook, sheet)
        if len(rows) != expected_count:
            raise ValueError(f"{sheet}: expected {expected_count} tasks, found {len(rows)}.")
        codes = [row["Task Type"] for row in rows]
        if len(set(codes)) != len(codes):
            raise ValueError(f"{sheet}: duplicate task codes require source-owner review.")
        codes_by_sheet[sheet] = set(codes)
        for row in rows:
            values = [row[header] for header in EXPECTED_HEADERS]
            if not values[0] or not values[1] or values[2] not in {"0", "1"}:
                raise ValueError(f"{sheet}: invalid task code, description or Active flag.")
            if any(len(value.encode("utf-16-le")) // 2 > limit for value, limit in zip(values, LIMITS, strict=True)):
                raise ValueError(f"{sheet}/{values[0]}: source text exceeds the TaskMaster column size.")
            record = dict(zip(STAGE_COLUMNS, ["CA", *values], strict=True))
            record.update(sourceSheet=sheet, active=values[2] == "1")
            records.append(record)
    shared = codes_by_sheet["Task Type - MACS"] & codes_by_sheet["Task Type - CTI"]
    return {
        "schemaVersion": "1.0-canada-taskmaster-load", "sourceFile": SOURCE_FILE,
        "sourceSha256": digest, "sourceVersion": "CONTENT-" + digest[:32],
        "businessVersion": None, "effectiveFrom": None, "effectiveTo": None,
        "sheets": SHEETS, "sharedCodeCount": len(shared), "rows": records,
    }


def render_sql(payload: dict) -> str:
    declaration = _compressed_payload(payload).replace("@Popa", "@CanadaTask")
    return TEMPLATE.read_text(encoding="utf-8").replace(
        "DECLARE @Payload nvarchar(max)=N'{}';", declaration, 1,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = build_payload(args.workbook)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    sql = args.output_dir / "canada-taskmaster-preview.sql"
    sql.write_text(render_sql(payload), encoding="utf-8")
    snapshot = args.output_dir / "canada-taskmaster-source.json"
    snapshot.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    review = args.output_dir / "canada-taskmaster-review.csv"
    with review.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[*STAGE_COLUMNS, "sourceSheet"])
        writer.writeheader()
        writer.writerows(payload["rows"])
    with ZipFile(args.output_dir / "canada-taskmaster-handoff.zip", "w", ZIP_DEFLATED) as archive:
        for path in (sql, snapshot, review):
            archive.write(path, path.name)
    print(json.dumps({
        "outputDir": str(args.output_dir.resolve()), "rows": len(payload["rows"]),
        "sharedCodeCount": payload["sharedCodeCount"], "sourceSha256": payload["sourceSha256"],
        "sqlExecuted": False,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
