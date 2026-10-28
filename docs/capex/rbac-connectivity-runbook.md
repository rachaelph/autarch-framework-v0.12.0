# CICT-35 RBAC and Connectivity Runbook

## Purpose

Configure and prove least-privilege identity, RBAC, database permissions,
secret handling, and network access for the CapEx preparation job, outbox
publisher, Invoice Agent Invocation Function, and Foundry invocation.

## Important current-state finding

Both deployed Container Apps Jobs currently use:

```text
cict-ai-cus-capex-id
```

This identity currently has:

- `AcrPull` on `cictaicusacr`
- `Azure Service Bus Data Sender` on
  `cict-ai-cus-bus/topics/capex-invoice-ready-v1`
- `Storage Blob Data Contributor` on `cictaicusst/test`
- preparation and outbox object permissions inside Azure SQL

The confirmed CapEx Blob container is now `landing`, not `test`.

The current assignments were reverified from Azure on October 21, 2026:

| Role | Exact scope |
|---|---|
| `AcrPull` | `cictaicusacr` registry |
| `Azure Service Bus Data Sender` | `cict-ai-cus-bus/topics/capex-invoice-ready-v1` |
| `Storage Blob Data Contributor` | `cictaicusst/blobServices/default/containers/test` |

No Blob role assignment on `landing` was returned. This is the active RBAC
blocker for live preparation-job validation.

Read-only inventory on October 21, 2026 also confirmed:

- no Function App currently exists in `cict-aisandbox-cus-rg`;
- `capex-invoice-ready-v1` currently has no consumer subscription;
- the resource group contains only `cict-ai-cus-capex-id` and the unrelated
  `cict-ai-cus-fuel-id`; no dedicated CapEx dispatcher or Function UAMI
  exists;
- the Foundry invocation handler exists in source but has no caller;
- no implemented component currently persists `TaskSuggestion` objects to
  `task.TaskSuggestion`.

For the PoC, the delivery team has chosen to continue using
`cict-ai-cus-capex-id` for preparation, dispatch, and the future invocation
Function. Rachael Phillips confirmed on October 21, 2026 that this is the
approved identity for the complete CapEx demo use case; separate approval
from Ehsan is not required for the demo. Because the shared identity receives
the union of all permissions, runtime-specific access-denied tests cannot
fully prove isolation. Separate identities remain a pre-production hardening
recommendation rather than a demo blocker.

### Shared-UAMI exception record

| Field | Current value |
|---|---|
| Status | Approved for the CapEx demo |
| Approver | Rachael Phillips, CapEx demo delivery owner |
| Approval date | October 21, 2026 |
| Reason | Time-boxed customer PoC and demo |
| Known limitation | Preparation and dispatcher receive the union of the shared identity's permissions; runtime-specific isolation cannot be proven |
| Separation owner | CapEx delivery team; tracked by Rachael Phillips |
| Required milestone | Reassess and separate preparation, dispatcher, and Function identities before production deployment |

This approval applies to the CapEx demo only and removes the shared-UAMI
decision as a CICT-35 demo blocker.

## Proposed identity model

### Temporary PoC model

| Runtime | Proposed identity | Purpose |
|---|---|---|
| Preparation Job | `cict-ai-cus-capex-id` | Read/write approved Blob data and persist preparation state/outbox inserts |
| Outbox Dispatcher | `cict-ai-cus-capex-id` | Claim/update outbox rows and send Service Bus events |
| Invocation Function | `cict-ai-cus-capex-id` | Receive messages, read published packages, invoke Foundry |

Do not add Function-specific permissions until the Function, consumer entity,
package-reference mechanism, and Foundry target actually exist.

### Required pre-production model

Separate preparation, dispatcher, Function, and agent identities before
production. Migrate one runtime at a time, validate it, and remove the old
permission from the shared identity.

## Target Azure RBAC matrix

| Identity | Resource | Required role | Required scope | Prohibited |
|---|---|---|---|---|
| Preparation | ACR | `AcrPull` | `cictaicusacr` | Push/delete images |
| Preparation | Blob | `Storage Blob Data Contributor` | `cictaicusst/landing` | Access other containers |
| Preparation | Service Bus | Shared PoC identity already has Sender | Topic | Receiver remains deferred |
| Dispatcher | ACR | `AcrPull` | `cictaicusacr` | Push/delete images |
| Dispatcher | Service Bus | `Azure Service Bus Data Sender` | `capex-invoice-ready-v1` topic | Receive/manage |
| Dispatcher | Blob | Inherited through shared PoC identity | `landing` | Cannot prove dispatcher denial during exception |
| Function | Service Bus | `Azure Service Bus Data Receiver` when implemented | Final topic subscription or approved queue | Shared identity will also retain Sender during exception |
| Function | Blob | Existing Contributor may cover package read during exception | Exact `landing` container | Cannot prove write denial during exception |
| Function | Foundry | `Azure AI User` or approved inference role when implemented | Exact Foundry project/resource | Deploy/manage models |
| Function | ACR | `AcrPull` only if deployed from a custom container | Exact registry | Push/delete images |

Do not assign a Function Receiver role to the shared identity until:

1. the Function's actual UAMI is known;
2. the Service Bus topic subscription or queue is confirmed;
3. the queue-versus-topic architecture decision is recorded.

## Target SQL permission matrix

Azure RBAC on the SQL ARM resource does not grant table DML. Each identity
must be created as an Entra contained database user and receive object-level
permissions.

### Preparation identity

```sql
GRANT CONNECT TO [cict-ai-cus-capex-id];

GRANT SELECT, INSERT, UPDATE, DELETE
ON OBJECT::prep.InvoicePackage
TO [cict-ai-cus-capex-id];

GRANT INSERT
ON OBJECT::prep.InvoiceLine
TO [cict-ai-cus-capex-id];

GRANT INSERT
ON OBJECT::prep.InvoiceDataQualityWarning
TO [cict-ai-cus-capex-id];

GRANT INSERT
ON OBJECT::prep.InvoiceOutbox
TO [cict-ai-cus-capex-id];
```

The exact `InvoicePackage` permissions should be checked against retry and
idempotency SQL. Keep only the operations exercised by that code.

### Dispatcher identity

After its contained user is created:

```sql
GRANT CONNECT TO [cict-ai-cus-capex-dispatch-id];

GRANT SELECT, UPDATE
ON OBJECT::prep.InvoiceOutbox
TO [cict-ai-cus-capex-dispatch-id];
```

Confirm whether dispatcher SQL uses any additional operation before applying
the final grant.

### Invocation Function identity

If the published package is read from SQL:

```sql
GRANT CONNECT TO [cict-ai-cus-capex-function-id];

GRANT SELECT
ON OBJECT::pub.v1_InvoicePackage
TO [cict-ai-cus-capex-function-id];
```

Do not also grant Blob Reader unless the event contract points to Blob. Pick
one approved package-reference mechanism.

Do not assign `SQL DB Data Writer` through Azure RBAC. Any future result writer
must receive grants only on the final `task.*` result objects.

### Recorded SQL least-privilege result - October 21, 2026

A read-only recursive catalog audit evaluated the contained
`cict-ai-cus-capex-id` principal, its database-role membership, and direct or
role-inherited elevated permissions. It returned:

```text
leastPrivilegeStatus: PASS
conclusion: No elevated database, DDL, schema-alteration, ownership, or
            object-creation permission was found.
finding: NULL
```

The external UAMI could not be impersonated in Azure SQL. Catalog evaluation
was therefore used as the safe denial test instead of attempting a
destructive DDL operation. The result confirms that no prohibited elevated
permission was present.

