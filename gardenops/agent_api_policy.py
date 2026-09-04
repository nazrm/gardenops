from __future__ import annotations

import re
from typing import Final

_ID = r"[^/]+"
_GARDEN_ID = r"[1-9][0-9]*"


def _route(template: str) -> re.Pattern[str]:
    escaped = re.escape(template)
    escaped = escaped.replace(r"\{garden_id\}", _GARDEN_ID)
    pattern = re.sub(r"\\\{[^}]+\\\}", _ID, escaped)
    return re.compile(f"^{pattern}$")


# Closed registry: adding a future HTTP route never exposes it to the agent by accident.
_ROUTES: Final = tuple(
    (frozenset(methods), _route(path))
    for methods, path in (
        (("POST",), "/api/ai/identify-plant"),
        (("GET",), "/api/external-plants"),
        (("GET",), "/api/gardens"),
        (("GET",), "/api/attention/today"),
        (("GET", "PUT"), "/api/attention/preferences"),
        (("POST",), "/api/attention/items/{item_id}/read"),
        (("POST",), "/api/attention/items/{item_id}/dismiss"),
        (("POST",), "/api/attention/items/{item_id}/snooze"),
        (("POST",), "/api/attention/items/{item_id}/restore"),
        (("POST",), "/api/attention/outcomes/{outcome_id}/restore"),
        (("GET", "PATCH"), "/api/calendar/preferences"),
        (("GET",), "/api/calendar/events"),
        (("GET",), "/api/calendar/export.ics"),
        (("POST",), "/api/calendar/manual-events"),
        (("PATCH", "DELETE"), "/api/calendar/manual-events/{event_id}"),
        (("GET",), "/api/dashboard/badge-counts"),
        (("GET",), "/api/dashboard/today"),
        (("GET",), "/api/exports/plants"),
        (("GET",), "/api/exports/tasks"),
        (("GET",), "/api/exports/journal"),
        (("GET",), "/api/exports/harvest"),
        (("GET",), "/api/exports/inventory"),
        (("GET",), "/api/exports/issues"),
        (("GET",), "/api/exports/procurement"),
        (("GET",), "/api/exports/seasonal-summary"),
        (("GET", "PATCH"), "/api/gardens/{garden_id}/settings"),
        (("GET",), "/api/gardens/{garden_id}/geocode"),
        (("POST",), "/api/gardens/{garden_id}/zones"),
        (("GET", "POST"), "/api/gardens/{garden_id}/map-objects"),
        (("PATCH", "DELETE"), "/api/gardens/{garden_id}/map-objects/{object_id}"),
        (("POST",), "/api/gardens/{garden_id}/map-objects/{object_id}/containers/from-plots"),
        (("POST",), "/api/gardens/{garden_id}/map-objects/{object_id}/units"),
        (("PATCH", "DELETE"), "/api/gardens/{garden_id}/map-objects/{object_id}/units/{unit_id}"),
        (("POST",), "/api/gardens/{garden_id}/containers"),
        (("GET", "PATCH", "DELETE"), "/api/gardens/{garden_id}/containers/{plot_id}"),
        (("GET", "POST"), "/api/harvest"),
        (("GET",), "/api/harvest/summary"),
        (("GET", "PATCH", "DELETE"), "/api/harvest/{entry_id}"),
        (("GET", "POST"), "/api/inventory"),
        (("GET", "PATCH", "DELETE"), "/api/inventory/{item_id}"),
        (("POST",), "/api/inventory/{item_id}/plant"),
        (("GET", "POST"), "/api/inventory/{item_id}/transactions"),
        (("GET", "POST"), "/api/issues"),
        (("GET",), "/api/issues/summary"),
        (("GET", "PATCH", "DELETE"), "/api/issues/{issue_id}"),
        (("GET",), "/api/issues/{issue_id}/history"),
        (("POST",), "/api/issues/{issue_id}/resolve"),
        (("GET", "POST"), "/api/journal"),
        (("GET", "PATCH", "DELETE"), "/api/journal/{entry_id}"),
        (("GET", "PATCH"), "/api/layout-state"),
        (("GET",), "/api/media"),
        (("POST",), "/api/media/summaries"),
        (("GET",), "/api/media/plants/missing-covers"),
        (("GET", "DELETE"), "/api/media/{asset_id}"),
        (("POST", "DELETE"), "/api/media/{asset_id}/links"),
        (("POST",), "/api/media/plants/{plant_id}/cover"),
        (("GET",), "/api/notifications"),
        (("GET",), "/api/notifications/count"),
        (("GET", "PUT"), "/api/notifications/preferences"),
        (("POST",), "/api/notifications/{notification_id}/read"),
        (("DELETE",), "/api/notifications/{notification_id}"),
        (("POST",), "/api/notifications/read-all"),
        (("GET",), "/api/planner/suggestions"),
        (("GET",), "/api/planner/companions"),
        (("GET",), "/api/planner/garden-profile"),
        (("GET", "PUT"), "/api/planner/goal"),
        (("GET", "POST"), "/api/plants"),
        (("GET",), "/api/plants/next-id"),
        (("GET",), "/api/plants/search"),
        (("GET",), "/api/plants/export-csv"),
        (("GET",), "/api/plants/{plant_id}/details"),
        (("GET",), "/api/plants/{plant_id}/plots"),
        (("GET",), "/api/plants/{plant_id}/assignments"),
        (("PATCH", "DELETE"), "/api/plants/{plant_id}"),
        (("POST",), "/api/plants/batch-update"),
        (("POST",), "/api/plants/batch-journal-entry"),
        (("GET", "POST"), "/api/plots"),
        (("GET",), "/api/plots/alerts"),
        (("GET",), "/api/plots/export"),
        (("PATCH",), "/api/plots/plants/seen-growing"),
        (("GET", "PATCH", "DELETE"), "/api/plots/{plot_id}"),
        (("GET",), "/api/plots/{plot_id}/delete-impact"),
        (("GET",), "/api/plots/{plot_id}/plant-alerts"),
        (("GET",), "/api/plots/{plot_id}/room-labels"),
        (("GET",), "/api/plots/{plot_id}/plants"),
        (("POST", "PATCH", "DELETE"), "/api/plots/{plot_id}/plants/{plant_id}"),
        (("POST",), "/api/plots/{from_plot}/plants/{plant_id}/move/{to_plot}"),
        (("POST",), "/api/plots/batch-move"),
        (("GET", "PATCH"), "/api/plots/elevations"),
        (("GET",), "/api/procurement/summary"),
        (("GET", "POST"), "/api/procurement"),
        (("GET", "PATCH", "DELETE"), "/api/procurement/{item_id}"),
        (("POST",), "/api/procurement/{item_id}/transition"),
        (("GET", "POST"), "/api/saved-views"),
        (("GET",), "/api/saved-views/presets"),
        (("PATCH", "DELETE"), "/api/saved-views/{view_id}"),
        (("GET",), "/api/shademap/config"),
        (("GET",), "/api/shademap/monthly-estimated-sun"),
        (("GET",), "/api/shademap/sun-window"),
        (("GET",), "/api/shademap/features"),
        (("GET", "PATCH"), "/api/shademap/state"),
        (("GET", "PATCH"), "/api/shademap/calibration"),
        (("GET", "POST"), "/api/shademap/obstacles"),
        (("PATCH", "DELETE"), "/api/shademap/obstacles/{obstacle_id}"),
        (("GET",), "/api/statistics/actions"),
        (("GET",), "/api/statistics/automation-status"),
        (("GET",), "/api/statistics/reports"),
        (("GET", "POST"), "/api/tasks"),
        (("GET", "PATCH", "DELETE"), "/api/tasks/{task_id}"),
        (("POST",), "/api/tasks/{task_id}/action"),
        (("POST",), "/api/tasks/batch-action"),
        (("POST",), "/api/tasks/refresh-descriptions"),
        (("POST",), "/api/tasks/generate"),
        (("GET",), "/api/weather/forecast"),
        (("GET",), "/api/weather/alerts"),
        (("GET",), "/api/weather/summary"),
        (("POST",), "/api/weather/check"),
        (("POST",), "/api/weather/alerts/{alert_id}/dismiss"),
        (("GET",), "/api/workflows/available"),
        (("POST",), "/api/workflows/start"),
    )
)

