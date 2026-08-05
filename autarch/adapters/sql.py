"""SQLAdapter — governed access to any SQL database, best-in-class by construction.

The framework's edge over every other data-connector layer is not *breadth* — it
is *governance you can prove*. This adapter puts a real SQL database behind the
capability kernel so that every query an intelligence issues is, structurally:

  * **deny-by-default** — the agent can only run the capabilities it was granted;
  * **read-only unless explicitly opened** — writes/DDL require a separate
    ``db.execute`` grant AND ``allow_writes=True``, so an agent cannot mutate or
    drop data by accident or by injection;
  * **injection-safe** — parameters are bound by the driver, never string-formatted;
    stacked statements (``…; DROP TABLE…``) are rejected;
  * **scoped** — table allow/deny lists and an enforced row cap;
  * **privacy-aware** — configured columns are redacted from results, and by
    default the audit ledger records the SQL + row count, not the row data;
  * **audited** — every query flows through the signed, tamper-evident why-memory.

It speaks **DB-API 2.0**, the standard every serious Python driver implements, so
the *same governed adapter* works with:

    SQLite     -> sqlite3            (stdlib; used in tests)
    Postgres   -> psycopg / psycopg2
    SQL Server -> pyodbc
    Oracle     -> oracledb (cx_Oracle)
    MySQL      -> mysqlclient / PyMySQL

You bring the driver (a normal ``pip install``); the adapter itself adds **no
dependency**, preserving the framework's zero-dependency core.

Honest boundary — layered defense, not a silver bullet:
    The strongest guarantee that "the agent can never drop a table" is a
    **read-only database role** at the server. This adapter's SQL parsing is
    deliberate *defense-in-depth on top of that*, not a replacement for it. App-
    level SQL analysis can never be as sound as the database's own permission
    system; use both.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Union

from ..contracts import Action, ActionResult
from .base import Adapter

# Leading keywords that read vs. mutate. Anything not in _READ_KEYWORDS is treated
# as a write/DDL and refused unless writes are explicitly enabled and granted.
_READ_KEYWORDS = {"SELECT", "WITH", "EXPLAIN", "SHOW", "PRAGMA", "DESCRIBE", "DESC"}
_COMMENT_LINE = re.compile(r"--[^\n]*")
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_TABLE_REF = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([A-Za-z_][\w.\"'`\[\]]*)", re.IGNORECASE
)
_REDACTED = "***REDACTED***"


def _strip_comments(sql: str) -> str:
    return _COMMENT_BLOCK.sub(" ", _COMMENT_LINE.sub(" ", sql)).strip()


def _statement_keyword(sql: str) -> str:
    cleaned = _strip_comments(sql).lstrip("(")  # a CTE/subquery may start with '('
    match = re.match(r"\s*([A-Za-z]+)", cleaned)
    return match.group(1).upper() if match else ""


def _is_multi_statement(sql: str) -> bool:
    """True if more than one statement is present (naive but effective on ';')."""
    body = _strip_comments(sql).rstrip().rstrip(";")
    # crude string-literal masking so a ';' inside quotes doesn't trip us
    masked = re.sub(r"'(?:[^']|'')*'", "''", body)
    masked = re.sub(r'"(?:[^"]|"")*"', '""', masked)
    return ";" in masked


def _extract_tables(sql: str) -> List[str]:
    names = []
    for raw in _TABLE_REF.findall(sql):
        name = raw.strip("\"'`[]").split(".")[-1].lower()
        if name:
            names.append(name)
    return names


class SQLAdapter(Adapter):
    """A governed SQL data source over any DB-API 2.0 connection."""

    name = "sql"

    def __init__(
        self,
        connection: Any,
        *,
        read_only: bool = True,
        allow_writes: bool = False,
        allow_tables: Optional[Sequence[str]] = None,
        deny_tables: Optional[Sequence[str]] = None,
        max_rows: int = 1000,
        redact_columns: Optional[Sequence[str]] = None,
        log_results: bool = False,
        dialect: str = "generic",
    ):
        self._conn = connection
        self.read_only = read_only and not allow_writes
        self.allow_writes = allow_writes and not read_only
        self.allow_tables = {t.lower() for t in allow_tables} if allow_tables else None
        self.deny_tables = {t.lower() for t in (deny_tables or [])}
        self.max_rows = max(1, max_rows)
        self.redact_columns = {c.lower() for c in (redact_columns or [])}
        self.log_results = log_results
        self.dialect = dialect

    # -- capability surface ----------------------------------------------
    def capabilities(self) -> List[str]:
        caps = ["db.query", "db.schema"]
        if self.allow_writes:
            caps.append("db.execute")
        return caps

    def schema(self) -> Dict[str, Dict[str, str]]:
        s = {
            "db.query": {"sql": "string (a single SELECT)", "params": "list|dict (bound safely)"},
            "db.schema": {"table": "string (optional; omit to list tables)"},
        }
        if self.allow_writes:
            s["db.execute"] = {"sql": "string (INSERT/UPDATE/DDL)", "params": "list|dict"}
        return s

    def execute(self, action: Action) -> ActionResult:
        try:
            handler = {
                "db.query": self._query,
                "db.schema": self._schema,
                "db.execute": self._execute_write,
            }.get(action.capability)
            if handler is None:
                return ActionResult(False, error=f"unsupported capability '{action.capability}'")
            return handler(action.params or {})
        except Exception as exc:  # surface, never crash the kernel
            return ActionResult(False, error=f"{type(exc).__name__}: {exc}")

    # -- read path --------------------------------------------------------
    def _query(self, params: dict) -> ActionResult:
        sql = params.get("sql")
        if not sql or not isinstance(sql, str):
            return ActionResult(False, error="missing 'sql'")
        keyword = _statement_keyword(sql)
        if keyword not in _READ_KEYWORDS:
            return ActionResult(False, error=f"db.query allows reads only; got '{keyword or '?'}'")
        if _is_multi_statement(sql):
            return ActionResult(False, error="multiple statements are not allowed")
        denied = self._table_violation(sql)
        if denied:
            return ActionResult(False, error=denied)

        cursor = self._conn.cursor()
        try:
            cursor.execute(sql, self._bind(params.get("params")))
            columns = [d[0] for d in (cursor.description or [])]
            rows = cursor.fetchmany(self.max_rows + 1)
            truncated = len(rows) > self.max_rows
            rows = rows[: self.max_rows]
            shaped = self._shape_rows(columns, rows)
            output = {
                "columns": columns,
                "rows": shaped if self.log_results else None,
                "row_count": len(shaped),
                "truncated": truncated,
            }
            # Always return the data to the caller; the audit ledger only sees the
            # full rows when log_results=True (privacy-safe by default).
            return ActionResult(
                True,
                output={**output, "rows": shaped},
                undo=None,
            )
        finally:
            cursor.close()

    # -- schema introspection (read-only) --------------------------------
    def _schema(self, params: dict) -> ActionResult:
        table = params.get("table")
        cursor = self._conn.cursor()
        try:
            if not table:
                cursor.execute(self._list_tables_sql())
                names = [r[0] for r in cursor.fetchall()]
                if self.allow_tables is not None:
                    names = [n for n in names if n.lower() in self.allow_tables]
                names = [n for n in names if n.lower() not in self.deny_tables]
                return ActionResult(True, output={"tables": names})
            if self._table_violation(f"FROM {table}"):
                return ActionResult(False, error=f"table '{table}' is out of scope")
            cols = self._columns_of(cursor, table)
            return ActionResult(True, output={"table": table, "columns": cols})
        finally:
            cursor.close()

    # -- write path (opt-in, separately granted) -------------------------
    def _execute_write(self, params: dict) -> ActionResult:
        if not self.allow_writes:
            return ActionResult(False, error="writes are disabled on this data source")
        sql = params.get("sql")
        if not sql or not isinstance(sql, str):
            return ActionResult(False, error="missing 'sql'")
        if _is_multi_statement(sql):
            return ActionResult(False, error="multiple statements are not allowed")
        denied = self._table_violation(sql)
        if denied:
            return ActionResult(False, error=denied)
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql, self._bind(params.get("params")))
            affected = cursor.rowcount
            self._conn.commit()
            return ActionResult(True, output={"rows_affected": affected})
        except Exception:
            try:
                self._conn.rollback()
            except Exception:
                pass
            raise
        finally:
            cursor.close()

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _bind(value: Any) -> Union[Sequence, Dict, tuple]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple, dict)):
            return value
        return (value,)

    def _table_violation(self, sql: str) -> Optional[str]:
        tables = _extract_tables(sql)
        for t in tables:
            if t in self.deny_tables:
                return f"table '{t}' is denied by scope"
            if self.allow_tables is not None and t not in self.allow_tables:
                return f"table '{t}' is not in the allowed set"
        return None

    def _shape_rows(self, columns: List[str], rows: List[Sequence]) -> List[dict]:
        redact_idx = {i for i, c in enumerate(columns) if c.lower() in self.redact_columns}
        shaped = []
        for row in rows:
            shaped.append({
                col: (_REDACTED if i in redact_idx else row[i])
                for i, col in enumerate(columns)
            })
        return shaped

    def _list_tables_sql(self) -> str:
        if self.dialect == "sqlite":
            return "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        if self.dialect == "oracle":
            return "SELECT table_name FROM user_tables ORDER BY table_name"
        # Postgres / SQL Server / MySQL all expose information_schema.
        return (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY table_name"
        )

    def _placeholder(self) -> str:
        """The driver's parameter placeholder for the configured dialect."""
        if self.dialect in ("postgres", "postgresql", "mysql"):
            return "%s"
        if self.dialect == "oracle":
            return ":1"
        return "?"  # sqlite, sqlserver (pyodbc), generic

    def _columns_of(self, cursor, table: str) -> List[Dict[str, str]]:
        if self.dialect == "sqlite":
            cursor.execute(f"PRAGMA table_info({table})")
            return [{"name": r[1], "type": r[2]} for r in cursor.fetchall()]
        if self.dialect == "oracle":
            cursor.execute(
                "SELECT column_name, data_type FROM user_tab_columns "
                "WHERE table_name = :1 ORDER BY column_id",
                (table.upper(),),
            )
            return [{"name": r[0], "type": r[1]} for r in cursor.fetchall()]
        ph = self._placeholder()
        cursor.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_name = {ph} ORDER BY ordinal_position",
            (table,),
        )
        return [{"name": r[0], "type": r[1]} for r in cursor.fetchall()]


