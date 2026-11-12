# CapEx end-to-end demo: presenter script

**Audience:** Circle K. **Target:** Thursday, November 12, 2026.
**Length:** 12-15 minutes, excluding the batch's processing time.

> The model recommends. Application code validates and persists.
> The analyst reviews and decides. No ERP posting occurs.

## What changed, why, and what to say tomorrow

Task-label correction requested after checking the first run: NA
`task.TaskSuggestion.assetClassCode` and its computed `SuggestedAssetClass`
must contain the selected `TaskMaster.description`, **not** either asset
category. For example, taskCode `5220A` keeps that code and displays `SHELVING`,
not `STORE EQUIPMENT`. EU asset-class codes remain unchanged. The earlier
`d6ca5111-e1ab-4f6f-b186-454fad2b1d40` run contains the old mapping and must
not be presented as corrected. The code correction passes task/native SQL,
governed-policy and ADF integration tests, and the three changed producers pass
Pyright using the tested interpreter. Publication/rerun is blocked: the current
Azure login has only read access on `cictaicusacr`, so the safe build failed
before scheduling a new image. Restore the existing approved build access or
refresh the intended login. No corrected image or new RunId has been published.

Earlier SQL-governed publication status: published; its ADF run and worker succeeded,
but it contains the reported task-label mapping defect.
Independent same-run SQL acceptance is still blocked by verifier-start
authorization. Do not describe that final reconciliation as passed.

**ADF itself:** the existing pipeline and activities were retained. Published a new
immutable worker-image digest in its execution override and explicitly enabled
`governedSqlReferences`. The existing processing opt-in remains required.
Classification, accounting, screening and category selection run inside the
worker; they are not new boxes in ADF Monitor. Their individual model calls
appear in same-run `capex.LlmShowcaseRun.EvidenceJson`.

**Why:** earlier processing did not consume all the newly populated policy
tables or populate the primary assessment category. Source-format routing also
replaces the filename-prefix convention, so regional classification is explicit.

Suggested narration:

1. "These four original files contain ten invoices. The two North American
   ZIP sources use TaskMaster and write task suggestions. The two European
   workbooks use assetClassMaster and write asset-class suggestions. European
   codes are never substituted with US task codes."
2. "GPT-5.1 considers the invoice context alongside the SQL policy tables.
   Never-capitalize keywords are not blanket bans: their applicability rules
   matter, particularly for renovations, replacement and installation."
3. "The assessment holds three separate things: the CAPEX/OPEX/REVIEW
   recommendation, a primary slide-category suggestion when supported, and
   independent repair/maintenance and ARO review flags. A potential maintenance
   signal is not automatically an expense conclusion."
4. "ARO means evidence warrants later review. It does not mean we established
   an obligation, calculated a liability, or posted anything to the ledger."
5. "The current SQL thresholds are labelled unapproved synthetic demo values.
   We preserve that label. They specify native unit cost, so missing prices
   remain missing and may require Review. We do not invent quantities, use
   exchange rates, or silently substitute a line total."
6. "Bundling is deliberately deferred. Empty bundle tables are not a model
   finding that no bundling potential exists. The bundling category is disabled
   for this demonstration."
7. "Everything shown belongs to one ADF RunId. We reconcile its original
   sources, actual extracted lines, model evidence and persisted SQL results.
   A completed workflow can contain legitimate Review recommendations."

Not changed: original invoices, historical results, prepared invoice packages,
saved Container Apps job definitions, network/firewall settings, RBAC, or ERP.
Do not run a reset or combine totals from different workflow runs.

## Earlier run: label defect confirmed; do not present as the corrected rerun

- Deployment `capex-sql-governed-cj1c` succeeded at 21:50 UTC on November 11.
  Published image SHA-256:
  `ee6cd5650e97e9377002df37600e5922ddbd3ff3b16893d8977b56077cf16648`.
  Live readback preserves the previous pipeline definition except its worker
  image and governed-SQL option. The saved job is exactly unchanged.
- ADF RunId: `d6ca5111-e1ab-4f6f-b186-454fad2b1d40`.
  ADF succeeded at 22:02 UTC; its named worker
  `capex-task-determination-proof-c2m3cd3` succeeded at 22:01 UTC.
- The completed worker's existing Log Analytics summary reports four sources,
  ten model-processed invoices and 29 actual lines: 21 NA task-suggestion rows,
  eight EU asset-class rows and 29 assessments. It reports 20 resolved task
  codes, six resolved asset classes, 11 primary categories and screening of all
  29 lines. There are ten review documents and one imported review handoff.
  These are worker-reported committed counts, not a separately executed SQL
  acceptance result.
- Accounting reports **0 CAPEX, 11 OPEX and 18 REVIEW**. Do not promise a positive
  CAPEX example from this run. The actual SQL unit-cost rules, missing facts
  and evidence requirements govern the outcome; do not lower thresholds,
  invent prices or restore line-total proxies to force capitalization.
  A null primary category may legitimately accompany accounting Review.
- The worker reports zero supported ARO signals and zero bundle decisions/groups.
  ARO screening is not a liability conclusion; bundling was deliberately deferred.
  Results remain `REVIEW_REQUIRED` / `AWAITING_ANALYST`, with no human approval
  or ERP writes.

### Remaining acceptance action

The read-only independent verifier could not start: both REST and the standard
CLI returned `JobStartOverrideAuthorizationFailed`, requiring
`Microsoft.App/jobs/write`, although the effective-permissions listing appeared
to include it. No permissions or resource settings were changed to bypass this.
Direct replica logs were unavailable (`No replicas found`); the completion summary was recovered
from the environment's explicitly configured Log Analytics workspace.

Restore the existing authorized job-override access, or have the SQL colleague
run the two read-only queries below. In **each** query, change only its first
declaration to:

```sql
DECLARE @WorkflowRunId nvarchar(128) = N'd6ca5111-e1ab-4f6f-b186-454fad2b1d40';
```

1. [`validate_capex_adf_demo_run.sql`](../../sql/validation/validate_capex_adf_demo_run.sql)
   must finish with `ADF_SQL_REVIEW_VERIFIED`.
2. [`present_capex_adf_demo_run.sql`](../../sql/validation/present_capex_adf_demo_run.sql)
   exposes the actual category/flag values, per-invoice coverage and accepted
   model stages for the demo.

No additional tables or imports are currently requested. Do not reset or rerun
the invoice workflow merely to work around this separate verification blocker.

Read-only private-network execution `capex-task-determination-proof-4lh8zrn`
verified the two NA ZIPs, two EU workbooks, all seven reference tables and the
migration-018 assessment/category columns. Counts: 761 task entries, 79 EU asset
classes, three thresholds, three contextual exclusions, 31 maintenance indicators,
22 ARO indicators and seven outcome categories. Do not rerun imports or reset
tables to execute a fresh workflow. Preserve prepared packages and historical rows.

The new worker requires `--sql-persist --governed-sql-references`.
It reads and snapshots SQL policy inputs, screens with SQL indicator rules,
and recommends an enabled category after accounting and independent flag screening.
It rejects changed policy data before committing. The new image and validated
ADF publication are live; same-run independent readback remains required before
claiming final SQL acceptance.

