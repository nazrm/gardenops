"""Calendar service -- normalize task/weather events and emit ICS feeds."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from typing import Any, Literal, cast

from fastapi import HTTPException

from gardenops.branding import app_name, app_slug
from gardenops.db import DbConn
from gardenops.router_helpers import validate_date
from gardenops.services.task_windows import (
    RECOMMENDED_WINDOW_RULES,
    window_state_for_range,
)

CalendarViewMode = Literal["month", "week", "agenda"]
CalendarPresetKey = Literal[
    "essential",
    "all_care",
    "watering",
    "harvest_season",
    "high_value",
]
CALENDAR_VIEW_MODES: tuple[CalendarViewMode, ...] = ("month", "week", "agenda")
CALENDAR_SOURCE_KEYS: tuple[str, ...] = (
    "garden_event",
    "protect",
    "prune",
    "deadhead",
    "divide",
    "fertilize",
    "sow",
    "plant_out",
    "observe_bloom",
    "harvest",
    "inspect_issue",
    "water",
    "weather_alert",
)
CALENDAR_PRESET_SOURCES: dict[CalendarPresetKey, tuple[str, ...]] = {
    "essential": (
        "garden_event",
        "protect",
        "prune",
        "fertilize",
        "sow",
        "plant_out",
        "observe_bloom",
        "harvest",
        "inspect_issue",
        "weather_alert",
    ),
    "all_care": CALENDAR_SOURCE_KEYS,
    "watering": ("water", "weather_alert"),
    "harvest_season": ("garden_event", "harvest", "observe_bloom", "weather_alert"),
    "high_value": (
        "garden_event",
        "protect",
        "prune",
        "fertilize",
        "sow",
        "plant_out",
        "harvest",
        "inspect_issue",
        "weather_alert",
    ),
}
DEFAULT_SUBSCRIPTION_PAST_DAYS = 14
DEFAULT_SUBSCRIPTION_FUTURE_DAYS = 180
DEFAULT_RECENT_HISTORY_DAYS = 21


def default_calendar_preferences() -> dict[str, Any]:
    return {
        "default_view": "month",
        "selected_preset": "essential",
        "visible_sources": list(CALENDAR_PRESET_SOURCES["essential"]),
        "include_recent_history": False,
        "selected_plant_ids": [],
        "selected_plot_ids": [],
        "selected_zone_codes": [],
    }


def preset_definitions() -> list[dict[str, Any]]:
    return [
        {"key": key, "source_keys": list(source_keys)}
        for key, source_keys in CALENDAR_PRESET_SOURCES.items()
    ]


def source_definitions() -> list[dict[str, str]]:
    return [
        {
            "key": source_key,
            "kind": (
                "weather"
                if source_key == "weather_alert"
                else "manual"
                if source_key == "garden_event"
                else "task"
            ),
        }
        for source_key in CALENDAR_SOURCE_KEYS
    ]


def normalize_calendar_view_mode(raw: str | None) -> CalendarViewMode:
    value = (raw or "month").strip().lower()
    if value not in CALENDAR_VIEW_MODES:
        raise HTTPException(status_code=422, detail="Invalid calendar view")
    return cast(CalendarViewMode, value)


def normalize_calendar_preset(raw: str | None) -> CalendarPresetKey:
    value = (raw or "essential").strip().lower()
    if value not in CALENDAR_PRESET_SOURCES:
        raise HTTPException(status_code=422, detail="Invalid calendar preset")
    return cast(CalendarPresetKey, value)


def normalize_visible_sources(
    values: list[str] | None,
    *,
    preset_key: CalendarPresetKey,
) -> list[str]:
    if values is None:
        return list(CALENDAR_PRESET_SOURCES[preset_key])
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw).strip().lower()
        if not value or value in seen:
            continue
        if value not in CALENDAR_SOURCE_KEYS:
            raise HTTPException(status_code=422, detail=f"Unsupported calendar source: {value}")
        seen.add(value)
        ordered.append(value)
    return ordered


def parse_visible_sources_query(
    raw: str | None,
    *,
    preset_key: CalendarPresetKey,
) -> list[str]:
    if raw is None:
        return list(CALENDAR_PRESET_SOURCES[preset_key])
    values = [part.strip() for part in raw.split(",")]
    return normalize_visible_sources(values, preset_key=preset_key)


def normalize_selected_plant_ids(values: list[str] | None) -> list[str]:
    if values is None:
        return []
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()[:40]
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def parse_selected_plant_ids_query(raw: str | None) -> list[str]:
    if raw is None:
        return []
    values = [part.strip() for part in raw.split(",")]
    return normalize_selected_plant_ids(values)


def normalize_selected_plot_ids(values: list[str] | None) -> list[str]:
    if values is None:
        return []
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()[:40]
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def parse_selected_plot_ids_query(raw: str | None) -> list[str]:
    if raw is None:
        return []
    values = [part.strip() for part in raw.split(",")]
    return normalize_selected_plot_ids(values)


def normalize_selected_zone_codes(values: list[str] | None) -> list[str]:
    if values is None:
        return []
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip().upper()[:20]
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def parse_selected_zone_codes_query(raw: str | None) -> list[str]:
    if raw is None:
        return []
    values = [part.strip() for part in raw.split(",")]
    return normalize_selected_zone_codes(values)


def validate_calendar_range(start_on: str, end_on: str) -> tuple[date, date]:
    validate_date(start_on)
    validate_date(end_on)
    start = date.fromisoformat(start_on)
    end = date.fromisoformat(end_on)
    if end <= start:
        raise HTTPException(status_code=422, detail="Calendar end must be after start")
    if (end - start).days > 400:
        raise HTTPException(status_code=422, detail="Calendar range too large")
    return start, end


def load_calendar_preferences(
    conn: DbConn,
    *,
    user_id: int | None,
    garden_id: int,
) -> tuple[dict[str, Any], bool]:
    defaults = default_calendar_preferences()
    if user_id is None:
        return defaults, False
    row = conn.execute(
        """
        SELECT default_view, selected_preset, visible_sources_json, include_recent_history,
               selected_plant_ids_json, selected_plot_ids_json, selected_zone_codes_json
        FROM user_calendar_preferences
        WHERE user_id = %s AND garden_id = %s
        """,
        (user_id, garden_id),
    ).fetchone()
    if not row:
        return defaults, False
    preset_key = normalize_calendar_preset(str(row["selected_preset"] or "essential"))
    visible_sources = normalize_visible_sources(
        _parse_json_array(row["visible_sources_json"]),
        preset_key=preset_key,
    )
    return (
        {
            "default_view": normalize_calendar_view_mode(str(row["default_view"] or "month")),
            "selected_preset": preset_key,
            "visible_sources": visible_sources,
            "include_recent_history": bool(int(row["include_recent_history"] or 0)),
            "selected_plant_ids": normalize_selected_plant_ids(
                _parse_json_array(row["selected_plant_ids_json"]),
            ),
            "selected_plot_ids": normalize_selected_plot_ids(
                _parse_json_array(row["selected_plot_ids_json"]),
            ),
            "selected_zone_codes": normalize_selected_zone_codes(
                _parse_json_array(row["selected_zone_codes_json"]),
            ),
        },
        True,
    )


def upsert_calendar_preferences(
    conn: DbConn,
    *,
    user_id: int,
    garden_id: int,
    default_view: CalendarViewMode,
    selected_preset: CalendarPresetKey,
    visible_sources: list[str],
    include_recent_history: bool,
    selected_plant_ids: list[str],
    selected_plot_ids: list[str],
    selected_zone_codes: list[str],
    now_ms: int,
) -> dict[str, Any]:
    conn.execute(
        """
        INSERT INTO user_calendar_preferences
            (user_id, garden_id, default_view, selected_preset, visible_sources_json,
             include_recent_history, selected_plant_ids_json, selected_plot_ids_json,
             selected_zone_codes_json, created_at_ms, updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, garden_id) DO UPDATE
        SET default_view = EXCLUDED.default_view,
            selected_preset = EXCLUDED.selected_preset,
            visible_sources_json = EXCLUDED.visible_sources_json,
            include_recent_history = EXCLUDED.include_recent_history,
            selected_plant_ids_json = EXCLUDED.selected_plant_ids_json,
            selected_plot_ids_json = EXCLUDED.selected_plot_ids_json,
            selected_zone_codes_json = EXCLUDED.selected_zone_codes_json,
            updated_at_ms = EXCLUDED.updated_at_ms
        """,
        (
            user_id,
            garden_id,
            default_view,
            selected_preset,
            json.dumps(visible_sources, ensure_ascii=False),
            1 if include_recent_history else 0,
            json.dumps(selected_plant_ids, ensure_ascii=False),
            json.dumps(selected_plot_ids, ensure_ascii=False),
            json.dumps(selected_zone_codes, ensure_ascii=False),
            now_ms,
            now_ms,
        ),
    )
    return {
        "default_view": default_view,
        "selected_preset": selected_preset,
        "visible_sources": visible_sources,
        "include_recent_history": include_recent_history,
        "selected_plant_ids": selected_plant_ids,
        "selected_plot_ids": selected_plot_ids,
        "selected_zone_codes": selected_zone_codes,
    }


def build_calendar_payload(
    conn: DbConn,
    *,
    garden_id: int,
    start: date,
    end: date,
    visible_sources: list[str],
    include_recent_history: bool,
    selected_plant_ids: list[str],
    selected_plot_ids: list[str],
    selected_zone_codes: list[str],
    today: date | None = None,
) -> dict[str, Any]:
    events, latest_ms = build_calendar_events(
        conn,
        garden_id=garden_id,
        start=start,
        end=end,
        visible_sources=visible_sources,
        include_recent_history=include_recent_history,
        selected_plant_ids=selected_plant_ids,
        selected_plot_ids=selected_plot_ids,
        selected_zone_codes=selected_zone_codes,
        today=today,
    )
    return {
        "events": events,
        "range": {
            "start_on": start.isoformat(),
            "end_on": end.isoformat(),
        },
        "latest_updated_at_ms": latest_ms,
    }


def build_calendar_events(
    conn: DbConn,
    *,
    garden_id: int,
    start: date,
    end: date,
    visible_sources: list[str],
    include_recent_history: bool,
    selected_plant_ids: list[str],
    selected_plot_ids: list[str],
    selected_zone_codes: list[str],
    today: date | None = None,
) -> tuple[list[dict[str, Any]], int]:
    visible_task_sources = [
        source for source in visible_sources if source not in {"weather_alert", "garden_event"}
    ]
    events: list[dict[str, Any]] = []
    latest_ms = 0
    if "garden_event" in visible_sources:
        manual_rows = _load_manual_events(conn, garden_id=garden_id, start=start, end=end)
        manual_plant_ids = _load_manual_event_plants(
            conn,
            [int(row["id"]) for row in manual_rows],
        )
        manual_plot_ids = _load_manual_event_plots(conn, [int(row["id"]) for row in manual_rows])
        _merge_plot_ids_from_plants(
            conn,
            garden_id=garden_id,
            plot_ids_by_owner=manual_plot_ids,
            plant_ids_by_owner=manual_plant_ids,
        )
        for row in manual_rows:
            event = _serialize_manual_event(
                row,
                plant_ids=manual_plant_ids.get(int(row["id"]), []),
                plot_ids=manual_plot_ids.get(int(row["id"]), []),
            )
            events.append(event)
            latest_ms = max(latest_ms, int(row["updated_at_ms"] or 0))
    if visible_task_sources:
        task_rows = _load_calendar_tasks(
            conn,
            garden_id=garden_id,
            start=start,
            end=end,
            task_types=visible_task_sources,
            include_recent_history=include_recent_history,
            today=today,
        )
        relations = _load_task_relations(
            conn,
            garden_id=garden_id,
            task_ids=[int(row["id"]) for row in task_rows],
        )
        for row in task_rows:
            event = _serialize_task_event(row, relations)
            events.append(event)
            latest_ms = max(latest_ms, int(row["updated_at_ms"] or 0))
    if "weather_alert" in visible_sources:
        alert_rows = _load_weather_alerts(conn, garden_id=garden_id, start=start, end=end)
        alert_plant_ids = _load_weather_alert_plants(conn, [int(row["id"]) for row in alert_rows])
        for row in alert_rows:
            event = _serialize_weather_alert(row, alert_plant_ids.get(int(row["id"]), []))
            events.append(event)
            latest_ms = max(latest_ms, int(row["created_at_ms"] or 0))
    selected_plant_ids_set = set(selected_plant_ids)
    selected_plot_ids_set = set(selected_plot_ids)
    selected_zone_codes_set = set(selected_zone_codes)
    if selected_zone_codes_set:
        plot_zone_codes = _load_plot_zone_codes(conn, garden_id=garden_id)
    else:
        plot_zone_codes = {}
    if selected_plant_ids_set or selected_plot_ids_set or selected_zone_codes_set:
        events = [
            event
            for event in events
            if _event_matches_selected_filters(
                event,
                selected_plant_ids=selected_plant_ids_set,
                selected_plot_ids=selected_plot_ids_set,
                selected_zone_codes=selected_zone_codes_set,
                plot_zone_codes=plot_zone_codes,
            )
        ]
        latest_ms = max((int(event.get("updated_at_ms") or 0) for event in events), default=0)
    events.sort(key=_event_sort_key)
    return events, latest_ms


def build_calendar_ics(
    *,
    garden_name: str,
    events: list[dict[str, Any]],
    generated_at: datetime | None = None,
) -> tuple[str, str, str | None]:
    content_timestamps = [
        datetime.fromtimestamp(int(event["updated_at_ms"]) / 1000, tz=UTC)
        for event in events
        if int(event.get("updated_at_ms") or 0) > 0
    ]
    if generated_at is not None:
        content_timestamps.append(generated_at.astimezone(UTC))
    content_timestamp = max(
        content_timestamps,
        default=datetime(1970, 1, 1, tzinfo=UTC),
    )
    dtstamp = _ical_datetime(content_timestamp)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{app_name()}//Garden Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ical_text(garden_name)}",
    ]
    for event in events:
        start = date.fromisoformat(str(event["start_on"]))
        end = date.fromisoformat(str(event["end_on"]))
        updated_ms = int(event.get("updated_at_ms") or 0)
        event_dt = (
            datetime.fromtimestamp(updated_ms / 1000, tz=UTC) if updated_ms else content_timestamp
        )
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{_ical_text(str(event['id']))}@{app_slug()}",
                f"DTSTAMP:{dtstamp}",
                f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{end.strftime('%Y%m%d')}",
                f"SUMMARY:{_ical_text(str(event['title']))}",
                f"DESCRIPTION:{_ical_text(_event_description_for_ics(event))}",
                f"CATEGORIES:{_ical_text(str(event['source_key']))}",
                f"LAST-MODIFIED:{_ical_datetime(event_dt)}",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    body = "\r\n".join(_fold_ical_line(line) for line in lines) + "\r\n"
    etag = '"' + hashlib.sha256(body.encode("utf-8")).hexdigest() + '"'
    last_modified = format_datetime(content_timestamp.astimezone(UTC), usegmt=True)
    return body, etag, last_modified


def subscription_window(today: date | None = None) -> tuple[date, date]:
    anchor = today or date.today()
    return (
        anchor - timedelta(days=DEFAULT_SUBSCRIPTION_PAST_DAYS),
        anchor + timedelta(days=DEFAULT_SUBSCRIPTION_FUTURE_DAYS),
    )


def _load_calendar_tasks(
    conn: DbConn,
    *,
    garden_id: int,
    start: date,
    end: date,
    task_types: list[str],
    include_recent_history: bool,
    today: date | None = None,
) -> list[dict[str, Any]]:
    type_sql, type_params = _in_clause("t.task_type", task_types)
    params: list[Any] = [garden_id, *type_params, start.isoformat(), end.isoformat()]
    status_clauses = [
        "((t.status = 'pending' OR t.status = 'snoozed')"
        " AND COALESCE(t.snoozed_until, t.due_on) >= %s"
        " AND COALESCE(t.snoozed_until, t.due_on) < %s)"
    ]
    if include_recent_history:
        recent_cutoff = max(
            start,
            (today or date.today()) - timedelta(days=DEFAULT_RECENT_HISTORY_DAYS),
        )
        params.extend(
            [
                recent_cutoff.isoformat(),
                end.isoformat(),
                recent_cutoff.isoformat(),
                end.isoformat(),
            ]
        )
        status_clauses.append(
            " OR (t.status = 'completed' AND t.completed_at_ms IS NOT NULL"
            " AND TO_TIMESTAMP(t.completed_at_ms / 1000.0)::date >= %s"
            " AND TO_TIMESTAMP(t.completed_at_ms / 1000.0)::date < %s)"
        )
        status_clauses.append(
            " OR (t.status = 'skipped'"
            " AND TO_TIMESTAMP(t.updated_at_ms / 1000.0)::date >= %s"
            " AND TO_TIMESTAMP(t.updated_at_ms / 1000.0)::date < %s)"
        )
    where_parts = [
        "t.garden_id = %s",
        type_sql,
        "(" + "".join(status_clauses) + ")",
    ]
    rows = conn.execute(
        f"""
        SELECT t.*
        FROM garden_tasks t
        WHERE {" AND ".join(where_parts)}
        ORDER BY COALESCE(t.snoozed_until, t.due_on) ASC, t.updated_at_ms DESC
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _load_task_relations(
    conn: DbConn,
    *,
    garden_id: int,
    task_ids: list[int],
) -> dict[str, dict[int, list[str]]]:
    result: dict[str, dict[int, list[str]]] = {
        "plant_ids": defaultdict(list),
        "plot_ids": defaultdict(list),
    }
    if not task_ids:
        return result
    task_sql, task_params = _in_clause("task_id", task_ids)
    for row in conn.execute(
        f"SELECT task_id, plt_id FROM garden_task_plants WHERE {task_sql} ORDER BY task_id, plt_id",
        task_params,
    ).fetchall():
        result["plant_ids"][int(row["task_id"])].append(str(row["plt_id"]))
    for row in conn.execute(
        (
            "SELECT task_id, plot_id FROM garden_task_plots "
            f"WHERE {task_sql} ORDER BY task_id, plot_id"
        ),
        task_params,
    ).fetchall():
        result["plot_ids"][int(row["task_id"])].append(str(row["plot_id"]))
    _merge_plot_ids_from_plants(
        conn,
        garden_id=garden_id,
        plot_ids_by_owner=result["plot_ids"],
        plant_ids_by_owner=result["plant_ids"],
    )
    return result


