"""Lightweight automation rules for garden data changes."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from gardenops.db import DbConn, current_timestamp_ms
from gardenops.services.attention.outcomes import upsert_attention_outcome
from gardenops.services.attention.types import NO_ACTION_RETENTION_DAYS

_logger = logging.getLogger(__name__)


def on_issue_created(
    db: DbConn,
    garden_id: int,
    issue_id: int,
    actor_user_id: int | None,
) -> int:
    """Generate a follow-up inspection task when an issue is created.

    Returns the created task ID, or 0 if skipped.
    """
    issue = db.execute(
        """
        SELECT title, follow_up_on, severity, public_id
        FROM garden_issues
        WHERE id = %s AND garden_id = %s
        """,
        (issue_id, garden_id),
    ).fetchone()
    if not issue:
        return 0

    follow_up = issue["follow_up_on"]
    if not follow_up:
        follow_up = (date.today() + timedelta(days=7)).isoformat()

    issue_public_id = str(issue["public_id"])
    rule_source = f"auto:issue_followup:{issue_public_id}"
    existing = db.execute(
        "SELECT 1 FROM garden_tasks WHERE garden_id = %s AND rule_source = %s",
        (garden_id, rule_source),
    ).fetchone()
    if existing:
        return 0

    now_ms = current_timestamp_ms()
    title = f"Follow up: {issue['title']}"
    severity = issue["severity"]
    if severity not in ("low", "normal", "high"):
        severity = "normal"

    desc_en = f"Auto-generated from issue {issue_public_id}. Review and update status."
    desc_no = f"Automatisk opprettet fra sak {issue_public_id}. Gjennomg\u00e5 og oppdater status."
    meta = json.dumps({"description_no": desc_no})
    arow = db.execute(
        """INSERT INTO garden_tasks
           (garden_id, task_type, title, description, status,
            severity, due_on, rule_source, metadata_json,
            created_by_user_id, created_at_ms, updated_at_ms)
           VALUES (%s, 'inspect_issue', %s, %s, 'pending',
                   %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (
            garden_id,
            title,
            desc_en,
            severity,
            follow_up,
            rule_source,
            meta,
            actor_user_id,
            now_ms,
            now_ms,
        ),
    ).fetchone()
    assert arow is not None
    task_id = int(arow["id"])

    for row in db.execute(
        "SELECT plt_id FROM garden_issue_plants WHERE issue_id = %s",
        (issue_id,),
    ).fetchall():
        db.execute(
            "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
            (task_id, str(row["plt_id"])),
        )
    for row in db.execute(
        "SELECT plot_id FROM garden_issue_plots WHERE issue_id = %s",
        (issue_id,),
    ).fetchall():
        db.execute(
            "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, %s)",
            (task_id, str(row["plot_id"])),
        )

    return task_id


def _issue_followup_due(follow_up_on: object) -> str:
    if follow_up_on:
        return str(follow_up_on)
    return (date.today() + timedelta(days=7)).isoformat()


def _sync_issue_followup_links(db: DbConn, issue_id: int, task_id: int) -> None:
    db.execute("DELETE FROM garden_task_plants WHERE task_id = %s", (task_id,))
    db.execute("DELETE FROM garden_task_plots WHERE task_id = %s", (task_id,))
    for row in db.execute(
        "SELECT plt_id FROM garden_issue_plants WHERE issue_id = %s",
        (issue_id,),
    ).fetchall():
        db.execute(
            "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
            (task_id, str(row["plt_id"])),
        )
    for row in db.execute(
        "SELECT plot_id FROM garden_issue_plots WHERE issue_id = %s",
        (issue_id,),
    ).fetchall():
        db.execute(
            "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, %s)",
            (task_id, str(row["plot_id"])),
        )


