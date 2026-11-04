# Invoice Operational Store

Azure SQL assets for prepared CapEx invoices.

- `migrations/` contains ordered, rerunnable schema migrations.
- `tests/` contains executable, rollback-based verification scripts.
- `prep` is the durable operational schema.
- `task` stores governed task-determination results.
- `capex` stores append-only assessment and bundle evidence.
- `pub` exposes versioned, read-only contracts to downstream components.

Apply migrations in filename order:

1. `001_create_invoice_operational_store.sql`
2. `002_create_published_invoice_package_view.sql`
3. `003_create_invoice_outbox.sql`
4. `004_create_task_suggestion_table.sql`
5. `005_create_task_master.sql`
6. `006_align_task_suggestion.sql`
7. `007_create_capex_assessment_store.sql`
8. `008_grant_capex_demo_v2_access.sql`
9. `009_complete_capex_model_tables.sql`
10. `010_preserve_source_invoice_gaps.sql`

## Load real invoice headers, packages, and lines first

The November 4 operator correction requests information from **both**
`JC2000A07465-Ariba.xlsx` and `JC2000A07814 - Ariba+Proj.tool.xlsx`.
These are different projects containing different invoices, not two copies
of one invoice. With the two ABBYY ZIPs, source extraction contains:

| Source | Invoice headers/packages | Lines |
|---|---:|---:|
| Bruegmann | 1 | 12 |
| Kimley-Horn | 1 | 10 |
| JC2000A07465 | 7 distinct DKK invoices | 7 |
| JC2000A07814 | 1 EUR invoice (`20294`) | 1 |
| Total | 10 | 30 |

Six `JP` accrual/reversal rows and one `OV` row are retained as related source
evidence, not included in invoice line totals. The original three-invoice V2
request and its 23-line validators are unchanged; loading the extra seven
invoices does not automatically add them to that workflow.

From the existing working SQL connection, with application writers paused:

1. Apply [migration 010](migrations/010_preserve_source_invoice_gaps.sql).
   It allows NULL invoice dates/vendor IDs and line quantity/unit cost, and
   adds JSON-validated nullable provenance fields to packages and lines.
   Existing rows, keys, TaskMaster, and V1 writers are retained. The known
   vendor/number uniqueness constraint and quantity check are recreated
   transactionally around the nullability changes, with the same keys/rule.
   This is a deliberate physical-model nullability change: missing source
   facts must not become invented dates, vendor IDs, or zeros.
2. Run [admin/load_capex_demo_source_invoices.sql](admin/load_capex_demo_source_invoices.sql)
   in `cict-ai-cus-sqldb`. Its reviewed source payload is embedded; it needs
   no Blob access, local-file access from SQL, parameter edits, or dummy data.
3. Save its single result grid: `SOURCE_INVOICES_LOADED`, ten invoice/package
   rows, and line counts `12`, `10`, then eight `1`s. Packages intentionally
   remain `PREPARING`; no InvoiceReady event or other outbox row is created.
   This is source staging, not a completed workflow.

The loader is transactional, insert-only, and rerunnable while matching rows
remain in source staging. It rejects conflicting or progressed records and
compares all persisted source columns plus counts before committing. It only
writes `prep.Invoice`, `prep.InvoicePackage`, and `prep.InvoiceLine`, linking
every line's `InvoiceID` and `PackageId`. TaskMaster is read and verified
unchanged. No task, accounting, bundling, reference, or review results are
created. Do not rerun the full reset to resolve a conflict.

Bruegmann vendor `2153631` and Kimley-Horn vendor `2117718` are explicit
source vendor IDs. All eight JC invoices lack an explicitly labeled vendor
ID and invoice date; JDE `Address Number` values and G/L/service/tax/batch
date serials remain in provenance under their actual source labels.
They are not silently promoted to vendor IDs or invoice dates.
The three unchecked Bruegmann order candidates are retained without choosing
one as the PO. The PO from the separate JC `OV` row is not assigned to
invoice `20294`. Source account/task suffixes are not treated as PO tasks.

