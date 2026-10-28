# CapEx CICT-42 Deployment Plan

## Status

Validated

## Objective

Validate and close the deployed CapEx runtime through durable InvoiceReady
publication. TaskSuggestion consumption, Foundry invocation, and persistence
are explicitly deferred to the next increment.

## Deployment Mode

MODIFY an existing private non-production Azure environment. Validate the
resources deployed during CICT-35 through CICT-41. Do not replace healthy
resources or perform broad infrastructure changes.

## Azure Context

- Tenant: 19da1f3c-d958-44ea-850d-195c1502525c
- Subscription: NA AI Dev/Test
- Subscription ID: 0451df15-250e-42f5-b15c-f19a9bb9c21a
- Resource group: cict-aisandbox-cus-rg
- Region: Central US

The operator must reconfirm this context before any Azure write operation.

## Runtime Boundary

Approved ABBYY ZIP
-> Blob landing/capex/input
-> Container Apps preparation job
-> Azure SQL package and pub.v1_InvoicePackage
-> transactional InvoiceOutbox
-> Container Apps dispatcher job
-> Service Bus capex-invoice-ready-v1

## Existing Resources

- Storage account: cictaicusst
- Blob container: landing
- Container Apps environment: cict-ai-cus-acaenv
- Preparation job: capex-invoice-preparation
- Dispatcher job: capex-invoice-ready-dispatch
- ACR: cictaicusacr.azurecr.io
- ACR pool: fuelbuildpool
- SQL server: cict-ai-cus-sql.database.windows.net
- SQL database: cict-ai-cus-sqldb
- Service Bus namespace: cict-ai-cus-bus
- Topic: capex-invoice-ready-v1
- UAMI: cict-ai-cus-capex-id
- UAMI client ID: 562512ef-d515-428a-8df5-6c4c589812ef
- UAMI principal ID: cf7d7929-5beb-426b-bd19-493c5c0c506d

## Immutable Images

Preparation:

- cictaicusacr.azurecr.io/capex/invoice-runtime:20261021.4
- sha256:502cd19c969b0d3076f042938777ed25652363cbd9317b848aadb44ece604bb8

Dispatcher:

- cictaicusacr.azurecr.io/capex/invoice-runtime:20261021.5
- sha256:893428ea9f5b8242775bdef60fc249c04cab684bd59534b69c60a5573b8cf340

The distinct tags are intentional. Version 20261021.5 contains a
dispatcher-only transaction correction and does not invalidate the proven
preparation execution.

## Validation

1. Confirm branch dev_rachael_phillips is clean and synchronized with origin.
2. Run all discovered tests/test_capex*.py files; expect 119 passing tests.
3. Compile all committed CapEx Bicep files.
4. Confirm generated ARM JSON agrees with its Bicep source.
5. Confirm no passwords, keys, SAS tokens, or connection strings are used.
6. Confirm both jobs use cict-ai-cus-capex-id.
7. Confirm Blob, SQL, and Service Bus roles are narrowly scoped.
8. Confirm preparation uses Blob input, output, and quarantine prefixes.
9. Confirm dispatcher command is capex-dispatch and image is 20261021.5.
10. Confirm Service Bus topic is active with PT1H duplicate detection.
11. Confirm ACR pool count is zero and provisioning state is Succeeded.
12. Confirm SQL package, published view, and outbox evidence remain durable.
13. Confirm approved Blob input and generated outputs remain present.

## Existing Live Evidence

- Preparation execution: capex-invoice-preparation-w35kbzw
- Run ID: run-20261022T031623Z-eee6efc5
- Package ID: pkg-2a26689e-5097-5cfe-b18c-10d34d75c0da
- Invoice ID: 135d03ac-3598-498a-83fe-d273d046bfea
- Event ID: evt-06d9fc90-73ce-5e2f-ac87-32b9fbc72841
- Correlation ID: corr-82167cbd-60d4-5c1d-b854-f6e335fd7b6a
- Dispatcher execution: capex-invoice-ready-dispatch-64nugpm
- Outbox status: PUBLISHED
- Attempt count: 1
- Published at: 2026-10-22T10:04:40.909Z

