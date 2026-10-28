# InvoicePackage v1 Assembly and Validation

`autarch.capex.build_invoice_package` converts normalized ABBYY invoice data
into a validated `InvoicePackage v1` object.

## Inputs

The assembler accepts:

- normalized output from `ABBYYInvoiceAdapter`,
- a preparation `run_id`,
- a UTC creation timestamp,
- and optional project, business-unit, bundling, and package identifiers.

When `package_id` is omitted, the assembler derives a stable UUID-based ID from
the run ID and invoice ID. Retrying the same invoice within the same run
therefore produces the same package ID.

## Assembly behavior

The assembler:

- preserves every normalized invoice line in deterministic order,
- creates stable invoice-line identifiers,
- maps vendor, part-number, PO, project, site, and business-unit context,
- carries forward the ABBYY package name and complete-ZIP SHA-256 hash,
- preserves explicit source data-quality warnings,
- derives missing-PO, missing-project, and missing-site flags,
- detects invoice-header versus extracted-line-total differences,
- and never changes source financial values to make totals reconcile.

## Validation

Every assembled package is validated against the packaged
`InvoicePackage v1` Draft 2020-12 JSON Schema with format checking enabled.

Assembly fails explicitly for missing identifiers, empty invoices, duplicate
source line IDs, invalid numbers, negative quantities/totals, invalid PO line
numbers, non-UTC timestamps, malformed hashes, invalid dates/currencies, or any
other contract violation.

The canonical schema remains under `contracts/`. An identical packaged copy is
included under `autarch.capex.schemas` so validation works in installed wheels;
automated tests prevent the copies from drifting.

## Workflow boundary

Assembly is deterministic and does not invoke an AI model. Database persistence,
transactional outbox creation, Service Bus publication, and agent invocation are
separate workflow stages.