`SourceMetadataJson` stores relevant header/business-unit/tax information,
unconfirmed order/address/date evidence, and data-quality warnings.
`SourceLineJson` stores source line fields, including JC document/account/task
information. No synthetic overlays or quantity=1 assumptions are applied.
Nineteen line quantities and nineteen unit costs remain NULL. Bruegmann's
header exceeds its extracted line sum by USD 99.33, matching its source tax
field; the loader does not fabricate a tax line or change the invoice total.

V1 quantity/unit-cost JSON contracts still require numbers. The new source
packages stay `PREPARING`, so the unchanged published V1 view does not expose
their nullable lines. Before later ready/event publication, implement an
explicit nullable-aware V2 path or obtain authoritative missing values;
do not simply change package status to `READY`.
The existing V1 writer remains a separate path and does not gain model-header
or nullable-value support from this handoff.

The checked-in [source extraction](../examples/data/capex/source-load/invoice-source-extract.json)
and embedded SQL are generated directly from the four repository source
files, not from saved model suggestions. Rebuild them with:

```powershell
.\.venv314\Scripts\python.exe -m tools.build_capex_source_invoice_load
```

This command extracts/generates locally; it does not connect to SQL or verify
that current private Blob contents are identical to the repository copies.
The existing normalized three-invoice identities are retained.
Workbook metadata changed during extraction without changing business cell
values. Each package's source-integrity metadata records its current raw
hash, the historical normalized-reference hash, and the normalized-facts
comparison. The generator rejects changed normalized facts before reusing
these identities. Historical reference artifacts are not overwritten.
Before workflow execution, reconcile those historical reference hashes
with the current source lineage; a matching invoice ID does not imply
identical file bytes.

## Full demo restart, preserving TaskMaster

Use [admin/reset_capex_demo_all_tables.sql](admin/reset_capex_demo_all_tables.sql)
only for an authorized restart of `cict-ai-cus-sqldb`. It supersedes the
task-only reset for a full rehearsal. It targets the 25 known application
tables other than `ref.TaskMaster`, including both outboxes and all other
reference tables. It clears data, not schemas, views, grants, or constraints.
Identity counters are not reseeded.

1. Run unchanged (`@CommitReset = 0`) and save the `PREVIEW_ONLY` inventory
   of all 25 tables and the preserved TaskMaster count. The preview does not
   delete application rows or retain backup tables, but briefly locks tables.
2. Pause **all** invoice producers, jobs, outbox dispatchers, and queue/event
   consumers. SQL reset does not clear Service Bus messages or Blob artifacts;
   already queued events can repopulate tables if consumers resume.
3. Review the inventory, set `@WritersPaused = 1` and `@CommitReset = 1`,
   then rerun the entire script. These flags are operator confirmations, not
   automatic proof that Azure workloads are paused.
4. Require `RESET_COMMITTED` for all 25 tables, `RemainingRows = 0`, and
   `PRESERVED_UNCHANGED` for TaskMaster. Save the result, including reset ID
   and retained backup table names. Keep writers paused until ingestion is ready.

Before any deletion, every nonempty target is copied into a uniquely named
`ops.CapexDemoBackup_<reset-id>_<schema>_<table>` table in the same transaction.
Backups retain column values, not the original constraints, indexes, or
computed definitions, and are not an automatic restore mechanism. Empty
targets need no backup. Existing reset backups are intentionally retained,
not part of the working tables being cleared. They contain the same business
data as the originals; retain the database's access controls and data-retention
requirements. The reset requires SELECT/DELETE on targets and CREATE TABLE
plus control of `ops` for snapshots.

The script stops on missing or unexpected visible tables, enabled table
triggers, or incompatible/untrusted foreign-key relationships. It never
disables constraints, expands its target list automatically, or clears
TaskMaster. Any backup/deletion/preservation error rolls back the transaction.
It compares every TaskMaster column before commit, not only its row count.

After reset, reference prerequisites must be loaded from approved sources
or explicitly labeled synthetic fixtures. `prep.Invoice`, `prep.InvoicePackage`,
and linked `prep.InvoiceLine` rows must be generated during ingestion for the
three selected invoices; do not prefill task, assessment, bundle, approval,
or handoff results to imitate processing. The current runtime still needs
the documented model-writer integration before this can be claimed end to end.

## Synthetic prerequisite seed after reset

