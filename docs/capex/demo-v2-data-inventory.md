# CapEx Demo V2 Data Inventory and Gap Register

**Prepared:** October 30, 2026  
**Demo:** November 6, 2026  
**Target invoices:** Bruegmann, Kimley-Horn, and JC2000A07814  
**Companion project extract:** JC2000A07465

## Purpose

This register identifies what is actually present in the supplied files, whether each target can be normalized for Demo V2, and which missing values must come from Circle K or be visibly marked synthetic for the demo.

The inventory does not treat a blank field, a zero placeholder, an account-code suffix, or a meeting assumption as authoritative business data.

## Demo source approval

Approval was confirmed on October 30, 2026 to use the available **Bruegmann**, **Kimley-Horn**, and **JC** invoice sources in Demo V2.

This closes the source-selection and demo-use approval question for all three target cases. It does not change source-missing values, approve a threshold basis, supply European TaskMaster data, authorize cross-currency aggregation, or establish an expected CapEx/OpEx outcome. Those decisions remain governed separately and continue to produce `REVIEW` when required inputs are unavailable.

## Source inventory

| Source | Format and contents | Integrity | Demo role |
|---|---|---|---|
| Bruegmann `INV12-62027` | ABBYY ZIP and extracted PDF/JSON/CSV; 12 lines | ZIP SHA-256 `974d61a406edf85e3cadfa75a5177795481d3a82ce962dc414e9d276b885ee5b` | Primary US task and bundling case |
| Kimley-Horn `36764068-GRP005` | ABBYY ZIP and extracted PDF/JSON/CSV; 10 lines | ZIP SHA-256 `ede8b96961483673d0e06d8d73f684a835f05d9a061f67a8bcb84b98be445292` | Primary US task and professional-services case |
| `JC2000A07814 - Ariba+Proj.tool.xlsx` | One 46-column project/accounting sheet; 2 transaction rows | SHA-256 `bdb5a904f1d661a04ca24db2f365c9de4c06916579b0f250a519151a22227510` | Primary requested JC project case; not yet an invoice package |
| `JC2000A07465-Ariba.xlsx` | One 46-column project/accounting sheet; 13 accounting rows | SHA-256 `6cdb3b8cc42e0a252e4f2716feb4cfa94de7037f537d0b3ca4585092f7ae2e42` | Companion multi-invoice/project evidence; not a target invoice |
| `Task_Type_Export_9_6_2026.xlsx` | US and Canadian task/reference sheets | SHA-256 `cc41b492a5de454efd7405066ef1eaa6cde1ff050c8e71ed180831197b6eba13` | Governed task source for US/Canada only |
| `Cases.xlsx` | Flattened convenience workbook with 1 EU row and 22 US rows | SHA-256 `6b34fdd3ff115bd8a2fb920713649100d41a4cbb451b21c31609bd9f894e1dd0` | Inspection aid only; not the canonical source |

## Readiness summary

| Target | Task suggestion | Threshold assessment | Bundling | Overall |
|---|---|---|---|---|
| Bruegmann | Approved demo source; ready with governed US TaskMaster | Values available, but all unit costs are below 1,000 USD | Needs approved/synthetic project and site context | **Conditionally ready** |
| Kimley-Horn | Approved demo source; ready with governed US TaskMaster | Blocked by missing meaningful quantity/unit cost unless line amount is approved as the basis | Needs approved/synthetic project and site context | **Conditionally ready** |
| JC2000A07814 | Approved demo source; blocked for a governed result because embedded code `0303` is absent from the supplied US/Canadian TaskMaster | Blocked by EUR/DKK rows and missing governed conversion/basis | Blocked by missing site, currency normalization, and invoice-line semantics | **Approved as input; review case** |

No target is currently ready for a fully truthful end-to-end capitalization and bundling result without either additional Circle K data or explicitly labeled demo context.

## Invoice-level findings

### Bruegmann

- Invoice number: `INV12-62027`.
- Source system: ABBYY.
- Region/currency: North America / USD.
- Line count: 12.
- Invoice total: 1,627.50 USD.
- Extracted line total: 1,528.17 USD.
- Difference: 99.33 USD, consistent with the existing package warning for tax or another header-level charge.
- Description is populated on all 12 lines.
- Vendor part number is populated on 11 of 12 lines.
- Quantity and unit cost are substantively populated on 11 of 12 lines.
- The shipping line has a 617.09 USD line amount but missing source quantity/unit price; the normalized example currently represents those missing values as zero.
- Every represented unit cost is below the proposed 1,000 USD threshold.
- PO, PO line, project/AFE, site, project status, and approved bundling scope are absent.

