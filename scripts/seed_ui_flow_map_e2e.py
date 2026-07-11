#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlsplit

from psycopg.conninfo import conninfo_to_dict

from gardenops.db import close_pool, executemany, get_db, return_db
from gardenops.security import generate_passkey_user_handle, hash_password
from gardenops.services.attention.outcomes import upsert_attention_outcome

_DISPOSABLE_MARKER_SETTING = "gardenops.disposable_marker"
_DISPOSABLE_DATABASE_NAME = "gardenops_test"

E2E_GARDEN_SLUG = "ui-flow-map-e2e"
E2E_GARDEN_NAME = "UI Flow Map E2E"
E2E_ADMIN_USERNAME = os.environ.get(
    "GARDENOPS_UI_FLOW_E2E_USERNAME",
    "ui_flow_map_e2e_admin",
)
E2E_ADMIN_PASSWORD = os.environ.get(
    "GARDENOPS_UI_FLOW_E2E_PASSWORD",
    "UiFlowMapE2EAdmin!Passphrase2026",
)
E2E_EDITOR_USERNAME = os.environ.get(
    "GARDENOPS_UI_FLOW_E2E_EDITOR_USERNAME",
    "ui_flow_map_e2e_editor",
)
E2E_EDITOR_PASSWORD = os.environ.get(
    "GARDENOPS_UI_FLOW_E2E_EDITOR_PASSWORD",
    "UiFlowMapE2EEditor!Passphrase2026",
)
E2E_VIEWER_USERNAME = os.environ.get(
    "GARDENOPS_UI_FLOW_E2E_VIEWER_USERNAME",
    "ui_flow_map_e2e_viewer",
)
E2E_VIEWER_PASSWORD = os.environ.get(
    "GARDENOPS_UI_FLOW_E2E_VIEWER_PASSWORD",
    "UiFlowMapE2EViewer!Passphrase2026",
)
E2E_OUTDOOR_PLOT_ID = "UI-FLOW-OUTDOOR"
E2E_INDOOR_PLOT_ID = "UI-FLOW-INDOOR"
E2E_PLANT_IDS = ("UI-FLOW-TOMATO", "UI-FLOW-BASIL", "UI-FLOW-LETTUCE")
E2E_TASK_IDS = (
    "tsk_ui_flow_map_open",
    "tsk_ui_flow_map_completed",
    "tsk_ui_flow_map_snoozed",
)
E2E_JOURNAL_PUBLIC_ID = "jrn_ui_flow_map_observation"
E2E_ISSUE_PUBLIC_ID = "iss_ui_flow_map_aphids"
E2E_HARVEST_PUBLIC_ID = "hrv_ui_flow_map_tomatoes"
E2E_CALENDAR_PUBLIC_ID = "calevt_ui_flow_map_workshop"
E2E_MAP_OBJECT_PUBLIC_ID = "obj_ui_flow_map_greenhouse"
E2E_MAP_UNIT_PUBLIC_ID = "unit_ui_flow_map_bench"
E2E_LAYOUT_SNAPSHOT_PUBLIC_ID = "snap_ui_flow_map_initial"
E2E_NOTIFICATION_PUBLIC_ID = "note_ui_flow_map_weather"
E2E_ATTENTION_OUTCOME_PUBLIC_ID = "attnout_ui_flow_map_rain"
E2E_INVENTORY_PUBLIC_ID = "inv_ui_flow_map_tomato_seed"
E2E_PROCUREMENT_PUBLIC_ID = "prc_ui_flow_map_compost"


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"UI-flow map E2E requires {name}")
    return value


def _fixture_date_from_environment() -> date:
    raw = os.environ.get("GARDENOPS_UI_FLOW_E2E_DATE", "").strip()
    if not raw:
        return date.today()
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise RuntimeError("GARDENOPS_UI_FLOW_E2E_DATE must use YYYY-MM-DD") from exc


E2E_DATE = _fixture_date_from_environment()
SEEDED_NOW_MS = int(datetime.combine(E2E_DATE, time(hour=12), tzinfo=UTC).timestamp() * 1000)


def _fixture_date(days: int = 0) -> str:
    return (E2E_DATE + timedelta(days=days)).isoformat()


def _require_fixture_attention_clock() -> None:
    frozen_date = os.environ.get("GARDENOPS_ATTENTION_FROZEN_DATE", "").strip()
    frozen_now_ms = os.environ.get("GARDENOPS_ATTENTION_FROZEN_NOW_MS", "").strip()
    if frozen_date != _fixture_date() or frozen_now_ms != str(SEEDED_NOW_MS):
        raise RuntimeError("UI-flow map E2E attention clock must match GARDENOPS_UI_FLOW_E2E_DATE")


def require_ui_flow_map_e2e_database(database_url: str) -> None:
    if os.environ.get("GARDENOPS_UI_FLOW_MAP_E2E_CHILD", "") != "1":
        raise RuntimeError("UI-flow map E2E must run as the disposable runner child")
    if os.environ.get("APP_ENV", "").strip().lower() != "test":
        raise RuntimeError("UI-flow map E2E seeding requires APP_ENV=test")
    if os.environ.get("AUTH_REQUIRED", "").strip().lower() != "true":
        raise RuntimeError("UI-flow map E2E seeding requires AUTH_REQUIRED=true")
    if os.environ.get("AUTH_MODE", "").strip().lower() != "session":
        raise RuntimeError("UI-flow map E2E seeding requires AUTH_MODE=session")
    if os.environ.get("GARDENOPS_NOTIFICATION_SCHEDULER_ENABLED", "").strip().lower() != "false":
        raise RuntimeError(
            "UI-flow map E2E seeding requires the notification scheduler to be disabled"
        )
    if os.environ.get("GARDENOPS_UI_FLOW_E2E_ALLOW_TRUNCATE", "").strip() != "1":
        raise RuntimeError(
            "UI-flow map E2E seeding requires GARDENOPS_UI_FLOW_E2E_ALLOW_TRUNCATE=1"
        )

    expected_url = _required_env("GARDENOPS_DISPOSABLE_POSTGRES_URL")
    if database_url != expected_url:
        raise RuntimeError("UI-flow map E2E DATABASE_URL must exactly match the runner-issued URL")

    marker = _required_env("GARDENOPS_DISPOSABLE_POSTGRES_MARKER")
    system_identifier = _required_env("GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER")
    if not system_identifier.isdecimal():
        raise RuntimeError("UI-flow map E2E runner system identifier must be numeric")
    if not marker.startswith(f"{system_identifier}."):
        raise RuntimeError("UI-flow map E2E disposable marker is not bound to the runner cluster")

    try:
        conninfo = conninfo_to_dict(database_url)
        parsed = urlsplit(database_url)
        parsed_port = parsed.port
    except Exception as exc:
        raise RuntimeError("UI-flow map E2E database URL is invalid") from exc
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.query or parsed.fragment:
        raise RuntimeError("UI-flow map E2E database URL must be an exact TCP URL")
    if parsed.hostname != "127.0.0.1":
        raise RuntimeError("UI-flow map E2E database URL must use disposable TCP loopback")
    if parsed.path != f"/{_DISPOSABLE_DATABASE_NAME}":
        raise RuntimeError("UI-flow map E2E database URL must use the disposable test database")
    if parsed_port is None or parsed_port == 5432:
        raise RuntimeError("UI-flow map E2E database URL must not use port 5432")
    if not 1 <= parsed_port <= 65535:
        raise RuntimeError("UI-flow map E2E database URL has an invalid disposable port")
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
            "UI-flow map E2E database URL must resolve only to disposable TCP loopback"
        )


def verify_ui_flow_map_e2e_database_marker(conn) -> None:
    expected_marker = _required_env("GARDENOPS_DISPOSABLE_POSTGRES_MARKER")
    expected_system_identifier = _required_env(
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER"
    )
    marker_row = conn.execute(
        "SELECT current_setting(%s, true) AS disposable_marker",
        (_DISPOSABLE_MARKER_SETTING,),
    ).fetchone()
    actual_marker = str(marker_row["disposable_marker"] or "") if marker_row else ""
    if actual_marker != expected_marker:
        raise RuntimeError(
            "UI-flow map E2E database marker does not match the runner-issued marker"
        )
    system_row = conn.execute(
        "SELECT system_identifier FROM pg_control_system()",
    ).fetchone()
    actual_system_identifier = (
        str(system_row["system_identifier"] or "") if system_row else ""
    )
    if actual_system_identifier != expected_system_identifier:
        raise RuntimeError(
            "UI-flow map E2E database system identifier does not match the runner"
        )


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


def _insert_fixture_user(conn, *, username: str, role: str, password: str) -> int:
    row = conn.execute(
        """
        INSERT INTO auth_users (
            username, password_hash, password_auth_disabled, passkey_user_handle,
            role, is_active, must_change_password, subscription_tier
        )
        VALUES (%s, %s, 0, %s, %s, 1, 0, %s)
        RETURNING id
        """,
        (
            username,
            hash_password(password),
            generate_passkey_user_handle(),
            role,
            "pro" if role == "admin" else "home",
        ),
    ).fetchone()
    if not row:
        raise RuntimeError(f"Failed to create fixture user {username}")
    return int(row["id"])


def ensure_fixture_users(conn) -> dict[str, int]:
    users = {
        "admin": _insert_fixture_user(
            conn,
            username=E2E_ADMIN_USERNAME,
            role="admin",
            password=E2E_ADMIN_PASSWORD,  # push-sanitizer: allow SECRET_ASSIGNMENT
        ),
        "editor": _insert_fixture_user(
            conn,
            username=E2E_EDITOR_USERNAME,
            role="editor",
            password=E2E_EDITOR_PASSWORD,  # push-sanitizer: allow SECRET_ASSIGNMENT
        ),
        "viewer": _insert_fixture_user(
            conn,
            username=E2E_VIEWER_USERNAME,
            role="viewer",
            password=E2E_VIEWER_PASSWORD,  # push-sanitizer: allow SECRET_ASSIGNMENT
        ),
    }
    conn.execute("DELETE FROM gardens WHERE slug = 'default'")
    return users


def ensure_garden(conn, *, owner_user_id: int, editor_user_id: int, viewer_user_id: int) -> int:
    row = conn.execute(
        """
        INSERT INTO gardens (
            slug, name, grid_rows, grid_cols, latitude, longitude, address,
            onboarding_complete, owner_user_id
        )
        VALUES (%s, %s, 12, 16, 59.9139, 10.7522, 'UI Flow Test Garden', 1, %s)
        ON CONFLICT (slug) DO UPDATE SET
            name = excluded.name,
            grid_rows = excluded.grid_rows,
            grid_cols = excluded.grid_cols,
            latitude = excluded.latitude,
            longitude = excluded.longitude,
            address = excluded.address,
            onboarding_complete = excluded.onboarding_complete,
            owner_user_id = excluded.owner_user_id
        RETURNING id
        """,
        (E2E_GARDEN_SLUG, E2E_GARDEN_NAME, owner_user_id),
    ).fetchone()
    if not row:
        raise RuntimeError("Failed to create UI-flow map E2E garden")
    garden_id = int(row["id"])
    executemany(
        conn,
        """
        INSERT INTO garden_memberships (garden_id, user_id, role)
        VALUES (%s, %s, %s)
        ON CONFLICT (garden_id, user_id) DO UPDATE SET role = excluded.role
        """,
        [
            (garden_id, owner_user_id, "admin"),
            (garden_id, editor_user_id, "editor"),
            (garden_id, viewer_user_id, "viewer"),
        ],
    )
    return garden_id


