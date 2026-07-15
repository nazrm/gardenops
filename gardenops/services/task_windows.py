"""Helpers for persisted recommended task scheduling windows."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

WindowKind = Literal["recommended", "manual"]
WindowState = Literal["upcoming", "active", "elapsed"]

RECOMMENDED_WINDOW_RULES: dict[str, tuple[int, int]] = {
    "prune": (21, 14),
    "fertilize": (7, 7),
    "sow": (10, 5),
    "plant_out": (5, 7),
    "harvest": (4, 7),
}


def derive_recommended_window(
    task_type: str,
    due_on: date,
) -> tuple[date, date] | None:
    offsets = RECOMMENDED_WINDOW_RULES.get(str(task_type or "").strip().lower())
    if offsets is None:
        return None
    days_before, days_after = offsets
    return (
        due_on - timedelta(days=days_before),
        due_on + timedelta(days=days_after),
    )


def derive_recommended_window_strings(
    task_type: str,
    due_on: str,
) -> tuple[str, str] | None:
    start_end = derive_recommended_window(task_type, date.fromisoformat(due_on))
    if start_end is None:
        return None
    start_on, end_on = start_end
    return (start_on.isoformat(), end_on.isoformat())


def weekly_watering_recurrence_deadline(rule_source: str) -> str | None:
    """Return the last date before a generated weekly watering recurrence."""
    source = str(rule_source or "").strip()
    if not source.startswith("water:"):
        return None
    try:
        recurrence_date = date.fromisoformat(source.rsplit(":", 1)[-1])
    except ValueError:
        return None
    return (recurrence_date + timedelta(days=6)).isoformat()


def window_state_for_range(
    start_on: date,
    end_on: date,
    *,
    today: date | None = None,
) -> WindowState:
    current = today or date.today()
    if current < start_on:
        return "upcoming"
    if current > end_on:
        return "elapsed"
    return "active"
