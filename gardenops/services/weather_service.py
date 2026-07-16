"""Weather data fetching and alert generation for garden-aware guidance."""

from __future__ import annotations

import json
import logging
import math
import os
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta

from gardenops.branding import app_user_agent
from gardenops.db import DbConn, current_timestamp_ms
from gardenops.services.attention.types import attention_request_clock

_logger = logging.getLogger(__name__)


def _external_forecast_fetch_allowed() -> bool:
    """Return whether this process may make a remote weather-provider request."""
    configured = os.environ.get("GARDENOPS_WEATHER_EXTERNAL_FETCH_ENABLED", "").strip()
    if configured:
        return configured.lower() in {"1", "true", "yes", "on"}
    return os.environ.get("APP_ENV", "").strip().lower() != "test"


def _weather_timestamp_ms() -> int:
    """Use the attention test clock for weather state written during test runs."""
    now_ms, _frozen_date = attention_request_clock(now_ms=current_timestamp_ms())
    return now_ms


def _record_lifecycle_transition(
    metadata: dict[str, object],
    transition: dict[str, object],
) -> None:
    current = metadata.get("lifecycle")
    history = metadata.get("lifecycle_history")
    transitions = list(history) if isinstance(history, list) else []
    if isinstance(current, dict) and current != transition:
        transitions.append(dict(current))
    if transitions:
        metadata["lifecycle_history"] = transitions[-20:]
    metadata["lifecycle"] = transition


# RHS hardiness to minimum temperature tolerance (deg C)
# H1: >15 (tender), H2: 1-5, H3: -5 to 1, H4: -10 to -5, H5: -15 to -10
# H6: -20 to -15, H7: <-20 (fully hardy)
_HARDINESS_MIN_TEMP: dict[str, float] = {
    "H1": 15.0,
    "H2": 1.0,
    "H3": -5.0,
    "H4": -10.0,
    "H5": -15.0,
    "H6": -20.0,
    "H7": -20.0,
}

CACHE_TTL_MS = 3 * 60 * 60 * 1000  # 3 hours
_MET_PRECIPITATION_WINDOWS = (
    ("next_1_hours", 1),
    ("next_6_hours", 6),
    ("next_12_hours", 12),
)
_FORECAST_ALERT_TYPES_BY_FIELD = {
    "temperature_2m_min": ("frost_warning",),
    "temperature_2m_max": ("heat_wave",),
    "precipitation_sum": ("dry_spell", "rain_surplus"),
}
_FORECAST_ALERT_TYPES = frozenset(
    alert_type
    for alert_types in _FORECAST_ALERT_TYPES_BY_FIELD.values()
    for alert_type in alert_types
)
_FORECAST_RECONCILIATION_MIN_DAYS = {
    "frost_warning": 1,
    "heat_wave": 3,
    "dry_spell": 5,
    "rain_surplus": 3,
}
_FORECAST_RECONCILIATION_SCOPE_KEY = "_forecast_reconciliation_scope"
_DAILY_COVERAGE_KEY = "daily_coverage"
_CACHE_STATUS_KEY = "_weather_cache_status"
_CACHE_LOCK_NAMESPACE = 1_465_145_172


def _parse_hardiness(raw: str) -> str | None:
    """Extract RHS hardiness code (H1-H7) from raw string."""
    if not raw:
        return None
    raw = raw.strip().upper()
    for code in ("H1", "H2", "H3", "H4", "H5", "H6", "H7"):
        if code in raw:
            return code
    return None


def _min_temp_for_hardiness(code: str) -> float:
    """Return minimum safe temp for a hardiness code."""
    return _HARDINESS_MIN_TEMP.get(code, -20.0)


def _met_precipitation_window(entry: dict) -> tuple[float, int] | None:
    """Return the shortest available MET precipitation window for an entry."""
    data = entry.get("data", {})
    for field, hours in _MET_PRECIPITATION_WINDOWS:
        raw_amount = data.get(field, {}).get("details", {}).get("precipitation_amount")
        if raw_amount is None:
            continue
        try:
            return float(raw_amount), hours
        except TypeError, ValueError:
            continue
    return None


def _met_entry_timestamp(entry: dict) -> datetime | None:
    raw_timestamp = entry.get("time")
    if not isinstance(raw_timestamp, str):
        return None
    try:
        return datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_frost_vulnerable_at_temperature(hardiness: str | None, min_temp: float) -> bool:
    """Return whether a plant's known hardiness is unsafe at ``min_temp``."""
    code = _parse_hardiness(hardiness or "")
    return code is not None and min_temp < _min_temp_for_hardiness(code)


