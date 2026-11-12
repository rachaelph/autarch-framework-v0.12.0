"""Loopback-only analyst-review prototype; not an authenticated Power App."""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import os
import secrets
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Literal, Optional, Sequence
from urllib.parse import parse_qs, urlencode, urlparse

from pydantic import Field, model_validator

from .demo_v2_job import _read_bytes
from .demo_v2_normalization import NormalizationModel
from .demo_v2_reference import canonical_sha256
from .demo_v2_store import SqlSession
from .persistence import DatabaseConnection


class ReviewDecision(NormalizationModel):
    document_id: str = Field(min_length=1)
    expected_version: int = Field(ge=0)
    actor: str = Field(min_length=1, max_length=100)
    action: Literal["CONFIRM", "CORRECT", "DEFER"]
    reason: str = Field(min_length=1, max_length=2000)
    corrections: dict[str, str] = Field(default_factory=dict)
    simulated_popa_completed: bool = False

    @model_validator(mode="after")
    def consistent_action(self) -> "ReviewDecision":
        if self.action == "CONFIRM" and not self.simulated_popa_completed:
            raise ValueError("Confirmation requires explicit simulated manual POPA completion.")
        if self.action == "CORRECT" and not self.corrections:
            raise ValueError("A correction must identify at least one changed field.")
        if self.action != "CORRECT" and self.corrections:
            raise ValueError("Only CORRECT may contain corrections.")
        allowed = {"invoiceNumber", "invoiceDate", "vendorName", "poNumber", "analystTreatment", "analystTaskCode"}
        if not set(self.corrections) <= allowed or any(not value.strip() for value in self.corrections.values()):
            raise ValueError("Unsupported or empty correction; use the displayed correction fields.")
        return self


class ReviewConflictError(ValueError):
    """Immutable import or optimistic review version conflicts."""


