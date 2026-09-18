# Autarch

**You don't use AI. You preside over it.**

A council of minds deliberates. You rule. Nothing acts without your word. Everything it does, it can prove.

Autarch is an **AI-native operating layer**. Its core is not a hardware kernel — it is a **Capability Kernel**: a deterministic gate that governs every action an intelligence is allowed to take. Intelligence is unlimited and swappable; **consequences are governed**.

See [docs/MANIFESTO.md](docs/MANIFESTO.md) for the vision and [docs/PLAN.md](docs/PLAN.md) for the build plan.

---

## The one rule

> **AI is the orchestrator, never the kernel. The model proposes; the deterministic kernel disposes.**

## Install

```bash
pip install -e .
```

Zero runtime dependencies — pure Python standard library. Works fully offline with the built-in deterministic mock model (no API keys needed).

## Quickstart — build a governed agent in a few lines

```python
from autarch import Agent, capability

agent = Agent(
    intent="create a file called notes.txt that says Hello Autarch",
    council=["mock"],                      # or ["ollama:llama3"] for a real local model
    grants=[
        capability("file.write", scope={"path_prefix": "."}),
        capability("file.read"),
        # no file.delete grant -> the agent literally cannot delete. Provable.
    ],
    workspace="./sandbox",
)

result = agent.run()
print("executed:", result.executed, "| why:", result.why_id)
```

Run it:

```bash
python examples/quickstart.py
```

## Build governed agents for any domain

The production agent factory compiles declarative, versioned `AgentBlueprint`
specifications from an industry `DomainPack`. Creation is deny-by-default and
passes through structural validation, static guarantees, content-bound quorum
approval, optional RBAC filtering, and immutable fingerprints before an agent
can run. The factory creates configuration—not arbitrary executable code.

```python
from autarch import AgentBlueprint, AgentFactory, DomainPack, FileSystemAdapter, Invariant, capability

blueprint = AgentBlueprint(
  name="report-writer", version="1.0.0",
  grants=[capability("file.write", scope={"path_prefix": "reports"})],
  adapters=["filesystem"],
  invariants=[Invariant.forbid("file.delete")],
  requires_approval=False,  # use quorum approval in production
)
pack = DomainPack(
  name="reporting", version="1.0.0",
  blueprints={blueprint.name: blueprint},
  adapter_catalog={"filesystem": lambda root: FileSystemAdapter(root)},
)
factory = AgentFactory()
factory.register(pack, workspace="./sandbox")
deployment = factory.create(
  "reporting", "report-writer", "create reports/status.txt that says ready",
  workspace="./sandbox",
)
result = deployment.run()
```

See [docs/AGENT_FACTORY.md](docs/AGENT_FACTORY.md) for approval, lifecycle, RBAC,
domain-pack, and production deployment guidance.

## Already know the action? `enact()` it — governed, no council

When the AI should *decide* what to do, use `run()` and the council deliberates.
When you already know the action (a workflow step, a planner's decision, a
directly invoked tool), use `enact()` — it skips deliberation but still runs the
**full deterministic pipeline** (kernel + policy + budget) and signs the outcome
into the same tamper-evident ledger.

```python
# Govern + execute + sign a known action. The kernel can still refuse it.
read = agent.enact("doc.read", {"path": "report.pdf"})
print(read.executed, read.why_id)        # signed, provable — no ceremony
```

Calling `enact` *is* the act of presiding — but a missing grant, a `deny` policy,
or an exhausted budget still blocks it. *AI proposes, the kernel disposes — even
when you are the one proposing.* See `examples/extract.py` (governed PDF read →
LLM extraction → deterministic validation).

## Flagship application — Circle K CapEx / CIP invoice governance

A production-shaped use case built on the kernel: read a capital-project **invoice PDF** and produce a
governed, audit-ready **per-line CapEx/OpEx + task-code + use-tax determination** *before* payment.
It runs the full CapEx-exception-review flow end to end — Document Intelligence extraction → PO/AFE
match → task-code + capitalization ($2k/$100k) → tax matrix + local rates → **an independent LLM tax
assessment dual-validated against the rules engine** → confidence-tiered routing — with a signed audit
trail and **deterministic, reproducible** results (same invoice → same determination every run).

```powershell
python examples/extract_invoice.py "C:\path\to\invoice.pdf" `
    --model azure:gpt-4.1-rp --auth aad `
    --doci "https://circlekdoci.cognitiveservices.azure.com/" `
    --embed azure:text-embedding-3-small --html out.html --csv out.csv