**Paused:** the operator subsequently chose real-source ingestion first.
The earlier seed below requires the pre-ingestion empty state and must not
be run after the invoice load. It needs a revised source-first validation
contract before the guided rehearsal resumes synthetic seeding.

Run [admin/seed_capex_demo_synthetic_prerequisites.sql](admin/seed_capex_demo_synthetic_prerequisites.sql)
with writers paused after the full reset. It commits three synthetic projects,
three synthetic sites, and one explicitly unapproved USD threshold, matching
the normalized V2 context IDs and JSON policy amount/basis/comparison. It never
changes TaskMaster. No flag changes are required.

The seed is transactional and insert-only. Identical fixtures can be rerun;
conflicting values are rejected, never overwritten. All other 22 application
tables must still be empty, and only the expected rows may exist in the three
seeded tables. A count mismatch rolls back the seed. Archive backup tables
are excluded from this working-state check and remain retained.

One result grid reports all 26 application tables with expected/actual counts,
`SYNTHETIC_PREREQUISITES_READY`, and `UNAPPROVED_SYNTHETIC_DEMO`.
The preserved catalog count is measured, not hardcoded to 761.
Projects use explicitly registered dummy budget/type values; site lease
status and start dates remain NULL. Threshold comparison and basis are
encoded in the synthetic type and policy reference, not new schema fields.
No EUR threshold, task mappings, POs, keyword rules, human decisions, or
processing results are fabricated. See the
[assumption register](../examples/data/capex/reference-v2/synthetic-input-register-v2.json)
for `SYN-SQL-001` and `SYN-SQL-002`.

These are synthetic SQL fixtures, not a production policy load or a complete
reference library. The current V2 job still reads JSON references/context.
Loading these rows alone does not wire SQL reference reads or the missing
invoice-header/line integration. Those must be verified before an end-to-end
claim; do not immediately resume processing after the seed.

## Completing the supplied v2.1 data model

The supplied "Circle K - CapEx CIP - 9a - Class Diagram - Tables - v2.1"
contains **23 tables and 212 named columns**. The November 4 database-column
inventory contained seven of those tables and three additional runtime tables.
Migration 009 is an **additive, runtime-compatible physical implementation**,
not an exact key/type replacement of the logical diagram.

Run these scripts in the existing database from its approved private network:

1. Apply `migrations/009_complete_capex_model_tables.sql` after 001-007.
   Migration 008 can remain applied; 009 does not change those runtime grants.
2. Run `validation/validate_capex_model_schema.sql`. Return **all** result
   sets, including missing-column details, physical types/nullability/computed
   expressions, and foreign keys. A `COLUMN_COVERAGE_PASS` means all modeled
   names exist, not that every logical key or relationship is equivalent.
3. Run `tests/003_verify_capex_assessment_store.sql` to confirm that the original
   runtime insert/view contract still works after the additive changes.
4. Run `tests/004_verify_capex_model_tables.sql` for rolled-back parent,
   reference, review-case, approved-coding, handoff, and outbox insert checks.
   These synthetic test rows are never committed. Identity values can advance
   despite rollback.

No application run or workflow ID is required for these schema checks.
`validation/validate_capex_demo_v2_readiness.sql` is a different check that
requires populated, completed workflow evidence.

### Tables created by 009

| Schema | New modeled tables |
|---|---|
| `prep` | `Invoice`, `CodingDistribution`, `Project`, `Site`, `PurchaseOrder`, `PurchaseOrderLine` |
| `review` | `ReviewCase`, `ReviewDecision`, `BundleDecision`, `ApprovedCoding`, `POPAHandoff` |
| `ops` | `Outbox` |
| `ref` | `PartMaster`, `CapitalizationThreshold`, `NeverCapitalizeException`, `RepairMaintenanceIndicator` |

The seven existing modeled tables are extended without recreating them:
`prep.InvoicePackage`, `prep.InvoiceLine`, `task.TaskSuggestion`,
`capex.Assessment`, `capex.BundleCandidateGroup`,
`capex.BundleCandidateMember`, and `ref.TaskMaster`.

