# CapEx Demo V2 Client Runbook

## What this workflow does

### SQL-backed expanded rehearsal

The original artifact-based runner below still uses three packages/23 lines.
For the expanded SQL-staged input set, use
[`capex-demo-v2-sql`](../../autarch/capex/demo_v2_sql_job.py) instead.
This is an opt-in **synthetic reference rehearsal**, not automatic ingestion
from landing and not proof of a deployed or completed Azure execution.
Do not rerun an old seed/reset or start the old preparation/dispatch jobs.

The SQL path reads all packages tagged with the supplied fixture ID, validates
explicit expected invoice/line counts, and reads actual SQL Project, Site,
PurchaseOrder/Line, PartMaster, threshold, keyword-rule and active TaskMaster
rows. It checks header/package consistency, PO/part associations, reference
provenance, effective dates, catalog hashes and preservation of known source
values. Missing source quantities, prices, vendor IDs and dates remain NULL
in SQL and in the captured source packages. Substitutes exist only in the
processing copies and retain synthetic provenance.

An unambiguous PO/PartMaster agreement produces an exact-reference task
determination. A conflict/inactive code produces Review; codes are never
invented. **This path makes no LLM calls.** Do not describe the seeded task
codes as model-generated suggestions or the US catalog proxies as governed
European mappings. The existing model-assisted artifact runner is separate.
Native USD/EUR/DKK demo thresholds are selected per currency without FX
conversion or mixed-currency totals. The old USD-only/current-policy path
retains its Review behavior for unsupported regions/currencies.
The repair/never-capitalize sentinel examples do not demonstrate those
branches unless an actual line description matches their keywords.

Execution prerequisites:

1. Apply [011_grant_sql_demo_input_read_access.sql](../../sql/migrations/011_grant_sql_demo_input_read_access.sql)
   using the authorized SQL operator connection. It grants the existing runtime
   identity SELECT on eleven input tables, creates no tables/data, and does not
   replace migration 008's append-only result permissions.
2. Build an updated image using the existing
   [Dockerfile.capex-v2](../../Dockerfile.capex-v2). A local test does not update
   the deployed image. Use an authorized private-network Azure host with the
   assigned managed identity, ODBC driver, SQL grants and Blob artifact access.
   The job command must be `["capex-demo-v2-sql"]`; the old image entrypoint
   `capex-demo-v2` does not select this mode automatically.
   Use [capex-demo-v2-sql-job.bicep](../../infra/container-apps/capex-demo-v2-sql-job.bicep)
   for this mode, not the original artifact-runner template. Supply
   `jobArguments` using the read-only CLI flags below, without `--execute`
   or `--persist-results` for the first checkpoint. The job is manual,
   single-replica and has automatic retries disabled to avoid replaying a
   partially persisted execution.
3. Give every snapshot/result a fresh private artifact URI. Do not overwrite
   preflight evidence or use an already-evidenced workflow run ID.

**Private build prerequisite:** use an approved runner that can authenticate
to the private registry and Blob endpoints. Build from a clean context
containing only the Dockerfile, package manifest, README and required
`autarch` source/assets; do not upload local environments, secrets or source
invoices. A network-rule denial is not fixed by signing in again. Do not
enable public access or resize unrelated build pools to bypass this blocker.

Run the following on that host (PowerShell argument-continuation form shown):

```powershell
python -m autarch.capex.demo_v2_sql_job `
  --fixture-id '<operator-supplied-fixture-id>' `
  --allow-synthetic-inputs `
  --expected-invoices 10 --expected-lines 30 `
  --snapshot-uri '<fresh-private-preflight-snapshot-uri>'
```

Default behavior is read-only SQL preparation/validation plus creation of an
immutable input artifact. It performs no task/accounting/bundling processing,
publishes no events and writes no SQL results. Require `SQL_INPUTS_VALIDATED`,
ten invoices/thirty lines and retain `inputSha256`. Inspect the snapshot's
original versus processing packages, policies and reference matches.
The `sqlSnapshot` section retains the actual input/reference rows, including
their SQL identifiers, so the digest and reference provenance can be audited
without relying on later database contents.

After review, use a fresh execution snapshot/result URI and pass the retained
digest. Changed source/reference data fail before processing:

```powershell
python -m autarch.capex.demo_v2_sql_job `
  --fixture-id '<same-fixture-id>' `
  --allow-synthetic-inputs `
  --expected-invoices 10 --expected-lines 30 `
  --snapshot-uri '<fresh-private-execution-snapshot-uri>' `
  --expected-input-sha256 '<preflight-inputSha256>' `
  --workflow-run-id '<new-workflow-id>' `
  --output-uri '<fresh-private-result-uri>' `
  --execute --persist-results
```