_GARDEN_SCOPED_PATH: Final = re.compile(r"^/api/gardens/([1-9][0-9]*)(?:/|$)")
_CONFIRMATION_REQUIRED: Final = tuple(
    _route(path)
    for path in (
        "/api/gardens/{garden_id}/settings",
        "/api/gardens/{garden_id}/zones",
        "/api/gardens/{garden_id}/map-objects",
        "/api/gardens/{garden_id}/map-objects/{object_id}",
        "/api/gardens/{garden_id}/map-objects/{object_id}/containers/from-plots",
        "/api/gardens/{garden_id}/map-objects/{object_id}/units",
        "/api/gardens/{garden_id}/map-objects/{object_id}/units/{unit_id}",
        "/api/gardens/{garden_id}/containers",
        "/api/gardens/{garden_id}/containers/{plot_id}",
        "/api/layout-state",
        "/api/notifications/read-all",
        "/api/plants/batch-update",
        "/api/plants/batch-journal-entry",
        "/api/plots/batch-move",
        "/api/plots/plants/seen-growing",
        "/api/plots/elevations",
        "/api/shademap/state",
        "/api/shademap/calibration",
        "/api/shademap/obstacles",
        "/api/shademap/obstacles/{obstacle_id}",
        "/api/tasks/batch-action",
        "/api/tasks/refresh-descriptions",
        "/api/tasks/generate",
        "/api/workflows/start",
    )
)


def agent_api_path_garden_id(path: str) -> int | None:
    """Return an explicit numeric garden ID carried by a GardenOps API path."""
    match = _GARDEN_SCOPED_PATH.match(path)
    return int(match.group(1)) if match else None


def agent_api_request_allowed(method: str, path: str) -> bool:
    """Return whether the Matrix-bound MCP principal may call one existing API route."""
    normalized_method = method.strip().upper()
    if (
        not path.startswith("/api/")
        or len(path) > 512
        or any(character in path for character in ("?", "#", "\\", "%"))
        or "//" in path
        or any(segment in {".", ".."} for segment in path.split("/"))
    ):
        return False
    return any(
        normalized_method in methods and pattern.fullmatch(path) is not None
        for methods, pattern in _ROUTES
    )


def agent_api_confirmation_required(method: str, path: str) -> bool:
    """Return whether a mutation needs an explicit confirmed flag."""
    normalized_method = method.strip().upper()
    return normalized_method == "DELETE" or any(
        pattern.fullmatch(path) is not None for pattern in _CONFIRMATION_REQUIRED
    )
