"""Postgres-backed API tests for Attention storage and feature gating."""

import json
from unittest.mock import patch

import gardenops.db as db
from tests.base import BaseApiTest


class TestAttentionStorageAndGate(BaseApiTest):
    def test_attention_tables_exist_after_migrations(self) -> None:
        conn = db.get_db()
        try:
            tables = {
                row["tablename"]
                for row in conn.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'",
                ).fetchall()
            }
        finally:
            db.return_db(conn)

        self.assertIn("user_attention_preferences", tables)
        self.assertIn("user_attention_item_state", tables)
        self.assertIn("attention_outcomes", tables)

    def test_attention_route_is_tier_gated(self) -> None:
        from gardenops.feature_gates import feature_allowed, feature_for_route

        self.assertEqual(feature_for_route("/api/attention/today"), "attention")
        self.assertFalse(feature_allowed("home", "attention"))
        self.assertTrue(feature_allowed("enthusiast", "attention"))

    def test_attention_prefix_rejects_home_tier_requests_before_router_exists(self) -> None:
        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE auth_users SET subscription_tier = 'home' WHERE username = 'test_admin'",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            "os.environ",
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
        ):
            _, csrf_token = self._login_session("test_admin", "testadminpass")
            headers = self._session_headers(csrf_token)

            self.assertEqual(self.client.get("/api/attention/today").status_code, 403)
            self.assertEqual(
                self.client.put(
                    "/api/attention/preferences",
                    json={},
                    headers=headers,
                ).status_code,
                403,
            )
            self.assertEqual(
                self.client.post(
                    "/api/attention/items/attn:task:demo/read",
                    json={},
                    headers=headers,
                ).status_code,
                403,
            )


class TestAttentionPreferences(BaseApiTest):
    @staticmethod
    def _item(
        *,
        item_type: str,
        category: str = "needs_action",
        severity: str = "normal",
        delivery: tuple[str, ...] = ("panel_only", "inbox", "digest"),
        metadata: dict | None = None,
    ):
        from gardenops.services.attention.types import AttentionItem

        return AttentionItem(
            id=f"attn:test:{item_type}",
            provider="task",
            type=item_type,
            category=category,
            severity=severity,
            title=item_type,
            body="",
            reason="",
            target_type=None,
            target_id=None,
            garden_id=1,
            audience_user_id=1,
            delivery_eligibility=delivery,
            metadata=metadata or {},
        )

    def test_defaults_use_balanced_panel_first_preferences(self) -> None:
        from gardenops.services.attention.preferences import resolve_attention_preferences

        preferences = resolve_attention_preferences(
            user_id=42,
            legacy_preferences=None,
            saved_attention_preferences=None,
        )

        self.assertEqual(preferences.user_id, 42)
        self.assertEqual(preferences.preset, "balanced")
        self.assertTrue(preferences.rules["needs_action"]["panel"])
        self.assertTrue(preferences.rules["needs_action"]["inbox"])
        self.assertFalse(preferences.rules["needs_action"]["digest"])
        self.assertTrue(preferences.show_no_action_history)

    def test_legacy_notification_preferences_migrate_to_custom_panel_visible(self) -> None:
        from gardenops.services.attention.preferences import resolve_attention_preferences

        preferences = resolve_attention_preferences(
            user_id=7,
            legacy_preferences={
                "in_app_enabled": False,
                "email_enabled": False,
                "notification_rules": {
                    "task_due": {"in_app_enabled": False, "email_enabled": False},
                    "weather_alert:frost_warning": {
                        "in_app_enabled": False,
                        "email_enabled": False,
                        "min_severity": "high",
                    },
                },
            },
            saved_attention_preferences=None,
        )

        self.assertEqual(preferences.preset, "custom")
        self.assertTrue(preferences.rules["task_due"]["panel"])
        self.assertFalse(preferences.rules["task_due"]["inbox"])
        self.assertFalse(preferences.rules["task_due"]["digest"])
        self.assertTrue(preferences.rules["frost_warning"]["panel"])

    def test_guardrails_keep_high_critical_safety_frost_security_and_system_visible(self) -> None:
        from gardenops.services.attention.preferences import (
            AttentionPreferenceSet,
            apply_preferences,
        )

        preferences = AttentionPreferenceSet(
            user_id=1,
            preset="custom",
            rules={
                "default": {"panel": False, "inbox": False, "digest": False},
                "safety_alert": {"panel": False, "inbox": False, "digest": False},
                "frost_warning": {"panel": False, "inbox": False, "digest": False},
                "security_alert": {"panel": False, "inbox": False, "digest": False},
                "system": {"panel": False, "inbox": False, "digest": False},
            },
        )
        items = [
            self._item(item_type="safety_alert", category="warning", severity="high"),
            self._item(item_type="frost_warning", category="warning", severity="high"),
            self._item(item_type="security_alert", category="warning", severity="critical"),
            self._item(item_type="system", category="system", severity="high"),
            self._item(
                item_type="rain_alert",
                category="warning",
                severity="high",
                metadata={"guardrail": True},
            ),
            self._item(item_type="rain_alert_muted", category="warning", severity="high"),
        ]

        visible = apply_preferences(items, preferences, surface="panel")

        visible_ids = {item.id for item in visible}
        self.assertIn("attn:test:safety_alert", visible_ids)
        self.assertIn("attn:test:frost_warning", visible_ids)
        self.assertIn("attn:test:security_alert", visible_ids)
        self.assertIn("attn:test:system", visible_ids)
        self.assertIn("attn:test:rain_alert", visible_ids)
        self.assertNotIn("attn:test:rain_alert_muted", visible_ids)
        self.assertTrue(
            all(
                item.metadata.get("preference_guardrail") is True
                for item in visible
                if item.id != "attn:test:rain_alert_muted"
            )
        )

    def test_calm_balanced_and_detailed_surface_differences(self) -> None:
        from gardenops.services.attention.preferences import (
            apply_preferences,
            resolve_attention_preferences,
        )

        needs_action = self._item(item_type="task_due", category="needs_action")
        warning_normal = self._item(item_type="rain_alert", category="warning")
        upcoming_high = self._item(
            item_type="calendar_event_due",
            category="upcoming",
            severity="high",
        )

        def visible_ids(preset: str, surface: str) -> set[str]:
            preferences = resolve_attention_preferences(
                user_id=1,
                legacy_preferences=None,
                saved_attention_preferences={"preset": preset},
            )
            return {
                item.id
                for item in apply_preferences(
                    [needs_action, warning_normal, upcoming_high],
                    preferences,
                    surface=surface,
                )
            }

        self.assertEqual(visible_ids("calm", "panel"), {"attn:test:task_due"})
        self.assertEqual(visible_ids("calm", "inbox"), set())
        self.assertEqual(visible_ids("calm", "digest"), set())

        self.assertEqual(
            visible_ids("balanced", "panel"),
            {
                "attn:test:task_due",
                "attn:test:rain_alert",
                "attn:test:calendar_event_due",
            },
        )
        self.assertEqual(
            visible_ids("balanced", "inbox"),
            {"attn:test:task_due", "attn:test:rain_alert"},
        )
        self.assertEqual(visible_ids("balanced", "digest"), {"attn:test:rain_alert"})

        self.assertEqual(
            visible_ids("detailed", "panel"),
            {
                "attn:test:task_due",
                "attn:test:rain_alert",
                "attn:test:calendar_event_due",
            },
        )
        self.assertEqual(
            visible_ids("detailed", "inbox"),
            {
                "attn:test:task_due",
                "attn:test:rain_alert",
                "attn:test:calendar_event_due",
            },
        )
        self.assertEqual(
            visible_ids("detailed", "digest"),
            {"attn:test:task_due", "attn:test:rain_alert"},
        )