The CICT-35 acceptance criteria define the Invocation Function's permissions
as Service Bus Receiver, published-package read, and Foundry invocation. The
Function therefore must not receive general SQL write access. If task
suggestions are persisted later, first identify the component and identity
that owns that write, then grant only the required operations on the specific
`task.*` objects inside the database.

### Recommended task-suggestion writer pattern

Keep `task.TaskSuggestion` append-only and expose a narrowly scoped stored
procedure such as `task.InsertTaskSuggestion`. The procedure must validate the
invoice line, run, task-master version, model/prompt versions, evidence,
confidence, and idempotency key before inserting.

Create a dedicated Entra contained user for the approved result-writer
identity, then grant:

```sql
GRANT CONNECT TO [approved-task-result-writer-id];

GRANT EXECUTE
ON OBJECT::task.InsertTaskSuggestion
TO [approved-task-result-writer-id];
```

Do not grant direct `UPDATE`, `DELETE`, schema-level DML, `db_datawriter`, or
`db_owner`. Consumers should read results from `pub.v1_TaskResult`, not the
base table.

If a stored procedure cannot be implemented for the PoC, the temporary
fallback is:

```sql
GRANT CONNECT TO [approved-task-result-writer-id];

GRANT INSERT
ON OBJECT::task.TaskSuggestion
TO [approved-task-result-writer-id];
```

This fallback still requires a stable unique constraint/idempotency key and
an explicit access-denied test for `UPDATE` and `DELETE`.

## Storage transition

The existing Blob assignment is on `test`. The confirmed container is
`landing`.

Use this safe order:

1. Add `Storage Blob Data Contributor` on `landing`.
2. Verify the assignment.
3. Update the preparation-job configuration to `landing`.
4. Run Blob read/write validation from the actual preparation job.
5. Remove the obsolete `test` assignment.

Do not remove `test` before the new configuration has passed.

## Service Bus requirements

Current verified role:

```text
cict-ai-cus-capex-id
  -> Azure Service Bus Data Sender
  -> capex-invoice-ready-v1 topic
```

Target:

```text
Dispatcher identity
  -> Data Sender
  -> capex-invoice-ready-v1

Function identity
  -> Data Receiver
  -> final consumer subscription or queue
```

A topic consumer requires a subscription. A Receiver role on a topic without
a consumer subscription does not produce a working Function trigger.

## Foundry permissions

Determine whether the Function invokes:

- a Foundry project agent;
- an Azure OpenAI model deployment directly;
- or another Cognitive Services endpoint.

Then assign only the matching role:

- `Azure AI User` at the exact Foundry project/resource for agent invocation;
  or
- the approved Cognitive Services/OpenAI inference role at the exact account
  for direct model inference.

Do not assign Contributor, model-deployment management, or resource-management
permissions to the runtime identity.

## Secret-validation checklist

The repository must not contain:

- account keys;
- SAS tokens;
- SQL passwords;
- Service Bus connection strings;
- model/API keys;
- client secrets;
- bearer tokens.

Use managed identity and resource endpoints/names in configuration.

Run repository-native secret scanning if configured. At minimum, inspect
tracked files:

```powershell
git grep -n -I -E `
  '(AccountKey=|SharedAccessKey=|SharedAccessSignature=|sig=|Password=|ClientSecret|api[_-]?key|Bearer [A-Za-z0-9._-]+)'
