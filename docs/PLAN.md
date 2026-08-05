# Autarch — Build Plan

The detailed, phased plan. Each phase has a goal, concrete steps, and a "done when" bar.
We build the smallest thing that proves the idea is real and safe, then grow it.

---

## Guiding principles (apply to every phase)

1. **AI proposes, kernel disposes.** Every effect passes through a deterministic capability gate.
2. **Vertical slices, not horizontal layers.** Each phase ends with something you can *run and feel*.
3. **The boring layer is the show.** Governance/audit are repurposed into the "council you preside over" experience.
4. **Single model works on day 1.** The full council is the ceiling, never a gate on value.
5. **Contracts are the moat.** We own the typed interfaces; models/tools/substrates are pluggable.

---

## Phase 0 — Foundations & decisions  (the groundwork)  ✅ DONE

**Goal:** lock the principles and the stack so we never thrash mid-build.

> Status: complete. Python + flat layout, zero runtime deps. Core contracts defined
> (`Intent`, `CapabilityGrant`, `Action`, `GateResult`, `ActionResult`, `WhyRecord`).
> Repo scaffolded; 21 tests green.

Steps:
1. Approve `docs/MANIFESTO.md` (the vision of record).
2. Choose the implementation stack (recommendation below — needs your nod).
3. Define the **core contracts** (interfaces) on paper: `Intent`, `Proposal`, `CapabilityGrant`, `Action`, `Ruling`, `WhyRecord`.
4. Scaffold the repo: package layout, lint/format, test runner, basic CI.

**Done when:** repo runs `hello` end-to-end (empty pipeline) with tests + lint green.

---

## Phase 1 — The Nucleus (single mind + challenger + one real action)  ✅ DONE

**Goal:** the heartbeat. One intent → a proposal → a *challenger* critiques it → capability gate checks it → one real action on the machine → a why-record you can read back.

> Status: complete and validated. `autarch do/why/history` works; quickstart works;
> deny-by-default delete demonstrated; every action audited and reversible.

Steps:
1. **Intelligence Bus v0** — a `ModelProvider` interface + 2 implementations:
   - `LocalEcho`/mock provider (deterministic, for tests, no API key needed)
   - one real provider (your choice: OpenAI / Anthropic / local Ollama) behind the same interface.
