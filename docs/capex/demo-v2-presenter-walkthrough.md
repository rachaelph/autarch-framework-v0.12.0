# CapEx Demo V2 Presenter Walkthrough

> **Historical reference:** the current Thursday presenter sequence and
> implementation boundaries are in the
> [architecture-aligned demo guide](thursday-architecture-demo.md).
> The early readiness statements below predate the successful Friday
> GPT/SQL run and must not be used as current execution status.

## Status of CICT-242

CICT-242 is **in progress**, not closed. It should be closed only after the
presenter can explain the complete flow, identify which values are source
data versus synthetic assumptions, run the demo, interpret every output, and
answer the expected client questions.

## The most important truth to understand

There are two related implementations in this repository:

1. **The earlier V1 operational proof**, which used Blob preparation, Azure
   SQL, an outbox, Service Bus, and a Function-oriented TaskSuggestion path.
2. **Demo V2**, which deliberately simplifies the demo to a direct finite
   Container Apps Job and adds deterministic capitalization and bundling.

Do not present every earlier V1 resource as part of the V2 runtime.

### What is known to be in live Azure SQL

Earlier live acceptance proved:

- One prepared Bruegmann package in `prep.InvoicePackage`.
- Twelve Bruegmann lines in `prep.InvoiceLine`.
- One published package through `pub.v1_InvoicePackage`.
- One `prep.InvoiceOutbox` event that reached `PUBLISHED`.
- 761 active governed US TaskMaster rows in `ref.TaskMaster` and
  `pub.v1_TaskMaster`.
- At least three CICT-187 TaskSuggestion acceptance rows:
  - two `SUGGESTED`;
  - one `UNRESOLVED`;
  - exact replay did not add duplicates.

The known live Bruegmann V1 identifiers are:

| Identifier | Value |
|---|---|
| Run | `run-20261022T031623Z-eee6efc5` |
| Package | `pkg-2a26689e-5097-5cfe-b18c-10d34d75c0da` |
| Invoice | `135d03ac-3598-498a-83fe-d273d046bfea` |
| Event | `evt-06d9fc90-73ce-5e2f-ac87-32b9fbc72841` |
| Correlation | `corr-82167cbd-60d4-5c1d-b854-f6e335fd7b6a` |
| Outbox final state | `PUBLISHED`, attempt count 1 |

### Updated SQL state: November 4

The presenter reported successful application of migrations 007 and 008,
and supplied a column inventory confirming the assessment/bundle tables.
The subsequent `capex.AssessmentRun` query returned **no rows**. Schema
creation is complete for those objects, but no persisted V2 workflow run
has been demonstrated. Do **not** claim the following contain live demo results:

- `capex.AssessmentRun`
- `capex.Assessment`
- `capex.BundleCandidateGroup`
- `capex.BundleCandidateMember`
- `pub.v1_Assessment`

The V2 persistence adapter was validated with a stateful SQL recording
harness. The live GPT-5.1 V2 runs wrote JSON result artifacts, not live Azure
SQL assessment rows.

Migration 009 now prepares additive coverage of the remaining v2.1 model
tables/columns. It preserves runtime identifiers rather than replacing them
with the diagram's incompatible key types. Run
`sql/validation/validate_capex_model_schema.sql` after applying 009 and read
`sql/README.md` for physical differences and deferred relationships. These
schema checks do not require a workflow run ID.

Run
`sql/validation/validate_capex_demo_v2_inventory.sql` from a network path
that can reach Azure SQL before the client demo. It inventories every table,
all live rows, replay groups, key TaskMaster entries, and whether migration
007 is present.

## The one-sentence demo explanation

> “The demo takes three normalized invoice packages, uses governed
> TaskMaster data plus GPT-5.1 only to suggest task codes, applies
> deterministic accounting rules for CapEx/OpEx/Review, identifies possible
> bundle candidates, and preserves every result and assumption as
> auditable evidence.”

## Architecture you should describe

```text
Normalized packages + governed references in Blob
                      |
                      v
        Direct Container Apps finite job
          |        |          |
          |        |          +--> deterministic bundling
          |        +-------------> deterministic capitalization
          +----------------------> governed task determination / GPT-5.1
                      |
             +--------+--------+
             |                 |
             v                 v
       Azure SQL evidence   Result JSON in Blob
```

