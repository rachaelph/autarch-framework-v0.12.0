---
marp: true
theme: uncover
paginate: true
size: 16:9
backgroundColor: #0b1f3a
color: #eaf0f8
style: |
  section {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: linear-gradient(160deg, #0b1f3a 0%, #102a4c 100%);
    color: #eaf0f8;
    font-size: 26px;
    padding: 60px 70px;
    text-align: left;
    justify-content: flex-start;
  }
  h1 { color: #ffffff; font-size: 50px; line-height: 1.1; }
  h2 { color: #5db0ff; font-size: 38px; }
  h3 { color: #9ecbff; font-size: 28px; margin-bottom: 6px; }
  strong { color: #ffd36b; }
  a { color: #7cc4ff; }
  table { font-size: 20px; border-collapse: collapse; width: 100%; }
  th { background: #16365f; color: #ffffff; padding: 8px 12px; }
  td { border-bottom: 1px solid #25456f; padding: 7px 12px; }
  code { background: #0a1830; color: #9be3a1; padding: 2px 7px; border-radius: 4px; }
  ul { line-height: 1.45; }
  section.lead { text-align: center; justify-content: center; }
  section.lead h1 { font-size: 62px; }
  blockquote { border-left: 4px solid #ffd36b; padding-left: 18px; color: #cfe0f2; font-style: italic; }
  .tag { color: #ffd36b; font-size: 20px; letter-spacing: 2px; text-transform: uppercase; }
  footer { color: #6f8bb0; font-size: 14px; }
---

<!-- _class: lead -->
<!-- _paginate: false -->

<span class="tag">Governed Agentic AI</span>

# AUTARCH

### You don't use AI. **You preside over it.**

The agent framework where intelligence is unlimited —
but every consequence is **governed, recorded, and provable.**

<!-- Speaker note: Open with the one-line thesis. Autarch is not another orchestrator; it is the governance and proof layer for AI agents. -->

---

## The problem nobody else is solving

AI agents are starting to take **real-world actions** — moving money, changing records, calling tools, deleting data.

Today's frameworks are built to make agents **act**.
None are built to make them **accountable.**

- 🔓 Tools run with unrestricted access
- 🌀 Agents loop, overspend, double-execute
- ❓ "What did it do, and who allowed it?" — no answer
- 🚫 Nothing a regulator or auditor can trust

> Ungoverned agents are a **liability** waiting to happen.

<!-- Speaker note: Frame the pain. These are documented failure modes across LangChain, AutoGen, CrewAI. The market is racing on capability and ignoring consequence. -->

---

## The shift

| The old question | The Autarch question |
|---|---|
| *"What can the AI do?"* | *"What may the AI do — and can you prove it?"* |

**Intelligence becomes a commodity. Governance becomes the moat.**

Autarch inverts the model: the AI only ever **proposes**.
A deterministic kernel **disposes.**

<!-- Speaker note: This is the core reframe. As models commoditize, the differentiator is control and provability, not raw capability. -->

---

## What is Autarch?

A **self-contained Python framework** for building AI agents whose every action is:

- ✅ **Authorized** — by an explicit capability grant
- ✅ **Bounded** — by policy and budget
- ✅ **Recorded** — in a tamper-evident ledger
- ✅ **Provable** — cryptographically, to a third party

> Pure Python + SQLite. **Zero required dependencies.** `pip install` and it runs anywhere — fully offline.

<!-- Speaker note: Emphasize self-contained. No Postgres, no Redis, no broker. Runs on a laptop or in an air-gapped environment. -->

---

<!-- _class: lead -->

## The Moat

### Five things no other framework can do
*(without re-architecting from scratch)*

---

## 1 · Provable execution

Every action is **signed** into a tamper-evident ledger
(Ed25519 + hash chain).

You can **prove** — to an auditor, a regulator, a court —
*what* an agent did, *by whose authority*, and that the
record was **not altered.**

> No orchestration framework has a chokepoint to sign. Autarch is built around one.

---

## 2 · Deterministic capability kernel

**"AI proposes, the kernel disposes."**

- Nothing acts without an explicit, ratified grant
- **Deny by default**
- Prompt injection can't escalate — the kernel gates the **action**, no matter what the prompt says

> The model can be fooled. The kernel cannot be talked out of the rules.

---

## 3 · Formal safety guarantees

**Prove an entire class of actions is impossible — *before* running.**

```
autarch guarantee --forbid file.delete
→ PROVEN: this agent can never delete a file.
```

If a guarantee can't hold, you get a **counterexample**, not a surprise in production.

> Not testing. Not hoping. A sound static proof.

---

## 4 · Governed evaluation

Built-in **judge-LLMs** and deterministic checks —
and the verdict is **signed into the ledger.**

Prove an output was **evaluated**, by **which judge**,
and **what it scored.**

> Everyone else's evaluation is an ungoverned side-activity. Yours is evidence.

---

## 5 · Right-to-be-forgotten — *with the proof intact*

Redact PII for GDPR / compliance —
while the integrity chain **still verifies.**

- The sensitive data is **provably gone**
- The audit proof **survives**

> The hard part of compliance, solved: forget the data, keep the trust.

---

## Core governance toolkit

| Capability | What it does |
|---|---|
| **Capability kernel** | fine-grained grants, scope, limits — deny by default |
| **Policy-as-code** | allow / deny / require-ratify rules |
| **RBAC** | roles decide *who* may wield what |
| **Delegation** | sub-agents get *strictly weaker* authority |
| **Economic kernel** | budgets every action — *"allowed ≠ affordable"* |
| **Human-in-the-loop** | ratify / overrule / send-back, with precedent |

<!-- Speaker note: These compose: RBAC (who) -> kernel (what) -> delegation (narrower) -> policy (conditions) -> budget (affordability). -->

---

## How it works

```
Intent
  ↓
Council deliberates   ← AI proposes (any model, in parallel)
  ↓
Kernel · Policy · Budget   ← deterministic gates (all must pass)
  ↓
Preside: ratify / overrule / send-back
  ↓
Execute  →  Sign into tamper-evident ledger
```

**AI on top. A deterministic, provable kernel underneath.**

<!-- Speaker note: Walk the spine. The AI half is swappable and advisory; the governance half is deterministic and signed. -->

---

## Reliability that doesn't fall over

- 🔁 **Never crashes on rate limits** — proactive token-aware queue + retry + circuit breaker, auto-applied to every model call
- 💾 **Durable & resumable** — crash-safe; resume never double-executes
- ↩️ **Governed rewind** — audited, reversible undo
- 🧩 **Typed errors** — stable, catchable codes

> The boring production problems that sink other agents — already handled.

---

## Observability & compliance

- 📡 **Structured event stream** — every step, machine-readable
- 📊 **Telemetry** — JSON-lines or OpenTelemetry
- 📜 **Regulator-grade audit export** + retention controls
- ❤️ **Health/readiness probes** — container-ready

> Observable, exportable, forgettable, provable — without losing the integrity proof.

---

## Strategy: absorb, don't reinvent

Autarch sits **underneath** the tools you already use.

- 🔌 **MCP** — govern external MCP tools; serve yours *as* a governed MCP server
- 🔗 **LangChain bridge** — run their tools **governed**; expose yours to their agents

> **Keep your LangChain. Add Autarch underneath. Now it's auditable.**

Their ecosystem becomes **your** feature set — instantly governed.

---

## Runs anywhere

- 🪶 **Pure Python + stdlib SQLite** — zero required dependencies
- 🔒 **Local-first** — first-class Ollama; data never leaves the machine
- 🌐 **Encrypted mesh** — one identity, AES-256-GCM sync, no broker
- 🐳 **Docker + CI included**
- ⌨️ **CLI** — `do · why · prove · guarantee · health · audit · mesh`

> From an air-gapped laptop to a container fleet — same package.

---

## Where it stands today

<span class="tag">Verified, not vapor</span>

- ✅ **390 automated tests passing**
- ✅ **24 runnable examples**
- ✅ **v0.9.0** — coherent, end-to-end
- ✅ Works **100% offline**, zero runtime dependencies

> A serious, working framework — proven on every claim above.

<!-- Speaker note: These numbers are real and reproducible. Use them to establish credibility without overclaiming. -->

---

## How we compare

| | **Autarch** | LangChain / AutoGen / CrewAI |
|---|:--:|:--:|
| Provable, signed actions | 🟢 **core** | 🔴 none |
| Deterministic kernel / deny-by-default | 🟢 **core** | 🔴 none |
| Formal safety guarantees | 🟢 **unique** | 🔴 none |
| Governed, signed evaluation | 🟢 **unique** | 🔴 none |
| GDPR redaction w/ proof intact | 🟢 **unique** | 🔴 none |
| Huge integration ecosystem | 🟡 *absorbs theirs* | 🟢 mature |
| Adoption / battle-testing | 🔴 early | 🟢 large |

> We don't out-feature them. We **govern** them.

---

## Who needs this

The buyers for whom *"prove every AI action"* is a **requirement**, not a nice-to-have:

- 🏦 **Financial services** — auditable, budgeted AI actions
- 🏥 **Healthcare** — provenance + right-to-be-forgotten
- 🛡️ **Defense & critical infrastructure** — air-gapped, deny-by-default
- 🏛️ **Regulated enterprise** — compliance you can demonstrate

> Generic users want capability. **Regulated buyers will pay for control.**

---

## Honest status & roadmap

**Shipped (v0.9.0):** governance, provenance, guarantees, economy, evaluation, resilience, durability, MCP/LangChain bridges, mesh, **provable memory**, **governed multi-agent orchestration**.

**Next (planned):** async runtime, structured output, richer specialist libraries.

> Early access / prototype — **no production users or third-party security audit yet.** We sell trust; we won't overclaim it.

<!-- Speaker note: This slide protects credibility. Being honest about maturity is itself part of the trust pitch. Adjust depending on audience. -->

---

<!-- _class: lead -->

# The one-liner that wins

## *"The only agent framework that can **prove** what it did."*

For AI you can put in front of a **regulator.**

**Autarch** — govern the consequences, not the intelligence.

---

<!-- _class: lead -->
<!-- _paginate: false -->

# Thank you

**Autarch** · Governed, provable agentic AI
*You don't use AI. You preside over it.*

`pip install autarch`