**Policy limitation:** the actual threshold rows are explicitly unapproved
synthetic demo unit-cost rules, not approved manual-derived local thresholds.
Conditional-demo opt-in permits their disclosed use, not finance approval.
No FX, budget-rate conversion or line-amount substitution is performed.
Missing printed unit costs require accounting Review for capitalizable nature.
The demo-run date is the disclosed policy-effective-date basis, not approval of
historical booking treatment. Bundling is deferred, not a negative finding.

## SQL presentation order: what each table proves

Use ONE completed `WorkflowRunId` throughout. Never use unfiltered table totals
or combine favorable results from different runs. Source file count, invoice
count and invoice-line count are different: four files represent ten invoices,
with multiple lines for Bruegmann and Kimley-Horn.

Run [`present_capex_adf_demo_run.sql`](../../sql/validation/present_capex_adf_demo_run.sql)
after changing only its first run-ID declaration. It provides the summary,
per-invoice coverage, source manifest, warnings, linked task/accounting rows,
conditional policy assumptions, bundling and model-call evidence.
Then run
[`validate_capex_adf_demo_run.sql`](../../sql/validation/validate_capex_adf_demo_run.sql)
with that same ID for reconciliation. Both are read-only and now require the
EU/tag migrations above.

### EU classification and potential tags

For original XLSX sources, show `ref.assetClassMaster`: 79 real EU
asset classes with 869 preserved account mappings from the supplied workbook.
Then show `task.assetClassSuggestion`, **not** `task.TaskSuggestion`, for those
invoice lines. The shared `capex.Assessment` links by `AssetClassSuggestionID`
and retains `SuggestedAssetClassCode`; original NA ZIPs keep task links.
Review/null remains legitimate when source evidence does not justify a code.

After migration 018, show `capex.Assessment.RepairMaintenanceFlag`, `AroFlag`
and `PotentialTagsJson`. The two flags no longer live on suggestion tables;
legacy tag JSON and potential-bundling metadata remain there for compatibility.
Show the potential flags alongside their reasons and original source quotes.
Multiple tags are allowed. NULL means not screened; false/`[]` means screened
with no supported signal. These are review aids, not final classifications.
Existing assessment/bundle tables still hold the actual bundling proposals;
the keyword tag does not create another bundle engine or approve a group.
ARO is marked for later review only: no obligation or liability is calculated.
The reporting manual is not an approved runtime accounting rule set.

`ref.AssessmentOutcomeCategory` holds the seven slide labels and guidance,
with potential bundling disabled for this demo. `CategorySuggestion` references
that catalog separately from the CAPEX/OPEX/REVIEW recommendation; the older
`CapExOpExSuggestion` computed alias is retained. The governed mode asks GPT-5.1 for a primary category constrained by the enabled
catalog, actual accounting result, Nuvolo provenance and supported ARO evidence.
Null with a reason remains correct when none of the seven categories applies.
An independent flag is retained even when another primary category is selected.

### Main walkthrough: these are the relevant current stores

| Order | SQL object | What to show and say |
|---|---|---|
| 1 | `ref.TaskMaster`, exposed through the **view** `pub.v1_TaskMaster` | The real, published task/category catalog: codes, descriptions, categories and region. The worker checked 761 active US entries against its pinned snapshot. "The model suggests existing codes; this is not a physical-asset register or an approved European mapping." The worker reads the view, not an invented task list. |
| 2 | `mvp_review.imports` | One committed run envelope, keyed by `run_id`. Expand `payload` for the four-file manifest and hashes, ten invoice packages, normalized source lines, extraction warnings, policy snapshots and results. "This ties the source evidence and recommendations to one ADF run." It is a persisted handoff, not proof that an analyst reviewed it. |
| 3 | `mvp_review.documents` | Per-invoice document identity, hash and stored source-document bytes. "We can trace each invoice back to the original evidence." Ten invoice records do not mean ten distinct input files; several invoices share a workbook source. Avoid dumping binary content in the presentation. |
| 4 | `task.TaskSuggestion` / `task.assetClassSuggestion` | Original NA ZIPs receive TaskMaster `taskCode`; EU XLSX sources receive `assetClassCode` from `ref.assetClassMaster`. Show line identity, resolution/confidence/rationale; filter `correlationId` to the ADF RunId. Null/Review is explicit uncertainty, not a fabricated mapping. |
| 5 | `capex.AssessmentRun` | The accounting run header, keyed by `WorkflowRunId`, with timestamps, provenance and run counts. "This is the assessment execution record, not an individual CapEx/OpEx recommendation or a finance approval." |
| 6 | **`capex.Assessment`** | **The CapEx versus OpEx suggestions.** Show line identity, `TaskSuggestionID` or `AssetClassSuggestionID`, `SuggestedAssetClassCode`, nature, recommendation, outcome basis, threshold, rationale, missing inputs and model provenance. "Capitalizable by nature can still mean OpEx below the demo threshold." Link to the corresponding suggestion and distinguish model-backed advice from classification/accounting exceptions. |
| 7 | `capex.BundleDecision` | No rows for the current governed run: bundling is deferred. This is not a model `NO_BUNDLE` finding. Historical runs may have dispositions; never mix them into this presentation. |
| 8 | `capex.BundleCandidateGroup` | Candidate group identity, combined value, task or EU asset class, site/project, disposition and rationale, **if a supported group exists**. "These are proposals, not approved assets." Zero rows is legitimate when the invoices lack a governed common asset/site/project relationship. |
| 9 | `capex.BundleCandidateMember` | The exact invoice lines belonging to a proposed group. "This is how we trace a combined proposal back to its contributing invoices." It should also be empty when there are no groups. Never force a positive bundle merely to populate these tables. |
| 10 | `capex.LlmShowcaseRun` | Same-run model deployment, status, response counts, hashes and `EvidenceJson`. Expand `calls` for task/EU asset classification, SQL-governed accounting, potential-tag screening and primary-category suggestions; PDF calls are separate in `extractionCalls`. Show the SQL reference snapshot hash and original unapproved threshold labels. No bundle model call is made. Actual model calls do not certify business correctness. |

An optional published reporting **view**, `pub.v1_Assessment`, flattens parts
of the assessment/bundle data. It is not another processing stage or another
set of recommendations. The presenter query uses native tables so the
conditional basis and direct task linkage are visible.

### How the model decides "capitalizable"

The accounting request contains the source/validated invoice package, proposed
task and asset category, a pinned policy snapshot and application-computed
line constraints. GPT interprets the expense's nature from that supplied
context; it does not browse SQL or independently query the reference tables.

`NatureResult=CAPITALIZABLE` means potentially capitalizable **by nature**,
not an approved CapEx outcome. The separate `Recommendation` also accounts
for the supplied monetary policy: capitalizable equipment below the applicable
demo threshold can receive an OpEx recommendation. Routine repair/maintenance
or never-capitalize signals must not produce a CapEx recommendation.

The worker reads the published TaskMaster for task suggestions. It does **not**
currently read SQL threshold, exclusion, maintenance, PartMaster, Project,
Site or PO tables as accounting inputs. Thresholds come from the supplied
policy snapshot. The snapshot explicitly records that operational nature
rules are unapproved and the nature-rule library is not supplied. Model
interpretation is therefore conditional advice, not a fully governed
accounting rule-engine result. Code checks response consistency and threshold
arithmetic; that does not independently certify semantic accounting judgment.
Use same-run `capex.LlmShowcaseRun.EvidenceJson` to inspect the exact inputs
and response behind `capex.Assessment`.

