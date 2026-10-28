# CapEx InvoiceReady Service Bus publishing

## Purpose

The CapEx preparation workflow persists an `InvoicePackage v1` and a pointer-only
`InvoiceReady v1` event in one database transaction. The outbox dispatcher then
publishes that durable event to Azure Service Bus.

This prevents a successfully prepared invoice package from being lost when a
broker call fails and prevents publishing before the database transaction commits.

## Azure resources

The workflow reuses these existing resources in `cict-aisandbox-cus-rg`:

- Service Bus Premium namespace: `cict-ai-cus-bus`
- Container Apps environment: `cict-ai-cus-acaenv`
- Invoice-ready topic: `capex-invoice-ready-v1`

The topic was deployed on October 19, 2026 with:

- duplicate detection enabled;
- a one-hour duplicate-detection window;
- a fourteen-day default message TTL;
- message ordering enabled;
- a 1 MB maximum message size;
- a 1 GB topic capacity.

The CapEx deployment creates only the topic. It does not modify the shared
namespace or create a consumer subscription. Consumer subscriptions will be
added only when an approved consumer and its delivery requirements are known.

## Message mapping

The publisher validates `InvoiceReady v1` before sending it and maps:

- `eventId` to the Service Bus message ID;
- `correlationId` to the correlation ID;
- `eventType` to the message subject;
- `schemaVersion`, `eventType`, `packageId`, and `runId` to application properties;
- the complete pointer-only event to the JSON body.

A deterministic message ID lets Service Bus duplicate detection suppress a
repeated send when the broker accepted a message but the dispatcher could not
finalize its outbox row.

## Authentication and authorization

Runtime publishing uses Microsoft Entra authentication through
`DefaultAzureCredential`. The application does not use connection strings,
SAS keys, or namespace access keys.

The managed identity of the workload running the outbox dispatcher requires
`Azure Service Bus Data Sender` at the `capex-invoice-ready-v1` topic or the
narrowest parent scope supported by Circle K standards.

Contributor is a management-plane role. It permits topic deployment but does
not grant the application permission to publish messages.

The workload and its managed identity must exist before the platform team can
create the data-plane role assignment.

## Private networking

The namespace has public network access disabled and uses a private endpoint.
The Container Apps workload running the dispatcher must have:

- routing to the private endpoint;
- DNS resolution through `privatelink.servicebus.windows.net`;
- outbound connectivity required by the selected Service Bus transport.

## Outbox dispatch behavior

The dispatcher:

1. recovers stale `PUBLISHING` claims;
2. claims eligible `PENDING` or retryable `FAILED` rows with SQL locking;
3. commits each claim before network I/O;
4. validates and publishes the event;
5. marks the row `PUBLISHED` after a successful send;
6. records bounded failure details and schedules exponential retry after errors.

If publishing succeeds but finalization fails, the same event is retried with
the same message ID, allowing broker-side duplicate detection to protect
against duplicate delivery.

## Deployment

The Bicep template is
`infra/service-bus/invoice-ready-service-bus.bicep`.

Use `az deployment group what-if` before `az deployment group create`.
The expected preview contains only the `capex-invoice-ready-v1` topic.

A live publish test remains dependent on deploying the publisher workload,
assigning `Azure Service Bus Data Sender` to its managed identity, and
confirming private connectivity from the Container Apps environment.
## Runtime jobs

The runtime image is stored in the private `cictaicusacr` registry as
`capex/invoice-runtime:20261020.1`. The validated image digest is
`sha256:9171ba96f2ed36227c0fbafb47a5b10fbc4b36c4c20754f0ca34e4df9311f871`.

Two Container Apps Jobs use this image:

- `capex-invoice-preparation` prepares packages and transactionally persists
  packages and outbox records to Azure SQL.
- `capex-invoice-ready-dispatch` drains a bounded batch of outbox records and
  publishes them to Service Bus.

Both jobs use an approved user-assigned managed identity and are initially deployed
with manual triggers. Scheduling must remain disabled until registry, database,
Service Bus, and private-network access are confirmed.

The approved user-assigned identity requires `AcrPull` plus its least-privilege Azure SQL
contained-user grants. For dispatch, the approved identity requires `AcrPull`, Azure SQL
`SELECT` and `UPDATE` access to `prep.InvoiceOutbox`, and topic-scoped
`Azure Service Bus Data Sender`.

No Service Bus Data Receiver assignment or consumer subscription is required
until an approved consumer exists.
