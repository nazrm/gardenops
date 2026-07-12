#!/usr/bin/env python3
"""Seed and inspect the disposable Phase 0 complete-journey database."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from psycopg import sql

from gardenops.db import close_pool, get_db, return_db
from gardenops.security import generate_passkey_user_handle, hash_password

ADMIN_USERNAME = os.environ.get(
    "GARDENOPS_COMPLETE_JOURNEYS_E2E_USERNAME", "complete_journeys_e2e_admin"
)
ADMIN_PASSWORD = os.environ.get(
    "GARDENOPS_COMPLETE_JOURNEYS_E2E_PASSWORD",
    "CompleteJourneysE2E!Passphrase2026",
)
EDITOR_LOGIN = ("complete_journeys_e2e_editor", "CompleteJourneysEditorE2E!Passphrase2026")
VIEWER_LOGIN = ("complete_journeys_e2e_viewer", "CompleteJourneysViewerE2E!Passphrase2026")


def _require_child_environment() -> None:
    if os.environ.get("GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD") != "1":
        raise RuntimeError("Complete journey E2E must run as the disposable runner child")
    if os.environ.get("APP_ENV") != "test":
        raise RuntimeError("Complete journey E2E requires APP_ENV=test")
    if os.environ.get("AUTH_REQUIRED") != "true" or os.environ.get("AUTH_MODE") != "session":
        raise RuntimeError("Complete journey E2E requires session authentication")
    if os.environ.get("GARDENOPS_COMPLETE_JOURNEYS_E2E_ALLOW_TRUNCATE") != "1":
        raise RuntimeError("Complete journey E2E truncation guard is required")
    required = (
        "DATABASE_URL",
        "GARDENOPS_DISPOSABLE_POSTGRES_URL",
        "GARDENOPS_DISPOSABLE_POSTGRES_MARKER",
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER",
    )
    if any(not os.environ.get(name) for name in required):
        raise RuntimeError("Complete journey E2E requires runner-issued disposable evidence")
    if os.environ["DATABASE_URL"] != os.environ["GARDENOPS_DISPOSABLE_POSTGRES_URL"]:
        raise RuntimeError("Complete journey DATABASE_URL must match the runner-issued URL")
    system_identifier = os.environ["GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER"]
    marker = os.environ["GARDENOPS_DISPOSABLE_POSTGRES_MARKER"]
    if not system_identifier.isdecimal() or not marker.startswith(f"{system_identifier}."):
        raise RuntimeError("Complete journey disposable marker is not bound to the runner cluster")


def _configure_reused_seed_guard() -> None:
    os.environ["GARDENOPS_ALLOW_DESTRUCTIVE_E2E"] = "1"
    os.environ["GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_ALLOW_TRUNCATE"] = "1"
    os.environ["GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_USERNAME"] = ADMIN_USERNAME
    os.environ["GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_PASSWORD"] = ADMIN_PASSWORD


def _insert_user(conn, *, username: str, password: str, role: str) -> int:
    row = conn.execute(
        """
        INSERT INTO auth_users (
            username, password_hash, password_auth_disabled, passkey_user_handle,
            role, is_active, must_change_password, subscription_tier
        )
        VALUES (%s, %s, 0, %s, %s, 1, 0, 'home')
        RETURNING id
        """,
        (username, hash_password(password), generate_passkey_user_handle(), role),
    ).fetchone()
    if not row:
        raise RuntimeError(f"Complete journey E2E failed to create {role} user")
    return int(row["id"])


def _add_role_fixtures(conn, *, garden_ids: list[int]) -> None:
    editor_id = _insert_user(
        conn,
        username=EDITOR_LOGIN[0],
        password=EDITOR_LOGIN[1],
        role="editor",
    )
    viewer_id = _insert_user(
        conn,
        username=VIEWER_LOGIN[0],
        password=VIEWER_LOGIN[1],
        role="viewer",
    )
    for garden_id in garden_ids:
        for user_id, role in ((editor_id, "editor"), (viewer_id, "viewer")):
            conn.execute(
                "INSERT INTO garden_memberships (garden_id, user_id, role) VALUES (%s, %s, %s)",
                (garden_id, user_id, role),
            )


def _count(conn, table: str) -> int:
    allowed = {
        "auth_users",
        "garden_memberships",
        "garden_map_objects",
        "garden_tasks",
        "gardens",
        "layout_state",
        "notification_events",
        "plant_ownership",
        "plants",
        "plot_ownership",
        "plot_plants",
        "plots",
        "weather_alerts",
    }
    if table not in allowed:
        raise RuntimeError(f"Unsupported complete journey snapshot table: {table}")
    row = conn.execute(f'SELECT COUNT(*) AS count FROM "{table}"').fetchone()
    return int(row["count"] if row else 0)


def _domain_table_state(conn) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename <> 'schema_migrations'
          AND tablename NOT IN ('audit_events', 'auth_sessions', 'auth_users')
        ORDER BY tablename
        """
    ).fetchall()
    state: dict[str, dict[str, Any]] = {}
    for row in rows:
        table = str(row["tablename"])
        result = conn.execute(
            sql.SQL(
                """
                SELECT
                    COUNT(*) AS count,
                    md5(COALESCE(
                        string_agg(to_jsonb(row_value)::text, E'\\n'
                            ORDER BY to_jsonb(row_value)::text),
                        ''
                    )) AS digest
                FROM {} AS row_value
                """
            ).format(sql.Identifier(table))
        ).fetchone()
        state[table] = {
            "count": int(result["count"] if result else 0),
            "digest": str(result["digest"] if result else ""),
        }
    return state


