"""Simple forward-only migration system for the Postgres database."""

import logging
from collections.abc import Callable

from gardenops.db import DbConn

_logger = logging.getLogger(__name__)


def _ensure_migrations_table(conn: DbConn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)


def get_current_version(conn: DbConn) -> int:
    _ensure_migrations_table(conn)
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    assert row is not None
    return int(row["v"]) if row["v"] is not None else 0


def run_migrations(conn: DbConn) -> int:
    """Run all pending migrations. Returns number applied."""
    _ensure_migrations_table(conn)
    current = get_current_version(conn)
    applied = 0
    for version, migrate_fn in _MIGRATIONS:
        if version <= current:
            continue
        _logger.info("Applying migration %d", version)
        migrate_fn(conn)
        conn.execute(
            "INSERT INTO schema_migrations (version) VALUES (%s)",
            (version,),
        )
        conn.commit()
        applied += 1
        _logger.info("Migration %d applied", version)
    return applied


# Register migrations as (version, callable) tuples.
# Each callable receives a DbConn.
# Add new migrations at the end with incrementing version numbers.
_MIGRATIONS: list[tuple[int, Callable[[DbConn], None]]] = [
    # (1, _migrate_v1),
]
