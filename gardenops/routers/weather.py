"""Weather router -- forecast, alerts, and weather-aware guidance."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from gardenops.db import DB, DbConn
from gardenops.router_helpers import (
    active_garden_id as _active_garden_id,
)
from gardenops.router_helpers import (
    auth_context as _auth_context,
)
from gardenops.router_helpers import (
    require_write as _require_write,
)
from gardenops.services.automation import (
    on_dry_spell_alert,
    on_frost_alert,
    on_heat_alert,
    on_rain_alert,
)
from gardenops.services.notification_service import create_weather_alert_notifications
from gardenops.services.weather_service import (
    check_weather_and_generate_alerts,
    find_frost_vulnerable_plants,
    find_watering_sensitive_plants,
    get_cached_forecast,
    get_or_fetch_forecast,
)
from gardenops.sql_dates import offset_days_iso

router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────


def _get_garden_location(db: DbConn, garden_id: int) -> tuple[float, float]:
    """Return (latitude, longitude) or raise 422 if not configured."""
    row = db.execute(
        "SELECT latitude, longitude FROM gardens WHERE id = %s",
        (garden_id,),
    ).fetchone()
    if not row or row["latitude"] is None or row["longitude"] is None:
        raise HTTPException(
            status_code=422,
            detail="Garden location (latitude/longitude) not configured.",
        )
    return (float(row["latitude"]), float(row["longitude"]))


def _serialize_alert(row: dict[str, Any], plant_ids: list[str]) -> dict:
    metadata: dict = {}
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except (
        json.JSONDecodeError,
        TypeError,
    ):
        pass
    return {
        "id": int(row["id"]),
        "garden_id": int(row["garden_id"]),
        "alert_type": str(row["alert_type"]),
        "severity": str(row["severity"]),
        "title": str(row["title"]),
        "description": str(row["description"]),
        "valid_from": str(row["valid_from"]),
        "valid_until": str(row["valid_until"]),
        "metadata": metadata,
        "dismissed": bool(row["dismissed"]),
        "created_at_ms": int(row["created_at_ms"]),
        "plant_ids": plant_ids,
    }


def _load_active_alerts(db: DbConn, garden_id: int) -> list[dict]:
    """Load active (non-dismissed, not expired) alerts with linked plants."""
    today_iso = offset_days_iso(0)
    rows = db.execute(
        """
        SELECT * FROM weather_alerts
        WHERE garden_id = %s AND dismissed = 0 AND valid_until >= %s
        ORDER BY
            CASE severity WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
            valid_from ASC
        """,
        (garden_id, today_iso),
    ).fetchall()
    result = []
    for row in rows:
        plant_rows = db.execute(
            "SELECT plt_id FROM weather_alert_plants WHERE alert_id = %s",
            (row["id"],),
        ).fetchall()
        plant_ids = [str(r["plt_id"]) for r in plant_rows]
        result.append(_serialize_alert(row, plant_ids))
    return result


# ── Endpoints ────────────────────────────────────────────────


@router.get("/weather/forecast")
def get_forecast(request: Request, db: DB) -> dict:
    """Get cached or fresh 7-day forecast for the active garden."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    lat, lng = _get_garden_location(db, garden_id)
    forecast = get_or_fetch_forecast(db, garden_id, lat, lng)
    if not forecast or "daily" not in forecast:
        return {"forecast_available": False, "daily": {}}
    return {"forecast_available": True, **forecast}


