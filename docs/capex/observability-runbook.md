# CICT-36 CapEx Observability and Diagnostics Runbook

> Related work item: CICT-36

## Purpose

Provide repeatable diagnostics for the CapEx preparation and InvoiceReady
dispatcher jobs using structured console logs, Container Apps execution state,
Azure SQL outbox state, and Service Bus metrics.

## Current scope

This runbook covers components that currently exist:

- `capex-invoice-preparation`
- `capex-invoice-ready-dispatch`
- Blob input, output, and quarantine activity
- Azure SQL package persistence and transactional outbox
- Service Bus publication to `capex-invoice-ready-v1`

It does not claim downstream message consumption or dead-letter processing.
There is currently no deployed Function consumer or Service Bus subscription.
Consumer retry and dead-letter diagnostics must be added when those components
exist.

## Correlation identifiers

| Identifier | Purpose |
|---|---|
| `runId` | Correlates one invoice preparation run and its Blob output prefix. |
| `packageId` | Identifies one normalized invoice package. |
| `invoiceId` | Identifies the source invoice within the normalized contract. |
| `correlationId` | Correlates InvoiceReady and downstream agent processing. |
| `dispatchRunId` | Correlates one finite dispatcher execution across start, completion, and failure logs. |
| `eventId` | Identifies an outbox/Service Bus event and supports duplicate detection. |

## Structured events

### Preparation

- `invoice_preparation_started`
- `invoice_preparation_succeeded`
- `invoice_preparation_failed`
- `invoice_preparation_completed`

### Blob staging

- `blob_job_started`
- `blob_downloaded`
- `blob_download_error`
- `blob_inputs_downloaded`
- `blob_output_uploaded`
- `blob_upload_error`
- `blob_summary_uploaded`
- `blob_summary_upload_error`
- `blob_quarantine_uploaded`
- `blob_quarantine_error`
- `blob_job_completed`
- `blob_preparation_error`

### Dispatcher

- `invoice_ready_dispatch_started`
- `invoice_ready_dispatch_completed`
- `invoice_ready_dispatch_configuration_error`
- `invoice_ready_dispatch_failed`

Every dispatcher terminal event must carry the same `dispatchRunId` as its
start event.

## Container Apps execution checks

List recent preparation executions:

```powershell
az containerapp job execution list `
  --name capex-invoice-preparation `
  --resource-group cict-aisandbox-cus-rg `
  --query "[].{name:name,status:properties.status,start:properties.startTime,end:properties.endTime}" `
  --output table
```

List recent dispatcher executions:

```powershell
az containerapp job execution list `
  --name capex-invoice-ready-dispatch `
  --resource-group cict-aisandbox-cus-rg `
  --query "[].{name:name,status:properties.status,start:properties.startTime,end:properties.endTime}" `
  --output table
```

Record the execution name for every demo or validation run.

### Recorded deployed baseline - October 21, 2026

The preparation job returned no execution history.

The dispatcher returned:

| Execution | Status | Start UTC | End UTC |
|---|---|---|---|
| `capex-invoice-ready-dispatch-3447evl` | `Failed` | `2026-10-20T19:35:53Z` | Not reported |
| `capex-invoice-ready-dispatch-5zfp76x` | `Succeeded` | `2026-10-20T20:32:57Z` | `2026-10-20T20:33:32Z` |

These executions predate deployment of commit `aee6371`, which adds
`dispatchRunId` and the dispatcher start event. They can provide historical
failure/success context, but they do not satisfy the live correlated-log
acceptance evidence. A new immutable image and deployment are required before
capturing that evidence.

Both deployed jobs currently use:

```text
cictaicusacr.azurecr.io/capex/invoice-runtime:20261020.2
```

Deployed container names:

- preparation: `capex-prepare`
- dispatcher: `capex-dispatch`

The preparation container name and image also predate the Blob-backed
`capex-prepare-blob` entry-point correction. Do not expect a live Blob
preparation trace until the new image is built and deployed.

Direct log retrieval for both historical dispatcher executions returned:

```text
No replicas found for execution
```

The finite execution replicas have expired, so `az containerapp job logs show`
cannot retrieve these old logs. Use retained Log Analytics data for any
remaining historical evidence. This does not affect future evidence capture;
retrieve logs promptly after each new validation execution and also preserve
the Log Analytics query result.

The Container Apps environment is configured with:

| Setting | Recorded value |
|---|---|
| Log destination | `log-analytics` |
| Workspace/customer ID | `d370bca3-708d-4fd7-b477-1fa1ceb0088a` |
| Dynamic JSON columns | `false` |
| Shared key returned by inspection | `null` |