**Conclusion:** This is a strong task-suggestion case and a useful bundling example because its equipment lines are individually below threshold. It cannot demonstrate approved bundling until site/project context is supplied or explicitly synthesized.

### Kimley-Horn

- Invoice number: `36764068-GRP005`.
- Source system: ABBYY.
- Region/currency: North America / USD.
- Line count: 10.
- Invoice total and extracted line total both equal 28,805.00 USD.
- Description is populated on all 10 lines.
- Source quantity and unit price are absent on all 10 lines.
- Two lines have zero line amount.
- The normalized example currently represents every missing quantity and unit cost as zero even when line amount is non-zero.
- Part number, PO, PO line, project/AFE, site, project status, and approved bundling scope are absent.

**Conclusion:** This is a strong professional-services task-suggestion case, but it must not be evaluated with a unit-price threshold while zero placeholders stand in for missing values. Finance must approve line amount as the basis, or Circle K must provide actual quantity/unit-cost semantics.

### JC2000A07814

- The workbook contains 2 accounting rows, not a complete normalized invoice-line export.
- Both rows use project/business unit `JC2000A07814`.
- Both rows use account `JC2000A07814.118010.0303` and description `TLG system`.
- The account suffix suggests source task/category `0303`, but `0303` is absent from both supplied US and Canadian TaskMaster sheets.
- One row is EUR and one row is DKK.
- Positive source amounts total 20,968.76, but these values cannot be summed as one business amount without confirming ledger/currency semantics.
- One of two rows has invoice number `20294`.
- One of two rows has PO `12038910`.
- One row contains `FFXP0011831`, which resembles the unconfirmed Nuvolo/R&M prefix discussed in the policy review.
- Site, asset number, subledger, complete quantity/unit cost, approved exchange rate, and invoice-line detail are missing.

**Conclusion:** This file is useful project/accounting context. It can be normalized losslessly into a review artifact, but it is not sufficient to produce a governed CapEx/OpEx result. The workflow needs an EU task/asset reference source and an approved interpretation of the accounting rows and currencies.

### JC2000A07465 companion

- The workbook contains 13 accounting rows under project/business unit `JC2000A07465`.
- Seven rows are document type `AI`; six are `JP` accrual/reversal rows.
- All rows are DKK.
- The seven `AI` rows have invoice identifiers; the accrual/reversal rows do not.
- Source account suffixes are `0601`, `0604`, `1201`, and `3307`.
- None of those codes is present in the supplied US or Canadian TaskMaster sheets.
- Positive row amounts total 2,061,237.95 DKK; the net including reversals is 1,090,291.42 DKK.
- No row contains a purchase order, site, asset number, quantity, or complete invoice-line detail.
- The data demonstrates multiple vendors and categories within one project, but the accrual/reversal rows must be excluded or reconciled before candidate totals are calculated.

**Conclusion:** Use this file only as companion project-history evidence until Circle K confirms document-type and reversal handling. It must not be added blindly to the JC2000A07814 candidate total.

## Field coverage against the workflow

| Required field | Bruegmann | Kimley-Horn | JC2000A07814 | Required action |
|---|---|---|---|---|
| Stable invoice ID | Yes | Yes | Partial | Confirm identity for the row without invoice number |
| Invoice number | Yes | Yes | 1 of 2 rows | Confirm whether rows are separate invoices |
| Vendor | Yes | Yes | Yes | None |
| Description | 12/12 | 10/10 | Generic account description only | Provide actual JC invoice-line descriptions |
| Quantity | 11/12 substantive | 0/10 substantive | 1/2 | Provide or define missing-value behavior |
| Unit cost | 11/12 substantive | 0/10 substantive | Not reliable | Confirm threshold basis or provide actual values |
| Line amount | 12/12 | 10/10 | Accounting amount present | Confirm JC amount and currency semantics |
| Currency | USD | USD | EUR and DKK | Provide governed budget rates and target currency |
| Part number | 11/12 | 0/10 | None | Optional for LangChain; useful for exact lookup |
| PO number | Missing | Missing | 1/2 | Provide if required for project/context validation |
| PO line/task | Missing | Missing | Embedded source account suffix only | Do not treat suffix as governed task without reference data |
| Governed task list | US list available | US list available | EU list missing | Obtain European task/asset reference data |
| Project/AFE ID | Missing | Missing | `JC2000A07814` available | Provide or synthesize US demo project IDs |
| Site ID | Missing | Missing | Missing | Provide or visibly synthesize |
| Project status | Missing | Missing | Missing | Provide or visibly synthesize |
| Bundling-in-scope flag | Missing/false default | Missing/false default | Missing | Obtain approved value or label demo assumption |
| R&M/work-order source | Missing | Missing | `FFXP0011831` on one row | Confirm prefix and source semantics |
| Expected finance result | Missing | Missing | Missing | Finance owner must provide acceptance expectations |

