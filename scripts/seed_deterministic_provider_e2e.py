#!/usr/bin/env python3
"""Seed the guarded deterministic-provider Playwright fixture."""

from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlsplit

from psycopg.conninfo import conninfo_to_dict

from gardenops.db import close_pool, get_db, return_db
from gardenops.feature_gates import features_for_tier
from gardenops.security import generate_passkey_user_handle, hash_password

_DISPOSABLE_MARKER_SETTING = "gardenops.disposable_marker"
_DISPOSABLE_DATABASE_NAME = "gardenops_test"
_TRUNCATE_ENV = "GARDENOPS_DETERMINISTIC_PROVIDER_E2E_ALLOW_TRUNCATE"
_DETERMINISTIC_PROVIDER_ENV = "GARDENOPS_E2E_DETERMINISTIC_AI_PROVIDER"

E2E_GARDEN_SLUG = "deterministic-provider-e2e"
E2E_GARDEN_NAME = "Deterministic Provider E2E"
E2E_ADMIN_USERNAME = os.environ.get(
    "GARDENOPS_DETERMINISTIC_PROVIDER_E2E_USERNAME",
    "deterministic_provider_e2e_admin",
).strip()
E2E_ADMIN_PASSWORD = os.environ.get(
    "GARDENOPS_DETERMINISTIC_PROVIDER_E2E_PASSWORD",
    "DeterministicProviderE2E!Passphrase2026",
)
E2E_PROVIDER_FEATURE = "ai-garden-chat"


def _require_nonempty_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Deterministic-provider E2E requires {name}")
    return value


def require_deterministic_provider_e2e_database(database_url: str) -> None:
    """Reject every database except the runner-issued disposable command database."""
    if os.environ.get("APP_ENV", "") != "test":
        raise RuntimeError("Deterministic-provider E2E seeding requires APP_ENV=test")
    if os.environ.get("AUTH_REQUIRED", "") != "true":
        raise RuntimeError("Deterministic-provider E2E seeding requires AUTH_REQUIRED=true")
    if os.environ.get("AUTH_MODE", "") != "session":
        raise RuntimeError("Deterministic-provider E2E seeding requires AUTH_MODE=session")
    if os.environ.get(_TRUNCATE_ENV, "") != "1":
        raise RuntimeError(f"Deterministic-provider E2E seeding requires {_TRUNCATE_ENV}=1")
    if os.environ.get(_DETERMINISTIC_PROVIDER_ENV, "") != "1":
        raise RuntimeError(
            f"Deterministic-provider E2E seeding requires {_DETERMINISTIC_PROVIDER_ENV}=1"
        )
    if os.environ.get("AI_PROVIDER", "") != "disabled":
        raise RuntimeError("Deterministic-provider E2E seeding requires AI_PROVIDER=disabled")

    expected_url = _require_nonempty_env("GARDENOPS_DISPOSABLE_POSTGRES_URL")
    if database_url != expected_url:
        raise RuntimeError(
            "Deterministic-provider E2E DATABASE_URL must exactly match the runner-issued URL"
        )

    marker = _require_nonempty_env("GARDENOPS_DISPOSABLE_POSTGRES_MARKER")
    system_identifier = _require_nonempty_env(
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER"
    )
    if not system_identifier.isdecimal():
        raise RuntimeError("Deterministic-provider E2E system identifier must be numeric")
    if not marker.startswith(f"{system_identifier}."):
        raise RuntimeError(
            "Deterministic-provider E2E marker is not bound to the runner system identifier"
        )

    try:
        parsed = urlsplit(database_url)
        conninfo = conninfo_to_dict(database_url)
        parsed_port = parsed.port
    except Exception as exc:
        raise RuntimeError("Deterministic-provider E2E database URL is invalid") from exc

    if parsed.scheme not in {"postgres", "postgresql"} or parsed.query or parsed.fragment:
        raise RuntimeError("Deterministic-provider E2E database URL must be an exact TCP URL")
    if parsed.hostname != "127.0.0.1":
        raise RuntimeError("Deterministic-provider E2E database URL must use TCP loopback")
    if parsed.path != f"/{_DISPOSABLE_DATABASE_NAME}":
        raise RuntimeError("Deterministic-provider E2E database URL must use gardenops_test")
    if parsed_port is None or parsed_port == 5432 or not 1 <= parsed_port <= 65535:
        raise RuntimeError(
            "Deterministic-provider E2E database URL must use a non-5432 disposable port"
        )

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
        raise RuntimeError(
            "Deterministic-provider E2E database URL must resolve only to disposable TCP loopback"
        )