def seed_layout(conn, garden_id: int) -> None:
    conn.execute(
        """
        INSERT INTO layout_state (
            garden_id, house_row, house_col, house_width, house_height,
            north_degrees, grid_rows, grid_cols
        )
        VALUES (%s, 2, 2, 4, 3, 12, 12, 16)
        ON CONFLICT (garden_id) DO UPDATE SET
            house_row = excluded.house_row,
            house_col = excluded.house_col,
            house_width = excluded.house_width,
            house_height = excluded.house_height,
            north_degrees = excluded.north_degrees,
            grid_rows = excluded.grid_rows,
            grid_cols = excluded.grid_cols
        """,
        (garden_id,),
    )
    conn.execute(
        """
        INSERT INTO layout_snapshots (public_id, name, data, garden_id)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (public_id) DO UPDATE SET
            name = excluded.name, data = excluded.data, garden_id = excluded.garden_id
        """,
        (
            E2E_LAYOUT_SNAPSHOT_PUBLIC_ID,
            "Initial UI flow layout",
            json.dumps(
                {
                    "grid_rows": 12,
                    "grid_cols": 16,
                    "house": {"row": 2, "col": 2, "width": 4, "height": 3},
                    "plots": [E2E_OUTDOOR_PLOT_ID, E2E_INDOOR_PLOT_ID],
                },
                sort_keys=True,
            ),
            garden_id,
        ),
    )


def seed_plots_and_plants(conn, *, garden_id: int, owner_user_id: int) -> None:
    plots = [
        (
            E2E_OUTDOOR_PLOT_ID,
            garden_id,
            "A",
            "Outdoor beds",
            1,
            7,
            2,
            "South",
            "Full sun and drip line",
            "#6fa66f",
        ),
        (
            E2E_INDOOR_PLOT_ID,
            garden_id,
            "I",
            "Indoor growing",
            1,
            None,
            None,
            "Greenhouse",
            "Warm shelf for seedlings",
            "#6f91a6",
        ),
    ]
    executemany(
        conn,
        """
        INSERT INTO plots (
            plot_id, garden_id, zone_code, zone_name, plot_number,
            grid_row, grid_col, sub_zone, notes, color
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (plot_id) DO UPDATE SET
            garden_id = excluded.garden_id, zone_code = excluded.zone_code,
            zone_name = excluded.zone_name, plot_number = excluded.plot_number,
            grid_row = excluded.grid_row, grid_col = excluded.grid_col,
            sub_zone = excluded.sub_zone, notes = excluded.notes, color = excluded.color
        """,
        plots,
    )
    executemany(
        conn,
        """
        INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (plot_id) DO UPDATE SET
            owner_user_id = excluded.owner_user_id, garden_id = excluded.garden_id
        """,
        [(plot_id, owner_user_id, garden_id) for plot_id, *_ in plots],
    )

    plants = [
        (
            "UI-FLOW-TOMATO",
            "Balcony Tomato",
            "vegetable",
            "Solanum lycopersicum",
            "full sun",
            "water deeply twice weekly",
            "rich, well-drained soil",
            "plant out after frost",
            "Stake and inspect twice weekly",
            "Stake early; feed when fruit sets",
            E2E_OUTDOOR_PLOT_ID,
            3,
            int(_fixture_date(-51)[:4]),
            _fixture_date(-51),
            "",
        ),
        (
            "UI-FLOW-BASIL",
            "Genovese Basil",
            "herb",
            "Ocimum basilicum",
            "bright light",
            "keep evenly moist",
            "light potting mix",
            "pinch after six leaves",
            "Pinch tips and rotate the tray",
            "Harvest often to delay flowering",
            E2E_INDOOR_PLOT_ID,
            4,
            int(_fixture_date(-38)[:4]),
            _fixture_date(-38),
            "Greenhouse shelf",
        ),
        (
            "UI-FLOW-LETTUCE",
            "Butterhead Lettuce",
            "vegetable",
            "Lactuca sativa",
            "morning sun",
            "water lightly each morning",
            "cool, compost-rich soil",
            "succession sow every 14 days",
            "Remove outer leaves as needed",
            "Shade during hot afternoons",
            E2E_OUTDOOR_PLOT_ID,
            8,
            int(_fixture_date(-22)[:4]),
            _fixture_date(-22),
            "",
        ),
    ]
    executemany(
        conn,
        """
        INSERT INTO plants (
            plt_id, name, latin, category, bloom_month, color, hardiness,
            height_cm, light, link, care_watering, care_soil, care_planting,
            care_maintenance, care_notes, year_planted, seen_growing, seen_growing_date
        )
        VALUES (%s, %s, %s, %s, 'July', %s, 'H5', NULL, %s, '', %s, %s, %s, %s, %s, %s, 1, %s)
        ON CONFLICT (plt_id) DO UPDATE SET
            name = excluded.name, latin = excluded.latin, category = excluded.category,
            color = excluded.color, light = excluded.light, care_watering = excluded.care_watering,
            care_soil = excluded.care_soil, care_planting = excluded.care_planting,
            care_maintenance = excluded.care_maintenance, care_notes = excluded.care_notes,
            year_planted = excluded.year_planted, seen_growing = excluded.seen_growing,
            seen_growing_date = excluded.seen_growing_date
        """,
        [
            (
                plant_id,
                name,
                latin,
                category,
                "#7aa65d",
                light,
                watering,
                soil,
                planting,
                maintenance,
                notes,
                year,
                seen_date,
            )
            for (
                plant_id,
                name,
                category,
                latin,
                light,
                watering,
                soil,
                planting,
                maintenance,
                notes,
                plot_id,
                quantity,
                year,
                seen_date,
                room_label,
            ) in plants
        ],
    )
    executemany(
        conn,
        """
        INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (plt_id, garden_id) DO UPDATE SET owner_user_id = excluded.owner_user_id
        """,
        [(plant_id, owner_user_id, garden_id) for plant_id, *_ in plants],
    )
    executemany(
        conn,
        """
        INSERT INTO plot_plants
            (plot_id, plt_id, quantity, seen_growing, seen_growing_date, room_label)
        VALUES (%s, %s, %s, 1, %s, %s)
        ON CONFLICT (plot_id, plt_id) DO UPDATE SET
            quantity = excluded.quantity, seen_growing = excluded.seen_growing,
            seen_growing_date = excluded.seen_growing_date, room_label = excluded.room_label
        """,
        [
            (plot_id, plant_id, quantity, seen_date, room_label)
            for (
                plant_id,
                _,
                _,
                _,
                _,
                _,
                _,
                _,
                _,
                _,
                plot_id,
                quantity,
                _,
                seen_date,
                room_label,
            ) in plants
        ],
    )


