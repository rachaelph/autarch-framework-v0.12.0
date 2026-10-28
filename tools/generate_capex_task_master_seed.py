"""Generate an idempotent SQL seed from the approved CapEx TaskMaster workbook."""

from __future__ import annotations

import argparse
import hashlib
import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

EXPECTED_SHA256 = (
    "CC41B492A5DE454EFD7405066EF1EAA6CDE1FF050C8E71ED180831197B6EBA13"
)
SOURCE_FILE = "Task_Type_Export_9_6_2026.xlsx"
SOURCE_SHEET = "US Task Type"
SOURCE_VERSION = "2026-09-06"
REGION_CODE = "US"
EXPECTED_ROW_COUNT = 761

EXPECTED_HEADERS = (
    "Task Type",
    "Description",
    "Active",
    "GL CIP Major Account",
    "GL CIP Sub Account",
    "GL Asset Major Account",
    "GL Asset Sub Account",
    "GL Accum Major Account",
    "GL Accum Sub Account",
    "GL Depr Major Account",
    "GL Depr Sub Account",
    "Asset Category Major",
    "Asset Category Minor",
    "Property Type",
)

STAGE_COLUMNS = (
    "regionCode",
    "taskCode",
    "description",
    "active",
    "glCipMajorAccount",
    "glCipSubAccount",
    "glAssetMajorAccount",
    "glAssetSubAccount",
    "glAccumMajorAccount",
    "glAccumSubAccount",
    "glDeprMajorAccount",
    "glDeprSubAccount",
    "assetCategoryMajor",
    "assetCategoryMinor",
    "propertyType",
)


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference)
    if letters is None:
        raise ValueError(f"Invalid cell reference: {reference}")

    value = 0
    for character in letters.group(0):
        value = value * 26 + ord(character) - ord("A") + 1
    return value - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(
            archive.read("xl/sharedStrings.xml")
        )
    except KeyError:
        return []

    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    return [
        "".join(item.itertext())
        for item in root.findall(f"{namespace}si")
    ]


def _sheet_path(
    archive: zipfile.ZipFile,
    sheet_name: str,
) -> str:
    workbook = ElementTree.fromstring(
        archive.read("xl/workbook.xml")
    )
    relationships = ElementTree.fromstring(
        archive.read("xl/_rels/workbook.xml.rels")
    )

    workbook_namespace = (
        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    )
    relationship_namespace = (
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    )
    package_relationship_namespace = (
        "{http://schemas.openxmlformats.org/package/2006/relationships}"
    )

    relationship_targets = {
        relationship.attrib["Id"]: relationship.attrib["Target"]
        for relationship in relationships.findall(
            f"{package_relationship_namespace}Relationship"
        )
    }

    for sheet in workbook.findall(
        f".//{workbook_namespace}sheet"
    ):
        if sheet.attrib["name"] != sheet_name:
            continue

        relationship_id = sheet.attrib[
            f"{relationship_namespace}id"
        ]
        target = relationship_targets[relationship_id].lstrip("/")

        if not target.startswith("xl/"):
            target = f"xl/{target}"

        return posixpath.normpath(target)

    raise ValueError(f"Workbook sheet not found: {sheet_name}")


def _cell_value(
    cell: ElementTree.Element,
    shared_strings: list[str],
) -> str | None:
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    cell_type = cell.attrib.get("t")
    value = cell.find(f"{namespace}v")

    if cell_type == "inlineStr":
        inline = cell.find(f"{namespace}is")
        return None if inline is None else "".join(inline.itertext())

    if value is None or value.text is None:
        return None

    if cell_type == "s":
        return shared_strings[int(value.text)]

    if cell_type == "b":
        return "1" if value.text == "1" else "0"

    return value.text


