# Circle K — CapEx / CIP Invoice Governance

Governed, audit-ready **capital-project invoice determination** built on Autarch + the Microsoft
Agent Framework. Point it at a capital-project invoice PDF and it produces a per-line
**CapEx/OpEx + task code + use-tax** determination *before* payment — with a signed audit trail,
deterministic reproducibility, and low-confidence items routed to a human.

This is the "shift-left" answer to the CIP-validation pain: instead of sampling 20K–40K invoices/
month and finding errors months later in audit, **every** invoice is evaluated up front, exceptions
are flagged with evidence, and only the genuinely ambiguous cases reach a reviewer.

- Tool: [extract_invoice.py](extract_invoice.py)
- Supporting modules: [refdata.py](refdata.py) · [docintel.py](docintel.py) · [decision_cache.py](decision_cache.py)
- Governed reference data: [reference/](reference/)

---

## The flow it implements

It follows the *CapEx exception review* process end to end (the numbers match the process diagram):

| # | Step | What happens | Source |
|---|------|--------------|--------|
| 1 | Invoice intake | Governed, read-only ingest of the PDF (provably cannot write/delete/network; signed why-record) | the PDF |
| 2 | Extraction | Azure **Document Intelligence** `prebuilt-invoice` pulls header + line items with confidence; scanned PDFs fall back to vision-OCR | `--doci` |
| 3 | PO / AFE lookup | Match the invoice to the committed PO (PO#/invoice#/vendor aliases) and flag discrepancies | `seed-po-records.json` |
| 4 | Task-code matching | Map each line to the best task code (semantic + LLM) | `seed-task-codes.json` |
| 5 | Capitalization rules | CapEx vs OpEx from the task master **plus $2,000 / $100,000 thresholds**; period costs (freight, fuel surcharge, travel) are expensed | logic in code |
| 6 | Tax matrix lookup | `state × item type → Taxable / Exempt / Ambiguous` | `seed-taxability-matrix.json` |
| 7 | Tax engine calc | Effective rate = **state rate + local (county/city)** × taxable base | `seed-taxability-matrix.json` |
| 8 | LLM tax assessment | An **independent** model verdict on each line's taxability | the model |
| 9 | Dual validation | Compare the tax engine vs the LLM — **agree → may auto-post; diverge/ambiguous → analyst review** | logic in code |
| 10 | Routing decision | Confidence tiers: **≥ 0.85 auto-approve · 0.70–0.85 auto-post + 48h review flag · < 0.70 review** | logic in code |

Plus: grounding/anti-hallucination checks, quality + safety judge panels, source citations, precedent
lookup, multi-jurisdiction detection, token-usage/cost, and a full evidence package.

**Deterministic by design:** the same invoice yields the **same determination every run** (a decision
cache keyed on vendor + ship-to state + line description), so results are auditable and reproducible.

---

## Prerequisites

1. **Python 3.10+** and the framework installed from the repo root:
   ```powershell
   pip install -e .
   ```
2. **Reasoning + extraction dependencies:**
   ```powershell
   pip install agent-framework-core agent-framework-openai azure-identity azure-ai-documentintelligence pymupdf pypdf
   ```
   > Install `agent-framework-core` + `agent-framework-openai` only — do **not** `pip install agent-framework` (the `[all]` extra fails to resolve).
3. **Azure CLI** signed in to the tenant that owns the resources:
   ```powershell
   az login --tenant 3e41b164-59e6-4ce9-8c15-767e2c81431c
   ```

### Azure resources used

| Purpose | Resource | Endpoint | Deployment / model |
|---|---|---|---|
| Reasoning (chat) | Azure OpenAI `aif-learning` | `https://aif-learning.cognitiveservices.azure.com/` | `gpt-4.1-rp` |
| Semantic mapping (embeddings) | same resource | same endpoint | `text-embedding-3-small` |
| Invoice extraction | Document Intelligence `circlekdoci` | `https://circlekdoci.cognitiveservices.azure.com/` | `prebuilt-invoice` |

Auth is **Microsoft Entra ID (AAD)** — no API keys. The signed-in identity needs the data-plane roles
`Cognitive Services OpenAI User` (on the OpenAI resource) and `Cognitive Services User` (on the
Document Intelligence resource). Because these resources live in a tenant where the runner may be a
guest, the credential is **tenant-pinned** via `AZURE_OPENAI_TENANT_ID`.

---

## Run it

```powershell
# 1) Warm the Entra ID token first (avoids a cold-start CLI timeout that would drop to offline mode)
az account get-access-token --tenant 3e41b164-59e6-4ce9-8c15-767e2c81431c --resource https://cognitiveservices.azure.com | Out-Null

# 2) Configure the environment (Entra ID auth, key auth is disabled on the resource)
$env:AZURE_OPENAI_ENDPOINT    = "https://aif-learning.cognitiveservices.azure.com/"
$env:AZURE_OPENAI_DEPLOYMENT  = "gpt-4.1-rp"
$env:AZURE_OPENAI_API_VERSION = "2024-12-01-preview"
$env:AZURE_OPENAI_TENANT_ID   = "3e41b164-59e6-4ce9-8c15-767e2c81431c"
Remove-Item Env:\AZURE_OPENAI_API_KEY -ErrorAction SilentlyContinue

# 3) Run the full pipeline on an invoice
python examples/extract_invoice.py "C:\path\to\invoice.pdf" `
    --model azure:gpt-4.1-rp --auth aad `
    --doci "https://circlekdoci.cognitiveservices.azure.com/" `
    --embed azure:text-embedding-3-small `
    --html out\invoice_report.html --csv out\invoice_lines.csv
```

### Try it with zero setup (offline)

```powershell
python examples/extract_invoice.py --demo
```
Runs the **same** governed pipeline on a bundled sample invoice with a deterministic offline model —
no Azure, no keys — so you can see the exact output shape.

---

## CLI flags

| Flag | Meaning |
|---|---|
| `<invoice.pdf>` | Path to the invoice (PDF; scanned/image-only supported via vision-OCR) |
| `--model azure:<deployment>` | Reasoning deployment (e.g. `azure:gpt-4.1-rp`) |
| `--auth aad\|key\|auto` | Credential mode; use `aad` (key auth is disabled) |
| `--doci [endpoint]` | Extract with Document Intelligence `prebuilt-invoice` (bare uses `AZURE_DOCINTEL_ENDPOINT`) |
| `--embed [spec]` | Semantic cross-check; use `azure:text-embedding-3-small` (learned) |
| `--threshold 0.85` | Auto-post confidence threshold |
| `--cache [path]` / `--no-cache` | Deterministic decision cache (on by default at `examples/decision_cache.json`) |
| `--html [path]` / `--csv [path]` | Write the HTML report / per-line CSV |
| `--json` | Emit the full result as JSON |
| `--demo` | Offline deterministic run on the bundled sample |

---

## Outputs

- **Console report** — header, per-line CapEx/OpEx + task + tax + dual-validation + route, invoice
  rollup (CapEx/OpEx totals, capitalization decision, tax reconciliation), evaluation panels,
  citations, cost, and the final DECISION.
- **HTML report** (`--html`) — a self-contained, shareable report with a colored decision banner.
- **CSV** (`--csv`) — one row per line for downstream posting / SME triage, plus a rollup row.
- **Decision cache** (`decision_cache.json`) — persisted classifications for reproducibility.

---

## The governed reference data (customize for production)

Everything rules-based lives in [reference/](reference/) — **illustrative seed data, not tax advice**.
In production these come from Circle K's tax-dept taxability matrix, Avalara (live rates), and the ERP
task/PO masters. All four files are read **once, under an agent granted only `file.read`** and proven
unable to write or delete — every read is signed.

| File | Drives | Shape |
|---|---|---|
| [seed-taxability-matrix.json](reference/seed-taxability-matrix.json) | steps 6–7 | `matrix[state][item_type] → T/E/A`, `tax_rates[state]`, `local_rates[state]` |
| [seed-task-codes.json](reference/seed-task-codes.json) | steps 4–5 | task codes with `cap_eligible`, `asset_class`, `useful_life_months`, `depreciation` |
| [seed-po-records.json](reference/seed-po-records.json) | step 3 | PO master with `alt_po_numbers` + `vendor_aliases` for fuzzy matching |
| [seed-history.json](reference/seed-history.json) | precedent | past routing decisions for confidence calibration |

**To make it your own:** replace these JSON files with your real matrix/rates/task-codes/PO master
(same shape), then delete `examples/decision_cache.json` once so decisions are re-derived. No code
change is required — the taxability item types, task codes, and rates all come from the data.

To onboard a new equipment category, add it to `item_types` + each state row in the taxability matrix,
add a matching keyword hint in [refdata.py](refdata.py) `_ITEM_TYPE_HINTS`, and add its task code(s)
to `seed-task-codes.json`.

---

## What makes it audit-ready

- **Governed read** — the agent can only read; it is provably unable to write, delete, or reach the
  network, and each read is signed into a tamper-evident ledger.
- **Rules-first** — tax and capitalization come from governed reference data, not the model's free pick.
- **Dual validation** — the model and the tax engine must agree; genuine disagreements route to a
  named analyst instead of silently posting.
- **Deterministic** — same invoice → same determination, every run.
- **Evidence** — per-line rationale, source citations, judge panels, and cost, produced automatically.

---

## Troubleshooting

- **Drops to "offline deterministic provider"** — the Azure CLI token cold-started; run the
  `az account get-access-token …` warm-up line first, then re-run.
- **401 "principal does not have access"** — the token was minted for the wrong tenant; ensure
  `AZURE_OPENAI_TENANT_ID` is set to `3e41b164-59e6-4ce9-8c15-767e2c81431c` and you ran `az login`
  against that tenant.
- **`--doci` falls back to OCR+LLM** — Document Intelligence RBAC can be eventually-consistent for a
  few minutes after assignment; retry. Confirm the `Cognitive Services User` role on `circlekdoci`.
- **Everything shows $0 tax** — the item type has no taxable cell for that state; check the taxability
  matrix for that `state × item_type`.