def verify_deterministic_provider_e2e_database_marker(conn) -> None:
    """Verify the marker and system identifier issued by command mode before truncation."""
    expected_marker = _require_nonempty_env("GARDENOPS_DISPOSABLE_POSTGRES_MARKER")
    expected_system_identifier = _require_nonempty_env(
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER"
    )
    marker_row = conn.execute(
        "SELECT current_setting(%s, true) AS disposable_marker",
        (_DISPOSABLE_MARKER_SETTING,),
    ).fetchone()
    actual_marker = str(marker_row["disposable_marker"] or "") if marker_row else ""
    if actual_marker != expected_marker:
        raise RuntimeError(
            "Deterministic-provider E2E database marker does not match the runner-issued marker"
        )

    identifier_row = conn.execute(
        "SELECT system_identifier FROM pg_control_system()"
    ).fetchone()
    actual_system_identifier = (
        str(identifier_row["system_identifier"] or "") if identifier_row else ""
    )
    if actual_system_identifier != expected_system_identifier:
        raise RuntimeError(
            "Deterministic-provider E2E database system identifier does not match the runner"
        )


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def truncate_public_tables(conn) -> None:
    rows = conn.execute(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename <> 'schema_migrations'
        ORDER BY tablename
        """
    ).fetchall()
    table_names = [str(row["tablename"]) for row in rows]
    if not table_names:
        return
    quoted_tables = ", ".join(f"public.{_quote_identifier(name)}" for name in table_names)
    conn.execute(f"TRUNCATE TABLE {quoted_tables} RESTART IDENTITY CASCADE")


def seed_admin(conn) -> int:
    row = conn.execute(
        """
        INSERT INTO auth_users (
            username, password_hash, password_auth_disabled, passkey_user_handle,
            role, is_active, must_change_password, subscription_tier
        )
        VALUES (%s, %s, 0, %s, 'admin', 1, 0, 'pro')
        RETURNING id
        """,
        (
            E2E_ADMIN_USERNAME,
            hash_password(E2E_ADMIN_PASSWORD),  # push-sanitizer: allow SECRET_ASSIGNMENT
            generate_passkey_user_handle(),
        ),
    ).fetchone()
    if not row:
        raise RuntimeError("Failed to create deterministic-provider E2E admin")
    return int(row["id"])


def seed_active_garden(conn, *, owner_user_id: int) -> int:
    row = conn.execute(
        """
        INSERT INTO gardens (
            slug, name, grid_rows, grid_cols, onboarding_complete, owner_user_id
        )
        VALUES (%s, %s, 8, 8, 1, %s)
        RETURNING id
        """,
        (E2E_GARDEN_SLUG, E2E_GARDEN_NAME, owner_user_id),
    ).fetchone()
    if not row:
        raise RuntimeError("Failed to create deterministic-provider E2E garden")
    garden_id = int(row["id"])
    conn.execute(
        """
        INSERT INTO garden_memberships (garden_id, user_id, role)
        VALUES (%s, %s, 'admin')
        """,
        (garden_id, owner_user_id),
    )
    return garden_id


def seed(conn) -> None:
    truncate_public_tables(conn)
    admin_user_id = seed_admin(conn)
    seed_active_garden(conn, owner_user_id=admin_user_id)


def print_snapshot(conn) -> None:
    user = conn.execute(
        """
        SELECT id, username, subscription_tier
        FROM auth_users
        WHERE username = %s
        """,
        (E2E_ADMIN_USERNAME,),
    ).fetchone()
    garden = conn.execute(
        """
        SELECT g.id, g.slug, g.name, gm.role AS membership_role
        FROM gardens g
        JOIN garden_memberships gm ON gm.garden_id = g.id
        JOIN auth_users u ON u.id = gm.user_id
        WHERE g.slug = %s AND u.username = %s
        """,
        (E2E_GARDEN_SLUG, E2E_ADMIN_USERNAME),
    ).fetchone()
    garden_count_row = conn.execute("SELECT COUNT(*) AS count FROM gardens").fetchone()
    usage_rows = conn.execute(
        """
        SELECT usage_day, feature, scope_type, scope_id, request_count
        FROM provider_daily_usage
        ORDER BY feature, scope_type, scope_id
        """
    ).fetchall()

    if not user or not garden:
        print(json.dumps({"status": "missing"}, sort_keys=True))
        return
    tier = str(user["subscription_tier"] or "home")
    print(
        json.dumps(
            {
                "garden": {
                    "id": int(garden["id"]),
                    "membership_role": str(garden["membership_role"]),
                    "name": str(garden["name"]),
                    "slug": str(garden["slug"]),
                },
                "garden_count": int(garden_count_row["count"] if garden_count_row else 0),
                "planner_enabled": "planner" in features_for_tier(tier),
                "provider_usage": [
                    {
                        "feature": str(row["feature"]),
                        "request_count": int(row["request_count"]),
                        "scope_id": int(row["scope_id"]),
                        "scope_type": str(row["scope_type"]),
                        "usage_day": str(row["usage_day"]),
                    }
                    for row in usage_rows
                ],
                "status": "seeded",
                "user": {
                    "id": int(user["id"]),
                    "subscription_tier": tier,
                    "username": str(user["username"]),
                },
            },
            sort_keys=True,
        )
    )


def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    require_deterministic_provider_e2e_database(database_url)
    conn = None
    try:
        conn = get_db()
        verify_deterministic_provider_e2e_database_marker(conn)
        if len(sys.argv) == 2 and sys.argv[1] == "snapshot":
            print_snapshot(conn)
            return
        if len(sys.argv) != 1:
            raise SystemExit("Usage: seed_deterministic_provider_e2e.py [snapshot]")
        seed(conn)
        conn.commit()
        print_snapshot(conn)
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