def _auth_state(conn) -> dict[str, Any]:
    user = conn.execute(
        "SELECT id, last_login_at FROM auth_users WHERE username = %s",
        (ADMIN_USERNAME,),
    ).fetchone()
    if not user:
        raise RuntimeError("Complete journey fixture administrator is missing")
    session = conn.execute(
        "SELECT COUNT(*) AS count FROM auth_sessions WHERE user_id = %s",
        (int(user["id"]),),
    ).fetchone()
    digest = conn.execute(
        """
        SELECT md5(COALESCE(
            string_agg(
                jsonb_set(
                    to_jsonb(user_value),
                    '{last_login_at}',
                    CASE
                        WHEN username = %s THEN 'null'::jsonb
                        ELSE COALESCE(to_jsonb(last_login_at), 'null'::jsonb)
                    END
                )::text,
                E'\\n' ORDER BY jsonb_set(
                    to_jsonb(user_value),
                    '{last_login_at}',
                    CASE
                        WHEN username = %s THEN 'null'::jsonb
                        ELSE COALESCE(to_jsonb(last_login_at), 'null'::jsonb)
                    END
                )::text
            ),
            ''
        )) AS digest
        FROM auth_users AS user_value
        """,
        (ADMIN_USERNAME, ADMIN_USERNAME),
    ).fetchone()
    session_rows = conn.execute(
        """
        SELECT users.username, COUNT(sessions.token_hash) AS count
        FROM auth_users AS users
        LEFT JOIN auth_sessions AS sessions ON sessions.user_id = users.id
        GROUP BY users.username
        ORDER BY users.username
        """
    ).fetchall()
    invalid_session = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM auth_sessions
        WHERE expires_at_ms <= created_at_ms
           OR expires_at_ms - created_at_ms <> 43200000
           OR last_seen_at_ms < created_at_ms
           OR length(token_hash) <> 64
           OR reauthenticated_at_ms < created_at_ms
           OR mfa_authenticated_at_ms <> 0
           OR mfa_setup_required <> 0
        """
    ).fetchone()
    return {
        "admin_last_login_at": (
            str(user["last_login_at"]) if user["last_login_at"] is not None else None
        ),
        "admin_session_count": int(session["count"] if session else 0),
        "invalid_session_count": int(invalid_session["count"] if invalid_session else 0),
        "session_user_counts": {str(row["username"]): int(row["count"]) for row in session_rows},
        "users_expected_digest": str(digest["digest"] if digest else ""),
    }


def _audit_state(conn) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_count,
            COUNT(*) FILTER (
                WHERE method = 'POST'
                  AND path = '/api/auth/login'
                  AND status_code = 200
                  AND actor_user_id IS NULL
                  AND actor_username = 'anonymous'
                  AND actor_role = 'anonymous'
                  AND actor_auth_type = 'none'
                  AND garden_id IS NULL
                  AND remote_host IN ('127.0.0.1', '::1')
                  AND detail = ''
            ) AS expected_login_count
        FROM audit_events
        """
    ).fetchone()
    total = int(row["total_count"] if row else 0)
    expected = int(row["expected_login_count"] if row else 0)
    return {
        "expected_login_count": expected,
        "total_count": total,
        "unexpected_count": total - expected,
    }


