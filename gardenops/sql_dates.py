from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta


def offset_days_iso(days: int, *, today: date | None = None) -> str:
    anchor = today or date.today()
    return (anchor + timedelta(days=days)).isoformat()


def offset_months_iso(months: int, *, today: date | None = None) -> str:
    anchor = today or date.today()
    month_index = anchor.month - 1 + months
    year = anchor.year + (month_index // 12)
    month = (month_index % 12) + 1
    day = min(anchor.day, monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def month_number_sql(column_sql: str) -> str:
    return f"CAST(EXTRACT(MONTH FROM {column_sql}::date) AS INTEGER)"
