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
- Bundle groups: 2.
- Bundle members: 11.
- Exact SQL-adapter replay: 0 additional inserts.
- Invalid governed task codes: 0.

The deterministic evidence file is
`examples/data/capex/results-v2/workflow.synthetic-validation.json`.

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

The current user can invoke GPT-5.1 and write Blob data, but cannot validate
or deploy ARM templates. An authorized operator must run these steps.

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
> managed identity `cict-ai-cus-capex-id` has `SELECT` on
> `pub.v1_TaskMaster`, plus `SELECT` and `INSERT` on the task and CapEx
> persistence tables.
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

Expected synthetic counts are 23 assessments, 2 groups, and 11 members.

## Ten-minute presenter script

| Time | Show | Say |
|---:|---|---|
| 0:00-1:00 | Architecture | “This is a finite Container Apps batch. Blob holds governed inputs and evidence; SQL holds immutable results; GPT only interprets task codes.” |
| 1:00-2:00 | Three source packages | “We preserve source values and hashes. Missing values remain missing.” |
| 2:00-4:00 | Truthful result | “With policy still unapproved, all 23 lines correctly remain Review. The system does not fabricate CapEx/OpEx policy.” |
| 4:00-6:00 | Synthetic register | “For demonstration only, each assumption is explicit, opt-in, unapproved, and tied to a Circle K replacement question.” |
| 6:00-8:00 | Synthetic result | “The demo produces 7 CapEx, 11 OpEx, 5 Review, and 2 cross-invoice bundle groups.” |
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
groups; synthetic result has 2 groups and 11 members.

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