def seed_map_object(conn, *, garden_id: int, owner_user_id: int) -> None:
    row = conn.execute(
        """
        INSERT INTO garden_map_objects (
            public_id, garden_id, object_type, name, shape_type, geometry_json,
            style_json, z_index, has_internal_layout, internal_layout_json,
            created_by_user_id, created_at_ms, updated_at_ms
        )
        VALUES (
            %s, %s, 'greenhouse', 'Seedling greenhouse', 'rectangle', %s, %s,
            2, 1, %s, %s, %s, %s
        )
        ON CONFLICT (public_id) DO UPDATE SET
            garden_id = excluded.garden_id, name = excluded.name,
            geometry_json = excluded.geometry_json, style_json = excluded.style_json,
            has_internal_layout = excluded.has_internal_layout,
            internal_layout_json = excluded.internal_layout_json,
            created_by_user_id = excluded.created_by_user_id, updated_at_ms = excluded.updated_at_ms
        RETURNING id
        """,
        (
            E2E_MAP_OBJECT_PUBLIC_ID,
            garden_id,
            json.dumps({"x": 8, "y": 2, "width": 5, "height": 4}, sort_keys=True),
            json.dumps({"color": "#8aa7a0"}, sort_keys=True),
            json.dumps({"rows": 4, "cols": 5}, sort_keys=True),
            owner_user_id,
            SEEDED_NOW_MS,
            SEEDED_NOW_MS,
        ),
    ).fetchone()
    if not row:
        raise RuntimeError("Failed to create UI-flow map object")
    conn.execute(
        """
        INSERT INTO garden_map_object_units (
            public_id, garden_id, map_object_id, unit_type, name, shape_type,
            geometry_json, style_json, sort_order, created_at_ms, updated_at_ms
        )
        VALUES (%s, %s, %s, 'raised_bed', 'Basil bench', 'rectangle', %s, %s, 1, %s, %s)
        ON CONFLICT (public_id) DO UPDATE SET
            garden_id = excluded.garden_id, map_object_id = excluded.map_object_id,
            name = excluded.name, geometry_json = excluded.geometry_json,
            style_json = excluded.style_json, updated_at_ms = excluded.updated_at_ms
        """,
        (
            E2E_MAP_UNIT_PUBLIC_ID,
            garden_id,
            int(row["id"]),
            json.dumps({"x": 2, "y": 1, "width": 3, "height": 1}, sort_keys=True),
            json.dumps({"color": "#b7c98a"}, sort_keys=True),
            SEEDED_NOW_MS,
            SEEDED_NOW_MS,
        ),
    )


def set_task_links(conn, task_id: int, plant_ids: list[str], plot_ids: list[str]) -> None:
    conn.execute("DELETE FROM garden_task_plants WHERE task_id = %s", (task_id,))
    conn.execute("DELETE FROM garden_task_plots WHERE task_id = %s", (task_id,))
    executemany(
        conn,
        "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
        [(task_id, plant_id) for plant_id in plant_ids],
    )
    executemany(
        conn,
        "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, %s)",
        [(task_id, plot_id) for plot_id in plot_ids],
    )


def seed_tasks(conn, *, garden_id: int, owner_user_id: int) -> None:
    task_rows = [
        (
            E2E_TASK_IDS[0],
            "water",
            "Water tomato bed",
            "Check the outdoor bed before watering deeply.",
            "pending",
            "high",
            _fixture_date(),
            None,
            "manual:ui-flow-map",
            None,
            None,
            ["UI-FLOW-TOMATO"],
            [E2E_OUTDOOR_PLOT_ID],
        ),
        (
            E2E_TASK_IDS[1],
            "harvest",
            "Harvest ripe tomatoes",
            "Collect the first ripe fruit and record the yield.",
            "completed",
            "normal",
            _fixture_date(-2),
            None,
            "manual:ui-flow-map",
            owner_user_id,
            SEEDED_NOW_MS - 172800000,
            ["UI-FLOW-TOMATO"],
            [E2E_OUTDOOR_PLOT_ID],
        ),
        (
            E2E_TASK_IDS[2],
            "fertilize",
            "Feed greenhouse basil",
            "Use the dilute liquid feed on the next greenhouse visit.",
            "snoozed",
            "normal",
            _fixture_date(-1),
            _fixture_date(),
            "manual:ui-flow-map",
            None,
            None,
            ["UI-FLOW-BASIL"],
            [E2E_INDOOR_PLOT_ID],
        ),
    ]
    for (
        public_id,
        task_type,
        title,
        description,
        status,
        severity,
        due_on,
        snoozed_until,
        rule_source,
        completed_by,
        completed_at,
        plant_ids,
        plot_ids,
    ) in task_rows:
        row = conn.execute(
            """
            INSERT INTO garden_tasks (
                public_id, garden_id, task_type, title, description, status, severity,
                due_on, snoozed_until, rule_source, metadata_json, created_by_user_id,
                completed_by_user_id, completed_at_ms, created_at_ms, updated_at_ms,
                window_start_on, window_end_on, window_kind
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, 'recommended'
            )
            ON CONFLICT (public_id) DO UPDATE SET
                garden_id = excluded.garden_id, task_type = excluded.task_type,
                title = excluded.title, description = excluded.description,
                status = excluded.status, severity = excluded.severity,
                due_on = excluded.due_on, snoozed_until = excluded.snoozed_until,
                rule_source = excluded.rule_source, metadata_json = excluded.metadata_json,
                created_by_user_id = excluded.created_by_user_id,
                completed_by_user_id = excluded.completed_by_user_id,
                completed_at_ms = excluded.completed_at_ms, updated_at_ms = excluded.updated_at_ms,
                window_start_on = excluded.window_start_on, window_end_on = excluded.window_end_on,
                window_kind = excluded.window_kind
            RETURNING id
            """,
            (
                public_id,
                garden_id,
                task_type,
                title,
                description,
                status,
                severity,
                due_on,
                snoozed_until,
                rule_source,
                json.dumps({"fixture": "ui_flow_map", "status_reason": "seeded"}, sort_keys=True),
                owner_user_id,
                completed_by,
                completed_at,
                SEEDED_NOW_MS,
                SEEDED_NOW_MS,
                _fixture_date(-3),
                _fixture_date(4),
            ),
        ).fetchone()
        if not row:
            raise RuntimeError(f"Failed to create task {public_id}")
        set_task_links(conn, int(row["id"]), plant_ids, plot_ids)