### Three disclosures to say while showing the results

1. **Real inputs:** original invoices/workbooks and the checked TaskMaster.
   Missing quantities, unit prices, asset identity and Site/Project mappings
   are not fabricated.
2. **Demo policy/context:** explicitly unapproved thresholds of USD 1,000,
   EUR 1,000 and DKK 7,500, with no FX conversion; US task catalog use across
   regions is a demo reference, not a governed mapping. A missing unit price
   remains null. Where used, the source line amount is an explicitly labeled
   **conditional expenditure proxy**, not a printed unit price.
3. **Recommendations, not approvals:** inspect `OutcomeCategory` and
   `Rationale`, not just `Recommendation`. Kimley's actual archive header can
   corroborate USD separately from a missing PDF currency value. This does
   not waive arithmetic/source validation. Power Apps review and ERP actions
   remain future work.

### Supporting schema: explain as architecture, not current-run evidence

These are repository-defined supporting objects. Do not imply that this ADF
worker populated them, that their business data is approved, or that a table's
existence proves the corresponding integration is complete. If an audience
member asks to inspect them, establish their actual live contents separately.

| Objects | Talking point and current boundary |
|---|---|
| `prep.InvoicePackage`, `prep.InvoiceLine`, `prep.InvoiceDataQualityWarning` | Operational normalized-invoice storage and data-quality warnings for the earlier SQL intake path. Current ADF reads the pinned Blob sources and retains its run's packages/evidence in Blob plus the SQL handoff; do not promise fresh ADF rows in these tables. |
| `pub.v1_InvoicePackage` (**view**), `prep.InvoiceOutbox` | Earlier published ready-package contract and intake publication queue. Not the current ADF-to-Container-Apps start mechanism. |
| `prep.Invoice` | Structured invoice-header integration model. Do not substitute historical rows here for the current run's source manifest/document evidence. |
| `prep.PurchaseOrder`, `prep.PurchaseOrderLine` | Intended PO/line linkage and coding/project/site context. The current demo does not invent a PO match or an authoritative asset relationship from a printed identifier alone. |
| `prep.Project`, `prep.Site` | Governed project status, budget, bundling scope and site context needed for stronger business decisions and applicable grouping. Schema existence is not proof that invoices have validated mappings. |
| `ref.CapitalizationThreshold` | Intended governed regional/currency policy reference. The current conditional demo uses disclosed policy snapshots; do not describe them as approved rows read from this table. |
| `ref.NeverCapitalizeException`, `ref.RepairMaintenanceIndicator` | Intended governed exclusion and repair/maintenance signals. Explain these policy concepts without claiming a live reference-table integration that the current run did not perform. |
| `ref.PartMaster` | Intended versioned vendor/part-to-task mappings and their confirmation/conflict status. Better governed mappings are a future way to reduce ambiguous suggestions; the demo does not fabricate approved part mappings. |
| `prep.CodingDistribution` | Existing approved invoice coding/distribution model. It is not where the current worker stores proposed CapEx/OpEx advice. |
| `review.ReviewCase`, `review.ReviewDecision` | Future analyst case and decision records. Real analyst review/audit and the Power Apps experience are explicitly deferred. |
| `review.BundleDecision` | Human bundle decision model, distinct from current **model** decisions in `capex.BundleDecision`. No automated human approval is claimed. |
| `review.ApprovedCoding` | Future approved coding output, not the current recommendation table. |
| `review.POPAHandoff`, `ops.Outbox` | Intended downstream handoff/outbound delivery. Do not claim ERP/POPA submission; the current worker performs no ERP writes. |
| `mvp_review.decisions` | Versioned review-decision store available to the separate review/API path. The current ADF worker commits pending handoffs/documents, not real analyst decisions. |
| `mvp_batch.runs`, `mvp_batch.checkpoints`, `mvp_batch.attempts` | Separate batch/restart/checkpoint implementation stores. ADF's current single-job path is tracked by ADF execution, immutable run artifacts and native assessment/model tables; do not require these stores to be populated for this flow. |

**Suggested story:** real source evidence -> real reference catalog -> proposed
task -> conditional CapEx/OpEx advice -> per-line bundling evaluation ->
traceable SQL persistence. Then show the future review/approval/handoff boundary.
Spend most of the presentation on task suggestions, `capex.Assessment` and
`capex.BundleDecision`; supporting schema is an appendix, not evidence that
every production integration is already implemented.

## Historical baseline: November 11, 14:56 UTC

**Current scope ends at SQL persistence.** Power Apps analyst review/audit is
a later phase, explicitly deferred by the user. Pending review records do not
mean a Power App or actual analyst action has been delivered.

The actual ADF-to-model-to-SQL flow now completed:

| Evidence | Observed value |
|---|---|
| ADF RunId | `4156fe29-9f0a-42cb-8cb3-ce96cf82f6bb` |
| ADF status | Succeeded; `2026-11-11T14:51:38.5436945Z` to `2026-11-11T14:55:39.5295244Z` |
| Exact worker execution | `capex-task-determination-proof-q4l3jjl`, Succeeded |
| Image | ACR cj14, `e843176c81a289f68322fb64ab9c210ec76241f604747f6a12b0bcaaaa6f7e7a` |
| Inputs | Four real files, ten invoice packages, seven excluded ledger records |
| Persisted lines | 29 task rows and 29 linked accounting assessments |
| Actual model requests/responses | 5 / 5: two PDF extractions, one task request, one accounting request, one batch bundling request |
| Model-backed coverage | Bruegmann: eleven extracted lines; other nine packages: eighteen non-model Review lines |
| Accounting recommendations | 0 CapEx, 0 OpEx, 29 Review; eleven model-backed, eighteen reference/extraction exceptions |
| Task/nature outcomes | Zero resolved task codes; model classified eleven lines as CAPITALIZABLE nature, not approved CapEx |
| Bundling | Eleven persisted model `NO_BUNDLE` decisions; zero groups/members |
| SQL proof | `ADF_SQL_REVIEW_VERIFIED`: ten documents, 29 task/assessment lines, eleven model bundle decisions, eighteen non-model Review lines |
| Independent verifier | `capex-task-determination-proof-bmutu5m`, Succeeded; all three real model-stage responses and presenter queries verified |
| Final marker | `MODEL_WORKFLOW_COMPLETED`, `REVIEW_REQUIRED`, SHA256 `c81531cc0e78e7cf11433333b9ee2b11dcfc84c0812d7b846bcf98459977112c` |
| Production actions | No human approvals and no ERP writes |

**Do not turn this into a positive-result claim:** the model actually ran the
task and accounting stages, but the observed recommendations remain Review.
Do not say that all invoices received an assigned task code or that this run
produced CapEx/OpEx approvals. A green run proves technical completion, not
that missing business evidence has been resolved.

**Why every recommendation is Review:** Bruegmann's model-backed lines have
capitalizable nature, but task codes remain unresolved and policy settings are
unconfirmed demo assumptions. Kimley-Horn is blocked by missing extracted
currency evidence; the eight workbook packages lack matching regional/native
references. These are different reasons, not twenty-nine copies of one
technical failure.

### The five-screen presentation

1. **Storage inputs:** show both PDF archives and both workbooks. Say:
   "These invoices are real. Four files represent ten invoice packages."
