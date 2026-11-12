# CapEx Demo V2 Client Runbook

> **Thursday, November 12:** use the
> [architecture-aligned presenter guide](thursday-architecture-demo.md).
> It covers the new opt-in extraction/review prototype and the preserved
> Friday SQL fallback. The invoices-first script below is the historical
> Friday walkthrough; do not describe its job as the new two-pass MVP.

## Thirty-minute Azure presenter script: invoices first

This is a spoken walkthrough, not a new workflow query. Use the existing
successful execution and already-opened, read-only table/result views.
Do not rebuild, seed/reset tables, change networking or click Run now.

### Prepare the screens before the audience joins

Use the approved VM/session that already reaches private SQL and Storage.
Open these screens in separate tabs/windows:

1. Azure Portal, resource group `cict-aisandbox-cus-rg`.
2. Storage account `cictaicusst`, container `landing`, original invoice
   artifacts. Open the two original US invoice PDFs from their source bundles
   and both JC workbooks in approved local viewers before the meeting.
3. Database `cict-ai-cus-sqldb`, plus the existing working SQL data views for
   `prep.Invoice`, `prep.InvoiceLine`, `prep.InvoicePackage`,
   `task.TaskSuggestion`, `capex.Assessment`, `capex.BundleDecision` and
   `capex.LlmShowcaseRun`. Preselect the successful v4 run's result rows.
4. Container Apps Job `capex-demo-v2-sql`, execution history,
   execution `capex-demo-v2-sql-qfr1i2b`, status Succeeded.
5. Foundry resource `cict-ai-cus-foundry`, existing model deployment `gpt-5.1`.
6. Container registry `cictaicusacr`, repository `capex/demo-v2`, tag
   `sql-20261105T145410Z-f32b4631`.
7. Storage folder `landing/capex/demo-v2/sql` and the existing retained log
   view for the successful execution, if available.

The Azure SQL resource Overview page does not itself display invoice rows.
Use the working SQL editor/table-data view or existing result grids for the
data portion. If the Portal Query editor is not already working from the
private session, switch to the proven SQL tool; do not troubleshoot access
or change firewall rules on stage. No additional workflow SQL is supplied.
Do not edit data in a table viewer.

### 00:00-02:00: opening and dummy-data disclaimer

**Show:** original invoice/source-file tab, before any recommendation tables.

**Say:**

> Today I will follow the invoices from their original source, through the
> SQL invoice model, into task suggestion, CapEx/OpEx recommendation and
> cross-invoice bundling evaluation. I am showing the persisted results of
> a completed Azure execution, so we can inspect the same evidence at each
> step without rerunning paid model calls.
>
> The source invoice descriptions, currencies and amounts are real. Where
> the extracts lacked required processing information, we added explicitly
> labeled synthetic demo context. Those assumptions are not approved
> business facts or approved accounting policy. Original missing source
> values remain missing in the source records. These are recommendations
> for human review, not finance approvals or ERP postings.

**Know exactly what was synthetic and where it resides:**

| Demo input | Where to point if asked |
|---|---|
| Project/site identities, project budget/status, bundling scope and site assumptions | `prep.Project`, `prep.Site`, and `prep.InvoicePackage.SourceMetadataJson.demoFixture` |
| Missing processing vendor IDs, invoice dates and header PO context | Effective values in package `SourceMetadataJson.demoFixture`; original header columns remain the source facts |
| Synthetic PO headers/lines and unconfirmed part-to-task proxy mappings | `prep.PurchaseOrder`, `prep.PurchaseOrderLine`, `ref.PartMaster`; not a model answer key |
| Effective quantities, unit costs and part numbers where required | `prep.InvoiceLine.SourceLineJson.demoFixture` and the derived in-memory processing package, separate from original source columns |
| Native-currency unit-cost thresholds and policy/nature assumptions | `ref.CapitalizationThreshold`, fixture metadata and the saved input snapshot; USD 1,000, EUR 1,000 and DKK 7,500 are unapproved rehearsal thresholds, not FX conversions |
| US-catalog proxy context for European source accounts | Explicitly labeled synthetic/unconfirmed; not a governed European TaskMaster mapping |

Do not call every quantity/price synthetic: existing source values were
preserved. The source inventory records 19 missing quantities and 19 missing
unit costs. The US TaskMaster came from the supplied reference export;
it was not invented for the demo. Proxy PO/PartMaster mappings used by the
reference fixture are not fed to the GPT task stage as the correct answer.

### 02:00-07:00: original invoices and all source lines

**Show:** `cictaicusst` -> `landing`, then the pre-opened original documents.
Do not start in the generated snapshot/result/evidence JSON folder.

1. Bruegmann invoice `INV12-62027`: show header, currency and all 12 lines.
   Highlight one physical-item description and the shipping line.
2. Kimley-Horn invoice `36764068-GRP005`: show all 10 professional-services
   lines. Choose one line to follow through the rest of the demonstration.
3. JC2000A07465 workbook: show the seven invoice-type (`AI`) records:
   `2001653935`, `2001653932`, `304430`, `5063`, `2001665940`,
   `2001069567` and `2001704723`.
4. JC2000A07814 workbook: show invoice-type record `20294`.
   Explain that non-invoice JP/OV accounting records were not counted as
   additional invoices.

**Say:**

> There are three source families, represented by two US invoice bundles
> and two JC workbooks. Together they provide ten invoice records and thirty
> invoice lines. The JC workbooks contain invoice-type ledger records; they
> are not eight separate PDFs. We preserve source gaps rather than treating
> blank fields as known zero values.

Do not add invoice amounts across USD, DKK and EUR as one currency total.
For Bruegmann, the source header-versus-line difference is retained tax
provenance, not an invented extra invoice line.

### 07:00-11:00: Invoice, InvoiceLine and InvoicePackage

**Show:** the database resource to establish the Azure SQL location, then
the three pre-opened SQL table-data views. No SQL writing during the talk.

1. **`prep.Invoice`:** vendor, invoice number, source date, currency and
   header total. Point to a missing source field rather than hiding it.
2. **`prep.InvoiceLine`:** show all 30 lines across the ten invoices.
   Point to description, original quantity/unit cost, amount and line ID.
3. **`prep.InvoicePackage`:** show the package ID, invoice ID, source file,
   source hash and fixture provenance. Expand one known fixture metadata
   value to illustrate the source-versus-processing separation.

**Say:**

> Invoice is the business header. InvoiceLine holds the individual source
> charges. InvoicePackage is the processing envelope that links them to
> source provenance and context. Stable package and line identities let us
> connect the later recommendations back to the original input.

**Important explanations:**

- `PackageStatus=PREPARING` is intentional in this SQL rehearsal. It avoids
  publishing legacy READY/outbox events; it is not the container job's status.
- The source-load RunId and the later GPT WorkflowRunId are different
  identifiers for different processing boundaries.
- Source normalization/manual SQL staging happened before this execution.
  Do not claim that opening/uploading a Blob now triggers fresh OCR or this job.

### 11:00-14:00: Azure architecture and the actual job

**Show:** the resource group, then Container Apps Job -> execution history ->
`capex-demo-v2-sql-qfr1i2b`.

**Say:**

> This implementation has one manually triggered Container Apps Job with
> three logical model stages inside it, not three separately deployed jobs
> or three Foundry-hosted agents. For each invoice, it determines tasks and
> performs accounting assessment. Once the accounting batch is complete,
> it evaluates bundling across the invoices.

Show Succeeded, the job's pinned image and its existing `--llm-workflow`,
`--model-deployment gpt-5.1` and persistence arguments. Do not change them.
Execution timestamps are 14:59:53-15:03:09 UTC; this is a completed run,
not a live model invocation during the presentation.

Briefly show the matching ACR repository/tag and Foundry `gpt-5.1` deployment.

| Azure component | What it actually does here |
|---|---|
| Blob Storage `cictaicusst` | Source artifacts plus immutable snapshot/result/model-evidence JSON |
| Azure SQL `cict-ai-cus-sqldb` | Staged invoices, reference/context data, native recommendations and SQL audit |
| ACR `cictaicusacr` | Builds/stores the versioned container image; build `cj10` supplied this release |
| Container Apps Job `capex-demo-v2-sql` | Runs the Python SQL-backed batch workflow in the existing environment |
| Foundry resource `cict-ai-cus-foundry` / deployment `gpt-5.1` | Serves actual model requests from that workflow |
| Managed identity `cict-ai-cus-capex-id` | Authenticates the running workload without embedding service keys |
| Log Analytics `cict-ai-cus-law` | Retains execution console evidence |

Do not introduce unrelated Functions, Logic Apps, Data Factory or automatic
Blob triggers as if they ran this recorded SQL-backed workflow.

### 14:00-19:00: task suggestion, then its persisted results

**Show:** Foundry deployment, return to the same job execution/log evidence,
then the existing `task.TaskSuggestion` result view.

**Explain the work:**

1. The source line description and supplied task-catalog candidates form
   the request; every source line receives model consideration.
2. GPT-5.1 returns the interpreted item, task selection or Review, and rationale.
3. Code checks permitted task codes, ambiguity, evidence and exact line coverage.
4. The accepted result is persisted against the original package/invoice/line.

**Show in the result:** ItemIdentified, SuggestedTaskCode, SuggestedAssetClass,
MatchMethod, model deployment/version and rationale. Follow the selected
Kimley-Horn line; then contrast it with an unresolved task if useful.

**Say:**

> This task came from the source description and the supplied catalog.
> Here is the model's rationale. Where catalog entries cannot be distinguished,
> the system retains Review rather than making an arbitrary assignment.

Task-table legacy recommendation fields are not the authority for the next
accounting stage. Use `capex.Assessment` to demonstrate CapEx/OpEx.

### 19:00-23:00: CapEx/OpEx determination, then its persisted results

**Show:** the same job and `ACCOUNTING_DECISION` evidence if already open;
then `capex.Assessment`. Do not search for a separate accounting job.

**Explain the work:**

1. GPT receives source and labeled processing context, task results and policy.
2. It supplies actual spend nature, recommendation and rationale.
3. Code checks nature/repair/exclusion consistency, required Review states,
   native-currency unit-cost thresholds and permitted evidence.
4. The accepted assessment is stored with its InvoiceLineID and TaskSuggestionID.

