"""Governed data access — SQL and AI search, every query provable.

The framework's edge isn't connector breadth; it's that every query an
intelligence issues against your data is capability-scoped, injection-safe,
privacy-aware, and signed into a tamper-evident ledger.

  * SQL over any DB-API 2.0 database (SQLite here; Oracle/SQL Server/Postgres by
    swapping only the driver).
  * Semantic search over a vector index using the same embedder seam.

Run from the repo root:
    python examples/governed_data.py
"""
import shutil
import sqlite3
from pathlib import Path

from autarch import (Agent, ExtractionAdapter, SQLAdapter, VectorSearchAdapter,
                     capability)
from autarch.intelligence.base import ModelProvider
from autarch.intelligence.embedding import HashingEmbedder


def banner(t):
    print("\n" + "=" * 66 + "\n" + t + "\n" + "=" * 66)


def main():
    ws = Path("./sandbox/_data")
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)

    # --- a governed SQL data source ----------------------------------------
    banner("Governed SQL — read-only, PII-redacted, injection-safe, audited")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE orders (id INTEGER, customer TEXT, card TEXT, total REAL)")
    conn.executemany("INSERT INTO orders VALUES (?,?,?,?)",
                     [(1, "Alice", "4111-1111-1111-1111", 42.0),
                      (2, "Bob", "5500-0000-0000-0004", 99.5)])
    conn.commit()

    db = SQLAdapter(conn, dialect="sqlite", read_only=True,
                    redact_columns=["card"], max_rows=100)
    agent = Agent(
        intent="analyze orders",
        grants=[capability("db.query"), capability("db.schema")],
        adapters=[db], workspace=str(ws), auto_preside=False,
    )
    print("tables:", agent.enact("db.schema", {}).result.output["tables"])
    q = agent.enact("db.query",
                    {"sql": "SELECT customer, card, total FROM orders WHERE total > ?",
                     "params": [50]})
    print("rows  :", q.result.output["rows"])  # card is redacted

    inj = agent.enact("db.query",
                      {"sql": "SELECT * FROM orders WHERE customer = ?",
                       "params": ["Bob'; DROP TABLE orders;--"]})
    print("injection payload matched rows:", inj.result.output["row_count"], "(table intact)")
    drop = agent.enact("db.execute", {"sql": "DROP TABLE orders"})
    print("DROP attempt executed:", drop.executed, "(db.execute not granted)")

    # --- a governed vector search index ------------------------------------
    banner("Governed semantic search — over an AI index, same governance")
    index = VectorSearchAdapter(HashingEmbedder(), max_k=3)
    index.index_many([
        ("kb1", "Refund requests are processed within five business days."),
        ("kb2", "Standard shipping takes two to three business days."),
        ("kb3", "Warranty covers manufacturing defects for one year."),
    ])
    searcher = Agent(
        intent="find relevant knowledge",
        grants=[capability("search.query")],
        adapters=[index], workspace=str(ws / "s"), auto_preside=False,
    )
    hits = searcher.enact("search.query",
                          {"query": "how long until I get my refund", "k": 2}).result.output["hits"]
    for h in hits:
        print(f"  {h['score']:.3f}  {h['text']}")

    banner("Every query above is signed into the tamper-evident ledger")
    print("SQL ledger entries:", len(agent.memory.all()))
    print("Search ledger entries:", len(searcher.memory.all()))

    # --- smart extraction from unstructured docs ---------------------------
    banner("Smart extraction — structured fields out of an unstructured doc")

    class Extractor(ModelProvider):
        name = "extractor"

        def complete(self, prompt, system=None):
            import json
            return json.dumps({"invoice_no": "INV-4471", "total": "1240.50",
                               "vendor": "Acme Corp"})

    docs = ws / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "note.txt").write_text(
        "Hi — enclosed is Invoice INV-4471 from Acme Corp, amount due $1,240.50."
    )
    extractor = Agent(
        intent="pull invoice fields",
        grants=[capability("doc.parse"), capability("doc.extract")],
        adapters=[ExtractionAdapter(root=str(docs), model=Extractor())],
        workspace=str(ws / "x"), auto_preside=False,
    )
    fields = extractor.enact(
        "doc.extract",
        {"path": "note.txt", "fields": ["invoice_no", "total", "vendor"]},
    ).result.output["fields"]
    print("extracted:", fields)

    banner("Connecting to real services (drop-in — same governance)")
    print("Postgres : db = connect_postgres('postgresql://ro_user@host/db',")
    print("               read_only=True, redact_columns=['ssn'])")
    print("Azure AI : idx = AzureAISearchAdapter(endpoint, index, api_key, embedder=...)")
    print("Oracle   : db = connect_oracle(user, password, dsn, read_only=True)")


if __name__ == "__main__":
    main()