2. **ADF Monitor:** open the exact RunId above. Explain discovery/opt-in,
   safe job start, named-execution polling, and completion-marker checks.
   Say: "ADF orchestrates; the three model stages run inside the worker."
3. **Run artifacts:** show `intake.json`, a PDF extraction audit,
   `reference-exceptions.json`, `llm-evidence.json`, and `batch.json`.
   Both PDF dates are now extracted correctly. Bruegmann's incomplete
   adjustment evidence is retained as a provisional warning. Kimley-Horn
   still lacks extracted currency evidence and is blocked. Eight workbook
   packages still lack matching regional/native-currency references.
4. **SQL results:** run
   [`present_capex_adf_demo_run.sql`](../../sql/validation/present_capex_adf_demo_run.sql),
   changing only its first `@WorkflowRunId` declaration to this completed RunId.
   Its result sets show the source manifest, warnings, linked task/accounting
   rows, bundle decisions/groups, real model-stage counts and demo policy.
   Then run
   [`validate_capex_adf_demo_run.sql`](../../sql/validation/validate_capex_adf_demo_run.sql)
   for the same RunId to demonstrate reconciliation. Neither query creates
   tables or changes data.
5. **Current limits / next phase:** say:
   "The recommendations are unapproved. Currency/reference gaps, unresolved
   task mapping and absent governed asset context produce explicit Review or
   no-bundle results. Power Apps review and any downstream ERP action are
   future work."

### What is real, dummy and still to build

- **Real business inputs:** the original invoices/workbooks and SQL
  `pub.v1_TaskMaster` (761 verified entries). No fabricated invoice amounts,
  dates, FX rates, Site/Project IDs or task codes were inserted.
- **Dummy/demo assumptions:** the supplied USD policy snapshot, including
  the proposed USD 1,000 unit threshold and unapproved business rules.
  Any separate synthetic positive-bundle example must be labeled separately.
- **Actual execution, not canned responses:** ADF/job execution, GPT
  requests/responses, model rationales, SQL writes and document hashes are
  genuine. Their outputs are *demo recommendations*, not approved accounting.
- **Explicit demo behavior:** this publication sets
  `pdfRecommendationMode=provisional-demo`. Missing date, quantity/unit-cost
  or adjustment details may remain visible warnings when reliable core
  evidence exists. Strict is still the CLI/template default. Low confidence,
  missing currency/core evidence and inconsistent known arithmetic remain
  hard blockers. No warning is silently converted into a source fact.
- **Still needed:** verified currency evidence for Kimley-Horn; governed
  European task/native-currency policies; better validated task retrieval/
  domain mappings; approved accounting policy; verified mapped Site/Project/
  physical-asset context; Power Apps with authenticated decisions/audit;
  approved downstream integration and production operational validation.
  Printed source identifiers are not automatically a governed asset mapping.
- **Not in this increment:** invoice parallelism and Azure OpenAI Batch API.
  This is one batch worker with sequential invoice calls, multiple lines
  per invoice prompt and a final cross-invoice bundling call.

The shared model layer still contains legacy fixture terminology in some
prompt/evidence references. Use the actual source hashes, manifest and ADF
provenance to identify this original-source run; do not describe the real
invoices as synthetic because of those inherited labels. Production
provenance terminology should be cleaned up before release.

## Required disclosure: real inputs versus demo data

### Demo intake gates versus production intake

`SelectDemoFiles` is the hardcoded allowlist for the four demonstration files.
Production replaces it with supported-file/location rules or an event-driven
arrival, while excluding outputs and preventing duplicate processing.

`RequireAllSources` checks that all four expected demo sources were discovered.
It prevents an accidentally incomplete rehearsal. Production does not wait for
these four files: individual intake validates the arriving file's readiness,
supported format and duplicate identity. A business-defined batch can instead
validate a manifest or expected delivery count, with explicit missing-file
handling. Do not block unrelated invoices unless complete-set processing is an
actual business requirement.

### What StartJob hands off to the worker

`StartJob` starts one execution of the existing Container Apps Job using the
pinned execution-override image, input/output locations, reference hashes,
processing options and ADF RunId. The saved job's historical name does not
describe the entire worker's current capability.

Inside that execution, the worker verifies SQL TaskMaster/access, normalizes
the four sources into ten invoice packages, extracts/validates original PDFs,
retrieves task candidates and obtains validated GPT task suggestions, obtains
accounting recommendations, evaluates cross-invoice bundling, then atomically
persists the native results and evidence in SQL. It publishes the completion
marker last, after successful persistence/readback. Missing facts remain
visible; conditional demo advice is not finance approval.

`StartJob` does not synchronously perform those stages itself. It obtains the
named execution identity; later ADF activities poll that exact execution and
check the marker. No ERP posting or analyst approval occurs inside the worker.

State this at the opening and again before showing recommendations:

> The original invoices and the source TaskMaster are real Circle K business
> inputs. Everything else we supply as business data for this demonstration
> is dummy/demo data, including policy settings, thresholds, supplemental
> context and synthetic examples. The generated suggestions, assessments,
> bundle proposals and analyst actions are demo/test outputs, not approved
> accounting decisions or production transactions.
>
> The Azure execution, source hashes, SQL persistence and audit records are
> genuine evidence of what this demo actually did. They are not fabricated
> results. We do not change the invoices or invent missing source facts.

| What the audience sees | How to label it |
|---|---|
| Original PDF invoices and selected invoice rows in the two workbooks | Real source inputs; retain the original files and hashes. |
| Published SQL `pub.v1_TaskMaster` and its checked catalog snapshot | Real source task/category catalog; the live worker must verify it against the pinned reference before inference. Not a physical-asset register. |
| Accounting policy, USD threshold and any added business context | Dummy/demo assumptions, not approved Circle K policy or verified business facts. |
| Task suggestions, CapEx/OpEx assessments, bundle proposals and review actions | Demo/test outputs generated or recorded during the demonstration; not posted or approved financial decisions. |
| Separate positive-bundle example | Entirely synthetic; never mix its amounts, context, RunId or counts with the real invoices. |
| ADF/job IDs, model-call evidence, persisted rows and analyst audit | Actual demo execution evidence; show only observed, same-run results. |

Missing Site/Project, asset identity or regional policy stays unresolved. Do
not insert dummy context into the real-invoice run to force a desired outcome.
If showing a supplied dummy context example, keep it in the separately labeled
synthetic exhibit.

## Read this before presenting

The current ADF-to-SQL evidence is recorded above. Older preparation and
failure records below are historical, not the current readiness state.

### November 11 approval and continuation

Ehsan approved the UAMI approach. A live read confirmed Container Apps Jobs
Contributor assigned to UAMI principal `cf7d7929-5beb-426b-bd19-493c5c0c506d`
at the existing proof-job scope. Following refreshed PIM sign-in and completed
official validation, both approved Incremental deployments succeeded on
November 11: `capex-adf-uami-identity-20261111` and
`capex-adf-uami-pipeline-20261111`.

The approved configuration is deployed from
[`capex-adf-identity.bicep`](../../infra/data-factory/capex-adf-identity.bicep)
and the existing pipeline template's opt-in `armCredentialName=cred_capex_uami`.
It retains the system identity, current factory properties/tags, Blob linked
service and saved jobs. Only ARM Web activities switch to the UAMI.