**Show:** the same Kimley-Horn line's task link, NatureResult,
CapExOpExSuggestion, threshold currency/amount, rationale, UsedLlm=1 and model.
Choose actual saved examples of one CapEx, one OpEx and one Review result.
Summarize the specific recorded rationale for each; do not invent why a line
was expensed or reviewed.

**Say:**

> GPT is making the accounting recommendation, not merely explaining a
> precomputed label. Code independently validates the response, and unresolved
> evidence stays visible as Review. This is not a finance approval.

The frozen run has seven CapEx, two OpEx and twenty-one Review assessments.
Do not equate Review with processing failure or claim all Review decisions
are business-validated correct.

### 23:00-27:00: cross-invoice bundling, then its persisted results

**Show:** the same job's `BUNDLE_DECISION` evidence if available; then
`capex.BundleDecision` across all invoices.

**Explain the work:**

1. Code gates eligible members from actual model accounting and input facts.
2. GPT considers the invoice batch together and proposes single-asset groups
   only where the evidence supports them.
3. Code checks identity coverage, compatible task/site/project/currency,
   minimum membership, exact sums and duplicate/group membership.
4. Every line receives a persisted model bundle decision. Valid proposed
   groups would also populate `capex.BundleCandidateGroup` and
   `capex.BundleCandidateMember`; human `review.BundleDecision` is separate.

**Say:**

> Different invoice numbers, vendors or POs do not automatically prevent a
> shared-asset proposal. But eligibility and a defensible asset relationship
> are required. This run produced thirty decisions and no proposed groups.
> Here is the actual rationale for this line.

Show ModelDecision, invoice/line identity, rationale and BundleGroupId.
For this run, show the zero group count instead of pretending an empty
group/member table contains an approved bundle. This is a working negative
case, not a demonstration of a formed cross-invoice asset group.

### 27:00-30:00: quality, audit and close

**Show:** the already-verified `capex.LlmShowcaseRun` audit/result counts,
the successful execution, and the Blob SQL-artifact folder if time permits.
For this run the artifact names begin `gpt-contract-v4-` and end
`20261105T150000Z.json` for snapshot, result and evidence.

**Say:**

> The completed run has thirty task suggestions, thirty assessments and
> thirty bundle decisions. We have model-response evidence and stable source
> links, rather than only a console answer. All twenty-two model requests
> received responses, and the final run succeeded.

Explain 22 calls carefully: the normal structure is ten task calls, ten
accounting calls and one cross-invoice bundling call. Bounded validation
regeneration can add a call. Job replica retries are zero; model validation
regeneration is a different mechanism. Do not guess which stage regenerated
from the total count alone.

**What "good results" can honestly mean:**

- Complete 30/30 output coverage at each decision stage.
- Traceability from source line to task, assessment and bundle decision.
- Recorded rationales, model identity, audit hashes and successful persistence.
- Visible Review outcomes and rejection of invalid model proposals.

These are coverage, traceability and control results, not a measured model
accuracy percentage. Finance-reviewed expected labels are still needed to
claim accuracy. Confidence values are not proof of correctness.

**Close:**

> We have demonstrated how real-source invoice records move through a
> traceable Azure workflow into persisted task, accounting and bundling
> recommendations. Production readiness still requires approved context,
> regional mappings and policy, human validation, and a separately validated
> positive bundling case if stakeholders require one.

## Technical evidence and earlier rehearsal reference

## What this workflow does

### Requested demo: GPT-5.1 recommendations written to native SQL tables

#### Latest cloud result: succeeded; SQL verification reported successful

Execution `capex-demo-v2-sql-qfr1i2b` succeeded on November 5, 2026,
ending at 15:03:09 UTC. Its workflow is
`friday-sql-gpt-contract-v4-20261105T150000Z`, using ACR run `cj10`,
image digest
`sha256:19a722900c25882af2ef4d80f154fe6f682d7d58f238557a502f2829fecbe236`.
Before execution, read-only ARM inspection verified the exact image and all
22 arguments, identity, registry pull identity, SQL target, model endpoint,
environment and manual/retry/concurrency settings.

Console evidence confirms ten invoices/thirty lines, zero workflow errors,
thirty persisted task suggestions, thirty persisted assessments and zero
bundle groups/members. Actual assessments are seven CapEx, two OpEx and
twenty-one Review. `LLM_SHOWCASE_COMPLETE` records 22 real model responses,
actual model accounting and one complete SQL audit, SHA256
`dfbcfcf6033808dec772d5ff6554d705052b827fd9e22d88799e39268c2d22da`.
The immutable Blob evidence is
`landing/capex/demo-v2/sql/gpt-contract-v4-evidence-20261105T150000Z.json`.
No positive bundle group was proposed; do not describe one as model-approved.

The operator subsequently reported that the final SQL verification query
succeeded. This is operator-reported verification, not a separate database
readback performed by the assistant. For repeat verification, run
`sql/validation/validate_capex_sql_llm_workflow.sql` with the workflow declaration
set to the v4 ID above through the working private connection. Require
`GPT_INVOICE_WORKFLOW_VERIFIED`. This checks all thirty native model bundle
decisions, source/line/task links, accepted raw model responses and audit hashes,
in addition to the task/assessment writes visible in logs. The assistant's
local SQL connection is blocked by private networking; a successful container
exit alone is not a substitute for this SQL result.

For a cleaner presentation, run
[rehearse_capex_sql_llm_workflow.sql](../../sql/validation/rehearse_capex_sql_llm_workflow.sql).
It is a separate read-only query with the successful workflow ID already set.
Its five grids show invoice coverage, Kimley-Horn task suggestions, linked
accounting assessments, all-invoice bundle decisions and final audit/counts.
No rebuild, deployment, SQL migration or additional model call is needed.

**Rehearse without rerunning the job:**

1. Run the read-only verification and retain its marker and result grids.
2. Introduce ten invoices from three source families and thirty lines; disclose
   the unapproved synthetic processing context and native-currency assumptions.
3. Show a Kimley-Horn line's model task suggestion, then its linked assessment
   and actual rationale. Contrast one CapEx result with an OpEx or Review result.
4. Show the per-line model bundle decisions. This run proposes no groups;
   explain the recorded rationale rather than forcing unrelated or unresolved
   lines into a positive bundle. Candidate recommendations are not human approvals.
5. Repeat the walkthrough using the same read-only result grids. Do not click
   Run now, reuse the immutable workflow ID or incur more model calls to practice.

#### Current correction: coherent accounting and complete-line bundling

The v3 image built in ACR run `cjy` was deployed with its exact digest and
all 22 arguments verified read-only. Execution `capex-demo-v2-sql-fapkef6`,
workflow `friday-sql-gpt-accounting-v3-20261105T142200Z`, failed after twenty
model responses on a repair/exclusion consistency check for the one-line
invoice `JC2000A07465:5063` ("Water and sewage"). Logs confirm zero committed
native task/assessment/group/member rows and one retained INCOMPLETE SQL audit.
The exact rejected accounting response remains in that private SQL audit;
the console error alone does not establish its nature/recommendation/flag tuple.
Preserve this workflow and its immutable artifacts; do not replay its IDs.

Contract `sql-llm-accounting-v4` validates model accounting combinations
explicitly. Repair/exclusion indicators prohibit capitalizable nature and
CapEx, but expense nature may remain REVIEW with a stated unresolved input,
consistent with the existing reference assessment contract. GPT supplies the
actual nature, recommendation and rationale; code never rewrites them.
If an accounting response violates the contract, the model receives the exact
validation error and rejected response for ONE regeneration of the complete
invoice batch. A second invalid response fails closed. Transport/authentication
errors are not retried by this validation mechanism.

A full ten-source/thirty-line local rehearsal also exposed a separate final
model response that covered only the two eligible bundle members, omitting
28 required per-line decisions. Bundling contract `sql-llm-bundling-v2` now
includes an explicit all-line identity catalog and exact response length.
It likewise permits one audited regeneration after validation failure.
Partial or invalid proposals do not leak group IDs into an accepted result.

Both original and correction calls retain their actual prompt, response and
validationError in SQL/Blob evidence. Successful runs require 21 ACCEPTED
responses (ten task, ten accounting, one bundling); actual paid calls may be
21 through 32, with at most two accounting responses per invoice and two
bundling responses. The read-only native SQL validator reconciles these
bounded counts and verifies assessments against accepted raw model responses.
No reference fallback or invented human approval is introduced.

119 focused regressions pass. The full ten-invoice/thirty-line actual GPT-5.1
rehearsal succeeded with thirty task results, thirty accounting assessments and
thirty bundle decisions. It made 22 real calls: ten task, ten accounting and
two bundling calls. The first proposed a zero-value group and was rejected;
GPT corrected the response to no groups on its single audited regeneration.
Accepted recommendations/rationales/flags reconcile to the raw model responses.
This local test uses explicitly labeled synthetic processing quantity/cost/
context, not a replay of the private SQL snapshot; it performs no SQL writes.
No additional SQL migration is required.

Use the new `capex-sql-gpt-contract-v4-source.tar.gz` for an authorized build
from a fresh extraction directory. The prepared
`sql-gpt-contract-v4-execution.parameters.json` must receive that new build's
verified image digest before deployment. It uses the new workflow
`friday-sql-gpt-contract-v4-20261105T150000Z` and fresh Blob artifact names.
Do not redeploy or rerun the previous v3 image/workflow as the corrected build.
Final end-to-end completion still requires cloud execution and the native
SQL validator's `GPT_INVOICE_WORKFLOW_VERIFIED` marker through the working
private SQL connection.

#### Historical v3 correction: remove redundant model eligibility

The second cloud execution, `capex-demo-v2-sql-qocrqf9`, workflow
`friday-sql-gpt-accounting-v2-20261105T140000Z`, also failed closed after
twenty actual model responses, before any native task/assessment/group commits.
Its incomplete SQL audit remains intact. The precise error was Kimley-Horn
line `58aceec5-f35a-48a7-8faa-2f1a195af203:3`: the model returned OPEX,
OPEX_BY_NATURE and a resolved task, but also bundlingEligible=true.
Prompt-only constraints did not prevent that redundant-field contradiction.

Contract `sql-llm-accounting-v3` removes bundlingEligible from the accounting
model's output entirely. Code gates eligibility from the actual model nature
and recommendation, resolved task, native threshold and project/site facts.
It preserves the model's classification and rationale unchanged; this is not
a fallback to reference accounting or a silent correction of a model flag.
The final cross-invoice GPT call still makes the actual asset grouping and
BUNDLE/NO_BUNDLE/REVIEW recommendations, receiving only eligible members as
possible group members. Unexpected extra model fields are rejected, not ignored.

