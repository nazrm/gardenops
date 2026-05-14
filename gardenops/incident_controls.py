from gardenops.db import DbConn, current_timestamp_ms, get_db, return_db

_FLAG_EMERGENCY_READ_ONLY = "emergency_read_only"
_FLAG_EMERGENCY_READ_ONLY_EXPIRES_AT_MS = "emergency_read_only_expires_at_ms"


def get_runtime_flag(conn: DbConn, key: str, default: str = "0") -> str:
    row = conn.execute(
        "SELECT value FROM security_runtime_flags WHERE key = %s",
        (key,),
    ).fetchone()
    if not row:
        return default
    return str(row["value"])


def set_runtime_flag(conn: DbConn, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO security_runtime_flags (key, value, updated_at)
        VALUES (%s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value),
    )


def _read_positive_int_flag(conn: DbConn, key: str) -> int | None:
    raw = get_runtime_flag(conn, key, "0").strip()
    try:
        parsed = int(raw)
    except ValueError:
        return None
    if parsed <= 0:
        return None
    return parsed


def get_emergency_read_only_status(
    conn: DbConn,
) -> dict[str, int | bool | None]:
    enabled = get_runtime_flag(conn, _FLAG_EMERGENCY_READ_ONLY, "0") == "1"
    expires_at_ms = _read_positive_int_flag(conn, _FLAG_EMERGENCY_READ_ONLY_EXPIRES_AT_MS)
    if enabled and expires_at_ms is not None and expires_at_ms <= current_timestamp_ms():
        set_runtime_flag(conn, _FLAG_EMERGENCY_READ_ONLY, "0")
        set_runtime_flag(conn, _FLAG_EMERGENCY_READ_ONLY_EXPIRES_AT_MS, "0")
        conn.commit()
        enabled = False
        expires_at_ms = None
    return {
        "enabled": enabled,
        "expires_at_ms": expires_at_ms,
    }


def is_emergency_read_only() -> bool:
    conn = get_db()
    try:
        status = get_emergency_read_only_status(conn)
        return bool(status["enabled"])
    finally:
        return_db(conn)


def set_emergency_read_only(
    enabled: bool,
    *,
    expires_at_ms: int | None = None,
) -> dict[str, int | bool | None]:
    conn = get_db()
    try:
        set_runtime_flag(conn, _FLAG_EMERGENCY_READ_ONLY, "1" if enabled else "0")
        set_runtime_flag(
            conn,
            _FLAG_EMERGENCY_READ_ONLY_EXPIRES_AT_MS,
            str(expires_at_ms) if enabled and expires_at_ms and expires_at_ms > 0 else "0",
        )
        conn.commit()
        return get_emergency_read_only_status(conn)
    finally:
        return_db(conn)


def list_active_sessions(conn: DbConn) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT
               s.token_hash,
               s.user_id,
               s.expires_at_ms,
               s.created_at_ms,
               s.last_seen_at_ms,
               s.reauthenticated_at_ms,
               s.mfa_authenticated_at_ms,
               s.mfa_setup_required,
               u.username, u.role
        FROM auth_sessions s
        JOIN auth_users u ON u.id = s.user_id
        ORDER BY s.last_seen_at_ms DESC
        """,
    ).fetchall()
    return [
        {
            "token_hash": str(row["token_hash"]),
            "user_id": int(row["user_id"]),
            "username": str(row["username"]),
            "role": str(row["role"]),
            "expires_at_ms": int(row["expires_at_ms"]),
            "created_at_ms": int(row["created_at_ms"]),
            "last_seen_at_ms": int(row["last_seen_at_ms"]),
            "reauthenticated_at_ms": int(row["reauthenticated_at_ms"]),
            "mfa_authenticated_at_ms": int(row["mfa_authenticated_at_ms"]),
            "mfa_setup_required": bool(int(row["mfa_setup_required"])),
        }
        for row in rows
    ]


def revoke_sessions_by_user(conn: DbConn, username: str) -> int:
    row = conn.execute(
        "SELECT id FROM auth_users WHERE username = %s",
        (username.strip(),),
    ).fetchone()
    if not row:
        return 0
    user_id = int(row["id"])
    deleted = conn.execute(
        "DELETE FROM auth_sessions WHERE user_id = %s",
        (user_id,),
    )
    return int(deleted.rowcount)


def revoke_all_sessions(conn: DbConn, *, except_token_hash: str | None = None) -> int:
    if except_token_hash:
        deleted = conn.execute(
            "DELETE FROM auth_sessions WHERE token_hash <> %s",
            (except_token_hash,),
        )
    else:
        deleted = conn.execute("DELETE FROM auth_sessions")
    return int(deleted.rowcount)
