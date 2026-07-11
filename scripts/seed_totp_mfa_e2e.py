#!/usr/bin/env python3
"""Seed and verify the disposable real-backend TOTP MFA browser journey."""

from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlsplit

from psycopg.conninfo import conninfo_to_dict

from gardenops.db import close_pool, get_db, return_db
from gardenops.feature_gates import features_for_tier
from gardenops.security import generate_passkey_user_handle, hash_password

_DISPOSABLE_DATABASE_NAME = "gardenops_test"
_DISPOSABLE_MARKER_SETTING = "gardenops.disposable_marker"
_TEST_MFA_SECRET_KEY = "gardenops-totp-mfa-e2e-test-key-only-2026-07-10"  # noqa: E501  # push-sanitizer: allow SECRET_ASSIGNMENT

E2E_USERNAME = "totp_mfa_e2e_admin"
E2E_GARDEN_SLUG = "totp-mfa-e2e"
E2E_GARDEN_NAME = "TOTP MFA E2E Garden"


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"TOTP MFA E2E requires {name}")
    return value


def _fixture_password() -> str:
    password = _required_env("GARDENOPS_TOTP_MFA_E2E_PASSWORD")
    if (
        len(password) < 20
        or not any(char.islower() for char in password)
        or not any(char.isupper() for char in password)
        or not any(char.isdigit() for char in password)
        or not any(not char.isalnum() for char in password)
    ):
        raise RuntimeError(
            "TOTP MFA E2E fixture password does not meet the strong-password contract"
        )
    return password


def require_totp_mfa_e2e_database(database_url: str) -> None:
    """Reject every database except the runner-issued disposable test database."""
    if os.environ.get("GARDENOPS_TOTP_MFA_E2E_CHILD", "") != "1":
        raise RuntimeError("TOTP MFA E2E must run as the disposable runner child")
    if os.environ.get("APP_ENV", "").strip().lower() != "test":
        raise RuntimeError("TOTP MFA E2E seeding requires APP_ENV=test")
    if os.environ.get("AUTH_REQUIRED", "").strip().lower() != "true":
        raise RuntimeError("TOTP MFA E2E seeding requires AUTH_REQUIRED=true")
    if os.environ.get("AUTH_MODE", "").strip().lower() != "session":
        raise RuntimeError("TOTP MFA E2E seeding requires AUTH_MODE=session")
    if os.environ.get("AUTH_ADMIN_MFA_REQUIRED", "").strip().lower() != "true":
        raise RuntimeError("TOTP MFA E2E seeding requires AUTH_ADMIN_MFA_REQUIRED=true")
    if os.environ.get("GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED", "").strip().lower() != "false":
        raise RuntimeError(
            "TOTP MFA E2E seeding requires the notification scheduler to be disabled"
        )
    if os.environ.get("GARDENOPS_TOTP_MFA_E2E_ALLOW_TRUNCATE", "") != "1":
        raise RuntimeError("TOTP MFA E2E seeding requires its explicit truncate guard")
    if os.environ.get("AUTH_MFA_SECRET_KEY", "") != _TEST_MFA_SECRET_KEY:
        raise RuntimeError("TOTP MFA E2E requires its fixed test-only MFA encryption key")

    expected_url = _required_env("GARDENOPS_DISPOSABLE_POSTGRES_URL")
    if database_url != expected_url:
        raise RuntimeError("TOTP MFA E2E DATABASE_URL must exactly match the runner-issued URL")

    marker = _required_env("GARDENOPS_DISPOSABLE_POSTGRES_MARKER")
    system_identifier = _required_env("GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER")
    if not system_identifier.isdecimal():
        raise RuntimeError("TOTP MFA E2E runner system identifier must be numeric")
    if not marker.startswith(f"{system_identifier}."):
        raise RuntimeError("TOTP MFA E2E disposable marker is not bound to the runner cluster")

    try:
        conninfo = conninfo_to_dict(database_url)
        parsed = urlsplit(database_url)
        parsed_port = parsed.port
    except Exception as exc:
        raise RuntimeError("TOTP MFA E2E database URL is invalid") from exc
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.query or parsed.fragment:
        raise RuntimeError("TOTP MFA E2E database URL must be an exact TCP URL")
    if parsed.hostname != "127.0.0.1":
        raise RuntimeError("TOTP MFA E2E database URL must use disposable TCP loopback")
    if parsed.path != f"/{_DISPOSABLE_DATABASE_NAME}":
        raise RuntimeError("TOTP MFA E2E database URL must use the disposable test database")
    if parsed_port is None or parsed_port == 5432:
        raise RuntimeError("TOTP MFA E2E database URL must not use port 5432")
    if not 1 <= parsed_port <= 65535:
        raise RuntimeError("TOTP MFA E2E database URL has an invalid disposable port")
    effective_host = str(conninfo.get("host") or "").strip()
    effective_port = str(conninfo.get("port") or "").strip()
    effective_database = str(conninfo.get("dbname") or "").strip()
    if (
        effective_host != "127.0.0.1"
        or conninfo.get("hostaddr")
        or conninfo.get("service")
        or effective_port != str(parsed_port)
        or effective_database != _DISPOSABLE_DATABASE_NAME
    ):
        raise RuntimeError("TOTP MFA E2E database URL must resolve only to disposable TCP loopback")


