# CapEx Demo V2 Policy Inputs and Assumptions

**Prepared:** October 30, 2026  
**Demo:** November 6, 2026  
**Scope:** North America demo using Bruegmann, Kimley-Horn, and JC2000A07814

## Purpose

This document separates policy supported by supplied Circle K material from implementation assumptions that still require business approval. Demo V2 must not turn an unconfirmed assumption into an accounting decision. When a required rule or business attribute is unavailable, the workflow returns `REVIEW` and records the reason.

## Evidence reviewed

- Circle K, *Reporting Manual - April 2026 - Final*, property and equipment section, PDF pages 46-51.
- *IAS 16 Property, Plant and Equipment*, recognition and subsequent-cost guidance, PDF pages 3-4.
- *Circle K - CapEx CIP - 8 - Invoice Bundling Sequence - v4.1*.
- *Circle K - CapEx CIP - 9a - Class Diagram - Tables - v2.1*.
- September 30, October 6, and October 7 CapEx discovery and invoice-bundling meeting records.
- Existing TaskMaster data, Demo V2 lossless normalized packages, and workflow contracts.

The reporting manual is the strongest supplied source for the numeric threshold and accounting treatment. The meeting records and diagrams define workflow direction, but explicitly identify several items that must be confirmed before implementation.

## Status definitions

- **Policy supported**: directly supported by a supplied Circle K policy document.
- **Workflow confirmed**: consistently documented in the latest diagrams and meeting decisions.
- **Proposed demo assumption**: safe implementation choice for the demo, but not approved accounting policy.
- **Business confirmation required**: must be confirmed by Marie, Éric, Amanda, or another designated finance owner.
- **Deferred**: intentionally outside the Demo V2 scope.

## Policy and workflow matrix

