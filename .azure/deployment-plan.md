# CapEx TaskSuggestion Demo Deployment Plan

## Plan status

- Status: Approved
- Approval required before deployment: Yes
- Deployment target: Circle K CICT Azure sandbox
- Planned deployment date: 2026-10-22
- Owner: CapEx demo team
- Change type: Additive database, messaging, RBAC, and Function deployment

No Azure resource changes may be made under this plan until its status is changed to `Approved`.

## Objective

Deploy and prove the customer-demo path:

`InvoiceReady -> Service Bus subscription -> Azure Function -> Microsoft Foundry model -> task.TaskSuggestion`

The deployment creates governed TaskMaster reference data, aligns durable line-level TaskSuggestion persistence, provisions the dedicated Service Bus consumer subscription, grants the existing CapEx managed identity model invocation access, deploys the Function infrastructure and code package, and validates an end-to-end invoice result.

## Source baseline

The deployment uses these pushed source increments:

- `9ea8627` - governed TaskMaster schema, published view, and deterministic US seed.
- `e7db342` - transactional, line-level TaskSuggestion persistence.
- `cd8a4a1` - managed-identity Foundry task determination.
- `c66dc72` - dedicated subscription and deployable Function package.
- `b737b0f` - Azure SQL-compatible TaskSuggestion constraint migration.

The deployment must use the current `dev_rachael_phillips` branch head containing these commits.

## Azure target

- Tenant ID: `19da1f3c-d958-44ea-850d-195c1502525c`
- Subscription: `NA AI Dev/Test`
- Subscription ID: `0451df15-250e-42f5-b15c-f19a9bb9c21a`
- Resource group: `cict-aisandbox-cus-rg`
- Region: `Central US`

### Existing resources

- Service Bus namespace: `cict-ai-cus-bus`
- InvoiceReady topic: `capex-invoice-ready-v1`
- Storage account: `cictaicusst`
- Blob container: `landing`
- SQL server: `cict-ai-cus-sql.database.windows.net`
- SQL database: `cict-ai-cus-sqldb`
- Foundry account: `cict-ai-cus-foundry`
- Foundry project: `proj-cict-ai`
- Foundry endpoint: `https://cict-ai-cus-foundry.openai.azure.com/`
- Model deployment: `gpt-6.1-sol`
- CapEx UAMI: `cict-ai-cus-capex-id`
- UAMI client ID: `562512ef-d515-428a-8df5-6c4c589812ef`
- UAMI principal ID: `cf7d7929-5beb-426b-bd19-493c5c0c506d`

### Resources introduced or changed

- Create `ref.TaskMaster`.
- Create `pub.v1_TaskMaster`.
- Load the governed 761-row US TaskMaster snapshot once.
- Align `task.TaskSuggestion` for line-level, replay-safe results.
- Create subscription `invoice-agent-invocation` under topic `capex-invoice-ready-v1`.
- Add `Cognitive Services OpenAI User` for the CapEx UAMI at the Foundry account scope.
- Create or update Function App `capex-invoice-agent-invocation`.
- Deploy the deterministic Function ZIP package.

## Governed data contract

The authoritative TaskMaster source is:

- Workbook: `examples/data/capex/Task_Type_Export_9_6_2026.xlsx`
- Worksheet: `US Task Type`
- Region: `US`
- Expected published rows: `761`
- Expected unique active task codes: `761`
- Source SHA-256: `CC41B492A5DE454EFD7405066EF1EAA6CDE1FF050C8E71ED180831197B6EBA13`

The workbook is imported once for this demo. Runtime Function invocations query the published SQL view; they do not reimport the workbook.

## Deployment artifacts

### SQL

Apply in this exact order:

1. `sql/migrations/005_create_task_master.sql`
2. `sql/data/005_seed_us_task_master.sql`
3. `sql/migrations/006_align_task_suggestion.sql`

All scripts must run with stop-on-error behavior. Do not continue after a failed SQL batch.

### Service Bus

Deploy:

- `infra/service-bus/invoice-ready-service-bus.bicep`

Expected subscription configuration:

- Name: `invoice-agent-invocation`
- Status: Active
- Lock duration: 5 minutes
- Maximum delivery count: 10
- Dead-letter on message expiration: Enabled
- Dead-letter on filter evaluation exception: Enabled
- Default message TTL: 14 days
- Sessions: Disabled

The existing topic duplicate-detection and ordering settings must remain unchanged.

### RBAC

