import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import call, patch

from fastapi.testclient import TestClient

import gardenops.db as db
from gardenops import audit as audit_service
from gardenops.main import app
from gardenops.router_helpers import generate_public_id
from gardenops.routers import gardens as gardens_router
from gardenops.security import create_user
from tests.base import BaseApiTest, strong_password


class TestGardenDeleteAuditAtomicity(BaseApiTest):
    def _create_deletable_garden(self) -> dict[str, int | str]:
        suffix = os.urandom(4).hex()
        username = f"atomic_delete_admin_{suffix}"
        password = strong_password("atomic-delete-admin-password")
        slug = f"atomic-delete-{suffix}"
        plot_id = f"ATOMIC-{suffix}"
        plant_id = f"ATOMIC-PLANT-{suffix}"
        conn = db.get_db()
        try:
            admin = create_user(
                conn,
                username=username,
                password=password,
                role="admin",
            )
            garden = conn.execute(
                """
                INSERT INTO gardens (slug, name, onboarding_complete)
                VALUES (%s, %s, 1)
                RETURNING id
                """,
                (slug, "Atomic Delete Garden"),
            ).fetchone()
            assert garden is not None
            garden_id = int(garden["id"])
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (garden_id, int(admin["id"])),
            )
            conn.execute(
                """
                INSERT INTO plots (
                    plot_id, garden_id, zone_code, zone_name, plot_number, grid_row, grid_col,
                    sub_zone, notes, color
                )
                VALUES (%s, %s, 'A', 'Atomic', 1, 1, 1, '', '', '#4a7c59')
                """,
                (plot_id, garden_id),
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                (plot_id, int(admin["id"]), garden_id),
            )
            conn.execute(
                """
                INSERT INTO plants (
                    plt_id, name, latin, category, bloom_month, color, hardiness,
                    height_cm, light, link
                )
                VALUES (%s, 'Atomic Plant', '', 'busker', '', '', '', NULL, '', '')
                """,
                (plant_id,),
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                (plant_id, int(admin["id"]), garden_id),
            )
            conn.execute(
                "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES (%s, %s, 1)",
                (plot_id, plant_id),
            )
            conn.execute(
                """
                INSERT INTO layout_snapshots (public_id, name, data, garden_id)
                VALUES (%s, 'Atomic snapshot', '{"plots":[]}', %s)
                """,
                (generate_public_id("snap"), garden_id),
            )
            asset_id = f"atomic-media-{suffix}"
            conn.execute(
                """
                INSERT INTO media_assets (
                    asset_id, garden_id, storage_key, preview_storage_key,
                    original_filename, mime_type, bytes, width, height,
                    created_at_ms, actor_user_id
                )
                VALUES (%s, %s, %s, %s, 'atomic.png', 'image/png', 4, 1, 1, %s, %s)
                """,
                (
                    asset_id,
                    garden_id,
                    f"original/atomic/{suffix}.png",
                    f"preview/atomic/{suffix}.png",
                    db.current_timestamp_ms(),
                    int(admin["id"]),
                ),
            )
            conn.execute(
                """
                INSERT INTO media_links (asset_id, target_type, target_id, sort_order)
                VALUES (%s, 'plot', %s, 0)
                """,
                (asset_id, plot_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)
        for storage_key in (
            f"original/atomic/{suffix}.png",
            f"preview/atomic/{suffix}.png",
            f"lidar/garden-{garden_id}/terrain.laz",
        ):
            path = self.test_media_dir / storage_key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test")
        return {
            "admin_id": int(admin["id"]),
            "asset_id": asset_id,
            "garden_id": garden_id,
            "lidar_storage_key": f"lidar/garden-{garden_id}/terrain.laz",
            "password": password,
            "preview_storage_key": f"preview/atomic/{suffix}.png",
            "storage_key": f"original/atomic/{suffix}.png",
            "plant_id": plant_id,
            "plot_id": plot_id,
            "slug": slug,
            "username": username,
        }

    def _destructive_headers(
        self,
        client: TestClient,
        garden: dict[str, int | str],
    ) -> dict[str, str]:
        _, csrf = self._login_session(
            str(garden["username"]),
            str(garden["password"]),
            client=client,
        )
        headers = self._session_headers(csrf, garden_id=int(garden["garden_id"]))
        return self._reauth_and_refresh_headers(
            client,
            headers,
            password=str(garden["password"]),
        )

    def _assert_related_state_exists(self, garden: dict[str, int | str]) -> None:
        garden_id = int(garden["garden_id"])
        plot_id = str(garden["plot_id"])
        plant_id = str(garden["plant_id"])
        conn = db.get_db()
        try:
            self.assertIsNotNone(
                conn.execute("SELECT 1 FROM gardens WHERE id = %s", (garden_id,)).fetchone()
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT 1 FROM garden_memberships WHERE garden_id = %s",
                    (garden_id,),
                ).fetchone()
            )
            self.assertIsNotNone(
                conn.execute("SELECT 1 FROM plots WHERE plot_id = %s", (plot_id,)).fetchone()
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT 1 FROM plot_ownership WHERE plot_id = %s AND garden_id = %s",
                    (plot_id, garden_id),
                ).fetchone()
            )
            self.assertIsNotNone(
                conn.execute("SELECT 1 FROM plants WHERE plt_id = %s", (plant_id,)).fetchone()
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT 1 FROM plant_ownership WHERE plt_id = %s AND garden_id = %s",
                    (plant_id, garden_id),
                ).fetchone()
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT 1 FROM plot_plants WHERE plot_id = %s AND plt_id = %s",
                    (plot_id, plant_id),
                ).fetchone()
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT 1 FROM layout_snapshots WHERE garden_id = %s",
                    (garden_id,),
                ).fetchone()
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT 1 FROM media_assets WHERE garden_id = %s",
                    (garden_id,),
                ).fetchone()
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT 1 FROM media_links WHERE asset_id = %s",
                    (garden["asset_id"],),
                ).fetchone()
            )
        finally:
            db.return_db(conn)
        self.assertTrue((self.test_media_dir / str(garden["storage_key"])).is_file())
        self.assertTrue((self.test_media_dir / str(garden["preview_storage_key"])).is_file())
        self.assertTrue((self.test_media_dir / str(garden["lidar_storage_key"])).is_file())

    def test_garden_delete_commits_required_audit_event_before_side_effects(self) -> None:
        garden = self._create_deletable_garden()
        garden_id = int(garden["garden_id"])

        def assert_delete_is_committed() -> None:
            conn = db.get_db()
            try:
                self.assertIsNone(
                    conn.execute("SELECT 1 FROM gardens WHERE id = %s", (garden_id,)).fetchone()
                )
                audit_row = conn.execute(
                    """
                    SELECT 1
                    FROM audit_events
                    WHERE path = %s AND detail LIKE 'garden.delete %%'
                    """,
                    (f"/api/gardens/{garden_id}",),
                ).fetchone()
                self.assertIsNotNone(audit_row)
            finally:
                db.return_db(conn)

        def assert_cleanup_after_commit(*_args: object, **_kwargs: object) -> None:
            assert_delete_is_committed()

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            client = self._new_client()
            headers = self._destructive_headers(client, garden)
            with (
                patch(
                    "gardenops.routers.gardens.notify_garden_modified",
                    side_effect=assert_delete_is_committed,
                ) as notify,
                patch("gardenops.routers.gardens.record_security_event") as record_security,
                patch("gardenops.audit.enqueue_security_telemetry") as enqueue_telemetry,
                patch(
                    "gardenops.routers.gardens.drain_media_cleanup_jobs_best_effort",
                    side_effect=assert_cleanup_after_commit,
                ) as drain_cleanup,
            ):
                response = client.delete(
                    f"/api/gardens/{garden_id}",
                    headers={**headers, "x-action-reason": "atomic-delete-success"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "garden_id": garden_id,
                "garden_name": "Atomic Delete Garden",
                "plots_deleted": 1,
                "snapshots_deleted": 1,
                "plants_deleted": 1,
            },
        )
        notify.assert_called_once_with()
        enqueue_telemetry.assert_called_once()
        drain_cleanup.assert_called_once()
        self.assertEqual(
            drain_cleanup.call_args.kwargs["storage_pairs"],
            [
                (garden["lidar_storage_key"], ""),
                (garden["storage_key"], garden["preview_storage_key"]),
            ],
        )
        self.assertEqual(enqueue_telemetry.call_args.args[0], "audit_event")
        self.assertEqual(
            enqueue_telemetry.call_args.args[1]["path"],
            f"/api/gardens/{garden_id}",
        )
        self.assertEqual(
            record_security.call_args_list,
            [
                call("destructive_admin_actions"),
                call("destructive_admin_actions_delete_garden"),
            ],
        )

        conn = db.get_db()
        try:
            audit_row = conn.execute(
                """
                SELECT actor_user_id, actor_username, actor_role, actor_auth_type,
                       garden_id, method, path, status_code, detail
                FROM audit_events
                WHERE path = %s AND detail LIKE 'garden.delete %%'
                """,
                (f"/api/gardens/{garden_id}",),
            ).fetchone()
            self.assertIsNotNone(audit_row)
            assert audit_row is not None
            self.assertEqual(audit_row["actor_user_id"], int(garden["admin_id"]))
            self.assertEqual(audit_row["actor_username"], garden["username"])
            self.assertEqual(audit_row["actor_role"], "admin")
            self.assertEqual(audit_row["actor_auth_type"], "session")
            self.assertIsNone(audit_row["garden_id"])
            self.assertEqual(audit_row["method"], "DELETE")
            self.assertEqual(audit_row["status_code"], 200)
            detail = json.loads(str(audit_row["detail"])[len("garden.delete ") :])
            self.assertEqual(detail["action_reason"], "atomic-delete-success")
            self.assertEqual(detail["garden_id"], garden_id)
            self.assertEqual(detail["garden_name"], "Atomic Delete Garden")
            self.assertEqual(detail["slug"], garden["slug"])
            self.assertEqual(detail["plots_deleted"], 1)
            self.assertEqual(detail["snapshots_deleted"], 1)
            self.assertEqual(detail["plants_deleted"], 1)
        finally:
            db.return_db(conn)

    def test_garden_delete_remains_committed_when_telemetry_enqueue_fails(self) -> None:
        garden = self._create_deletable_garden()
        garden_id = int(garden["garden_id"])

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            client = self._new_client()
            headers = self._destructive_headers(client, garden)
            with patch(
                "gardenops.audit.enqueue_security_telemetry",
                side_effect=RuntimeError("telemetry unavailable"),
            ) as enqueue_telemetry:
                response = client.delete(
                    f"/api/gardens/{garden_id}",
                    headers={**headers, "x-action-reason": "telemetry-failure"},
                )

        self.assertEqual(response.status_code, 200)
        enqueue_telemetry.assert_called_once()
        conn = db.get_db()
        try:
            self.assertIsNone(
                conn.execute("SELECT 1 FROM gardens WHERE id = %s", (garden_id,)).fetchone()
            )
            self.assertIsNotNone(
                conn.execute(
                    """
                    SELECT 1 FROM audit_events
                    WHERE path = %s AND detail LIKE 'garden.delete %%'
                    """,
                    (f"/api/gardens/{garden_id}",),
                ).fetchone()
            )
        finally:
            db.return_db(conn)

    def test_concurrent_garden_delete_has_one_success_and_one_audit(self) -> None:
        garden = self._create_deletable_garden()
        garden_id = int(garden["garden_id"])

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            clients = [self._new_client(), self._new_client()]
            headers = [self._destructive_headers(client, garden) for client in clients]
            barrier = threading.Barrier(2)

            def delete_once(index: int) -> int:
                barrier.wait(timeout=10)
                response = clients[index].delete(
                    f"/api/gardens/{garden_id}",
                    headers={**headers[index], "x-action-reason": "concurrent-delete"},
                )
                return response.status_code

            with (
                patch("gardenops.audit.enqueue_security_telemetry"),
                patch("gardenops.routers.gardens.notify_garden_modified") as notify,
                patch("gardenops.routers.gardens.record_security_event") as record_security,
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                statuses = list(pool.map(delete_once, range(2)))

        self.assertEqual(sorted(statuses), [200, 404])
        notify.assert_called_once_with()
        self.assertEqual(
            record_security.call_args_list,
            [
                call("destructive_admin_actions"),
                call("destructive_admin_actions_delete_garden"),
            ],
        )
        conn = db.get_db()
        try:
            audit_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM audit_events
                WHERE path = %s AND detail LIKE 'garden.delete %%'
                """,
                (f"/api/gardens/{garden_id}",),
            ).fetchone()
            assert audit_count is not None
            self.assertEqual(int(audit_count["count"]), 1)
        finally:
            db.return_db(conn)

    def test_garden_delete_rolls_back_when_required_audit_write_fails(self) -> None:
        garden = self._create_deletable_garden()
        garden_id = int(garden["garden_id"])

        original_audit_insert = audit_service._insert_audit_event_row

        def fail_audit_insert(
            conn: db.DbConn,
            values: tuple[object, ...],
            *,
            reserve: bool = False,
        ) -> int:
            if reserve:
                return original_audit_insert(conn, values, reserve=True)
            conn.execute("SELECT 1 / 0")
            raise AssertionError("unreachable")

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            self.addCleanup(client.close)
            headers = self._destructive_headers(client, garden)
            with (
                patch(
                    "gardenops.audit._insert_audit_event_row",
                    side_effect=fail_audit_insert,
                ) as audit_insert,
                patch("gardenops.routers.gardens.notify_garden_modified") as notify,
                patch("gardenops.routers.gardens.record_security_event") as record_security,
            ):
                response = client.delete(
                    f"/api/gardens/{garden_id}",
                    headers={**headers, "x-action-reason": "atomic-delete-failure"},
                )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(audit_insert.call_count, 2)
        notify.assert_not_called()
        record_security.assert_not_called()
        self._assert_related_state_exists(garden)

        conn = db.get_db()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS count, MAX(status_code) AS status_code "
                "FROM audit_events WHERE path = %s",
                (f"/api/gardens/{garden_id}",),
            ).fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(int(row["count"]), 1)
            self.assertEqual(int(row["status_code"]), 102)
        finally:
            db.return_db(conn)

    def test_garden_delete_child_failure_rolls_back_complete_graph_and_files(self) -> None:
        garden = self._create_deletable_garden()
        garden_id = int(garden["garden_id"])
        original_delete_plots = gardens_router.delete_plots_for_replacement

        def fail_after_child_deletes(
            db_conn: db.DbConn,
            *,
            garden_id: int,
            plot_ids: list[str],
        ) -> object:
            original_delete_plots(
                db_conn,
                garden_id=garden_id,
                plot_ids=plot_ids,
            )
            raise RuntimeError("injected child delete failure")

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            self.addCleanup(client.close)
            headers = self._destructive_headers(client, garden)
            with (
                patch(
                    "gardenops.routers.gardens.delete_plots_for_replacement",
                    side_effect=fail_after_child_deletes,
                ),
                patch("gardenops.routers.gardens.notify_garden_modified") as notify,
                patch("gardenops.routers.gardens.record_security_event") as record_security,
                patch(
                    "gardenops.routers.gardens.drain_media_cleanup_jobs_best_effort"
                ) as drain_cleanup,
            ):
                response = client.delete(
                    f"/api/gardens/{garden_id}",
                    headers={**headers, "x-action-reason": "child-delete-failure"},
                )

        self.assertEqual(response.status_code, 500)
        notify.assert_not_called()
        record_security.assert_not_called()
        drain_cleanup.assert_not_called()
        self._assert_related_state_exists(garden)

        conn = db.get_db()
        try:
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM media_cleanup_jobs WHERE storage_key = %s",
                    (garden["storage_key"],),
                ).fetchone()
            )
        finally:
            db.return_db(conn)

    def test_garden_delete_detects_future_uncascaded_garden_state(self) -> None:
        garden = self._create_deletable_garden()
        garden_id = int(garden["garden_id"])
        conn = db.get_db()
        try:
            conn.execute(
                """
                CREATE TABLE phase_six_uncascaded_garden_state (
                    id BIGSERIAL PRIMARY KEY,
                    garden_id BIGINT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO phase_six_uncascaded_garden_state (garden_id) VALUES (%s)",
                (garden_id,),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        try:
            with patch.dict(
                os.environ,
                {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
                clear=False,
            ):
                client = TestClient(app, raise_server_exceptions=False)
                self.addCleanup(client.close)
                headers = self._destructive_headers(client, garden)
                response = client.delete(
                    f"/api/gardens/{garden_id}",
                    headers={**headers, "x-action-reason": "future-table-guard"},
                )

            self.assertEqual(response.status_code, 500)
            self._assert_related_state_exists(garden)
            conn = db.get_db()
            try:
                retained = conn.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM phase_six_uncascaded_garden_state
                    WHERE garden_id = %s
                    """,
                    (garden_id,),
                ).fetchone()
                self.assertEqual(int(retained["cnt"]), 1)
            finally:
                db.return_db(conn)
        finally:
            conn = db.get_db()
            try:
                conn.execute("DROP TABLE IF EXISTS phase_six_uncascaded_garden_state")
                conn.commit()
            finally:
                db.return_db(conn)