### Why each Azure resource exists

| Resource | Current planned name | What it does | Why it is needed |
|---|---|---|---|
| Blob Storage | `cictaicusst`, container `landing` | Holds immutable requests, normalized inputs, TaskMaster/policy snapshots, and result JSON | Separates source evidence from compute and makes runs reproducible |
| Container Apps Job | `capex-demo-v2` | Runs one request, produces one result, and exits | The use case is batch processing, not a continuously running API |
| Container Apps Environment | `cict-ai-cus-acaenv` | Supplies managed runtime, networking, and logs | Lets the job reach private resources and gives operators execution evidence |
| ACR | `cictaicusacr` | Stores the exact container image | The demo can identify precisely which code version ran |
| Foundry/OpenAI `gpt-5.1` | `cict-ai-cus-foundry` | Interprets invoice descriptions against a bounded TaskMaster candidate list | Handles language ambiguity without giving the model authority over accounting policy |
| Azure SQL server/database | `cict-ai-cus-sql` / `cict-ai-cus-sqldb` | Stores governed reference data and append-only execution evidence | Supports audit, replay control, SQL reporting, and analyst review |
| User-assigned managed identity | `cict-ai-cus-capex-id` | Authenticates the job to ACR, Blob, SQL, and the model | Avoids API keys, passwords, and connection-string secrets |

The current deployment parameters target the existing
`cict-aisandbox-cus-rg` resource group in Central US. They do not target
`rg_learning`. This was selected to reuse the existing private-networked CICT
environment and identity rather than create a parallel stack. The new
`capex-demo-v2` job has not yet been deployed. If the deployment must instead
use `rg_learning`, its Container Apps environment, networking, SQL access,
managed identity, role assignments, and parameter file must first be
prepared for that resource group.

Service Bus, Azure Functions, Microsoft Agent Framework, and a hosted Foundry
agent are not part of Demo V2 execution.

## Inputs: what is real and what is synthetic

### Real source data

| Input | Real content |
|---|---|
| Bruegmann | Source invoice package, 12 extracted lines, quantities and unit costs where supplied, USD amounts, source file hash |
| Kimley-Horn | Source professional-services invoice, 10 line amounts, USD total, source file hash |
| JC | Source accounting row for project `JC2000A07814`, EUR amount, source task/accounting data, source file hash |
| TaskMaster | 761 active US task codes from the governed source workbook |
| Policy evidence | Reporting Manual, IAS 16, bundling sequence, and table/class diagrams |

Real source values remain unchanged in the normalized artifacts.

### Synthetic or unapproved data

| ID | Synthetic value | Where it affects the demo | What Marie must replace |
|---|---|---|---|
| `SYN-CAP-RULESET-001` | A separate demo-only nature-rule library is enabled | Bruegmann and Kimley task-resolved lines | Approved, versioned operational nature-rule file |
| `SYN-CAP-001` | Task `5225 WIRE SHELVING` is capitalizable by nature | Bruegmann lines resolved to 5225 | Approved treatment of 5225 and exclusions |
| `SYN-CAP-002` | Task `0007 ENVIRONMENTAL` is capitalizable by nature | Kimley-Horn lines 1-9 | Approved treatment of environmental professional fees and required project linkage |
| `SYN-CAP-003` | Exactly USD 1,000 meets the threshold | All North America synthetic assessments | Approved equality rule |
| `SYN-CAP-004` | Threshold is evaluated using unit cost | All North America synthetic assessments | Approved threshold basis and calculation instructions |
| `SYN-CAP-005` | The USD 300/site and USD 30,000/project exception is not auto-applied | Below-threshold Bruegmann and Kimley lines | Approved exception, qualifying spend, aggregation source, and period |
| `SYN-DATA-001` | Kimley quantity is 1 and unit cost equals line amount | All 10 Kimley lines | Authoritative quantity/unit-cost data or approval to use line amount |
| `SYN-CTX-001` | `DEMO-` project/site IDs, `OPEN` status, and bundling-in-scope | All three packages | Authoritative project, site, status, and bundling scope |

