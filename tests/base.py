"""Shared base test class for all API tests.

Uses TRUNCATE + re-seed for test isolation on Postgres.
Each test's setUp truncates all public tables (except schema_migrations)
and subclasses seed their data.
"""

import hashlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
from starlette.requests import Request as StarletteRequest

import gardenops.db as db
from gardenops.main import app
from gardenops.rate_limit import reset_rate_limits
from gardenops.routers.shademap import reset_shademap_abuse_tracking
from gardenops.security import create_user
from gardenops.security_metrics import reset_security_metrics
from gardenops.security_mfa import _totp_at, _totp_period_seconds
from gardenops.security_telemetry import reset_security_telemetry

_CLASS_ENV = {
    "SHADEMAP": "test-private-key",
    "SHADEMAP_PUBLIC_API_KEY": "test-public-key",
    "SHADEMAP_TILE_SIGNING_SECRET": "test-tile-signing-secret",
    "AUTH_REQUIRED": "false",
    "RATE_LIMIT_BACKEND": "memory",
    "APP_ENV": "test",
    "INTERNET_EXPOSED": "false",
}

_DB_TEST_ENV = {"APP_ENV": "test"}


def _truncate_all_tables() -> None:
    """Truncate all public tables except schema_migrations."""
    conn = db.get_db()
    try:
        rows = conn.execute(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename != 'schema_migrations'
            """
        ).fetchall()
        tables = [row["tablename"] for row in rows]
        if tables:
            conn.execute("TRUNCATE {} CASCADE".format(", ".join(tables)))
        conn.commit()
    finally:
        db.return_db(conn)


class DbTestBase(unittest.TestCase):
    """Lightweight base for tests that need a real Postgres DB but no API client.

    Uses TRUNCATE + re-seed: each ``setUp`` truncates all public tables,
    then seeds fresh data.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._env_patcher = patch.dict("os.environ", _DB_TEST_ENV)
        cls._env_patcher.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._env_patcher.stop()

    def setUp(self) -> None:
        _truncate_all_tables()

        # Seed a default garden and admin user
        conn = db.get_db()
        try:
            db.ensure_default_garden(conn)
            conn.commit()

            user = create_user(
                conn,
                username="dbtest_admin",
                password=strong_password("testpassword123"),
                role="admin",
            )
            conn.commit()
            self._owner_id: int = int(user["id"])
            self.garden_id: int = int(
                conn.execute(
                    "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
                ).fetchone()["id"],
            )
        finally:
            db.return_db(conn)

        self.conn = db.get_db()

    def tearDown(self) -> None:
        db.return_db(self.conn)

    # -- Shared helpers -------------------------------------------------------

    def _insert_plant(
        self,
        plt_id: str,
        name: str,
        latin: str = "",
        category: str = "busker",
        hardiness: str = "",
        care_watering: str = "",
        bloom_month: str = "",
        height_cm: int | None = None,
        light: str = "",
        year_planted: str = "",
        care_soil: str = "",
        care_planting: str = "",
        care_maintenance: str = "",
        care_notes: str = "",
    ) -> None:
        self.conn.execute(
            "INSERT INTO plants "
            "(plt_id, name, latin, category, bloom_month, color, "
            "hardiness, height_cm, light, link, care_watering, "
            "care_soil, care_planting, care_maintenance, care_notes) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                plt_id,
                name,
                latin,
                category,
                bloom_month,
                "",
                hardiness,
                height_cm,
                light,
                "",
                care_watering,
                care_soil,
                care_planting,
                care_maintenance,
                care_notes,
            ),
        )
        self.conn.execute(
            "INSERT INTO plant_ownership "
            "(plt_id, owner_user_id, garden_id) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (plt_id, self._owner_id, self.garden_id),
        )
        self.conn.commit()


_TEST_PASSWORD_SYMBOLS = set("!@#$%^&*()-_=+[]{};:,.?/|~`")


def _password_meets_default_policy(password: str) -> bool:
    return (
        len(password) >= 30
        and any(ch.islower() for ch in password)
        and any(ch.isupper() for ch in password)
        and any(ch.isdigit() for ch in password)
        and any(ch in _TEST_PASSWORD_SYMBOLS for ch in password)
    )


def strong_password(seed: str) -> str:
    """Return a deterministic password that satisfies the default policy."""
    if _password_meets_default_policy(seed):
        return seed
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"Test!{digest[:12]}Aa-{digest[12:24]}Z9"


