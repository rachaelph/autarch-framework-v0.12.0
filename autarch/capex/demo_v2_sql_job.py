"""Opt-in SQL reference rehearsal; never publishes legacy InvoiceReady events."""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal, Mapping, Optional, Sequence

from pydantic import Field

from .demo_v2_capitalization import CapitalizationNatureRule, CapitalizationRuleSet
from .demo_v2_contracts import (
    DemoWorkflowRequest, InvoicePackageReference, TaskDetermination,
    NatureResult, TaskMatchMethod, TaskResolutionStatus,
)
from .demo_v2_job import _write_json, _write_result, run_demo_v2_workflow
from .demo_v2_normalization import (
    DemoContextOverlay, NormalizationModel, NormalizedInvoiceLine,
    NormalizedInvoicePackage,
)
from .demo_v2_reference import (
    DemoPolicySnapshot, TaskMasterSnapshot, TaskReferenceEntry,
    build_demo_policy_snapshot, canonical_sha256,
)
from .persistence import DatabaseConnection
from .sql_connection import connect_azure_sql


Row = Mapping[str, object]
_SETTINGS = ("threshold-equality", "threshold-basis", "below-threshold-exception")
_SCOPE = "JSON_VALUE(p.SourceMetadataJson, '$.demoFixture.fixtureId') = ?"
_QUERIES = {
    "packages": f"SELECT p.* FROM prep.InvoicePackage p WHERE {_SCOPE} FOR JSON PATH, INCLUDE_NULL_VALUES",
    "invoices": f"""SELECT i.* FROM prep.Invoice i
        JOIN prep.InvoicePackage p ON p.InvoiceId=i.InvoiceID WHERE {_SCOPE} FOR JSON PATH, INCLUDE_NULL_VALUES""",
    "lines": f"""SELECT l.* FROM prep.InvoiceLine l
        JOIN prep.InvoicePackage p ON p.PackageId=l.PackageId WHERE {_SCOPE} FOR JSON PATH, INCLUDE_NULL_VALUES""",
    "projects": f"""SELECT DISTINCT x.* FROM prep.Project x
        JOIN prep.InvoicePackage p ON x.AFEProjectID=JSON_VALUE(p.SourceMetadataJson,'$.demoFixture.projectId')
        WHERE {_SCOPE} FOR JSON PATH, INCLUDE_NULL_VALUES""",
    "sites": f"""SELECT DISTINCT x.* FROM prep.Site x
        JOIN prep.InvoicePackage p ON x.SiteID=JSON_VALUE(p.SourceMetadataJson,'$.demoFixture.siteId')
        WHERE {_SCOPE} FOR JSON PATH, INCLUDE_NULL_VALUES""",
    "orders": f"""SELECT DISTINCT x.* FROM prep.PurchaseOrder x
        JOIN prep.InvoiceLine l ON x.PONumber=JSON_VALUE(l.SourceLineJson,'$.demoFixture.poNumber')
        JOIN prep.InvoicePackage p ON p.PackageId=l.PackageId WHERE {_SCOPE} FOR JSON PATH, INCLUDE_NULL_VALUES""",
    "po_lines": f"""SELECT DISTINCT x.* FROM prep.PurchaseOrderLine x
        JOIN prep.InvoiceLine l ON x.PONumber=JSON_VALUE(l.SourceLineJson,'$.demoFixture.poNumber')
            AND x.POLineNumber=TRY_CONVERT(int,JSON_VALUE(l.SourceLineJson,'$.demoFixture.poLineNumber'))
        JOIN prep.InvoicePackage p ON p.PackageId=l.PackageId WHERE {_SCOPE} FOR JSON PATH, INCLUDE_NULL_VALUES""",
    "parts": f"""SELECT DISTINCT x.* FROM ref.PartMaster x
        JOIN prep.InvoiceLine l ON x.VendorID=JSON_VALUE(l.SourceLineJson,'$.demoFixture.effectiveVendorId')
            AND x.PartNumber=JSON_VALUE(l.SourceLineJson,'$.demoFixture.effectivePartNumber')
        JOIN prep.InvoicePackage p ON p.PackageId=l.PackageId
        WHERE {_SCOPE} AND x.IsCurrent=1 FOR JSON PATH, INCLUDE_NULL_VALUES""",
    "thresholds": f"""SELECT DISTINCT x.* FROM ref.CapitalizationThreshold x
        JOIN prep.InvoicePackage p ON x.ThresholdID=TRY_CONVERT(int,
            JSON_VALUE(p.SourceMetadataJson,'$.demoFixture.thresholdId'))
        WHERE {_SCOPE} FOR JSON PATH, INCLUDE_NULL_VALUES""",
    "never": f"""SELECT DISTINCT x.* FROM ref.NeverCapitalizeException x
        JOIN prep.InvoicePackage p ON x.PolicyReference=JSON_VALUE(p.SourceMetadataJson,'$.demoFixture.provenance')
        WHERE {_SCOPE} AND x.Active=1 FOR JSON PATH, INCLUDE_NULL_VALUES""",
    "repair": f"""SELECT DISTINCT x.* FROM ref.RepairMaintenanceIndicator x
        JOIN prep.InvoicePackage p ON x.PolicyReference=JSON_VALUE(p.SourceMetadataJson,'$.demoFixture.provenance')
        WHERE {_SCOPE} FOR JSON PATH, INCLUDE_NULL_VALUES""",
    "tasks": "SELECT * FROM pub.v1_TaskMaster WHERE regionCode='US' FOR JSON PATH, INCLUDE_NULL_VALUES",
}


class SqlNatureRule(NormalizationModel):
    task_code: str
    nature_result: Literal["UNKNOWN", "CAPITALIZABLE"]
    policy_reference: str


class SqlDemoContext(NormalizationModel):
    fixture_id: str = Field(min_length=1)
    synthetic: Literal[True]
    business_approved: Literal[False]
    project_id: str
    site_id: str
    project_budget: float = Field(ge=0)
    project_budget_currency: str
    project_status: Literal["OPEN"]
    project_type: Literal["SYNTHETIC_DEMO"]
    bundling_in_scope: bool
    is_leased: bool
    header_po_number: str
    effective_vendor_id: str
    effective_invoice_date: date
    task_catalog_region: Literal["US"]
    regional_mapping_is_synthetic: bool
    threshold_type: str
    threshold_id: int = Field(ge=1)
    unit_cost_threshold: float = Field(ge=0)
    threshold_currency: str = Field(pattern=r"^[A-Z]{3}$")
    threshold_comparison: Literal["GREATER_THAN_OR_EQUAL"]
    threshold_basis: Literal["UNIT_COST"]
    below_threshold_exception_enabled: Literal[False]
    currency_treatment: Literal["NATIVE_CURRENCY_NO_FX"]
    grouping_key: Literal["TASK_CODE,SITE_ID,PROJECT_ID,CURRENCY"]
    minimum_bundle_members: int = Field(ge=2)
    historical_lookback_enabled: Literal[False]
    unknown_nature_behavior: Literal["REVIEW"]
    synthetic_nature_rules: list[SqlNatureRule]
    provenance: str = Field(min_length=1)


class SqlDemoLine(NormalizationModel):
    fixture_id: str
    synthetic: Literal[True]
    effective_vendor_id: str
    effective_invoice_date: date
    effective_part_number: str = Field(min_length=1)
    effective_quantity: float = Field(gt=0)
    effective_unit_cost: float = Field(ge=0)
    po_number: str
    po_line_number: int = Field(ge=1)
    po_task_code: str
    project_id: str
    site_id: str
    task_catalog_region: Literal["US"]
    task_master_source_sha256: str = Field(pattern=r"^[A-Fa-f0-9]{64}$")
    part_mapping_business_confirmed: Literal[False]
    assumption: str = Field(min_length=1)