The additional `prep.InvoiceDataQualityWarning`, `prep.InvoiceOutbox`, and
`capex.AssessmentRun` tables are retained because existing application paths
depend on them. `ops.Outbox` does **not** replace the preparation outbox.
No existing rows, columns, views, or tables are deleted or renamed.

### Explicit physical-model decisions and differences

The diagram omits lengths, decimal precision/scale, identity generation, and
several runtime-specific dimensions. New string identifiers use `nvarchar`
to remain compatible with existing identifiers. Their full declarations are
in 009; new monetary/signal values use `decimal(19,4)`, timestamps use
`datetime2(3)`, and review concurrency uses SQL `rowversion`.

| Area | Physical implementation |
|---|---|
| Package ID | Retain existing `nvarchar(128)`, not diagram `bigint`. |
| Assessment/task IDs | Retain GUID primary keys; new references to them are also GUIDs, not diagram `bigint`. |
| Bundle identity | Retain string `BundleGroupId` scoped by `WorkflowRunId`; `review.BundleDecision` adds that run dimension for its composite FK. The separate GUID bundle-row PK is retained. |
| Invoice-line identity | Retain `(PackageId, InvoiceLineId)`, not globally unique `InvoiceLineId`. New nullable `prep.InvoiceLine.InvoiceID` references `prep.Invoice` but is not populated by existing writers. |
| Reference identity | Retain TaskMaster GUID PK, region/source hashes, and existing current-task indexes. Add nullable version, GL/accounting treatment, and effective dates without inventing their values. |
| Nullability | Missing business inputs added to populated tables are nullable, even where the diagram suggests mandatory values. No default says unknown risks are false or an unapproved task is capitalizable. Existing confidence/date/quantity nullability and original types stay unchanged. |
| Aliases | Read-only computed columns expose unambiguous logical names over existing storage, avoiding stale duplicate values. They are not new writable input fields. |
| Bundling scope versus eligibility | `BundlingEligible` is a separate nullable field, not an alias of `BundlingInScope`; scope does not prove eligibility. |
| Group counts/sequence | New `MemberCount` and `GroupSeq` are nullable until a writer explicitly maintains them. Actual runtime member count is already available in `pub.v1_Assessment`. No zero count or group sequence is fabricated. |

Computed model-name mappings:

- `prep.InvoiceLine.LineDescription` -> `Description`.
- Task `InvoiceLineID`, `SuggestedTaskCode`, `SuggestedAssetClass`,
  `ResolutionStatus`, `Evidence`, and `ModelVersion` -> `lineId`, `taskCode`,
  `assetClassCode`, `resultStatus`, `evidenceRefs`, and `modelDeployment`.
  The model deployment is the stored provenance identifier, not a claim about
  the model's underlying weight/version revision.
- Assessment `InvoiceLineID`, `RunID`, `CapExOpExSuggestion`, and
  `ContractVersion` -> `LineId`, `WorkflowRunId`, `Recommendation`, and
  `SchemaVersion`.
- Bundle `AFEProjectID`, `TaskCode`, `CombinedValue`, `GroupStatus`, and
  `LastEvaluatedRunID` -> `ProjectId`, `SuggestedTaskCode`, `TotalAmount`,
  `Disposition`, and `WorkflowRunId`.
- Bundle `UpdatedAtUtc` -> immutable snapshot `CreatedAtUtc`; this is not a
  mutable, historical cross-run group.
- Member `InvoiceLineID`, `LineValue`, `AddedRunID`, and `AddedAtUtc` ->
  `LineId`, `LineAmount`, `WorkflowRunId`, and `InsertedAtUtc`.
- TaskMaster `TaskDescription` -> `description`.

### Relationship enforcement and remaining integration

009 adds trusted foreign keys for newly created parent/child tables where
the physical keys are already unambiguous: PO/project/site/invoice, review
case/invoice, decisions to existing assessment/task GUIDs, bundle decisions
to run-scoped groups, approved coding, and handoffs. Nullable new task-part,
assessment-task, assessment-threshold, group-threshold, and line-invoice
references also receive checked FKs. These do not require invented parents
for existing rows, because newly added relationship fields start NULL.

The following diagram relationships are **deliberately not enforced or
backfilled yet**:

