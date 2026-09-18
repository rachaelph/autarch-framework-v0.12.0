"""Adaptive, budgeted agent runtime used by the token-efficiency demo.

The module intentionally depends only on Autarch and the Python standard library.
It is an executable design, not a simulated dashboard: both benchmark paths call
providers, provision agents, account for tokens, and evaluate their output.
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from autarch import Agent, capability
from autarch.adapters.tool import ToolAdapter
from autarch.intelligence.base import ModelProvider
from autarch.intelligence.pricing import PriceBook, estimate_tokens

_WORD = re.compile(r"[a-z][a-z0-9_.-]{2,}", re.IGNORECASE)
_CITATION = re.compile(r"\[([a-z]+-[0-9]{3})\]", re.IGNORECASE)
_STOPWORDS = {
    "add", "after", "all", "and", "are", "can", "each", "for", "from",
    "have", "into", "must", "not", "only", "remain", "that", "the", "their",
    "then", "this", "use", "with", "without",
}


def _terms(text: str) -> Set[str]:
    return {token.lower() for token in _WORD.findall(text) if token.lower() not in _STOPWORDS}


class TokenBudgetExceeded(RuntimeError):
    """Raised before a model call that cannot fit its declared allowance."""


@dataclass(frozen=True)
class Reservation:
    id: int
    phase: str
    tokens: int


@dataclass(frozen=True)
class CallTrace:
    phase: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    duration_ms: float
    admitted_tokens: int
    truncated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class TokenLedger:
    """Thread-safe admission ledger for model calls.

    Calls reserve their full prompt plus output allowance atomically. This prevents
    concurrent workers from all observing the same remaining budget. Reservations
    are reconciled to estimated actual use after completion.
    """

    def __init__(self, limit: int, phase_limits: Optional[Mapping[str, int]] = None) -> None:
        if limit <= 0:
            raise ValueError("token limit must be positive")
        self.limit = int(limit)
        self.phase_limits = {str(k): int(v) for k, v in (phase_limits or {}).items()}
        self._spent = 0
        self._reserved = 0
        self._phase_spent: Dict[str, int] = {}
        self._phase_reserved: Dict[str, int] = {}
        self._next_id = 1
        self._active: Dict[int, Reservation] = {}
        self._traces: List[CallTrace] = []
        self._lock = threading.Lock()

    def reserve(self, phase: str, tokens: int) -> Reservation:
        tokens = max(1, int(tokens))
        phase = str(phase)
        with self._lock:
            projected = self._spent + self._reserved + tokens
            if projected > self.limit:
                raise TokenBudgetExceeded(
                    "global token admission denied: {} > {}".format(projected, self.limit)
                )
            phase_limit = self.phase_limits.get(phase)
            phase_projected = (
                self._phase_spent.get(phase, 0)
                + self._phase_reserved.get(phase, 0)
                + tokens
            )
            if phase_limit is not None and phase_projected > phase_limit:
                raise TokenBudgetExceeded(
                    "phase {!r} admission denied: {} > {}".format(
                        phase, phase_projected, phase_limit
                    )
                )
            reservation = Reservation(self._next_id, phase, tokens)
            self._next_id += 1
            self._active[reservation.id] = reservation
            self._reserved += tokens
            self._phase_reserved[phase] = self._phase_reserved.get(phase, 0) + tokens
            return reservation

    def release(self, reservation: Reservation) -> None:
        with self._lock:
            current = self._active.pop(reservation.id, None)
            if current is None:
                return
            self._reserved -= current.tokens
            self._phase_reserved[current.phase] -= current.tokens

    def commit(self, reservation: Reservation, trace: CallTrace) -> None:
        with self._lock:
            current = self._active.pop(reservation.id, None)
            if current is None:
                raise RuntimeError("reservation is no longer active")
            self._reserved -= current.tokens
            self._phase_reserved[current.phase] -= current.tokens
            actual = trace.total_tokens
            self._spent += actual
            self._phase_spent[current.phase] = self._phase_spent.get(current.phase, 0) + actual
            self._traces.append(trace)

    @property
    def traces(self) -> List[CallTrace]:
        with self._lock:
            return list(self._traces)

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            return {
                "limit": self.limit,
                "spent": self._spent,
                "reserved": self._reserved,
                "remaining": max(0, self.limit - self._spent - self._reserved),
                "phase_spent": dict(self._phase_spent),
                "calls": len(self._traces),
            }


class BudgetedProvider(ModelProvider):
    """Provider decorator adding pre-call token admission and call telemetry."""

    def __init__(self, provider: ModelProvider, ledger: TokenLedger) -> None:
        self.provider = provider
        self.ledger = ledger
        self.name = getattr(provider, "name", provider.__class__.__name__)

    def complete_budgeted(
        self,
        prompt: str,
        *,
        phase: str,
        system: Optional[str] = None,
        output_allowance: int = 300,
    ) -> str:
        prompt_tokens = estimate_tokens((system or "") + "\n" + prompt)
        reservation = self.ledger.reserve(phase, prompt_tokens + output_allowance)
        started = time.perf_counter()
        try:
            result = str(self.provider.complete(prompt, system=system))
        except Exception:
            self.ledger.release(reservation)
            raise
        duration_ms = (time.perf_counter() - started) * 1000.0
        completion_tokens = estimate_tokens(result)
        truncated = completion_tokens > output_allowance
        if truncated:
            # This bounds downstream context. Provider-native output limits should
            # additionally be configured because generation cost has already occurred.
            result = result[: output_allowance * 4]
            completion_tokens = estimate_tokens(result)
        trace = CallTrace(
            phase=phase,
            model=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=duration_ms,
            admitted_tokens=reservation.tokens,
            truncated=truncated,
        )
        self.ledger.commit(reservation, trace)
        return result

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        return self.complete_budgeted(prompt, phase="default", system=system)


@dataclass(frozen=True)
class KnowledgeItem:
    id: str
    text: str
    kind: str
    tags: Tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "KnowledgeItem":
        return cls(
            id=str(value["id"]),
            text=str(value["text"]),
            kind=str(value.get("kind", "general")),
            tags=tuple(str(tag) for tag in value.get("tags", [])),
        )


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    keywords: Tuple[str, ...]
    instructions: str
    tools: Tuple[str, ...]
    required_kinds: Tuple[str, ...] = ()


class SkillRegistry:
    """Ranks modular skill manifests and activates only useful specialists."""

    def __init__(self, skills: Sequence[Skill]) -> None:
        if not skills:
            raise ValueError("at least one skill is required")
        names = [skill.name for skill in skills]
        if len(names) != len(set(names)):
            raise ValueError("skill names must be unique")
        self.skills = tuple(skills)

    @staticmethod
    def _terms(text: str) -> Set[str]:
        return _terms(text)

    def route(self, intent: str, max_skills: int = 4) -> List[Skill]:
        terms = self._terms(intent)
        scored: List[Tuple[int, str, Skill]] = []
        for skill in self.skills:
            keywords = {word.lower() for word in skill.keywords}
            score = 3 * len(terms & keywords)
            score += len(terms & self._terms(skill.description))
            if score:
                scored.append((score, skill.name, skill))
        scored.sort(key=lambda row: (-row[0], row[1]))
        selected = [row[2] for row in scored[: max(1, max_skills)]]
        return selected or [self.skills[0]]


@dataclass(frozen=True)
class ContextBundle:
    text: str
    selected_ids: Tuple[str, ...]
    rejected_ids: Tuple[str, ...]
    tokens: int


class ContextAssembler:
    """Retrieves and packs evidence without exceeding the serialized envelope."""

    def __init__(self, items: Sequence[KnowledgeItem]) -> None:
        self.items = tuple(items)

    @staticmethod
    def _terms(text: str) -> Set[str]:
        return _terms(text)

    def assemble(
        self,
        intent: str,
        skill: Skill,
        token_limit: int,
        *,
        include_all: bool = False,
    ) -> ContextBundle:
        if token_limit <= 0:
            raise ValueError("context token limit must be positive")
        query = self._terms(intent + " " + " ".join(skill.keywords))
        ranked = []
        for item in self.items:
            item_terms = self._terms(item.text + " " + " ".join(item.tags))
            overlap = len(query & item_terms)
            kind_bonus = 3 if item.kind in skill.required_kinds else 0
            ranked.append((overlap + kind_bonus, item.id, item))
        ranked.sort(key=lambda row: (-row[0], row[1]))

        lines: List[str] = []
        selected: List[str] = []
        rejected: List[str] = []
        for score, _, item in ranked:
            if not include_all and score <= 0:
                rejected.append(item.id)
                continue
            line = "[{}] ({}) {}".format(item.id, item.kind, item.text)
            candidate = "\n".join(lines + [line])
            if estimate_tokens(candidate) <= token_limit:
                lines.append(line)
                selected.append(item.id)
            else:
                rejected.append(item.id)
        text = "\n".join(lines)
        return ContextBundle(text, tuple(selected), tuple(rejected), estimate_tokens(text))


@dataclass
class RunMetrics:
    mode: str
    selected_skills: List[str]
    selected_sources: List[str]
    exposed_tools: List[str]
    provisioned_agents: List[str]
    model_calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    latency_ms: float
    context_tokens: int
    citation_score: float
    section_score: float
    output: str
    traces: List[Dict[str, object]] = field(default_factory=list)

    def public_dict(self) -> Dict[str, object]:
        value = asdict(self)
        value.pop("output", None)
        return value


@dataclass
class ComparisonReport:
    baseline: RunMetrics
    optimized: RunMetrics

    @staticmethod
    def _reduction(before: float, after: float) -> float:
        return 0.0 if before <= 0 else 100.0 * (before - after) / before

    @property
    def token_reduction_percent(self) -> float:
        return self._reduction(self.baseline.total_tokens, self.optimized.total_tokens)

    @property
    def tool_reduction_percent(self) -> float:
        return self._reduction(len(self.baseline.exposed_tools), len(self.optimized.exposed_tools))

    def to_dict(self) -> Dict[str, object]:
        return {
            "baseline": self.baseline.public_dict(),
            "optimized": self.optimized.public_dict(),
            "improvement": {
                "token_reduction_percent": round(self.token_reduction_percent, 2),
                "tool_reduction_percent": round(self.tool_reduction_percent, 2),
                "latency_reduction_percent": round(
                    self._reduction(self.baseline.latency_ms, self.optimized.latency_ms), 2
                ),
                "quality_preserved": (
                    self.optimized.citation_score >= self.baseline.citation_score
                    and self.optimized.section_score >= self.baseline.section_score
                ),
            },
        }


class DeterministicEngineeringProvider(ModelProvider):
    """Offline provider that preserves the same contracts as a network model."""

    name = "mock"

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        sources = list(dict.fromkeys(_CITATION.findall(prompt)))
        citations = " ".join("[{}]".format(source) for source in sources[:4])
        skill_match = re.search(r"SPECIALIST:\s*([\w-]+)", prompt)
        if skill_match:
            skill = skill_match.group(1).replace("-", " ").title()
            return (
                "### {} finding\nImplement the smallest boundary change supported by the "
                "evidence. Preserve deny-by-default behavior and add deterministic "
                "verification. {}".format(skill, citations)
            ).strip()
        return (
            "# Implementation plan\n\n"
            "## Architecture\nAdd the change at the API boundary and keep business services "
            "independent of credential parsing. {0}\n\n"
            "## Security\nValidate credentials completely, separate authorization, and fail "
            "closed without leaking validation details. {0}\n\n"
            "## Testing\nCover valid, missing, malformed, expired, and insufficient-role cases; "
            "assert rejected requests do not reach services. {0}\n\n"
            "## Operations\nUse managed secrets, safe telemetry, rotation overlap, and an emergency "
            "revocation runbook. {0}\n\n"
            "## Rollout\nDeploy behind configuration, monitor categorized failures, and retain a "
            "rollback path. {0}"
        ).format(citations).strip()


def default_skills() -> List[Skill]:
    return [
        Skill("architecture", "Design service and API boundaries", ("api", "service", "architecture", "backward", "authentication"), "Prefer narrow interfaces, compatibility, and reversible changes.", ("repo.search", "repo.symbols"), ("architecture",)),
        Skill("security", "Threat-model authentication and authorization", ("jwt", "token", "authentication", "authorization", "issuer", "audience", "security", "secret"), "Fail closed, minimize authority, and never expose credentials.", ("repo.search", "repo.config"), ("security",)),
        Skill("testing", "Define unit, integration, and adversarial tests", ("test", "tests", "pytest", "acceptance", "integration", "invalid", "expired"), "Turn acceptance criteria and threat cases into deterministic tests.", ("repo.search", "repo.tests"), ("testing",)),
        Skill("operations", "Plan deployment, observability, and rollback", ("deploy", "deployment", "telemetry", "logging", "rotation", "runbook", "operations"), "Protect secrets and make rollout observable and reversible.", ("repo.config", "repo.telemetry"), ("operations",)),
        Skill("documentation", "Specify API and operator documentation", ("documentation", "docs", "operator", "runbook", "api"), "Document contracts, configuration, failure modes, and recovery.", ("repo.search", "repo.docs"), ("documentation",)),
        Skill("frontend", "Design accessible user interfaces", ("frontend", "css", "accessibility", "screen", "component"), "Follow the design system and accessibility requirements.", ("repo.search", "repo.ui"), ("frontend",)),
        Skill("data-science", "Design governed data and model delivery", ("model", "forecast", "dataset", "embedding", "drift", "machine-learning"), "Define data contracts, evaluations, and monitoring.", ("repo.search", "repo.data"), ("data", "machine-learning")),
    ]


def load_knowledge(path: Path) -> List[KnowledgeItem]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("knowledge file must contain a JSON list")
    return [KnowledgeItem.from_dict(item) for item in raw]


class TokenEfficientEngineering:
    """Runs comparable full-context and adaptive engineering workflows."""

    REQUIRED_SECTIONS = ("Architecture", "Security", "Testing", "Operations", "Rollout")

    def __init__(
        self,
        provider: ModelProvider,
        knowledge: Sequence[KnowledgeItem],
        *,
        workspace: Path,
        skills: Optional[Sequence[Skill]] = None,
        price_book: Optional[PriceBook] = None,
        max_parallel: int = 4,
    ) -> None:
        self.provider = provider
        self.knowledge = tuple(knowledge)
        self.registry = SkillRegistry(skills or default_skills())
        self.assembler = ContextAssembler(self.knowledge)
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.price_book = price_book or PriceBook()
        self.max_parallel = max(1, int(max_parallel))
        all_tools = sorted({tool for skill in self.registry.skills for tool in skill.tools})
        adapter = ToolAdapter(
            {name.partition(".")[2]: (lambda **_: "governed") for name in all_tools},
            namespace="repo",
        )
        self.master = Agent(
            "token-efficient engineering supervisor",
            grants=[capability(name) for name in all_tools],
            adapters=[adapter],
            workspace=self.workspace,
            council=[provider],
        )

    @staticmethod
    def _skill_contract(skill: Skill, include_catalog: Sequence[Skill]) -> str:
        catalog = "\n".join(
            "- {}: {} | tools={}".format(s.name, s.instructions, ",".join(s.tools))
            for s in include_catalog
        )
        return "ACTIVE SKILL: {}\n{}\nAVAILABLE CATALOG:\n{}".format(
            skill.name, skill.instructions, catalog
        )

    def _provision(self, skill: Skill) -> Agent:
        return self.master.spawn(
            "specialist: {}".format(skill.name),
            grants=[capability(name) for name in skill.tools],
            adapters=self.master.adapters,
            council=[self.provider],
        )

    def _run(
        self,
        intent: str,
        *,
        optimized: bool,
        budget: int,
        context_budget: int,
    ) -> RunMetrics:
        started = time.perf_counter()
        skills = self.registry.route(intent) if optimized else list(self.registry.skills)
        # The baseline mirrors a fixed harness: every specialist receives every
        # skill manifest, tool contract, and knowledge item.
        catalog = skills if optimized else self.registry.skills
        per_call_context = max(1, context_budget // max(1, len(skills))) if optimized else max(context_budget, 4000)
        phase_limits = {"specialist": int(budget * 0.72), "synthesis": int(budget * 0.28)} if optimized else None
        ledger = TokenLedger(budget, phase_limits=phase_limits)
        metered = BudgetedProvider(self.provider, ledger)
        provisioned: List[Tuple[Skill, Agent, ContextBundle]] = []
        for skill in skills:
            bundle = self.assembler.assemble(
                intent, skill, per_call_context, include_all=not optimized
            )
            provisioned.append((skill, self._provision(skill), bundle))

        def analyze(entry: Tuple[Skill, Agent, ContextBundle]) -> Tuple[str, ContextBundle, str]:
            skill, agent, bundle = entry
            prompt = (
                "MODE: ANALYSIS\nSPECIALIST: {name}\nAGENT: {agent}\nINTENT:\n{intent}\n\n"
                "{contract}\n\nEVIDENCE:\n{evidence}\n\n"
                "Return one concise, actionable finding with source citations."
            ).format(
                name=skill.name,
                agent=agent.run_id,
                intent=intent,
                contract=self._skill_contract(skill, catalog),
                evidence=bundle.text,
            )
            finding = metered.complete_budgeted(
                prompt, phase="specialist", output_allowance=180
            )
            return skill.name, bundle, finding

        workers = min(self.max_parallel, len(provisioned))
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                findings = list(pool.map(analyze, provisioned))
        else:
            findings = [analyze(entry) for entry in provisioned]

        selected_sources = sorted({source for _, bundle, _ in findings for source in bundle.selected_ids})
        evidence = "\n".join(
            "[{}] {}".format(item.id, item.text)
            for item in self.knowledge
            if item.id in selected_sources
        )
        handoffs = "\n\n".join("### {}\n{}".format(name, finding) for name, _, finding in findings)
        synthesis_prompt = (
            "MODE: SYNTHESIS\nINTENT:\n{intent}\n\nSELECTED EVIDENCE:\n{evidence}\n\n"
            "SPECIALIST HANDOFFS:\n{handoffs}\n\nCreate an implementation plan with exactly "
            "these sections: Architecture, Security, Testing, Operations, Rollout. "
            "Cite evidence IDs for factual claims."
        ).format(intent=intent, evidence=evidence, handoffs=handoffs)
        output = metered.complete_budgeted(
            synthesis_prompt, phase="synthesis", output_allowance=700
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        traces = ledger.traces
        prompt_tokens = sum(trace.prompt_tokens for trace in traces)
        completion_tokens = sum(trace.completion_tokens for trace in traces)
        cited = set(_CITATION.findall(output))
        supported = cited & set(selected_sources)
        citation_score = 1.0 if not cited and not selected_sources else len(supported) / max(1, len(cited))
        section_score = sum(
            1 for section in self.REQUIRED_SECTIONS if "## {}".format(section) in output
        ) / len(self.REQUIRED_SECTIONS)
        models = [(trace.model, trace.prompt_tokens, trace.completion_tokens) for trace in traces]
        cost = sum(self.price_book.token_cost(model, p, c) for model, p, c in models)
        exposed_tools = sorted({tool for skill in skills for tool in skill.tools})
        return RunMetrics(
            mode="optimized" if optimized else "baseline",
            selected_skills=[skill.name for skill in skills],
            selected_sources=selected_sources,
            exposed_tools=exposed_tools,
            provisioned_agents=[agent.run_id for _, agent, _ in provisioned],
            model_calls=len(traces),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            estimated_cost_usd=cost,
            latency_ms=latency_ms,
            context_tokens=sum(bundle.tokens for _, bundle, _ in findings),
            citation_score=citation_score,
            section_score=section_score,
            output=output,
            traces=[asdict(trace) for trace in traces],
        )

    def compare(self, intent: str, *, optimized_budget: int = 12000, context_budget: int = 2400) -> ComparisonReport:
        # The baseline is intentionally observation-only: it receives enough room
        # to reveal the actual cost of broadcasting all context and capabilities.
        baseline = self._run(
            intent, optimized=False, budget=max(optimized_budget * 8, 60000), context_budget=context_budget
        )
        optimized = self._run(
            intent, optimized=True, budget=optimized_budget, context_budget=context_budget
        )
        return ComparisonReport(baseline, optimized)