112 targeted tests pass, including OpEx-by-nature exclusion with the original
model rationale retained. A real-source, three-call Kimley-Horn smoke with
explicitly labeled local synthetic quantity/cost inputs succeeded for all ten
lines. Its actual line 3 resolved to task 0007, OPEX/OPEX_BY_NATURE, zero unit
cost and NOT_ELIGIBLE; no groups or SQL writes. This reproduces the observed
failure condition, not the complete cloud SQL input snapshot.

Use `capex-sql-gpt-accounting-v3-source.tar.gz` and fresh
`sql-gpt-accounting-v3-execution.parameters.json` after a new authorized build.
The image placeholder must be replaced with that build's verified digest.
No new migration is needed. The current native SQL validation requires the
v3 contract and recorded code-gate authority. Preserve both failed runs,
their SQL audit rows and immutable Blob outputs; never replay their IDs.

#### Historical first attempt and prompt-only v2 correction

The operator applied migrations 012/013 and successfully built/deployed ACR
run `cjw`, image digest
`sha256:b8d2fb5416818da0fff42ea196806b25e129bab1d74f802494e2f9e413655af0`.
Execution `capex-demo-v2-sql-bgenol3`, workflow
`friday-sql-gpt-workflow-20261105T024500Z`, then failed closed on a model
accounting bundling-eligibility violation. Log Analytics confirms twenty real
model responses, zero committed task/assessment/group/member rows, and one
`INCOMPLETE` SQL audit record. The final cross-invoice model call did not run
because the accounting batch was incomplete. Preserve this failed run and its
immutable Blob/SQL evidence; do not replay its IDs.

Accounting prompt `sql-llm-accounting-v2` now supplies explicit per-line task,
cost and context constraints, and a response schema that prohibits
`bundlingEligible=true` for Review, CapEx, OpEx-by-nature, repair/exclusion or
statically ineligible lines. It still validates model output independently
and never silently changes an invalid model recommendation. Errors identify
the exact line and failed eligibility facts.

112 targeted regressions pass, including contradictory Review/eligibility
rejection and schema checks. A separate local genuine GPT-5.1 smoke on the
real ten-line Kimley-Horn normalized source completed task, accounting and
bundling stages with ten decisions each, no groups, and zero SQL writes.
This validates the new contract, not the failed cloud run's exact SQL input
snapshot; the rejected cloud response remains in its SQL audit record.

Use the refreshed `capex-sql-gpt-accounting-v2-source.tar.gz` for a new build.
The new planned parameters, `sql-gpt-accounting-v2-execution.parameters.json`,
have fresh workflow/Blob IDs, explicit existing tags/location and an image
placeholder to replace only with the verified new build digest. Migrations
need not be reapplied for this prompt-only correction. Deployment and full
ten-invoice SQL verification remain pending. The sections below document
workflow behavior and earlier verification checkpoints.

Use **`--llm-workflow --model-deployment gpt-5.1 --execute --persist-results`**,
not the earlier advisory `--llm-showcase` mode. Packages are reconstructed from
the ten real-source invoices/thirty lines already staged in SQL; this is not
a fresh Blob-ingestion run. Original source gaps remain unchanged. Explicitly
labeled synthetic processing context and unapproved policy inputs are still
required for the rehearsal.

1. GPT-5.1 receives **every invoice line**, including source-coded and ancillary
   lines, in ten invoice-level task calls. Each line produces a
   `task.TaskSuggestion` row with its model rationale, identified item, task
   code or Review, match method and model version. Seeded PO/PartMaster answers
   are not used as model task answers. Indistinguishable catalog variants
   remain Review rather than invented assignments.
2. Ten further GPT-5.1 calls recommend actual CapEx/OpEx/Review outcomes.
   Thirty `capex.Assessment` rows contain these model recommendations,
   `UsedLlm=1`, `ModelVersion=gpt-5.1`, the corresponding `InvoiceLineID`,
   model rationale and a link to that line's `TaskSuggestionID`. Code rejects
   fabricated identities, unsupported citations and policy/threshold violations;
   it does not replace model recommendations with reference answers.
3. One GPT-5.1 call considers **all invoices together** and recommends groups
   only where the asset relationship and matching task/site/project/currency
   support them. Different invoice numbers, vendors and POs are allowed.
   Thirty `capex.BundleDecision` rows record BUNDLE/NO_BUNDLE/REVIEW, including
   lines with no applicable bundle. Valid proposals also populate
   `capex.BundleCandidateGroup` and `capex.BundleCandidateMember`; exact amounts
   are verified with Decimal arithmetic. Candidate groups remain unapproved,
   and no human reviewer or ERP posting is fabricated.

**SQL operator handoff, in order:**

- Apply [012_store_capex_llm_showcase_outputs.sql](../../sql/migrations/012_store_capex_llm_showcase_outputs.sql);
  require `CAPEX_LLM_SQL_OUTPUT_STORE_READY`.
- Apply [013_store_gpt_bundle_decisions.sql](../../sql/migrations/013_store_gpt_bundle_decisions.sql);
  require `GPT_BUNDLE_DECISION_STORE_READY`.
  This creates the model recommendation table `capex.BundleDecision`.
  It deliberately does not populate `review.BundleDecision`, which is a
  human approval table requiring a real review case and reviewer.
  Runtime grants are object-scoped SELECT/INSERT and column-scoped updates
  for task metadata and assessment links, not broad database access.
- Build and publish **the updated source**, then replace the planned image
  placeholder with its verified digest URI. The previous reference image and
  both previous LLM source archives lack the complete native workflow.
- Configure the existing Foundry endpoint and managed identity, and start
  the job using fresh workflow/snapshot/result/evidence names.
- Run [validate_capex_sql_llm_workflow.sql](../../sql/validation/validate_capex_sql_llm_workflow.sql)
  with that new run ID. Require `GPT_INVOICE_WORKFLOW_VERIFIED` plus job success,
  a complete SQL audit row, 21 accepted model responses (21-32 actual calls
  with bounded audited accounting/bundling regeneration), thirty rows in
  each native line-level table, matching identities/links and reconciled groups.
  Outcomes and bundle counts are not pinned to the reference rehearsal.

The full responses are retained automatically for audit in
`capex.LlmShowcaseRun` and an immutable Blob mirror; the demo need not display
them. Its legacy `AccountingExplanationCount` column counts native accounting
recommendations in `LLM_DECISIONS` mode, and `BundleExplanationCount` counts
proposed groups. The JSON mode/stages and native SQL rows distinguish this
workflow from the older advisory mode.

Task/assessment/group writes commit before supplementary native metadata,
bundle decisions and audit JSON are committed together. If any later step fails,
the job fails explicitly; earlier rows may remain, but are **not a verified
complete run**. Inspect incomplete evidence and use fresh IDs for correction.
Never replay a partially committed run or overwrite the completed baseline.

**Local verification:** 109 targeted regressions pass, including all-line model
coverage, native SQL writes/links, cross-invoice grouping, invalid-output
rejection, pre-call schema checks, rollback and empty eligibility handling.
The CLI help and changed Python syntax validate. Pylance reports only the known
editor-interpreter missing-pydantic diagnostic; all thirteen orchestrator callers
are signature-compatible. A real-source Bruegmann smoke obtained twelve task and
twelve accounting responses. Its first bundling proposal violated eligibility
and was rejected. With an explicit exhaustive eligibility catalog and constrained
empty-group schema, the separate corrected GPT bundling call returned twelve
valid decisions and no groups. These local calls wrote zero SQL rows and are
not a completed ten-invoice cloud rehearsal. Positive cross-invoice groups are
verified with fictional model-provider tests, not asserted for the real sources.

**Cloud status:** these changes are locally validated, not live-deployed.
The previous ACR source-upload authorization denial still blocks publication;
an authorized operator must apply the migrations, build/publish, and update/start
through the approved session. No new native GPT decision rows have been inserted
into the live database.

**Current operator source package:** `capex-sql-gpt-workflow-source.tar.gz`.
It contains source-only runtime files, both migrations, both native/advisory
validators, tests, this runbook, the build helper and the SQL job Bicep.
No invoice files, workbook, credentials, virtual environment or cached responses
are included. The separate planned execution parameters deliberately contain
`REPLACE_WITH_VERIFIED_NEW_GPT_WORKFLOW_IMAGE_DIGEST_URI`; do not deploy them
unchanged. Recheck the staged input digest before execution; if it changed,
review a new preflight rather than bypassing the digest check.

An authorized operator can extract to a fresh directory, then run:

```powershell
.\tools\Build-Push-CapexSqlAcr.ps1 -AgentPool shared
```

This uses only the already operator-approved ACR shared-worker access path and
does not change networking or identities. Use the resulting verified image
reference in the planned parameters. If access is denied, stop and request the
appropriate operator rather than retrying through another API.

### Historical advisory mode (not the requested three-stage demo)

The completed SQL reference rehearsal below is **not the AI showcase**: it made
zero model calls. The SQL CLI now supports `--llm-showcase` with a fresh
`--llm-evidence-uri` in addition to all existing execution/digest/persistence
flags. Set `AZURE_OPENAI_ENDPOINT` to the existing Foundry account endpoint and
`--model-deployment gpt-5.1`. The job's existing managed identity requires
Cognitive Services OpenAI User on that account; do not add an API key.
The SQL Bicep accepts optional `azureOpenAIEndpoint` for this environment value.

**SQL operator prerequisite:** run
[012_store_capex_llm_showcase_outputs.sql](../../sql/migrations/012_store_capex_llm_showcase_outputs.sql)
in the working private SQL connection and require
`CAPEX_LLM_SQL_OUTPUT_STORE_READY`. This additive migration creates one empty
`capex.LlmShowcaseRun` table and grants the existing runtime user only
object-scoped SELECT/INSERT on it. It does not seed responses, alter the baseline,
or require broad database privileges for the runtime. Model execution with
`--persist-results` checks this table and its grants **before any paid model call**.
Default zero-call reference mode does not require migration 012.

- Task suggestions use real **source** descriptions/part numbers and retrieved
  TaskMaster candidates, not the seeded PO/PartMaster answers. Existing exact
  source tasks and ancillary shipping/reimbursable review gates remain
  deterministic. Not every line needs or receives a model call.
  If the highest-ranked candidates are indistinguishable, their codes are
  withheld and the line requires Review. The model cannot substitute an
  unrelated lower-ranked task just to produce a resolved answer.
