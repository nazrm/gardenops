import logging
from typing import Any

from gardenops.db import DbConn, current_timestamp_ms, get_db, return_db
from gardenops.security import AuthContext
from gardenops.security_telemetry import enqueue_security_telemetry

_logger = logging.getLogger(__name__)


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
        if resolved_garden_id is None and auth_context.garden_id is not None:
            resolved_garden_id = int(auth_context.garden_id)

    owns_conn = db is None
    conn = get_db() if owns_conn else db
    assert conn is not None
    try:
        conn.execute(
            """
            INSERT INTO audit_events (
                occurred_at_ms, actor_user_id, actor_username, actor_role, actor_auth_type,
                garden_id, method, path, status_code, remote_host, detail
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                occurred_at_ms,
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
            ),
        )
        conn.commit()
        try:
            enqueue_security_telemetry(
                "audit_event",
                {
                    "occurred_at_ms": occurred_at_ms,
                    "actor_user_id": actor_user_id,
                    "actor_username": actor_username,
                    "actor_role": actor_role,
                    "actor_auth_type": actor_auth_type,
                    "garden_id": resolved_garden_id,
                    "method": method,
                    "path": path,
                    "status_code": int(status_code),
                    "remote_host": remote_host,
                    "detail": detail.strip(),
                },
                created_at_ms=occurred_at_ms,
                db=conn,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            _logger.warning("security telemetry enqueue failed after audit write", exc_info=True)
    except Exception:
        _logger.warning("audit write failed", exc_info=True)
        return
    finally:
        if owns_conn:
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
        SELECT id, occurred_at_ms, actor_user_id, actor_username, actor_role, actor_auth_type,
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