```

Review every match; do not silently ignore findings. Also verify generated
deployment outputs and workflow logs do not echo credentials.

### Recorded result - October 21, 2026

- Repository findings were placeholders, synthetic test values, or generic
  API-key support; no active credential was identified.
- Both deployed Container Apps Jobs had no configured secrets and no
  `secretRef` environment values.
- `azure_function/local.settings.json` contained placeholders only. It was
  removed from Git tracking, retained locally, and excluded with
  `/azure_function/local.settings.json` in `.gitignore`.
- The post-cleanup tracked-sensitive-filename scan returned no results.
- Repository cleanup commit: `c5f88fa`.

No credential rotation was required because no active secret was found.

## Network and connectivity evidence

Document for each dependency:

| Source runtime | Destination | Expected route | Required evidence |
|---|---|---|---|
| Preparation Job | ACR | Existing private/approved route | Successful image pull |
| Preparation Job | Blob | Private endpoint/DNS or approved PoC setting | Actual managed-identity list/read/write |
| Preparation Job | Azure SQL | Existing private endpoint/DNS | Successful managed-identity connection and transaction |
| Dispatcher | Azure SQL | Existing private endpoint/DNS | Successful outbox query/update |
| Dispatcher | Service Bus | Existing private endpoint/DNS | Successful send and metric/outbox evidence |
| Function | Service Bus | Existing private endpoint/DNS | Trigger receives controlled event |
| Function | Package source | Private endpoint/DNS or approved route | Successful read |
| Function | Foundry | Approved endpoint/network path | Successful controlled invocation |

Tests must run from the actual Azure runtime. A successful developer-laptop
connection is not sufficient evidence.

### Recorded ACR route - October 21, 2026

| Setting | Recorded value |
|---|---|
| Registry | `cictaicusacr.azurecr.io` |
| Public network access | `Disabled` |
| Default network action | `Deny` |
| Network-rule bypass | `AzureServices` |
| Admin user enabled | `true` |
| Runtime evidence | Both deployed jobs pulled and ran immutable image `20261020.2` using UAMI `AcrPull` |

This completes the PoC runtime evidence for the ACR route. The enabled ACR
admin account is not used by either deployed job and no registry credential
was present in their configuration. Treat disabling that account as a
separate hardening action after confirming that no unrelated workload still
depends on it; do not change it during demo preparation without that
inventory.

### Recorded Azure SQL route - October 21, 2026

| Setting | Recorded value |
|---|---|
| Server endpoint | `cict-ai-cus-sql.database.windows.net` |
| Public network access | `Disabled` |
| Minimum TLS version | `1.2` |
| Restrict outbound network access | `Disabled` |
| Private-endpoint connection | `pe-cict-ai-cus-sql-8acd7ea1-c868-4e11-b1d2-50804681f709` |
| Private endpoint | `pe-cict-ai-cus-sql` |
| Connection status | `Approved` |
| Private DNS zone group | `default` |
| Private DNS zone | `privatelink.database.windows.net` |
| Runtime evidence | Dispatcher execution `capex-invoice-ready-dispatch-5zfp76x` succeeded using managed identity and Azure SQL |

Because public network access is disabled, the approved private endpoint and
private DNS zone plus successful execution complete the SQL runtime route
evidence.

### Recorded Blob Storage settings - October 21, 2026

| Setting | Recorded value |
|---|---|
| Blob endpoint | `https://cictaicusst.blob.core.windows.net/` |
| Public network access | `Disabled` |
| Network default action | `Allow` |
| Network bypass | `None` |
| HTTPS only | `true` |
| Minimum TLS version | `TLS1_2` |
| Anonymous Blob access | `false` |
| Shared-key access | Not explicitly disabled (`null`) |
| Blob private endpoint | `pe-cictaicusst` (`Approved`) |
| DFS private endpoint | `pe-cictaicusst-dfs` (`Approved`) |
| Blob private DNS zone | `privatelink.blob.core.windows.net` |
| DFS private DNS zone | `privatelink.dfs.core.windows.net` |

Public network access being disabled takes precedence over the account's
`Allow` default action. The CapEx jobs are configured for managed identity,
not shared keys. Treat explicitly disabling shared-key access as a separate
hardening action after inventorying unrelated consumers; do not change it
during demo preparation. Both private-endpoint connections are approved and
use the expected Blob and DFS private DNS zones. Only the live
managed-identity `landing` read/write test remains, blocked on the missing
container-scoped role.

### Recorded Service Bus settings - October 21, 2026