Deploy:

- `infra/rbac/capex-managed-identity-roles.bicep`

Required model role:

- Role: `Cognitive Services OpenAI User`
- Role definition ID: `5e0bd9bd-7b93-4f28-af87-19fc36ad61bd`
- Principal: `cict-ai-cus-capex-id`
- Scope: `cict-ai-cus-foundry`

Existing Blob and Service Bus sender/receiver role declarations must remain intact.

### Function infrastructure

Deploy:

- `infra/function-apps/capex-invoice-agent-invocation.bicep`

Required runtime configuration:

- User-assigned identity: `cict-ai-cus-capex-id`
- Service Bus identity connection prefix: `ServiceBusConnection`
- Service Bus namespace: `cict-ai-cus-bus.servicebus.windows.net`
- Topic: `capex-invoice-ready-v1`
- Subscription: `invoice-agent-invocation`
- Blob account URL derived from the target Azure environment
- SQL hostname derived from the target Azure environment
- Foundry endpoint: `https://cict-ai-cus-foundry.openai.azure.com/`
- Foundry deployment: `gpt-6.1-sol`
- TaskMaster region: `US`
- TaskMaster SHA-256: governed workbook hash
- Prompt version: `task-determination-v1`
- No Foundry API key or secret setting

### Function code

Build with:

- `tools/build_capex_function_package.py`

Expected artifact:

- `dist/capex-invoice-agent-invocation.zip`

The package must contain:

- `function_app.py`
- `host.json`
- `requirements.txt`
- tracked `autarch` runtime source

The package must not contain:

- `local.settings.json`
- Python bytecode
- `__pycache__`
- credentials or API keys

Record the package SHA-256 before deployment.


## Validation workflow checklist

- [ ] Core validation: Azure CLI, authentication, Bicep build,
  ARM validation, and ARM what-if.
- [ ] Complete CapEx test suite passes.
- [ ] Function deployment package builds and passes content checks.
- [ ] Azure Policy impact is reviewed.
- [ ] Static managed-identity role assignments are verified.
- [ ] Validation commands and results are recorded as proof.
- [ ] All validation failures are resolved.
- [ ] Plan status is changed from Approved to Validated only after
  every check passes.

## Pre-deployment checks

Before changing Azure:

1. Confirm the active tenant and subscription exactly match this plan.
2. Confirm the working tree is clean and the branch contains all source-baseline commits.
3. Run all `test_capex*.py` modules successfully.
4. Compile the Service Bus, Function, and RBAC Bicep files.
5. Build the Function package and record its SHA-256.
6. Confirm the Foundry endpoint and `gpt-6.1-sol` deployment still exist.
7. Confirm the CapEx UAMI IDs match this plan.
8. Confirm the SQL database is reachable using approved Microsoft Entra authentication.
9. Capture current Service Bus topic settings.
10. Capture any existing Function App configuration and deployment state.
11. Run ARM what-if for each Bicep deployment.
12. Review what-if output and stop on destructive replacement or deletion.

## Deployment sequence

### Phase 1 - Database

1. Apply migration 005.
2. Verify `ref.TaskMaster` and `pub.v1_TaskMaster` exist.
3. Apply the one-time US TaskMaster seed.
4. Verify exactly 761 published US rows.
5. Verify exactly 761 distinct task codes.
6. Verify the published source hash matches the governed SHA-256.
7. Apply migration 006.
8. Verify the TaskSuggestion columns, constraints, and unique `(eventId, lineId)` replay index.

Stop immediately if any row count, uniqueness check, hash check, or schema check fails.

### Phase 2 - Messaging and RBAC

1. Deploy the Service Bus Bicep.
2. Verify subscription `invoice-agent-invocation` and its dead-letter/retry settings.
3. Deploy the RBAC Bicep.
4. Verify the CapEx UAMI has `Cognitive Services OpenAI User` at the Foundry account.
5. Allow normal Azure RBAC propagation time before model invocation testing.

### Phase 3 - Function infrastructure and code

1. Deploy the Function Bicep.
2. Verify the UAMI attachment.
3. Verify identity-based Service Bus settings.
4. Verify no Foundry API-key setting exists.
5. Deploy the recorded Function ZIP with remote dependency build enabled.
6. Wait for deployment completion.
7. Verify the Function host is healthy.
8. Verify the Service Bus trigger is discovered and listening.
9. Verify Application Insights receives startup telemetry without credential errors.

### Phase 4 - End-to-end proof