class ReviewStore:
    """Review-owned imports and versioned decisions, never agent-writable tools."""

    imports_table = "imports"
    documents_table = "documents"
    decisions_table = "decisions"
    begin_statement = "BEGIN IMMEDIATE"
    write_lock = ""

    def __init__(self, path: Path):
        self.path = path
        connection = sqlite3.connect(path)
        self.connection: sqlite3.Connection | SqlSession = connection
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS imports (
                run_id TEXT PRIMARY KEY, sha TEXT NOT NULL, payload TEXT NOT NULL, imported_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS documents (
                run_id TEXT NOT NULL REFERENCES imports(run_id), document_id TEXT NOT NULL,
                sha TEXT NOT NULL, content BLOB NOT NULL, PRIMARY KEY(run_id,document_id));
            CREATE TABLE IF NOT EXISTS decisions (
                run_id TEXT NOT NULL REFERENCES imports(run_id), document_id TEXT NOT NULL,
                version INTEGER NOT NULL, payload TEXT NOT NULL, previous_sha TEXT,
                sha TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(run_id,document_id,version));
        """)

    def import_result(self, result: dict, *, read=_read_bytes) -> bool:
        adf = result.get("schemaVersion") == "1.0-capex-adf-review"
        if not adf and (result.get("schemaVersion") != "1.0-capex-mvp-result" \
                or result.get("status") not in {"SUCCEEDED", "REVIEW_REQUIRED"} \
                or result.get("synthetic") is not True or result.get("businessApproved") is not False \
                or result.get("sqlWrites") != 0 or result.get("popaWrites") != 0):
            raise ValueError("Only completed, explicitly unapproved MVP rehearsal results may be imported.")
        if adf:
            from .demo_v2_adf_review import AdfReviewEnvelope

            AdfReviewEnvelope.model_validate(result)
        run_id = result["workflowRunId"]
        digest = canonical_sha256(result)
        existing = self.connection.execute(
            f"SELECT sha FROM {self.imports_table} {self.write_lock} WHERE run_id=?", (run_id,),
        ).fetchone()
        if existing:
            if existing[0] != digest:
                raise ReviewConflictError("Run already imported with a different payload.")
            return False
        documents = []
        seen = set()
        for item in result["documents"]:
            doc = item["document"]
            if doc["documentId"] in seen:
                raise ValueError("Duplicate review document identity.")
            seen.add(doc["documentId"])
            data = read(doc["uri"])
            if adf:
                from .demo_v2_intake import _validate_archive

                if doc.get("archiveMember") is not None:
                    _validate_archive(data, doc["uri"])
                    if hashlib.sha256(data).hexdigest() != doc.get("archiveSha256"):
                        raise ValueError("Review archive bytes differ from the extracted source.")
                    with zipfile.ZipFile(io.BytesIO(data)) as archive:
                        if doc["archiveMember"] not in archive.namelist():
                            raise ValueError("Review PDF member is missing from its original archive.")
                        data = archive.read(doc["archiveMember"])
                media = doc.get("mediaType")
                if media in {"application/zip", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}:
                    _validate_archive(data, doc["uri"])
                elif media != "application/pdf" or not data.startswith(b"%PDF-"):
                    raise ValueError("Unsupported or mismatched ADF review source format.")
            if len(data) > 20 * 1024 * 1024 or (not adf and not data.startswith(b"%PDF-")) \
                    or hashlib.sha256(data).hexdigest() != doc["sha256"]:
                raise ValueError("Review import source PDF hash or size is invalid.")
            documents.append((run_id, doc["documentId"], doc["sha256"], data))
        if not documents:
            raise ValueError("Review imports require source documents.")
        with self.connection:
            self.connection.execute(self.begin_statement)
            existing = self.connection.execute(
                f"SELECT sha FROM {self.imports_table} {self.write_lock} WHERE run_id=?", (run_id,),
            ).fetchone()
            if existing:
                if existing[0] != digest:
                    raise ReviewConflictError("Run already imported with a different payload.")
                return False
            self.connection.execute(f"INSERT INTO {self.imports_table} VALUES (?,?,?,?)", (
                run_id, digest, json.dumps(result, allow_nan=False), datetime.now(timezone.utc).isoformat(),
            ))
            self.connection.executemany(f"INSERT INTO {self.documents_table} VALUES (?,?,?,?)", documents)
        return True

    def result(self, run_id: str) -> dict:
        row = self.connection.execute(f"SELECT payload FROM {self.imports_table} WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown imported workflow run.")
        return json.loads(row[0])

    def history(self, run_id: str, document_id: str) -> list[dict]:
        rows = self.connection.execute(f"""
            SELECT payload,created_at,sha,previous_sha FROM {self.decisions_table} {self.write_lock}
            WHERE run_id=? AND document_id=? ORDER BY version
        """, (run_id, document_id)).fetchall()
        return [{"decision": json.loads(row[0]), "createdAtUtc": row[1],
                 "sha256": row[2], "previousSha256": row[3]} for row in rows]

    def decide(self, run_id: str, decision: ReviewDecision) -> dict:
        result = self.result(run_id)
        document = next((item for item in result["documents"]
                         if item["document"]["documentId"] == decision.document_id), None)
        if document is None:
            raise ValueError("Decision refers to a document outside the imported run.")
        if decision.corrections.get("analystTreatment") not in {None, "CAPEX", "OPEX", "REVIEW"}:
            raise ValueError("Analyst treatment must be CAPEX, OPEX or REVIEW.")
        code = decision.corrections.get("analystTaskCode")
        if code is not None and code not in result["activeTaskCodes"]:
            raise ValueError("Analyst task correction is not in the supplied active TaskMaster.")
        if decision.action == "CONFIRM":
            if document["reviewReasons"]:
                raise ValueError("Extraction exceptions cannot be confirmed; correct or defer for reprocessing.")
            assessments = [a for p in result["partitions"] for a in p["result"]["assessments"]
                           if a["invoiceId"] == decision.document_id]
            if not assessments or any(a["recommendation"] == "REVIEW" for a in assessments):
                raise ValueError("Unresolved assessments must be corrected or deferred, not confirmed.")
        created = datetime.now(timezone.utc).isoformat()
        try:
            self.connection.execute(self.begin_statement)
            history = self.history(run_id, decision.document_id)
            if len(history) != decision.expected_version:
                raise ReviewConflictError("Stale review version; reload before submitting.")
            if decision.action == "CONFIRM" and any(
                item["decision"]["action"] == "CORRECT" for item in history
            ):
                raise ValueError("Corrected inputs need a new processing run before confirmation.")
            payload = decision.model_dump(mode="json", by_alias=True)
            previous = history[-1]["sha256"] if history else None
            digest = canonical_sha256({"runId": run_id, "decision": payload,
                                       "previousSha256": previous, "createdAtUtc": created})
            self.connection.execute(f"INSERT INTO {self.decisions_table} VALUES (?,?,?,?,?,?,?)", (
                run_id, decision.document_id, len(history) + 1,
                json.dumps(payload, allow_nan=False), previous, digest, created,
            ))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return {"version": len(history) + 1, "sha256": digest,
                "status": {"CONFIRM": "CONFIRMED_DEMO_ONLY", "CORRECT": "NEEDS_REPROCESSING",
                           "DEFER": "DEFERRED"}[decision.action]}

    def close(self) -> None:
        self.connection.close()

    def reopen(self) -> "ReviewStore":
        return ReviewStore(self.path)

    def document(self, run_id: str, document_id: str) -> Optional[bytes]:
        row = self.connection.execute(
            f"SELECT content FROM {self.documents_table} WHERE run_id=? AND document_id=?", (run_id, document_id),
        ).fetchone()
        return bytes(row[0]) if row else None


class SqlReviewStore(ReviewStore):
    """Uses migration 014; never creates schema or grants runtime permissions."""

    imports_table = "mvp_review.imports"
    documents_table = "mvp_review.documents"
    decisions_table = "mvp_review.decisions"
    begin_statement = "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"
    write_lock = "WITH (UPDLOCK, HOLDLOCK)"

    def __init__(
        self, connection: DatabaseConnection, *,
        connection_factory: Optional[Callable[[], DatabaseConnection]] = None,
    ):
        self.connection = SqlSession(connection)
        self.connection_factory = connection_factory

    def reopen(self) -> "SqlReviewStore":
        if self.connection_factory is None:
            raise ValueError("SQL review serving requires a separate connection factory per HTTP request.")
        return SqlReviewStore(self.connection_factory(), connection_factory=self.connection_factory)


def _escaped_json(value: object) -> str:
    return html.escape(json.dumps(value, indent=2, ensure_ascii=False))


def _summary_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    return "<table><thead><tr>" + "".join(f"<th>{html.escape(str(value))}</th>" for value in headers) \
        + "</tr></thead><tbody>" + "".join(
            "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in row) + "</tr>"
            for row in rows
        ) + "</tbody></table>"


def review_page(store: ReviewStore, run_id: str, csrf_token: str, message: str = "") -> str:
    result = store.result(run_id)
    cards = []
    for item in result["documents"]:
        doc_id = item["document"]["documentId"]
        history = store.history(run_id, doc_id)
        partitions = [p for p in result["partitions"]
                      if any(a["invoiceId"] == doc_id for a in p["result"]["assessments"])]
        recommendations = [{
            "siteId": p["siteId"], "projectId": p["projectId"],
            "taskSuggestions": [a for a in p["result"]["taskDeterminations"] if a["invoiceId"] == doc_id],
            "assessments": [a for a in p["result"]["assessments"] if a["invoiceId"] == doc_id],
            "bundleDecisions": [a for a in (p["evidence"].get("bundleDecisions") or {}).get("items", [])
                                if a["invoiceId"] == doc_id],
            "bundleGroups": p["result"]["bundleGroups"],
        } for p in partitions]
        tasks = [task for p in recommendations for task in p["taskSuggestions"]]
        task_by_line = {task["lineId"]: task for task in tasks}
        assessments = [a for p in recommendations for a in p["assessments"]]
        assessment_table = _summary_table(
            ["Line", "Task", "Nature", "Individual treatment", "Bundle disposition"],
            [[a["lineId"], task_by_line[a["lineId"]].get("suggestedTaskCode") or "REVIEW",
              a["natureResult"], a["recommendation"], a["bundlingDisposition"]] for a in assessments],
        )
        bundle_table = _summary_table(
            ["Proposed group", "Exact source sum", "Currency", "Members", "Authority"],
            [[group["bundleGroupId"], group["totalAmount"], group["currency"], len(group["members"]),
              "PROVISIONAL / HUMAN REVIEW"] for p in recommendations for group in p["bundleGroups"]
             if any(member["invoiceId"] == doc_id for member in group["members"])],
        )
        source_url = "/document?" + urlencode({"run": run_id, "id": doc_id})
        preview = f'<iframe title="Source invoice" src="{html.escape(source_url)}"></iframe>' \
            if item["document"].get("mediaType", "application/pdf") == "application/pdf" else \
            "<p>Workbook/archive preview is not available; download the original source above.</p>"
        cards.append(f"""
        <section><h2>{html.escape(doc_id)}</h2>
        <p>Source SHA-256: <code>{html.escape(item['document']['sha256'])}</code></p>
        <div class="columns"><div>
          <a href="{html.escape(source_url)}" target="_blank" rel="noopener">Open original invoice source</a>
          {preview}
          <h3>Extraction, confidence and page references</h3><pre>{_escaped_json(item['extraction'])}</pre>
        </div><div><h3>Code validation / exception reasons</h3><pre>{_escaped_json(item['reviewReasons'])}</pre>
          <h3>Task and accounting recommendations</h3>{assessment_table}
          <h3>Provisional bundle proposals (not final accounting)</h3>{bundle_table}
          <details><summary>Rationales and evidence references</summary><pre>{_escaped_json(recommendations)}</pre></details>
          <p>TaskMaster is the current demo asset/task reference catalog, not a physical asset inventory.
          No existing asset ID is inferred. Confidence is not accuracy.</p>
          <details><summary>Reference matches and deterministic routing (if enabled)</summary>
          <pre>{_escaped_json(item.get('referenceRouting', 'Not enabled for this saved run.'))}</pre></details>
          <form method="post" action="/decision">
            <input type="hidden" name="csrf" value="{html.escape(csrf_token)}">
            <input type="hidden" name="run" value="{html.escape(run_id)}">
            <input type="hidden" name="document_id" value="{html.escape(doc_id)}">
            <input type="hidden" name="expected_version" value="{len(history)}">
            <label>Demo analyst label (not authenticated identity)<input name="actor" required maxlength="100"></label>
            <label>Reason<textarea name="reason" required maxlength="2000"></textarea></label>
            <label>Correction field<select name="correction_field">
              <option value="">None</option><option>invoiceNumber</option><option>invoiceDate</option>
              <option>vendorName</option><option>poNumber</option><option>analystTreatment</option>
              <option>analystTaskCode</option></select></label>
            <label>Corrected value<input name="correction_value"></label>
            <label><input type="checkbox" name="simulated_popa_completed" value="yes">
              Simulate that manual POPA coding was completed (no actual POPA integration)</label>
            <button name="action" value="CONFIRM">Confirm recommendation (demo only)</button>
            <button name="action" value="CORRECT">Save correction / needs reprocessing</button>
            <button name="action" value="DEFER">Defer</button>
          </form>
          <h3>Versioned analyst audit</h3><pre>{_escaped_json(history)}</pre>
        </div></div></section>""")
    navigation = " | ".join(
        f'<a href="/?{html.escape(urlencode({"run": row[0]}))}">{html.escape(row[0])}</a>'
        for row in store.connection.execute(
            f"SELECT run_id FROM {store.imports_table} ORDER BY imported_at DESC",
        ).fetchall()
    )
    assessment_count = sum(len(p["result"]["assessments"]) for p in result["partitions"])
    bundle_count = sum(len(p["result"]["bundleGroups"]) for p in result["partitions"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>CapEx MVP local review prototype</title>
      <style>body{{font:16px system-ui;margin:24px;color:#17233a;background:#f4f6fa}}
      .banner{{background:#fff0bd;padding:18px;border:2px solid #925c00}}
      section{{background:white;padding:20px;margin-top:24px;border:1px solid #b5c3d6}}
      .columns{{display:grid;grid-template-columns:1fr 1fr;gap:24px}}
      pre{{white-space:pre-wrap;overflow-wrap:anywhere;max-height:420px;overflow:auto;background:#edf2f8;padding:12px}}
      iframe{{width:100%;height:520px;border:1px solid #b5c3d6}}
      label{{display:block;margin:10px 0}}input:not([type=checkbox]),textarea,select{{display:block;width:95%;padding:8px}}
      button{{padding:10px;margin:6px 6px 6px 0}}@media(max-width:900px){{.columns{{grid-template-columns:1fr}}}}
      table{{border-collapse:collapse;width:100%;margin-bottom:20px}}th,td{{border:1px solid #b5c3d6;padding:8px;text-align:left;overflow-wrap:anywhere}}
      </style></head><body>
      <h1>CapEx analyst review</h1><div class="banner"><strong>LOCAL DEMO PROTOTYPE</strong> -
      not a deployed Power App, not authenticated, no business approval or ERP posting.
      Unapproved demo policy assumptions; source/context provenance is shown below. Decisions use the selected review store;
      they are not used for future model inference. Corrections do not silently recompute recommendations.</div>
      <p>Run: <code>{html.escape(run_id)}</code> | Batch: {html.escape(result['status'])}</p>
      <nav aria-label="Imported rehearsal runs">{navigation}</nav>
      <p>{len(result['documents'])} source documents | {assessment_count} assessments | {bundle_count} provisional groups |
      {result['exceptionDocumentCount']} documents requiring review | {result['llmCallsAttempted']} recorded model attempts</p>
      <p role="status">{html.escape(message)}</p>
      <details><summary>Supplied policy snapshots (unapproved, not IFRS source text)</summary>
      <pre>{_escaped_json(result['policySnapshots'])}</pre></details>
      {''.join(cards)}</body></html>"""


def make_handler(store: ReviewStore, run_id: str, port: int):
    token = secrets.token_urlsafe(32)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{host}" for host in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.review_store = store.reopen()

        def finish(self) -> None:
            try:
                super().finish()
            finally:
                self.review_store.close()

        def respond(self, status: int, data: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; frame-src 'self'; "
                             "form-action 'self'; base-uri 'none'; frame-ancestors 'self'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.headers.get("Host") not in allowed_hosts:
                self.respond(403, b"Local access only.", "text/plain")
                return
            parsed = urlparse(self.path)
            if parsed.path == "/":
                selected = parse_qs(parsed.query).get("run", [run_id])[0]
                try:
                    self.respond(200, review_page(self.review_store, selected, token).encode(), "text/html; charset=utf-8")
                except ValueError as exc:
                    self.respond(404, str(exc).encode(), "text/plain")
            elif parsed.path == "/document":
                query = parse_qs(parsed.query)
                content = self.review_store.document(
                    query.get("run", [""])[0], query.get("id", [""])[0],
                )
                self.respond(
                    200 if content is not None else 404,
                    content if content is not None else b"Document not found.",
                    ("application/pdf" if content.startswith(b"%PDF-") else "application/octet-stream")
                    if content is not None else "text/plain",
                )
            else:
                self.respond(404, b"Not found.", "text/plain")

        def do_POST(self) -> None:
            if self.headers.get("Host") not in allowed_hosts or self.headers.get("Origin") not in allowed_origins:
                self.respond(403, b"Local origin required.", "text/plain")
                return
            selected = run_id
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384 or self.path != "/decision":
                    raise ValueError("Invalid request path or body length.")
                data = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
                def value(name: str) -> str:
                    entries = data.get(name, [""])
                    if len(entries) != 1:
                        raise ValueError("Duplicate form field.")
                    return entries[0]
                if not secrets.compare_digest(value("csrf"), token):
                    self.respond(403, b"Invalid review token or run.", "text/plain")
                    return
                selected = value("run")
                self.review_store.result(selected)
                field, corrected = value("correction_field"), value("correction_value").strip()
                if bool(field) != bool(corrected):
                    raise ValueError("Correction field and value must be supplied together.")
                decision = ReviewDecision.model_validate({
                    "documentId": value("document_id"), "expectedVersion": int(value("expected_version")),
                    "actor": value("actor").strip(), "action": value("action"), "reason": value("reason").strip(),
                    "corrections": {field: corrected} if field else {},
                    "simulatedPopaCompleted": value("simulated_popa_completed") == "yes",
                })
                outcome = self.review_store.decide(selected, decision)
                self.respond(200, review_page(self.review_store, selected, token, json.dumps(outcome)).encode(),
                             "text/html; charset=utf-8")
            except (ValueError, UnicodeError, sqlite3.Error) as exc:
                print(f"REVIEW_REJECTED: {type(exc).__name__}: {exc}", file=sys.stderr)
                self.respond(400, review_page(self.review_store, run_id, token, str(exc)).encode(), "text/html; charset=utf-8")

    return Handler


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", choices=("sqlite", "azure-sql"), default="sqlite")
    parser.add_argument(
        "--sql-auth", choices=("managed-identity", "azure-cli"), default="managed-identity",
        help="Azure SQL authentication; azure-cli explicitly uses the signed-in CLI account.",
    )
    parser.add_argument("--db", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="Application-owned immutable/idempotent assessment import")
    importer.add_argument("--result-uri", required=True)
    serve = commands.add_parser("serve", help="Local demonstration only; do not expose to a network")
    serve.add_argument("--workflow-run-id", required=True)
    serve.add_argument("--port", type=int, default=8765)
    export = commands.add_parser("export", help="Export imported recommendations and analyst decisions")
    export.add_argument("--workflow-run-id", required=True)
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.store == "sqlite" and args.db is None:
        parser.error("--db is required for the SQLite review store.")
    if args.store == "azure-sql" and args.db is not None:
        parser.error("--db cannot be used with the Azure SQL review store.")
    if args.sql_auth == "azure-cli" and args.store != "azure-sql":
        parser.error("--sql-auth azure-cli requires --store azure-sql.")
    def sql_connection() -> DatabaseConnection:
        from .sql_connection import connect_azure_sql

        return connect_azure_sql(
            server_fqdn=os.environ.get("CAPEX_SQL_SERVER", ""),
            database_name=os.environ.get("CAPEX_SQL_DATABASE", ""),
            managed_identity_client_id=(
                os.environ.get("AZURE_CLIENT_ID") if args.sql_auth == "managed-identity" else None
            ),
            authentication="azure-cli" if args.sql_auth == "azure-cli" else "managed-identity",
        )

    store: Optional[ReviewStore] = None
    try:
        if args.store == "azure-sql":
            store = SqlReviewStore(sql_connection(), connection_factory=sql_connection)
        else:
            assert args.db is not None
            store = ReviewStore(args.db)
        if args.command == "import":
            result = json.loads(_read_bytes(args.result_uri))
            inserted = store.import_result(result)
            print(json.dumps({"workflowRunId": result["workflowRunId"], "imported": inserted,
                              "replayedWithoutChanges": not inserted,
                              "store": str(args.db) if args.store == "sqlite" else "AZURE_SQL"}))
        elif args.command == "export":
            result = store.result(args.workflow_run_id)
            decisions = {item["document"]["documentId"]: store.history(
                args.workflow_run_id, item["document"]["documentId"],
            ) for item in result["documents"]}
            with args.output.open("x", encoding="utf-8") as handle:
                json.dump({"result": result, "analystAudit": decisions, "demoOnly": True}, handle, indent=2)
            print(f"REVIEW_AUDIT_EXPORTED: {args.output}")
        else:
            store.result(args.workflow_run_id)
            server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store, args.workflow_run_id, args.port))
            print(f"LOCAL_DEMO_ONLY: http://127.0.0.1:{args.port}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
        return 0
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        print(f"REVIEW_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