def _serialize_task_event(
    row: dict[str, Any],
    relations: dict[str, dict[int, list[str]]],
) -> dict[str, Any]:
    task_id = int(row["id"])
    status = str(row["status"])
    source_key = str(row["task_type"])
    event_date = _task_event_date(row)
    window_hint = _task_window_hint(row)
    return {
        "id": f"task:{row['public_id']}",
        "kind": "task",
        "source_key": source_key,
        "title": str(row["title"]),
        "description": str(row["description"] or ""),
        "start_on": event_date.isoformat(),
        "end_on": (event_date + timedelta(days=1)).isoformat(),
        "all_day": True,
        "status": status,
        "severity": str(row["severity"]),
        "read_only": False,
        "target_type": "task",
        "target_id": str(row["public_id"]),
        "plant_ids": relations["plant_ids"].get(task_id, []),
        "plot_ids": relations["plot_ids"].get(task_id, []),
        "due_on": str(row["due_on"]),
        "snoozed_until": str(row["snoozed_until"]) if row["snoozed_until"] else None,
        "updated_at_ms": int(row["updated_at_ms"] or 0),
        "created_at_ms": int(row["created_at_ms"] or 0),
        "completed_at_ms": int(row["completed_at_ms"] or 0) if row["completed_at_ms"] else None,
        **window_hint,
    }