def seed_journal_issue_harvest(conn, *, garden_id: int, owner_user_id: int) -> None:
    journal = conn.execute(
        """
        INSERT INTO garden_journal_entries (
            public_id, garden_id, event_type, occurred_on, title, notes,
            metadata_json, actor_user_id, created_at_ms, updated_at_ms
        )
        VALUES (%s, %s, 'observed', %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (public_id) DO UPDATE SET
            garden_id = excluded.garden_id, event_type = excluded.event_type,
            occurred_on = excluded.occurred_on, title = excluded.title,
            notes = excluded.notes, metadata_json = excluded.metadata_json,
            actor_user_id = excluded.actor_user_id, updated_at_ms = excluded.updated_at_ms
        RETURNING id
        """,
        (
            E2E_JOURNAL_PUBLIC_ID,
            garden_id,
            _fixture_date(-1),
            "Tomato truss observation",
            "First flowers have set fruit; leaves are healthy after the rain.",
            json.dumps({"observation": "fruit_set", "weather": "rain"}, sort_keys=True),
            owner_user_id,
            SEEDED_NOW_MS - 86400000,
            SEEDED_NOW_MS - 86400000,
        ),
    ).fetchone()
    if not journal:
        raise RuntimeError("Failed to create journal observation")
    conn.execute(
        "DELETE FROM garden_journal_entry_plants WHERE entry_id = %s", (int(journal["id"]),)
    )
    conn.execute(
        "DELETE FROM garden_journal_entry_plots WHERE entry_id = %s", (int(journal["id"]),)
    )
    executemany(
        conn,
        "INSERT INTO garden_journal_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
        [(int(journal["id"]), "UI-FLOW-TOMATO")],
    )
    executemany(
        conn,
        "INSERT INTO garden_journal_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
        [(int(journal["id"]), E2E_OUTDOOR_PLOT_ID)],
    )

    issue = conn.execute(
        """
        INSERT INTO garden_issues (
            public_id, garden_id, issue_type, title, description, severity, status,
            suspected_cause, treatment_plan, follow_up_on, metadata_json,
            created_by_user_id, created_at_ms, updated_at_ms
        )
        VALUES (%s, %s, 'pest', 'Aphids on tomato tips', %s, 'normal', 'monitoring',
                'Recent tender growth', %s, %s, %s, %s, %s, %s)
        ON CONFLICT (public_id) DO UPDATE SET
            garden_id = excluded.garden_id, title = excluded.title,
            description = excluded.description, severity = excluded.severity,
            status = excluded.status, suspected_cause = excluded.suspected_cause,
            treatment_plan = excluded.treatment_plan, follow_up_on = excluded.follow_up_on,
                metadata_json = excluded.metadata_json,
                created_by_user_id = excluded.created_by_user_id,
            updated_at_ms = excluded.updated_at_ms
        RETURNING id
        """,
        (
            E2E_ISSUE_PUBLIC_ID,
            garden_id,
            "Small cluster found beneath the newest leaves.",
            "Rinse leaves, release beneficial insects, and inspect again in three days.",
            _fixture_date(3),
            json.dumps({"follow_up_status": "scheduled", "fixture": "ui_flow_map"}, sort_keys=True),
            owner_user_id,
            SEEDED_NOW_MS - 259200000,
            SEEDED_NOW_MS,
        ),
    ).fetchone()
    if not issue:
        raise RuntimeError("Failed to create issue")
    conn.execute("DELETE FROM garden_issue_plants WHERE issue_id = %s", (int(issue["id"]),))
    conn.execute("DELETE FROM garden_issue_plots WHERE issue_id = %s", (int(issue["id"]),))
    executemany(
        conn,
        "INSERT INTO garden_issue_plants (issue_id, plt_id) VALUES (%s, %s)",
        [(int(issue["id"]), "UI-FLOW-TOMATO")],
    )
    executemany(
        conn,
        "INSERT INTO garden_issue_plots (issue_id, plot_id) VALUES (%s, %s)",
        [(int(issue["id"]), E2E_OUTDOOR_PLOT_ID)],
    )

    harvest = conn.execute(
        """
        INSERT INTO harvest_entries (
            public_id, garden_id, occurred_on, quantity, unit, quality, notes,
            metadata_json, actor_user_id, created_at_ms, updated_at_ms
        )
        VALUES (%s, %s, %s, 0.65, 'kg', 'excellent', %s, %s, %s, %s, %s)
        ON CONFLICT (public_id) DO UPDATE SET
            garden_id = excluded.garden_id, occurred_on = excluded.occurred_on,
            quantity = excluded.quantity, unit = excluded.unit, quality = excluded.quality,
            notes = excluded.notes, metadata_json = excluded.metadata_json,
            actor_user_id = excluded.actor_user_id, updated_at_ms = excluded.updated_at_ms
        RETURNING id
        """,
        (
            E2E_HARVEST_PUBLIC_ID,
            garden_id,
            _fixture_date(),
            "First tomato harvest of the season.",
            json.dumps({"batch": "first-ripe", "fixture": "ui_flow_map"}, sort_keys=True),
            owner_user_id,
            SEEDED_NOW_MS,
            SEEDED_NOW_MS,
        ),
    ).fetchone()
    if not harvest:
        raise RuntimeError("Failed to create harvest")
    conn.execute("DELETE FROM harvest_entry_plants WHERE entry_id = %s", (int(harvest["id"]),))
    conn.execute("DELETE FROM harvest_entry_plots WHERE entry_id = %s", (int(harvest["id"]),))
    executemany(
        conn,
        "INSERT INTO harvest_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
        [(int(harvest["id"]), "UI-FLOW-TOMATO")],
    )
    executemany(
        conn,
        "INSERT INTO harvest_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
        [(int(harvest["id"]), E2E_OUTDOOR_PLOT_ID)],
    )