### Explicitly not assumed

The demo does not assume:

- Shipping or freight is always OpEx or never capitalizable.
- Reimbursable expenses are automatically OpEx.
- JC source codes map to US TaskMaster.
- EUR can be converted without an approved exchange-rate source.
- `ACTIVE`, `COMPLETE`, or `COMPLETED` are approved project-status aliases.

## What to request from Marie

### Copy-ready email

> Subject: CapEx Demo V2 — policy and source data needed to replace demo assumptions
>
> Hi Marie,
>
> We have completed the technical Demo V2 workflow, but several values are
> intentionally synthetic or awaiting finance confirmation. Before we treat
> the results as Circle K accounting outcomes, could you please provide or
> confirm the following?
>
> 1. The approved operational CapEx/OpEx nature-rule library, including task
>    or category mappings, never-capitalize rules, repair/maintenance rules,
>    priorities, effective dates, scope, and policy citations.
> 2. Whether TaskMaster task `5225 WIRE SHELVING` is capitalizable by nature,
>    including treatment of replacement, repair, consumables, shipping, and
>    merchandising components.
> 3. When TaskMaster task `0007 ENVIRONMENTAL` professional fees are
>    capitalizable versus expensed, and what project or asset linkage is
>    required.
> 4. Whether exactly USD 1,000 meets the capitalization threshold.
> 5. Whether the threshold is evaluated using unit cost, line amount, invoice
>    total, asset total, or project aggregate.
> 6. Whether the USD 300 per-site / USD 30,000 project exception should be
>    enabled, what spend qualifies, and what source and period define the
>    complete project aggregate.
> 7. Authoritative quantity and unit-cost values for the Kimley-Horn lines,
>    or approval to treat line amount as unit cost when quantity is absent.
> 8. The authoritative project ID, site ID, project status, bundling scope,
>    and approved status values for each of the three demo invoices.
> 9. The governed JC-to-TaskMaster mapping, or the applicable regional
>    TaskMaster for the European JC data.
> 10. The approved non-USD exchange-rate source and effective-date rule.
> 11. The historical source and lookback period used when bundling spend
>     across prior invoices.
> 12. The expected finance outcome for every line in the three demo invoices
>     so we can compare the workflow result to an approved answer key.
> 13. Approved treatment of shipping, freight, installation, labor,
>     reimbursables, repair, and replacement costs.
>
> Until these items are supplied, the demo will show truthful results as
> Review and will label the separate synthetic scenario as unapproved demo
> data.
>
> Thank you.

## Processing stages and why each one exists

### Stage 1 — Normalize and validate

The normalizer converts different source formats into one line-level
contract. It preserves missing values instead of turning them into zero.

What it proves:

- The source package has a stable hash.
- Every line has a stable ID.
- Missing quantity, unit cost, task, project, or site remains visible.
- Source warnings travel with the package.

Important source observations:

- Bruegmann invoice total is USD 1,627.50 while extracted line amounts total
  USD 1,528.17. The source warning says tax or another header-level charge may
  explain the difference; the workflow does not invent the explanation.
- Kimley-Horn supplies line amounts but no quantity or unit cost.
- JC is EUR, uses an ungoverned source task code, and is not a complete
  invoice-line export.

### Stage 2 — Governed task determination

The task stage answers: **“What governed work/task code best describes this
line?”**

Order of operation:

1. Accept an exact governed source task when available.
2. Retrieve a small candidate list from the 761-row TaskMaster.
3. Use GPT-5.1 only for candidate-backed language interpretation.
4. Reject any model answer that is not in the allowed candidate list and
   TaskMaster.
5. Return Review when a specific governed task cannot be selected safely.

The model does not see the whole accounting policy and cannot decide CapEx or
OpEx.

Task output fields to explain:

| Field | Meaning |
|---|---|
| `resolutionStatus` | `RESOLVED` or `REVIEW` |
| `suggestedTaskCode` | Governed code or null |
| `matchMethod` | Exact match, LangChain/GPT interpretation, or Review |
| `confidence` | Confidence in the bounded task choice |
| `rationale` | Why that candidate was selected |
| `evidenceRefs` | Source line and TaskMaster candidates used |
| `taskMasterSha256` | Exact governed catalog version |
| `modelDeployment` | Model used when applicable |

