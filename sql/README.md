# Invoice Operational Store

Azure SQL assets for prepared CapEx invoices.

## JC/EU asset classes, potential review tags and result reset

These are prerequisites for the local worker; apply only missing steps.
Read-only private-network execution `capex-task-determination-proof-4lh8zrn`
verified the EU catalog, migration-018 columns and committed policy imports.
All seven references are readable: 761 tasks, 79 EU classes, three thresholds,
three exclusions, 31 maintenance indicators, 22 ARO indicators and seven categories.
The SQL-governed worker is published. ADF run
`d6ca5111-e1ab-4f6f-b186-454fad2b1d40` and its named worker succeeded.
Completion telemetry reports 21 NA task rows, eight EU asset rows and 29
assessments across ten invoices. Independent SQL acceptance remains pending:
Azure rejected the verifier start with `JobStartOverrideAuthorizationFailed`.
No new tables or imports are requested. An authorized SQL operator can set the
first RunId declaration in
[validate_capex_adf_demo_run.sql](validation/validate_capex_adf_demo_run.sql)
and [present_capex_adf_demo_run.sql](validation/present_capex_adf_demo_run.sql)
to that exact ID and run both read-only queries. Do not reset or rerun invoices
to address this separate verification-access issue.
The full prerequisite sequence is:

1. [015_create_eu_asset_class_stores.sql](migrations/015_create_eu_asset_class_stores.sql):
   creates `ref.assetClassMaster` and `task.assetClassSuggestion`, plus exclusive
   task/asset-class assessment links and EU bundle classification fields.
   Migration 009's computed `BundleCandidateGroup.TaskCode` depends on
   `SuggestedTaskCode`. Migration 015 temporarily removes that computed alias,
   makes the source nullable for EU groups, then restores its original
   expression/persistence within the same transaction. Stored group rows and
   US task values are retained. Unexpected physical `TaskCode` columns or
   column-specific permissions cause a rollback rather than being discarded.
2. [006_seed_eu_asset_class_master.sql](data/006_seed_eu_asset_class_master.sql):
   seeds 79 classes, retaining all 869 original account mappings from `Sheet2`
   of [Asset classes EU.xlsx](../examples/data/capex/Asset%20classes%20EU.xlsx).
   `Pivot` is not the detailed reference. Existing differing catalog data is
   rejected rather than overwritten. Regenerate the snapshot and seed with
   `.\.venv314\Scripts\python.exe -m tools.generate_capex_eu_asset_classes`.
   For a web/query editor, use the numbered SQL files in
   [006_eu_asset_class_batches](data/006_eu_asset_class_batches), **one complete
   file per execution**, in numeric order. Each query is at most 60,000 UTF-16
   characters, with short, individually escaped literals and a payload
   checksum. Do not paste all files together: the combined query can be clipped
   even when each line is short. Batches insert missing classes, verify any
   existing matching classes and reject conflicting source data. They commit
   separately and can be resumed; partial progress is not a complete catalog.
   Finish with
   [999_verify_eu_asset_classes.sql](data/006_eu_asset_class_batches/999_verify_eu_asset_classes.sql),
   which checks every class's names, group, mapping-data hash and source
   metadata. Do not run ADF until it reports `EU_ASSET_CLASS_CATALOG_VERIFIED`
   with 79 classes and 869 mappings. The combined 006 script remains available
   for clients that execute a complete local file without paste-size limits.
3. [016_add_potential_review_tags.sql](migrations/016_add_potential_review_tags.sql):
   adds `PotentialBundling`, `PotentialRepairMaintenanceOpex`, `PotentialAro`
   and `PotentialTagsJson` to both suggestion tables. NULL means not screened;
   false flags plus `[]` mean screened with no supported signal.
4. [018_align_assessment_review_categories.sql](migrations/018_align_assessment_review_categories.sql):
   moves the repair/ARO flag ownership to `capex.Assessment` and creates the
   outcome-category catalog. See the controlled handoff below.