# or fully offline, zero setup:
python examples/extract_invoice.py --demo
```

**Full setup + run guide: [examples/README-circle-k.md](examples/README-circle-k.md).**
The tax matrix, task codes, PO master, and rates are governed reference data you swap for your own —
no code change. Every reference read is signed; low-confidence or disputed lines route to a human.

## Use the CLI — preside over the council

```bash
# Propose -> deliberate -> you [r]atify / [o]verrule / [s]end back -> execute
autarch do "create a file called hello.txt that says hi"

# Convene a council of several voices and watch them (dis)agree
autarch do "delete the file hello.txt" \
    --council mock:bold --council mock:cautious --grant file.delete

# Ask the OS to justify any past action
autarch why <why_id>

# See recent rulings (a '!' marks disagreement; '↶' marks a rewind)
autarch history

# Show a VERIFIABLE accountability receipt for an action…
autarch prove <why_id>
# …and verify the integrity of the entire tamper-evident ledger
autarch prove --chain

# Show this workspace's cryptographic node identity (Ed25519, key-bound id)
autarch identity

# PROVE a safety invariant before running (exits non-zero on failure — CI-friendly)
autarch guarantee --forbid file.delete --confine "file.write=reports"

# Run under a budget — refuse actions that would bust a ceiling
autarch do "create report.txt that says hi" --budget-calls 5 --budget-risk 3

# Governed, audited undo — reverse past actions (each reversal is itself recorded)
autarch rewind --last 1
autarch rewind --since "1 hour" --keep-id <why_id>   # undo the last hour, keep one thing

# See the host you're running on (the portable substrate)
autarch substrate

# Mesh: one identity, one policy, many devices (local-first encrypted sync)
autarch --workspace ./laptop mesh init --realm home --deny file.delete
autarch --workspace ./phone  mesh init --realm home --join <REALM_KEY>
autarch --workspace ./laptop mesh export laptop.bundle   # offline: encrypted file
autarch --workspace ./phone  mesh import laptop.bundle   # merges memory + shared policy

# Or sync directly over the network (stdlib HTTP, no broker)
autarch --workspace ./laptop mesh serve --port 8787          # serve this node's ledger
autarch --workspace ./phone  mesh sync http://laptop:8787    # pull + push over HTTP

# Gossip across many nodes — records converge epidemically
autarch --workspace ./phone mesh peer add http://laptop:8787
autarch --workspace ./phone mesh gossip                      # sync with all known peers
```

(If not installed, use `python -m autarch do "..."` from the repo root.)

The council learns your judgments: **overrule an action once and the precedent is
remembered and applied automatically next time.** Declarative **policy-as-code**
can deny or escalate even granted actions (e.g. large writes require explicit
ratification). Every action is sealed into a **tamper-evident hash chain** — alter
any past record and `prove --chain` will catch it.

## Absorb an existing tool (LangChain / MCP) — governed, without changing its code

```python
from autarch import Agent, capability, from_langchain_tools

# Your existing LangChain tools, now running INSIDE the kernel:
adapter = from_langchain_tools([my_search_tool])   # becomes capability `tool.<name>`

agent = Agent(
    intent="search for the latest filing",
    grants=[capability("tool.web_search")],   # ungranted tools are denied by default
    adapters=[adapter],
)
```

The tool gains capability-gating, deliberation, and a full audit trail it never
had. See `examples/tools.py`. This is the absorb-then-replace move: existing
frameworks become *features* that run under Autarch's governance.

## Absorb the ecosystem: MCP + LangChain, both directions

Instead of rebuilding hundreds of integrations, Autarch **inherits** the MCP and
LangChain ecosystems *under governance* — no hard dependency on either.

- **Autarch as a governed MCP server** — expose capabilities to any MCP client
  (an IDE, Claude Desktop, another agent); every `tools/call` passes the capability
  kernel first, so a client that never had governance suddenly does.
- **Govern external MCP tools** — `from_mcp_server(command)` wraps another server's
  tools as governed capabilities.
- **LangChain bridge** — `govern_langchain_tools(tools)` runs their tools inside the
  kernel; `as_langchain_tool(...)` exposes a Autarch-governed capability as a
  LangChain tool that still enforces the kernel.

```python
from autarch import MCPServer, capability, from_callables
from autarch.kernel import CapabilityKernel

adapter = from_callables({"search": do_search, "wipe_db": drop_everything})
kernel  = CapabilityKernel([capability("tool.search")])     # wipe_db ungranted
server  = MCPServer([adapter], kernel)
# An MCP client calling tool.wipe_db is refused: "denied by governance".
```

See `examples/mcp.py` and `examples/langchain.py`. *Their tools gain governance;
our governed tools drop into their agents.*

## Safe multi-agent: delegate strictly weaker authority

A parent agent can spawn a sub-agent, but only hand it a *subset* of its own
authority. Attenuation may narrow the capability name, the scope, and any limits —
never widen them — and the kernel enforces it structurally. A child can never
out-reach its parent, and nested delegation only ever shrinks authority.

```python
orchestrator = Agent(
    intent="coordinate a team",
    grants=[capability("file.write", scope={"path_prefix": "."})],   # whole workspace
)