### Stage 3 — Deterministic capitalization

This stage answers: **“Given the task, source values, and policy, what is the
line recommendation?”**

It evaluates:

1. Whether a governed task was resolved.
2. Nature of spend.
3. Repair/maintenance and never-capitalize rules.
4. Currency and exchange-rate availability.
5. Project status and required context.
6. Threshold amount, equality rule, and threshold basis.
7. Missing values and unapproved policy settings.

Outputs are `CAPEX`, `OPEX`, or `REVIEW`. This stage makes zero model calls.

### Stage 4 — Deterministic bundling

Below-threshold capitalizable-by-nature lines can be grouped by:

```text
task code + site + project + currency
```

The demo permits grouping across invoice, vendor, and purchase order, but not
across project or currency. A group is evidence for review; it is not an
automatic accounting posting.

### Stage 5 — Persistence and replay

Every workflow run has one immutable ID and canonical result hash.

- Exact replay: accepted without duplicate inserts.
- Same run ID with changed content: rejected.
- Assessment, group, and member rows: one SQL transaction.
- Original AI evidence: never overwritten by a later human decision.

## Azure SQL: what every object is for

### Earlier preparation and publication objects

| Object | Purpose | Known live evidence |
|---|---|---|
| `prep.InvoicePackage` | One prepared invoice header and source identity | One Bruegmann V1 package was proven |
| `prep.InvoiceLine` | Line-level source values | Twelve Bruegmann V1 rows were proven |
| `prep.InvoiceDataQualityWarning` | Non-fatal missing/mismatch warnings | Exists; exact current live rows require inventory query |
| `pub.v1_InvoicePackage` | Read-only governed JSON contract over ready packages | One Bruegmann package was proven |
| `prep.InvoiceOutbox` | Durable event waiting to be published | One event reached `PUBLISHED` |

### Governed task objects

| Object | Purpose | Known live evidence |
|---|---|---|
| `ref.TaskMaster` | Physical governed task catalog with provenance | 761 current active US rows |
| `pub.v1_TaskMaster` | Read-only current active catalog exposed to runtime | 761 rows |
| `task.TaskSuggestion` | One line-level task suggestion with model/catalog provenance | At least three CICT-187 acceptance rows; run inventory for exact total |

`pub.*` objects are views. Data is not separately inserted into them; they
expose governed projections of physical tables.

### Demo V2 accounting objects

| Object | Purpose | Expected synthetic run rows | Live state |
|---|---|---:|---|
| `capex.AssessmentRun` | Run ID, canonical hash, and expected counts | 1 | Created; presenter query returned no runs |
| `capex.Assessment` | One immutable accounting result per line | 23 | Created; workflow results not demonstrated |
| `capex.BundleCandidateGroup` | One header per bundle candidate | 1 | Created; workflow results not demonstrated |
| `capex.BundleCandidateMember` | Exact lines included in each group | 9 | Created; workflow results not demonstrated |
| `pub.v1_Assessment` | Read-only reporting view joining assessments and groups | 23 visible rows | 007 reported applied; workflow results not demonstrated |

## Complete synthetic output

### Package summary

| Package | Lines | Task resolved/review | CapEx | OpEx | Review |
|---|---:|---:|---:|---:|---:|
| Bruegmann | 12 | 9 / 3 | 0 | 9 | 3 |
| Kimley-Horn | 10 | 9 / 1 | 7 | 2 | 1 |
| JC | 1 | 0 / 1 | 0 | 0 | 1 |
| **Total** | **23** | **18 / 5** | **7** | **11** | **5** |

### Bruegmann line results

