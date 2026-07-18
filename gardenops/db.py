import logging
import os
import re
import time
from collections.abc import Generator, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, cast

import psycopg
import psycopg.rows
from fastapi import Depends, Request
from psycopg.abc import Params, Query
from psycopg.pq import TransactionStatus
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool as _PgConnectionPool

from gardenops.performance_queries import active_query_execution_collector
from gardenops.schema_signature import (
    bootstrap_schema_diagnostics_from_snapshot,
    collect_schema_snapshot,
)

_logger = logging.getLogger(__name__)

type DbRow = DictRow

if TYPE_CHECKING:

    class DbConn(psycopg.Connection[DbRow]):
        def execute(
            self,
            query: str | Query,
            params: Params | None = None,
            *,
            prepare: bool | None = None,
            binary: bool = False,
        ) -> psycopg.Cursor[DbRow]: ...
else:
    DbConn = psycopg.Connection[DbRow]

SHADEMAP_MODES = ("shadow", "sun-hours")
SHADEMAP_PRESETS = ("now", "custom", "spring", "summer", "autumn", "winter")
SHADEMAP_OBSTACLE_KINDS = ("tree", "structure")
_DEFAULT_GARDEN_SLUG = "default"
_DEFAULT_GARDEN_NAME = "Default Garden"


# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------

_pool: _PgConnectionPool[DbConn] | None = None
_REQUEST_DB_CONN_STATE = "_db_conn"
_STATE_MISSING = object()


class _QueryCountingCursor:
    """Proxy a psycopg cursor when a request-local performance probe is active."""

    def __init__(self, cursor: Any, collector: Any) -> None:
        self._cursor = cursor
        self._collector = collector

    def execute(self, query: Query, params: Params | None = None, **kwargs: Any) -> Any:
        self._collector.record(query)
        return self._cursor.execute(query, params, **kwargs)

    def executemany(self, query: Query, params_seq: Iterable[Any], **kwargs: Any) -> Any:
        self._collector.record(query)
        return self._cursor.executemany(query, params_seq, **kwargs)

    def __enter__(self) -> _QueryCountingCursor:
        self._cursor.__enter__()
        return self

    def __exit__(self, *args: Any) -> Any:
        return self._cursor.__exit__(*args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _QueryCountingConnection:
    """Proxy connection entry points so direct and cursor executions are counted once."""

    def __init__(self, connection: DbConn, collector: Any) -> None:
        self._gardenops_raw_connection = connection
        self._collector = collector

    def execute(self, query: Query, params: Params | None = None, **kwargs: Any) -> Any:
        self._collector.record(query)
        return self._gardenops_raw_connection.execute(query, params, **kwargs)

    def cursor(self, *args: Any, **kwargs: Any) -> _QueryCountingCursor:
        cursor = self._gardenops_raw_connection.cursor(*args, **kwargs)
        return _QueryCountingCursor(cursor, self._collector)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._gardenops_raw_connection, name)


def _raw_connection(conn: DbConn | _QueryCountingConnection) -> DbConn:
    if isinstance(conn, _QueryCountingConnection):
        return conn._gardenops_raw_connection
    return conn


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Postgres is required.")
    return url


def _get_pool() -> _PgConnectionPool[DbConn]:
    global _pool
    if _pool is None:
        _pool = _PgConnectionPool(
            _database_url(),
            min_size=2,
            max_size=10,
            open=True,
            kwargs={"row_factory": psycopg.rows.dict_row},
        )
    return _pool


def get_db() -> DbConn:
    connection = _get_pool().getconn()
    collector = active_query_execution_collector()
    if collector is None:
        return connection
    return cast(DbConn, _QueryCountingConnection(connection, collector))


def request_scoped_db_conn(
    request: Request,
) -> DbConn | None:
    return getattr(request.state, _REQUEST_DB_CONN_STATE, None)


def return_db(conn: DbConn) -> None:
    conn = _raw_connection(conn)
    if conn.info.transaction_status != TransactionStatus.IDLE:
        conn.rollback()
    _get_pool().putconn(conn)


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def db_dep(request: Request) -> Generator[DbConn]:
    conn = get_db()
    previous = getattr(request.state, _REQUEST_DB_CONN_STATE, _STATE_MISSING)
    setattr(request.state, _REQUEST_DB_CONN_STATE, conn)
    try:
        yield conn
    finally:
        current = getattr(request.state, _REQUEST_DB_CONN_STATE, _STATE_MISSING)
        if current is conn:
            if previous is _STATE_MISSING:
                delattr(request.state, _REQUEST_DB_CONN_STATE)
            else:
                setattr(request.state, _REQUEST_DB_CONN_STATE, previous)
        return_db(conn)


DB = Annotated[DbConn, Depends(db_dep)]


# ---------------------------------------------------------------------------
# executemany helper (psycopg3 Connection lacks executemany)
# ---------------------------------------------------------------------------