The updated ADF worker routes the two original XLSX files to the EU catalog
and writes only `task.assetClassSuggestion` for those invoices. The two original
NA ZIPs use TaskMaster and `task.TaskSuggestion`, regardless of a `JC` prefix. Both link into
`capex.Assessment`; EU codes are not disguised as US task codes.
Reference hashes and full catalog contents are checked before model calls.
The EU asset reference does not approve accounting thresholds or policies.

Potential tags contain a reason and source-line quotations; multiple tags may
coexist. ARO is a possible retirement/removal/closure review signal, not a
liability calculation. Contextual supporting lines must be in the same invoice.
Tags do not change accounting, create approved assets, post to GL, or implement
final Power BI categories. Existing `capex.BundleDecision`,
`BundleCandidateGroup`, `BundleCandidateMember` and assessment bundling fields
remain the bundling proposal mechanism. The reporting manual has not been
adopted as an approved machine-executable accounting policy.

### Clear results immediately before the next approved run

**Not part of the current workflow. Do not execute a reset for the SQL-governed
run: preserve prepared packages, policy references and historical results.**
The legacy script below has a broader, destructive scope and requires separate
explicit approval; in particular, it can remove the newly imported exclusions.

Use [reset_capex_demo_results.sql](admin/reset_capex_demo_results.sql), not the
older all-tables reset (which also targets source/reference data).
It targets **all rows** in these result/prepared-data stores in this demo database:

- `task.TaskSuggestion`, `task.assetClassSuggestion` if installed.
- `capex.Assessment`, `capex.AssessmentRun`.
- `capex.BundleDecision`, `capex.BundleCandidateGroup`, `capex.BundleCandidateMember`.
- `capex.LlmShowcaseRun`.
- `mvp_review.documents`, then `mvp_review.imports` (child before parent).
- `prep.InvoicePackage`, with `InvoiceLine`, `InvoiceDataQualityWarning` and
  `InvoiceOutbox` explicitly archived/deleted first instead of unarchived cascades.
- `prep.PurchaseOrderLine`, `prep.PurchaseOrder`, `prep.Project`, `prep.Site`
  in foreign-key-safe order.
- `ref.NeverCapitalizeException` and `ref.PartMaster`, after their dependent
  result rows. `ref.TaskMaster` and `ref.assetClassMaster` are not cleared.

Run unchanged for a preview. Pause invoice jobs and other application writers;
copy the preview's exact `ExpectedCountsJson` into `@ExpectedCountsJson`, set
`@WritersPaused=1` and `@CommitReset=1`, and run again. All source rows are
archived to uniquely named `ops.CapexResultBackup_*` tables before deletion.
Counts are rechecked under locks; any mismatch or deletion failure rolls back
the entire reset. Existing native or MVP human-review decisions block it.
Do not delete those decisions merely to bypass this guard.

Each scope expansion requires a fresh preview and new counts JSON, including
the `mvp_review` and `prep` tables. Do not reuse earlier confirmation counts.
`prep.Invoice` is preserved: the preview reports invoice-to-PO links and commit
is blocked if any retained invoices reference purchase orders being cleared.
Do not delete or detach those invoices merely to bypass this guard.

The script preserves schemas, views, processing/bundling logic, TaskMaster,
EU asset master, capitalization thresholds, repair indicators, `prep.Invoice` headers, and original
landing/source blobs. It clears the listed prepared/project/PO/site data as
well as SQL review handoff
envelopes and stored document-byte copies, after archiving them in `ops`;
it does not delete the original blobs. Archived rows remain available to the
DBA; they are not an automatic restore workflow.
This script does not reload project/PO/site data; the current ADF invoice demo
has no authoritative POPA/PO/site feed to repopulate those master tables.
Historical runs' native-table verifiers will no longer reconcile after a
committed reset. Use a fresh ADF RunId and same-run reconciliation afterward.