def _load_manual_events(
    conn: DbConn,
    *,
    garden_id: int,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM garden_calendar_events
        WHERE garden_id = %s
          AND event_on >= %s
          AND event_on < %s
        ORDER BY event_on ASC, updated_at_ms DESC
        """,
        (garden_id, start.isoformat(), end.isoformat()),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_manual_event_plots(
    conn: DbConn,
    event_ids: list[int],
) -> dict[int, list[str]]:
    result: dict[int, list[str]] = defaultdict(list)
    if not event_ids:
        return result
    event_sql, event_params = _in_clause("event_id", event_ids)
    for row in conn.execute(
        (
            "SELECT event_id, plot_id FROM garden_calendar_event_plots "
            f"WHERE {event_sql} ORDER BY event_id, plot_id"
        ),
        event_params,
    ).fetchall():
        result[int(row["event_id"])].append(str(row["plot_id"]))
    return result


def _load_manual_event_plants(
    conn: DbConn,
    event_ids: list[int],
) -> dict[int, list[str]]:
    result: dict[int, list[str]] = defaultdict(list)
    if not event_ids:
        return result
    event_sql, event_params = _in_clause("event_id", event_ids)
    for row in conn.execute(
        (
            "SELECT event_id, plt_id FROM garden_calendar_event_plants "
            f"WHERE {event_sql} ORDER BY event_id, plt_id"
        ),
        event_params,
    ).fetchall():
        result[int(row["event_id"])].append(str(row["plt_id"]))
    return result


def _merge_plot_ids_from_plants(
    conn: DbConn,
    *,
    garden_id: int,
    plot_ids_by_owner: dict[int, list[str]],
    plant_ids_by_owner: dict[int, list[str]],
) -> None:
    plant_ids = sorted(
        {
            plant_id
            for owner_plant_ids in plant_ids_by_owner.values()
            for plant_id in owner_plant_ids
        }
    )
    if not plant_ids:
        return
    plant_sql, plant_params = _in_clause("plt_id", plant_ids)
    plant_plot_ids: dict[str, list[str]] = defaultdict(list)
    for row in conn.execute(
        (
            "SELECT pp.plt_id, pp.plot_id "
            "FROM plot_plants pp "
            "LEFT JOIN plot_ownership po ON po.plot_id = pp.plot_id "
            f"WHERE {plant_sql} "
            "AND ("
            "  po.garden_id = %s "
            "  OR ("
            "    po.garden_id IS NULL "
            "    AND %s = (SELECT id FROM gardens WHERE slug = 'default' LIMIT 1)"
            "  )"
            ") "
            "ORDER BY pp.plt_id, pp.plot_id"
        ),
        [*plant_params, garden_id, garden_id],
    ).fetchall():
        plant_plot_ids[str(row["plt_id"])].append(str(row["plot_id"]))
    for owner_id, owner_plant_ids in plant_ids_by_owner.items():
        merged_plot_ids = plot_ids_by_owner[owner_id]
        seen = set(merged_plot_ids)
        for plant_id in owner_plant_ids:
            for plot_id in plant_plot_ids.get(plant_id, []):
                if plot_id in seen:
                    continue
                seen.add(plot_id)
                merged_plot_ids.append(plot_id)


def _serialize_manual_event(
    row: dict[str, Any],
    *,
    plant_ids: list[str],
    plot_ids: list[str],
) -> dict[str, Any]:
    event_on = date.fromisoformat(str(row["event_on"]))
    return {
        "id": f"manual_event:{row['public_id']}",
        "kind": "manual_event",
        "source_key": "garden_event",
        "title": str(row["title"]),
        "description": str(row["description"] or ""),
        "start_on": event_on.isoformat(),
        "end_on": (event_on + timedelta(days=1)).isoformat(),
        "all_day": True,
        "status": "scheduled",
        "severity": "normal",
        "read_only": False,
        "target_type": "manual_event",
        "target_id": str(row["public_id"]),
        "plant_ids": plant_ids,
        "plot_ids": plot_ids,
        "updated_at_ms": int(row["updated_at_ms"] or 0),
        "created_at_ms": int(row["created_at_ms"] or 0),
    }


def _task_event_date(row: dict[str, Any]) -> date:
    status = str(row["status"])
    if status == "completed" and row["completed_at_ms"]:
        return datetime.fromtimestamp(int(row["completed_at_ms"]) / 1000, tz=UTC).date()
    if status == "skipped":
        return datetime.fromtimestamp(int(row["updated_at_ms"]) / 1000, tz=UTC).date()
    if row["snoozed_until"]:
        return date.fromisoformat(str(row["snoozed_until"]))
    return date.fromisoformat(str(row["due_on"]))


def _task_window_hint(row: dict[str, Any]) -> dict[str, Any]:
    window_start_raw = row.get("window_start_on")
    window_end_raw = row.get("window_end_on")
    if window_start_raw and window_end_raw:
        window_start = date.fromisoformat(str(window_start_raw))
        window_end = date.fromisoformat(str(window_end_raw))
        return {
            "window_start_on": window_start.isoformat(),
            "window_end_on": window_end.isoformat(),
            "window_kind": str(row.get("window_kind") or "recommended"),
            "window_state": window_state_for_range(window_start, window_end),
        }
    offsets = RECOMMENDED_WINDOW_RULES.get(str(row.get("task_type") or "").strip().lower())
    if offsets is None:
        return {}
    anchor = date.fromisoformat(str(row["due_on"]))
    days_before, days_after = offsets
    window_start = anchor - timedelta(days=days_before)
    window_end = anchor + timedelta(days=days_after)
    return {
        "window_start_on": window_start.isoformat(),
        "window_end_on": window_end.isoformat(),
        "window_kind": "recommended",
        "window_state": window_state_for_range(window_start, window_end),
    }


def _load_weather_alerts(
    conn: DbConn,
    *,
    garden_id: int,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM weather_alerts
        WHERE garden_id = %s
          AND dismissed = 0
          AND valid_until >= %s
          AND valid_from < %s
        ORDER BY valid_from ASC, created_at_ms DESC
        """,
        (garden_id, start.isoformat(), end.isoformat()),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_weather_alert_plants(
    conn: DbConn,
    alert_ids: list[int],
) -> dict[int, list[str]]:
    result: dict[int, list[str]] = defaultdict(list)
    if not alert_ids:
        return result
    alert_sql, alert_params = _in_clause("alert_id", alert_ids)
    for row in conn.execute(
        (
            "SELECT alert_id, plt_id FROM weather_alert_plants "
            f"WHERE {alert_sql} ORDER BY alert_id, plt_id"
        ),
        alert_params,
    ).fetchall():
        result[int(row["alert_id"])].append(str(row["plt_id"]))
    return result


def _serialize_weather_alert(row: dict[str, Any], plant_ids: list[str]) -> dict[str, Any]:
    valid_from = date.fromisoformat(str(row["valid_from"]))
    valid_until = date.fromisoformat(str(row["valid_until"]))
    return {
        "id": f"weather_alert:{row['id']}",
        "kind": "weather_alert",
        "source_key": "weather_alert",
        "title": str(row["title"]),
        "description": str(row["description"] or ""),
        "start_on": valid_from.isoformat(),
        "end_on": (valid_until + timedelta(days=1)).isoformat(),
        "all_day": True,
        "status": "active",
        "severity": str(row["severity"]),
        "read_only": True,
        "target_type": "weather_alert",
        "target_id": str(row["id"]),
        "plant_ids": plant_ids,
        "plot_ids": [],
        "valid_from": valid_from.isoformat(),
        "valid_until": valid_until.isoformat(),
        "updated_at_ms": int(row["created_at_ms"] or 0),
        "created_at_ms": int(row["created_at_ms"] or 0),
    }


def _load_plot_zone_codes(
    conn: DbConn,
    *,
    garden_id: int,
) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT p.plot_id, p.zone_code
        FROM plots p
        LEFT JOIN plot_ownership po ON po.plot_id = p.plot_id
        WHERE po.garden_id = %s
           OR (
             po.garden_id IS NULL
             AND %s = (SELECT id FROM gardens WHERE slug = 'default' LIMIT 1)
           )
        """,
        (garden_id, garden_id),
    ).fetchall()
    return {str(row["plot_id"]): str(row["zone_code"] or "").upper() for row in rows}


def _event_sort_key(event: dict[str, Any]) -> tuple[str, int, str]:
    severity_rank = {"high": 0, "critical": 0, "normal": 1, "low": 2}.get(
        str(event.get("severity") or "normal"),
        1,
    )
    return (str(event["start_on"]), severity_rank, str(event["title"]).lower())


def _event_matches_selected_filters(
    event: dict[str, Any],
    *,
    selected_plant_ids: set[str],
    selected_plot_ids: set[str],
    selected_zone_codes: set[str],
    plot_zone_codes: dict[str, str],
) -> bool:
    if str(event.get("kind") or "") == "weather_alert":
        return True
    if selected_plant_ids:
        event_plant_ids = {
            str(plant_id) for plant_id in cast(list[str], event.get("plant_ids") or [])
        }
        if not (event_plant_ids & selected_plant_ids):
            return False
    event_plot_ids = {str(plot_id) for plot_id in cast(list[str], event.get("plot_ids") or [])}
    if selected_plot_ids and not (event_plot_ids & selected_plot_ids):
        return False
    if selected_zone_codes:
        event_zone_codes = {
            plot_zone_codes.get(plot_id, "").upper()
            for plot_id in event_plot_ids
            if plot_zone_codes.get(plot_id)
        }
        if not (event_zone_codes & selected_zone_codes):
            return False
    return True


def _parse_json_array(raw: Any) -> list[str]:
    try:
        payload = json.loads(raw or "[]")
    except TypeError, json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(value) for value in payload]


def _in_clause(column: str, values: list[Any]) -> tuple[str, list[Any]]:
    if not values:
        return f"{column} IN (NULL)", []
    placeholders = ", ".join(["%s"] * len(values))
    return f"{column} IN ({placeholders})", list(values)


def _ical_text(value: str) -> str:
    return (
        str(value)
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\\", "\\\\")
        .replace(";", r"\;")
        .replace(",", r"\,")
        .replace("\n", r"\n")
    )


def _ical_datetime(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _event_description_for_ics(event: dict[str, Any]) -> str:
    lines = [str(event.get("description") or "").strip()]
    if event.get("kind") == "task":
        if event.get("status"):
            lines.append(f"Status: {event['status']}")
        if event.get("due_on"):
            lines.append(f"Due on: {event['due_on']}")
    if event.get("kind") == "weather_alert":
        lines.append(f"Valid: {event['start_on']} to {event['end_on']}")
    plant_ids = event.get("plant_ids") or []
    if plant_ids:
        lines.append("Plants: " + ", ".join(str(value) for value in plant_ids))
    plot_ids = event.get("plot_ids") or []
    if plot_ids:
        lines.append("Plots: " + ", ".join(str(value) for value in plot_ids))
    return "\n".join(line for line in lines if line)


def _fold_ical_line(line: str, limit: int = 75) -> str:
    """Fold an iCalendar content line without splitting UTF-8 characters."""
    if not line:
        return line

    parts: list[str] = []
    chunk: list[str] = []
    chunk_bytes = 0
    chunk_limit = limit
    for character in line:
        character_bytes = len(character.encode("utf-8"))
        if chunk and chunk_bytes + character_bytes > chunk_limit:
            parts.append((" " if parts else "") + "".join(chunk))
            chunk = []
            chunk_bytes = 0
            chunk_limit = limit - 1
        chunk.append(character)
        chunk_bytes += character_bytes
    parts.append((" " if parts else "") + "".join(chunk))
    return "\r\n".join(parts)