## Required Circle K data

The following requests should be made before the demo if possible:

1. European task/asset category data that governs codes such as `0303`, `0601`, `0604`, `1201`, and `3307`.
2. Actual invoice-line descriptions, quantities, unit costs, invoice identities, and site for JC2000A07814.
3. Confirmation of whether the EUR and DKK rows are separate invoices and which amount/currency is used for capitalization.
4. The governed budget exchange rate and target currency if the JC rows will be assessed together.
5. Definitions for document types `AI`, `OV`, and `JP`, including how accrual and reversal rows must be excluded or netted.
6. Project/AFE, site, project status, and bundling-scope values for Bruegmann and Kimley-Horn.
7. Confirmation that Kimley-Horn may use line amount when unit price and quantity are not supplied.
8. Expected task, capitalization, and bundling outcomes for all three target invoices.

## Minimum synthetic data allowed for the demo

If Circle K cannot provide all operational context before the demo, create only a clearly labeled demo-context overlay:

- Synthetic site ID for Bruegmann.
- Synthetic project/AFE ID and `OPEN` project status for Bruegmann.
- Synthetic site ID for Kimley-Horn.
- Synthetic project/AFE ID and `OPEN` project status for Kimley-Horn.
- Explicit `bundlingInScope=true` only for the scenarios intended to demonstrate bundling.

Every synthetic value must:

- Use a visible `DEMO-` prefix.
- Be stored separately from the immutable source invoice data.
- Be included in output evidence as synthetic context.
- Be removable without changing the normalized source invoice.

Do **not** synthesize:

- TaskMaster entries or European task mappings.
- Financial policy, threshold, or exchange rates.
- Unit costs for Kimley-Horn.
- JC invoice identifiers, currency conversions, or document-type meaning.
- Expected finance decisions.

If these values are not provided, the workflow must return `REVIEW`.

## Normalization requirements for the next subtask

1. Preserve the ABBYY ZIPs and JC workbooks as immutable sources with their SHA-256 values.
2. Resolve a contract gap before loading Kimley-Horn: `InvoicePackage v1`
   requires numeric quantity and unit cost, while the current adapter represents
   source-missing values as zero. Introduce a backward-compatible way to
   preserve missing-value semantics; do not silently treat missing as zero.
3. Add a clearly versioned demo-context overlay for synthetic project/site/status fields.
4. Normalize Bruegmann and Kimley-Horn from their canonical ABBYY exports.
5. Define and test an explicit JC row-selection rule before creating invoice lines.
6. Prevent `JP` accrual/reversal rows from being double-counted.
7. Reject mixed-currency bundle aggregation without a governed conversion.
8. Validate every package against the selected, versioned invoice-package schema.
9. Record the source-to-contract field mapping and all warnings.

## Inventory decision

- **Proceed** with Bruegmann and Kimley-Horn normalization and task suggestion.
- **Proceed conditionally** with a visibly synthetic context overlay for US bundling.
- **Do not claim a governed JC2000A07814 result** until the European reference data and row/currency semantics are resolved.
- JC2000A07814 may still be shown as an intentional `REVIEW` case if the missing inputs are the point of the demonstration.

## Demo V2 normalization outputs

The Demo V2 normalizer writes validated artifacts to `examples/data/capex/normalized-v2`:

- Bruegmann preserves the shipping line's missing quantity and unit cost as `null`.
- Kimley-Horn preserves all ten missing quantities and unit costs as `null`.
- JC2000A07814 includes only its approved `AI` invoice row, excludes the unapproved `OV` row, and records that the workbook contains both EUR and DKK rather than aggregating the currencies.
- JC2000A07465 remains a classification artifact: seven `AI` invoices are separated and six `JP` accrual/reversal rows are excluded to prevent double counting.
- `demo-v2-context-overlays.json` contains visibly synthetic `DEMO-` project/site context and never overwrites source-derived fields.
- `manifest.json` records package IDs, source hashes, normalization status, overlay use, and review reasons.

Regenerate the artifacts with:

```powershell
.\.venv314\Scripts\python.exe tools\normalize_capex_demo_v2.py
```

The legacy `InvoicePackage v1` preparation job is unchanged. Demo V2 uses the separate `2.0-normalized` contract so missing numeric values cannot silently become real zeroes.