| Setting | Recorded value |
|---|---|
| Namespace | `cict-ai-cus-bus` |
| Endpoint | `https://cict-ai-cus-bus.servicebus.windows.net:443/` |
| SKU | `Premium` |
| Status | `Active` |
| Provisioning state | `Succeeded` |
| Public network access | `Disabled` |
| Minimum TLS version | `1.2` |
| Zone redundant | `true` |
| Local/SAS authentication disabled | `false` |
| Private endpoint | `pe-cict-ai-cus-bus` |
| Private-endpoint connection | `a6f0e2cd-823e-4fc7-9a0d-08ef080e952b` |
| Connection status | `Approved` |
| Private DNS zone group | `default` |
| Private DNS zone | `privatelink.servicebus.windows.net` |

The dispatcher uses managed identity and a topic-scoped Sender role, not a
connection string. Treat disabling local/SAS authentication as a separate
hardening action after inventorying unrelated namespace consumers; do not
change it during demo preparation. The approved endpoint and private DNS route
are recorded; a live controlled publication remains.

## Positive tests

### Preparation

- Pull image with UAMI.
- List/read controlled Blob input.
- Write controlled package output.
- Insert package, lines, warnings, and outbox event.

### Dispatcher

- Pull image with its own UAMI.
- Read and update outbox state.
- Send a controlled Service Bus event.

### Function

- Receive one controlled Service Bus message.
- Read the referenced published package.
- Invoke the approved Foundry agent/model.

## Required prohibited-action tests

Use safe test objects/messages only.

| Identity | Prohibited action that must fail |
|---|---|
| Preparation | Receive from Service Bus before Function permissions are added |
| Dispatcher | Receive from Service Bus before Function permissions are added |
| Dispatcher | Insert/update `prep.InvoicePackage` |
| Function | Insert/update preparation tables |
| Function | Deploy or modify a model |

The temporary shared identity prevents reliable denial tests for actions that
another shared runtime legitimately requires, including dispatcher send,
preparation Blob write, and preparation SQL access. Record these tests as
`Not testable under approved shared-identity PoC exception`, not as passed.

Record:

- UTC time;
- identity client/principal ID;
- resource and operation;
- expected denial;
- actual error code/message;
- correlation/request ID where available.

## Completion gates

CICT-35 is complete only when:

1. Each runtime's actual attached identity is recorded.
2. The approved shared CapEx demo UAMI and its combined-access limitation are
   recorded.
3. Azure RBAC matches the PoC target matrix.
4. SQL contained-user grants match the SQL matrix.
5. No repository or deployed-configuration secrets are found.
6. Positive ACR, Blob, SQL, and Service Bus tests pass from the actual
   runtimes.
7. Safe prohibited-operation evidence is recorded.
8. Private endpoint and DNS behavior is documented and evidenced.
9. The obsolete `test` Blob role is removed after `landing` succeeds.
10. Deferred Function, consumer, Foundry, and task-result-writer permissions
    remain unassigned until those components exist.
11. The final matrix and evidence links are recorded in this runbook.

## Outstanding platform request

The only current Azure RBAC request required to finish the deployed
preparation and dispatcher path is:

```text
Identity: cict-ai-cus-capex-id
Principal ID: cf7d7929-5beb-426b-bd19-493c5c0c506d
Role: Storage Blob Data Contributor
Scope:
/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a
/resourceGroups/cict-aisandbox-cus-rg
/providers/Microsoft.Storage/storageAccounts/cictaicusst
/blobServices/default/containers/landing
```

After the assignment is granted:

1. Verify the exact `landing` scope.
2. Build and deploy the approved immutable preparation image.
3. Run one approved sample through the actual preparation job.
4. Capture Blob input/output, SQL package/outbox, and job execution evidence.
5. Run the dispatcher and capture `PUBLISHED` plus Service Bus evidence.
6. Remove the obsolete Contributor assignment from `test`.

Do not request ARM-level `SQL DB Data Writer`. Azure SQL permissions remain
contained-database object grants.