1. Use the approved Bruegmann demo invoice.
2. Produce or replay one governed `InvoiceReady` event.
3. Capture the event ID, package ID, run ID, invoice ID, and correlation ID.
4. Verify the dedicated subscription receives and completes the message.
5. Verify the Function loads the matching invoice package.
6. Verify the Function loads the governed TaskMaster catalog.
7. Verify the CapEx UAMI invokes `gpt-6.1-sol`.
8. Verify strict JSON is accepted only when every source line is represented exactly once.
9. Verify all returned task codes exist in `pub.v1_TaskMaster`.
10. Verify one `task.TaskSuggestion` row per invoice line.
11. Verify every line is `SUGGESTED` or `UNRESOLVED`.
12. Replay the same event.
13. Verify no duplicate logical rows are created.
14. Capture SQL and telemetry evidence for Jira closure.

## Acceptance criteria

The deployment is successful only when all conditions are true:

- `pub.v1_TaskMaster` returns 761 US rows and 761 distinct task codes.
- TaskMaster provenance matches the approved workbook SHA-256.
- Migration 006 is present and replay protection is enforced.
- Subscription `invoice-agent-invocation` is active with the planned settings.
- The CapEx UAMI has the Foundry model-user role.
- The Function uses the UAMI for Service Bus, Blob, SQL, and Foundry access.
- No Foundry API key exists in Function configuration.
- A real InvoiceReady event produces one durable result per invoice line.
- Every suggested task code is governed by `pub.v1_TaskMaster`.
- Replaying the same event does not create duplicate logical rows.
- Correlation IDs connect the event, Function execution, model invocation, and SQL rows.
- Dead-letter count remains zero for the successful demonstration.

## Rollback and containment

The preferred rollback is containment rather than destructive database reversal.

1. Disable Function `capex-invoice-agent-invocation`.
2. Disable subscription `invoice-agent-invocation`.
3. Preserve dead-lettered and active messages for diagnosis.
4. If this was an update, redeploy the previously captured Function package and configuration.
5. Remove the new Foundry role assignment only if access must be revoked.
6. Do not drop `ref.TaskMaster`, `pub.v1_TaskMaster`, or `task.TaskSuggestion` during incident response.
7. Do not delete seeded governance data or persisted evidence.
8. Correct the issue in source, rerun validation, and deploy through a revised approved plan.

Because migrations 005 and 006 are additive and the TaskMaster seed is governed evidence, database rollback is not performed automatically.

## Known risks and mitigations

- RBAC propagation delay:
  Deploy RBAC before the Function smoke test and retry only permission-denied failures for a bounded period.
- Model output contract failure:
  Reject malformed or incomplete output; do not create success-shaped fallback rows.
- Unsupported task code:
  Reject the response before persistence.
- Duplicate Service Bus delivery:
  Enforce unique `(eventId, lineId)` persistence.
- Partial SQL persistence:
  Persist all invoice lines in one transaction.
- Function dependency omission:
  Use and validate the deterministic package builder.
- Trigger authentication error:
  Use hierarchical identity-based Service Bus settings with the UAMI client ID.
- Demo message failure:
  Retain the message in the dead-letter queue and capture telemetry before retrying.
- Unexpected ARM replacement:
  Stop deployment if what-if shows deletion or replacement of shared resources.

## Evidence to retain

- Git commit hashes.
- Complete CapEx test result.
- Bicep compilation result.
- Function package SHA-256.
- ARM what-if outputs.
- SQL migration output.
- TaskMaster row-count, uniqueness, and source-hash query output.
- RBAC assignment output.
- Service Bus subscription configuration.
- Function deployment result.
- Function and Application Insights correlation logs.
- Foundry invocation result metadata.
- Final TaskSuggestion SQL rows.
- Replay/idempotency query output.

## Execution record

- Formal plan approval was recorded on 2026-10-22.
- The database phase was completed and validated before the remaining
  messaging, RBAC, and Function deployment phases.
- Live TaskMaster validation returned 761 published US rows and 761
  distinct task codes with the approved workbook SHA-256.
- TaskSuggestion schema validation confirmed five new columns, four
  trusted constraints, three required indexes, a unique event-line
  replay guard, and zero duplicate event-line groups.

## Approval

- Requested by: Rachael Phillips
- Reviewed by: Rachael Phillips
- Approved by: Rachael Phillips
- Approval date: 2026-10-22
- Status after approval: `Approved`
