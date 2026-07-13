import logging
from typing import Any

from gardenops.db import DbConn, current_timestamp_ms, get_db, return_db
from gardenops.observability import get_request_id
from gardenops.security import AuthContext
from gardenops.security_telemetry import enqueue_security_telemetry

_logger = logging.getLogger(__name__)

_AUDIT_EVENT_INSERT_SQL = """
    INSERT INTO audit_events (
        occurred_at_ms, request_id, actor_user_id, actor_username, actor_role, actor_auth_type,
        garden_id, method, path, status_code, remote_host, detail
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (request_id) WHERE request_id != ''
    DO UPDATE SET
        occurred_at_ms = EXCLUDED.occurred_at_ms,
        actor_user_id = EXCLUDED.actor_user_id,
        actor_username = EXCLUDED.actor_username,
        actor_role = EXCLUDED.actor_role,
        actor_auth_type = EXCLUDED.actor_auth_type,
        garden_id = EXCLUDED.garden_id,
        method = EXCLUDED.method,
        path = EXCLUDED.path,
        status_code = EXCLUDED.status_code,
        remote_host = EXCLUDED.remote_host,
        detail = EXCLUDED.detail
    WHERE audit_events.status_code = 102
      AND audit_events.detail = 'mutation_started'
    RETURNING id
"""

_AUDIT_EVENT_RESERVE_SQL = """
    INSERT INTO audit_events (
        occurred_at_ms, request_id, actor_user_id, actor_username, actor_role, actor_auth_type,
        garden_id, method, path, status_code, remote_host, detail
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (request_id) WHERE request_id != '' DO NOTHING
    RETURNING id
"""

type AuditEventValues = tuple[
    int,
    str,
    int | None,
    str,
    str,
    str,
    int | None,
    str,
    str,
    int,
    str,
    str,
]


def _audit_event_values(
    *,
    method: str,
    path: str,
    status_code: int,
    remote_host: str,
    detail: str,
    auth_context: AuthContext | None,
    garden_id: int | None,
    use_auth_context_garden: bool,
) -> AuditEventValues:
    occurred_at_ms = current_timestamp_ms()
    actor_user_id: int | None = None
    actor_username = "anonymous"
    actor_role = "anonymous"
    actor_auth_type = "none"
    resolved_garden_id = garden_id
    if auth_context:
        actor_user_id = auth_context.user_id
        actor_username = auth_context.username
        actor_role = auth_context.role
        actor_auth_type = auth_context.auth_type
        if (
            use_auth_context_garden
            and resolved_garden_id is None
            and auth_context.garden_id is not None
        ):
            resolved_garden_id = int(auth_context.garden_id)

    return (
        occurred_at_ms,
        get_request_id(),
        actor_user_id,
        actor_username,
        actor_role,
        actor_auth_type,
        resolved_garden_id,
        method,
        path,
        int(status_code),
        remote_host,
        detail.strip(),
    )


def _insert_audit_event_row(
    conn: DbConn,
    values: tuple[object, ...],
    *,
    reserve: bool = False,
) -> None:
    sql = _AUDIT_EVENT_RESERVE_SQL if reserve else _AUDIT_EVENT_INSERT_SQL
    if conn.execute(sql, values).fetchone() is None:
        raise RuntimeError("Audit request ID is already finalized or reserved")


def write_required_audit_event(
    *,
    method: str,
    path: str,
    status_code: int,
    remote_host: str,
    detail: str = "",
    auth_context: AuthContext | None = None,
    garden_id: int | None = None,
    use_auth_context_garden: bool = True,
    db: DbConn,
) -> AuditEventValues:
    """Insert an audit event in the caller-owned transaction.

    This deliberately does not commit, roll back, enqueue telemetry, or handle
    insertion errors. Callers that require audit durability must commit or roll
    back their business operation and this audit row together.
    """
    values = _audit_event_values(
        method=method,
        path=path,
        status_code=status_code,
        remote_host=remote_host,
        detail=detail,
        auth_context=auth_context,
        garden_id=garden_id,
        use_auth_context_garden=use_auth_context_garden,
    )
    _insert_audit_event_row(db, values)
    return values