- Accounting and grouping still use deterministic policy/math and visibly
  unapproved synthetic processing context. Real model task suggestions may
  change outcomes or leave more lines in Review; do not require the baseline's
  14/14/2 split or its USD 911.08 bundle.
- There is one advisory accounting explanation call per invoice and one
  advisory bundle explanation call for the completed run, including when no
  groups qualify. Strict output checks reject changed outcomes, missing or
  duplicate decisions, and invented evidence references. Prose remains
  **unreviewed AI explanation**, not approved accounting policy or a posting.
- Raw prompts, responses, deployment, call IDs/times, structured explanations,
  source-input digest and complete workflow-result digest are retained in the
  immutable `capex.LlmShowcaseRun.EvidenceJson` for each workflow when
  `--persist-results` is set, plus a matching Blob mirror. Scalar SQL columns
  record status, hashes, call counts and explanation counts. Task rows include
  `llm-call:` references for actually model-assisted lines.
  `UsedLlm` in accounting rows correctly remains false: it describes accounting
  authority, not whether an advisory explanation was generated elsewhere.

| SQL location | Stored outputs |
|---|---|
| `task.TaskSuggestion` | Thirty line-level tasks, rationales and evidence references |
| `capex.Assessment` | Thirty deterministic CapEx/OpEx/Review decisions |
| `capex.BundleCandidateGroup` / `capex.BundleCandidateMember` | Deterministic candidate groups and member amounts |
| `capex.LlmShowcaseRun` | Full actual task/model prompts and responses, accounting explanations, bundle explanation/summary, provenance and hashes |

Use fresh workflow/snapshot/result/evidence names and the newly built image,
not the completed baseline arguments. Require **all** of:
job success, `LLM_SHOWCASE_COMPLETE`, evidence `status=SUCCEEDED`, actual
`sqlLlmEvidenceRows=1`, matching SQL/Blob evidence hashes,
`llmCallsAttempted=llmResponsesReceived>0`, thirty accounting explanation items
across ten invoices, exact bundle explanation coverage, and
[validate_capex_sql_llm_demo_run.sql](../../sql/validation/validate_capex_sql_llm_demo_run.sql)
with the new run ID. That read-only query verifies the SQL evidence hash,
raw response counts and exact explanation/decision coverage, then selects the
full actual prompts/responses and explanations using `nvarchar(max)` JSON
projections, not the 4,000-character `JSON_VALUE` limit. It intentionally does
not assert fixed model outcomes.

Results are persisted before advisory explanation generation. If an explanation
fails, the job fails and stores `INCOMPLETE` SQL evidence including any actual
responses received before the error. A SQL evidence write failure rolls back
that write, fails the job and retains an `INCOMPLETE` Blob record without
claiming a SQL commit. SQL evidence commits before the Blob mirror, so a mirror
failure does not lose the actual outputs already stored in SQL. The job still
fails if a required mirror fails. Existing task/assessment commits may already
exist. Never mistake SQL reconciliation alone for AI-demo completion, and never
replay the same workflow after partial commits. Review the evidence and use a
fresh workflow/artifact set for a corrected rehearsal.

#### Current verification and build blocker

85 targeted regressions pass. The SQL-output extension tests exact stored JSON, large Unicode
responses, migration/grant preflight, immutable retry handling, rollback and
SQL/Blob failure behavior. A genuine GPT-5.1 source-only Bruegmann task call
passed the strengthened ambiguity boundary and returned twelve Review outcomes:
the shelving task variants need distinguishing evidence, and shipping retains
its ancillary review gate. This is not a reason to fabricate a resolved code.
Separate genuine read-only advisory calls produced twelve accounting explanation
items and one explanation of the **existing baseline** bundle. These local
smokes wrote no SQL rows and do not prove a new ten-invoice cloud execution.

The updated Bicep template compiles, and the existing job identity has Cognitive
Services OpenAI User on the existing Foundry account. However, the November 4
new-image build attempt was denied at
`Microsoft.ContainerRegistry/registries/listBuildSourceUploadUrl/action`.
No build was queued and no updated image was published. An authorized operator
must run [Build-Push-CapexSqlAcr.ps1](../../tools/Build-Push-CapexSqlAcr.ps1)
with `-AgentPool shared`, or upload the prepared allowlisted source archive to
an approved Cloud Shell session and perform the equivalent ACR build. Do not
change network rules, reuse the old image for these new flags, or retry denied
actions through a different API. After the image is built, configure the model
endpoint and start a fresh Portal execution, then verify all completion checks
above.

Use the refreshed `capex-sql-llm-sql-output-source.tar.gz` archive; its 98 files
are hash-checked against the final local source. The earlier
`capex-sql-llm-source.tar.gz` predates SQL LLM-output persistence and must not be
used for this requirement.
The archive includes migration 012 and the read-only SQL verification query.
The planned Portal parameter file
is `sql-llm-execution.parameters.json`, targeting workflow
`friday-sql-llm-20261105T014600Z`. Its image tag is **planned, not published**.
An authorized operator can build that exact tag from the archive without Docker:

```powershell
New-Item -ItemType Directory -Path .\capex-sql-llm-build-20261105T014600Z -ErrorAction Stop
tar -xzf .\capex-sql-llm-sql-output-source.tar.gz -C .\capex-sql-llm-build-20261105T014600Z
az acr build --subscription 0451df15-250e-42f5-b15c-f19a9bb9c21a --registry cictaicusacr --image capex/demo-v2:sql-llm-sql-output-20261105T014600Z --file Dockerfile.capex-v2 .\capex-sql-llm-build-20261105T014600Z
```

This requires normal ACR build authorization in that operator's approved session.
Require build success and verify the published digest/source before using the
parameters; preferably replace their image value with the verified digest URI.
Do not deploy the planned tag before it exists.

### Verified November 4 live SQL rehearsal

The Portal-deployed SQL job completed read-only preflight
`capex-demo-v2-sql-uxudg9w`, then execution
`capex-demo-v2-sql-796qxrx` with workflow ID
`friday-sql-20261105T010058Z`. Both executions report Succeeded.
Durable Log Analytics recorded `SQL_INPUTS_VALIDATED` with ten invoices,
thirty lines and input digest
`9dfff2660420c24831190657cf9d8c540135e896f9cea42a3bf7188948539567`.
The complete result JSON was reconstructed from split console records,
parsed and retained as session evidence.

| Evidence | Actual result |
|---|---|
| Task determinations / reported committed task rows | 30 / 30 |
| Accounting assessments / reported committed rows | 30 / 30 |
| Recommendations | 14 CapEx, 14 OpEx, 2 Review |
| Bundle groups / reported committed members | 1 / 11 |
| Bundle | Bruegmann task 5225, USD 911.08; CANDIDATE below USD 1000 threshold |
| Workflow errors | 0 |
| LLM calls / legacy events | 0 / 0 |

Run [validate_capex_sql_demo_run.sql](../../sql/validation/validate_capex_sql_demo_run.sql)
in the working SQL operator connection to independently reconcile the actual
persisted rows, source line coverage, outcomes and bundle membership.
Require `SQL_DEMO_RUN_VERIFIED`. This is read-only and pins this completed
run; do not use the historical three-package/23-line readiness script.
At 20:18 Eastern on November 4, the operator reported
`SQL_DEMO_RUN_VERIFIED` for `friday-sql-20261105T010058Z` with demo mode
`UNAPPROVED SYNTHETIC REFERENCE REHEARSAL; ZERO LLM CALLS`.
The query's source-line, persisted-row, recommendation and bundle
reconciliation checks therefore passed in the working SQL connection.
This operator-reported readback is retained separately from runtime logs.

Bruegmann has 11 OpEx lines and one Review; Kimley-Horn has seven CapEx,
two OpEx and one Review. JC07465 has six CapEx invoices and one OpEx
invoice (5063); JC07814 invoice 20294 is CapEx. Both Review assessments use
task 0000 and lack a matched approved nature rule. They are valid explicit
Review outcomes, not runtime errors. The USD 911.08 bundle is a future
candidate, not an approved asset or a threshold-crossing capitalization.

For rehearsal: show the ten source invoices, the matching line-level task
determinations, the recommendation/rationale/citations, the eleven-member
bundle, and the two unresolved-nature Review rows. State that reference
enrichment and accounting thresholds are synthetic, task resolution is
deterministic PO/PartMaster matching, and this branch makes no model calls.
It does not populate human approvals, ERP handoffs or event outboxes.

The saved job now has `--execute --persist-results` and this already-used
workflow/artifact identity. **Do not press Run now again unchanged.**
Any later run needs reviewed fresh artifacts and workflow ID, a new
preflight when inputs change, and reconciliation of prior committed rows.

### SQL-backed expanded rehearsal

The original artifact-based runner below still uses three packages/23 lines.
For the expanded SQL-staged input set, use
[`capex-demo-v2-sql`](../../autarch/capex/demo_v2_sql_job.py) instead.
This is an opt-in **synthetic reference rehearsal**, not automatic ingestion
from landing and not proof of a deployed or completed Azure execution.
Do not rerun an old seed/reset or start the old preparation/dispatch jobs.

The SQL path reads all packages tagged with the supplied fixture ID, validates
explicit expected invoice/line counts, and reads actual SQL Project, Site,
PurchaseOrder/Line, PartMaster, threshold, keyword-rule and active TaskMaster
rows. It checks header/package consistency, PO/part associations, reference
provenance, effective dates, catalog hashes and preservation of known source
values. Missing source quantities, prices, vendor IDs and dates remain NULL
in SQL and in the captured source packages. Substitutes exist only in the
processing copies and retain synthetic provenance.

An unambiguous PO/PartMaster agreement produces an exact-reference task
determination. A conflict/inactive code produces Review; codes are never
invented. **This path makes no LLM calls.** Do not describe the seeded task
codes as model-generated suggestions or the US catalog proxies as governed
European mappings. The existing model-assisted artifact runner is separate.
Native USD/EUR/DKK demo thresholds are selected per currency without FX
conversion or mixed-currency totals. The old USD-only/current-policy path
retains its Review behavior for unsupported regions/currencies.
The repair/never-capitalize sentinel examples do not demonstrate those
branches unless an actual line description matches their keywords.

Execution prerequisites:

1. Apply [011_grant_sql_demo_input_read_access.sql](../../sql/migrations/011_grant_sql_demo_input_read_access.sql)
   using the authorized SQL operator connection. It grants the existing runtime
   identity SELECT on eleven input tables, creates no tables/data, and does not
   replace migration 008's append-only result permissions.
2. Build an updated image using the existing
   [Dockerfile.capex-v2](../../Dockerfile.capex-v2). A local test does not update
   the deployed image. Use an authorized private-network Azure host with the
   assigned managed identity, ODBC driver, SQL grants and Blob artifact access.
   The job command must be `["capex-demo-v2-sql"]`; the old image entrypoint
   `capex-demo-v2` does not select this mode automatically.
   Use [capex-demo-v2-sql-job.bicep](../../infra/container-apps/capex-demo-v2-sql-job.bicep)
   for this mode, not the original artifact-runner template. Supply
   `jobArguments` using the read-only CLI flags below, without `--execute`
   or `--persist-results` for the first checkpoint. The job is manual,
   single-replica and has automatic retries disabled to avoid replaying a
   partially persisted execution.
3. Give every snapshot/result a fresh private artifact URI. Do not overwrite
   preflight evidence or use an already-evidenced workflow run ID.

**Private build prerequisite:** use an approved runner that can authenticate
to the private registry and Blob endpoints. Build from a clean context
containing only the Dockerfile, package manifest, README and required
`autarch` source/assets; do not upload local environments, secrets or source
invoices. A network-rule denial is not fixed by signing in again. Do not
enable public access or resize unrelated build pools to bypass this blocker.

#### Build in ACR without local Docker

The existing CapEx ACR Tasks pools can build this image without Docker on
the operator's computer. After approval to temporarily allocate one worker
in the named pool, run from the repository root with PowerShell 7, `tar`
(included with current Windows), and Azure CLI signed into the Circle K tenant:

```powershell
.\tools\Build-Push-CapexSqlAcr.ps1 -AgentPool fuelbuildpool
```

If the registry owner has explicitly enabled an access path for Azure's
shared workers, use `-AgentPool shared` instead. This mode currently requires
public network access Enabled with default action Allow, makes no private
pool calls, and does not change or restore the owner's network settings.
Do not enable broader access simply to run the script without owner approval.

The script requires an idle pool at count zero with no active/queued builds.
That idle-pool requirement applies only to named private pools.
It stages only Python source, the two packaged schemas, manifest, README and
Dockerfile; it does not upload invoices, local environments or credentials.
It uploads and queues a uniquely tagged build before starting a billable
worker. Submission uses ACR's ARM API through `az rest` to retain a structured
run ID even with CLI versions that suppress `acr build --no-wait` output.
The signed upload URL is kept in memory, not printed or saved.
The Dockerfile runs `capex-demo-v2-sql --help` as the non-root runtime
user; build success therefore includes that CLI import/entrypoint check.
The image's default entrypoint remains `capex-demo-v2`; the SQL job must
still explicitly select `capex-demo-v2-sql`.

Build evidence is saved under `%LOCALAPPDATA%\CapexSqlBuilds\<unique-tag>`
(override with `-OutputDirectory`). Retain `image-reference.json`, containing
the immutable digest obtained from the successful ACR run, the source
manifest, run result and build log. Registry data-plane access from the
operator's computer is not needed to obtain that digest from ARM.

On completion or failure, the script cancels only its own unfinished run
and returns its worker allocation to zero if no other work or capacity
changes are detected. If shared activity prevents safe cleanup, it fails
explicitly and requires operator review; do not scale away someone else's
build. A source-upload/network denial stops before worker allocation.
This script does not deploy/start a Container Apps job or change SQL data,
firewalls, public access, identities or role assignments.

**November 4 live attempt:** source upload and ARM submission succeeded
(`cju`), but `fuelbuildpool` failed worker provisioning with provider code
`Runtime failure` / `Service request failed. Status: 200` before the build
started. That run was canceled. The platform owner must restore pool health
before retrying that private pool. Do not use
`fuelbuildpool2` as an automatic fallback: it still has another run, `cjg`,
marked Running since October 27, despite zero allocated workers. Resolve
ownership before canceling that run or changing its pool capacity.

**Subsequent successful build:** after the user enabled registry network
access for shared workers, run `cjv` built and pushed
`capex/demo-v2:sql-20261104T211338Z-f44052d0`. Its verified immutable digest is
`sha256:e20cd46e6b97abe1793b2d8a0a9851bd89e1c7a74af16c642494c56a5f0d6456`.
The raw log contains the SQL CLI help output and successful build/push.
Raw log download avoids a Windows CLI character-encoding failure encountered
after the successful build. Both private pools remain at count zero;
`fuelbuildpool`'s zero-worker recovery is still Creating and needs platform
owner follow-up. No other run was canceled, and the assistant did not alter
the user's network settings. The SQL job is not deployed or executed yet.

#### Authorized-operator deployment handoff

The November 4 deployment recheck passed compilation and ARM validation
but Azure denied `Microsoft.Resources/deployments/whatIf/action`, confirmed
independently through ARM. Refresh the deployment account/PIM session or
use an authorized Circle K operator; do not bypass the failed check.
No new SQL tables are needed for this deployment step.

A subsequent fresh device-code sign-in authenticated the user but was
rejected by organization access criteria. That pending login was stopped.
The exact policy is unconfirmed until an Entra administrator reviews the
sign-in's Conditional Access details. Do not retry alternate flows to evade
the restriction or weaken policies. Use an administrator-approved access
path or an authorized Circle K operator in an approved environment.
The image already exists in ACR; this operator does not need local Docker.

Use the supplied `capex-demo-v2-sql-job.arm.json` and
`sql-preflight.parameters.json` together. The parameters pin the verified
image digest and contain only the read-only 10-invoice/30-line flags.
From the folder containing those two files, an authorized operator runs:

```powershell
az account set --subscription 0451df15-250e-42f5-b15c-f19a9bb9c21a
az deployment group validate -g cict-aisandbox-cus-rg --template-file .\capex-demo-v2-sql-job.arm.json --parameters .\sql-preflight.parameters.json
az deployment group what-if -g cict-aisandbox-cus-rg --template-file .\capex-demo-v2-sql-job.arm.json --parameters .\sql-preflight.parameters.json
```

Stop on any error or any change other than the single new SQL job.
After all required validation checks pass and the deployment is approved:

```powershell
az deployment group create -g cict-aisandbox-cus-rg -n capex-sql-preflight --template-file .\capex-demo-v2-sql-job.arm.json --parameters .\sql-preflight.parameters.json
az containerapp job start -g cict-aisandbox-cus-rg -n capex-demo-v2-sql
az containerapp job execution list -g cict-aisandbox-cus-rg -n capex-demo-v2-sql
```

Require a successful execution and return its logs with
`SQL_INPUTS_VALIDATED`, ten invoices, thirty lines and `inputSha256`.
Do not add `--execute` or `--persist-results` to this first checkpoint.
Return explicit failure logs instead of resetting/reloading the fixture.
The snapshot is create-only; use a fresh URI for any subsequent attempt.

Run the following on that host (PowerShell argument-continuation form shown):

```powershell
python -m autarch.capex.demo_v2_sql_job `
  --fixture-id '<operator-supplied-fixture-id>' `
  --allow-synthetic-inputs `
  --expected-invoices 10 --expected-lines 30 `
  --snapshot-uri '<fresh-private-preflight-snapshot-uri>'
```

Default behavior is read-only SQL preparation/validation plus creation of an
immutable input artifact. It performs no task/accounting/bundling processing,
publishes no events and writes no SQL results. Require `SQL_INPUTS_VALIDATED`,
ten invoices/thirty lines and retain `inputSha256`. Inspect the snapshot's
original versus processing packages, policies and reference matches.
The `sqlSnapshot` section retains the actual input/reference rows, including
their SQL identifiers, so the digest and reference provenance can be audited
without relying on later database contents.

After review, use a fresh execution snapshot/result URI and pass the retained
digest. Changed source/reference data fail before processing:

```powershell
python -m autarch.capex.demo_v2_sql_job `
  --fixture-id '<same-fixture-id>' `
  --allow-synthetic-inputs `
  --expected-invoices 10 --expected-lines 30 `
  --snapshot-uri '<fresh-private-execution-snapshot-uri>' `
  --expected-input-sha256 '<preflight-inputSha256>' `
  --workflow-run-id '<new-workflow-id>' `
  --output-uri '<fresh-private-result-uri>' `
  --execute --persist-results
```

Omit `--persist-results` for a processing-only rehearsal. Result exit codes
are 0/SUCCEEDED, 2/PARTIAL and 1/FAILED. Require exactly one determination and
assessment for each of the thirty source line IDs, the same workflow ID in
task correlation IDs and assessments, and bundle totals/membership that
reconcile to eligible processing lines. Never assert historical result counts
without inspecting the actual run.

This direct-batch path intentionally leaves legacy package status PREPARING
and emits no v1 InvoiceReady events: the v1 publication contract is not
nullable-aware. Normalized v2 packages are prepared in memory and captured
in the input artifact. `AssessmentRun` and result evidence, not a manually
changed legacy status, demonstrate processing. Source ingestion remains the
earlier manual SQL handoff.

Task persistence commits per package; accounting/group/member persistence is
a separate atomic transaction. A failure can leave committed task evidence.
Inspect PARTIAL errors and actual SQL rows; do not delete results or hide
partial execution. This SQL command requires a new run ID rather than
claiming a fresh-read/model rerun is an identical replay. It does not write
review decisions, approvals, handoffs or either outbox.

Presenter checkpoints:

| Stage | Evidence and truthful wording |
|---|---|
| Inputs | "Ten invoices/thirty lines were manually staged from source exports; supporting reference data are synthetic." |
| Preparation | "The original facts are preserved; the processing copy applies explicitly labeled demo enrichment." |
| Tasks | "The workflow calculates exact PO/PartMaster matches against the active catalog; this branch did not call an LLM." |
| Accounting | "Deterministic native-currency demo policies produce CapEx, OpEx or Review with citations; these are not approved business policy." |
| Bundling | "Only compatible eligible members form candidates. A candidate is not an approved asset." |
| Audit | "SQL rows and artifacts identify the execution, source lines and synthetic assumptions. Human approval/handoff are not prefilled." |

### Original artifact-based three-package workflow

