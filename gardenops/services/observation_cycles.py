from __future__ import annotations

from datetime import date


def observation_year(raw_date: str | None) -> int | None:
    if raw_date is None:
        return None
    text = raw_date.strip()
    if len(text) < 4 or not text[:4].isdigit():
        return None
    return int(text[:4])


def is_current_observation_year(
    raw_date: str | None,
    *,
    today: date | None = None,
) -> bool:
    year = observation_year(raw_date)
    if year is None:
        return False
    current_year = (today or date.today()).year
    return year == current_year
