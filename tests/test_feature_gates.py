# tests/test_feature_gates.py
"""Unit tests for feature tier gating."""

import unittest

from gardenops.feature_gates import (
    _ROUTE_GATES,
    TIER_ORDER,
    feature_allowed,
    feature_for_route,
    features_for_tier,
    tier_for_feature,
)


class TierOrderTests(unittest.TestCase):
    def test_tier_order(self) -> None:
        assert TIER_ORDER == ("home", "enthusiast", "pro")

    def test_home_tier_includes_core_features(self) -> None:
        home_features = features_for_tier("home")
        for f in ("map", "plots", "plants", "journal", "harvest_basic", "media", "snapshots"):
            assert f in home_features, f"{f} should be in home tier"

    def test_home_tier_excludes_higher_features(self) -> None:
        home_features = features_for_tier("home")
        for f in ("tasks", "issues", "weather", "procurement", "ai", "workflows"):
            assert f not in home_features, f"{f} should NOT be in home tier"

    def test_enthusiast_tier_includes_home_plus_own(self) -> None:
        features = features_for_tier("enthusiast")
        assert "map" in features  # inherited from home
        assert "tasks" in features
        assert "weather" in features
        assert "statistics" in features
        assert "calendar" in features
        assert "calendar_subscriptions" in features

    def test_enthusiast_excludes_pro(self) -> None:
        features = features_for_tier("enthusiast")
        for f in ("procurement", "ai", "workflows", "user_management", "admin_panel"):
            assert f not in features, f"{f} should NOT be in enthusiast tier"

    def test_pro_tier_includes_everything(self) -> None:
        pro = features_for_tier("pro")
        enth = features_for_tier("enthusiast")
        home = features_for_tier("home")
        assert home.issubset(pro)
        assert enth.issubset(pro)
        assert "procurement" in pro
        assert "ai" in pro

    def test_feature_allowed_respects_tier(self) -> None:
        assert feature_allowed("home", "map") is True
        assert feature_allowed("home", "tasks") is False
        assert feature_allowed("enthusiast", "tasks") is True
        assert feature_allowed("enthusiast", "ai") is False
        assert feature_allowed("pro", "ai") is True

    def test_tier_for_feature_returns_minimum_tier(self) -> None:
        assert tier_for_feature("map") == "home"
        assert tier_for_feature("tasks") == "enthusiast"
        assert tier_for_feature("ai") == "pro"

    def test_unknown_feature_is_denied(self) -> None:
        assert feature_allowed("pro", "nonexistent_xyz") is False

    def test_tier_for_unknown_feature_returns_none(self) -> None:
        assert tier_for_feature("nonexistent_xyz") is None

    def test_invalid_tier_treated_as_home(self) -> None:
        assert feature_allowed("invalid", "map") is True
        assert feature_allowed("invalid", "tasks") is False


class RouteFeatureMapTests(unittest.TestCase):
    def test_home_routes_return_none(self) -> None:
        """Home-tier routes have no gate (accessible to all)."""
        assert feature_for_route("/api/plots") is None
        assert feature_for_route("/api/plants") is None
        assert feature_for_route("/api/plants/123") is None
        assert feature_for_route("/api/journal") is None

    def test_enthusiast_routes(self) -> None:
        assert feature_for_route("/api/tasks") == "tasks"
        assert feature_for_route("/api/tasks/42/complete") == "tasks"
        assert feature_for_route("/api/calendar") == "calendar"
        assert feature_for_route("/api/calendar/export.ics") == "calendar"
        assert feature_for_route("/api/calendar/subscriptions") == "calendar_subscriptions"
        assert feature_for_route("/api/issues") == "issues"
        assert feature_for_route("/api/weather/forecast") == "weather"
        assert feature_for_route("/api/notifications/count") == "notifications"
        assert feature_for_route("/api/statistics/summary") == "statistics"
        assert feature_for_route("/api/inventory") == "inventory"
        assert feature_for_route("/api/saved-views") == "saved_views"
        assert feature_for_route("/api/shademap/state") == "shade_map"
        assert feature_for_route("/api/planner/suggestions") == "planner"
        assert feature_for_route("/api/exports/backup") == "exports_full"

    def test_pro_routes(self) -> None:
        assert feature_for_route("/api/procurement") == "procurement"
        assert feature_for_route("/api/workflows") == "workflows"
        assert feature_for_route("/api/ai/chat") == "ai"

    def test_auth_routes_are_ungated(self) -> None:
        assert feature_for_route("/api/auth/me") is None
        assert feature_for_route("/api/auth/login") is None
        assert feature_for_route("/api/version") is None

    def test_unknown_route_is_ungated(self) -> None:
        assert feature_for_route("/api/unknown-thing") is None

    def test_export_routes(self) -> None:
        assert feature_for_route("/api/exports/plants") is None  # basic export
        assert feature_for_route("/api/exports/tasks") == "exports_full"
        assert feature_for_route("/api/exports/issues") == "exports_full"
        assert feature_for_route("/api/exports/inventory") == "exports_full"
        assert feature_for_route("/api/exports/procurement") == "exports_full"
        assert feature_for_route("/api/exports/workflows") == "exports_full"


class MiddlewareGatingTests(unittest.TestCase):
    """Verify that route+tier logic would produce correct allow/deny."""

    def test_home_user_blocked_from_tasks(self) -> None:
        feature = feature_for_route("/api/tasks")
        assert feature == "tasks"
        assert feature_allowed("home", feature) is False

    def test_enthusiast_user_allowed_tasks(self) -> None:
        feature = feature_for_route("/api/tasks")
        assert feature is not None
        assert feature_allowed("enthusiast", feature) is True

    def test_home_user_allowed_plots(self) -> None:
        feature = feature_for_route("/api/plots")
        # plots route is ungated
        assert feature is None

    def test_pro_user_allowed_everything(self) -> None:
        for prefix, feat in _ROUTE_GATES:
            assert feature_allowed("pro", feat) is True, f"pro should access {feat}"


class TierValidationTests(unittest.TestCase):
    def test_valid_tiers(self) -> None:
        for tier in TIER_ORDER:
            assert tier in ("home", "enthusiast", "pro")

    def test_tier_count(self) -> None:
        assert len(TIER_ORDER) == 3


if __name__ == "__main__":
    unittest.main()