Omit `--persist-results` for a processing-only rehearsal. Result exit codes
are 0/SUCCEEDED, 2/PARTIAL and 1/FAILED. Require exactly one determination and
assessment for each of the thirty source line IDs, the same workflow ID in
task correlation IDs and assessments, and bundle totals/membership that
reconcile to eligible processing lines. Never assert historical result counts
without inspecting the actual run.

This direct-batch path intentionally leaves legacy package status PREPARING
and emits no v1 InvoiceReady events: the v1 publication contract is not
nullable-aware. Normalized v2 packages are prepared in memory and captured
in the input artifact. `AssessmentRun` and result evidence, not a manually
changed legacy status, demonstrate processing. Source ingestion remains the
earlier manual SQL handoff.

Task persistence commits per package; accounting/group/member persistence is
a separate atomic transaction. A failure can leave committed task evidence.
Inspect PARTIAL errors and actual SQL rows; do not delete results or hide
partial execution. This SQL command requires a new run ID rather than
claiming a fresh-read/model rerun is an identical replay. It does not write
review decisions, approvals, handoffs or either outbox.

Presenter checkpoints:

| Stage | Evidence and truthful wording |
|---|---|
| Inputs | "Ten invoices/thirty lines were manually staged from source exports; supporting reference data are synthetic." |
| Preparation | "The original facts are preserved; the processing copy applies explicitly labeled demo enrichment." |
| Tasks | "The workflow calculates exact PO/PartMaster matches against the active catalog; this branch did not call an LLM." |
| Accounting | "Deterministic native-currency demo policies produce CapEx, OpEx or Review with citations; these are not approved business policy." |
| Bundling | "Only compatible eligible members form candidates. A candidate is not an approved asset." |
| Audit | "SQL rows and artifacts identify the execution, source lines and synthetic assumptions. Human approval/handoff are not prefilled." |

### Original artifact-based three-package workflow

The workflow processes the three demo invoice packages in one finite batch:

1. Verifies each normalized package, invoice, run, and source hash.
2. Uses governed US TaskMaster data for deterministic or GPT-5.1-assisted
   task suggestions.
3. Applies deterministic capitalization policy. GPT-5.1 never decides CapEx
   or OpEx.
4. Groups eligible below-threshold lines by task, site, project, and currency.
5. Persists immutable task, assessment, bundle, and replay evidence.
6. Writes one result artifact with package summaries and explicit errors.

Service Bus, Azure Functions, Microsoft Agent Framework, and hosted Foundry
agents are not in the Demo V2 execution path.

## Guided rehearsal: clean starting point

Proceed one stage at a time; save each result before advancing. The presenter
supplied 15 existing task rows: 12 prior Bruegmann results and three CICT-187
acceptance rows, all stamped with `gpt-6.1-sol`. These are historical evidence,
not proof that the new three-invoice V2 workflow has run.

### Stage 1: read-only baseline

Run [validate_capex_demo_start.sql](../../sql/validation/validate_capex_demo_start.sql)
in `cict-ai-cus-sqldb` and retain all result sets. It inventories task rows,
dependent evidence, prepared invoices, governed tasks, and new model tables.
It changes no data and requires no workflow ID.

Presenter wording:

> "Before processing the invoices, I verify our starting state. This separates
> historical test results from today's run, protects dependent audit evidence,
> and confirms which source and reference records are actually available."

Expected from the supplied snapshot, not an assertion about current SQL:
15 task rows across two packages. Capture the actual counts and dependencies.
Archive the original task rows before
an explicitly scoped reset; stop if references exist rather than deleting
human decisions or disabling constraints. A previous committed outbox event
must not be replayed and presented as fresh extraction.

### Stage 2: archive and clear all working tables except TaskMaster

The operator subsequently authorized a full restart of the 25 application
tables other than `ref.TaskMaster`, including preparation, task, accounting,
review, other reference tables, and both SQL outboxes. This supersedes the
task-only reset. Existing audit backup tables are intentionally preserved.

Run [reset_capex_demo_all_tables.sql](../../sql/admin/reset_capex_demo_all_tables.sql)
in `cict-ai-cus-sqldb` unchanged first. Expect `PREVIEW_ONLY` with all 25
target counts and the preserved TaskMaster count. Save and review the result.
The preview changes no application rows, but temporarily locks the tables.

Before committing, pause invoice producers, jobs, dispatchers, and event
consumers. SQL reset does not clear external queued events or Blob artifacts.
Set both `@WritersPaused = 1` and `@CommitReset = 1`, then rerun the entire
script. Every nonempty table is archived before any deletion. Require
`RESET_COMMITTED`, matching before/archived/deleted counts, zero remaining
rows for all targets, and `PRESERVED_UNCHANGED` for TaskMaster. Save the reset
ID and backup table names. Do not remove the snapshots.

