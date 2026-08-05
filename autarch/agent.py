"""The Agent SDK — build a governed agentic system in a few lines.

This is the developer-facing surface. An Agent wires together a council, the
capability kernel, policy-as-code, precedent, adapters, and the why-memory, then
runs the full loop:

    intent -> deliberate (propose + critique across voices)
           -> authorize (kernel) -> evaluate (policy) -> recall (precedent)
           -> preside (ratify / overrule / send back) -> execute -> record why

Governance, audit, reversibility, and explainability come for free.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional, Union

from .adapters.base import Adapter
from .adapters.filesystem import FileSystemAdapter
from .contracts import (
    Action,
    ActionResult,
    CapabilityGrant,
    GateResult,
    HumanDecision,
    Intent,
    Verdict,
    WhyRecord,
    new_id,
)
from .council.deliberation import Council, Deliberation, Position
from .delegation import delegate
from .economy import Budget, BudgetDecision, CostModel, EconomicKernel
from .events import (
    ACTION_EXECUTED,
    BUDGET_CHECKED,
    DECISION_MADE,
    DELIBERATION_COMPLETE,
    EVALUATION_COMPLETE,
    GATE_CHECKED,
    POLICY_CHECKED,
    RUN_BLOCKED,
    RUN_COMPLETE,
    RUN_RESUMED,
    RUN_START,
    EventSink,
    NullSink,
    emit,
)
from .intelligence.base import ModelProvider
from .intelligence.factory import build_embedder, build_provider
from .kernel import CapabilityKernel
from .memory import WhyMemory
from .policy import Policy, PolicyDecision, PolicyEngine
from .precedent import Precedent, PrecedentStore
from .provenance import NodeIdentity
from .errors import CapabilityDenied, ValidationError
from .runlog import STATUS_BLOCKED, STATUS_COMPLETE, RunJournal

if TYPE_CHECKING:
    from .evaluation import Evaluator, Verdict
    from .guarantees import GuaranteeReport, Invariant
    from .intelligence.embedding import EmbeddingProvider
    from .rbac import AccessControl, Principal
    from .recall import RecallMemory


def capability(name: str, scope: Optional[dict] = None, limits: Optional[dict] = None) -> CapabilityGrant:
    """Declare a capability grant. Sugar for building a CapabilityGrant."""
    return CapabilityGrant(name=name, scope=scope or {}, limits=limits or {})


@dataclass
class RunResult:
    deliberation: Deliberation
    gate: GateResult
    human_decision: str
    executed: bool
    result: Optional[ActionResult]
    why_id: Optional[str]
    policy: Optional[PolicyDecision] = None
    precedent: Optional[Precedent] = None
    budget_decision: Optional[BudgetDecision] = None
    verdict: "Optional[Verdict]" = None

    @property
    def action(self) -> Optional[Action]:
        return self.deliberation.action


# A presiding function decides the outcome given the deliberation + gate. It may
# read deliberation.policy and deliberation.precedent, which are attached before
# the call. It returns a HumanDecision value: ratify / overrule / send_back.
PresideFn = Callable[[Deliberation, GateResult], str]

# Observer invoked once per deliberation round, before presiding (used by the CLI
# to render the council). Signature: (deliberation, gate, policy, precedent).
OnRoundFn = Callable[[Deliberation, GateResult, PolicyDecision, Optional[Precedent]], None]

# Sentinel for spawn() overrides: "inherit this from the parent" — distinct from an
# explicit None, which means "give the child its own (or none) of this".
_INHERIT = object()


class Agent:
    def __init__(
        self,
        intent: Union[str, Intent],
        council: Optional[List[Union[str, ModelProvider]]] = None,
        grants: Optional[List[CapabilityGrant]] = None,
        workspace: Union[str, Path] = "./sandbox",
        adapters: Optional[List[Adapter]] = None,
        memory: Optional[WhyMemory] = None,
        policies: Optional[List[Policy]] = None,
        precedents: Optional[PrecedentStore] = None,
        auto_preside: bool = True,
        preside_fn: Optional[PresideFn] = None,
        on_round: Optional[OnRoundFn] = None,
        max_rounds: int = 3,
        debate_rounds: int = 0,
        node_id: str = "local",
        identity: Optional[NodeIdentity] = None,
        sign: bool = True,
        budget: Optional[Union[Budget, dict]] = None,
        prices: Optional[dict] = None,
        parallel: bool = True,
        run_id: Optional[str] = None,
        events: Optional[EventSink] = None,
        journal: Optional[Union[RunJournal, bool]] = None,
        principal: Optional["Principal"] = None,
        access: Optional["AccessControl"] = None,
        recall: "Optional[RecallMemory]" = None,
        embedder: "Optional[EmbeddingProvider]" = None,
    ):
        self.intent = Intent(text=intent) if isinstance(intent, str) else intent
        self.providers = [build_provider(spec) for spec in (council or ["mock"])]

        # RBAC (governance of *who*): if a principal + access control are supplied,
        # the agent may only wield capabilities its roles permit; the rest are
        # dropped (deny by default), before the kernel ever sees them.
        requested_grants = list(grants or [])
        self.principal = principal
        self.access = access
        self.denied_grants: List[CapabilityGrant] = []
        if principal is not None and access is not None:
            requested_grants, self.denied_grants = access.authorize_grants(principal, requested_grants)
        self.grants = requested_grants
        self.kernel = CapabilityKernel(self.grants)
        self.workspace = Path(workspace)
        self.adapters = adapters if adapters is not None else [FileSystemAdapter(self.workspace)]

        self._by_capability = {}
        for adapter in self.adapters:
            for cap in adapter.capabilities():
                self._by_capability[cap] = adapter

        # Provenance: load or create this workspace's signing identity so every
        # action it records is cryptographically attributable (no-op without crypto).
        self.node_id = node_id
        if identity is None and sign:
            identity = NodeIdentity.load_or_create(self.workspace)
        self.identity = identity
        self.memory = memory or WhyMemory(
            self.workspace / ".autarch" / "why.db", node_id=node_id, identity=identity
        )
        # Long-term recall memory (governed, provenance-signed). Created lazily on
        # first use, so it adds zero overhead unless the agent actually remembers.
        # An embedder may be passed as an object or a string spec ("openai",
        # "ollama:nomic-embed-text", "hash:512") for meaning-aware recall.
        self.embedder = build_embedder(embedder) if isinstance(embedder, str) else embedder
        self.recall_memory = recall
        self.precedents = precedents or PrecedentStore(self.workspace / ".autarch" / "precedents.db")
        self.policy_engine = PolicyEngine(policies)
        self.auto_preside = auto_preside
        self.preside_fn = preside_fn
        self.on_round = on_round
        self.max_rounds = max(1, max_rounds)
        # Debate depth: 0 = single critique round (default); >0 = hold that many
        # rounds of cross-examination where voices respond to each other.
        self.debate_rounds = max(0, debate_rounds)

        # Economic kernel: an optional Budget caps what this agent (and the
        # sub-agents it spawns, which share the same budget) may spend.
        self.cost_model = CostModel(prices)
        if isinstance(budget, dict):
            budget = Budget(limits=dict(budget))
        self.budget = budget
        self.economic_kernel = EconomicKernel(budget, self.cost_model) if budget is not None else None

        advertised = [g.name for g in self.grants] or list(self._by_capability)
        # Collect parameter schemas from adapters so the council can tell models
        # the exact param names each capability expects.
        schemas: dict = {}
        for adapter in self.adapters:
            schemas.update(adapter.schema())
        self.parallel = parallel
        self.council = Council(
            self.providers, capabilities=advertised, schemas=schemas,
            max_workers=(8 if parallel else 1),
        )

        # Delegation bookkeeping (set when this agent was spawned by a parent).
        self.parent: Optional["Agent"] = None
        self.dropped_delegations: List[CapabilityGrant] = []

        # Reliability: structured event stream + optional durable run journal.
        self.run_id = run_id or new_id("run")
        self.events: EventSink = events or NullSink()
        if journal is True:
            journal = RunJournal(self.workspace / ".autarch" / "runs.db")
        self.journal: Optional[RunJournal] = journal if journal not in (None, False) else None

    def spawn(
        self,
        intent: Union[str, Intent],
        grants: Optional[List[CapabilityGrant]] = None,
        council: Optional[List[Union[str, ModelProvider]]] = None,
        preside_fn: Optional[PresideFn] = None,
        auto_preside: bool = True,
        adapters: Optional[List[Adapter]] = None,
        node_id=_INHERIT,
        memory=_INHERIT,
        recall=_INHERIT,
        journal=_INHERIT,
        budget=_INHERIT,
        precedents=_INHERIT,
    ) -> "Agent":
        """Create a sub-agent with strictly attenuated authority.

        The child's requested grants are attenuated under *this* agent's grants:
        each must be a subset of some parent grant, or it is dropped. The child can
        never exceed the authority delegated to it. By default the child shares this
        agent's memory, budget, and signing identity, so its actions are recorded
        and signed in the same ledger under delegated authority.

        ``adapters`` isolates the child's *tools*: pass a subset of this agent's
        adapters and the child can reach only those — on top of the (already
        narrower) attenuated grants. Omitted, the child inherits the full toolset.

        The ``node_id`` / ``memory`` / ``recall`` / ``journal`` / ``budget`` /
        ``precedents`` overrides let an orchestrator give a child its *own*
        storage (a distinct signed sub-chain and per-thread SQLite connections)
        or its *own* budget ceiling — the basis for safe parallel execution. Left
        as the inherit sentinel, each is shared with the parent as before.
        """
        requested = list(grants or [])
        granted, dropped = delegate(self.grants, requested)

        child = Agent(
            intent=intent,
            council=council if council is not None else self.providers,
            grants=granted,
            workspace=self.workspace,
            adapters=adapters if adapters is not None else self.adapters,
            memory=self.memory if memory is _INHERIT else memory,
            recall=self.recall_memory if recall is _INHERIT else recall,
            embedder=self.embedder,
            precedents=self.precedents if precedents is _INHERIT else precedents,
            policies=self.policy_engine.policies,
            identity=self.identity,
            node_id=self.node_id if node_id is _INHERIT else node_id,
            auto_preside=auto_preside,
            preside_fn=preside_fn,
            budget=self.budget if budget is _INHERIT else budget,
            events=self.events,  # sub-agents emit into the same event stream
            journal=self.journal if journal is _INHERIT else journal,
            principal=self.principal,  # the same actor governs the sub-agent
            access=self.access,
        )
        child.parent = self
        child.dropped_delegations = dropped
        return child

    # -- long-term memory (governed recall) -------------------------------
    def _ensure_recall(self) -> "RecallMemory":
        if self.recall_memory is None:
            from .recall import RecallMemory

            self.recall_memory = RecallMemory(
                self.workspace / ".autarch" / "recall.db",
                node_id=self.node_id,
                identity=self.identity,
                embedder=self.embedder,
            )
        return self.recall_memory

    def remember(self, content: str, *, govern: bool = False, **kwargs) -> str:
        """Store a long-term memory, signed and attributable to this agent.

        With ``govern=True`` the write passes through the capability kernel as a
        ``memory.write`` action, so what an agent may commit to memory is itself a
        governed, deny-by-default decision — the first line of defense against
        memory poisoning. Returns the new memory id.
        """
        if govern:
            gate = self.kernel.authorize(Action("memory.write", {"content": content}))
            if not gate.allowed:
                raise CapabilityDenied(
                    f"memory.write denied: {gate.reason}",
                    context={"capability": "memory.write"},
                )
        return self._ensure_recall().remember(content, **kwargs)

    def recall(self, query: str, **kwargs):
        """Retrieve relevant long-term memories (hybrid, trust-gated, bounded).

        See :meth:`autarch.recall.RecallMemory.recall` for the full ranking and
        filtering options (``k``, ``token_budget``, ``min_trust``, ``scope`` …).
        """
        return self._ensure_recall().recall(query, **kwargs)

    def guarantee(self, invariants: "List[Invariant]") -> "GuaranteeReport":
        """Statically prove safety invariants against this agent's authority.

        Reasons over the agent's grants and policies *before* running anything —
        a sound proof that holds regardless of what the model proposes.
        """
        from .guarantees import prove_guarantees

        return prove_guarantees(invariants, self.grants, self.policy_engine.policies)

    def run(self, evaluate: "Optional[Evaluator]" = None) -> RunResult:
        # Durable resume: if this run_id already completed, return its recorded
        # outcome WITHOUT re-executing the side effect (idempotency guarantee).
        if self.journal is not None:
            prior = self.journal.get(self.run_id)
            if prior is not None and prior.is_terminal and prior.why_id:
                cached = self._cached_result(prior.why_id)
                if cached is not None:
                    emit(self.events, RUN_RESUMED, self.run_id,
                         intent=self.intent.text, why_id=prior.why_id, status=prior.status)
                    return cached
            self.journal.start(self.run_id, self.intent.text)

        emit(self.events, RUN_START, self.run_id, intent=self.intent.text)

        feedback: Optional[str] = None
        exclude: set = set()
        round_index = 1
        deliberation: Deliberation
        gate: GateResult
        policy: PolicyDecision
        precedent: Optional[Precedent]
        decision: str

        while True:
            if self.debate_rounds > 0:
                deliberation = self.council.debate(
                    self.intent, feedback=feedback, exclude=exclude,
                    round_index=round_index, debate_rounds=self.debate_rounds,
                )
            else:
                deliberation = self.council.deliberate(
                    self.intent, feedback=feedback, exclude=exclude, round_index=round_index
                )
            motion = deliberation.motion
            emit(self.events, DELIBERATION_COMPLETE, self.run_id,
                 round=round_index,
                 motion=(motion.capability if motion else None),
                 recommendation=deliberation.recommendation,
                 voices=deliberation.voices)

            if motion is None:
                policy = PolicyDecision()
                precedent = None
                deliberation.policy = policy
                gate = GateResult(False, "no actionable proposal")
                if self.on_round is not None:
                    self.on_round(deliberation, gate, policy, precedent)
                why_id = self._record(deliberation, gate, HumanDecision.AUTO.value, False, None, policy, None, {})
                if self.journal is not None:
                    self.journal.record_step(self.run_id, "complete", STATUS_BLOCKED, why_id=why_id)
                emit(self.events, RUN_BLOCKED, self.run_id, reason="no actionable proposal", why_id=why_id)
                return RunResult(deliberation, gate, HumanDecision.AUTO.value, False, None, why_id, policy, None)

            # Canonicalize params via the responsible adapter BEFORE gating, so the
            # kernel, the adapter, and the audit record all see one consistent shape
            # even when a real model used synonym parameter names.
            adapter = self._by_capability.get(motion.capability)
            if adapter is not None:
                motion.params = adapter.normalize_params(motion.capability, motion.params)

            gate = self.kernel.authorize(motion)
            policy = self.policy_engine.evaluate(motion)
            precedent = self.precedents.lookup(motion.capability)
            deliberation.policy = policy
            deliberation.precedent = precedent
            emit(self.events, GATE_CHECKED, self.run_id,
                 capability=motion.capability, allowed=gate.allowed, reason=gate.reason)
            emit(self.events, POLICY_CHECKED, self.run_id,
                 effect=policy.effect, note=policy.note())

            if self.on_round is not None:
                self.on_round(deliberation, gate, policy, precedent)

            decision = self._decide(deliberation, gate, policy, precedent)

            if decision == HumanDecision.SEND_BACK.value and round_index < self.max_rounds:
                feedback = deliberation.critique.rationale or "Reconsider and find a safer approach."
                exclude.add(motion.capability)
                round_index += 1
                continue
            break

        if self.journal is not None:
            self.journal.record_step(self.run_id, "deliberated", data={"capability": motion.capability})

        # A send-back that was never resolved into ratify dies without enactment.
        if decision == HumanDecision.SEND_BACK.value:
            decision = HumanDecision.OVERRULE.value

        # Economic kernel: even an allowed, ratified action is refused if it would
        # bust the budget. The estimate is recorded either way.
        budget_decision: Optional[BudgetDecision] = None
        estimate: dict = {}
        if self.economic_kernel is not None:
            budget_decision = self.economic_kernel.authorize(motion)
            estimate = budget_decision.estimate
            emit(self.events, BUDGET_CHECKED, self.run_id,
                 ok=budget_decision.ok, reason=budget_decision.reason, estimate=estimate)

        emit(self.events, DECISION_MADE, self.run_id, decision=decision)
        if self.journal is not None:
            self.journal.record_step(self.run_id, "decided", data={"decision": decision})

        blocked = (not gate.allowed) or policy.denies or (budget_decision is not None and not budget_decision.ok)
        executed, result = False, None
        if decision == HumanDecision.RATIFY.value and not blocked:
            adapter = self._by_capability.get(motion.capability)
            if adapter is None:
                result = ActionResult(False, error=f"no adapter for '{motion.capability}'")
            else:
                result = adapter.execute(motion)
                executed = result.ok
                # Charge the budget only for an action that actually ran.
                if executed and self.economic_kernel is not None:
                    self.economic_kernel.charge(estimate)
        elif (
            decision == HumanDecision.RATIFY.value
            and budget_decision is not None
            and not budget_decision.ok
            and gate.allowed
            and not policy.denies
        ):
            # Surface the economic refusal as the reason it didn't run.
            result = ActionResult(False, error=budget_decision.reason)

        emit(self.events, ACTION_EXECUTED, self.run_id,
             capability=motion.capability, executed=executed,
             output=(result.output if result else None),
             error=(result.error if result else None))

        # Governed evaluation: score the executed output with the supplied judge.
        # The verdict is recorded in the signed why-memory, so an output's quality
        # is itself provable. (Scoring only; never re-executes the side effect.)
        verdict = None
        if evaluate is not None and executed and result is not None and result.ok:
            try:
                verdict = evaluate.evaluate(result.output)
            except Exception:
                verdict = None
            if verdict is not None:
                emit(self.events, EVALUATION_COMPLETE, self.run_id,
                     evaluator=verdict.evaluator, score=verdict.score, passed=verdict.passed)

        # Remember this ruling — but only when it reflects a genuine presiding
        # judgment by a human/custom presider over an action that was actually
        # enactable. Mechanical outcomes (auto-presiding, gate denials, policy
        # denials) must never harden into precedent.
        genuine_ruling = (
            self.preside_fn is not None
            and gate.allowed
            and not policy.denies
            and decision in (HumanDecision.RATIFY.value, HumanDecision.OVERRULE.value)
        )
        if genuine_ruling:
            self.precedents.record(motion.capability, decision, self.intent.text)

        why_id = self._record(deliberation, gate, decision, executed, result, policy, precedent, estimate, verdict)

        if self.journal is not None:
            # Persist the terminal state WITH the why_id, so a resume returns this
            # outcome instead of re-executing the action.
            terminal = STATUS_COMPLETE if executed else STATUS_BLOCKED
            self.journal.record_step(self.run_id, "complete", terminal, why_id=why_id)
        emit(self.events, (RUN_COMPLETE if executed else RUN_BLOCKED), self.run_id,
             executed=executed, decision=decision, why_id=why_id)

        return RunResult(deliberation, gate, decision, executed, result, why_id, policy, precedent, budget_decision, verdict)

    def enact(
        self,
        action: Union[Action, str],
        params: Optional[dict] = None,
        *,
        actor: str = "caller",
        evaluate: "Optional[Evaluator]" = None,
    ) -> RunResult:
        """Govern, execute, and SIGN a *known* action — without deliberation.

        Use this when you already know the action: a deterministic workflow step,
        a decision handed over by an external planner, a directly invoked governed
        tool, or a replay. The action still passes the **full deterministic
        pipeline** — the capability kernel, policy, and budget all dispose — and
        the outcome is recorded in the same signed, tamper-evident ledger as
        ``run()``. Only the *intelligence* half (the council) is skipped.

        Calling ``enact`` is itself the act of presiding: the caller commands the
        action, which satisfies a ``require_ratify`` policy. The kernel can still
        deny it (no grant), policy can still ``deny`` it, and the budget can still
        refuse it — AI proposes, the kernel disposes, even when *you* are the one
        proposing.

            agent.enact("doc.read", {"path": "report.pdf"})
            agent.enact(Action("file.write", {"path": "x", "content": "y"}))
        """
        if isinstance(action, str):
            action = Action(capability=action, params=dict(params or {}),
                            rationale="directly enacted")
        elif isinstance(action, Action):
            if params:
                action = Action(action.capability, {**action.params, **params}, action.rationale)
        else:
            raise ValidationError(
                "enact() expects an Action or a capability name (str)",
                context={"got": type(action).__name__},
            )

        # Durable-resume parity with run(): a completed run_id returns its prior
        # outcome without re-executing (idempotency for known actions too).
        if self.journal is not None:
            prior = self.journal.get(self.run_id)
            if prior is not None and prior.is_terminal and prior.why_id:
                cached = self._cached_result(prior.why_id)
                if cached is not None:
                    emit(self.events, RUN_RESUMED, self.run_id,
                         intent=self.intent.text, why_id=prior.why_id, status=prior.status)
                    return cached
            self.journal.start(self.run_id, self.intent.text)

        emit(self.events, RUN_START, self.run_id, intent=self.intent.text)

        # Canonicalize params via the responsible adapter BEFORE gating, so the
        # kernel, the adapter, and the audit record all see one consistent shape.
        adapter = self._by_capability.get(action.capability)
        if adapter is not None:
            action.params = adapter.normalize_params(action.capability, action.params)

        # Synthesize an honest "no deliberation" record: the caller is the
        # proposer; there was no council review (rounds=0, empty tally).
        proposer = Position(actor, "proposer", Verdict.PROPOSE.value,
                            action.rationale or "directly enacted (no deliberation)", action)
        critic = Position("—", "critic", Verdict.APPROVE.value,
                          "direct enactment by caller; no council review")
        deliberation = Deliberation(
            intent=self.intent, proposals=[proposer], critiques=[critic],
            motion=action, recommendation=Verdict.APPROVE.value, tally={},
            proposal_disagreement=False, rounds=0,
        )

        gate = self.kernel.authorize(action)
        policy = self.policy_engine.evaluate(action)
        deliberation.policy = policy
        emit(self.events, GATE_CHECKED, self.run_id,
             capability=action.capability, allowed=gate.allowed, reason=gate.reason)
        emit(self.events, POLICY_CHECKED, self.run_id, effect=policy.effect, note=policy.note())

        # The caller commands the action: this is the human ratification. It still
        # only runs if the kernel, policy, and budget all allow it.
        decision = HumanDecision.RATIFY.value

        budget_decision: Optional[BudgetDecision] = None
        estimate: dict = {}
        if self.economic_kernel is not None:
            budget_decision = self.economic_kernel.authorize(action)
            estimate = budget_decision.estimate
            emit(self.events, BUDGET_CHECKED, self.run_id,
                 ok=budget_decision.ok, reason=budget_decision.reason, estimate=estimate)

        emit(self.events, DECISION_MADE, self.run_id, decision=decision)

        blocked = (not gate.allowed) or policy.denies or (budget_decision is not None and not budget_decision.ok)
        executed, result = False, None
        if not blocked:
            if adapter is None:
                result = ActionResult(False, error=f"no adapter for '{action.capability}'")
            else:
                result = adapter.execute(action)
                executed = result.ok
                if executed and self.economic_kernel is not None:
                    self.economic_kernel.charge(estimate)
        elif not gate.allowed:
            result = ActionResult(False, error=gate.reason)
        elif policy.denies:
            result = ActionResult(False, error=policy.note() or "denied by policy")
        elif budget_decision is not None and not budget_decision.ok:
            result = ActionResult(False, error=budget_decision.reason)

        emit(self.events, ACTION_EXECUTED, self.run_id,
             capability=action.capability, executed=executed,
             output=(result.output if result else None),
             error=(result.error if result else None))

        # Governed evaluation parity: score the executed output and sign the
        # verdict into the ledger (never re-executes the side effect).
        verdict = None
        if evaluate is not None and executed and result is not None and result.ok:
            try:
                verdict = evaluate.evaluate(result.output)
            except Exception:
                verdict = None
            if verdict is not None:
                emit(self.events, EVALUATION_COMPLETE, self.run_id,
                     evaluator=verdict.evaluator, score=verdict.score, passed=verdict.passed)

        why_id = self._record(deliberation, gate, decision, executed, result, policy, None, estimate, verdict)

        if self.journal is not None:
            terminal = STATUS_COMPLETE if executed else STATUS_BLOCKED
            self.journal.record_step(self.run_id, "complete", terminal, why_id=why_id)
        emit(self.events, (RUN_COMPLETE if executed else RUN_BLOCKED), self.run_id,
             executed=executed, decision=decision, why_id=why_id)

        return RunResult(deliberation, gate, decision, executed, result, why_id, policy, None, budget_decision, verdict)

    def resume(self, run_id: str) -> RunResult:
        """Re-enter a run by id. If it already completed, its recorded outcome is
        returned without re-executing; otherwise it runs to completion now."""
        self.run_id = run_id
        return self.run()

    def _cached_result(self, why_id: str) -> Optional[RunResult]:
        """Reconstruct a RunResult from a persisted why-record (for resume)."""
        record = self.memory.get(why_id)
        if record is None:
            return None
        motion = Action(record.capability, dict(record.params), record.rationale)
        proposal = Position(record.proposer, "proposer", Verdict.PROPOSE.value, record.rationale, motion)
        critique = Position(record.challenger, "critic", record.critique_verdict, record.critique_reasons, motion)
        deliberation = Deliberation(
            intent=self.intent,
            proposals=[proposal],
            critiques=[critique],
            motion=motion,
            recommendation=record.recommendation or record.critique_verdict,
            tally=dict(record.tally or {}),
            proposal_disagreement=record.proposal_disagreement,
            rounds=record.rounds or 1,
        )
        gate = GateResult(record.gate_allowed, record.gate_reason)
        result = ActionResult(
            ok=bool(record.result_ok),
            output=record.result_output,
            undo=record.undo,
            error=record.result_error,
        )
        return RunResult(
            deliberation, gate, record.human_decision, record.executed,
            result, record.id, None, None, None,
        )

    # -- presiding --------------------------------------------------------
    def _decide(self, deliberation, gate, policy, precedent) -> str:
        if self.preside_fn is not None:
            return self.preside_fn(deliberation, gate)
        if self.auto_preside:
            if not gate.allowed:
                return HumanDecision.OVERRULE.value
            if policy.denies:
                return HumanDecision.OVERRULE.value
            # A standing overrule precedent is applied automatically.
            if precedent is not None and precedent.decision == HumanDecision.OVERRULE.value:
                return HumanDecision.OVERRULE.value
            # Auto-presiding may never ratify what policy says needs a human.
            if policy.requires_ratify:
                return HumanDecision.OVERRULE.value
            if deliberation.recommendation == Verdict.APPROVE.value and not deliberation.has_disagreement:
                return HumanDecision.RATIFY.value
            return HumanDecision.OVERRULE.value
        return HumanDecision.PENDING.value

    # -- recording --------------------------------------------------------
    def _record(self, deliberation, gate, decision, executed, result, policy, precedent, cost=None, verdict=None) -> str:
        action = deliberation.action
        record = WhyRecord(
            intent_text=self.intent.text,
            capability=action.capability if action else "",
            params=action.params if action else {},
            rationale=deliberation.proposal.rationale,
            proposer=deliberation.proposal.voice,
            challenger=deliberation.critique.voice,
            critique_verdict=deliberation.critique.verdict,
            critique_reasons=deliberation.critique.rationale,
            gate_allowed=gate.allowed,
            gate_reason=gate.reason,
            human_decision=decision,
            executed=executed,
            result_ok=(result.ok if result else None),
            result_output=(result.output if result else None),
            result_error=(result.error if result else None),
            undo=(result.undo if result else None),
            recommendation=deliberation.recommendation,
            voices=deliberation.voices,
            tally=dict(deliberation.tally),
            proposal_disagreement=deliberation.proposal_disagreement,
            rounds=deliberation.rounds,
            precedent_note=(precedent.note() if precedent else ""),
            policy_note=(policy.note() if policy else ""),
            cost=dict(cost or {}),
            eval_score=(verdict.score if verdict else None),
            eval_passed=(verdict.passed if verdict else None),
            eval_reasons=(verdict.reasons if verdict else ""),
            evaluator=(verdict.evaluator if verdict else ""),
        )
        return self.memory.record(record)
