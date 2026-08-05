"""Governed Orchestration — a Master agent that plans, provisions safe Child
agents on the fly, runs them, and synthesizes one unified answer.

The multi-agent *supervisor / worker* pattern is everywhere now: a master
decomposes a request, spins up specialist children, runs them, and consolidates
their findings. Autarch adds the piece the other frameworks leave out —
**governance**. Because every child is created with :meth:`Agent.spawn`:

* it is **capability-attenuated** — a child can never exceed the authority of its
  master (enforced structurally in :mod:`autarch.delegation`, not by trust);
* it is **tool-isolated** — it receives only the adapters its subtask needs;
* it draws from the **same budget pool** — no runaway fan-out of spend;
* every action it takes is **signed and recorded** in the one shared ledger, so
  the whole orchestration is auditable and attributable;
* the entire tree can be **statically proven** safe before it runs
  (:meth:`Orchestrator.guarantee`).

Children report results only to the master (never to the end user); the master
alone emits the single unified response — the "no direct messaging" best
practice, enforced by construction.

This module is pure-Python and runs fully offline with the deterministic
:class:`RulePlanner` and :class:`ConcatSynthesizer`. Model-backed planning and
synthesis (Phase 2) plug into the same :class:`Planner` / :class:`Synthesizer`
seams without touching the governed execution core.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

from .contracts import CapabilityGrant, new_id
from .errors import GovernanceError
from .events import (
    CHILD_COMPLETE,
    CHILD_SPAWNED,
    ORCHESTRATION_DECOMPOSED,
    ORCHESTRATION_SYNTHESIZED,
    emit,
)
from .memory import WhyMemory
from .precedent import PrecedentStore
from .recall import RecallMemory
from .util import extract_json

if TYPE_CHECKING:  # avoid runtime coupling; provided by the caller
    from .adapters.base import Adapter
    from .agent import Agent, RunResult
    from .guarantees import GuaranteeReport, Invariant


# Verb -> capability inference. Kept identical to the mock provider's mapping so a
# deterministic child proposes exactly the capability its subtask was granted
# (the offline happy path stays aligned end to end).
_VERB_CAP = [
    (re.compile(r"\b(delete|remove|erase|rm)\b", re.I), "file.delete"),
    (re.compile(r"\b(move|rename|mv)\b", re.I), "file.move"),
    (re.compile(r"\b(create|write|make|save|add|new|put)\b", re.I), "file.write"),
    (re.compile(r"\b(read|show|open|cat|display|view|print)\b", re.I), "file.read"),
]
_DEFAULT_CAP = "file.read"

# Natural-language connectives that separate independent subtasks.
_CONNECTIVE = re.compile(r"\s*(?:;|,| and | then | after that | & )\s*", re.I)


@dataclass
class Subtask:
    """A structured delegation directive — what a child is asked to do, and with
    exactly what authority and tools.

    ``grants`` and ``tools`` are *requests*: they are attenuated under the master
    at provision time (deny-by-default), so a subtask can never manufacture
    authority the master does not hold.
    """

    description: str
    grants: List[CapabilityGrant] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)          # capability names the child may use
    depends_on: List[str] = field(default_factory=list)     # ids of prerequisite subtasks
    specialist: str = ""                                     # optional template name
    budget: Optional[dict] = None                           # optional per-child budget ceiling
    id: str = field(default_factory=lambda: new_id("task"))


@dataclass
class Plan:
    """An ordered set of subtasks decomposed from one intent, possibly a DAG."""

    intent: str
    subtasks: List[Subtask] = field(default_factory=list)

    def by_id(self) -> Dict[str, Subtask]:
        return {t.id: t for t in self.subtasks}

    def waves(self) -> List[List[Subtask]]:
        """Group subtasks into dependency-respecting waves (Kahn's algorithm).

        Each wave may run in parallel; waves run in order. A dependency cycle is
        broken best-effort by appending the remainder as a final wave, so a bad
        plan degrades instead of hanging.
        """
        remaining = {t.id: set(t.depends_on) for t in self.subtasks}
        index = self.by_id()
        done: set = set()
        waves: List[List[Subtask]] = []
        while remaining:
            ready = [tid for tid, deps in remaining.items() if deps <= done]
            if not ready:  # cycle / dangling dependency — flush the rest
                waves.append([index[tid] for tid in remaining])
                break
            ready.sort(key=lambda tid: [t.id for t in self.subtasks].index(tid))
            waves.append([index[tid] for tid in ready])
            done.update(ready)
            for tid in ready:
                remaining.pop(tid)
        return waves


# -- planners -----------------------------------------------------------------
class Planner:
    """Turns an intent into a :class:`Plan`. The 'reasoning' half of the master."""

    def decompose(self, intent: str, available: Optional[List[str]] = None) -> Plan:  # pragma: no cover - interface
        raise NotImplementedError


class RulePlanner(Planner):
    """Deterministic, offline decomposition — no model, no network.

    Splits an intent on natural connectives ("and", "then", commas, …) and infers
    each clause's capability from its verb. Perfect for tests and for a
    predictable baseline; swap in :class:`~autarch.orchestration.ModelPlanner`
    (Phase 2) for genuine reasoning.
    """

    def decompose(self, intent: str, available: Optional[List[str]] = None) -> Plan:
        clauses = [c.strip() for c in _CONNECTIVE.split(intent) if c and c.strip()]
        if not clauses:
            clauses = [intent.strip()]
        subtasks: List[Subtask] = []
        for clause in clauses:
            cap = self._infer(clause)
            subtasks.append(
                Subtask(
                    description=clause,
                    grants=[CapabilityGrant(name=cap)],
                    tools=[cap],
                )
            )
        return Plan(intent=intent, subtasks=subtasks)

    @staticmethod
    def _infer(clause: str) -> str:
        for pattern, cap in _VERB_CAP:
            if pattern.search(clause):
                return cap
        return _DEFAULT_CAP


_SYSTEM_PLANNER = (
    "You are a supervisor that breaks a goal into the fewest concrete subtasks. "
    "Respond with ONLY a single JSON object — no prose, no markdown fences."
)
_PLANNER_TEMPLATE = """ROLE: PLANNER
Break the GOAL into the fewest independent subtasks that accomplish it. For each
subtask, name the single capability it needs, chosen from the ALLOWED list.
ALLOWED: {capabilities}
GOAL: {intent}
Respond with ONLY a JSON object:
{{"subtasks": [{{"description": "<what to do>", "capability": "<one of allowed>"}}]}}
"""


class ModelPlanner(Planner):
    """Decompose an intent with a real model — **fail-closed** to a safe fallback.

    The model is asked for a JSON list of subtasks. If it is unreachable, returns
    nothing parseable, or proposes an empty plan, we degrade to the deterministic
    ``fallback`` (a :class:`RulePlanner` by default) instead of failing the run.
    The capability a subtask requests is only that — a *request*: it is still
    attenuated under the master at provision time, so a hallucinated capability
    cannot manufacture authority.
    """

    def __init__(self, provider, fallback: Optional[Planner] = None, max_subtasks: int = 8):
        if isinstance(provider, str):
            from .intelligence.factory import build_provider

            provider = build_provider(provider)
        self.provider = provider
        self.fallback = fallback or RulePlanner()
        self.max_subtasks = max(1, max_subtasks)

    def decompose(self, intent: str, available: Optional[List[str]] = None) -> Plan:
        caps = ", ".join(available) if available else "(any declared capability)"
        prompt = _PLANNER_TEMPLATE.format(capabilities=caps, intent=intent)
        try:
            raw = self.provider.complete(prompt, system=_SYSTEM_PLANNER)
        except Exception:
            return self.fallback.decompose(intent, available)
        subtasks = self._parse(extract_json(raw))
        if not subtasks:
            return self.fallback.decompose(intent, available)  # fail-closed
        return Plan(intent=intent, subtasks=subtasks)

    def _parse(self, data) -> List[Subtask]:
        if not isinstance(data, dict):
            return []
        raw_tasks = data.get("subtasks") or data.get("tasks") or []
        if not isinstance(raw_tasks, list):
            return []
        subtasks: List[Subtask] = []
        for item in raw_tasks[: self.max_subtasks]:
            if isinstance(item, str):
                desc, cap = item, RulePlanner._infer(item)
            elif isinstance(item, dict):
                desc = item.get("description") or item.get("task") or item.get("subtask") or ""
                cap = item.get("capability") or item.get("tool") or ""
            else:
                continue
            desc = str(desc).strip()
            if not desc:
                continue
            cap = str(cap).strip() or RulePlanner._infer(desc)
            subtasks.append(
                Subtask(description=desc, grants=[CapabilityGrant(name=cap)], tools=[cap])
            )
        return subtasks


# -- synthesizers -------------------------------------------------------------
class Synthesizer:
    """Consolidates child results into one answer. The 'handoff' half."""

    def synthesize(self, intent: str, results: List["ChildResult"]) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class ConcatSynthesizer(Synthesizer):
    """Deterministic, offline synthesis: a structured digest of every child.

    Reports each subtask's governed outcome (done / blocked) and output, so the
    master's single response reflects exactly what the children did — including
    what governance *stopped* them from doing.
    """

    def synthesize(self, intent: str, results: List["ChildResult"]) -> str:
        done = sum(1 for r in results if r.executed)
        lines = [f"Intent: {intent}", f"Subtasks completed: {done}/{len(results)}", ""]
        for i, child in enumerate(results, 1):
            status = "[done]" if child.executed else "[blocked]"
            lines.append(f"{status} {i}. {child.subtask.description}")
            detail = child.summary()
            if detail:
                lines.append(f"        -> {detail}")
        return "\n".join(lines)


_SYSTEM_SYNTH = (
    "You consolidate worker results into one clear answer for the user. Respond "
    "with ONLY a single JSON object — no prose, no markdown fences."
)
_SYNTH_TEMPLATE = """ROLE: SYNTHESIZER
The GOAL and the workers' FINDINGS are below. Write ONE unified answer to the
GOAL, mentioning anything governance blocked.
GOAL: {intent}
FINDINGS:
{findings}
Respond with ONLY a JSON object:
{{"summary": "<the unified answer>"}}
"""


class ModelSynthesizer(Synthesizer):
    """Consolidate child results with a real model — **fail-closed** to concat.

    Feeds the model a deterministic digest of what every child did (so it cannot
    invent outcomes) and asks for one unified answer. If the model is unreachable
    or unparseable, falls back to the structured :class:`ConcatSynthesizer` rather
    than dropping the response.
    """

    def __init__(self, provider, fallback: Optional[Synthesizer] = None):
        if isinstance(provider, str):
            from .intelligence.factory import build_provider

            provider = build_provider(provider)
        self.provider = provider
        self.fallback = fallback or ConcatSynthesizer()

    def synthesize(self, intent: str, results: List["ChildResult"]) -> str:
        findings = ConcatSynthesizer().synthesize(intent, results)
        prompt = _SYNTH_TEMPLATE.format(intent=intent, findings=findings)
        try:
            raw = self.provider.complete(prompt, system=_SYSTEM_SYNTH)
        except Exception:
            return self.fallback.synthesize(intent, results)
        data = extract_json(raw)
        if isinstance(data, dict) and str(data.get("summary", "")).strip():
            return str(data["summary"]).strip()
        text = (raw or "").strip()
        if text and not text.startswith("{"):
            return text  # the model answered in prose
        return self.fallback.synthesize(intent, results)  # fail-closed


# -- results ------------------------------------------------------------------
@dataclass
class ChildResult:
    """One child's subtask paired with its governed :class:`RunResult`."""

    subtask: Subtask
    result: "RunResult"
    dropped_grants: List[CapabilityGrant] = field(default_factory=list)

    @property
    def executed(self) -> bool:
        return bool(self.result.executed)

    @property
    def output(self):
        return self.result.result.output if self.result.result else None

    def summary(self) -> str:
        rr = self.result
        if rr.result is not None:
            if rr.result.ok:
                out = rr.result.output
                return "" if out is None else str(out)
            return str(rr.result.error or "blocked by governance")
        # No execution result — say honestly *why* it was blocked.
        if rr.action is None:
            return "no actionable proposal"
        if not rr.gate.allowed:
            return f"denied by kernel: {rr.gate.reason}"
        if rr.policy is not None and rr.policy.denies:
            return "denied by policy"
        if rr.budget_decision is not None and not rr.budget_decision.ok:
            return str(rr.budget_decision.reason)
        return "blocked by governance"


@dataclass
class OrchestrationResult:
    """The master's consolidated outcome for one intent."""

    plan: Plan
    children: List[ChildResult]
    synthesis: str

    @property
    def why_ids(self) -> List[str]:
        return [c.result.why_id for c in self.children if c.result.why_id]

    @property
    def executed_count(self) -> int:
        return sum(1 for c in self.children if c.executed)

    def results_by_id(self) -> Dict[str, "RunResult"]:
        return {c.subtask.id: c.result for c in self.children}


# -- specialists (reusable child templates) -----------------------------------
@dataclass
class Specialist:
    """A reusable child template: default authority, tools, and a directive.

    A registry of specialists lets a planner name *what kind* of worker a subtask
    needs ("researcher", "writer", "security-reviewer") and have its grants, tools
    and instructions filled in consistently — without re-specifying them per task.
    A subtask's own explicit grants/tools always take precedence over the template.
    """

    name: str
    grants: List[CapabilityGrant] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    directive: str = ""
    council: Optional[list] = None


class SpecialistRegistry:
    """A named collection of :class:`Specialist` templates."""

    def __init__(self, specialists: Optional[List[Specialist]] = None):
        self._by_name: Dict[str, Specialist] = {}
        for spec in specialists or []:
            self.register(spec)

    def register(self, specialist: Specialist) -> "SpecialistRegistry":
        self._by_name[specialist.name] = specialist
        return self

    def get(self, name: str) -> Optional[Specialist]:
        return self._by_name.get(name)

    def names(self) -> List[str]:
        return list(self._by_name)

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    @classmethod
    def defaults(cls) -> "SpecialistRegistry":
        """A conservative starter fleet: only the ``writer`` may write.

        Deliberately read-only by default — a safe baseline that callers extend
        with their own specialists for richer teams.
        """
        read = ["file.read"]
        return cls([
            Specialist("researcher", grants=[CapabilityGrant("file.read")], tools=list(read),
                       directive="gather and read source material only"),
            Specialist("analyst", grants=[CapabilityGrant("file.read")], tools=list(read),
                       directive="analyze the material and report findings"),
            Specialist("security-reviewer", grants=[CapabilityGrant("file.read")], tools=list(read),
                       directive="review for risks; do not modify anything"),
            Specialist("writer", grants=[CapabilityGrant("file.write")], tools=["file.write"],
                       directive="produce the written output"),
        ])


# -- the master ---------------------------------------------------------------
class Orchestrator:
    """A master (supervisor) agent that governs a fleet of child workers.

    Wraps a fully-configured :class:`Agent` (the master) and runs the lifecycle:
    **decompose -> provision -> execute -> synthesize**. Every child is spawned
    from the master, so attenuation, tool isolation, the shared budget, the signed
    ledger, and static guarantees all apply automatically.

    * ``registry`` — resolve a subtask's ``specialist`` into default grants/tools.
    * ``max_parallel`` — run independent subtasks (within a dependency wave)
      concurrently; each parallel child gets its *own* signed sub-chain and
      per-thread storage, then merges into the one auditable ledger.
    * ``guarantees`` — safety invariants proven over the master *before* any child
      runs; if any fails, the whole orchestration is refused (fail-closed).
    """

    def __init__(
        self,
        master: "Agent",
        planner: Optional[Planner] = None,
        synthesizer: Optional[Synthesizer] = None,
        registry: Optional[SpecialistRegistry] = None,
        max_parallel: int = 1,
        guarantees: "Optional[List[Invariant]]" = None,
    ):
        self.master = master
        self.planner = planner or RulePlanner()
        self.synthesizer = synthesizer or ConcatSynthesizer()
        self.registry = registry
        self.max_parallel = max(1, int(max_parallel))
        self.guarantees = list(guarantees or [])

    # -- lifecycle --------------------------------------------------------
    def decompose(self, intent: str) -> Plan:
        available = [g.name for g in self.master.grants] or list(self.master._by_capability)
        plan = self.planner.decompose(intent, available)
        emit(
            self.master.events, ORCHESTRATION_DECOMPOSED, self.master.run_id,
            intent=intent, subtasks=len(plan.subtasks),
            descriptions=[t.description for t in plan.subtasks],
        )
        return plan

    def provision(self, subtask: Subtask, isolate: bool = False) -> "Agent":
        """Instantiate a governed child for one subtask (attenuated + isolated).

        With ``isolate=True`` the child is given its own signed sub-chain (a
        distinct origin) and its own per-thread SQLite connections, so it is safe
        to run alongside its siblings in parallel.
        """
        grants, tools, description, council = self._resolve(subtask)
        adapters = self._isolate_tools(tools)

        overrides: dict = {}
        if isolate:
            origin = f"{self.master.node_id}:{subtask.id}"
            overrides.update(
                node_id=origin,
                memory=WhyMemory(self.master.memory.db_path, node_id=origin, identity=self.master.identity),
                precedents=PrecedentStore(self.master.precedents.db_path),
                recall=None,   # a fresh handle is opened lazily in this child's thread if needed
                journal=None,  # avoid sharing one journal connection across threads
            )
        if subtask.budget is not None:
            overrides["budget"] = dict(subtask.budget)  # per-child ceiling

        child = self.master.spawn(
            intent=description, grants=grants, adapters=adapters, council=council, **overrides,
        )
        emit(
            self.master.events, CHILD_SPAWNED, self.master.run_id,
            subtask=subtask.id, description=description, specialist=subtask.specialist,
            granted=[g.name for g in child.grants],
            dropped=[g.name for g in child.dropped_delegations],
            tools=[c for a in child.adapters for c in a.capabilities()],
            isolated=isolate,
        )
        return child

    def run(self, intent: Optional[str] = None) -> OrchestrationResult:
        """Decompose the intent, run each governed child, and synthesize one answer.

        Independent subtasks run concurrently when ``max_parallel > 1``; dependent
        ones wait for their prerequisites (dependency waves). Raises if a required
        safety guarantee does not hold.
        """
        intent = intent if intent is not None else self.master.intent.text
        self._guard()
        plan = self.decompose(intent)

        parallel = self.max_parallel > 1
        children: List[ChildResult] = []
        for wave in plan.waves():
            if parallel and len(wave) > 1:
                workers = min(self.max_parallel, len(wave))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    children.extend(pool.map(lambda st: self._execute(st, True), wave))
            else:
                children.extend(self._execute(st, False) for st in wave)

        synthesis = self.synthesizer.synthesize(intent, children)
        emit(
            self.master.events, ORCHESTRATION_SYNTHESIZED, self.master.run_id,
            executed=sum(1 for c in children if c.executed), total=len(children),
        )
        return OrchestrationResult(plan, children, synthesis)

    # -- governance -------------------------------------------------------
    def guarantee(self, invariants: "List[Invariant]") -> "GuaranteeReport":
        """Statically prove safety invariants over the master's authority.

        Because children are strictly attenuated, any invariant that holds for the
        master holds for every child it can spawn — the proof covers the whole
        tree before a single agent runs.
        """
        return self.master.guarantee(invariants)

    def _guard(self) -> None:
        """Fail-closed gate: refuse to orchestrate if a required guarantee fails."""
        if not self.guarantees:
            return
        report = self.master.guarantee(self.guarantees)
        if not report.all_hold:
            failed = [p.invariant.label() for p in report.failures()]
            raise GovernanceError(
                "orchestration blocked: required safety guarantee(s) do not hold: "
                + "; ".join(failed),
                context={"failures": failed},
            )

    # -- helpers ----------------------------------------------------------
    def _resolve(self, subtask: Subtask):
        """Apply a specialist template, returning (grants, tools, description, council)."""
        spec = self.registry.get(subtask.specialist) if (self.registry and subtask.specialist) else None
        grants = list(subtask.grants)
        tools = list(subtask.tools)
        description = subtask.description
        council = None
        if spec is not None:
            grants = grants or [CapabilityGrant(g.name, dict(g.scope), dict(g.limits)) for g in spec.grants]
            tools = tools or list(spec.tools)
            council = spec.council
            if spec.directive:
                description = f"{description} ({spec.directive})"
        return grants, tools, description, council

    def _execute(self, subtask: Subtask, isolate: bool) -> ChildResult:
        child = self.provision(subtask, isolate=isolate)
        run_result = child.run()
        cr = ChildResult(subtask, run_result, list(child.dropped_delegations))
        emit(
            self.master.events, CHILD_COMPLETE, self.master.run_id,
            subtask=subtask.id, executed=cr.executed, why_id=run_result.why_id,
        )
        return cr

    def _isolate_tools(self, tools: List[str]) -> List["Adapter"]:
        """Select the master's adapters that serve the requested capabilities.

        Deny-by-default: a subtask that declares no tools receives no adapters (a
        pure-reasoning child). Adapter selection is coarse (an adapter may serve
        several capabilities); the fine-grained restriction is the attenuated
        grant, which the kernel enforces per action.
        """
        if not tools:
            return []
        wanted = set(tools)
        return [a for a in self.master.adapters if set(a.capabilities()) & wanted]

