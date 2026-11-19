"""Render and verify the CapEx physical model from a read-only SQL catalog export."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from pathlib import Path
from typing import Any


SCHEMAS = {"prep", "task", "capex", "ref", "review", "ops", "pub"}


def validate_snapshot(snapshot: dict[str, Any]) -> list[str]:
    if snapshot.get("snapshotVersion") != 1:
        raise ValueError("Unsupported or missing schema snapshotVersion.")
    for field in ("serverName", "databaseName", "capturedAtUtc"):
        if not isinstance(snapshot.get(field), str) or not snapshot[field]:
            raise ValueError(f"Missing schema export field: {field}.")
    for field in ("schemas", "tables", "views"):
        if not isinstance(snapshot.get(field), list):
            raise ValueError(f"Schema export must contain a {field} array.")
    if not snapshot["tables"]:
        raise ValueError("Empty catalog export; cannot establish a physical model.")
    warnings = []
    if snapshot.get("databaseViewDefinition") != 1:
        visible = {item["name"] for item in snapshot["schemas"]
                   if item.get("viewDefinition") == 1}
        missing = SCHEMAS - visible
        if missing:
            warnings.append(
                "Full metadata visibility is not established for: "
                + ", ".join(sorted(missing))
                + ". An owner with VIEW DEFINITION must repeat the export."
            )
    names: set[str] = set()
    for table in snapshot["tables"]:
        name = f'{table["schema"]}.{table["name"]}'
        if table["schema"] not in SCHEMAS or name in names:
            raise ValueError(f"Unexpected or duplicate table: {name}.")
        names.add(name)
        if not table["columns"]:
            raise ValueError(f"No visible columns: {name}.")
        column_names = {column["name"] for column in table["columns"]}
        if len(column_names) != len(table["columns"]):
            raise ValueError(f"Duplicate columns: {name}.")
        for column in table["columns"]:
            sql_type(column)
            if column["computed"] and column["computedDefinition"] is None:
                warnings.append(f'{name}.{column["name"]}: computed expression is hidden.')
            if column["defaultName"] and column["defaultDefinition"] is None:
                warnings.append(f'{name}.{column["name"]}: default expression is hidden.')
        for index in table["indexes"] or []:
            if index["hasFilter"] and index["filterDefinition"] is None:
                warnings.append(f'{name}.{index["name"]}: index filter is hidden.')
            for column in index["columns"]:
                if column["name"] not in column_names:
                    raise ValueError(f'Unknown index column in {name}.{index["name"]}.')
        for foreign_key in table["foreignKeys"] or []:
            for pair in foreign_key["columns"]:
                if pair["sourceColumn"] not in column_names:
                    raise ValueError(f'Unknown FK column in {name}.{foreign_key["name"]}.')
        for check in table["checks"] or []:
            if check["definition"] is None:
                warnings.append(f'{name}.{check["name"]}: check expression is hidden.')
    for table in snapshot["tables"]:
        for foreign_key in table["foreignKeys"] or []:
            target = f'{foreign_key["targetSchema"]}.{foreign_key["targetTable"]}'
            if foreign_key["targetSchema"] in SCHEMAS and target not in names:
                raise ValueError(f"Foreign key target is missing from export: {target}.")
            if target in names:
                target_table = next(item for item in snapshot["tables"]
                                    if f'{item["schema"]}.{item["name"]}' == target)
                target_columns = {item["name"] for item in target_table["columns"]}
                if any(pair["targetColumn"] not in target_columns
                       for pair in foreign_key["columns"]):
                    raise ValueError(f"Foreign key target column is missing: {target}.")
    for view in snapshot["views"]:
        if view["definition"] is None:
            warnings.append(f'{view["schema"]}.{view["name"]}: view definition is hidden.')
    return warnings


def sql_type(column: dict[str, Any]) -> str:
    name = column["type"]
    if column["typeSchema"] != "sys":
        return f'[{column["typeSchema"]}].[{name}]'
    if name in {"nvarchar", "nchar", "varchar", "char", "varbinary", "binary"}:
        length = column["maxLengthBytes"]
        if length == -1:
            size = "max"
        else:
            if name in {"nvarchar", "nchar"}:
                if length % 2:
                    raise ValueError(f"Invalid Unicode byte length: {length}.")
                length //= 2
            size = str(length)
        return f"{name}({size})"
    if name in {"decimal", "numeric"}:
        return f'{name}({column["precision"]},{column["scale"]})'
    if name in {"datetime2", "datetimeoffset", "time"}:
        return f'{name}({column["scale"]})'
    if name == "float":
        return f'float({column["precision"]})'
    return name


def fingerprint(snapshot: dict[str, Any]) -> str:
    facts = {key: value for key, value in snapshot.items() if key != "capturedAtUtc"}
    return hashlib.sha256(
        json.dumps(facts, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _escaped(value: object) -> str:
    return html.escape(str(value), quote=True)


def _definition_list(items: list[dict[str, Any]]) -> str:
    if not items:
        return "<p>None.</p>"
    return "<pre>" + _escaped(json.dumps(items, indent=2, ensure_ascii=True)) + "</pre>"


def render(snapshot: dict[str, Any]) -> str:
    warnings = validate_snapshot(snapshot)
    tables = snapshot["tables"]
    columns = sum(len(table["columns"]) for table in tables)
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Circle K CapEx CIP - Verified Physical SQL Model</title>",
        """<style>
