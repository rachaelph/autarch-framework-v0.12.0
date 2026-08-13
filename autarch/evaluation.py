"""Governed evaluation & reflection — judge LLMs and self-improvement, built in.

Developers keep re-writing the same plumbing: an "LLM-as-judge" to score outputs,
deterministic assertions, and a reflect-then-retry loop. Autarch provides the
**reusable contract** so you don't — and, crucially, makes evaluation *governed*:
a verdict can be recorded in the tamper-evident, signed why-memory, so you can
later **prove** an output was evaluated, by which judge, and what it scored.

The framework owns the *shape*; you bring the *judgment* (your rubric, your gold
answers, your threshold). The reference evaluators that ship:

  * ``AssertionEvaluator`` — deterministic checks (prefer these; no LLM bias).
  * ``RubricJudge``        — an LLM-as-judge against your rubric (fails closed).
  * ``ConsensusEvaluator`` — several judges vote (mitigates single-judge bias).
  * ``GroundednessEvaluator`` / ``CoverageEvaluator`` — anti-hallucination and
    anti-detail-loss, deterministic (precision and recall against a source).
  * ``InjectionEvaluator`` / ``PIIEvaluator`` — deterministic content-safety
    scans (prompt-injection in untrusted input; PII in an output).
  * ``EvaluationPanel``    — group related evaluators under one label (e.g.
    "quality" or "safety") and run a caller-selected subset in a single report.
  * ``quality_panel`` / ``safety_panel`` — ready-made panels you can consume
    directly (completeness/groundedness/coverage; injection/PII/harm-judge).

And ``reflect(produce, evaluator, ...)`` is a bounded improve-then-retry loop.

Honest caveats (this pattern is over-hyped):
  * LLM judges have documented biases (position, verbosity, self-preference). Use
    ``ConsensusEvaluator`` and prefer deterministic checks where you can.
  * Reflection does not reliably improve quality; it is opt-in and *bounded*.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .util import extract_json, fold, nfkc, unicode_sentence_spans, unicode_sentences, word_tokens

_JUDGE_SYSTEM = (
    "You are a strict, impartial evaluator. Respond with ONLY a single JSON object "
    "and no prose or markdown."
)

_JUDGE_TEMPLATE = """ROLE: JUDGE
Score how well the OUTPUT satisfies the RUBRIC, from 0.0 (fails) to 1.0 (perfect).
RUBRIC: {rubric}
OUTPUT: {item}
Respond with ONLY a JSON object:
{{"score": <0.0-1.0>, "reasons": "<brief justification>"}}
"""


def _clamp(x: float) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


@dataclass
class Verdict:
    """The result of evaluating one item."""

    score: float
    passed: bool
    reasons: str = ""
    evaluator: str = ""
    details: dict = field(default_factory=dict)


class Evaluator(ABC):
    """The reusable evaluation contract. Bring your own judgment."""

    name: str = "evaluator"

    @abstractmethod
    def evaluate(self, item: Any, context: Optional[dict] = None) -> Verdict:
        raise NotImplementedError


class AssertionEvaluator(Evaluator):
    """Deterministic checks — no model, no bias. Prefer this where possible.

    `checks` is a list of ``(label, predicate)`` where ``predicate(item)`` returns
    truthy on success. Score is the fraction passed; `passed` is all-of-them.
    """

    def __init__(self, checks: List, name: str = "assertions"):
        self.name = name
        self._checks = list(checks)

    def evaluate(self, item: Any, context: Optional[dict] = None) -> Verdict:
        results = []
        for label, predicate in self._checks:
            try:
                ok = bool(predicate(item))
            except Exception as exc:  # a throwing check simply fails
                ok = False
                label = f"{label} ({type(exc).__name__})"
            results.append((label, ok))
        passed_n = sum(1 for _, ok in results if ok)
        total = len(results) or 1
        score = passed_n / total
        failed = [label for label, ok in results if not ok]
        return Verdict(
            score=score,
            passed=passed_n == len(results),
            reasons=("all checks passed" if not failed else "failed: " + "; ".join(failed)),
            evaluator=self.name,
            details={"results": results},
        )


class RubricJudge(Evaluator):
    """An LLM-as-judge scoring an item against a rubric. Fails closed.

    `model` is a `ModelProvider` or a provider spec (e.g. ``"ollama:llama3"``).
    Unparseable or failed judgments score 0.0 (never a false pass).
    """

    def __init__(self, model, rubric: str, threshold: float = 0.7, name: str = "rubric"):
        from .intelligence.factory import build_provider

        self._model = build_provider(model)
        self._rubric = rubric
        self.threshold = threshold
        self.name = name

    def evaluate(self, item: Any, context: Optional[dict] = None) -> Verdict:
        prompt = _JUDGE_TEMPLATE.format(rubric=self._rubric, item=str(item))
        try:
            raw = self._model.complete(prompt, system=_JUDGE_SYSTEM)
        except Exception as exc:
            return Verdict(0.0, False, f"judge unavailable ({type(exc).__name__})", self.name)
        data = extract_json(raw)
        if not data or "score" not in data:
            return Verdict(0.0, False, "judge response was unparseable", self.name)
        score = _clamp(data.get("score", 0.0))
        return Verdict(
            score=score,
            passed=score >= self.threshold,
            reasons=str(data.get("reasons", "")),
            evaluator=self.name,
            details={"threshold": self.threshold},
        )


class ConsensusEvaluator(Evaluator):
    """Aggregate several evaluators — mitigates single-judge bias.

    `strategy`: ``"mean"`` (average score), ``"min"`` (worst), or ``"majority"``
    (fraction that individually passed). `passed` is score >= `threshold`.
    """

    def __init__(self, evaluators: List[Evaluator], strategy: str = "mean", threshold: float = 0.7, name: str = "consensus"):
        if not evaluators:
            raise ValueError("ConsensusEvaluator needs at least one evaluator")
        self._evaluators = list(evaluators)
        self.strategy = strategy
        self.threshold = threshold
        self.name = name

    def evaluate(self, item: Any, context: Optional[dict] = None) -> Verdict:
        verdicts = [e.evaluate(item, context) for e in self._evaluators]
        scores = [v.score for v in verdicts]
        if self.strategy == "min":
            score = min(scores)
        elif self.strategy == "majority":
            score = sum(1 for v in verdicts if v.passed) / len(verdicts)
        else:  # mean
            score = sum(scores) / len(scores)
        reasons = " | ".join(f"{v.evaluator}={v.score:.2f}" for v in verdicts)
        return Verdict(
            score=score,
            passed=score >= self.threshold,
            reasons=reasons,
            evaluator=self.name,
            details={"strategy": self.strategy, "verdicts": verdicts},
        )


@dataclass
class ReflectionResult:
    output: Any
    verdict: Verdict
    revisions: int
    history: List = field(default_factory=list)  # [(output, verdict), ...]


# A producer takes optional feedback from the previous verdict and returns an item.
ProduceFn = Callable[[Optional[str]], Any]


def reflect(
    produce: ProduceFn,
    evaluator: Evaluator,
    min_score: float = 0.7,
    max_revisions: int = 2,
    on_attempt: Optional[Callable[[int, Any, Verdict], None]] = None,
) -> ReflectionResult:
    """Produce, evaluate, and improve — bounded.

    Calls ``produce(feedback)`` (feedback is None on the first try, then the prior
    verdict's reasons), evaluates the result, and retries up to `max_revisions`
    until the score meets `min_score`. The producer controls idempotency, so this
    is safe for generative/read work; it never re-executes Autarch side effects.
    """
    feedback: Optional[str] = None
    history: List = []
    verdict: Optional[Verdict] = None
    output: Any = None
    for attempt in range(max_revisions + 1):
        output = produce(feedback)
        verdict = evaluator.evaluate(output)
        history.append((output, verdict))
        if on_attempt is not None:
            on_attempt(attempt, output, verdict)
        if verdict.passed or verdict.score >= min_score:
            break
        feedback = verdict.reasons
    return ReflectionResult(output=output, verdict=verdict, revisions=len(history) - 1, history=history)


# --------------------------------------------------------------------------- #
# Faithful summarization — grounding, coverage, and structure-preserving
# compression. These target the well-known GenAI-summary failure modes:
#   * hallucination        -> GroundednessEvaluator (nothing invented)
#   * oversummarization    -> CoverageEvaluator (nothing critical dropped)
#   * naive compression    -> extractive_summary / compress_history
# Groundedness + Coverage are a matched pair: precision (no invented facts) and
# recall (no lost facts). Both are deterministic and dependency-free, so a
# faithfulness verdict can be scored offline and — via ``Agent.run(evaluate=)`` —
# signed into the tamper-evident ledger (provable faithfulness, not a promise).
# --------------------------------------------------------------------------- #
# Numbers use ASCII digits; NFKC (in _numbers) folds fullwidth ５ to them, and the
# Unicode-aware \d additionally matches Arabic-Indic ٥ and other decimal scripts.
_NUMBER_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")
# The named-entity heuristic keys off Latin capitalization (English, French, ...).
# Caseless scripts (CJK, Arabic, Hebrew) yield no entities here by design — the
# Unicode word-overlap and the optional semantic path carry grounding for them.
_ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+)*\b")

# Capitalized function words that start sentences — not real entities, so they
# must not count as "facts" to preserve or ground.
_ENTITY_STOP = {
    "The", "This", "That", "These", "Those", "A", "An", "It", "They", "We", "I",
    "In", "On", "At", "Our", "Their", "His", "Her", "Its", "There", "He", "She",
    "If", "As", "To", "Of", "For", "And", "But", "Or", "So", "When", "While",
}
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "to", "of", "and",
    "or", "in", "on", "at", "for", "with", "as", "by", "that", "this", "it", "its",
    "has", "have", "had", "will", "there", "a", "but", "from", "we", "our",
}


def _sentences(text: str) -> List[str]:
    """Atomic claims/units, split across scripts (Latin, CJK, Arabic, ...)."""
    return unicode_sentences(text)


def _words(text: str) -> List[str]:
    """Case/compat-folded, Unicode-aware tokens (CJK yields character bigrams)."""
    return word_tokens(text)


def _content_words(text: str) -> set:
    return {w for w in _words(text) if w not in _STOPWORDS}


def _normalize_number(token: str) -> str:
    """Canonicalize a number token for comparison ($50,000 -> 50000; 50% -> 50%)."""
    normalized = token.lstrip("$").replace(",", "").strip()
    suffix = "%" if normalized.endswith("%") else ""
    numeric = normalized.removesuffix("%")
    if "." in numeric:
        numeric = numeric.rstrip("0").rstrip(".")
    numeric = numeric.lstrip("0") or "0"
    return numeric + suffix


def _numbers(text: str) -> set:
    # NFKC folds fullwidth digits to ASCII; \d (Unicode) also matches Arabic-Indic
    # and other decimal digits, so numbers are compared within their own script.
    return {_normalize_number(m) for m in _NUMBER_RE.findall(nfkc(text))}


def _entities(text: str) -> set:
    found = set()
    for m in _ENTITY_RE.findall(text or ""):
        # Keep multi-word proper nouns whole; drop lone capitalized stopwords.
        if " " not in m and m in _ENTITY_STOP:
            continue
        found.add(m)
    return found


def _overlap(claim_words: set, source_words: set) -> float:
    """Fraction of a claim's content words that appear in the source."""
    if not claim_words:
        return 1.0
    return len(claim_words & source_words) / len(claim_words)


class GroundednessEvaluator(Evaluator):
    """Verify every claim in an output is supported by the source (anti-hallucination).

    Splits the output into atomic claims (sentences) and, for each, checks three
    things against the source: enough content-word overlap, every **number**
    present in the source, and every **named entity** present in the source. A
    claim that invents a figure ($50k -> $500k) or a name (a CEO who never
    appears) is flagged as ungrounded. The score is the fraction of grounded
    claims; ``details['ungrounded']`` lists exactly which claims failed and why.

    Deterministic and dependency-free by default (word/number/entity checks —
    *not* ROUGE or BLEU, which only measure surface overlap and miss invented
    facts). Supply an ``embedder`` to ground by *meaning* instead: each claim is
    scored by cosine similarity to the source, which is language-agnostic and
    paraphrase-tolerant (use a multilingual model for non-English text). For an
    LLM judge, compose a ``RubricJudge`` via ``ConsensusEvaluator``. The source is
    given at construction or per-call via ``context={'source': ...}``.
    """

    def __init__(self, source: str = "", threshold: float = 0.8, min_support: float = 0.5,
                 name: str = "groundedness", embedder: Optional[Any] = None,
                 semantic_min: float = 0.72):
        self._source = source
        self.threshold = threshold
        self.min_support = min_support
        self.name = name
        # Optional multilingual seam: when an EmbeddingProvider is supplied, each
        # claim is grounded by *meaning* (cosine similarity to the source) instead
        # of word overlap — language-agnostic and paraphrase-tolerant. Falls back
        # to the lexical path automatically if embedding is unavailable at runtime.
        self._embedder = embedder
        self.semantic_min = semantic_min
        self._src_vec_cache: Dict[str, List[List[float]]] = {}

    def evaluate(self, item: Any, context: Optional[dict] = None) -> Verdict:
        source = (context or {}).get("source", self._source) or ""
        claims = _sentences(str(item))
        if not claims:
            return Verdict(1.0, True, "empty output (vacuously grounded)", self.name)
        if self._embedder is not None:
            semantic = self._evaluate_semantic(claims, source)
            if semantic is not None:
                return semantic
            # Embedding unavailable (no network / model) -> fail safe to lexical.
        return self._evaluate_lexical(claims, source)

    def _evaluate_lexical(self, claims: List[str], source: str) -> Verdict:
        source_words = _content_words(source)
        source_numbers = _numbers(source)
        source_entities = _entities(source)
        source_folded = fold(source)

        grounded = 0
        ungrounded: List[Dict[str, str]] = []
        for claim in claims:
            claim_words = _content_words(claim)
            claim_numbers = _numbers(claim)
            support = _overlap(claim_words, source_words)
            bad_numbers = claim_numbers - source_numbers
            # An entity counts as invented only if it appears NOWHERE in the source
            # (case-folded): capitalized common words ("Coordinates") or partial
            # names ("The TES Tse") that DO occur in the source are not
            # hallucinations, so they must not be flagged.
            bad_entities = {
                e for e in (_entities(claim) - source_entities)
                if fold(e) not in source_folded
            }
            non_numeric_words = _content_words(_NUMBER_RE.sub("", nfkc(claim)))
            numeric_only_support = bool(claim_numbers) and not non_numeric_words
            if (support >= self.min_support or numeric_only_support) and not bad_numbers and not bad_entities:
                grounded += 1
            else:
                why = []
                if support < self.min_support:
                    why.append(f"low support ({support:.2f})")
                if bad_numbers:
                    why.append("invented numbers: " + ", ".join(sorted(bad_numbers)))
                if bad_entities:
                    why.append("invented entities: " + ", ".join(sorted(bad_entities)))
                ungrounded.append({"claim": claim, "reason": "; ".join(why)})
        return self._verdict(grounded, claims, ungrounded, method="lexical")

    def _evaluate_semantic(self, claims: List[str], source: str) -> Optional[Verdict]:
        """Ground each claim by cosine similarity to the source's sentences.

        Returns ``None`` (never a wrong answer) if embeddings can't be produced,
        so :meth:`evaluate` can fall back to the deterministic lexical path.
        """
        src_sents = _sentences(source) or ([source] if source.strip() else [])
        if not src_sents:
            return None
        try:
            from .intelligence.embedding import cosine
            src_vecs = self._src_vec_cache.get(source)
            if src_vecs is None:
                src_vecs = [self._embedder.embed(s) for s in src_sents]
                self._src_vec_cache[source] = src_vecs
        except Exception:
            return None
        source_numbers = _numbers(source)
        grounded = 0
        ungrounded: List[Dict[str, str]] = []
        for claim in claims:
            try:
                claim_vec = self._embedder.embed(claim)
            except Exception:
                return None  # mid-run failure -> lexical fallback for the whole item
            sim = max((cosine(claim_vec, sv) for sv in src_vecs), default=0.0)
            bad_numbers = _numbers(claim) - source_numbers
            if sim >= self.semantic_min and not bad_numbers:
                grounded += 1
            else:
                why = []
                if sim < self.semantic_min:
                    why.append(f"low semantic support ({sim:.2f})")
                if bad_numbers:
                    why.append("invented numbers: " + ", ".join(sorted(bad_numbers)))
                ungrounded.append({"claim": claim, "reason": "; ".join(why)})
        return self._verdict(grounded, claims, ungrounded, method="semantic")

    def _verdict(self, grounded: int, claims: List[str],
                 ungrounded: List[Dict[str, str]], *, method: str) -> Verdict:
        score = grounded / len(claims)
        if ungrounded:
            reasons = f"{len(ungrounded)}/{len(claims)} claim(s) not grounded: " + " | ".join(
                f"{u['claim']!r} ({u['reason']})" for u in ungrounded
            )
        else:
            reasons = f"all {len(claims)} claim(s) grounded in the source"
        return Verdict(
            score=score,
            passed=score >= self.threshold,
            reasons=reasons,
            evaluator=self.name,
            details={"ungrounded": ungrounded, "claims": len(claims), "method": method},
        )


class CoverageEvaluator(Evaluator):
    """Detect detail loss: check the source's critical points survive in the output.

    The complement of :class:`GroundednessEvaluator`. Where groundedness guards
    *precision* (no invented facts), coverage guards *recall* (no dropped facts) —
    the oversummarization failure where an agent flattens carefully reviewed
    material and quietly deletes a figure, a party, or a deadline.

    ``required`` is an explicit list of must-keep points; if omitted, the critical
    points are auto-extracted from the source (every number and named entity — the
    data most dangerous to lose). Score is the fraction present in the output;
    ``details['missing']`` names exactly what was dropped.
    """

    def __init__(self, source: str = "", required: Optional[List[str]] = None,
                 threshold: float = 0.8, name: str = "coverage"):
        self._source = source
        self._required = required
        self.threshold = threshold
        self.name = name

    def _auto_required(self, source: str) -> List[str]:
        return sorted(_numbers(source) | _entities(source))

    def evaluate(self, item: Any, context: Optional[dict] = None) -> Verdict:
        source = (context or {}).get("source", self._source) or ""
        required = self._required if self._required is not None else self._auto_required(source)
        if not required:
            return Verdict(1.0, True, "no required points to cover", self.name)

        text = str(item)
        haystack = fold(text)  # NFKC + casefold: substring test works in any script
        haystack_numbers = _numbers(text)
        present, missing = [], []
        for point in required:
            norm = _normalize_number(str(point))
            hit = (
                fold(point) in haystack
                or norm in haystack_numbers
                or fold(norm) in haystack
            )
            (present if hit else missing).append(point)

        score = len(present) / len(required)
        reasons = (
            f"all {len(required)} key point(s) preserved"
            if not missing
            else f"dropped {len(missing)}/{len(required)}: " + ", ".join(map(str, missing))
        )
        return Verdict(
            score=score,
            passed=score >= self.threshold,
            reasons=reasons,
            evaluator=self.name,
            details={"missing": missing, "present": present},
        )


def _score_sentences(sentences: List[str]) -> Dict[int, float]:
    """Centrality score per sentence: how much shared vocabulary it carries."""
    freq: Dict[str, int] = {}
    for sent in sentences:
        for w in _content_words(sent):
            freq[w] = freq.get(w, 0) + 1
    scores: Dict[int, float] = {}
    for i, sent in enumerate(sentences):
        words = _content_words(sent)
        scores[i] = (sum(freq[w] for w in words) / (len(words) + 1)) if words else 0.0
    return scores


def extractive_summary(texts, max_sentences: int = 5, keep_numeric_entities: bool = True) -> str:
    """Compress text by SELECTING salient sentences — grounded by construction.

    Unlike abstractive (generative) summarization, this copies whole sentences
    verbatim from the source, so it **cannot hallucinate** and every output claim
    is automatically grounded. Sentences carrying numbers or named entities are
    retained preferentially, so the data most often lost to oversummarization
    (figures, parties, dates) survives. Original order is preserved, so structure
    is not destroyed. This is a drop-in ``summarize=`` for
    :meth:`autarch.recall.RecallMemory.consolidate`.

    ``texts`` may be a string or a list of strings.
    """
    if isinstance(texts, str):
        texts = [texts]
    sentences: List[str] = []
    for block in texts:
        sentences.extend(_sentences(block))
    if len(sentences) <= max_sentences:
        return " ".join(sentences)

    scores = _score_sentences(sentences)
    if keep_numeric_entities:
        # A large boost guarantees fact-bearing sentences win a limited budget.
        for i, sent in enumerate(sentences):
            if _numbers(sent) or _entities(sent):
                scores[i] += 1000.0
    chosen = sorted(sorted(scores, key=lambda i: scores[i], reverse=True)[:max_sentences])
    return " ".join(sentences[i] for i in chosen)


def compress_history(turns, keep_recent: int = 3, max_summary_sentences: int = 5) -> str:
    """Compress a long conversation without destroying its structure (anti-"naive dump").

    Long-running agents must shrink past message history to fit the context
    window. Dumping old turns through a generative summarizer loses preferences
    and past conclusions and makes agents repeat mistakes. This keeps the most
    recent ``keep_recent`` turns **verbatim** (recency matters most) and
    *extractively* summarizes the older turns (so numbers, names, and stated
    preferences are preserved, never invented). The result is a structured digest,
    not a flattened blob.

    ``turns`` is a list of strings, or of ``{'role', 'content'}`` dicts.
    """
    def _text(turn) -> str:
        if isinstance(turn, dict):
            role = turn.get("role", "")
            content = turn.get("content", "")
            return f"{role}: {content}".strip(": ").strip() if role else str(content)
        return str(turn)

    items = [_text(t) for t in turns if _text(t)]
    if len(items) <= keep_recent:
        return "\n".join(items)

    older, recent = items[:-keep_recent], items[-keep_recent:]
    summary = extractive_summary(older, max_sentences=max_summary_sentences)
    parts = []
    if summary:
        parts.append("[Earlier conversation — key points]\n" + summary)
    parts.append("[Recent turns]\n" + "\n".join(f"- {t}" for t in recent))
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Content-safety evaluators — deterministic, model-free guardrails for the two
# risks every document- or tool-using agent faces: untrusted input trying to
# hijack the model (prompt injection), and sensitive data leaking into an output
# (PII). Both are first-line filters — cheap and explainable — not a substitute
# for a full moderation/DLP stack. For nuanced or semantic safety, add an LLM
# ``RubricJudge`` and combine via ``ConsensusEvaluator`` or ``EvaluationPanel``.
# --------------------------------------------------------------------------- #
_INJECTION_PATTERNS: Tuple[str, ...] = (
    r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+instructions",
    r"disregard\s+(?:the\s+)?(?:above|previous|prior|earlier)",
    r"forget\s+(?:everything|all|the\s+above)",
    r"you\s+are\s+now\b",
    r"new\s+instructions?\s*:",
    r"system\s+prompt",
    r"reveal\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions)",
    r"override\s+(?:your|the)\s+(?:instructions|rules|polic(?:y|ies))",
    r"do\s+not\s+(?:tell|inform)\s+the\s+user",
    r"</?\s*(?:system|instructions?)\s*>",
    r"exfiltrat",
)


class InjectionEvaluator(Evaluator):
    """Deterministic scan for prompt-injection / instruction-hijacking attempts.

    Searches the item for known injection tells — "ignore previous instructions",
    "reveal your system prompt", smuggled ``<system>`` tags, exfiltration verbs,
    and the like. Intended to run over **untrusted input** (a fetched page, a tool
    result, an uploaded document) *before* it reaches a model, so a malicious file
    cannot quietly rewrite the agent's instructions. Score is ``1.0`` when clean
    and ``0.0`` when any pattern matches; ``details['hits']`` lists the offending
    spans. Pass ``extra_patterns`` to extend the built-in set.

    Deterministic and model-free — a fast, deliberately conservative first-line
    filter; pair it with an LLM judge for paraphrased or obfuscated attacks.
    """

    DEFAULT_PATTERNS: Tuple[str, ...] = _INJECTION_PATTERNS

    def __init__(self, extra_patterns: Sequence[str] = (), name: str = "injection"):
        self._patterns: Tuple[str, ...] = tuple(self.DEFAULT_PATTERNS) + tuple(extra_patterns)
        self.name = name

    def evaluate(self, item: Any, context: Optional[dict] = None) -> Verdict:
        text = str(item)
        hits: List[str] = []
        for pat in self._patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                hits.append(m.group(0).strip())
        passed = not hits
        reasons = (
            "no injection patterns detected"
            if passed
            else f"{len(hits)} suspicious span(s): " + "; ".join(hits[:3])
        )
        return Verdict(
            score=1.0 if passed else 0.0,
            passed=passed,
            reasons=reasons,
            evaluator=self.name,
            details={"hits": hits},
        )


_PII_PATTERNS: Dict[str, str] = {
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "phone": r"\+\d{1,3}[\s.-]?\(?\d{2,4}\)?(?:[\s.-]?\d{2,4}){2,4}",
}


class PIIEvaluator(Evaluator):
    """Deterministic scan for personally identifiable information in an output.

    Flags emails, phone numbers, and US Social-Security numbers by default. Pass
    ``patterns`` (a ``label -> regex`` mapping) to override or extend for your
    jurisdiction. Intended to run over an agent's **output** before it flows
    downstream. Score is ``1.0`` when nothing matches, else ``0.0``;
    ``details['found']`` maps each category to a match count.

    Deterministic and model-free — a first-line filter, not a substitute for a
    full DLP/redaction pipeline (it will not catch names, addresses, or PII
    expressed in prose).
    """

    DEFAULT_PATTERNS: Dict[str, str] = _PII_PATTERNS

    def __init__(self, patterns: Optional[Dict[str, str]] = None, name: str = "pii"):
        self._patterns: Dict[str, str] = dict(patterns) if patterns is not None else dict(self.DEFAULT_PATTERNS)
        self.name = name

    def evaluate(self, item: Any, context: Optional[dict] = None) -> Verdict:
        text = str(item)
        found: Dict[str, int] = {}
        for kind, pat in self._patterns.items():
            matches = re.findall(pat, text)
            if matches:
                found[kind] = len(matches)
        passed = not found
        reasons = (
            "no PII detected"
            if passed
            else "found " + ", ".join(f"{k}x{n}" for k, n in found.items())
        )
        return Verdict(
            score=1.0 if passed else 0.0,
            passed=passed,
            reasons=reasons,
            evaluator=self.name,
            details={"found": found},
        )


# --------------------------------------------------------------------------- #
# Evaluation panels — group related evaluators under one label (e.g. "quality"
# or "safety") and run a selectable subset in a single pass. This is the reusable
# contract behind "score an output across many dimensions, and let the caller
# choose which dimensions apply" — so nothing has to be re-wired per call site.
# --------------------------------------------------------------------------- #
@dataclass
class PanelReport:
    """The combined result of an :class:`EvaluationPanel` run.

    ``verdicts`` maps each dimension that ran to its :class:`Verdict`; ``skipped``
    lists dimensions that were filtered out or had no item. ``passed`` is True only
    if every dimension that ran passed; ``score`` is the mean of their scores.
    """

    name: str
    verdicts: Dict[str, Verdict]
    skipped: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(v.passed for v in self.verdicts.values())

    @property
    def score(self) -> float:
        if not self.verdicts:
            return 1.0
        return sum(v.score for v in self.verdicts.values()) / len(self.verdicts)

    def rows(self) -> List[Tuple[str, float, bool, str]]:
        """Flatten to ``(dimension, score, passed, reasons)`` tuples for display."""
        return [(name, v.score, v.passed, v.reasons) for name, v in self.verdicts.items()]


class EvaluationPanel:
    """Run a named, configurable group of evaluators as one report.

    A *panel* groups related evaluators under a single label — e.g. a ``"quality"``
    panel (completeness, groundedness, coverage, an accuracy judge, ...) or a
    ``"safety"`` panel (injection, PII, a harmful-content judge, ...). Register the
    dimensions once as a ``name -> Evaluator`` mapping, then at call time choose
    which to run with ``include`` (whitelist) and/or ``exclude`` (skip); by default
    every dimension that receives an item is run — the sensible default.

    Each dimension scores its **own** item (passed via the ``items`` mapping), so a
    single panel can mix heterogeneous inputs — a source document for injection, a
    model output for PII, a prompt-plus-output for an accuracy judge — in one pass.
    A shared ``context`` dict is forwarded to every evaluator (e.g. a groundedness
    ``source``). Unknown names in ``include`` / ``exclude`` are ignored, so one
    filter can be applied across several panels. The panel is model-free unless one
    of its dimensions is an LLM judge.
    """

    def __init__(self, dimensions: Dict[str, Evaluator], *, name: str = "panel"):
        if not dimensions:
            raise ValueError("EvaluationPanel needs at least one dimension")
        self._dims: Dict[str, Evaluator] = dict(dimensions)
        self.name = name

    def dimensions(self) -> List[str]:
        """The names of every registered dimension, in registration order."""
        return list(self._dims)

    def evaluate(
        self,
        items: Dict[str, Any],
        *,
        context: Optional[dict] = None,
        include: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
    ) -> PanelReport:
        keep = set(include) if include is not None else None
        drop = set(exclude or ())
        verdicts: Dict[str, Verdict] = {}
        skipped: List[str] = []
        for dim, evaluator in self._dims.items():
            if (keep is not None and dim not in keep) or dim in drop or dim not in items:
                skipped.append(dim)
                continue
            verdicts[dim] = evaluator.evaluate(items[dim], context)
        return PanelReport(name=self.name, verdicts=verdicts, skipped=skipped)


def quality_panel(
    source: str = "",
    *,
    required: Optional[Sequence[str]] = None,
    coverage_source: Optional[str] = None,
    coverage_threshold: float = 0.5,
    judges: Optional[Dict[str, Evaluator]] = None,
    extra: Optional[Dict[str, Evaluator]] = None,
    name: str = "quality",
) -> EvaluationPanel:
    """Assemble a standard **quality** panel for a structured (JSON-object) output.

    Composes the reusable quality dimensions so a caller doesn't have to re-wire
    them:

      * ``completeness`` — every key in ``required`` is present and non-empty (the
        item fed to this dimension is the output as a JSON-object string);
      * ``groundedness`` — the output's claims are supported by ``source`` (guards
        precision: nothing invented);
      * ``coverage`` — the key points of ``coverage_source`` survive in the output
        (guards recall: nothing dropped), added only when ``coverage_source`` is
        given.

    Bring your own LLM ``judges`` (e.g. an accuracy or coherence ``RubricJudge``)
    and any ``extra`` deterministic checks; both are merged in as further
    dimensions. Returns an :class:`EvaluationPanel` — feed its ``evaluate`` a
    ``name -> item`` mapping and optional ``include`` / ``exclude`` to choose which
    dimensions run.
    """
    dims: Dict[str, Evaluator] = {}
    if required:
        dims["completeness"] = AssertionEvaluator(
            [(f"{k} present", (lambda kk: lambda s: bool(json.loads(s).get(kk)))(k)) for k in required]
        )
    dims["groundedness"] = GroundednessEvaluator(source=source)
    if coverage_source:
        dims["coverage"] = CoverageEvaluator(source=coverage_source, threshold=coverage_threshold)
    if judges:
        dims.update(judges)
    if extra:
        dims.update(extra)
    return EvaluationPanel(dims, name=name)


def safety_panel(
    model: Any = None,
    *,
    harm_rubric: Optional[str] = None,
    harm_threshold: float = 0.5,
    injection: bool = True,
    pii: bool = True,
    extra_injection_patterns: Sequence[str] = (),
    pii_patterns: Optional[Dict[str, str]] = None,
    extra: Optional[Dict[str, Evaluator]] = None,
    name: str = "safety",
) -> EvaluationPanel:
    """Assemble a standard **safety** panel of reusable content guardrails.

      * ``prompt_injection`` — deterministic scan of untrusted *input* text for
        instruction-hijacking attempts;
      * ``pii_exposure`` — deterministic scan of an *output* for leaked PII;
      * ``harmful_content`` — an LLM judge, added only when both ``model`` and
        ``harm_rubric`` are supplied.

    Toggle the deterministic scanners with ``injection`` / ``pii`` and extend them
    via ``extra_injection_patterns`` / ``pii_patterns``. Pass ``extra`` to add
    caller-specific dimensions (e.g. a governance check that the acting agent was
    capability-bound). Returns an :class:`EvaluationPanel` — feed its ``evaluate``
    a ``name -> item`` mapping and optional ``include`` / ``exclude``.
    """
    dims: Dict[str, Evaluator] = {}
    if injection:
        dims["prompt_injection"] = InjectionEvaluator(extra_patterns=extra_injection_patterns)
    if pii:
        dims["pii_exposure"] = PIIEvaluator(patterns=pii_patterns)
    if model is not None and harm_rubric:
        dims["harmful_content"] = RubricJudge(
            model, threshold=harm_threshold, name="harmful_content", rubric=harm_rubric
        )
    if extra:
        dims.update(extra)
    if not dims:
        raise ValueError(
            "safety_panel produced no dimensions; enable injection/pii, pass model+harm_rubric, or extra"
        )
    return EvaluationPanel(dims, name=name)


def check_grounding(
    fields: Dict[str, Any],
    source: str,
    *,
    exempt: Sequence[str] = (),
    min_support: float = 0.5,
    embedder: Optional[Any] = None,
) -> List[Tuple[str, str, str]]:
    """Deterministic anti-hallucination check for a structured ``field -> value``
    extraction. Flags any value not grounded in ``source`` — invented numbers or
    named entities, or too little word overlap. A value that appears verbatim
    (case-insensitively) in the source is always treated as grounded; keys in
    ``exempt`` are skipped (e.g. fields deliberately *inferred* rather than lifted
    verbatim). Returns ``[(field, value, reason), ...]`` — empty when all grounded.

    It *flags* rather than removes: deterministic grounding can false-positive (a
    partial name, a paraphrase), so deleting a value is the caller's decision. Pair
    it with an LLM ``RubricJudge`` and human review for high-stakes use. This is the
    precision half of faithfulness (nothing invented); :class:`CoverageEvaluator`
    is the recall half (nothing dropped).

    Pass ``embedder`` (any :class:`EmbeddingProvider`) to ground non-verbatim
    values by *meaning* rather than word overlap — the multilingual, paraphrase-
    tolerant path. It falls back to the lexical check if embedding is unavailable.
    """
    grounder = GroundednessEvaluator(source=source, min_support=min_support, embedder=embedder)
    haystack = " ".join(fold(source).split())  # NFKC + casefold: verbatim test is script-wide
    exempt_set = set(exempt)
    flagged: List[Tuple[str, str, str]] = []
    for key, raw in fields.items():
        if key in exempt_set:
            continue
        value = str(raw).strip()
        if not value:
            continue
        if " ".join(fold(value).split()) in haystack:
            continue  # appears verbatim in the source -> grounded
        if _equivalent_date_in_source(value, source):
            continue
        verdict = grounder.evaluate(value)
        if not verdict.passed:
            flagged.append((key, value, verdict.reasons))
    return flagged


def _equivalent_date_in_source(value: str, source: str) -> bool:
    """Whether an ISO date value appears in the source in a common US numeric format."""
    import datetime
    import re

    try:
        expected = datetime.date.fromisoformat(value)
    except ValueError:
        return False
    for month, day, year in re.findall(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2}|\d{4})\b", source):
        full_year = int(year) + 2000 if len(year) == 2 else int(year)
        try:
            if datetime.date(full_year, int(month), int(day)) == expected:
                return True
        except ValueError:
            continue
    return False


