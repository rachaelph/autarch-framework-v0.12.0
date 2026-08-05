"""Adapters — typed, governed actions on the world.

Each adapter declares the capabilities it provides and executes actions. The
kernel authorizes; the adapter performs (and re-checks its own boundaries).
"""
from __future__ import annotations

from .base import Adapter
from .document import DocumentAdapter
from .extraction import ExtractionAdapter
from .filesystem import FileSystemAdapter
from .search import SearchAdapter, SearchHit, VectorSearchAdapter
from .search_rest import (AzureAISearchAdapter, ElasticsearchAdapter,
                          RestSearchAdapter)
from .sql import (SQLAdapter, connect_mysql, connect_oracle, connect_postgres,
                  connect_sqlite, connect_sqlserver)
from .tool import ToolAdapter, from_callables, from_langchain_tools, from_mcp_tools

__all__ = [
    "Adapter",
    "DocumentAdapter",
    "ExtractionAdapter",
    "FileSystemAdapter",
    "SQLAdapter",
    "connect_sqlite",
    "connect_postgres",
    "connect_sqlserver",
    "connect_oracle",
    "connect_mysql",
    "SearchAdapter",
    "SearchHit",
    "VectorSearchAdapter",
    "RestSearchAdapter",
    "AzureAISearchAdapter",
    "ElasticsearchAdapter",
    "ToolAdapter",
    "from_callables",
    "from_langchain_tools",
    "from_mcp_tools",
]