- Existing package-to-new-invoice: existing packages lack normalized parent
  rows, and existing preparation writers do not insert those parents.
- Existing line-to-project/site and task/assessment/member-to-line:
  historical source references can be missing and V2 currently writes from
  Blob without a corresponding preparation package/line. A global line FK
  would also discard the existing package dimension.
- Task-to-package: V2 normalized package IDs are not necessarily in the V1
  preparation store.
- Assessment-to-group: runtime writes assessments before groups in the same
  transaction; SQL Server has no deferred FK enforcement. Retain the current
  membership FKs rather than break this ordering.
- Code-only TaskMaster relationships: task codes are region-scoped and
  versioned by source hash. The diagram's code-only FKs (marked with a dagger)
  are not unique, and no approved integer-version mapping has been supplied.
  `TaskMasterVersion` cannot safely be guessed to create that mapping.
- Bundle grouping-key uniqueness by project/site/task/sequence: the demo's
  immutable run-scoped grouping is not a mutable multi-run asset registry.
- New review decision line text is not independently FK-enforced; the GUID
  assessment/task references are enforced, but application integration must
  additionally check invoice/line/case consistency.

The new tables are intentionally **empty and not wired into the runtime**.
Existing V1/V2 writers and public views retain their existing contract.
Computed aliases reflect current writes; newly nullable fields require
explicit future population. No accounting policy, project budget, task
capitalization treatment, or risk flag is inferred or seeded.

Review-case UI, human decisions, reference-policy loading, and the generalized
outbox are not made functional merely by creating these tables. Migration 008
does not grant the runtime access to these new tables; schema creation does
not broaden its permissions. A separate approved writer/grant integration is
required before those features execute.

009 checks for existing objects/columns to allow replay after a successful
deployment. It does not repair conflicting pre-existing definitions silently.
Run the schema audit and have the DBA review any pre-existing objects with
different types or constraints before treating them as aligned.

**Validation boundary:** repository checks verify the committed DDL/coverage
inventory and unchanged workflow tests. The new migration and SQL tests must
still be executed on live SQL by an authorized operator; local Python tests
are not proof of SQL compilation, deployed constraints, or live table state.

`contracts/invoice-package-v1.schema.json` is the source of truth for the
published `InvoicePackage v1` shape. `contracts/invoice-ready-v1.schema.json`
is the source of truth for the pointer-only event stored in
`prep.InvoiceOutbox`.

The Demo V2 persistence boundary writes one immutable
`capex.AssessmentRun`, its `capex.Assessment` rows, and any
`capex.BundleCandidateGroup` and `capex.BundleCandidateMember` rows in one
transaction. The run-level result hash makes exact retries idempotent and
rejects changed content under an existing workflow run ID.

`pub.v1_Assessment` exposes the line-level rule outcome, review flags, model
and policy provenance, and joined bundle context. Human-final decisions must
be stored separately; they do not update these AI-original records.

A package and its outbox event are written in one database transaction. The
package remains `PREPARING` until all header, line, warning, and outbox rows
exist. The final transaction step changes the package to `READY`, allowing
`pub.v1_InvoicePackage` to expose it.

The outbox stores durable publication intent. It does not publish directly to
Azure Service Bus. A later dispatcher claims `PENDING` records, publishes
them, and updates their delivery state.

Raw ABBYY documents are not stored in these SQL scripts or committed to the
repository.
# Expanded SQL-backed demo input access

After migrations 001-010, an authorized SQL operator can run
[011_grant_sql_demo_input_read_access.sql](migrations/011_grant_sql_demo_input_read_access.sql).
It grants the existing runtime identity SELECT on eleven source/reference
tables. It creates/seeds no tables and grants no input UPDATE/DELETE or
schema-wide permissions. Migration 008 still supplies append-only result
access. Managed identity authentication alone does not grant these reads.

The opt-in [SQL demo runner](../autarch/capex/demo_v2_sql_job.py) consumes
fixture-tagged metadata and SQL references without modifying validated
source facts or publishing legacy ready events. See the
[expanded rehearsal instructions](../docs/capex/demo-v2-demo-runbook.md#sql-backed-expanded-rehearsal).
Dummy import data are operator-supplied, not part of this migration.