class TestAttentionMutations(BaseApiTest):
    def _garden_user_and_task(self, public_id: str = "task_attention_mutation") -> tuple[int, int]:
        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"]
            )
            user_id = int(
                conn.execute("SELECT id FROM auth_users WHERE username = 'test_admin'").fetchone()[
                    "id"
                ]
            )
            conn.execute(
                """
                INSERT INTO garden_tasks
                (public_id, garden_id, task_type, title, description, status, severity, due_on,
                 rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES (%s, %s, 'water', 'Water mutation basil', '', 'pending', 'normal',
                        '2026-07-05', '', '{}', 1, 1)
                """,
                (public_id, garden_id),
            )
            conn.commit()
            return garden_id, user_id
        finally:
            db.return_db(conn)

    def _create_tier_user(
        self,
        username: str,
        tier: str,
        *,
        role: str = "editor",
    ) -> tuple[int, str]:
        password = f"{username}-pass"
        user = self._create_test_user(username, password, role=role)
        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE auth_users SET subscription_tier = %s WHERE id = %s",
                (tier, int(user["id"])),
            )
            conn.commit()
            return int(user["id"]), password
        finally:
            db.return_db(conn)

    @staticmethod
    def _feed_item_ids(body: dict) -> set[str]:
        return {item["id"] for section in body["sections"] for item in section["items"]}

    @staticmethod
    def _feed_item(body: dict, item_id: str) -> dict:
        return next(
            item
            for section in body["sections"]
            for item in section["items"]
            if item["id"] == item_id
        )

    def test_read_dismiss_and_snooze_are_user_scoped(self) -> None:
        garden_id, admin_id = self._garden_user_and_task()
        other_id, other_password = self._create_tier_user("attention_other", "pro")
        item_id = "attn:task:task_attention_mutation"

        with patch.dict(
            "os.environ",
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            admin_client, admin_headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
                garden_id=garden_id,
            )
            other_client, other_headers = self._authenticated_client(
                "attention_other",
                other_password,
                garden_id=garden_id,
            )

            read = admin_client.post(f"/api/attention/items/{item_id}/read", headers=admin_headers)
            self.assertEqual(read.status_code, 200)
            admin_visible = admin_client.get("/api/attention/today", headers=admin_headers)
            self.assertEqual(admin_visible.status_code, 200)
            self.assertEqual(
                self._feed_item(admin_visible.json(), item_id)["user_state"],
                "read",
            )
            other_visible = other_client.get("/api/attention/today", headers=other_headers)
            self.assertEqual(other_visible.status_code, 200)
            self.assertEqual(
                self._feed_item(other_visible.json(), item_id)["user_state"],
                "unread",
            )

            dismissed = admin_client.post(
                f"/api/attention/items/{item_id}/dismiss",
                headers=admin_headers,
            )
            self.assertEqual(dismissed.status_code, 200)
            admin_hidden = admin_client.get("/api/attention/today", headers=admin_headers)
            self.assertNotIn(item_id, self._feed_item_ids(admin_hidden.json()))
            other_still_visible = other_client.get("/api/attention/today", headers=other_headers)
            self.assertIn(item_id, self._feed_item_ids(other_still_visible.json()))

            snoozed = other_client.post(
                f"/api/attention/items/{item_id}/snooze",
                headers=other_headers,
                json={"snoozed_until_ms": 1783267200000, "reason": "later"},
            )
            self.assertEqual(snoozed.status_code, 200)
            other_hidden = other_client.get("/api/attention/today", headers=other_headers)
            self.assertNotIn(item_id, self._feed_item_ids(other_hidden.json()))

        conn = db.get_db()
        try:
            rows = conn.execute(
                """
                SELECT user_id, user_state
                FROM user_attention_item_state
                WHERE garden_id = %s AND item_id = %s
                ORDER BY user_id ASC
                """,
                (garden_id, item_id),
            ).fetchall()
        finally:
            db.return_db(conn)

        self.assertEqual(
            [(int(row["user_id"]), str(row["user_state"])) for row in rows],
            [(admin_id, "dismissed"), (other_id, "snoozed")],
        )

    def test_viewer_can_save_personal_attention_state_and_preferences_without_task_write_access(
        self,
    ) -> None:
        garden_id, _admin_id = self._garden_user_and_task("task_attention_viewer_personal")
        viewer_id, viewer_password = self._create_tier_user(
            "attention_viewer_personal",
            "pro",
            role="viewer",
        )
        item_id = "attn:task:task_attention_viewer_personal"

        with patch.dict(
            "os.environ",
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            viewer_client, viewer_headers = self._authenticated_client(
                "attention_viewer_personal",
                viewer_password,
                garden_id=garden_id,
            )
            preferences = viewer_client.put(
                "/api/attention/preferences",
                headers=viewer_headers,
                json={
                    "preset": "calm",
                    "rules": {},
                    "quiet_hours": {},
                    "metadata": {"viewer_personal": True},
                },
            )
            self.assertEqual(preferences.status_code, 200, preferences.text)
            self.assertEqual(preferences.json()["metadata"]["viewer_personal"], True)
            self.assertEqual(
                viewer_client.post(
                    f"/api/attention/items/{item_id}/read",
                    headers=viewer_headers,
                ).status_code,
                200,
            )
            self.assertEqual(
                viewer_client.post(
                    f"/api/attention/items/{item_id}/dismiss",
                    headers=viewer_headers,
                ).status_code,
                200,
            )
            self.assertEqual(
                viewer_client.post(
                    f"/api/attention/items/{item_id}/snooze",
                    headers=viewer_headers,
                    json={"snoozed_until_ms": 1783267200000},
                ).status_code,
                200,
            )
            task_write = viewer_client.post(
                "/api/tasks/task_attention_viewer_personal/action",
                headers=viewer_headers,
                json={"action": "snooze", "snooze_until": "2026-07-06"},
            )
            self.assertEqual(task_write.status_code, 403, task_write.text)

        conn = db.get_db()
        try:
            saved = conn.execute(
                "SELECT preset, metadata_json FROM user_attention_preferences WHERE user_id = %s",
                (viewer_id,),
            ).fetchone()
            state = conn.execute(
                """
                SELECT user_state FROM user_attention_item_state
                WHERE garden_id = %s AND user_id = %s AND item_id = %s
                """,
                (garden_id, viewer_id, item_id),
            ).fetchone()
        finally:
            db.return_db(conn)

        assert saved is not None
        assert state is not None
        self.assertEqual(str(saved["preset"]), "calm")
        self.assertIn("viewer_personal", str(saved["metadata_json"]))
        self.assertEqual(str(state["user_state"]), "snoozed")

    def test_home_tier_is_forbidden_and_paid_tiers_reach_real_attention_behavior(self) -> None:
        garden_id, _user_id = self._garden_user_and_task("task_attention_tier")
        item_id = "attn:task:task_attention_tier"
        _home_id, home_password = self._create_tier_user("attention_home", "home")
        _enthusiast_id, enthusiast_password = self._create_tier_user(
            "attention_enthusiast",
            "enthusiast",
        )
        _pro_id, pro_password = self._create_tier_user("attention_pro", "pro")

        with patch.dict(
            "os.environ",
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            home_client, home_headers = self._authenticated_client(
                "attention_home",
                home_password,
                garden_id=garden_id,
            )
            self.assertEqual(home_client.get("/api/attention/today").status_code, 403)
            self.assertEqual(
                home_client.put(
                    "/api/attention/preferences",
                    headers=home_headers,
                    json={"preset": "calm", "rules": {}, "quiet_hours": {}},
                ).status_code,
                403,
            )
            self.assertEqual(
                home_client.post(
                    f"/api/attention/items/{item_id}/read", headers=home_headers
                ).status_code,
                403,
            )

            enthusiast_client, enthusiast_headers = self._authenticated_client(
                "attention_enthusiast",
                enthusiast_password,
                garden_id=garden_id,
            )
            enthusiast_feed = enthusiast_client.get(
                "/api/attention/today",
                headers=enthusiast_headers,
            )
            self.assertEqual(enthusiast_feed.status_code, 200)
            self.assertIn(item_id, self._feed_item_ids(enthusiast_feed.json()))

            pro_client, pro_headers = self._authenticated_client(
                "attention_pro",
                pro_password,
                garden_id=garden_id,
            )
            missing = pro_client.post(
                "/api/attention/items/attn:task:missing/read",
                headers=pro_headers,
            )
            self.assertEqual(missing.status_code, 404)

    def test_unsupported_outcome_restore_returns_409(self) -> None:
        from gardenops.services.attention.outcomes import upsert_attention_outcome

        garden_id, _user_id = self._garden_user_and_task("task_attention_restore_unsupported")
        conn = db.get_db()
        try:
            outcome_id = upsert_attention_outcome(
                conn,
                garden_id=garden_id,
                provider="weather",
                outcome_type="rain_alert_deduped",
                source_type="weather_alert",
                source_id="1",
                source_public_id="alert:1",
                target_type="weather_alert",
                target_id="1",
                title="Rain alert deduped",
                explanation="Already covered.",
                occurred_at_ms=1783180800000,
                expires_at_ms=1785772800000,
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            "os.environ",
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            client, headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
                garden_id=garden_id,
            )
            response = client.post(
                f"/api/attention/outcomes/{outcome_id}/restore",
                headers=headers,
            )

        self.assertEqual(response.status_code, 409)

    def test_supported_watering_restore_validates_recovery_action_without_notification_writes(
        self,
    ) -> None:
        from gardenops.services.attention.outcomes import upsert_attention_outcome

        garden_id, user_id = self._garden_user_and_task("task_attention_restore_supported")
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO plants
                    (plt_id, name, latin, category, bloom_month, color, hardiness,
                     height_cm, light, link, care_watering, care_soil, care_planting,
                     care_maintenance, care_notes)
                VALUES ('RESTORE', 'Restore hydrangea', '', 'busker', '', '', '',
                        NULL, '', '', 'regular moisture', '', '', '', '')
                ON CONFLICT (plt_id) DO NOTHING
                """,
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES ('RESTORE', %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (user_id, garden_id),
            )
            conn.execute(
                """
                INSERT INTO plots
                    (plot_id, garden_id, zone_code, zone_name, plot_number,
                     grid_row, grid_col, sub_zone, notes)
                VALUES ('B1', %s, 'B', 'Beds', 1, 2, 2, '', '')
                ON CONFLICT (plot_id) DO UPDATE SET
                    garden_id = excluded.garden_id,
                    grid_row = excluded.grid_row,
                    grid_col = excluded.grid_col
                """,
                (garden_id,),
            )
            outcome_id = upsert_attention_outcome(
                conn,
                garden_id=garden_id,
                provider="weather",
                outcome_type="watering_covered_by_rain",
                source_type="task_generator",
                source_id="77",
                source_public_id="water:RESTORE:2026-07-05",
                target_type="plant",
                target_id="RESTORE",
                title="Watering covered by rain",
                explanation="Rain covered watering.",
                reason="Rain surplus covers the watering date",
                plant_ids=("RESTORE",),
                plot_ids=("B1",),
                metadata={"due_on": "2026-07-05", "rain_mm": 18},
                recovery_action={
                    "kind": "restore_generated_watering_task",
                    "label": "Restore watering",
                    "source_public_id": "water:RESTORE:2026-07-05",
                    "target_type": "plant",
                    "target_id": "RESTORE",
                    "due_on": "2026-07-05",
                    "plant_ids": ["RESTORE"],
                    "plot_ids": ["B1"],
                },
                occurred_at_ms=1783180800000,
                expires_at_ms=1785772800000,
            )
            conn.execute(
                """
                INSERT INTO notification_events
                    (public_id, garden_id, user_id, notification_type, title, body,
                     target_type, target_id, read_at_ms, emailed_at_ms, metadata_json,
                     dismissed, created_at_ms, notification_subtype, severity, expires_at_ms,
                     cleared_at_ms, clear_reason, superseded_by_id)
                VALUES
                    ('note_attention_restore_guard', %s, %s, 'weather', 'Rain', '',
                     'plant', 'RESTORE', NULL, NULL, '{}', 0, 1, 'rain_surplus',
                     'normal', NULL, NULL, NULL, NULL)
                """,
                (garden_id, user_id),
            )
            before = conn.execute(
                """
                SELECT COUNT(*) AS c,
                       COALESCE(SUM(dismissed), 0) AS dismissed_count,
                       COUNT(cleared_at_ms) AS cleared_count
                FROM notification_events
                WHERE garden_id = %s
                """,
                (garden_id,),
            ).fetchone()
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            "os.environ",
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            client, headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
                garden_id=garden_id,
            )
            response = client.post(
                f"/api/attention/outcomes/{outcome_id}/restore",
                headers=headers,
            )

            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"status": "restored"})

        conn = db.get_db()
        try:
            restored_task = conn.execute(
                """
                SELECT id, public_id, task_type, title, status, due_on, rule_source
                FROM garden_tasks
                WHERE garden_id = %s
                  AND rule_source = 'water:RESTORE:2026-07-05'
                """,
                (garden_id,),
            ).fetchone()
            assert restored_task is not None
            restored_plants = [
                str(row["plt_id"])
                for row in conn.execute(
                    "SELECT plt_id FROM garden_task_plants WHERE task_id = %s ORDER BY plt_id",
                    (int(restored_task["id"]),),
                ).fetchall()
            ]
            restored_plots = [
                str(row["plot_id"])
                for row in conn.execute(
                    "SELECT plot_id FROM garden_task_plots WHERE task_id = %s ORDER BY plot_id",
                    (int(restored_task["id"]),),
                ).fetchall()
            ]
            outcome = conn.execute(
                """
                SELECT expires_at_ms
                FROM attention_outcomes
                WHERE public_id = %s
                """,
                (outcome_id,),
            ).fetchone()
            after = conn.execute(
                """
                SELECT COUNT(*) AS c,
                       COALESCE(SUM(dismissed), 0) AS dismissed_count,
                       COUNT(cleared_at_ms) AS cleared_count
                FROM notification_events
                WHERE garden_id = %s
                """,
                (garden_id,),
            ).fetchone()
        finally:
            db.return_db(conn)

        self.assertEqual(str(restored_task["task_type"]), "water")
        self.assertEqual(str(restored_task["status"]), "pending")
        self.assertEqual(str(restored_task["due_on"]), "2026-07-05")
        self.assertEqual(restored_plants, ["RESTORE"])
        self.assertEqual(restored_plots, ["B1"])
        self.assertEqual(int(outcome["expires_at_ms"]), 1783180799999)
        self.assertEqual(dict(after), dict(before))

    def test_supported_watering_restore_moves_existing_rescheduled_task_back(
        self,
    ) -> None:
        from gardenops.services.attention.outcomes import (
            read_active_attention_outcomes,
            upsert_attention_outcome,
        )

        garden_id, _user_id = self._garden_user_and_task("task_attention_restore_existing")
        conn = db.get_db()
        try:
            owner_id = int(
                conn.execute("SELECT id FROM auth_users WHERE username = 'test_admin'").fetchone()[
                    "id"
                ]
            )
            conn.execute(
                """
                INSERT INTO plants
                    (plt_id, name, latin, category, bloom_month, color,
                     hardiness, height_cm, light, link, year_planted,
                     care_watering, care_soil, care_planting, care_maintenance,
                     care_notes)
                VALUES ('RESTORE2', 'Restore hydrangea', '', 'stauder', '', '',
                        '', NULL, '', '', NULL, 'regular moisture', '', '', '', '')
                ON CONFLICT (plt_id) DO NOTHING
                """
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES ('RESTORE2', %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (owner_id, garden_id),
            )
            task = conn.execute(
                """
                INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, description, status,
                     severity, due_on, rule_source, metadata_json,
                     created_at_ms, updated_at_ms)
                VALUES ('task_attention_restore_existing', %s, 'water',
                        'Water restore hydrangea', '', 'pending', 'normal',
                        '2026-07-08', 'water:RESTORE2:2026-07-05',
                        '{"rescheduled_from":"2026-07-05","rescheduled_reason":"rain_alert"}',
                        1, 1)
                ON CONFLICT (public_id) DO UPDATE SET
                    garden_id = excluded.garden_id,
                    task_type = excluded.task_type,
                    title = excluded.title,
                    description = excluded.description,
                    status = excluded.status,
                    severity = excluded.severity,
                    due_on = excluded.due_on,
                    snoozed_until = NULL,
                    rule_source = excluded.rule_source,
                    metadata_json = excluded.metadata_json,
                    completed_by_user_id = NULL,
                    completed_at_ms = NULL,
                    updated_at_ms = excluded.updated_at_ms
                RETURNING id
                """,
                (garden_id,),
            ).fetchone()
            assert task is not None
            conn.execute(
                """
                INSERT INTO garden_task_plants (task_id, plt_id)
                VALUES (%s, 'RESTORE2')
                ON CONFLICT DO NOTHING
                """,
                (int(task["id"]),),
            )
            outcome_id = upsert_attention_outcome(
                conn,
                garden_id=garden_id,
                provider="weather",
                outcome_type="watering_rescheduled_by_rain",
                source_type="task_generator",
                source_id="77",
                source_public_id="water:RESTORE2:2026-07-05",
                target_type="plant",
                target_id="RESTORE2",
                title="Watering rescheduled by rain",
                explanation="Rain moved watering.",
                reason="Rain rescheduled watering",
                plant_ids=("RESTORE2",),
                metadata={"due_on": "2026-07-05", "new_due_on": "2026-07-08"},
                recovery_action={
                    "kind": "restore_generated_watering_task",
                    "label": "Restore watering",
                    "source_public_id": "water:RESTORE2:2026-07-05",
                    "target_type": "plant",
                    "target_id": "RESTORE2",
                    "due_on": "2026-07-05",
                    "plant_ids": ["RESTORE2"],
                    "plot_ids": [],
                },
                occurred_at_ms=1783180800000,
                expires_at_ms=1785772800000,
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            "os.environ",
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            client, headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
                garden_id=garden_id,
            )
            response = client.post(
                f"/api/attention/outcomes/{outcome_id}/restore",
                headers=headers,
            )

        self.assertEqual(response.status_code, 200, response.text)

        conn = db.get_db()
        try:
            task_after = conn.execute(
                """
                SELECT due_on, status, metadata_json
                FROM garden_tasks
                WHERE garden_id = %s
                  AND rule_source = 'water:RESTORE2:2026-07-05'
                """,
                (garden_id,),
            ).fetchone()
            assert task_after is not None
            active_outcomes = read_active_attention_outcomes(
                conn,
                garden_id=garden_id,
                provider="weather",
                outcome_types=("watering_rescheduled_by_rain",),
                now_ms=1783180800000,
            )
        finally:
            db.return_db(conn)

        metadata = json.loads(str(task_after["metadata_json"]))
        self.assertEqual(str(task_after["due_on"]), "2026-07-05")
        self.assertEqual(str(task_after["status"]), "pending")
        self.assertEqual(metadata["restored_from_attention_outcome"], outcome_id)
        self.assertEqual(metadata["restored_due_on_from"], "2026-07-08")
        self.assertEqual(active_outcomes, [])

    def test_watering_restore_rejects_stale_cross_garden_plant_and_plot_scope(self) -> None:
        from gardenops.services.attention.outcomes import upsert_attention_outcome

        garden_id, user_id = self._garden_user_and_task("task_attention_restore_scope_guard")
        conn = db.get_db()
        try:
            foreign_garden = conn.execute(
                "INSERT INTO gardens (slug, name) VALUES ('restore-foreign', 'Foreign') "
                "RETURNING id",
            ).fetchone()
            assert foreign_garden is not None
            foreign_garden_id = int(foreign_garden["id"])
            conn.execute(
                """
                INSERT INTO plants (plt_id, name, category)
                VALUES
                    ('RESTORE-VALID', 'Valid restore plant', 'test'),
                    ('RESTORE-FOREIGN', 'Foreign restore plant', 'test')
                """,
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES
                    ('RESTORE-VALID', %s, %s),
                    ('RESTORE-FOREIGN', %s, %s)
                """,
                (user_id, garden_id, user_id, foreign_garden_id),
            )
            conn.execute(
                """
                INSERT INTO plots
                    (plot_id, garden_id, zone_code, zone_name, plot_number,
                     grid_row, grid_col, sub_zone, notes)
                VALUES ('RESTORE-FOREIGN-PLOT', %s, 'F', 'Foreign', 1, 1, 1, '', '')
                """,
                (foreign_garden_id,),
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES ('RESTORE-FOREIGN-PLOT', %s, %s)
                """,
                (user_id, foreign_garden_id),
            )
            foreign_plot_outcome = upsert_attention_outcome(
                conn,
                garden_id=garden_id,
                provider="weather",
                outcome_type="watering_covered_by_rain",
                source_type="task_generator",
                source_id="scope-plot",
                source_public_id="water:RESTORE-VALID:2026-07-05",
                target_type="plant",
                target_id="RESTORE-VALID",
                title="Watering covered by rain",
                explanation="Rain covered watering.",
                plant_ids=("RESTORE-VALID",),
                plot_ids=("RESTORE-FOREIGN-PLOT",),
                metadata={"due_on": "2026-07-05"},
                recovery_action={
                    "kind": "restore_generated_watering_task",
                    "label": "Restore watering",
                    "source_public_id": "water:RESTORE-VALID:2026-07-05",
                    "target_type": "plant",
                    "target_id": "RESTORE-VALID",
                    "due_on": "2026-07-05",
                    "plant_ids": ["RESTORE-VALID"],
                    "plot_ids": ["RESTORE-FOREIGN-PLOT"],
                },
                occurred_at_ms=1783180800000,
                expires_at_ms=1785772800000,
            )
            foreign_plant_outcome = upsert_attention_outcome(
                conn,
                garden_id=garden_id,
                provider="weather",
                outcome_type="watering_covered_by_rain",
                source_type="task_generator",
                source_id="scope-plant",
                source_public_id="water:RESTORE-FOREIGN:2026-07-05",
                target_type="plant",
                target_id="RESTORE-FOREIGN",
                title="Watering covered by rain",
                explanation="Rain covered watering.",
                plant_ids=("RESTORE-FOREIGN",),
                metadata={"due_on": "2026-07-05"},
                recovery_action={
                    "kind": "restore_generated_watering_task",
                    "label": "Restore watering",
                    "source_public_id": "water:RESTORE-FOREIGN:2026-07-05",
                    "target_type": "plant",
                    "target_id": "RESTORE-FOREIGN",
                    "due_on": "2026-07-05",
                    "plant_ids": ["RESTORE-FOREIGN"],
                    "plot_ids": [],
                },
                occurred_at_ms=1783180800000,
                expires_at_ms=1785772800000,
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            "os.environ",
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            client, headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
                garden_id=garden_id,
            )
            plot_response = client.post(
                f"/api/attention/outcomes/{foreign_plot_outcome}/restore",
                headers=headers,
            )
            plant_response = client.post(
                f"/api/attention/outcomes/{foreign_plant_outcome}/restore",
                headers=headers,
            )

        self.assertEqual(plot_response.status_code, 409, plot_response.text)
        self.assertEqual(plant_response.status_code, 409, plant_response.text)
        conn = db.get_db()
        try:
            tasks = conn.execute(
                """
                SELECT public_id
                FROM garden_tasks
                WHERE rule_source IN (
                    'water:RESTORE-VALID:2026-07-05',
                    'water:RESTORE-FOREIGN:2026-07-05'
                )
                """,
            ).fetchall()
        finally:
            db.return_db(conn)
        self.assertEqual(tasks, [])

    def test_watering_restore_rolls_back_implicit_cross_garden_plot_link(self) -> None:
        from gardenops.services.attention.outcomes import upsert_attention_outcome

        garden_id, user_id = self._garden_user_and_task("task_attention_restore_link_guard")
        with patch.dict(
            "os.environ",
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            client, headers = self._authenticated_client(
                "test_admin",
                "testadminpass",
                garden_id=garden_id,
            )
            conn = db.get_db()
            try:
                foreign_garden = conn.execute(
                    "INSERT INTO gardens (slug, name) VALUES ('restore-link-foreign', 'Foreign') "
                    "RETURNING id",
                ).fetchone()
                assert foreign_garden is not None
                foreign_garden_id = int(foreign_garden["id"])
                conn.execute(
                    "INSERT INTO plants (plt_id, name, category) "
                    "VALUES ('RESTORE-LINK', 'Restore link plant', 'test')",
                )
                conn.execute(
                    "INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id) "
                    "VALUES ('RESTORE-LINK', %s, %s)",
                    (user_id, garden_id),
                )
                conn.execute(
                    """
                    INSERT INTO plots
                        (plot_id, garden_id, zone_code, zone_name, plot_number,
                         grid_row, grid_col, sub_zone, notes)
                    VALUES ('RESTORE-LINK-PLOT', %s, 'R', 'Restore', 1, 8, 8, '', '')
                    """,
                    (garden_id,),
                )
                conn.execute(
                    "INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id) "
                    "VALUES ('RESTORE-LINK-PLOT', %s, %s)",
                    (user_id, foreign_garden_id),
                )
                conn.execute(
                    "UPDATE plots SET garden_id = %s WHERE plot_id = 'RESTORE-LINK-PLOT'",
                    (garden_id,),
                )
                conn.execute(
                    "INSERT INTO plot_plants (plot_id, plt_id, quantity) "
                    "VALUES ('RESTORE-LINK-PLOT', 'RESTORE-LINK', 1)",
                )
                outcome_id = upsert_attention_outcome(
                    conn,
                    garden_id=garden_id,
                    provider="weather",
                    outcome_type="watering_covered_by_rain",
                    source_type="task_generator",
                    source_id="scope-generated-link",
                    source_public_id="water:RESTORE-LINK:2026-07-05",
                    target_type="plant",
                    target_id="RESTORE-LINK",
                    title="Watering covered by rain",
                    explanation="Rain covered watering.",
                    plant_ids=("RESTORE-LINK",),
                    metadata={"due_on": "2026-07-05"},
                    recovery_action={
                        "kind": "restore_generated_watering_task",
                        "label": "Restore watering",
                        "source_public_id": "water:RESTORE-LINK:2026-07-05",
                        "target_type": "plant",
                        "target_id": "RESTORE-LINK",
                        "due_on": "2026-07-05",
                        "plant_ids": ["RESTORE-LINK"],
                        "plot_ids": [],
                    },
                    occurred_at_ms=1783180800000,
                    expires_at_ms=1785772800000,
                )
                conn.commit()
            finally:
                db.return_db(conn)

            response = client.post(
                f"/api/attention/outcomes/{outcome_id}/restore",
                headers=headers,
            )

        self.assertEqual(response.status_code, 409, response.text)
        conn = db.get_db()
        try:
            task = conn.execute(
                "SELECT id FROM garden_tasks WHERE rule_source = %s",
                ("water:RESTORE-LINK:2026-07-05",),
            ).fetchone()
            outcome = conn.execute(
                "SELECT expires_at_ms FROM attention_outcomes WHERE public_id = %s",
                (outcome_id,),
            ).fetchone()
        finally:
            db.return_db(conn)
        self.assertIsNone(task)
        self.assertIsNotNone(outcome)
        self.assertGreater(int(outcome["expires_at_ms"]), 1783180800000)


class TestAttentionTodayApi(BaseApiTest):
    def _seed_due_task(self, public_id: str = "task_attention_auth") -> None:
        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"]
            )
            conn.execute(
                """
                INSERT INTO garden_tasks
                (public_id, garden_id, task_type, title, description, status, severity, due_on,
                 rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES (%s, %s, 'water', 'Water basil', '', 'pending', 'normal',
                        '2026-07-05', '', '{}', 1, 1)
                """,
                (public_id, garden_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

    def test_today_returns_bounded_sections_and_stable_ids(self) -> None:
        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"]
            )
            for idx in range(7):
                conn.execute(
                    """
                    INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, description, status, severity, due_on,
                     rule_source, metadata_json, created_at_ms, updated_at_ms)
                    VALUES (%s, %s, 'water', %s, '', 'pending', 'normal', '2026-07-05',
                            '', '{}', 1, 1)
                    """,
                    (f"task_due_{idx}", garden_id, f"Water plant {idx}"),
                )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            "os.environ",
            {
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            r = self.client.get("/api/attention/today")

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["sections"][0]["key"], "needs_attention")
        self.assertLessEqual(len(body["sections"][0]["items"]), 5)
        self.assertTrue(body["sections"][0]["items"][0]["id"].startswith("attn:"))

    def test_today_uses_task_provider_only_in_first_slice(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            r = self.client.get("/api/attention/today")
        self.assertEqual(r.status_code, 200)
        providers = {
            item["provider"] for section in r.json()["sections"] for item in section["items"]
        }
        self.assertLessEqual(providers, {"task"})

    def test_today_includes_rain_alert_and_no_action_watering_outcome(self) -> None:
        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"]
            )
            alert = conn.execute(
                """
                INSERT INTO weather_alerts
                    (garden_id, alert_type, severity, title, description,
                     valid_from, valid_until, metadata_json, created_at_ms)
                VALUES (%s, 'rain_surplus', 'high', 'Heavy rain expected',
                        'Skip watering and check drainage', '2026-07-05',
                        '2026-07-07', %s, 1)
                RETURNING id
                """,
                (garden_id, json.dumps({"total_mm": 24.0})),
            ).fetchone()
            from gardenops.services.attention.outcomes import upsert_attention_outcome

            upsert_attention_outcome(
                conn,
                garden_id=garden_id,
                provider="weather",
                outcome_type="watering_covered_by_rain",
                source_type="task_generator",
                source_id=str(alert["id"]),
                source_public_id="water:RAIN1:2026-07-05",
                target_type="plant",
                target_id="RAIN1",
                title="Watering covered by rain",
                explanation="24 mm rain already covers the scheduled watering for Hydrangea.",
                reason="Rain surplus covers the watering date",
                plant_ids=("RAIN1",),
                plot_ids=("A1",),
                metadata={"due_on": "2026-07-05", "rain_mm": 24.0},
                occurred_at_ms=1783180800000,
                expires_at_ms=1785772800000,
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            "os.environ",
            {
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            r = self.client.get("/api/attention/today")

        self.assertEqual(r.status_code, 200)
        sections = {section["key"]: section["items"] for section in r.json()["sections"]}
        warning = next(item for item in sections["warnings"] if item["provider"] == "weather")
        self.assertEqual(warning["type"], "rain_alert")
        self.assertIsNone(warning["primary_action"])
        no_action = next(
            item
            for item in sections["no_action_needed"]
            if item["type"] == "watering_covered_by_rain"
        )
        self.assertIn("24 mm rain", no_action["body"])
        self.assertEqual(no_action["metadata"]["due_on"], "2026-07-05")

    def test_today_watering_rain_preference_can_show_rain_handled_task(self) -> None:
        from gardenops.services.attention import AttentionService
        from gardenops.services.attention.outcomes import upsert_attention_outcome

        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"]
            )
            user_id = int(
                conn.execute("SELECT id FROM auth_users WHERE username = 'test_admin'").fetchone()[
                    "id"
                ]
            )
            conn.execute(
                """
                INSERT INTO plots
                    (plot_id, garden_id, zone_code, zone_name, plot_number,
                     grid_row, grid_col, sub_zone, notes)
                VALUES ('OUT-RAIN-PREF', %s, 'B', 'Bed', 98, 8, 9, '', '')
                """,
                (garden_id,),
            )
            task = conn.execute(
                """
                INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, description, status, severity,
                     due_on, snoozed_until, rule_source, metadata_json,
                     created_at_ms, updated_at_ms)
                VALUES ('task_rain_pref_visible', %s, 'water', 'Water outdoor basil', '',
                        'pending', 'normal', '2026-07-05', NULL,
                        'water:RAINPREF:2026-07-05', '{}', 1, 1)
                RETURNING id
                """,
                (garden_id,),
            ).fetchone()
            assert task is not None
            conn.execute(
                "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, 'OUT-RAIN-PREF')",
                (int(task["id"]),),
            )
            upsert_attention_outcome(
                conn,
                garden_id=garden_id,
                provider="weather",
                outcome_type="watering_covered_by_rain",
                source_type="task_generator",
                source_id="1",
                source_public_id="water:RAINPREF:2026-07-05",
                target_type="plant",
                target_id="RAINPREF",
                title="Watering covered by rain",
                explanation="18 mm rain covers this watering.",
                reason="Rain surplus covers the watering date",
                plant_ids=("RAINPREF",),
                plot_ids=(),
                metadata={"due_on": "2026-07-05", "rain_mm": 18},
                occurred_at_ms=1783180800000,
                expires_at_ms=1785772800000,
            )
            conn.execute(
                """
                INSERT INTO user_attention_preferences
                    (user_id, preset, rules_json, quiet_hours_json, show_no_action_history,
                     metadata_json, created_at_ms, updated_at_ms)
                VALUES (%s, 'balanced', '{}', '{}', 1, %s, 1, 1)
                """,
                (
                    user_id,
                    json.dumps({"weather_aware_watering_suppression": False}),
                ),
            )
            conn.commit()

            response = AttentionService(frozen_date="2026-07-05").today(
                conn,
                garden_id=garden_id,
                user_id=user_id,
                now_ms=1783180800000,
            )
        finally:
            db.return_db(conn)

        item_ids = {item["id"] for section in response["sections"] for item in section["items"]}
        self.assertIn("attn:task:task_rain_pref_visible", item_ids)

    def test_today_hides_stale_generated_watering_but_keeps_manual_overdue(self) -> None:
        from gardenops.services.attention import AttentionService

        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"]
            )
            user_id = int(
                conn.execute("SELECT id FROM auth_users WHERE username = 'test_admin'").fetchone()[
                    "id"
                ]
            )
            conn.execute(
                """
                INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, description, status, severity,
                     due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES
                    ('task_attention_stale_generated_water', %s, 'water',
                     'Generated old water', '', 'pending', 'normal',
                     '2026-07-04', 'water:ATTN-STALE:2026-07-04', '{}', 1, 1),
                    ('task_attention_stale_generated_dry_water', %s, 'water',
                     'Generated old dry water', '', 'pending', 'normal',
                     '2026-07-04', 'auto:dry_water:456:ATTN-STALE', '{}', 1, 1),
                    ('task_attention_manual_old_water', %s, 'water',
                     'Manual old water', '', 'pending', 'normal',
                     '2026-07-04', '', '{}', 1, 1)
                """,
                (garden_id, garden_id, garden_id),
            )
            conn.commit()

            response = AttentionService(frozen_date="2026-07-05").today(
                conn,
                garden_id=garden_id,
                user_id=user_id,
                now_ms=1783180800000,
            )
        finally:
            db.return_db(conn)

        item_ids = {item["id"] for section in response["sections"] for item in section["items"]}
        self.assertIn("attn:task:task_attention_manual_old_water", item_ids)
        self.assertNotIn("attn:task:task_attention_stale_generated_water", item_ids)
        self.assertNotIn("attn:task:task_attention_stale_generated_dry_water", item_ids)

    def test_today_shows_recently_expired_generated_watering_as_no_action_history(self) -> None:
        from gardenops.services.attention import AttentionService, TaskAttentionProvider

        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"]
            )
            user_id = int(
                conn.execute("SELECT id FROM auth_users WHERE username = 'test_admin'").fetchone()[
                    "id"
                ]
            )
            conn.execute(
                """
                INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, description, status, severity,
                     due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES
                    ('task_attention_expired_generated_water', %s, 'water',
                     'Generated expired water', '', 'expired', 'normal',
                     '2026-07-04', 'water:ATTN-EXPIRED:2026-07-04',
                     '{"lifecycle":{"status":"expired","reason":"stale_generated_watering"}}',
                     1, 1783180800000)
                """,
                (garden_id,),
            )
            conn.commit()

            response = AttentionService(
                providers=[TaskAttentionProvider(frozen_date="2026-07-05")]
            ).today(
                conn,
                garden_id=garden_id,
                user_id=user_id,
                now_ms=1783180800000,
            )
        finally:
            db.return_db(conn)

        sections = {section["key"]: section for section in response["sections"]}
        no_action_items = {item["id"]: item for item in sections["no_action_needed"]["items"]}
        item = no_action_items["attn:task:task_attention_expired_generated_water"]
        self.assertEqual(item["type"], "task_expired")
        self.assertEqual(item["domain_state"], "expired")
        self.assertEqual(item["category"], "no_action_needed")
        self.assertEqual(item["reason"], "Expired")
        self.assertIsNone(item["primary_action"])

    def test_today_force_degraded_weather_keeps_task_items_in_tests(self) -> None:
        self._seed_due_task("task_weather_degrade")
        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"]
            )
            conn.execute(
                """
                INSERT INTO weather_alerts
                    (garden_id, alert_type, severity, title, description,
                     valid_from, valid_until, metadata_json, created_at_ms)
                VALUES (%s, 'rain_surplus', 'normal', 'Rain', 'Skip watering',
                        '2026-07-05', '2026-07-06', '{}', 1)
                """,
                (garden_id,),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            "os.environ",
            {
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            r = self.client.get("/api/attention/today?force_degraded_provider=weather")

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(
            body["degraded_providers"],
            [{"provider": "weather", "reason": "forced_degraded"}],
        )
        items = [item for section in body["sections"] for item in section["items"]]
        self.assertIn("attn:task:task_weather_degrade", {item["id"] for item in items})
        self.assertNotIn("weather", {item["provider"] for item in items})

    def test_today_weather_provider_failure_keeps_task_items(self) -> None:
        from gardenops.services.attention.providers.weather import WeatherAttentionProvider

        self._seed_due_task("task_weather_failure")
        with (
            patch.dict(
                "os.environ",
                {
                    "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                    "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
                },
            ),
            patch.object(
                WeatherAttentionProvider,
                "collect",
                side_effect=RuntimeError("weather unavailable"),
            ),
        ):
            r = self.client.get("/api/attention/today")

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(
            body["degraded_providers"],
            [{"provider": "weather", "reason": "provider_failed"}],
        )
        items = [item for section in body["sections"] for item in section["items"]]
        self.assertIn("attn:task:task_weather_failure", {item["id"] for item in items})

    def test_require_item_continues_when_one_provider_fails(self) -> None:
        from gardenops.services.attention import AttentionService, TaskAttentionProvider

        class BrokenSqlProvider:
            key = "weather"

            def collect(self, conn, *, garden_id: int, user_id: int, now_ms: int):
                conn.execute("SELECT * FROM attention_missing_table_for_require_item_test")
                return []

        self._seed_due_task("task_require_item_degraded")
        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"]
            )
            user_id = int(
                conn.execute("SELECT id FROM auth_users WHERE username = 'test_admin'").fetchone()[
                    "id"
                ]
            )
            service = AttentionService(
                providers=[
                    BrokenSqlProvider(),
                    TaskAttentionProvider(frozen_date="2026-07-05"),
                ],
            )
            service.require_item(
                conn,
                garden_id=garden_id,
                user_id=user_id,
                item_id="attn:task:task_require_item_degraded",
                now_ms=1783180800000,
            )
        finally:
            db.return_db(conn)

    def test_today_force_degraded_provider_rejects_unknown_test_provider(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            r = self.client.get("/api/attention/today?force_degraded_provider=bogus")

        self.assertEqual(r.status_code, 422)

    def test_today_force_degraded_provider_rejects_non_test_environment(self) -> None:
        with patch.dict("os.environ", {"APP_ENV": "production"}, clear=False):
            r = self.client.get("/api/attention/today?force_degraded_provider=weather")

        self.assertEqual(r.status_code, 422)

    def test_authenticated_preferences_state_and_restore_are_user_scoped(self) -> None:
        self._seed_due_task()
        item_id = "attn:task:task_attention_auth"

        with patch.dict(
            "os.environ",
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            _, csrf_token = self._login_session("test_admin", "testadminpass")
            headers = self._session_headers(csrf_token)
            prefs_response = self.client.put(
                "/api/attention/preferences",
                json={"preset": "calm", "rules": {}, "quiet_hours": {}},
                headers=headers,
            )
            self.assertEqual(prefs_response.status_code, 200)
            self.assertEqual(prefs_response.json()["preset"], "calm")

            unknown = self.client.post(
                "/api/attention/items/attn:task:missing/read",
                json={},
                headers=headers,
            )
            self.assertEqual(unknown.status_code, 404)

            dismissed = self.client.post(
                f"/api/attention/items/{item_id}/dismiss",
                json={},
                headers=headers,
            )
            self.assertEqual(dismissed.status_code, 200)
            hidden = self.client.get("/api/attention/today")
            self.assertEqual(hidden.status_code, 200)
            self.assertNotIn(
                item_id,
                [item["id"] for section in hidden.json()["sections"] for item in section["items"]],
            )

            self.client.cookies.clear()
            with patch.dict(
                "os.environ",
                {
                    "AUTH_REQUIRED": "false",
                    "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                    "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
                },
            ):
                fallback = self.client.get("/api/attention/today")
            self.assertEqual(fallback.status_code, 200)
            self.assertIn(
                item_id,
                [
                    item["id"]
                    for section in fallback.json()["sections"]
                    for item in section["items"]
                ],
            )

            _, csrf_token = self._login_session("test_admin", "testadminpass")
            headers = self._session_headers(csrf_token)
            expired_snooze = self.client.post(
                f"/api/attention/items/{item_id}/snooze",
                json={"snoozed_until_ms": 1},
                headers=headers,
            )
            self.assertEqual(expired_snooze.status_code, 422)

            restored = self.client.post(
                f"/api/attention/items/{item_id}/restore",
                json={},
                headers=headers,
            )
            self.assertEqual(restored.status_code, 200)
            visible = self.client.get("/api/attention/today")
            self.assertEqual(visible.status_code, 200)
            self.assertIn(
                item_id,
                [item["id"] for section in visible.json()["sections"] for item in section["items"]],
            )

    def test_local_admin_fallback_preference_save_is_noop_success_for_dev_e2e(self) -> None:
        conn = db.get_db()
        try:
            before = int(
                conn.execute("SELECT COUNT(*) AS c FROM user_attention_preferences").fetchone()["c"]
            )
        finally:
            db.return_db(conn)

        with patch.dict("os.environ", {"AUTH_REQUIRED": "false"}):
            response = self.client.put(
                "/api/attention/preferences",
                json={
                    "preset": "detailed",
                    "rules": {},
                    "quiet_hours": {},
                    "show_no_action_history": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user_id"], 0)
        self.assertEqual(response.json()["preset"], "detailed")

        conn = db.get_db()
        try:
            after = int(
                conn.execute("SELECT COUNT(*) AS c FROM user_attention_preferences").fetchone()["c"]
            )
        finally:
            db.return_db(conn)
        self.assertEqual(after, before)

    def test_authenticated_custom_preferences_round_trip_full_customization(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
        ):
            _, csrf_token = self._login_session("test_admin", "testadminpass")
            headers = self._session_headers(csrf_token)
            payload = {
                "preset": "custom",
                "rules": {
                    "calendar_event_due": {
                        "panel": False,
                        "inbox": True,
                        "digest": False,
                        "min_severity": "high",
                    }
                },
                "quiet_hours": {"digest": {"enabled": True, "start": "21:30", "end": "06:15"}},
                "show_no_action_history": False,
                "metadata": {"weather_aware_watering_suppression": False},
            }

            saved = self.client.put(
                "/api/attention/preferences",
                json=payload,
                headers=headers,
            )
            self.assertEqual(saved.status_code, 200, saved.text)

            loaded = self.client.get("/api/attention/preferences", headers=headers)
            self.assertEqual(loaded.status_code, 200, loaded.text)

        body = loaded.json()
        self.assertEqual(body["preset"], "custom")
        self.assertFalse(body["rules"]["calendar_event_due"]["panel"])
        self.assertEqual(body["rules"]["calendar_event_due"]["min_severity"], "high")
        self.assertTrue(body["quiet_hours"]["digest"]["enabled"])
        self.assertEqual(body["quiet_hours"]["digest"]["start"], "21:30")
        self.assertFalse(body["show_no_action_history"])
        self.assertFalse(body["metadata"]["weather_aware_watering_suppression"])

        conn = db.get_db()
        try:
            audit_row = conn.execute(
                """
                SELECT method, status_code, actor_username, actor_auth_type
                FROM audit_events
                WHERE path = '/api/attention/preferences'
                  AND method = 'PUT'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            db.return_db(conn)
        self.assertIsNotNone(audit_row)
        assert audit_row is not None
        self.assertEqual(str(audit_row["method"]), "PUT")
        self.assertEqual(int(audit_row["status_code"]), 200)
        self.assertEqual(str(audit_row["actor_username"]), "test_admin")
        self.assertEqual(str(audit_row["actor_auth_type"]), "session")


