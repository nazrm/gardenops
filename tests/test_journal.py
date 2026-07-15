import os

import gardenops.db as db
from gardenops.security import create_user
from tests.base import BaseApiTest, strong_password


class TestJournal(BaseApiTest):
    def _ensure_assignment(self, plant_id: str, plot_id: str) -> None:
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO plot_plants (plot_id, plt_id, quantity)
                VALUES (%s, %s, 1)
                ON CONFLICT (plot_id, plt_id) DO NOTHING
                """,
                (plot_id, plant_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

    def _seen_state(self, plant_id: str, plot_id: str) -> tuple[dict, dict]:
        conn = db.get_db()
        try:
            plant = conn.execute(
                "SELECT seen_growing, seen_growing_date FROM plants WHERE plt_id = %s",
                (plant_id,),
            ).fetchone()
            assignment = conn.execute(
                """
                SELECT seen_growing, seen_growing_date
                FROM plot_plants
                WHERE plt_id = %s AND plot_id = %s
                """,
                (plant_id, plot_id),
            ).fetchone()
            assert plant is not None and assignment is not None
            return dict(plant), dict(assignment)
        finally:
            db.return_db(conn)

    def test_journal_crud_lifecycle(self) -> None:
        """Create, read, update, and delete a journal entry."""
        r = self.client.post(
            "/api/journal",
            json={
                "event_type": "planted",
                "occurred_on": "2026-03-10",
                "title": "Spring bulbs",
                "notes": "Planted tulips in zone B",
                "plant_ids": ["PLT-TEST"],
                "plot_ids": ["B1"],
            },
        )
        self.assertEqual(r.status_code, 201, r.text)
        entry_id = r.json()["id"]

        r = self.client.get(f"/api/journal/{entry_id}")
        self.assertEqual(r.status_code, 200)
        entry = r.json()
        self.assertEqual(entry["event_type"], "planted")
        self.assertEqual(entry["occurred_on"], "2026-03-10")
        self.assertEqual(entry["title"], "Spring bulbs")
        self.assertEqual(entry["plant_ids"], ["PLT-TEST"])
        self.assertEqual(entry["plot_ids"], ["B1"])

        r = self.client.patch(
            f"/api/journal/{entry_id}",
            json={
                "title": "Updated title",
                "notes": "Updated notes",
                "plot_ids": ["B1", "B2"],
            },
        )
        self.assertEqual(r.status_code, 200)

        r = self.client.get(f"/api/journal/{entry_id}")
        entry = r.json()
        self.assertEqual(entry["title"], "Updated title")
        self.assertEqual(entry["notes"], "Updated notes")
        self.assertCountEqual(entry["plot_ids"], ["B1", "B2"])
        self.assertEqual(entry["plant_ids"], ["PLT-TEST"])

        r = self.client.delete(f"/api/journal/{entry_id}")
        self.assertEqual(r.status_code, 200)

        r = self.client.get(f"/api/journal/{entry_id}")
        self.assertEqual(r.status_code, 404)

    def test_journal_list_filters(self) -> None:
        """Verify filtering by event_type, plant_id, plot_id, and date range."""
        self.client.post(
            "/api/journal",
            json={
                "event_type": "planted",
                "occurred_on": "2026-03-01",
                "plant_ids": ["PLT-TEST"],
                "plot_ids": ["B1"],
            },
        )
        self.client.post(
            "/api/journal",
            json={
                "event_type": "pruned",
                "occurred_on": "2026-03-05",
                "plant_ids": ["PLT-002"],
                "plot_ids": ["B2"],
            },
        )
        self.client.post(
            "/api/journal",
            json={
                "event_type": "watered",
                "occurred_on": "2026-03-10",
                "plant_ids": ["PLT-TEST", "PLT-002"],
            },
        )

        r = self.client.get("/api/journal")
        self.assertEqual(r.json()["total"], 3)

        r = self.client.get("/api/journal?event_type=planted")
        self.assertEqual(r.json()["total"], 1)
        self.assertEqual(r.json()["entries"][0]["event_type"], "planted")

        r = self.client.get("/api/journal?event_type=planted,pruned")
        self.assertEqual(r.json()["total"], 2)

        r = self.client.get("/api/journal?plant_id=PLT-TEST")
        self.assertEqual(r.json()["total"], 2)

        r = self.client.get("/api/journal?plot_id=B2")
        self.assertEqual(r.json()["total"], 1)

        r = self.client.get("/api/journal?date_from=2026-03-05")
        self.assertEqual(r.json()["total"], 2)

        r = self.client.get("/api/journal?date_from=2026-03-01&date_to=2026-03-05")
        self.assertEqual(r.json()["total"], 2)

    def test_journal_batch_entry_persists_plot_links(self) -> None:
        r = self.client.post(
            "/api/plants/batch-journal-entry",
            json={
                "plt_ids": ["PLT-TEST", "PLT-002"],
                "event_type": "watered",
                "occurred_on": "2026-03-11",
                "title": "Watered bed edge",
                "plot_ids": ["B1", "B2"],
            },
        )
        self.assertEqual(r.status_code, 201, r.text)
        entry_id = r.json()["id"]

        r = self.client.get(f"/api/journal/{entry_id}")
        self.assertEqual(r.status_code, 200)
        entry = r.json()
        self.assertCountEqual(entry["plant_ids"], ["PLT-TEST", "PLT-002"])
        self.assertCountEqual(entry["plot_ids"], ["B1", "B2"])

    def test_bloom_edit_date_reconciles_plant_and_assignment(self) -> None:
        self._ensure_assignment("PLT-TEST", "B1")
        created = self.client.post(
            "/api/journal",
            json={
                "event_type": "bloomed",
                "occurred_on": "2026-06-20",
                "plant_ids": ["PLT-TEST"],
                "plot_ids": ["B1"],
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        updated = self.client.patch(
            f"/api/journal/{created.json()['id']}",
            json={"occurred_on": "2026-05-15"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        plant, assignment = self._seen_state("PLT-TEST", "B1")
        self.assertEqual(str(plant["seen_growing_date"]), "2026-05-15")
        self.assertEqual(str(assignment["seen_growing_date"]), "2026-05-15")

    def test_bloom_edit_links_clears_old_and_marks_new_assignment(self) -> None:
        self._ensure_assignment("PLT-TEST", "B1")
        self._ensure_assignment("PLT-002", "B2")
        created = self.client.post(
            "/api/journal",
            json={
                "event_type": "bloomed",
                "occurred_on": "2026-06-21",
                "plant_ids": ["PLT-TEST"],
                "plot_ids": ["B1"],
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        updated = self.client.patch(
            f"/api/journal/{created.json()['id']}",
            json={"plant_ids": ["PLT-002"], "plot_ids": ["B2"]},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        old_plant, old_assignment = self._seen_state("PLT-TEST", "B1")
        new_plant, new_assignment = self._seen_state("PLT-002", "B2")
        self.assertIsNone(old_plant["seen_growing_date"])
        self.assertIsNone(old_assignment["seen_growing_date"])
        self.assertEqual(str(new_plant["seen_growing_date"]), "2026-06-21")
        self.assertEqual(str(new_assignment["seen_growing_date"]), "2026-06-21")

    def test_bloom_edit_type_removes_stale_derived_state(self) -> None:
        self._ensure_assignment("PLT-TEST", "B1")
        created = self.client.post(
            "/api/journal",
            json={
                "event_type": "bloomed",
                "occurred_on": "2026-06-22",
                "plant_ids": ["PLT-TEST"],
                "plot_ids": ["B1"],
            },
        )
        updated = self.client.patch(
            f"/api/journal/{created.json()['id']}",
            json={"event_type": "observed"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        plant, assignment = self._seen_state("PLT-TEST", "B1")
        self.assertIsNone(plant["seen_growing"])
        self.assertIsNone(plant["seen_growing_date"])
        self.assertIsNone(assignment["seen_growing"])
        self.assertIsNone(assignment["seen_growing_date"])

    def test_bloom_delete_uses_latest_remaining_and_preserves_newer_manual_state(self) -> None:
        self._ensure_assignment("PLT-TEST", "B1")
        older = self.client.post(
            "/api/journal",
            json={
                "event_type": "bloomed",
                "occurred_on": "2026-05-01",
                "plant_ids": ["PLT-TEST"],
                "plot_ids": ["B1"],
            },
        )
        newer = self.client.post(
            "/api/journal",
            json={
                "event_type": "bloomed",
                "occurred_on": "2026-06-01",
                "plant_ids": ["PLT-TEST"],
                "plot_ids": ["B1"],
            },
        )
        deleted = self.client.delete(f"/api/journal/{newer.json()['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        plant, assignment = self._seen_state("PLT-TEST", "B1")
        self.assertEqual(str(plant["seen_growing_date"]), "2026-05-01")
        self.assertEqual(str(assignment["seen_growing_date"]), "2026-05-01")

        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE plants SET seen_growing = 1, seen_growing_date = '2026-07-01' "
                "WHERE plt_id = 'PLT-TEST'"
            )
            conn.execute(
                "UPDATE plot_plants SET seen_growing = 1, seen_growing_date = '2026-07-01' "
                "WHERE plt_id = 'PLT-TEST' AND plot_id = 'B1'"
            )
            conn.commit()
        finally:
            db.return_db(conn)
        deleted = self.client.delete(f"/api/journal/{older.json()['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        plant, assignment = self._seen_state("PLT-TEST", "B1")
        self.assertEqual(str(plant["seen_growing_date"]), "2026-07-01")
        self.assertEqual(str(assignment["seen_growing_date"]), "2026-07-01")

    def test_bloom_observation_does_not_update_shared_global_plant(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        os.environ["AUTH_API_KEY"] = ""
        try:
            gid1, gid2, username, password = self._setup_admin_two_gardens()
            conn = db.get_db()
            try:
                conn.execute("DELETE FROM plant_ownership")
                db.executemany(
                    conn,
                    """
                    INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT(plt_id, garden_id) DO UPDATE SET
                        owner_user_id = excluded.owner_user_id
                    """,
                    [
                        ("PLT-TEST", self._owner_id, gid1),
                        ("PLT-TEST", self._owner_id, gid2),
                    ],
                )
                conn.execute(
                    """
                    UPDATE plants
                    SET seen_growing = NULL, seen_growing_date = NULL
                    WHERE plt_id = %s
                    """,
                    ("PLT-TEST",),
                )
                conn.execute(
                    """
                    UPDATE plot_plants
                    SET seen_growing = NULL, seen_growing_date = NULL
                    WHERE plot_id = %s AND plt_id = %s
                    """,
                    ("B1", "PLT-TEST"),
                )
                conn.execute(
                    """
                    INSERT INTO plot_plants (
                        plot_id, plt_id, quantity, seen_growing, seen_growing_date
                    )
                    VALUES (%s, %s, 1, NULL, NULL)
                    ON CONFLICT(plot_id, plt_id) DO UPDATE SET
                        seen_growing = NULL,
                        seen_growing_date = NULL
                    """,
                    ("B1", "PLT-TEST"),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            client = self._new_client()
            _, csrf = self._login_session(username, password, client=client)
            headers = self._session_headers(csrf, garden_id=gid1)
            response = client.post(
                "/api/journal",
                headers=headers,
                json={
                    "event_type": "bloomed",
                    "occurred_on": "2026-06-15",
                    "plant_ids": ["PLT-TEST"],
                    "plot_ids": ["B1"],
                },
            )
            self.assertEqual(response.status_code, 201, response.text)

            conn = db.get_db()
            try:
                plant_row = conn.execute(
                    "SELECT seen_growing, seen_growing_date FROM plants WHERE plt_id = %s",
                    ("PLT-TEST",),
                ).fetchone()
                assignment_row = conn.execute(
                    """
                    SELECT seen_growing, seen_growing_date
                    FROM plot_plants
                    WHERE plot_id = %s AND plt_id = %s
                    """,
                    ("B1", "PLT-TEST"),
                ).fetchone()
            finally:
                db.return_db(conn)

            self.assertIsNone(plant_row["seen_growing"])
            self.assertIsNone(plant_row["seen_growing_date"])
            self.assertEqual(int(assignment_row["seen_growing"]), 1)
            self.assertEqual(str(assignment_row["seen_growing_date"]), "2026-06-15")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_bloom_observation_rejects_peer_owned_plant_side_effect(self) -> None:
        owner = self._create_test_user("bloom_peer_owner", "ownerpass", "editor")
        self._create_test_user("bloom_peer_editor", "editorpass", "editor")
        garden_id = self._get_default_garden_id()
        conn = db.get_db()
        try:
            conn.execute(
                """
                UPDATE plant_ownership
                SET owner_user_id = %s
                WHERE plt_id = 'PLT-TEST' AND garden_id = %s
                """,
                (int(owner["id"]), garden_id),
            )
            conn.execute(
                """
                UPDATE plot_ownership
                SET owner_user_id = %s
                WHERE plot_id = 'B1' AND garden_id = %s
                """,
                (int(owner["id"]), garden_id),
            )
            conn.execute(
                """
                UPDATE plants
                SET seen_growing = NULL, seen_growing_date = NULL
                WHERE plt_id = 'PLT-TEST'
                """
            )
            conn.execute(
                """
                INSERT INTO plot_plants (plot_id, plt_id, quantity)
                VALUES ('B1', 'PLT-TEST', 1)
                ON CONFLICT (plot_id, plt_id) DO UPDATE SET
                    seen_growing = NULL,
                    seen_growing_date = NULL
                """
            )
            conn.commit()
        finally:
            db.return_db(conn)

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        os.environ["AUTH_API_KEY"] = ""
        try:
            client = self._new_client()
            _, csrf = self._login_session("bloom_peer_editor", "editorpass", client=client)
            response = client.post(
                "/api/journal",
                headers=self._session_headers(csrf, garden_id=garden_id),
                json={
                    "event_type": "bloomed",
                    "occurred_on": "2026-06-19",
                    "plant_ids": ["PLT-TEST"],
                    "plot_ids": ["B1"],
                },
            )
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

        self.assertEqual(response.status_code, 404, response.text)
        conn = db.get_db()
        try:
            plant_row = conn.execute(
                "SELECT seen_growing, seen_growing_date FROM plants WHERE plt_id = 'PLT-TEST'",
            ).fetchone()
            assignment_row = conn.execute(
                """
                SELECT seen_growing, seen_growing_date
                FROM plot_plants
                WHERE plot_id = 'B1' AND plt_id = 'PLT-TEST'
                """,
            ).fetchone()
        finally:
            db.return_db(conn)

        assert plant_row is not None
        assert assignment_row is not None
        self.assertIsNone(plant_row["seen_growing"])
        self.assertIsNone(plant_row["seen_growing_date"])
        self.assertIsNone(assignment_row["seen_growing"])
        self.assertIsNone(assignment_row["seen_growing_date"])

    def test_journal_batch_entry_rejects_invalid_date(self) -> None:
        r = self.client.post(
            "/api/plants/batch-journal-entry",
            json={
                "plt_ids": ["PLT-TEST"],
                "event_type": "watered",
                "occurred_on": "2026-02-31",
                "plot_ids": ["B1"],
            },
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("Invalid date", r.json()["detail"])

    def test_journal_rejects_unknown_plot_links(self) -> None:
        r = self.client.post(
            "/api/journal",
            json={
                "event_type": "observed",
                "occurred_on": "2026-03-10",
                "plot_ids": ["NOPE-404"],
            },
        )
        self.assertEqual(r.status_code, 404)
        self.assertIn("Plots not found in active garden", r.json()["detail"])

    def test_journal_list_supports_text_and_actor_filters(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            conn = db.get_db()
            user_a = create_user(
                conn,
                username="journal_actor_a",
                password=strong_password("actorpass"),
                role="editor",
            )
            user_b = create_user(
                conn,
                username="journal_actor_b",
                password=strong_password("actorpass"),
                role="editor",
            )
            garden_row = conn.execute(
                "INSERT INTO gardens (slug, name, onboarding_complete) "
                "VALUES ('journal-test', 'Journal Test', 1) "
                "RETURNING id",
            ).fetchone()
            assert garden_row is not None
            garden_id = int(garden_row["id"])
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'editor') ON CONFLICT DO NOTHING
                """,
                (garden_id, int(user_a["id"])),
            )
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'editor') ON CONFLICT DO NOTHING
                """,
                (garden_id, int(user_b["id"])),
            )
            # Create plots and plants needed for journal entries
            conn.execute(
                "INSERT INTO plots (plot_id, zone_code, zone_name, plot_number, grid_row, grid_col)"
                " VALUES ('B1', 'B', 'Bed', 1, 1, 1), ('B2', 'B', 'Bed', 2, 1, 2)"
                " ON CONFLICT(plot_id) DO NOTHING",
            )
            conn.execute(
                "INSERT INTO plants (plt_id, name, latin, category, bloom_month, color,"
                " hardiness, height_cm, light, link, year_planted, deer_resistant)"
                " VALUES ('PLT-TEST', 'Test', '', 'Stauder', '', '', '', NULL, '', '', '', 0),"
                " ('PLT-002', 'Test2', '', 'Stauder', '', '', '', NULL, '', '', '', 0)"
                " ON CONFLICT(plt_id) DO NOTHING",
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES ('B1', %s, %s), ('B2', %s, %s)
                ON CONFLICT(plot_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id,
                    garden_id = excluded.garden_id
                """,
                (int(user_a["id"]), garden_id, int(user_a["id"]), garden_id),
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES ('PLT-TEST', %s, %s), ('PLT-002', %s, %s)
                ON CONFLICT(plt_id, garden_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id
                """,
                (int(user_a["id"]), garden_id, int(user_a["id"]), garden_id),
            )
            conn.commit()
            db.return_db(conn)

            client_a = self._new_client()
            _, csrf_a = self._login_session("journal_actor_a", "actorpass", client=client_a)
            headers_a = self._session_headers(csrf_a)
            headers_a["x-garden-id"] = str(garden_id)
            r = client_a.post(
                "/api/journal",
                headers=headers_a,
                json={
                    "event_type": "observed",
                    "occurred_on": "2026-03-10",
                    "title": "Mulched B1 edge",
                    "plant_ids": ["PLT-TEST"],
                    "plot_ids": ["B1"],
                },
            )
            self.assertEqual(r.status_code, 201, r.text)

            client_b = self._new_client()
            _, csrf_b = self._login_session("journal_actor_b", "actorpass", client=client_b)
            headers_b = self._session_headers(csrf_b)
            headers_b["x-garden-id"] = str(garden_id)
            r = client_b.post(
                "/api/journal",
                headers=headers_b,
                json={
                    "event_type": "observed",
                    "occurred_on": "2026-03-11",
                    "title": "Checked hedge",
                    "plant_ids": ["PLT-002"],
                    "plot_ids": ["B2"],
                },
            )
            self.assertEqual(r.status_code, 201, r.text)

            r = client_a.get("/api/journal?q=mulch", headers=headers_a)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["total"], 1)
            self.assertEqual(r.json()["entries"][0]["title"], "Mulched B1 edge")

            r = client_a.get("/api/journal?actor=journal_actor_b", headers=headers_a)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["total"], 1)
            self.assertEqual(r.json()["entries"][0]["actor_username"], "journal_actor_b")
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_media_cleanup_runs_when_journal_entry_deleted(self) -> None:
        created = self.client.post(
            "/api/journal",
            json={
                "event_type": "observed",
                "occurred_on": "2026-03-13",
                "title": "Photo day",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        entry_id = created.json()["id"]

        payload = self._image_bytes(fmt="PNG")
        uploaded = self.client.post(
            f"/api/media/upload?target_type=journal_entry&target_id={entry_id}",
            content=payload,
            headers={
                "content-type": "image/png",
                "x-upload-filename": "journal.png",
            },
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        asset_id = uploaded.json()["asset_id"]

        deleted = self.client.delete(f"/api/journal/{entry_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)

        missing = self.client.get(f"/api/media/{asset_id}")
        self.assertEqual(missing.status_code, 404, missing.text)

        conn = db.get_db()
        try:
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM media_assets WHERE asset_id = %s",
                    (asset_id,),
                ).fetchone(),
            )
            self.assertFalse(any(self.test_media_dir.rglob(f"{asset_id}.*")))
        finally:
            db.return_db(conn)

    def test_journal_pagination(self) -> None:
        """Verify limit and offset pagination."""
        for i in range(5):
            self.client.post(
                "/api/journal",
                json={
                    "event_type": "observed",
                    "occurred_on": f"2026-03-{10 + i:02d}",
                },
            )
        r = self.client.get("/api/journal?limit=2&offset=0")
        data = r.json()
        self.assertEqual(data["total"], 5)
        self.assertEqual(len(data["entries"]), 2)

        r = self.client.get("/api/journal?limit=2&offset=4")
        self.assertEqual(len(r.json()["entries"]), 1)

    def test_journal_invalid_event_type_rejected(self) -> None:
        """Reject unknown event types."""
        r = self.client.post(
            "/api/journal",
            json={
                "event_type": "exploded",
                "occurred_on": "2026-03-10",
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_journal_invalid_date_rejected(self) -> None:
        """Reject malformed dates."""
        r = self.client.post(
            "/api/journal",
            json={
                "event_type": "planted",
                "occurred_on": "not-a-date",
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_journal_nonexistent_entry_returns_404(self) -> None:
        """Getting/updating/deleting a nonexistent entry returns 404."""
        self.assertEqual(self.client.get("/api/journal/99999").status_code, 404)
        self.assertEqual(
            self.client.patch("/api/journal/99999", json={"title": "x"}).status_code,
            404,
        )
        self.assertEqual(
            self.client.delete("/api/journal/99999").status_code,
            404,
        )

    def test_journal_entry_with_metadata(self) -> None:
        """Structured metadata is stored and returned."""
        r = self.client.post(
            "/api/journal",
            json={
                "event_type": "harvested",
                "occurred_on": "2026-08-15",
                "metadata": {"weight_kg": 2.5, "variety": "cherry"},
            },
        )
        self.assertEqual(r.status_code, 201)
        entry_id = r.json()["id"]

        r = self.client.get(f"/api/journal/{entry_id}")
        entry = r.json()
        self.assertEqual(entry["metadata"]["weight_kg"], 2.5)
        self.assertEqual(entry["metadata"]["variety"], "cherry")
