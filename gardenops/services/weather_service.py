"""Weather data fetching and alert generation for garden-aware guidance."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from collections import defaultdict

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
        date = entry["time"][:10]
        by_date[date].append(entry)

    dates = sorted(by_date.keys())[:7]
    temp_min_list: list[float | None] = []
    temp_max_list: list[float | None] = []
    precip_sum_list: list[float | None] = []
    wind_max_list: list[float | None] = []

    for date in dates:
        entries = by_date[date]
        temps = [
            e["data"]["instant"]["details"]["air_temperature"]
            for e in entries
            if "air_temperature" in e["data"].get("instant", {}).get("details", {})
        ]
        temp_min_list.append(min(temps) if temps else None)
        temp_max_list.append(max(temps) if temps else None)

        precip = 0.0
        precipitation_complete = True
        for e in entries:
            p1 = e["data"].get("next_1_hours", {}).get("details", {}).get("precipitation_amount")
            if p1 is None:
                precipitation_complete = False
                break
            precip += p1
        precip_sum_list.append(precip if precipitation_complete else None)

        winds = [
            e["data"]["instant"]["details"]["wind_speed"]
            for e in entries
            if "wind_speed" in e["data"].get("instant", {}).get("details", {})
        ]
        wind_max_list.append(max(winds) if winds else None)

    return {
        "daily": {
            "time": dates,
            "temperature_2m_min": temp_min_list,
            "temperature_2m_max": temp_max_list,
            "precipitation_sum": precip_sum_list,
            "precipitation_probability_max": [None] * len(dates),
            "wind_speed_10m_max": wind_max_list,
        },
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
        return _aggregate_met_timeseries(raw)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        _logger.warning("MET Norway forecast fetch failed: %s", exc)
        return {}


def get_cached_forecast(db: DbConn, garden_id: int) -> dict | None:
    """Return cached forecast if fresh enough, else None."""
    now = _weather_timestamp_ms()
    row = db.execute(
        """
        SELECT forecast_json, fetched_at_ms FROM weather_cache
        WHERE garden_id = %s ORDER BY fetched_at_ms DESC LIMIT 1
        """,
        (garden_id,),
    ).fetchone()
    if row and (now - row["fetched_at_ms"]) < CACHE_TTL_MS:
        try:
            return json.loads(row["forecast_json"])  # type: ignore[no-any-return]
        except (
            json.JSONDecodeError,
            TypeError,
        ):
            return None
    return None


def save_forecast_cache(
    db: DbConn,
    garden_id: int,
    latitude: float,
    longitude: float,
    forecast: dict,
) -> None:
    """Save forecast to cache, removing old entries."""
    now = _weather_timestamp_ms()
    db.execute("DELETE FROM weather_cache WHERE garden_id = %s", (garden_id,))
    db.execute(
        """
        INSERT INTO weather_cache (garden_id, fetched_at_ms, forecast_json, latitude, longitude)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (garden_id, now, json.dumps(forecast), latitude, longitude),
    )
    db.commit()