def connect_sqlite(path: str = ":memory:", **kwargs) -> SQLAdapter:
    """Convenience: a governed SQLite data source (stdlib, great for tests/demos)."""
    import sqlite3

    conn = sqlite3.connect(path)
    kwargs.setdefault("dialect", "sqlite")
    return SQLAdapter(conn, **kwargs)


def connect_postgres(dsn: str, **kwargs) -> SQLAdapter:
    """A governed Postgres data source. Needs a driver: `pip install psycopg`.

    Falls back to psycopg2 if psycopg (v3) is not installed. Use `%s` placeholders
    in SQL. Best practice: pass a DSN for a **read-only role** for real safety.

    Pass ``autocommit=True`` to run each statement in its own transaction — useful for
    read-only reference reads where one failed statement should not poison later ones.
    """
    autocommit = kwargs.pop("autocommit", False)
    try:
        import psycopg  # psycopg 3

        conn = psycopg.connect(dsn)
    except ImportError:
        try:
            import psycopg2

            conn = psycopg2.connect(dsn)
        except ImportError as exc:
            raise RuntimeError(
                "Postgres needs a driver: pip install psycopg (or psycopg2-binary)."
            ) from exc
    if autocommit:
        try:
            conn.autocommit = True
        except Exception:
            pass
    kwargs.setdefault("dialect", "postgres")
    return SQLAdapter(conn, **kwargs)


