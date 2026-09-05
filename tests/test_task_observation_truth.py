import json
import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

import gardenops.db as db
from gardenops.offline_idempotency import (
    OfflineOperation,
    canonical_request_fingerprint,
    reserve_operation,
)
from gardenops.services.observation_clock import observation_timezone, observation_today
from gardenops.services.observation_cycles import is_current_observation_year
from gardenops.services.task_generator import generate_tasks, infer_task_description
from tests.base import BaseApiTest

FROZEN_CLOCK = {
    "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783252800000",
    "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
}


class TestTaskObservationTruth(BaseApiTest):
    def _task(self, *, task_type: str = "observe_bloom", plants: list[str] | None = None) -> dict:
        response = self.client.post(
            "/api/tasks",
            json={
                "task_type": task_type,
                "title": "Record work",
                "due_on": "2026-06-01",
                "plant_ids": plants or ["PLT-TEST"],
                "plot_ids": ["B1", "B2"],
            },
        )
        assert response.status_code == 201, response.text
        return self.client.get(f"/api/tasks/{response.json()['id']}").json()

    def _assign(self, plot: str = "B1") -> None:
        response = self.client.post(f"/api/plots/{plot}/plants/PLT-TEST", json={"quantity": 1})
        assert response.status_code == 201, response.text

    def _complete(self, task: dict, **fields):
        with patch.dict(os.environ, FROZEN_CLOCK):
            return self.client.post(
                f"/api/tasks/{task['id']}/action",
                json={"action": "complete", "completion_outcome": "done", **fields},
            )

    def test_backdate_retains_actual_audit_time_and_current_year_truth(self) -> None:
        self._assign()
        task = self._task()
        response = self._complete(task, occurred_on="2025-05-03", observed_plot_ids=["B1"])
        assert response.status_code == 200, response.text
        entry = self.client.get("/api/journal?plant_id=PLT-TEST").json()["entries"][0]
        assert entry["occurred_on"] == "2025-05-03"
        assert entry["created_at_ms"] == int(FROZEN_CLOCK["GARDENOPS_ATTENTION_FROZEN_NOW_MS"])
        assert entry["metadata"]["observation_scope"] == "explicit"
        assignment = self.client.get("/api/plants/PLT-TEST/assignments").json()[0]
        assert assignment["seen_growing_date"] == "2025-05-03"
        assert not assignment["seen_growing_is_current_year"]
        task = self.client.get(f"/api/tasks/{task['id']}").json()
        assert task["completed_at_ms"] == entry["created_at_ms"]

    def test_invalid_and_future_dates_are_atomic(self) -> None:
        task = self._task()
        for value in ("2026-02-30", "2026-07-06", "20260503", "bad"):
            response = self._complete(task, occurred_on=value)
            assert response.status_code == 422, response.text
        assert self.client.get(f"/api/tasks/{task['id']}").json()["status"] == "pending"
        assert self.client.get("/api/journal?plant_id=PLT-TEST").json()["total"] == 0

    def test_explicit_empty_and_omission_never_infer_single_placement(self) -> None:
        self._assign()
        for fields in ({}, {"observed_plot_ids": []}):
            task = self._task()
            response = self._complete(task, **fields)
            assert response.status_code == 200, response.text
        entries = self.client.get("/api/journal?plant_id=PLT-TEST").json()["entries"]
        assert len(entries) == 2
        assert all(entry["plot_ids"] == [] for entry in entries)
        assert all(entry["metadata"]["observation_scope"] == "explicit" for entry in entries)
        assignment = self.client.get("/api/plants/PLT-TEST/assignments").json()[0]
        assert assignment["seen_growing_date"] is None

    def test_scope_requires_actual_membership_and_preserves_unobserved_plot(self) -> None:
        self._assign()
        task = self._task()
        for plots, status in ((["B2"], 422), (["absent"], 404)):
            response = self._complete(task, observed_plot_ids=plots)
            assert response.status_code == status, response.text
        self._assign("B2")
        response = self._complete(task, observed_plot_ids=["B1"])
        assert response.status_code == 200, response.text
        assignments = {
            item["plot_id"]: item
            for item in self.client.get("/api/plants/PLT-TEST/assignments").json()
        }
        assert assignments["B1"]["seen_growing_date"] == "2026-07-05"
        assert assignments["B2"]["seen_growing_date"] is None

    def test_grouped_bloom_scope_requires_every_selected_plant_at_each_place(self) -> None:
        self._assign("B1")
        assigned = self.client.post("/api/plots/B2/plants/PLT-002", json={"quantity": 1})
        assert assigned.status_code == 201, assigned.text
        task = self._task(plants=["PLT-TEST", "PLT-002"])
        fields = {"completed_plant_ids": ["PLT-TEST", "PLT-002"]}
        rejected = self._complete(task, observed_plot_ids=["B1"], **fields)
        assert rejected.status_code == 422, rejected.text
        assert self.client.get("/api/journal?event_type=bloomed").json()["total"] == 0
        assert self.client.get(f"/api/tasks/{task['id']}").json()["status"] == "pending"
        assigned = self.client.post("/api/plots/B1/plants/PLT-002", json={"quantity": 1})
        assert assigned.status_code == 201, assigned.text
        completed = self._complete(task, observed_plot_ids=["B1"], **fields)
        assert completed.status_code == 200, completed.text
        entry = self.client.get("/api/journal?event_type=bloomed").json()["entries"][0]
        assert sorted(entry["plant_ids"]) == ["PLT-002", "PLT-TEST"]
        assert entry["plot_ids"] == ["B1"]

    def test_batch_and_grouped_partial_use_occurrence_date(self) -> None:
        tasks = [
            self._task(task_type=kind, plants=["PLT-TEST", "PLT-002"])
            for kind in ("prune", "fertilize")
        ]
        with patch.dict(os.environ, FROZEN_CLOCK):
            response = self.client.post(
                "/api/tasks/batch-action",
                json={
                    "action": "complete",
                    "task_ids": [task["id"] for task in tasks],
                    "expected_updated_at_ms_by_task_id": {
                        task["id"]: task["updated_at_ms"] for task in tasks
                    },
                    "completed_plant_ids": ["PLT-TEST"],
                    "occurred_on": "2025-04-02",
                },
            )
        assert response.status_code == 200, response.text
        for task in tasks:
            current = self.client.get(f"/api/tasks/{task['id']}").json()
            assert current["status"] == "pending"
            assert current["plant_ids"] == ["PLT-002"]
        entries = self.client.get("/api/journal?plant_id=PLT-TEST").json()["entries"]
        assert len(entries) == 2
        assert {entry["occurred_on"] for entry in entries} == {"2025-04-02"}

    def test_batch_bloom_scope_is_validated_atomically(self) -> None:
        self._assign()
        tasks = [self._task(), self._task(plants=["PLT-002"])]
        response = self.client.post(
            "/api/tasks/batch-action",
            json={
                "action": "complete",
                "completion_outcome": "done",
                "task_ids": [task["id"] for task in tasks],
                "expected_updated_at_ms_by_task_id": {
                    task["id"]: task["updated_at_ms"] for task in tasks
                },
                "observed_plot_ids": ["B1"],
                "occurred_on": "2025-04-02",
            },
        )
        assert response.status_code == 422, response.text
        assert self.client.get("/api/journal?plant_id=PLT-TEST").json()["total"] == 0
        assert all(
            self.client.get(f"/api/tasks/{task['id']}").json()["status"] == "pending"
            for task in tasks
        )

    def test_old_operation_fingerprint_replays_and_explicit_new_fields_conflict(self) -> None:
        task = self._task()
        completed = self._complete(task)
        assert completed.status_code == 200, completed.text
        payload = {
            "task_id": task["id"],
            "action": "complete",
            "snooze_until": None,
            "reschedule_to": None,
            "notes": None,
            "completed_plant_ids": None,
            "completion_outcome": "done",
            "confirm_outside_window": False,
            "expected_updated_at_ms": task["updated_at_ms"],
        }
        conn = db.get_db()
        try:
            reserve_operation(
                conn,
                operation=OfflineOperation(
                    garden_id=self._get_default_garden_id(),
                    endpoint="task_action",
                    operation_id="legacy-task",
                    request_fingerprint=canonical_request_fingerprint(payload),
                ),
                target_id=task["id"],
                created_at_ms=db.current_timestamp_ms(),
            )
            conn.commit()
        finally:
            db.return_db(conn)
        request = {
            "action": "complete",
            "completion_outcome": "done",
            "expected_updated_at_ms": task["updated_at_ms"],
        }
        for fields, status in (
            ({}, 200),
            ({"observed_plot_ids": []}, 409),
            ({"occurred_on": "2025-05-03"}, 409),
        ):
            response = self.client.post(
                f"/api/tasks/{task['id']}/action",
                json={**request, **fields},
                headers={"X-Offline-Operation-Id": "legacy-task"},
            )
            assert response.status_code == status, response.text
        assert self.client.get("/api/journal?plant_id=PLT-TEST").json()["total"] == 1

    def test_occurrence_date_and_scope_remain_fixed_on_replay(self) -> None:
        self._assign()
        task = self._task()
        payload = {
            "action": "complete",
            "completion_outcome": "done",
            "occurred_on": "2025-05-03",
            "observed_plot_ids": [],
            "expected_updated_at_ms": task["updated_at_ms"],
        }
        for day in ("2026-07-05", "2026-07-06"):
            with patch.dict(os.environ, {**FROZEN_CLOCK, "GARDENOPS_ATTENTION_FROZEN_DATE": day}):
                response = self.client.post(
                    f"/api/tasks/{task['id']}/action",
                    json=payload,
                    headers={"X-Offline-Operation-Id": "fixed-observation"},
                )
            assert response.status_code == 200, response.text
        entries = self.client.get("/api/journal?plant_id=PLT-TEST").json()["entries"]
        assert len(entries) == 1
        assert entries[0]["occurred_on"] == "2025-05-03"
        assert entries[0]["plot_ids"] == []

    def test_closure_uses_occurrence_year_not_task_due_year(self) -> None:
        task = self._task()
        response = self._complete(
            task,
            completion_outcome="not_seen_blooming_this_season",
            occurred_on="2025-05-03",
            observed_plot_ids=[],
        )
        assert response.status_code == 200, response.text
        conn = db.get_db()
        try:
            conn.execute("UPDATE plants SET bloom_month = 'juni' WHERE plt_id = 'PLT-TEST'")
            conn.commit()
            for year in (2025, 2026):
                generate_tasks(conn, self._get_default_garden_id(), 6, year, self._owner_id)
            rows = conn.execute(
                "SELECT rule_source FROM garden_tasks "
                "WHERE rule_source LIKE 'bloom_observe:PLT-TEST:%%'"
            ).fetchall()
            assert [row["rule_source"] for row in rows] == ["bloom_observe:PLT-TEST:2026-06"]
        finally:
            db.return_db(conn)

    def test_timing_refresh_uses_snapshot_without_retrofitting_legacy(self) -> None:
        task = self._task()
        conn = db.get_db()
        try:
            row = dict(
                conn.execute(
                    "SELECT * FROM garden_tasks WHERE public_id = %s", (task["id"],)
                ).fetchone()
            )
            row["rule_source"] = "bloom_observe:PLT-TEST:2026-06"
            desc, _ = infer_task_description(conn, row)
            assert "Timing:" not in desc
            row["metadata_json"] = json.dumps(
                {
                    "bloom_timing": {
                        "source": "local_observations",
                        "effective_months": [7],
                        "observed_months": [7],
                        "catalog_months": [6],
                    }
                }
            )
            desc, _ = infer_task_description(conn, row)
            assert "Timing: recorded local bloom in month(s) 7." in desc
        finally:
            db.return_db(conn)

    def test_task_timezone_is_configured(self) -> None:
        with patch.dict(os.environ, {"GARDENOPS_TIMEZONE": "Pacific/Auckland"}):
            assert self._task()["observation_timezone"] == "Pacific/Auckland"

    def test_reconciliation_does_not_use_remaining_plant_only_task_as_plot_evidence(self) -> None:
        self._assign()
        scoped = self._task()
        assert (
            self._complete(scoped, occurred_on="2025-04-02", observed_plot_ids=["B1"]).status_code
            == 200
        )
        plant_only = self._task()
        assert (
            self._complete(plant_only, occurred_on="2025-05-03", observed_plot_ids=[]).status_code
            == 200
        )
        entries = self.client.get("/api/journal?plant_id=PLT-TEST").json()["entries"]
        scoped_entry = next(entry for entry in entries if entry["plot_ids"])
        response = self.client.delete(f"/api/journal/{scoped_entry['id']}")
        assert response.status_code == 200, response.text
        assignment = self.client.get("/api/plants/PLT-TEST/assignments").json()[0]
        assert assignment["seen_growing_date"] is None

    def test_explicit_plant_only_reconciliation_preserves_same_date_manual_assignment(self) -> None:
        self._assign()
        task = self._task()
        assert (
            self._complete(task, occurred_on="2025-05-03", observed_plot_ids=[]).status_code == 200
        )
        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE plot_plants SET seen_growing = 1, seen_growing_date = '2025-05-03' "
                "WHERE plot_id = 'B1' AND plt_id = 'PLT-TEST'"
            )
            conn.commit()
        finally:
            db.return_db(conn)
        entry = self.client.get("/api/journal?plant_id=PLT-TEST").json()["entries"][0]
        response = self.client.delete(f"/api/journal/{entry['id']}")
        assert response.status_code == 200, response.text
        assignment = self.client.get("/api/plants/PLT-TEST/assignments").json()[0]
        assert assignment["seen_growing_date"] == "2025-05-03"

    def test_plant_only_task_journal_edits_preserve_scope_and_never_infer_placement(self) -> None:
        self._assign()
        task = self._task()
        assert (
            self._complete(task, occurred_on="2025-05-03", observed_plot_ids=[]).status_code == 200
        )
        entry = self.client.get("/api/journal?plant_id=PLT-TEST").json()["entries"][0]
        for updates in (
            {"occurred_on": "2025-05-04", "metadata": {}},
            {"metadata": None},
            {"metadata": {"notes_source": "edited"}, "notes": "Updated notes"},
            {"event_type": "observed"},
            {"event_type": "bloomed"},
        ):
            response = self.client.patch(f"/api/journal/{entry['id']}", json=updates)
            assert response.status_code == 200, response.text
            current = self.client.get(f"/api/journal/{entry['id']}").json()
            assert current["metadata"]["observation_scope"] == "explicit"
            assert current["plot_ids"] == []
            assignment = self.client.get("/api/plants/PLT-TEST/assignments").json()[0]
            assert assignment["seen_growing_date"] is None

    def test_plant_only_edit_reconciliation_preserves_same_date_manual_assignment(self) -> None:
        self._assign()
        task = self._task()
        assert (
            self._complete(task, occurred_on="2025-05-03", observed_plot_ids=[]).status_code == 200
        )
        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE plot_plants SET seen_growing = 1, seen_growing_date = '2025-05-03' "
                "WHERE plot_id = 'B1' AND plt_id = 'PLT-TEST'"
            )
            conn.commit()
        finally:
            db.return_db(conn)
        entry = self.client.get("/api/journal?plant_id=PLT-TEST").json()["entries"][0]
        response = self.client.patch(
            f"/api/journal/{entry['id']}", json={"occurred_on": "2025-05-04"}
        )
        assert response.status_code == 200, response.text
        assignment = self.client.get("/api/plants/PLT-TEST/assignments").json()[0]
        assert assignment["seen_growing_date"] == "2025-05-03"

    def test_removing_explicit_plot_scope_does_not_reinfer_it(self) -> None:
        self._assign()
        task = self._task()
        assert (
            self._complete(task, occurred_on="2025-05-03", observed_plot_ids=["B1"]).status_code
            == 200
        )
        entry = self.client.get("/api/journal?plant_id=PLT-TEST").json()["entries"][0]
        response = self.client.patch(
            f"/api/journal/{entry['id']}", json={"plot_ids": [], "metadata": {}}
        )
        assert response.status_code == 200, response.text
        assignment = self.client.get("/api/plants/PLT-TEST/assignments").json()[0]
        assert assignment["seen_growing_date"] is None
        current = self.client.get(f"/api/journal/{entry['id']}").json()
        assert current["metadata"]["observation_scope"] == "explicit"

    def test_deliberate_metadata_scope_change_reconciles_without_other_edits(self) -> None:
        self._assign()
        task = self._task()
        assert (
            self._complete(task, occurred_on="2025-05-03", observed_plot_ids=[]).status_code == 200
        )
        entry = self.client.get("/api/journal?plant_id=PLT-TEST").json()["entries"][0]
        for scope, expected_date in ((None, "2025-05-03"), ("explicit", None)):
            response = self.client.patch(
                f"/api/journal/{entry['id']}", json={"metadata": {"observation_scope": scope}}
            )
            assert response.status_code == 200, response.text
            assignment = self.client.get("/api/plants/PLT-TEST/assignments").json()[0]
            assert assignment["seen_growing_date"] == expected_date


def test_observation_timezone_fallback_and_invalid_configuration(monkeypatch):
    monkeypatch.delenv("GARDENOPS_TIMEZONE", raising=False)
    monkeypatch.delenv("MATRIX_TIMEZONE", raising=False)
    assert observation_timezone() == "Europe/Oslo"
    monkeypatch.setenv("MATRIX_TIMEZONE", "America/New_York")
    assert observation_timezone() == "America/New_York"
    monkeypatch.setenv("GARDENOPS_TIMEZONE", "Pacific/Auckland")
    assert observation_timezone() == "Pacific/Auckland"
    monkeypatch.setenv("GARDENOPS_TIMEZONE", "Invalid/Zone")
    with pytest.raises(RuntimeError, match="Invalid observation timezone"):
        observation_timezone()


def test_observation_clock_crosses_year_in_configured_zone(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("GARDENOPS_TIMEZONE", "Europe/Oslo")
    timestamp = int(datetime(2025, 12, 31, 23, 30, tzinfo=UTC).timestamp() * 1000)
    today = observation_today(now_ms=timestamp)
    assert today.isoformat() == "2026-01-01"
    assert is_current_observation_year("2026-01-01", today=today)
    assert not is_current_observation_year("2025-12-31", today=today)