**No reset has been executed by the assistant.** After SQL prerequisites, build,
validate and publish a new worker image through the approved deployment workflow
before rerunning ADF. The currently published image does not contain these
EU/tag changes. Run the updated read-only
[presenter](validation/present_capex_adf_demo_run.sql) and
[verifier](validation/validate_capex_adf_demo_run.sql) only after migrations
015/016/018, using the new completed RunId.

### Assessment categories, review flags and scoped exclusions

Pause writers before running
[018_align_assessment_review_categories.sql](migrations/018_align_assessment_review_categories.sql).
It creates `ref.AssessmentOutcomeCategory` and adds nullable
`CategorySuggestion`, `RepairMaintenanceFlag`, `AroFlag` and
`PotentialTagsJson` to `capex.Assessment`. `CategorySuggestion` references
the catalog's stable `CategoryCode`; join to `CategoryName` for display.
The existing `Recommendation` and its computed `CapExOpExSuggestion` alias
are retained: the accounting choice CAPEX/OPEX/REVIEW is not the same as
the slide's overlapping review categories.

The migration copies existing suggestion flags through the assessment's
`TaskSuggestionID`/`AssetClassSuggestionID` links, rejects orphaned non-NULL
flags and conflicting evidence, then drops `PotentialRepairMaintenanceOpex`
and `PotentialAro` from both suggestion tables. Supporting tag JSON is copied
to assessments; legacy JSON and potential-bundling metadata remain on
suggestions for compatibility. NULL means not screened, 0 means screened
without that signal, and 1 means potential review. The existing accounting
`RepairMaintenanceIndicator` remains a separate nature decision.
Success: `ASSESSMENT_REVIEW_SCHEMA_READY`.

The updated ADF writer persists/readbacks flags and evidence on assessments
transactionally for both US and EU lines. Do not run an old flag-writing
worker after migration 018. Migration 016 recognizes the completed move
when replayed and does not recreate the retired columns.

Next, run the following separately in preview mode and review before committing:

- [009_seed_assessment_outcome_categories.sql](data/009_seed_assessment_outcome_categories.sql):
  set `@ApplyChanges=1` after preview. It imports the slide's seven labels,
  considerations and suggested actions, plus explicitly proposed decision
  criteria. Six are enabled for the demo; potential bundling is disabled.
  Success: `ASSESSMENT_CATEGORIES_LOADED`, seven categories, six enabled.
  Existing conflicting definitions are rejected, not overwritten.
- [010_replace_contextual_never_capitalize_exceptions.sql](data/010_replace_contextual_never_capitalize_exceptions.sql):
  preview existing rows, set `@ExpectedCurrentRows` from `CurrentTableRows`,
  then set `@ApplyChanges=1` to replace them with three scoped PP&E rules.
  The old rows are archived to a unique `ops.NeverCapitalizeBackup_*` table
  in the same transaction. Success: `CONTEXTUAL_EXCLUSIONS_LOADED`, three rules.
  Migration 018 adds `ApplicabilityRule`: evaluate that context, never the
  keyword alone. The rules address routine maintenance, only the maintenance
  portion of renovations, and transferred-asset installation where original
  installation costs are not disposed. Mixed/unsubstantiated cases require
  REVIEW, not automatic whole-line OPEX.

Catalog import does not assign historical `CategorySuggestion` values.
Selecting primary categories/precedence and reading the new SQL policy rules
into the GPT stage remain separate integration work. Existing rows stay NULL
where no classification/screening was performed. Do not relabel old generic
recommendations as if they were the slide's approved categories.

### ARO slide reference import

1. Run [017_create_aro_indicator_store.sql](migrations/017_create_aro_indicator_store.sql)
   to create `ref.AROIndicator` and grant the existing demo runtime user
   `SELECT` on that table only. Success: `ARO_INDICATOR_STORE_READY`.