Live readback verified the retained system identity, attached existing UAMI,
`cred_capex_uami` resource ID, and all three ARM activities' UAMI authentication.
The Blob linked service and saved proof/Friday image digests are unchanged.
The worker override is now pinned to build cj14 and `processInvoices` defaults to
false. Factory trust/security properties were preserved in the final preview.
[`Test-CapexAdfUami.ps1`](../../tools/Test-CapexAdfUami.ps1) records fresh
backups and validates/previews only; it does not deploy.

### Historical November 11 cj13 run: persistence verified, model stages blocked

- ADF RunId: `54208739-dea8-462b-9778-98ff7f0e876d`, **Succeeded** at
  `2026-11-11T14:29:49.8873952Z`.
- Exact worker execution: `capex-task-determination-proof-2sorcdy`,
  **Succeeded**. ACR cj13 digest:
  `3f9239c659b7f36372e8e7eba75a7b763ba26fe4bac1af101485ae30a8c26a11`.
- Four real source files, ten invoice packages, thirty lines, seven excluded
  ledger records. Two actual GPT extraction requests/responses.
- **Zero task/accounting/bundling model requests.** All ten packages were
  routed to non-model Review. SQL contains thirty Review task rows and thirty
  Review assessment rows, zero model bundle decisions/groups, ten review
  documents and one imported review handoff. These are exception records,
  not successful GPT task suggestions or CapEx/OpEx recommendations.
- Run-scoped `batch.json` stage is `REFERENCE_REVIEW_COMPLETED`, status
  `REVIEW_REQUIRED`. SHA256:
  `a3d5c9e5d6ad4ce6d434da9319594e4bb24700fecace6c5556b650dda8557066`.
- Read-only verifier execution `capex-task-determination-proof-wv07ksm`
  **Succeeded**, running
  [`validate_capex_adf_demo_run.sql`](../../sql/validation/validate_capex_adf_demo_run.sql).
  It returned `ADF_SQL_REVIEW_VERIFIED`: ten documents, thirty task/assessment
  lines, zero model bundle decisions and thirty non-model Review lines.
  Source document hashes reconciled. No analyst decisions exist for this run.

**Why the model decision stages did not run:**

| Source | Observed extraction/reference gate |
|---|---|
| Bruegmann | Extraction omitted invoiceDate; header adjustment evidence was incomplete. |
| Kimley-Horn | Extraction omitted invoiceDate and currency evidence; the service invoice has no quantity/unit-cost columns, and header adjustment evidence was incomplete. |
| Eight workbook invoice packages | No governed European TaskMaster/native DKK or EUR policy; no FX conversion is authorized. |

Both original PDF first pages visibly contain dates: Bruegmann August 26,
2026; Kimley-Horn July 31, 2026. **Do not describe the extraction omissions
as dates missing from the source.** No source facts, artifacts or SQL records
were patched to force an eligible result. No validation gate was weakened.
Before another paid full run, resolve PDF extraction quality and agree how
source-absent service quantities/adjustment evidence should affect eligibility.
The requested three-stage demo and browser analyst audit remain incomplete.

On the prepared private VM, the existing explicit CLI-user review mode can
open this imported run. From the extracted repository, use this single line:

```powershell
$env:CAPEX_SQL_SERVER='cict-ai-cus-sql.database.windows.net'; $env:CAPEX_SQL_DATABASE='cict-ai-cus-sqldb'; .\.venv-review\Scripts\python.exe -m autarch.capex.demo_v2_review --store azure-sql --sql-auth azure-cli serve --workflow-run-id 54208739-dea8-462b-9778-98ff7f0e876d --port 8765
```

Open the loopback review page on that VM. A real user still needs to inspect
the evidence and record an explicitly labeled demo action (for example Defer
with a reason); no action or browser verification is claimed here.

Earlier November 11 attempts are not this successful run: `405d2382-...`
failed initial SQL login timeout; `ca0fcdbe-...` passed SQL but exposed workbook
validator reuse. The validator was corrected and its Python 3.12 model
construction smoke test now runs during image build. The same-image SQL
diagnostic `capex-task-determination-proof-nlr4w2r` verified private DNS/TCP,
UAMI token acquisition and all 761 TaskMaster entries with no model calls or
SQL writes.

### Historical live preparation update: November 10, 20:07 UTC

- Corrected worker build `cj12` was published to the existing ADF pipeline.
  Digest: `81d581aac57c9552dadc66af70a8f0caccfb69a9fb3991e08467058d1f750d72`
  in `cictaicusacr.azurecr.io/capex/demo-v2-adf`.
- SQL preflight execution `capex-task-determination-proof-i3syk3y` succeeded
  using that image and the shared SQL connection helper: 761 TaskMaster entries,
  zero SQL writes and zero model calls. Direct SELECT/INSERT grants on the
  three review tables supplied access without review-role membership.
- Image-update preview showed only the existing pipeline modified. Publication
  succeeded; `processInvoices` still defaults to false. Both saved proof and
  Friday job image definitions were independently verified unchanged.
- Original-invoice ADF run `6baba80b-6cb7-469a-b0ab-b351f40987e8` started at
  `2026-11-10T20:03:54Z`. Discovery, all-source selection and saved-job safety
  gates succeeded. `StartJob` failed with `JobStartOverrideAuthorizationFailed`:
  Azure requires `Microsoft.App/jobs/write` for execution-template overrides.
  The inspected `Container Apps Jobs Operator` role does not include that action.
  No worker execution, model calls or batch SQL writes occurred for this run.
  This is not a completed rehearsal; output counts, SQL reconciliation and
  analyst audit remain unverified.
- The live pipeline was independently read back with 12 top-level activities
  and `lastPublishTime=2026-11-10T20:02:43Z`; the factory has no Git repository
  configured. Task suggestion, accounting and bundling execute inside the
  worker launched by `StartJob`, not as separate ADF activities.

### Historical platform-owner handoff: execution override permission

The approved temporary ADF system-identity exception needs a further explicit
authorization decision before another ADF run:

- Principal: `c631d874-9b97-4a98-9eac-eaf011d211e5`
  (`cict-ai-cus-capex-adf` system-assigned identity).
- Missing action: `Microsoft.App/jobs/write`, in addition to the existing
  job read/start/status actions.
- Requested maximum resource scope:
  `/subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourceGroups/cict-aisandbox-cus-rg/providers/Microsoft.App/jobs/capex-task-determination-proof`.
- Reason: Azure authorizes a per-execution image/command/arguments override
  against the job write action, even though the saved job definition is not
  changed by this pipeline.

This action also permits saved-job modification at that scope; it is broader
than start-only access. Have the platform owner approve and choose the narrowest
permitted role/assignment. Do not silently add Contributor at resource-group
scope, attach another identity, change saved jobs or create a role definition.
If the permission is declined, the current existing-only ADF design cannot
launch the override unchanged. A manual worker demonstration must be labeled
as manual, not as a successful ADF end-to-end run. Do not retry until effective
authorization has changed and propagated.

### Preparation while authorization is pending

- The local review CLI help check passed, and 21 focused review/document/audit
  tests passed on November 10. These are local/harness checks, not proof of a
  live analyst action against Azure SQL.