def _aggregate_met_timeseries(raw: dict) -> dict:
    """Convert MET Norway hourly timeseries to daily aggregates.

    Returns the same shape as the old Open-Meteo daily response so
    downstream code (analyze_forecast, router summary) stays unchanged.
    """
    timeseries = raw.get("properties", {}).get("timeseries", [])
    if not timeseries:
        return {}

    by_date: dict[str, list[dict]] = defaultdict(list)
    for entry in timeseries:
        day = entry["time"][:10]
        by_date[day].append(entry)

    dates = sorted(by_date.keys())[:7]
    temp_min_list: list[float | None] = []
    temp_max_list: list[float | None] = []
    precip_sum_list: list[float | None] = []
    wind_max_list: list[float | None] = []

    precipitation_by_date = {date: 0.0 for date in dates}
    precipitation_complete = {date: True for date in dates}
    precipitation_covered_until: datetime | None = None
    precipitation_entries = sorted(
        (entry for date in dates for entry in by_date[date]),
        key=lambda entry: str(entry.get("time", "")),
    )
    for entry in precipitation_entries:
        day = str(entry.get("time", ""))[:10]
        timestamp = _met_entry_timestamp(entry)
        window = _met_precipitation_window(entry)
        if timestamp is None or window is None:
            if timestamp is None or (
                precipitation_covered_until is None or timestamp >= precipitation_covered_until
            ):
                precipitation_complete[day] = False
            continue
        if precipitation_covered_until is not None and timestamp < precipitation_covered_until:
            continue
        amount, window_hours = window
        window_end = timestamp + timedelta(hours=window_hours)
        segment_start = timestamp
        while segment_start < window_end:
            next_midnight = datetime.combine(
                segment_start.date() + timedelta(days=1),
                datetime.min.time(),
                tzinfo=segment_start.tzinfo,
            )
            segment_end = min(window_end, next_midnight)
            segment_date = segment_start.date().isoformat()
            if segment_date in precipitation_by_date:
                segment_hours = (segment_end - segment_start).total_seconds() / 3600
                precipitation_by_date[segment_date] += amount * segment_hours / window_hours
            segment_start = segment_end
        precipitation_covered_until = window_end

    for day in dates:
        entries = by_date[day]
        temps = [
            e["data"]["instant"]["details"]["air_temperature"]
            for e in entries
            if "air_temperature" in e["data"].get("instant", {}).get("details", {})
        ]
        temp_min_list.append(min(temps) if temps else None)
        temp_max_list.append(max(temps) if temps else None)

        precip_sum_list.append(
            precipitation_by_date[day] if precipitation_complete[day] else None,
        )

        winds = [
            e["data"]["instant"]["details"]["wind_speed"]
            for e in entries
            if "wind_speed" in e["data"].get("instant", {}).get("details", {})
        ]
        wind_max_list.append(max(winds) if winds else None)

    # Locationforecast responses can begin and end in the middle of a calendar
    # day. Interior buckets are the only dates bounded by provider data on both
    # sides, so retain that conservative coverage separately from display data.
    complete_dates: list[str] = []
    try:
        parsed_dates = [date.fromisoformat(day) for day in dates]
    except ValueError:
        parsed_dates = []
    if parsed_dates and all(
        current == previous + timedelta(days=1)
        for previous, current in zip(parsed_dates, parsed_dates[1:])
    ):
        complete_dates = dates[1:-1]

    return {
        "daily": {
            "time": dates,
            "temperature_2m_min": temp_min_list,
            "temperature_2m_max": temp_max_list,
            "precipitation_sum": precip_sum_list,
            "precipitation_probability_max": [None] * len(dates),
            "wind_speed_10m_max": wind_max_list,
        },
        _DAILY_COVERAGE_KEY: {"complete_dates": complete_dates},
    }


def fetch_forecast(latitude: float, longitude: float) -> dict:
    """Fetch forecast from MET Norway (Yr) Locationforecast 2.0.

    Returns daily aggregates in the same shape consumed by
    analyze_forecast() and the weather router.
    """
    if not _external_forecast_fetch_allowed():
        _logger.info("Weather forecast fetch skipped because external network access is disabled")
        return {}

    lat = round(latitude, 4)
    lon = round(longitude, 4)
    url = f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={lat}&lon={lon}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": app_user_agent("weather-service")},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        forecast = _aggregate_met_timeseries(raw)
        if not _is_valid_forecast_payload(forecast):
            _logger.warning("MET Norway forecast response was malformed")
            return {}
        return forecast
    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        AttributeError,
        IndexError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        _logger.warning("MET Norway forecast fetch failed: %s", exc)
        return {}