body{font:14px "Segoe UI",Arial,sans-serif;margin:0;background:#f4f6fa;color:#172b4d}
header{background:#005a9e;color:white;padding:22px}main{padding:20px}
a{color:#005a9e}header a{color:white}.warning{background:#fff0c2;padding:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,650px),1fr));gap:18px}
section{background:white;border:1px solid #becbda;border-radius:6px;padding:14px;min-width:0}
h2{margin-top:0;font-size:19px}table{border-collapse:collapse;width:100%}
th,td{text-align:left;vertical-align:top;border-bottom:1px solid #ddd;padding:6px;overflow-wrap:anywhere}
code{font-size:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}
.scroll{overflow:auto}.keys{background:#eef4fa;padding:10px}
details{margin-top:12px}li{margin:5px 0}.meta{font-size:12px}
@media print{.grid{display:block}section{break-inside:avoid;margin-bottom:12px}}
</style></head><body>""",
        "<header><h1>Circle K CapEx CIP - Physical SQL Model</h1>",
        f'<p>{_escaped(snapshot["serverName"])} / {_escaped(snapshot["databaseName"])}</p>',
        f'<p>Catalog capture: {_escaped(snapshot["capturedAtUtc"])} | '
        f'{len(tables)} tables | {columns} columns | {len(snapshot["views"])} views</p>',
        f'<p class="meta">Schema fingerprint: {fingerprint(snapshot)}</p></header><main>',
        "<p>This is the deployed physical structure, not a proposed business model or "
        "proof of source completeness, policy approval, runtime acceptance, or data quality. "
        "Scope: prep, task, capex, ref, review, ops and pub tables/views. "
        "Only declared foreign keys are shown; no relationships are inferred. "
        "Permissions, procedures, triggers and storage configuration are outside this diagram.</p>",
    ]
    if warnings:
        parts.append('<div class="warning"><strong>PARTIAL METADATA - NOT FULLY VERIFIED</strong><ul>')
        parts.extend(f"<li>{_escaped(warning)}</li>" for warning in warnings)
        parts.append("</ul></div>")
    else:
        parts.append("<p><strong>Complete metadata visibility established for the scoped schemas. "
                     "Use the export and --check command to verify future drift.</strong></p>")
    parts.append('<nav><ul>')
    for table in tables:
        name = f'{table["schema"]}.{table["name"]}'
        parts.append(f'<li><a href="#{_escaped(name)}">{_escaped(name)}</a></li>')
    parts.append('</ul></nav><div class="grid">')
    for table in tables:
        name = f'{table["schema"]}.{table["name"]}'
        pk = {column["name"] for index in table["indexes"] or [] if index["primaryKey"]
              for column in index["columns"] if column["keyOrdinal"] > 0}
        fk = {pair["sourceColumn"] for key in table["foreignKeys"] or [] for pair in key["columns"]}
        parts.append(f'<section id="{_escaped(name)}"><h2>{_escaped(name)}</h2>')
        parts.append(f'<p>Temporal: {_escaped(table["temporalType"])}. '
                     f'{len(table["columns"])} columns.</p>')
        parts.append('<div class="scroll"><table><thead><tr><th>Column</th><th>SQL type</th>'
                     '<th>Nullability</th><th>Properties</th></tr></thead><tbody>')
        for column in table["columns"]:
            badges = [badge for badge, present in
                      (("PK", column["name"] in pk), ("FK", column["name"] in fk))
                      if present]
            properties = []
            if column["identity"]:
                properties.append(f'IDENTITY({column["identitySeed"]},{column["identityIncrement"]})')
            if column["computed"]:
                expression = column["computedDefinition"]
                properties.append("AS " + (expression if expression is not None else "[HIDDEN]"))
                if column["persisted"]:
                    properties.append("PERSISTED")
            if column["defaultName"]:
                properties.append(f'DEFAULT {column["defaultDefinition"]}')
            for flag in ("rowGuid", "sparse", "hidden"):
                if column[flag]:
                    properties.append(flag)
            if column["generatedAlways"] != "NOT_APPLICABLE":
                properties.append(column["generatedAlways"])
            parts.append(
                f'<tr><td><code>{_escaped(column["name"])}</code> {" ".join(badges)}</td>'
                f'<td><code>{_escaped(sql_type(column))}</code></td>'
                f'<td>{"NULL" if column["nullable"] else "NOT NULL"}</td>'
                f'<td><code>{_escaped("; ".join(properties))}</code></td></tr>'
            )
        parts.append('</tbody></table></div><div class="keys"><strong>Declared relationships</strong>')
        if not table["foreignKeys"]:
            parts.append("<p>No declared foreign keys.</p>")
        for key in table["foreignKeys"] or []:
            target = f'{key["targetSchema"]}.{key["targetTable"]}'
            source_columns = ", ".join(pair["sourceColumn"] for pair in key["columns"])
            target_columns = ", ".join(pair["targetColumn"] for pair in key["columns"])
            parts.append(
                f'<p>{_escaped(key["name"])}: ({_escaped(source_columns)}) &rarr; '
                f'<a href="#{_escaped(target)}">{_escaped(target)}</a>'
                f' ({_escaped(target_columns)}); DELETE {_escaped(key["onDelete"])}; '
                f'UPDATE {_escaped(key["onUpdate"])}; disabled={key["disabled"]}; '
                f'not trusted={key["notTrusted"]}</p>'
            )
        parts.append("</div><details><summary>Exact column definitions, keys, indexes and checks</summary>")
        parts.append(_definition_list([table]))
        parts.append("</details></section>")
    parts.append("</div><h2>Published and supporting views</h2>")
    for view in snapshot["views"]:
        name = f'{view["schema"]}.{view["name"]}'
        parts.append(f'<section id="{_escaped(name)}"><h2>{_escaped(name)}</h2>')
        parts.append(_definition_list([view]) + "</section>")
    embedded = json.dumps(snapshot, ensure_ascii=True, sort_keys=True).replace("<", "\\u003c")
    parts.append(f'<script id="physical-schema" type="application/json">{embedded}</script>')
    parts.append("</main></body></html>\n")
    return "\n".join(parts)


def verify(snapshot: dict[str, Any], document: str) -> None:
    warnings = validate_snapshot(snapshot)
    if warnings:
        raise ValueError("Cannot certify full schema parity: " + " ".join(warnings))
    match = re.search(
        r'<script id="physical-schema" type="application/json">(.*?)</script>',
        document, re.DOTALL,
    )
    if match is None:
        raise ValueError("Diagram has no machine-readable physical schema.")
    stored = json.loads(match.group(1))
    if fingerprint(snapshot) != fingerprint(stored):
        raise ValueError("SCHEMA_DRIFT: live export differs from the diagram.")
    if document != render(stored):
        raise ValueError("DIAGRAM_DRIFT: visible diagram differs from its embedded schema.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", type=Path)
    action.add_argument("--check", type=Path)
    parser.add_argument("--allow-partial", action="store_true",
                        help="Render an explicitly unverified diagram when metadata is restricted.")
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
    warnings = validate_snapshot(snapshot)
    if args.check:
        verify(snapshot, args.check.read_text(encoding="utf-8"))
        print("PHYSICAL_SCHEMA_PARITY_PASS " + fingerprint(snapshot))
    else:
        if warnings and not args.allow_partial:
            raise ValueError("Refusing to render an apparently complete model: " + " ".join(warnings))
        args.output.write_text(render(snapshot), encoding="utf-8")
        print(("PARTIAL_METADATA " if warnings else "PHYSICAL_MODEL_WRITTEN ") + str(args.output))


if __name__ == "__main__":
    main()