The script preserves schemas, views, grants, and constraints. It validates
foreign-key deletion order rather than disabling constraints and rolls back
on any failure. Missing/unexpected tables or enabled triggers require review.
See the [SQL reset instructions](../../sql/README.md#full-demo-restart-preserving-taskmaster)
for permissions, backup scope, and failure handling.

Presenter wording:

> "I preserve historical records in audit snapshots, then clear the working
> tables for a clean rehearsal. The governed task master remains unchanged.
> Today's invoice, suggestion, and assessment records will be produced by
> the workflow, not prefilled to look like completed processing."

Keep writers paused until ingestion is ready. Next inventory and load the
required reference inputs, using approved source data or explicitly labeled
synthetic fixtures. The ingestion path must write `prep.Invoice` and link
`prep.InvoiceLine.InvoiceID`, not just populate the older package/line store.
Do not prefill downstream processing or human-review results.

Later stages must verify source/preparation persistence for all three inputs,
task determination, deterministic accounting, conditional bundling, persisted
reconciliation, and replay. Existing V2 only writes task/assessment/bundle
evidence; it does not populate all the newly created logical-model tables.
Missing integration is work to complete, not an expected side effect of 009.
Only populate reference/project/PO/review/handoff tables when appropriate
source data or a real human action exists. An unresolved task or accounting
Review remains a valid result; do not force a governed code or classification.
Fresh model output may differ from prior saved deterministic evidence.

### Stage 3: load real invoice facts before synthetic data

The operator corrected the JC source selection to include both workbooks.
They contain two different projects and eight distinct invoice-type records.
Together with Bruegmann and Kimley-Horn, extract ten distinct invoice headers,
ten packages, and thirty source lines. Do not merge the JC projects/currencies
or copy a PO from a separate non-invoice row. The original three-invoice
workflow request still covers only 23 lines; the expanded source inventory
must not be claimed as fully processed by that unchanged request.

Keep application writers paused. In `cict-ai-cus-sqldb`:

1. Apply [010_preserve_source_invoice_gaps.sql](../../sql/migrations/010_preserve_source_invoice_gaps.sql).
   It permits actual source gaps as NULL and adds JSON provenance columns.
2. Run [load_capex_demo_source_invoices.sql](../../sql/admin/load_capex_demo_source_invoices.sql).
   No flag changes or synthetic seed are required.
3. Save its single `SOURCE_INVOICES_LOADED` result grid. Expect ten
   invoice/package rows and thirty lines, with all packages `PREPARING`.

The extraction is available as
[invoice-source-extract.json](../../examples/data/capex/source-load/invoice-source-extract.json).
It includes confirmed vendor IDs, source header/line facts, business units,
tax context, and unconfirmed order/address/date information under source
labels. It is generated from the repository's four original source files.
No fresh private Blob inventory or live SQL execution is implied.
The loader preserves the original three normalized invoice/package/line
identities and creates separate identities for the seven additional invoices.

Missing dates/vendor IDs remain NULL for the eight JC invoices. Nineteen
quantity/unit-cost values remain NULL, including Bruegmann shipping.
No fake dates, zeros, FX conversion, PO selection, AFE mapping, or synthetic
context are applied. Confirmed source vendor IDs are `2153631` and `2117718`;
JDE Address Number is retained without claiming it is a confirmed VendorID.

Presenter wording:

> "I load the actual extracted invoice records first. Invoice headers,
> packages, and lines share their source identities, while source gaps and
> unconfirmed mappings remain visible. These two JC exports contain multiple
> invoices, so I keep their projects, invoice numbers, and currencies separate."

Stop after the SQL result is verified. This is source staging, not model
execution or event publication. No task/accounting/bundle/review/outbox rows
are inserted. The unchanged V1 published contract requires numeric quantity
and unit cost, so do not mark these nullable packages READY or dispatch them
through V1 without a nullable-aware integration path.
See the [source-load handoff](../../sql/README.md#load-real-invoice-headers-packages-and-lines-first).

### Stage 4: synthetic prerequisites (paused pending source-first revision)

**Do not run the earlier seed yet.** The operator changed the order to real
invoice ingestion first. The retained seed below checks the pre-ingestion
empty state; it must be adapted and verified against the loaded source rows
before the synthetic stage resumes. No synthetic SQL data has been loaded
by the assistant.

The operator reported `PRESERVED_UNCHANGED` with 761 TaskMaster rows and
exported the reset backup names. Do not run the reset again. Keep writers
paused and run
[seed_capex_demo_synthetic_prerequisites.sql](../../sql/admin/seed_capex_demo_synthetic_prerequisites.sql)
in `cict-ai-cus-sqldb`. No flag edits are required. This is a committing seed,
not a preview. It inserts only missing, matching fixtures; a conflicting
existing row causes an error rather than an overwrite. Repeating it is safe
before ingestion, but it deliberately rejects a database that has progressed
beyond this clean prerequisite stage.

The script returns **one result grid** with
`SYNTHETIC_PREREQUISITES_READY` and the explicitly unapproved scenario label:

| Table | Expected rows | Meaning |
|---|---:|---|
| `ref.TaskMaster` | 761, based on the operator's last result | Unchanged governed catalog; actual preserved count is reported |
| `prep.Project` | 3 | Existing DEMO-prefixed overlay IDs, OPEN, bundling in scope |
| `prep.Site` | 3 | Existing DEMO-prefixed overlay IDs; lease status remains NULL |
| `ref.CapitalizationThreshold` | 1 | Unapproved USD 1,000 unit-cost >= fixture; no EUR threshold |
| Other 22 working tables | 0 each | Waiting for source ingestion, processing, or actual human review |

`SYN-SQL-001` explicitly records mandatory project-column placeholders:
budget 0 and type `SYNTHETIC_DEMO`. Zero is not an authoritative budget and
must not qualify a project-budget exception. Project start date and lease
status are not invented. `SYN-SQL-002` records the threshold's fixture-only
effective date. Both assumptions are in the
[synthetic input register](../../examples/data/capex/reference-v2/synthetic-input-register-v2.json).
Do not treat the SQL column name `GovernedThreshold` as evidence of approval.
The row is labeled synthetic in `ThresholdType` and its policy reference.

Do not seed PO/header/line records without source data, pre-map parts to
tasks to force a desired model response, invent keyword exceptions, or
prefill task/assessment/bundle/review/outbox results. PO and part mappings
are optional; missing facts remain missing. The existing synthetic
capitalization rules remain in JSON and are not represented by fake
never-capitalize or repair/maintenance rows.

Presenter wording:

> "The task catalog is real and unchanged. Because source project and site
> context is unavailable, I load clearly labeled demo fixtures and a
> synthetic threshold. These are not approved business records. Invoice
> headers and lines, task suggestions, and accounting results will be
> created by their processing steps, not preloaded as completed results."

Stop after saving the single result grid. SQL seeding does not run invoices
or prove integration: V2 currently reads its JSON reference snapshots and
context overlays, not these SQL fixtures. The subsequent ingestion stage
must wire and verify `prep.Invoice`, `prep.InvoicePackage`, and linked
`prep.InvoiceLine` for all three inputs. Missing JC invoice date/vendor ID
and nullable normalized quantities versus non-null legacy SQL columns also
require explicit handling; do not silently substitute dates, vendors, or zeros.

## November 4 live-readiness check

The workstation is signed into the target subscription. Storage and SQL both
have public network access disabled. Direct Blob inventory failed and SQL
returned error 47073 (Deny Public Network Access). Starting a read-only
inventory execution of the existing `capex-task-determination-proof` job was
also denied (`Microsoft.App/jobs/start/action`), including after the reported
PIM activation. No firewall settings, SQL rows, or job definitions were changed.
Refresh the Circle K login after PIM activation and have an authorized operator
run the inventory from the private network. Do not equate PIM activation with
network access or a successful deployment.

The ARM job inventory did not contain `capex-demo-v2`. Local replay of prior
task results is available for all three inputs, but does not verify current
Blob contents, a fresh model call, or live SQL persistence.

Demo V2 loads normalized packages from Blob and writes task and assessment
evidence; it does **not** load those packages into the V1 `prep.InvoicePackage`
or `prep.InvoiceLine` tables. Deploying V2 alone will not make the other two
invoices appear in those V1 tables. Missing PO, site, EU task, or approved
policy data must not be manufactured to populate the logical table diagram.

## Azure resources and responsibilities

| Resource | Purpose |
|---|---|
| Container Apps Job `capex-demo-v2` | Runs one direct three-invoice finite batch and exits |
| Container Apps Environment `cict-ai-cus-acaenv` | Hosts the job and captures execution logs |
| ACR `cictaicusacr` | Stores the immutable `capex/demo-v2` image |
| Storage `cictaicusst`, container `landing` | Stores requests, normalized inputs, governed references, and immutable results |
| Azure SQL `cict-ai-cus-sqldb` | Stores TaskSuggestion, Assessment, BundleCandidateGroup, and BundleCandidateMember evidence |
| Foundry account `cict-ai-cus-foundry` | Hosts the direct `gpt-5.1` Responses deployment |
| UAMI `cict-ai-cus-capex-id` | Authenticates to ACR, Blob, SQL, and GPT-5.1 without secrets |

Verified UAMI assignments:

- `AcrPull` on `cictaicusacr`.
- `Storage Blob Data Contributor` on the `landing` container.
- `Cognitive Services OpenAI User` on the Foundry account.

The SQL contained user and database roles still require verification from a
network path that can reach the SQL endpoint.

## Current acceptance evidence

### Truthful current policy

- Status: `SUCCEEDED`.
- Packages: 3.
- Lines: 23.
- Task determinations: 23.
- Assessments: 23 Review, 0 CapEx, 0 OpEx.
- Bundle groups: 0.
- Reason: Circle K policy confirmations are still missing; the workflow does
  not invent accounting policy.

### Explicit synthetic demo

> **Warning:** These are unapproved demo assumptions, not Circle K policy.

- Status: `SUCCEEDED`.
- Packages: 3.
- Lines: 23.
- Assessments: 7 CapEx, 11 OpEx, 5 Review.
- Bundle groups: 1.
- Bundle members: 9, totaling USD 455.56 on Bruegmann.
- Two zero-dollar Kimley lines remain ungrouped candidates, not a USD 0 bundle.
- Exact SQL-adapter replay: 0 additional inserts.
- Invalid governed task codes: 0.

The deterministic evidence file is
`examples/data/capex/results-v2/workflow.synthetic-validation.json`.

The supplied invoices do not demonstrate a cross-invoice or threshold-reaching
bundle: their eligible task/site/project keys differ. Cross-invoice grouping
is covered by automated synthetic tests, not by these source invoices. Do not
present the Bruegmann candidate as an approved capitalization decision.

## Pre-demo checklist

- [ ] Confirm the client understands that truthful and synthetic results are
  shown separately.
- [ ] Keep the synthetic warning visible.
- [ ] Confirm `gpt-5.1` is available.
- [ ] Confirm migration 007 exists in Azure SQL.
- [ ] Confirm the job identity can read/write the `landing` container.
- [ ] Confirm the job identity is an Azure SQL contained user with execute,
  select, and insert permissions for the CapEx schemas.
- [ ] Confirm the immutable image tag in the parameter file exists in ACR.
- [ ] Confirm the requested result blob does not already exist.
- [ ] Run one rehearsal and save the execution name.
- [ ] Keep the committed local evidence open as the fallback.

## Local rehearsal commands

Truthful current-policy run:

```powershell
.\.venv314\Scripts\python.exe tools\build_capex_demo_v2_request.py `
  --workflow-run-id capex-demo-v2-rehearsal-current `
  --output .session\capex-current-request.json

$env:AZURE_OPENAI_ENDPOINT = "https://cict-ai-cus-foundry.openai.azure.com/"
.\.venv314\Scripts\python.exe -m autarch.capex.demo_v2_job `
  --request-uri .session\capex-current-request.json `
  --task-master-uri examples\data\capex\reference-v2\task-master-us-v2.json `
  --policy-uri examples\data\capex\reference-v2\policy-config-v2.json `
  --output-uri .session\capex-current-result.json
```

Synthetic demo run:

```powershell
.\.venv314\Scripts\python.exe tools\build_capex_demo_v2_request.py `
  --workflow-run-id capex-demo-v2-rehearsal-synthetic `
  --synthetic `
  --output .session\capex-synthetic-request.json

$env:AZURE_OPENAI_ENDPOINT = "https://cict-ai-cus-foundry.openai.azure.com/"
.\.venv314\Scripts\python.exe -m autarch.capex.demo_v2_job `
  --request-uri .session\capex-synthetic-request.json `
  --task-master-uri examples\data\capex\reference-v2\task-master-us-v2.json `
  --policy-uri examples\data\capex\reference-v2\policy-config-v2.json `
  --rule-set-uri examples\data\capex\reference-v2\synthetic-capitalization-rules-v2.json `
  --synthetic-register-uri examples\data\capex\reference-v2\synthetic-input-register-v2.json `
  --output-uri .session\capex-synthetic-result.json
```

Deterministic fallback:

```powershell
.\.venv314\Scripts\python.exe tools\validate_capex_demo_v2_workflow.py
```

## Azure deployment and execution

Earlier acceptance used GPT-5.1 and Blob writes. The November 4 workstation
checks cannot access Blob/SQL directly or start the private-network job.
An authorized operator must run these steps.

### SQL contact handoff

Connect to `cict-ai-cus-sql.database.windows.net`, database
`cict-ai-cus-sqldb`, from an approved private-network path. These scripts can
be opened in SSMS or the VS Code SQL editor; no SQLCMD substitution mode is
required.

#### Letting the presenter run the SQL scripts

The SQL administrator can run
[grant_capex_demo_operator_access.sql](../../sql/admin/grant_capex_demo_operator_access.sql)
connected directly to `cict-ai-cus-sqldb`. It targets the presenter's Circle K
Entra account directly. The administrator must add that contained database
user first and grant the listed permissions.

The script follows the approved colleague-style schema-control pattern:

- Creates `prep`, `task`, `capex`, `review`, `ops`, and `ref` schemas
  from the shared data model, plus `pub` for the published views required by
  the repository.
- Grants CONTROL on those seven schemas to the human operator.
- Grants database CREATE TABLE, VIEW, PROCEDURE, FUNCTION, TYPE, and SYNONYM
  permissions, with schema control providing the target-schema access.

It contains only schema creation and the same schema-control/object-creation
grants as the colleague's example, with no existence checks. Skip CREATE
SCHEMA statements for schemas that already exist; execute schema creation
statements individually in the SQL editor. It does not create users, grant CONNECT
with grant option, or grant visibility of the runtime principal. The DBA
must separately handle any additional principal visibility or CONNECT
delegation needed to run migration 008, or execute 008 directly.

This supports schema work, inventory, rollback verification, and readiness
queries. Creating schemas does not create their business
tables or insert data. Review/ops table implementations are not added by this
access script. No database-owner or user-administration role is added.

**This is elevated schema-administration access, including changing/deleting
data and objects and delegating permissions, not read-only access.** Shared
`dbo` schema ownership can also permit wider access through ownership
chaining. The DBA must approve this level of trust and review/remove the
temporary deployment and delegation privileges after the demo while
preserving the runtime grants from 008. If that access is not acceptable,
the DBA should execute the migrations directly instead.

SQL grants do not enable the private-network connection or grant permission
to start Azure jobs. Reconnect using Entra authentication after provisioning.
This access script has repository contract checks but has not been
executed against live SQL from this workstation.

#### Execution order

For the complete v2.1 model inventory, apply
[009_complete_capex_model_tables.sql](../../sql/migrations/009_complete_capex_model_tables.sql)
after the earlier migrations and run
[validate_capex_model_schema.sql](../../sql/validation/validate_capex_model_schema.sql).
This creates all 16 missing modeled tables and adds missing names to the
seven existing ones without changing runtime IDs. It needs no completed
workflow ID. See [the SQL model-alignment notes](../../sql/README.md) for
type/key differences, nullable fields, aliases, and deferred relationships;
column coverage is not exact physical-model equivalence.

1. Run [validate_capex_demo_v2_inventory.sql](../../sql/validation/validate_capex_demo_v2_inventory.sql)
   and return its result sets. This is read-only and determines what is
   actually missing; current live table contents are not yet verified.
2. Apply [007_create_capex_assessment_store.sql](../../sql/migrations/007_create_capex_assessment_store.sql).
   It creates the four missing V2 tables if absent:
   `capex.AssessmentRun`, `capex.Assessment`, `capex.BundleCandidateGroup`,
   and `capex.BundleCandidateMember`, and creates/updates the read-only
   `pub.v1_Assessment` view. It does not insert invoice or demo result data.
3. If the inventory shows that `task.TaskSuggestion` or `pub.v1_TaskMaster`
   is missing, apply the corresponding prerequisite migrations
   [004](../../sql/migrations/004_create_task_suggestion_table.sql),
   [005](../../sql/migrations/005_create_task_master.sql), and
   [006](../../sql/migrations/006_align_task_suggestion.sql) in order as
   needed. Creating TaskMaster does not load its governed catalog; use the
   approved reference seed, never invented task rows.
4. Apply [008_grant_capex_demo_v2_access.sql](../../sql/migrations/008_grant_capex_demo_v2_access.sql).
   It grants object-level SELECT/INSERT to the existing approved runtime
   user, plus read access to the published views. It adds no UPDATE, DELETE,
   DDL, database-wide writer, or owner permissions and does not remove any
   existing shared-identity permissions.
   If the contained user is missing, an authorized Entra database
   administrator must first verify the approved managed identity and run:

   ```sql
   CREATE USER [cict-ai-cus-capex-id] FROM EXTERNAL PROVIDER;
   ```

5. Run [003_verify_capex_assessment_store.sql](../../sql/tests/003_verify_capex_assessment_store.sql).
   This exercises the table constraints using a rolled-back test transaction.
6. After the application runs **both** modes with separate, new workflow IDs,
   run [validate_capex_demo_v2_readiness.sql](../../sql/validation/validate_capex_demo_v2_readiness.sql)
   twice. Edit `@WorkflowRunId` to the completed ID and set `@Synthetic` to
   `0` for source/current-policy evidence or `1` for the visibly synthetic
   demo. Return all result sets and any errors.

The readiness query checks all three normalized package IDs individually:
Bruegmann 12 lines, Kimley-Horn 10, JC invoice 20294 one line. It reconciles
23 task determinations with 23 assessments, validates governed task codes,
checks the selected accounting baseline, and verifies positive-value bundles
and their exact membership. It throws on missing/inconsistent evidence;
empty tables cannot pass. It is tailored to the committed normalized inputs
and corrected deterministic baseline, not arbitrary future invoice batches.
A fresh model run that differs must be investigated, not forced to pass.

These new SQL scripts have repository contract checks but have not been
executed against live SQL from this workstation. The SQL contact must verify
execution and runtime permissions on the private network.

**Creating tables is only the schema step.** The application still must be
built/deployed and executed to populate them. SQL permissions do not grant
Azure `Microsoft.App/jobs/start/action`, which remains an operator/PIM
issue. V2 also does not populate the V1 preparation tables.

### Copy-ready operator request

> Subject: CapEx Demo V2 deployment permission and SQL execution
>
> Hi Ehsan,
>
> The CapEx Demo V2 code and three-invoice acceptance validation are complete.
> The remaining step is to deploy one manual Container Apps Job into the
> existing `cict-aisandbox-cus-rg` environment and apply additive SQL
> migration 007.
>
> My Circle K account can invoke GPT-5.1 and write the demo Blob artifacts,
> but ARM validation currently fails because it does not have
> `Microsoft.Resources/deployments/validate/action` or
> `Microsoft.Resources/deployments/whatIf/action`. Azure SQL is also not
> reachable from my current network path.
>
> Could you either run the documented deployment steps below, or temporarily
> grant Contributor at the resource-group scope to my account? The CLI command
> for the latter is:
>
> ```powershell
> az role assignment create `
>   --assignee-object-id <RACHAEL-OBJECT-ID> `
>   --assignee-principal-type User `
>   --role Contributor `
>   --scope /subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourceGroups/cict-aisandbox-cus-rg
> ```
>
> Please also run migration
> `sql/migrations/007_create_capex_assessment_store.sql` from a network path
> that can reach `cict-ai-cus-sql.database.windows.net`, and confirm that
> managed identity `cict-ai-cus-capex-id` receives the object-level grants
> in `sql/migrations/008_grant_capex_demo_v2_access.sql`. Please return
> the inventory results, then run
> `sql/validation/validate_capex_demo_v2_readiness.sql` after each completed
> application run using its workflow ID and demo mode.
>
> The UAMI already has the required `AcrPull`,
> `Storage Blob Data Contributor`, and
> `Cognitive Services OpenAI User` assignments.
>
> Thank you.

### 1. Build the immutable image

```powershell
az acr build `
  --registry cictaicusacr `
  --image capex/demo-v2:20261104.1 `
  --file Dockerfile.capex-v2 `
  .
```

### 2. Apply the additive SQL migration

From a network path that can reach Azure SQL:

```powershell
sqlcmd -S cict-ai-cus-sql.database.windows.net `
  -d cict-ai-cus-sqldb `
  -G `
  -b `
  -i sql\migrations\007_create_capex_assessment_store.sql
```

Run the rollback-based verification:

```powershell
sqlcmd -S cict-ai-cus-sql.database.windows.net `
  -d cict-ai-cus-sqldb `
  -G `
  -b `
  -i sql\tests\003_verify_capex_assessment_store.sql
```

### 3. Stage immutable Blob inputs

Upload the three normalized artifacts and four reference artifacts under:

```text
landing/capex/demo-v2/normalized/
landing/capex/demo-v2/reference/
```

Use `az storage blob upload --auth-mode login --overwrite false` for each
artifact. Then build a Blob-backed request:

```powershell
.\.venv314\Scripts\python.exe tools\build_capex_demo_v2_request.py `
  --workflow-run-id capex-demo-v2-synthetic-20261104 `
  --artifact-base-uri https://cictaicusst.blob.core.windows.net/landing/capex/demo-v2/normalized `
  --synthetic `
  --persist-results `
  --output .session\synthetic-20261104.json
```

Upload that request to:

```text
landing/capex/demo-v2/requests/synthetic-20261104.json
```

### 4. Validate and deploy the job

```powershell
az deployment group validate `
  --resource-group cict-aisandbox-cus-rg `
  --template-file infra\container-apps\capex-demo-v2-job.bicep `
  --parameters infra\container-apps\capex-demo-v2-job.parameters.json

az deployment group what-if `
  --resource-group cict-aisandbox-cus-rg `
  --template-file infra\container-apps\capex-demo-v2-job.bicep `
  --parameters infra\container-apps\capex-demo-v2-job.parameters.json

az deployment group create `
  --name capex-demo-v2-job `
  --resource-group cict-aisandbox-cus-rg `
  --template-file infra\container-apps\capex-demo-v2-job.bicep `
  --parameters infra\container-apps\capex-demo-v2-job.parameters.json
```

What-if must report no deletes before deployment.

### 5. Start and observe

```powershell
$execution = az containerapp job start `
  --name capex-demo-v2 `
  --resource-group cict-aisandbox-cus-rg `
  --query name -o tsv

az containerapp job execution show `
  --name capex-demo-v2 `
  --resource-group cict-aisandbox-cus-rg `
  --job-execution-name $execution `
  --output json
```

Retrieve the result blob and reconcile it with:

```sql
SELECT * FROM capex.AssessmentRun WHERE workflowRunId = '<workflow-run-id>';
SELECT COUNT(*) FROM pub.v1_Assessment WHERE workflowRunId = '<workflow-run-id>';
SELECT COUNT(*) FROM capex.BundleCandidateGroup WHERE workflowRunId = '<workflow-run-id>';
SELECT COUNT(*) FROM capex.BundleCandidateMember WHERE workflowRunId = '<workflow-run-id>';
```

Expected synthetic counts are 23 assessments, 1 group, and 9 members.
Use a new workflow ID and result blob for the corrected bundle behavior;
do not overwrite or replay a previously persisted two-group result under
the same workflow ID.

## Ten-minute presenter script

| Time | Show | Say |
|---:|---|---|
| 0:00-1:00 | Architecture | “This is a finite Container Apps batch. Blob holds governed inputs and evidence; SQL holds immutable results; GPT only interprets task codes.” |
| 1:00-2:00 | Three source packages | “We preserve source values and hashes. Missing values remain missing.” |
| 2:00-4:00 | Truthful result | “With policy still unapproved, all 23 lines correctly remain Review. The system does not fabricate CapEx/OpEx policy.” |
| 4:00-6:00 | Synthetic register | “For demonstration only, each assumption is explicit, opt-in, unapproved, and tied to a Circle K replacement question.” |
| 6:00-8:00 | Synthetic result | “The demo produces 7 CapEx, 11 OpEx, 5 Review, and one within-invoice bundle candidate of USD 455.56; it does not reach the threshold.” |
| 8:00-9:00 | Replay and persistence | “The workflow validates governed task codes, reconciles every group, and makes exact replay a no-op.” |
| 9:00-10:00 | Close | “Once Circle K supplies the requested policy and source data, we replace assumptions rather than changing the architecture.” |

## Circle K replacement-data request

Ask for:

1. The approved CapEx-by-nature, OpEx-by-nature, never-capitalize, and
   repair/maintenance rule library, including scope, priority, effective
   dates, and policy citations.
2. Approved treatment and exceptions for TaskMaster `5225 WIRE SHELVING`.
3. Approved treatment and directly attributable cost criteria for TaskMaster
   `0007 ENVIRONMENTAL`.
4. Whether exactly USD 1,000 meets the threshold.
5. Whether the threshold uses unit cost, line amount, asset total, invoice
   total, or project aggregate.
6. Whether the USD 300/site and USD 30,000/project exception is enabled, plus
   the complete project aggregate source and period.
7. Authoritative Kimley-Horn quantity/unit-cost values.
8. Project ID, site ID, project status, bundling scope, and approved status
   values for all three invoices.
9. The governed JC-to-TaskMaster mapping or applicable regional TaskMaster.
10. The approved non-USD exchange-rate source and effective-date rule.
11. The historical bundling source and lookback period.
12. Expected finance outcomes for the three demo invoices.
13. Approved handling of shipping, freight, installation, labor,
    reimbursables, repair, and replacement costs.

## Jira-ready close-outs

### CICT-238

Implemented deterministic candidate grouping across invoices, vendors, and
purchase orders with stable group IDs, reconciliation, missing-context
safeguards, and human-final task override support. Truthful result has no
groups; corrected synthetic result has 1 group and 9 members. The source
invoices do not demonstrate cross-invoice grouping.

### CICT-239

Implemented append-only assessment and bundle persistence with one atomic
transaction, canonical replay hashing, no-op exact replay, and rejection of a
changed replay. Added migration 007 and rollback-based SQL verification.

### CICT-240

Implemented the direct finite batch, local/Blob loaders, source verification,
governed task stage, deterministic capitalization and bundling, task and
assessment persistence adapters, result output, exit codes, non-root image,
manual Container Apps Job Bicep, and request builder. Source is complete;
live Azure deployment awaits operator ARM permission and SQL network access.

### CICT-241

Validated all three packages under one workflow ID: 23 lines, 23 task
determinations, 23 assessments, 0 invalid task codes, reconciled persistence
counts, and 0 additional inserts on exact replay. Live GPT-5.1 CLI runs
completed for both truthful and synthetic modes.

### CICT-242

**Status: In progress. Do not close yet.**

The operator runbook, presenter script, commands, expected results, fallback
evidence, and data-request checklist are prepared. Closure additionally
requires presenter training, a complete SQL inventory, review of all
synthetic assumptions with their Circle K replacement requests, and at least
one timed rehearsal plus a question-and-answer rehearsal. Use
`docs/capex/demo-v2-presenter-walkthrough.md` as the training guide.