def _text(row: Row, key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must contain nonempty text")
    return value


def _object(value: object) -> dict[str, object]:
    result = json.loads(value) if isinstance(value, str) else value
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


def _index(rows: Sequence[Row], *fields: str) -> dict[tuple[str, ...], Row]:
    result: dict[tuple[str, ...], Row] = {}
    for row in rows:
        if any(row.get(field) is None for field in fields):
            raise ValueError(f"Missing index fields: {fields}")
        key = tuple(str(row[field]) for field in fields)
        if key in result:
            raise ValueError(f"Duplicate SQL reference key: {key}")
        result[key] = row
    return result


def _same(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise ValueError(f"{label} differs from the explicit demo input")


def _preserved(actual: object, effective: object, label: str) -> None:
    if actual is not None:
        _same(actual, effective, label)


def _task_master(rows: Sequence[Row]) -> TaskMasterSnapshot:
    if not rows:
        raise ValueError("Active SQL TaskMaster is empty")
    rows = sorted(rows, key=lambda row: _text(row, "taskCode"))
    first = rows[0]
    for row in rows:
        for field in ("regionCode", "sourceFile", "sourceSheet", "sourceVersion", "sourceSha256"):
            _same(row[field], first[field], f"TaskMaster.{field}")
        _same(row["active"], True, "TaskMaster.active")
        _same(row["regionCode"], "US", "TaskMaster.regionCode")
    tasks = []
    for row in rows:
        payload = {
            field.alias or name: row.get(field.alias or name)
            for name, field in TaskReferenceEntry.model_fields.items()
            if name != "search_text"
        }
        payload["searchText"] = " ".join(
            _text(row, key).lower() for key in
            ("taskCode", "description", "assetCategoryMajor", "assetCategoryMinor", "propertyType")
        )
        tasks.append(TaskReferenceEntry.model_validate(payload))
    return TaskMasterSnapshot.model_validate(dict(
        snapshotId=f"sql-us-{_text(first, 'sourceVersion')}",
        source=dict(fileName=first["sourceFile"], sheetName=first["sourceSheet"],
                    sourceVersion=first["sourceVersion"], sha256=_text(first, "sourceSha256").upper()),
        totalSourceRows=len(rows), activeTaskCount=len(tasks), tasks=tasks,
    ))


def _policy(context: SqlDemoContext, region: str) -> DemoPolicySnapshot:
    payload = build_demo_policy_snapshot().model_dump(mode="json", by_alias=True)
    payload.update(policyId=context.fixture_id, region=region)
    payload["capitalization"].update(
        currency=context.threshold_currency, baseThreshold=context.unit_cost_threshold,
    )
    payload["bundling"]["minimumCandidateMembers"] = context.minimum_bundle_members
    for setting in payload["settings"]:
        setting.update(status="PROPOSED_DEMO_ASSUMPTION", evidence=context.provenance)
        if setting["settingId"] == "base-threshold":
            setting["value"] = dict(amount=context.unit_cost_threshold, currency=context.threshold_currency)
    payload["evidenceDocuments"] = [context.provenance]
    return DemoPolicySnapshot.model_validate(payload)


@dataclass(frozen=True)
class SqlDemoInputs:
    fixture_id: str
    input_sha256: str
    sources: list[NormalizedInvoicePackage]
    enriched: list[NormalizedInvoicePackage]
    task_master: TaskMasterSnapshot
    policies: dict[str, DemoPolicySnapshot]
    rule_set: CapitalizationRuleSet
    matches: dict[tuple[str, str], tuple[Optional[str], list[str]]]
    sql_snapshot: dict[str, list[dict[str, object]]]

    def request(self, workflow_run_id: str, *, persist: bool, now: datetime) -> DemoWorkflowRequest:
        return DemoWorkflowRequest(
            workflow_run_id=workflow_run_id, requested_at_utc=now,
            packages=[InvoicePackageReference(
                package_id=p.package_id, run_id=p.run_id,
                package_uri=f"sql://{self.fixture_id}/{p.package_id}",
                expected_invoice_id=p.invoice_id, expected_source_sha256=p.source.sha256,
            ) for p in self.sources],
            model_deployment="REFERENCE_ONLY", task_prompt_version="sql-demo-reference-v1",
            capitalization_policy_version=self.fixture_id, bundling_policy_version=self.fixture_id,
            allow_synthetic_inputs=True, persist_results=persist,
        )

    def resolve(self, package: NormalizedInvoicePackage, request: DemoWorkflowRequest) -> list[TaskDetermination]:
        catalog = {t.task_code: t for t in self.task_master.tasks}
        digest = canonical_sha256(self.task_master.model_dump(mode="json", by_alias=True))
        results = []
        for line in package.lines:
            code, evidence = self.matches[(package.package_id, line.line_id)]
            task = catalog.get(code or "")
            resolved = task is not None
            results.append(TaskDetermination(
                workflow_run_id=request.workflow_run_id, package_id=package.package_id,
                invoice_id=package.invoice_id, line_id=line.line_id,
                resolution_status=TaskResolutionStatus.RESOLVED if resolved else TaskResolutionStatus.REVIEW,
                suggested_task_code=code if resolved else None,
                suggested_asset_class=task.asset_category_major if task else None,
                item_identified=line.description,
                match_method=TaskMatchMethod.EXACT_PART if resolved else TaskMatchMethod.REVIEW,
                confidence=1.0 if resolved else 0.0,
                unresolved_reason=None if resolved else "PO and PartMaster do not agree on an active task.",
                rationale=(
                    "Exact PO/PartMaster agreement using unapproved synthetic reference inputs; "
                    "US catalog proxies are not governed European mappings. No LLM was used."
                    if resolved else "Conflicting or inactive synthetic reference mapping requires review."
                ),
                evidence_refs=evidence + [
                    f"synthetic-fixture:{self.fixture_id}", f"sql-input-sha256:{self.input_sha256}",
                ],
                task_master_version=self.task_master.source.source_version,
                task_master_sha256=digest, model_deployment=None,
                prompt_version=request.task_prompt_version, created_at_utc=request.requested_at_utc,
            ))
        return results

    def manifest(self) -> dict[str, object]:
        return {
            "schemaVersion": "1.0-sql-demo-inputs", "fixtureId": self.fixture_id,
            "inputSha256": self.input_sha256, "synthetic": True,
            "sqlSnapshot": self.sql_snapshot,
            "invoiceCount": len(self.sources), "lineCount": sum(len(p.lines) for p in self.sources),
            "sourcePackages": [p.model_dump(mode="json", by_alias=True) for p in self.sources],
            "processingPackages": [p.model_dump(mode="json", by_alias=True) for p in self.enriched],
            "taskMaster": self.task_master.model_dump(mode="json", by_alias=True),
            "policies": {c: p.model_dump(mode="json", by_alias=True) for c, p in self.policies.items()},
            "ruleSet": self.rule_set.model_dump(mode="json", by_alias=True),
            "referenceMatches": [
                dict(packageId=p, lineId=l, taskCode=code, evidenceRefs=evidence)
                for (p, l), (code, evidence) in sorted(self.matches.items())
            ],
        }


def build_sql_demo_inputs(
    tables: Mapping[str, Sequence[Row]], *, fixture_id: str,
    expected_invoices: int, expected_lines: int,
) -> SqlDemoInputs:
    """Validate a read-only SQL snapshot before generating any decisions."""
    if not fixture_id.strip() or expected_invoices < 1 or expected_lines < 1:
        raise ValueError("An explicit fixture and positive coverage expectations are required")
    packages = _index(tables["packages"], "PackageId")
    invoices = _index(tables["invoices"], "InvoiceID")
    _same(len(packages), expected_invoices, "Package count")
    _same(len(invoices), expected_invoices, "Invoice count")
    _same(len(tables["lines"]), expected_lines, "Line count")
    projects = _index(tables["projects"], "AFEProjectID")
    sites = _index(tables["sites"], "SiteID")
    orders = _index(tables["orders"], "PONumber")
    po_lines = _index(tables["po_lines"], "PONumber", "POLineNumber")
    parts = _index(tables["parts"], "VendorID", "PartNumber")
    thresholds = _index(tables["thresholds"], "ThresholdID")
    master = _task_master(tables["tasks"])
    task_codes = {t.task_code for t in master.tasks}
    lines_by_package: dict[str, list[Row]] = defaultdict(list)
    _index(tables["lines"], "PackageId", "InvoiceLineId")
    for line in tables["lines"]:
        key = _text(line, "PackageId")
        if (key,) not in packages:
            raise ValueError("A source line references an unrequested package")
        lines_by_package[key].append(line)
    sources, enriched = [], []
    matches: dict[tuple[str, str], tuple[Optional[str], list[str]]] = {}
    policies: dict[str, DemoPolicySnapshot] = {}
    nature_rules: dict[str, CapitalizationNatureRule] = {}
    invoice_ids: set[str] = set()
    for (package_id,), row in sorted(packages.items()):
        context = SqlDemoContext.model_validate(_object(row["SourceMetadataJson"])["demoFixture"])
        _same(context.fixture_id, fixture_id, "Fixture")
        _same(row["PackageStatus"], "PREPARING", "Legacy package status")
        invoice_id = _text(row, "InvoiceId")
        if invoice_id in invoice_ids:
            raise ValueError("Multiple packages reference the same invoice")
        invoice_ids.add(invoice_id)
        invoice = invoices[(invoice_id,)]
        for package_field, header_field in (
            ("InvoiceNumber", "InvoiceNumber"), ("InvoiceDate", "InvoiceDate"),
            ("VendorId", "VendorID"), ("VendorName", "VendorName"),
            ("Currency", "InvoiceCurrency"), ("InvoiceTotal", "InvoiceTotal"),
            ("CountryRegion", "CountryRegion"), ("PoNumber", "PONumber"),
        ):
            _same(row[package_field], invoice[header_field], package_field)
        _preserved(invoice["VendorID"], context.effective_vendor_id, "Source vendor")
        _preserved(invoice["InvoiceDate"], context.effective_invoice_date.isoformat(), "Source invoice date")
        _preserved(invoice["PONumber"], context.header_po_number, "Confirmed header PO")
        currency = _text(row, "Currency")
        _same(context.threshold_currency, currency, "Threshold currency")
        _same(context.project_budget_currency, currency, "Budget currency")
        region = _text(row, "CountryRegion")
        if region not in {"North America", "Europe"}:
            raise ValueError(f"Unsupported fixture region: {region}")
        if region == "Europe" and not context.regional_mapping_is_synthetic:
            raise ValueError("European US-catalog proxy mappings must be labeled synthetic")
        project = projects[(context.project_id,)]
        site = sites[(context.site_id,)]
        for field, value in (
            ("ProjectStatus", context.project_status), ("ProjectType", context.project_type),
            ("ProjectTotalBudget", context.project_budget), ("BundlingInScope", context.bundling_in_scope),
            ("CountryRegion", region), ("ProjectDescription", context.provenance),
        ):
            _same(project[field], value, f"Project.{field}")
        _same(site["CountryRegion"], region, "Site country/region")
        _same(site["IsLeased"], context.is_leased, "Site lease flag")
        threshold = thresholds[(str(context.threshold_id),)]
        for field, value in (
            ("GovernedThreshold", context.unit_cost_threshold), ("Currency", currency),
            ("ThresholdType", context.threshold_type), ("CountryRegion", region),
            ("CapitalizationPolicyReference", context.provenance),
        ):
            _same(threshold[field], value, f"Threshold.{field}")
        effective = context.effective_invoice_date
        if effective < date.fromisoformat(_text(threshold, "EffectiveFrom")) or (
            threshold.get("EffectiveTo") is not None
            and effective > date.fromisoformat(_text(threshold, "EffectiveTo"))
        ):
            raise ValueError("Demo threshold is not effective on the processing invoice date")
        policy = _policy(context, region.upper().replace(" ", "_"))
        if currency in policies:
            _same(policies[currency], policy, "Currency policy")
        policies[currency] = policy
        for rule in context.synthetic_nature_rules:
            if rule.nature_result == "UNKNOWN":
                continue
            new_rule = CapitalizationNatureRule(
                rule_id=f"{fixture_id}:{rule.task_code}", version=fixture_id,
                nature_result=NatureResult.CAPITALIZABLE, task_codes=[rule.task_code],
                policy_citation=rule.policy_reference, assumption_refs=[fixture_id],
            )
            if rule.task_code in nature_rules:
                _same(nature_rules[rule.task_code], new_rule, "Nature rule")
            nature_rules[rule.task_code] = new_rule
        raw_lines, processing_lines = [], []
        package_lines = sorted(lines_by_package[package_id], key=lambda l: int(str(l["LineNumber"])))
        if not package_lines:
            raise ValueError("An invoice package has no source lines")
        for raw in package_lines:
            _same(raw["InvoiceID"], invoice_id, "Line invoice ID")
            original = _object(raw["SourceLineJson"])
            overlay = SqlDemoLine.model_validate(original["demoFixture"])
            for actual, expected, label in (
                (overlay.fixture_id, fixture_id, "Line fixture"),
                (overlay.project_id, context.project_id, "Line project"),
                (overlay.site_id, context.site_id, "Line site"),
                (overlay.effective_vendor_id, context.effective_vendor_id, "Line vendor"),
                (overlay.effective_invoice_date, effective, "Line date"),
                (overlay.task_master_source_sha256.upper(), master.source.sha256, "Catalog source hash"),
            ):
                _same(actual, expected, label)
            for field, value in (
                ("Quantity", overlay.effective_quantity), ("UnitCost", overlay.effective_unit_cost),
                ("PartNumber", overlay.effective_part_number),
            ):
                _preserved(raw[field], value, f"Source {field}")
            if abs(Decimal(str(overlay.effective_quantity)) * Decimal(str(overlay.effective_unit_cost))
                   - Decimal(str(raw["LineAmount"]))) > Decimal("0.0001"):
                raise ValueError("Effective quantity/unit cost do not reconcile to the source line amount")
            po = po_lines[(overlay.po_number, str(overlay.po_line_number))]
            order = orders[(overlay.po_number,)]
            part = parts[(overlay.effective_vendor_id, overlay.effective_part_number)]
            for field, value in (
                ("AFEProjectID", context.project_id), ("SiteID", context.site_id),
                ("VendorID", overlay.effective_vendor_id), ("POTaskCode", overlay.po_task_code),
                ("PODescription", context.provenance),
            ):
                _same(po[field], value, f"PO line.{field}")
            _same(order["VendorID"], overlay.effective_vendor_id, "PO vendor")
            _same(order["AFEProjectID"], context.project_id, "PO project")
            _same(part["Source"], context.provenance, "Part provenance")
            _same(part["ConflictStatus"], "SYNTHETIC_UNCONFIRMED", "Part status")
            _same(part.get("ConfirmedBy"), None, "Part business confirmation")
            _same(part.get("ConfirmedAtUtc"), None, "Part business confirmation date")
            _same(part["IsCurrent"], True, "Current part")
            code = overlay.po_task_code
            agreement = code in task_codes and part["MappedTaskCode"] == code
            line_id = _text(raw, "InvoiceLineId")
            matches[(package_id, line_id)] = (
                code if agreement else None,
                [f"sql-po:{overlay.po_number}:{overlay.po_line_number}",
                 f"sql-part:{part['PartMasterID']}", f"task:{code}"],
            )
            account = original.get("Account Number")
            source_task = account.rsplit(".", 1)[-1] if isinstance(account, str) else None
            source_line = NormalizedInvoiceLine.model_validate(dict(
                lineId=line_id, lineNumber=raw["LineNumber"], description=raw["Description"],
                partNumber=raw["PartNumber"], quantity=raw["Quantity"], unitCost=raw["UnitCost"],
                lineAmount=raw["LineAmount"], currency=currency,
                sourceTransactionType=original.get("Do Ty"), sourceDocumentNumber=original.get("Doc Number"),
                sourceInvoiceNumber=original.get("Invoice Number"), sourceAccount=account, sourceTaskCode=source_task,
            ))
            raw_lines.append(source_line)
            processing_lines.append(source_line.model_copy(update={
                "quantity": overlay.effective_quantity, "unit_cost": overlay.effective_unit_cost,
                "part_number": overlay.effective_part_number,
            }))
        missing_q = [l.line_id for l in raw_lines if l.quantity is None]
        missing_c = [l.line_id for l in raw_lines if l.unit_cost is None]
        reasons = ["Missing source facts retained; synthetic replacements require explicit opt-in."] if (
            missing_q or missing_c or invoice["VendorID"] is None or invoice["InvoiceDate"] is None
        ) else []
        source = NormalizedInvoicePackage.model_validate(dict(
            packageId=package_id, runId=row["RunId"], invoiceId=invoice_id,
            invoiceNumber=invoice["InvoiceNumber"], invoiceDate=invoice["InvoiceDate"],
            vendorId=invoice["VendorID"], vendorName=invoice["VendorName"],
            invoiceTotal=invoice["InvoiceTotal"], currency=currency,
            countryRegion=region, poNumber=invoice["PONumber"],
            normalizationStatus="REVIEW_REQUIRED" if reasons else "NORMALIZED", lines=raw_lines,
            dataQuality=dict(missingQuantityLineIds=missing_q, missingUnitCostLineIds=missing_c,
                             sourceCurrencies=[currency], reviewReasons=reasons,
                             warnings=["Source was manually staged in SQL, not freshly ingested from Blob."]),
            source=dict(fileName=row["SourcePackageName"], sha256=row["SourceSha256"], sourceSystem=row["SourceSystem"]),
            createdAtUtc=row["CreatedAtUtc"],
        ))
        sources.append(source)
        enriched.append(NormalizedInvoicePackage.model_validate({
            **source.model_dump(mode="json", by_alias=True),
            "vendorId": context.effective_vendor_id, "invoiceDate": effective.isoformat(),
            "poNumber": context.header_po_number, "normalizationStatus": "NORMALIZED",
            "lines": [l.model_dump(mode="json", by_alias=True) for l in processing_lines],
            "contextOverlay": DemoContextOverlay(
                overlay_version=fixture_id, target_invoice_id=invoice_id,
                project_id=context.project_id, site_id=context.site_id,
                project_status=context.project_status, bundling_in_scope=context.bundling_in_scope,
            ).model_dump(mode="json", by_alias=True),
            "dataQuality": {
                **source.data_quality.model_dump(mode="json", by_alias=True), "reviewReasons": [],
                "warnings": source.data_quality.warnings + [context.provenance],
            },
        }))
    for kind in ("never", "repair"):
        for row in tables[kind]:
            keyword = _text(row, "Keyword")
            descriptions = sorted({
                l.description for p in sources for l in p.lines
                if keyword.casefold() in l.description.casefold()
            })
            if descriptions:
                rule_id = f"{fixture_id}:{kind}:{keyword}"
                nature_rules[rule_id] = CapitalizationNatureRule(
                    rule_id=rule_id, version=fixture_id, nature_result=NatureResult.OPEX_BY_NATURE,
                    exact_descriptions=descriptions, policy_citation=_text(row, "PolicyReference"),
                    assumption_refs=[fixture_id], never_capitalize=kind == "never",
                    repair_maintenance_indicator=kind == "repair",
                )
    _same(invoice_ids, {key[0] for key in invoices}, "Invoice coverage")
    return SqlDemoInputs(
        fixture_id, canonical_sha256({
            name: sorted(rows, key=canonical_sha256) for name, rows in tables.items()
        }), sources, enriched, master, policies,
        CapitalizationRuleSet(rule_set_id=fixture_id, approved=False, synthetic=True,
                              assumption_refs=[fixture_id], rules=list(nature_rules.values())),
        matches, json.loads(json.dumps(tables, allow_nan=False)),
    )


def load_sql_demo_inputs(
    connection: DatabaseConnection, *, fixture_id: str,
    expected_invoices: int, expected_lines: int,
) -> SqlDemoInputs:
    """Read one consistent SQL snapshot; the caller owns transaction cleanup."""
    cursor = connection.cursor()
    try:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;")
        tables = {}
        for name, query in _QUERIES.items():
            cursor.execute(query, () if name == "tasks" else (fixture_id,))
            payload = "".join(str(row[0]) for row in cursor.fetchall())
            rows = json.loads(payload)
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise ValueError(f"Invalid SQL JSON response for {name}")
            tables[name] = rows
        return build_sql_demo_inputs(
            tables, fixture_id=fixture_id,
            expected_invoices=expected_invoices, expected_lines=expected_lines,
        )
    finally:
        cursor.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-id", required=True)
    parser.add_argument("--allow-synthetic-inputs", action="store_true", required=True)
    parser.add_argument("--expected-invoices", type=int, required=True)
    parser.add_argument("--expected-lines", type=int, required=True)
    parser.add_argument("--snapshot-uri", required=True)
    parser.add_argument("--workflow-run-id")
    parser.add_argument("--output-uri")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--persist-results", action="store_true")
    parser.add_argument("--expected-input-sha256")
    args = parser.parse_args(argv)
    if args.execute and (not args.workflow_run_id or not args.output_uri or not args.expected_input_sha256):
        parser.error("--execute requires --workflow-run-id, --output-uri and --expected-input-sha256")
    if args.persist_results and not args.execute:
        parser.error("--persist-results requires --execute")
    if args.output_uri == args.snapshot_uri:
        parser.error("Snapshot and result artifacts must have distinct URIs")
    if args.expected_invoices < 1 or args.expected_lines < 1:
        parser.error("Expected invoice and line counts must be positive")
    def connect() -> DatabaseConnection:
        return connect_azure_sql(
            server_fqdn=os.environ["CAPEX_SQL_SERVER"], database_name=os.environ["CAPEX_SQL_DATABASE"],
            managed_identity_client_id=os.getenv("AZURE_CLIENT_ID"),
        )
    connection = connect()
    try:
        inputs = load_sql_demo_inputs(
            connection, fixture_id=args.fixture_id,
            expected_invoices=args.expected_invoices, expected_lines=args.expected_lines,
        )
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()
    if args.expected_input_sha256 and args.expected_input_sha256 != inputs.input_sha256:
        raise ValueError("SQL inputs changed after preflight; review a fresh snapshot before execution")
    _write_json(args.snapshot_uri, inputs.manifest(), exclusive=True)
    print(json.dumps(dict(stage="SQL_INPUTS_VALIDATED", invoices=len(inputs.sources),
                          lines=len(inputs.matches), inputSha256=inputs.input_sha256,
                          synthetic=True, legacyReadyEventsPublished=0, llmCalls=0)))
    if not args.execute:
        return 0
    now = datetime.now(timezone.utc)
    request = inputs.request(args.workflow_run_id, persist=args.persist_results, now=now)
    connection = connect() if args.persist_results else None
    try:
        if connection is not None:
            cursor = connection.cursor()
            try:
                cursor.execute("""SELECT 1 FROM capex.AssessmentRun WHERE WorkflowRunId=?
                    UNION ALL SELECT 1 FROM task.TaskSuggestion WHERE correlationId=?""",
                    (request.workflow_run_id, request.workflow_run_id))
                if cursor.fetchone() is not None:
                    raise ValueError("Workflow ID already has evidence; choose a fresh workflow-run-id")
            finally:
                cursor.close()
                connection.rollback()
        result = run_demo_v2_workflow(
            request=request, packages=inputs.sources, assessment_packages=inputs.enriched,
            task_master=inputs.task_master, policy=next(iter(inputs.policies.values())),
            currency_policies=inputs.policies, task_resolver=inputs.resolve,
            rule_set=inputs.rule_set, synthetic_setting_ids=_SETTINGS, connection=connection,
        )
        _write_result(args.output_uri, result)
        print(result.model_dump_json(by_alias=True))
        return {"SUCCEEDED": 0, "PARTIAL": 2, "FAILED": 1}[result.status]
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
