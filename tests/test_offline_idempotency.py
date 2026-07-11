from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest.mock import patch

import gardenops.db as db
from gardenops.offline_idempotency import (
    OFFLINE_OPERATION_REQUEST_CONFLICT_DETAIL,
    OFFLINE_OPERATION_RETENTION_MS,
    OFFLINE_OPERATION_TARGET_GONE_DETAIL,
    OfflineOperation,
    OfflineOperationReservation,
)
from gardenops.offline_idempotency import (
    reserve_operation as reserve_offline_operation,
)
from gardenops.services.observation_updates import mark_seen_growing_from_observation
from tests.base import BaseApiTest

_OPERATION_HEADER = "x-offline-operation-id"


class TestOfflineCreateIdempotency(BaseApiTest):
    def test_bloom_journal_replay_returns_original_without_repeating_side_effects(self) -> None:
        garden_id = self._get_default_garden_id()
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO plot_plants (plot_id, plt_id, quantity, seen_growing, seen_growing_date)
                VALUES ('B1', 'PLT-TEST', 1, NULL, NULL)
                ON CONFLICT (plot_id, plt_id) DO UPDATE
                SET seen_growing = NULL, seen_growing_date = NULL
                """,
            )
            conn.commit()
        finally:
            db.return_db(conn)

        headers = {_OPERATION_HEADER: "offline-journal-bloom-replay"}
        payload = {
            "event_type": "bloomed",
            "occurred_on": "2026-06-15",
            "plant_ids": ["PLT-TEST"],
            "plot_ids": ["B1"],
        }
        with patch(
            "gardenops.routers.journal.mark_seen_growing_from_observation",
            wraps=mark_seen_growing_from_observation,
        ) as mark_seen:
            first = self.client.post("/api/journal", headers=headers, json=payload)
            replay = self.client.post("/api/journal", headers=headers, json=payload)
            conflict = self.client.post(
                "/api/journal",
                headers=headers,
                json={**payload, "title": "Different bloom"},
            )

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(replay.json()["id"], first.json()["id"])
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["detail"], OFFLINE_OPERATION_REQUEST_CONFLICT_DETAIL)
        self.assertEqual(mark_seen.call_count, 1)

        conn = db.get_db()
        try:
            journal_count = conn.execute(
                "SELECT COUNT(*) AS c FROM garden_journal_entries WHERE garden_id = %s",
                (garden_id,),
            ).fetchone()
            link_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM garden_journal_entry_plants links
                JOIN garden_journal_entries entry ON entry.id = links.entry_id
                WHERE entry.garden_id = %s AND links.plt_id = 'PLT-TEST'
                """,
                (garden_id,),
            ).fetchone()
            assignment = conn.execute(
                """
                SELECT seen_growing, seen_growing_date
                FROM plot_plants
                WHERE plot_id = 'B1' AND plt_id = 'PLT-TEST'
                """,
            ).fetchone()
            operation_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM offline_create_operations
                WHERE garden_id = %s AND endpoint = 'journal' AND operation_id = %s
                """,
                (garden_id, headers[_OPERATION_HEADER]),
            ).fetchone()
        finally:
            db.return_db(conn)

        self.assertEqual(int(journal_count["c"]), 1)
        self.assertEqual(int(link_count["c"]), 1)
        self.assertEqual(int(assignment["seen_growing"]), 1)
        self.assertEqual(str(assignment["seen_growing_date"]), "2026-06-15")
        self.assertEqual(int(operation_count["c"]), 1)

    def test_issue_and_harvest_replays_do_not_duplicate_domain_side_effects(self) -> None:
        garden_id = self._get_default_garden_id()
        issue_headers = {_OPERATION_HEADER: "offline-issue-replay"}
        issue_payload = {
            "issue_type": "pest",
            "title": "Aphids",
            "follow_up_on": "2026-07-20",
            "plant_ids": ["PLT-TEST"],
            "plot_ids": ["B1"],
        }
        issue_first = self.client.post("/api/issues", headers=issue_headers, json=issue_payload)
        issue_replay = self.client.post("/api/issues", headers=issue_headers, json=issue_payload)

        self.assertEqual(issue_first.status_code, 201, issue_first.text)
        self.assertEqual(issue_replay.status_code, 201, issue_replay.text)
        issue_id = issue_first.json()["id"]
        self.assertEqual(issue_replay.json()["id"], issue_id)

        harvest_headers = {_OPERATION_HEADER: "offline-harvest-replay"}
        harvest_payload = {
            "occurred_on": date.today().isoformat(),
            "quantity": 2.0,
            "unit": "kg",
            "plant_ids": ["PLT-TEST"],
            "plot_ids": ["B1"],
        }
        harvest_first = self.client.post(
            "/api/harvest",
            headers=harvest_headers,
            json=harvest_payload,
        )
        harvest_replay = self.client.post(
            "/api/harvest",
            headers=harvest_headers,
            json=harvest_payload,
        )

        self.assertEqual(harvest_first.status_code, 201, harvest_first.text)
        self.assertEqual(harvest_replay.status_code, 201, harvest_replay.text)
        harvest_id = harvest_first.json()["id"]
        self.assertEqual(harvest_replay.json()["id"], harvest_id)
        self.assertEqual(
            harvest_replay.json()["journal_entry_id"],
            harvest_first.json()["journal_entry_id"],
        )

        conn = db.get_db()
        try:
            issue_count = conn.execute(
                "SELECT COUNT(*) AS c FROM garden_issues WHERE garden_id = %s",
                (garden_id,),
            ).fetchone()
            followup_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM garden_tasks
                WHERE garden_id = %s AND rule_source = %s
                """,
                (garden_id, f"auto:issue_followup:{issue_id}"),
            ).fetchone()
            issue_journal_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM garden_journal_entries
                WHERE garden_id = %s
                  AND metadata_json::jsonb ->> 'issue_id' = %s
                  AND metadata_json::jsonb ->> 'issue_event' = 'created'
                """,
                (garden_id, issue_id),
            ).fetchone()
            harvest_count = conn.execute(
                "SELECT COUNT(*) AS c FROM harvest_entries WHERE garden_id = %s",
                (garden_id,),
            ).fetchone()
            harvest_journal_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM garden_journal_entries
                WHERE garden_id = %s
                  AND metadata_json::jsonb ->> 'linked_harvest_entry_id' = %s
                """,
                (garden_id, harvest_id),
            ).fetchone()
            operation_rows = conn.execute(
                """
                SELECT endpoint, COUNT(*) AS c
                FROM offline_create_operations
                WHERE garden_id = %s AND operation_id IN (%s, %s)
                GROUP BY endpoint
                """,
                (garden_id, issue_headers[_OPERATION_HEADER], harvest_headers[_OPERATION_HEADER]),
            ).fetchall()
            rollup = conn.execute(
                "SELECT value FROM app_settings WHERE key = %s",
                (f"harvest_rollup:{garden_id}:{date.today().year}",),
            ).fetchone()
        finally:
            db.return_db(conn)

        self.assertEqual(int(issue_count["c"]), 1)
        self.assertEqual(int(followup_count["c"]), 1)
        self.assertEqual(int(issue_journal_count["c"]), 1)
        self.assertEqual(int(harvest_count["c"]), 1)
        self.assertEqual(int(harvest_journal_count["c"]), 1)
        self.assertEqual(
            {str(row["endpoint"]): int(row["c"]) for row in operation_rows},
            {"issues": 1, "harvest": 1},
        )
        assert rollup is not None
        by_unit = json.loads(str(rollup["value"]))["by_unit"]
        self.assertEqual(by_unit, [{"unit": "kg", "total_qty": 2.0, "entries": 1}])

    def test_concurrent_different_payload_loser_rolls_back_issue_side_effects(self) -> None:
        garden_id = self._get_default_garden_id()
        operation_id = "offline-concurrent-issue-conflict"
        headers = {_OPERATION_HEADER: operation_id}
        payloads = [
            {
                "issue_type": "pest",
                "title": "Concurrent aphids",
                "follow_up_on": "2026-07-20",
            },
            {
                "issue_type": "damage",
                "title": "Concurrent broken stem",
                "follow_up_on": "2026-07-21",
            },
        ]
        barrier = Barrier(2)

        def synchronized_reservation(
            db_conn: db.DbConn,
            *,
            operation: OfflineOperation,
            target_id: str | int,
            created_at_ms: int,
            target_type: object | None = None,
            result_id: str | int | None = None,
        ) -> OfflineOperationReservation:
            barrier.wait(timeout=10)
            return reserve_offline_operation(
                db_conn,
                operation=operation,
                target_id=target_id,
                created_at_ms=created_at_ms,
                target_type=target_type,
                result_id=result_id,
            )

        clients = [self._new_client(), self._new_client()]
        with patch(
            "gardenops.routers.issues.reserve_operation",
            side_effect=synchronized_reservation,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(client.post, "/api/issues", headers=headers, json=payload)
                    for client, payload in zip(clients, payloads, strict=True)
                ]
                responses = [future.result(timeout=20) for future in futures]

        self.assertEqual(sorted(response.status_code for response in responses), [201, 409])
        winner_index = next(
            index for index, response in enumerate(responses) if response.status_code == 201
        )
        conflict_response = next(response for response in responses if response.status_code == 409)
        self.assertEqual(
            conflict_response.json()["detail"],
            OFFLINE_OPERATION_REQUEST_CONFLICT_DETAIL,
        )

        conn = db.get_db()
        try:
            issues = conn.execute(
                "SELECT id, public_id, title FROM garden_issues WHERE garden_id = %s",
                (garden_id,),
            ).fetchall()
            issue_journal_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM garden_journal_entries
                WHERE garden_id = %s
                  AND metadata_json::jsonb ->> 'issue_event' = 'created'
                """,
                (garden_id,),
            ).fetchone()
            followup_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM garden_tasks
                WHERE garden_id = %s AND task_type = 'inspect_issue'
                """,
                (garden_id,),
            ).fetchone()
            operation = conn.execute(
                """
                SELECT request_fingerprint
                FROM offline_create_operations
                WHERE garden_id = %s AND endpoint = 'issues' AND operation_id = %s
                """,
                (garden_id, operation_id),
            ).fetchone()
        finally:
            db.return_db(conn)

        self.assertEqual(len(issues), 1)
        self.assertEqual(str(issues[0]["title"]), payloads[winner_index]["title"])
        self.assertEqual(int(issue_journal_count["c"]), 1)
        self.assertEqual(int(followup_count["c"]), 1)
        assert operation is not None
        self.assertEqual(len(str(operation["request_fingerprint"])), 64)

    def test_task_actions_replay_once_and_reject_changed_payloads(self) -> None:
        completed_task_id = ""
        actions = [
            ("complete", {"action": "complete", "notes": "Finished task"}, "completed"),
            ("skip", {"action": "skip", "notes": "Skip task"}, "skipped"),
            (
                "snooze",
                {
                    "action": "snooze",
                    "snooze_until": "2026-08-01",
                    "notes": "Snooze task",
                },
                "snoozed",
            ),
            (
                "reschedule",
                {
                    "action": "reschedule",
                    "reschedule_to": "2026-08-15",
                    "notes": "Reschedule task",
                },
                "pending",
            ),
        ]

        for action, body, expected_status in actions:
            task_payload: dict[str, object] = {
                "task_type": "fertilize" if action == "complete" else "water",
                "title": f"Offline {action}",
                "due_on": "2026-07-01",
            }
            if action == "complete":
                task_payload["plant_ids"] = ["PLT-TEST"]
            created = self.client.post("/api/tasks", json=task_payload)
            self.assertEqual(created.status_code, 201, created.text)
            task_id = created.json()["id"]
            headers = {_OPERATION_HEADER: f"offline-task-{action}"}

            first = self.client.post(f"/api/tasks/{task_id}/action", headers=headers, json=body)
            replay = self.client.post(f"/api/tasks/{task_id}/action", headers=headers, json=body)
            conflict = self.client.post(
                f"/api/tasks/{task_id}/action",
                headers=headers,
                json={**body, "notes": f"Changed {action} task"},
            )

            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertEqual(conflict.status_code, 409, conflict.text)
            self.assertEqual(
                conflict.json()["detail"],
                OFFLINE_OPERATION_REQUEST_CONFLICT_DETAIL,
            )

            task = self.client.get(f"/api/tasks/{task_id}")
            self.assertEqual(task.status_code, 200, task.text)
            task_body = task.json()
            self.assertEqual(task_body["status"], expected_status)
            action_notes = task_body["metadata"].get("action_notes", [])
            self.assertEqual(len(action_notes), 1)
            if action == "snooze":
                self.assertEqual(task_body["snoozed_until"], "2026-08-01")
            if action == "reschedule":
                self.assertEqual(task_body["due_on"], "2026-08-15")
            if action == "complete":
                completed_task_id = task_id

        conn = db.get_db()
        try:
            completion_journal_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM garden_journal_entries
                WHERE metadata_json::jsonb ->> 'source_task_id' = %s
                """,
                (completed_task_id,),
            ).fetchone()
            task_operation_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM offline_create_operations
                WHERE endpoint = 'task_action'
                """,
            ).fetchone()
        finally:
            db.return_db(conn)

        self.assertEqual(int(completion_journal_count["c"]), 1)
        self.assertEqual(int(task_operation_count["c"]), 4)

    def test_attachment_lost_ack_replays_each_media_operation_once(self) -> None:
        previous_rate_limit = os.environ.get("MEDIA_UPLOAD_RATE_LIMIT")
        os.environ["MEDIA_UPLOAD_RATE_LIMIT"] = "100"
        try:
            journal = self.client.post(
                "/api/journal",
                json={"event_type": "observed", "occurred_on": "2026-07-01"},
            )
            issue = self.client.post(
                "/api/issues",
                json={"issue_type": "pest", "title": "Offline media issue"},
            )
            harvest = self.client.post(
                "/api/harvest",
                json={
                    "occurred_on": date.today().isoformat(),
                    "quantity": 1,
                    "unit": "pieces",
                },
            )
            for response in (journal, issue, harvest):
                self.assertEqual(response.status_code, 201, response.text)

            targets = [
                ("journal_entry", journal.json()["id"]),
                ("issue", issue.json()["id"]),
                ("harvest_entry", harvest.json()["id"]),
                ("plant", "PLT-TEST"),
                ("plot", "B1"),
            ]
            first_attachment: tuple[str, str, dict[str, str], bytes] | None = None
            for target_index, (target_type, target_id) in enumerate(targets):
                for attachment_index in range(2):
                    operation_id = f"offline-{target_type}-attachment-{attachment_index}"
                    payload = self._image_bytes(
                        fmt="PNG",
                        color=(60 + target_index, 100 + attachment_index, 140, 255),
                    )
                    headers = {
                        _OPERATION_HEADER: operation_id,
                        "content-type": "image/png",
                        "x-upload-filename": f"{target_type}-{attachment_index}.png",
                    }
                    path = f"/api/media/upload?target_type={target_type}&target_id={target_id}"
                    first = self.client.post(path, content=payload, headers=headers)
                    replay = self.client.post(path, content=payload, headers=headers)

                    self.assertEqual(first.status_code, 201, first.text)
                    self.assertEqual(replay.status_code, 201, replay.text)
                    self.assertEqual(replay.json()["asset_id"], first.json()["asset_id"])
                    if first_attachment is None:
                        first_attachment = (path, target_type, headers, payload)

            assert first_attachment is not None
            path, _target_type, headers, _payload = first_attachment
            conflict = self.client.post(
                path,
                content=self._image_bytes(fmt="PNG", color=(210, 70, 80, 255)),
                headers=headers,
            )
            self.assertEqual(conflict.status_code, 409, conflict.text)
            self.assertEqual(
                conflict.json()["detail"],
                OFFLINE_OPERATION_REQUEST_CONFLICT_DETAIL,
            )

            conn = db.get_db()
            try:
                asset_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM media_assets",
                ).fetchone()
                operation_count = conn.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM offline_create_operations
                    WHERE endpoint = 'media_upload'
                    """,
                ).fetchone()
            finally:
                db.return_db(conn)

            self.assertEqual(int(asset_count["c"]), 10)
            self.assertEqual(int(operation_count["c"]), 10)
        finally:
            self._restore_env("MEDIA_UPLOAD_RATE_LIMIT", previous_rate_limit)

    def test_deleted_target_keeps_operation_key_returns_gone_and_allows_new_operation(self) -> None:
        payload = {"event_type": "observed", "occurred_on": "2026-07-02"}
        headers = {_OPERATION_HEADER: "offline-deleted-journal"}
        created = self.client.post("/api/journal", headers=headers, json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        journal_id = created.json()["id"]

        attachment_headers = {
            _OPERATION_HEADER: "offline-deleted-journal-attachment",
            "content-type": "image/png",
            "x-upload-filename": "deleted-target.png",
        }
        attachment_payload = self._image_bytes(fmt="PNG")
        attachment = self.client.post(
            f"/api/media/upload?target_type=journal_entry&target_id={journal_id}",
            content=attachment_payload,
            headers=attachment_headers,
        )
        self.assertEqual(attachment.status_code, 201, attachment.text)

        deleted = self.client.delete(f"/api/journal/{journal_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)

        conflict = self.client.post(
            "/api/journal",
            headers=headers,
            json={**payload, "title": "Changed after deletion"},
        )
        replay = self.client.post("/api/journal", headers=headers, json=payload)
        attachment_replay = self.client.post(
            f"/api/media/upload?target_type=journal_entry&target_id={journal_id}",
            content=attachment_payload,
            headers=attachment_headers,
        )

        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(
            conflict.json()["detail"],
            OFFLINE_OPERATION_REQUEST_CONFLICT_DETAIL,
        )
        for response in (replay, attachment_replay):
            self.assertEqual(response.status_code, 410, response.text)
            self.assertEqual(response.json()["detail"], OFFLINE_OPERATION_TARGET_GONE_DETAIL)

        replacement = self.client.post(
            "/api/journal",
            headers={_OPERATION_HEADER: "offline-recreated-journal"},
            json=payload,
        )
        self.assertEqual(replacement.status_code, 201, replacement.text)
        self.assertNotEqual(replacement.json()["id"], journal_id)

        conn = db.get_db()
        try:
            operation_rows = conn.execute(
                """
                SELECT endpoint, target_id, created_at_ms, expires_at_ms
                FROM offline_create_operations
                WHERE operation_id IN (%s, %s)
                ORDER BY endpoint
                """,
                (headers[_OPERATION_HEADER], attachment_headers[_OPERATION_HEADER]),
            ).fetchall()
        finally:
            db.return_db(conn)

        self.assertEqual(len(operation_rows), 2)
        self.assertEqual(
            {str(row["endpoint"]) for row in operation_rows}, {"journal", "media_upload"}
        )
        for row in operation_rows:
            self.assertEqual(
                int(row["expires_at_ms"]) - int(row["created_at_ms"]),
                OFFLINE_OPERATION_RETENTION_MS,
            )
        journal_operation = next(row for row in operation_rows if row["endpoint"] == "journal")
        self.assertEqual(str(journal_operation["target_id"]), journal_id)

    def test_expiry_boundary_allows_a_new_create_with_the_same_operation_id(self) -> None:
        headers = {_OPERATION_HEADER: "offline-expiry-boundary"}
        payload = {"event_type": "watered", "occurred_on": "2026-07-03"}
        first = self.client.post("/api/journal", headers=headers, json=payload)
        self.assertEqual(first.status_code, 201, first.text)

        garden_id = self._get_default_garden_id()
        conn = db.get_db()
        try:
            conn.execute(
                """
                UPDATE offline_create_operations
                SET expires_at_ms = %s
                WHERE garden_id = %s AND endpoint = 'journal' AND operation_id = %s
                """,
                (db.current_timestamp_ms(), garden_id, headers[_OPERATION_HEADER]),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        replay_after_expiry = self.client.post("/api/journal", headers=headers, json=payload)
        self.assertEqual(replay_after_expiry.status_code, 201, replay_after_expiry.text)
        self.assertNotEqual(replay_after_expiry.json()["id"], first.json()["id"])

        conn = db.get_db()
        try:
            operation_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM offline_create_operations
                WHERE garden_id = %s AND endpoint = 'journal' AND operation_id = %s
                """,
                (garden_id, headers[_OPERATION_HEADER]),
            ).fetchone()
        finally:
            db.return_db(conn)

        self.assertEqual(int(operation_count["c"]), 1)

    def test_operation_ids_are_scoped_to_garden_and_endpoint(self) -> None:
        previous_auth_required = os.environ.get("AUTH_REQUIRED")
        previous_auth_mode = os.environ.get("AUTH_MODE")
        previous_api_key = os.environ.get("AUTH_API_KEY")
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        os.environ["AUTH_API_KEY"] = ""
        try:
            first_garden_id, second_garden_id, username, password = self._setup_admin_two_gardens()
            client = self._new_client()
            _, csrf = self._login_session(username, password, client=client)
            operation_id = "offline-shared-operation-id"
            first_headers = self._session_headers(
                csrf,
                garden_id=first_garden_id,
                extra={_OPERATION_HEADER: operation_id},
            )
            second_headers = self._session_headers(
                csrf,
                garden_id=second_garden_id,
                extra={_OPERATION_HEADER: operation_id},
            )

            journal_first = client.post(
                "/api/journal",
                headers=first_headers,
                json={"event_type": "observed", "occurred_on": "2026-06-01"},
            )
            journal_second_garden = client.post(
                "/api/journal",
                headers=second_headers,
                json={"event_type": "observed", "occurred_on": "2026-06-02"},
            )
            issue = client.post(
                "/api/issues",
                headers=first_headers,
                json={"issue_type": "damage", "title": "Broken stem"},
            )
            harvest = client.post(
                "/api/harvest",
                headers=first_headers,
                json={
                    "occurred_on": "2026-06-03",
                    "quantity": 1.0,
                    "unit": "pieces",
                },
            )

            for response in (journal_first, journal_second_garden, issue, harvest):
                self.assertEqual(response.status_code, 201, response.text)
            self.assertNotEqual(journal_first.json()["id"], journal_second_garden.json()["id"])
            self.assertTrue(issue.json()["id"].startswith("iss_"))
            self.assertTrue(harvest.json()["id"].startswith("hrv_"))

            cross_garden_get = client.get(
                f"/api/journal/{journal_first.json()['id']}",
                headers=second_headers,
            )
            self.assertEqual(cross_garden_get.status_code, 404)

            conn = db.get_db()
            try:
                operation_rows = conn.execute(
                    """
                    SELECT garden_id, endpoint, COUNT(*) AS c
                    FROM offline_create_operations
                    WHERE operation_id = %s
                    GROUP BY garden_id, endpoint
                    ORDER BY garden_id, endpoint
                    """,
                    (operation_id,),
                ).fetchall()
            finally:
                db.return_db(conn)

            self.assertEqual(
                {
                    (int(row["garden_id"]), str(row["endpoint"])): int(row["c"])
                    for row in operation_rows
                },
                {
                    (first_garden_id, "harvest"): 1,
                    (first_garden_id, "issues"): 1,
                    (first_garden_id, "journal"): 1,
                    (second_garden_id, "journal"): 1,
                },
            )
        finally:
            self._restore_env("AUTH_REQUIRED", previous_auth_required)
            self._restore_env("AUTH_MODE", previous_auth_mode)
            self._restore_env("AUTH_API_KEY", previous_api_key)

        first_online = self.client.post(
            "/api/journal",
            json={"event_type": "watered", "occurred_on": "2026-06-04"},
        )
        second_online = self.client.post(
            "/api/journal",
            json={"event_type": "watered", "occurred_on": "2026-06-04"},
        )
        self.assertEqual(first_online.status_code, 201, first_online.text)
        self.assertEqual(second_online.status_code, 201, second_online.text)
        self.assertNotEqual(first_online.json()["id"], second_online.json()["id"])

    @staticmethod
    def _restore_env(name: str, previous: str | None) -> None:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous
