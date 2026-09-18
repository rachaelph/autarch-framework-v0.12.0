---
marp: true
theme: uncover
paginate: true
size: 16:9
title: Autarch — Governed Agent Factory
description: Enterprise pitch for governed, provable agentic AI
backgroundColor: #071426
color: #eaf2ff
style: |
  section {
    font-family: 'Aptos', 'Segoe UI', system-ui, sans-serif;
    background: linear-gradient(145deg, #071426 0%, #0d2542 62%, #123557 100%);
    color: #eaf2ff;
    font-size: 25px;
    padding: 54px 68px;
    text-align: left;
    justify-content: flex-start;
  }
  h1 { color: #ffffff; font-size: 50px; line-height: 1.08; margin-bottom: 20px; }
  h2 { color: #67c7ff; font-size: 39px; margin-bottom: 18px; }
  h3 { color: #a8ddff; font-size: 27px; margin: 10px 0 6px; }
  strong { color: #ffc857; }
  em { color: #bcd3e9; }
  a { color: #67c7ff; }
  table { font-size: 19px; border-collapse: collapse; width: 100%; }
  th { background: #173b61; color: #ffffff; padding: 8px 11px; }
  td { border-bottom: 1px solid #2b5075; padding: 7px 11px; }
  code { background: #06101f; color: #79e6ae; padding: 2px 7px; border-radius: 4px; }
  ul { line-height: 1.38; }
  li { margin-bottom: 5px; }
  section.lead { text-align: center; justify-content: center; }
  section.lead h1 { font-size: 62px; }
  blockquote { border-left: 4px solid #ffc857; padding-left: 18px; color: #d6e6f5; font-style: italic; }
  .eyebrow { color: #67c7ff; font-size: 18px; letter-spacing: 2.8px; text-transform: uppercase; font-weight: 700; }
  .muted { color: #98afc6; font-size: 18px; }
  footer { color: #7693ad; font-size: 13px; }
---

<!-- _class: lead -->
<!-- _paginate: false -->

<span class="eyebrow">The governed agent factory</span>

# AUTARCH

### Governed AI agents for finance, audit, and taxation —
### with every conclusion **bounded, approved, and provable.**

**You don't use AI. You preside over it.**

<!-- Speaker note: Autarch is the control plane for creating and operating enterprise agents across industries. It generates reviewed configurations, never unrestricted authority. -->

---

## Finance wants AI. It cannot compromise control.

AI agents now review ledgers, test controls, interpret tax rules, reconcile accounts, and prepare management decisions.

Yet most deployments still rely on:

- broad ERP access and prompt-level guardrails
- manually assembled finance agents with inconsistent controls
- logs that describe activity but cannot prove integrity
- approvals disconnected from the exact configuration deployed
- pilots that cannot cross the gap into regulated production

> The blocker is no longer intelligence. It is **financial control, evidence, and accountability at scale.**

---

## The category: a governed agent factory

Autarch turns a business requirement into a deployable agent specification — then places deterministic controls between intelligence and consequence.

| Conventional agent builder | **Autarch** |
|---|---|
| Configure prompts and tools | Define authority, policy, proof, and lifecycle |
| Trust the model to comply | Enforce rules outside the model |
| Rebuild controls per project | Reuse governed industry DomainPacks |
| Audit logs after the event | Produce signed evidence by construction |

**One finance control plane. Many governed specialist agents.**

---

## From requirement to governed deployment

```text
Finance requirement
        ↓
Reviewed Finance DomainPack  · accounting + tax rules + trusted integrations
        ↓
AgentBlueprint       · role + tools + grants + budget + invariants
        ↓
Validate → Prove → Approve → Apply RBAC → Compile
        ↓
AgentDeployment      · monitored + signed + retireable
```

The factory creates **declarative configuration, not arbitrary executable code.**

> AI may propose the agent. The governance plane decides whether it exists.

---

## Universal governance. Finance-specific trust.

### Shared platform
- requirement decomposition and multi-agent orchestration
- capability security, budgets, retries, memory, and telemetry
- approval workflow, versioning, fingerprints, and lifecycle
- static guarantees and tamper-evident evidence

### Finance DomainPacks
- accounting, audit, and tax workflows
- materiality, segregation-of-duties, and approval thresholds
- approved ERP, consolidation, tax, and evidence connectors
- jurisdiction-specific rules and finance evaluation suites

**Scale across the office of the CFO without weakening financial controls.**

---

## The deterministic governance stack

| Control | Enterprise question answered |
|---|---|
| **RBAC** | Who may deploy or wield this authority? |
| **Capability kernel** | What exact action is permitted? |
| **Scope + limits** | Where, and how much? |
| **Policy-as-code** | Must this action be denied or escalated? |
| **Economic kernel** | Is it within cost, call, latency, and risk budgets? |
| **Approval plane** | Which humans ratified this exact specification? |
| **Signed Why-ledger** | Can we prove what happened and why? |

> The model can be manipulated. The kernel cannot be prompted out of policy.

---

## Reference architecture: intelligence above, control below

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Finance experiences: Audit · Tax · Close · Treasury · FP&A          │
├─────────────────────────────────────────────────────────────────────┤
│ Agent factory: DomainPack · Blueprint · Registry · Approval         │
├─────────────────────────────────────────────────────────────────────┤
│ Intelligence: Planner · Council · Specialists · Evaluators          │
├─────────────────────────────────────────────────────────────────────┤
│ GOVERNANCE CHOKEPOINT                                               │
│ RBAC → Capability → Scope → Policy → Budget → Human decision        │
├─────────────────────────────────────────────────────────────────────┤
│ Execution: Adapters · MCP · MAF · LangChain · SQL · Documents       │
├─────────────────────────────────────────────────────────────────────┤
│ Evidence: Why-memory · Signatures · Run journal · Events · OTel     │
└─────────────────────────────────────────────────────────────────────┘
```

**No model, planner, framework, or tool bypasses the governance chokepoint.**

---

## Technical sequence: one governed finance action

```text
1  Intent        “Review this journal entry”
2  Deliberate    multiple model voices propose + challenge
3  Normalize     adapter canonicalizes typed parameters
4  Authorize     capability + scope + quantitative limits
5  Evaluate      deterministic policy + budget
6  Preside       auto-rule or durable quorum approval
7  Execute       trusted adapter performs the side effect
8  Assess        deterministic / model evaluation panel
9  Seal          result + rationale + controls signed to hash chain
10 Emit          structured events, telemetry, compliance evidence
```

If a gate fails, execution does not occur—and the refusal is itself explainable.

---

## Security architecture: authority is structural

| Layer | Mechanism | Finance control outcome |
|---|---|---|
| Identity | principal + node identity | attributable actor and runtime |
| Entitlement | RBAC-filtered grants | role-aligned access |
| Least privilege | exact/wildcard capabilities | no ambient tool authority |
| Confinement | path/data scopes + limits | bounded records and amounts |
| Delegation | monotonic attenuation | child can never exceed parent |
| Integrity | Ed25519 + hash chain | tamper-evident evidence |
| Confidentiality | local-first + encrypted mesh | deployment-controlled data path |

**Prompt injection may alter a proposal. It cannot manufacture a grant.**

---

## Memory, state, and evidence are different things

| Plane | Purpose | Autarch primitive |
|---|---|---|
| Working context | current reasoning | council + run state |
| Recall | reusable semantic/episodic knowledge | governed recall memory |
| Precedent | prior human rulings | precedent store |
| Durability | resume without double execution | run journal |
| Accountability | immutable why/outcome record | signed Why-memory |
| Operations | live monitoring | structured events + OTel |

Separating these planes prevents “conversation history” from being mistaken for audit evidence.

---

## Deployment topology: control stays portable

```text
Finance UI / workflow / API
              │
      Governance Gateway ───── Approval Queue / reviewer console
              │
    ┌────────┴────────┐
    │ Autarch runtime │  policy · budgets · identities · guarantees
    └────────┬────────┘
              │
  ERP · Tax · Treasury · Data platform · MCP tools · model providers
              │
  Signed local ledger ───── OTel / SIEM / compliance export
```

- laptop, container, air-gapped host, or distributed mesh
- models remain replaceable; the decision and evidence contract remains stable
- sensitive finance data can remain inside the selected deployment boundary

---

## Approval is bound to what actually ships

Every `AgentBlueprint` and `DomainPack` receives a deterministic SHA-256 fingerprint.

- semantic versions are immutable after registration
- quorum approval is tied to the exact pack, blueprint, and fingerprint
- changing grants, policies, adapters, or budgets invalidates prior approval
- RBAC narrows authority again at compilation
- empty grants mean **no authority by default**

**No stale approval. No silent privilege expansion. No configuration ambiguity.**

---

## Prove safety before execution

Autarch statically checks deterministic grants and unconditional policies.

```text
forbid(file.delete)
require_approval(payment.send)
confine(file.write, "reports/")
```

- a passing proof applies to every child agent because delegation only narrows authority
- a failing proof returns a counterexample
- compilation fails closed when a required invariant does not hold

> This is not a model promising good behavior. It is the control plane proving a boundary.

---

## Multi-agent work — without authority sprawl

```text
Supervisor intent
   ├── Researcher   read-only
   ├── Analyst      read-only + governed models
   ├── Reviewer     policy-specific checks
   └── Writer       scoped output permission
```

- planners decompose work into dependency-aware subtasks
- specialists are selected from reviewed templates
- children receive strictly attenuated grants and isolated tools
- independent tasks execute in parallel under shared or child budgets
- the supervisor synthesizes one result with one auditable chain

**Specialization with least privilege—not a swarm with shared credentials.**

---

## Factory internals: safe specialization at scale

```text
Finance requirement
        → select Finance DomainPack
        → resolve versioned AgentBlueprint
        → validate adapters, grants, policy DSL, budget, invariants
        → prove mandatory guarantees
        → bind quorum approval to SHA-256 fingerprint
        → filter authority through principal RBAC
        → compile AgentDeployment
        → run once / monitor / retire
```

`DomainPack` standardizes accounting and tax controls. `AgentBlueprint` specializes role and authority. `AgentDeployment` manages the executable lifecycle.

**The factory scales agents without scaling unreviewed privilege.**

---

## Evidence is part of execution

Every governed action can capture:

- intent, proposal, critique, and recommendation
- kernel, policy, budget, and human decisions
- tool result, errors, undo data, and evaluation verdict
- actor identity and cryptographic signature
- tamper-evident chain position and provenance

Retention controls can redact sensitive fields while preserving integrity verification.

**From “the agent says it complied” to verifiable operational evidence.**

---

## Flagship workflow: continuous audit and tax assurance

Autarch enables a governed finance-assurance workflow:

1. ingest ledger, subledger, policy, and supporting evidence
2. risk-rank transactions, journals, accounts, and controls
3. test accounting treatment and tax position against governed rules
4. identify anomalies, control exceptions, and jurisdictional exposure
5. route material or uncertain findings to qualified reviewers
6. produce signed workpapers, evidence links, and decision trails

**The pattern generalizes:** ingest → reason → verify → approve → act → prove.

<span class="muted">A finance control pattern—not a claim of autonomous audit opinion or tax advice.</span>

---

## One platform across the office of the CFO

| Finance function | Example governed agents |
|---|---|
| **Internal audit** | control tester, sample selector, evidence and finding reviewer |
| **Taxation** | indirect-tax classifier, provision reviewer, jurisdiction monitor |
| **Controllership** | journal reviewer, reconciler, close and consolidation assistant |
| **Treasury and risk** | cash forecaster, payment-control reviewer, exposure monitor |
| **FP&A** | variance analyst, scenario reviewer, management-report assistant |

Start with one finance process. Reuse the same controls across the CFO portfolio.

---

## Fit into the stack customers already own

- **MCP:** govern imported tools or serve governed capabilities
- **LangChain:** place existing tools behind Autarch controls
- **Microsoft Agent Framework bridge:** govern functions and middleware
- **ERP, SQL, document, search, and vector adapters:** connect finance evidence
- **OpenAI, Azure OpenAI, Anthropic, Ollama:** keep intelligence replaceable
- **JSONL and OpenTelemetry:** integrate with operational monitoring

> Autarch is not another model ecosystem. It is the governance substrate beneath it.

---

## Deploy from edge to enterprise

- pure Python core with SQLite; zero required runtime dependencies
- fully offline mock path and first-class local-model support
- container-ready health and readiness checks
- durable run journal, retries, rate limits, and circuit breakers
- encrypted mesh and signed provenance for distributed operation
- typed errors and structured events for production integration

**Same governance model on a laptop, an air-gapped host, or a service fleet.**

---

## Business value

### Ship faster
Reuse blueprints, policies, integrations, evaluations, and approval patterns.

### Reduce blast radius
Each agent receives only the tools, scope, limits, and budget it needs.

### Accelerate assurance
Make controls and evidence part of the runtime—not a spreadsheet after deployment.

### Avoid lock-in
Models and orchestration libraries remain replaceable; governance stays consistent.

---

## Competitive position

| Capability | **Autarch** | Typical orchestration-first stack |
|---|:--:|:--:|
| Declarative, versioned agent factory | **Native** | Application-built |
| Deny-by-default capability kernel | **Native** | Tool/auth layer |
| Static safety guarantees | **Native** | Tests / policy checks |
| Content-bound quorum approval | **Native** | Workflow integration |
| Signed action + evaluation evidence | **Native** | Logging integration |
| Model and tool ecosystems | Integrates | Often mature/native |

**Autarch complements orchestration frameworks—and owns the governance layer.**

---

## The market has runtimes. Autarch adds a control plane.

| Platform | Designed primarily for | Documented strength |
|---|---|---|
| **Microsoft Agent Framework** | typed agents and graph workflows | Microsoft ecosystem, middleware, sessions, checkpointing |
| **LangGraph** | low-level stateful orchestration | durable graphs, persistence, interrupts, LangSmith ecosystem |
| **CrewAI** | role-based crews and event-driven flows | accessible multi-agent patterns, state, persistence |
| **OpenAI Agents SDK** | lightweight agents and handoffs | minimal primitives, tools, guardrails, tracing |
| **AutoGen** | event-driven multi-agent research/runtime | flexible messaging and team patterns |
| **Autarch** | governed agent creation and consequence | authority kernel, static proof, signed evidence, immutable approval |

These products overlap—but optimize different architectural layers.

---

## Autarch vs Microsoft Agent Framework (MAF)

### MAF is strong at
- typed agents, sessions, middleware, model/provider integrations
- explicit graph workflows, checkpointing, streaming, and human request/response
- agents, functions, and sub-workflows in one Microsoft-supported programming model

### Autarch differentiates at
- deny-by-default capability grants with scope and quantitative limits
- mathematically monotonic delegation: every child is weaker than its parent
- static proofs for forbid / require-approval / confinement invariants
- approval bound to immutable blueprint content—not only a workflow pause
- signed, tamper-evident rationale + decision + outcome evidence

**Best architecture:** MAF orchestrates; Autarch governs MAF tools and functions at the consequence boundary.

---

## Autarch vs LangGraph

### LangGraph is strong at
- low-level graphs mixing deterministic and agentic nodes
- checkpointed durable execution, time travel, stores, and interrupts
- mature visualization, debugging, deployment, and LangSmith observability

### Autarch differentiates at
- authorization is a kernel primitive, not graph-node convention
- approval carries actor/quorum and exact configuration identity
- side effects pass the same capability, scope, policy, and budget gate
- safety properties can be proven before any graph runs
- evidence is cryptographically attributable, not only traceable

**Best architecture:** keep LangGraph state machines; place governed Autarch actions behind tool nodes.

---

## Autarch vs CrewAI and AutoGen

### CrewAI / AutoGen are strong at
- intuitive teams, roles, delegation, collaboration, and reusable flow patterns
- state, memory, persistence, tracing, and flexible agent coordination
- rapid prototyping of autonomous multi-agent behavior

### Autarch differentiates at
- delegation narrows actual authority—not just task responsibility
- tool possession does not equal permission to execute
- deterministic policy and economic kernels precede side effects
- fleet-wide guarantees follow from attenuation
- one signed ledger records accepted and blocked actions

**Best architecture:** use crews or teams for reasoning; use Autarch for authority and evidence.

---

## Autarch vs OpenAI Agents SDK

### OpenAI Agents SDK is strong at
- small, productive primitives: agents, tools, handoffs, sessions, tracing
- function-tool input/output guardrails and human-in-the-loop runs
- deep OpenAI model, hosted-tool, realtime, and evaluation integration

### Autarch differentiates at
- provider-neutral governance remains stable when models change
- grant/scope/limit authorization is separate from model guardrails
- unconditional policy supports sound pre-run safety proofs
- approval is durable, quorum-aware, and fingerprint-bound
- signed evidence can be retained locally and verified independently

**Best architecture:** OpenAI agents reason and hand off; Autarch controls consequential tools.

---

## Capability matrix: where Autarch supersedes vs complements

| Capability | Autarch | MAF | LangGraph | CrewAI | OpenAI SDK |
|---|:--:|:--:|:--:|:--:|:--:|
| Agent / workflow orchestration | ✓ | **✓✓** | **✓✓** | **✓✓** | ✓ |
| Durable state / resume | ✓ | **✓✓** | **✓✓** | ✓ | ✓ |
| Deny-by-default action authority | **✓✓** | app policy | app policy | app policy | tool guardrail |
| Scoped + limited delegation | **✓✓** | custom | custom | custom | custom |
| Static safety proof | **✓✓** | — | — | — | — |
| Content-bound quorum approval | **✓✓** | custom | custom | custom | custom |
| Signed tamper-evident action proof | **✓✓** | telemetry | tracing | tracing | tracing |
| Ecosystem breadth | developing | **✓✓** | **✓✓** | **✓✓** | **✓✓** |

**Supersedes at governance; complements at orchestration and ecosystem.**

---

## The winning architecture is compositional

```text
MAF / LangGraph / CrewAI / OpenAI Agents
           planning · state · collaboration · UX
                           │
                           ▼
                AUTARCH GOVERNANCE PLANE
      identity · authority · policy · budget · approval · proof
                           │
                           ▼
          ERP / tax / treasury / files / APIs / MCP tools
```

- no forced rewrite of the customer’s orchestration layer
- one control contract across heterogeneous agent stacks
- finance teams standardize governance while developers keep preferred runtimes

> Autarch does not win by replacing every framework. It wins by becoming the layer none should bypass.

<span class="muted">Competitive assessment based on public documentation reviewed 2 September 2026; validate versions and deployment requirements during diligence.</span>

---

## Go-to-market: land with one governed workflow

### 1 · Finance design partner
Choose a costly, evidence-heavy process with a clear control owner.

### 2 · DomainPack
Encode its authority, policies, integrations, evaluations, and approval model.

### 3 · Controlled pilot
Run shadow mode; measure coverage, reviewer hours, exceptions, and false positives.

### 4 · CFO portfolio expansion
Reuse the governance plane across audit, tax, close, treasury, and FP&A.

**Commercial wedge:** governed finance workflow deployment + reusable control packs.

---

## Readiness and responsible roadmap

### Available in the v0.12 framework line
- governed agents and multi-agent orchestration
- capabilities, policies, RBAC, budgets, approvals, guarantees
- signed provenance, memory, evaluation, resilience, and telemetry
- declarative agent blueprints, DomainPacks, fingerprints, and deployment lifecycle

### Next validation milestones
- external security review and threat-model publication
- design-partner pilots with measurable business outcomes
- signed DomainPack distribution and enterprise registry service
- model-assisted requirement-to-blueprint proposals under the same controls

> Production-oriented architecture, with production claims earned through validation.

---

## Competitive research sources

Official product documentation reviewed **2 September 2026**:

- Microsoft Agent Framework — `learn.microsoft.com/agent-framework/`
- LangGraph — `docs.langchain.com/oss/python/langgraph/overview`
- CrewAI — `docs.crewai.com/`
- AutoGen — `microsoft.github.io/autogen/stable/`
- OpenAI Agents SDK — `openai.github.io/openai-agents-python/`

Comparisons describe documented architectural emphasis, not an assertion that adjacent controls cannot be custom-built. Product capabilities and licensing should be revalidated during diligence.

---

<!-- _class: lead -->

<span class="eyebrow">The enterprise agent control plane</span>

# Scale finance intelligence.
# Preserve financial control.

### **Autarch** — the governed agent factory

From finance requirement to **approved, bounded, and provable** deployment.

---

<!-- _class: lead -->
<!-- _paginate: false -->

# AUTARCH

### You don't use AI. **You preside over it.**

`pip install autarch`

<span class="muted">Finance-controlled · Audit-ready · Model-agnostic · Provable</span>
