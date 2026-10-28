# CapEx Transactional Persistence and Outbox

## Purpose

The persistence layer stores one validated `InvoicePackage v1` and creates
its pointer-only `InvoiceReady v1` outbox record in the same Azure SQL
transaction.

This prevents a package from becoming visible without durable publication
intent and avoids publishing an event for data that later rolls back.

## Public API

`autarch.capex.persist_invoice_package` accepts:

- a DB-API-compatible connection;
- a validated `InvoicePackage v1` mapping;
- an optional package URI;
- an optional UTC occurrence timestamp.

It returns the persisted `InvoiceReady v1` event.

The persistence module intentionally does not select or install an Azure SQL
driver. Driver selection, connection authentication, pooling, and managed
identity belong to the later Azure deployment task.

## Transaction order

For a new package, persistence performs these operations in one explicit SQL
transaction:

1. Acquire update/hold locks for the logical `(runId, invoiceId)` identity.
2. Insert the package with status `PREPARING`.
3. Insert all invoice lines.
4. Insert all data-quality warnings.
5. Insert one `PENDING` `InvoiceReady v1` outbox record.
6. Transition the package from `PREPARING` to `READY`.
7. Commit.

Any failure rolls back the package and outbox together.

## Retry behavior

`PackageId`, `EventId`, and `correlationId` are deterministic.

- A retry of an already `READY` package returns the existing validated
  outbox event without inserting duplicate rows.
- A retry of an interrupted `PREPARING` or `FAILED` package replaces its
  incomplete transactional rows and writes them again.
- A retry is rejected if the package ID or source SHA-256 differs for the
  same `(runId, invoiceId)` identity.

The outbox uniqueness constraint allows only one logical event type/version
per package.

## Event boundary

`InvoiceReady v1` contains only:

- event, package, invoice, run, and correlation identifiers;
- the package URI;
- the UTC occurrence timestamp;
- event type and schema version.

It does not contain invoice lines, source documents, banking details,
payment instructions, or extracted images.

## Outbox dispatch boundary

This subtask creates durable `PENDING` records only. It does not publish to
Azure Service Bus.

A later dispatcher must:

1. claim eligible records safely;
2. publish with `EventId` as the broker message ID;
3. propagate `CorrelationId`;
4. mark successful records `PUBLISHED`;
5. record retry attempts and failures without losing the payload.

## Verification

Automated tests cover:

- event schema parity and pointer-only content;
- deterministic identity;
- UTC timestamp enforcement;
- transaction ordering;
- rollback when outbox insertion fails;
- completed and incomplete retry behavior;
- source-content conflict rejection;
- migration ordering and outbox constraints.

`sql/tests/002_verify_invoice_outbox.sql` provides a non-destructive,
rollback-based verification for an Azure SQL database after migrations are
deployed.