def _is_valid_forecast_payload(forecast: object) -> bool:
    """Return whether a provider forecast is safe to display and cache."""
    if not isinstance(forecast, dict):
        return False
    daily = forecast.get("daily")
    dates = _validated_contiguous_forecast_dates(forecast)
    if not isinstance(daily, dict) or dates is None or len(dates) > 7:
        return False

    numeric_fields = (
        "temperature_2m_min",
        "temperature_2m_max",
        "precipitation_sum",
        "precipitation_probability_max",
        "wind_speed_10m_max",
    )
    normalized: dict[str, list[float | None]] = {}
    for field in numeric_fields:
        values = daily.get(field)
        if not isinstance(values, list) or len(values) != len(dates):
            return False
        field_values: list[float | None] = []
        for value in values:
            if value is None:
                field_values.append(None)
                continue
            if isinstance(value, bool) or not isinstance(value, int | float):
                return False
            numeric_value = float(value)
            if not math.isfinite(numeric_value):
                return False
            field_values.append(numeric_value)
        normalized[field] = field_values

    minimums = normalized["temperature_2m_min"]
    maximums = normalized["temperature_2m_max"]
    if not any(value is not None for value in minimums + maximums):
        return False
    if any(
        minimum is not None and maximum is not None and minimum > maximum
        for minimum, maximum in zip(minimums, maximums)
    ):
        return False
    if any(
        value is not None and value < 0
        for field in ("precipitation_sum", "wind_speed_10m_max")
        for value in normalized[field]
    ):
        return False
    if any(
        value is not None and not 0 <= value <= 100
        for value in normalized["precipitation_probability_max"]
    ):
        return False
    return True


def _cached_forecast_entry(
    db: DbConn,
    garden_id: int,
    *,
    latitude: float | None = None,
    longitude: float | None = None,
) -> tuple[dict, int] | None:
    """Load the latest valid cache entry for the current garden location."""
    row = db.execute(
        """
        SELECT forecast_json, fetched_at_ms, latitude, longitude
        FROM weather_cache
        WHERE garden_id = %s ORDER BY fetched_at_ms DESC LIMIT 1
        """,
        (garden_id,),
    ).fetchone()
    if not row:
        return None
    if latitude is not None and longitude is not None:
        if (
            abs(float(row["latitude"]) - latitude) > 0.000_001
            or abs(float(row["longitude"]) - longitude) > 0.000_001
        ):
            return None
    try:
        forecast = json.loads(row["forecast_json"])
    except json.JSONDecodeError, TypeError:
        return None
    if not _is_valid_forecast_payload(forecast):
        return None
    return forecast, int(row["fetched_at_ms"])


def _with_cache_status(
    forecast: dict,
    *,
    fetched_at_ms: int,
    now_ms: int,
    source: str,
    fallback: bool,
) -> dict:
    result = dict(forecast)
    age_ms = max(0, now_ms - fetched_at_ms)
    result[_CACHE_STATUS_KEY] = {
        "source": source,
        "fetched_at_ms": fetched_at_ms,
        "age_ms": age_ms,
        "stale": age_ms >= CACHE_TTL_MS,
        "fallback": fallback,
    }
    return result


def forecast_cache_status(forecast: dict | None) -> dict[str, object]:
    """Return cache trust metadata carried by a loaded forecast."""
    if not isinstance(forecast, dict):
        return {}
    status = forecast.get(_CACHE_STATUS_KEY)
    return dict(status) if isinstance(status, dict) else {}


def forecast_without_cache_status(forecast: dict) -> dict:
    """Return provider data without internal cache bookkeeping."""
    return {key: value for key, value in forecast.items() if key != _CACHE_STATUS_KEY}


