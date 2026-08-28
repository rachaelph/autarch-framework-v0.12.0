"""Offline tests for the Azure AI Search taxability seed helpers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.taxability_search import document_id, embedding_text, load_categories


SOURCE = (
    Path(__file__).parents[1]
    / "examples"
    / "reference"
    / "seed-taxability-matrix-descriptive.json"
)


def test_seed_file_has_valid_unique_categories() -> None:
    categories = load_categories(SOURCE)

    assert len(categories) == 40
    assert len({row["item_type"] for row in categories}) == 40


def test_embedding_text_omits_exclusions() -> None:
    category = load_categories(SOURCE)[0]

    text = embedding_text(category["item_type"], category["embedding_description"])

    assert text == f"{category['item_type']}\n\n{category['embedding_description']}"
    assert not any(exclusion in text for exclusion in category["exclusions"])


def test_document_id_is_deterministic_and_search_safe() -> None:
    first = document_id("SOFTWARE AS A SERVICE (SaaS)")

    assert first == document_id("SOFTWARE AS A SERVICE (SaaS)")
    assert len(first) == 24
    assert first.isalnum()


def test_load_categories_rejects_invalid_exclusions(tmp_path: Path) -> None:
    source = tmp_path / "invalid.json"
    source.write_text(
        json.dumps(
            [
                {
                    "item_type": "TEST",
                    "embedding_description": "Positive category description.",
                    "exclusions": [],
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid exclusions"):
        load_categories(source)