@router.get("/weather/alerts")
def get_alerts(request: Request, db: DB) -> dict:
    """List active (non-dismissed) weather alerts."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    alerts = _load_active_alerts(db, garden_id)
    return {"alerts": alerts, "total": len(alerts)}


@router.post("/weather/check")
def check_weather(request: Request, db: DB) -> dict:
    """Trigger weather check and alert generation."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    _require_write(context)
    lat, lng = _get_garden_location(db, garden_id)

    result = check_weather_and_generate_alerts(db, garden_id, lat, lng)

    # Create per-recipient notifications for new active alerts.
    if result["alerts_created"] > 0:
        notification_result = create_weather_alert_notifications(
            db,
            garden_id=garden_id,
            alerts=list(result.get("alerts", [])),
        )
        if notification_result.get("created", 0) > 0:
            db.commit()

    # Generate frost protection tasks for new frost alerts
    frost_tasks = 0
    for alert in result.get("alerts", []):
        if alert.get("alert_type") == "frost_warning":
            row = db.execute(
                "SELECT id FROM weather_alerts"
                " WHERE garden_id = %s AND alert_type = 'frost_warning'"
                " AND valid_from = %s AND dismissed = 0"
                " ORDER BY id DESC LIMIT 1",
                (garden_id, alert["valid_from"]),
            ).fetchone()
            if row:
                frost_tasks += on_frost_alert(
                    db,
                    garden_id,
                    int(row["id"]),
                    context.user_id,
                )
    if frost_tasks > 0:
        db.commit()

    # Generate tasks for other alert types
    other_tasks = 0
    alert_type_handlers = {
        "heat_wave": on_heat_alert,
        "dry_spell": on_dry_spell_alert,
        "rain_surplus": on_rain_alert,
    }
    for alert in result.get("alerts", []):
        handler = alert_type_handlers.get(alert.get("alert_type", ""))
        if not handler:
            continue
        row = db.execute(
            "SELECT id FROM weather_alerts"
            " WHERE garden_id = %s AND alert_type = %s"
            " AND valid_from = %s AND dismissed = 0"
            " ORDER BY id DESC LIMIT 1",
            (garden_id, alert["alert_type"], alert["valid_from"]),
        ).fetchone()
        if row:
            other_tasks += handler(
                db,
                garden_id,
                int(row["id"]),
                context.user_id,
            )
    if other_tasks > 0:
        db.commit()

    return {
        "forecast_available": result["forecast_available"],
        "alerts_created": result["alerts_created"],
        "alerts_skipped": result["alerts_skipped"],
    }


@router.post("/weather/alerts/{alert_id}/dismiss")
def dismiss_alert(alert_id: int, request: Request, db: DB) -> dict:
    """Dismiss a weather alert."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    _require_write(context)

    row = db.execute(
        "SELECT id FROM weather_alerts WHERE id = %s AND garden_id = %s",
        (alert_id, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found")

    db.execute(
        "UPDATE weather_alerts SET dismissed = 1 WHERE id = %s",
        (alert_id,),
    )
    db.commit()
    return {"status": "dismissed", "id": alert_id}


@router.get("/weather/summary")
def get_summary(request: Request, db: DB) -> dict:
    """Combined weather summary: forecast + active alerts + vulnerable plants."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)

    # Get garden location -- return graceful empty if not configured
    row = db.execute(
        "SELECT latitude, longitude FROM gardens WHERE id = %s",
        (garden_id,),
    ).fetchone()
    if not row or row["latitude"] is None or row["longitude"] is None:
        return {
            "forecast_available": False,
            "forecast_days": [],
            "alerts": [],
            "frost_vulnerable_plants": [],
            "watering_sensitive_plants": [],
        }

    # Use cached forecast only (don't trigger fetch on read)
    forecast = get_cached_forecast(db, garden_id)
    forecast_days: list[dict] = []
    if forecast and "daily" in forecast:
        daily = forecast["daily"]
        dates = daily.get("time", [])
        temp_min = daily.get("temperature_2m_min", [])
        temp_max = daily.get("temperature_2m_max", [])
        precip_list = daily.get("precipitation_sum", [])
        precip_prob = daily.get("precipitation_probability_max", [])
        wind = daily.get("wind_speed_10m_max", [])
        for i, d in enumerate(dates):
            forecast_days.append(
                {
                    "date": d,
                    "temp_min": temp_min[i] if i < len(temp_min) else None,
                    "temp_max": temp_max[i] if i < len(temp_max) else None,
                    "precipitation": precip_list[i] if i < len(precip_list) else None,
                    "precipitation_probability": (precip_prob[i] if i < len(precip_prob) else None),
                    "wind_speed": wind[i] if i < len(wind) else None,
                }
            )

    # Active alerts
    alerts = _load_active_alerts(db, garden_id)

    # Frost-vulnerable plants (if any frost alert active)
    frost_plants: list[dict] = []
    for alert in alerts:
        if alert["alert_type"] == "frost_warning":
            coldest = alert.get("metadata", {}).get("coldest", 0)
            frost_plants = find_frost_vulnerable_plants(db, garden_id, float(coldest))
            break

    # Watering-sensitive plants (if dry/rain alert active)
    watering_plants: list[dict] = []
    for alert in alerts:
        if alert["alert_type"] in ("heat_wave", "dry_spell", "rain_surplus"):
            watering_plants = find_watering_sensitive_plants(db, garden_id)
            break

    return {
        "forecast_available": bool(forecast_days),
        "forecast_days": forecast_days,
        "alerts": alerts,
        "frost_vulnerable_plants": frost_plants,
        "watering_sensitive_plants": watering_plants,
    }
