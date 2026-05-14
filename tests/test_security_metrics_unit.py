"""Unit tests for gardenops.security_metrics — counters, rates, snapshots, alerts."""

import time
import unittest
from unittest.mock import patch

from gardenops.security_metrics import (
    _COUNTERS,
    _EVENTS,
    _LOCK,
    record_security_event,
    reset_security_metrics,
    security_event_rate,
)
from tests.base import BaseApiTest


class TestRecordSecurityEvent(unittest.TestCase):
    """record_security_event — counter increments."""

    def setUp(self) -> None:
        reset_security_metrics()

    def tearDown(self) -> None:
        reset_security_metrics()

    def test_single_event_increments_counter(self) -> None:
        record_security_event("test_metric")
        with _LOCK:
            self.assertEqual(_COUNTERS["test_metric"], 1)

    def test_multiple_events_accumulate(self) -> None:
        record_security_event("test_metric")
        record_security_event("test_metric")
        record_security_event("test_metric")
        with _LOCK:
            self.assertEqual(_COUNTERS["test_metric"], 3)

    def test_count_parameter(self) -> None:
        record_security_event("bulk_metric", count=5)
        with _LOCK:
            self.assertEqual(_COUNTERS["bulk_metric"], 5)

    def test_negative_count_treated_as_one(self) -> None:
        record_security_event("neg_metric", count=-3)
        with _LOCK:
            self.assertEqual(_COUNTERS["neg_metric"], 1)

    def test_separate_metrics_independent(self) -> None:
        record_security_event("metric_a", count=2)
        record_security_event("metric_b", count=7)
        with _LOCK:
            self.assertEqual(_COUNTERS["metric_a"], 2)
            self.assertEqual(_COUNTERS["metric_b"], 7)


class TestSecurityEventRate(unittest.TestCase):
    """security_event_rate — windowed rate calculation."""

    def setUp(self) -> None:
        reset_security_metrics()

    def tearDown(self) -> None:
        reset_security_metrics()

    def test_no_events_returns_zero(self) -> None:
        self.assertEqual(security_event_rate("nonexistent", 60), 0)

    def test_recent_events_counted(self) -> None:
        record_security_event("rate_test", count=3)
        rate = security_event_rate("rate_test", 60)
        self.assertEqual(rate, 3)

    def test_window_filters_old_events(self) -> None:
        now = time.monotonic()
        with _LOCK:
            dq = _EVENTS["old_metric"]
            # Inject events 120 seconds ago (outside a 60s window)
            for _ in range(5):
                dq.append(now - 120)
            # Inject 2 recent events
            dq.append(now - 1)
            dq.append(now)
            _COUNTERS["old_metric"] = 7
        rate = security_event_rate("old_metric", 60)
        self.assertEqual(rate, 2)


class TestSecurityMetricsSnapshot(BaseApiTest):
    """security_metrics_snapshot — structure and aggregation."""

    def test_snapshot_structure(self) -> None:
        # Import here since it calls get_db() internally
        from gardenops.security_metrics import security_metrics_snapshot

        reset_security_metrics()
        record_security_event("auth_failures", count=3)
        snapshot = security_metrics_snapshot()
        self.assertIn("counters", snapshot)
        self.assertIn("rates", snapshot)
        self.assertIn("garden_scope", snapshot)
        self.assertIn("provider_limits", snapshot)
        self.assertIn("exporter", snapshot)

    def test_counters_in_snapshot(self) -> None:
        from gardenops.security_metrics import security_metrics_snapshot

        reset_security_metrics()
        record_security_event("auth_failures", count=5)
        snapshot = security_metrics_snapshot()
        counters = snapshot["counters"]
        self.assertEqual(counters["auth_failures"], 5)

    def test_rates_keys_present(self) -> None:
        from gardenops.security_metrics import security_metrics_snapshot

        reset_security_metrics()
        snapshot = security_metrics_snapshot()
        rates = snapshot["rates"]
        self.assertIn("auth_failures_per_minute", rates)
        self.assertIn("rate_limit_hits_per_minute", rates)
        self.assertIn("mutations_per_minute", rates)


class TestSecurityAlertsSnapshot(BaseApiTest):
    """security_alerts_snapshot — threshold triggering."""

    def test_no_alerts_when_quiet(self) -> None:
        from gardenops.security_metrics import security_alerts_snapshot

        reset_security_metrics()
        result = security_alerts_snapshot()
        self.assertIn("alerts", result)
        self.assertIn("thresholds", result)
        self.assertIn("rates", result)
        self.assertEqual(len(result["alerts"]), 0)

    def test_alert_triggered_above_threshold(self) -> None:
        from gardenops.security_metrics import security_alerts_snapshot

        reset_security_metrics()
        # Default threshold for auth_failures_per_minute is 30
        for _ in range(35):
            record_security_event("auth_failures")
        result = security_alerts_snapshot()
        alert_names = [a["name"] for a in result["alerts"]]
        self.assertIn("auth_failures_per_minute", alert_names)

    def test_alert_has_required_fields(self) -> None:
        from gardenops.security_metrics import security_alerts_snapshot

        reset_security_metrics()
        for _ in range(35):
            record_security_event("auth_failures")
        result = security_alerts_snapshot()
        for alert in result["alerts"]:
            self.assertIn("name", alert)
            self.assertIn("value", alert)
            self.assertIn("threshold", alert)
            self.assertIn("severity", alert)

    def test_custom_threshold_via_env(self) -> None:
        from gardenops.security_metrics import security_alerts_snapshot

        reset_security_metrics()
        record_security_event("auth_failures", count=3)
        with patch.dict("os.environ", {"ALERT_AUTH_FAILURES_PER_MINUTE": "2"}):
            result = security_alerts_snapshot()
        alert_names = [a["name"] for a in result["alerts"]]
        self.assertIn("auth_failures_per_minute", alert_names)
