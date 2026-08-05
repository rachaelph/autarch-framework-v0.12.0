# Autarch

**You don't use AI. You preside over it.**

A council of minds deliberates. You rule. Nothing acts without your word. Everything it does, it can prove.

---

## 1. What Autarch is

Autarch is an **AI-native operating layer**. Where a traditional OS abstracts *hardware for programs*, Autarch abstracts *intent for outcomes*.

Its core is not a hardware kernel — it is a **Capability Kernel**: a deterministic gate that governs every action an intelligence is allowed to take. Intelligence is unlimited and swappable; **consequences are governed**.

The headline experience: instead of trusting one oracle model, you watch a **council of models** (yours, GPT, Claude, local) propose, challenge, and veto each other in the open — and **you preside**, ratifying or overruling. Every ruling is remembered. Nothing acts without your grant.

## 2. The one rule

> **AI is the orchestrator, never the kernel. The model proposes; the deterministic kernel disposes.**

Anything that must never be wrong (files, money, identity, device control) passes through a capability gate the AI cannot bypass. This single discipline is what makes "AI OS" a real system instead of a crash-prone demo.

## 3. The architecture — four pillars, one chamber

| Pillar | Role in the chamber | What it does |
|---|---|---|
| **Capability Kernel** | The floor | No councilor acts without a ratified, least-privilege, revocable, audited grant. Deterministic. |
| **Intelligence Bus** | The seats | Any model joins as a voice. Your model is primary; GPT/Claude/local are hot-swappable guests. |
| **Why-Memory** | The record | Every deliberation, decision, and your ruling is remembered with evidence + approving policy. |
| **Provable Trust** | The constitution | The council literally cannot defy the autarch. Every action can be challenged with "prove it." |

```
            ┌─────────────────────────────────────────┐
   intent → │  CAPABILITY KERNEL (deterministic gate)  │
            │  grant · bound · revoke · audit · explain │
            └───────┬───────────┬───────────┬──────────┘
                    │           │           │
            Intelligence    Capability   Substrate
                Bus            Bus          Bus
             (the seats)   (adapters)   (Linux/Android
            own·GPT·Claude  tools·devices  /Windows now,
             ·local           ·services   microkernel later)
                    │
               Why-Memory (the record)
```

## 4. What makes it distinct (the moat)

- **Model-autarch** — your brain primary, market brains interchangeable. Never hostage to a vendor.
- **Capability-governed** — nothing acts without a provable license to act.
- **Self-explaining** — ask the OS to justify any action, and it can.
- **Governance of plurality** — the unsolved problem of the next decade: when you have ten capable models, *whose judgment do you trust, and how do you stay in command?* Autarch is built for that world. The big labs structurally cannot lead here — their business is "trust our one model."

## 5. Honest prior art vs. our novelty

**Not new (proven primitives we build on — by design, lower risk):**
capability-based security (KeyKOS, seL4, object-capability model), model routing (LiteLLM, OpenRouter), tool adapters (MCP, function-calling), guardrails (NeMo, Guardrails AI), provenance (W3C PROV), local-first sync (CRDTs), AI computer-control (Computer Use).

**Genuinely new (synthesis + stance + experience):**
1. Capability-security as the *kernel of an AI runtime*, not a bolt-on.
2. Model-autarch + capability-governed + self-explaining fused into one runtime.
3. Positioned *below* orchestration frameworks (absorb-then-replace), not beside them.
4. **The felt experience**: a council you preside over — visible deliberation, human as autarch.

> Our novelty is **synthesis, stance, and a new feeling** — not invented primitives. That is how Unix, the iPhone, and Kubernetes won: none invented their core parts.

## 6. Relationship to LangChain / Microsoft Agent Framework

They **orchestrate**; Autarch **governs**. You can build orchestration on top of governance; you cannot retrofit governance under orchestration.

- **Absorb first:** wrap them as adapters on the Capability Bus. Existing agents instantly become governed, audited, model-autarch. We inherit their ecosystem.
- **Replace later:** once intelligence flows through our kernel, the native governed planner makes them unnecessary for anyone who cares about safety, audit, or vendor independence.

## 7. Day-1 reality

- Works with **a single model + a "challenger" critic pass** — the council is the ceiling, not the floor.
- Grows more magical as you add minds.
- First real capability: **local-machine control** (files/apps/system actions) — tangible and demoable.
- The wow must be **fast, legible, decisive** — a verdict, not a debate club.