| Area | Evidence-based rule | Status | Demo V2 treatment |
|---|---|---|---|
| Task determination order | Determine and validate the governed task before capitalization and bundling. | Workflow confirmed | Task stage runs first; unknown task codes cannot proceed as resolved. |
| AI boundary | AI may interpret ambiguous line descriptions; thresholds, exclusions, grouping, and persistence are deterministic. | Workflow confirmed | LangChain is limited to governed task determination. |
| Nature gate | Apply CapEx/OpEx-by-nature guidance before the monetary threshold. | Workflow confirmed; rule list missing | Use only approved entries. An unknown nature rule returns `REVIEW`. |
| Base threshold | Property-and-equipment expenditure below **$1,000 USD** must normally be expensed. | Policy supported | Proposed governed NA threshold is 1,000 USD, pending confirmation that this manual/version applies to the demo. |
| Below-threshold project exception | A below-$1,000 item may qualify when it is at least **$300 USD per site** and the aggregate project is at least **$30,000 USD**, with all stated policy criteria met. | Policy supported | Do not auto-apply until project aggregate and finance approval are available; otherwise return `REVIEW`. |
| Other threshold exceptions | A below-threshold item may require reporting-team consideration when it is critical, provides long-term benefit, or is below threshold because of scale/geography. | Policy supported | Always return `REVIEW`; never auto-capitalize this exception. |
| Threshold equality | The manual says “less than $1,000,” which implies 1,000 meets the threshold, but the latest sequence diagram explicitly requests confirmation of equality handling. | Business confirmation required | Proposed demo comparison is `unit cost >= 1,000 USD`; label the policy snapshot as unapproved until confirmed. |
| Threshold basis | The reporting manual describes an “expenditure”; the latest sequence diagram says the threshold applies to **unit price**, not invoice total. | Business confirmation required | Proposed demo basis is normalized line `unitCost`, because that is the latest workflow direction. |
| Currency | The manual uses USD and budget-rate conversion for Canadian, European, and Hong Kong currencies. | Policy supported; exchange-rate source missing | Demo only USD invoices. Non-USD records return `REVIEW` until a governed budget rate is supplied. |
| Directly attributable costs | Delivery, handling, installation, site preparation, testing, and professional fees may form asset cost when directly related and recognition criteria are met. | Policy supported | Treat as possible bundle-related cost, not automatic CapEx. Require an associated asset/task and evidence. |
| Labor | Directly attributable labor may be capitalizable, while day-to-day servicing labor is expense. The diagram still asks what labor is included in unit price. | Policy principle supported; calculation unconfirmed | Do not add or allocate labor to unit cost without an approved rule. Ambiguous labor lines return `REVIEW`. |
| Routine repair and maintenance | Repairs that only preserve existing performance, including repainting, cleaning, and minor surface repair, are expensed. | Policy supported | Deterministic `OPEX` when an approved nature rule matches. |
| Replacement or improvement | Work that restores/enhances service potential, extends useful life, increases capacity, or improves efficiency may be capitalized when recognition criteria are met. | Policy supported | Require evidence of replacement/improvement; ambiguous descriptions return `REVIEW`. |
| Major replacement reference | The manual identifies major replacement costs at **$10,000 or more per asset or project**, while minor replacements require greater judgment. | Policy supported | Preserve as policy evidence; do not add a separate automatic demo threshold unless finance requests it. |
| Major inspection | A major inspection can be recognized as replacement cost when the recognition criteria are met; ordinary tank-inspection examples were discussed as OpEx by nature. | Policy supported but fact-sensitive | Generic inspection lines return `REVIEW` unless an approved nature rule clearly resolves them. |
| Bundling key | Match the same governed task, site, project, and currency. Different POs and vendors may match. | Workflow confirmed | Use a deterministic stable group key; require all key fields. |
| Bundling scope | The latest diagram applies bundling to new and existing projects when required context is available. | Workflow confirmed; historical lookback missing | Process only the candidate activity loaded for the demo run. Do not imply complete historical coverage. |
| Missing site/project | A line cannot be safely bundled without site and project context. | Workflow confirmed | Return `REVIEW`, identify missing fields, and do not invent context. |
| Combined value | Sum the current line and matching sibling lines deterministically. | Workflow confirmed | No model call is permitted for aggregation or threshold comparison. |
| Above-threshold bundle | Matching lines above the threshold create a bundling candidate; matching fields and amount do not prove one asset. | Workflow confirmed | Output `CapEx - Bundling Candidate - Above Threshold` for human confirmation; do not auto-reclassify. |
| Below-threshold/open project | A future matching line may arrive while a project remains open. | Workflow confirmed; timing/lookback unconfirmed | Output `OpEx - Potential Future Bundling` and preserve the candidate evidence. |
| Below-threshold/closed project | If the complete candidate total remains below threshold, the documented outcome is OpEx. | Workflow confirmed; project-status source required | Apply only when project status and candidate-pool completeness are trustworthy; otherwise return `REVIEW`. |
| Human control | A fixed-asset analyst confirms whether candidate lines form one asset. | Workflow confirmed | Demo output is a recommendation and evidence package, not an accounting posting. |
| Nuvolo repair/maintenance source | Nuvolo-originated work should retain a recommendation plus a risk/source flag. FWKD/FFX prefixes were proposed but not confirmed. | Business confirmation required | Do not implement prefix detection as policy until confirmed; use explicit source data when available. |
| Leased sites | Leased-site useful-life treatment was discussed as a later phase. | Deferred | Exclude from Demo V2 decision logic; retain context if supplied. |
| Project closure reassessment | A later deterministic closure pass was proposed, but ownership and trigger are not approved. | Deferred | Do not implement for the demo. |

## Proposed governed demo snapshot

The following values allow engineering and testing to continue without claiming final finance approval:

| Setting | Proposed demo value |
|---|---|
| Policy identifier | `circle-k-na-demo-unapproved-2026-10-30` |
| Region | North America |
| Currency | USD only |
| Base threshold | 1,000 USD |
| Equality | `unitCost >= 1,000` meets the base threshold |
| Threshold basis | Invoice-line unit cost |
| Bundling match | Governed task code + site ID + project ID + currency |
| Cross-invoice grouping | Allowed |
| Cross-vendor/PO grouping | Allowed |
| Minimum candidate membership | Two lines |
| Missing task/site/project | `REVIEW` |
| Missing or non-USD exchange rate | `REVIEW` |
| Above-threshold group | Candidate for human asset confirmation, not automatic capitalization |
| Unapproved nature/labor/exception rule | `REVIEW` |

