#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys

from gardenops.db import close_pool, get_db, return_db, run_migrations
from gardenops.security import create_user
from gardenops.services.attention import require_attention_e2e_database, upsert_attention_outcome

E2E_GARDEN_SLUG = "attention-today-e2e"
E2E_GARDEN_NAME = "Attention Today E2E"
E2E_USERNAME = "attention_today_e2e_admin"
E2E_BASIL_PLANT_ID = "BASIL-E2E"
E2E_HYDRANGEA_PLANT_ID = "HYD-E2E"
E2E_CUCUMBER_PLANT_ID = "CUC-E2E"
E2E_INDOOR_PLOT_ID = "INDOOR-KITCHEN"
E2E_BASIL_TASK_PUBLIC_ID = "tsk_attention_today_e2e_water_indoor_basil"
E2E_HYDRANGEA_TASK_PUBLIC_ID = "tsk_attention_today_e2e_water_hydrangea"
E2E_HYDRANGEA_RULE_SOURCE = "water:HYD-E2E:2026-07-05"
E2E_ISSUE_PUBLIC_ID = "iss_attention_today_e2e_mildew"
E2E_CALENDAR_PUBLIC_ID = "calevt_attention_today_e2e_seed_swap"
E2E_STATUS_NOTIFICATION_PUBLIC_ID = "note_attention_today_e2e_backup_status"


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
        password="AttentionE2E!Passphrase1234567890",  # push-sanitizer: allow SECRET_ASSIGNMENT
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
        VALUES ('A1', %s, 'A', 'Annuals', 1, 1, 1, '', '', '#7fb069')
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
        (garden_id,),
    )
    conn.execute(
        """
        INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
        VALUES ('A1', %s, %s)
        ON CONFLICT (plot_id) DO UPDATE SET
            owner_user_id = excluded.owner_user_id,
            garden_id = excluded.garden_id
        """,
        (owner_user_id, garden_id),
    )
    conn.execute(
        """
        INSERT INTO plots (
            plot_id, garden_id, zone_code, zone_name, plot_number,
            grid_row, grid_col, sub_zone, notes, color
        )
        VALUES (%s, %s, 'I', 'Indoors', 1, NULL, NULL, 'Kitchen', 'Kitchen shelf', '#7c9eb2')
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
        (E2E_INDOOR_PLOT_ID, garden_id),
    )
    conn.execute(
        """
        INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (plot_id) DO UPDATE SET
            owner_user_id = excluded.owner_user_id,
            garden_id = excluded.garden_id
        """,
        (E2E_INDOOR_PLOT_ID, owner_user_id, garden_id),
    )


def seed_plant(
    conn,
    *,
    garden_id: int,
    owner_user_id: int,
    plant_id: str,
    name: str,
    category: str,
    watering: str,
    plot_id: str | None,
    room_label: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO plants (
            plt_id, name, latin, category, bloom_month, color, hardiness,
            height_cm, light, link, care_watering, care_soil, care_planting,
            care_maintenance, care_notes
        )
        VALUES (%s, %s, '', %s, '', '', '', NULL, '', '', %s, '', '', '', '')
        ON CONFLICT (plt_id) DO UPDATE SET
            name = excluded.name,
            category = excluded.category,
            care_watering = excluded.care_watering
        """,
        (plant_id, name, category, watering),
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
    if plot_id is None:
        return
    conn.execute(
        """
        INSERT INTO plot_plants
            (plot_id, plt_id, quantity, seen_growing, seen_growing_date, room_label)
        VALUES (%s, %s, 1, 1, '2026-07-05', %s)
        ON CONFLICT (plot_id, plt_id) DO UPDATE SET
            quantity = excluded.quantity,
            seen_growing = excluded.seen_growing,
            seen_growing_date = excluded.seen_growing_date,
            room_label = excluded.room_label
        """,
        (plot_id, plant_id, room_label),
    )


def seed_plants(conn, garden_id: int, owner_user_id: int) -> None:
    seed_plant(
        conn,
        garden_id=garden_id,
        owner_user_id=owner_user_id,
        plant_id=E2E_BASIL_PLANT_ID,
        name="Indoor basil",
        category="urter",
        watering="keep evenly moist indoors",
        plot_id=E2E_INDOOR_PLOT_ID,
        room_label="Kitchen",
    )
    seed_plant(
        conn,
        garden_id=garden_id,
        owner_user_id=owner_user_id,
        plant_id=E2E_HYDRANGEA_PLANT_ID,
        name="Hydrangea",
        category="busker",
        watering="regular moisture",
        plot_id="A1",
    )
    seed_plant(
        conn,
        garden_id=garden_id,
        owner_user_id=owner_user_id,
        plant_id=E2E_CUCUMBER_PLANT_ID,
        name="Cucumber",
        category="grønnsaker",
        watering="consistent moisture",
        plot_id="A1",
    )


def seed_task(conn, garden_id: int, owner_user_id: int) -> None:
    row = conn.execute(
        """
        INSERT INTO garden_tasks (
            public_id, garden_id, task_type, title, description, status,
            severity, due_on, rule_source, metadata_json, created_by_user_id,
            created_at_ms, updated_at_ms
        )
        VALUES (
            %s, %s, 'water', 'Water indoor basil', 'Water the kitchen basil by hand.', 'pending',
            'high', '2026-07-05', '', '{}', %s,
            1783180800000, 1783180800000
        )
        ON CONFLICT (public_id) DO UPDATE SET
            garden_id = excluded.garden_id,
            task_type = excluded.task_type,
            title = excluded.title,
            description = excluded.description,
            status = excluded.status,
            severity = excluded.severity,
            due_on = excluded.due_on,
            snoozed_until = NULL,
            rule_source = excluded.rule_source,
            metadata_json = excluded.metadata_json,
            created_by_user_id = excluded.created_by_user_id,
            completed_by_user_id = NULL,
            completed_at_ms = NULL,
            updated_at_ms = excluded.updated_at_ms
        RETURNING id
        """,
        (E2E_BASIL_TASK_PUBLIC_ID, garden_id, owner_user_id),
    ).fetchone()
    if not row:
        raise RuntimeError("Failed to create E2E task")
    task_id = int(row["id"])
    conn.execute(
        """
        INSERT INTO garden_task_plants (task_id, plt_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        (task_id, E2E_BASIL_PLANT_ID),
    )
    conn.execute(
        """
        INSERT INTO garden_task_plots (task_id, plot_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        (task_id, E2E_INDOOR_PLOT_ID),
    )

    hydrangea = conn.execute(
        """
        INSERT INTO garden_tasks (
            public_id, garden_id, task_type, title, description, status,
            severity, due_on, rule_source, metadata_json, created_by_user_id,
            created_at_ms, updated_at_ms
        )
        VALUES (
            %s, %s, 'water', 'Water hydrangea', 'Generated watering now covered by rain.',
            'pending', 'normal', '2026-07-05', %s, '{}', %s, 1783180800000, 1783180800000
        )
        ON CONFLICT (public_id) DO UPDATE SET
            garden_id = excluded.garden_id,
            task_type = excluded.task_type,
            title = excluded.title,
            description = excluded.description,
            status = excluded.status,
            severity = excluded.severity,
            due_on = excluded.due_on,
            snoozed_until = NULL,
            rule_source = excluded.rule_source,
            metadata_json = excluded.metadata_json,
            created_by_user_id = excluded.created_by_user_id,
            completed_by_user_id = NULL,
            completed_at_ms = NULL,
            updated_at_ms = excluded.updated_at_ms
        RETURNING id
        """,
        (
            E2E_HYDRANGEA_TASK_PUBLIC_ID,
            garden_id,
            E2E_HYDRANGEA_RULE_SOURCE,
            owner_user_id,
        ),
    ).fetchone()
    if not hydrangea:
        raise RuntimeError("Failed to create E2E hydrangea task")
    hydrangea_task_id = int(hydrangea["id"])
    conn.execute(
        """
        INSERT INTO garden_task_plants (task_id, plt_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        (hydrangea_task_id, E2E_HYDRANGEA_PLANT_ID),
    )
    conn.execute(
        """
        INSERT INTO garden_task_plots (task_id, plot_id)
        VALUES (%s, 'A1')
        ON CONFLICT DO NOTHING
        """,
        (hydrangea_task_id,),
    )


def seed_weather_and_outcome(conn, garden_id: int) -> None:
    alert = conn.execute(
        """
        INSERT INTO weather_alerts
            (garden_id, alert_type, severity, title, description,
             valid_from, valid_until, metadata_json, dismissed, created_at_ms)
        VALUES (%s, 'rain_surplus', 'high', '18 mm rain expected',
                '18 mm rain expected before evening; outdoor watering is covered.',
                '2026-07-05', '2026-07-06', %s, 0, 1783180800000)
        RETURNING id
        """,
        (garden_id, json.dumps({"rain_mm": 18})),
    ).fetchone()
    if not alert:
        raise RuntimeError("Failed to create E2E rain alert")
    alert_id = int(alert["id"])
    conn.execute(
        """
        INSERT INTO weather_alert_plants (alert_id, plt_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        (alert_id, E2E_HYDRANGEA_PLANT_ID),
    )
    upsert_attention_outcome(
        conn,
        garden_id=garden_id,
        provider="weather",
        outcome_type="watering_covered_by_rain",
        source_type="task_generator",
        source_id=str(alert_id),
        source_public_id=E2E_HYDRANGEA_RULE_SOURCE,
        target_type="plant",
        target_id=E2E_HYDRANGEA_PLANT_ID,
        title="Watering covered by rain",
        explanation="18 mm rain expected already covers scheduled watering for Hydrangea.",
        reason="Rain surplus covers the watering date",
        plant_ids=(E2E_HYDRANGEA_PLANT_ID,),
        plot_ids=("A1",),
        metadata={"due_on": "2026-07-05", "rain_mm": 18, "plant_name": "Hydrangea"},
        recovery_action={
            "kind": "restore_generated_watering_task",
            "label": "Restore watering",
            "source_public_id": E2E_HYDRANGEA_RULE_SOURCE,
            "target_type": "plant",
            "target_id": E2E_HYDRANGEA_PLANT_ID,
            "due_on": "2026-07-05",
            "plant_ids": [E2E_HYDRANGEA_PLANT_ID],
            "plot_ids": ["A1"],
        },
        occurred_at_ms=1783180800000,
        expires_at_ms=1785772800000,
    )
    for idx in range(5):
        upsert_attention_outcome(
            conn,
            garden_id=garden_id,
            provider="weather",
            outcome_type="watering_covered_by_rain",
            source_type="task_generator",
            source_id=str(alert_id),
            source_public_id=f"water:HYD-E2E-EXTRA-{idx}:2026-07-05",
            target_type="plant",
            target_id=f"HYD-E2E-EXTRA-{idx}",
            title=f"Extra watering covered by rain {idx + 1}",
            explanation=f"Extra rain outcome {idx + 1} stays in no-action history.",
            reason="Rain surplus covers the watering date",
            plant_ids=(),
            plot_ids=("A1",),
            metadata={"due_on": "2026-07-05", "rain_mm": 18},
            occurred_at_ms=1783180700000 - idx,
            expires_at_ms=1785772800000,
        )


def seed_issue(conn, garden_id: int, owner_user_id: int) -> None:
    issue = conn.execute(
        """
        INSERT INTO garden_issues
            (public_id, garden_id, issue_type, title, description, severity, status,
             suspected_cause, treatment_plan, follow_up_on, metadata_json,
             created_by_user_id, created_at_ms, updated_at_ms)
        VALUES (%s, %s, 'disease', 'Check mildew on cucumber',
                'Follow up on the cucumber leaves after the first treatment.',
                'high', 'open', 'Powdery mildew', 'Inspect leaves and improve airflow.',
                '2026-07-04', '{}', %s, 1783094400000, 1783180800000)
        RETURNING id
        """,
        (E2E_ISSUE_PUBLIC_ID, garden_id, owner_user_id),
    ).fetchone()
    if not issue:
        raise RuntimeError("Failed to create E2E issue")
    issue_id = int(issue["id"])
    conn.execute(
        """
        INSERT INTO garden_issue_plants (issue_id, plt_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        (issue_id, E2E_CUCUMBER_PLANT_ID),
    )
    conn.execute(
        """
        INSERT INTO garden_issue_plots (issue_id, plot_id)
        VALUES (%s, 'A1')
        ON CONFLICT DO NOTHING
        """,
        (issue_id,),
    )


def seed_calendar(conn, garden_id: int, owner_user_id: int) -> None:
    event = conn.execute(
        """
        INSERT INTO garden_calendar_events
            (public_id, garden_id, title, description, event_on,
             created_by_user_id, updated_by_user_id, created_at_ms, updated_at_ms)
        VALUES (%s, %s, 'Community seed swap', 'Bring saved seed envelopes.',
                '2026-07-05', %s, %s, 1783180800000, 1783180800000)
        ON CONFLICT (public_id) DO UPDATE SET
            garden_id = excluded.garden_id,
            title = excluded.title,
            description = excluded.description,
            event_on = excluded.event_on,
            updated_by_user_id = excluded.updated_by_user_id,
            updated_at_ms = excluded.updated_at_ms
        RETURNING id
        """,
        (E2E_CALENDAR_PUBLIC_ID, garden_id, owner_user_id, owner_user_id),
    ).fetchone()
    if not event:
        raise RuntimeError("Failed to create E2E calendar event")
    conn.execute(
        """
        INSERT INTO garden_calendar_event_plots (event_id, plot_id)
        VALUES (%s, 'A1')
        ON CONFLICT DO NOTHING
        """,
        (int(event["id"]),),
    )


def seed_status_notification(conn, garden_id: int) -> None:
    conn.execute(
        """
        INSERT INTO notification_events
            (public_id, garden_id, user_id, notification_type, notification_subtype,
             severity, title, body, target_type, target_id, read_at_ms, emailed_at_ms,
             metadata_json, dismissed, created_at_ms, expires_at_ms, cleared_at_ms,
             clear_reason, superseded_by_id)
        VALUES (%s, %s, NULL, 'system', 'backup', 'high',
                'Backup status needs review', 'Nightly backup finished later than usual.',
                'status', 'backup', NULL, NULL, %s, 0, 1783180800000,
                NULL, NULL, NULL, NULL)
        ON CONFLICT (public_id) DO UPDATE SET
            garden_id = excluded.garden_id,
            user_id = excluded.user_id,
            notification_type = excluded.notification_type,
            notification_subtype = excluded.notification_subtype,
            severity = excluded.severity,
            title = excluded.title,
            body = excluded.body,
            target_type = excluded.target_type,
            target_id = excluded.target_id,
            read_at_ms = excluded.read_at_ms,
            emailed_at_ms = excluded.emailed_at_ms,
            metadata_json = excluded.metadata_json,
            dismissed = excluded.dismissed,
            created_at_ms = excluded.created_at_ms,
            expires_at_ms = excluded.expires_at_ms,
            cleared_at_ms = excluded.cleared_at_ms,
            clear_reason = excluded.clear_reason,
            superseded_by_id = excluded.superseded_by_id
        """,
        (
            E2E_STATUS_NOTIFICATION_PUBLIC_ID,
            garden_id,
            json.dumps({"target_type": "status", "target_id": "backup"}),
        ),
    )


def print_notification_snapshot(conn) -> None:
    rows = conn.execute(
        """
        SELECT public_id, notification_type, notification_subtype, severity, title, body,
               target_type, target_id, dismissed, read_at_ms, cleared_at_ms,
               clear_reason, superseded_by_id
        FROM notification_events
        ORDER BY public_id
        """,
    ).fetchall()
    print(
        json.dumps(
            [
                {
                    "public_id": str(row["public_id"]),
                    "notification_type": str(row["notification_type"]),
                    "notification_subtype": (
                        str(row["notification_subtype"])
                        if row["notification_subtype"] is not None
                        else None
                    ),
                    "severity": str(row["severity"] or "normal"),
                    "title": str(row["title"]),
                    "body": str(row["body"]),
                    "target_type": str(row["target_type"] or ""),
                    "target_id": str(row["target_id"] or ""),
                    "dismissed": int(row["dismissed"] or 0),
                    "read_at_ms": int(row["read_at_ms"]) if row["read_at_ms"] else None,
                    "cleared_at_ms": (int(row["cleared_at_ms"]) if row["cleared_at_ms"] else None),
                    "clear_reason": str(row["clear_reason"] or ""),
                    "superseded_by_id": (
                        int(row["superseded_by_id"]) if row["superseded_by_id"] else None
                    ),
                }
                for row in rows
            ],
            sort_keys=True,
        )
    )


def main() -> None:
    require_attention_e2e_database(os.environ.get("DATABASE_URL", ""))
    conn = None
    try:
        run_migrations()
        conn = get_db()
        if len(sys.argv) == 2 and sys.argv[1] == "snapshot-notifications":
            print_notification_snapshot(conn)
            return
        truncate_public_tables(conn)
        user_id = ensure_admin_user(conn)
        garden_id = ensure_garden(conn, user_id)
        seed_layout(conn, garden_id)
        seed_plot(conn, garden_id, user_id)
        seed_plants(conn, garden_id, user_id)
        seed_task(conn, garden_id, user_id)
        seed_weather_and_outcome(conn, garden_id)
        seed_issue(conn, garden_id, user_id)
        seed_calendar(conn, garden_id, user_id)
        seed_status_notification(conn, garden_id)
        conn.commit()
    finally:
        if conn is not None:
            return_db(conn)
        close_pool()


if __name__ == "__main__":
    main()
