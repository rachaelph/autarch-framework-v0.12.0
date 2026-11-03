# CapEx Demo V2 Client Runbook

## What this workflow does

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

Prepared the operator runbook, ten-minute presenter script, exact local and
Azure commands, expected results, fallback evidence, data-request checklist,
and Jira close-outs.
