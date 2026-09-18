# CAPEX: Microsoft Agent Framework + Autarch

The default engine is now **seven real Microsoft Agent Framework LLM specialists**,
supervised by Autarch. The earlier keyword example remains only as `--engine rules`.
There is no automatic fallback from failed live execution to rules or a mock model.

This is an advisory finance workflow, not a production accounting system. It consumes
ABBYY-extracted data, does not repeat OCR, and never posts to ERP or changes P2P payments.

## Install and Run

Use Python 3.10+ from the repository root. On this Windows workspace, Python 3.12
is available at the following path; the old `.venv` is not needed:

```powershell
$py = "C:\Users\deepakbalan\AppData\Local\Programs\Python\Python312\python.exe"
& $py -m pip install -e ".[capex]"
$env:AZURE_OPENAI_ENDPOINT = "https://YOUR_RESOURCE.openai.azure.com/"
$env:AZURE_OPENAI_DEPLOYMENT = "YOUR_EXISTING_DEPLOYMENT"
az login
& $py examples/capex_flow.py --auth aad
```

Replace the endpoint and deployment placeholders with your resource's values.
Entra authentication uses `DefaultAzureCredential` (Azure CLI or managed identity,
for example). The identity needs inference permission such as **Cognitive Services
OpenAI User** on the resource. The script does not launch an interactive sign-in.
The deployment must support chat completions and JSON-schema structured outputs.

Alternatively, configure `AZURE_OPENAI_API_KEY` privately in your terminal/environment
and use `--auth key`. Do not put keys in source files, reports, command arguments, or chat.
`AZURE_OPENAI_API_VERSION` defaults to `2024-10-21` and can be overridden.

```powershell
& $py examples/capex_flow.py --model azure:YOUR_EXISTING_DEPLOYMENT --auth aad --max-calls 100 --max-output-tokens 6000 --timeout 90
Start-Process ".\sandbox\capex_flow\outputs\dashboard.html"
```

Without endpoint/deployment settings, the program exits with status 2 before making
model calls or replacing existing outputs. Previous reports may still be from the
rules engine; successful new reports explicitly identify their engine and model.

For the old offline example only:

```powershell
& $py examples/capex_flow.py --engine rules --threshold 2000
```

`--threshold` is an illustrative scenario input, **not an approved policy override**
for live MAF agents. Real CAPEX/OPEX recommendations need supplied applicable policies.

## The Agent Flow

The entry point is [capex_flow.py](capex_flow.py); the MAF implementation is
[capex_agents.py](capex_agents.py). Autarch spawns a capability-restricted child for
each specialist. That child authorizes a call through `MAFModelProvider`, which
constructs a named `agent_framework.Agent` and invokes its asynchronous `run` method.
There is no replacement of the model's decisions with the old keyword classifier.

| Specialist | Responsibility and guardrail |
|---|---|
| Document intelligence | Interpret each ABBYY line; preserve IDs and amounts, cite source evidence. |
| Asset classification | Select a supplied regional task/JDE code. Unknown codes are rejected. |
| Coding validation | Compare with approved coding; missing codes are not treated as matches. |
| Capitalization decision | Reason against supplied country/currency policies. No applicable policy means `REVIEW`. |
| Asset relationship | Assess components against prior, actually capitalized records with matching AFE/project/asset identifiers. |
| Exception management | Explain exceptions; mandatory mismatches, gaps and bundle flags cannot be suppressed. |
| Continuous learning | Propose narrowly scoped rules from distinct reviewed invoice cases; never activate rules. |

Execution is source intake, six model stages per invoice, then one learning stage
over eligible reviews. For two invoices this is 13 model calls plus one governed
source read. `--max-calls` limits Autarch actions including that read; each model
request has a timeout and output-token bound, with SDK retries disabled.

Every model action has a signed Autarch why-record. Hash chains and record signatures
are verified before returning a successful package. Each run is retained under
`sandbox/capex_flow/governance/<run-id>/`; report refreshes do not delete this history.
Agents have no posting, payment, human-approval, rule-activation or file-deletion grants.
They receive structured invoice fields, not the raw ABBYY bank-account fields or PDFs.

## Inputs and Finance References

`--data` defaults to `examples/data/capex`. Supported ABBYY ZIPs contain invoice-header
and line-item CSVs matching the supplied exports. Accessible cases workbooks are grouped
by transaction ID and deduplicated against ZIP transactions.

At the last verified customer-data run, both supplied Excel workbooks were Purview/DRM
protected. Obtain authorized readable XLSX exports through your organization's process;
the program does not bypass protection. **Live MAF does not use the hardcoded two-task
fallback.** Missing catalogs remain missing and are visible in the report and review queue.
The workbook reader uses the US task and internal-book sheets; Canadian tasks can be
supplied through the regional JSON reference contract below.

Supply approved policy excerpts, approved coding, regional catalog exports and historical
capitalized records in a JSON file using `--references PATH`. When present,
`examples/data/capex/references.json` is loaded automatically. These are file-based
integration contracts, not live Laserfiche, JDE, POPA or ERP connectors.

