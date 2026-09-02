from __future__ import annotations

import json
import os
from unittest.mock import patch

import gardenops.db as db
from gardenops.db import current_timestamp_ms
from gardenops.security import AuthContext
from gardenops.services.assistant import apply_request
from gardenops.services.integration_config import AssistantBinding
from tests.base import BaseApiTest

CAPTURE_TOKEN = (  # push-sanitizer: allow SECRET_ASSIGNMENT - fixed disposable fixture
    "assistant-action-test-token-0123456789abcdef"
)


class TestAssistantActions(BaseApiTest):
    def _binding(self) -> AssistantBinding:
        return AssistantBinding(
            room_id="!garden:example.org",
            sender_id="@owner:example.org",
            username="test_admin",
            garden_slug="default",
            user_id=self._owner_id,
            garden_id=self._get_default_garden_id(),
            context=AuthContext(
                user_id=self._owner_id,
                username="test_admin",
                role="admin",
                auth_type="session",
                garden_id=self._get_default_garden_id(),
                garden_role="admin",
                subscription_tier="pro",
            ),
        )

    def _proposal(self, kind: str, fields: dict, *, capture_asset_id: str = "") -> str:
        request_id = f"asst_{kind}_{current_timestamp_ms()}"
        proposal = {"kind": kind, "summary": f"Test {kind}", "fields": fields}
        now = current_timestamp_ms()
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO assistant_requests
                    (public_id, garden_id, actor_user_id, source_channel,
                     source_room_id, source_event_id, source_sender_id,
                     request_kind, state, input_text, capture_asset_id,
                     payload_json, result_json,
                     created_at_ms, updated_at_ms, expires_at_ms, last_source_event_id)
                VALUES (%s, %s, %s, 'matrix', %s, %s, %s, %s, 'proposal', '',
                        %s, %s, '{}', %s, %s, %s, %s)
                """,
                (
                    request_id,
                    self._get_default_garden_id(),
                    self._owner_id,
                    "!garden:example.org",
                    f"${request_id}",
                    "@owner:example.org",
                    kind,
                    capture_asset_id or None,
                    json.dumps({"schema_version": 1, "proposal": proposal}),
                    now,
                    now,
                    now + 60_000,
                    f"${request_id}",
                ),
            )
            conn.commit()
        finally:
            db.return_db(conn)
        return request_id

    def _apply(self, request_id: str):  # type: ignore[no-untyped-def]
        conn = db.get_db()
        try:
            result = apply_request(
                conn,
                self._binding(),
                request_id=request_id,
                source_event_id=f"$save-{request_id}",
            )
            conn.commit()
            return result
        finally:
            db.return_db(conn)

    def test_journal_apply_is_atomic_and_idempotent(self) -> None:
        integration_env = {
            "MCP_ENABLED": "true",
            "MCP_BEARER_TOKEN": CAPTURE_TOKEN,
            "MATRIX_ROOM_ID": "!garden:example.org",
            "MATRIX_ALLOWED_SENDER": "@owner:example.org",
            "MATRIX_GARDENOPS_USERNAME": "test_admin",
            "MATRIX_GARDEN_SLUG": "default",
        }
        with patch.dict(os.environ, integration_env, clear=False):
            captured = self.client.post(
                "/api/integrations/matrix/captures",
                content=self._image_bytes(),
                headers={
                    "Authorization": f"Bearer {CAPTURE_TOKEN}",
                    "Content-Type": "image/png",
                    "X-Matrix-Room-Id": "!garden:example.org",
                    "X-Matrix-Event-Id": "$journal-photo",
                    "X-Matrix-Sender": "@owner:example.org",
                },
            )
        self.assertEqual(captured.status_code, 201, captured.text)
        capture_asset_id = str(captured.json()["capture_asset_id"])
        request_id = self._proposal(
            "journal",
            {
                "schema_version": 1,
                "event_type": "observed",
                "occurred_on": "2026-09-02",
                "title": "Rose check",
                "notes": "Healthy",
                "plant_ids": ["PLT-002"],
                "plot_ids": [],
                "metadata": {},
            },
            capture_asset_id=capture_asset_id,
        )
        first = self._apply(request_id)
        repeated = self._apply(request_id)
        self.assertEqual(first.state, "applied")
        self.assertEqual(repeated.records, first.records)
        conn = db.get_db()
        try:
            count = conn.execute("SELECT COUNT(*) AS count FROM garden_journal_entries").fetchone()
            self.assertEqual(int(count["count"]), 1)
            links = conn.execute(
                "SELECT target_type FROM media_links WHERE asset_id = %s",
                (capture_asset_id,),
            ).fetchall()
            self.assertEqual(
                {str(row["target_type"]) for row in links},
                {"journal_entry", "plant"},
            )
        finally:
            db.return_db(conn)

    def test_harvest_issue_and_task_proposals_use_domain_commands(self) -> None:
        harvest = self._proposal(
            "harvest",
            {
                "schema_version": 1,
                "occurred_on": "2026-09-02",
                "quantity": 3,
                "unit": "kg",
                "quality": "good",
                "notes": "Ripe",
                "plant_ids": ["PLT-002"],
                "plot_ids": [],
            },
        )
        issue = self._proposal(
            "issue",
            {
                "schema_version": 1,
                "issue_type": "pest",
                "title": "Aphids",
                "description": "Seen on shoots",
                "severity": "normal",
                "suspected_cause": "Aphids",
                "treatment_plan": "Inspect weekly",
                "follow_up_on": None,
                "plant_ids": ["PLT-002"],
                "plot_ids": [],
            },
        )
        created_task = self.client.post(
            "/api/tasks",
            json={
                "task_type": "prune",
                "title": "Prune rose",
                "due_on": "2026-09-02",
                "plant_ids": ["PLT-002"],
            },
        )
        self.assertEqual(created_task.status_code, 201, created_task.text)
        task_data = created_task.json()
        conn = db.get_db()
        try:
            task_row = conn.execute(
                "SELECT updated_at_ms FROM garden_tasks WHERE public_id = %s",
                (task_data["id"],),
            ).fetchone()
            assert task_row is not None
            task_updated_at_ms = int(task_row["updated_at_ms"])
        finally:
            db.return_db(conn)
        task = self._proposal(
            "task_completion",
            {
                "schema_version": 1,
                "task_id": task_data["id"],
                "expected_updated_at_ms": task_updated_at_ms,
                "completed_plant_ids": ["PLT-002"],
                "completion_outcome": "done",
                "notes": "Finished",
                "occurred_on": "2026-09-02",
            },
        )
        self.assertEqual(self._apply(harvest).state, "applied")
        self.assertEqual(self._apply(issue).state, "applied")
        self.assertEqual(self._apply(task).state, "applied")
        conn = db.get_db()
        try:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM harvest_entries").fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM garden_issues").fetchone())
            row = conn.execute(
                "SELECT status FROM garden_tasks WHERE public_id = %s", (task_data["id"],)
            ).fetchone()
            self.assertEqual(row["status"], "completed")
        finally:
            db.return_db(conn)

    def test_bloom_apply_updates_seen_growing(self) -> None:
        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE plants SET seen_growing = NULL, seen_growing_date = NULL "
                "WHERE plt_id = 'PLT-002'"
            )
            conn.commit()
        finally:
            db.return_db(conn)
        request_id = self._proposal(
            "journal",
            {
                "schema_version": 1,
                "event_type": "bloomed",
                "occurred_on": "2026-09-02",
                "title": "First bloom",
                "notes": "Seen from Matrix",
                "plant_ids": ["PLT-002"],
                "plot_ids": [],
                "metadata": {},
            },
        )
        self.assertEqual(self._apply(request_id).state, "applied")
        conn = db.get_db()
        try:
            row = conn.execute(
                "SELECT seen_growing, seen_growing_date FROM plants WHERE plt_id = 'PLT-002'"
            ).fetchone()
            self.assertEqual(int(row["seen_growing"]), 1)
            self.assertEqual(str(row["seen_growing_date"]), "2026-09-02")
        finally:
            db.return_db(conn)

    def test_apply_failure_rolls_back_domain_write_and_keeps_proposal(self) -> None:
        request_id = self._proposal(
            "journal",
            {
                "schema_version": 1,
                "event_type": "observed",
                "occurred_on": "2026-09-02",
                "title": "Atomicity check",
                "notes": "Must roll back",
                "plant_ids": ["PLT-002"],
                "plot_ids": [],
                "metadata": {},
            },
        )
        conn = db.get_db()
        try:
            with patch(
                "gardenops.services.assistant.write_required_audit_event",
                side_effect=RuntimeError("audit unavailable"),
            ):
                with self.assertRaises(RuntimeError):
                    apply_request(
                        conn,
                        self._binding(),
                        request_id=request_id,
                        source_event_id="$failed-save",
                    )
            conn.rollback()
            request = conn.execute(
                "SELECT state FROM assistant_requests WHERE public_id = %s",
                (request_id,),
            ).fetchone()
            journal = conn.execute(
                "SELECT 1 FROM garden_journal_entries WHERE title = 'Atomicity check'"
            ).fetchone()
            self.assertEqual(request["state"], "proposal")
            self.assertIsNone(journal)
        finally:
            db.return_db(conn)
