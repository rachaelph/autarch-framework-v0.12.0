from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from openpyxl import load_workbook

from autarch.capex.demo_v2_asset_classes import load_asset_class_master
from autarch.capex.demo_v2_contracts import DemoWorkflowRequest, InvoicePackageReference
from autarch.capex.demo_v2_llm_decisions import SqlLlmDecisionWorkflow
from autarch.capex.demo_v2_reference import DemoPolicySnapshot, TaskMasterSnapshot
from autarch.capex.demo_v2_adf_sql import load_adf_asset_class_master
from tests.test_capex_demo_v2_adf_integration import ConditionalModel
from tests.test_capex_demo_v2_adf_job import MASTER
from tests.test_capex_demo_v2_task_suggestion import _line, _package
from tools.generate_capex_eu_asset_classes import _sql_catalog_declaration


class ClassificationModel(ConditionalModel):
    def __init__(self, code="295"):
        super().__init__()
        self.code = code

    def complete(self, prompt, system=None):
        if prompt.startswith("Invoice request JSON:"):
            return super().complete(prompt, system)
        payload = json.loads(prompt)
        if payload["stage"] != "ASSET_CLASS_SUGGESTION":
            return super().complete(prompt, system)
        return json.dumps({"lines": [{
            "lineId": item["lineId"], "assetClassCode": self.code,
            "resolutionStatus": "RESOLVED" if self.code else "REVIEW",
            "unresolvedReason": None if self.code else "MOCK TEST ONLY: unsupported category.",
            "itemIdentified": "MOCK TEST ONLY", "confidence": 0.9 if self.code else 0.1,
            "rationale": "MOCK TEST ONLY: supplied EU catalog classification.",
            "evidenceRefs": [f"line:{item['lineId']}"]
                + ([f"asset-class:{self.code}"] if self.code else []),
        } for item in payload["lines"]]})


def classification(filename, description="Shop furniture", code="295"):
    package = _package([_line("line-1", description)])
    package = package.model_copy(update={"source": package.source.model_copy(update={"file_name": filename})})
    request = DemoWorkflowRequest(
        workflow_run_id="mock-eu-routing", requested_at_utc=package.created_at_utc,
        packages=[InvoicePackageReference(
            package_id=package.package_id, run_id=package.run_id, package_uri="memory:test",
        )],
        model_deployment="gpt-5.1", task_prompt_version="mock-classification",
    )
    workflow = SqlLlmDecisionWorkflow(
        sources=[package], provider=ClassificationModel(code), deployment="gpt-5.1",
        task_master=TaskMasterSnapshot.model_validate_json(MASTER.read_bytes()),
        fixture_id=request.workflow_run_id, input_sha256="0" * 64, demo_recommendations=True,
        asset_class_master=load_asset_class_master(),
    )
    return package, request, workflow


def test_eu_snapshot_preserves_every_original_account_mapping():
    master = load_asset_class_master()
    path = MASTER.parent.parent / master.source_file
    assert master.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        expected = {
            (i, str(row[2]), row[0], row[1], row[3], row[4], row[5] or "")
            for i, row in enumerate(workbook[master.source_sheet].iter_rows(min_row=2, values_only=True), 2)
        }
    finally:
        workbook.close()
    actual = {
        (mapping.source_row, item.asset_class_code, mapping.object_account, mapping.sub_account,
         mapping.account_name, mapping.account_group, mapping.account_description)
        for item in master.classes for mapping in item.account_mappings
    }
    assert actual == expected


