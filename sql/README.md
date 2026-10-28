# Invoice Operational Store

Azure SQL assets for prepared CapEx invoices.

- `migrations/` contains ordered, rerunnable schema migrations.
- `tests/` contains executable, rollback-based verification scripts.
- `prep` is the durable operational schema.
- `pub` exposes versioned, read-only contracts to downstream components.

Apply migrations in filename order:

1. `001_create_invoice_operational_store.sql`
2. `002_create_published_invoice_package_view.sql`
3. `003_create_invoice_outbox.sql`

`contracts/invoice-package-v1.schema.json` is the source of truth for the
published `InvoicePackage v1` shape. `contracts/invoice-ready-v1.schema.json`
is the source of truth for the pointer-only event stored in
`prep.InvoiceOutbox`.

A package and its outbox event are written in one database transaction. The
package remains `PREPARING` until all header, line, warning, and outbox rows
exist. The final transaction step changes the package to `READY`, allowing
`pub.v1_InvoicePackage` to expose it.

The outbox stores durable publication intent. It does not publish directly to
Azure Service Bus. A later dispatcher claims `PENDING` records, publishes
them, and updates their delivery state.

Raw ABBYY documents are not stored in these SQL scripts or committed to the
repository.
