# CICT-37 CapEx Customer Demo Runbook

> Related work item: CICT-37

## Purpose

Demonstrate the governed CapEx invoice workflow using approved North American
ABBYY samples, the existing Azure resources, managed identity, Azure SQL, the
transactional outbox, and Service Bus publication.

This runbook separates:

- the preferred live demonstration;
- evidence that can be prepared before the meeting;
- safe fallbacks when a downstream component is not ready.

Do not claim Function consumption, Foundry invocation, task-suggestion
persistence, POPA posting, production readiness, or European coverage unless
the corresponding live evidence has been captured.

## Verified Azure context

| Item | Value |
|---|---|
| Subscription | `NA AI Dev/Test` |
| Subscription ID | `0451df15-250e-42f5-b15c-f19a9bb9c21a` |
| Resource group | `cict-aisandbox-cus-rg` |
| Region | `Central US` |
| Managed identity | `cict-ai-cus-capex-id` |
| Managed identity client ID | `562512ef-d515-428a-8df5-6c4c589812ef` |
| Managed identity principal ID | `cf7d7929-5beb-426b-bd19-493c5c0c506d` |
| Storage account | `cictaicusst` |
| Blob container | `landing` |
| Preparation job | `capex-invoice-preparation` |
| Dispatcher job | `capex-invoice-ready-dispatch` |
| SQL database | `cict-ai-cus-sqldb` |
| Service Bus namespace | `cict-ai-cus-bus` |
| Service Bus topic | `capex-invoice-ready-v1` |
| Foundry account | `cict-ai-cus-foundry` |
| Foundry project | `proj-cict-ai` |

## Demo samples

### Bruegmann USA

| Item | Value |
|---|---|
| Invoice | `INV12-62027` |
| Location | Minnesota, US |
| Currency | USD |
| Lines | 12 |
| ABBYY transaction | `135d03ac-3598-498a-83fe-d273d046bfea` |
| ABBYY creation time | `2026-09-08T09:40:49.2180876Z` |

### Kimley-Horn

| Item | Value |
|---|---|
| Invoice | `36764068-GRP005` |
| Location | California, US |
| Currency | USD |
| Lines | 10 |
| ABBYY transaction | `58aceec5-f35a-48a7-8faa-2f1a195af203` |
| ABBYY creation time | `2026-09-08T09:40:49.5356142Z` |

Only use samples approved for the customer demonstration.

## Current implementation status

### Verified (as of 2026-10-22)

- Versioned `InvoicePackage v1` and `InvoiceReady v1` contracts.
- Deterministic ABBYY normalization and package validation.
- Azure SQL operational store and `pub.v1_InvoicePackage`.
- Transactional outbox.
- Finite Container Apps preparation and dispatcher jobs.
- Managed-identity SQL authentication.
- **Blob-enabled preparation job execution** (CICT-35).
- **Successful dispatcher execution with nested-transaction fix** (CICT-36).
- Service Bus publication with duplicate detection enabled (PT1H history).
- Service Bus Sender confirmed operational on topic.
- **All CapEx tests passing** (CICT-37, 119 tests).
- Preparation-job and RBAC Bicep compilation.

### Verified end-to-end boundary

**Live workflow validates through InvoiceReady publication:**
- Preparation: blob download → validate → normalize → SQL insert → succeed
- Outbox: transactional event creation → claim → publish → mark PUBLISHED
- Dispatcher: claim pending → publish to Service Bus → update outbox status

**Current scope:** validation ends at InvoiceReady publication to Service Bus.
**Not yet deployed:** consumer subscription, Function, Foundry invocation, TaskSuggestion persistence.

### Pending deployment (not yet live)

- Service Bus consumer subscription.
- Invoice Agent Invocation Function deployment.
- Foundry agent invocation.
- `task.TaskSuggestion` persistence and schema validation.
- End-to-end Function → SQL validation.

## Day-before preflight

Run from:

```text
C:\Users\cictadmin\Downloads\cict-service-capex
```

### 1. Confirm repository state

```powershell
git branch --show-current
git status --short
git diff --check
```

Expected branch:

```text
dev_rachael_phillips
```

Required commits (CICT-35/36/37 fixes):

```text
32207dd fix(capex): persist blob preparation to sql
c94ad06 fix(capex): avoid nested sql transaction
90a9c9e fix(capex): avoid nested dispatcher transactions
```

### 2. Run focused tests

```powershell
python -m pytest `
  tests\test_capex_blob_job.py `
  tests\test_capex_job.py `
  tests\test_capex_outbox_dispatcher.py `
  -q
```

Expected result:

```text
25 passed
```

**Full CapEx suite validation (CICT-37):**

```powershell
$CapExTests = @(
  Get-ChildItem -Path ".\tests\test_capex*.py" -File |
    Sort-Object Name |
    Select-Object -ExpandProperty FullName
)
python -m pytest @CapExTests -q
```