2. Run [008_seed_aro_indicators.sql](data/008_seed_aro_indicators.sql)
   with `@ApplyChanges=0` to preview. Set `@ApplyChanges=1` and rerun to
   commit. Success: `ARO_INDICATORS_LOADED`, five groups and 22 total rows.

The source is **slide 3 only** of `CAPEX_Microsoft_AI_POC_V3 nov 11.pptx`.
`Category` contains the exact **Signal group**, `Keyword` contains an
individual **Keywords / phrases to test** entry, and `PolicyReference`
contains only the group's exact **Proposed review rule**. The first demolition
phrase, `Remove / demolish / dispose of tanks, canopy or lines`, is one entry:
the comma separates affected assets, not unrelated keywords.

`IndicatorID` starts at 1 for the newly created table. `Active=1` enables
the seeded reference entries; `RequiresRemovalContext=1` conservatively
requires a supported retirement/removal/closure connection for every signal.
Explicit retirement wording may provide that context itself. These are
screening settings, not liability measurements or final accounting decisions.
The import inserts missing entries, validates existing matching entries,
rejects conflicting/extra rows, and does not delete or overwrite existing
data. An unchanged rerun inserts zero rows.

This creates/imports reference data only. It does **not** screen invoices,
populate assessment `AroFlag`, calculate an ARO liability, enable bundling, or wire
the worker to SQL rules. SQL-backed screening still requires the worker
integration and deployment; migration 018 moves the review flags to assessments.

### Repair/maintenance slide reference update

[007_seed_potential_repair_maintenance_indicators.sql](data/007_seed_potential_repair_maintenance_indicators.sql)
loads 31 English keyword/phrase rows from **slide 4** of
`CAPEX_Microsoft_AI_POC_V3 nov 11.pptx`. `Category` contains the exact
**Signal group** label (for example, `Preventive maintenance`), and
`PolicyReference` contains only that group's exact **Guideline review rule**.
The source slide cites FY28 CAPEX Definition Guideline (WIP), Appendix B;
this provenance is documented here rather than appended to each rule.
Preview by setting `@ApplyChanges=0`; review the existing rows marked `DELETE` and the
31 proposed rows marked `INSERT`. To commit, set `@ExpectedCurrentRows` to
the preview's `CurrentTableRows`, set `@ApplyChanges=1`, and rerun.
This intentionally deletes **all existing rows in this table** and replaces
them with the slide-4 catalog; it does not preserve previous indicator IDs.
`TRUNCATE TABLE` resets the existing `IDENTITY(1,1)`, so the new IDs are
1 through 31, including when the table was previously empty. This requires
`ALTER` permission on the table; referencing foreign keys or other SQL Server
restrictions cause an error rather than disabling constraints.
The table definition and other tables are unchanged. Truncation, insertion
and readback validation share one transaction: an error rolls everything
back. No persistent backup is created. Success reports
`SLIDE_4_REPAIR_INDICATORS_LOADED`, 31 keywords, six groups, 31 total rows,
`FirstIndicatorID=1` and `LastIndicatorID=31`.

The demo scope agreed on November 11 defers bundling. Its columns/proposal machinery are not
enabled or disabled by this reference-data query. For ARO review, migration
018 places nullable `AroFlag` on each line's `capex.Assessment` row, with source
evidence in `PotentialTagsJson`. A schema migration does not screen historical invoices:
NULL remains "not screened", 0 means screened without supported ARO evidence,
and 1 means potential ARO for human review, not a measured liability or a GL
instruction. The updated worker must be published and verified before claiming
these flags are populated by ADF.

`SignalStrength=1` is uniform keyword-presence metadata, not a probability,
an approved accounting weight or an automatic OpEx instruction. The slide's
rules are draft/WIP review guidance, not final accounting decisions. Removal is not an
automatic OpEx or ARO determination; replacements/directly attributable costs
still require review. No invented translations or differentiated scores are
added. The current worker's accounting policy remains pinned JSON; populating
this SQL table alone does not wire it into accounting or override those policies.