def sync_issue_followup_task(
    db: DbConn,
    garden_id: int,
    issue_id: int,
    actor_user_id: int | None,
    *,
    now_ms: int | None = None,
) -> list[tuple[str, str]]:
    """Keep the generated issue follow-up task aligned with the issue state.

    Returns ``(task_public_id, clear_reason)`` pairs for task notifications that
    should be dismissed by the caller.
    """
    issue = db.execute(
        """
        SELECT title, follow_up_on, severity, public_id, status
        FROM garden_issues
        WHERE id = %s AND garden_id = %s
        """,
        (issue_id, garden_id),
    ).fetchone()
    if not issue:
        return []

    issue_public_id = str(issue["public_id"])
    rule_source = f"auto:issue_followup:{issue_public_id}"
    task = db.execute(
        """
        SELECT id, public_id, status, due_on
        FROM garden_tasks
        WHERE garden_id = %s AND rule_source = %s
        FOR UPDATE
        """,
        (garden_id, rule_source),
    ).fetchone()

    now = int(now_ms or current_timestamp_ms())
    status = str(issue["status"] or "open")
    if status in {"resolved", "dismissed"}:
        if not task:
            return []
        task_status = str(task["status"] or "")
        if task_status in {"pending", "snoozed"}:
            db.execute(
                """
                UPDATE garden_tasks
                SET status = 'skipped',
                    snoozed_until = NULL,
                    completed_by_user_id = NULL,
                    completed_at_ms = NULL,
                    updated_at_ms = %s
                WHERE id = %s
                """,
                (now, int(task["id"])),
            )
            return [(str(task["public_id"]), status)]
        return []

    if not task:
        created_task_id = on_issue_created(db, garden_id, issue_id, actor_user_id)
        if not created_task_id:
            return []
        _sync_issue_followup_links(db, issue_id, created_task_id)
        return []

    due_on = _issue_followup_due(issue["follow_up_on"])
    severity = str(issue["severity"] or "normal")
    if severity not in {"low", "normal", "high"}:
        severity = "normal"
    title = f"Follow up: {issue['title']}"
    task_status = str(task["status"] or "")
    db.execute(
        """
        UPDATE garden_tasks
        SET title = %s,
            severity = %s,
            due_on = %s,
            status = CASE
                WHEN status IN ('pending', 'snoozed', 'skipped') THEN 'pending'
                ELSE status
            END,
            snoozed_until = NULL,
            completed_by_user_id = CASE
                WHEN status = 'completed' THEN completed_by_user_id
                ELSE NULL
            END,
            completed_at_ms = CASE
                WHEN status = 'completed' THEN completed_at_ms
                ELSE NULL
            END,
            updated_at_ms = %s
        WHERE id = %s
        """,
        (title, severity, due_on, now, int(task["id"])),
    )
    _sync_issue_followup_links(db, issue_id, int(task["id"]))
    if task_status in {"pending", "snoozed"} and str(task["due_on"]) != due_on:
        return [(str(task["public_id"]), "rescheduled")]
    return []


_WATERING_KEYWORDS = ("regular", "often", "jevnlig", "ofte", "mye", "frequently")

_HARDINESS_SQL = """
    SELECT p.plt_id, p.name, p.hardiness
    FROM plants p
    JOIN plant_ownership po ON po.plt_id = p.plt_id
    WHERE po.garden_id = %s
      AND p.hardiness != '' AND p.hardiness IS NOT NULL
"""

_CARE_WATERING_SQL = """
    SELECT p.plt_id, p.name, p.care_watering
    FROM plants p
    JOIN plant_ownership po ON po.plt_id = p.plt_id
    WHERE po.garden_id = %s
      AND p.care_watering IS NOT NULL AND p.care_watering != ''
"""


def _is_frost_vulnerable(plant: dict[str, Any]) -> bool:
    hardiness = str(plant["hardiness"] or "").lower()
    return not any(h in hardiness for h in ("h7", "h6", "zone 1", "zone 2", "zone 3"))


def _needs_extra_watering(plant: dict[str, Any]) -> bool:
    watering = str(plant["care_watering"]).lower()
    return any(kw in watering for kw in _WATERING_KEYWORDS)