def get_cached_forecast(
    db: DbConn,
    garden_id: int,
    *,
    allow_stale: bool = False,
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict | None:
    """Return a valid cached forecast, optionally including stale display data."""
    now = _weather_timestamp_ms()
    cached = _cached_forecast_entry(
        db,
        garden_id,
        latitude=latitude,
        longitude=longitude,
    )
    if cached is None:
        return None
    forecast, fetched_at_ms = cached
    if not allow_stale and now - fetched_at_ms >= CACHE_TTL_MS:
        return None
    return _with_cache_status(
        forecast,
        fetched_at_ms=fetched_at_ms,
        now_ms=now,
        source="cache",
        fallback=False,
    )


def save_forecast_cache(
    db: DbConn,
    garden_id: int,
    latitude: float,
    longitude: float,
    forecast: dict,
) -> None:
    """Save forecast to cache, removing old entries."""
    now = _weather_timestamp_ms()
    db.execute(
        "SELECT pg_advisory_xact_lock(%s, %s)",
        (_CACHE_LOCK_NAMESPACE, garden_id),
    )
    db.execute("DELETE FROM weather_cache WHERE garden_id = %s", (garden_id,))
    db.execute(
        """
        INSERT INTO weather_cache (garden_id, fetched_at_ms, forecast_json, latitude, longitude)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            garden_id,
            now,
            json.dumps(forecast_without_cache_status(forecast)),
            latitude,
            longitude,
        ),
    )


def get_or_fetch_forecast(
    db: DbConn,
    garden_id: int,
    latitude: float,
    longitude: float,
) -> dict:
    """Get a fresh forecast, falling back to valid stale display data."""
    cached = get_cached_forecast(
        db,
        garden_id,
        latitude=latitude,
        longitude=longitude,
    )
    if cached:
        return cached
    stale = get_cached_forecast(
        db,
        garden_id,
        allow_stale=True,
        latitude=latitude,
        longitude=longitude,
    )
    if not _external_forecast_fetch_allowed():
        _logger.info("Weather forecast unavailable because external network access is disabled")
        if stale:
            status = forecast_cache_status(stale)
            return _with_cache_status(
                forecast_without_cache_status(stale),
                fetched_at_ms=int(status["fetched_at_ms"]),
                now_ms=_weather_timestamp_ms(),
                source="cache",
                fallback=True,
            )
        return {}
    forecast = fetch_forecast(latitude, longitude)
    if _is_valid_forecast_payload(forecast):
        save_forecast_cache(db, garden_id, latitude, longitude, forecast)
        now = _weather_timestamp_ms()
        return _with_cache_status(
            forecast,
            fetched_at_ms=now,
            now_ms=now,
            source="provider",
            fallback=False,
        )
    if stale:
        status = forecast_cache_status(stale)
        return _with_cache_status(
            forecast_without_cache_status(stale),
            fetched_at_ms=int(status["fetched_at_ms"]),
            now_ms=_weather_timestamp_ms(),
            source="cache",
            fallback=True,
        )
    return {}


def _validated_contiguous_forecast_dates(forecast: dict) -> list[str] | None:
    daily = forecast.get("daily")
    if not isinstance(daily, dict):
        return None
    dates = daily.get("time")
    if (
        not isinstance(dates, list)
        or not dates
        or any(not isinstance(value, str) or not value for value in dates)
    ):
        return None
    try:
        parsed_dates = [date.fromisoformat(value) for value in dates]
    except ValueError:
        return None
    if any(
        current != previous + timedelta(days=1)
        for previous, current in zip(parsed_dates, parsed_dates[1:])
    ):
        return None
    return list(dates)


def _validated_complete_forecast_dates(forecast: dict) -> list[str] | None:
    """Return dates safe for authoritative weather work.

    Forecasts without MET coverage metadata retain the legacy contract: their
    validated daily dates are authoritative. A MET response must explicitly
    identify a contiguous subset of complete dates; malformed coverage is
    treated as no authority rather than widening destructive reconciliation.
    """
    dates = _validated_contiguous_forecast_dates(forecast)
    if dates is None:
        return None
    coverage = forecast.get(_DAILY_COVERAGE_KEY)
    if coverage is None:
        return dates
    if not isinstance(coverage, dict):
        return []
    raw_dates = coverage.get("complete_dates")
    if not isinstance(raw_dates, list):
        return []
    if not raw_dates:
        return []
    if any(not isinstance(value, str) for value in raw_dates):
        return []
    try:
        parsed_dates = [date.fromisoformat(value) for value in raw_dates]
    except ValueError:
        return []
    if len(set(raw_dates)) != len(raw_dates) or any(
        current != previous + timedelta(days=1)
        for previous, current in zip(parsed_dates, parsed_dates[1:])
    ):
        return []
    known_dates = set(dates)
    if any(value not in known_dates for value in raw_dates):
        return []
    return list(raw_dates)


def _complete_forecast_alert_types(forecast: dict) -> set[str]:
    """Return alert families backed by a complete daily forecast series.

    A missing value means the provider has not authoritatively said that the
    corresponding weather condition is absent. Those families may still have
    persisted alerts and generated tasks, so they must be excluded from
    destructive reconciliation.
    """
    daily = forecast.get("daily")
    all_dates = _validated_contiguous_forecast_dates(forecast)
    dates = _validated_complete_forecast_dates(forecast)
    if not isinstance(daily, dict) or all_dates is None or not dates:
        return set()
    indexes = [all_dates.index(day) for day in dates]

    complete_types: set[str] = set()
    for field, alert_types in _FORECAST_ALERT_TYPES_BY_FIELD.items():
        values = daily.get(field)
        if not isinstance(values, list) or len(values) != len(all_dates):
            continue
        try:
            selected_values = [values[index] for index in indexes]
            normalized_values = [float(value) for value in selected_values]
        except TypeError, ValueError:
            continue
        if all(not isinstance(value, bool) for value in selected_values) and all(
            math.isfinite(value) for value in normalized_values
        ):
            complete_types.update(alert_types)
    return complete_types


def _forecast_reconciliation_coverage_bounds(
    forecast: dict,
    complete_alert_types: set[str],
) -> dict[str, tuple[str, str]]:
    """Return safe reconciliation bounds for each fully observed alert family."""
    dates = _validated_complete_forecast_dates(forecast)
    if not dates:
        return {}
    bounds: dict[str, tuple[str, str]] = {}
    for alert_type in complete_alert_types:
        minimum_days = _FORECAST_RECONCILIATION_MIN_DAYS[alert_type]
        if len(dates) >= minimum_days:
            bounds[alert_type] = (dates[0], dates[-1])
    return bounds


def _forecast_with_complete_daily_families(
    forecast: dict,
    complete_alert_types: set[str],
) -> dict:
    """Return a numeric daily forecast with incomplete families omitted.

    ``analyze_forecast`` predates provider partial-response handling and
    assumes numeric series. Restricting analysis to complete families means a
    malformed precipitation series cannot prevent a complete temperature
    family from being processed, or turn into an accidental reconciliation.
    """
    daily = dict(forecast["daily"])
    all_dates = _validated_contiguous_forecast_dates(forecast)
    complete_dates = _validated_complete_forecast_dates(forecast)
    if all_dates is None or complete_dates is None:
        return {**forecast, "daily": {**daily, "time": []}}
    indexes = [all_dates.index(day) for day in complete_dates]
    daily["time"] = complete_dates
    for field, alert_types in _FORECAST_ALERT_TYPES_BY_FIELD.items():
        if not set(alert_types).issubset(complete_alert_types):
            daily[field] = []
            continue
        daily[field] = [float(daily[field][index]) for index in indexes]
    return {**forecast, "daily": daily}


def _forecast_reconciliation_scope_marker(
    complete_alert_types: set[str],
    coverage_bounds: dict[str, tuple[str, str]],
) -> dict[str, object]:
    """Carry forecast completeness through callers that only pass alert rows.

    The weather routes intentionally hand a plain alerts list to downstream
    reconciliation. This non-alert marker preserves completeness and validated
    date bounds without changing that route contract; notification
    reconciliation removes it before any alert work is performed.
    """
    return {
        "alert_type": "",
        "valid_from": "",
        _FORECAST_RECONCILIATION_SCOPE_KEY: {
            "complete_alert_types": sorted(complete_alert_types),
            "coverage_bounds": {
                alert_type: {"start": start, "end": end}
                for alert_type, (start, end) in sorted(coverage_bounds.items())
            },
        },
    }


def analyze_forecast(forecast: dict) -> list[dict]:
    """Analyze forecast data and return alert conditions.

    Returns list of dicts with keys: alert_type, severity, title, description,
    valid_from, valid_until, metadata.
    """
    alerts: list[dict] = []
    daily = forecast.get("daily", {})
    dates = daily.get("time", [])
    temp_min = daily.get("temperature_2m_min", [])
    temp_max = daily.get("temperature_2m_max", [])
    precip = daily.get("precipitation_sum", [])

    if not dates:
        return alerts

    # Frost warning: any day with min temp <= 0 deg C
    frost_days = []
    for i, d in enumerate(dates):
        if i < len(temp_min) and temp_min[i] is not None and temp_min[i] <= 0:
            frost_days.append((d, temp_min[i]))

    if frost_days:
        coldest = min(frost_days, key=lambda x: x[1])
        severity = "high" if coldest[1] <= -5 else "normal" if coldest[1] <= -2 else "low"
        alerts.append(
            {
                "alert_type": "frost_warning",
                "severity": severity,
                "title": f"Frost warning: {coldest[1]:.0f}\u00b0C expected",
                "description": (
                    f"Frost expected on {len(frost_days)} day(s). "
                    f"Coldest: {coldest[1]:.1f}\u00b0C on {coldest[0]}. "
                    f"Protect tender plants."
                ),
                "valid_from": frost_days[0][0],
                "valid_until": frost_days[-1][0],
                "metadata": {
                    "frost_days": frost_days,
                    "coldest": coldest[1],
                    "coldest_date": coldest[0],
                },
            }
        )

    # Heat wave: 3+ consecutive days with max temp >= 30 deg C
    heat_streak = 0
    heat_start: str | None = None
    heat_end: str | None = None
    peak_temp = 0.0
    for i, d in enumerate(dates):
        if i < len(temp_max) and temp_max[i] is not None and temp_max[i] >= 30:
            if heat_streak == 0:
                heat_start = d
            heat_streak += 1
            heat_end = d
            peak_temp = max(peak_temp, temp_max[i])
        else:
            if heat_streak >= 3 and heat_start and heat_end:
                alerts.append(
                    {
                        "alert_type": "heat_wave",
                        "severity": "high" if peak_temp >= 35 else "normal",
                        "title": f"Heat wave: {peak_temp:.0f}\u00b0C peak",
                        "description": (
                            f"{heat_streak} consecutive hot days "
                            f"({heat_start} to {heat_end}). "
                            f"Increase watering and provide shade for sensitive plants."
                        ),
                        "valid_from": heat_start,
                        "valid_until": heat_end,
                        "metadata": {"days": heat_streak, "peak": peak_temp},
                    }
                )
            heat_streak = 0
            heat_start = None
            peak_temp = 0.0
    # Check trailing streak
    if heat_streak >= 3 and heat_start and heat_end:
        alerts.append(
            {
                "alert_type": "heat_wave",
                "severity": "high" if peak_temp >= 35 else "normal",
                "title": f"Heat wave: {peak_temp:.0f}\u00b0C peak",
                "description": (f"{heat_streak} consecutive hot days. Increase watering."),
                "valid_from": heat_start,
                "valid_until": heat_end,
                "metadata": {"days": heat_streak, "peak": peak_temp},
            }
        )

    # Dry spell: 5+ consecutive days with < 1mm precipitation
    dry_streak = 0
    dry_start: str | None = None
    dry_end: str | None = None
    for i, d in enumerate(dates):
        if i < len(precip) and precip[i] is not None and precip[i] < 1.0:
            if dry_streak == 0:
                dry_start = d
            dry_streak += 1
            dry_end = d
        else:
            if dry_streak >= 5 and dry_start and dry_end:
                alerts.append(
                    {
                        "alert_type": "dry_spell",
                        "severity": "normal" if dry_streak < 7 else "high",
                        "title": f"Dry spell: {dry_streak} days without rain",
                        "description": (
                            f"No significant rain from {dry_start} to {dry_end}. "
                            f"Water regularly, especially newly planted and shallow-rooted plants."
                        ),
                        "valid_from": dry_start,
                        "valid_until": dry_end,
                        "metadata": {"days": dry_streak},
                    }
                )
            dry_streak = 0
            dry_start = None
    if dry_streak >= 5 and dry_start and dry_end:
        alerts.append(
            {
                "alert_type": "dry_spell",
                "severity": "normal" if dry_streak < 7 else "high",
                "title": f"Dry spell: {dry_streak} days without rain",
                "description": "No significant rain expected. Water regularly.",
                "valid_from": dry_start,
                "valid_until": dry_end,
                "metadata": {"days": dry_streak},
            }
        )

    # Rain surplus: 3+ consecutive significant-rain days totaling at least 15mm.
    # Separate intervals keep intervening dry dates available for watering tasks.
    rain_interval: list[tuple[str, float]] = []

    def add_rain_surplus_alert(interval: list[tuple[str, float]]) -> None:
        total_rain = sum(amount for _date, amount in interval)
        if len(interval) < 3 or total_rain < 15.0:
            return
        alerts.append(
            {
                "alert_type": "rain_surplus",
                "severity": "normal" if total_rain < 30 else "high",
                "title": f"Heavy rain expected: {total_rain:.0f}mm",
                "description": (
                    f"Significant rain on {len(interval)} consecutive days "
                    f"(total {total_rain:.0f}mm). Skip watering. "
                    f"Check drainage for waterlogging-sensitive plants."
                ),
                "valid_from": interval[0][0],
                "valid_until": interval[-1][0],
                "metadata": {"rain_days": len(interval), "total_mm": total_rain},
            }
        )

    for i, d in enumerate(dates):
        if i < len(precip) and precip[i] is not None and precip[i] >= 3.0:
            rain_interval.append((d, float(precip[i])))
            continue
        add_rain_surplus_alert(rain_interval)
        rain_interval = []
    add_rain_surplus_alert(rain_interval)

    return alerts


def find_frost_vulnerable_plants(
    db: DbConn,
    garden_id: int,
    min_temp: float,
) -> list[dict]:
    """Find plants vulnerable to a given minimum temperature.

    Uses RHS hardiness codes to determine which plants are at risk.
    """
    plants = db.execute(
        """
        SELECT p.plt_id, p.name, p.hardiness, p.category
        FROM plants p
        JOIN plant_ownership po ON po.plt_id = p.plt_id
        WHERE po.garden_id = %s AND p.hardiness IS NOT NULL AND p.hardiness != ''
        """,
        (garden_id,),
    ).fetchall()

    vulnerable = []
    for p in plants:
        hardiness = str(p["hardiness"])
        code = _parse_hardiness(hardiness)
        if code and is_frost_vulnerable_at_temperature(hardiness, min_temp):
            vulnerable.append(
                {
                    "plt_id": str(p["plt_id"]),
                    "name": str(p["name"]),
                    "hardiness": str(p["hardiness"]),
                    "category": str(p["category"] or ""),
                    "min_safe_temp": _min_temp_for_hardiness(code),
                }
            )
    return vulnerable


def find_watering_sensitive_plants(
    db: DbConn,
    garden_id: int,
) -> list[dict]:
    """Find plants that need regular watering."""
    plants = db.execute(
        """
        SELECT p.plt_id, p.name, p.care_watering
        FROM plants p
        JOIN plant_ownership po ON po.plt_id = p.plt_id
        WHERE po.garden_id = %s
          AND p.care_watering IS NOT NULL AND p.care_watering != ''
        """,
        (garden_id,),
    ).fetchall()

    sensitive = []
    for p in plants:
        watering = str(p["care_watering"]).lower()
        if any(
            kw in watering for kw in ("regular", "often", "jevnlig", "ofte", "mye", "frequently")
        ):
            sensitive.append(
                {
                    "plt_id": str(p["plt_id"]),
                    "name": str(p["name"]),
                    "care_watering": str(p["care_watering"]),
                }
            )
    return sensitive


def save_weather_alerts(
    db: DbConn,
    garden_id: int,
    alerts: list[dict],
    frost_plants: list[dict] | None = None,
    watering_plants: list[dict] | None = None,
) -> dict[str, int]:
    """Links relevant plants to alerts and builds plant_advice in metadata.

    Deduplicates by (garden_id, alert_type, valid_from).
    A same-identity forecast replaces its severity and validity window. Plant
    links are replaced only when the caller supplied that family's complete
    affected-plant set.
    Does not commit; the caller owns the transaction with downstream work.
    Returns {"created": N, "skipped": N}.
    """
    created = 0
    skipped = 0
    now = _weather_timestamp_ms()

    for alert in alerts:
        # Build plant_advice based on alert type
        alert_meta = dict(alert.get("metadata", {}))
        plant_list: list[dict] = []
        alert_type = alert["alert_type"]
        plant_links_authoritative = False

        if alert_type == "frost_warning" and frost_plants is not None:
            plant_list = list(frost_plants)
            plant_links_authoritative = True
            alert_meta["plant_advice"] = [
                {
                    "plt_id": p["plt_id"],
                    "name": p["name"],
                    "hardiness": p.get("hardiness", ""),
                    "min_safe_temp": p.get("min_safe_temp", 0),
                }
                for p in sorted(plant_list, key=lambda plant: str(plant["plt_id"]))
            ]
        elif (
            alert_type in ("heat_wave", "dry_spell", "rain_surplus") and watering_plants is not None
        ):
            plant_list = list(watering_plants)
            plant_links_authoritative = True
            alert_meta["plant_advice"] = [
                {
                    "plt_id": p["plt_id"],
                    "name": p["name"],
                    "care_watering": p.get("care_watering", ""),
                }
                for p in sorted(plant_list, key=lambda plant: str(plant["plt_id"]))
            ]
        if plant_links_authoritative:
            alert_meta["forecast_plant_links_authoritative"] = True

        metadata = json.dumps(alert_meta, default=str)
        wrow = db.execute(
            """
            INSERT INTO weather_alerts
                (garden_id, alert_type, severity, title, description,
                 valid_from, valid_until, metadata_json, created_at_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (garden_id, alert_type, valid_from) DO NOTHING
            RETURNING id
            """,
            (
                garden_id,
                alert["alert_type"],
                alert["severity"],
                alert["title"],
                alert["description"],
                alert["valid_from"],
                alert["valid_until"],
                metadata,
                now,
            ),
        ).fetchone()
        if wrow:
            alert_id = int(wrow["id"])
            created += 1
        else:
            existing = db.execute(
                """
                SELECT id, severity, title, description, valid_until, metadata_json
                FROM weather_alerts
                WHERE garden_id = %s
                  AND alert_type = %s
                  AND valid_from = %s
                FOR UPDATE
                """,
                (garden_id, alert["alert_type"], alert["valid_from"]),
            ).fetchone()
            if existing is None:
                raise RuntimeError("Weather alert identity conflict could not be resolved")
            alert_id = int(existing["id"])
            existing_meta: dict[str, object]
            try:
                parsed_meta = json.loads(str(existing["metadata_json"] or "{}"))
                existing_meta = parsed_meta if isinstance(parsed_meta, dict) else {}
            except TypeError, ValueError, json.JSONDecodeError:
                existing_meta = {}
            merged_meta: dict[str, object] = {**existing_meta, **alert_meta}
            lifecycle = existing_meta.get("lifecycle")
            reappeared = (
                isinstance(lifecycle, dict)
                and lifecycle.get("status") == "resolved"
                and lifecycle.get("resolution_kind") == "automatic_forecast"
            )
            if reappeared:
                _record_lifecycle_transition(
                    merged_meta,
                    {
                        "status": "active",
                        "reason": "reappeared_in_current_forecast",
                        "reappeared_at_ms": now,
                        "source": "forecast_reconciliation",
                    },
                )
                merged_meta["notification_rearm_pending"] = True
            severity_rank = {"low": 0, "normal": 1, "high": 2, "critical": 3}
            existing_severity = str(existing["severity"] or "normal")
            incoming_severity = str(alert["severity"] or "normal")
            severity_escalated = severity_rank.get(
                incoming_severity,
                severity_rank["normal"],
            ) > severity_rank.get(existing_severity, severity_rank["normal"])
            db.execute(
                """
                UPDATE weather_alerts
                SET severity = %s,
                    title = COALESCE(NULLIF(%s, ''), title),
                    description = COALESCE(NULLIF(%s, ''), description),
                    dismissed = 0,
                    valid_until = %s,
                    metadata_json = %s
                WHERE id = %s
                """,
                (
                    incoming_severity,
                    alert["title"],
                    alert["description"],
                    alert["valid_until"],
                    json.dumps(merged_meta, default=str),
                    alert_id,
                ),
            )
            if severity_escalated or reappeared:
                db.execute(
                    """
                    DELETE FROM user_attention_item_state
                    WHERE garden_id = %s
                      AND item_id = %s
                      AND user_state = 'dismissed'
                    """,
                    (garden_id, f"attn:weather:alert:{alert_id}"),
                )
            skipped += 1

        if plant_links_authoritative:
            plant_ids = sorted({str(plant["plt_id"]) for plant in plant_list})
            if plant_ids:
                db.execute(
                    "DELETE FROM weather_alert_plants "
                    "WHERE alert_id = %s AND NOT (plt_id = ANY(%s))",
                    (alert_id, plant_ids),
                )
            else:
                db.execute("DELETE FROM weather_alert_plants WHERE alert_id = %s", (alert_id,))
            for plant_id in plant_ids:
                db.execute(
                    "INSERT INTO weather_alert_plants"
                    " (alert_id, plt_id) VALUES (%s, %s)"
                    " ON CONFLICT DO NOTHING",
                    (alert_id, plant_id),
                )
    return {"created": created, "skipped": skipped}


def check_weather_and_generate_alerts(
    db: DbConn,
    garden_id: int,
    latitude: float,
    longitude: float,
) -> dict:
    """Full pipeline: fetch forecast, analyze, save alerts, return summary.

    Returns {
        "forecast_available": bool,
        "alerts_created": int,
        "alerts_skipped": int,
        "alerts": [...],
        "frost_vulnerable_plants": [...],
        "watering_sensitive_plants": [...],
    }
    """
    forecast = get_or_fetch_forecast(db, garden_id, latitude, longitude)
    if not isinstance(forecast, dict) or not isinstance(forecast.get("daily"), dict):
        return {
            "forecast_available": False,
            "alerts_created": 0,
            "alerts_skipped": 0,
            "alerts": [],
            "forecast_complete_alert_types": [],
            "frost_vulnerable_plants": [],
            "watering_sensitive_plants": [],
        }

    cache_status = forecast_cache_status(forecast)
    if bool(cache_status.get("fallback")) or bool(cache_status.get("stale")):
        return {
            "forecast_available": True,
            "alerts_created": 0,
            "alerts_skipped": 0,
            "alerts": [_forecast_reconciliation_scope_marker(set(), {})],
            "forecast_complete_alert_types": [],
            "frost_vulnerable_plants": [],
            "watering_sensitive_plants": [],
        }

    complete_alert_types = _complete_forecast_alert_types(forecast)
    coverage_bounds = _forecast_reconciliation_coverage_bounds(
        forecast,
        complete_alert_types,
    )
    analysis_forecast = _forecast_with_complete_daily_families(forecast, complete_alert_types)
    alerts = analyze_forecast(analysis_forecast)

    # Find vulnerable plants for frost warnings
    frost_plants: list[dict] = []
    for alert in alerts:
        if alert["alert_type"] == "frost_warning":
            coldest = alert["metadata"].get("coldest", 0)
            frost_plants = find_frost_vulnerable_plants(db, garden_id, coldest)
            break

    # Find watering-sensitive plants for dry/rain alerts
    watering_plants: list[dict] = []
    for alert in alerts:
        if alert["alert_type"] in ("heat_wave", "dry_spell", "rain_surplus"):
            watering_plants = find_watering_sensitive_plants(db, garden_id)
            break

    result = save_weather_alerts(
        db,
        garden_id,
        alerts,
        frost_plants,
        watering_plants,
    )
    reconciliation_alerts = list(alerts)
    reconciliation_alerts.append(
        _forecast_reconciliation_scope_marker(complete_alert_types, coverage_bounds)
    )

    return {
        "forecast_available": True,
        "alerts_created": result["created"],
        "alerts_skipped": result["skipped"],
        "alerts": reconciliation_alerts,
        "forecast_complete_alert_types": sorted(complete_alert_types),
        "frost_vulnerable_plants": frost_plants,
        "watering_sensitive_plants": watering_plants,
    }
