"""Multilingual evaluation — Unicode hygiene (Tier 2) and semantic grounding (Tier 1).

Everything here is deterministic and offline (the semantic path uses the offline
HashingEmbedder), so these tests pin the cross-language behaviour without a model
or a network.
"""
from __future__ import annotations

from autarch import Citation, Citer, CoverageEvaluator, GroundednessEvaluator, check_grounding, cite
from autarch.intelligence.embedding import HashingEmbedder
from autarch.util import fold, nfc, nfkc, unicode_sentence_spans, unicode_sentences, word_tokens


# --- Tier 2: Unicode text hygiene -------------------------------------------------------------- #

def test_fold_is_casefold_and_accent_wide():
    assert fold("Stra\u00dfe") == "strasse"   # \u00df (ß) -> ss, which .lower() cannot do
    assert fold("\u00c9T\u00c9") == "\u00e9t\u00e9"  # ÉTÉ -> été
    assert fold("  Mixed CASE  ".strip()) == "mixed case"


def test_nfc_and_nfkc_normalization():
    assert nfc("e\u0301") == "\u00e9"          # e + combining acute -> composed é
    assert nfkc("\uff15\uff10") == "50"         # fullwidth digits -> ASCII
    assert nfkc("\uff21\uff22\uff23") == "ABC"  # fullwidth Latin -> ASCII


def test_word_tokens_latin_and_cjk_bigrams():
    assert word_tokens("Hello WORLD") == ["hello", "world"]
    assert word_tokens("\u00c9valuation") == ["\u00e9valuation"]
    # a no-space CJK run becomes character bigrams so overlap is meaningful
    assert word_tokens("\u4e2d\u6587\u6863") == ["\u4e2d\u6587", "\u6587\u6863"]


def test_unicode_sentences_multiscript():
    text = "\u4f60\u597d\u3002\u518d\u89c1\uff01ok."  # 你好。再见！ok.
    assert unicode_sentences(text) == ["\u4f60\u597d\u3002", "\u518d\u89c1\uff01", "ok."]


# --- Tier 2: deterministic grounding is no longer vacuous for non-ASCII ------------------------- #

def test_groundedness_french_grounded_lexically():
    src = "Le projet de barrage est situ\u00e9 sur la rivi\u00e8re. Le budget est de 50000 euros."
    g = GroundednessEvaluator(source=src)
    v = g.evaluate("Le projet de barrage est situ\u00e9 sur la rivi\u00e8re.")
    assert v.passed
    assert v.details["method"] == "lexical"


def test_groundedness_flags_invented_number_in_french():
    src = "Le projet de barrage est situ\u00e9 sur la rivi\u00e8re. Le budget est de 50000 euros."
    g = GroundednessEvaluator(source=src)
    bad = g.evaluate("Le budget du projet est de 999999 euros.")
    assert not bad.passed
    assert any("999999" in u["reason"] for u in bad.details["ungrounded"])


def test_groundedness_cjk_not_vacuously_grounded():
    src = "\u9879\u76ee\u4f4d\u4e8e\u6cb3\u6d41\u4e0a\u3002"  # 项目位于河流上。
    g = GroundednessEvaluator(source=src)
    assert g.evaluate("\u9879\u76ee\u4f4d\u4e8e\u6cb3\u6d41\u4e0a\u3002").passed
    # unrelated CJK content must NOT pass as vacuously grounded
    unrelated = g.evaluate("\u8fd9\u662f\u5b8c\u5168\u4e0d\u540c\u7684\u5185\u5bb9\u554a\u3002")
    assert unrelated.score < 1.0


# --- Tier 1: embedding-based semantic grounding ------------------------------------------------ #

def test_groundedness_semantic_path_uses_embedder():
    src = "Le projet de barrage est situ\u00e9 sur la rivi\u00e8re."
    g = GroundednessEvaluator(source=src, embedder=HashingEmbedder(dim=256), semantic_min=0.5)
    v = g.evaluate("Le projet de barrage est situ\u00e9 sur la rivi\u00e8re.")
    assert v.passed
    assert v.details["method"] == "semantic"


