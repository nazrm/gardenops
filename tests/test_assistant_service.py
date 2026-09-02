from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

from fastapi import HTTPException

from gardenops.security import AuthContext
from gardenops.services.assistant import (
    apply_request,
    cancel_request,
    expire_and_cleanup_requests,
    get_request,
    process_text,
)
from gardenops.services.assistant_models import AssistantIntent
from gardenops.services.assistant_resolution import resolve_garden_target
from gardenops.services.integration_config import AssistantBinding
from tests.base import DbTestBase


class TestAssistantService(DbTestBase):
    def _binding(self) -> AssistantBinding:
        context = AuthContext(
            user_id=self._owner_id,
            username="dbtest_admin",
            role="admin",
            auth_type="session",
            garden_id=self.garden_id,
            garden_role="admin",
            subscription_tier="pro",
        )
        return AssistantBinding(
            room_id="!garden:example.org",
            sender_id="@owner:example.org",
            username="dbtest_admin",
            garden_slug="default",
            user_id=self._owner_id,
            garden_id=self.garden_id,
            context=context,
        )

    def test_proposal_is_non_mutating_and_source_event_is_idempotent(self) -> None:
        self._insert_plant("PLT-ROSE", "Dog rose", "Rosa canina")
        intent = AssistantIntent(
            intent="journal",
            confidence=0.95,
            plant_query="Rosa canina",
            occurred_on="2026-09-02",
            event_type="bloomed",
            notes="First flower",
        )
        with patch("gardenops.services.assistant._interpret", return_value=intent):
            first = process_text(
                self.conn,
                self._binding(),
                source_room_id="!garden:example.org",
                source_event_id="$event-1",
                source_sender_id="@owner:example.org",
                text="The dog rose bloomed",
                occurred_on="2026-09-02",
            )
            repeated = process_text(
                self.conn,
                self._binding(),
                source_room_id="!garden:example.org",
                source_event_id="$event-1",
                source_sender_id="@owner:example.org",
                text="The dog rose bloomed",
                occurred_on="2026-09-02",
            )
        self.assertEqual(first.state, "proposal")
        self.assertEqual(repeated.request_id, first.request_id)
        by_reference = get_request(self.conn, self._binding(), request_id=first.reference)
        self.assertEqual(by_reference.request_id, first.request_id)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM garden_journal_entries").fetchone())

    def test_resolution_precedence_and_unambiguous_sentence_match(self) -> None:
        self._insert_plant("PLT-ROSE", "Dog rose", "Rosa canina")
        self._insert_plant("PLT-OTHER", "Rose", "Rosa rugosa")
        exact = resolve_garden_target(self.conn, self._binding().context, plant_query="Rosa canina")
        sentence = resolve_garden_target(
            self.conn,
            self._binding().context,
            plant_query="I observed Rosa canina flowering today",
        )
        ambiguous = resolve_garden_target(self.conn, self._binding().context, plant_query="Rosa")
        self.assertEqual(exact.plant_id, "PLT-ROSE")
        self.assertEqual(sentence.plant_id, "PLT-ROSE")
        self.assertEqual(ambiguous.status, "ambiguous_plant")

    def test_resolution_does_not_expose_a_plot_from_another_garden(self) -> None:
        self._insert_plant("PLT-SHARED", "Shared rose", "Rosa canina")
        other_garden_id = int(
            self.conn.execute(
                "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                ("other", "Other garden"),
            ).fetchone()["id"]
        )
        self.conn.execute(
            """
            INSERT INTO plots (
                plot_id, zone_code, zone_name, plot_number, grid_row, grid_col,
                sub_zone, notes, color, garden_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            ("FOREIGN", "F", "Foreign", 1, 1, 1, "", "", None, other_garden_id),
        )
        self.conn.execute(
            "INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id) VALUES (%s, %s, %s)",
            ("FOREIGN", self._owner_id, other_garden_id),
        )
        self.conn.execute(
            "INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id) VALUES (%s, %s, %s)",
            ("PLT-SHARED", self._owner_id, other_garden_id),
        )
        self.conn.execute(
            "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES (%s, %s, %s)",
            ("FOREIGN", "PLT-SHARED", 1),
        )

        resolved = resolve_garden_target(
            self.conn,
            self._binding().context,
            plant_query="Rosa canina",
        )

        self.assertEqual(resolved.status, "resolved")
        self.assertEqual(resolved.plant_id, "PLT-SHARED")
        self.assertEqual(resolved.plot_id, "")

    def test_expiry_state_overrides_stored_proposal(self) -> None:
        self._insert_plant("PLT-ROSE", "Dog rose", "Rosa canina")
        intent = AssistantIntent(
            intent="journal",
            confidence=1,
            plant_query="Rosa canina",
            event_type="observed",
        )
        with patch("gardenops.services.assistant._interpret", return_value=intent):
            result = process_text(
                self.conn,
                self._binding(),
                source_room_id="!garden:example.org",
                source_event_id="$expired",
                source_sender_id="@owner:example.org",
                text="Observed the rose",
                occurred_on="2026-09-02",
            )
        self.conn.execute(
            "UPDATE assistant_requests SET expires_at_ms = 1 WHERE public_id = %s",
            (result.request_id,),
        )
        expire_and_cleanup_requests(self.conn, now_ms=2)
        expired = get_request(self.conn, self._binding(), request_id=result.request_id)
        self.assertEqual(expired.state, "error")
        self.assertIn("expired", expired.message)

    def test_cancel_is_idempotent_and_prevents_apply(self) -> None:
        self._insert_plant("PLT-ROSE", "Dog rose", "Rosa canina")
        intent = AssistantIntent(
            intent="journal",
            confidence=1,
            plant_query="Rosa canina",
            event_type="observed",
        )
        with patch("gardenops.services.assistant._interpret", return_value=intent):
            proposal = process_text(
                self.conn,
                self._binding(),
                source_room_id="!garden:example.org",
                source_event_id="$cancelled",
                source_sender_id="@owner:example.org",
                text="Observed the rose",
                occurred_on="2026-09-02",
            )
        first = cancel_request(
            self.conn,
            self._binding(),
            request_id=proposal.request_id,
            source_event_id="$cancel-1",
        )
        repeated = cancel_request(
            self.conn,
            self._binding(),
            request_id=proposal.request_id,
            source_event_id="$cancel-2",
        )
        self.assertEqual(first.state, "cancelled")
        self.assertEqual(repeated, first)
        with self.assertRaises(HTTPException) as raised:
            apply_request(
                self.conn,
                self._binding(),
                request_id=proposal.request_id,
                source_event_id="$save-after-cancel",
            )
        self.assertEqual(raised.exception.status_code, 409)

    def test_apply_rejects_a_request_from_another_room(self) -> None:
        self._insert_plant("PLT-ROSE", "Dog rose", "Rosa canina")
        intent = AssistantIntent(
            intent="journal",
            confidence=1,
            plant_query="Rosa canina",
            event_type="observed",
        )
        with patch("gardenops.services.assistant._interpret", return_value=intent):
            proposal = process_text(
                self.conn,
                self._binding(),
                source_room_id="!garden:example.org",
                source_event_id="$cross-room",
                source_sender_id="@owner:example.org",
                text="Observed the rose",
                occurred_on="2026-09-02",
            )
        wrong_room = replace(self._binding(), room_id="!other:example.org")
        with self.assertRaises(HTTPException) as raised:
            apply_request(
                self.conn,
                wrong_room,
                request_id=proposal.request_id,
                source_event_id="$cross-room-save",
            )
        self.assertEqual(raised.exception.status_code, 403)