The workflow processes the three demo invoice packages in one finite batch:

1. Verifies each normalized package, invoice, run, and source hash.
2. Uses governed US TaskMaster data for deterministic or GPT-5.1-assisted
   task suggestions.
3. Applies deterministic capitalization policy. GPT-5.1 never decides CapEx
   or OpEx.
4. Groups eligible below-threshold lines by task, site, project, and currency.
5. Persists immutable task, assessment, bundle, and replay evidence.
6. Writes one result artifact with package summaries and explicit errors.

Service Bus, Azure Functions, Microsoft Agent Framework, and hosted Foundry
agents are not in the Demo V2 execution path.

## Guided rehearsal: clean starting point

Proceed one stage at a time; save each result before advancing. The presenter
supplied 15 existing task rows: 12 prior Bruegmann results and three CICT-187
acceptance rows, all stamped with `gpt-6.1-sol`. These are historical evidence,
not proof that the new three-invoice V2 workflow has run.

### Stage 1: read-only baseline

Run [validate_capex_demo_start.sql](../../sql/validation/validate_capex_demo_start.sql)
in `cict-ai-cus-sqldb` and retain all result sets. It inventories task rows,
dependent evidence, prepared invoices, governed tasks, and new model tables.
It changes no data and requires no workflow ID.

Presenter wording:

> "Before processing the invoices, I verify our starting state. This separates
> historical test results from today's run, protects dependent audit evidence,
> and confirms which source and reference records are actually available."

Expected from the supplied snapshot, not an assertion about current SQL:
15 task rows across two packages. Capture the actual counts and dependencies.
Archive the original task rows before
an explicitly scoped reset; stop if references exist rather than deleting
human decisions or disabling constraints. A previous committed outbox event
must not be replayed and presented as fresh extraction.

### Stage 2: archive and clear all working tables except TaskMaster

The operator subsequently authorized a full restart of the 25 application
tables other than `ref.TaskMaster`, including preparation, task, accounting,
review, other reference tables, and both SQL outboxes. This supersedes the
task-only reset. Existing audit backup tables are intentionally preserved.

Run [reset_capex_demo_all_tables.sql](../../sql/admin/reset_capex_demo_all_tables.sql)
in `cict-ai-cus-sqldb` unchanged first. Expect `PREVIEW_ONLY` with all 25
target counts and the preserved TaskMaster count. Save and review the result.
The preview changes no application rows, but temporarily locks the tables.

Before committing, pause invoice producers, jobs, dispatchers, and event
consumers. SQL reset does not clear external queued events or Blob artifacts.
Set both `@WritersPaused = 1` and `@CommitReset = 1`, then rerun the entire
script. Every nonempty table is archived before any deletion. Require
`RESET_COMMITTED`, matching before/archived/deleted counts, zero remaining
rows for all targets, and `PRESERVED_UNCHANGED` for TaskMaster. Save the reset
ID and backup table names. Do not remove the snapshots.