@dataclass
class Citation:
    """The passage in a source that best supports a value — a grounding *citation*.

    ``text`` is the quoted source sentence; ``start``/``end`` are its character offsets in the
    original source (valid for slicing and for pointing a reader at the evidence); ``score`` in
    ``[0, 1]`` is the support strength; ``method`` is how it was found (``'verbatim'``,
    ``'lexical'``, or ``'semantic'``). This turns a grounding check into visible, auditable
    provenance: not just *that* a value is supported, but *where*.
    """

    text: str
    start: int
    end: int
    score: float
    method: str

    def as_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "start": self.start, "end": self.end,
                "score": round(self.score, 4), "method": self.method}


class Citer:
    """Find the source passage that best supports a value — the citation behind a grounded fact.

    Verbatim substring wins (the value appears in a source sentence, case/accent/width-folded);
    otherwise the best source *sentence* by content-word overlap (lexical) or, when an ``embedder``
    is supplied, by cosine similarity (semantic — language-agnostic, paraphrase-tolerant). The
    source is parsed once (and, for semantic, embedded once and cached), so citing many values over
    one document is cheap. Deterministic and offline unless a networked embedder is used.

    Same seam as :class:`GroundednessEvaluator`: grounding proves nothing is invented; a citation
    shows the exact evidence. Model-agnostic — no provider-specific citation API required.
    """

    def __init__(self, source: str, *, embedder: Optional[Any] = None, min_score: float = 0.35):
        self._source = str(source or "")
        self._spans = unicode_sentence_spans(self._source)
        if not self._spans and self._source.strip():  # single-sentence / unpunctuated source
            stripped = self._source.strip()
            start = self._source.find(stripped)
            self._spans = [(stripped, max(start, 0), max(start, 0) + len(stripped))]
        self._folded = [" ".join(fold(t).split()) for t, _, _ in self._spans]
        self._embedder = embedder
        self._min_score = min_score
        self._src_vecs: Optional[List[List[float]]] = None

    def cite(self, value: Any) -> Optional[Citation]:
        """Return the best :class:`Citation` for ``value`` (or ``None`` if nothing clears the
        threshold). Verbatim match scores 1.0; otherwise the best-supporting sentence."""
        v = str(value or "").strip()
        if not v or not self._spans:
            return None
        v_fold = " ".join(fold(v).split())
        if v_fold:  # verbatim: the value occurs inside a source sentence (script-wide fold)
            for (text, start, end), folded in zip(self._spans, self._folded):
                if v_fold in folded:
                    return Citation(text, start, end, 1.0, "verbatim")
        if self._embedder is not None:
            cited = self._cite_semantic(v)
            if cited is not None:
                return cited
        return self._cite_lexical(v)

    def _cite_lexical(self, value: str) -> Optional[Citation]:
        v_words = _content_words(value) or set(_words(value))
        if not v_words:
            return None
        best: Optional[Citation] = None
        for text, start, end in self._spans:
            s_words = set(_words(text))
            if not s_words:
                continue
            score = len(v_words & s_words) / len(v_words)
            if best is None or score > best.score:
                best = Citation(text, start, end, score, "lexical")
        return best if (best is not None and best.score >= self._min_score) else None

    def _cite_semantic(self, value: str) -> Optional[Citation]:
        try:
            from .intelligence.embedding import cosine
            if self._src_vecs is None:
                self._src_vecs = [self._embedder.embed(t) for t, _, _ in self._spans]
            value_vec = self._embedder.embed(value)
        except Exception:
            return None  # embedding unavailable -> caller falls back to lexical
        best: Optional[Citation] = None
        for (text, start, end), svec in zip(self._spans, self._src_vecs):
            score = cosine(value_vec, svec)
            if best is None or score > best.score:
                best = Citation(text, start, end, score, "semantic")
        return best if (best is not None and best.score >= self._min_score) else None


def cite(value: Any, source: str, *, embedder: Optional[Any] = None,
         min_score: float = 0.35) -> Optional[Citation]:
    """Return the source passage that best supports ``value`` (or ``None``). Convenience wrapper
    that builds a one-off :class:`Citer`; use :class:`Citer` directly to cite many values over the
    same source (it caches sentence parsing and embeddings)."""
    return Citer(source, embedder=embedder, min_score=min_score).cite(value)