The following describes the shape only. Replace placeholders with actual approved data;
it is not a sample company policy or an assertion that any invoice was approved:

```json
{
	"policies": [{
		"id": "POLICY_VERSION_AND_SECTION",
		"country": "US",
		"currency": "USD",
		"text": "Insert the actual approved policy excerpt and its applicability here.",
		"source": "Reporting manual version and section",
		"approved_by": "Accountable policy owner"
	}],
	"tasks": [],
	"asset_classes": [{
		"code": "EXISTING_JDE_CODE",
		"country": "NO",
		"description": "Actual JDE description",
		"source": "JDE export version"
	}],
	"history": [{
		"id": "PRIOR_ASSET_RECORD_ID",
		"invoice_id": "PRIOR_TRANSACTION_ID",
		"invoice_date": "2026-01-01",
		"afe_number": "ACTUAL_AFE",
		"project_number": "ACTUAL_PROJECT",
		"asset_id": "ACTUAL_ASSET_ID",
		"capitalized": true,
		"description": "Description from the prior capitalized record",
		"source": "Fixed asset register export"
	}],
	"business_coding": {
		"ABBYY_TRANSACTION_ID": {
			"afe_number": "ACTUAL_AFE",
			"project_number": "ACTUAL_PROJECT",
			"lines": {
				"1": {"task_code": "APPROVED_TASK_CODE", "capex_opex": "CAPEX"}
			}
		}
	}
}
```

Regional `tasks` entries require `code`, `country`, `description`, and `source`;
they may also carry `asset_class` and `useful_life_months`. Do not duplicate workbook
codes in this list. EU approved coding uses `asset_class_code` instead of `task_code`.
Policy country/currency values are matched before the model sees the policies;
`*` is an explicit all-country/all-currency scope, not an inferred default.
Only historical records with `capitalized: true`, an earlier invoice date and an exact
nonempty AFE/project/asset link qualify as bundle candidates. Vendor similarity alone
does not establish that two invoices belong to the same asset.

## Human Review and Learning

Inspect the report and evidence, then use the full transaction `invoice_id`. Commands
below are alternatives, not a sequence of decisions to execute on one invoice:

```powershell
& $py examples/capex_flow.py review INVOICE_ID approve --reviewer "Finance User" --comment "Decision rationale"
& $py examples/capex_flow.py review INVOICE_ID reject --reviewer "Finance User" --comment "Decision rationale"
& $py examples/capex_flow.py review INVOICE_ID reclassify --reclassified-as opex --reviewer "Finance User" --comment "Decision rationale"
```

The MAF review command binds the decision to the exact case digest and refreshes the
current report without calling a model again. An unresolved `REVIEW` cannot simply be
approved; Finance must explicitly reclassify or reject it. Original recommendations
and exceptions remain visible alongside the Finance decision. Reclassification is
invoice-level; it does not rewrite the original model's line recommendations.

Case snapshots persist under `sandbox/capex_flow/cases/`, and review events append to
`sandbox/capex_flow/reviews.jsonl`. A new analysis may differ even for identical inputs:
changed evidence/model outputs create a different case digest, making the old approval
`STALE_REVIEW`. It cannot approve the new case. Archived reviewed cases remain available
to the learning agent across batches without being treated as posted assets.

The default learning threshold is 50 **distinct reviewed invoices**, not 50 lines or
50 independent reviewers. Duplicate line items/events cannot increase support. Proposals
are grouped by country/currency and treatment and remain `PENDING_RULE_OWNER_APPROVAL`.
There is no automatic activation or model retraining. Approved business-policy updates
must come back through the authorized reference-data process.

## Outputs and Validation

Successful runs regenerate `sandbox/capex_flow/outputs/`:

- `dashboard.html`: engine/model, source gaps, per-line evidence, recommendations and Finance decisions.
- `decision_package.json`: complete specialist outputs, case digests and governance metadata.
- `invoices.csv`, `invoice_lines.csv`: Power BI facts, related by `invoice_id`.
- `review_queue.csv`: generated exceptions, including ones retained after review.
- `candidate_rules.csv`: inactive learning proposals with supporting review IDs.
- `audit_trace.csv`, `signed_audit.jsonl`: model action IDs and signed audit export.

CSV exports are not a published Power BI dashboard. Import them into Power BI Desktop;
use `review_status`/`final_decision` to distinguish unresolved cases from reviewed cases.
Missing-policy recommendations are `REVIEW`, never silently counted as OPEX.

```powershell
& $py -m pytest tests/test_capex_flow.py -q
```

Tests exercise genuine MAF agents with synthetic local chat/HTTP transports, Autarch
denials, budgets/signatures, response validation, and review binding. These do not prove
your Azure credentials or deployment work; that requires a configured live run.

Production prerequisites still include authenticated reviewer identities, protected and
externally anchored audit/review storage, approved policy/reference feeds, security review,
and professional validation of model accuracy. Local reviewer names are attributed, not
authenticated, and local files are not a substitute for enterprise immutable storage.