The script preserves schemas, views, grants, and constraints. It validates
foreign-key deletion order rather than disabling constraints and rolls back
on any failure. Missing/unexpected tables or enabled triggers require review.
See the [SQL reset instructions](../../sql/README.md#full-demo-restart-preserving-taskmaster)
for permissions, backup scope, and failure handling.

Presenter wording:

> "I preserve historical records in audit snapshots, then clear the working
> tables for a clean rehearsal. The governed task master remains unchanged.
> Today's invoice, suggestion, and assessment records will be produced by
> the workflow, not prefilled to look like completed processing."

Keep writers paused until ingestion is ready. Next inventory and load the
required reference inputs, using approved source data or explicitly labeled
synthetic fixtures. The ingestion path must write `prep.Invoice` and link
`prep.InvoiceLine.InvoiceID`, not just populate the older package/line store.
Do not prefill downstream processing or human-review results.

Later stages must verify source/preparation persistence for all three inputs,
task determination, deterministic accounting, conditional bundling, persisted
reconciliation, and replay. Existing V2 only writes task/assessment/bundle
evidence; it does not populate all the newly created logical-model tables.
Missing integration is work to complete, not an expected side effect of 009.
Only populate reference/project/PO/review/handoff tables when appropriate
source data or a real human action exists. An unresolved task or accounting
Review remains a valid result; do not force a governed code or classification.
Fresh model output may differ from prior saved deterministic evidence.

### Stage 3: load real invoice facts before synthetic data

The operator corrected the JC source selection to include both workbooks.
They contain two different projects and eight distinct invoice-type records.
Together with Bruegmann and Kimley-Horn, extract ten distinct invoice headers,
ten packages, and thirty source lines. Do not merge the JC projects/currencies
or copy a PO from a separate non-invoice row. The original three-invoice
workflow request still covers only 23 lines; the expanded source inventory
must not be claimed as fully processed by that unchanged request.

Keep application writers paused. In `cict-ai-cus-sqldb`:

1. Apply [010_preserve_source_invoice_gaps.sql](../../sql/migrations/010_preserve_source_invoice_gaps.sql).
   It permits actual source gaps as NULL and adds JSON provenance columns.
2. Run [load_capex_demo_source_invoices.sql](../../sql/admin/load_capex_demo_source_invoices.sql).
   No flag changes or synthetic seed are required.
3. Save its single `SOURCE_INVOICES_LOADED` result grid. Expect ten
   invoice/package rows and thirty lines, with all packages `PREPARING`.

The extraction is available as
[invoice-source-extract.json](../../examples/data/capex/source-load/invoice-source-extract.json).
It includes confirmed vendor IDs, source header/line facts, business units,
tax context, and unconfirmed order/address/date information under source
labels. It is generated from the repository's four original source files.
No fresh private Blob inventory or live SQL execution is implied.
The loader preserves the original three normalized invoice/package/line
identities and creates separate identities for the seven additional invoices.

Missing dates/vendor IDs remain NULL for the eight JC invoices. Nineteen
quantity/unit-cost values remain NULL, including Bruegmann shipping.
No fake dates, zeros, FX conversion, PO selection, AFE mapping, or synthetic
context are applied. Confirmed source vendor IDs are `2153631` and `2117718`;
JDE Address Number is retained without claiming it is a confirmed VendorID.

Presenter wording:

> "I load the actual extracted invoice records first. Invoice headers,
> packages, and lines share their source identities, while source gaps and
> unconfirmed mappings remain visible. These two JC exports contain multiple
> invoices, so I keep their projects, invoice numbers, and currencies separate."

Stop after the SQL result is verified. This is source staging, not model
execution or event publication. No task/accounting/bundle/review/outbox rows
are inserted. The unchanged V1 published contract requires numeric quantity
and unit cost, so do not mark these nullable packages READY or dispatch them
through V1 without a nullable-aware integration path.
See the [source-load handoff](../../sql/README.md#load-real-invoice-headers-packages-and-lines-first).

### Stage 4: synthetic prerequisites (paused pending source-first revision)

**Do not run the earlier seed yet.** The operator changed the order to real
invoice ingestion first. The retained seed below checks the pre-ingestion
empty state; it must be adapted and verified against the loaded source rows
before the synthetic stage resumes. No synthetic SQL data has been loaded
by the assistant.

The operator reported `PRESERVED_UNCHANGED` with 761 TaskMaster rows and
exported the reset backup names. Do not run the reset again. Keep writers
paused and run
[seed_capex_demo_synthetic_prerequisites.sql](../../sql/admin/seed_capex_demo_synthetic_prerequisites.sql)
in `cict-ai-cus-sqldb`. No flag edits are required. This is a committing seed,
not a preview. It inserts only missing, matching fixtures; a conflicting
existing row causes an error rather than an overwrite. Repeating it is safe
before ingestion, but it deliberately rejects a database that has progressed
beyond this clean prerequisite stage.

The script returns **one result grid** with
`SYNTHETIC_PREREQUISITES_READY` and the explicitly unapproved scenario label:

| Table | Expected rows | Meaning |
|---|---:|---|
| `ref.TaskMaster` | 761, based on the operator's last result | Unchanged governed catalog; actual preserved count is reported |
| `prep.Project` | 3 | Existing DEMO-prefixed overlay IDs, OPEN, bundling in scope |
| `prep.Site` | 3 | Existing DEMO-prefixed overlay IDs; lease status remains NULL |
| `ref.CapitalizationThreshold` | 1 | Unapproved USD 1,000 unit-cost >= fixture; no EUR threshold |
| Other 22 working tables | 0 each | Waiting for source ingestion, processing, or actual human review |

`SYN-SQL-001` explicitly records mandatory project-column placeholders:
budget 0 and type `SYNTHETIC_DEMO`. Zero is not an authoritative budget and
must not qualify a project-budget exception. Project start date and lease
status are not invented. `SYN-SQL-002` records the threshold's fixture-only
effective date. Both assumptions are in the
[synthetic input register](../../examples/data/capex/reference-v2/synthetic-input-register-v2.json).
Do not treat the SQL column name `GovernedThreshold` as evidence of approval.
The row is labeled synthetic in `ThresholdType` and its policy reference.

Do not seed PO/header/line records without source data, pre-map parts to
tasks to force a desired model response, invent keyword exceptions, or
prefill task/assessment/bundle/review/outbox results. PO and part mappings
are optional; missing facts remain missing. The existing synthetic
capitalization rules remain in JSON and are not represented by fake
never-capitalize or repair/maintenance rows.

Presenter wording:

> "The task catalog is real and unchanged. Because source project and site
> context is unavailable, I load clearly labeled demo fixtures and a
> synthetic threshold. These are not approved business records. Invoice
> headers and lines, task suggestions, and accounting results will be
> created by their processing steps, not preloaded as completed results."

Stop after saving the single result grid. SQL seeding does not run invoices
or prove integration: V2 currently reads its JSON reference snapshots and
context overlays, not these SQL fixtures. The subsequent ingestion stage
must wire and verify `prep.Invoice`, `prep.InvoicePackage`, and linked
`prep.InvoiceLine` for all three inputs. Missing JC invoice date/vendor ID
and nullable normalized quantities versus non-null legacy SQL columns also
require explicit handling; do not silently substitute dates, vendors, or zeros.

## November 4 live-readiness check

The workstation is signed into the target subscription. Storage and SQL both
have public network access disabled. Direct Blob inventory failed and SQL
returned error 47073 (Deny Public Network Access). Starting a read-only
inventory execution of the existing `capex-task-determination-proof` job was
also denied (`Microsoft.App/jobs/start/action`), including after the reported
PIM activation. No firewall settings, SQL rows, or job definitions were changed.
Refresh the Circle K login after PIM activation and have an authorized operator
run the inventory from the private network. Do not equate PIM activation with
network access or a successful deployment.

The ARM job inventory did not contain `capex-demo-v2`. Local replay of prior
task results is available for all three inputs, but does not verify current
Blob contents, a fresh model call, or live SQL persistence.

Demo V2 loads normalized packages from Blob and writes task and assessment
evidence; it does **not** load those packages into the V1 `prep.InvoicePackage`
or `prep.InvoiceLine` tables. Deploying V2 alone will not make the other two
invoices appear in those V1 tables. Missing PO, site, EU task, or approved
policy data must not be manufactured to populate the logical table diagram.

## Azure resources and responsibilities

| Resource | Purpose |
|---|---|
| Container Apps Job `capex-demo-v2` | Runs one direct three-invoice finite batch and exits |
| Container Apps Environment `cict-ai-cus-acaenv` | Hosts the job and captures execution logs |
| ACR `cictaicusacr` | Stores the immutable `capex/demo-v2` image |
| Storage `cictaicusst`, container `landing` | Stores requests, normalized inputs, governed references, and immutable results |
| Azure SQL `cict-ai-cus-sqldb` | Stores TaskSuggestion, Assessment, BundleCandidateGroup, and BundleCandidateMember evidence |
| Foundry account `cict-ai-cus-foundry` | Hosts the direct `gpt-5.1` Responses deployment |
| UAMI `cict-ai-cus-capex-id` | Authenticates to ACR, Blob, SQL, and GPT-5.1 without secrets |

Verified UAMI assignments:

- `AcrPull` on `cictaicusacr`.
- `Storage Blob Data Contributor` on the `landing` container.
- `Cognitive Services OpenAI User` on the Foundry account.

The SQL contained user and database roles still require verification from a
network path that can reach the SQL endpoint.

## Current acceptance evidence

### Truthful current policy

- Status: `SUCCEEDED`.
- Packages: 3.
- Lines: 23.
- Task determinations: 23.
- Assessments: 23 Review, 0 CapEx, 0 OpEx.
- Bundle groups: 0.
- Reason: Circle K policy confirmations are still missing; the workflow does
  not invent accounting policy.

### Explicit synthetic demo

> **Warning:** These are unapproved demo assumptions, not Circle K policy.

- Status: `SUCCEEDED`.
- Packages: 3.
- Lines: 23.
- Assessments: 7 CapEx, 11 OpEx, 5 Review.
- Bundle groups: 1.
- Bundle members: 9, totaling USD 455.56 on Bruegmann.
- Two zero-dollar Kimley lines remain ungrouped candidates, not a USD 0 bundle.
- Exact SQL-adapter replay: 0 additional inserts.
- Invalid governed task codes: 0.

The deterministic evidence file is
`examples/data/capex/results-v2/workflow.synthetic-validation.json`.

The supplied invoices do not demonstrate a cross-invoice or threshold-reaching
bundle: their eligible task/site/project keys differ. Cross-invoice grouping
is covered by automated synthetic tests, not by these source invoices. Do not
present the Bruegmann candidate as an approved capitalization decision.

## Pre-demo checklist

- [ ] Confirm the client understands that truthful and synthetic results are
  shown separately.
- [ ] Keep the synthetic warning visible.
- [ ] Confirm `gpt-5.1` is available.
- [ ] Confirm migration 007 exists in Azure SQL.
- [ ] Confirm the job identity can read/write the `landing` container.
- [ ] Confirm the job identity is an Azure SQL contained user with execute,
  select, and insert permissions for the CapEx schemas.
- [ ] Confirm the immutable image tag in the parameter file exists in ACR.
- [ ] Confirm the requested result blob does not already exist.
- [ ] Run one rehearsal and save the execution name.
- [ ] Keep the committed local evidence open as the fallback.

## Local rehearsal commands

Truthful current-policy run:

```powershell
.\.venv314\Scripts\python.exe tools\build_capex_demo_v2_request.py `
  --workflow-run-id capex-demo-v2-rehearsal-current `
  --output .session\capex-current-request.json

$env:AZURE_OPENAI_ENDPOINT = "https://cict-ai-cus-foundry.openai.azure.com/"
.\.venv314\Scripts\python.exe -m autarch.capex.demo_v2_job `
  --request-uri .session\capex-current-request.json `
  --task-master-uri examples\data\capex\reference-v2\task-master-us-v2.json `
  --policy-uri examples\data\capex\reference-v2\policy-config-v2.json `
  --output-uri .session\capex-current-result.json
```

Synthetic demo run:

```powershell
.\.venv314\Scripts\python.exe tools\build_capex_demo_v2_request.py `
  --workflow-run-id capex-demo-v2-rehearsal-synthetic `
  --synthetic `
  --output .session\capex-synthetic-request.json

$env:AZURE_OPENAI_ENDPOINT = "https://cict-ai-cus-foundry.openai.azure.com/"
.\.venv314\Scripts\python.exe -m autarch.capex.demo_v2_job `
  --request-uri .session\capex-synthetic-request.json `
  --task-master-uri examples\data\capex\reference-v2\task-master-us-v2.json `
  --policy-uri examples\data\capex\reference-v2\policy-config-v2.json `
  --rule-set-uri examples\data\capex\reference-v2\synthetic-capitalization-rules-v2.json `
  --synthetic-register-uri examples\data\capex\reference-v2\synthetic-input-register-v2.json `
  --output-uri .session\capex-synthetic-result.json
```

Deterministic fallback:

```powershell
.\.venv314\Scripts\python.exe tools\validate_capex_demo_v2_workflow.py
```

## Azure deployment and execution

Earlier acceptance used GPT-5.1 and Blob writes. The November 4 workstation
checks cannot access Blob/SQL directly or start the private-network job.
An authorized operator must run these steps.

### SQL contact handoff

Connect to `cict-ai-cus-sql.database.windows.net`, database
`cict-ai-cus-sqldb`, from an approved private-network path. These scripts can
be opened in SSMS or the VS Code SQL editor; no SQLCMD substitution mode is
required.

#### Letting the presenter run the SQL scripts

The SQL administrator can run
[grant_capex_demo_operator_access.sql](../../sql/admin/grant_capex_demo_operator_access.sql)
connected directly to `cict-ai-cus-sqldb`. It targets the presenter's Circle K
Entra account directly. The administrator must add that contained database
user first and grant the listed permissions.

The script follows the approved colleague-style schema-control pattern:

- Creates `prep`, `task`, `capex`, `review`, `ops`, and `ref` schemas
  from the shared data model, plus `pub` for the published views required by
  the repository.
- Grants CONTROL on those seven schemas to the human operator.
- Grants database CREATE TABLE, VIEW, PROCEDURE, FUNCTION, TYPE, and SYNONYM
  permissions, with schema control providing the target-schema access.

It contains only schema creation and the same schema-control/object-creation
grants as the colleague's example, with no existence checks. Skip CREATE
SCHEMA statements for schemas that already exist; execute schema creation
statements individually in the SQL editor. It does not create users, grant CONNECT
with grant option, or grant visibility of the runtime principal. The DBA
must separately handle any additional principal visibility or CONNECT
delegation needed to run migration 008, or execute 008 directly.

This supports schema work, inventory, rollback verification, and readiness
queries. Creating schemas does not create their business
tables or insert data. Review/ops table implementations are not added by this
access script. No database-owner or user-administration role is added.

**This is elevated schema-administration access, including changing/deleting
data and objects and delegating permissions, not read-only access.** Shared
`dbo` schema ownership can also permit wider access through ownership
chaining. The DBA must approve this level of trust and review/remove the
temporary deployment and delegation privileges after the demo while
preserving the runtime grants from 008. If that access is not acceptable,
the DBA should execute the migrations directly instead.

SQL grants do not enable the private-network connection or grant permission
to start Azure jobs. Reconnect using Entra authentication after provisioning.
This access script has repository contract checks but has not been
executed against live SQL from this workstation.

#### Execution order

For the complete v2.1 model inventory, apply
[009_complete_capex_model_tables.sql](../../sql/migrations/009_complete_capex_model_tables.sql)
after the earlier migrations and run
[validate_capex_model_schema.sql](../../sql/validation/validate_capex_model_schema.sql).
This creates all 16 missing modeled tables and adds missing names to the
seven existing ones without changing runtime IDs. It needs no completed
workflow ID. See [the SQL model-alignment notes](../../sql/README.md) for
type/key differences, nullable fields, aliases, and deferred relationships;
column coverage is not exact physical-model equivalence.

1. Run [validate_capex_demo_v2_inventory.sql](../../sql/validation/validate_capex_demo_v2_inventory.sql)
   and return its result sets. This is read-only and determines what is
   actually missing; current live table contents are not yet verified.
2. Apply [007_create_capex_assessment_store.sql](../../sql/migrations/007_create_capex_assessment_store.sql).
   It creates the four missing V2 tables if absent:
   `capex.AssessmentRun`, `capex.Assessment`, `capex.BundleCandidateGroup`,
   and `capex.BundleCandidateMember`, and creates/updates the read-only
   `pub.v1_Assessment` view. It does not insert invoice or demo result data.
3. If the inventory shows that `task.TaskSuggestion` or `pub.v1_TaskMaster`
   is missing, apply the corresponding prerequisite migrations
   [004](../../sql/migrations/004_create_task_suggestion_table.sql),
   [005](../../sql/migrations/005_create_task_master.sql), and
   [006](../../sql/migrations/006_align_task_suggestion.sql) in order as
   needed. Creating TaskMaster does not load its governed catalog; use the
   approved reference seed, never invented task rows.
4. Apply [008_grant_capex_demo_v2_access.sql](../../sql/migrations/008_grant_capex_demo_v2_access.sql).
   It grants object-level SELECT/INSERT to the existing approved runtime
   user, plus read access to the published views. It adds no UPDATE, DELETE,
   DDL, database-wide writer, or owner permissions and does not remove any
   existing shared-identity permissions.
   If the contained user is missing, an authorized Entra database
   administrator must first verify the approved managed identity and run:

   ```sql
   CREATE USER [cict-ai-cus-capex-id] FROM EXTERNAL PROVIDER;
   ```

5. Run [003_verify_capex_assessment_store.sql](../../sql/tests/003_verify_capex_assessment_store.sql).
   This exercises the table constraints using a rolled-back test transaction.
6. After the application runs **both** modes with separate, new workflow IDs,
   run [validate_capex_demo_v2_readiness.sql](../../sql/validation/validate_capex_demo_v2_readiness.sql)
   twice. Edit `@WorkflowRunId` to the completed ID and set `@Synthetic` to
   `0` for source/current-policy evidence or `1` for the visibly synthetic
   demo. Return all result sets and any errors.

The readiness query checks all three normalized package IDs individually:
Bruegmann 12 lines, Kimley-Horn 10, JC invoice 20294 one line. It reconciles
23 task determinations with 23 assessments, validates governed task codes,
checks the selected accounting baseline, and verifies positive-value bundles
and their exact membership. It throws on missing/inconsistent evidence;
empty tables cannot pass. It is tailored to the committed normalized inputs
and corrected deterministic baseline, not arbitrary future invoice batches.
A fresh model run that differs must be investigated, not forced to pass.

These new SQL scripts have repository contract checks but have not been
executed against live SQL from this workstation. The SQL contact must verify
execution and runtime permissions on the private network.

**Creating tables is only the schema step.** The application still must be
built/deployed and executed to populate them. SQL permissions do not grant
Azure `Microsoft.App/jobs/start/action`, which remains an operator/PIM
issue. V2 also does not populate the V1 preparation tables.

### Copy-ready operator request

> Subject: CapEx Demo V2 deployment permission and SQL execution
>
> Hi Ehsan,
>
> The CapEx Demo V2 code and three-invoice acceptance validation are complete.
> The remaining step is to deploy one manual Container Apps Job into the
> existing `cict-aisandbox-cus-rg` environment and apply additive SQL
> migration 007.
>
> My Circle K account can invoke GPT-5.1 and write the demo Blob artifacts,
> but ARM validation currently fails because it does not have
> `Microsoft.Resources/deployments/validate/action` or
> `Microsoft.Resources/deployments/whatIf/action`. Azure SQL is also not
> reachable from my current network path.
>
> Could you either run the documented deployment steps below, or temporarily
> grant Contributor at the resource-group scope to my account? The CLI command
> for the latter is:
>
> ```powershell
> az role assignment create `
>   --assignee-object-id <RACHAEL-OBJECT-ID> `
>   --assignee-principal-type User `
>   --role Contributor `
>   --scope /subscriptions/0451df15-250e-42f5-b15c-f19a9bb9c21a/resourceGroups/cict-aisandbox-cus-rg
> ```
>
> Please also run migration
> `sql/migrations/007_create_capex_assessment_store.sql` from a network path
> that can reach `cict-ai-cus-sql.database.windows.net`, and confirm that
> managed identity `cict-ai-cus-capex-id` receives the object-level grants
> in `sql/migrations/008_grant_capex_demo_v2_access.sql`. Please return
> the inventory results, then run
> `sql/validation/validate_capex_demo_v2_readiness.sql` after each completed
> application run using its workflow ID and demo mode.
>
> The UAMI already has the required `AcrPull`,
> `Storage Blob Data Contributor`, and
> `Cognitive Services OpenAI User` assignments.
>
> Thank you.

### 1. Build the immutable image

```powershell
az acr build `
  --registry cictaicusacr `
  --image capex/demo-v2:20261104.1 `
  --file Dockerfile.capex-v2 `
  .
```

### 2. Apply the additive SQL migration

From a network path that can reach Azure SQL:

```powershell
sqlcmd -S cict-ai-cus-sql.database.windows.net `
  -d cict-ai-cus-sqldb `
  -G `
  -b `
  -i sql\migrations\007_create_capex_assessment_store.sql
```

Run the rollback-based verification:

```powershell
sqlcmd -S cict-ai-cus-sql.database.windows.net `
  -d cict-ai-cus-sqldb `
  -G `
  -b `
  -i sql\tests\003_verify_capex_assessment_store.sql
```

### 3. Stage immutable Blob inputs

Upload the three normalized artifacts and four reference artifacts under:

```text
landing/capex/demo-v2/normalized/
landing/capex/demo-v2/reference/
```

Use `az storage blob upload --auth-mode login --overwrite false` for each
artifact. Then build a Blob-backed request:

```powershell
.\.venv314\Scripts\python.exe tools\build_capex_demo_v2_request.py `
  --workflow-run-id capex-demo-v2-synthetic-20261104 `
  --artifact-base-uri https://cictaicusst.blob.core.windows.net/landing/capex/demo-v2/normalized `
  --synthetic `
  --persist-results `
  --output .session\synthetic-20261104.json
```

Upload that request to:

```text
landing/capex/demo-v2/requests/synthetic-20261104.json
```

### 4. Validate and deploy the job

```powershell
az deployment group validate `
  --resource-group cict-aisandbox-cus-rg `
  --template-file infra\container-apps\capex-demo-v2-job.bicep `
  --parameters infra\container-apps\capex-demo-v2-job.parameters.json

az deployment group what-if `
  --resource-group cict-aisandbox-cus-rg `
  --template-file infra\container-apps\capex-demo-v2-job.bicep `
  --parameters infra\container-apps\capex-demo-v2-job.parameters.json

az deployment group create `
  --name capex-demo-v2-job `
  --resource-group cict-aisandbox-cus-rg `
  --template-file infra\container-apps\capex-demo-v2-job.bicep `
  --parameters infra\container-apps\capex-demo-v2-job.parameters.json
```

What-if must report no deletes before deployment.

### 5. Start and observe

```powershell
$execution = az containerapp job start `
  --name capex-demo-v2 `
  --resource-group cict-aisandbox-cus-rg `
  --query name -o tsv

az containerapp job execution show `
  --name capex-demo-v2 `
  --resource-group cict-aisandbox-cus-rg `
  --job-execution-name $execution `
  --output json
```

Retrieve the result blob and reconcile it with:

```sql
SELECT * FROM capex.AssessmentRun WHERE workflowRunId = '<workflow-run-id>';
SELECT COUNT(*) FROM pub.v1_Assessment WHERE workflowRunId = '<workflow-run-id>';
SELECT COUNT(*) FROM capex.BundleCandidateGroup WHERE workflowRunId = '<workflow-run-id>';
SELECT COUNT(*) FROM capex.BundleCandidateMember WHERE workflowRunId = '<workflow-run-id>';
```

Expected synthetic counts are 23 assessments, 1 group, and 9 members.
Use a new workflow ID and result blob for the corrected bundle behavior;
do not overwrite or replay a previously persisted two-group result under
the same workflow ID.

## Ten-minute presenter script

| Time | Show | Say |
|---:|---|---|
| 0:00-1:00 | Architecture | “This is a finite Container Apps batch. Blob holds governed inputs and evidence; SQL holds immutable results; GPT only interprets task codes.” |
| 1:00-2:00 | Three source packages | “We preserve source values and hashes. Missing values remain missing.” |
| 2:00-4:00 | Truthful result | “With policy still unapproved, all 23 lines correctly remain Review. The system does not fabricate CapEx/OpEx policy.” |
| 4:00-6:00 | Synthetic register | “For demonstration only, each assumption is explicit, opt-in, unapproved, and tied to a Circle K replacement question.” |
| 6:00-8:00 | Synthetic result | “The demo produces 7 CapEx, 11 OpEx, 5 Review, and one within-invoice bundle candidate of USD 455.56; it does not reach the threshold.” |
| 8:00-9:00 | Replay and persistence | “The workflow validates governed task codes, reconciles every group, and makes exact replay a no-op.” |
| 9:00-10:00 | Close | “Once Circle K supplies the requested policy and source data, we replace assumptions rather than changing the architecture.” |

## Circle K replacement-data request

Ask for:

1. The approved CapEx-by-nature, OpEx-by-nature, never-capitalize, and
   repair/maintenance rule library, including scope, priority, effective
   dates, and policy citations.
2. Approved treatment and exceptions for TaskMaster `5225 WIRE SHELVING`.
3. Approved treatment and directly attributable cost criteria for TaskMaster
   `0007 ENVIRONMENTAL`.
4. Whether exactly USD 1,000 meets the threshold.
5. Whether the threshold uses unit cost, line amount, asset total, invoice
   total, or project aggregate.
6. Whether the USD 300/site and USD 30,000/project exception is enabled, plus
   the complete project aggregate source and period.
7. Authoritative Kimley-Horn quantity/unit-cost values.
8. Project ID, site ID, project status, bundling scope, and approved status
   values for all three invoices.
9. The governed JC-to-TaskMaster mapping or applicable regional TaskMaster.
10. The approved non-USD exchange-rate source and effective-date rule.
11. The historical bundling source and lookback period.
12. Expected finance outcomes for the three demo invoices.
13. Approved handling of shipping, freight, installation, labor,
    reimbursables, repair, and replacement costs.

## Jira-ready close-outs

### CICT-238

Implemented deterministic candidate grouping across invoices, vendors, and
purchase orders with stable group IDs, reconciliation, missing-context
safeguards, and human-final task override support. Truthful result has no
groups; corrected synthetic result has 1 group and 9 members. The source
invoices do not demonstrate cross-invoice grouping.

### CICT-239

Implemented append-only assessment and bundle persistence with one atomic
transaction, canonical replay hashing, no-op exact replay, and rejection of a
changed replay. Added migration 007 and rollback-based SQL verification.

### CICT-240

Implemented the direct finite batch, local/Blob loaders, source verification,
governed task stage, deterministic capitalization and bundling, task and
assessment persistence adapters, result output, exit codes, non-root image,
manual Container Apps Job Bicep, and request builder. Source is complete;
live Azure deployment awaits operator ARM permission and SQL network access.

### CICT-241

Validated all three packages under one workflow ID: 23 lines, 23 task
determinations, 23 assessments, 0 invalid task codes, reconciled persistence
counts, and 0 additional inserts on exact replay. Live GPT-5.1 CLI runs
completed for both truthful and synthetic modes.

### CICT-242

**Status: In progress. Do not close yet.**

The operator runbook, presenter script, commands, expected results, fallback
evidence, and data-request checklist are prepared. Closure additionally
requires presenter training, a complete SQL inventory, review of all
synthetic assumptions with their Circle K replacement requests, and at least
one timed rehearsal plus a question-and-answer rehearsal. Use
`docs/capex/demo-v2-presenter-walkthrough.md` as the training guide.
