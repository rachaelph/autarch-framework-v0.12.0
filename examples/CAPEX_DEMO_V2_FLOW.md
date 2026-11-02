# CapEx Demo V2 Workflow and Contracts

## Purpose

Demo V2 processes normalized invoice lines, recommends governed task codes, applies deterministic capitalization and bundling rules, and persists the resulting evidence. The demo path runs directly in an Azure Container Apps Job. It does not use Azure Service Bus, Azure Functions, or Microsoft Agent Framework.

The workflow reuses Azure Blob Storage, Azure SQL, managed identity, and existing TaskMaster data. Demo V2 reads the lossless `2.0-normalized` package while the existing `InvoicePackage v1` path remains available unchanged. The model integration uses the Autarch framework wrapper with LangChain and the Foundry deployment named `gpt-5.1`.

## Runtime flow

1. A Container Apps Job receives one `DemoWorkflowRequest`.
2. The job loads each referenced Demo V2 normalized package from Blob Storage and verifies its package ID, invoice ID, and source hash when expected values were supplied.
3. The job loads the governed TaskMaster snapshot for the requested region.
4. Deterministic token-overlap retrieval selects a small governed candidate set. Task determination first uses exact governed lookup; LangChain/GPT-5.1 is used only when a deterministic match is unavailable.
5. Candidate-backed unresolved lines from the same invoice are sent in one structured LangChain call. Every returned line and task code is validated against both the request and loaded TaskMaster before a `TaskDetermination` can be persisted.
6. Deterministic code applies nature-of-spend, never-capitalize, repair/maintenance, threshold, and missing-context rules to produce a `CapitalizationAssessment`.
7. Eligible lines are grouped using the approved task code, site, project, and currency. Missing required grouping context produces `REVIEW`; the workflow does not invent values.
8. Task determinations, assessments, and bundle candidates are persisted transactionally by workflow run.
9. The job emits one `DemoWorkflowResult` containing package summaries, line-level outputs, bundle groups, persistence counts, and explicit errors.

## Contract ownership

| Contract | Producer | Consumer | Purpose |
|---|---|---|---|
| `2.0-normalized` invoice package | Demo V2 normalizer | Demo V2 job | Lossless invoice values, source provenance, data-quality blockers, and separate synthetic context |
| `DemoWorkflowRequest` | Job launcher | Demo V2 job | Direct-batch input; replaces the Service Bus event in the V2 path |
| `TaskDetermination` | Governed lookup/LangChain task stage | Capitalization stage and `task.TaskSuggestion` adapter | Task code only; never decides CapEx or OpEx |
| `CapitalizationAssessment` | Deterministic policy stage | SQL persistence and demo output | Line-level CapEx, OpEx, or Review result |
| `BundleCandidateGroup` | Deterministic bundling stage | SQL persistence and demo output | Bundle grouping and evidence |
| `DemoWorkflowResult` | Demo V2 job | Operator/demo harness | Complete execution evidence and status |

The authoritative Python contracts are in `autarch.capex.demo_v2_contracts`. They reject unknown properties, accept camelCase JSON, and can emit JSON Schema with `model_json_schema(by_alias=True)`.

The generated CICT-232 reference artifacts are in `examples/data/capex/reference-v2`:

- `task-master-us-v2.json` contains 761 active, unique US tasks with retrieval text and source metadata.
- `policy-config-v2.json` contains the evidence-based capitalization and bundling settings, approval states, unresolved confirmation IDs, and JC review behavior.
- `reference-manifest.json` records artifact hashes, the governed task count, policy ID, and unresolved-confirmation count.

Regenerate them with:

```powershell
.\.venv314\Scripts\python.exe tools\generate_capex_demo_v2_reference.py
```

The generator verifies the original workbook SHA-256 before producing output. GPT-5.1 may receive only candidates from this governed snapshot, and every returned code must still pass membership validation.

The CICT-233 task-suggestion implementation is in `autarch.capex.demo_v2_task_suggestion`. Its production builder wraps the Autarch Azure OpenAI provider in a LangChain structured-output pipeline. Azure OpenAI uses an API key only when one is explicitly configured; otherwise the provider acquires a Microsoft Entra token through `azure-identity`, using `AZURE_CLIENT_ID` for a user-assigned managed identity when supplied. The active deployment name remains `gpt-5.1`.

The task stage makes no model call when a governed source task code or unique exact task description is available. An unknown supplied source task code, including the supplied JC code `0303`, is returned as `REVIEW` without asking GPT-5.1 to invent a mapping. If any remaining line has governed retrieval candidates, all such lines for that invoice are sent in one model call.

Candidate retrieval ignores numeric-only tokens so dimensions such as `10`, `20`, and `886` do not match unrelated numeric task-code fragments. Line-level candidates are supplemented with bounded invoice-level candidates, which gives proprietary product descriptions such as Bruegmann `FlexRoller` access to the governed shelving candidates found elsewhere on the same invoice.

Environmental specialty descriptions are normalized to the governed `0007` `ENVIRONMENTAL` candidate when they contain bounded domain markers such as air-quality emissions, biological or cultural resources, health risk, noise and vibration, Joshua-tree conservation, or common source misspellings. Reimbursable expenses are held in deterministic `REVIEW` because TaskMaster has no standalone reimbursable-expense task and no approved allocation rule maps them to a project task.