def test_generated_sql_catalog_uses_bounded_literals_and_round_trips_every_mapping():
    root = Path(__file__).resolve().parents[1]
    sql = (root / "sql" / "data" / "006_seed_eu_asset_class_master.sql").read_text(encoding="utf-8")
    assert max(len(line.encode("utf-16le")) // 2 for line in sql.splitlines()) <= 2048
    fragments = re.findall(r"^SET @Catalog \+= N'((?:[^']|'')*)';$", sql, re.MULTILINE)
    assert len(fragments) > 1
    catalog = "".join(fragment.replace("''", "'") for fragment in fragments)
    assert json.loads(catalog) == load_asset_class_master().model_dump(mode="json", by_alias=True)
    checksum = re.search(r"HASHBYTES\('SHA2_256', @Catalog\) <> 0x([0-9a-f]{64})", sql)
    assert checksum is not None
    assert checksum[1] == hashlib.sha256(catalog.encode("utf-16le")).hexdigest()


@pytest.mark.parametrize("quote_offset", [999, 1000])
def test_sql_catalog_chunks_escape_quotes_at_boundaries_without_losing_unicode(quote_offset):
    prefix = '{"description":"'
    catalog = prefix + "x" * (quote_offset - len(prefix)) + "'O'Brien'' \u00e6 \U0001f600" + '"}'
    sql = _sql_catalog_declaration(catalog)
    fragments = re.findall(r"^SET @Catalog \+= N'((?:[^']|'')*)';$", sql, re.MULTILINE)
    restored = "".join(fragment.replace("''", "'") for fragment in fragments)
    assert restored == catalog
    assert json.loads(restored)["description"].endswith("O'Brien'' \u00e6 \U0001f600")
    assert max(len(line.encode("utf-16le")) // 2 for line in sql.splitlines()) <= 2048


def test_small_sql_batches_preserve_complete_sheet2_catalog_under_query_size_limit():
    directory = Path(__file__).resolve().parents[1] / "sql" / "data" / "006_eu_asset_class_batches"
    paths = sorted(directory.glob("[0-9][0-9][0-9]_seed_eu_asset_classes.sql"))
    assert len(paths) > 1
    expected = load_asset_class_master().model_dump(mode="json", by_alias=True)
    restored_classes = []
    for path in paths:
        sql = path.read_bytes().decode("utf-8")
        assert len(sql.encode("utf-16le")) // 2 <= 60_000
        fragments = re.findall(r"^SET @Catalog \+= N'((?:[^']|'')*)';$", sql, re.MULTILINE)
        catalog = "".join(fragment.replace("''", "'") for fragment in fragments)
        payload = json.loads(catalog)
        assert payload["sourceSheet"] == "Sheet2"
        assert {key: value for key, value in payload.items() if key != "classes"} == {
            key: value for key, value in expected.items() if key != "classes"
        }
        checksum = re.search(r"HASHBYTES\('SHA2_256', @Catalog\) <> 0x([0-9a-f]{64})", sql)
        assert checksum is not None
        assert checksum[1] == hashlib.sha256(catalog.encode("utf-16le")).hexdigest()
        restored_classes.extend(payload["classes"])
    assert restored_classes == expected["classes"]


def test_final_small_sql_verifier_hashes_all_classes_and_original_mapping_data():
    path = Path(__file__).resolve().parents[1] / "sql" / "data" / "006_eu_asset_class_batches" / \
        "999_verify_eu_asset_classes.sql"
    sql = path.read_bytes().decode("utf-8")
    assert len(sql.encode("utf-16le")) // 2 <= 60_000
    rows = re.findall(
        r"^\(N'([^']+)', 0x([0-9a-f]{64}), 0x([0-9a-f]{64}), 0x([0-9a-f]{64})\)[,;]?$",
        sql, re.MULTILINE,
    )
    expected = {}
    for entry in load_asset_class_master().model_dump(mode="json", by_alias=True)["classes"]:
        values = (
            entry["assetClassName"], entry["accountGroup"],
            json.dumps(entry["accountMappings"], ensure_ascii=True, separators=(",", ":")),
        )
        expected[entry["assetClassCode"]] = tuple(
            hashlib.sha256(value.encode("utf-16le")).hexdigest() for value in values
        )
    assert {row[0]: tuple(row[1:]) for row in rows} == expected


@pytest.mark.parametrize("filename", ["JC2000A07465-Ariba.xlsx", "JC2000A07814 - Ariba+Proj.tool.xlsx", "Bruegmann.zip"])
def test_source_filename_routes_eu_and_us_to_distinct_models_and_accounting(filename):
    package, request, workflow = classification(filename)
    tasks = workflow.resolve(package, request)
    task = tasks[0]
    eu = filename.startswith("JC")
    assert task.classification_type == ("ASSET_CLASS" if eu else "TASK")
    assert (task.suggested_task_code is None) == eu
    assert (task.suggested_asset_class == "295") == eu
    stages = {call.stage for call in workflow.calls}
    assert stages == ({"ASSET_CLASS_SUGGESTION"} if eu else {"TASK_RETRIEVAL", "TASK_SUGGESTION"})
    policy = DemoPolicySnapshot.model_validate_json((MASTER.parent / "policy-config-v2.json").read_bytes())
    assessment = workflow.assess(package, tasks, policy, request)[0]
    assert assessment.suggested_task_code == task.suggested_task_code
    assert assessment.suggested_asset_class_code == ("295" if eu else None)
    accounting_prompt = json.loads(workflow.calls[-1].prompt)
    assert ("assetClassSuggestions" in accounting_prompt) == eu


@pytest.mark.parametrize(("description", "code", "valid"), [
    ("Electrical Installation", "800", False),
    ("EV charging electrical installation", "800", True),
    ("Shop furniture", "999999", False),
    ("Unknown charge", None, True),
])
def test_eu_classification_rejects_unknown_and_unsupported_ev_codes_but_allows_review(description, code, valid):
    package, request, workflow = classification("JC-example.xlsx", description, code)
    if not valid:
        with pytest.raises(ValueError, match="unknown or ineligible"):
            workflow.resolve(package, request)
    else:
        result = workflow.resolve(package, request)[0]
        assert result.suggested_asset_class == code
        assert result.suggested_task_code is None
        assert result.resolution_status == ("RESOLVED" if code else "REVIEW")


@pytest.mark.parametrize("tamper", [False, True])
def test_sql_eu_catalog_is_checked_against_complete_pinned_workbook_reference(tamper):
    expected = load_asset_class_master()
    payload = expected.model_dump(mode="json", by_alias=True)
    metadata = {key: value for key, value in payload.items() if key != "classes"}
    rows = [{**metadata, **item} for item in payload["classes"]]
    if tamper:
        rows[0]["assetClassName"] = "Unreviewed catalog edit"

    class Connection:
        closed = rolled_back = False

        def cursor(self):
            return self

        def execute(self, query):
            assert "FROM ref.assetClassMaster" in query

        def fetchall(self):
            return [(json.dumps(rows),)]

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    connection = Connection()
    if tamper:
        with pytest.raises(ValueError, match="differs from the pinned"):
            load_adf_asset_class_master(connection, expected)
    else:
        assert load_adf_asset_class_master(connection, expected) == expected
    assert connection.closed and connection.rolled_back