| Line | Source item | Unit cost | Task | Result | Explanation |
|---:|---|---:|---|---|---|
| 1 | Layover front-retainer component, 4-pack | 253.71 | Review | Review | Candidate suffixes are indistinguishable |
| 2 | Layover front-retainer component, 1-pack | 67.27 | Review | Review | Candidate suffixes are indistinguishable |
| 3 | Reinforced 2-wire dividers, 50-pack | 98.12 | 5225 | OpEx + bundle candidate | Synthetic nature is capitalizable, but unit cost is below threshold |
| 4 | Reinforced 2-wire dividers, 10-pack | 20.83 | 5225 | OpEx + bundle candidate | Below threshold |
| 5 | FlexRoller clear retainer | 41.61 | 5225 | OpEx + bundle candidate | Below threshold |
| 6 | FlexRoller clear retainer | 21.75 | 5225 | OpEx + bundle candidate | Below threshold |
| 7 | Back wire retainer | 15.69 | 5225 | OpEx + bundle candidate | Below threshold |
| 8 | Single-wire Infostrip | 9.07 | 5225 | OpEx + bundle candidate | Below threshold |
| 9 | Double-wire Infostrip | 12.17 | 5225 | OpEx + bundle candidate | Below threshold |
| 10 | Reinforced wire dividers, 20-pack | 58.74 | 5225 | OpEx + bundle candidate | Below threshold |
| 11 | Reinforced wire dividers, 10-pack | 30.20 | 5225 | OpEx + bundle candidate | Below threshold |
| 12 | Shipping | Missing | Review | Review | No approved shipping rule and no unit cost |

The nine 5225 lines form one candidate group totaling USD 455.56. The group
is kept as evidence for future aggregation but does not reach USD 1,000 in
this run.

### Kimley-Horn line results

The source quantity and unit cost are missing. In the synthetic run only,
quantity is set to 1 and unit cost is set equal to the real source line
amount.

| Line | Service | Synthetic unit cost | Task | Result |
|---:|---|---:|---|---|
| 1 | Environmental assessment form | 2,625.00 | 0007 | CapEx |
| 2 | Air quality and greenhouse gas assessment | 5,120.00 | 0007 | CapEx |
| 3 | Biological resources assessment | 0.00 | 0007 | OpEx + bundle candidate |
| 4 | Cultural resources assessment | 3,760.00 | 0007 | CapEx |
| 5 | Health risk assessment | 3,400.00 | 0007 | CapEx |
| 6 | Noise and vibration | 5,400.00 | 0007 | CapEx |
| 7 | Western Joshua Tree site census | 6,300.00 | 0007 | CapEx |
| 8 | Joshua Tree Conservation Act incidental line | 0.00 | 0007 | OpEx + bundle candidate |
| 9 | Environmental project management/meetings | 1,700.00 | 0007 | CapEx |
| 10 | Reimbursable expenses | 500.00 | Review | Review |

Lines 3 and 8 remain ungrouped candidates: zero combined value does not
create a bundle group. Line 10 remains
Review because there is no approved reimbursable allocation or nature rule.

### JC line result

| Source item | Amount | Source context | Result | Why |
|---|---:|---|---|---|
| TLG system | EUR 16,123.79 | European JC accounting data | Review | No governed JC-to-US TaskMaster mapping, no approved European capitalization policy, and no approved EUR rate |

The workflow deliberately does not ask GPT-5.1 to invent a mapping.

### Bundle groups

| Task | Site | Project | Members | Total | Disposition |
|---|---|---|---:|---:|---|
| 5225 | `DEMO-SITE-001` | `DEMO-PROJECT-BRUEGMANN` | 9 | USD 455.56 | Candidate |

This is a within-invoice candidate below the threshold, not a demonstrated
cross-invoice bundle or approved capitalization. The source invoices do not
share the required grouping keys. Cross-invoice grouping is tested separately
with synthetic matching invoices.

## Why truthful and synthetic outputs are both shown

### Truthful current-policy output

All 23 lines are Review because the operational nature rules, threshold
details, project context, regional mapping, and exchange-rate source are not
fully approved.

This is the evidence that the solution is governed: it refuses to fabricate
accounting decisions.

### Synthetic output

The synthetic run demonstrates what the downstream experience will look like
after Circle K supplies the missing policy and source data. Every synthetic
value is labeled, opt-in, separately persisted, and tied to a replacement
question.

