import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gardenops.services.attention import (
    AttentionAction,
    AttentionItem,
    AttentionPreferenceSet,
    apply_preferences,
    attention_request_clock,
    attention_today_date,
    group_attention_items,
    is_generated_watering_task,
    rank_attention_items,
    require_attention_e2e_database,
    resolve_attention_preferences,
    stable_group_id,
)


def _load_e2e_safety_module():
    path = Path("scripts/check_attention_e2e_db_safety.py")
    spec = importlib.util.spec_from_file_location("check_attention_e2e_db_safety", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_attention_today_date_uses_explicit_frozen_date() -> None:
    assert attention_today_date(now_ms=1783180800000, frozen_date="2026-07-05") == "2026-07-05"


def test_is_generated_watering_task_accepts_only_generated_water_sources() -> None:
    assert is_generated_watering_task("water", "water:PLT1:2026-07-05")
    assert is_generated_watering_task("water", "auto:dry_water:12:PLT1")
    assert not is_generated_watering_task("water", "")
    assert not is_generated_watering_task("water", None)
    assert not is_generated_watering_task("water", "auto:rain_drainage:12:PLT1")
    assert not is_generated_watering_task("prune", "water:PLT1:2026-07-05")


def test_generated_watering_predicate_accepts_only_recognized_sources() -> None:
    assert is_generated_watering_task("water", "water:seasonal:PLT1")
    assert is_generated_watering_task("water", "auto:dry_water:alert:1")
    assert not is_generated_watering_task("water", "")
    assert not is_generated_watering_task("water", "manual")
    assert not is_generated_watering_task("prune", "water:seasonal:PLT1")


def test_attention_today_date_rejects_missing_clock_in_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    with pytest.raises(RuntimeError, match="frozen_date"):
        attention_today_date(now_ms=1783180800000, frozen_date=None)


def test_attention_request_clock_reads_explicit_test_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_NOW_MS", "1783180800000")
    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_DATE", "2026-07-05")

    now_ms, frozen_date = attention_request_clock(now_ms=99)

    assert now_ms == 1783180800000
    assert frozen_date == "2026-07-05"


def test_attention_request_clock_rejects_test_env_outside_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_DATE", "2026-07-05")

    with pytest.raises(RuntimeError, match="test-only"):
        attention_request_clock(now_ms=99)


def test_attention_e2e_database_guard_rejects_non_test_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE", "1")
    with pytest.raises(RuntimeError, match="disposable"):
        require_attention_e2e_database("postgresql://localhost/gardenops")


def test_attention_e2e_database_guard_rejects_libpq_host_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE", "1")

    with pytest.raises(RuntimeError, match="local"):
        require_attention_e2e_database(
            "postgresql://localhost/gardenops_attention_e2e_test?host=db.example.com"
        )


def test_attention_e2e_database_guard_rejects_libpq_hostaddr_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE", "1")

    with pytest.raises(RuntimeError, match="local"):
        require_attention_e2e_database(
            "postgresql://localhost/gardenops_attention_e2e_test?hostaddr=192.0.2.1"
        )


def test_attention_e2e_database_guard_rejects_libpq_dbname_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE", "1")

    with pytest.raises(RuntimeError, match="disposable"):
        require_attention_e2e_database(
            "postgresql://localhost/gardenops_attention_e2e_test?dbname=gardenops"
        )


def test_attention_e2e_database_guard_rejects_missing_allow_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.delenv("GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE", raising=False)

    with pytest.raises(RuntimeError, match="ALLOW_TRUNCATE"):
        require_attention_e2e_database("postgresql://localhost/gardenops_attention_e2e_test")


def test_attention_e2e_database_guard_accepts_named_test_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE", "1")
    require_attention_e2e_database("postgresql://localhost/gardenops_attention_e2e_test")


def test_attention_e2e_database_guard_accepts_local_socket_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE", "1")
    require_attention_e2e_database(
        "postgresql:///gardenops_attention_e2e_test?host=/var/run/postgresql"
    )


def test_attention_e2e_safety_checker_rejects_nested_uncalled_guard() -> None:
    module = _load_e2e_safety_module()
    source = """
from gardenops.services.attention import require_attention_e2e_database
import gardenops.db as db

def main():
    def never_called():
        require_attention_e2e_database("postgresql://localhost/gardenops_attention_e2e_test")
    db.run_migrations()
"""

    with pytest.raises(SystemExit, match="must call require_attention_e2e_database"):
        module.validate_seed_source(source)


def test_attention_e2e_safety_checker_rejects_module_db_work_before_main() -> None:
    module = _load_e2e_safety_module()
    source = """
from gardenops.services.attention import require_attention_e2e_database
import gardenops.db as db

db.run_migrations()

def main():
    require_attention_e2e_database("postgresql://localhost/gardenops_attention_e2e_test")
"""

    with pytest.raises(SystemExit, match="database work appears before E2E guard"):
        module.validate_seed_source(source)


def test_attention_e2e_safety_checker_rejects_called_helper_before_guard() -> None:
    module = _load_e2e_safety_module()
    source = """
from gardenops.services.attention import require_attention_e2e_database
import gardenops.db as db

def main():
    def do_db_work():
        db.run_migrations()
    do_db_work()
    require_attention_e2e_database("postgresql://localhost/gardenops_attention_e2e_test")
"""

    with pytest.raises(SystemExit, match="database work appears before E2E guard"):
        module.validate_seed_source(source)


def test_attention_e2e_safety_checker_accepts_guard_before_db_work() -> None:
    module = _load_e2e_safety_module()
    source = """
from gardenops.services.attention import require_attention_e2e_database
import gardenops.db as db

def main():
    require_attention_e2e_database("postgresql://localhost/gardenops_attention_e2e_test")
    db.run_migrations()
"""

    module.validate_seed_source(source)


def make_item(**overrides):
    base = {
        "id": "attn:task:task_1",
        "provider": "task",
        "type": "task_due",
        "category": "needs_action",
        "severity": "normal",
        "title": "Water basil",
        "body": "Water basil today.",
        "reason": "Due today",
        "target_type": "task",
        "target_id": "task_1",
        "garden_id": 1,
        "audience_user_id": 2,
        "due_on": "2026-07-05",
        "updated_at_ms": 1783180800000,
        "primary_action": AttentionAction(
            kind="open_task",
            label="Open task",
            target_type="task",
            target_id="task_1",
        ),
    }
    base.update(overrides)
    return AttentionItem(**base)


def test_rank_attention_items_prioritizes_high_warnings_before_due_tasks():
    items = [
        make_item(id="attn:task:due", severity="normal", type="task_due"),
        make_item(
            id="attn:weather:frost",
            provider="weather",
            type="frost_warning",
            category="warning",
            severity="high",
            title="Protect basil from frost",
        ),
    ]

    ranked = rank_attention_items(items)

    assert [item.id for item in ranked] == [
        "attn:weather:frost",
        "attn:task:due",
    ]


def test_rank_attention_items_keeps_low_warnings_after_due_tasks():
    items = [
        make_item(id="attn:task:due", severity="normal", type="task_due"),
        make_item(
            id="attn:weather:mild",
            provider="weather",
            type="mild_weather_note",
            category="warning",
            severity="low",
            title="Light rain possible",
        ),
    ]

    ranked = rank_attention_items(items)

    assert [item.id for item in ranked] == ["attn:task:due", "attn:weather:mild"]


def test_group_attention_items_does_not_hide_high_severity_in_low_group():
    low = make_item(id="attn:task:water_low", group_key="water:plot:A", severity="low")
    high = make_item(id="attn:task:water_high", group_key="water:plot:A", severity="high")

    grouped = group_attention_items([low, high])

    assert [item.id for item in grouped] == [
        "attn:task:water_high",
        "attn:task:water_low",
    ]


def test_group_attention_items_groups_repeated_low_priority_items_with_stable_id():
    low_a = make_item(id="attn:task:water_a", group_key="water:plot:A", severity="low")
    low_b = make_item(
        id="attn:task:water_b",
        group_key="water:plot:A",
        severity="normal",
        due_on="2026-07-06",
    )
    high = make_item(id="attn:task:water_high", group_key="water:plot:A", severity="high")

    grouped = group_attention_items([low_a, low_b, high])

    expected_group_id = stable_group_id(
        "task",
        "water:plot:A",
        ["attn:task:water_a", "attn:task:water_b"],
    )
    assert [item.id for item in grouped] == ["attn:task:water_high", expected_group_id]
    assert grouped[1].metadata["child_count"] == 2
    assert grouped[1].metadata["child_ids"] == ["attn:task:water_a", "attn:task:water_b"]
    assert grouped[1].primary_action is not None
    assert grouped[1].primary_action.kind == "open_attention_detail"


def test_group_attention_items_includes_ranked_child_summaries_for_expansion():
    low = make_item(
        id="attn:task:water_low",
        title="Water basil",
        reason="Due today",
        target_type="task",
        target_id="task_low",
        plot_ids=("plot_b",),
        group_key="water:plot:A",
        severity="low",
        due_on="2026-07-06",
        updated_at_ms=1783180800000,
        primary_action=AttentionAction(
            kind="open_task",
            label="Open low task",
            target_type="task",
            target_id="task_low",
        ),
    )
    normal = make_item(
        id="attn:task:water_normal",
        title="Water tomatoes",
        reason="Moisture is low",
        target_type="task",
        target_id="task_normal",
        plot_ids=("plot_a",),
        group_key="water:plot:A",
        severity="normal",
        due_on="2026-07-05",
        updated_at_ms=1783267200000,
        primary_action=AttentionAction(
            kind="select_plot",
            label="Open plot",
            target_type="plot",
            target_id="plot_a",
        ),
    )

    grouped = group_attention_items([low, normal])

    summaries = grouped[0].metadata["children"]
    assert [summary["id"] for summary in summaries] == [
        item.id for item in rank_attention_items([low, normal])
    ]
    assert summaries[0] == {
        "id": "attn:task:water_normal",
        "title": "Water tomatoes",
        "reason": "Moisture is low",
        "severity": "normal",
        "category": "needs_action",
        "type": "task_due",
        "target_type": "task",
        "target_id": "task_normal",
        "plot_ids": ["plot_a"],
        "plant_ids": [],
        "primary_action": {
            "kind": "select_plot",
            "label": "Open plot",
            "target_type": "plot",
            "target_id": "plot_a",
        },
        "due_on": "2026-07-05",
        "updated_at_ms": 1783267200000,
    }


def test_balanced_preferences_keep_panel_visible_when_inbox_is_disabled():
    prefs = resolve_attention_preferences(
        user_id=2,
        legacy_preferences={
            "in_app_enabled": False,
            "email_enabled": False,
            "notification_rules": {
                "task_due": {
                    "in_app_enabled": False,
                    "email_enabled": False,
                    "min_severity": "low",
                },
            },
        },
        saved_attention_preferences=None,
    )
    item = make_item(type="task_due", delivery_eligibility=("panel_only", "inbox"))

    visible = apply_preferences([item], prefs, surface="panel")

    assert [i.id for i in visible] == ["attn:task:task_1"]
    assert prefs.preset == "custom"


def test_guardrail_keeps_critical_system_visible_on_non_email_surface():
    prefs = AttentionPreferenceSet(
        user_id=2,
        preset="custom",
        rules={
            "system": {
                "panel": False,
                "inbox": False,
                "digest": False,
                "min_severity": "critical",
            }
        },
        quiet_hours={},
        show_no_action_history=True,
    )
    item = make_item(
        id="attn:notification:system",
        provider="notification_status",
        type="system",
        category="system",
        severity="critical",
    )

    visible = apply_preferences([item], prefs, surface="panel")

    assert visible[0].id == "attn:notification:system"
    assert visible[0].user_state == "unread"


def test_guardrail_still_respects_channel_eligibility():
    prefs = AttentionPreferenceSet(
        user_id=2,
        preset="custom",
        rules={
            "frost_warning": {
                "panel": False,
                "inbox": False,
                "digest": False,
                "min_severity": "critical",
            }
        },
        quiet_hours={},
        show_no_action_history=True,
    )
    item = make_item(
        id="attn:weather:frost",
        provider="weather",
        type="frost_warning",
        category="warning",
        severity="critical",
        delivery_eligibility=("panel_only",),
    )

    assert [i.id for i in apply_preferences([item], prefs, surface="panel")] == [
        "attn:weather:frost"
    ]
    assert apply_preferences([item], prefs, surface="inbox") == []


def test_legacy_weather_alert_frost_warning_rule_applies_to_attention_type():
    prefs = resolve_attention_preferences(
        user_id=2,
        legacy_preferences={
            "in_app_enabled": True,
            "email_enabled": False,
            "notification_rules": {
                "weather_alert:frost_warning": {
                    "in_app_enabled": False,
                    "email_enabled": False,
                    "min_severity": "critical",
                },
                "legacy:unknown": {
                    "in_app_enabled": False,
                    "email_enabled": False,
                    "min_severity": "critical",
                },
            },
        },
        saved_attention_preferences=None,
    )
    item = make_item(
        id="attn:weather:frost",
        provider="weather",
        type="frost_warning",
        category="warning",
        severity="normal",
        delivery_eligibility=("inbox",),
    )

    assert apply_preferences([item], prefs, surface="inbox") == []
    assert "weather_alert:frost_warning" not in prefs.rules
    assert "legacy:unknown" not in prefs.rules
    assert prefs.metadata["legacy_notification_rules"] == {
        "legacy:unknown": {
            "in_app_enabled": False,
            "email_enabled": False,
            "min_severity": "critical",
        }
    }


def test_legacy_rule_that_already_matches_attention_key_is_preserved():
    prefs = resolve_attention_preferences(
        user_id=2,
        legacy_preferences={
            "in_app_enabled": True,
            "email_enabled": False,
            "notification_rules": {
                "task_due": {
                    "in_app_enabled": False,
                    "email_enabled": False,
                    "min_severity": "low",
                },
            },
        },
        saved_attention_preferences=None,
    )
    item = make_item(type="task_due", delivery_eligibility=("inbox",))

    assert prefs.rules["task_due"]["inbox"] is False
    assert apply_preferences([item], prefs, surface="inbox") == []


def test_legacy_notification_policy_keys_map_to_attention_rule_types():
    prefs = resolve_attention_preferences(
        user_id=2,
        legacy_preferences={
            "in_app_enabled": True,
            "email_enabled": True,
            "notification_rules": {
                "issue_created": {
                    "in_app_enabled": False,
                    "email_enabled": False,
                    "min_severity": "high",
                },
                "weather_alert:rain_surplus": {
                    "in_app_enabled": False,
                    "email_enabled": False,
                    "min_severity": "critical",
                },
                "task_upcoming": {
                    "in_app_enabled": True,
                    "email_enabled": True,
                    "min_severity": "normal",
                },
            },
        },
        saved_attention_preferences=None,
    )

    assert prefs.rules["issue_follow_up_due"]["inbox"] is False
    assert prefs.rules["rain_alert"]["digest"] is False
    assert prefs.rules["task_upcoming"]["digest"] is True
    assert "issue_created" not in prefs.metadata.get("legacy_notification_rules", {})
    assert "weather_alert:rain_surplus" not in prefs.metadata.get(
        "legacy_notification_rules",
        {},
    )


def test_legacy_rule_without_email_channel_uses_type_enablement_for_digest():
    prefs = resolve_attention_preferences(
        user_id=2,
        legacy_preferences={
            "in_app_enabled": True,
            "email_enabled": True,
            "notification_rules": {
                "task_due": {
                    "in_app_enabled": True,
                    "min_severity": "low",
                },
                "task_overdue": {
                    "in_app_enabled": False,
                    "min_severity": "low",
                },
            },
        },
        saved_attention_preferences=None,
    )
    due_item = make_item(type="task_due", delivery_eligibility=("digest",))
    overdue_item = make_item(type="task_overdue", delivery_eligibility=("digest",))

    assert prefs.rules["task_due"]["digest"] is True
    assert prefs.rules["task_overdue"]["digest"] is False
    assert [i.id for i in apply_preferences([due_item], prefs, surface="digest")] == [
        "attn:task:task_1"
    ]
    assert apply_preferences([overdue_item], prefs, surface="digest") == []


def test_no_action_history_includes_recent_completed_items_on_panel():
    prefs = resolve_attention_preferences(
        user_id=2,
        legacy_preferences=None,
        saved_attention_preferences=None,
    )
    item = make_item(
        id="attn:task:completed",
        type="task_completed",
        category="no_action_needed",
        domain_state="completed",
        primary_action=None,
    )

    visible = apply_preferences([item], prefs, surface="panel")

    assert [i.id for i in visible] == ["attn:task:completed"]


def test_balanced_preset_has_explicit_rules_for_planned_attention_types():
    prefs = resolve_attention_preferences(
        user_id=2,
        legacy_preferences=None,
        saved_attention_preferences=None,
    )

    assert prefs.rules["task_overdue"]["inbox"] is True
    assert prefs.rules["frost_warning"]["inbox"] is True
    assert prefs.rules["issue_follow_up_overdue"]["inbox"] is True


def test_quiet_hours_suppress_digest_but_not_panel():
    prefs = AttentionPreferenceSet(
        user_id=2,
        preset="custom",
        rules={
            "task_due": {
                "panel": True,
                "inbox": True,
                "digest": True,
                "min_severity": "low",
            }
        },
        quiet_hours={"digest": {"active": True}},
        show_no_action_history=True,
    )
    item = make_item(delivery_eligibility=("panel_only", "inbox", "digest"))

    assert [i.id for i in apply_preferences([item], prefs, surface="panel")] == ["attn:task:task_1"]
    assert apply_preferences([item], prefs, surface="digest") == []


def test_scheduled_quiet_hours_suppress_digest_only_inside_window():
    prefs = AttentionPreferenceSet(
        user_id=2,
        preset="custom",
        rules={
            "task_due": {
                "panel": True,
                "inbox": True,
                "digest": True,
                "min_severity": "low",
            }
        },
        quiet_hours={"digest": {"enabled": True, "start": "21:30", "end": "06:15"}},
        show_no_action_history=True,
    )
    item = make_item(delivery_eligibility=("panel_only", "inbox", "digest"))
    before_quiet_hours_ms = int(datetime(2026, 7, 5, 21, 29, tzinfo=UTC).timestamp() * 1000)
    quiet_start_ms = int(datetime(2026, 7, 5, 21, 30, tzinfo=UTC).timestamp() * 1000)
    quiet_end_minus_one_ms = int(datetime(2026, 7, 6, 6, 14, tzinfo=UTC).timestamp() * 1000)
    quiet_end_ms = int(datetime(2026, 7, 6, 6, 15, tzinfo=UTC).timestamp() * 1000)

    assert [
        i.id
        for i in apply_preferences([item], prefs, surface="digest", now_ms=before_quiet_hours_ms)
    ] == ["attn:task:task_1"]
    assert apply_preferences([item], prefs, surface="digest", now_ms=quiet_start_ms) == []
    assert apply_preferences([item], prefs, surface="digest", now_ms=quiet_end_minus_one_ms) == []
    assert [
        i.id for i in apply_preferences([item], prefs, surface="digest", now_ms=quiet_end_ms)
    ] == ["attn:task:task_1"]
    assert [
        i.id for i in apply_preferences([item], prefs, surface="panel", now_ms=quiet_start_ms)
    ] == ["attn:task:task_1"]


def test_legacy_quiet_window_fallback_preserves_minute_precision():
    prefs = AttentionPreferenceSet(
        user_id=2,
        preset="custom",
        rules={
            "task_due": {
                "panel": True,
                "inbox": True,
                "digest": True,
                "min_severity": "low",
            }
        },
        quiet_hours={"start": "21:30", "end": "06:15"},
        show_no_action_history=True,
    )
    item = make_item(delivery_eligibility=("panel_only", "inbox", "digest"))
    inside_quiet_hours_ms = int(datetime(2026, 7, 6, 6, 14, tzinfo=UTC).timestamp() * 1000)
    outside_quiet_hours_ms = int(datetime(2026, 7, 6, 6, 15, tzinfo=UTC).timestamp() * 1000)

    assert apply_preferences([item], prefs, surface="digest", now_ms=inside_quiet_hours_ms) == []
    assert [
        i.id
        for i in apply_preferences([item], prefs, surface="digest", now_ms=outside_quiet_hours_ms)
    ] == ["attn:task:task_1"]


def test_non_configurable_notification_rows_follow_attention_delivery_filter():
    from gardenops.services.notification_service import notification_rows_allowed_by_attention

    prefs = AttentionPreferenceSet(
        user_id=2,
        preset="custom",
        rules={
            "system": {
                "panel": False,
                "inbox": False,
                "digest": False,
                "min_severity": "critical",
            }
        },
        quiet_hours={},
        show_no_action_history=True,
    )
    row = {
        "id": 1,
        "public_id": "note_system",
        "garden_id": 1,
        "user_id": 2,
        "notification_type": "system",
        "notification_subtype": None,
        "severity": "low",
        "title": "System notice",
        "body": "Backup status changed",
        "target_type": None,
        "target_id": None,
        "created_at_ms": 1,
    }

    assert (
        notification_rows_allowed_by_attention(
            [row],
            preferences=prefs,
            surface="digest",
            garden_id=1,
            user_id=2,
        )
        == []
    )


def test_active_provider_items_are_eligible_for_inbox_and_digest_surfaces():
    from gardenops.services.attention.providers.calendar import CalendarAttentionProvider
    from gardenops.services.attention.providers.issues import IssueAttentionProvider
    from gardenops.services.attention.providers.tasks import TaskAttentionProvider
    from gardenops.services.attention.providers.weather import WeatherAttentionProvider

    task_item = TaskAttentionProvider(frozen_date="2026-07-05")._item_from_row(
        {
            "public_id": "task_delivery",
            "garden_id": 1,
            "status": "pending",
            "snoozed_until": None,
            "due_on": "2026-07-05",
            "metadata_json": "{}",
            "severity": "normal",
            "title": "Water basil",
            "description": "",
            "task_type": "water",
            "rule_source": "",
            "updated_at_ms": 1,
        },
        plot_ids=(),
        plant_ids=(),
        user_id=2,
        today="2026-07-05",
    )
    weather_provider = WeatherAttentionProvider(frozen_date="2026-07-05")
    issue_item = IssueAttentionProvider(frozen_date="2026-07-05")._item_from_row(
        {
            "public_id": "issue_delivery",
            "garden_id": 1,
            "status": "open",
            "follow_up_on": "2026-07-05",
            "severity": "normal",
            "title": "Check mildew",
            "description": "",
            "issue_type": "mildew",
            "updated_at_ms": 1,
            "resolved_at_ms": None,
        },
        plant_ids=(),
        plot_ids=(),
        user_id=2,
        today="2026-07-05",
    )
    calendar_item = CalendarAttentionProvider(frozen_date="2026-07-05")._item_from_row(
        {
            "public_id": "event_delivery",
            "garden_id": 1,
            "event_on": "2026-07-05",
            "title": "Review beds",
            "description": "",
            "updated_at_ms": 1,
            "created_at_ms": 1,
        },
        plant_ids=(),
        plot_ids=(),
        user_id=2,
        today="2026-07-05",
    )
    weather_alert = make_item(
        id="attn:weather:alert:1",
        provider="weather",
        type="rain_alert",
        category="warning",
        delivery_eligibility=weather_provider._delivery_eligibility("rain_alert", "normal"),
    )

    for item in [task_item, weather_alert, issue_item, calendar_item]:
        assert "inbox" in item.delivery_eligibility
        assert "digest" in item.delivery_eligibility


def test_weather_attention_provider_maps_alert_types_to_notification_subtypes() -> None:
    from gardenops.services.attention.providers.weather import WeatherAttentionProvider

    class _Rows:
        def fetchall(self):
            return [
                {
                    "id": index,
                    "garden_id": 1,
                    "alert_type": alert_type,
                    "severity": "normal",
                    "title": alert_type,
                    "description": "",
                    "valid_from": "2026-07-05",
                    "valid_until": "2026-07-05",
                    "metadata_json": "{}",
                    "created_at_ms": 1,
                }
                for index, alert_type in enumerate(
                    ["frost_warning", "rain_surplus", "heat_wave", "dry_spell", "unknown"],
                    start=1,
                )
            ]

    class _Connection:
        def execute(self, _query, _params):
            return _Rows()

    items = WeatherAttentionProvider(frozen_date="2026-07-05")._active_alert_items(
        _Connection(),
        garden_id=1,
        user_id=2,
        today="2026-07-05",
    )

    assert {item.metadata["alert_type"]: item.type for item in items} == {
        "frost_warning": "frost_warning",
        "rain_surplus": "rain_alert",
        "heat_wave": "heat_wave",
        "dry_spell": "dry_spell",
        "unknown": "weather_alert",
    }