Validate the approved Bruegmann package with:

```powershell
$env:AZURE_OPENAI_ENDPOINT = "<Foundry OpenAI v1 Responses endpoint>"
$env:AZURE_OPENAI_DEPLOYMENT = "gpt-5.1"
.\.venv314\Scripts\python.exe tools\validate_capex_demo_v2_bruegmann.py `
  --output examples\data\capex\results-v2\bruegmann.task-suggestions.json
```

The validator requires exactly one model call and 12 determinations. It keeps shipping in `REVIEW` because the governed TaskMaster has no shipping/freight task and flags any resolved Bruegmann product line whose governed task is not shelving-related. Authentication uses an explicitly configured API key only when present; otherwise it uses Microsoft Entra authentication. Do not place model keys in source files, command arguments, screenshots, or committed configuration.

Validate the approved Kimley-Horn package with:

```powershell
$env:AZURE_OPENAI_ENDPOINT = "<Foundry OpenAI v1 Responses endpoint>"
$env:AZURE_OPENAI_DEPLOYMENT = "gpt-5.1"
.\.venv314\Scripts\python.exe tools\validate_capex_demo_v2_kimley_horn.py `
  --output examples\data\capex\results-v2\kimley-horn.task-suggestions.json
```

The validator requires exactly one model call and 10 determinations. Lines 1-9 must resolve to governed task `0007` (`ENVIRONMENTAL`). Line 10 must remain deterministic `REVIEW` because it is a reimbursable expense with no approved task-allocation rule.

Validate the approved JC target and companion accounting artifacts with:

```powershell
.\.venv314\Scripts\python.exe tools\validate_capex_demo_v2_jc.py `
  --output examples\data\capex\results-v2\jc.task-reviews.json
```

The JC validator does not require a Foundry endpoint because GPT-5.1 must not be called. It verifies that the included `AI` rows use configured JC source codes that remain absent from the governed US TaskMaster, produces `REVIEW` for every included line without remapping it, retains the target `OV` row as excluded evidence, and retains the six companion `JP` accrual/reversal rows as excluded evidence. A newly supplied approved European mapping would require a separate policy and TaskMaster update rather than silent model inference.

For direct model invocation, assign the least-privilege `Cognitive Services OpenAI User` role at the Foundry account scope to the developer identity and, later, the Container Apps Job managed identity. This role includes `Microsoft.CognitiveServices/accounts/OpenAI/responses/*`. A broader `Foundry User` assignment also provides model data access but is not required for this direct Responses API validation. If a model key is ever exposed, rotate it before further use.

The evidence-based policy matrix, proposed demo snapshot, and finance confirmation questions are documented in [Demo V2 policy inputs and assumptions](../docs/capex/demo-v2-policy-assumptions.md). Values marked unapproved must not be represented as final Circle K accounting policy.

## AI and deterministic boundaries

### LangChain/model responsibility

- Interpret ambiguous invoice-line descriptions.
- Select a candidate only from the supplied governed TaskMaster records.
- Return task rationale and evidence references.
- Return exactly one structured result for each requested unresolved line.

### Deterministic responsibility

- Exact part/task lookup before model invocation.
- TaskMaster membership validation.
- Candidate-set, line-completeness, duplicate-line, and evidence-reference validation.
- OpEx-by-nature and never-capitalize rules.
- Repair and maintenance handling.
- Capitalization threshold comparison.
- Bundling scope, grouping, and aggregation.
- Missing-context and missing-policy handling.
- Stable identifiers, replay protection, transactions, and persistence.

The task stage does not produce a CapEx/OpEx recommendation. Capitalization and bundling are separate contract stages so model output cannot bypass policy rules.

## Review behavior

The workflow returns `REVIEW`, not a fabricated decision, when required context or policy is unavailable. A review assessment must include at least one `reviewReason`; missing fields are listed in `missingInputs`. A bundle can be accepted as a non-review candidate only when task code, site, project, and currency are present.

For the demo, synthetic site, project, or PO context may be used only when it is clearly marked and approved for the scenario. Financial policy, thresholds, and expected decisions must not be silently invented.

## Persistence targets

| Output | Target |
|---|---|
| Governed task determination | Existing `task.TaskSuggestion` through a V2 adapter |
| Capitalization assessment | `capex.Assessment` |
| Bundle header | `capex.BundleCandidateGroup` |
| Bundle members | `capex.BundleCandidateMember` |

The assessment and bundle tables are contract targets and require their own migration and persistence subtask. Existing V1 Service Bus and Function resources remain untouched but are outside this execution path.

## Failure and replay behavior

- Contract, source-hash, TaskMaster, policy, or SQL errors are explicit workflow errors.
- Unknown task codes are rejected before assessment or persistence.
- The same workflow run and line identity must not create duplicate durable rows.
- `FAILED` results contain errors; `SUCCEEDED` results do not.
- `PARTIAL` is used when only part of a multi-package run completes.
- Operator-visible summaries must reconcile task and assessment counts with each package's line count.

## Deferred integrations

Demo V2 does not implement ADF/source-system ingestion, POPA writeback, a review UI/API, Power BI, project-closure reassessment, or production policy-document ingestion. Those systems can consume the defined outputs later without changing the core task, assessment, and bundle boundaries.
