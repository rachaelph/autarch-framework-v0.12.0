"""Deliberation: a council of voices -> a moderated recommendation.

Each voice proposes an action; a deterministic moderator selects the motion
with the most support. Then each voice critiques that motion. The moderator
tallies the verdicts under a *most-cautious-wins* rule: any veto blocks, else any
revise cautions, else the motion is approved. Disagreement is surfaced, not hidden
— that visibility is the point of a council.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Dict, List, Optional

from ..contracts import Action, Intent, Verdict
from ..intelligence.base import ModelProvider
from ..util import extract_json

if TYPE_CHECKING:  # avoid runtime coupling; these are attached by the Agent
    from ..policy import PolicyDecision
    from ..precedent import Precedent

# System prompts steer real models toward bare, parseable JSON (the mock ignores
# them). Kept terse on purpose — long system prompts hurt small local models.
_SYSTEM_PROPOSER = (
    "You are a careful planning councilor. Respond with ONLY a single JSON object "
    "and no prose, no explanation, and no markdown code fences."
)
_SYSTEM_CHALLENGER = (
    "You are a cautious safety reviewer. Respond with ONLY a single JSON object "
    "containing a verdict and reasons. No prose, no markdown fences."
)

# The only verdicts a critic may return (excludes the proposer's 'propose').
_CRITIQUE_VERDICTS = {Verdict.APPROVE.value, Verdict.REVISE.value, Verdict.VETO.value}

_PROPOSER_TEMPLATE = """ROLE: PROPOSER
You are a councilor in the Autarch system. Propose exactly ONE concrete action
that best achieves the intent. Choose from the allowed capabilities only.
ALLOWED: {capabilities}
INTENT: {intent}
{feedback}Respond with ONLY a JSON object:
{{"capability": "<one of allowed>", "params": {{...}}, "rationale": "<why>"}}
"""

_CHALLENGER_TEMPLATE = """ROLE: CHALLENGER
You are a councilor whose duty is to find risk, overreach, or error in a proposed
action and protect the autarch (the user). Review the action below.
ACTION: {action_json}
Choose a verdict: "approve", "revise", or "veto".
Respond with ONLY a JSON object:
{{"verdict": "<approve|revise|veto>", "reasons": "<why>"}}
"""

_REBUTTAL_TEMPLATE = """ROLE: CHALLENGER (rebuttal round {round})
You are reconsidering the motion after hearing your fellow councilors. Weigh their
arguments honestly: if a dissent exposes a real risk, move toward caution; if it is
unfounded, hold your ground and say why.
ACTION: {action_json}
YOUR PRIOR VERDICT: {prior_verdict} ({prior_reasons})
OTHER COUNCILORS SAID:
{others}
Now give your (possibly revised) verdict as ONLY a JSON object:
{{"verdict": "<approve|revise|veto>", "reasons": "<why>"}}
"""


def action_signature(action: Action) -> str:
    """A stable identity for an action, so equivalent proposals can be grouped."""
    return f"{action.capability}:{json.dumps(action.params, sort_keys=True)}"


@dataclass
class Position:
    """One councilor's stance during a deliberation."""

    voice: str
    role: str  # "proposer" | "critic"
    verdict: str
    rationale: str
    action: Optional[Action] = None


@dataclass
class Deliberation:
    """The full record of a council's reasoning about one intent."""

    intent: Intent
    proposals: List[Position]
    critiques: List[Position]
    motion: Optional[Action]
    recommendation: str
    tally: Dict[str, int]
    proposal_disagreement: bool = False
    rounds: int = 1
    # A per-round record of the debate: [{voice: (verdict, reasons)}, ...]. Empty
    # for a single-round deliberation; populated when the council debates.
    transcript: List[Dict[str, tuple]] = field(default_factory=list)
    # Attached by the Agent before presiding (kept loosely typed to avoid coupling).
    policy: "Optional[PolicyDecision]" = None
    precedent: "Optional[Precedent]" = None

    # -- back-compat convenience views -----------------------------------
    @property
    def action(self) -> Optional[Action]:
        return self.motion

    @property
    def voices(self) -> List[str]:
        seen, names = set(), []
        for pos in self.proposals:
            if pos.voice not in seen:
                seen.add(pos.voice)
                names.append(pos.voice)
        return names

    @property
    def proposal(self) -> Position:
        """The proposing position behind the motion (or the first proposal)."""
        if self.motion is not None:
            target = action_signature(self.motion)
            for pos in self.proposals:
                if pos.action is not None and action_signature(pos.action) == target:
                    return pos
        if self.proposals:
            return self.proposals[0]
        return Position("council", "proposer", "abstain", "No proposal produced.")

    @property
    def critique(self) -> Position:
        """The lead critique: strongest dissent first (veto > revise > approve)."""
        for wanted in (Verdict.VETO.value, Verdict.REVISE.value):
            for pos in self.critiques:
                if pos.verdict == wanted:
                    return pos
        if self.critiques:
            return self.critiques[0]
        return Position("council", "critic", self.recommendation, "No critique produced.")

    @property
    def unanimous(self) -> bool:
        return len(self.tally) <= 1

    @property
    def has_disagreement(self) -> bool:
        return (
            self.proposal_disagreement
            or not self.unanimous
            or self.recommendation != Verdict.APPROVE.value
        )