Expected: 119 passed. The Pydantic `json_encoders` deprecation warning is
non-blocking (v2.0→3.0 migration, no functional impact).

### 3. Compile Bicep

```powershell
$PreparationJson = Join-Path $env:TEMP 'capex-invoice-preparation-job.json'
$RbacJson = Join-Path $env:TEMP 'capex-managed-identity-roles.json'

az bicep build `
  --file 'infra\container-apps\capex-invoice-preparation-job.bicep' `
  --outfile $PreparationJson

az bicep build `
  --file 'infra\rbac\capex-managed-identity-roles.bicep' `
  --outfile $RbacJson
```

Both commands must exit successfully.

### 4. Confirm required role assignments

```powershell
$SubscriptionId = '0451df15-250e-42f5-b15c-f19a9bb9c21a'
$ResourceGroup = 'cict-aisandbox-cus-rg'
$PrincipalId = 'cf7d7929-5beb-426b-bd19-493c5c0c506d'

$LandingScope = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.Storage/storageAccounts/cictaicusst/blobServices/default/containers/landing"
$TopicScope = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.ServiceBus/namespaces/cict-ai-cus-bus/topics/capex-invoice-ready-v1"
$FoundryProjectScope = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.CognitiveServices/accounts/cict-ai-cus-foundry/projects/proj-cict-ai"

foreach ($Scope in @($LandingScope, $TopicScope, $FoundryProjectScope)) {
    az role assignment list `
      --assignee-object-id $PrincipalId `
      --scope $Scope `
      --include-inherited `
      --query "[].{role:roleDefinitionName,scope:scope}" `
      --output table
}
```

**Verified Blob authorization (as of 2026-10-22):**

- UAMI principal ID: `cf7d7929-5beb-426b-bd19-493c5c0c506d`
- Role: `Storage Blob Data Contributor`
- Scope: `/subscriptions/.../storageAccounts/cictaicusst/blobServices/default/containers/landing`

Required for later downstream components:

- `Azure Service Bus Data Receiver` on the topic or consumer subscription.
- `Azure AI User` on `proj-cict-ai`.

Do not request or claim Azure RBAC `SQL DB Data Writer`. SQL data access is
implemented through contained-database grants.

### 5. Confirm ACR agent pool is not left running

```powershell
az acr agentpool show `
  --registry cictaicusacr `
  --name fuelbuildpool `
  --query '{count:count,provisioningState:provisioningState,tier:tier}' `
  --output json
```

Expected idle state:

```text
count: 0
```

## Preferred live demonstration

### Step 1: Explain the flow

Use this short explanation:

> ABBYY produces a controlled source package. The preparation job reads the
> package, validates and normalizes the invoice into a versioned contract, and
> stores the invoice and its publication event transactionally in Azure SQL.
> The dispatcher publishes pending events to Service Bus. Downstream task
> determination and human review build on that governed package.

### Step 2: Show one approved source package

Show:

- ABBYY transaction ID;
- creation time;
- invoice number;
- supplier;
- currency;
- line count.

Do not expose credentials, unrelated invoices, or personal data.

### Step 3: Upload to the controlled landing prefix

Use Entra authentication from an approved workstation. Do not use account keys
or SAS tokens.

```powershell
$SamplePath = '<approved ZIP path>'
$BlobName = "capex/input/$([IO.Path]::GetFileName($SamplePath))"

az storage blob upload `
  --auth-mode login `
  --account-name cictaicusst `
  --container-name landing `
  --name $BlobName `
  --file $SamplePath `
  --overwrite false
```

### Step 4: Start the preparation job

```powershell
$PreparationExecution = az containerapp job start `
  --name capex-invoice-preparation `
  --resource-group cict-aisandbox-cus-rg `
  --query name `
  --output tsv

Write-Host "Preparation execution: $PreparationExecution"
```

Capture the execution name.

### Step 5: Verify Blob output

Expected prefixes:

```text
capex/input/
capex/output/<run-id>/
capex/quarantine/<run-id>/
```

```powershell
az storage blob list `
  --auth-mode login `
  --account-name cictaicusst `
  --container-name landing `
  --prefix 'capex/output/' `
  --query "[].{name:name,lastModified:properties.lastModified,size:properties.contentLength}" `
  --output table
```

Show the package JSON and `_run-summary.json`, but avoid displaying sensitive
fields that are not needed for the demonstration.

### Step 6: Show SQL publication evidence

Show:

- invoice header;
- line count;
- data-quality status;
- published package view;
- pending outbox event.

Use the existing read-only operator access. Do not alter data manually.

### Step 7: Start the dispatcher

```powershell
$DispatcherExecution = az containerapp job start `
  --name capex-invoice-ready-dispatch `
  --resource-group cict-aisandbox-cus-rg `
  --query name `
  --output tsv

