#!/usr/bin/env python3
"""Seed and inspect the disposable database used by optimization journey E2E."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from urllib.parse import urlsplit

from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from gardenops.db import close_pool, executemany, get_db, return_db
from gardenops.security import generate_passkey_user_handle, hash_password

_DISPOSABLE_DATABASE_NAME = "gardenops_test"
_DISPOSABLE_MARKER_SETTING = "gardenops.disposable_marker"

SUITE_NAME = "optimization-journeys-e2e"
ADMIN_USERNAME = os.environ.get(
    "GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_USERNAME",
    "optimization_journeys_e2e_admin",
)
ADMIN_PASSWORD = os.environ.get(
    "GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_PASSWORD",
    "OptimizationJourneysE2E!Passphrase2026",
)

GARDEN_A_SLUG = "optimization-journeys-a"
GARDEN_B_SLUG = "optimization-journeys-b"
DELETE_TARGET_SLUG = "e2e-optimization-delete-target"
GARDEN_A_NAME = "Optimization Garden A"
GARDEN_B_NAME = "Optimization Garden B"
DELETE_TARGET_NAME = "E2E Optimization Delete Target"

RESTORE_SNAPSHOT_ID = "snap_optimization_journeys_restore"
OFFLINE_REPLAY_TITLE = "Optimization Offline Journal Replay"
GARDEN_A_NOTIFICATION = "Optimization Garden A notice"
GARDEN_B_NOTIFICATION = "Optimization Garden B notice"
GARDEN_A_WEATHER_ALERT = "Frost warning: -1°C expected"
GARDEN_B_WEATHER_ALERT = "Frost warning: -9°C expected"

_GARDEN_SPECS = (
    {
        "key": "a",
        "slug": GARDEN_A_SLUG,
        "name": GARDEN_A_NAME,
        "plot_id": "OPT-JOURNEY-A-PLOT",
        "plot_label": "Optimization A Restored Plot",
        "plant_id": "OPT-JOURNEY-A-PLANT",
        "plant_name": "Optimization A Plant",
        "task_id": "tsk_optimization_journeys_a",
        "journal_id": "jrn_optimization_journeys_a",
        "object_id": "obj_optimization_journeys_a",
        "object_name": "Optimization A Map Object",
    },
    {
        "key": "b",
        "slug": GARDEN_B_SLUG,
        "name": GARDEN_B_NAME,
        "plot_id": "OPT-JOURNEY-B-PLOT",
        "plot_label": "Optimization B Plot",
        "plant_id": "OPT-JOURNEY-B-PLANT",
        "plant_name": "Optimization B Plant",
        "task_id": "tsk_optimization_journeys_b",
        "journal_id": "jrn_optimization_journeys_b",
        "object_id": "obj_optimization_journeys_b",
        "object_name": "Optimization B Map Object",
    },
    {
        "key": "delete_target",
        "slug": DELETE_TARGET_SLUG,
        "name": DELETE_TARGET_NAME,
        "plot_id": "OPT-JOURNEY-DELETE-PLOT",
        "plot_label": "E2E Delete Target Plot",
        "plant_id": "OPT-JOURNEY-DELETE-PLANT",
        "plant_name": "E2E Delete Target Plant",
        "task_id": "tsk_optimization_journeys_delete_target",
        "journal_id": "jrn_optimization_journeys_delete_target",
        "object_id": "obj_optimization_journeys_delete_target",
        "object_name": "E2E Delete Target Map Object",
    },
)


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def require_optimization_journeys_e2e_database(database_url: str) -> None:
    """Reject every database except the exact temporary runner database."""
    if os.environ.get("APP_ENV", "").strip().lower() != "test":
        raise RuntimeError("Optimization journey E2E seeding requires APP_ENV=test")
    if os.environ.get("AUTH_REQUIRED", "").strip().lower() != "true":
        raise RuntimeError("Optimization journey E2E seeding requires AUTH_REQUIRED=true")
    if os.environ.get("AUTH_MODE", "").strip().lower() != "session":
        raise RuntimeError("Optimization journey E2E seeding requires AUTH_MODE=session")
    if os.environ.get("GARDENOPS_ALLOW_DESTRUCTIVE_E2E", "").strip() != "1":
        raise RuntimeError(
            "Optimization journey E2E seeding requires GARDENOPS_ALLOW_DESTRUCTIVE_E2E=1"
        )
    if os.environ.get("GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_ALLOW_TRUNCATE", "").strip() != "1":
        raise RuntimeError(
            "Optimization journey E2E seeding requires "
            "GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_ALLOW_TRUNCATE=1"
        )

    expected_url = os.environ.get("GARDENOPS_DISPOSABLE_POSTGRES_URL", "")
    if not expected_url:
        raise RuntimeError(
            "Optimization journey E2E seeding requires a runner-issued disposable database URL"
        )
    if database_url != expected_url:
        raise RuntimeError(
            "Optimization journey E2E DATABASE_URL must exactly match the runner-issued URL"
        )

    marker = os.environ.get("GARDENOPS_DISPOSABLE_POSTGRES_MARKER", "").strip()
    system_identifier = os.environ.get(
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER", ""
    ).strip()
    if not marker or not system_identifier:
        raise RuntimeError(
            "Optimization journey E2E seeding requires a runner-issued disposable marker"
        )
    if not system_identifier.isdecimal():
        raise RuntimeError("Optimization journey E2E runner system identifier must be numeric")
    if not marker.startswith(f"{system_identifier}."):
        raise RuntimeError(
            "Optimization journey E2E disposable marker is not bound to the runner cluster"
        )

    try:
        conninfo = conninfo_to_dict(database_url)
        parsed = urlsplit(database_url)
        parsed_port = parsed.port
    except Exception as exc:  # pragma: no cover - parser behavior is third-party
        raise RuntimeError("Optimization journey E2E database URL is invalid") from exc
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.query or parsed.fragment:
        raise RuntimeError("Optimization journey E2E database URL must be an exact TCP URL")
    if parsed.hostname != "127.0.0.1":
        raise RuntimeError("Optimization journey E2E database URL must use disposable TCP loopback")
    if parsed.path != f"/{_DISPOSABLE_DATABASE_NAME}":
        raise RuntimeError(
            "Optimization journey E2E database URL must use the disposable test database"
        )
    if parsed_port is None or parsed_port == 5432:
        raise RuntimeError(
            "Optimization journey E2E database URL must not use conventional port 5432"
        )
    if not 1 <= parsed_port <= 65535:
        raise RuntimeError("Optimization journey E2E database URL has an invalid disposable port")

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
            "Optimization journey E2E database URL must resolve only to disposable TCP loopback"
        )


def verify_optimization_journeys_e2e_database_marker(conn) -> None:
    expected_marker = os.environ.get("GARDENOPS_DISPOSABLE_POSTGRES_MARKER", "").strip()
    expected_system_identifier = os.environ.get(
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER", ""
    ).strip()
    row = conn.execute(
        "SELECT current_setting(%s, true) AS disposable_marker",
        (_DISPOSABLE_MARKER_SETTING,),
    ).fetchone()
    actual_marker = str(row["disposable_marker"] or "") if row else ""
    if not expected_marker or actual_marker != expected_marker:
        raise RuntimeError(
            "Optimization journey E2E database marker does not match the runner-issued marker"
        )
    identifier_row = conn.execute("SELECT system_identifier FROM pg_control_system()").fetchone()
    actual_system_identifier = (
        str(identifier_row["system_identifier"] or "") if identifier_row else ""
    )
    if not expected_system_identifier or actual_system_identifier != expected_system_identifier:
        raise RuntimeError(
            "Optimization journey E2E database system identifier does not match the runner"
        )


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
    quoted_names = ", ".join(f'public."{name}"' for name in table_names)
    conn.execute(f"TRUNCATE TABLE {quoted_names} RESTART IDENTITY CASCADE")


def _insert_admin(conn) -> int:
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
            ADMIN_USERNAME,
            hash_password(ADMIN_PASSWORD),
            generate_passkey_user_handle(),
        ),
    ).fetchone()
    if not row:
        raise RuntimeError("Failed to create optimization journey E2E administrator")
    return int(row["id"])


def _insert_garden(conn, *, spec: dict[str, str], admin_id: int) -> int:
    row = conn.execute(
        """
        INSERT INTO gardens (
            slug, name, grid_rows, grid_cols, latitude, longitude, address,
            onboarding_complete, owner_user_id
        )
        VALUES (%s, %s, 12, 16, 59.9139, 10.7522, 'Disposable E2E fixture', 1, %s)
        RETURNING id
        """,
        (spec["slug"], spec["name"], admin_id),
    ).fetchone()
    if not row:
        raise RuntimeError(f"Failed to create fixture garden {spec['slug']}")
    garden_id = int(row["id"])
    conn.execute(
        """
        INSERT INTO garden_memberships (garden_id, user_id, role)
        VALUES (%s, %s, 'admin')
        """,
        (garden_id, admin_id),
    )
    return garden_id


def _seed_layout(conn, *, garden_id: int) -> None:
    conn.execute(
        """
        INSERT INTO layout_state (
            garden_id, house_row, house_col, house_width, house_height,
            north_degrees, grid_rows, grid_cols
        )
        VALUES (%s, 2, 2, 4, 3, 18, 12, 16)
        """,
        (garden_id,),
    )


def _seed_plot_and_plant(
    conn,
    *,
    garden_id: int,
    admin_id: int,
    spec: dict[str, str],
) -> None:
    conn.execute(
        """
        INSERT INTO plots (
            plot_id, garden_id, zone_code, zone_name, plot_number,
            grid_row, grid_col, sub_zone, notes, color
        )
        VALUES (%s, %s, 'A', %s, 1, 7, 3, %s, 'Disposable E2E plot', '#6fa66f')
        """,
        (spec["plot_id"], garden_id, spec["plot_label"], spec["plot_label"]),
    )
    conn.execute(
        """
        INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
        VALUES (%s, %s, %s)
        """,
        (spec["plot_id"], admin_id, garden_id),
    )
    conn.execute(
        """
        INSERT INTO plants (
            plt_id, name, latin, category, bloom_month, color, hardiness,
            height_cm, light, link, care_watering, care_soil, care_planting,
            care_maintenance, care_notes, year_planted, seen_growing, seen_growing_date
        )
        VALUES (
            %s, %s, 'Solanum lycopersicum', 'vegetable', 'July', '#7aa65d', 'H5',
            90, 'full sun', '', 'water deeply', 'well-drained soil',
            'plant after frost', 'stake early', 'fixture plant', '2026', 1, '2026-07-01'
        )
        """,
        (spec["plant_id"], spec["plant_name"]),
    )
    conn.execute(
        """
        INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
        VALUES (%s, %s, %s)
        """,
        (spec["plant_id"], admin_id, garden_id),
    )
    conn.execute(
        """
        INSERT INTO plot_plants
            (plot_id, plt_id, quantity, seen_growing, seen_growing_date, room_label)
        VALUES (%s, %s, 2, 1, '2026-07-01', '')
        """,
        (spec["plot_id"], spec["plant_id"]),
    )


def _seed_map_object(conn, *, garden_id: int, admin_id: int, spec: dict[str, str]) -> None:
    now_ms = _now_ms()
    conn.execute(
        """
        INSERT INTO garden_map_objects (
            public_id, garden_id, object_type, name, shape_type, geometry_json,
            style_json, z_index, has_internal_layout, internal_layout_json,
            created_by_user_id, created_at_ms, updated_at_ms
        )
        VALUES (%s, %s, 'greenhouse', %s, 'rectangle', %s, %s, 2, 1, %s, %s, %s, %s)
        """,
        (
            spec["object_id"],
            garden_id,
            spec["object_name"],
            json.dumps({"x": 9, "y": 2, "width": 4, "height": 3}, sort_keys=True),
            json.dumps({"color": "#8aa7a0"}, sort_keys=True),
            json.dumps({"cols": 5, "rows": 4}, sort_keys=True),
            admin_id,
            now_ms,
            now_ms,
        ),
    )


def _seed_task(conn, *, garden_id: int, admin_id: int, spec: dict[str, str]) -> None:
    now_ms = _now_ms()
    row = conn.execute(
        """
        INSERT INTO garden_tasks (
            public_id, garden_id, task_type, title, description, status, severity,
            due_on, snoozed_until, rule_source, metadata_json, created_by_user_id,
            completed_by_user_id, completed_at_ms, created_at_ms, updated_at_ms,
            window_start_on, window_end_on, window_kind
        )
        VALUES (
            %s, %s, 'water', %s, 'Representative task for the disposable E2E garden.',
            'pending', 'normal', '2026-07-10', NULL, 'manual:optimization-e2e', %s,
            %s, NULL, NULL, %s, %s, '2026-07-01', '2026-07-20', 'recommended'
        )
        RETURNING id
        """,
        (
            spec["task_id"],
            garden_id,
            f"Water {spec['plant_name']}",
            json.dumps({"fixture": SUITE_NAME}, sort_keys=True),
            admin_id,
            now_ms,
            now_ms,
        ),
    ).fetchone()
    if not row:
        raise RuntimeError(f"Failed to create fixture task for {spec['slug']}")
    task_id = int(row["id"])
    executemany(
        conn,
        "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
        [(task_id, spec["plant_id"])],
    )
    executemany(
        conn,
        "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, %s)",
        [(task_id, spec["plot_id"])],
    )


def _seed_journal(conn, *, garden_id: int, admin_id: int, spec: dict[str, str]) -> None:
    now_ms = _now_ms()
    row = conn.execute(
        """
        INSERT INTO garden_journal_entries (
            public_id, garden_id, event_type, occurred_on, title, notes,
            metadata_json, actor_user_id, created_at_ms, updated_at_ms
        )
        VALUES (%s, %s, 'observed', '2026-07-09', %s, 'Representative seeded journal entry.',
                %s, %s, %s, %s)
        RETURNING id
        """,
        (
            spec["journal_id"],
            garden_id,
            f"Observation for {spec['plant_name']}",
            json.dumps({"fixture": SUITE_NAME}, sort_keys=True),
            admin_id,
            now_ms,
            now_ms,
        ),
    ).fetchone()
    if not row:
        raise RuntimeError(f"Failed to create fixture journal entry for {spec['slug']}")
    journal_id = int(row["id"])
    executemany(
        conn,
        "INSERT INTO garden_journal_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
        [(journal_id, spec["plant_id"])],
    )
    executemany(
        conn,
        "INSERT INTO garden_journal_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
        [(journal_id, spec["plot_id"])],
    )


def _seed_notification(conn, *, garden_id: int, admin_id: int, spec: dict[str, str]) -> None:
    title = GARDEN_A_NOTIFICATION if spec["key"] == "a" else GARDEN_B_NOTIFICATION
    if spec["key"] == "delete_target":
        title = "Disposable delete target notice"
    conn.execute(
        """
        INSERT INTO notification_events (
            public_id, garden_id, user_id, notification_type, notification_subtype,
            severity, title, body, target_type, target_id, metadata_json,
            dismissed, created_at_ms
        )
        VALUES (%s, %s, %s, 'system', 'optimization_fixture', 'normal', %s,
                'Garden-scoped notification fixture.', 'garden', %s, '{}', 0, %s)
        """,
        (
            f"note_optimization_{spec['key']}",
            garden_id,
            admin_id,
            title,
            str(garden_id),
            _now_ms(),
        ),
    )


def _seed_weather_alert(conn, *, garden_id: int, spec: dict[str, str]) -> None:
    if spec["key"] not in {"a", "b"}:
        return
    title = GARDEN_A_WEATHER_ALERT if spec["key"] == "a" else GARDEN_B_WEATHER_ALERT
    coldest = -1 if spec["key"] == "a" else -9
    row = conn.execute(
        """
        INSERT INTO weather_alerts (
            garden_id, alert_type, severity, title, description, valid_from,
            valid_until, metadata_json, created_at_ms
        )
        VALUES (%s, 'frost_warning', 'normal', %s, 'Garden-scoped weather fixture.',
                '2026-07-10', '2026-07-12', %s, %s)
        RETURNING id
        """,
        (
            garden_id,
            title,
            json.dumps(
                {
                    "coldest": coldest,
                    "coldest_date": "2026-07-11",
                    "frost_days": [["2026-07-11", coldest]],
                },
                sort_keys=True,
            ),
            _now_ms(),
        ),
    ).fetchone()
    if not row:
        raise RuntimeError(f"Failed to seed weather alert for {spec['slug']}")
    conn.execute(
        "INSERT INTO weather_alert_plants (alert_id, plt_id) VALUES (%s, %s)",
        (int(row["id"]), spec["plant_id"]),
    )


def _seed_delete_target_extra_state(
    conn,
    *,
    garden_id: int,
    admin_id: int,
    spec: dict[str, str],
) -> None:
    now_ms = _now_ms()
    issue = conn.execute(
        """
        INSERT INTO garden_issues (
            public_id, garden_id, issue_type, title, description, severity, status,
            metadata_json, created_by_user_id, created_at_ms, updated_at_ms
        )
        VALUES ('iss_optimization_delete', %s, 'pest', 'Disposable issue', '',
                'normal', 'open', '{}', %s, %s, %s)
        RETURNING id
        """,
        (garden_id, admin_id, now_ms, now_ms),
    ).fetchone()
    harvest = conn.execute(
        """
        INSERT INTO harvest_entries (
            public_id, garden_id, occurred_on, quantity, unit, quality, notes,
            metadata_json, actor_user_id, created_at_ms, updated_at_ms
        )
        VALUES ('hrv_optimization_delete', %s, '2026-07-09', 1, 'pieces',
                'good', '', '{}', %s, %s, %s)
        RETURNING id
        """,
        (garden_id, admin_id, now_ms, now_ms),
    ).fetchone()
    calendar = conn.execute(
        """
        INSERT INTO garden_calendar_events (
            public_id, garden_id, title, description, event_on,
            created_by_user_id, updated_by_user_id, created_at_ms, updated_at_ms
        )
        VALUES ('cal_optimization_delete', %s, 'Disposable event', '', '2026-07-12',
                %s, %s, %s, %s)
        RETURNING id
        """,
        (garden_id, admin_id, admin_id, now_ms, now_ms),
    ).fetchone()
    alert = conn.execute(
        """
        INSERT INTO weather_alerts (
            garden_id, alert_type, severity, title, description, valid_from,
            valid_until, metadata_json, created_at_ms
        )
        VALUES (%s, 'rain_surplus', 'normal', 'Disposable rain', '',
                '2026-07-10', '2026-07-12', '{}', %s)
        RETURNING id
        """,
        (garden_id, now_ms),
    ).fetchone()
    if not issue or not harvest or not calendar or not alert:
        raise RuntimeError("Failed to seed disposable garden related state")
    conn.execute(
        "INSERT INTO garden_issue_plants (issue_id, plt_id) VALUES (%s, %s)",
        (int(issue["id"]), spec["plant_id"]),
    )
    conn.execute(
        "INSERT INTO garden_issue_plots (issue_id, plot_id) VALUES (%s, %s)",
        (int(issue["id"]), spec["plot_id"]),
    )
    conn.execute(
        "INSERT INTO harvest_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
        (int(harvest["id"]), spec["plant_id"]),
    )
    conn.execute(
        "INSERT INTO harvest_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
        (int(harvest["id"]), spec["plot_id"]),
    )
    conn.execute(
        "INSERT INTO garden_calendar_event_plants (event_id, plt_id) VALUES (%s, %s)",
        (int(calendar["id"]), spec["plant_id"]),
    )
    conn.execute(
        "INSERT INTO garden_calendar_event_plots (event_id, plot_id) VALUES (%s, %s)",
        (int(calendar["id"]), spec["plot_id"]),
    )
    conn.execute(
        "INSERT INTO weather_alert_plants (alert_id, plt_id) VALUES (%s, %s)",
        (int(alert["id"]), spec["plant_id"]),
    )
    conn.execute(
        """
        INSERT INTO media_assets (
            asset_id, garden_id, storage_key, preview_storage_key, original_filename,
            mime_type, bytes, width, height, created_at_ms, actor_user_id
        )
        VALUES ('media_optimization_delete', %s,
                'original/optimization/delete.png', 'preview/optimization/delete.png',
                'delete.png', 'image/png', 4, 1, 1, %s, %s)
        """,
        (garden_id, now_ms, admin_id),
    )
    conn.execute(
        """
        INSERT INTO media_links (asset_id, target_type, target_id, sort_order)
        VALUES ('media_optimization_delete', 'journal_entry', %s, 0)
        """,
        (spec["journal_id"],),
    )
    conn.execute(
        """
        INSERT INTO offline_create_operations (
            garden_id, endpoint, operation_id, request_fingerprint,
            target_type, target_id, result_id, created_at_ms, expires_at_ms
        )
        VALUES (%s, 'journal', 'optimization-delete-operation', %s,
                'journal_entry', %s, %s, %s, %s)
        """,
        (
            garden_id,
            "0" * 64,
            spec["journal_id"],
            spec["journal_id"],
            now_ms,
            now_ms + 30 * 86_400_000,
        ),
    )


def _seed_snapshot(conn, *, garden_id: int, spec: dict[str, str]) -> None:
    # Use the production serializer so the UI restore journey exercises the real format.
    from gardenops.main import snapshot_layout

    snapshot_id = RESTORE_SNAPSHOT_ID if spec["key"] == "a" else f"snap_{spec['key']}_journeys"
    snapshot_name = (
        "Optimization restore layout" if spec["key"] == "a" else f"{spec['name']} layout"
    )
    conn.execute(
        """
        INSERT INTO layout_snapshots (public_id, name, data, garden_id)
        VALUES (%s, %s, %s, %s)
        """,
        (snapshot_id, snapshot_name, snapshot_layout(conn, garden_id), garden_id),
    )


def seed(conn) -> None:
    truncate_public_tables(conn)
    admin_id = _insert_admin(conn)
    for spec in _GARDEN_SPECS:
        garden_id = _insert_garden(conn, spec=spec, admin_id=admin_id)
        _seed_layout(conn, garden_id=garden_id)
        _seed_plot_and_plant(conn, garden_id=garden_id, admin_id=admin_id, spec=spec)
        _seed_map_object(conn, garden_id=garden_id, admin_id=admin_id, spec=spec)
        _seed_task(conn, garden_id=garden_id, admin_id=admin_id, spec=spec)
        _seed_journal(conn, garden_id=garden_id, admin_id=admin_id, spec=spec)
        _seed_notification(conn, garden_id=garden_id, admin_id=admin_id, spec=spec)
        _seed_weather_alert(conn, garden_id=garden_id, spec=spec)
        if spec["key"] == "delete_target":
            _seed_delete_target_extra_state(
                conn,
                garden_id=garden_id,
                admin_id=admin_id,
                spec=spec,
            )
        _seed_snapshot(conn, garden_id=garden_id, spec=spec)


def _safe_snapshot_target_id() -> int | None:
    raw = os.environ.get("GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_SNAPSHOT_TARGET_GARDEN_ID", "")
    if not raw:
        return None
    if not raw.isdigit() or int(raw) <= 0:
        raise RuntimeError("Optimization journey E2E snapshot target garden id is invalid")
    return int(raw)


def _garden_row(conn, slug: str):
    return conn.execute(
        "SELECT id, name FROM gardens WHERE slug = %s LIMIT 1",
        (slug,),
    ).fetchone()


def _garden_counts(conn, garden_id: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    garden_tables = conn.execute(
        """
        SELECT table_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND column_name = 'garden_id'
          AND table_name <> 'audit_events'
        ORDER BY table_name
        """
    ).fetchall()
    for table_row in garden_tables:
        table = str(table_row["table_name"])
        row = conn.execute(
            sql.SQL("SELECT COUNT(*) AS count FROM {} WHERE garden_id = %s").format(
                sql.Identifier(table)
            ),
            (garden_id,),
        ).fetchone()
        counts[table] = int(row["count"] if row else 0)
    related_specs = (
        ("plots", "plots", "plot_ownership", "plot_id", "plot_id"),
        ("plot_plants", "plot_plants", "plot_ownership", "plot_id", "plot_id"),
        ("garden_task_plants", "garden_task_plants", "garden_tasks", "task_id", "id"),
        ("garden_task_plots", "garden_task_plots", "garden_tasks", "task_id", "id"),
        ("garden_issue_plants", "garden_issue_plants", "garden_issues", "issue_id", "id"),
        ("garden_issue_plots", "garden_issue_plots", "garden_issues", "issue_id", "id"),
        (
            "garden_journal_entry_plants",
            "garden_journal_entry_plants",
            "garden_journal_entries",
            "entry_id",
            "id",
        ),
        (
            "garden_journal_entry_plots",
            "garden_journal_entry_plots",
            "garden_journal_entries",
            "entry_id",
            "id",
        ),
        ("harvest_entry_plants", "harvest_entry_plants", "harvest_entries", "entry_id", "id"),
        ("harvest_entry_plots", "harvest_entry_plots", "harvest_entries", "entry_id", "id"),
        (
            "garden_calendar_event_plants",
            "garden_calendar_event_plants",
            "garden_calendar_events",
            "event_id",
            "id",
        ),
        (
            "garden_calendar_event_plots",
            "garden_calendar_event_plots",
            "garden_calendar_events",
            "event_id",
            "id",
        ),
        ("weather_alert_plants", "weather_alert_plants", "weather_alerts", "alert_id", "id"),
        ("media_links", "media_links", "media_assets", "asset_id", "asset_id"),
        (
            "garden_map_object_units_via_parent",
            "garden_map_object_units",
            "garden_map_objects",
            "map_object_id",
            "id",
        ),
    )
    for key, child_table, parent_table, child_key, parent_key in related_specs:
        statement = sql.SQL(
            """
            SELECT COUNT(*) AS count
            FROM {} child
            JOIN {} parent ON parent.{} = child.{}
            WHERE parent.garden_id = %s
            """
        ).format(
            sql.Identifier(child_table),
            sql.Identifier(parent_table),
            sql.Identifier(parent_key),
            sql.Identifier(child_key),
        )
        row = conn.execute(statement, (garden_id,)).fetchone()
        counts[key] = int(row["count"] if row else 0)
    return counts


def _garden_evidence(conn, *, slug: str, fallback_id: int | None = None) -> dict[str, object]:
    row = _garden_row(conn, slug)
    garden_id = int(row["id"]) if row else fallback_id
    return {
        "counts": _garden_counts(conn, garden_id) if garden_id is not None else {},
        "exists": row is not None,
        "id": garden_id,
        "name": str(row["name"]) if row else None,
    }


def snapshot(conn) -> dict[str, object]:
    target_id = _safe_snapshot_target_id()
    gardens = {
        "a": _garden_evidence(conn, slug=GARDEN_A_SLUG),
        "b": _garden_evidence(conn, slug=GARDEN_B_SLUG),
        "delete_target": _garden_evidence(
            conn,
            slug=DELETE_TARGET_SLUG,
            fallback_id=target_id,
        ),
    }
    garden_a_id = gardens["a"]["id"]
    offline_journal_count = 0
    offline_operation_counts: dict[str, int] = {}
    offline_media_asset_count = 0
    task_status = None
    if isinstance(garden_a_id, int):
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM garden_journal_entries
            WHERE garden_id = %s AND title = %s
            """,
            (garden_a_id, OFFLINE_REPLAY_TITLE),
        ).fetchone()
        offline_journal_count = int(row["count"] if row else 0)
        operation_rows = conn.execute(
            """
            SELECT endpoint, COUNT(*) AS count
            FROM offline_create_operations
            WHERE garden_id = %s
            GROUP BY endpoint
            """,
            (garden_a_id,),
        ).fetchall()
        offline_operation_counts = {
            str(operation_row["endpoint"]): int(operation_row["count"])
            for operation_row in operation_rows
        }
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM media_assets WHERE garden_id = %s",
            (garden_a_id,),
        ).fetchone()
        offline_media_asset_count = int(row["count"] if row else 0)
        row = conn.execute(
            "SELECT status FROM garden_tasks WHERE garden_id = %s AND public_id = %s",
            (garden_a_id, _GARDEN_SPECS[0]["task_id"]),
        ).fetchone()
        task_status = str(row["status"]) if row else None

    audit_count = 0
    session_audit_count = 0
    if target_id is None:
        existing_target_id = gardens["delete_target"]["id"]
        target_id = existing_target_id if isinstance(existing_target_id, int) else None
    if target_id is not None:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS count,
                COUNT(*) FILTER (
                    WHERE actor_username = %s AND actor_auth_type = 'session'
                ) AS session_count
            FROM audit_events
            WHERE method = 'DELETE'
              AND status_code = 200
              AND path = %s
              AND detail LIKE %s
            """,
            (
                ADMIN_USERNAME,
                f"/api/gardens/{target_id}",
                f'%"slug":"{DELETE_TARGET_SLUG}"%',
            ),
        ).fetchone()
        audit_count = int(row["count"] if row else 0)
        session_audit_count = int(row["session_count"] if row else 0)

    row = conn.execute("SELECT COUNT(*) AS count FROM provider_daily_usage").fetchone()
    provider_usage_rows = int(row["count"] if row else 0)
    result = {
        "admin": {"username": ADMIN_USERNAME},
        "audit": {
            "delete_target_count": audit_count,
            "delete_target_session_count": session_audit_count,
        },
        "gardens": gardens,
        "journal": {
            "offline_replay_entry_count": offline_journal_count,
        },
        "offline": {
            "media_asset_count": offline_media_asset_count,
            "operation_counts": offline_operation_counts,
            "task_status": task_status,
        },
        "provider_usage_rows": provider_usage_rows,
        "suite": SUITE_NAME,
    }
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return result


def main() -> None:
    require_optimization_journeys_e2e_database(os.environ.get("DATABASE_URL", ""))
    conn = None
    try:
        conn = get_db()
        try:
            verify_optimization_journeys_e2e_database_marker(conn)
            if len(sys.argv) == 2 and sys.argv[1] == "snapshot":
                snapshot(conn)
                return
            if len(sys.argv) != 1:
                raise SystemExit("Usage: seed_optimization_journeys_e2e.py [snapshot]")
            seed(conn)
            conn.commit()
            snapshot(conn)
        except Exception:
            conn.rollback()
            raise
    finally:
        if conn is not None:
            return_db(conn)
        close_pool()


if __name__ == "__main__":
    main()
