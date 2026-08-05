# Changelog

## 0.12.0 — external connectors + smart document extraction

Extends governed data access (0.11.0) from "SQLite + in-memory index" to real,
external sources, and adds structured **and** unstructured document extraction.

### Added
- **External database connectors** (`autarch/adapters/sql.py`) — one-call, governed
  helpers, each lazily importing its driver (no new hard dependency): `connect_postgres`
  (psycopg/psycopg2), `connect_sqlserver` (pyodbc), `connect_oracle` (oracledb),
  `connect_mysql` (PyMySQL). Dialect-aware placeholders (`%s` / `?` / `:1`) and
  catalog queries (`information_schema` / `user_tables` / `sqlite_master`) so schema
  introspection and parameter binding are correct per engine.
- **AI search connectors** (`autarch/adapters/search_rest.py`) — `AzureAISearchAdapter`
  (keyword and vector search), `ElasticsearchAdapter` (match and kNN, also OpenSearch),
  and a `RestSearchAdapter` base for any JSON search API (Pinecone, Weaviate, ...).
  Stdlib `urllib`, injectable transport, offline-tested request/response shaping.
- **Document extraction** (`autarch/adapters/extraction.py`) — `ExtractionAdapter` with
  two governed capabilities: `doc.parse` (deterministic: CSV/TSV -> records, JSON ->
  object, txt/md/HTML -> clean text, PDF via optional pypdf) and `doc.extract` (smart,
  schema-guided field extraction from unstructured prose via any model provider,
  chunked, null-safe, no hallucinated shapes).
- **Tests** — `test_search_rest.py` (4), `test_extraction.py` (11), plus Postgres/Oracle
  dialect tests. Example: extended `examples/governed_data.py`.

### Honest boundary
- Database connectors are validated against SQLite plus fake-DB-API unit tests for the
  Postgres/Oracle dialect shaping; live Oracle/SQL Server/Postgres/MySQL were not run
  in this environment (no servers, drivers not installed). They work by the standard
  DB-API 2.0 contract.
- AI-search adapters are tested offline against recorded response shapes; live behavior
  depends on your service, index schema, and credentials.

## 0.11.0 — governed data access (SQL + AI search)

The framework's answer to "connect the agent to any data source": not another
connector zoo, but **governed, provable** data access. Every query an intelligence
issues is capability-scoped, injection-safe, privacy-aware, and signed into the
tamper-evident ledger.

### Added

- **`SQLAdapter`** (`autarch/adapters/sql.py`) — governed access to any SQL
  database over **DB-API 2.0**, so the same adapter works with SQLite, Postgres
  (psycopg), SQL Server (pyodbc), Oracle (oracledb), and MySQL by swapping only
  the driver — with **no new dependency** in the framework itself.
  - **Read-only by default**; writes/DDL require both ``allow_writes=True`` and a
    separately granted ``db.execute`` capability.
  - **Injection-safe**: bound parameters (never string-formatted); stacked
    ``…; DROP…`` statements rejected; non-SELECT refused on ``db.query``.
  - **Scoped**: table allow/deny lists and an enforced row cap.
  - **Privacy-aware**: configured columns redacted from results; the ledger logs
    SQL + row counts, not row data, by default.
  - **Schema introspection** (``db.schema``) so an agent can discover tables and
    columns (governed, read-only) before it queries.
  - ``connect_sqlite()`` convenience for demos/tests.
- **Search adapters** (`autarch/adapters/search.py`) — ``SearchAdapter`` base
  (the seam for Azure AI Search, Elasticsearch/OpenSearch kNN, Pinecone, pgvector)
  plus ``VectorSearchAdapter``, a dependency-free in-memory vector index over the
  framework's ``EmbeddingProvider`` (offline hashing, local Ollama, or cloud
  OpenAI embeddings). Governed ``search.query`` with top-k cap and min-score.
- **Tests** — `tests/test_sql_adapter.py` (12, against real SQLite) and
  `tests/test_search_adapter.py` (6), including end-to-end governance through the
  kernel. Example: `examples/governed_data.py`.

### Honest boundary — layered defense, not a silver bullet

- The strongest guarantee that an agent "can never drop a table" is a **read-only
  database role** at the server. The adapter's SQL parsing is deliberate
  defense-in-depth on top of that, never a replacement for the database's own
  permission system. Use both.
- Tested against SQLite only (stdlib). Oracle/SQL Server/Postgres work by the same
  DB-API contract but were not live-tested in this environment.
- External AI-search indexes need their own client/credentials; the base adapter
  is the governed seam, not a shipped integration for each vendor.

## 0.10.2 — provider ergonomics fix

### Fixed
- `build_provider` (and `council=[...]`) now accept **bare model names** like
  `"gpt-4o"`, `"o3-mini"`, and `"claude-3-5-sonnet-latest"`, not just the
  `"openai:gpt-4o"` form. A mixed-vendor council written the natural way now works.
- `build_embedder` accepts bare `"text-embedding-3-small"` too.

## 0.10.1 — semantic recall out of the box

Closes the one gap left open in 0.10.0: meaning-aware long-term memory without
having to run a local model.