Structured JSON must therefore be parsed from the raw console-log field using
the KQL examples below.

The first CLI query after interactive extension installation returned an
unfiltered workspace dump containing unrelated Fuel workloads. This proved
workspace access but was not accepted as CapEx evidence. Re-run focused
queries after installation using a single-line KQL argument and the confirmed
`ContainerJobName_s` and `ContainerGroupName_s` columns.

A focused seven-day query found four retained dispatcher log lines:

| Container group | UTC timestamp | Lines |
|---|---|---|
| `capex-invoice-ready-dispatch-3447evl-x4zrg` | `2026-10-20T19:36:22.0923756Z` | 1 |
| `capex-invoice-ready-dispatch-3447evl-skdpt` | `2026-10-20T19:37:07.7212626Z` | 1 |
| `capex-invoice-ready-dispatch-3447evl-sdtgg` | `2026-10-20T19:37:55.9024061Z` | 1 |
| `capex-invoice-ready-dispatch-5zfp76x-68w6j` | `2026-10-20T20:33:24.8707461Z` | 1 |

The failed execution's three container groups are consistent with Container
Apps retrying the failed finite job. Retrieve and preserve each raw message
before classifying the failure cause.

The retained messages show:

| Execution | Attempt evidence | Structured result |
|---|---|---|
| `capex-invoice-ready-dispatch-3447evl` | Three replica log lines at `19:36:22Z`, `19:37:07Z`, and `19:37:55Z` | `invoice_ready_dispatch_failed`: `AzureSqlConnectionError: Could not connect to Azure SQL using the workload managed identity.` |
| `capex-invoice-ready-dispatch-5zfp76x` | One replica log line at `20:33:24Z` | `invoice_ready_dispatch_completed`, `publishedCount: 0` |

This proves that the deployed job emitted structured terminal events for both
failure and success, and that the platform retried the earlier connection
failure. The later success proves the managed-identity SQL connectivity issue
was resolved by that execution. Because the outbox was empty, this is not
publication or outbox-retry evidence.

The four historical messages contain no credential, token, connection string,
or invoice content. They predate the `dispatchRunId` deployment and therefore
cannot prove execution-level correlation.

The documented `parse_json(Log_s)` approach was executed successfully against
the retained rows. It projected:

- three `invoice_ready_dispatch_failed` events with the managed-identity SQL
  connection error; and
- one `invoice_ready_dispatch_completed` event with `PublishedCount = 0`.

This validates the structured-event KQL pattern against the deployed Log
Analytics schema.

The deployed dispatcher execution policy is:

| Setting | Value |
|---|---|
| Trigger type | `Manual` |
| Parallelism | `1` |
| Replica completion count | `1` |
| Replica retry limit | `2` |
| Replica timeout | `900` seconds |

The failed execution's three replicas match the configured policy exactly:
one initial attempt plus two retries. This validates platform-level retry
observability for a startup/connectivity failure. It does not replace the
application outbox-retry evidence required after a publishable event exists.

The deployed preparation execution policy is:

| Setting | Value |
|---|---|
| Trigger type | `Manual` |
| Parallelism | `1` |
| Replica completion count | `1` |
| Replica retry limit | `2` |
| Replica timeout | `1800` seconds |
| Schedule configuration | None |

The job is finite and appropriately serialized, but no preparation execution
exists yet. Its start/completion/failure and Blob-event evidence therefore
remains dependent on deploying the new image and running an approved sample.

## Log Analytics queries

Container Apps environments commonly write console output to
`ContainerAppConsoleLogs_CL`. Column names can vary by schema version, so these
queries use `column_ifexists`.

### Discover recent CapEx logs

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(24h)
| extend
    JobName = tostring(column_ifexists("ContainerJobName_s", "")),
    ExecutionName = tostring(column_ifexists("ContainerJobExecutionName_s", "")),
    RawLog = tostring(column_ifexists("Log_s", ""))