def test_groundedness_falls_back_to_lexical_when_embedder_raises():
    class BrokenEmbedder:
        name = "broken"

        def embed(self, text):
            raise RuntimeError("no network")

    src = "Le projet est situ\u00e9 sur la rivi\u00e8re."
    g = GroundednessEvaluator(source=src, embedder=BrokenEmbedder())
    v = g.evaluate("Le projet est situ\u00e9 sur la rivi\u00e8re.")
    assert v.details["method"] == "lexical"   # embedding failure -> deterministic fallback
    assert v.passed


def test_semantic_source_vectors_are_cached_per_source():
    class CountingEmbedder(HashingEmbedder):
        def __init__(self):
            super().__init__(dim=128)
            self.calls = 0

        def embed(self, text):
            self.calls += 1
            return super().embed(text)

    emb = CountingEmbedder()
    src = "Alpha. Beta. Gamma."
    g = GroundednessEvaluator(source=src, embedder=emb, semantic_min=0.4)
    g.evaluate("Alpha.")
    after_first = emb.calls
    g.evaluate("Beta.")
    # the 3 source-sentence embeddings are reused; the 2nd call only embeds its own claim
    assert emb.calls == after_first + 1


# --- Coverage + check_grounding are script-wide too -------------------------------------------- #

def test_coverage_is_accent_and_case_insensitive():
    src = "Budget 50000. Rivi\u00e8re G\u00e9n\u00e9ral."
    cov = CoverageEvaluator(source=src, required=["50000", "G\u00e9n\u00e9ral"])
    v = cov.evaluate("le budget est 50000 pour le projet g\u00e9n\u00e9ral")
    assert not v.details["missing"]


def test_check_grounding_folded_verbatim_and_embedder_param():
    src = "Le projet est situ\u00e9 sur la rivi\u00e8re. Budget 50000."
    # folded verbatim match (case/accent-insensitive) -> nothing flagged
    assert check_grounding({"loc": "Sur La Rivi\u00e8re", "b": "50000"}, src) == []
    # the embedder argument is accepted and a grounded value stays unflagged
    assert check_grounding({"loc": "sur la rivi\u00e8re"}, src, embedder=HashingEmbedder()) == []

# --- Grounding citations (evidence: the supporting source passage) ----------------------------- #

def test_sentence_spans_offsets_and_decimals_stay_whole():
    src = "Area is 0.48 km2. Budget 50000 euros. \\u4f60\\u597d\\u3002"  # decimals must NOT split at '.'
    spans = unicode_sentence_spans(src)
    for text, start, end in spans:
        assert src[start:end] == text            # offsets index the original string exactly
    assert any("0.48 km2" in t for t, _, _ in spans)  # 0.48 kept whole (\\s+ after Latin '.')
    assert spans[-1][0] == "\\u4f60\\u597d\\u3002"    # CJK terminator still splits without a space


def test_cite_verbatim_returns_locating_offsets():
    src = "Le projet est situ\\u00e9 sur la rivi\\u00e8re. The reservoir area is 0.48 km2 at 631 masl."
    c = cite("area is 0.48 km2", src)
    assert isinstance(c, Citation)
    assert c.method == "verbatim" and c.score == 1.0
    assert src[c.start:c.end] == c.text and "0.48 km2" in c.text


def test_cite_lexical_picks_best_sentence():
    src = "Le projet est situ\\u00e9 sur la rivi\\u00e8re. Le budget total est de 50000 euros."
    c = cite("budget du projet en euros", src)
    assert c is not None and "budget" in c.text.lower()
    assert c.method in ("lexical", "verbatim")


def test_citer_semantic_caches_source_vectors():
    class CountingEmbedder(HashingEmbedder):
        def __init__(self):
            super().__init__(dim=128)
            self.calls = 0

        def embed(self, text):
            self.calls += 1
            return super().embed(text)

    emb = CountingEmbedder()
    citer = Citer("Alpha river dam. Beta budget euros. Gamma reservoir.", embedder=emb, min_score=0.0)
    citer.cite("hydroelectric barrier")   # not a verbatim substring -> forces the semantic path
    after_first = emb.calls           # 3 source sentences + 1 value embedded
    citer.cite("fiscal spending")     # source vectors reused; only the new value is embedded
    assert emb.calls == after_first + 1
    assert citer.cite("monetary allocation").method == "semantic"


def test_cite_returns_none_when_unsupported():
    src = "Le projet est situ\\u00e9 sur la rivi\\u00e8re. Budget 50000 euros."
    assert cite("quantum entanglement of penguins", src, min_score=0.5) is None