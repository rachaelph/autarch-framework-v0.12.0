"""SQL session adapter for the opt-in MVP's application-owned stores."""
from __future__ import annotations

from types import TracebackType
from typing import Optional, Sequence

from .persistence import DatabaseConnection, DatabaseCursor


class SqlSession:
    def __init__(self, connection: DatabaseConnection):
        self.connection = connection
        self.cursors: list[DatabaseCursor] = []
        self.execute("SET XACT_ABORT ON; SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;")
        connection.commit()

    def execute(self, query: str, parameters: Sequence[object] = ()) -> DatabaseCursor:
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, parameters)
        except Exception:
            cursor.close()
            raise
        self.cursors.append(cursor)
        return cursor

    def executemany(self, query: str, parameters: Sequence[Sequence[object]]) -> None:
        cursor = self.connection.cursor()
        try:
            cursor.executemany(query, parameters)
        finally:
            cursor.close()

    def _close_cursors(self) -> None:
        while self.cursors:
            self.cursors.pop().close()

    def commit(self) -> None:
        try:
            self.connection.commit()
        finally:
            self._close_cursors()

    def rollback(self) -> None:
        try:
            self.connection.rollback()
        finally:
            self._close_cursors()

    def close(self) -> None:
        try:
            self.rollback()
        finally:
            self.connection.close()

    def __enter__(self) -> "SqlSession":
        return self

    def __exit__(
        self, exc_type: Optional[type[BaseException]], exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