| where JobName in (
    "capex-invoice-preparation",
    "capex-invoice-ready-dispatch"
)
| project TimeGenerated, JobName, ExecutionName, RawLog
| order by TimeGenerated desc
```

If this returns no rows, inspect the table schema:

```kusto
ContainerAppConsoleLogs_CL
| getschema
```

Then adjust only the job, execution, and log column names.

### Parse structured CapEx events

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(24h)
| extend
    JobName = tostring(column_ifexists("ContainerJobName_s", "")),
    ExecutionName = tostring(column_ifexists("ContainerJobExecutionName_s", "")),
    RawLog = tostring(column_ifexists("Log_s", ""))
| where JobName in (
    "capex-invoice-preparation",
    "capex-invoice-ready-dispatch"
)
| extend Payload = parse_json(RawLog)
| where isnotempty(tostring(Payload.event))
| project
    TimeGenerated,
    JobName,
    ExecutionName,
    Event = tostring(Payload.event),
    RunId = tostring(Payload.runId),
    DispatchRunId = tostring(Payload.dispatchRunId),
    PackageId = tostring(Payload.packageId),
    InvoiceId = tostring(Payload.invoiceId),
    CorrelationId = tostring(Payload.correlationId),
    PublishedCount = toint(Payload.publishedCount),
    Error = tostring(Payload.error)
| order by TimeGenerated asc
```

### Trace one preparation run

Replace the placeholder with the captured run ID:

```kusto
let TargetRunId = "<run-id>";
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(24h)
| extend RawLog = tostring(column_ifexists("Log_s", ""))
| extend Payload = parse_json(RawLog)
| where tostring(Payload.runId) == TargetRunId
| project
    TimeGenerated,
    Event = tostring(Payload.event),
    PackageId = tostring(Payload.packageId),
    InvoiceId = tostring(Payload.invoiceId),
    Source = tostring(Payload.source),
    Error = tostring(Payload.error),
    RawLog
| order by TimeGenerated asc
```

### Trace one dispatcher execution

Replace the placeholder with the `dispatchRunId` from the start event:

```kusto
let TargetDispatchRunId = "<dispatch-run-id>";
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(24h)
| extend RawLog = tostring(column_ifexists("Log_s", ""))
| extend Payload = parse_json(RawLog)
| where tostring(Payload.dispatchRunId) == TargetDispatchRunId
| project
    TimeGenerated,
    Event = tostring(Payload.event),
    TopicName = tostring(Payload.topicName),
    MaxMessages = toint(Payload.maxMessages),
    PublishedCount = toint(Payload.publishedCount),
    Error = tostring(Payload.error)
| order by TimeGenerated asc
```

Expected successful sequence:

```text
invoice_ready_dispatch_started
invoice_ready_dispatch_completed
```

Expected failed sequence:

```text
invoice_ready_dispatch_started
invoice_ready_dispatch_failed
```

### Find errors and quarantine activity

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(24h)
| extend RawLog = tostring(column_ifexists("Log_s", ""))
| extend Payload = parse_json(RawLog)
| extend Event = tostring(Payload.event)
| where
    Event endswith "_error"
    or Event endswith "_failed"
    or Event == "blob_quarantine_uploaded"
| project
    TimeGenerated,
    Event,
    RunId = tostring(Payload.runId),
    DispatchRunId = tostring(Payload.dispatchRunId),
    Blob = tostring(Payload.blob),
    Source = tostring(Payload.source),
    Error = tostring(Payload.error)
| order by TimeGenerated desc
```

## Retry diagnostics

The dispatcher uses the transactional SQL outbox for retry state:

- eligible records are claimed one at a time;
- failures are recorded before the job exits;
- retry delay uses bounded exponential backoff;
- events stop being eligible after the configured maximum attempts;
- broker duplicate detection must suppress a retry when publication succeeded
  but marking the outbox row `PUBLISHED` failed.

For a dispatcher failure:

1. Capture the `dispatchRunId`.
2. Capture the full `invoice_ready_dispatch_failed` error.
3. Identify the event ID named in the error.
4. Query the outbox row through the approved read-only SQL path.
5. Record attempt count, next attempt time, status, and last error.
6. Do not manually reset or delete the row during the demo.

Do not grant Azure RBAC `SQL DB Data Writer`. Runtime SQL permissions are
contained-database grants.

### Read-only outbox diagnostic query

This query excludes `Payload` so diagnostics do not expose normalized invoice
content:

```sql
SELECT
    OutboxStatus,
    COUNT_BIG(*) AS EventCount,
    MIN(InsertedAtUtc) AS FirstInsertedAtUtc,
    MAX(UpdatedAtUtc) AS LastUpdatedAtUtc,
    MAX(AttemptCount) AS MaximumAttemptCount
FROM prep.InvoiceOutbox
GROUP BY OutboxStatus
ORDER BY OutboxStatus;