2. **Proposer + Challenger** — proposer drafts an action; challenger does a critique pass (risk, cost, "should we veto?"). Single model can play both roles on day 1.
3. **Capability Kernel v0** — deterministic gate:
   - typed `CapabilityGrant` (what's allowed, scope, limits)
   - permission check (deny by default)
   - audit log (append-only)
   - reversibility hook (record an undo where possible).
4. **One Adapter** — `FileSystemAdapter` scoped to a sandbox dir: `read`, `write`, `move`, `delete` — each gated.
5. **Why-Memory v0** — every action writes evidence + the policy that approved it; a `why <action-id>` command replays it.
6. **CLI** — `autarch do "<intent>"` runs the loop and prints the deliberation + ruling.

**Done when:** you type an intent, watch proposer vs challenger, approve, and a file action executes — then run `autarch why <id>` and see the full justification. All gated, all logged, all reversible.

---

## Phase 2 — The Council (plurality + presiding UX)  ✅ DONE

**Goal:** turn the single-mind loop into a real council you preside over.

> Status: complete and validated. A multi-voice council deliberates with a
> deterministic *most-cautious-wins* moderator; disagreement is surfaced; the
> autarch ratifies / overrules / sends back; rulings are remembered as
> precedent and applied automatically next time; policy-as-code can deny or
> escalate even granted actions. 42 tests green.

Steps:
1. **Multi-voice deliberation** — N providers (your model + GPT + Claude) each propose/critique; a deterministic **moderator** collects positions, surfaces *disagreement*, and produces a verdict. → `autarch/council/deliberation.py`; offline disagreement via `mock:bold` / `mock:cautious` personas.
2. **Presiding controls** — ratify / overrule / send back to debate. → `HumanDecision.SEND_BACK`, bounded re-deliberation that excludes the rejected approach.
3. **Legibility** — render the deliberation as a *fast, decisive verdict*: voices, tally, a `[DISAGREEMENT]`/`[consensus]` badge, gate, policy, precedent, recommendation.
4. **Rulings remembered** — `autarch/precedent.py`. Genuine presiding judgments (not mechanical auto/policy outcomes) are stored and applied to future proposals.
5. **Policy-as-code** — `autarch/policy.py`. Effects `deny > require_ratify > allow`; `require_ratify` blocks *auto*-ratification only.

**Done when:** a single intent produces a visible multi-model deliberation, you overrule it once, and the override is remembered and applied next time. ✓ verified end-to-end.

---

## Phase 3 — Ecosystem & "prove it" (adapters + provable trust)  ✅ DONE

**Goal:** make it extensible and make trust *felt*.

> Status: complete and validated (62 tests green). A LangChain/MCP-style tool runs
> *inside* the kernel via the ToolAdapter; the why-memory is a tamper-evident hash
> chain you can verify; `prove` renders a verifiable receipt and `prove --chain`
> validates the whole ledger; `rewind` reverses past actions under governance and
> records each reversal as a new auditable action.

Steps:
1. **Adapter SDK** — `autarch/adapters/tool.py`: wrap any callable as a governed `tool.<name>` capability (`from_callables`). The ecosystem play.
2. **Wrap an existing framework** — `from_langchain_tools` / `from_mcp_tools` (duck-typed, no dependency) run third-party tools *inside* the kernel — absorb-then-replace, proven in `examples/tools.py`.
3. **"Prove it" command** — `autarch prove <id>`: full evidence + a verifiable integrity seal; `autarch prove --chain`: validates the entire tamper-evident ledger.
4. **Rewind** — `autarch rewind --last N | --since "1 hour" | --id <id>` with `--keep`/`--keep-id`. Governed (passes the kernel) and itself recorded (`rewind_of`). → `autarch/rewind.py`.
5. **Persistent why-memory** — SQLite hash chain (`seal`, `prev_seal`), `verify`, `verify_chain`, `since`, `all`. Tamper to any record is detected.

**Done when:** a third-party-style adapter runs under governance, and you can `prove` and `rewind` real actions. ✓ verified end-to-end (including live tamper detection).

---

## Phase 4 — Mesh & substrate (multi-device, optional microkernel)  ✅ DONE

**Goal:** "runs anywhere, one place."

> Status: complete and validated (83 tests green). Two nodes share one realm
> identity and a converging policy set; the why-memory syncs node-to-node via
> encrypted, authenticated bundles (CRDT union by id); each node's records form an
> independently-verifiable sub-chain so a merged ledger stays intact.

Steps:
1. **Substrate Bus** — `autarch/substrate.py`: a dependency-free description of the host (OS/machine/Python, form-factor tags, per-platform data dir). `autarch substrate` shows it. The portable seam a future microkernel would implement.
2. **Mesh v0** — `autarch/mesh.py`: a `Realm` (shared key identity + per-node id), encrypt-then-MAC bundles (stdlib HMAC-SHA256 keystream — honest prototype crypto; production → AEAD), and `export_bundle`/`import_bundle` doing an idempotent union merge. Per-origin hash chaining in `autarch/memory.py` keeps merged ledgers verifiable. Shared policies travel with the bundle and converge across nodes. CLI: `mesh init|status|export|import`.
3. **(Optional, later) microkernel substrate** — deferred until a real bare-metal/confidential/real-time need appears.

**Done when:** an intent on one node, governed by shared policy, with memory synced to another node. ✓ verified end-to-end (laptop creates a `deny file.delete` realm policy → syncs to phone → phone enforces it on a granted delete; merged ledger spans both origins, intact).

---

## Path ahead — from working prototype to best-in-class

The four phases are built and tested (102 tests). The road to a defensible,
production-grade product splits into three tiers. Tiers 1–2 make it *credible*;
**Tier 3 is the moat** — features no agent framework has, that incumbents are
structurally disinclined to build (their business is power, not control).

### Tier 1 — Make it REAL (harden the prototype)
- **Live model end-to-end** ✅ *done and verified with Ollama `llama3`.*
  Pipeline hardened for real output: Ollama JSON mode, robust parser (prose/fences/trailing
  text), a **fail-closed council** (unreachable/unparseable critique → *revise*, never a silent
  approve; a failing model abstains), and **adapter parameter schemas + synonym normalization** so
  free-form model output lines up with typed adapters. Proven live: llama3 created a file through the
  full governed loop; and when llama3's own critic *approved* a deletion, the **kernel still denied it**
  (no grant) and the file survived — "AI proposes, the kernel disposes," demonstrated with a real model.
  See `examples/ollama_live.py`.
- **AEAD crypto** ✅ *done and verified.* Mesh bundles now use **AES-256-GCM** (via
  `cryptography`, installed `pip install autarch[crypto]`) with a transparent stdlib
  fallback when the library is absent, so zero-dependency installs still work. The wire
  format is self-describing (a scheme tag), so AES-GCM and the fallback interoperate and
  the cipher can be upgraded without breaking existing bundles. See `autarch/mesh.py`.
- **Concurrency safety** ✅ *done.* SQLite **WAL** mode + `busy_timeout` + `synchronous=NORMAL`
  on both the why-memory and precedent stores (stdlib only) — concurrent readers and a writer no
  longer block or error. See `autarch/util.py:configure_sqlite`.
- **Network transport** ✅ *done and verified.* Node-to-node sync over **stdlib HTTP**
  (`http.server` + `urllib`) behind the existing encrypted-bundle seam — no broker, no dependency.
  `MeshServer` serves/receives AES-GCM bundles; `pull`/`push`/`sync` clients; `autarch mesh serve`
  and `autarch mesh sync <url>`. Binds to **loopback by default** (secure by default); security
  rides on the realm key (end-to-end AEAD), not the transport. See `autarch/transport.py`,
  `examples/network.py`.

### Tier 2 — Make it SCALE (pulled by real need, not pre-built)
> **Design constraint (locked):** Autarch stays a **self-contained Python package** — pure
> Python + stdlib SQLite, no external services to provision. "pip install and it runs, anywhere"
> is a core promise. Scale work must honor this; no mandatory Postgres/Redis/brokers.
- **Async execution** ✅ *done and verified.* The council polls its voices **concurrently** via a
  stdlib `ThreadPoolExecutor` (model calls are blocking I/O, so threads are the right, dependency-free
  choice), so deliberation latency is the slowest model, not the sum. Order and determinism are
  preserved; a single voice skips the pool. Measured **~4× speedup** with 4 voices (2.42s → 0.61s).
  `Agent(parallel=True)` (default); `Council(max_workers=...)`. See `tests/test_council_async.py`.
- **Storage at scale** ⬜ — stay on SQLite (it scales remarkably far). The `WhyMemory` interface is
  swappable *if* a deployment ever needs it, but no external DB is required or assumed.
- **N-node gossip mesh** ✅ *done and verified.* Nodes register peer URLs (`mesh peer add`); one
  `mesh gossip` round syncs with all of them, tolerating unreachable peers. Because the ledger is a
  grow-only CRDT, records converge **epidemically** — a record on A reaches C through B without A and C
  ever talking directly. Verified across a 3-node chain (with cross-node provenance intact). See
  `autarch/transport.py:gossip`, `examples/gossip.py`.

The hardening tiers are complete; everything remains a self-contained Python package (stdlib + SQLite,
optional `cryptography` for AES-GCM), with nothing external to provision.

### Tier 3 — Make it UNCOPYABLE (the differentiation)
1. **Non-repudiable provenance** ✅ *done and verified.* Every action's seal is signed with
   the node's **Ed25519** key; the node id is *derived from* the public key, so authorship is
   cryptographically bound and unforgeable. `prove` shows a `Provenance` line; signatures travel
   with mesh bundles so a peer can verify *who* authored each record — and a realm member holding
   the shared symmetric key still cannot forge another node's signature. Graceful no-op without
   `cryptography`. See `autarch/provenance.py`, `examples/provenance.py`, `autarch identity`.
2. **Capability attenuation & delegation** ✅ *done and verified.* `Agent.spawn(...)` hands a
   sub-agent a **strictly weaker** capability: the request is attenuated under the parent's grants
   along name, scope, and limits — anything that would widen is dropped (deny-by-default for
   delegation). Enforced *structurally* by the kernel, which now confines a `path_prefix` scope to
   its actual subdirectory. Nested delegation only ever shrinks authority. See `autarch/delegation.py`,
   `examples/delegation.py`. (Also fixed a latent kernel bug: `path_prefix` previously only blocked
   sandbox escape, not subdirectory confinement.)
3. **Formal policy guarantees** ✅ *done and verified.* `prove_guarantees(...)` / `Agent.guarantee(...)`
   statically **prove** safety invariants over the deterministic grant + policy model, *before* the agent
   runs and regardless of what the model proposes: `Invariant.forbid(C)`, `Invariant.require_approval(C)`
   (two-person-rule basis), `Invariant.confine(C, prefix)`. Failures return a counterexample. `autarch
   guarantee` exits non-zero so it can gate CI. Delegation preserves guarantees (attenuation only narrows).
   See `autarch/guarantees.py`, `examples/guarantees.py`.
   > Soundness boundary: a sound static proof over grants, scopes, and *unconditional* policy effects — not
   > full theorem-proving. Conditional (`when`) policies are treated conservatively (never relied on for a
   > guarantee), so a "GUARANTEED" result is always sound, though the checker may be conservative.
4. **Economic / governance kernel** ✅ *done and verified.* An optional `Budget` meters every
   execution (cost, model calls, risk, or any custom meter); the `EconomicKernel` refuses an action
   *before it runs* if its estimated cost would bust a ceiling — even when the capability gate allows
   it. A `CostModel` (with custom per-capability prices) estimates cost; the budget is charged on
   successful execution and recorded in the why-memory. A spawned sub-agent **shares the parent's
   budget pool**, so spend is controlled across a whole agent tree. `autarch do --budget-cost/-calls/-risk`.
   See `autarch/economy.py`, `examples/economy.py`.

Sequence: live Ollama → AEAD → provenance → attenuation → formal guarantees → economic kernel.
Scale work runs in parallel, driven by actual load.

> Honest boundary on provenance: it proves *authorship* (the holder of key K signed this, and the
> node id is bound to K). It does not yet prove K is an *authorized* realm member — recording the
> set of member public keys is the natural next trust-management layer.

---

## Enterprise hardening program (production-grade)

Turning the differentiated core into something deployable in a regulated enterprise. The
self-contained constraint still holds (stdlib + SQLite; optional deps only). Strategy:
**absorb, don't reinvent** — sit *below* LangChain/MS, wrap their ecosystem under governance.

### Phase A — Reliability core ✅ *done and verified*
- **Typed errors** (`autarch/errors.py`) — a stable, catchable taxonomy (`AutarchError` →
  `GovernanceError`/`CapabilityDenied`/`PolicyDenied`/`BudgetExceeded`/`AdapterError`/`ModelError`/
  `ValidationError`) with `code` + structured `context`, raised at boundaries.
- **Structured observability** (`autarch/events.py`) — every run emits a typed `Event` stream
  (`run.start → deliberation.complete → gate/policy/budget.checked → decision.made → action.executed
  → run.complete`). Pluggable `EventSink` (Null default = zero overhead; `ListSink`; `CallbackSink`
  for OTel/JSON-lines export). Emission never raises.
- **Durable, resumable execution** (`autarch/runlog.py`) — `RunJournal` (SQLite/WAL) records each
  run's lifecycle. `Agent(run_id=, journal=, events=)` + `Agent.resume(run_id)`. **Resuming a
  completed run returns its recorded outcome WITHOUT re-executing the side effect** — verified: the
  side effect ran exactly once across a simulated crash/restart. `journal.unfinished()` lists runs
  awaiting crash recovery. All defaults off, so existing behavior is unchanged.
  See `examples/durable.py`.
  > Honest boundary: the narrow window between a side effect committing and the journal recording it
  > requires idempotent adapters for exactly-once across a hard crash — true of every durable engine
  > without distributed transactions. The common case (process restart/retry) is handled correctly.

### Phase B — Enterprise security & identity ✅ *done and verified*
- **Secrets at rest** (`autarch/provenance.py`) — `NodeIdentity.save(workspace, passphrase=...)`
  encrypts the private key with **scrypt + AES-256-GCM**; the plaintext key never touches disk
  (verified). `load(..., passphrase=...)` raises a typed `SecretError` on a wrong/missing passphrase.
  Plaintext save still works (back-compat) but is clearly flagged `private_plaintext: true`. File is
  written `0o600`. Old plaintext `identity.json` files still load.
- **RBAC over capabilities** (`autarch/rbac.py`) — `Role(grantable=[patterns])`, `Principal(roles)`,
  `RoleRegistry`, `AccessControl`. `Agent(principal=, access=)` filters requested grants to those the
  principal's roles permit; the rest are dropped (deny by default) into `agent.denied_grants`, before
  the kernel ever sees them. Composes with the kernel (who-may-act) + delegation (only-narrower).
  Propagates to spawned sub-agents. All defaults off, so existing behavior is unchanged.
- **Identity hook** — `Principal` is the integration point; a host wires OIDC/Entra by resolving a
  principal (id + roles) however it likes. See `examples/security.py`.

### Phase C — Ecosystem absorption ✅ *done and verified*  *(the gap-closing leverage)*
- **MCP, both directions** (`autarch/mcp.py`, stdlib JSON-RPC, no `mcp` dependency):
  `from_mcp_server(command)` connects to an external MCP server and wraps its tools as *governed*
  capabilities; **`MCPServer`** exposes Autarch's capabilities *as* an MCP server where **every
  `tools/call` is authorized by the capability kernel first** — so any MCP client (an IDE, Claude
  Desktop, another agent) gets governance it never had. Verified: a granted tool runs, an ungranted
  tool is refused *"denied by governance"*. See `examples/mcp.py`.
- **LangChain bridge, both directions** (`autarch/langchain_bridge.py`, duck-typed, no LangChain
  dependency): `govern_langchain_tools(tools)` wraps LangChain tools as governed capabilities (with
  argument schemas surfaced to the council); `as_langchain_tool(...)` exposes a Autarch-governed
  capability as a LangChain-compatible tool that still enforces the kernel when a LangChain agent calls
  it. Verified both ways. See `examples/langchain.py`.

> Strategy realized: instead of rebuilding hundreds of integrations, Autarch **inherits** the MCP and
> LangChain ecosystems *under governance* — "their tools gain governance; our governed tools drop into
> their agents." That turns "few integrations" into "governs the whole ecosystem."

### Phase D — Operability & compliance ✅ *done and verified*
- **Telemetry** (`autarch/telemetry.py`) — `JsonlSink` writes the event stream as durable JSON
  lines (stdlib); `otel_sink()` bridges to OpenTelemetry *if* `opentelemetry-api` is installed
  (`pip install autarch[otel]`), else a clear error. OTel is strictly optional.
- **Compliance** (`autarch/memory.py`) — `export_audit(path)` exports the full trail (records +
  seals + signatures); `redact(why_id, fields)` masks PII for **right-to-be-forgotten** via a
  *separate overlay* so the sealed payload is untouched — **`verify_chain` and `verify_provenance`
  keep passing** while reads/exports show `[redacted]`. `prune(older_than)` for retention. Verified:
  redaction masks PII *and* the ledger still verifies.
- **Health/readiness** (`autarch/health.py`) — `health_check(workspace)` reports storage, ledger
  integrity, identity, and crypto; `autarch health [--json]` exits non-zero on a broken ledger
  (for container probes).
- **Packaging** — `Dockerfile` (non-root, volume, `HEALTHCHECK`), `.github/workflows/ci.yml`
  (test matrix 3.9–3.12 + example smoke + health), `docs/QUICKSTART.md`. CLI: `autarch health`,
  `autarch audit export|redact`. See `examples/observability.py`.

**The enterprise hardening program is complete (A + B + C + D).** Autarch is a self-contained,
governed, durable, observable, compliant, ecosystem-absorbing AI operating layer — pure Python +
SQLite, with `cryptography`/`opentelemetry` optional.

### Phase E — Governed evaluation & reflection ✅ *done and verified*
Judge-LLMs, deterministic checks, and reflect-then-retry are common developer hand-rolls; Autarch
ships the **reusable contract** so they're built in — and makes evaluation *governed*.
- **`Evaluator` contract + 3 references** (`autarch/evaluation.py`): `AssertionEvaluator`
  (deterministic — preferred, no LLM bias), `RubricJudge` (LLM-as-judge, **fails closed** on
  unparseable output, clamps score), `ConsensusEvaluator` (mean/min/majority across judges — mitigates
  single-judge bias).
- **`reflect(produce, evaluator, min_score, max_revisions)`** — a *bounded* produce→evaluate→improve
  loop. Opt-in; the producer controls idempotency, so it never re-executes Autarch side effects.
- **Governed evaluation (the moat)** — `Agent.run(evaluate=...)` scores the action's output and
  records the verdict in the **signed, tamper-evident** why-memory (`eval_score`/`eval_passed`/
  `evaluator`), emits an `evaluation.complete` event, and surfaces it in `prove`. So you can
  *prove* an output was evaluated, by which judge, and what it scored — verified: provenance still
  verifies with the verdict in the signed payload. See `examples/evaluation.py`.
  > Honest boundaries: LLM judges are biased (position/verbosity/self-preference) — prefer
  > deterministic checks and consensus. Reflection doesn't reliably improve quality — it's opt-in and
  > bounded. A score threshold is a *runtime* gate, not a static `guarantee`.

### Phase F — Resilience: never fall over on rate limits or flaky providers ✅ *done and verified*
Every production agent hits rate limits, token-quota errors, and transient 5xx/timeouts under load.
Today most teams hand-roll fragile retry loops. Autarch owns this at the **one seam every model call
passes through** (`ModelProvider.complete`), so developers write *zero* resilience code and the whole
system — every council member, challenger, and judge — inherits it.
- **`Resilient` wrapper** (`autarch/resilience.py`) — a drop-in `ModelProvider` (reports the inner
  `name` unchanged) composing three mechanisms, all pure-stdlib and thread-safe for the parallel
  council:
  - **Retry** — exponential backoff + **full jitter**, only on transient failures; honors a server's
    `Retry-After`. Terminal errors (bad request/auth) are never retried — that just wastes quota.
  - **Proactive rate limiting** — a token bucket over **requests/min *and* tokens/min** that *waits*
    for capacity instead of firing and failing. Tell Autarch your provider's limit once and it
    **guarantees you stay under it** — rate limits stop being errors and become backpressure. A
    throttle never trips the breaker and never surfaces as a failure (bounded by `max_throttle_waits`).
  - **Circuit breaker** — closed→open→half-open; fails fast while a provider is down so you don't burn
    budget hammering a dead endpoint, then probes for recovery.
  - **Adaptive control (AIMD)** — every throttle multiplicatively narrows the effective rate; sustained
    success additively widens it back. The pace **tunes itself** to what the provider currently
    tolerates, from the transactions you actually fire.
- **Typed model errors** (`errors.py`) — `RateLimited` (with `retry_after`), `ModelUnavailable`,
  `CircuitOpen`, all subclassing `ModelError`, so the layer reacts precisely. `OllamaProvider` now
  raises these (a real **bugfix**: a transient 503 used to crash the run; now it's retried).
- **Automatic & observable** — `build_provider` wraps network providers by default (`resilient=False`
  to opt out); the offline mock is never wrapped (stays deterministic). Every retry/throttle/trip emits
  a `provider.*` event. For a cloud model, add proactive limits in one line:
  `make_resilient(MyApiProvider(), rate=RateLimit(requests_per_minute=3500, tokens_per_minute=90_000))`.
  See `examples/resilience.py`.
  > Honest boundaries: the token count is a `~4 chars/token` estimate (inject a real tokenizer for
  > exactness) — fine for pacing, not billing. Completion length is unknown pre-call, so a fixed
  > `est_completion_tokens` headroom keeps tpm budgeting conservative. Under heavy multi-thread
  > concurrency the bucket is approximate (bounded over/under), not a hard cap.

### Phase G — `enact()`: govern a KNOWN action without deliberation ✅ *done and verified*
The council is the right tool when the *AI* decides what to do. But plenty of real work is a known
action: a deterministic workflow step, a decision handed over by an external planner, a directly
invoked governed tool, or a replay. Before this, the only ways to run a known action were to script a
fake council (ceremony) or drop to `kernel.authorize()` + `adapter.execute()` directly — which loses
the signed ledger, events, and journal that `Agent` wires together. That was a real gap in the
governance kernel.
- **`Agent.enact(action, params=..., evaluate=...)`** (`autarch/agent.py`) — govern + execute + **sign**
  a known action with no deliberation. Accepts a capability name (`"doc.read"`) or a ready `Action`.
  It runs the **full deterministic pipeline** — capability kernel, policy, and budget all dispose — and
  records the outcome in the same signed, tamper-evident why-memory as `run()`, emitting the same
  event stream (`gate.checked`/`action.executed`/`run.complete`). Only the *intelligence* half (the
  council) is skipped. Durable-resume parity: a completed `run_id` returns its prior outcome without
  re-executing. Governed-evaluation parity: pass `evaluate=` and the verdict is signed into the ledger.
- **On-thesis, not a shortcut** — calling `enact` *is* the act of presiding (it satisfies a
  `require_ratify` policy), but the kernel can still deny it (no grant), policy can still `deny` it, and
  the budget can still refuse it. *AI proposes, the kernel disposes — even when you are the one
  proposing.* The audit record is honest: the caller is the proposer, `rounds=0`, no council review.
- tests `test_enact.py` (11): granted-runs-and-signs, kernel-refuses-ungranted, `Action`-object input,
  `require_ratify` satisfied, `deny`/budget blocked, same event stream, governed evaluation signed,
  synonym-param normalization, bad-input rejection, actor-as-proposer. `examples/extract.py` rewritten
  to use it (governed PDF read → llama3 extraction → deterministic validation), dropping the scripted-
  council boilerplate. 339 tests pass.
  > Honest boundary: `enact()` governs the *action*. A pure model call that only produces text is not
  > an `Action` (it has no external consequence), so generation itself stays outside governance — by
  > design, consistent with "govern the consequences." Deliberately scoped: NOT a document toolkit
  > (chunking/loaders/structured-output belong to the LangChain/MCP tools Autarch *governs*, not *is*).

### Phase H — Governed recall memory: solve the agent-memory problems ✅ *done and verified*
Naive agent memory (a vector store bolted onto the model) fails in five well-known ways: it forgets
nothing (stale facts retrieved with false confidence), retrieves *similar* not *relevant*, grows
context linearly, loses institutional knowledge across agents, and can be silently **poisoned** to
taint every future session. Autarch treats long-term memory as a **governed substrate** with the same
guarantees as the action ledger — the differentiated move (nobody bolting on Pinecone has this).
- **`RecallMemory` + `MemoryEntry`** (`autarch/recall.py`) — SQLite, per-origin hash chain + Ed25519
  signing, mirroring `memory.py`. The **key design**: the signed assertion is *immutable*, while usage
  and supersession live in a *separate mutable overlay*, so memories can decay, be reinforced, and be
  revised **without ever breaking** `verify_chain`/`verify_provenance` (the same trick the redaction
  overlay uses for RTBF).
  - **Forgetting/updating** → `effective_strength` (salience decayed by age, lifted by use),
    `supersede` (belief revision — the old belief is retired, not blended), `decay_sweep`, `reinforce`.
  - **Noisy retrieval** → hybrid ranking: lexical overlap + optional semantic similarity + strength +
    **structural filters** (kind/scope/subject/tags), so *relevant* beats merely *similar*.
  - **Cost vs. loss** → `token_budget` greedy fill (context can't blow up) + `consolidate` that keeps
    the originals (`derived_from`), so summarization never destroys granular detail.
  - **Multi-agent** → scopes/namespaces + signed `export_rows`/`import_row` (grow-only union), so
    memories travel across agents/mesh with authorship intact.
  - **Poisoning** → provenance + integrity + `min_trust` quarantine of unverifiable memories + a
    governed `agent.remember(..., govern=True)` that routes the write through the capability kernel.
- **Optional semantic seam** (`autarch/intelligence/embedding.py`) — `EmbeddingProvider` ABC +
  `HashingEmbedder` (deterministic, offline, zero-dep) + `OllamaEmbedder` (real local embeddings,
  stdlib). Recall works with *no* embedder (lexical + structural + recency), so nothing is required.
- **Agent SDK** — `Agent(recall=, embedder=)`, `agent.remember(...)`, `agent.recall(...)`; the store is
  created lazily (zero overhead unless used) and shared across spawned sub-agents.
- tests `test_recall.py` (18, offline/deterministic; crypto via `importorskip`). `examples/memory.py`.
  All defaults off, so prior behavior is unchanged.
  > Honest boundary: `HashingEmbedder` is bag-of-words (no synonyms) — real semantics needs
  > `OllamaEmbedder`; lexical relevance is coverage-based, not BM25. The governance (signing, integrity,
  > trust-gating, decay) is the moat, not the embedding math.

### Phase I — Governed orchestration: safe master-child multi-agent ✅ *done and verified*
The supervisor/worker pattern (a master decomposes a request, spins up specialist children, runs them,
and synthesizes one answer) is everywhere — CrewAI, LangGraph-supervisor, AutoGen, Copilot Studio. They
orchestrate on **trust**: a spawned child can call any tool, spend unboundedly, and its output is
unattributable. Autarch already had the hard half (structural child containment via `spawn` +
delegation); Phase I adds the lifecycle **and keeps every child governed** — the only *safe* version.
- **The lifecycle** (`autarch/orchestration.py`) — `Orchestrator(master)` runs **decompose → provision
  → execute → synthesize**. Every child is created with `master.spawn(...)`, so attenuation, tool
  isolation, the shared budget, the signed ledger, and static guarantees all apply automatically.
  Children report only to the master; the master alone emits the single unified answer ("no direct
  messaging", enforced by construction).
- **Structured directives** — `Subtask` (description, requested grants, tools, `depends_on`,
  `specialist`, per-child `budget`); `Plan.waves()` topologically orders a dependency DAG (cycle-safe).
- **Planning & synthesis, both seams fail-closed** — `RulePlanner`/`ConcatSynthesizer` are deterministic
  and offline; `ModelPlanner`/`ModelSynthesizer` use a real model but **degrade to the deterministic
  fallback** if it's unreachable or returns unparseable JSON (never a crashed run).
- **Tool isolation** — new `Agent.spawn(adapters=...)`: a child receives only the adapters its subtask
  needs (deny-by-default — no tools means no adapters), *on top of* the already-attenuated grants.
- **Parallel fleets, safely** — `max_parallel > 1` runs an independent wave concurrently on a stdlib
  `ThreadPoolExecutor`; each parallel child gets its **own signed sub-chain** (a distinct origin) and
  per-thread SQLite connections, then merges into one ledger that still `verify_chain`s. `Budget` was
  made thread-safe for the shared pool.
- **Reusable specialists** — `Specialist` + `SpecialistRegistry.defaults()` (researcher / analyst /
  security-reviewer read-only, writer) provision consistent workers from a name.
- **Whole-tree guarantee gate** — `Orchestrator(guarantees=[...])` **proves** the invariants over the
  master *before spawning anything*; a failure raises `GovernanceError` (fail-closed). Because
  attenuation only narrows, a proof over the master covers every child.
- tests `test_orchestration.py` (27): decomposition, DAG waves, child-can't-exceed-master, tool
  isolation, model planner/synth + fail-closed, specialists, parallel + intact ledger, per-child
  sub-budget, guarantee gate. `examples/orchestration.py` (3 scenarios offline) + `examples/
  orchestration_live.py` (live Ollama). All defaults off; **373 tests pass**.
  > Honest boundary: under `max_parallel > 1` the budget is check-then-act, so a shared pool can be
  > overshot by the in-flight set — use `max_parallel = 1` for a strict ceiling. Adapter tool-isolation
  > is coarse (an adapter may serve several capabilities); the fine-grained control is the attenuated
  > grant, which the kernel enforces per action.
### Phase J \u2014 Faithful summarization: govern what a summary claims \u2705 *done and verified*
GenAI summaries fail in four well-documented ways: they invent facts, drop critical detail, overstate
what was done, and lose information when compressing long context. Standard metrics (ROUGE/BLEU) only
measure surface word overlap, so they miss exactly the failure that matters \u2014 factual consistency.
Autarch treats a summary as a governed action: it is *evaluated*, and the verdict is *signed into the
tamper-evident ledger*, so faithfulness is provable rather than promised.
- **`GroundednessEvaluator`** (`autarch/evaluation.py`) \u2014 precision / anti-hallucination. Splits the
  output into atomic claims and checks each against the source for content-word support **and** the
  presence of every number and named entity, so an invented figure (`$50k \u2192 $500k`) or a fabricated
  party is flagged; ``details['ungrounded']`` names the offending claims. Deterministic, dependency-free.
- **`CoverageEvaluator`** \u2014 recall / anti-oversummarization. Checks the source's critical points (an
  explicit list, or auto-extracted numbers + entities) survive in the output; ``details['missing']``
  names what was dropped. Groundedness + coverage are a matched precision/recall pair.
- **`extractive_summary` / `compress_history`** \u2014 structure-preserving compression that is *grounded by
  construction*: it selects verbatim source sentences (so it cannot hallucinate) and preferentially
  keeps fact-bearing sentences (numbers, entities, dates), retaining original order. `compress_history`
  keeps recent turns verbatim and extractively summarizes older ones \u2014 a safe replacement for the naive
  \u201cdump old context into a fresh prompt\u201d that makes long-running agents forget preferences and conclusions.
  Drop-in as `RecallMemory.consolidate(summarize=...)`.
- **Illusions of progress** are structurally defeated by the existing ledger: `RunResult.executed` and
  the signed why-record are ground truth, and orchestration's synthesizer reports `[done]`/`[blocked]`
  from the *actual* outcome \u2014 a summary cannot claim work the kernel never authorized.
- **Governed & provable** \u2014 all evaluators are `Evaluator`s, so `Agent.run(evaluate=...)` /
  `Agent.enact(evaluate=...)` score the real output and sign the faithfulness verdict into the ledger
  (`eval_score`/`eval_passed`/`evaluator`), and provenance still verifies. Compose with an LLM
  `RubricJudge` via `ConsensusEvaluator` for semantic paraphrase.
- tests `test_faithfulness.py` (17): invented number/entity flagged, partial score, dropped-detail
  detection, auto-extracted required points, grounded-by-construction extraction, bounded compression,
  history compression preserving facts+structure, consolidate integration, **signed-into-ledger**
  verdict (faithful passes, hallucinated fails, both recorded), consensus precision+recall, reflect-
  until-grounded. `examples/faithfulness.py` (all four modes, offline). **390 tests pass.**
  > Honest boundary: the deterministic checks are lexical (word/number/entity), so a correct *paraphrase*
  > that shares few surface words can score low \u2014 add an LLM `RubricJudge` (via `ConsensusEvaluator`) for
  > semantic entailment. Entity detection is a Title-Case heuristic, not a trained NER. The guarantee is
  > *provable evaluation of faithfulness*, not perfect natural-language understanding.

