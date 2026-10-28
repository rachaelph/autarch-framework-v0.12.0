# ABBYY Invoice Input Adapter

`autarch.adapters.ABBYYInvoiceAdapter` provides deterministic, read-only
ingestion of ABBYY Vantage invoice ZIP packages.

## Capabilities

- `invoice.list_abbyy` lists ZIP packages beneath the configured invoice root.
- `invoice.read_abbyy` parses one ZIP package beneath that root.

The adapter never extracts archive members to disk and never invokes an AI
model. It reads the invoice-header and line-item CSV members directly in memory.

## Normalized output

The adapter returns:

- stable invoice and transaction identifiers,
- invoice number, date, vendor, region, currency, and total,
- normalized line identifiers, descriptions, quantities, prices, and amounts,
- optional part and task references,
- the source package name,
- and the SHA-256 hash of the complete source ZIP.

Raw bank-account, bank-code, payment-reference, remit-address, tax-ID, PDF, and
page-image content is not returned in the normalized object.

## Validation

The adapter rejects:

- files outside the configured root,
- non-ZIP or corrupt inputs,
- missing or duplicate header/line CSV members,
- invalid UTF-8 CSV content,
- missing required columns or values,
- invalid or non-finite numbers,
- invalid currencies,
- negative invoice totals or quantities,
- duplicate line identifiers,
- and CSV members over the configured size limit.

These failures are explicit and do not produce success-shaped fallback data.

## Workflow boundary

This adapter is responsible only for deterministic ABBYY intake and
normalization. `InvoicePackage v1` assembly, database persistence, and event
publication are separate workflow stages.
