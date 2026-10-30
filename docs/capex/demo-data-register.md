# CICT-41 CapEx Demo Data Register

> Related work item: CICT-41
>
> Demo V2 expands this original two-invoice register. See
> [CapEx Demo V2 Data Inventory and Gap Register](./demo-v2-data-inventory.md)
> for the JC project files, current field coverage, and synthetic-data rules.

## Objective

Maintain a small, controlled, customer-approved dataset for the CapEx
demonstration. This register identifies the intended samples, their expected
business characteristics, and the evidence required before they are uploaded
or displayed.

Approval must come from the appropriate Circle K data or business owner. The
presence of a file in the repository or working folder is not approval.

## Candidate samples

| Sample | Invoice | Supplier | Region | Currency | Expected lines | ABBYY transaction |
|---|---|---|---|---|---:|---|
| 1 | `INV12-62027` | Bruegmann USA | Minnesota, US | USD | 12 | `135d03ac-3598-498a-83fe-d273d046bfea` |
| 2 | `36764068-GRP005` | Kimley-Horn | California, US | USD | 10 | `58aceec5-f35a-48a7-8faa-2f1a195af203` |

ABBYY creation times:

- Bruegmann USA: `2026-09-08T09:40:49.2180876Z`
- Kimley-Horn: `2026-09-08T09:40:49.5356142Z`

Both samples are North American. They do not establish European or global
coverage.

## Approval register

Complete this table before the customer meeting.

| Sample | Approved for customer demo | Approver | Approval date | Permitted display | Notes |
|---|---|---|---|---|---|
| Bruegmann USA | Confirmed for demo use | Rachael Phillips | 2026-10-21 | Architecture / normalized fields / generated output | Full invoice-content display is not assumed. |
| Kimley-Horn | Confirmed for demo use | Rachael Phillips | 2026-10-21 | Architecture / normalized fields / generated output | Full invoice-content display is not assumed. |

Do not infer approval from prior technical use. Record an explicit approver and
date.

## Pre-upload validation

For each approved ZIP:

- [x] File is the intended ABBYY export.
- [x] File opens successfully.
- [x] `Transaction.Id` matches this register.
- [x] `Transaction.CreationTime` matches this register.
- [x] `Document.Id` is present.
- [x] Invoice number matches this register.
- [x] Currency is USD.
- [x] Expected line count matches.
- [x] No unrelated invoices are included.
- [x] No credentials, tokens, or connection strings are included.
- [x] Display restrictions are recorded.
- [x] SHA-256 is recorded below.
- [x] Intended Blob name is recorded below.

## Integrity and Blob manifest

Populate from the approved files:

| Sample | Local filename | SHA-256 | Blob name | Blob ETag | Blob LastModified |
|---|---|---|---|---|---|
| Bruegmann USA | `Bruegmann  - INV1262027.pdf-135d03ac-3598-498a-83fe-d273d046bfea.zip` | `974D61A406EDF85E3CADFA75A5177795481D3A82CE962DC414E9D276B885EE5B` | `capex/input/Bruegmann  - INV1262027.pdf-135d03ac-3598-498a-83fe-d273d046bfea.zip` | `"0x8DF2FE25B5A2AE0"` | `2026-10-22T02:15:35+00:00` |
| Kimley-Horn | `Kimley Hor - 36764068GRP005.pdf-58aceec5-f35a-48a7-8faa-2f1a195af203.zip` | `EDE8B96961483673D0E06D8D73F684A835F05D9A061F67A8BCB84B98BE445292` | `capex/input/Kimley Hor - 36764068GRP005.pdf-58aceec5-f35a-48a7-8faa-2f1a195af203.zip` |  |  |

Compute SHA-256:

```powershell
Get-FileHash `
  -Algorithm SHA256 `
  -Path '<approved ZIP path>' |
  Select-Object Path, Hash
```

Upload only after approval and Blob RBAC validation:

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

Record Blob integrity metadata:

```powershell
az storage blob show `
  --auth-mode login `
  --account-name cictaicusst `
  --container-name landing `
  --name $BlobName `
  --query '{name:name,etag:properties.etag,lastModified:properties.lastModified,size:properties.contentLength}' `
  --output json
```

## Demonstration controls

- Use Entra authentication; do not use storage keys or SAS tokens.
- Use only the `landing` container and `capex/input/` prefix.
- Do not overwrite an existing sample during the meeting.
- Do not delete or move source blobs during the first demo implementation.
- Do not expose full invoice content unless explicitly approved.
- Prefer showing normalized fields and result summaries.
- Do not claim that invoice date is an incremental-processing watermark.
- Use ABBYY creation time as arrival time, transaction ID as stable identity,
  and SHA-256 plus Blob ETag/LastModified for change detection.

## Expected output evidence

For each executed sample, record:

| Sample | Run ID | Output package blob | Summary blob | SQL package present | Outbox state | Dispatcher execution |
|---|---|---|---|---|---|---|
| Bruegmann USA | `run-20261022T031623Z-eee6efc5` | `capex/output/run-20261022T031623Z-eee6efc5/pkg-2a26689e-5097-5cfe-b18c-10d34d75c0da.json` | `capex/output/run-20261022T031623Z-eee6efc5/_run-summary.json` | `READY`; 12 lines; published view count 1 | `PUBLISHED`; attempt 1; `2026-10-22T10:04:40.909Z` | `capex-invoice-ready-dispatch-64nugpm` |
| Kimley-Horn |  |  |  |  |  |  |

Expected Blob locations:

```text
capex/output/<run-id>/<package-output>
capex/output/<run-id>/_run-summary.json
```

If preparation fails, record the controlled quarantine location:

```text
capex/quarantine/<run-id>/<source-file>
```

## Post-demo retention and deletion

Retain the approved source ZIPs and generated outputs through the
customer demonstration. Do not delete, move, or overwrite them until
the Circle K data owner explicitly approves deletion. Record the
approval, deletion date, operator, and affected Blob names before
removing any demo data.
## Safe customer statements

Approved:

- The demo uses two controlled North American ABBYY samples.
- The workflow retains source identity and arrival metadata.
- The workflow normalizes and validates invoices into a versioned contract.
- The workflow records outputs and publication state with traceable run IDs.

Not approved without additional evidence:

- The samples represent all Circle K invoice formats.
- The workflow is production-ready.
- European invoices are supported.
- Foundry task determination completed for these samples.
- POPA or Ariba was updated.

## CICT-41 completion criteria

- [x] At least one sample has explicit demo approval.
- [x] Approver and date are recorded.
- [x] SHA-256 and permitted display scope are recorded.
- [x] Source values match this register.
- [x] The sample is uploaded only to the controlled Blob prefix.
- [x] Input, output, SQL, and dispatch evidence are linked or recorded.
- [x] Any post-demo retention or deletion requirement is recorded.