class Council:
    def __init__(
        self,
        providers: List[ModelProvider],
        capabilities: List[str],
        schemas: Optional[Dict[str, dict]] = None,
        max_workers: int = 8,
    ):
        if not providers:
            raise ValueError("A council needs at least one provider.")
        self.providers = providers
        self.capabilities = capabilities
        self.schemas = schemas or {}
        self.max_workers = max(1, max_workers)

    def _map(self, fn: Callable, items: list) -> list:
        """Apply `fn` across `items`, concurrently when several, preserving order.

        Voices query their models in parallel so deliberation latency is the
        slowest model, not the sum. Order is preserved (so tie-breaking stays
        deterministic), and a single voice skips the thread pool entirely.
        Each callee already handles its own errors, so futures never raise.
        """
        if len(items) <= 1 or self.max_workers == 1:
            return [fn(x) for x in items]
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(items))) as pool:
            return list(pool.map(fn, items))

    def _capabilities_block(self) -> str:
        """Render allowed capabilities, with parameter schemas when known."""
        if not self.capabilities:
            return "(none)"
        lines = []
        for cap in self.capabilities:
            schema = self.schemas.get(cap)
            if schema:
                params = ", ".join(f'"{k}": {v}' for k, v in schema.items())
                lines.append(f"{cap} (params: {{{params}}})")
            else:
                lines.append(cap)
        return "; ".join(lines)

    def deliberate(
        self,
        intent: Intent,
        feedback: Optional[str] = None,
        exclude: Optional[set] = None,
        round_index: int = 1,
    ) -> Deliberation:
        exclude = exclude or set()

        # --- proposal round: every voice proposes (in parallel) ----------
        proposals: List[Position] = []
        candidates: Dict[str, list] = {}  # signature -> [action, [voice names]]
        proposed = self._map(
            lambda provider: (provider, self._propose(provider, intent, feedback)),
            self.providers,
        )
        for provider, action in proposed:
            if action is None or action.capability in exclude:
                proposals.append(
                    Position(provider.name, "proposer", "abstain", "No actionable proposal.", None)
                )
                continue
            proposals.append(
                Position(provider.name, "proposer", Verdict.PROPOSE.value, action.rationale, action)
            )
            sig = action_signature(action)
            candidates.setdefault(sig, [action, []])[1].append(provider.name)

        motion = self._select_motion(candidates)
        proposal_disagreement = len(candidates) > 1

        if motion is None:
            return Deliberation(
                intent, proposals, [], None, Verdict.VETO.value, {},
                proposal_disagreement=proposal_disagreement, rounds=round_index,
            )

        # --- critique round: every voice judges the motion (in parallel) -
        critiques: List[Position] = []
        tally: Dict[str, int] = {}
        critiqued = self._map(
            lambda provider: (provider, self._critique(provider, motion)),
            self.providers,
        )
        for provider, (verdict, reasons) in critiqued:
            critiques.append(Position(provider.name, "critic", verdict, reasons, motion))
            tally[verdict] = tally.get(verdict, 0) + 1

        recommendation = self._recommend(tally)
        return Deliberation(
            intent, proposals, critiques, motion, recommendation, tally,
            proposal_disagreement=proposal_disagreement, rounds=round_index,
        )

    def debate(
        self,
        intent: Intent,
        feedback: Optional[str] = None,
        exclude: Optional[set] = None,
        round_index: int = 1,
        debate_rounds: int = 1,
    ) -> Deliberation:
        """Deliberate, then hold up to ``debate_rounds`` rounds of rebuttal.

        A real council does not just vote once — voices *respond to each other*.
        After the first critique, if the councilors disagree, each is re-polled
        with the others' arguments in front of it and may revise its verdict.
        The full exchange is captured in ``Deliberation.transcript`` (so it can be
        shown and signed into the ledger). Rebuttal stops early once the council
        reaches consensus. Deterministic mocks hold their ground (debate never
        fabricates agreement); real models genuinely move.
        """
        delib = self.deliberate(intent, feedback=feedback, exclude=exclude, round_index=round_index)
        if delib.motion is None or debate_rounds <= 0:
            return delib

        # Seed the transcript with round 1 (the initial critiques).
        positions = {c.voice: (c.verdict, c.rationale) for c in delib.critiques}
        delib.transcript.append(dict(positions))

        for r in range(2, debate_rounds + 2):
            if len({v for v, _ in positions.values()}) <= 1:
                break  # already unanimous — nothing left to debate
            revised = self._map(
                lambda provider: (provider, self._rebut(provider, delib.motion, positions, r)),
                self.providers,
            )
            new_positions: Dict[str, tuple] = {}
            for provider, (verdict, reasons) in revised:
                new_positions[provider.name] = (verdict, reasons)
            positions = new_positions
            delib.transcript.append(dict(positions))
            delim_unchanged = delib.transcript[-1] == delib.transcript[-2]
            if delim_unchanged:
                break  # positions stabilized; further rounds add nothing

        # Rebuild critiques + tally from the final round.
        final_critiques: List[Position] = []
        tally: Dict[str, int] = {}
        for voice, (verdict, reasons) in positions.items():
            final_critiques.append(Position(voice, "critic", verdict, reasons, delib.motion))
            tally[verdict] = tally.get(verdict, 0) + 1
        delib.critiques = final_critiques
        delib.tally = tally
        delib.recommendation = self._recommend(tally)
        delib.rounds = len(delib.transcript)
        return delib

    def _rebut(self, provider: ModelProvider, action: Action, positions: Dict[str, tuple], round_no: int):
        """Re-poll one voice with the others' current positions visible."""
        prior = positions.get(provider.name, (Verdict.REVISE.value, ""))
        others = "\n".join(
            f"- {voice}: {verdict} — {reasons}"
            for voice, (verdict, reasons) in positions.items()
            if voice != provider.name
        ) or "(no other councilors)"
        prompt = _REBUTTAL_TEMPLATE.format(
            round=round_no,
            action_json=json.dumps({"capability": action.capability, "params": action.params}),
            prior_verdict=prior[0],
            prior_reasons=prior[1],
            others=others,
        )
        try:
            raw = provider.complete(prompt, system=_SYSTEM_CHALLENGER)
        except Exception as exc:
            return Verdict.REVISE.value, f"reviewer unavailable in rebuttal ({type(exc).__name__}); caution"
        data = extract_json(raw)
        if not data or "verdict" not in data or data.get("verdict") not in _CRITIQUE_VERDICTS:
            # Fail closed but keep a prior *stronger* dissent rather than softening.
            return prior if prior[0] in (Verdict.VETO.value, Verdict.REVISE.value) else (Verdict.REVISE.value, "unparseable rebuttal; caution")
        return data["verdict"], str(data.get("reasons", ""))

    # -- moderator rules --------------------------------------------------
    @staticmethod
    def _select_motion(candidates: Dict[str, list]) -> Optional[Action]:
        """Pick the most-supported candidate; ties resolve to insertion order."""
        if not candidates:
            return None
        best = max(candidates.values(), key=lambda entry: len(entry[1]))
        return best[0]

    @staticmethod
    def _recommend(tally: Dict[str, int]) -> str:
        """Most-cautious-wins: any veto blocks, else any revise cautions."""
        if tally.get(Verdict.VETO.value):
            return Verdict.VETO.value
        if tally.get(Verdict.REVISE.value):
            return Verdict.REVISE.value
        return Verdict.APPROVE.value

    # -- model interaction ------------------------------------------------
    def _propose(self, provider: ModelProvider, intent: Intent, feedback: Optional[str]) -> Optional[Action]:
        feedback_line = f"PRIOR_FEEDBACK: {feedback}\n" if feedback else ""
        prompt = _PROPOSER_TEMPLATE.format(
            capabilities=self._capabilities_block(),
            intent=intent.text,
            feedback=feedback_line,
        )
        try:
            raw = provider.complete(prompt, system=_SYSTEM_PROPOSER)
        except Exception:
            # A flaky/failed model abstains rather than crashing the council.
            return None
        data = extract_json(raw) or {}
        capability = data.get("capability")
        if not capability or not isinstance(capability, str):
            return None
        params = data.get("params")
        if not isinstance(params, dict):
            params = {}
        return Action(
            capability=capability,
            params=params,
            rationale=str(data.get("rationale", "")),
        )

    def _critique(self, provider: ModelProvider, action: Action):
        """Critique the motion. Fails CLOSED: any uncertainty defaults to 'revise'.

        With a real model, an unreachable, malformed, or off-spec response must
        never silently approve a risky action. 'revise' blocks auto-ratification
        while still letting a human explicitly ratify.
        """
        prompt = _CHALLENGER_TEMPLATE.format(
            action_json=json.dumps({"capability": action.capability, "params": action.params})
        )
        try:
            raw = provider.complete(prompt, system=_SYSTEM_CHALLENGER)
        except Exception as exc:
            return Verdict.REVISE.value, f"reviewer unavailable ({type(exc).__name__}); caution by default"

        data = extract_json(raw)
        if not data or "verdict" not in data:
            return Verdict.REVISE.value, "reviewer response was unparseable; caution by default"
        verdict = data.get("verdict")
        if verdict not in _CRITIQUE_VERDICTS:
            return Verdict.REVISE.value, f"reviewer returned an invalid verdict ({verdict!r}); caution by default"
        return verdict, str(data.get("reasons", ""))