The four supplied classification/bundling/repair/ARO slides contain **no numeric
capitalization thresholds, country/currency assignments or effective dates**.
`ref.CapitalizationThreshold` is therefore unchanged pending those actual values.
Do not treat review-category labels or draft keywords as monetary thresholds.

## Opt-in Thursday MVP stores (separate from Friday)

For the newly approved service-boundary increment, hand the DBA
[014_create_thursday_mvp_stores.sql](migrations/014_create_thursday_mvp_stores.sql).
It creates six isolated tables under `mvp_batch` and `mvp_review`, plus separate
runtime roles. It does not change Friday's tables, TaskMaster or stored results.
It does not create Entra users or assign role members automatically.
After approved identities are known, the DBA uses the commented identity
examples at the end of the script, granting each identity only its matching
runtime role. Agent and reference services receive no SQL role.

Then run the read-only
[014_validate_thursday_mvp_stores.sql](tests/014_validate_thursday_mvp_stores.sql).
These scripts have not been executed against Azure SQL by the assistant.
Live ODBC/schema/concurrency checks remain required before cloud demonstration.
See the [Thursday operator handoff](../docs/capex/thursday-architecture-demo.md#repository-service-increment-and-private-vm-handoff)
for API roles, VM commands and remaining deployment gates.

### Existing-assets ADF handoff verification

The ADF worker reuses the existing native task/assessment/model-bundle tables
and migration 014's review tables. The JC/EU extension additionally needs
migrations 015/016/018 above. The explicitly chosen
shared demo identity also needs effective `mvp_review` read/insert permissions
(normally `capex_mvp_review_runtime` membership). This is a demo compromise,
not the distinct-identity production design above.

If the runtime probe reports missing `mvp_review` permissions, first run
[validate_capex_adf_review_access.sql](validation/validate_capex_adf_review_access.sql)
as a DBA in `cict-ai-cus-sqldb`. It checks table/role presence and the existing
shared identity's database principal, role membership and grants without
changing anything. Missing permission can also mean an object is not visible;
do not recreate tables or grant broad database access from that error alone.

When the DBA has confirmed the existing tables/role/user and the missing
membership, [grant_capex_adf_review_membership.sql](admin/grant_capex_adf_review_membership.sql)
adds only the approved shared demo UAMI to the existing review runtime role.
It validates the database and pins the UAMI object/client IDs, is rerunnable, and does not
create objects or grant `db_owner`. This intentionally allows the shared demo
identity to import review evidence and record review actions; production
separation is still required. Repeat the workload's effective-access
preflight afterward; role membership alone is not proof of effective access.
The existing demo user was observed with a 16-byte client-ID SID; the guard
accepts that verified mapping as well as the object-ID SID, never a name-only
match. It does not remap or recreate the database user.

If the operator can grant table permissions but cannot alter role membership,
[grant_capex_adf_review_table_access.sql](admin/grant_capex_adf_review_table_access.sql)
is a narrower, object-scoped alternative for the same shared demo identity.
It validates the database, existing tables and pinned identity, then grants
only `SELECT`/`INSERT` on `mvp_review.imports`, `documents` and `decisions`
transactionally. It does not require role membership or grant schema-wide
access. The worker checks effective permissions, not membership; membership
may therefore remain zero after successful direct grants. Existing DENY
permissions can still prevent effective access, so repeat the worker preflight.
If the three direct grants already succeeded, do not rerun them merely to
obtain role membership; continue with runtime verification.

After an approved live ADF run succeeds and publishes its run-scoped `batch.json`,
run [validate_capex_adf_demo_run.sql](validation/validate_capex_adf_demo_run.sql)
from the existing authorized private SQL connection. Set its `@WorkflowRunId`
to the ADF RunId. It is read-only and uses actual extracted line counts from the
committed handoff, not Friday's fixed thirty-line fixture. It checks task links,
model versus non-model provenance, model bundle coverage and source hashes.
This script has not been executed against live Azure SQL by the assistant.

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