This snapshot must remain visibly marked **unapproved** until the finance owner confirms the threshold version, equality behavior, unit-price basis, and exception handling.

## Required inputs and current availability

| Input | Needed for | Current state |
|---|---|---|
| Governed US TaskMaster | Task determination and grouping | Available |
| Invoice line description, quantity, unit cost, and amount | Task and threshold evaluation | Available in the Demo V2 normalized package when present in source; missing quantity/unit cost remain `null` |
| Currency and region | Threshold selection | Available |
| Site ID | Bundling key | Contract supports it; not guaranteed in supplied invoices |
| Project/AFE ID | Bundling key and project aggregate | Contract supports it; not guaranteed in supplied invoices |
| Project status | Open/closed bundling category | Contract supports it; authoritative POPA feed is not wired |
| `bundlingInScope` | Scope gate | Contract supports it; governed source/value still required |
| Project aggregate | Below-threshold exception | Not currently available as an authoritative demo input |
| Approved CapEx/OpEx-by-nature library | Nature gate | Not supplied as a governed dataset |
| Approved repair/maintenance indicators | Nature and risk checks | Policy prose exists; operational keyword/prefix list is not approved |
| Budget exchange rates | Non-USD threshold conversion | Not supplied |
| Policy/version record | Audit evidence | Document exists; governed SQL record is not implemented |
| Expected result for each demo invoice | Acceptance validation | Not yet supplied by finance |

## Questions requiring Circle K confirmation

Send these questions to Marie and include Éric/Amanda or the designated finance owner:

1. Is *Reporting Manual - April 2026 - Final* the approved policy source for the North America demo?
2. Should the demo use the manual’s 1,000 USD base property-and-equipment threshold?
3. Does exactly 1,000 USD meet the threshold?
4. Should the comparison use invoice-line unit cost, line amount, or another value?
5. Should the $300-per-site and $30,000-project exception be implemented in the demo, or shown only as `REVIEW`?
6. What labor, installation, electrical, delivery, travel, or displacement costs belong in the asset/bundle value?
7. Can finance provide the approved CapEx-by-nature, OpEx-by-nature, and never-capitalize lists or keywords?
8. Are FWKD and FFX the approved Nuvolo/R&M work-order prefixes, and where should the work-order source come from?
9. Is task + site + project + currency the approved demo grouping key?
10. What historical lookback and source data are required for existing-project bundles?
11. What should happen when site, project, or project status is missing?
12. What are the expected task and CapEx/OpEx/bundling outcomes for Bruegmann, Kimley-Horn, and JC2000A07814?

## Ready-to-send message

> Hi Marie — we reviewed the April 2026 Circle K reporting manual and the latest bundling diagrams for next Friday’s CapEx Demo V2. The manual appears to define a 1,000 USD base threshold, a below-threshold exception using 300 USD per site plus a 30,000 USD project aggregate, and repair/maintenance guidance. Before we encode these as demo rules, could you or the appropriate finance owner confirm: (1) that this manual/version is the approved North America source, (2) whether exactly 1,000 meets the threshold, (3) whether the comparison should use unit cost or line amount, (4) whether the below-threshold exception should be demonstrated, and (5) the approved treatment of labor/installation and Nuvolo R&M indicators? We also need the expected results for the Bruegmann, Kimley-Horn, and JC2000A07814 examples. Any unavailable input will be shown as Review rather than guessed.

## Engineering decision

Implementation may proceed using the proposed unapproved snapshot for test fixtures and visibly labeled demo output. Production-like persistence must retain the policy ID/version and the evidence used. Any case depending on an unanswered question must produce `REVIEW`.
