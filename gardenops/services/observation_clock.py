from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException


def observation_timezone() -> str:
    name = (
        os.environ.get("GARDENOPS_TIMEZONE", "").strip()
        or os.environ.get("MATRIX_TIMEZONE", "").strip()
        or "Europe/Oslo"
    )
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RuntimeError(f"Invalid observation timezone: {name}") from exc
    return name


def frozen_observation_clock() -> tuple[int, str] | None:
    now_ms = os.environ.get("GARDENOPS_ATTENTION_FROZEN_NOW_MS", "").strip()
    day = os.environ.get("GARDENOPS_ATTENTION_FROZEN_DATE", "").strip()
    if os.environ.get("APP_ENV", "").strip().lower() != "test" or not (now_ms or day):
        return None
    if not now_ms or not day:
        raise RuntimeError("Task frozen clock requires both frozen now_ms and frozen_date")
    try:
        parsed_day = date.fromisoformat(day).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid date: {day}") from exc
    try:
        return int(now_ms), parsed_day
    except ValueError as exc:
        raise RuntimeError("Task frozen clock is invalid") from exc


def observation_today(*, now_ms: int | None = None) -> date:
    zone = ZoneInfo(observation_timezone())
    frozen = frozen_observation_clock()
    if frozen is not None:
        return date.fromisoformat(frozen[1])
    if now_ms is not None:
        return datetime.fromtimestamp(now_ms / 1000, zone).date()
    return datetime.now(zone).date()


def resolve_observation_date(value: str | None, *, now_ms: int | None = None) -> str:
    today = observation_today(now_ms=now_ms)
    if value is None:
        return today.isoformat()
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid occurred_on: {value}") from exc
    if parsed.isoformat() != value:
        raise HTTPException(status_code=422, detail="occurred_on must be YYYY-MM-DD")
    if parsed > today:
        raise HTTPException(status_code=422, detail="occurred_on cannot be in the future")
    return parsed.isoformat()