# The worker is confined to the 'reports' subdirectory — and nothing else.
worker = orchestrator.spawn(
    intent="write the quarterly report",
    grants=[capability("file.write", scope={"path_prefix": "reports"})],
)
# worker writing outside 'reports' -> structurally DENIED by the kernel.
# worker asking for file.delete (never delegated) -> dropped at spawn.
```

See `examples/delegation.py`.

## Governed orchestration — a master that spawns safe children on the fly

The supervisor/worker pattern everyone builds — a master decomposes a request,
provisions specialist children, runs them, and synthesizes one answer — but here
**every child is governed**. Because children are spawned from the master, each is
capability-attenuated (can never out-reach the master), **tool-isolated** (only the
adapters its subtask needs), budget-bounded (one shared pool), and signed into the
same audit ledger. The whole fleet can be **proven safe before it runs**.

```python
from autarch import Agent, Orchestrator, Invariant, capability

master = Agent(
    intent="coordinate the report workflow",
    grants=[capability("file.write"), capability("file.read")],   # NOT delete
)

result = Orchestrator(
    master,
    max_parallel=3,                                   # run independent children concurrently
    guarantees=[Invariant.forbid("file.delete")],     # proven before any child spawns
).run("create report.txt that says numbers look strong then read it then delete it")

print(result.synthesis)          # one unified answer
print(result.executed_count)     # the delete child was refused by governance, not trust
```

A model can plan the decomposition (`ModelPlanner`) and write the final answer
(`ModelSynthesizer`) — both **fail closed** to deterministic fallbacks if the model
is unreachable or returns junk. Reusable `Specialist` templates (researcher, writer,
security-reviewer) provision consistent workers. Independent children run in
parallel, each on its own signed sub-chain that merges into one verifiable ledger.
See `examples/orchestration.py` (offline) and `examples/orchestration_live.py`
(live Ollama).

## Governed long-term memory — recall that can't be silently poisoned

Naive agent memory is a vector store bolted onto the model: it retrieves stale
facts with false confidence, drowns relevance in mere similarity, and — worst — a
*poisoned* memory silently taints every future session. Autarch treats long-term
memory as a **governed substrate** with the same guarantees as the action ledger.

```python
from autarch import Agent, capability

agent = Agent(intent="remember and recall", grants=[capability("memory.write")])

agent.remember("The staging URL is https://old.example.com", subject="staging.url")
# A belief changed? Supersede it — the old one is retired, not blended.
mem = agent._ensure_recall()
old = agent.recall("staging url", subject="staging.url")[0]
mem.supersede(old.id, "The staging URL is https://new.example.com")

# Hybrid, trust-gated, budget-bounded recall (relevance over similarity):
hits = agent.recall("where do I deploy", k=3, token_budget=200, min_trust=1)
```

Every memory is **Ed25519-signed** and hash-chained, so a forged or tampered
memory fails `verify_provenance` and `min_trust=1` **quarantines** it. Memories
**decay** and are **reinforced** by use (graceful forgetting), are **superseded**
on update (belief revision, not blending), and **consolidate** losslessly (the
originals are kept). Recall is **hybrid** — lexical + optional semantic embedding +
recency + structural filters — and fits a **token budget**, so context can neither
blow up nor be dominated by keyword noise. Semantic search is an *optional* seam
(`HashingEmbedder` offline, `OllamaEmbedder` live); nothing is required. See
`examples/memory.py`.

## Faithful summarization — no invented facts, no dropped detail, no faked progress

GenAI agents condense text well and lie about it badly: they flatten away a
figure, invent a claim, or hand you a polished “all done!” that masks skipped
work. Autarch treats a summary like any other action — it is **evaluated**, and
the verdict is **signed into the tamper-evident ledger**, so faithfulness is
*provable*, not promised. (ROUGE/BLEU only measure word overlap and miss invented
facts; these are claim-level checks instead.)

```python
from autarch import Agent, GroundednessEvaluator, CoverageEvaluator, capability, from_callables

source = "Q3 revenue was $50,000. The deadline is March 15. Acme Corp signed."
tool   = from_callables({"summarize": lambda source: "Q3 revenue was $50,000."})
agent  = Agent("summarize", grants=[capability("tool.summarize")], adapters=[tool])