SELECT TOP (20)
    EventId,
    PackageId,
    EventType,
    EventVersion,
    CorrelationId,
    OutboxStatus,
    AttemptCount,
    AvailableAtUtc,
    LockedAtUtc,
    PublishedAtUtc,
    LastError,
    InsertedAtUtc,
    UpdatedAtUtc
FROM prep.InvoiceOutbox
ORDER BY InsertedAtUtc DESC;
```

Do not include `Payload` in screenshots or routine operational evidence.

### Recorded outbox baseline - October 21, 2026

The status aggregate and recent-event query both returned no rows.
`prep.InvoiceOutbox` is empty, consistent with the successful historical
dispatcher execution reporting `publishedCount: 0`.

There is therefore no existing event that can prove:

- application-level retry state;
- transition to `PUBLISHED`;
- event/correlation IDs across SQL and Service Bus; or
- incoming-message publication activity for this workflow.

Do not fabricate or manually insert an outbox event for observability
evidence. Capture these items after the approved preparation run creates the
first transactional outbox record.

## Service Bus publication checks

The current topic may have no consumer subscription. Publication can still be
proven using:

- dispatcher completion with `publishedCount > 0`;
- outbox state `PUBLISHED`;
- an increase in the topic's Service Bus `IncomingMessages` metric.

`OutgoingMessages` represents delivery from Service Bus to a receiver; it is
not publisher-send evidence. Do not claim downstream consumption without a
subscription and receiver execution.

### Recorded Service Bus baseline - October 20, 2026

Hourly metrics for `capex-invoice-ready-v1` from `19:00Z` through `21:00Z`
showed:

| Metric | 19:00Z | 20:00Z | 21:00Z |
|---|---:|---:|---:|
| `IncomingMessages` | 0 | 0 | 0 |
| `OutgoingMessages` | 0 | 0 | 0 |

This matches the empty SQL outbox and dispatcher `publishedCount: 0`. It is a
valid zero-traffic baseline, not publication evidence.

## Dead-letter scope

There is no current consumer subscription or deployed Function, so there is no
downstream dead-letter queue to inspect.

When a consumer subscription is created, extend this runbook with:

- subscription name and scope;
- maximum delivery count;
- dead-letter reason and description;
- receiver execution/correlation ID;
- message event ID, package ID, and correlation ID;
- safe replay procedure;
- evidence that replay is idempotent.

Do not create a subscription or dead-letter workflow solely to satisfy this
runbook without architecture approval.

## Evidence checklist

- [ ] Preparation execution name.
- [ ] Preparation `runId`.
- [ ] Preparation start and completion events.
- [ ] Package and invoice identifiers.
- [ ] Blob download/output/quarantine counts.
- [ ] Dispatcher execution name.
- [ ] Dispatcher `dispatchRunId`.
- [ ] Dispatcher start and terminal event.
- [ ] Published count.
- [ ] SQL outbox status.
- [ ] Service Bus topic `IncomingMessages` publication evidence.
- [ ] Failure/retry evidence when testing a failure path.
- [ ] Confirmation that no secrets or full invoice contents appear in logs.

## CICT-36 completion criteria

- Structured preparation and Blob events are available.
- Dispatcher events share one `dispatchRunId`.
- Successful and failed dispatcher paths are covered by tests.
- The full CapEx test suite passes.
- A live preparation execution can be traced by `runId`.
- A live dispatcher execution can be traced by `dispatchRunId`.
- Retry state can be correlated to an outbox event.
- The absence of downstream dead-letter infrastructure is documented rather
  than represented as completed functionality.

## Current completion status - October 21, 2026

Completed:

- structured preparation, Blob, and dispatcher event implementation;
- dispatcher `dispatchRunId` correlation implementation;
- success/failure correlation tests and full CapEx test suite;
- Log Analytics destination and structured JSON parsing validation;
- historical dispatcher success/failure diagnostics;
- platform retry-policy and retry-attempt correlation;
- empty outbox and zero-traffic Service Bus baselines;
- secret/invoice-content review of retained logs;
- downstream dead-letter deferral documentation.

Pending deployment and live data:

- deploy an immutable image containing commit `aee6371`;
- execute preparation and trace one `runId`;
- execute the updated dispatcher and trace one `dispatchRunId`;
- create the first legitimate outbox event through preparation;
- capture application retry state if a controlled failure occurs;
- capture outbox `PUBLISHED`, dispatcher `publishedCount > 0`, and topic
  `IncomingMessages` evidence.

The pending items depend on the same new image and approved `landing` run as
CICT-35/CICT-40. No additional local observability implementation is required
before that deployment.