def executemany(
    conn: DbConn,
    sql: Query,
    params_seq: Iterable[Any],
) -> None:
    cur = conn.cursor()
    cur.executemany(sql, params_seq)


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------


def run_migrations() -> None:
    """Run pending SQL migrations from the migrations/ directory."""
    conn = cast(
        DbConn,
        psycopg.connect(_database_url(), row_factory=cast(Any, psycopg.rows.dict_row)),
    )
    try:
        conn.execute("SELECT pg_advisory_lock(1)")
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMPTZ DEFAULT now()
                )
            """)
            conn.commit()
            applied = {
                row["version"]
                for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
            }
            migrations_dir = Path(__file__).parent.parent / "migrations"
            all_sql_files = sorted(migrations_dir.glob("*.sql"))
            if not applied:
                diagnostics = _migration_bootstrap_diagnostics(conn)
                if diagnostics["mode"] == "incomplete-existing-schema":
                    _raise_incomplete_bootstrap_schema(diagnostics)
                if diagnostics["mode"] in {"verified-baseline", "verified-upgrade-baseline"}:
                    all_versions = [int(f.stem.split("_")[0]) for f in all_sql_files]
                    stamp_through = int(diagnostics.get("stamp_through", max(all_versions)))
                    stamped_versions = [ver for ver in all_versions if ver <= stamp_through]
                    for ver in stamped_versions:
                        conn.execute(
                            "INSERT INTO schema_migrations (version)"
                            " VALUES (%s)"
                            " ON CONFLICT DO NOTHING",
                            (ver,),
                        )
                    conn.commit()
                    applied.update(stamped_versions)
                    _logger.info(
                        "Stamped %d existing migration(s) through version %04d "
                        "on verified bootstrapped database",
                        len(stamped_versions),
                        stamp_through,
                    )
                    if stamp_through == max(all_versions):
                        return
            for sql_file in all_sql_files:
                version = int(sql_file.stem.split("_")[0])
                if version in applied:
                    continue
                try:
                    conn.execute(sql_file.read_text())
                    conn.execute(
                        "INSERT INTO schema_migrations (version)"
                        " VALUES (%s)"
                        " ON CONFLICT DO NOTHING",
                        (version,),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                _logger.info("Applied migration %04d: %s", version, sql_file.name)
        finally:
            try:
                conn.execute("SELECT pg_advisory_unlock(1)")
                conn.commit()
            except Exception:
                conn.rollback()
    finally:
        conn.close()


def _migration_bootstrap_diagnostics(conn: DbConn) -> dict[str, object]:
    return bootstrap_schema_diagnostics_from_snapshot(collect_schema_snapshot(conn))


def migration_bootstrap_diagnostics(database_url: str | None = None) -> dict[str, object]:
    """Return read-only migration bootstrap diagnostics for operator tooling."""
    conn = cast(
        DbConn,
        psycopg.connect(
            database_url or _database_url(),
            row_factory=cast(Any, psycopg.rows.dict_row),
        ),
    )
    try:
        conn.execute("BEGIN READ ONLY")
        try:
            return _migration_bootstrap_diagnostics(conn)
        finally:
            conn.rollback()
    finally:
        conn.close()


def _raise_incomplete_bootstrap_schema(diagnostics: dict[str, object]) -> None:
    raw_missing = diagnostics.get("missing", [])
    missing = raw_missing if isinstance(raw_missing, list) else []
    missing_parts: list[str] = []
    for part in missing:
        if not isinstance(part, dict):
            continue
        part_dict = cast(dict[str, object], part)
        kind = part_dict.get("kind")
        schema_object = part_dict.get("object")
        if kind is None or schema_object is None:
            continue
        missing_parts.append(f"{kind}:{schema_object}")
    sample = ", ".join(missing_parts[:12])
    if len(missing_parts) > 12:
        sample = f"{sample}, ... ({len(missing_parts)} total)"
    raise RuntimeError(
        "Existing public database schema is incomplete; refusing to stamp migrations "
        "as applied. Run `python scripts/check_backend_integrity.py --bootstrap-only "
        "--format text` against the database and repair or restore the schema before "
        f"startup. Missing schema pieces: {sample or 'unknown'}"
    )


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def current_timestamp_ms() -> int:
    return int(time.time() * 1000)


def default_shademap_state() -> dict[str, object]:
    return {
        "mode": "shadow",
        "selected_plot_id": None,
        "analysis_timestamp_ms": current_timestamp_ms(),
        "preset": "now",
    }


def default_shademap_calibration() -> dict[str, object]:
    return {
        "enabled": False,
        "calibration_type": "house-corners",
        "origin_grid_col": None,
        "origin_grid_row": None,
        "origin_latitude": None,
        "origin_longitude": None,
        "axis_grid_col": None,
        "axis_grid_row": None,
        "axis_latitude": None,
        "axis_longitude": None,
        "house_nw_latitude": None,
        "house_nw_longitude": None,
        "house_ne_latitude": None,
        "house_ne_longitude": None,
        "house_se_latitude": None,
        "house_se_longitude": None,
        "house_sw_latitude": None,
        "house_sw_longitude": None,
    }


# ---------------------------------------------------------------------------
# Garden / ownership helpers (used by security.py and main.py at startup)
# ---------------------------------------------------------------------------


def _garden_slug_from_seed(seed: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", seed.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:80].rstrip("-") or "garden"


def ensure_default_garden(
    conn: DbConn,
) -> int:
    conn.execute(
        """
        INSERT INTO gardens (slug, name)
        VALUES (%s, %s)
        ON CONFLICT(slug) DO NOTHING
        """,
        (_DEFAULT_GARDEN_SLUG, _DEFAULT_GARDEN_NAME),
    )
    row = conn.execute(
        "SELECT id FROM gardens WHERE slug = %s LIMIT 1",
        (_DEFAULT_GARDEN_SLUG,),
    ).fetchone()
    if not row:
        raise RuntimeError("Failed to create default garden")
    return int(row["id"])


def ensure_default_garden_membership(
    conn: DbConn,
    *,
    user_id: int,
    role: str,
) -> int:
    default_garden_id = ensure_default_garden(conn)
    conn.execute(
        """
        INSERT INTO garden_memberships (garden_id, user_id, role)
        VALUES (%s, %s, %s)
        ON CONFLICT(garden_id, user_id) DO UPDATE SET
            role = excluded.role
        """,
        (default_garden_id, user_id, role),
    )
    return default_garden_id


def ensure_indoor_plot(
    conn: DbConn,
    garden_id: int,
    owner_user_id: int | None = None,
) -> None:
    """Create the INDOOR plot + ownership row for a garden, idempotently."""
    plot_id = f"INDOOR-{garden_id}"
    conn.execute(
        "INSERT INTO plots "
        "(plot_id, garden_id, zone_code, zone_name, plot_number, grid_row, grid_col) "
        "VALUES (%s, %s, 'I', 'Innendors', 0, NULL, NULL) "
        "ON CONFLICT DO NOTHING",
        (plot_id, garden_id),
    )
    # Use provided owner_user_id, fall back to first user for migration
    effective_owner = owner_user_id
    if effective_owner is None:
        row = conn.execute("SELECT MIN(id) AS uid FROM auth_users").fetchone()
        effective_owner = row["uid"] if row else None  # type: ignore[index]
    if effective_owner is not None:
        conn.execute(
            "INSERT INTO plot_ownership"
            " (plot_id, owner_user_id, garden_id) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (plot_id, effective_owner, garden_id),
        )
    conn.commit()


def ensure_data_ownership(
    conn: DbConn,
    owner_user_id: int,
) -> None:
    """Assign unowned plots/plants to the given user in the default garden."""
    default_garden_id = ensure_default_garden(conn)
    conn.execute(
        """
        INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
        SELECT p.plot_id, %s, %s
        FROM plots p
        WHERE NOT EXISTS (
            SELECT 1 FROM plot_ownership po WHERE po.plot_id = p.plot_id
        )
        ON CONFLICT DO NOTHING
        """,
        (owner_user_id, default_garden_id),
    )
    conn.execute(
        """
        UPDATE plots p
        SET garden_id = po.garden_id
        FROM plot_ownership po
        WHERE po.plot_id = p.plot_id
          AND p.garden_id IS DISTINCT FROM po.garden_id
        """,
    )
    conn.execute(
        """
        INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
        SELECT p.plt_id, %s, %s
        FROM plants p
        WHERE NOT EXISTS (
            SELECT 1 FROM plant_ownership po
            WHERE po.plt_id = p.plt_id AND po.garden_id = %s
        )
        ON CONFLICT DO NOTHING
        """,
        (owner_user_id, default_garden_id, default_garden_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Diagnostics (used by health router and startup checks)
# ---------------------------------------------------------------------------


def db_quick_check(
    conn: DbConn,
) -> str:
    """Basic database connectivity check."""
    try:
        conn.execute("SELECT 1")
        return "ok"
    except Exception as exc:
        return str(exc)[:200]


def db_foreign_key_violations(
    conn: DbConn,
) -> list[tuple[str, ...]]:
    """Return FK violations.  Postgres enforces FKs at write time, so this
    is always empty for a healthy database."""
    return []


def db_table_count(
    conn: DbConn,
) -> int:
    row = conn.execute(
        "SELECT count(*) AS cnt FROM information_schema.tables"
        " WHERE table_schema = 'public'"
        " AND table_type = 'BASE TABLE'"
    ).fetchone()
    return int(row["cnt"]) if row else 0  # type: ignore[index]


# ---------------------------------------------------------------------------
# init_db — run migrations at startup
# ---------------------------------------------------------------------------


def init_db() -> None:
    run_migrations()