# The faithfulness verdict is scored on the ACTUAL output and signed into the ledger:
result = agent.enact("tool.summarize", {"source": source},
                     evaluate=GroundednessEvaluator(source=source))
print(result.verdict.passed)                    # False if it invents a figure/name
```

- **`GroundednessEvaluator`** (precision) flags any claim not supported by the
  source — catching invented numbers (`$50k → $500k`) and invented entities.
- **`CoverageEvaluator`** (recall) detects *detail loss* — a dropped figure,
  party, or deadline — and names exactly what went missing.
- **`extractive_summary` / `compress_history`** compress context by *selecting*
  verbatim sentences (grounded by construction — cannot hallucinate) while
  preserving numbers, entities, and structure — a safe replacement for the naive
  “dump old turns into a prompt” that makes long-running agents forget.
- **Illusions of progress** are structurally impossible: the ledger records what
  *actually executed*, so a summary can’t claim work the kernel never authorized.

All deterministic and offline (compose an LLM `RubricJudge` via `ConsensusEvaluator`
for semantic paraphrase). See `examples/faithfulness.py`.

## Prove safety before running (formal guarantees)

Because the kernel and policy engine are deterministic, safety properties can be
*proven* statically — not merely tested — and the proof holds no matter what the
model proposes.

```python
from autarch import Agent, capability, Invariant

agent = Agent(
    intent="summarize the data",
    grants=[capability("file.read"), capability("file.write", scope={"path_prefix": "."})],
)

report = agent.guarantee([
    Invariant.forbid("file.delete"),                 # can never delete
    Invariant.require_approval("payment.send"),       # never auto-pays (two-person rule)
    Invariant.confine("file.write", "."),             # writes stay in the sandbox
])
assert report.all_hold        # proven before a single action runs
```

`autarch guarantee --forbid payment.send` exits non-zero when a property can't
be proven, so an unsafe configuration **fails your build**. Delegation preserves
guarantees: anything proven for a parent holds for every sub-agent it spawns.
See `examples/guarantees.py`.

*Soundness boundary (honest):* this is a sound static proof over grants, scopes,
and *unconditional* policy effects — not full theorem-proving. Policies with a
runtime predicate are treated conservatively (never relied on for a guarantee), so
a "GUARANTEED" result is always sound.

## One identity, one policy, many devices (the mesh)

Your laptop, phone, and hub are nodes of one **realm**. They share an identity (a
realm key) and a converging policy set, and sync memory **local-first** — no
mandatory central server. A node exports an **encrypted, authenticated** bundle of
its ledger; another node imports and merges it (an idempotent union). Each node's
records form an independently-verifiable hash sub-chain, so a merged ledger stays
tamper-evident.

```python
from autarch import Realm, export_bundle, import_bundle
from autarch.memory import WhyMemory

laptop = Realm.create("home")                 # forms the realm, holds the key
phone  = Realm.join("home", laptop.key_hex)   # joins with the shared key