def seed_calendar_inventory_procurement(conn, *, garden_id: int, owner_user_id: int) -> None:
    calendar = conn.execute(
        """
        INSERT INTO garden_calendar_events (
            public_id, garden_id, title, description, event_on,
            created_by_user_id, updated_by_user_id, created_at_ms, updated_at_ms
        )
        VALUES (%s, %s, 'Greenhouse check-in', %s, %s, %s, %s, %s, %s)
        ON CONFLICT (public_id) DO UPDATE SET
            garden_id = excluded.garden_id, title = excluded.title,
            description = excluded.description, event_on = excluded.event_on,
            updated_by_user_id = excluded.updated_by_user_id, updated_at_ms = excluded.updated_at_ms
        RETURNING id
        """,
        (
            E2E_CALENDAR_PUBLIC_ID,
            garden_id,
            "Inspect basil, refill trays, and review the aphid follow-up.",
            _fixture_date(2),
            owner_user_id,
            owner_user_id,
            SEEDED_NOW_MS,
            SEEDED_NOW_MS,
        ),
    ).fetchone()
    if not calendar:
        raise RuntimeError("Failed to create calendar event")
    calendar_id = int(calendar["id"])
    conn.execute("DELETE FROM garden_calendar_event_plants WHERE event_id = %s", (calendar_id,))
    conn.execute("DELETE FROM garden_calendar_event_plots WHERE event_id = %s", (calendar_id,))
    executemany(
        conn,
        "INSERT INTO garden_calendar_event_plants (event_id, plt_id) VALUES (%s, %s)",
        [(calendar_id, "UI-FLOW-BASIL")],
    )
    executemany(
        conn,
        "INSERT INTO garden_calendar_event_plots (event_id, plot_id) VALUES (%s, %s)",
        [(calendar_id, E2E_INDOOR_PLOT_ID)],
    )

    item = conn.execute(
        """
        INSERT INTO inventory_items (
            public_id, garden_id, plt_id, label, inventory_type, unit, created_at_ms
        )
        VALUES (%s, %s, 'UI-FLOW-TOMATO', 'Tomato seed packet', 'seed', 'packets', %s)
        ON CONFLICT (public_id) DO UPDATE SET
            garden_id = excluded.garden_id, plt_id = excluded.plt_id,
            label = excluded.label, inventory_type = excluded.inventory_type,
            unit = excluded.unit, created_at_ms = excluded.created_at_ms
        RETURNING id
        """,
        (E2E_INVENTORY_PUBLIC_ID, garden_id, SEEDED_NOW_MS),
    ).fetchone()
    if not item:
        raise RuntimeError("Failed to create inventory item")
    item_id = int(item["id"])
    conn.execute(
        """
        INSERT INTO inventory_transactions (
            item_id, delta, reason, source_name, cost_minor, occurred_on,
            storage_location, notes, actor_user_id, created_at_ms
        )
        VALUES (%s, 2, 'purchased', 'Nordic Seeds', 8900, %s, %s, %s, %s, %s)
        """,
        (
            item_id,
            _fixture_date(),
            "Seed cabinet",
            "Two packets received for the fall crop.",
            owner_user_id,
            SEEDED_NOW_MS,
        ),
    )
    conn.execute(
        """
        INSERT INTO procurement_items (
            public_id, garden_id, label, inventory_type, linked_plt_id, linked_plot_id,
            vendor_name, vendor_url, status, cost_minor, currency, quantity, unit,
            expected_on, notes, metadata_json, created_by_user_id, created_at_ms, updated_at_ms
        )
        VALUES (%s, %s, 'Compost refill', 'other', 'UI-FLOW-TOMATO', %s, 'Oslo Soil Co',
                'https://example.invalid/compost', 'wanted', 24900, 'NOK', 2, 'bags',
                %s, %s, %s, %s, %s, %s)
        ON CONFLICT (public_id) DO UPDATE SET
            garden_id = excluded.garden_id, label = excluded.label,
            inventory_type = excluded.inventory_type, linked_plt_id = excluded.linked_plt_id,
            linked_plot_id = excluded.linked_plot_id, vendor_name = excluded.vendor_name,
            vendor_url = excluded.vendor_url, status = excluded.status,
            cost_minor = excluded.cost_minor, quantity = excluded.quantity,
            unit = excluded.unit, expected_on = excluded.expected_on, notes = excluded.notes,
            metadata_json = excluded.metadata_json,
            created_by_user_id = excluded.created_by_user_id,
            updated_at_ms = excluded.updated_at_ms
        """,
        (
            E2E_PROCUREMENT_PUBLIC_ID,
            garden_id,
            E2E_OUTDOOR_PLOT_ID,
            _fixture_date(5),
            "Add compost before the next tomato feeding.",
            json.dumps({"fixture": "ui_flow_map", "priority": "medium"}, sort_keys=True),
            owner_user_id,
            SEEDED_NOW_MS,
            SEEDED_NOW_MS,
        ),
    )