Do not rerun preparation or dispatch merely to recreate existing evidence.

## Deployment Strategy

The default action is validation only. If no drift is found, do not redeploy
healthy resources.

If drift is found:

1. Report the exact drift before changing Azure.
2. Apply only the smallest approved update.
3. Do not delete or replace SQL, Storage, Service Bus, ACR, identities, private
   endpoints, or the Container Apps environment.
4. Revalidate the affected component.
5. Preserve immutable images and existing demo data.

Any destructive or broad-scope change requires explicit user approval.

## Rollback

Restore the previously verified immutable image or configuration for the
affected job. Preserve Blob data, SQL records, outbox history, and failure
evidence. Do not delete published events.

## Success Criteria

CICT-42 is complete when:

- CapEx tests pass.
- Bicep compilation passes.
- Runtime configuration matches committed infrastructure.
- Managed identity and RBAC remain least privilege.
- Blob, SQL, outbox, and Service Bus evidence remain durable.
- The demo is traceable through the approved identifiers.
- Configuration and logs contain no secrets or invoice payloads.
- Documentation accurately states the boundary at InvoiceReady.
- No downstream TaskSuggestion claim is made.

## Deferred TaskSuggestion Increment

After CICT-42:

1. Create or confirm the Service Bus consumer subscription.
2. Deploy an idempotent invocation Function.
3. Configure the Foundry Task Determination Agent.
4. Implement transactional and idempotent TaskSuggestion persistence.
5. Prove InvoiceReady to persisted TaskSuggestion end to end.


## Bicep Recipe Validation Checklist

- [x] Azure CLI is installed and authenticated to the approved subscription.
- [x] Azure context matches the approved tenant, subscription, and resource group.
- [x] Every committed CapEx Bicep template compiles successfully.
- [x] Application tests and build verification pass.
- [x] Static managed-identity and RBAC verification passes.
- [x] Existing Container Apps jobs match their approved immutable images.
- [x] Service Bus topic is active with duplicate detection enabled.
- [x] ACR build pool is scaled to zero.
- [x] Azure Policy assignments are recorded.
- [x] Validation commands and results are recorded as proof.
- [x] No unresolved validation errors remain.

## Role Assignment Verification

- Status: Verified
- Identity: cict-ai-cus-capex-id
- ACR role: AcrPull scoped to cictaicusacr.
- Blob role: Storage Blob Data Contributor scoped to landing.
- Service Bus sender role: Azure Service Bus Data Sender scoped to capex-invoice-ready-v1.
- Service Bus receiver role: Azure Service Bus Data Receiver scoped to capex-invoice-ready-v1 for the deferred consumer increment.
- SQL access: contained-database identity permissions, previously proven by durable preparation and dispatcher executions.
- Source correction: added the existing live Service Bus Data Sender assignment to capex-managed-identity-roles.bicep.
- Issues remaining: none.

## Validation Proof

Validation date: 2026-10-22T10:50:16Z

- Azure context matched tenant 19da1f3c-d958-44ea-850d-195c1502525c and subscription 0451df15-250e-42f5-b15c-f19a9bb9c21a.
- Branch: dev_rachael_phillips.
- Twelve CapEx test modules passed, containing 119 tests.
- Existing Pydantic json_encoders deprecation warning remains non-blocking.
- Five CapEx Bicep templates compiled successfully.
- Corrected RBAC Bicep compiled and linted successfully.
- Preparation image matched immutable tag 20261021.4.
- Dispatcher image matched immutable tag 20261021.5.
- Both jobs used cict-ai-cus-capex-id.
- Neither job contained secretRef-backed environment variables.
- Service Bus topic was Active.
- Duplicate detection was enabled with a PT1H history window.
- Live sender, receiver, Blob contributor, and AcrPull roles were verified at narrow resource scopes.
- ACR pool count was zero with provisioning state Succeeded.
- Existing Blob, SQL package, published view, outbox, and Service Bus publication evidence remained authoritative.
- No Azure deployment was required because live runtime configuration already matched the validated target.
- Validation result: PASS.