def _create_weather_tasks(
    db: DbConn,
    garden_id: int,
    alert_id: int,
    actor_user_id: int | None,
    *,
    plant_sql: str,
    plant_filter: Callable[[dict[str, Any]], bool],
    task_type: str,
    rule_prefix: str,
    severity: str,
    title_tpl: str,
    desc_en_tpl: str,
    desc_no_tpl: str,
) -> int:
    """Create weather-alert tasks for matching plants. Returns count created."""
    alert = db.execute(
        "SELECT valid_from FROM weather_alerts WHERE id = %s AND garden_id = %s",
        (alert_id, garden_id),
    ).fetchone()
    if not alert:
        return 0

    due_on = str(alert["valid_from"])[:10]
    now_ms = current_timestamp_ms()
    created = 0

    for plant in db.execute(plant_sql, (garden_id,)).fetchall():
        if not plant_filter(plant):
            continue

        plt_id = str(plant["plt_id"])
        rule_source = f"{rule_prefix}:{alert_id}:{plt_id}"
        if db.execute(
            "SELECT 1 FROM garden_tasks WHERE garden_id = %s AND rule_source = %s",
            (garden_id, rule_source),
        ).fetchone():
            continue

        pname = plant["name"]

        # Find plots this plant is assigned to in this garden
        plot_rows = db.execute(
            """
            SELECT pp.plot_id FROM plot_plants pp
            JOIN plot_ownership po ON pp.plot_id = po.plot_id
            WHERE pp.plt_id = %s AND po.garden_id = %s
            """,
            (plt_id, garden_id),
        ).fetchall()
        plot_ids = [str(r["plot_id"]) for r in plot_rows]

        # Include first plot in title for location context
        if len(plot_ids) == 1:
            title = title_tpl.format(name=f"{pname} ({plot_ids[0]})")
        elif len(plot_ids) > 1:
            title = title_tpl.format(name=f"{pname} ({plot_ids[0]}, \u2026)")
        else:
            title = title_tpl.format(name=pname)

        meta = json.dumps({"description_no": desc_no_tpl.format(name=pname)})
        brow = db.execute(
            """INSERT INTO garden_tasks
               (garden_id, task_type, title, description, status,
                severity, due_on, rule_source, metadata_json,
                created_by_user_id, created_at_ms, updated_at_ms)
               VALUES (%s, %s, %s, %s, 'pending',
                       %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                garden_id,
                task_type,
                title,
                desc_en_tpl.format(name=pname),
                severity,
                due_on,
                rule_source,
                meta,
                actor_user_id,
                now_ms,
                now_ms,
            ),
        ).fetchone()
        assert brow is not None
        task_id = int(brow["id"])
        db.execute(
            "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
            (task_id, plt_id),
        )
        for pid in plot_ids:
            db.execute(
                "INSERT INTO garden_task_plots"
                " (task_id, plot_id) VALUES (%s, %s)"
                " ON CONFLICT DO NOTHING",
                (task_id, pid),
            )
        created += 1

    return created


def on_frost_alert(
    db: DbConn,
    garden_id: int,
    alert_id: int,
    actor_user_id: int | None,
) -> int:
    """Generate protection tasks for frost-vulnerable plants."""
    return _create_weather_tasks(
        db,
        garden_id,
        alert_id,
        actor_user_id,
        plant_sql=_HARDINESS_SQL,
        plant_filter=_is_frost_vulnerable,
        task_type="protect",
        rule_prefix="auto:frost_protect",
        severity="high",
        title_tpl="Protect from frost: {name}",
        desc_en_tpl="Frost alert \u2014 cover or move {name} to shelter",
        desc_no_tpl="Frostvarsel \u2014 dekk til eller flytt {name} i ly",
    )


def on_heat_alert(
    db: DbConn,
    garden_id: int,
    alert_id: int,
    actor_user_id: int | None,
) -> int:
    """Generate shade/water tasks for watering-sensitive plants."""
    return _create_weather_tasks(
        db,
        garden_id,
        alert_id,
        actor_user_id,
        plant_sql=_CARE_WATERING_SQL,
        plant_filter=_needs_extra_watering,
        task_type="protect",
        rule_prefix="auto:heat_protect",
        severity="high",
        title_tpl="Provide shade: {name}",
        desc_en_tpl="Heat wave \u2014 provide shade and extra water for {name}",
        desc_no_tpl="Heteb\u00f8lge \u2014 gi skygge og ekstra vann til {name}",
    )


def on_dry_spell_alert(
    db: DbConn,
    garden_id: int,
    alert_id: int,
    actor_user_id: int | None,
) -> int:
    """Generate watering tasks for watering-sensitive plants during dry spells."""
    return _create_weather_tasks(
        db,
        garden_id,
        alert_id,
        actor_user_id,
        plant_sql=_CARE_WATERING_SQL,
        plant_filter=_needs_extra_watering,
        task_type="water",
        rule_prefix="auto:dry_water",
        severity="normal",
        title_tpl="Water regularly: {name}",
        desc_en_tpl="Dry spell \u2014 water {name} regularly, check soil moisture",
        desc_no_tpl="T\u00f8rkeperiode \u2014 vann {name} jevnlig, sjekk jordfuktighet",
    )


def on_rain_alert(
    db: DbConn,
    garden_id: int,
    alert_id: int,
    actor_user_id: int | None,
) -> int:
    """Generate drainage check tasks and reschedule pending watering tasks."""
    created = _create_weather_tasks(
        db,
        garden_id,
        alert_id,
        actor_user_id,
        plant_sql=_CARE_WATERING_SQL,
        plant_filter=_needs_extra_watering,
        task_type="protect",
        rule_prefix="auto:rain_drainage",
        severity="normal",
        title_tpl="Check drainage: {name}",
        desc_en_tpl="Heavy rain \u2014 check drainage around {name}, avoid waterlogging",
        desc_no_tpl="Kraftig regn \u2014 sjekk drenering rundt {name}, unng\u00e5 vannmetning",
    )
    _reschedule_watering_during_rain(db, garden_id, alert_id)
    return created


def _parse_mapping_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _rain_mm_from_alert_metadata(metadata: dict[str, Any]) -> float | None:
    for key in ("rain_mm", "total_mm", "total_precip_mm", "precip_mm"):
        raw = metadata.get(key)
        if raw in (None, ""):
            continue
        try:
            return float(raw)
        except TypeError:
            continue
        except ValueError:
            continue
    return None


def _format_rain_mm(rain_mm: float | None) -> str:
    if rain_mm is None:
        return "Rain"
    if rain_mm.is_integer():
        return f"{int(rain_mm)} mm rain"
    return f"{rain_mm:.1f} mm rain"


def _plant_id_from_water_rule(rule_source: str) -> str:
    parts = rule_source.split(":")
    if len(parts) >= 3 and parts[0] == "water":
        return parts[1]
    return ""


def _plant_ids_for_task(db: DbConn, task_id: int) -> tuple[str, ...]:
    rows = db.execute(
        """
        SELECT plt_id
        FROM garden_task_plants
        WHERE task_id = %s
        ORDER BY plt_id
        """,
        (task_id,),
    ).fetchall()
    return tuple(str(row["plt_id"]) for row in rows)


def _plot_ids_for_task(db: DbConn, task_id: int) -> tuple[str, ...]:
    rows = db.execute(
        """
        SELECT plot_id
        FROM garden_task_plots
        WHERE task_id = %s
        ORDER BY plot_id
        """,
        (task_id,),
    ).fetchall()
    return tuple(str(row["plot_id"]) for row in rows)


def _write_watering_rescheduled_by_rain_outcome(
    db: DbConn,
    *,
    garden_id: int,
    alert: Any,
    task: Any,
    old_due_on: str,
    new_due_on: str,
    plant_ids: tuple[str, ...],
    plot_ids: tuple[str, ...],
    now_ms: int,
) -> None:
    rule_source = str(task["rule_source"] or "")
    target_id = plant_ids[0] if plant_ids else _plant_id_from_water_rule(rule_source)
    if not target_id:
        return
    alert_metadata = _parse_mapping_json(alert["metadata_json"])
    rain_mm = _rain_mm_from_alert_metadata(alert_metadata)
    task_title = str(task["title"] or "Watering")
    outcome_plant_ids = plant_ids or ((target_id,) if target_id else ())
    metadata: dict[str, Any] = {
        "due_on": old_due_on,
        "new_due_on": new_due_on,
        "rule_source": rule_source,
        "task_public_id": str(task["public_id"]),
        "task_title": task_title,
        "weather_alert_id": str(alert["id"]),
        "alert_valid_from": str(alert["valid_from"]),
        "alert_valid_until": str(alert["valid_until"]),
        "alert": alert_metadata,
    }
    if rain_mm is not None:
        metadata["rain_mm"] = rain_mm
    upsert_attention_outcome(
        db,
        garden_id=garden_id,
        provider="weather",
        outcome_type="watering_rescheduled_by_rain",
        source_type="task_generator",
        source_id=str(alert["id"]),
        source_public_id=rule_source,
        target_type="plant",
        target_id=target_id,
        title="Watering rescheduled by rain",
        explanation=(
            f"{_format_rain_mm(rain_mm)} moved {task_title} from {old_due_on} to {new_due_on}."
        ),
        reason="Rain rescheduled watering",
        plant_ids=outcome_plant_ids,
        plot_ids=plot_ids,
        metadata=metadata,
        recovery_action={
            "kind": "restore_generated_watering_task",
            "label": "Restore watering",
            "source_public_id": rule_source,
            "target_type": "plant",
            "target_id": target_id,
            "due_on": old_due_on,
            "plant_ids": list(outcome_plant_ids),
            "plot_ids": list(plot_ids),
        },
        occurred_at_ms=now_ms,
        expires_at_ms=now_ms + (NO_ACTION_RETENTION_DAYS * 86_400_000),
    )


def _reschedule_watering_during_rain(
    db: DbConn,
    garden_id: int,
    alert_id: int,
) -> None:
    """Reschedule seasonal watering tasks that fall within a rain alert window."""
    alert = db.execute(
        """
        SELECT id, valid_from, valid_until, metadata_json
        FROM weather_alerts
        WHERE id = %s
        """,
        (alert_id,),
    ).fetchone()
    if not alert:
        return
    valid_from = alert["valid_from"]
    valid_until = alert["valid_until"]
    # Only reschedule seasonal watering tasks (rule_source starts with 'water:'),
    # not auto-generated drainage tasks ('auto:rain_drainage:').
    rows = db.execute(
        """
        SELECT id, public_id, title, due_on, rule_source, metadata_json
        FROM garden_tasks
        WHERE garden_id = %s AND task_type = 'water' AND status = 'pending'
          AND rule_source LIKE 'water:%%'
          AND due_on >= %s AND due_on <= %s
        """,
        (garden_id, valid_from, valid_until),
    ).fetchall()
    if not rows:
        return
    # Reschedule to one day after the rain alert ends
    new_due = (date.fromisoformat(valid_until) + timedelta(days=1)).isoformat()
    now_ms = current_timestamp_ms()
    for row in rows:
        old_meta = row["metadata_json"] if "metadata_json" in row.keys() else "{}"
        meta = _parse_mapping_json(old_meta)
        old_due_on = str(row["due_on"])
        meta["rescheduled_from"] = old_due_on
        meta["rescheduled_reason"] = "rain_alert"
        plant_ids = _plant_ids_for_task(db, int(row["id"]))
        plot_ids = _plot_ids_for_task(db, int(row["id"]))
        db.execute(
            """
            UPDATE garden_tasks
            SET due_on = %s, metadata_json = %s, updated_at_ms = %s
            WHERE id = %s
            """,
            (new_due, json.dumps(meta, separators=(",", ":")), now_ms, row["id"]),
        )
        _write_watering_rescheduled_by_rain_outcome(
            db,
            garden_id=garden_id,
            alert=alert,
            task=row,
            old_due_on=old_due_on,
            new_due_on=new_due,
            plant_ids=plant_ids,
            plot_ids=plot_ids,
            now_ms=now_ms,
        )
    _logger.info(
        "Rescheduled %d watering tasks to %s due to rain alert %d",
        len(rows),
        new_due,
        alert_id,
    )


_SEVERITY_ORDER = ["low", "normal", "high", "critical"]


def _bump_severity(current: str) -> str:
    """Return the next severity level, capping at critical."""
    idx = _SEVERITY_ORDER.index(current) if current in _SEVERITY_ORDER else 1
    return _SEVERITY_ORDER[min(idx + 1, len(_SEVERITY_ORDER) - 1)]


def escalate_overdue_follow_ups(
    db: DbConn,
    garden_id: int,
) -> dict[str, int]:
    """Escalate issues whose follow-up date has passed.

    Bumps severity and creates an inspect_issue task for each
    overdue issue that hasn't already been escalated.

    Returns dict with count of escalated issues.
    """
    today_iso = date.today().isoformat()
    overdue = db.execute(
        """SELECT id, public_id, title, severity, follow_up_on
           FROM garden_issues
           WHERE garden_id = %s
             AND status IN ('open', 'monitoring', 'treating')
             AND follow_up_on IS NOT NULL
             AND follow_up_on < %s""",
        (garden_id, today_iso),
    ).fetchall()

    now_ms = current_timestamp_ms()
    due_on = (date.today() + timedelta(days=3)).isoformat()
    count = 0

    for issue in overdue:
        issue_id = int(issue["id"])
        issue_public_id = str(issue["public_id"])
        follow_up_on = str(issue["follow_up_on"])
        rule_source = f"auto:escalation:{issue_public_id}:{follow_up_on}"

        if db.execute(
            "SELECT 1 FROM garden_tasks WHERE garden_id = %s AND rule_source = %s",
            (garden_id, rule_source),
        ).fetchone():
            continue

        new_severity = _bump_severity(str(issue["severity"]))
        db.execute(
            "UPDATE garden_issues SET severity = %s, updated_at_ms = %s "
            "WHERE id = %s AND garden_id = %s",
            (new_severity, now_ms, issue_id, garden_id),
        )

        # garden_tasks.severity CHECK allows low/normal/high only
        task_severity = "high" if new_severity == "critical" else new_severity

        esc_desc_en = (
            f"Issue {issue_public_id} passed follow-up date {follow_up_on}. "
            "Needs immediate attention."
        )
        esc_desc_no = (
            f"Sak {issue_public_id} passerte oppf\u00f8lgingsdato {follow_up_on}."
            " Trenger umiddelbar oppmerksomhet."
        )
        esc_meta = json.dumps({"description_no": esc_desc_no})
        crow = db.execute(
            """INSERT INTO garden_tasks
               (garden_id, task_type, title, description, status,
                severity, due_on, rule_source, metadata_json,
                created_by_user_id, created_at_ms, updated_at_ms)
               VALUES (%s, 'inspect_issue', %s, %s, 'pending',
                       %s, %s, %s, %s, NULL, %s, %s) RETURNING id""",
            (
                garden_id,
                f"Overdue follow-up: {issue['title']}",
                esc_desc_en,
                task_severity,
                due_on,
                rule_source,
                esc_meta,
                now_ms,
                now_ms,
            ),
        ).fetchone()
        assert crow is not None
        task_id = int(crow["id"])

        for row in db.execute(
            "SELECT plt_id FROM garden_issue_plants WHERE issue_id = %s",
            (issue_id,),
        ).fetchall():
            db.execute(
                "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
                (task_id, str(row["plt_id"])),
            )
        for row in db.execute(
            "SELECT plot_id FROM garden_issue_plots WHERE issue_id = %s",
            (issue_id,),
        ).fetchall():
            db.execute(
                "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, %s)",
                (task_id, str(row["plot_id"])),
            )

        count += 1

    if count:
        db.commit()

    return {"escalated": count}


def on_harvest_logged(
    db: DbConn,
    garden_id: int,
    harvest_id: int,
) -> None:
    """Update yield rollup metadata after a harvest entry is logged.

    Stores aggregated yield summary in app_settings for quick access.
    The harvest_id parameter identifies the triggering entry.
    """
    _ = harvest_id  # used for traceability, not queried
    year = date.today().year

    rows = db.execute(
        """
        SELECT h.unit,
               SUM(h.quantity) AS total_qty,
               COUNT(*) AS entry_count
        FROM harvest_entries h
        WHERE h.garden_id = %s AND h.occurred_on ILIKE %s
        GROUP BY h.unit
        """,
        (garden_id, f"{year}-%"),
    ).fetchall()

    rollup = {
        "year": year,
        "garden_id": garden_id,
        "by_unit": [
            {
                "unit": str(r["unit"]),
                "total_qty": float(r["total_qty"]),
                "entries": int(r["entry_count"]),
            }
            for r in rows
        ],
    }

    key = f"harvest_rollup:{garden_id}:{year}"
    db.execute(
        "INSERT INTO app_settings (key, value) VALUES (%s, %s)"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (key, json.dumps(rollup)),
    )
