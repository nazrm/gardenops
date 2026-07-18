"""Unit coverage for the request-scoped Phase 9 query collector."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from psycopg.pq import TransactionStatus

import gardenops.db as db
from gardenops.performance_queries import (
    QueryEvidenceCollector,
    activate_query_execution_collector,
    reset_query_execution_collector,
)


class _Cursor:
    def __init__(self) -> None:
        self.executed: list[tuple[object, object]] = []

    def execute(self, query: object, params: object = None, **_kwargs: object) -> _Cursor:
        self.executed.append((query, params))
        return self

    def executemany(self, query: object, params: object, **_kwargs: object) -> _Cursor:
        self.executed.append((query, params))
        return self


class _Connection:
    def __init__(self) -> None:
        self.cursor_instance = _Cursor()
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)
        self.executed: list[tuple[object, object]] = []

    def cursor(self, *_args: object, **_kwargs: object) -> _Cursor:
        return self.cursor_instance

    def execute(self, query: object, params: object = None, **_kwargs: object) -> _Cursor:
        self.executed.append((query, params))
        return self.cursor_instance


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.returned: list[object] = []

    def getconn(self) -> _Connection:
        return self.connection

    def putconn(self, connection: object) -> None:
        self.returned.append(connection)


def test_active_collector_counts_connection_and_cursor_execution_without_sql_text(
    monkeypatch: Any,
) -> None:
    raw = _Connection()
    pool = _Pool(raw)
    collector = QueryEvidenceCollector()
    monkeypatch.setattr(db, "_get_pool", lambda: pool)
    token = activate_query_execution_collector(collector)
    try:
        connection = db.get_db()
        connection.execute("SELECT secret_value FROM example WHERE id = %s", (42,))
        connection.cursor().execute("SELECT another_secret FROM example WHERE id = %s", (99,))
        connection.cursor().executemany("INSERT INTO example VALUES (%s)", iter([(1,), (2,)]))
    finally:
        reset_query_execution_collector(token)

    db.return_db(connection)
    snapshot = collector.snapshot()

    assert snapshot["query_count"] == 3
    assert snapshot["batch_rows"] == 0
    assert len(snapshot["statement_fingerprints"]) == 3
    assert "secret_value" not in str(snapshot)
    assert pool.returned == [raw]