- Run the operator guide's
  [review-host prerequisite checks](thursday-architecture-demo.md#opt-in-original-pdf-extraction-and-sql-review-handoff)
  on the intended VM. Confirm the Python environment, ODBC driver, private SQL
  reachability and an already-assigned authorized identity before relying on
  the browser walkthrough. Alternatively, the explicit local-review
  `--sql-auth azure-cli` mode can use an already-authorized user's CLI sign-in
  without attaching an identity; grants to the worker do not authorize the user.
- Practice the opening, architecture explanation, real/demo disclosure and
  honest Review/zero-bundle explanations now. Leave the live evidence card
  incomplete until an actual batch and analyst audit have been verified.
- The approved UAMI route is now deployed and live-verified, as recorded above.
  Do not confuse that configuration success with completed model decision
  stages or an actual analyst review.

### Historical baseline before these live updates

At initial preparation on November 10:

- Actual ADF discovery of all four selected files had succeeded.
- The published `pl_capex_intake_check` still had only its `Dataset`
  Get Metadata activity, with no processing parameter.
- The initial read-only role query returned no job-scoped or inherited
  assignments for ADF's system identity. After Ehsan's response, a new check
  confirmed `Container Apps Jobs Operator` on `capex-task-determination-proof`
  for that identity as an explicitly temporary demo exception.
- Ehsan reaffirmed UAMI-based authentication as the agreed target. ADF is
  currently system-assigned only, with no UAMI attached or credential object.
  The worker already uses `cict-ai-cus-capex-id`. Do not describe the temporary
  ADF system-identity grant as completion of the UAMI design.
- The proof job's saved image, shared identity, manual/single-replica settings,
  zero retries and 900-second timeout were unchanged.
- The expanded worker/review path passed local tests, including actual PDF
  rendering. The new path's model responses were mocked, and SQL transaction
  tests used a DB-API harness, not a live SQL Server.
- No new worker image build, ADF publication or full live execution was performed
  during this preparation.

**Use the live sequence below only after the readiness gate passes.**
Otherwise use the explicitly labeled fallback near the end. Local tests and a
green file-discovery activity do not prove the new cloud path works.

## 1. Before the meeting: readiness gate and tabs

### Go/no-go checklist

- [x] ADF has effective authorization for execution overrides at proof-job
      scope, including the separately approved `Microsoft.App/jobs/write`
      action. Jobs Operator alone was proven insufficient by the live run.
- [x] State the identity mode accurately: the three ARM Web activities use
      approved `cred_capex_uami`; the existing Blob linked service retains
      its system-identity connection.
- [x] The worker identity's batch SQL effective permissions are verified.
      Migration 014 already exists; do not ask for new tables by default.
      The corrected-image preflight passed the required permissions and real
      TaskMaster comparison. Analyst decision writes still need live validation.
- [x] The image has been built in the existing registry and its immutable
      digest recorded. The image build's ODBC/PDF/CLI smoke checks passed.
- [x] The separately approved ADF update was published to the existing pipeline
      and dataset, without new services or changes to saved job definitions.
- [x] An actual exception-routing/persistence rehearsal passed within 900 seconds.
- [x] Its named job execution succeeded, its run-scoped `batch.json` exists,
      and SQL/review results reconcile for the **same ADF RunId**.
- [x] An eligible original-source batch actually exercises GPT task,
      CapEx/OpEx and bundling stages: cj14 RunId in the current evidence card.
- [ ] The existing private SQL presentation client is open and ready.
      Do not depend on signing in, installing drivers or changing networking
      during the presentation.
- [x] The actual results are known: extracted lines, task/accounting outcomes,
      exceptions, bundle decisions/groups and model attempts. Do not copy
      counts from mock tests or Friday's run.
- [ ] Any supplemental synthetic example is visibly labeled and kept separate.
- [ ] The opening and recommendation screens distinguish real invoices and
      TaskMaster from dummy/demo business assumptions and demo/test outputs.

### Open these tabs in advance

1. Storage account `cictaicusst`: container `landing`, folder `capex/input`.
2. ADF Studio for `cict-ai-cus-capex-adf`: Author and Monitor views of
   `pl_capex_intake_check`.
3. Container Apps Job `capex-task-determination-proof`: execution history/logs.
4. Storage output for `landing/capex/output/adf-runs/<ADF-RunId>/`.
5. The existing private SQL connection to
   `cict-ai-cus-sql.database.windows.net`, database `cict-ai-cus-sqldb`.
6. The current evidence card and read-only presenter SQL query. Power Apps
   review is future work; no review-browser demonstration is required.
7. The frozen Friday fallback, clearly labeled as historical evidence.

Prepare this small evidence card from the **completed live rehearsal**:

| Field | Fill from the actual run |
|---|---|
| ADF RunId / workflowRunId | `4156fe29-9f0a-42cb-8cb3-ce96cf82f6bb` |
| Named job execution | `capex-task-determination-proof-q4l3jjl` |
| Worker image digest | cj14, `e843176c81a289f68322fb64ab9c210ec76241f604747f6a12b0bcaaaa6f7e7a` |
| Output folder | `landing/capex/output/adf-runs/4156fe29-9f0a-42cb-8cb3-ce96cf82f6bb/` |
| SQL verification | `ADF_SQL_REVIEW_VERIFIED` for that RunId |
| Source / package / actual line counts | 4 / 10 / 29; seven excluded ledger rows |
| CapEx / OpEx / Review and bundle groups | 0 / 0 / 29; eleven NO_BUNDLE decisions, zero groups |
| Review run and audit | Same RunId imported; Power Apps/analyst audit deferred |

The same RunId must connect the screens. Never select "latest execution" as a
substitute for the execution returned by this ADF run.

## 2. Opening: explain the whole flow (0:00-1:00)

**Show:** ADF pipeline overview, or the flow below.

```text
Existing Blob input folder
  -> ADF discovers and checks the four selected source files
  -> ADF starts the existing proof job with a one-execution worker override
  -> Worker verifies SQL access and the actual SQL TaskMaster catalog
  -> Worker extracts original PDFs / normalizes workbook invoice rows
  -> Code validates source facts and routes unsupported inputs to Review
  -> Eligible invoices receive model task, accounting and bundling recommendations
  -> Native SQL results, model evidence and analyst-review handoff commit together
  -> Worker publishes batch.json last; ADF checks the named execution and marker
  -> Analyst opens persisted source evidence/results and records a review action
```

**Say:**

> We start with the actual invoice files in the existing storage account.
> Data Factory coordinates the batch; the worker performs extraction and
> validation, asks the model for recommendations, and saves the results.
> Task suggestions use the published SQL TaskMaster. Accounting and bundling
> are recommendations, not business approvals. The analyst receives the
> original source evidence and the recommendations. Authenticated analyst
> decisions and audit through Power Apps are a later phase.

> Only the invoices and source TaskMaster are real business inputs here.
> All other supplied business data is dummy/demo data. These are demo
> recommendations, not approved Circle K accounting policy or transactions.

> We are reusing existing Azure resources. We are not creating a new service,
> updating ERP, or allowing the model to write SQL directly.

**If identity comes up, say:**

> ADF's ARM calls and the worker use the approved user-assigned managed
> identity. The existing Blob linked service still uses ADF's retained system
> identity. The UAMI conversion is deployed and verified.

**Architecture clarification:** this demo uses one batch worker and in-process
model calls. It does not claim that separate agent/reference/review APIs or a
Power App have been deployed.

## 3. Show all the sources, not just Bruegmann (1:00-3:00)

**Where:** `cictaicusst` -> `landing` -> `capex` -> `input`.

**Point to all four files:**

- `Bruegmann  - INV1262027.pdf-135d03ac-3598-498a-83fe-d273d046bfea.zip`
- `Kimley Hor - 36764068GRP005.pdf-58aceec5-f35a-48a7-8faa-2f1a195af203.zip`
- `JC2000A07465-Ariba.xlsx`
- `JC2000A07814 - Ariba+Proj.tool.xlsx`

**Say:**

> Bruegmann is only one of the inputs. We also have Kimley-Horn and both
> JC workbooks. A file is not necessarily one invoice: the selected invoice
> rows in the workbooks produce seven DKK invoice packages and one EUR
> package. Together with the two PDF invoices, that is ten packages.

> Seven other ledger rows are kept as exclusion evidence, not silently
> counted as invoices. The earlier normalization had thirty lines, but fresh
> PDF extraction can produce different line counts. We show the actual
> count for this run rather than force it to match an older result.

**Evidence:** later show the same four source URIs/hashes in `intake.json`.
Do not claim that unrelated container folders were processed.

## 4. Show ADF starting and tracking the batch (3:00-5:00)

**Where:** ADF Studio, existing `pl_capex_intake_check`.

**Show these activity groups after the approved update:**

1. `DiscoverSources`, `RequireProcessingOptIn`, `SelectDemoFiles`,
   `RequireAllSources`.
2. `ReadExistingJob`, `RequireSafeJobConfiguration`, `StartJob`,
   `RequireExecutionIdentity`.
3. `WaitForExecution`, `RequireExecutionSucceeded`,
   `CheckCompletionMarker`, `RequireCompletionMarker`.

**Say:**

> ADF discovers the input folder and checks the four allowed filenames.
> The worker independently reads that same allowlist; ADF is not copying
> PDFs through a Copy activity or sending invoice content to ARM.

> Processing is deliberately opt-in. The pipeline's default is discovery
> followed by an explicit safety stop. For an approved processing run,
> `processInvoices` must be true.

> We supply True as a per-run parameter through Trigger now or the createRun
> API. The saved default stays False. If the parameter is omitted or False,
> RequireProcessingOptIn stops the run before worker/model processing.

**If a fresh run was approved:** use **Add trigger -> Trigger now**, set
`processInvoices=true`, and record its RunId. Starting processing incurs cost.
Do not trigger just to make the screen look active.

**Otherwise:** open the completed rehearsal in Monitor and clearly call it a
replay of that actual execution.

**Say:**

> This starts the existing `capex-task-determination-proof` job with a pinned
> worker image for this execution only. The saved proof-job configuration
> and Friday's job are left alone. ADF follows the execution returned by
> this start request, not whichever execution happens to be newest.

**Proof:** show `StartJob`'s execution name/ID and that exact execution's
status/logs. The saved job Overview image is not evidence of the override
image; use the ADF start body and recorded execution/build evidence.

Do not say that the separate preparation/dispatch jobs or a Service Bus flow
were triggered. That is not this pipeline's implementation.

## 5. Show extraction and validation (5:00-7:00)

**Where:** this run's output folder; `intake.json`, an `extraction` audit
record, the corresponding `processing-packages` record and
`reference-exceptions.json`.

**Say:**

> The worker checks SQL access and verifies the actual published TaskMaster
> against the pinned catalog before inference. A catalog change or missing
> permission is a technical stop, not an invitation to guess.

> For the two ZIP sources, it opens the original PDF member, renders it,
> asks the model to extract structured facts, and validates the response.
> The audit links the archive hash, PDF hash, pages, prompt and response.
> The workbooks use their invoice-row normalization; we do not pretend
> they went through PDF vision extraction.

> Missing or inconsistent evidence stays visible. This demo explicitly
> permits provisional recommendations for reliable line evidence with
> incomplete noncritical details. The warnings and nulls are retained.
> Hard failures still route to non-model Review; we do not silently substitute
> preprocessed facts and claim fresh extraction succeeded.

**Show:** one original PDF alongside its extraction and one workbook invoice
package. Point to validation/review reasons rather than hiding them.

## 6. Show task suggestions and CapEx/OpEx recommendations (7:00-10:00)

**Where:** the existing private SQL client or persisted review result.

| Stage | Source / persisted evidence |
|---|---|
| Governed task catalog | `pub.v1_TaskMaster` |
| Per-line task suggestions | `task.TaskSuggestion` |
| Accounting recommendation and review reasons | `capex.Assessment` |
| Assessment run counts | `capex.AssessmentRun` |
| Model prompts/responses/provenance | `capex.LlmShowcaseRun` and run artifacts |

**Say:**

> The model proposes a task from the supplied active catalog. Code checks
> the response and persists the suggestion. TaskMaster is a task/category
> catalog, not an inventory of physical assets at each store.

> The accounting step then proposes CapEx, OpEx or Review, with rationale,
> cited demo policy settings and missing inputs. Code validates the contract,
> policy consistency and arithmetic. It does not turn a model answer into
> an approved accounting decision.

> The supplied USD policy assumptions are unapproved demo settings. We do
> not apply the USD threshold to DKK/EUR or invent an exchange rate. The
> eight JC packages lack the governed regional/native-currency references
> needed for this path, so their lines receive explicit, non-model Review
> tasks and assessments.

> The TaskMaster catalog is real; the policy and any supplemental context
> are dummy/demo inputs. A task suggestion or accounting row is a demo
> output, even though its SQL persistence and provenance are genuine.

**Point out:** the assessment's `TaskSuggestionID` links back to the exact
task row, and `WorkflowRunId`, package, invoice and line identify the source.
Non-model exceptions have `UsedLlm=0` and task model metadata
`NOT_CALLED_REFERENCE_REVIEW`; do not attribute them to GPT.

If the actual new run has only Review results, say so. Do not borrow CapEx/OpEx
counts from Friday and label them as the new run.

## 7. Show bundling honestly, including a zero-group result (10:00-12:00)

**Where:** per-line bundle decisions/reasons and any proposed groups.

| Surface | Meaning |
|---|---|
| `capex.BundleDecision` | Model-backed per-line `BUNDLE`, `NO_BUNDLE` or `REVIEW` |
| `capex.BundleCandidateGroup` / `capex.BundleCandidateMember` | Actual proposed groups/members |
| Non-model `capex.Assessment.BundlingDisposition` | Explicit Review disposition for routed exceptions |

**Say:**

> Bundling asks whether supported lines represent one asset, not merely
> whether several lines share a task code. Code gates eligible members and
> checks identity, currency, membership and sums.

> The real inputs do not supply governed Site/Project context for this
> handoff. That absence is preserved. We do not invent store/project IDs
> to force a positive bundle. Zero proposed groups can be the correct,
> reviewable result.

**Show the actual outcome.** A model bundle decision is not a group, and a
provisional group is not an approved asset. Routed non-model exceptions do
not go into the GPT-only bundle-decision table.

**Optional, only as a separate labeled exhibit:** use the previously verified
synthetic positive control to illustrate two USD 600 components proposed as
one USD 1,200 assembly. Say: "This is synthetic test evidence of the positive
proposal path, not a bundle found in these source invoices." Keep its RunId,
policy context and counts separate. Do not run a new control without approval.

## 8. Prove persistence, not just a successful start (12:00-14:00)

**Where:** ADF Monitor, named job execution, run-scoped output and SQL.

**Say:**

> A successful ARM start is not our completion signal. The named job must
> finish successfully. Native recommendation rows, model evidence and the
> analyst handoff are committed in one application-owned SQL transaction,
> with linked-row count readback. A failure rolls that transaction back.

> The worker writes `batch.json` last. ADF checks its existence after the
> execution succeeds. Its Binary dataset does not parse the JSON; we inspect
> its content and the SQL verification separately.

**Show in `batch.json`:** matching `workflowRunId`, actual counts,
`freshPdfExtraction=true`, `taskMasterSource=pub.v1_TaskMaster`,
`sqlPersistence`, `reviewStatus=AWAITING_ANALYST`, and `erpWrites=0`.

Run the read-only [ADF SQL verification query](../../sql/validation/validate_capex_adf_demo_run.sql),
with `@WorkflowRunId` set to this completed ADF RunId, through the authorized
private SQL connection. Show `ADF_SQL_REVIEW_VERIFIED` and the per-invoice
summary. Both the verifier and presenter query have been run successfully
against the current completed run from the authorized private workload.

`review.json` and candidate result counts alone do not prove a SQL commit.
If the job or final marker fails, stop and inspect; do not silently rerun an
uncertain start. A fresh approved retry uses a fresh RunId.

## 9. Future phase: Power Apps analyst review, not part of this demo

**End the current live walkthrough at SQL.** Explain that Power Apps will
provide the authenticated analyst experience later. Do not claim that it
already exists or that an analyst audit is required to complete today's
ADF-to-recommendation-to-SQL scope.

The loopback prototype instructions below are an optional historical/operator
appendix, not a required presentation step and not a Power App.

**Where:** the already-open loopback review page for the same RunId.

The [operator guide](thursday-architecture-demo.md#opt-in-original-pdf-extraction-and-sql-review-handoff)
contains the `--store azure-sql serve` command. It needs existing private
connectivity and an identity already assigned to that compute with SQL
permissions, or explicit `--sql-auth azure-cli` with the signed-in user's
existing SQL permissions. The review VM has been confirmed to have no managed
identity; its Python dependencies, Driver 18 and SQL TCP connectivity passed,
and its explicit Azure CLI SQL preflight passed on November 10: SELECT on
`mvp_review.imports`, `documents` and `decisions`, plus INSERT on `decisions`.
The VM can use the existing signed-in account without an identity attachment.
An actual same-run browser review and saved audit remain unverified. Do not open public
networking, attach identities or provision a host to bypass this requirement.

**Show:** original source, extraction/normalization, task, accounting,
bundling disposition, missing inputs and review history.

**Say:**

> This page reads the committed handoff and source evidence from the existing
> `mvp_review` tables, not a manually retyped result. PDF sources can be
> previewed; workbook sources can be downloaded.

> This is a loopback demo prototype, not a deployed Power App or browser-SSO
> experience. The analyst label entered here is not an authenticated user
> identity. The separate authenticated API is not claimed to be deployed.

**Safe rehearsal action, only on the approved demo run:**

1. Select an invoice with unresolved inputs.
2. Enter analyst label `Demo analyst`.
3. Enter reason `Demonstration only: defer pending source/context review`.
4. Click **Defer**.
5. Refresh, then show the saved version, timestamp and hash-linked audit.
6. Optionally show the corresponding `mvp_review.decisions` row.

**Say:**

> The review action is durable, but it does not overwrite the original
> recommendation, recompute accounting, train the model, or post to ERP.
> A correction marks an item for reprocessing. Unresolved items cannot be
> confirmed as though their missing evidence was supplied.

Avoid the simulated POPA confirmation checkbox in the main walkthrough.
If asked, explain that it is a simulation with no POPA integration.

## 10. Closing (17:00-18:00)

**After a verified live run, say:**

> We traced the real files through ADF orchestration, extraction and
> validation, governed task suggestions, accounting and bundling
> recommendations, linked SQL results, and analyst review.
> We also preserved the exceptions instead of manufacturing missing facts.
> The result is a reviewable recommendation, not automatic capitalization
> approval or ERP posting.

> To reiterate: the invoices and source TaskMaster are real; the other
> business inputs and resulting decisions shown here are demo/test data.

> The next production steps include governed regional policy/context,
> resolved extraction eligibility, an authenticated analyst experience,
> and approved downstream integration. Those are not being presented as
> completed capabilities today.

## If the new live path is not ready

**Say this first:**

> The new ADF run completed and its SQL exception records and review handoff
> reconcile. All sources required Review, so it did not exercise GPT task,
> accounting or bundling decisions. Analyst audit is also unverified. I will
> show those limits and keep historical model evidence separate.

1. Show actual ADF file-discovery output for all four sources.
2. Show the completed exception-routing run and explain which model stages
   were not reached. Explain the intended eligible-input flow using section 2.
3. Show the frozen Friday cloud evidence:
   - Job `capex-demo-v2-sql`, execution `capex-demo-v2-sql-qfr1i2b`.
   - Workflow `friday-sql-gpt-contract-v4-20261105T150000Z`.
   - The saved job uses `--allow-synthetic-inputs` and fixture
     `FRIDAY-DEMO-2026-11-06-V1`. Label its staged snapshot as a demo
     fixture, not evidence of fresh extraction of the original invoices.
   - Ten staged packages, thirty lines, thirty task suggestions, thirty
     accounting assessments and thirty model bundle decisions.
   - Twenty-two model requests/responses; 7 CapEx, 2 OpEx, 21 Review;
     zero proposed groups.
4. State explicitly: those are **Friday's saved results**, not a new
   ADF-triggered execution, fresh extraction or completed new review handoff.
5. If useful, show a labeled local/synthetic review example separately.
   Do not relabel a SQLite example as Azure SQL.

For a live batch still running, use the completed rehearsal evidence card
and say which RunId is completed versus in progress. Never mix their outputs.
Do not fill an on-stage wait by changing permissions, networking, resources
or the job timeout.

## Quick answers

| Question | Answer |
|---|---|
| Which data is real? | The original invoices and source SQL TaskMaster. Other supplied business inputs are dummy/demo data; recommendations and analyst decisions are demo/test outputs. Execution and audit evidence is genuine. |
| Why four files rather than three invoices? | Two PDF archives plus two workbooks; the selected workbook rows represent multiple invoices. |
| Does ADF perform the AI extraction? | No. ADF orchestrates; the batch worker invokes the model and validates outputs. |
| Is ingestion event-triggered? | Not in this increment. The pipeline is manual and processing is explicitly opt-in. |
| Does the model query or write SQL itself? | No. Application code supplies governed references and owns SQL operations. |
| Is ADF already using the UAMI? | Yes, for all three ARM Web activities. The existing Blob connection retains ADF's system identity. |
| Why are some lines Review? | Missing source/context or governed regional/currency policy is preserved, not guessed. |
| Are positive bundles guaranteed? | No. Show actual decisions; zero groups is valid. Synthetic positive controls stay separate. |
| Is TaskMaster a physical asset register? | No. No existing physical asset ID or historical asset match is invented. |
| Is cross-run/history-based bundling shown? | No. This flow evaluates the current batch, not a persisted physical-asset history. |
| Has a Power App or ERP integration been delivered? | No. This is a demo review prototype with no ERP writes. |
| Does green ADF discovery mean processing finished? | No. Require the exact execution, final marker, SQL readback and matching review run. |