blob = export_bundle(WhyMemory("laptop.db", node_id=laptop.node_id), laptop)
import_bundle(WhyMemory("phone.db", node_id=phone.node_id), phone, blob)
# phone can now explain actions taken on the laptop; shared policies converge.
```

See `examples/mesh.py`. *Crypto:* bundles use **AES-256-GCM** (a vetted AEAD)
when the optional `cryptography` package is installed (`pip install autarch[crypto]`),
and transparently fall back to a stdlib-only encrypt-then-MAC construction when it
isn't — so the package still installs with zero dependencies. The on-wire format is
self-describing, so the cipher can be upgraded without breaking existing bundles.

Nodes can exchange ledgers as **encrypted files** *or* sync **directly over the
network** with a self-contained stdlib HTTP server (`autarch mesh serve` /
`autarch mesh sync <url>`) — no broker, no dependency. The transport is just a
pipe: every bundle is AES-GCM encrypted and authenticated end-to-end, and the
server binds to loopback by default. See `examples/network.py`.

Across many nodes, **gossip** (`mesh peer add` + `mesh gossip`) converges the mesh
*epidemically*: because the ledger is a grow-only CRDT, a record on A reaches C
through B without A and C ever talking directly. See `examples/gossip.py`.

## What's new in v0.10 — the governance upgrade

Six capabilities layered on top of the kernel. All zero-dependency; the full test
suite (459 tests) stays green. See `examples/governance_upgrade.py` and
[CHANGELOG.md](CHANGELOG.md).

- **General scope algebra** — capabilities are no longer confined to `path_prefix`
  and `max_bytes`. Grants now carry typed, composable constraints: host/port
  allowlists (network egress), spend ceilings (`amount_max`), enums, regex shape,
  forbidden substrings (block `DROP TABLE`), and data-class guards (block PHI/PII).
  All checked by the same deterministic kernel; attenuation narrows them by subset.
  ```python
  capability("net.fetch", scope={"host_allowlist": ["api.github.com"]})
  capability("payment.send", limits={"amount_max": 100})
  capability("db.query", scope={"forbid_substrings": {"sql": ["DROP", "DELETE"]}})
  ```
- **Real multi-model council** — `OpenAIProvider` and `AnthropicProvider` (stdlib
  `urllib`, no new deps) join the mock and Ollama voices, with a real per-token
  **price book** so budgets meter actual spend. `council=["gpt-4o", "claude-3-5-sonnet-latest", "ollama:llama3"]`.
- **Deliberative debate** — voices now *respond to each other*: after the first
  critique, councilors are re-polled with the others' arguments in view and may
  revise. The exchange is recorded in `Deliberation.transcript`. `Agent(..., debate_rounds=2)`.
- **Governance gateway** — `GovernanceGateway` runs the full kernel/policy/budget
  pipeline behind a stdlib HTTP endpoint so *any* agent, in any language, routes
  its actions through one governed control plane. Loopback by default.
- **Async approval plane** — `ApprovalQueue` decouples proposing from ratifying:
  a human, or a **quorum** of humans, ratifies or overrules out of band, from any
  process or device. Durable, TTL-aware, attributed.
- **Compliance evidence** — `ComplianceReporter` maps the signed ledger to
  auditor-ready control reports (SOC 2, EU AI Act, HIPAA) and a portable,
  self-verifying evidence bundle.
- **Declarative policy DSL + kernel proof** — policy-as-data you can `simulate`
  and `diff` before shipping (`autarch.policydsl`), and `verify_kernel()`, which
  checks the kernel's four safety invariants by exhaustion over thousands of cases
  (formal model sketched in `docs/kernel.tla`).
- **Semantic recall out of the box** (v0.10.1) — `OpenAIEmbedder` gives recall
  memory real meaning-aware search with just an API key, no local model. Recall
  finds a "refund policy" note from a "how do I get my money back" query that
  shares no words with it. `Agent(..., embedder="openai")`, or `build_embedder`.
- **Governed data access** (v0.11.0–v0.12.0) — connect the agent to your data,
  governed and provable. `SQLAdapter` speaks DB-API 2.0 with one-call connectors
  for **Postgres** (`connect_postgres`), **SQL Server** (`connect_sqlserver`),
  **Oracle** (`connect_oracle`), and **MySQL** (`connect_mysql`): read-only by
  default, injection-safe, table-scoped, PII-redacting, schema-introspecting,
  audited. **AI search** via `AzureAISearchAdapter`, `ElasticsearchAdapter`, and a
  `RestSearchAdapter` base (Pinecone/Weaviate/…). And `ExtractionAdapter` gets data
  out of **structured** docs (CSV/JSON) *and* **unstructured** ones (PDF/txt/HTML) —
  including smart, schema-guided field extraction from messy prose via any model.
  ```python
  db = connect_postgres("postgresql://ro_user@host/db",
                        read_only=True, redact_columns=["ssn"], allow_tables=["orders"])
  idx = AzureAISearchAdapter(endpoint, index, api_key, embedder="openai")
  ext = ExtractionAdapter(root="./docs", model="claude-3-5-sonnet-latest")
  agent = Agent(intent="investigate the claim",
                grants=[capability("db.query"), capability("search.query"),
                        capability("doc.extract")],
                adapters=[db, idx, ext])   # every query scoped, audited, provable
  ```

## What you get for free (the platform, not the prompt)

Every action an agent takes is automatically:

- **Governed** — checked against typed capability grants (deny by default).
- **Deliberated** — a council of voices proposes and critiques; disagreement is surfaced, not hidden.
- **Policed** — declarative policies (local or realm-wide) can deny or escalate even granted actions.
- **Remembered** — your rulings become precedent the council applies next time.
- **Audited** — recorded in a tamper-evident hash chain you can `prove`.
- **Signed** — every action is cryptographically signed (Ed25519); authorship is attributable and unforgeable.
- **Access-controlled** — RBAC decides *who* may wield which capability; keys are encrypted at rest.
- **Delegable** — a sub-agent can be handed strictly *weaker* authority; it can never out-reach its parent.
- **Orchestrated** — a master decomposes a task and spawns governed children (attenuated, tool-isolated, budget-bounded, signed); the whole fleet is provable before it runs.
- **Remembering** — governed long-term memory: signed, hash-chained recall that decays, is reinforced, supersedes stale beliefs, and quarantines anything whose provenance doesn't verify.
- **Provable** — safety invariants ("can never delete", "always needs approval") are *proven* statically before running.
- **Budgeted** — every action carries a cost; the economic kernel refuses what would bust a budget, even if it's allowed.
- **Durable** — runs journal each step; a crashed run resumes without ever repeating a side effect.
- **Observable** — every run emits a typed event stream (`run.start → … → run.complete`) to a pluggable sink.
- **Compliant** — audit export + right-to-be-forgotten redaction that *keeps* the integrity proof.
- **Evaluated** — built-in judge LLMs + deterministic checks; every verdict is signed and *provable*.
- **Faithful** — summaries are checked for invented facts (groundedness) and dropped detail (coverage); the verdict is signed into the ledger.
- **Resilient** — retry, a token-aware queue that *never* trips a rate limit, and a circuit breaker — built in, zero dev code.
- **Operable** — `health` readiness probe, `Dockerfile`, and CI included; self-contained, runs anywhere.
- **Reversible** — `rewind` undoes past actions, itself under governance.
- **Synced** — memory and policy converge across your devices, local-first and encrypted.
- **Explainable** — ask `why` and get the real reason, not a guess.

## Architecture (the chamber)

| Pillar | Role | Module |
|---|---|---|
| Capability Kernel | the floor — nothing acts without a ratified grant | `autarch/kernel.py` |
| RBAC | governance of *who* — roles decide grantable capabilities | `autarch/rbac.py` |
| Delegation | attenuation — sub-agents get strictly weaker authority | `autarch/delegation.py` |
| Orchestration | governed master-child: decompose → provision → execute → synthesize | `autarch/orchestration.py` |
| Recall Memory | governed long-term memory: signed, decaying, self-revising, trust-gated | `autarch/recall.py` |
| Embeddings | optional semantic seam for recall (offline hashing / live Ollama) | `autarch/intelligence/embedding.py` |
| Guarantees | sound static proofs of safety invariants before running | `autarch/guarantees.py` |
| Economic Kernel | budgets every action; refuses what it can't afford | `autarch/economy.py` |
| Evaluation | governed judge-LLMs + deterministic checks; signed verdicts | `autarch/evaluation.py` |
| Faithfulness | groundedness + coverage checks + grounded-by-construction compression | `autarch/evaluation.py` |
| Resilience | retry + token-aware rate limiting + circuit breaker, auto-applied | `autarch/resilience.py` |
| Run Journal | durable, resumable execution (no double side effects) | `autarch/runlog.py` |
| Events | typed observability stream to a pluggable sink | `autarch/events.py` |
| Telemetry | JSON-lines / OpenTelemetry export of the event stream | `autarch/telemetry.py` |
| Compliance | audit export + RTBF redaction (integrity preserved) | `autarch/memory.py` |
| Health | readiness/liveness report for container probes | `autarch/health.py` |
| Errors | stable, catchable error taxonomy with codes | `autarch/errors.py` |
| Intelligence Bus | the seats — any model joins as a voice | `autarch/intelligence/` |
| Council | multi-voice deliberation + moderator (voices polled in parallel) | `autarch/council/` |
| Policy | declarative rules: deny / require-ratify / allow | `autarch/policy.py` |
| Precedent | the council remembers and applies your rulings | `autarch/precedent.py` |
| Adapters | typed actions on the world (files, tools, LangChain/MCP) | `autarch/adapters/` |
| MCP | govern external MCP tools; serve ours as a governed MCP server | `autarch/mcp.py` |
| LangChain bridge | run their tools governed; expose ours to their agents | `autarch/langchain_bridge.py` |
| Why-Memory | the record — a tamper-evident, verifiable hash chain | `autarch/memory.py` |
| Provenance | Ed25519 signing — attributable authorship; keys encrypted at rest | `autarch/provenance.py` |
| Rewind | governed, audited reversal of past actions | `autarch/rewind.py` |
| Mesh | one identity, encrypted local-first sync across nodes | `autarch/mesh.py` |
| Transport | stdlib HTTP node-to-node sync (no broker) | `autarch/transport.py` |
| Substrate | the portable host abstraction (runs anywhere) | `autarch/substrate.py` |
| Agent SDK | build governed agents fast (`run()` to deliberate, `enact()` for a known action) | `autarch/agent.py` |

Examples: `examples/quickstart.py`, `examples/finance_intelligence.py`, `examples/microsoft_finance_intelligence.py`, `examples/council.py`, `examples/tools.py`, `examples/mesh.py`, `examples/ollama_live.py`, `examples/provenance.py`, `examples/delegation.py`, `examples/orchestration.py`, `examples/orchestration_live.py`, `examples/memory.py`, `examples/agent_types.py`, `examples/guarantees.py`, `examples/economy.py`, `examples/network.py`, `examples/gossip.py`, `examples/durable.py`, `examples/security.py`, `examples/mcp.py`, `examples/langchain.py`, `examples/observability.py`, `examples/evaluation.py`, `examples/faithfulness.py`, `examples/resilience.py`, `examples/extract.py`.

### Finance intelligence reference architecture

`examples/finance_intelligence.py` is a fully offline implementation of a governed finance-intelligence platform: SEC, market, news, and macro ingestion fan out to eight capability-attenuated specialists; a committee synthesizes their signed evidence; and governed views are produced for investment firms, auditors, banks, and regulators.

```bash
python examples/finance_intelligence.py
```

It writes a polished self-contained HTML dashboard, the consolidated JSON result, consumer reports, static guarantee results, an architecture map, and the signed audit ledger under `sandbox/finance_intelligence/outputs/`. The deterministic fixtures can be replaced with live providers without changing the capability or evidence contracts.

### Company-parameterized financial intelligence

[examples/company_finance_intelligence.py](examples/company_finance_intelligence.py)
accepts a legal company name, exact ticker, or SEC CIK. It resolves identities from
the SEC directory rather than a fixed list of company-specific scripts.

Run from the repository root with Python 3.10 or newer:

```powershell
python examples/company_finance_intelligence.py --company "Alphabet Inc." --mode hybrid
python examples/company_finance_intelligence.py --ticker GOOGL --mode hybrid
python examples/company_finance_intelligence.py --ticker MSFT --mode hybrid
python examples/company_finance_intelligence.py --cik 1652044 --mode hybrid
```

The existing Microsoft entry point also accepts these selectors:

```powershell
python examples/microsoft_finance_intelligence.py --company "Alphabet Inc." --mode hybrid
```

With **no company selector**, that entry point retains its original 15-specialist
Microsoft product. With a selector, it uses the **six-analysis general profile**:
performance, cash flow/capital allocation, liquidity, quarterly/TTM results, tax
measures, and filing-change triage. The general profile does not relabel Microsoft's
Azure/Copilot analysis, valuation assumptions, or peer set for another company.

**Supported scope:** SEC US-GAAP companies filing Forms 10-K/10-Q. Fiscal year-end,
reporting currency, annual/quarter facts, and available fields are detected from
filings. Missing facts are shown as unavailable, never zero. Names with multiple
registrant matches require an exact ticker or CIK; GOOG and GOOGL resolve to the
same Alphabet registrant. IFRS/20-F filers, private companies, and unsupported
calendars/taxonomies fail with an explicit coverage message. Banks and insurers
may need additional sector mappings. Subsidiary discovery and standalone subsidiary
financial statements are **not implemented**; consolidated reporting is not a
separate report for GitHub, Waymo, or every owned company.

Provide your SEC-compliant identity using `--user-agent "YourOrganization contact@example.com"`
or `SEC_USER_AGENT`. `--mode live` refreshes sources; `--mode hybrid` uses fresh
cache and may label stale fallback evidence; `--mode cache-only` makes no network
requests and requires previously cached sources. `--cache-ttl-hours` sets the
general cache TTL; the SEC directory, filing metadata, facts, and filing documents
have source-specific freshness settings. No API key or LLM is required.

Outputs are isolated by CIK under `sandbox/company_finance_intelligence/`, or the
root passed to `--workspace`. Each company has persistent `cache/` and `reviews/`
directories, a fresh `runs/<run-id>/outputs/` folder per execution, and `latest.json`
pointing to its latest successful report. Previous runs and evidence are retained.
The run prints the exact HTML, JSON, and review database paths. Outputs include
HTML, Markdown, JSON, canonical review content, signed action audit, review audit,
artifact hashes, and a ZIP with member hashes. The HTML opens directly in a browser.

The general profile now uses the Microsoft-style dark financial dashboard by default:
an executive cockpit, six TTM KPI tiles, annual/quarterly/TTM charts, a searchable
specialist workbench with coverage filtering, fact drill-down, and dedicated review
and governance sections. Print/PDF uses a light layout. No styling flag or web
server is needed. Rerun the same company command to generate a new styled report;
existing run folders remain unchanged. Presentation is shared, but analytical scope
remains the six-analysis general profile, not the Microsoft-specific 15-specialist
product. Missing information and pending review remain visible.

Review uses the existing CLI with the company's database, for example:

```powershell
python examples/microsoft_finance_review.py --db sandbox/company_finance_intelligence/CIK0001652044/reviews/release_reviews.db list
python examples/microsoft_finance_review.py --db sandbox/company_finance_intelligence/CIK0001652044/reviews/release_reviews.db verify
```

The required reviewers are `finance-review-lead` and `risk-review-lead`. Approvals
are never generated automatically. Identical evidence and analysis recover the
same review; changed content supersedes the prior decision. Rerun after a review
decision to generate an updated snapshot. Integrity checks can pass while financial
coverage remains incomplete: reconciliation exceptions and retrieval gaps are
displayed and keep the combined review/reconciliation flag false. Approval never
grants trading or publication authority. CLI names are attribution, not authenticated
identity; production still requires an IdP, protected review storage, data rights,
and accountable professional validation.

### Live Microsoft finance-intelligence product

[examples/microsoft_finance_intelligence.py](examples/microsoft_finance_intelligence.py) is the real-data Microsoft **Quarterly & Change Intelligence v1.1 controlled pilot**. It dynamically discovers recent 10-K, 10-Q, and 8-K filings; retrieves the latest and prior annual reports and latest interim filing; normalizes annual, standalone-quarter, TTM, balance-sheet, segment, and product facts; and runs fifteen capability-attenuated specialists. Q2/Q3 are derived only from filed cumulative facts when direct quarter facts are absent, Q4 only from annual less nine-month YTD, and every available quarter sum is reconciled to the filed annual value.

Every raw response is URL-bound, SHA-256 verified, and retained in the content-addressed cache. Selected facts retain period, concept, accession, recast chain, formula, and input IDs. The release package adds deterministic filing-change triage, claim-to-fact links for structured-fact findings, a 15-check fail-closed package acceptance gate, signed action lineage, and a persistent two-person review bound to the exact canonical release digest.

Use a descriptive SEC-compliant user agent containing an organizational identity and contact channel:

```bash
python examples/microsoft_finance_intelligence.py --mode live --user-agent "YourOrganization research@example.com"
python examples/microsoft_finance_intelligence.py --mode hybrid
python examples/microsoft_finance_intelligence.py --mode cache-only
```

Operate the durable review independently from analysis generation:

```bash
python examples/microsoft_finance_review.py list
python examples/microsoft_finance_review.py status <review-id>
python examples/microsoft_finance_review.py comment <review-id> --reviewer finance-review-lead --comment "Tie-out complete"
python examples/microsoft_finance_review.py approve <review-id> --reviewer finance-review-lead --comment "Financial review complete"
python examples/microsoft_finance_review.py approve <review-id> --reviewer risk-review-lead --comment "Risk review complete"
python examples/microsoft_finance_review.py verify
```

Reviewer names entered through this CLI are asserted attribution, not authentication. A deployment must map principals from its IdP before invoking the same review-store API. After a decision, rerun the product to regenerate reports and manifests with the current review state; the same evidence and semantic content recover the same review ID and release digest.

The workflow writes a buyer-ready interactive HTML dashboard, detailed Markdown, normalized JSON, source and artifact manifests, review audit, verified signed action ledger, and a portable evidence bundle with its own member-hash manifest under `sandbox/microsoft_finance_intelligence/outputs/`. See [examples/microsoft_finance_intelligence_architecture.md](examples/microsoft_finance_intelligence_architecture.md) for the source hierarchy, quarter construction, calculations, governance model, specialist contracts, buyer personas, operating modes, acceptance criteria, and production-hardening path.

This is deliberately a controlled pilot—not a claim of complete enterprise production readiness. The public chart endpoint is indicative only and must be replaced by an approved licensed market-data source. Accountable professional review, data entitlements, authenticated identity, retention controls, and deployment security remain required. The workflow has no trading or external-publication authority and never auto-approves a release.

## Develop

```bash
pip install -e ".[dev]"
pytest
```

For production-grade mesh encryption (AES-256-GCM), install the crypto extra:

```bash
pip install -e ".[crypto]"
```

## Using a real local model (Ollama)

The deterministic `mock` provider runs everything offline with no setup. To put a
*real* brain in the council — same kernel, same governance, no core changes:

1. Install Ollama: https://ollama.com  (or `winget install Ollama.Ollama`)
2. Pull a model: `ollama pull llama3`
3. Run it end-to-end:

```bash
python examples/ollama_live.py                 # single real model
python examples/ollama_live.py --challenger qwen2.5   # a real two-model council
autarch do "create hello.txt that says hi" --council ollama:llama3
```

Data never leaves your machine — this is "model-autarch" in practice.

The live pipeline is hardened for real model output: Ollama runs in JSON mode,
the parser tolerates prose / markdown fences / trailing text, and the council
**fails closed** — if a model is unreachable or its critique is unparseable, the
verdict defaults to *revise* (never a silent approve). A failing model abstains
rather than crashing the council.