def verify_totp_mfa_e2e_database_marker(conn) -> None:
    expected_marker = _required_env("GARDENOPS_DISPOSABLE_POSTGRES_MARKER")
    expected_system_identifier = _required_env("GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER")
    marker_row = conn.execute(
        "SELECT current_setting(%s, true) AS disposable_marker",
        (_DISPOSABLE_MARKER_SETTING,),
    ).fetchone()
    actual_marker = str(marker_row["disposable_marker"] or "") if marker_row else ""
    if actual_marker != expected_marker:
        raise RuntimeError("TOTP MFA E2E database marker does not match the runner-issued marker")
    system_row = conn.execute(
        "SELECT system_identifier FROM pg_control_system()",
    ).fetchone()
    actual_system_identifier = str(system_row["system_identifier"] or "") if system_row else ""
    if actual_system_identifier != expected_system_identifier:
        raise RuntimeError("TOTP MFA E2E database system identifier does not match the runner")


def truncate_public_tables(conn) -> None:
    rows = conn.execute(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename <> 'schema_migrations'
        ORDER BY tablename
        """,
    ).fetchall()
    table_names = [str(row["tablename"]) for row in rows]
    if table_names:
        quoted = ", ".join(f'public."{name}"' for name in table_names)
        conn.execute(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")


def seed(conn) -> None:
    """Create exactly one active, password-authenticated pro admin and garden."""
    if "planner" not in features_for_tier("pro"):
        raise RuntimeError("TOTP MFA E2E requires planner to be available to pro users")
    truncate_public_tables(conn)
    password = _fixture_password()
    user_row = conn.execute(
        """
        INSERT INTO auth_users (
            username, password_hash, password_auth_disabled, passkey_user_handle,
            role, is_active, must_change_password, subscription_tier,
            mfa_totp_secret, mfa_totp_enabled, mfa_enrolled_at, last_totp_counter
        )
        VALUES (%s, %s, 0, %s, 'admin', 1, 0, 'pro', NULL, 0, NULL, 0)
        RETURNING id
        """,
        (E2E_USERNAME, hash_password(password), generate_passkey_user_handle()),
    ).fetchone()
    if not user_row:
        raise RuntimeError("TOTP MFA E2E could not create its admin fixture")
    user_id = int(user_row["id"])
    garden_row = conn.execute(
        """
        INSERT INTO gardens (
            slug, name, grid_rows, grid_cols, onboarding_complete, owner_user_id
        )
        VALUES (%s, %s, 8, 8, 1, %s)
        RETURNING id
        """,
        (E2E_GARDEN_SLUG, E2E_GARDEN_NAME, user_id),
    ).fetchone()
    if not garden_row:
        raise RuntimeError("TOTP MFA E2E could not create its active garden fixture")
    garden_id = int(garden_row["id"])
    conn.execute(
        """
        INSERT INTO garden_memberships (garden_id, user_id, role)
        VALUES (%s, %s, 'admin')
        """,
        (garden_id, user_id),
    )
    passkey_row = conn.execute(
        "SELECT COUNT(*) AS count FROM auth_passkeys WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    mfa_row = conn.execute(
        """
        SELECT mfa_totp_enabled
        FROM auth_users
        WHERE id = %s
        """,
        (user_id,),
    ).fetchone()
    if not passkey_row or int(passkey_row["count"] or 0) != 0:
        raise RuntimeError("TOTP MFA E2E fixture must not have passkeys")
    if not mfa_row or int(mfa_row["mfa_totp_enabled"] or 0) != 0:
        raise RuntimeError("TOTP MFA E2E fixture must start without MFA")


def _count(conn, query: str, params: tuple[object, ...]) -> int:
    row = conn.execute(query, params).fetchone()
    return int(row["count"] if row else 0)


def snapshot(conn) -> dict[str, bool | int]:
    """Return only boolean/count postconditions; never materialize credential values."""
    user_row = conn.execute(
        """
        SELECT id, mfa_totp_enabled, mfa_totp_secret IS NOT NULL AS has_totp_secret
        FROM auth_users
        WHERE username = %s
        LIMIT 1
        """,
        (E2E_USERNAME,),
    ).fetchone()
    if not user_row:
        raise RuntimeError("TOTP MFA E2E admin fixture is missing")
    user_id = int(user_row["id"])
    pending_count = _count(
        conn,
        "SELECT COUNT(*) AS count FROM auth_mfa_pending_enrollments WHERE user_id = %s",
        (user_id,),
    )
    unused_recovery_count = _count(
        conn,
        """
        SELECT COUNT(*) AS count
        FROM auth_mfa_recovery_codes
        WHERE user_id = %s AND used_at_ms IS NULL
        """,
        (user_id,),
    )
    mfa_session_count = _count(
        conn,
        """
        SELECT COUNT(*) AS count
        FROM auth_sessions
        WHERE user_id = %s
          AND mfa_authenticated_at_ms > 0
          AND mfa_setup_required = 0
        """,
        (user_id,),
    )
    reauthenticated_session_count = _count(
        conn,
        """
        SELECT COUNT(*) AS count
        FROM auth_sessions
        WHERE user_id = %s
          AND reauthenticated_at_ms > 0
          AND mfa_authenticated_at_ms > 0
          AND mfa_setup_required = 0
        """,
        (user_id,),
    )
    audit_totp_start_count = _count(
        conn,
        """
        SELECT COUNT(*) AS count
        FROM audit_events
        WHERE actor_user_id = %s AND detail LIKE %s
        """,
        (user_id, "auth.mfa.totp.start %"),
    )
    audit_totp_confirm_count = _count(
        conn,
        """
        SELECT COUNT(*) AS count
        FROM audit_events
        WHERE actor_user_id = %s AND detail LIKE %s
        """,
        (user_id, "auth.mfa.totp.confirm %"),
    )
    audit_reauthenticate_count = _count(
        conn,
        """
        SELECT COUNT(*) AS count
        FROM audit_events
        WHERE actor_user_id = %s AND detail LIKE %s
        """,
        (user_id, "auth.session.reauthenticate %"),
    )
    audit_recovery_regenerate_count = _count(
        conn,
        """
        SELECT COUNT(*) AS count
        FROM audit_events
        WHERE actor_user_id = %s AND detail LIKE %s
        """,
        (user_id, "auth.mfa.recovery-codes.regenerate %"),
    )
    manifest: dict[str, bool | int] = {
        "totp_enabled": bool(int(user_row["mfa_totp_enabled"] or 0)),
        "totp_secret_stored": bool(user_row["has_totp_secret"]),
        "pending_enrollment_gone": pending_count == 0,
        "pending_enrollment_count": pending_count,
        "unused_recovery_rows_exist": unused_recovery_count > 0,
        "unused_recovery_row_count": unused_recovery_count,
        "mfa_authenticated_session_count": mfa_session_count,
        "reauthenticated_mfa_session_count": reauthenticated_session_count,
        "audit_totp_start_count": audit_totp_start_count,
        "audit_totp_confirm_count": audit_totp_confirm_count,
        "audit_reauthenticate_count": audit_reauthenticate_count,
        "audit_recovery_regenerate_count": audit_recovery_regenerate_count,
    }
    if not manifest["totp_enabled"] or not manifest["totp_secret_stored"]:
        raise RuntimeError("TOTP MFA E2E did not persist enabled TOTP")
    if not manifest["pending_enrollment_gone"]:
        raise RuntimeError("TOTP MFA E2E left a pending enrollment")
    if not manifest["unused_recovery_rows_exist"]:
        raise RuntimeError("TOTP MFA E2E did not persist unused recovery rows")
    if mfa_session_count < 1 or reauthenticated_session_count < 1:
        raise RuntimeError("TOTP MFA E2E did not persist an MFA reauthenticated session")
    if (
        audit_totp_start_count != 1
        or audit_totp_confirm_count != 1
        or audit_reauthenticate_count != 1
        or audit_recovery_regenerate_count != 1
    ):
        raise RuntimeError("TOTP MFA E2E audit postconditions were not persisted")
    return manifest


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"seed", "snapshot"}:
        raise SystemExit("Usage: seed_totp_mfa_e2e.py {seed|snapshot}")
    require_totp_mfa_e2e_database(os.environ.get("DATABASE_URL", ""))
    conn = None
    try:
        conn = get_db()
        verify_totp_mfa_e2e_database_marker(conn)
        if sys.argv[1] == "seed":
            seed(conn)
            conn.commit()
        else:
            manifest = snapshot(conn)
            print(json.dumps(manifest, sort_keys=True))
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            return_db(conn)
        close_pool()


if __name__ == "__main__":
    main()
