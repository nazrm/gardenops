from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import HTTPException

from gardenops.db import DbConn, executemany
from gardenops.router_helpers import generate_public_id
from gardenops.security import AuthContext
from gardenops.services.observation_updates import mark_seen_growing_from_observation

CompletionOutcome = Literal["done", "not_seen_blooming_this_season"]

COMPLETION_EVENT_BY_TASK_TYPE = {
    "observe_bloom": "bloomed",
    "prune": "pruned",
    "fertilize": "fertilized",
}


def is_completion_capture_task(task_type: str) -> bool:
    return task_type in COMPLETION_EVENT_BY_TASK_TYPE


def parse_task_metadata(task_row: dict[str, Any]) -> dict[str, Any]:
    raw = task_row.get("metadata_json")
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def clear_completion_capture_metadata(task_row: dict[str, Any]) -> dict[str, Any]:
    metadata = parse_task_metadata(task_row)
    metadata.pop("completion_journal_entries", None)
    metadata.pop("completion_journal_entry_id", None)
    metadata.pop("completion_capture_original_plant_ids", None)
    return metadata


def append_bloom_not_yet_event(
    *,
    task_row: dict[str, Any],
    snooze_until: str,
    actor_user_id: int | None,
    now_ms: int,
) -> dict[str, Any]:
    metadata = parse_task_metadata(task_row)
    bloom_raw = metadata.setdefault("bloom_observation", {})
    if not isinstance(bloom_raw, dict):
        bloom_raw = {}
        metadata["bloom_observation"] = bloom_raw
    events_raw = bloom_raw.setdefault("not_yet_events", [])
    if not isinstance(events_raw, list):
        events_raw = []
        bloom_raw["not_yet_events"] = events_raw
    previous_action_date = str(task_row.get("snoozed_until") or task_row.get("due_on") or "")
    events_raw.append(
        {
            "action_at_ms": now_ms,
            "previous_action_date": previous_action_date,
            "new_snooze_date": snooze_until,
            "actor_user_id": actor_user_id,
            "source": "task_snooze_policy",
        }
    )
    return metadata


def completion_capture_key(
    *,
    task_public_id: str,
    event_type: str,
    outcome: CompletionOutcome,
    plant_ids: list[str],
) -> str:
    plants_key = ",".join(sorted(plant_ids))
    return f"{task_public_id}:{event_type}:{outcome}:{plants_key}"


def completion_capture_already_recorded(
    *,
    task_row: dict[str, Any],
    task_type: str,
    selected_plant_ids: list[str],
    outcome: CompletionOutcome,
) -> bool:
    if not selected_plant_ids or not is_completion_capture_task(task_type):
        return False
    event_type = COMPLETION_EVENT_BY_TASK_TYPE[task_type]
    if task_type == "observe_bloom" and outcome == "not_seen_blooming_this_season":
        event_type = "observed"
    metadata = parse_task_metadata(task_row)
    completion_records = metadata.get("completion_journal_entries")
    if not isinstance(completion_records, dict):
        return False
    key = completion_capture_key(
        task_public_id=str(task_row["public_id"]),
        event_type=event_type,
        outcome=outcome,
        plant_ids=selected_plant_ids,
    )
    existing = completion_records.get(key)
    return isinstance(existing, str) and bool(existing)


def remaining_plant_ids_after_completion(
    *,
    linked_plant_ids: list[str],
    completed_plant_ids: list[str],
) -> list[str]:
    completed = set(completed_plant_ids)
    return [plant_id for plant_id in linked_plant_ids if plant_id not in completed]


def update_task_plant_links(
    db: DbConn,
    *,
    task_id: int,
    remaining_plant_ids: list[str],
) -> None:
    db.execute("DELETE FROM garden_task_plants WHERE task_id = %s", (task_id,))
    executemany(
        db,
        "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
        [(task_id, plant_id) for plant_id in remaining_plant_ids],
    )


def current_plot_ids_for_plant_ids(
    db: DbConn,
    *,
    garden_id: int,
    plant_ids: list[str],
) -> list[str]:
    """Return current placements for selected plants in one garden."""
    if not plant_ids:
        return []
    rows = db.execute(
        """
        SELECT DISTINCT pp.plot_id
        FROM plot_plants pp
        JOIN plots p ON p.plot_id = pp.plot_id
        WHERE p.garden_id = %s
          AND pp.plt_id = ANY(%s)
        ORDER BY pp.plot_id
        """,
        (garden_id, plant_ids),
    ).fetchall()
    return [str(row["plot_id"]) for row in rows]


def update_task_plot_links(
    db: DbConn,
    *,
    task_id: int,
    remaining_plot_ids: list[str],
) -> None:
    db.execute("DELETE FROM garden_task_plots WHERE task_id = %s", (task_id,))
    executemany(
        db,
        "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, %s)",
        [(task_id, plot_id) for plot_id in sorted(set(remaining_plot_ids))],
    )


def plant_names_for_ids(db: DbConn, plant_ids: list[str]) -> list[str]:
    if not plant_ids:
        return []
    placeholders = ",".join(["%s"] * len(plant_ids))
    rows = db.execute(
        f"SELECT plt_id, name FROM plants WHERE plt_id IN ({placeholders})",  # noqa: S608
        plant_ids,
    ).fetchall()
    names_by_id = {str(row["plt_id"]): str(row["name"]) for row in rows}
    return [names_by_id[plant_id] for plant_id in plant_ids if plant_id in names_by_id]


