#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlsplit

from psycopg.conninfo import conninfo_to_dict

from gardenops.db import close_pool, executemany, get_db, return_db, run_migrations
from gardenops.security import create_user

ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}
ALLOWED_HOSTADDRS = {"127.0.0.1", "::1"}
ALLOWED_SOCKET_DIRS = {"/var/run/postgresql"}

E2E_GARDEN_SLUG = "task-history-e2e"
E2E_GARDEN_NAME = "Task History E2E"
E2E_USERNAME = "task_history_e2e_admin"
E2E_PLOT_ID = "A1"
E2E_TASK_IDS = (
    "tsk_e2e_bloom",
    "tsk_e2e_fertilize",
    "tsk_e2e_prune",
)
PLANTS = (
    ("BLOOM-E2E", "Bloom E2E", "flowers"),
    ("FERT-A-E2E", "Fert A E2E", "perennial"),
    ("FERT-B-E2E", "Fert B E2E", "perennial"),
    ("PRUNE-A-E2E", "Prune A E2E", "shrub"),
    ("PRUNE-B-E2E", "Prune B E2E", "shrub"),
)
SEEDED_NOW_MS = 1783180800000


def require_task_history_e2e_database(database_url: str) -> None:
    if os.environ.get("APP_ENV", "").strip().lower() != "test":
        raise RuntimeError("Task-history E2E seeding requires APP_ENV=test")
    if os.environ.get("AUTH_REQUIRED", "").strip().lower() != "false":
        raise RuntimeError("Task-history E2E seeding requires AUTH_REQUIRED=false")
    if os.environ.get("GARDENOPS_TASK_HISTORY_E2E_ALLOW_TRUNCATE", "").strip() != "1":
        raise RuntimeError(
            "Task-history E2E seeding requires GARDENOPS_TASK_HISTORY_E2E_ALLOW_TRUNCATE=1"
        )
    conninfo = conninfo_to_dict(database_url)
    parsed = urlsplit(database_url)
    effective_host = (conninfo.get("host") or parsed.hostname or "").strip()
    effective_hostaddr = (conninfo.get("hostaddr") or "").strip()
    effective_db_name = (conninfo.get("dbname") or "").strip().lower()
    if effective_host.startswith("/"):
        host_allowed = effective_host in ALLOWED_SOCKET_DIRS
    else:
        host_allowed = effective_host in ALLOWED_HOSTS
    if not host_allowed:
        raise RuntimeError("Task-history E2E database URL must use a local disposable database")
    if effective_hostaddr and effective_hostaddr not in ALLOWED_HOSTADDRS:
        raise RuntimeError("Task-history E2E database URL must use a local disposable database")
    db_name = effective_db_name or parsed.path.rsplit("/", 1)[-1].lower()
    if db_name != "gardenops_task_history_e2e_test" and not db_name.startswith(
        "gardenops_task_history_e2e_test_"
    ):
        raise RuntimeError(
            "Task-history E2E database URL must point at a disposable e2e test database"
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
    if not table_names:
        return
    quoted = ", ".join(f'public."{name}"' for name in table_names)
    conn.execute(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")


def ensure_admin_user(conn) -> int:
    row = conn.execute(
        "SELECT id FROM auth_users WHERE username = %s LIMIT 1",
        (E2E_USERNAME,),
    ).fetchone()
    if row:
        return int(row["id"])
    created = create_user(
        conn,
        username=E2E_USERNAME,
        password="TaskHistoryE2E!Passphrase1234567890",  # push-sanitizer: allow SECRET_ASSIGNMENT
        role="admin",
    )
    return int(created["id"])


def ensure_garden(conn, owner_user_id: int) -> int:
    row = conn.execute(
        """
        INSERT INTO gardens (slug, name, grid_rows, grid_cols, onboarding_complete, owner_user_id)
        VALUES (%s, %s, 8, 8, 1, %s)
        ON CONFLICT (slug) DO UPDATE SET
            name = excluded.name,
            grid_rows = excluded.grid_rows,
            grid_cols = excluded.grid_cols,
            onboarding_complete = excluded.onboarding_complete,
            owner_user_id = excluded.owner_user_id
        RETURNING id
        """,
        (E2E_GARDEN_SLUG, E2E_GARDEN_NAME, owner_user_id),
    ).fetchone()
    if not row:
        raise RuntimeError("Failed to create E2E garden")
    garden_id = int(row["id"])
    conn.execute(
        """
        INSERT INTO garden_memberships (garden_id, user_id, role)
        VALUES (%s, %s, 'admin')
        ON CONFLICT (garden_id, user_id) DO UPDATE SET
            role = excluded.role
        """,
        (garden_id, owner_user_id),
    )
    return garden_id


def seed_layout(conn, garden_id: int) -> None:
    conn.execute(
        """
        INSERT INTO layout_state (
            garden_id, house_row, house_col, house_width, house_height,
            north_degrees, grid_rows, grid_cols
        )
        VALUES (%s, 1, 1, 2, 2, 0, 8, 8)
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


def seed_plot(conn, garden_id: int, owner_user_id: int) -> None:
    conn.execute(
        """
        INSERT INTO plots (
            plot_id, garden_id, zone_code, zone_name, plot_number,
            grid_row, grid_col, sub_zone, notes, color
        )
        VALUES (%s, %s, 'A', 'Annual Beds', 1, 1, 1, '', 'Task history E2E plot', '#7fb069')
        ON CONFLICT (plot_id) DO UPDATE SET
            garden_id = excluded.garden_id,
            zone_code = excluded.zone_code,
            zone_name = excluded.zone_name,
            plot_number = excluded.plot_number,
            grid_row = excluded.grid_row,
            grid_col = excluded.grid_col,
            sub_zone = excluded.sub_zone,
            notes = excluded.notes,
            color = excluded.color
        """,
        (E2E_PLOT_ID, garden_id),
    )
    conn.execute(
        """
        INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (plot_id) DO UPDATE SET
            owner_user_id = excluded.owner_user_id,
            garden_id = excluded.garden_id
        """,
        (E2E_PLOT_ID, owner_user_id, garden_id),
    )


def seed_plants(conn, garden_id: int, owner_user_id: int) -> None:
    for plant_id, name, category in PLANTS:
        conn.execute(
            """
            INSERT INTO plants (
                plt_id, name, latin, category, bloom_month, color, hardiness,
                height_cm, light, link, care_watering, care_soil, care_planting,
                care_maintenance, care_notes
            )
            VALUES (%s, %s, '', %s, '', '', '', NULL, '', '', 'steady moisture', '', '', '', '')
            ON CONFLICT (plt_id) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                care_watering = excluded.care_watering
            """,
            (plant_id, name, category),
        )
        conn.execute(
            """
            INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (plt_id, garden_id) DO UPDATE SET
                owner_user_id = excluded.owner_user_id
            """,
            (plant_id, owner_user_id, garden_id),
        )
        conn.execute(
            """
            INSERT INTO plot_plants
                (plot_id, plt_id, quantity, seen_growing, seen_growing_date, room_label)
            VALUES (%s, %s, 1, 1, '2026-07-05', '')
            ON CONFLICT (plot_id, plt_id) DO UPDATE SET
                quantity = excluded.quantity,
                seen_growing = excluded.seen_growing,
                seen_growing_date = excluded.seen_growing_date,
                room_label = excluded.room_label
            """,
            (E2E_PLOT_ID, plant_id),
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


def upsert_task(
    conn,
    *,
    public_id: str,
    garden_id: int,
    owner_user_id: int,
    task_type: str,
    title: str,
    description: str,
    due_on: str,
    plant_ids: list[str],
    plot_ids: list[str],
    severity: str = "normal",
    window_start_on: str | None = None,
    window_end_on: str | None = None,
    window_kind: str | None = None,
) -> None:
    row = conn.execute(
        """
        INSERT INTO garden_tasks (
            public_id, garden_id, task_type, title, description, status,
            severity, due_on, snoozed_until, rule_source, metadata_json, created_by_user_id,
            created_at_ms, updated_at_ms, window_start_on, window_end_on, window_kind
        )
        VALUES (
            %s, %s, %s, %s, %s, 'pending',
            %s, %s, NULL, '', '{}', %s,
            %s, %s, %s, %s, %s
        )
        ON CONFLICT (public_id) DO UPDATE SET
            garden_id = excluded.garden_id,
            task_type = excluded.task_type,
            title = excluded.title,
            description = excluded.description,
            status = excluded.status,
            severity = excluded.severity,
            due_on = excluded.due_on,
            snoozed_until = excluded.snoozed_until,
            rule_source = excluded.rule_source,
            metadata_json = excluded.metadata_json,
            created_by_user_id = excluded.created_by_user_id,
            completed_by_user_id = NULL,
            completed_at_ms = NULL,
            updated_at_ms = excluded.updated_at_ms,
            window_start_on = excluded.window_start_on,
            window_end_on = excluded.window_end_on,
            window_kind = excluded.window_kind
        RETURNING id
        """,
        (
            public_id,
            garden_id,
            task_type,
            title,
            description,
            severity,
            due_on,
            owner_user_id,
            SEEDED_NOW_MS,
            SEEDED_NOW_MS,
            window_start_on,
            window_end_on,
            window_kind,
        ),
    ).fetchone()
    if not row:
        raise RuntimeError(f"Failed to upsert E2E task {public_id}")
    set_task_links(conn, int(row["id"]), plant_ids, plot_ids)


def seed_tasks(conn, garden_id: int, owner_user_id: int) -> None:
    upsert_task(
        conn,
        public_id="tsk_e2e_bloom",
        garden_id=garden_id,
        owner_user_id=owner_user_id,
        task_type="observe_bloom",
        title="Observe bloom: Bloom E2E",
        description="Check whether Bloom E2E started flowering.",
        due_on="2026-07-05",
        plant_ids=["BLOOM-E2E"],
        plot_ids=[E2E_PLOT_ID],
    )
    upsert_task(
        conn,
        public_id="tsk_e2e_fertilize",
        garden_id=garden_id,
        owner_user_id=owner_user_id,
        task_type="fertilize",
        title="Fertilize 2 plants",
        description="Complete fertilizing one plant at a time if needed.",
        due_on="2026-07-05",
        plant_ids=["FERT-A-E2E", "FERT-B-E2E"],
        plot_ids=[E2E_PLOT_ID],
    )
    upsert_task(
        conn,
        public_id="tsk_e2e_prune",
        garden_id=garden_id,
        owner_user_id=owner_user_id,
        task_type="prune",
        title="Prune 2 plants",
        description="Stay within the recommended pruning window.",
        due_on="2026-07-05",
        plant_ids=["PRUNE-A-E2E", "PRUNE-B-E2E"],
        plot_ids=[E2E_PLOT_ID],
        window_start_on="2026-07-05",
        window_end_on="2026-07-06",
        window_kind="recommended",
    )


def _task_plant_map(conn, task_ids: list[int]) -> dict[int, list[str]]:
    if not task_ids:
        return {}
    placeholders = ",".join(["%s"] * len(task_ids))
    rows = conn.execute(
        f"""
        SELECT task_id, plt_id
        FROM garden_task_plants
        WHERE task_id IN ({placeholders})
        ORDER BY task_id, plt_id
        """,
        task_ids,
    ).fetchall()
    plant_map = {task_id: [] for task_id in task_ids}
    for row in rows:
        plant_map[int(row["task_id"])].append(str(row["plt_id"]))
    return plant_map


def _journal_plant_map(conn, entry_ids: list[int]) -> dict[int, list[str]]:
    if not entry_ids:
        return {}
    placeholders = ",".join(["%s"] * len(entry_ids))
    rows = conn.execute(
        f"""
        SELECT entry_id, plt_id
        FROM garden_journal_entry_plants
        WHERE entry_id IN ({placeholders})
        ORDER BY entry_id, plt_id
        """,
        entry_ids,
    ).fetchall()
    plant_map = {entry_id: [] for entry_id in entry_ids}
    for row in rows:
        plant_map[int(row["entry_id"])].append(str(row["plt_id"]))
    return plant_map


def print_snapshot(conn) -> None:
    task_rows = conn.execute(
        """
        SELECT id, public_id, status, title, snoozed_until
        FROM garden_tasks
        WHERE public_id IN (%s, %s, %s)
        ORDER BY public_id
        """,
        E2E_TASK_IDS,
    ).fetchall()
    task_ids = [int(row["id"]) for row in task_rows]
    task_plant_map = _task_plant_map(conn, task_ids)
    tasks = [
        {
            "public_id": str(row["public_id"]),
            "status": str(row["status"]),
            "title": str(row["title"]),
            "plant_ids": task_plant_map.get(int(row["id"]), []),
            "snoozed_until": (
                str(row["snoozed_until"]) if row["snoozed_until"] is not None else None
            ),
        }
        for row in task_rows
    ]

    journal_rows = conn.execute(
        """
        SELECT id, public_id, event_type, metadata_json
        FROM garden_journal_entries
        ORDER BY created_at_ms ASC, public_id ASC
        """,
    ).fetchall()
    entry_ids = [int(row["id"]) for row in journal_rows]
    journal_plant_map = _journal_plant_map(conn, entry_ids)
    journal = [
        {
            "public_id": str(row["public_id"]),
            "event_type": str(row["event_type"]),
            "metadata": json.loads(str(row["metadata_json"]) or "{}"),
            "plant_ids": journal_plant_map.get(int(row["id"]), []),
        }
        for row in journal_rows
    ]
    print(json.dumps({"tasks": tasks, "journal": journal}, sort_keys=True))


def seed(conn) -> None:
    truncate_public_tables(conn)
    owner_user_id = ensure_admin_user(conn)
    garden_id = ensure_garden(conn, owner_user_id)
    seed_layout(conn, garden_id)
    seed_plot(conn, garden_id, owner_user_id)
    seed_plants(conn, garden_id, owner_user_id)
    seed_tasks(conn, garden_id, owner_user_id)


def main() -> None:
    require_task_history_e2e_database(os.environ.get("DATABASE_URL", ""))
    run_migrations()
    conn = get_db()
    try:
        if len(sys.argv) == 2 and sys.argv[1] == "snapshot":
            print_snapshot(conn)
            return
        if len(sys.argv) != 1:
            raise SystemExit("Usage: seed_task_completion_history_e2e.py [snapshot]")
        seed(conn)
        conn.commit()
    finally:
        return_db(conn)
        close_pool()


if __name__ == "__main__":
    main()