class TestAttentionTaskProvider(BaseApiTest):
    def _garden_and_user(self) -> tuple[int, int]:
        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"]
            )
            user_id = int(
                conn.execute("SELECT id FROM auth_users WHERE username = 'test_admin'").fetchone()[
                    "id"
                ]
            )
            return garden_id, user_id
        finally:
            db.return_db(conn)

    def test_task_provider_maps_due_overdue_snoozed_and_plot_context(self) -> None:
        from gardenops.services.attention import TaskAttentionProvider

        garden_id, user_id = self._garden_and_user()
        conn = db.get_db()
        try:
            conn.execute(
                "INSERT INTO plots (plot_id, garden_id, zone_code, zone_name, plot_number, "
                "grid_row, grid_col, sub_zone, notes) "
                "VALUES ('A1', %s, 'A', 'Beds', 1, 3, 3, '', '')",
                (garden_id,),
            )
            conn.execute(
                """
                INSERT INTO plants
                    (plt_id, name, latin, category, bloom_month, color,
                     hardiness, height_cm, light, link, year_planted,
                     care_watering, care_soil, care_planting, care_maintenance,
                     care_notes)
                VALUES ('ATTN-BASIL', 'Attention basil', '', 'urter', '', '',
                        '', NULL, '', '', NULL, 'regular moisture', '', '', '', '')
                ON CONFLICT (plt_id) DO NOTHING
                """
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES ('ATTN-BASIL', %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (user_id, garden_id),
            )
            conn.execute(
                """
                INSERT INTO garden_tasks
                (public_id, garden_id, task_type, title, description, status, severity, due_on,
                 snoozed_until, rule_source, metadata_json, completed_at_ms,
                 created_at_ms, updated_at_ms)
                VALUES
                ('task_due', %s, 'water', 'Water basil', '', 'pending', 'normal',
                 '2026-07-05', NULL, '', '{"group_key":"water:2026-W27"}', NULL, 1, 1),
                ('task_overdue', %s, 'prune', 'Prune roses', '', 'pending', 'high',
                 '2026-07-04', NULL, '', '{}', NULL, 1, 1),
                ('task_snoozed_ready', %s, 'harvest', 'Harvest lettuce', '', 'snoozed',
                 'normal', '2026-07-05', '2026-07-05', '', '{}', NULL, 1, 1),
                ('task_snoozed_future', %s, 'harvest', 'Harvest cabbage', '', 'snoozed',
                 'normal', '2026-07-05', '2026-07-07', '', '{}', NULL, 1, 1),
                ('task_completed', %s, 'water', 'Water parsley', '', 'completed', 'normal',
                 '2026-07-05', NULL, '', '{}', 1783180800000, 1, 1783180800000),
                ('task_expired', %s, 'water', 'Expired generated watering', '', 'expired',
                 'low', '2026-06-01', NULL, 'water:ATTN-BASIL:2026-06-01', '{}', NULL,
                 1, 1783180800000)
                """,
                (garden_id, garden_id, garden_id, garden_id, garden_id, garden_id),
            )
            due_id = int(
                conn.execute("SELECT id FROM garden_tasks WHERE public_id = 'task_due'").fetchone()[
                    "id"
                ]
            )
            conn.execute(
                "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, 'A1')",
                (due_id,),
            )
            conn.execute(
                "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, 'ATTN-BASIL')",
                (due_id,),
            )
            conn.commit()
            items = TaskAttentionProvider(frozen_date="2026-07-05").collect(
                conn,
                garden_id=garden_id,
                user_id=user_id,
                now_ms=1783180800000,
            )
        finally:
            db.return_db(conn)

        by_id = {item.id: item for item in items}
        assert by_id["attn:task:task_due"].type == "task_due"
        assert by_id["attn:task:task_due"].plot_ids == ("A1",)
        assert by_id["attn:task:task_due"].plant_ids == ("ATTN-BASIL",)
        assert by_id["attn:task:task_due"].group_key == "water:2026-W27"
        assert by_id["attn:task:task_overdue"].type == "task_overdue"
        assert by_id["attn:task:task_snoozed_ready"].type == "task_snoozed_active"
        assert "attn:task:task_snoozed_future" not in by_id
        assert by_id["attn:task:task_completed"].category == "no_action_needed"
        assert by_id["attn:task:task_completed"].reason == "Completed"
        assert by_id["attn:task:task_completed"].rank < by_id["attn:task:task_expired"].rank

    def test_task_provider_uses_terminal_transition_time_for_no_action_history(self) -> None:
        from gardenops.services.attention import TaskAttentionProvider

        garden_id, user_id = self._garden_and_user()
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO garden_tasks
                (public_id, garden_id, task_type, title, description, status, severity, due_on,
                 snoozed_until, rule_source, metadata_json, completed_at_ms,
                 created_at_ms, updated_at_ms)
                VALUES
                ('task_completed_recent', %s, 'water', 'Water basil', '', 'completed',
                 'normal', '2026-07-05', NULL, '', '{}', 1783180800000, 1, 1783180800000),
                ('task_completed_old_edited', %s, 'water', 'Water parsley', '', 'completed',
                 'normal', '2026-07-05', NULL, '', '{}', 1, 1, 1783180800000),
                ('task_skipped_recent', %s, 'water', 'Skip basil', '', 'skipped',
                 'normal', '2026-07-05', NULL, '', '{}', NULL, 1, 1783180800000),
                ('task_skipped_old', %s, 'water', 'Skip parsley', '', 'skipped',
                 'normal', '2026-07-05', NULL, '', '{}', NULL, 1, 1),
                ('task_snoozed_overdue', %s, 'water', 'Water mint', '', 'snoozed',
                 'normal', '2026-07-03', '2026-07-04', '', '{}', NULL, 1, 1)
                """,
                (garden_id, garden_id, garden_id, garden_id, garden_id),
            )
            conn.commit()
            items = TaskAttentionProvider(frozen_date="2026-07-05").collect(
                conn,
                garden_id=garden_id,
                user_id=user_id,
                now_ms=1783180800000,
            )
        finally:
            db.return_db(conn)

        by_id = {item.id: item for item in items}
        assert "attn:task:task_completed_recent" in by_id
        assert "attn:task:task_completed_old_edited" not in by_id
        assert by_id["attn:task:task_skipped_recent"].reason == "Skipped"
        assert by_id["attn:task:task_skipped_recent"].primary_action is None
        assert (
            by_id["attn:task:task_completed_recent"].rank
            < by_id["attn:task:task_skipped_recent"].rank
        )
        assert "attn:task:task_skipped_old" not in by_id
        assert by_id["attn:task:task_snoozed_overdue"].reason == "Snooze expired"

    def test_attention_service_classifies_expired_task_snoozes_as_overdue(self) -> None:
        from gardenops.services.attention import AttentionService, TaskAttentionProvider

        garden_id, user_id = self._garden_and_user()
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, description, status, severity,
                     due_on, snoozed_until, rule_source, metadata_json,
                     created_at_ms, updated_at_ms)
                VALUES ('task_attention_expired_snooze', %s, 'water', 'Water mint', '',
                        'snoozed', 'normal', '2026-07-03', '2026-07-04', '', '{}', 1, 1)
                """,
                (garden_id,),
            )
            conn.commit()

            response = AttentionService(
                providers=[TaskAttentionProvider(frozen_date="2026-07-05")]
            ).today(
                conn,
                garden_id=garden_id,
                user_id=user_id,
                now_ms=1783180800000,
            )
        finally:
            db.return_db(conn)

        item = next(
            item
            for section in response["sections"]
            for item in section["items"]
            if item["id"] == "attn:task:task_attention_expired_snooze"
        )
        self.assertEqual(item["type"], "task_overdue")
        self.assertEqual(item["reason"], "Overdue")
        self.assertEqual(item["category"], "needs_action")

    def test_task_provider_caps_do_not_crowd_out_high_value_rows(self) -> None:
        from gardenops.services.attention import TaskAttentionProvider

        garden_id, user_id = self._garden_and_user()
        conn = db.get_db()
        try:
            for idx in range(110):
                conn.execute(
                    """
                    INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, description, status, severity,
                     due_on, snoozed_until, rule_source, metadata_json,
                     created_at_ms, updated_at_ms)
                    VALUES (%s, %s, 'water', %s, '', 'pending', 'low',
                            '2026-06-01', NULL, '', '{}', 1, 1)
                    """,
                    (f"task_old_overdue_{idx}", garden_id, f"Old overdue {idx}"),
                )
            conn.execute(
                """
                INSERT INTO garden_tasks
                (public_id, garden_id, task_type, title, description, status, severity, due_on,
                 snoozed_until, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES
                ('task_high_due_today', %s, 'water', 'Water greenhouse', '', 'pending',
                 'high', '2026-07-05', NULL, '', '{}', 1, 1783180800000),
                ('task_completed_recent_cap', %s, 'water', 'Watered mint', '', 'completed',
                 'normal', '2026-07-05', NULL, '', '{}', 1, 1783180800000)
                """,
                (garden_id, garden_id),
            )
            conn.execute(
                """
                UPDATE garden_tasks
                SET completed_at_ms = 1783180800000
                WHERE public_id = 'task_completed_recent_cap'
                """
            )
            conn.commit()
            items = TaskAttentionProvider(frozen_date="2026-07-05").collect(
                conn,
                garden_id=garden_id,
                user_id=user_id,
                now_ms=1783180800000,
            )
        finally:
            db.return_db(conn)

        by_id = {item.id: item for item in items}
        assert by_id["attn:task:task_high_due_today"].severity == "high"
        assert by_id["attn:task:task_completed_recent_cap"].category == "no_action_needed"

    def test_rain_outcomes_suppress_only_currently_covered_watering(self) -> None:
        from gardenops.services.attention import TaskAttentionProvider
        from gardenops.services.attention.outcomes import upsert_attention_outcome

        garden_id, user_id = self._garden_and_user()
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO plots
                    (plot_id, garden_id, zone_code, zone_name, plot_number,
                     grid_row, grid_col, sub_zone, notes)
                VALUES
                    ('OUT1', %s, 'B', 'Bed', 99, 8, 8, '', ''),
                    ('INDOOR-ATTN', %s, 'I', 'Indoor', 0, NULL, NULL, '', '')
                """,
                (garden_id, garden_id),
            )
            conn.execute(
                """
                INSERT INTO garden_tasks
                (public_id, garden_id, task_type, title, description, status, severity, due_on,
                 snoozed_until, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES
                ('task_generated_suppressed', %s, 'water', 'Water outdoor covered', '',
                 'pending', 'normal', '2026-07-05', NULL, 'water:RAIN1:2026-07-05',
                 '{}', 1, 1),
                ('task_manual_visible', %s, 'water', 'Water manual', '',
                 'pending', 'normal', '2026-07-05', NULL, '', '{}', 1, 1),
                ('task_generated_without_outcome', %s, 'water', 'Water generated', '',
                 'pending', 'normal', '2026-07-05', NULL, 'water:RAIN2:2026-07-05',
                 '{}', 1, 1),
                ('task_generated_no_plot', %s, 'water', 'Water ambiguous', '',
                 'pending', 'normal', '2026-07-05', NULL, 'water:RAIN3:2026-07-05',
                 '{}', 1, 1),
                ('task_generated_indoor', %s, 'water', 'Water indoor', '',
                 'pending', 'normal', '2026-07-05', NULL, 'water:RAIN4:2026-07-05',
                 '{}', 1, 1),
                ('task_generated_snoozed', %s, 'water', 'Water snoozed', '',
                 'snoozed', 'normal', '2026-07-01', '2026-07-05',
                 'water:RAIN5:2026-07-01', '{}', 1, 1)
                """,
                (garden_id, garden_id, garden_id, garden_id, garden_id, garden_id),
            )
            task_rows = {
                str(row["public_id"]): int(row["id"])
                for row in conn.execute("SELECT id, public_id FROM garden_tasks").fetchall()
            }
            for plant_id in ("RAIN1", "RAIN2", "RAIN3", "RAIN4", "RAIN5"):
                conn.execute(
                    """
                    INSERT INTO plants
                        (plt_id, name, latin, category, bloom_month, color,
                         hardiness, height_cm, light, link)
                    VALUES (%s, %s, '', 'busker', '', '', '', NULL, '', '')
                    """,
                    (plant_id, plant_id),
                )
                conn.execute(
                    """
                    INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    """,
                    (plant_id, user_id, garden_id),
                )
            for public_id in (
                "task_generated_suppressed",
                "task_manual_visible",
                "task_generated_without_outcome",
            ):
                conn.execute(
                    "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, 'OUT1')",
                    (task_rows[public_id],),
                )
            conn.execute(
                "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, 'INDOOR-ATTN')",
                (task_rows["task_generated_indoor"],),
            )
            for public_id, plant_id in (
                ("task_generated_suppressed", "RAIN1"),
                ("task_generated_without_outcome", "RAIN2"),
                ("task_generated_no_plot", "RAIN3"),
                ("task_generated_indoor", "RAIN4"),
                ("task_generated_snoozed", "RAIN5"),
            ):
                conn.execute(
                    "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
                    (task_rows[public_id], plant_id),
                )
            conn.execute(
                """
                INSERT INTO plot_plants (plot_id, plt_id, quantity)
                VALUES
                    ('OUT1', 'RAIN1', 1),
                    ('OUT1', 'RAIN2', 1),
                    ('OUT1', 'RAIN5', 1),
                    ('INDOOR-ATTN', 'RAIN4', 1)
                """,
            )
            for rule in ("water:RAIN1:2026-07-05", "water:RAIN3:2026-07-05"):
                upsert_attention_outcome(
                    conn,
                    garden_id=garden_id,
                    provider="weather",
                    outcome_type="watering_covered_by_rain",
                    source_type="task_generator",
                    source_id="1",
                    source_public_id=rule,
                    target_type="plant",
                    target_id=rule.split(":")[1],
                    title="Watering covered by rain",
                    explanation="18 mm rain covers this watering.",
                    reason="Rain surplus covers the watering date",
                    plant_ids=(rule.split(":")[1],),
                    plot_ids=(),
                    metadata={"due_on": "2026-07-05", "rain_mm": 18},
                    occurred_at_ms=1783180800000,
                    expires_at_ms=1785772800000,
                )
            upsert_attention_outcome(
                conn,
                garden_id=garden_id,
                provider="weather",
                outcome_type="watering_covered_by_rain",
                source_type="task_generator",
                source_id="1",
                source_public_id="water:RAIN4:2026-07-05",
                target_type="plant",
                target_id="RAIN4",
                title="Watering covered by rain",
                explanation="18 mm rain covers this watering.",
                reason="Rain surplus covers the watering date",
                plant_ids=("RAIN4",),
                plot_ids=(),
                metadata={"due_on": "2026-07-05", "rain_mm": 18},
                occurred_at_ms=1783180800000,
                expires_at_ms=1785772800000,
            )
            upsert_attention_outcome(
                conn,
                garden_id=garden_id,
                provider="weather",
                outcome_type="watering_rescheduled_by_rain",
                source_type="task_generator",
                source_id="1",
                source_public_id="water:RAIN5:2026-07-01",
                target_type="plant",
                target_id="RAIN5",
                title="Watering rescheduled by rain",
                explanation="18 mm rain moved this watering.",
                reason="Rain surplus rescheduled the watering date",
                plant_ids=("RAIN5",),
                plot_ids=(),
                metadata={
                    "due_on": "2026-07-01",
                    "new_due_on": "2026-07-05",
                    "rain_mm": 18,
                },
                occurred_at_ms=1783180800000,
                expires_at_ms=1785772800000,
            )
            conn.commit()
            items = TaskAttentionProvider(frozen_date="2026-07-05").collect(
                conn,
                garden_id=garden_id,
                user_id=user_id,
                now_ms=1783180800000,
            )
        finally:
            db.return_db(conn)

        item_ids = {item.id for item in items}
        assert "attn:task:task_generated_suppressed" not in item_ids
        assert "attn:task:task_manual_visible" in item_ids
        assert "attn:task:task_generated_without_outcome" in item_ids
        assert "attn:task:task_generated_no_plot" in item_ids
        assert "attn:task:task_generated_indoor" in item_ids
        assert "attn:task:task_generated_snoozed" in item_ids


class TestAttentionExpandedProviders(BaseApiTest):
    def _garden_and_user(self) -> tuple[int, int]:
        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"]
            )
            user_id = int(
                conn.execute("SELECT id FROM auth_users WHERE username = 'test_admin'").fetchone()[
                    "id"
                ]
            )
            return garden_id, user_id
        finally:
            db.return_db(conn)

    def _today(self) -> dict:
        with patch.dict(
            "os.environ",
            {
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            response = self.client.get("/api/attention/today")
        self.assertEqual(response.status_code, 200)
        return response.json()

    @staticmethod
    def _sections(body: dict) -> dict[str, list[dict]]:
        return {section["key"]: section["items"] for section in body["sections"]}

    def test_open_high_severity_and_overdue_issue_follow_up_rank_before_routine_due_tasks(
        self,
    ) -> None:
        garden_id, user_id = self._garden_and_user()
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, description, status, severity,
                     due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES ('task_routine_due', %s, 'water', 'Water mint', '', 'pending',
                        'normal', '2026-07-05', '', '{}', 1, 1)
                """,
                (garden_id,),
            )
            conn.execute(
                """
                INSERT INTO garden_issues
                    (public_id, garden_id, issue_type, title, description, severity, status,
                     suspected_cause, treatment_plan, follow_up_on, metadata_json,
                     created_by_user_id, created_at_ms, updated_at_ms)
                VALUES
                    ('iss_high_open', %s, 'pest', 'Aphids spreading', '', 'high', 'open',
                     '', '', NULL, '{}', %s, 1, 10),
                    ('iss_follow_overdue', %s, 'disease', 'Check mildew recovery', '',
                     'normal', 'open', '', '', '2026-07-04', '{}', %s, 1, 20)
                """,
                (garden_id, user_id, garden_id, user_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        items = self._sections(self._today())["needs_attention"]
        item_ids = [item["id"] for item in items]
        self.assertLess(
            item_ids.index("attn:issue:iss_high_open"),
            item_ids.index("attn:task:task_routine_due"),
        )
        self.assertLess(
            item_ids.index("attn:issue:iss_follow_overdue"),
            item_ids.index("attn:task:task_routine_due"),
        )
        high_issue = next(item for item in items if item["id"] == "attn:issue:iss_high_open")
        self.assertEqual(high_issue["primary_action"]["kind"], "open_issue")

    def test_recently_resolved_issue_appears_in_no_action_needed(self) -> None:
        garden_id, user_id = self._garden_and_user()
        conn = db.get_db()
        try:
            issue = conn.execute(
                """
                INSERT INTO garden_issues
                    (public_id, garden_id, issue_type, title, description, severity, status,
                     suspected_cause, treatment_plan, follow_up_on, metadata_json,
                     created_by_user_id, resolved_by_user_id, resolved_at_ms,
                     created_at_ms, updated_at_ms)
                VALUES ('iss_recently_resolved', %s, 'pest', 'Scale removed', '',
                        'normal', 'resolved', '', '', NULL, '{}', %s, %s,
                        1783170000000, 1, 1783170000000)
                RETURNING id
                """,
                (garden_id, user_id, user_id),
            ).fetchone()
            conn.execute(
                "INSERT INTO garden_issue_plants (issue_id, plt_id) VALUES (%s, 'PLT-TEST')",
                (int(issue["id"]),),
            )
            conn.execute(
                "INSERT INTO garden_issue_plots (issue_id, plot_id) VALUES (%s, 'B1')",
                (int(issue["id"]),),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        no_action = self._sections(self._today())["no_action_needed"]
        resolved = next(
            item for item in no_action if item["id"] == "attn:issue:iss_recently_resolved"
        )
        self.assertEqual(resolved["type"], "issue_resolved")
        self.assertEqual(resolved["plant_ids"], ["PLT-TEST"])
        self.assertEqual(resolved["plot_ids"], ["B1"])

    def test_calendar_duplicate_detection_requires_explicit_source_link(self) -> None:
        from gardenops.services.attention.providers.calendar import CalendarAttentionProvider

        linked_row = {
            "metadata_json": json.dumps({"target_type": "task", "target_id": "task_linked"}),
            "target_type": "",
            "target_id": "",
            "source_key": "",
            "title": "Prune espalier",
            "event_on": "2026-07-05",
        }
        independent_row = {
            "metadata_json": "{}",
            "target_type": "",
            "target_id": "",
            "source_key": "",
            "title": "Prune espalier",
            "event_on": "2026-07-05",
        }

        self.assertTrue(CalendarAttentionProvider._is_duplicate(linked_row))
        self.assertFalse(CalendarAttentionProvider._is_duplicate(independent_row))

    def test_manual_calendar_event_due_today_keeps_same_title_independent_event(self) -> None:
        garden_id, user_id = self._garden_and_user()
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, description, status, severity,
                     due_on, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES ('task_calendar_duplicate', %s, 'prune', 'Prune espalier', '',
                        'pending', 'normal', '2026-07-05', '', '{}', 1, 1)
                """,
                (garden_id,),
            )
            rows = conn.execute(
                """
                INSERT INTO garden_calendar_events
                    (public_id, garden_id, title, description, event_on, created_by_user_id,
                     updated_by_user_id, created_at_ms, updated_at_ms)
                VALUES
                    ('calevt_same_title_manual', %s, 'Prune espalier', 'Independent manual note',
                     '2026-07-05', %s, %s, 1, 1),
                    ('calevt_unique_today', %s, 'Community seed swap', 'Bring saved seed',
                     '2026-07-05', %s, %s, 1, 2),
                    ('calevt_future', %s, 'Sharpen shears', '',
                     '2026-07-08', %s, %s, 1, 3)
                RETURNING id, public_id
                """,
                (
                    garden_id,
                    user_id,
                    user_id,
                    garden_id,
                    user_id,
                    user_id,
                    garden_id,
                    user_id,
                    user_id,
                ),
            ).fetchall()
            event_ids = {str(row["public_id"]): int(row["id"]) for row in rows}
            conn.execute(
                "INSERT INTO garden_calendar_event_plots (event_id, plot_id) VALUES (%s, 'B1')",
                (event_ids["calevt_unique_today"],),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        sections = self._sections(self._today())
        emitted_ids = {item["id"] for section in sections.values() for item in section}
        self.assertIn("attn:calendar:calevt_same_title_manual", emitted_ids)
        self.assertIn("attn:calendar:calevt_unique_today", emitted_ids)
        self.assertIn("attn:calendar:calevt_future", emitted_ids)
        today_event = next(
            item
            for item in sections["needs_attention"]
            if item["id"] == "attn:calendar:calevt_unique_today"
        )
        self.assertEqual(today_event["source_label"], "Calendar")
        self.assertEqual(today_event["plot_ids"], ["B1"])
        future_event = next(
            item for item in sections["coming_up"] if item["id"] == "attn:calendar:calevt_future"
        )
        self.assertEqual(future_event["category"], "upcoming")

    def test_legacy_system_status_notification_events_are_adapted(self) -> None:
        garden_id, user_id = self._garden_and_user()
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO notification_events
                    (public_id, garden_id, user_id, notification_type, notification_subtype,
                     severity, title, body, target_type, target_id, metadata_json,
                     dismissed, created_at_ms)
                VALUES
                    ('note_backup_status', %s, NULL, 'system', 'backup', 'high',
                     'Backup delayed', 'Nightly backup has not finished yet.',
                     'status', 'backup', %s, 0, 10),
                    ('note_task_legacy', %s, %s, 'task_due', NULL, 'normal',
                     'Water mint', 'Due today', 'task', 'task_legacy', '{}', 0, 20)
                """,
                (
                    garden_id,
                    json.dumps({"target_type": "status", "target_id": "backup"}),
                    garden_id,
                    user_id,
                ),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        warnings = self._sections(self._today())["warnings"]
        notification = next(
            item for item in warnings if item["id"] == "attn:notification:note_backup_status"
        )
        self.assertEqual(notification["provider"], "notification_status")
        self.assertEqual(notification["type"], "backup")
        self.assertEqual(notification["target_type"], "status")
        self.assertEqual(notification["target_id"], "backup")
        self.assertNotIn(
            "attn:notification:note_task_legacy",
            {item["id"] for item in warnings},
        )

    def test_notification_provider_collection_and_today_get_do_not_mutate_rows(self) -> None:
        from gardenops.services.attention import NotificationStatusAttentionProvider

        garden_id, user_id = self._garden_and_user()
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO notification_events
                    (public_id, garden_id, user_id, notification_type, notification_subtype,
                     severity, title, body, target_type, target_id, metadata_json,
                     dismissed, created_at_ms, cleared_at_ms, clear_reason, superseded_by_id)
                VALUES ('note_readonly_status', %s, NULL, 'status', 'security', 'critical',
                        'Security status changed', 'Review account security.',
                        'status', 'security', '{}', 0, 10, NULL, NULL, NULL)
                """,
                (garden_id,),
            )
            conn.commit()
            before_count = conn.execute(
                "SELECT COUNT(*) AS c FROM notification_events WHERE garden_id = %s",
                (garden_id,),
            ).fetchone()
            before = conn.execute(
                """
                SELECT dismissed, cleared_at_ms, clear_reason, superseded_by_id
                FROM notification_events
                WHERE public_id = 'note_readonly_status'
                """
            ).fetchone()
            items = NotificationStatusAttentionProvider().collect(
                conn,
                garden_id=garden_id,
                user_id=user_id,
                now_ms=1783180800000,
            )
            after_collect = conn.execute(
                """
                SELECT dismissed, cleared_at_ms, clear_reason, superseded_by_id
                FROM notification_events
                WHERE public_id = 'note_readonly_status'
                """
            ).fetchone()
            after_collect_count = conn.execute(
                "SELECT COUNT(*) AS c FROM notification_events WHERE garden_id = %s",
                (garden_id,),
            ).fetchone()
            self.assertEqual(
                [item.id for item in items],
                ["attn:notification:note_readonly_status"],
            )
            self.assertEqual(dict(after_collect), dict(before))
            self.assertEqual(int(after_collect_count["c"]), int(before_count["c"]))
        finally:
            db.return_db(conn)

        self._today()

        conn = db.get_db()
        try:
            after_get = conn.execute(
                """
                SELECT dismissed, cleared_at_ms, clear_reason, superseded_by_id
                FROM notification_events
                WHERE public_id = 'note_readonly_status'
                """
            ).fetchone()
            after_get_count = conn.execute(
                "SELECT COUNT(*) AS c FROM notification_events WHERE garden_id = %s",
                (garden_id,),
            ).fetchone()
            self.assertEqual(dict(after_get), dict(before))
            self.assertEqual(int(after_get_count["c"]), int(before_count["c"]))
        finally:
            db.return_db(conn)


class TestAttentionMorningGardenCheck(BaseApiTest):
    @staticmethod
    def _snapshot_notifications(conn, garden_id: int) -> list[dict]:
        rows = conn.execute(
            """
            SELECT public_id, notification_type, notification_subtype, severity, title, body,
                   target_type, target_id, dismissed, read_at_ms, cleared_at_ms,
                   clear_reason, superseded_by_id
            FROM notification_events
            WHERE garden_id = %s
            ORDER BY public_id
            """,
            (garden_id,),
        ).fetchall()
        return [
            {
                "public_id": str(row["public_id"]),
                "notification_type": str(row["notification_type"]),
                "notification_subtype": (
                    str(row["notification_subtype"])
                    if row["notification_subtype"] is not None
                    else None
                ),
                "severity": str(row["severity"] or "normal"),
                "title": str(row["title"]),
                "body": str(row["body"]),
                "target_type": str(row["target_type"] or ""),
                "target_id": str(row["target_id"] or ""),
                "dismissed": int(row["dismissed"] or 0),
                "read_at_ms": int(row["read_at_ms"]) if row["read_at_ms"] else None,
                "cleared_at_ms": int(row["cleared_at_ms"]) if row["cleared_at_ms"] else None,
                "clear_reason": str(row["clear_reason"] or ""),
                "superseded_by_id": (
                    int(row["superseded_by_id"]) if row["superseded_by_id"] else None
                ),
            }
            for row in rows
        ]

    def test_morning_garden_check_keeps_map_today_read_normalized_and_non_mutating(
        self,
    ) -> None:
        from gardenops.services.attention.outcomes import upsert_attention_outcome

        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"]
            )
            user_id = int(
                conn.execute("SELECT id FROM auth_users WHERE username = 'test_admin'").fetchone()[
                    "id"
                ]
            )
            for plot_id, zone_name, grid_row, grid_col in (
                ("MGC-A1", "Morning Bed", 6, 6),
                ("MGC-INDOOR", "Indoors", None, None),
            ):
                conn.execute(
                    """
                    INSERT INTO plots
                        (plot_id, garden_id, zone_code, zone_name, plot_number,
                         grid_row, grid_col, sub_zone, notes)
                    VALUES (%s, %s, 'M', %s, 1, %s, %s, '', '')
                    ON CONFLICT (plot_id) DO UPDATE SET
                        garden_id = excluded.garden_id,
                        grid_row = excluded.grid_row,
                        grid_col = excluded.grid_col
                    """,
                    (plot_id, garden_id, zone_name, grid_row, grid_col),
                )
                conn.execute(
                    """
                    INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (plot_id) DO UPDATE SET
                        owner_user_id = excluded.owner_user_id,
                        garden_id = excluded.garden_id
                    """,
                    (plot_id, user_id, garden_id),
                )
            for plant_id, name, plot_id in (
                ("MGC-HYD", "Hydrangea", "MGC-A1"),
                ("MGC-BASIL", "Indoor basil", "MGC-INDOOR"),
                ("MGC-CUC", "Cucumber", "MGC-A1"),
            ):
                conn.execute(
                    """
                    INSERT INTO plants
                        (plt_id, name, latin, category, bloom_month, color, hardiness,
                         height_cm, light, link, care_watering, care_soil,
                         care_planting, care_maintenance, care_notes)
                    VALUES (%s, %s, '', 'test', '', '', '', NULL, '', '',
                            'regular moisture', '', '', '', '')
                    ON CONFLICT (plt_id) DO UPDATE SET
                        name = excluded.name,
                        care_watering = excluded.care_watering
                    """,
                    (plant_id, name),
                )
                conn.execute(
                    """
                    INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (plt_id, garden_id) DO UPDATE SET
                        owner_user_id = excluded.owner_user_id
                    """,
                    (plant_id, user_id, garden_id),
                )
                conn.execute(
                    """
                    INSERT INTO plot_plants (plot_id, plt_id, quantity, seen_growing)
                    VALUES (%s, %s, 1, 1)
                    ON CONFLICT (plot_id, plt_id) DO UPDATE SET
                        quantity = excluded.quantity,
                        seen_growing = excluded.seen_growing
                    """,
                    (plot_id, plant_id),
                )

            task_rows = conn.execute(
                """
                INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, description, status, severity,
                     due_on, rule_source, metadata_json, created_by_user_id,
                     created_at_ms, updated_at_ms)
                VALUES
                    ('task_mgc_water_hydrangea', %s, 'water', 'Water hydrangea',
                     'Generated watering covered by rain.', 'pending', 'normal',
                     '2026-07-05', 'water:MGC-HYD:2026-07-05', '{}', %s, 1, 1),
                    ('task_mgc_water_indoor_basil', %s, 'water', 'Water indoor basil',
                     'Manual indoor watering still needs action.', 'pending', 'high',
                     '2026-07-05', '', '{}', %s, 1, 2)
                RETURNING id, public_id
                """,
                (garden_id, user_id, garden_id, user_id),
            ).fetchall()
            task_ids = {str(row["public_id"]): int(row["id"]) for row in task_rows}
            for public_id, plant_id, plot_id in (
                ("task_mgc_water_hydrangea", "MGC-HYD", "MGC-A1"),
                ("task_mgc_water_indoor_basil", "MGC-BASIL", "MGC-INDOOR"),
            ):
                conn.execute(
                    "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
                    (task_ids[public_id], plant_id),
                )
                conn.execute(
                    "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, %s)",
                    (task_ids[public_id], plot_id),
                )

            alert = conn.execute(
                """
                INSERT INTO weather_alerts
                    (garden_id, alert_type, severity, title, description,
                     valid_from, valid_until, metadata_json, created_at_ms)
                VALUES (%s, 'rain_surplus', 'high', '18 mm rain expected',
                        '18 mm rain expected before evening; outdoor watering is covered.',
                        '2026-07-05', '2026-07-06', %s, 1)
                RETURNING id
                """,
                (garden_id, json.dumps({"rain_mm": 18})),
            ).fetchone()
            upsert_attention_outcome(
                conn,
                garden_id=garden_id,
                provider="weather",
                outcome_type="watering_covered_by_rain",
                source_type="task_generator",
                source_id=str(alert["id"]),
                source_public_id="water:MGC-HYD:2026-07-05",
                target_type="plant",
                target_id="MGC-HYD",
                title="Watering covered by rain",
                explanation="18 mm rain expected already covers watering for Hydrangea.",
                reason="Rain surplus covers the watering date",
                plant_ids=("MGC-HYD",),
                plot_ids=("MGC-A1",),
                metadata={"due_on": "2026-07-05", "rain_mm": 18, "plant_name": "Hydrangea"},
                recovery_action={
                    "kind": "restore_generated_watering_task",
                    "label": "Restore watering",
                    "source_public_id": "water:MGC-HYD:2026-07-05",
                    "target_type": "plant",
                    "target_id": "MGC-HYD",
                    "due_on": "2026-07-05",
                    "plant_ids": ["MGC-HYD"],
                    "plot_ids": ["MGC-A1"],
                },
                occurred_at_ms=1783180800000,
                expires_at_ms=1785772800000,
            )

            issue = conn.execute(
                """
                INSERT INTO garden_issues
                    (public_id, garden_id, issue_type, title, description, severity, status,
                     suspected_cause, treatment_plan, follow_up_on, metadata_json,
                     created_by_user_id, created_at_ms, updated_at_ms)
                VALUES ('iss_mgc_mildew', %s, 'disease', 'Check mildew on cucumber',
                        'Follow up after treatment.', 'high', 'open',
                        'Powdery mildew', 'Inspect leaves.', '2026-07-04', '{}',
                        %s, 1, 2)
                RETURNING id
                """,
                (garden_id, user_id),
            ).fetchone()
            conn.execute(
                "INSERT INTO garden_issue_plants (issue_id, plt_id) VALUES (%s, 'MGC-CUC')",
                (int(issue["id"]),),
            )
            conn.execute(
                "INSERT INTO garden_issue_plots (issue_id, plot_id) VALUES (%s, 'MGC-A1')",
                (int(issue["id"]),),
            )
            conn.execute(
                """
                INSERT INTO notification_events
                    (public_id, garden_id, user_id, notification_type, notification_subtype,
                     severity, title, body, target_type, target_id, metadata_json,
                     dismissed, created_at_ms, cleared_at_ms, clear_reason, superseded_by_id)
                VALUES ('note_mgc_backup_status', %s, NULL, 'system', 'backup', 'high',
                        'Backup status needs review', 'Nightly backup finished late.',
                        'status', 'backup', '{}', 0, 10, NULL, NULL, NULL)
                """,
                (garden_id,),
            )
            before = self._snapshot_notifications(conn, garden_id)
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            "os.environ",
            {
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            response = self.client.get("/api/attention/today")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        active_titles = [
            item["title"]
            for section in body["sections"]
            if section["key"] != "no_action_needed"
            for item in section["items"]
        ]
        no_action_titles = [
            item["title"]
            for section in body["sections"]
            if section["key"] == "no_action_needed"
            for item in section["items"]
        ]

        self.assertIn("Check mildew on cucumber", active_titles)
        self.assertIn("Water indoor basil", active_titles)
        self.assertIn("18 mm rain expected", active_titles)
        self.assertIn("Backup status needs review", active_titles)
        self.assertNotIn("Water hydrangea", active_titles)
        self.assertTrue(any("Watering" in title for title in no_action_titles))

        conn = db.get_db()
        try:
            after = self._snapshot_notifications(conn, garden_id)
        finally:
            db.return_db(conn)
        self.assertEqual(before, after)


class TestAttentionRainWatering(BaseApiTest):
    """Focused rain/watering regressions named for the implementation plan."""

    def test_today_includes_rain_alert_and_no_action_watering_outcome(self) -> None:
        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"]
            )
            alert = conn.execute(
                """
                INSERT INTO weather_alerts
                    (garden_id, alert_type, severity, title, description,
                     valid_from, valid_until, metadata_json, created_at_ms)
                VALUES (%s, 'rain_surplus', 'high', 'Heavy rain expected',
                        'Skip watering and check drainage', '2026-07-05',
                        '2026-07-07', %s, 1)
                RETURNING id
                """,
                (garden_id, json.dumps({"rain_mm": 18})),
            ).fetchone()
            from gardenops.services.attention.outcomes import upsert_attention_outcome

            upsert_attention_outcome(
                conn,
                garden_id=garden_id,
                provider="weather",
                outcome_type="watering_covered_by_rain",
                source_type="task_generator",
                source_id=str(alert["id"]),
                source_public_id="water:RAIN1:2026-07-05",
                target_type="plant",
                target_id="RAIN1",
                title="Watering covered by rain",
                explanation="18 mm rain already covers the scheduled watering for Hydrangea.",
                reason="Rain surplus covers the watering date",
                plant_ids=("RAIN1",),
                plot_ids=("A1",),
                metadata={"due_on": "2026-07-05", "rain_mm": 18},
                occurred_at_ms=1783180800000,
                expires_at_ms=1785772800000,
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            "os.environ",
            {
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            r = self.client.get("/api/attention/today")

        self.assertEqual(r.status_code, 200)
        sections = {section["key"]: section["items"] for section in r.json()["sections"]}
        warning = next(item for item in sections["warnings"] if item["provider"] == "weather")
        self.assertEqual(warning["type"], "rain_alert")
        self.assertIsNone(warning["primary_action"])
        no_action = next(
            item
            for item in sections["no_action_needed"]
            if item["type"] == "watering_covered_by_rain"
        )
        self.assertIn("18 mm rain", no_action["body"])
        self.assertEqual(no_action["metadata"]["due_on"], "2026-07-05")


class TestAttentionOutcomes(BaseApiTest):
    def test_outcome_upsert_uses_source_key_and_updates_payload(self) -> None:
        from gardenops.services.attention.outcomes import (
            read_active_attention_outcomes,
            upsert_attention_outcome,
        )

        conn = db.get_db()
        try:
            garden_id = int(
                conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"]
            )
            for explanation, occurred_at_ms, rain_mm in (
                ("First explanation", 10, 12.0),
                ("Updated explanation", 20, 18.0),
            ):
                upsert_attention_outcome(
                    conn,
                    garden_id=garden_id,
                    provider="weather",
                    outcome_type="watering_covered_by_rain",
                    source_type="task_generator",
                    source_id="77",
                    source_public_id="water:UP1:2026-07-05",
                    target_type="plant",
                    target_id="UP1",
                    title="Watering covered by rain",
                    explanation=explanation,
                    reason="Rain surplus covers watering",
                    plant_ids=("UP1",),
                    plot_ids=("B1",),
                    metadata={"rain_mm": rain_mm},
                    occurred_at_ms=occurred_at_ms,
                    expires_at_ms=100,
                    recovery_action={"kind": "open_weather", "label": "Open weather"},
                )
            conn.commit()
            rows = read_active_attention_outcomes(
                conn,
                garden_id=garden_id,
                provider="weather",
                outcome_types=("watering_covered_by_rain",),
                now_ms=50,
            )
            total = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM attention_outcomes WHERE garden_id = %s",
                    (garden_id,),
                ).fetchone()["c"]
            )
        finally:
            db.return_db(conn)

        self.assertEqual(total, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["explanation"], "Updated explanation")
        self.assertEqual(rows[0]["metadata"]["rain_mm"], 18.0)
        self.assertEqual(rows[0]["occurred_at_ms"], 20)