def _snapshot(conn, optimization_seed: Any) -> dict[str, Any]:
    garden_rows = conn.execute(
        "SELECT id, slug, name FROM gardens WHERE slug = ANY(%s) ORDER BY slug",
        ([optimization_seed.GARDEN_A_SLUG, optimization_seed.GARDEN_B_SLUG],),
    ).fetchall()
    gardens_by_slug = {str(row["slug"]): row for row in garden_rows}

    def garden_payload(spec: dict[str, str], notification_title: str) -> dict[str, Any]:
        row = gardens_by_slug.get(spec["slug"])
        if not row:
            raise RuntimeError(f"Missing complete journey garden {spec['slug']}")
        return {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "notification_title": notification_title,
            "object_label": spec["object_name"],
            "object_public_id": spec["object_id"],
            "plant_name": spec["plant_name"],
            "slug": spec["slug"],
        }

    tables = (
        "auth_users",
        "garden_memberships",
        "garden_map_objects",
        "garden_tasks",
        "gardens",
        "layout_state",
        "notification_events",
        "plant_ownership",
        "plants",
        "plot_ownership",
        "plot_plants",
        "plots",
        "weather_alerts",
    )
    return {
        "database_snapshot": {
            "audit_state": _audit_state(conn),
            "auth_state": _auth_state(conn),
            "domain_counts": {table: _count(conn, table) for table in tables},
            "domain_tables": _domain_table_state(conn),
        },
        "gardens": {
            "alpha": garden_payload(
                optimization_seed._GARDEN_SPECS[0],
                optimization_seed.GARDEN_A_NOTIFICATION,
            ),
            "beta": garden_payload(
                optimization_seed._GARDEN_SPECS[1],
                optimization_seed.GARDEN_B_NOTIFICATION,
            ),
        },
        "git": {
            "dirty": bool(
                subprocess.run(
                    ["git", "status", "--porcelain"],
                    capture_output=True,
                    check=True,
                    text=True,
                ).stdout.strip()
            ),
            "sha": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                check=True,
                text=True,
            ).stdout.strip(),
        },
        "roles": {
            "admin": ADMIN_USERNAME,
            "editor": EDITOR_LOGIN[0],
            "viewer": VIEWER_LOGIN[0],
        },
        "suite": "complete-journeys-e2e",
    }


def _write_json_exclusive(output_path: Path, payload: dict[str, Any]) -> None:
    artifact_raw = os.environ.get("GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR", "")
    if not artifact_raw:
        raise RuntimeError("Complete journey artifact directory is required")
    artifact_dir = Path(artifact_raw).resolve(strict=True)
    if (
        output_path.name != "fixture.json"
        or output_path.parent.resolve(strict=True) != artifact_dir
    ):
        raise RuntimeError(
            "Complete journey fixture output must be fixture.json in the artifact directory"
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(output_path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"), sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


def main() -> None:
    _require_child_environment()
    _configure_reused_seed_guard()
    from scripts import seed_optimization_journeys_e2e as optimization_seed

    database_url = os.environ.get("DATABASE_URL", "")
    optimization_seed.require_optimization_journeys_e2e_database(database_url)
    snapshot_only = sys.argv[1:] == ["--snapshot"]
    output_path = Path(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == "--output" else None
    if sys.argv[1:] and not snapshot_only and output_path is None:
        raise SystemExit("Usage: seed_complete_journeys_e2e.py [--snapshot | --output PATH]")

    conn = None
    try:
        conn = get_db()
        try:
            optimization_seed.verify_optimization_journeys_e2e_database_marker(conn)
            if not snapshot_only:
                optimization_seed.seed(conn)
                conn.execute(
                    "DELETE FROM gardens WHERE slug = %s",
                    (optimization_seed.DELETE_TARGET_SLUG,),
                )
                garden_rows = conn.execute(
                    "SELECT id FROM gardens WHERE slug = ANY(%s) ORDER BY id",
                    ([optimization_seed.GARDEN_A_SLUG, optimization_seed.GARDEN_B_SLUG],),
                ).fetchall()
                _add_role_fixtures(conn, garden_ids=[int(row["id"]) for row in garden_rows])
                conn.commit()
            result = _snapshot(conn, optimization_seed)
            if output_path is not None:
                _write_json_exclusive(output_path, result)
            else:
                print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        except Exception:
            conn.rollback()
            raise
    finally:
        if conn is not None:
            return_db(conn)
        close_pool()


if __name__ == "__main__":
    main()