class BaseApiTest(unittest.TestCase):
    """Base class with optimised setUp via TRUNCATE + re-seed on Postgres."""

    @staticmethod
    def _request_with_api_key(
        api_key: str,
        host: str = "127.0.0.1",
    ) -> StarletteRequest:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-api-key", api_key.encode("utf-8"))],
            "client": (host, 5000),
        }
        return StarletteRequest(scope)

    @staticmethod
    def _request_with_bearer(
        token: str,
        host: str = "127.0.0.1",
    ) -> StarletteRequest:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (b"authorization", f"Bearer {token}".encode()),
            ],
            "client": (host, 5000),
        }
        return StarletteRequest(scope)

    def _setup_admin_two_gardens(self) -> tuple[int, int, str, str]:
        username = "secimp_admin"
        password = strong_password("secimp-admin-pass")
        conn = db.get_db()
        try:
            default_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert default_garden is not None
            default_garden_id = int(default_garden["id"])
            admin = create_user(
                conn,
                username=username,
                password=password,
                role="admin",
            )
            second_slug = f"secimp-g2-{__import__('os').urandom(4).hex()}"
            cursor = conn.execute(
                "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                (second_slug, "SEC-IMP Garden 2"),
            )
            second_garden_id = int(cursor.fetchone()["id"])
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (second_garden_id, int(admin["id"])),
            )
            conn.commit()
        finally:
            db.return_db(conn)
        return default_garden_id, second_garden_id, username, password

    def _new_client(self) -> TestClient:
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def _login_session(
        self,
        username: str,
        password: str,
        *,
        client: TestClient | None = None,
    ) -> tuple[TestClient, str]:
        target: TestClient = client or self.client
        response = target.post(
            "/api/auth/login",
            json={"username": username, "password": strong_password(password)},
        )
        self.assertEqual(response.status_code, 200)
        csrf_token = target.cookies.get("gardenops_csrf") or ""
        self.assertTrue(csrf_token)
        return target, csrf_token

    @staticmethod
    def _session_headers(
        csrf_token: str,
        *,
        garden_id: int | None = None,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        headers = dict(extra or {})
        if csrf_token:
            headers.setdefault("x-csrf-token", csrf_token)
        if garden_id is not None:
            headers["x-garden-id"] = str(garden_id)
        return headers

    @staticmethod
    def _totp_code(secret: str, *, offset: int = 0) -> str:
        counter = db.current_timestamp_ms() // (_totp_period_seconds() * 1000)
        return _totp_at(secret, counter + offset)

    def _reauth_and_refresh_headers(
        self,
        client: TestClient,
        headers: dict[str, str],
        *,
        password: str,
        mfa_code: str = "",
        recovery_code: str = "",
    ) -> dict[str, str]:
        body: dict[str, str] = {"current_password": password}
        if mfa_code:
            body["mfa_code"] = mfa_code
        if recovery_code:
            body["recovery_code"] = recovery_code
        resp = client.post(
            "/api/auth/reauthenticate",
            headers=headers,
            json=body,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        new_csrf = resp.json().get("csrf_token", "")
        garden_id = headers.get("x-garden-id")
        return self._session_headers(
            new_csrf,
            garden_id=int(garden_id) if garden_id else None,
        )

    @staticmethod
    def _image_bytes(
        *,
        fmt: str = "PNG",
        size: tuple[int, int] = (40, 24),
        color: tuple[int, int, int, int] = (80, 140, 90, 255),
    ) -> bytes:
        buffer = io.BytesIO()
        image = Image.new("RGBA", size, color)
        save_image = image if fmt.upper() != "JPEG" else image.convert("RGB")
        save_image.save(buffer, format=fmt)
        return buffer.getvalue()

    # -- Class-level setup / teardown -----------------------------------------

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp_dir = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp_dir.name)
        cls.test_media_dir = tmp / "media"

        cls._env_patcher = patch.dict(
            "os.environ",
            {**_CLASS_ENV, "MEDIA_STORAGE_DIR": str(cls.test_media_dir)},
        )
        cls._env_patcher.start()

        reset_rate_limits()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls._env_patcher.stop()
        cls.tmp_dir.cleanup()

    def _seed_data(self) -> None:
        conn = db.get_db()
        try:
            conn.execute(
                "INSERT INTO plots VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("B1", "B", "Bed", 1, 1, 1, "", "", None),
            )
            conn.execute(
                "INSERT INTO plots VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("B2", "B", "Bed", 2, 1, 2, "", "", None),
            )
            conn.execute(
                "INSERT INTO plants "
                "(plt_id, name, latin, category, bloom_month, color, "
                "hardiness, height_cm, light, link) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    "PLT-TEST",
                    "Test Plant",
                    "Testus plantus",
                    "froe",
                    "",
                    "",
                    "",
                    None,
                    "",
                    "",
                ),
            )
            conn.execute(
                "INSERT INTO plants "
                "(plt_id, name, latin, category, bloom_month, color, "
                "hardiness, height_cm, light, link) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    "PLT-002",
                    "Rose",
                    "Rosa canina",
                    "busker",
                    "juni",
                    "roed",
                    "H5",
                    150,
                    "sol",
                    "",
                ),
            )

            default_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert default_garden is not None
            gid = int(default_garden["id"])
            for plot_id in ("B1", "B2"):
                conn.execute(
                    """
                    INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT(plot_id) DO UPDATE SET
                        owner_user_id = excluded.owner_user_id,
                        garden_id = excluded.garden_id
                    """,
                    (plot_id, self._owner_id, gid),
                )
            for plt_id in ("PLT-TEST", "PLT-002"):
                conn.execute(
                    """
                    INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT(plt_id, garden_id) DO UPDATE SET
                        owner_user_id = excluded.owner_user_id
                    """,
                    (plt_id, self._owner_id, gid),
                )

            conn.execute(
                """
                INSERT INTO layout_state (
                    garden_id, house_row, house_col,
                    house_width, house_height, north_degrees
                ) VALUES (%s, 9, 6, 12, 8, 0)
                ON CONFLICT(garden_id) DO UPDATE SET
                    house_row = excluded.house_row,
                    house_col = excluded.house_col,
                    house_width = excluded.house_width,
                    house_height = excluded.house_height,
                    north_degrees = excluded.north_degrees
                """,
                (gid,),
            )
            conn.execute(
                """
                INSERT INTO shademap_state (
                    garden_id, mode, selected_plot_id,
                    analysis_timestamp_ms, preset
                ) VALUES (%s, 'shadow', NULL, 1772443603995, 'now')
                ON CONFLICT(garden_id) DO UPDATE SET
                    mode = excluded.mode,
                    selected_plot_id = excluded.selected_plot_id,
                    analysis_timestamp_ms = excluded.analysis_timestamp_ms,
                    preset = excluded.preset
                """,
                (gid,),
            )
            conn.execute(
                """
                INSERT INTO shademap_calibration (
                    garden_id, enabled,
                    origin_grid_col, origin_grid_row,
                    origin_latitude, origin_longitude,
                    axis_grid_col, axis_grid_row,
                    axis_latitude, axis_longitude
                ) VALUES (%s, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)
                ON CONFLICT(garden_id) DO UPDATE SET
                    enabled = 0,
                    origin_grid_col = NULL, origin_grid_row = NULL,
                    origin_latitude = NULL, origin_longitude = NULL,
                    axis_grid_col = NULL, axis_grid_row = NULL,
                    axis_latitude = NULL, axis_longitude = NULL
                """,
                (gid,),
            )
            conn.execute(
                """
                INSERT INTO security_runtime_flags (key, value)
                VALUES ('emergency_read_only', '0')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
            )
            conn.execute(
                """
                INSERT INTO security_runtime_flags (key, value)
                VALUES ('emergency_read_only_expires_at_ms', '0')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
            )
            conn.commit()
        finally:
            db.return_db(conn)

    # -- Shared helpers -------------------------------------------------------

    def _get_default_garden_id(self) -> int:
        conn = db.get_db()
        try:
            row = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert row is not None
            return int(row["id"])
        finally:
            db.return_db(conn)

    def _create_test_user(
        self,
        username: str,
        password: str,
        role: str = "editor",
    ) -> dict:
        conn = db.get_db()
        try:
            user = create_user(
                conn,
                username=username,
                password=strong_password(password),
                role=role,
            )
            # Test users get pro tier so tier gating doesn't block endpoint tests.
            conn.execute(
                "UPDATE auth_users SET subscription_tier = 'pro' WHERE id = %s",
                (user["id"],),
            )
            conn.commit()
            return dict(user)
        finally:
            db.return_db(conn)

    def _authenticated_client(
        self,
        username: str,
        password: str,
        *,
        garden_id: int | None = None,
    ) -> tuple[TestClient, dict[str, str]]:
        client = self._new_client()
        _, csrf = self._login_session(
            username,
            password,
            client=client,
        )
        headers = self._session_headers(csrf, garden_id=garden_id)
        return client, headers

    @staticmethod
    def _anthropic_mock_response(
        tool_name: str,
        tool_input: dict,
    ) -> tuple:
        """Return (mocked_client, response_payload) for Anthropic API."""
        from unittest.mock import MagicMock

        response_block = type(
            "ToolBlock",
            (),
            {
                "type": "tool_use",
                "name": tool_name,
                "input": tool_input,
            },
        )()
        response_payload = type(
            "AnthropicResponse",
            (),
            {"content": [response_block]},
        )()
        mocked_client = MagicMock()
        mocked_client.messages.create.return_value = response_payload
        return mocked_client, response_payload

    # -- Per-test setup -------------------------------------------------------

    def setUp(self) -> None:
        _truncate_all_tables()

        # Seed the default garden and a default admin user
        conn = db.get_db()
        try:
            db.ensure_default_garden(conn)
            conn.commit()
            user = create_user(
                conn,
                username="test_admin",
                password=strong_password("testadminpass"),
                role="admin",
            )
            conn.execute(
                "UPDATE auth_users SET subscription_tier = 'pro' WHERE id = %s",
                (user["id"],),
            )
            self._owner_id: int = int(user["id"])
            conn.commit()
        finally:
            db.return_db(conn)

        # Seed test data and set all users to pro tier
        self._seed_data()
        conn = db.get_db()
        try:
            conn.execute("UPDATE auth_users SET subscription_tier = 'pro'")
            conn.commit()
        finally:
            db.return_db(conn)

        reset_rate_limits()
        reset_security_metrics()
        reset_shademap_abuse_tracking()
        reset_security_telemetry()
        self.client.cookies.clear()
        shutil.rmtree(self.test_media_dir, ignore_errors=True)
        self.test_media_dir.mkdir(parents=True, exist_ok=True)
