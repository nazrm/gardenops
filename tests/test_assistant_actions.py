from __future__ import annotations

import json
import os
from unittest.mock import patch

from fastapi import HTTPException

import gardenops.db as db
from gardenops.db import current_timestamp_ms
from gardenops.incident_controls import set_emergency_read_only
from gardenops.security import AuthContext
from gardenops.services.assistant import (
    analyze_matrix_capture,
    apply_request,
    continue_request,
    process_text,
)
from gardenops.services.assistant_models import (
    AssistantIntent,
    CaptureAnalysis,
    CapturePlantCandidate,
)
from gardenops.services.domain_commands import assign_plant_command
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

    def test_emergency_read_only_prevents_applying_existing_plant_delete(self) -> None:
        request_id = self._proposal(
            "plant_delete",
            {"schema_version": 1, "plant_id": "PLT-002"},
        )
        conn = db.get_db()
        try:
            set_emergency_read_only(True, conn=conn)
            with self.assertRaises(HTTPException) as raised:
                apply_request(
                    conn,
                    self._binding(),
                    request_id=request_id,
                    source_event_id="$save-delete-read-only",
                )

            self.assertEqual(raised.exception.status_code, 503)
            self.assertEqual(
                raised.exception.detail,
                "Emergency read-only mode is active",
            )
            self.assertIsNotNone(
                conn.execute("SELECT 1 FROM plants WHERE plt_id = 'PLT-002'").fetchone()
            )
            state = conn.execute(
                "SELECT state FROM assistant_requests WHERE public_id = %s",
                (request_id,),
            ).fetchone()
            self.assertEqual(str(state["state"]), "proposal")
        finally:
            conn.rollback()
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
        self.assertEqual(repeated.message, first.message)
        self.assertIn("observed", first.message)
        self.assertIn("2026-09-02", first.message)
        self.assertIn(first.reference, first.message)
        self.assertNotIn("journal_entry", first.message)
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
        harvested = self._apply(harvest)
        self.assertEqual(harvested.state, "applied")
        self.assertIn("harvested 3 kg", harvested.message)
        reported = self._apply(issue)
        self.assertEqual(reported.state, "applied")
        self.assertIn("Aphids", reported.message)
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

    def test_photo_can_create_a_fully_enriched_plant_in_a_selected_plot(self) -> None:
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
                    "X-Matrix-Event-Id": "$new-plant-photo",
                    "X-Matrix-Sender": "@owner:example.org",
                },
            )
        self.assertEqual(captured.status_code, 201, captured.text)
        capture_id = str(captured.json()["capture_asset_id"])
        analysis = CaptureAnalysis(
            plant_candidates=[
                CapturePlantCandidate(
                    name="Blacklist lily",
                    latin="Lilium 'Blacklist'",
                    confidence=0.96,
                    source="test",
                )
            ]
        )
        intent_without_quantity = AssistantIntent(
            intent="plant_create",
            confidence=0.98,
            destination_plot_query="B1",
        )
        enriched = {
            "name": "Svart lilje 'Blacklist'",
            "latin": "Lilium 'Blacklist'",
            "category": "løk",
            "bloom_month": "juli-august",
            "color": "mørk burgunder",
            "hardiness": "H6",
            "height_cm": 100,
            "light": "sol",
            "link": "https://www.rhs.org.uk/plants/example/blacklist",
            "deer_resistant": True,
            "care_watering": "Water during dry periods.",
            "care_soil": "Use freely draining soil.",
            "care_planting": "Plant bulbs deeply.",
            "care_maintenance": "Remove faded flowers.",
            "care_notes": "Protect young shoots.",
            "year_planted": None,
        }
        conn = db.get_db()
        try:
            with (
                patch("gardenops.services.assistant.analyze_capture", return_value=analysis),
                patch(
                    "gardenops.services.assistant._interpret",
                    return_value=intent_without_quantity,
                ),
                patch("gardenops.services.assistant._enrich_new_plant", return_value=enriched),
            ):
                needs_quantity = analyze_matrix_capture(
                    conn,
                    self._binding(),
                    source_room_id="!garden:example.org",
                    source_event_id="$new-plant-photo",
                    source_sender_id="@owner:example.org",
                    capture_asset_id=capture_id,
                    caption="Add this to B1",
                    occurred_on="2026-09-02",
                )
                self.assertEqual(needs_quantity.state, "needs_input")
                self.assertIn("How many", needs_quantity.message)
                proposal = continue_request(
                    conn,
                    self._binding(),
                    request_id=needs_quantity.request_id,
                    source_event_id="$new-plant-quantity",
                    text="1",
                )
                conn.commit()
            self.assertEqual(proposal.state, "proposal")
            self.assertEqual(proposal.proposal.kind, "plant_create")
            applied = apply_request(
                conn,
                self._binding(),
                request_id=proposal.request_id,
                source_event_id="$save-new-plant",
            )
            conn.commit()
            plant_id = applied.records[0].id
            row = conn.execute(
                """
                SELECT p.*, pp.plot_id, pp.quantity
                FROM plants p JOIN plot_plants pp ON pp.plt_id = p.plt_id
                WHERE p.plt_id = %s
                """,
                (plant_id,),
            ).fetchone()
            self.assertEqual(row["latin"], "Lilium 'Blacklist'")
            self.assertEqual(row["care_watering"], "Water during dry periods.")
            self.assertEqual(row["plot_id"], "B1")
            self.assertEqual(int(row["quantity"]), 1)
            links = conn.execute(
                "SELECT target_type FROM media_links WHERE asset_id = %s",
                (capture_id,),
            ).fetchall()
            self.assertEqual({str(link["target_type"]) for link in links}, {"plant"})
        finally:
            db.return_db(conn)

    def test_move_and_delete_plant_proposals_apply_existing_domain_behavior(self) -> None:
        assigned = self.client.post("/api/plots/B1/plants/PLT-002", json={"quantity": 2})
        self.assertIn(assigned.status_code, {200, 201}, assigned.text)
        move_intent = AssistantIntent(
            intent="plant_move",
            confidence=0.99,
            plant_query="Rosa canina",
            source_plot_query="B1",
            destination_plot_query="B2",
        )
        conn = db.get_db()
        try:
            with patch("gardenops.services.assistant._interpret", return_value=move_intent):
                move = process_text(
                    conn,
                    self._binding(),
                    source_room_id="!garden:example.org",
                    source_event_id="$move-plant",
                    source_sender_id="@owner:example.org",
                    text="Move the rose from B1 to B2",
                    occurred_on="2026-09-02",
                )
                conn.commit()
            self.assertEqual(move.state, "proposal")
            self.assertEqual(move.proposal.kind, "plant_move")
            self.assertIn("Move all", move.proposal.summary)
            moved = apply_request(
                conn,
                self._binding(),
                request_id=move.request_id,
                source_event_id="$save-move",
            )
            conn.commit()
            self.assertEqual(moved.state, "applied")
            assignments = conn.execute(
                "SELECT plot_id, quantity FROM plot_plants WHERE plt_id = 'PLT-002'"
            ).fetchall()
            self.assertEqual(
                [(str(row["plot_id"]), int(row["quantity"])) for row in assignments],
                [("B2", 2)],
            )
        finally:
            db.return_db(conn)

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
                    "X-Matrix-Event-Id": "$delete-plant-photo",
                    "X-Matrix-Sender": "@owner:example.org",
                },
            )
        self.assertEqual(captured.status_code, 201, captured.text)
        delete_capture_id = str(captured.json()["capture_asset_id"])

        conn = db.get_db()
        try:
            delete_intent = AssistantIntent(
                intent="plant_delete",
                confidence=0.99,
                plant_query="Rosa canina",
            )
            with patch("gardenops.services.assistant._interpret", return_value=delete_intent):
                delete = process_text(
                    conn,
                    self._binding(),
                    source_room_id="!garden:example.org",
                    source_event_id="$delete-plant",
                    source_sender_id="@owner:example.org",
                    text="Delete the rose from GardenOps",
                    occurred_on="2026-09-02",
                )
                conn.commit()
            conn.execute(
                "UPDATE assistant_requests SET capture_asset_id = %s WHERE public_id = %s",
                (delete_capture_id, delete.request_id),
            )
            conn.commit()
            self.assertEqual(delete.state, "proposal")
            self.assertEqual(delete.proposal.kind, "plant_delete")
            self.assertIn("cannot be undone", delete.proposal.summary)
            result = apply_request(
                conn,
                self._binding(),
                request_id=delete.request_id,
                source_event_id="$save-delete",
            )
            conn.commit()
            self.assertEqual(result.state, "applied")
            self.assertTrue(result.message.startswith("Deleted:"))
            self.assertIn("rose", result.message.casefold())
            self.assertEqual(self._apply(delete.request_id).message, result.message)
            self.assertIsNone(
                conn.execute("SELECT 1 FROM plants WHERE plt_id = 'PLT-002'").fetchone()
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM media_assets WHERE asset_id = %s", (delete_capture_id,)
                ).fetchone()
            )
        finally:
            db.return_db(conn)

    def test_partial_receipt_uses_confirmed_links_and_survives_renaming(self) -> None:
        created = self.client.post(
            "/api/tasks",
            json={
                "task_type": "prune",
                "title": "Prune selected plants",
                "due_on": "2026-09-02",
                "plant_ids": ["PLT-TEST", "PLT-002"],
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        conn = db.get_db()
        try:
            task = conn.execute(
                "SELECT updated_at_ms FROM garden_tasks WHERE public_id = %s",
                (created.json()["id"],),
            ).fetchone()
            names = {
                str(row["plt_id"]): str(row["name"])
                for row in conn.execute(
                    "SELECT plt_id, name FROM plants WHERE plt_id IN ('PLT-TEST', 'PLT-002')"
                ).fetchall()
            }
        finally:
            db.return_db(conn)
        request_id = self._proposal(
            "task_completion",
            {
                "task_id": created.json()["id"],
                "expected_updated_at_ms": int(task["updated_at_ms"]),
                "completed_plant_ids": ["PLT-002"],
                "completion_outcome": "done",
                "occurred_on": "2026-09-02",
            },
        )
        with patch(
            "gardenops.services.assistant._interpret", side_effect=AssertionError("AI called")
        ):
            result = self._apply(request_id)
            self.assertIn(names["PLT-002"], result.message)
            self.assertNotIn(names["PLT-TEST"], result.message)
            self.assertIn("2026-09-02", result.message)
            conn = db.get_db()
            try:
                conn.execute("UPDATE plants SET name = 'Renamed' WHERE plt_id = 'PLT-002'")
                conn.commit()
            finally:
                db.return_db(conn)
            self.assertEqual(self._apply(request_id).message, result.message)

    def test_bloom_receipt_records_only_explicit_placement(self) -> None:
        for plot_id in ("B1", "B2"):
            assigned = self.client.post(
                f"/api/plots/{plot_id}/plants/PLT-002", json={"quantity": 1}
            )
            self.assertIn(assigned.status_code, {200, 201}, assigned.text)
        created = self.client.post(
            "/api/tasks",
            json={
                "task_type": "observe_bloom",
                "title": "Observe rose",
                "due_on": "2026-09-02",
                "plant_ids": ["PLT-002"],
                "plot_ids": ["B1", "B2"],
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        conn = db.get_db()
        try:
            task = conn.execute(
                "SELECT updated_at_ms FROM garden_tasks WHERE public_id = %s",
                (created.json()["id"],),
            ).fetchone()
        finally:
            db.return_db(conn)
        request_id = self._proposal(
            "task_completion",
            {
                "task_id": created.json()["id"],
                "expected_updated_at_ms": int(task["updated_at_ms"]),
                "completed_plant_ids": ["PLT-002"],
                "completion_outcome": "done",
                "occurred_on": "2026-09-02",
                "observed_plot_ids": ["B1"],
            },
        )
        result = self._apply(request_id)
        self.assertIn("B1", result.message)
        self.assertNotIn("B2", result.message)
        self.assertIn("2026-09-02", result.message)
        conn = db.get_db()
        try:
            journal_id = next(
                record.id for record in result.records if record.type == "journal_entry"
            )
            plots = conn.execute(
                "SELECT p.plot_id FROM garden_journal_entry_plots p "
                "JOIN garden_journal_entries e ON e.id = p.entry_id WHERE e.public_id = %s",
                (journal_id,),
            ).fetchall()
            self.assertEqual([str(row["plot_id"]) for row in plots], ["B1"])
        finally:
            db.return_db(conn)

    def test_negative_bloom_requires_explicit_closure_and_discloses_scope(self) -> None:
        created = self.client.post(
            "/api/tasks",
            json={
                "task_type": "observe_bloom",
                "title": "Observe rose bloom",
                "due_on": "2026-09-02",
                "plant_ids": ["PLT-002"],
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        intent = AssistantIntent(
            intent="task_completion", confidence=0.99, plant_query="Rosa canina", task_query="bloom"
        )
        conn = db.get_db()
        try:
            with patch("gardenops.services.assistant._interpret", return_value=intent):
                for index, text in enumerate(
                    (
                        "The rose has not bloomed yet",
                        "The rose isn't blooming yet",
                        "The rose is not in bloom",
                        "No bloom on the rose",
                        "Do not close the bloom season for the rose",
                        "Close the rose bloom season? Not yet",
                    )
                ):
                    result = process_text(
                        conn,
                        self._binding(),
                        source_room_id="!garden:example.org",
                        source_event_id=f"$negative-{index}",
                        source_sender_id="@owner:example.org",
                        text=text,
                        occurred_on="2026-09-02",
                    )
                    self.assertEqual(result.state, "needs_input")
                proposal = process_text(
                    conn,
                    self._binding(),
                    source_room_id="!garden:example.org",
                    source_event_id="$explicit-season",
                    source_sender_id="@owner:example.org",
                    text="Close the bloom season for the rose",
                    occurred_on="2025-09-02",
                )
                self.assertEqual(proposal.state, "proposal")
                self.assertEqual(proposal.proposal.fields["observed_plot_ids"], [])
                self.assertIn("2025-09-02", proposal.message)
                self.assertIn("no new bloom checks", proposal.message)
                self.assertIn("plant only", proposal.message)
                saved = apply_request(
                    conn,
                    self._binding(),
                    request_id=proposal.request_id,
                    source_event_id="$save-explicit-season",
                )
                self.assertIn("not seen blooming in 2025", saved.message)
                self.assertIn("plant only", saved.message)
        finally:
            conn.rollback()
            db.return_db(conn)

    def test_bloom_intent_uses_latest_edit_and_requires_observation(self) -> None:
        created = self.client.post(
            "/api/tasks",
            json={
                "task_type": "observe_bloom",
                "title": "Observe rose bloom",
                "due_on": "2026-09-02",
                "plant_ids": ["PLT-002"],
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        intent = AssistantIntent(
            intent="task_completion", confidence=0.99, plant_query="Rosa canina", task_query="bloom"
        )
        cases = (
            ("The rose isn't blooming yet", None, "needs_input"),
            ("The rose isn\u2019t blooming yet", None, "needs_input"),
            ("The rose is not in bloom", None, "needs_input"),
            ("Complete the rose check", None, "needs_input"),
            ("Complete the blooming task", None, "needs_input"),
            ("The rose will be blooming next week", None, "needs_input"),
            ("Is the rose blooming?", None, "needs_input"),
            ("Defer it for a week", None, "answer"),
            ("The rose isn't blooming yet", "Defer it for a week", "answer"),
            ("The rose isn't blooming yet", "It is still not in bloom", "needs_input"),
            ("The rose is blooming", "The rose isn't blooming yet", "needs_input"),
            ("The rose is blooming", "Defer it for a week", "answer"),
            ("The rose isn't blooming yet", "Actually, the rose is blooming", "proposal"),
            ("The rose isn't blooming yet", "I saw it in bloom today", "proposal"),
            ("The rose has bloomed", None, "proposal"),
            ("The rose isn't blooming yet", "Close the bloom season for the rose", "proposal"),
        )
        conn = db.get_db()
        try:
            for index, (initial, clarification, expected_state) in enumerate(cases):
                with self.subTest(initial=initial, clarification=clarification):
                    before_task = dict(
                        conn.execute(
                            "SELECT * FROM garden_tasks WHERE public_id = %s",
                            (created.json()["id"],),
                        ).fetchone()
                    )
                    before_journal = conn.execute(
                        "SELECT count(*) AS n FROM garden_journal_entries"
                    ).fetchone()["n"]
                    with patch(
                        "gardenops.services.assistant._interpret", return_value=intent
                    ) as interpret:
                        result = process_text(
                            conn,
                            self._binding(),
                            source_room_id="!garden:example.org",
                            source_event_id=f"$bloom-intent-{index}",
                            source_sender_id="@owner:example.org",
                            text=initial,
                            occurred_on="2026-09-02",
                        )
                        if clarification:
                            result = continue_request(
                                conn,
                                self._binding(),
                                request_id=result.request_id,
                                source_event_id=f"$bloom-edit-{index}",
                                text=clarification,
                            )
                        self.assertEqual(interpret.call_count, 2 if clarification else 1)
                    self.assertEqual(result.state, expected_state)
                    self.assertEqual(
                        dict(
                            conn.execute(
                                "SELECT * FROM garden_tasks WHERE public_id = %s",
                                (created.json()["id"],),
                            ).fetchone()
                        ),
                        before_task,
                    )
                    self.assertEqual(
                        conn.execute("SELECT count(*) AS n FROM garden_journal_entries").fetchone()[
                            "n"
                        ],
                        before_journal,
                    )
                    if expected_state != "proposal":
                        self.assertFalse(result.proposal)
                        self.assertFalse(result.records)
                        if expected_state == "answer":
                            self.assertIn("due date has not changed", result.message)
                            self.assertIn("Tasks view", result.message)
                        with self.assertRaises(HTTPException):
                            apply_request(
                                conn,
                                self._binding(),
                                request_id=result.request_id,
                                source_event_id=f"$invalid-save-{index}",
                            )
                    else:
                        closure = bool(clarification and clarification.startswith("Close"))
                        self.assertEqual(
                            result.proposal.fields["completion_outcome"],
                            "not_seen_blooming_this_season" if closure else "done",
                        )
                        if closure:
                            self.assertIn("no new bloom checks", result.message)
                            self.assertIn("Existing checks are not all cancelled", result.message)
                        saved = apply_request(
                            conn,
                            self._binding(),
                            request_id=result.request_id,
                            source_event_id=f"$bloom-save-{index}",
                        )
                        self.assertEqual(saved.state, "applied")
                        self.assertIn(
                            "not seen blooming in 2026" if closure else "bloomed", saved.message
                        )
                        self.assertIn("2026-09-02", saved.message)
                        self.assertIn("plant only", saved.message)
                    conn.rollback()
        finally:
            conn.rollback()
            db.return_db(conn)

    def test_plant_assignment_uses_exact_quantity_and_write_ownership_rules(self) -> None:
        conn = db.get_db()
        try:
            assign_plant_command(
                conn,
                self._binding().context,
                plant_id="PLT-002",
                plot_id="B1",
                quantity=5,
            )
            assign_plant_command(
                conn,
                self._binding().context,
                plant_id="PLT-002",
                plot_id="B1",
                quantity=2,
            )
            quantity = conn.execute(
                "SELECT quantity FROM plot_plants WHERE plot_id = 'B1' AND plt_id = 'PLT-002'"
            ).fetchone()
            self.assertEqual(int(quantity["quantity"]), 2)

            peer_context = AuthContext(
                user_id=self._owner_id + 1000,
                username="peer_editor",
                role="editor",
                auth_type="session",
                garden_id=self._get_default_garden_id(),
                garden_role="editor",
                subscription_tier="pro",
            )
            with self.assertRaises(HTTPException) as peer_error:
                assign_plant_command(
                    conn,
                    peer_context,
                    plant_id="PLT-002",
                    plot_id="B1",
                    quantity=1,
                )
            self.assertEqual(peer_error.exception.status_code, 404)

            conn.execute(
                "UPDATE plots SET archived_at_ms = %s WHERE plot_id = 'B2'",
                (current_timestamp_ms(),),
            )
            with self.assertRaises(HTTPException) as archived_error:
                assign_plant_command(
                    conn,
                    self._binding().context,
                    plant_id="PLT-002",
                    plot_id="B2",
                    quantity=1,
                )
            self.assertEqual(archived_error.exception.status_code, 410)
        finally:
            conn.rollback()
            db.return_db(conn)

    def test_editing_new_plant_identity_refreshes_cached_enrichment(self) -> None:
        first_intent = AssistantIntent(
            intent="plant_create",
            confidence=0.98,
            plant_query="Lilium old",
            destination_plot_query="B1",
            quantity=1,
        )
        corrected_intent = first_intent.model_copy(update={"plant_query": "Tulipa corrected"})
        conn = db.get_db()
        try:
            with (
                patch(
                    "gardenops.services.assistant._interpret",
                    side_effect=[first_intent, corrected_intent],
                ),
                patch(
                    "gardenops.services.assistant._enrich_new_plant",
                    side_effect=[
                        {"name": "Old lily", "latin": "Lilium old"},
                        {"name": "Corrected tulip", "latin": "Tulipa corrected"},
                    ],
                ) as enrich,
            ):
                proposal = process_text(
                    conn,
                    self._binding(),
                    source_room_id="!garden:example.org",
                    source_event_id="$new-plant-before-edit",
                    source_sender_id="@owner:example.org",
                    text="Add Lilium old to B1, quantity 1",
                    occurred_on="2026-09-02",
                )
                edited = continue_request(
                    conn,
                    self._binding(),
                    request_id=proposal.request_id,
                    source_event_id="$new-plant-identity-edit",
                    text="Actually, it is Tulipa corrected",
                )
            self.assertEqual(edited.state, "proposal")
            self.assertEqual(edited.proposal.fields["latin"], "Tulipa corrected")
            self.assertEqual(enrich.call_count, 2)
        finally:
            conn.rollback()
            db.return_db(conn)