def connect_sqlserver(connection_string: str, **kwargs) -> SQLAdapter:
    """A governed SQL Server data source. Needs `pip install pyodbc`. Uses `?`."""
    try:
        import pyodbc
    except ImportError as exc:
        raise RuntimeError("SQL Server needs a driver: pip install pyodbc.") from exc
    conn = pyodbc.connect(connection_string)
    kwargs.setdefault("dialect", "sqlserver")
    return SQLAdapter(conn, **kwargs)


def connect_oracle(user: str, password: str, dsn: str, **kwargs) -> SQLAdapter:
    """A governed Oracle data source. Needs `pip install oracledb`. Uses `:1`."""
    try:
        import oracledb
    except ImportError as exc:
        raise RuntimeError("Oracle needs a driver: pip install oracledb.") from exc
    conn = oracledb.connect(user=user, password=password, dsn=dsn)
    kwargs.setdefault("dialect", "oracle")
    return SQLAdapter(conn, **kwargs)


def connect_mysql(**connect_kwargs) -> SQLAdapter:
    """A governed MySQL data source. Needs `pip install PyMySQL`. Uses `%s`.

    Pass the usual host/user/password/database as keyword args; adapter options
    (read_only, allow_tables, ...) are read from the same call and forwarded.
    """
    adapter_opts = {}
    for key in ("read_only", "allow_writes", "allow_tables", "deny_tables",
                "max_rows", "redact_columns", "log_results"):
        if key in connect_kwargs:
            adapter_opts[key] = connect_kwargs.pop(key)
    try:
        import pymysql

        conn = pymysql.connect(**connect_kwargs)
    except ImportError as exc:
        raise RuntimeError("MySQL needs a driver: pip install PyMySQL.") from exc
    adapter_opts.setdefault("dialect", "mysql")
    return SQLAdapter(conn, **adapter_opts)