Write-Host "Dispatcher execution: $DispatcherExecution"
```

### Step 8: Prove publication

Use:

- dispatcher success;
- outbox state `PUBLISHED`;
- Service Bus outgoing-message metric increase.

Do not claim message consumption if no consumer subscription exists.

## Safe fallback demonstration

Use this path if Blob RBAC, image deployment, networking, or a live Azure
component is not ready:

1. Show the architecture diagram.
2. Show the two approved samples and explain their controlled use.
3. Show `InvoicePackage v1` and `InvoiceReady v1`.
4. Run the focused preparation and Blob tests.
5. Show the preparation-job and RBAC Bicep.
6. Show the successful historical dispatcher execution.
7. Show SQL objects and the transactional outbox design.
8. Show current managed-identity role scopes.
9. Explain that the Function/Foundry consumer is the next increment.

Safe statement:

> The governed preparation, SQL publication, outbox, dispatcher, and managed
> identity foundations are implemented and tested. The remaining live-demo
> dependency is completing the Blob-enabled image deployment and validating
> access to the confirmed landing container.

## Evidence capture checklist

Captured evidence (2026-10-22):

- [x] Git commits:
  - `32207dd` fix(capex): persist blob preparation to sql
  - `c94ad06` fix(capex): avoid nested sql transaction
  - `90a9c9e` fix(capex): avoid nested dispatcher transactions
- [x] Full CapEx test suite: 119 passed (including new regression test)
- [x] Successful preparation-job Bicep compilation.
- [x] Successful RBAC Bicep compilation.
- [x] Blob authorization: Storage Blob Data Contributor at landing container
- [x] Managed identity principal ID: `cf7d7929-5beb-426b-bd19-493c5c0c506d`
- [x] ACR image built and deployed: `cictaicusacr.azurecr.io/capex/invoice-runtime:20261021.5`
- [x] Image digest: `sha256:893428ea9f5b8242775bdef60fc249c04cab684bd59534b69c60a5573b8cf340`
- [x] Preparation execution: `capex-invoice-preparation-w35kbzw`
- [x] Preparation run ID: `run-20261022T031623Z-eee6efc5`
- [x] Input blob: `capex/input/Bruegmann  - INV1262027.pdf-135d03ac-3598-498a-83fe-d273d046bfea.zip`
- [x] Package ID: `pkg-2a26689e-5097-5cfe-b18c-10d34d75c0da`
- [x] Output package: `capex/output/run-20261022T031623Z-eee6efc5/pkg-2a26689e-5097-5cfe-b18c-10d34d75c0da.json`
- [x] Run summary: `capex/output/run-20261022T031623Z-eee6efc5/_run-summary.json`
- [x] Invoice ID: `135d03ac-3598-498a-83fe-d273d046bfea`
- [x] Correlation ID: `corr-82167cbd-60d4-5c1d-b854-f6e335fd7b6a`
- [x] Event ID: `evt-06d9fc90-73ce-5e2f-ac87-32b9fbc72841`
- [x] Package status: READY, 12 invoice lines
- [x] Blob preparation output: zero quarantine
- [x] SQL package and outbox stored and visible
- [x] Dispatcher execution: `capex-invoice-ready-dispatch-64nugpm`
- [x] Dispatcher run ID: `63164f4c-13d0-4e55-8132-120a3180ac9d`
- [x] Outbox status: `PUBLISHED`
- [x] AttemptCount: 1
- [x] PublishedAtUtc: `2026-10-22T10:04:40.909Z`
- [x] LastError: NULL
- [x] Service Bus IncomingMessages metric increase recorded
- [x] Service Bus duplicate detection: enabled (PT1H history)
- [ ] Screenshot/customer-deck evidence

## Rollback and cleanup

- Keep Container Apps jobs in manual mode until end-to-end validation passes.
- Do not overwrite an existing image tag.
- If a new job revision fails, restore the previous known-good image.
- Keep the old `test` Blob role only until `landing` access succeeds; then
  remove it.
- Keep the ACR agent pool at count 0 except during an active build.
- Do not delete source blobs during the first demonstration implementation.
- Do not deploy the Service Bus Receiver or Foundry role through a broad
  subscription- or resource-group-scoped assignment.

## Demo completion criteria

The preferred demonstration is ready when:

- an approved source ZIP is present under `capex/input/`;
- the deployed preparation image starts `capex-prepare-blob`;
- preparation succeeds using the managed identity;
- output and summary files appear under `capex/output/<run-id>/`;
- SQL contains the package and outbox event;
- dispatcher succeeds;
- the outbox event reaches `PUBLISHED`;
- evidence is captured.

If those criteria are not all met, use the safe fallback and state the missing
dependency explicitly.
