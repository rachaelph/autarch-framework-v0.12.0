# Token-Efficient Agentic Engineering

A production-oriented reference application showing how an agent runtime can keep
context proportional to the task instead of proportional to the entire tool and
knowledge ecosystem.

## What is different

- **Skills are routed, not broadcast.** Only relevant specialist instructions and
  tool contracts enter a prompt.
- **Context has a hard assembly ceiling.** Evidence is ranked, packed, and cited;
  oversized or irrelevant records are excluded.
- **Agents are provisioned on demand.** Autarch child agents receive only the
  capability grants needed by their selected skill.
- **Model calls are admission-controlled.** A thread-safe ledger reserves prompt
  and completion allowances before concurrent work starts.
- **Quality is measured with efficiency.** The result reports citation validity,
  required-section coverage, latency, estimated tokens, and cost.
- **The baseline is executable.** Compare mode runs a deliberately conventional
  fixed-agent/full-context pipeline against the routed pipeline.

## Run

From the repository root:

    python -m examples.token_efficient_agentic_engineering

Use a custom intent and budget:

    python -m examples.token_efficient_agentic_engineering --intent "Add JWT authentication and tests" --budget 12000

Use a configured model provider instead of the deterministic offline provider:

    python -m examples.token_efficient_agentic_engineering --provider ollama:llama3

The default is deterministic and requires no network, keys, or third-party
packages. Output artifacts are written under `sandbox/token-efficient-demo`.

## Architecture

1. `SkillRegistry` ranks modular capability manifests against the intent.
2. `ContextAssembler` ranks evidence and packs it into a strict token envelope.
3. A master Autarch agent dynamically spawns attenuated specialist agents.
4. Specialists run concurrently through `BudgetedProvider`.
5. A synthesis call receives only compact specialist handoffs and selected evidence.
6. Deterministic checks validate citations and required report sections.
7. `ComparisonReport` quantifies savings relative to the full-context baseline.

## Production notes

The token estimator is intentionally conservative and tokenizer-independent. API
usage returned by a provider remains the source of truth for billing. Because the
common provider interface does not expose a portable output-token parameter, the
ledger provides strict **pre-call admission control**, not a claim that every
vendor can be stopped mid-generation. Configure provider-native output limits in
production as an additional guard.

The example contains no financial or customer data and does not send repository
content anywhere unless a non-default network provider is explicitly selected.