def enqueue_audit_event_telemetry(
    values: AuditEventValues,
    *,
    db: DbConn,
) -> None:
    """Best-effort telemetry export after the durable audit transaction commits."""
    (
        occurred_at_ms,
        request_id,
        actor_user_id,
        actor_username,
        actor_role,
        actor_auth_type,
        resolved_garden_id,
        method,
        path,
        status_code,
        remote_host,
        detail,
    ) = values
    try:
        enqueue_security_telemetry(
            "audit_event",
            {
                "occurred_at_ms": occurred_at_ms,
                "request_id": request_id,
                "actor_user_id": actor_user_id,
                "actor_username": actor_username,
                "actor_role": actor_role,
                "actor_auth_type": actor_auth_type,
                "garden_id": resolved_garden_id,
                "method": method,
                "path": path,
                "status_code": int(status_code),
                "remote_host": remote_host,
                "detail": detail,
            },
            created_at_ms=occurred_at_ms,
            db=db,
        )
        db.commit()
    except Exception:
        db.rollback()
        _logger.warning("security telemetry enqueue failed after audit write", exc_info=True)


def write_audit_event(
    *,
    method: str,
    path: str,
    status_code: int,
    remote_host: str,
    detail: str = "",
    auth_context: AuthContext | None = None,
    garden_id: int | None = None,
    db: DbConn | None = None,
) -> None:
    owns_conn = db is None
    conn = get_db() if owns_conn else db
    assert conn is not None
    values = _audit_event_values(
        method=method,
        path=path,
        status_code=status_code,
        remote_host=remote_host,
        detail=detail,
        auth_context=auth_context,
        garden_id=garden_id,
        use_auth_context_garden=True,
    )
    try:
        _insert_audit_event_row(conn, values)
        conn.commit()
        enqueue_audit_event_telemetry(values, db=conn)
    except Exception:
        _logger.warning("audit write failed", exc_info=True)
        return
    finally:
        if owns_conn:
            return_db(conn)


def reserve_mutation_audit_event(
    *,
    method: str,
    path: str,
    remote_host: str,
    auth_context: AuthContext | None = None,
    garden_id: int | None = None,
) -> None:
    """Persist a fail-closed mutation intent before application code runs.

    The normal audit write later upserts this row by request ID with the final
    status. A process failure can therefore leave an explicit 102 intent, but
    never a successful mutation with no durable request-correlated audit row.
    """
    conn = get_db()
    try:
        values = _audit_event_values(
            method=method,
            path=path,
            status_code=102,
            remote_host=remote_host,
            detail="mutation_started",
            auth_context=auth_context,
            garden_id=garden_id,
            use_auth_context_garden=True,
        )
        if not values[1]:
            raise RuntimeError("Mutation audit reservation requires a request ID")
        _insert_audit_event_row(conn, values, reserve=True)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        return_db(conn)


def list_audit_events(
    conn: DbConn,
    *,
    limit: int = 200,
    offset: int = 0,
    garden_id: int | None = None,
    actor: str = "",
    path_prefix: str = "",
    method: str = "",
    status_code: int | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
) -> dict[str, Any]:
    safe_limit = max(1, min(limit, 1000))
    safe_offset = max(0, offset)
    filters: list[str] = []
    params: list[object] = []

    if garden_id is not None:
        filters.append("garden_id = %s")
        params.append(int(garden_id))

    actor = actor.strip()
    if actor:
        filters.append("actor_username ILIKE %s")
        params.append(f"%{actor}%")

    path_prefix = path_prefix.strip()
    if path_prefix:
        filters.append("path ILIKE %s")
        params.append(f"{path_prefix}%")

    method = method.strip().upper()
    if method:
        filters.append("method = %s")
        params.append(method)

    if status_code is not None:
        filters.append("status_code = %s")
        params.append(int(status_code))

    if from_ms is not None:
        filters.append("occurred_at_ms >= %s")
        params.append(int(from_ms))

    if to_ms is not None:
        filters.append("occurred_at_ms <= %s")
        params.append(int(to_ms))

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    count_sql = f"SELECT COUNT(*) AS c FROM audit_events {where_clause}"
    total_row = conn.execute(count_sql, tuple(params)).fetchone()
    total = int(total_row["c"] if total_row else 0)

    rows_sql = f"""
        SELECT id, occurred_at_ms, request_id, actor_user_id, actor_username,
               actor_role, actor_auth_type,
               garden_id, method, path, status_code, remote_host, detail
        FROM audit_events
        {where_clause}
        ORDER BY occurred_at_ms DESC, id DESC
        LIMIT %s OFFSET %s
    """
    rows = conn.execute(rows_sql, tuple([*params, safe_limit, safe_offset])).fetchall()
    return {
        "events": [dict(row) for row in rows],
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
    }