### Added

- **Cloud embedder** (`autarch/intelligence/openai_embedding.py`) —
  `OpenAIEmbedder`, real learned embeddings from the OpenAI embeddings API over
  stdlib `urllib` (no third-party dependency), with `embed` and a batched
  `embed_batch`. Recall's semantic path now works with just `OPENAI_API_KEY` set,
  matching the cloud chat providers added in 0.10.0.
- **Embedder factory** — `build_embedder("hash[:dim]" | "ollama[:model]" |
  "openai[:model]")`, and `Agent(..., embedder="openai")` now accepts a string
  spec, mirroring `council=["gpt-4o", ...]`.
- **Embedding prices** added to the price book (`text-embedding-3-small/large`,
  `ada-002`) so recall's embedding calls are budgetable.
- **Tests** (`tests/test_embedding_cloud.py`) — offline provider shaping plus an
  end-to-end test proving semantic recall retrieves a "refund policy" note from a
  "how do I get my money back" query that shares **no** content words with it.

### Honest boundary

- Like the OpenAI chat provider, the embedder is unit-tested offline only;
  `api.openai.com` is not reachable from the build environment, so live calls are
  unverified until run with a real key.

## 0.10.0 — the governance upgrade

This release closes the gaps between the pitch ("preside over a council of GPT,
Claude, and a local model; prove and govern every action") and the code, without
adding a single runtime dependency. The original 390 tests remain green; 69 new
tests (459 total) cover the additions, including an adversarial red-team suite.

### Added

- **General scope algebra** (`autarch/scoping.py`). The kernel understood only
  `path_prefix` and `max_bytes`. Grants now carry typed, composable, deterministic
  constraints, all enforced by the same kernel and narrowed by subset under
  delegation:
  - `host_allowlist`, `port_allowlist` — network egress control
  - `enum`, `regex`, `forbid_substrings` — value membership / shape / prohibition
  - `forbid_data_classes` — block actions tagged with e.g. PHI / PII
  - `amount_max`, `count_max` — numeric spend / quantity ceilings

- **Real cloud providers** — `OpenAIProvider` and `AnthropicProvider`
  (`autarch/intelligence/{openai,anthropic}.py`), stdlib-only, with the same typed
  error contract as the Ollama provider so resilience wraps them automatically.
  Injectable transport for offline testing. Factory specs: `openai[:model]`,
  `anthropic[:model]`, aliases `gpt` / `claude`.

- **Model price book** (`autarch/intelligence/pricing.py`) — real per-token list
  prices and a `PriceBook` so the economic kernel can meter actual model spend.

- **Deliberative debate** (`Council.debate`, `Agent(debate_rounds=…)`). Voices see
  each other's critiques and reconsider across rounds; the full exchange is
  recorded in `Deliberation.transcript`. Stops early on consensus or stability.

- **Governance gateway** (`autarch/gateway.py`) — `GovernanceGateway` +
  `GatewayClient`. The kernel/policy/budget pipeline behind a stdlib HTTP control
  plane; actions requiring ratification are parked in the approval plane.

- **Async approval plane** (`autarch/approval.py`) — `ApprovalQueue` / `Approval`.
  Out-of-band, quorum-aware ratify/overrule with TTL expiry; durable and
  concurrency-safe (SQLite/WAL). A blocking `wait` for callers that pause.

- **Compliance evidence** (`autarch/compliance.py`) — `ComplianceReporter` maps
  the ledger to SOC 2 / EU AI Act / HIPAA control evidence, a Markdown report, and
  a portable self-verifying evidence bundle.

- **Policy DSL** (`autarch/policydsl.py`) — declarative, JSON-serializable policy
  conditions compiled to kernel `Policy` objects, plus `simulate` and `diff` to
  review policy changes before deploying.

- **Kernel verification** (`autarch/verification.py`, `docs/kernel.tla`) —
  `verify_kernel()` checks four safety invariants (deny-by-default, no-scope-escape,
  attenuation monotonicity, determinism) by exhaustion over a bounded domain
  (~3,400 cases). A TLA+ model of the kernel accompanies it for a model checker.

- **Adversarial test suite** (`tests/test_adversarial.py`) — path traversal,
  injection-driven capability escalation, attenuation-widening, allowlist/ceiling
  bypass attempts, approval TOCTOU, and ledger tamper detection. Every test asserts
  the attack is refused.

### Changed

- `kernel._check_constraints` now delegates to `autarch.scoping.evaluate` (one
  source of truth shared by the kernel, delegation, and the guarantee prover).
  Fully backward compatible.
- `delegation` attenuates the new scope keys by subset (allowlists/enums shrink;
  prohibitions may only strengthen).
- `WhyMemory` gained an opt-in `same_thread=False` for the lock-serialized gateway.

### Honest boundaries

- The cloud providers are unit-tested offline (request/response shaping, headers,
  error typing). Verifying live calls requires your own API keys.
- `verify_kernel()` is a sound *bounded model check*, not a machine-checked proof.
  `docs/kernel.tla` states the model for a real checker (TLC).