def seed_notifications_weather_attention(conn, *, garden_id: int, owner_user_id: int) -> None:
    weather = conn.execute(
        """
        INSERT INTO weather_alerts (
            garden_id, alert_type, severity, title, description, valid_from,
            valid_until, metadata_json, dismissed, created_at_ms
        )
        VALUES (%s, 'rain_surplus', 'normal', 'Heavy rain expected', %s,
                %s, %s, %s, 0, %s)
        RETURNING id
        """,
        (
            garden_id,
            "Skip outdoor watering and check the greenhouse ventilation.",
            _fixture_date(),
            _fixture_date(1),
            json.dumps(
                {
                    "plant_advice": ["UI-FLOW-TOMATO"],
                    "rain_days": 2,
                    "rain_mm": 18,
                    "source": "fixture",
                    "total_mm": 18,
                },
                sort_keys=True,
            ),
            SEEDED_NOW_MS,
        ),
    ).fetchone()
    if not weather:
        raise RuntimeError("Failed to create weather alert")
    conn.execute(
        "INSERT INTO weather_alert_plants (alert_id, plt_id) VALUES (%s, 'UI-FLOW-TOMATO')",
        (int(weather["id"]),),
    )
    conn.execute(
        """
        INSERT INTO notification_events (
            public_id, garden_id, user_id, notification_type, notification_subtype,
            severity, title, body, target_type, target_id, metadata_json,
            dismissed, created_at_ms, expires_at_ms
        )
        VALUES (%s, %s, %s, 'weather_alert', 'rain_surplus', 'normal', %s, %s,
                'weather_alert', %s, %s, 0, %s, %s)
        ON CONFLICT (public_id) DO UPDATE SET
            garden_id = excluded.garden_id, user_id = excluded.user_id,
            title = excluded.title, body = excluded.body, target_id = excluded.target_id,
            metadata_json = excluded.metadata_json, dismissed = excluded.dismissed,
            created_at_ms = excluded.created_at_ms, expires_at_ms = excluded.expires_at_ms,
            cleared_at_ms = NULL, clear_reason = NULL
        """,
        (
            E2E_NOTIFICATION_PUBLIC_ID,
            garden_id,
            owner_user_id,
            "Heavy rain may cover watering",
            "Outdoor tomato watering is covered by the forecast through tomorrow.",
            int(weather["id"]),
            json.dumps(
                {"alert_id": int(weather["id"]), "plant_ids": ["UI-FLOW-TOMATO"]}, sort_keys=True
            ),
            SEEDED_NOW_MS,
            SEEDED_NOW_MS + 86400000,
        ),
    )
    upsert_attention_outcome(
        conn,
        garden_id=garden_id,
        provider="weather",
        outcome_type="watering_covered_by_rain",
        source_type="weather_alert",
        source_id=str(weather["id"]),
        source_public_id=E2E_NOTIFICATION_PUBLIC_ID,
        target_type="task",
        target_id=E2E_TASK_IDS[0],
        title="Watering covered by rain",
        explanation="Rain is expected for the outdoor tomato bed, so watering can wait.",
        reason="rain_forecast",
        plant_ids=("UI-FLOW-TOMATO",),
        plot_ids=(E2E_OUTDOOR_PLOT_ID,),
        metadata={"fixture": "ui_flow_map", "rain_mm": 18},
        recovery_action={"action": "restore_task", "task_public_id": E2E_TASK_IDS[0]},
        occurred_at_ms=SEEDED_NOW_MS,
        expires_at_ms=SEEDED_NOW_MS + 86400000,
    )
    conn.execute(
        """
        INSERT INTO user_notification_preferences (
            user_id, in_app_enabled, email_enabled, email_address, digest_frequency,
            quiet_hours_json, task_due_enabled, task_overdue_enabled, rules_json,
            created_at_ms, updated_at_ms
        )
        VALUES (%s, 1, 0, '', 'daily', %s, 1, 1, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            in_app_enabled = excluded.in_app_enabled, quiet_hours_json = excluded.quiet_hours_json,
            rules_json = excluded.rules_json, updated_at_ms = excluded.updated_at_ms
        """,
        (
            owner_user_id,
            json.dumps({"start": "22:00", "end": "07:00"}, sort_keys=True),
            json.dumps({"weather_alert": "in_app", "task_due": "in_app"}, sort_keys=True),
            SEEDED_NOW_MS,
            SEEDED_NOW_MS,
        ),
    )


def seed_saved_view_and_attention_state(conn, *, garden_id: int, owner_user_id: int) -> None:
    conn.execute(
        """
        INSERT INTO user_saved_views (
            user_id, garden_id, view_type, label, filter_json, is_preset,
            sort_order, created_at_ms, updated_at_ms
        )
        VALUES (%s, %s, 'tasks', 'Today and snoozed', %s, 0, 1, %s, %s)
        """,
        (
            owner_user_id,
            garden_id,
            json.dumps(
                {
                    "status": ["pending", "snoozed"],
                    "include_snoozed": True,
                    "date": _fixture_date(),
                },
                sort_keys=True,
            ),
            SEEDED_NOW_MS,
            SEEDED_NOW_MS,
        ),
    )
    conn.execute(
        """
        INSERT INTO user_attention_preferences (
            user_id, preset, rules_json, quiet_hours_json, show_no_action_history,
            metadata_json, created_at_ms, updated_at_ms
        )
        VALUES (%s, 'focused', %s, %s, 1, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            preset = excluded.preset, rules_json = excluded.rules_json,
            quiet_hours_json = excluded.quiet_hours_json,
            show_no_action_history = excluded.show_no_action_history,
            metadata_json = excluded.metadata_json, updated_at_ms = excluded.updated_at_ms
        """,
        (
            owner_user_id,
            json.dumps({"weather": {"enabled": True}, "task": {"enabled": True}}, sort_keys=True),
            json.dumps({"start": "22:00", "end": "07:00"}, sort_keys=True),
            json.dumps(
                {"fixture": "ui_flow_map", "last_reviewed": _fixture_date()}, sort_keys=True
            ),
            SEEDED_NOW_MS,
            SEEDED_NOW_MS,
        ),
    )
    conn.execute(
        """
        INSERT INTO user_attention_item_state (
            user_id, garden_id, item_id, user_state, snoozed_until_ms, reason,
            metadata_json, created_at_ms, updated_at_ms
        )
        VALUES (%s, %s, %s, 'snoozed', %s, 'Waiting for the next greenhouse visit', %s, %s, %s)
        ON CONFLICT (user_id, garden_id, item_id) DO UPDATE SET
            user_state = excluded.user_state, snoozed_until_ms = excluded.snoozed_until_ms,
            reason = excluded.reason, metadata_json = excluded.metadata_json,
            updated_at_ms = excluded.updated_at_ms
        """,
        (
            owner_user_id,
            garden_id,
            f"attn:task:{E2E_TASK_IDS[2]}",
            SEEDED_NOW_MS,
            json.dumps({"fixture": "ui_flow_map", "source": "task"}, sort_keys=True),
            SEEDED_NOW_MS,
            SEEDED_NOW_MS,
        ),
    )


