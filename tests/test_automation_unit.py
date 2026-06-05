"""Unit tests for gardenops.services.automation."""

import json
import unittest
from datetime import date, timedelta

import gardenops.db as db
from gardenops.router_helpers import generate_public_id
from gardenops.services.automation import (
    escalate_overdue_follow_ups,
    on_frost_alert,
    on_harvest_logged,
    on_heat_alert,
    on_issue_created,
    on_rain_alert,
)
from tests.base import DbTestBase


class TestOnIssueCreated(DbTestBase):
    def _create_issue(
        self,
        title: str = "Aphids on roses",
        severity: str = "normal",
        follow_up_on: str | None = None,
    ) -> int:
        now_ms = db.current_timestamp_ms()
        cursor = self.conn.execute(
            """INSERT INTO garden_issues
               (public_id, garden_id, issue_type, title, severity, status,
                follow_up_on, created_by_user_id, created_at_ms, updated_at_ms)
               VALUES (%s, %s, 'pest', %s, %s, 'open', %s, %s, %s, %s)
               RETURNING id""",
            (
                generate_public_id("iss"),
                self.garden_id,
                title,
                severity,
                follow_up_on,
                self._owner_id,
                now_ms,
                now_ms,
            ),
        )
        issue_id = cursor.fetchone()["id"]
        self.conn.commit()
        return issue_id

    def test_creates_followup_task(self) -> None:
        issue_id = self._create_issue(follow_up_on="2026-04-01")
        task_id = on_issue_created(
            self.conn,
            self.garden_id,
            issue_id,
            self._owner_id,
        )
        assert task_id > 0
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "inspect_issue"
        assert "Follow up" in task["title"]
        assert task["due_on"] == "2026-04-01"
        assert task["description"], "Follow-up task should have EN description"
        assert "issue iss_" in task["description"]
        meta = json.loads(task["metadata_json"])
        assert "description_no" in meta, "Follow-up task should have NO description"
        assert "sak iss_" in meta["description_no"]

    def test_default_followup_date_when_none(self) -> None:
        issue_id = self._create_issue(follow_up_on=None)
        task_id = on_issue_created(
            self.conn,
            self.garden_id,
            issue_id,
            self._owner_id,
        )
        assert task_id > 0
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
        expected = (date.today() + timedelta(days=7)).isoformat()
        assert task["due_on"] == expected

    def test_dedup_prevents_duplicate_task(self) -> None:
        issue_id = self._create_issue(follow_up_on="2026-04-01")
        first = on_issue_created(
            self.conn,
            self.garden_id,
            issue_id,
            self._owner_id,
        )
        second = on_issue_created(
            self.conn,
            self.garden_id,
            issue_id,
            self._owner_id,
        )
        assert first > 0
        assert second == 0

    def test_nonexistent_issue_returns_zero(self) -> None:
        result = on_issue_created(self.conn, self.garden_id, 9999, None)
        assert result == 0

    def test_severity_normalization(self) -> None:
        issue_id = self._create_issue(severity="high")
        task_id = on_issue_created(
            self.conn,
            self.garden_id,
            issue_id,
            self._owner_id,
        )
        task = self.conn.execute(
            "SELECT severity FROM garden_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
        assert task["severity"] == "high"

    def test_links_plants_and_plots(self) -> None:
        self._insert_plant("IP1", "Rose")
        issue_id = self._create_issue()
        self.conn.execute(
            "INSERT INTO garden_issue_plants (issue_id, plt_id) VALUES (%s, %s)",
            (issue_id, "IP1"),
        )
        self.conn.execute(
            "INSERT INTO garden_issue_plots (issue_id, plot_id) VALUES (%s, %s)",
            (issue_id, "B1"),
        )
        self.conn.commit()

        task_id = on_issue_created(
            self.conn,
            self.garden_id,
            issue_id,
            self._owner_id,
        )
        task_plant = self.conn.execute(
            "SELECT * FROM garden_task_plants WHERE task_id = %s",
            (task_id,),
        ).fetchone()
        assert task_plant is not None
        assert task_plant["plt_id"] == "IP1"

        task_plot = self.conn.execute(
            "SELECT * FROM garden_task_plots WHERE task_id = %s",
            (task_id,),
        ).fetchone()
        assert task_plot is not None
        assert task_plot["plot_id"] == "B1"


class TestOnFrostAlert(DbTestBase):
    def _create_frost_alert(self, valid_from: str = "2026-01-15") -> int:
        now_ms = db.current_timestamp_ms()
        cursor = self.conn.execute(
            """INSERT INTO weather_alerts
               (garden_id, alert_type, severity, title, description,
                valid_from, valid_until, metadata_json, created_at_ms)
               VALUES (%s, 'frost_warning', 'high', 'Frost', 'Cold',
                       %s, %s, '{}', %s)
               RETURNING id""",
            (self.garden_id, valid_from, valid_from, now_ms),
        )
        alert_id = cursor.fetchone()["id"]
        self.conn.commit()
        return alert_id

    def test_creates_protection_tasks(self) -> None:
        self._insert_plant("FP1", "Tender rose", hardiness="H3")
        alert_id = self._create_frost_alert()
        created = on_frost_alert(
            self.conn,
            self.garden_id,
            alert_id,
            self._owner_id,
        )
        assert created == 1
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source LIKE %s",
            (f"auto:frost_protect:{alert_id}:FP1",),
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "protect"
        assert "Protect from frost" in task["title"]
        assert task["severity"] == "high"
        assert task["description"], "Frost task should have EN description"
        frost_meta = json.loads(task["metadata_json"])
        assert "description_no" in frost_meta, "Frost task should have NO description"

    def test_skips_hardy_plants(self) -> None:
        self._insert_plant("FP2", "Super hardy", hardiness="H7")
        alert_id = self._create_frost_alert()
        created = on_frost_alert(
            self.conn,
            self.garden_id,
            alert_id,
            self._owner_id,
        )
        assert created == 0

    def test_skips_h6_plants(self) -> None:
        self._insert_plant("FP3", "Very hardy", hardiness="H6")
        alert_id = self._create_frost_alert()
        created = on_frost_alert(
            self.conn,
            self.garden_id,
            alert_id,
            self._owner_id,
        )
        assert created == 0

    def test_nonexistent_alert_returns_zero(self) -> None:
        result = on_frost_alert(self.conn, self.garden_id, 9999, None)
        assert result == 0

    def test_dedup_prevents_duplicate_tasks(self) -> None:
        self._insert_plant("FP4", "Tender", hardiness="H2")
        alert_id = self._create_frost_alert()
        first = on_frost_alert(
            self.conn,
            self.garden_id,
            alert_id,
            self._owner_id,
        )
        second = on_frost_alert(
            self.conn,
            self.garden_id,
            alert_id,
            self._owner_id,
        )
        assert first == 1
        assert second == 0


class TestWeatherTaskTyping(DbTestBase):
    def _create_alert(self, alert_type: str, valid_from: str = "2026-07-15") -> int:
        now_ms = db.current_timestamp_ms()
        cursor = self.conn.execute(
            """
            INSERT INTO weather_alerts (
                garden_id, alert_type, severity, title, description,
                valid_from, valid_until, metadata_json, created_at_ms
            ) VALUES (%s, %s, 'high', %s, '', %s, %s, '{}', %s)
            RETURNING id
            """,
            (
                self.garden_id,
                alert_type,
                alert_type,
                valid_from,
                valid_from,
                now_ms,
            ),
        )
        alert_id = cursor.fetchone()["id"]
        self.conn.commit()
        return alert_id

    def test_heat_alert_creates_protect_tasks(self) -> None:
        self._insert_plant("HT1", "Heat test", care_watering="Water regularly in summer")
        alert_id = self._create_alert("heatwave")

        created = on_heat_alert(
            self.conn,
            self.garden_id,
            alert_id,
            self._owner_id,
        )

        assert created == 1
        task = self.conn.execute(
            "SELECT task_type FROM garden_tasks WHERE rule_source LIKE %s",
            (f"auto:heat_protect:{alert_id}:HT1",),
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "protect"

    def test_rain_alert_creates_protect_tasks(self) -> None:
        self._insert_plant("RN1", "Rain test", care_watering="Water regularly in summer")
        alert_id = self._create_alert("rain_surplus")

        created = on_rain_alert(
            self.conn,
            self.garden_id,
            alert_id,
            self._owner_id,
        )

        assert created == 1
        task = self.conn.execute(
            "SELECT task_type FROM garden_tasks WHERE rule_source LIKE %s",
            (f"auto:rain_drainage:{alert_id}:RN1",),
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "protect"


class TestOnHarvestLogged(DbTestBase):
    def _create_harvest(
        self,
        quantity: float = 2.5,
        unit: str = "kg",
    ) -> int:
        now_ms = db.current_timestamp_ms()
        today = date.today().isoformat()
        cursor = self.conn.execute(
            """INSERT INTO harvest_entries
               (public_id, garden_id, occurred_on, quantity, unit, quality,
                notes, metadata_json, actor_user_id, created_at_ms, updated_at_ms)
               VALUES (%s, %s, %s, %s, %s, 'good', '', '{}', %s, %s, %s)
               RETURNING id""",
            (
                generate_public_id("hrv"),
                self.garden_id,
                today,
                quantity,
                unit,
                self._owner_id,
                now_ms,
                now_ms,
            ),
        )
        harvest_id = cursor.fetchone()["id"]
        self.conn.commit()
        return harvest_id

    def test_creates_rollup(self) -> None:
        h_id = self._create_harvest(2.5, "kg")
        on_harvest_logged(self.conn, self.garden_id, h_id)
        year = date.today().year
        key = f"harvest_rollup:{self.garden_id}:{year}"
        row = self.conn.execute(
            "SELECT value FROM app_settings WHERE key = %s",
            (key,),
        ).fetchone()
        assert row is not None
        import json

        rollup = json.loads(row["value"])
        assert rollup["year"] == year
        assert len(rollup["by_unit"]) == 1
        assert rollup["by_unit"][0]["unit"] == "kg"
        assert rollup["by_unit"][0]["total_qty"] == 2.5

    def test_rollup_aggregates_multiple_entries(self) -> None:
        self._create_harvest(2.0, "kg")
        h2 = self._create_harvest(3.0, "kg")
        self._create_harvest(5.0, "pieces")
        on_harvest_logged(self.conn, self.garden_id, h2)
        year = date.today().year
        key = f"harvest_rollup:{self.garden_id}:{year}"
        row = self.conn.execute(
            "SELECT value FROM app_settings WHERE key = %s",
            (key,),
        ).fetchone()
        import json

        rollup = json.loads(row["value"])
        units = {u["unit"]: u["total_qty"] for u in rollup["by_unit"]}
        assert units["kg"] == 5.0
        assert units["pieces"] == 5.0

    def test_rollup_replaces_on_update(self) -> None:
        h1 = self._create_harvest(1.0, "kg")
        on_harvest_logged(self.conn, self.garden_id, h1)

        h2 = self._create_harvest(2.0, "kg")
        on_harvest_logged(self.conn, self.garden_id, h2)

        year = date.today().year
        key = f"harvest_rollup:{self.garden_id}:{year}"
        count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM app_settings WHERE key = %s",
            (key,),
        ).fetchone()
        assert count["c"] == 1


class TestEscalateOverdueFollowUps(DbTestBase):
    def _create_issue(
        self,
        title: str = "Aphids on roses",
        severity: str = "normal",
        status: str = "open",
        follow_up_on: str | None = None,
    ) -> int:
        now_ms = db.current_timestamp_ms()
        cursor = self.conn.execute(
            """INSERT INTO garden_issues
               (public_id, garden_id, issue_type, title, severity, status,
                follow_up_on, created_by_user_id, created_at_ms, updated_at_ms)
               VALUES (%s, %s, 'pest', %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                generate_public_id("iss"),
                self.garden_id,
                title,
                severity,
                status,
                follow_up_on,
                self._owner_id,
                now_ms,
                now_ms,
            ),
        )
        issue_id = cursor.fetchone()["id"]
        self.conn.commit()
        return issue_id

    def test_escalates_overdue_issue(self) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        issue_id = self._create_issue(
            severity="normal",
            follow_up_on=yesterday,
        )
        result = escalate_overdue_follow_ups(self.conn, self.garden_id)
        assert result == {"escalated": 1}

        issue = self.conn.execute(
            "SELECT severity, public_id FROM garden_issues WHERE id = %s",
            (issue_id,),
        ).fetchone()
        assert issue["severity"] == "high"

        rule_source = f"auto:escalation:{issue['public_id']}:{yesterday}"
        task = self.conn.execute(
            "SELECT * FROM garden_tasks WHERE rule_source = %s",
            (rule_source,),
        ).fetchone()
        assert task is not None
        assert task["task_type"] == "inspect_issue"
        assert "Overdue follow-up" in task["title"]
        expected_due = (date.today() + timedelta(days=3)).isoformat()
        assert task["due_on"] == expected_due
        assert task["description"], "Escalation task should have EN description"
        assert issue["public_id"] in task["description"]
        esc_meta = json.loads(task["metadata_json"])
        assert "description_no" in esc_meta, "Escalation task should have NO description"
        assert issue["public_id"] in esc_meta["description_no"]

    def test_skips_resolved_issues(self) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self._create_issue(
            status="resolved",
            follow_up_on=yesterday,
        )
        result = escalate_overdue_follow_ups(self.conn, self.garden_id)
        assert result == {"escalated": 0}

    def test_dedup_prevents_double_escalation(self) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self._create_issue(
            severity="low",
            follow_up_on=yesterday,
        )
        first = escalate_overdue_follow_ups(self.conn, self.garden_id)
        second = escalate_overdue_follow_ups(self.conn, self.garden_id)
        assert first == {"escalated": 1}
        assert second == {"escalated": 0}

        task_count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM garden_tasks WHERE rule_source LIKE 'auto:escalation:%'",
        ).fetchone()
        assert task_count["c"] == 1

    def test_skips_future_followups(self) -> None:
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        self._create_issue(follow_up_on=tomorrow)
        result = escalate_overdue_follow_ups(self.conn, self.garden_id)
        assert result == {"escalated": 0}

    def test_links_plants_and_plots(self) -> None:
        self._insert_plant("EP1", "Rose")
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        issue_id = self._create_issue(follow_up_on=yesterday)
        self.conn.execute(
            "INSERT INTO garden_issue_plants (issue_id, plt_id) VALUES (%s, %s)",
            (issue_id, "EP1"),
        )
        self.conn.execute(
            "INSERT INTO garden_issue_plots (issue_id, plot_id) VALUES (%s, %s)",
            (issue_id, "B1"),
        )
        self.conn.commit()

        escalate_overdue_follow_ups(self.conn, self.garden_id)

        issue_public_id = self.conn.execute(
            "SELECT public_id FROM garden_issues WHERE id = %s",
            (issue_id,),
        ).fetchone()["public_id"]
        rule_source = f"auto:escalation:{issue_public_id}:{yesterday}"
        task = self.conn.execute(
            "SELECT id FROM garden_tasks WHERE rule_source = %s",
            (rule_source,),
        ).fetchone()
        assert task is not None

        task_plant = self.conn.execute(
            "SELECT * FROM garden_task_plants WHERE task_id = %s",
            (task["id"],),
        ).fetchone()
        assert task_plant is not None
        assert task_plant["plt_id"] == "EP1"

        task_plot = self.conn.execute(
            "SELECT * FROM garden_task_plots WHERE task_id = %s",
            (task["id"],),
        ).fetchone()
        assert task_plot is not None
        assert task_plot["plot_id"] == "B1"

    def test_severity_cap_at_critical(self) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        issue_id = self._create_issue(
            severity="critical",
            follow_up_on=yesterday,
        )
        escalate_overdue_follow_ups(self.conn, self.garden_id)

        issue = self.conn.execute(
            "SELECT severity FROM garden_issues WHERE id = %s",
            (issue_id,),
        ).fetchone()
        assert issue["severity"] == "critical"

        issue_public_id = self.conn.execute(
            "SELECT public_id FROM garden_issues WHERE id = %s",
            (issue_id,),
        ).fetchone()["public_id"]
        rule_source = f"auto:escalation:{issue_public_id}:{yesterday}"
        task = self.conn.execute(
            "SELECT severity FROM garden_tasks WHERE rule_source = %s",
            (rule_source,),
        ).fetchone()
        assert task is not None
        assert task["severity"] == "high"


if __name__ == "__main__":
    unittest.main()
