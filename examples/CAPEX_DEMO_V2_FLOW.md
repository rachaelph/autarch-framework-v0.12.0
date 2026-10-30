# CapEx Demo V2 Workflow and Contracts

## Purpose

Demo V2 processes normalized invoice lines, recommends governed task codes, applies deterministic capitalization and bundling rules, and persists the resulting evidence. The demo path runs directly in an Azure Container Apps Job. It does not use Azure Service Bus, Azure Functions, or Microsoft Agent Framework.

The workflow reuses Azure Blob Storage, Azure SQL, managed identity, and existing TaskMaster data. Demo V2 reads the lossless `2.0-normalized` package while the existing `InvoicePackage v1` path remains available unchanged. The model integration uses the Autarch framework wrapper with LangChain and the Foundry deployment named `gpt-5.1`.

## Runtime flow

1. A Container Apps Job receives one `DemoWorkflowRequest`.
2. The job loads each referenced Demo V2 normalized package from Blob Storage and verifies its package ID, invoice ID, and source hash when expected values were supplied.
3. The job loads the governed TaskMaster snapshot for the requested region.
4. Task determination first uses exact governed lookup. LangChain is used only when a deterministic match is unavailable.
5. Every proposed task code is validated against the loaded TaskMaster before a `TaskDetermination` can be persisted.
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

The evidence-based policy matrix, proposed demo snapshot, and finance confirmation questions are documented in [Demo V2 policy inputs and assumptions](../docs/capex/demo-v2-policy-assumptions.md). Values marked unapproved must not be represented as final Circle K accounting policy.

## AI and deterministic boundaries

### LangChain/model responsibility

- Interpret ambiguous invoice-line descriptions.
- Select a candidate only from the supplied governed TaskMaster records.
- Return task rationale and evidence references.

### Deterministic responsibility

- Exact part/task lookup before model invocation.
- TaskMaster membership validation.
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