def _fixture_garden_id(conn) -> int | None:
    row = conn.execute(
        "SELECT id FROM gardens WHERE slug = %s LIMIT 1", (E2E_GARDEN_SLUG,)
    ).fetchone()
    return int(row["id"]) if row else None


def snapshot(conn) -> None:
    garden_id = _fixture_garden_id(conn)
    if garden_id is None:
        print(json.dumps({"status": "missing", "garden_slug": E2E_GARDEN_SLUG}, sort_keys=True))
        return
    counts = {"gardens": 1}
    for table in (
        "layout_state",
        "garden_memberships",
        "plots",
        "garden_map_objects",
        "garden_map_object_units",
        "garden_tasks",
        "garden_journal_entries",
        "garden_issues",
        "harvest_entries",
        "garden_calendar_events",
        "inventory_items",
        "procurement_items",
        "notification_events",
        "weather_alerts",
        "user_saved_views",
        "layout_snapshots",
        "user_attention_item_state",
        "attention_outcomes",
    ):
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE garden_id = %s", (garden_id,)
        ).fetchone()
        counts[table] = int(row["c"] if row else 0)
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM plant_ownership WHERE garden_id = %s", (garden_id,)
    ).fetchone()
    counts["plants"] = int(row["c"] if row else 0)
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM plot_plants pp
        JOIN plots p ON p.plot_id = pp.plot_id
        WHERE p.garden_id = %s
        """,
        (garden_id,),
    ).fetchone()
    counts["plot_plants"] = int(row["c"] if row else 0)
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM inventory_transactions it
        JOIN inventory_items ii ON ii.id = it.item_id
        WHERE ii.garden_id = %s
        """,
        (garden_id,),
    ).fetchone()
    counts["inventory_transactions"] = int(row["c"] if row else 0)
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM weather_alert_plants wap
        JOIN weather_alerts wa ON wa.id = wap.alert_id
        WHERE wa.garden_id = %s
        """,
        (garden_id,),
    ).fetchone()
    counts["weather_alert_plants"] = int(row["c"] if row else 0)
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM auth_users
        WHERE username IN (%s, %s, %s)
        """,
        (E2E_ADMIN_USERNAME, E2E_EDITOR_USERNAME, E2E_VIEWER_USERNAME),
    ).fetchone()
    counts["auth_users"] = int(row["c"] if row else 0)
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM user_attention_preferences p
        JOIN garden_memberships m ON m.user_id = p.user_id
        WHERE m.garden_id = %s
        """,
        (garden_id,),
    ).fetchone()
    counts["user_attention_preferences"] = int(row["c"] if row else 0)
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM user_notification_preferences p
        JOIN garden_memberships m ON m.user_id = p.user_id
        WHERE m.garden_id = %s
        """,
        (garden_id,),
    ).fetchone()
    counts["user_notification_preferences"] = int(row["c"] if row else 0)
    task_rows = conn.execute(
        """
        SELECT status, COUNT(*) AS c
        FROM garden_tasks
        WHERE garden_id = %s
        GROUP BY status
        ORDER BY status
        """,
        (garden_id,),
    ).fetchall()
    attention_rows = conn.execute(
        """
        SELECT outcome_type, COUNT(*) AS c
        FROM attention_outcomes
        WHERE garden_id = %s
        GROUP BY outcome_type
        ORDER BY outcome_type
        """,
        (garden_id,),
    ).fetchall()
    membership_rows = conn.execute(
        """
        SELECT role, COUNT(*) AS c
        FROM garden_memberships
        WHERE garden_id = %s
        GROUP BY role
        ORDER BY role
        """,
        (garden_id,),
    ).fetchall()
    print(
        json.dumps(
            {
                "status": "seeded",
                "garden": {"id": garden_id, "slug": E2E_GARDEN_SLUG, "onboarding_complete": True},
                "counts": counts,
                "membership_roles": {str(row["role"]): int(row["c"]) for row in membership_rows},
                "task_status": {str(row["status"]): int(row["c"]) for row in task_rows},
                "attention_outcomes": {
                    str(row["outcome_type"]): int(row["c"]) for row in attention_rows
                },
            },
            sort_keys=True,
        )
    )


def seed(conn) -> None:
    truncate_public_tables(conn)
    users = ensure_fixture_users(conn)
    garden_id = ensure_garden(
        conn,
        owner_user_id=users["admin"],
        editor_user_id=users["editor"],
        viewer_user_id=users["viewer"],
    )
    seed_layout(conn, garden_id)
    # The viewer owns representative garden data so role checks exercise read-only
    # rendering instead of an empty owner-scoped result set.
    seed_plots_and_plants(conn, garden_id=garden_id, owner_user_id=users["viewer"])
    seed_map_object(conn, garden_id=garden_id, owner_user_id=users["admin"])
    seed_tasks(conn, garden_id=garden_id, owner_user_id=users["admin"])
    seed_journal_issue_harvest(conn, garden_id=garden_id, owner_user_id=users["admin"])
    seed_calendar_inventory_procurement(conn, garden_id=garden_id, owner_user_id=users["admin"])
    seed_notifications_weather_attention(conn, garden_id=garden_id, owner_user_id=users["admin"])
    seed_saved_view_and_attention_state(conn, garden_id=garden_id, owner_user_id=users["admin"])


def main() -> None:
    require_ui_flow_map_e2e_database(os.environ.get("DATABASE_URL", ""))
    _require_fixture_attention_clock()
    # Fixture credentials must never trigger the external HIBP password check.
    os.environ["AUTH_PASSWORD_CHECK_HIBP"] = "false"
    conn = None
    try:
        conn = get_db()
        try:
            verify_ui_flow_map_e2e_database_marker(conn)
            if len(sys.argv) == 2 and sys.argv[1] == "snapshot":
                snapshot(conn)
                return
            if len(sys.argv) != 1:
                raise SystemExit("Usage: seed_ui_flow_map_e2e.py [snapshot]")
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
