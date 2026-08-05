"""Tests for the governed SQL adapter (against SQLite; same DB-API as Oracle/PG/MSSQL)."""
import sqlite3

import pytest

from autarch.adapters.sql import SQLAdapter, connect_sqlite
from autarch.agent import Agent, capability
from autarch.contracts import Action


def _seeded_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id INTEGER, name TEXT, ssn TEXT)")
    conn.executemany("INSERT INTO users VALUES (?,?,?)",
                     [(1, "alice", "111-11-1111"), (2, "bob", "222-22-2222")])
    conn.execute("CREATE TABLE secrets (id INTEGER, value TEXT)")
    conn.commit()
    return conn


def _adapter(**kw):
    return SQLAdapter(_seeded_conn(), dialect="sqlite", **kw)


def test_select_returns_rows():
    a = _adapter()
    r = a.execute(Action("db.query", {"sql": "SELECT id, name FROM users ORDER BY id"}))
    assert r.ok
    assert r.output["row_count"] == 2
    assert r.output["rows"][0]["name"] == "alice"


def test_parameterized_query_is_injection_safe():
    a = _adapter()
    # the classic injection payload is treated as a *value*, matching nothing
    r = a.execute(Action("db.query",
                         {"sql": "SELECT * FROM users WHERE name = ?",
                          "params": ["alice'; DROP TABLE users;--"]}))
    assert r.ok and r.output["row_count"] == 0
    # table still exists
    assert a.execute(Action("db.query", {"sql": "SELECT * FROM users"})).ok


def test_read_only_blocks_writes_and_ddl():
    a = _adapter()  # read_only by default
    for sql in ["DELETE FROM users", "DROP TABLE users",
                "UPDATE users SET name='x'", "INSERT INTO users VALUES (3,'c','x')"]:
        r = a.execute(Action("db.query", {"sql": sql}))
        assert not r.ok, sql


def test_stacked_statements_rejected():
    a = _adapter()
    r = a.execute(Action("db.query", {"sql": "SELECT 1; DROP TABLE users"}))
    assert not r.ok and "multiple statements" in r.error


def test_write_disabled_capability_not_advertised():
    a = _adapter()
    assert "db.execute" not in a.capabilities()
    r = a.execute(Action("db.execute", {"sql": "INSERT INTO users VALUES (3,'c','x')"}))
    assert not r.ok


def test_writes_when_explicitly_enabled():
    a = _adapter(read_only=False, allow_writes=True)
    assert "db.execute" in a.capabilities()
    r = a.execute(Action("db.execute", {"sql": "INSERT INTO users VALUES (?,?,?)",
                                        "params": [3, "carol", "333"]}))
    assert r.ok and r.output["rows_affected"] == 1


def test_table_allowlist_confines_reads():
    a = _adapter(allow_tables=["users"])
    assert a.execute(Action("db.query", {"sql": "SELECT * FROM users"})).ok
    denied = a.execute(Action("db.query", {"sql": "SELECT * FROM secrets"}))
    assert not denied.ok and "not in the allowed set" in denied.error


def test_row_cap_truncates():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE nums (n INTEGER)")
    conn.executemany("INSERT INTO nums VALUES (?)", [(i,) for i in range(50)])
    conn.commit()
    a = SQLAdapter(conn, dialect="sqlite", max_rows=10)
    r = a.execute(Action("db.query", {"sql": "SELECT n FROM nums"}))
    assert r.output["row_count"] == 10 and r.output["truncated"] is True


def test_column_redaction():
    a = _adapter(redact_columns=["ssn"])
    r = a.execute(Action("db.query", {"sql": "SELECT name, ssn FROM users"}))
    assert r.output["rows"][0]["ssn"] == "***REDACTED***"
    assert r.output["rows"][0]["name"] == "alice"


def test_schema_introspection():
    a = _adapter()
    tables = a.execute(Action("db.schema", {})).output["tables"]
    assert "users" in tables and "secrets" in tables
    cols = a.execute(Action("db.schema", {"table": "users"})).output["columns"]
    assert {c["name"] for c in cols} == {"id", "name", "ssn"}


def test_connect_sqlite_helper():
    a = connect_sqlite(":memory:")
    a._conn.execute("CREATE TABLE t (x INTEGER)")
    a._conn.execute("INSERT INTO t VALUES (42)")
    r = a.execute(Action("db.query", {"sql": "SELECT x FROM t"}))
    assert r.output["rows"][0]["x"] == 42


def test_end_to_end_governed_through_kernel(tmp_path):
    # The kernel must ALSO deny db.execute when it isn't granted, even if the
    # adapter had writes on — defense in depth.
    conn = _seeded_conn()
    agent = Agent(
        intent="look up user data",
        grants=[capability("db.query"), capability("db.schema")],
        adapters=[SQLAdapter(conn, dialect="sqlite")],
        workspace=str(tmp_path),
        auto_preside=False,
    )
    ok = agent.enact("db.query", {"sql": "SELECT name FROM users WHERE id = ?", "params": [1]})
    assert ok.executed
    # db.execute was never granted -> kernel denies before the adapter is touched
    denied = agent.enact("db.execute", {"sql": "DROP TABLE users"})
    assert not denied.executed


class _FakeCursor:
    """Records executed SQL/params so we can verify dialect-specific shaping."""
    def __init__(self, log): self.log = log; self.description = [("table_name",)]; self._rows = [("orders",)]
    def execute(self, sql, params=()): self.log.append((sql, params))
    def fetchall(self): return self._rows
    def fetchmany(self, n): return self._rows
    def close(self): pass


class _FakeConn:
    def __init__(self): self.log = []
    def cursor(self): return _FakeCursor(self.log)
    def commit(self): pass
    def rollback(self): pass


def test_postgres_dialect_uses_percent_s_placeholder():
    conn = _FakeConn()
    a = SQLAdapter(conn, dialect="postgres")
    a.execute(Action("db.schema", {"table": "orders"}))
    # the information_schema.columns lookup must use %s for psycopg, not ?
    col_query = [s for s, _ in conn.log if "information_schema.columns" in s]
    assert col_query and "%s" in col_query[0] and "?" not in col_query[0]


def test_oracle_dialect_uses_colon_placeholder():
    conn = _FakeConn()
    a = SQLAdapter(conn, dialect="oracle")
    a.execute(Action("db.schema", {"table": "orders"}))
    col_query = [s for s, _ in conn.log if "user_tab_columns" in s]
    assert col_query and ":1" in col_query[0]


def test_connect_helpers_raise_clear_error_without_driver():
    import pytest
    from autarch.adapters.sql import connect_postgres, connect_oracle
    # drivers aren't installed here; the helper must fail with a helpful message
    with pytest.raises(RuntimeError):
        connect_postgres("postgresql://localhost/db")
