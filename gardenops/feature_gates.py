# gardenops/feature_gates.py
"""Feature tier gating — central registry of features and their minimum tiers."""

from __future__ import annotations

import re
from typing import Literal

Tier = Literal["home", "enthusiast", "pro"]
TIER_ORDER: tuple[Tier, ...] = ("home", "enthusiast", "pro")

# Maps each feature key to the minimum tier required.
_FEATURE_TIERS: dict[str, Tier] = {
    # ── Home tier ──
    "map": "home",
    "plots": "home",
    "plants": "home",
    "journal": "home",
    "harvest_basic": "home",
    "onboarding": "home",
    "media": "home",
    "theme": "home",
    "snapshots": "home",
    "exports_basic": "home",
    # ── Enthusiast tier ──
    "tasks": "enthusiast",
    "issues": "enthusiast",
    "weather": "enthusiast",
    "notifications": "enthusiast",
    "shade_map": "enthusiast",
    "planner": "enthusiast",
    "saved_views": "enthusiast",
    "statistics": "enthusiast",
    "inventory": "enthusiast",
    "care": "enthusiast",
    "calendar": "enthusiast",
    "calendar_subscriptions": "enthusiast",
    "exports_full": "enthusiast",
    # ── Professional tier ──
    "multi_garden": "pro",
    "user_management": "pro",
    "mfa": "pro",
    "procurement": "pro",
    "workflows": "pro",
    "ai": "pro",
    "email_notifications": "pro",
    "audit": "pro",
    "admin_panel": "pro",
    "api_key_access": "pro",
}


def _tier_index(tier: str) -> int:
    try:
        return TIER_ORDER.index(tier)  # type: ignore[arg-type]
    except ValueError:
        return 0  # unknown tier → home


def tier_for_feature(feature: str) -> Tier | None:
    """Return the minimum tier required for *feature*, or None if unknown."""
    return _FEATURE_TIERS.get(feature)


def feature_allowed(tier: str, feature: str) -> bool:
    """Check whether *tier* grants access to *feature*."""
    min_tier = _FEATURE_TIERS.get(feature)
    if min_tier is None:
        return False
    return _tier_index(tier) >= _tier_index(min_tier)


def features_for_tier(tier: str) -> frozenset[str]:
    """Return the set of features available to *tier*."""
    idx = _tier_index(tier)
    return frozenset(
        feat for feat, min_tier in _FEATURE_TIERS.items() if _tier_index(min_tier) <= idx
    )


# Route prefix → feature key.
# Routes not listed here are ungated (accessible to all tiers).
# Sorted by descending prefix length so longer/more-specific prefixes match first.
# IMPORTANT: When adding new gated routes, add them here. Ungated is the default.
_ROUTE_GATES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        [
            # ── Enthusiast ──
            ("/api/tasks", "tasks"),
            ("/api/issues", "issues"),
            ("/api/weather", "weather"),
            ("/api/notifications", "notifications"),
            ("/api/statistics", "statistics"),
            ("/api/inventory", "inventory"),
            ("/api/calendar/subscriptions", "calendar_subscriptions"),
            ("/api/calendar", "calendar"),
            ("/api/saved-views", "saved_views"),
            ("/api/shademap", "shade_map"),
            ("/shademap/terrain", "shade_map"),
            ("/api/planner", "planner"),
            # ── Professional ──
            ("/api/procurement", "procurement"),
            ("/api/workflows", "workflows"),
            ("/api/ai", "ai"),
            # ── Export sub-gating ──
            # Basic exports (plants, journal, harvest) are ungated.
            # Full exports (tasks, issues, inventory, procurement, etc.) require enthusiast.
            ("/api/exports/tasks", "exports_full"),
            ("/api/exports/issues", "exports_full"),
            ("/api/exports/inventory", "exports_full"),
            ("/api/exports/procurement", "exports_full"),
            ("/api/exports/workflows", "exports_full"),
            ("/api/exports/backup", "exports_full"),
        ],
        key=lambda pair: -len(pair[0]),
    )
)

_GARDEN_LIDAR_RE = re.compile(r"^/api/gardens/[+-]?\d+/lidar(?:/.*)?$")


def feature_for_route(path: str) -> str | None:
    """Return the feature key that gates *path*, or None if ungated."""
    if _GARDEN_LIDAR_RE.fullmatch(path):
        return "shade_map"
    for prefix, feature in _ROUTE_GATES:
        if path == prefix or path.startswith(prefix + "/"):
            return feature
    return None
