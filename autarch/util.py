"""Small shared utilities."""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from typing import List, Optional, Tuple

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)

# --- Unicode text hygiene ---------------------------------------------------
# Deterministic evaluators and reference-matching compare text across scripts.
# ``str.lower()`` and an ASCII ``[a-z0-9]+`` tokenizer silently drop every
# non-Latin character, so a French, Arabic, or Chinese value would compare as
# *empty* — vacuously "grounded" without ever being checked. These helpers make
# comparison Unicode-correct in any language without a single dependency.

# ``\w`` is already Unicode-aware for ``str`` in Python 3 (matches accented
# Latin, Cyrillic, Greek, CJK, Hangul, ...). One CJK run is a single match.
_WORD_UNICODE = re.compile(r"\w+", re.UNICODE)

# Scripts customarily written WITHOUT spaces between words: Hiragana, Katakana,
# CJK ideographs (BMP + Ext-A), Hangul, and CJK-compatibility/fullwidth forms.
# For these, whole-run tokens never overlap, so we fall back to character
# bigrams — the standard trick that makes lexical overlap meaningful for them.
_NO_SPACE_SCRIPT = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]"
)

# Sentence terminators across scripts. Latin terminators (. ! ? ;) must be followed by whitespace
# (or end of text) so decimals like ``0.48`` and abbreviations don't split mid-token; CJK/Arabic
# terminators (。！？；、 ۔) and the ellipsis need no following space (those scripts omit them).
_SENTENCE_SPLIT_UNICODE = re.compile(
    r"(?<=[.!?;])\s+"
    r"|(?<=[\u3002\uff01\uff1f\uff1b\u3001\u06d4\u2026])\s*"
    r"|\n+"
)


def nfc(text: object) -> str:
    """Canonical (NFC) Unicode normalization — for storage and display.

    Idempotent and lossless: composes accented characters to a single code point
    (``e`` + combining acute -> ``é``) so equal-looking strings compare equal.
    """
    return unicodedata.normalize("NFC", str(text if text is not None else ""))


def nfkc(text: object) -> str:
    """Compatibility (NFKC) normalization — for *matching*, not display.

    Folds fullwidth/compatibility forms to their canonical equivalents so that
    matching ignores presentation differences: fullwidth ``５`` -> ``5``, fullwidth
    Latin ``Ａ`` -> ``A``, the ligature ``ﬁ`` -> ``fi``, superscript ``²`` -> ``2``.
    (Distinct-script digits like Arabic-Indic ``٥`` keep their own code points; the
    Unicode-aware ``\\d`` in the number check matches those directly.)
    """
    return unicodedata.normalize("NFKC", str(text if text is not None else ""))


def fold(text: object) -> str:
    """Case-insensitive, script-wide comparison key: NFKC + ``casefold()``.

    ``casefold`` is the Unicode-aware sibling of ``lower`` (it folds ``ß`` -> ``ss``
    and lowercases Greek, Cyrillic, ... correctly), so this is the right key for
    every substring and set comparison the evaluators make.
    """
    return nfkc(text).casefold()


def word_tokens(text: object) -> List[str]:
    """Unicode-aware lexical tokens for overlap scoring in *any* script.

    Space-delimited words tokenize normally (folded for case/compat). Runs in a
    no-space script (CJK, Kana, Hangul) additionally yield character bigrams, so
    two Chinese sentences sharing vocabulary overlap instead of comparing as a
    single opaque token. Returns a flat list suitable for set-overlap scoring.
    """
    out: List[str] = []
    for word in _WORD_UNICODE.findall(fold(text)):
        if len(word) > 1 and _NO_SPACE_SCRIPT.search(word):
            out.extend(word[i : i + 2] for i in range(len(word) - 1))  # bigrams
        else:
            out.append(word)
    return out


def unicode_sentences(text: object) -> List[str]:
    """Split ``text`` into sentences across scripts (Latin, CJK, Arabic, ...)."""
    raw = str(text if text is not None else "")
    return [s.strip() for s in _SENTENCE_SPLIT_UNICODE.split(raw) if s and s.strip()]


def unicode_sentence_spans(text: object) -> List[Tuple[str, int, int]]:
    """Like :func:`unicode_sentences`, but also return each sentence's ``(start, end)`` character
    offsets in the ORIGINAL string, so a matched sentence can be quoted AND located exactly.

    Offsets index the original ``text`` (before any folding), so they stay valid for slicing and
    for pointing a reader at the supporting passage. Used to build grounding citations.
    """
    raw = str(text if text is not None else "")
    spans: List[Tuple[str, int, int]] = []
    pos = 0
    for m in _SENTENCE_SPLIT_UNICODE.finditer(raw):
        seg = raw[pos:m.start()]
        stripped = seg.strip()
        if stripped:
            start = pos + (len(seg) - len(seg.lstrip()))
            spans.append((stripped, start, start + len(stripped)))
        pos = m.end()
    seg = raw[pos:]
    stripped = seg.strip()
    if stripped:
        start = pos + (len(seg) - len(seg.lstrip()))
        spans.append((stripped, start, start + len(stripped)))
    return spans


def configure_sqlite(conn: sqlite3.Connection) -> None:
    """Tune a SQLite connection for safe concurrent local-first use.

    All settings are stdlib-only (no external services): WAL lets readers and a
    writer proceed concurrently, a busy timeout makes contending connections wait
    briefly instead of erroring, and NORMAL synchronous is the safe, fast pairing
    with WAL. In-memory databases ignore WAL harmlessly.
    """
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except sqlite3.Error:
        # Never let a pragma tweak break opening the database.
        pass


def _load_object(text: str) -> Optional[dict]:
    """Parse `text` as JSON, returning it only if it is an object."""
    try:
        obj = json.loads(text)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _first_balanced_object(text: str) -> Optional[str]:
    """Return the first complete, balanced ``{...}`` block in `text`.

    Brace-counts while respecting string literals and escapes, so it survives
    trailing prose, multiple objects, or braces inside string values.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json(text: str) -> Optional[dict]:
    """Best-effort extraction of a JSON object from model output.

    Real models wrap JSON in prose, markdown fences, or trailing commentary. This
    pulls out the first valid object regardless. Returns None if nothing
    parseable is found.
    """
    if not text:
        return None

    candidate = text.strip()

    # If the model fenced its answer (```json ... ```), prefer the fenced block.
    fenced = _FENCE_RE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    # Try the whole thing first (the happy path for JSON-mode models).
    obj = _load_object(candidate)
    if obj is not None:
        return obj

    # Otherwise scan for the first complete, balanced object.
    block = _first_balanced_object(candidate)
    if block is not None:
        return _load_object(block)
    return None