def refreshed_group_title(task_type: str, remaining_names: list[str]) -> str:
    count = len(remaining_names)
    prefix = "Prune" if task_type == "prune" else "Fertilize"
    if count == 1:
        return f"{prefix}: {remaining_names[0]}"
    return f"{prefix} {count} plants"


def validate_completed_plant_ids(
    *,
    task_type: str,
    linked_plant_ids: list[str],
    requested_plant_ids: list[str] | None,
) -> list[str]:
    if requested_plant_ids and not is_completion_capture_task(task_type):
        raise HTTPException(
            status_code=422,
            detail="completed_plant_ids is only supported for task types with completion capture",
        )
    if not is_completion_capture_task(task_type):
        return []
    requested = []
    seen = set()
    for raw in requested_plant_ids or []:
        value = str(raw).strip()
        if value and value not in seen:
            requested.append(value)
            seen.add(value)
    linked = set(linked_plant_ids)
    invalid = [plant_id for plant_id in requested if plant_id not in linked]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"completed_plant_ids must be linked to the task: {', '.join(invalid[:5])}",
        )
    if not linked_plant_ids:
        return []
    if len(linked_plant_ids) == 1 and requested_plant_ids is None:
        return linked_plant_ids
    if not requested:
        raise HTTPException(
            status_code=422,
            detail="completed_plant_ids is required for grouped horticultural completion",
        )
    return requested


def validate_completion_outcome(*, task_type: str, outcome: CompletionOutcome) -> None:
    if outcome == "not_seen_blooming_this_season" and task_type != "observe_bloom":
        raise HTTPException(
            status_code=422,
            detail=(
                "completion_outcome not_seen_blooming_this_season is only valid for "
                "observe_bloom tasks"
            ),
        )


def record_completion_journal_entry(
    db: DbConn,
    *,
    context: AuthContext,
    task_row: dict[str, Any],
    selected_plant_ids: list[str],
    selected_plot_ids: list[str],
    outcome: CompletionOutcome,
    notes: str | None,
    now_ms: int,
    occurred_on: str,
) -> tuple[str | None, dict[str, Any]]:
    task_type = str(task_row.get("task_type") or "")
    if not selected_plant_ids or not is_completion_capture_task(task_type):
        return None, parse_task_metadata(task_row)

    event_type = COMPLETION_EVENT_BY_TASK_TYPE[task_type]
    if task_type == "observe_bloom" and outcome == "not_seen_blooming_this_season":
        event_type = "observed"

    metadata = parse_task_metadata(task_row)
    completion_records_raw = metadata.setdefault("completion_journal_entries", {})
    if not isinstance(completion_records_raw, dict):
        completion_records_raw = {}
        metadata["completion_journal_entries"] = completion_records_raw
    completion_records: dict[str, Any] = completion_records_raw
    key = completion_capture_key(
        task_public_id=str(task_row["public_id"]),
        event_type=event_type,
        outcome=outcome,
        plant_ids=selected_plant_ids,
    )
    existing = completion_records.get(key)
    if isinstance(existing, str) and existing:
        return existing, metadata

    entry_metadata = {
        "source": "task_completion",
        "source_task_id": str(task_row["public_id"]),
        "source_task_type": task_type,
        "outcome": outcome,
        "selected_plant_ids": selected_plant_ids,
    }
    title = ""
    if outcome == "not_seen_blooming_this_season":
        title = "Not seen blooming this season"
    row = db.execute(
        """
        INSERT INTO garden_journal_entries
            (public_id, garden_id, event_type, occurred_on, title, notes,
             metadata_json, actor_user_id, created_at_ms, updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, public_id
        """,
        (
            generate_public_id("jrn"),
            int(task_row["garden_id"]),
            event_type,
            occurred_on,
            title,
            notes or "",
            json.dumps(entry_metadata, sort_keys=True, separators=(",", ":")),
            context.user_id,
            now_ms,
            now_ms,
        ),
    ).fetchone()
    assert row is not None
    entry_id = int(row["id"])
    entry_public_id = str(row["public_id"])
    executemany(
        db,
        "INSERT INTO garden_journal_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
        [(entry_id, plant_id) for plant_id in selected_plant_ids],
    )
    if task_type != "observe_bloom":
        executemany(
            db,
            "INSERT INTO garden_journal_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
            [(entry_id, plot_id) for plot_id in selected_plot_ids],
        )
    if task_type == "observe_bloom" and outcome == "done":
        mark_seen_growing_from_observation(
            db,
            garden_id=int(task_row["garden_id"]),
            plant_ids=selected_plant_ids,
            seen_date=occurred_on,
            # Completion selects plants, not a particular assignment. The
            # observation helper updates an assignment only when it is unique.
            plot_ids=None,
        )
    completion_records[key] = entry_public_id
    if task_type == "observe_bloom" and outcome == "done":
        metadata["completion_journal_entry_id"] = entry_public_id
    return entry_public_id, metadata