def read_us_task_rows(workbook_path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(workbook_path) as archive:
        shared_strings = _shared_strings(archive)
        sheet_path = _sheet_path(archive, SOURCE_SHEET)
        root = ElementTree.fromstring(archive.read(sheet_path))

    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    raw_rows: list[list[str | None]] = []

    for row in root.findall(f".//{namespace}row"):
        values: list[str | None] = []

        for cell in row.findall(f"{namespace}c"):
            index = _column_index(cell.attrib["r"])
            while len(values) <= index:
                values.append(None)
            values[index] = _cell_value(cell, shared_strings)

        raw_rows.append(values)

    if not raw_rows:
        raise ValueError("TaskMaster workbook sheet is empty.")

    headers = tuple(
        "" if value is None else value.strip()
        for value in raw_rows[0]
    )

    if headers != EXPECTED_HEADERS:
        raise ValueError(
            f"Unexpected headers for {SOURCE_SHEET}: {headers}"
        )

    rows: list[dict[str, str]] = []
    for raw_row in raw_rows[1:]:
        padded = raw_row + [None] * (
            len(EXPECTED_HEADERS) - len(raw_row)
        )
        normalized = {
            header: "" if value is None else value.strip()
            for header, value in zip(EXPECTED_HEADERS, padded, strict=True)
        }

        if not any(normalized.values()):
            continue

        rows.append(normalized)

    return rows


def _sql_string(value: str | None) -> str:
    if value is None or value == "":
        return "NULL"
    escaped = value.replace("'", "''")
    return f"N'{escaped}'"


def validate_rows(rows: list[dict[str, str]]) -> None:
    if len(rows) != EXPECTED_ROW_COUNT:
        raise ValueError(
            f"Expected {EXPECTED_ROW_COUNT} US tasks, found {len(rows)}."
        )

    task_codes = [row["Task Type"] for row in rows]

    if any(not task_code for task_code in task_codes):
        raise ValueError("TaskMaster contains an empty task code.")

    if len(set(task_codes)) != len(task_codes):
        raise ValueError("TaskMaster contains duplicate US task codes.")

    if any(not row["Description"] for row in rows):
        raise ValueError("TaskMaster contains an empty description.")

    if any(row["Active"] not in {"0", "1"} for row in rows):
        raise ValueError("TaskMaster contains an invalid Active value.")


def render_seed(rows: list[dict[str, str]]) -> str:
    values: list[str] = []

    for row in rows:
        record = (
            REGION_CODE,
            row["Task Type"],
            row["Description"],
            "1" if row["Active"] == "1" else "0",
            row["GL CIP Major Account"],
            row["GL CIP Sub Account"],
            row["GL Asset Major Account"],
            row["GL Asset Sub Account"],
            row["GL Accum Major Account"],
            row["GL Accum Sub Account"],
            row["GL Depr Major Account"],
            row["GL Depr Sub Account"],
            row["Asset Category Major"],
            row["Asset Category Minor"],
            row["Property Type"],
        )

        sql_values = [
            item if index == 3 else _sql_string(item)
            for index, item in enumerate(record)
        ]
        values.append(f"    ({', '.join(sql_values)})")

    column_list = ",\n        ".join(
        f"[{column}]" for column in STAGE_COLUMNS
    )
    value_list = ",\n".join(values)

    update_assignments = ",\n            ".join(
        f"target.[{column}] = source.[{column}]"
        for column in STAGE_COLUMNS[2:]
    )
    insert_columns = ",\n            ".join(
        f"[{column}]" for column in STAGE_COLUMNS
    )
    insert_values = ",\n            ".join(
        f"source.[{column}]" for column in STAGE_COLUMNS
    )

    return f"""-- Generated from {SOURCE_FILE}; do not edit manually.
-- Sheet: {SOURCE_SHEET}
-- SHA-256: {EXPECTED_SHA256}
-- Rows: {EXPECTED_ROW_COUNT}

SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @sourceFile NVARCHAR(260) = N'{SOURCE_FILE}';
DECLARE @sourceSheet NVARCHAR(128) = N'{SOURCE_SHEET}';
DECLARE @sourceVersion NVARCHAR(64) = N'{SOURCE_VERSION}';
DECLARE @sourceSha256 CHAR(64) = '{EXPECTED_SHA256}';
DECLARE @expectedRowCount INT = {EXPECTED_ROW_COUNT};

CREATE TABLE #TaskMasterStage
(
    [regionCode] CHAR(2) NOT NULL,
    [taskCode] NVARCHAR(64) NOT NULL,
    [description] NVARCHAR(512) NOT NULL,
    [active] BIT NOT NULL,
    [glCipMajorAccount] NVARCHAR(32) NULL,
    [glCipSubAccount] NVARCHAR(32) NULL,
    [glAssetMajorAccount] NVARCHAR(32) NULL,
    [glAssetSubAccount] NVARCHAR(32) NULL,
    [glAccumMajorAccount] NVARCHAR(32) NULL,
    [glAccumSubAccount] NVARCHAR(32) NULL,
    [glDeprMajorAccount] NVARCHAR(32) NULL,
    [glDeprSubAccount] NVARCHAR(32) NULL,
    [assetCategoryMajor] NVARCHAR(256) NULL,
    [assetCategoryMinor] NVARCHAR(256) NULL,
    [propertyType] NVARCHAR(64) NULL
);

INSERT INTO #TaskMasterStage
(
        {column_list}
)
VALUES
{value_list};

IF (SELECT COUNT(*) FROM #TaskMasterStage) <> @expectedRowCount
    THROW 51000, 'TaskMaster seed row count is invalid.', 1;

IF EXISTS
(
    SELECT [regionCode], [taskCode]
    FROM #TaskMasterStage
    GROUP BY [regionCode], [taskCode]
    HAVING COUNT(*) > 1
)
    THROW 51001, 'TaskMaster seed contains duplicate task codes.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    UPDATE [ref].[TaskMaster]
    SET [isCurrent] = 0
    WHERE
        [regionCode] = '{REGION_CODE}'
        AND [isCurrent] = 1
        AND [sourceSha256] <> @sourceSha256;

    MERGE [ref].[TaskMaster] WITH (HOLDLOCK) AS target
    USING #TaskMasterStage AS source
        ON target.[regionCode] = source.[regionCode]
        AND target.[taskCode] = source.[taskCode]
        AND target.[sourceSha256] = @sourceSha256
    WHEN MATCHED THEN
        UPDATE SET
            {update_assignments},
            target.[sourceFile] = @sourceFile,
            target.[sourceSheet] = @sourceSheet,
            target.[sourceVersion] = @sourceVersion,
            target.[isCurrent] = 1,
            target.[importedAtUtc] = SYSUTCDATETIME()
    WHEN NOT MATCHED THEN
        INSERT
        (
            {insert_columns},
            [sourceFile],
            [sourceSheet],
            [sourceVersion],
            [sourceSha256],
            [isCurrent]
        )
        VALUES
        (
            {insert_values},
            @sourceFile,
            @sourceSheet,
            @sourceVersion,
            @sourceSha256,
            1
        );

    IF
    (
        SELECT COUNT(*)
        FROM [ref].[TaskMaster]
        WHERE
            [regionCode] = '{REGION_CODE}'
            AND [isCurrent] = 1
            AND [active] = 1
            AND [sourceSha256] = @sourceSha256
    ) <> @expectedRowCount
        THROW 51002, 'TaskMaster current row count is invalid.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
"""

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workbook",
        type=Path,
        default=Path(
            "examples/data/capex/Task_Type_Export_9_6_2026.xlsx"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "sql/data/005_seed_us_task_master.sql"
        ),
    )
    arguments = parser.parse_args()

    digest = hashlib.sha256(
        arguments.workbook.read_bytes()
    ).hexdigest().upper()

    if digest != EXPECTED_SHA256:
        raise ValueError(
            f"Unexpected workbook SHA-256: {digest}"
        )

    rows = read_us_task_rows(arguments.workbook)
    validate_rows(rows)

    arguments.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    arguments.output.write_text(
        render_seed(rows),
        encoding="utf-8",
        newline="\n",
    )

    print(
        f"Generated {arguments.output} with "
        f"{len(rows)} unique US tasks."
    )


if __name__ == "__main__":
    main()