Do not tell the client that the 7/11/5 result is the approved accounting
answer.

## Exactly what to show during the demo

### Screen 1 — Architecture

Show the direct-batch diagram in this document.

Say:

> “Blob stores immutable inputs and evidence, the Container Apps Job performs
> one finite run, GPT only helps with governed task interpretation,
> deterministic code owns accounting, and SQL provides audit and replay
> control.”

### Screen 2 — The three inputs

Show the normalized manifest and the three package summaries.

Point out:

- Bruegmann: physical shelving components and shipping.
- Kimley-Horn: environmental professional services with missing
  quantity/unit cost.
- JC: European accounting data with an ungoverned source code and EUR.

### Screen 3 — Governed TaskMaster

Show `pub.v1_TaskMaster` filtered to `0007`, `5225`, `5225A`, and `5225B`.

Explain that the model cannot return an arbitrary code.

### Screen 4 — Task determinations

Show several examples:

- A Bruegmann line resolved to 5225.
- A Bruegmann line held for Review because suffixes are indistinguishable.
- Kimley environmental lines resolved to 0007.
- Reimbursables and JC held for Review.

### Screen 5 — Truthful output

Show the 23 Review result.

Say:

> “This is the honest answer with the information we currently have. The
> system stops instead of inventing policy.”

### Screen 6 — Synthetic register

Show `synthetic-input-register-v2.json`.

Explain the assumption ID, value, affected records, rationale, Marie
question, replacement data, and status.

### Screen 7 — Synthetic output

Show:

- 7 CapEx.
- 11 OpEx.
- 5 Review.
- 1 bundle group.
- 9 bundle members.

Use the line tables above to explain why.

### Screen 8 — SQL evidence and replay

If migration 007 is live, show:

- one `AssessmentRun`;
- 23 `Assessment` rows;
- 1 group row;
- 9 member rows;
- exact replay does not add rows.

If migration 007 is not live, show the deterministic validation JSON and say
that SQL persistence is validated in code but awaiting the authorized Azure
deployment. Do not imply the rows are live.

### Screen 9 — What Circle K supplies next

Show the Marie request and explain that approved data replaces assumptions
without changing the architecture.

## Questions you should be ready to answer

### “Is the model deciding CapEx or OpEx?”

No. GPT-5.1 can only select from bounded TaskMaster candidates. Deterministic
code applies policy.

### “Why are all truthful results Review?”

Because the operational nature rules and several source/context values are
not approved. Review is the safe, intended result.

### “Why do some capitalizable-by-nature lines show OpEx?”

Nature and threshold are separate gates. The synthetic rule says the spend
type may be capitalizable, but the unit cost is below the proposed threshold.
Those lines are expensed at line level while retained as bundle candidates.

### “Why is a USD 0 group created?”

The grouping engine is proving deterministic membership, not claiming the
group qualifies for capitalization. The zero-value lines remain evidence and
do not meet the threshold.

### “Why not map the JC code with GPT?”

That would invent governed master-data mapping. Circle K must provide the
regional mapping or applicable TaskMaster.

### “What happens when finance changes a decision?”

The AI-original evidence remains immutable. A human-final decision is stored
separately so both the original recommendation and final decision are
auditable.

### “What prevents duplicate rows?”

Stable event/run identity, unique SQL keys, a canonical result hash, and
exact-replay comparison.

## Rehearsal completion criteria

Do not close CICT-242 until the presenter can:

- [ ] Draw and explain the direct V2 architecture without notes.
- [ ] Explain every Azure resource and why it exists.
- [ ] Distinguish V1 live SQL evidence from V2 local validation evidence.
- [ ] Explain every synthetic assumption and Marie replacement request.
- [ ] Explain the truthful 23-Review result.
- [ ] Explain the synthetic 7 CapEx / 11 OpEx / 5 Review result.
- [ ] Explain both bundle groups and why they are candidates.
- [ ] Run the local truthful and synthetic commands.
- [ ] Run the SQL inventory from an approved network path.
- [ ] Demonstrate exact replay or show the deterministic replay evidence.
- [ ] Complete at least one timed rehearsal and one question-and-answer
  rehearsal.
