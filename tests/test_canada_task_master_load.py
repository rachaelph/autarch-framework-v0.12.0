from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

from tools import build_canada_task_master_load as loader


def workbook(tmp_path, monkeypatch, *, code="0000", description="ÉQUIPEMENT", active="1"):
    monkeypatch.setattr(loader, "SHEETS", {"Task Type - MACS": 1, "Task Type - CTI": 1})
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relationships = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    path = tmp_path / loader.SOURCE_FILE
    with ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{namespace}" xmlns:r="{relationships}"><sheets>'
                         '<sheet name="Task Type - MACS" sheetId="1" r:id="r1"/>'
                         '<sheet name="Task Type - CTI" sheetId="2" r:id="r2"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels",
                         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Id="r1" Target="worksheets/sheet1.xml"/>'
                         '<Relationship Id="r2" Target="worksheets/sheet2.xml"/></Relationships>')
        for number in (1, 2):
            values = [code, description if number == 1 else "AUTRE DESCRIPTION", active,
                      "1590", "01", "1530", "00", "1535", "00", "7500", "15",
                      "EQUIPMENT STORE", "EQUIPMENT STORE", "Depreciable"]
            rows = []
            for row_number, fields in enumerate((loader.EXPECTED_HEADERS, values), 1):
                cells = "".join(
                    f'<c r="{chr(65+i)}{row_number}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
                    for i, value in enumerate(fields)
                )
                rows.append(f'<row r="{row_number}">{cells}</row>')
            archive.writestr(f"xl/worksheets/sheet{number}.xml",
                             f'<worksheet xmlns="{namespace}"><sheetData>{"".join(rows)}</sheetData></worksheet>')
    return path, hashlib.sha256(path.read_bytes()).hexdigest().upper()


def test_canadian_scopes_preserve_shared_codes_french_text_and_original_accounts(tmp_path, monkeypatch):
    path, digest = workbook(tmp_path, monkeypatch, active="0")
    payload = loader.build_payload(path, expected_sha256=digest)
    assert payload["sharedCodeCount"] == 1 and len(payload["rows"]) == 2
    first, second = payload["rows"]
    assert first["taskCode"] == second["taskCode"] == "0000"
    assert first["sourceSheet"] != second["sourceSheet"]
    assert first["description"] == "ÉQUIPEMENT" and second["description"] == "AUTRE DESCRIPTION"
    assert first["glCipSubAccount"] == "01" and first["glAssetSubAccount"] == "00"
    assert first["active"] is False
    assert payload["businessVersion"] is payload["effectiveFrom"] is payload["effectiveTo"] is None
    assert payload["sourceSha256"] == digest and payload["sourceVersion"].startswith("CONTENT-")
    sql = loader.render_sql(payload)
    encoded = "".join(re.findall(r"\+ N'([A-Za-z0-9+/=]+)'", sql))
    assert json.loads(gzip.decompress(base64.b64decode(encoded))) == payload
    assert "DECLARE @ApplyChanges bit=0" in sql and "DECLARE @ApproveCatalogKeyChange bit=0" in sql
    assert "SQLCMD" in sql and "\n:r " not in sql


@pytest.mark.parametrize("change", [{"code": ""}, {"description": ""}, {"active": "3"}, {"description": "x" * 513}])
def test_invalid_canadian_source_values_stop_before_sql_generation(tmp_path, monkeypatch, change):
    path, digest = workbook(tmp_path, monkeypatch, **change)
    with pytest.raises(ValueError, match="invalid|exceeds"):
        loader.build_payload(path, expected_sha256=digest)


def test_changed_workbook_hash_is_not_silently_imported(tmp_path, monkeypatch):
    path, _ = workbook(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="filename/hash"):
        loader.build_payload(path)
