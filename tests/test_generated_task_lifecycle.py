import json

import gardenops.db as db
from gardenops.db import current_timestamp_ms
from gardenops.services.attention.providers.tasks import TaskAttentionProvider
from gardenops.services.generated_task_lifecycle import expire_stale_generated_tasks
from tests.base import DbTestBase


class TestGeneratedTaskLifecycle(DbTestBase):
    def test_dry_spell_watering_stays_active_until_alert_validity_ends(self) -> None:
        alert = self.conn.execute(
            """
            INSERT INTO weather_alerts
                (garden_id, alert_type, severity, title, description,
                 valid_from, valid_until, metadata_json, created_at_ms)
            VALUES (%s, 'dry_spell', 'normal', 'Dry spell', 'Water regularly',
                    '2026-07-10', '2026-07-16', '{}', 1)
            RETURNING id
            """,
            (self.garden_id,),
        ).fetchone()
        assert alert is not None
        task = self.conn.execute(
            """
            INSERT INTO garden_tasks
                (garden_id, public_id, task_type, title, description, status,
                 severity, due_on, rule_source, metadata_json,
                 created_by_user_id, created_at_ms, updated_at_ms)
            VALUES (%s, 'tsk_dry_validity', 'water', 'Water during dry spell', '',
                    'pending', 'normal', '2026-07-10', %s, '{}', %s, 1, 1)
            RETURNING id
            """,
            (
                self.garden_id,
                f"auto:dry_water:{int(alert['id'])}:PLT-TEST",
                self._owner_id,
            ),
        ).fetchone()
        assert task is not None

        expired = expire_stale_generated_tasks(
            self.conn,
            garden_id=self.garden_id,
            today_iso="2026-07-14",
            now_ms=1783987200000,
        )

        row = self.conn.execute(
            "SELECT status FROM garden_tasks WHERE id = %s",
            (int(task["id"]),),
        ).fetchone()
        assert expired == 0
        assert row is not None
        assert str(row["status"]) == "pending"

    def test_non_watering_weather_task_stays_active_through_validity_then_expires(self) -> None:
        alert = self.conn.execute(
            """
            INSERT INTO weather_alerts
                (garden_id, alert_type, severity, title, description,
                 valid_from, valid_until, metadata_json, created_at_ms)
            VALUES (%s, 'frost_warning', 'normal', 'Frost', 'Protect plants',
                    '2032-02-03', '2032-02-06', '{}', 1)
            RETURNING id
            """,
            (self.garden_id,),
        ).fetchone()
        assert alert is not None
        task = self.conn.execute(
            """
            INSERT INTO garden_tasks
                (garden_id, public_id, task_type, title, description, status,
                 severity, due_on, rule_source, metadata_json,
                 created_by_user_id, created_at_ms, updated_at_ms)
            VALUES (%s, 'tsk_frost_validity', 'protect', 'Protect from frost', '',
                    'pending', 'normal', '2032-02-03', %s, '{}', %s, 1, 1)
            RETURNING id
            """,
            (
                self.garden_id,
                f"auto:frost_protect:{int(alert['id'])}:PLT-TEST",
                self._owner_id,
            ),
        ).fetchone()
        assert task is not None

        still_active = expire_stale_generated_tasks(
            self.conn,
            garden_id=self.garden_id,
            today_iso="2032-02-06",
            now_ms=1_959_638_400_000,
        )
        active_items = TaskAttentionProvider(frozen_date="2032-02-06").collect(
            self.conn,
            garden_id=self.garden_id,
            user_id=self._owner_id,
            now_ms=1_959_638_400_000,
        )
        hidden_items = TaskAttentionProvider(frozen_date="2032-02-07").collect(
            self.conn,
            garden_id=self.garden_id,
            user_id=self._owner_id,
            now_ms=1_959_724_800_000,
        )
        expired = expire_stale_generated_tasks(
            self.conn,
            garden_id=self.garden_id,
            today_iso="2032-02-07",
            now_ms=1_959_724_800_000,
        )
        task_after = self.conn.execute(
            "SELECT status, metadata_json FROM garden_tasks WHERE id = %s",
            (int(task["id"]),),
        ).fetchone()

        assert still_active == 0
        assert {item.id for item in active_items} >= {"attn:task:tsk_frost_validity"}
        assert "attn:task:tsk_frost_validity" not in {item.id for item in hidden_items}
        assert expired == 1
        assert task_after is not None
        assert str(task_after["status"]) == "expired"
        lifecycle = json.loads(str(task_after["metadata_json"]))["lifecycle"]
        assert lifecycle["reason"] == "weather_alert_validity_ended"
        assert lifecycle["expired_on"] == "2032-02-07"

    def test_non_watering_weather_task_is_skipped_when_alert_is_dismissed(self) -> None:
        alert = self.conn.execute(
            """
            INSERT INTO weather_alerts
                (garden_id, alert_type, severity, title, description,
                 valid_from, valid_until, metadata_json, dismissed, created_at_ms)
            VALUES (%s, 'rain_surplus', 'normal', 'Rain', 'Check drainage',
                    '2032-02-03', '2032-02-10', '{}', 1, 1)
            RETURNING id
            """,
            (self.garden_id,),
        ).fetchone()
        assert alert is not None
        task = self.conn.execute(
            """
            INSERT INTO garden_tasks
                (garden_id, public_id, task_type, title, description, status,
                 severity, due_on, rule_source, metadata_json,
                 created_by_user_id, created_at_ms, updated_at_ms)
            VALUES (%s, 'tsk_rain_dismissed', 'protect', 'Check drainage', '',
                    'pending', 'normal', '2032-02-03', %s, '{}', %s, 1, 1)
            RETURNING id
            """,
            (
                self.garden_id,
                f"auto:rain_drainage:{int(alert['id'])}:PLT-TEST",
                self._owner_id,
            ),
        ).fetchone()
        assert task is not None

        retired = expire_stale_generated_tasks(
            self.conn,
            garden_id=self.garden_id,
            today_iso="2032-02-05",
            now_ms=1_959_552_000_000,
        )
        task_after = self.conn.execute(
            "SELECT status, metadata_json FROM garden_tasks WHERE id = %s",
            (int(task["id"]),),
        ).fetchone()

        assert retired == 1
        assert task_after is not None
        assert str(task_after["status"]) == "skipped"
        lifecycle = json.loads(str(task_after["metadata_json"]))["lifecycle"]
        assert lifecycle["reason"] == "weather_alert_resolved"
        assert lifecycle["skipped_on"] == "2032-02-05"

    def test_expiry_skips_task_locked_by_concurrent_user_action(self) -> None:
        now_ms = current_timestamp_ms()
        task = self.conn.execute(
            """
            INSERT INTO garden_tasks
                (garden_id, public_id, task_type, title, description, status,
                 severity, due_on, rule_source, metadata_json,
                 created_by_user_id, created_at_ms, updated_at_ms)
            VALUES (%s, 'tsk_expiry_race', 'water', 'Water locked plant', '', 'pending',
                    'normal', '2026-06-20', 'water:PLT-TEST:2026-06-20', '{}',
                    %s, %s, %s)
            RETURNING id
            """,
            (self.garden_id, self._owner_id, now_ms, now_ms),
        ).fetchone()
        assert task is not None
        self.conn.commit()

        action_conn = db.get_db()
        maintenance_conn = db.get_db()
        try:
            action_conn.execute(
                "SELECT id FROM garden_tasks WHERE id = %s FOR UPDATE",
                (int(task["id"]),),
            ).fetchone()
            expired = expire_stale_generated_tasks(
                maintenance_conn,
                garden_id=self.garden_id,
                today_iso="2026-07-12",
                now_ms=1783857600000,
            )
            maintenance_conn.commit()
            assert expired == 0

            action_conn.execute(
                """
                UPDATE garden_tasks
                SET status = 'completed', completed_at_ms = %s, updated_at_ms = %s
                WHERE id = %s
                """,
                (1783857600000, 1783857600000, int(task["id"])),
            )
            action_conn.commit()
        finally:
            db.return_db(maintenance_conn)
            db.return_db(action_conn)

        row = self.conn.execute(
            "SELECT status, completed_at_ms FROM garden_tasks WHERE id = %s",
            (int(task["id"]),),
        ).fetchone()
        assert row is not None
        assert row["status"] == "completed"
        assert int(row["completed_at_ms"]) == 1783857600000
