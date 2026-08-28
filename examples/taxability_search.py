"""Create, seed, and query the Circle K taxability vector index.

The embedding input contains only ``item_type`` and ``embedding_description``.
``exclusions`` are stored as retrievable metadata for a later rules or LLM
classification stage and are deliberately never sent to the embedding model.

Authentication uses Microsoft Entra ID. Set AZURE_OPENAI_TENANT_ID to pin Azure
CLI authentication to a tenant; otherwise DefaultAzureCredential is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

DEFAULT_SOURCE = Path(__file__).with_name("reference") / "seed-taxability-matrix-descriptive.json"
DEFAULT_INDEX = "taxability-categories"
VECTOR_FIELD = "description_vector"
VECTOR_PROFILE = "taxability-vector-profile"
VECTOR_ALGORITHM = "taxability-hnsw"


@dataclass(frozen=True)
class Settings:
    search_endpoint: str
    openai_endpoint: str
    embedding_deployment: str
    index_name: str = DEFAULT_INDEX
    embedding_dimensions: int = 1536
    openai_api_version: str = "2024-12-01-preview"
    tenant_id: str | None = None

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            search_endpoint=_required_env("AZURE_SEARCH_ENDPOINT"),
            openai_endpoint=_required_env("AZURE_OPENAI_ENDPOINT"),
            embedding_deployment=_required_env("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
            index_name=os.getenv("AZURE_SEARCH_INDEX", DEFAULT_INDEX),
            embedding_dimensions=_positive_int_env("AZURE_OPENAI_EMBEDDING_DIMENSIONS", 1536),
            openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
            tenant_id=os.getenv("AZURE_OPENAI_TENANT_ID") or os.getenv("AZURE_TENANT_ID"),
        )


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Required environment variable {name} is not set")
    return value


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    value = int(raw) if raw else default
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def embedding_text(item_type: str, description: str) -> str:
    """Build the positive-only text embedded for a category."""
    return f"{item_type.strip()}\n\n{description.strip()}"


def document_id(item_type: str) -> str:
    """Return a deterministic, Azure AI Search-safe key."""
    return hashlib.sha256(item_type.strip().encode("utf-8")).hexdigest()[:24]


def load_categories(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path} must contain a non-empty JSON array")

    categories: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, record in enumerate(value, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"Record {position} must be a JSON object")
        item_type = record.get("item_type")
        description = record.get("embedding_description")
        exclusions = record.get("exclusions")
        if not isinstance(item_type, str) or not item_type.strip():
            raise ValueError(f"Record {position} has an invalid item_type")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"Record {position} has an invalid embedding_description")
        if (
            not isinstance(exclusions, list)
            or not exclusions
            or any(not isinstance(item, str) or not item.strip() for item in exclusions)
        ):
            raise ValueError(f"Record {position} has invalid exclusions")
        if item_type in seen:
            raise ValueError(f"Duplicate item_type: {item_type}")
        seen.add(item_type)
        categories.append(
            {
                "item_type": item_type.strip(),
                "embedding_description": description.strip(),
                "exclusions": [item.strip() for item in exclusions],
            }
        )
    return categories


def _credential(tenant_id: str | None):
    try:
        from azure.identity import AzureCliCredential, DefaultAzureCredential
    except ImportError as exc:
        raise RuntimeError("Install the search dependencies with: pip install -e '.[search]'") from exc
    if tenant_id:
        return AzureCliCredential(tenant_id=tenant_id)
    return DefaultAzureCredential()


def _embedding_client(settings: Settings, credential):
    try:
        from azure.identity import get_bearer_token_provider
        from openai import AzureOpenAI
    except ImportError as exc:
        raise RuntimeError("Install the search dependencies with: pip install -e '.[search]'") from exc
    token_provider = get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )
    return AzureOpenAI(
        azure_endpoint=settings.openai_endpoint,
        api_version=settings.openai_api_version,
        azure_ad_token_provider=token_provider,
    )


def embed_texts(client, settings: Settings, texts: Sequence[str]) -> list[list[float]]:
    if not texts:
        return []
    response = client.embeddings.create(
        model=settings.embedding_deployment,
        input=list(texts),
        dimensions=settings.embedding_dimensions,
        encoding_format="float",
    )
    vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
    if len(vectors) != len(texts):
        raise RuntimeError(f"Embedding service returned {len(vectors)} vectors for {len(texts)} texts")
    wrong_sizes = {len(vector) for vector in vectors if len(vector) != settings.embedding_dimensions}
    if wrong_sizes:
        raise RuntimeError(
            "Embedding vector dimensions do not match AZURE_OPENAI_EMBEDDING_DIMENSIONS: "
            f"expected {settings.embedding_dimensions}, received {sorted(wrong_sizes)}"
        )
    return vectors


def create_index(settings: Settings, credential) -> None:
    try:
        from azure.search.documents.indexes import SearchIndexClient
        from azure.search.documents.indexes.models import (
            HnswAlgorithmConfiguration,
            HnswParameters,
            SearchableField,
            SearchField,
            SearchFieldDataType,
            SearchIndex,
            SimpleField,
            VectorSearch,
            VectorSearchAlgorithmMetric,
            VectorSearchProfile,
        )
    except ImportError as exc:
        raise RuntimeError("Install the search dependencies with: pip install -e '.[search]'") from exc

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
        SearchableField(name="item_type", type=SearchFieldDataType.String, retrievable=True),
        SearchableField(
            name="embedding_description", type=SearchFieldDataType.String, retrievable=True
        ),
        SearchField(
            name="exclusions",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            searchable=False,
            retrievable=True,
        ),
        SearchField(
            name=VECTOR_FIELD,
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            retrievable=False,
            stored=False,
            vector_search_dimensions=settings.embedding_dimensions,
            vector_search_profile_name=VECTOR_PROFILE,
        ),
    ]
    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(
                name=VECTOR_ALGORITHM,
                parameters=HnswParameters(metric=VectorSearchAlgorithmMetric.COSINE),
            )
        ],
        profiles=[
            VectorSearchProfile(
                name=VECTOR_PROFILE,
                algorithm_configuration_name=VECTOR_ALGORITHM,
            )
        ],
    )
    index = SearchIndex(name=settings.index_name, fields=fields, vector_search=vector_search)
    SearchIndexClient(settings.search_endpoint, credential).create_or_update_index(index)
    print(f"Created or updated index {settings.index_name!r}.")


def seed_index(settings: Settings, credential, source: Path) -> None:
    try:
        from azure.search.documents import SearchClient
    except ImportError as exc:
        raise RuntimeError("Install the search dependencies with: pip install -e '.[search]'") from exc

    categories = load_categories(source)
    embedding_client = _embedding_client(settings, credential)
    texts = [embedding_text(row["item_type"], row["embedding_description"]) for row in categories]
    vectors = embed_texts(embedding_client, settings, texts)
    documents = [
        {
            "id": document_id(row["item_type"]),
            "item_type": row["item_type"],
            "embedding_description": row["embedding_description"],
            "exclusions": row["exclusions"],
            VECTOR_FIELD: vector,
        }
        for row, vector in zip(categories, vectors)
    ]

    client = SearchClient(settings.search_endpoint, settings.index_name, credential)
    results = client.upload_documents(documents)
    failures = [result for result in results if not result.succeeded]
    if failures:
        details = "; ".join(
            f"{result.key}: {result.error_message or 'unknown indexing error'}" for result in failures
        )
        raise RuntimeError(f"Failed to upload {len(failures)} documents: {details}")
    print(f"Uploaded {len(documents)} taxability categories to {settings.index_name!r}.")


def hybrid_search(settings: Settings, credential, query: str, top: int) -> list[dict[str, Any]]:
    try:
        from azure.search.documents import SearchClient
        from azure.search.documents.models import VectorizedQuery
    except ImportError as exc:
        raise RuntimeError("Install the search dependencies with: pip install -e '.[search]'") from exc

    embedding_client = _embedding_client(settings, credential)
    query_vector = embed_texts(embedding_client, settings, [query])[0]
    vector_query = VectorizedQuery(
        vector=query_vector,
        k_nearest_neighbors=top,
        fields=VECTOR_FIELD,
    )
    results = SearchClient(settings.search_endpoint, settings.index_name, credential).search(
        search_text=query,
        search_fields=["item_type", "embedding_description"],
        vector_queries=[vector_query],
        select=["id", "item_type", "embedding_description", "exclusions"],
        top=top,
    )
    return [
        {
            "rank": rank,
            "score": result.get("@search.score"),
            "id": result["id"],
            "item_type": result["item_type"],
            "embedding_description": result["embedding_description"],
            "exclusions": result.get("exclusions", []),
        }
        for rank, result in enumerate(results, start=1)
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create, seed, and hybrid-query an Azure AI Search taxability index."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("create-index", help="Create or update the vector index schema.")
    seed = subparsers.add_parser("seed", help="Embed and upload the JSON taxability categories.")
    seed.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    setup = subparsers.add_parser("setup", help="Create the index, then embed and upload categories.")
    setup.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    query = subparsers.add_parser("query", help="Run hybrid keyword and vector retrieval.")
    query.add_argument("text", help="Invoice line or product/service description.")
    query.add_argument("--top", type=int, default=5, help="Candidate count (default: 5).")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if getattr(args, "top", 1) <= 0:
        raise ValueError("--top must be a positive integer")
    settings = Settings.from_environment()
    credential = _credential(settings.tenant_id)
    if args.command in {"create-index", "setup"}:
        create_index(settings, credential)
    if args.command in {"seed", "setup"}:
        seed_index(settings, credential, args.source)
    if args.command == "query":
        matches = hybrid_search(settings, credential, args.text, args.top)
        print(json.dumps(matches, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