def get_or_fetch_forecast(
    db: DbConn,
    garden_id: int,
    latitude: float,
    longitude: float,
) -> dict:
    """Get forecast from cache or fetch fresh."""
    cached = get_cached_forecast(db, garden_id)
    if cached:
        return cached
    if not _external_forecast_fetch_allowed():
        _logger.info("Weather forecast unavailable because external network access is disabled")
        return {}
    forecast = fetch_forecast(latitude, longitude)
    if forecast and "daily" in forecast:
        save_forecast_cache(db, garden_id, latitude, longitude, forecast)
    return forecast


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

    # Rain surplus: 3+ days with significant rain (>10mm total)
    rain_days = []
    total_rain = 0.0
    for i, d in enumerate(dates):
        if i < len(precip) and precip[i] is not None and precip[i] >= 3.0:
            rain_days.append((d, precip[i]))
            total_rain += precip[i]

    if len(rain_days) >= 3 and total_rain >= 15.0:
        alerts.append(
            {
                "alert_type": "rain_surplus",
                "severity": "normal" if total_rain < 30 else "high",
                "title": f"Heavy rain expected: {total_rain:.0f}mm",
                "description": (
                    f"Significant rain on {len(rain_days)} days "
                    f"(total {total_rain:.0f}mm). Skip watering. "
                    f"Check drainage for waterlogging-sensitive plants."
                ),
                "valid_from": rain_days[0][0],
                "valid_until": rain_days[-1][0],
                "metadata": {"rain_days": len(rain_days), "total_mm": total_rain},
            }
        )

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

        if alert_type == "frost_warning" and frost_plants:
            plant_list = frost_plants
            alert_meta["plant_advice"] = [
                {
                    "plt_id": p["plt_id"],
                    "name": p["name"],
                    "hardiness": p.get("hardiness", ""),
                    "min_safe_temp": p.get("min_safe_temp", 0),
                }
                for p in frost_plants
            ]
        elif alert_type in ("heat_wave", "dry_spell", "rain_surplus") and watering_plants:
            plant_list = watering_plants
            alert_meta["plant_advice"] = [
                {
                    "plt_id": p["plt_id"],
                    "name": p["name"],
                    "care_watering": p.get("care_watering", ""),
                }
                for p in watering_plants
            ]

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
            existing_advice = existing_meta.get("plant_advice")
            incoming_advice = alert_meta.get("plant_advice")
            if isinstance(existing_advice, list) or isinstance(incoming_advice, list):
                advice_by_value: dict[str, object] = {}
                for advice in [
                    *(existing_advice if isinstance(existing_advice, list) else []),
                    *(incoming_advice if isinstance(incoming_advice, list) else []),
                ]:
                    key = json.dumps(advice, sort_keys=True, separators=(",", ":"), default=str)
                    advice_by_value[key] = advice
                merged_meta["plant_advice"] = [
                    advice_by_value[key] for key in sorted(advice_by_value)
                ]
            severity_rank = {"low": 0, "normal": 1, "high": 2, "critical": 3}
            existing_severity = str(existing["severity"] or "normal")
            incoming_severity = str(alert["severity"] or "normal")
            severity_escalated = severity_rank.get(
                incoming_severity,
                severity_rank["normal"],
            ) > severity_rank.get(existing_severity, severity_rank["normal"])
            merged_severity = max(
                (existing_severity, incoming_severity),
                key=lambda value: severity_rank.get(value, severity_rank["normal"]),
            )
            db.execute(
                """
                UPDATE weather_alerts
                SET severity = %s,
                    title = CASE WHEN title = '' THEN %s ELSE title END,
                    description = CASE WHEN description = '' THEN %s ELSE description END,
                    valid_until = GREATEST(valid_until, %s),
                    metadata_json = %s
                WHERE id = %s
                """,
                (
                    merged_severity,
                    alert["title"],
                    alert["description"],
                    alert["valid_until"],
                    json.dumps(merged_meta, default=str),
                    alert_id,
                ),
            )
            if severity_escalated:
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

        if plant_list:
            for plant in plant_list:
                db.execute(
                    "INSERT INTO weather_alert_plants"
                    " (alert_id, plt_id) VALUES (%s, %s)"
                    " ON CONFLICT DO NOTHING",
                    (alert_id, plant["plt_id"]),
                )
    db.commit()
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
    if not forecast or "daily" not in forecast:
        return {
            "forecast_available": False,
            "alerts_created": 0,
            "alerts_skipped": 0,
            "alerts": [],
            "frost_vulnerable_plants": [],
            "watering_sensitive_plants": [],
        }

    alerts = analyze_forecast(forecast)

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

    return {
        "forecast_available": True,
        "alerts_created": result["created"],
        "alerts_skipped": result["skipped"],
        "alerts": alerts,
        "frost_vulnerable_plants": frost_plants,
        "watering_sensitive_plants": watering_plants,
    }
