import asyncio
import hashlib
import json
import os
import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest

import gardenops.db as db
import gardenops.main as main_module
import gardenops.passkeys as passkey_service
import gardenops.rate_limit as rate_limit_module
from gardenops.main import (
    _api_docs_enabled,
    _edge_proxy_violation_detail,
    _validate_runtime_security_config,
)
from gardenops.redaction import redact_external_log_text
from gardenops.routers.ai import _validate_plant_link, build_garden_context
from gardenops.security import AuthContext, _legacy_pbkdf2_hash_password, create_user
from gardenops.security_metrics import record_security_event
from gardenops.security_telemetry import (
    drain_security_telemetry_once,
    enqueue_security_telemetry,
    ensure_security_metrics_snapshot_enqueued,
)
from tests.base import BaseApiTest, strong_password


class TestSecurity(BaseApiTest):
    class _DummyResponse:
        def __init__(self, *, status: int = 200, body: bytes = b"ok") -> None:
            self.status = status
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size: int = -1) -> bytes:
            if size < 0:
                return self._body
            return self._body[:size]

        def getcode(self) -> int:
            return self.status

    @staticmethod
    def _edge_request(
        *,
        path: str = "/api/version",
        client_host: str = "127.0.0.1",
        headers: dict[str, str] | None = None,
    ) -> StarletteRequest:
        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": b"",
            "headers": [
                (key.lower().encode("utf-8"), value.encode("utf-8"))
                for key, value in (headers or {}).items()
            ],
            "client": (client_host, 5000),
            "server": ("testserver", 80),
        }
        return StarletteRequest(scope)

    def test_forged_auth_headers_do_not_create_distinct_pre_auth_rate_limit_keys(self) -> None:
        request_a = self._request_with_bearer("forged-a")
        request_b = self._request_with_bearer("forged-b")

        self.assertEqual(
            rate_limit_module._client_key(request_a, "auth-fail"),
            rate_limit_module._client_key(request_b, "auth-fail"),
        )

    def test_authenticated_rate_limit_key_uses_resolved_user_context(self) -> None:
        request_a = self._request_with_bearer("session-a", host="127.0.0.1")
        request_b = self._request_with_bearer("session-b", host="10.0.0.10")
        for request in (request_a, request_b):
            request.state.auth_context = AuthContext(
                user_id=42,
                username="rate-user",
                role="editor",
                auth_type="session",
            )

        self.assertEqual(
            rate_limit_module._client_key(request_a, "api-mutation"),
            rate_limit_module._client_key(request_b, "api-mutation"),
        )

    def test_streamed_api_body_without_content_length_still_enforces_limit(self) -> None:
        async def exercise_request() -> int:
            body_chunks = [
                {"type": "http.request", "body": b'{"name":"', "more_body": True},
                {"type": "http.request", "body": b"a" * 64, "more_body": True},
                {"type": "http.request", "body": b'"}', "more_body": False},
            ]
            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/snapshots",
                "raw_path": b"/api/snapshots",
                "query_string": b"",
                "headers": [(b"content-type", b"application/json")],
                "client": ("127.0.0.1", 5000),
                "server": ("testserver", 80),
            }
            sent: list[dict] = []

            async def receive() -> dict:
                if body_chunks:
                    return body_chunks.pop(0)
                return {"type": "http.disconnect"}

            async def send(message: dict) -> None:
                sent.append(message)

            with patch.dict(os.environ, {"MAX_API_BODY_BYTES": "16"}, clear=False):
                await main_module.app(scope, receive, send)

            for message in sent:
                if message["type"] == "http.response.start":
                    return int(message["status"])
            self.fail("ASGI app did not send a response start")

        self.assertEqual(asyncio.run(exercise_request()), 413)

    def test_provider_daily_budget_reservation_is_atomic_under_race(self) -> None:
        now_ms = 1_770_000_000_000
        feature = "race-budget-test"
        usage_day = rate_limit_module._provider_usage_day(now_ms)
        barrier = threading.Barrier(2)

        conn = db.get_db()
        try:
            conn.execute(
                """
                DELETE FROM provider_daily_usage
                WHERE usage_day = %s AND feature = %s
                """,
                (usage_day, feature),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        def reserve_once() -> int:
            conn = db.get_db()
            try:
                barrier.wait(timeout=10)
                try:
                    rate_limit_module.reserve_daily_provider_budget(
                        conn,
                        feature=feature,
                        user_id=self._owner_id,
                        user_limit=1,
                        request_count=1,
                        now_ms=now_ms,
                    )
                    return 200
                except HTTPException as exc:
                    return exc.status_code
            finally:
                db.return_db(conn)

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: reserve_once(), range(2)))

        self.assertEqual(sorted(statuses), [200, 429])
        conn = db.get_db()
        try:
            row = conn.execute(
                """
                SELECT request_count
                FROM provider_daily_usage
                WHERE usage_day = %s
                  AND feature = %s
                  AND scope_type = 'user'
                  AND scope_id = %s
                """,
                (usage_day, feature, self._owner_id),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertLessEqual(int(row["request_count"]), 1)
        finally:
            db.return_db(conn)

    def test_request_id_header_is_echoed_for_api_responses(self) -> None:
        response = self.client.get(
            "/api/version",
            headers={"x-request-id": "client-debug-123"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("x-request-id"), "client-debug-123")

    def test_request_id_header_is_generated_when_missing(self) -> None:
        response = self.client.get("/api/version")

        self.assertEqual(response.status_code, 200)
        request_id = response.headers.get("x-request-id", "")
        self.assertRegex(request_id, r"^[0-9a-f]{32}$")

    def test_git_version_file_fallback_logs_once(self) -> None:
        original_logged = main_module._GIT_FILE_FALLBACK_LOGGED
        main_module._GIT_FILE_FALLBACK_LOGGED = False
        try:
            with (
                patch("gardenops.main.subprocess.run", side_effect=RuntimeError("git failed")),
                patch("gardenops.main._git_head_from_files", return_value="abc123def456"),
                self.assertLogs("gardenops.main", level="DEBUG") as logs,
            ):
                first = main_module._git_version_state()
                second = main_module._git_version_state()

            self.assertEqual(first[0], "abc123def456")
            self.assertEqual(second[0], "abc123def456")
            self.assertEqual(
                sum(
                    "Resolved git version state from .git files after git CLI failed" in message
                    for message in logs.output
                ),
                1,
            )
        finally:
            main_module._GIT_FILE_FALLBACK_LOGGED = original_logged

    def test_git_version_commands_mark_repo_as_safe_directory(self) -> None:
        calls: list[list[str]] = []

        def fake_run(args: list[str], **_kwargs: object) -> MagicMock:
            calls.append(args)
            if "rev-parse" in args:
                return MagicMock(stdout="abc123def456\n")
            if "log" in args:
                return MagicMock(stdout="1710000000\n")
            return MagicMock(stdout="")

        with (
            patch("gardenops.main.subprocess.run", side_effect=fake_run),
            patch("gardenops.main._dirty_tracked_paths", return_value=[]),
        ):
            commit, dirty, _last_updated_at_ms, _dynamic_suffix = main_module._git_version_state()

        safe_prefix = ["git", "-c", f"safe.directory={main_module.ROOT}"]
        self.assertEqual(commit, "abc123def456")
        self.assertFalse(dirty)
        self.assertTrue(calls)
        self.assertTrue(all(call[:3] == safe_prefix for call in calls))

    def test_dirty_tracked_paths_marks_repo_as_safe_directory(self) -> None:
        calls: list[list[str]] = []

        def fake_run(args: list[str], **_kwargs: object) -> MagicMock:
            calls.append(args)
            return MagicMock(stdout="gardenops/main.py\0")

        with patch("gardenops.main.subprocess.run", side_effect=fake_run):
            dirty_paths = main_module._dirty_tracked_paths()

        safe_prefix = ["git", "-c", f"safe.directory={main_module.ROOT}"]
        self.assertEqual([path.as_posix() for path in dirty_paths], ["gardenops/main.py"])
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[:3] == safe_prefix for call in calls))

    def test_rate_limit_bucket_cap_and_expiry_pruning(self) -> None:
        old_max_buckets = os.environ.get("RATE_LIMIT_MAX_BUCKETS")
        os.environ["RATE_LIMIT_MAX_BUCKETS"] = "2"
        try:
            request_a = self._request_with_api_key("key-a", host="127.0.0.1")
            request_b = self._request_with_api_key("key-b", host="127.0.0.2")
            request_c = self._request_with_api_key("key-c", host="127.0.0.3")

            with patch(
                "gardenops.rate_limit.time.monotonic",
                side_effect=[0.0, 1.0, 2.0, 70.0],
            ):
                rate_limit_module.enforce_rate_limit(
                    request_a,
                    bucket="cap-test",
                    limit=10,
                    window_seconds=60,
                )
                key_a = rate_limit_module._client_key(request_a, "cap-test")

                rate_limit_module.enforce_rate_limit(
                    request_b,
                    bucket="cap-test",
                    limit=10,
                    window_seconds=60,
                )
                key_b = rate_limit_module._client_key(request_b, "cap-test")
                self.assertIn(key_a, rate_limit_module._BUCKETS)
                self.assertIn(key_b, rate_limit_module._BUCKETS)

                rate_limit_module.enforce_rate_limit(
                    request_c,
                    bucket="cap-test",
                    limit=10,
                    window_seconds=60,
                )
                key_c = rate_limit_module._client_key(request_c, "cap-test")
                self.assertNotIn(key_a, rate_limit_module._BUCKETS)
                self.assertIn(key_b, rate_limit_module._BUCKETS)
                self.assertIn(key_c, rate_limit_module._BUCKETS)
                self.assertLessEqual(len(rate_limit_module._BUCKETS), 2)

                rate_limit_module.enforce_rate_limit(
                    request_c,
                    bucket="cap-test",
                    limit=10,
                    window_seconds=60,
                )
                self.assertIn(key_c, rate_limit_module._BUCKETS)
                self.assertNotIn(key_b, rate_limit_module._BUCKETS)
        finally:
            if old_max_buckets is None:
                os.environ.pop("RATE_LIMIT_MAX_BUCKETS", None)
            else:
                os.environ["RATE_LIMIT_MAX_BUCKETS"] = old_max_buckets

    def test_rate_limit_global_limit_is_enforced(self) -> None:
        old_global = os.environ.get("RATE_LIMIT_GLOBAL_LIMIT_CAP_TEST")
        os.environ["RATE_LIMIT_GLOBAL_LIMIT_CAP_TEST"] = "2"
        try:
            request_a = self._request_with_api_key("key-a")
            request_b = self._request_with_api_key("key-b")
            request_c = self._request_with_api_key("key-c")
            rate_limit_module.enforce_rate_limit(
                request_a,
                bucket="cap-test",
                limit=10,
                window_seconds=60,
            )
            rate_limit_module.enforce_rate_limit(
                request_b,
                bucket="cap-test",
                limit=10,
                window_seconds=60,
            )
            with self.assertRaises(HTTPException):
                rate_limit_module.enforce_rate_limit(
                    request_c,
                    bucket="cap-test",
                    limit=10,
                    window_seconds=60,
                )
        finally:
            if old_global is None:
                os.environ.pop("RATE_LIMIT_GLOBAL_LIMIT_CAP_TEST", None)
            else:
                os.environ["RATE_LIMIT_GLOBAL_LIMIT_CAP_TEST"] = old_global

    def test_client_error_report_redacts_logged_url_to_path_only(self) -> None:
        with patch("gardenops.main._error_logger.warning") as warning:
            response = self.client.post(
                "/api/client-errors",
                data=json.dumps(
                    {
                        "message": "frontend boom",
                        "type": "api_error",
                        "handled": True,
                        "status_code": 503,
                        "request_id": "upstream-request-42",
                        "api_path": "/api/plants?query=rose",
                        "stack": "Error: frontend boom\n    at garden.js:1:2",
                        "url": "https://example.test/invite/landing?invite=secret-token&x=1#invite=secret-token",
                    }
                ),
                headers={
                    "content-type": "application/json",
                    "x-request-id": "client-report-99",
                },
            )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers.get("x-request-id"), "client-report-99")
        warning.assert_called_once()
        extra = warning.call_args.kwargs["extra"]
        self.assertEqual(extra["path"], "/invite/landing")
        self.assertEqual(extra["api_path"], "/api/plants")
        self.assertEqual(extra["request_id"], "upstream-request-42")
        self.assertEqual(extra["report_request_id"], "client-report-99")
        self.assertEqual(extra["status_code"], 503)
        self.assertEqual(extra["error_kind"], "api_error")
        self.assertTrue(extra["handled"])
        self.assertIn("frontend boom", extra["client_stack"])

    def test_layered_rate_limit_enforces_user_scope(self) -> None:
        request_a = self._request_with_bearer("token-user-a")
        request_a.state.auth_context = AuthContext(
            user_id=77,
            username="u77",
            role="editor",
            auth_type="session",
            garden_id=11,
            garden_role="editor",
        )
        request_b = self._request_with_bearer("token-user-b")
        request_b.state.auth_context = AuthContext(
            user_id=77,
            username="u77",
            role="editor",
            auth_type="session",
            garden_id=11,
            garden_role="editor",
        )

        rate_limit_module.enforce_layered_rate_limit(
            request_a,
            bucket="layer-user-test",
            identity_limit=20,
            user_limit=1,
            garden_limit=10,
            global_limit=20,
            window_seconds=60,
        )
        with self.assertRaises(HTTPException) as exc:
            rate_limit_module.enforce_layered_rate_limit(
                request_b,
                bucket="layer-user-test",
                identity_limit=20,
                user_limit=1,
                garden_limit=10,
                global_limit=20,
                window_seconds=60,
            )
        self.assertIn("User rate limit exceeded", str(exc.exception.detail))

    def test_layered_rate_limit_enforces_garden_scope(self) -> None:
        request_a = self._request_with_bearer("token-garden-a")
        request_a.state.auth_context = AuthContext(
            user_id=81,
            username="u81",
            role="editor",
            auth_type="session",
            garden_id=13,
            garden_role="editor",
        )
        request_b = self._request_with_bearer("token-garden-b")
        request_b.state.auth_context = AuthContext(
            user_id=82,
            username="u82",
            role="editor",
            auth_type="session",
            garden_id=13,
            garden_role="editor",
        )

        rate_limit_module.enforce_layered_rate_limit(
            request_a,
            bucket="layer-garden-test",
            identity_limit=20,
            user_limit=20,
            garden_limit=1,
            global_limit=20,
            window_seconds=60,
        )
        with self.assertRaises(HTTPException) as exc:
            rate_limit_module.enforce_layered_rate_limit(
                request_b,
                bucket="layer-garden-test",
                identity_limit=20,
                user_limit=20,
                garden_limit=1,
                global_limit=20,
                window_seconds=60,
            )
        self.assertIn("Garden rate limit exceeded", str(exc.exception.detail))

    def test_layered_rate_limit_enforces_global_scope(self) -> None:
        request_a = self._request_with_bearer("token-global-a")
        request_a.state.auth_context = AuthContext(
            user_id=91,
            username="u91",
            role="editor",
            auth_type="session",
            garden_id=21,
            garden_role="editor",
        )
        request_b = self._request_with_bearer("token-global-b")
        request_b.state.auth_context = AuthContext(
            user_id=92,
            username="u92",
            role="editor",
            auth_type="session",
            garden_id=22,
            garden_role="editor",
        )

        rate_limit_module.enforce_layered_rate_limit(
            request_a,
            bucket="layer-global-test",
            identity_limit=20,
            user_limit=20,
            garden_limit=20,
            global_limit=1,
            window_seconds=60,
        )
        with self.assertRaises(HTTPException) as exc:
            rate_limit_module.enforce_layered_rate_limit(
                request_b,
                bucket="layer-global-test",
                identity_limit=20,
                user_limit=20,
                garden_limit=20,
                global_limit=1,
                window_seconds=60,
            )
        self.assertIn("Global rate limit exceeded", str(exc.exception.detail))

    def test_key_rate_limit_enforces_explicit_target_scope(self) -> None:
        rate_limit_module.enforce_key_rate_limit(
            bucket="key-limit-test",
            key="username:alice",
            limit=1,
            window_seconds=60,
            scope_label="Target",
        )
        with self.assertRaises(HTTPException) as exc:
            rate_limit_module.enforce_key_rate_limit(
                bucket="key-limit-test",
                key="username:alice",
                limit=1,
                window_seconds=60,
                scope_label="Target",
            )
        self.assertIn("Target rate limit exceeded", str(exc.exception.detail))

    def test_concurrency_slot_enforces_limit_and_releases(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def hold_slot() -> None:
            with rate_limit_module.acquire_concurrency_slot(bucket="concurrency-test", limit=1):
                entered.set()
                release.wait(timeout=2)

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(hold_slot)
            self.assertTrue(entered.wait(timeout=1))
            with self.assertRaises(HTTPException) as exc:
                with rate_limit_module.acquire_concurrency_slot(
                    bucket="concurrency-test",
                    limit=1,
                ):
                    pass
            self.assertIn("Concurrent request limit exceeded", str(exc.exception.detail))
            self.assertEqual(
                rate_limit_module.active_concurrency_snapshot().get("concurrency-test"),
                1,
            )
            release.set()
            future.result(timeout=2)

        self.assertNotIn("concurrency-test", rate_limit_module.active_concurrency_snapshot())

    def test_rate_limit_redis_backend_requires_explicit_url(self) -> None:
        old_backend = os.environ.get("RATE_LIMIT_BACKEND")
        old_url = os.environ.get("RATE_LIMIT_REDIS_URL")
        os.environ["RATE_LIMIT_BACKEND"] = "redis"
        os.environ["RATE_LIMIT_REDIS_URL"] = ""
        try:
            rate_limit_module.reset_rate_limits()
            with self.assertRaisesRegex(
                RuntimeError,
                "RATE_LIMIT_BACKEND=redis requires RATE_LIMIT_REDIS_URL or REDIS_URL",
            ):
                rate_limit_module._get_backend()
        finally:
            if old_backend is None:
                os.environ.pop("RATE_LIMIT_BACKEND", None)
            else:
                os.environ["RATE_LIMIT_BACKEND"] = old_backend
            if old_url is None:
                os.environ.pop("RATE_LIMIT_REDIS_URL", None)
            else:
                os.environ["RATE_LIMIT_REDIS_URL"] = old_url

    @patch("gardenops.rate_limit.RedisRateLimitBackend", side_effect=OSError("redis down"))
    def test_rate_limit_redis_backend_raises_when_unavailable(
        self,
        _mock_backend: MagicMock,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "RATE_LIMIT_BACKEND": "redis",
                "RATE_LIMIT_REDIS_URL": "redis://127.0.0.1:6379/0",
            },
            clear=False,
        ):
            rate_limit_module.reset_rate_limits()
            with self.assertRaisesRegex(
                RuntimeError,
                "RATE_LIMIT_BACKEND=redis but redis is unavailable",
            ):
                rate_limit_module._get_backend()

    def test_auth_required_blocks_read_without_api_key(self) -> None:
        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_API_KEY": "secret-test-key", "AUTH_MODE": "api_key"},
            clear=False,
        ):
            denied = self.client.get("/api/plots")
            self.assertEqual(denied.status_code, 401)

            allowed = self.client.get(
                "/api/plots",
                headers={"x-api-key": "secret-test-key"},
            )
            self.assertEqual(allowed.status_code, 200)

    def test_auth_required_allows_cors_preflight_without_api_key(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_API_KEY": "secret-test-key",
                "AUTH_MODE": "api_key",
            },
            clear=False,
        ):
            client = self._new_client()
            response = client.options(
                "/api/plots",
                headers={
                    "Host": "localhost",
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "x-api-key",
                },
            )
        self.assertEqual(response.status_code, 200)

    def test_session_auth_bootstrap_login_and_me(self) -> None:
        conn = db.get_db()
        try:
            conn.execute("DELETE FROM garden_memberships")
            conn.execute("DELETE FROM auth_users")
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
            clear=False,
        ):
            status_before = self.client.get("/api/auth/status")
            self.assertEqual(status_before.status_code, 200)
            self.assertTrue(status_before.json()["bootstrap_required"])

            bootstrap = self.client.post(
                "/api/auth/bootstrap",
                json={
                    "username": "owner",
                    "password": strong_password("supersecret123"),
                    "role": "admin",
                },
            )
            self.assertEqual(bootstrap.status_code, 201)

            login = self.client.post(
                "/api/auth/login",
                json={"username": "owner", "password": strong_password("supersecret123")},
            )
            self.assertEqual(login.status_code, 200)

            me = self.client.get("/api/auth/me")
            self.assertEqual(me.status_code, 200)
            self.assertEqual(me.json()["username"], "owner")
            self.assertEqual(me.json()["role"], "admin")
            self.assertEqual(me.json()["auth_type"], "session")
            self.assertTrue(me.json()["write_access"])
            self.assertIn("garden_id", me.json())
            self.assertEqual(me.json()["garden_role"], "admin")

    def test_auth_login_applies_stricter_admin_target_limit(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="strict_admin_login",
                password=strong_password("adminpass123"),
                role="admin",
            )
            create_user(
                conn,
                username="normal_editor_login",
                password=strong_password("editorpass123"),
                role="editor",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "AUTH_LOGIN_RATE_LIMIT": "50",
                "AUTH_LOGIN_USERNAME_RATE_LIMIT": "10",
                "AUTH_LOGIN_ADMIN_USERNAME_RATE_LIMIT": "1",
                "AUTH_LOGIN_ADMIN_HOST_RATE_LIMIT": "50",
            },
            clear=False,
        ):
            admin_first = self.client.post(
                "/api/auth/login",
                json={"username": "strict_admin_login", "password": strong_password("wrong-pass")},
            )
            admin_second = self.client.post(
                "/api/auth/login",
                json={"username": "strict_admin_login", "password": strong_password("wrong-pass")},
            )
            editor_first = self.client.post(
                "/api/auth/login",
                json={"username": "normal_editor_login", "password": strong_password("wrong-pass")},
            )
            editor_second = self.client.post(
                "/api/auth/login",
                json={"username": "normal_editor_login", "password": strong_password("wrong-pass")},
            )

        self.assertEqual(admin_first.status_code, 401)
        self.assertEqual(admin_second.status_code, 429)
        self.assertEqual(editor_first.status_code, 401)
        self.assertEqual(editor_second.status_code, 401)

    def test_auth_login_adaptive_friction_hook_can_be_required(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="friction_login_user",
                password=strong_password("frictionpass123"),
                role="viewer",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "AUTH_ADAPTIVE_FRICTION_MODE": "require",
                "AUTH_ADAPTIVE_FRICTION_FLOWS": "login",
            },
            clear=False,
        ):
            blocked = self.client.post(
                "/api/auth/login",
                json={
                    "username": "friction_login_user",
                    "password": strong_password("frictionpass123"),
                },
            )
            allowed = self.client.post(
                "/api/auth/login",
                json={
                    "username": "friction_login_user",
                    "password": strong_password("frictionpass123"),
                    "friction_provider": "captcha",
                    "friction_token": "verified-token",
                },
            )

        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json()["detail"], "Additional verification required")
        self.assertEqual(allowed.status_code, 200)

    def test_session_cookie_auth_me_without_bearer_header(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
            clear=False,
        ):
            login = self.client.post(
                "/api/auth/login",
                json={"username": "test_admin", "password": strong_password("testadminpass")},
            )
            self.assertEqual(login.status_code, 200)
            payload = login.json()
            self.assertEqual(payload.get("status"), "ok")
            self.assertNotIn("access_token", payload)
            self.assertNotIn("csrf_token", payload)
            self.assertTrue(self.client.cookies.get("gardenops_csrf"))

            me = self.client.get("/api/auth/me")
            self.assertEqual(me.status_code, 200)
            self.assertEqual(me.json()["username"], "test_admin")
            self.assertEqual(me.json()["auth_type"], "session")

    def test_cookie_session_mutation_requires_valid_csrf_token(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
            clear=False,
        ):
            login = self.client.post(
                "/api/auth/login",
                json={"username": "test_admin", "password": strong_password("testadminpass")},
            )
            self.assertEqual(login.status_code, 200)
            csrf_token = self.client.cookies.get("gardenops_csrf", "")
            self.assertTrue(csrf_token)

            denied_missing = self.client.post("/api/snapshots", json={"name": "csrf-missing"})
            self.assertEqual(denied_missing.status_code, 403)
            self.assertIn("CSRF", denied_missing.json().get("detail", ""))

            denied_invalid = self.client.post(
                "/api/snapshots",
                headers={"x-csrf-token": "invalid-token"},
                json={"name": "csrf-invalid"},
            )
            self.assertEqual(denied_invalid.status_code, 403)

            allowed = self.client.post(
                "/api/snapshots",
                headers={"x-csrf-token": csrf_token},
                json={"name": "csrf-ok"},
            )
            self.assertEqual(allowed.status_code, 201)

    def test_session_bearer_header_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
            clear=False,
        ):
            login = self.client.post(
                "/api/auth/login",
                json={"username": "test_admin", "password": strong_password("testadminpass")},
            )
            self.assertEqual(login.status_code, 200)
            session_token = self.client.cookies.get("gardenops_session", "")
            self.assertTrue(session_token)

            stateless_client = self._new_client()
            response = stateless_client.patch(
                "/api/layout-state",
                headers={"authorization": f"Bearer {session_token}"},
                json={"row": 9, "col": 6, "width": 12, "height": 8, "north_degrees": 1},
            )
            self.assertEqual(response.status_code, 401)

    def test_user_lifecycle_schema_v1_exists(self) -> None:
        conn = db.get_db()
        try:
            auth_user_cols = {
                str(row["column_name"])
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'auth_users'"
                ).fetchall()
            }
            self.assertTrue(
                {
                    "created_by_user_id",
                    "must_change_password",
                    "deactivated_at",
                    "deactivated_reason",
                }.issubset(auth_user_cols),
            )

            reset_cols = {
                str(row["column_name"])
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'auth_password_reset_tokens'"
                ).fetchall()
            }
            self.assertTrue(
                {
                    "id",
                    "token_hash",
                    "user_id",
                    "created_by_user_id",
                    "created_at_ms",
                    "expires_at_ms",
                    "used_at_ms",
                    "used_by_user_id",
                }.issubset(reset_cols),
            )
            reset_idx = {
                str(row["indexname"])
                for row in conn.execute(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename = 'auth_password_reset_tokens'"
                ).fetchall()
            }
            self.assertIn("idx_auth_pw_reset_user", reset_idx)
            self.assertIn("idx_auth_pw_reset_expires", reset_idx)

            invitation_cols = {
                str(row["column_name"])
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'garden_invitations'"
                ).fetchall()
            }
            self.assertTrue(
                {
                    "id",
                    "garden_id",
                    "invitee_username",
                    "role",
                    "token_hash",
                    "created_by_user_id",
                    "created_at_ms",
                    "expires_at_ms",
                    "accepted_at_ms",
                    "accepted_user_id",
                    "revoked_at_ms",
                }.issubset(invitation_cols),
            )
            invitation_idx = {
                str(row["indexname"])
                for row in conn.execute(
                    "SELECT indexname FROM pg_indexes WHERE tablename = 'garden_invitations'"
                ).fetchall()
            }
            self.assertIn("idx_garden_invitations_garden", invitation_idx)
            self.assertIn("idx_garden_invitations_invitee", invitation_idx)
        finally:
            db.return_db(conn)

    def test_session_viewer_cannot_mutate(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="viewer1",
                password=strong_password("viewerpass123"),
                role="viewer",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
            clear=False,
        ):
            _, csrf = self._login_session("viewer1", "viewerpass123")

            read_ok = self.client.get("/api/plots")
            self.assertEqual(read_ok.status_code, 200)

            denied = self.client.patch(
                "/api/layout-state",
                headers=self._session_headers(csrf),
                json={"row": 9, "col": 6, "width": 12, "height": 8, "north_degrees": 0},
            )
            self.assertEqual(denied.status_code, 403)

    def test_session_editor_with_viewer_garden_role_cannot_mutate(self) -> None:
        conn = db.get_db()
        try:
            created = create_user(
                conn,
                username="editor_viewer_mix",
                password=strong_password("editorviewerpass123"),
                role="editor",
            )
            conn.execute(
                "UPDATE garden_memberships SET role = 'viewer' WHERE user_id = %s",
                (int(created["id"]),),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
            clear=False,
        ):
            _, csrf = self._login_session("editor_viewer_mix", "editorviewerpass123")

            me = self.client.get("/api/auth/me")
            self.assertEqual(me.status_code, 200)
            self.assertEqual(me.json()["role"], "editor")
            self.assertEqual(me.json()["garden_role"], "viewer")
            self.assertFalse(me.json()["write_access"])

            denied = self.client.patch(
                "/api/layout-state",
                headers=self._session_headers(csrf),
                json={"row": 9, "col": 6, "width": 12, "height": 8, "north_degrees": 0},
            )
            self.assertEqual(denied.status_code, 403)

    def test_auth_admin_user_management_create_list_patch_and_audit(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="admin_users_api",
                password=strong_password("adminpass123"),
                role="admin",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
            clear=False,
        ):
            _, csrf = self._login_session("admin_users_api", "adminpass123")
            headers = self._session_headers(csrf)

            created = self.client.post(
                "/api/auth/users",
                headers=headers,
                json={
                    "username": "managed_user_1",
                    "password": strong_password("managedpass123"),
                    "role": "viewer",
                    "must_change_password": True,
                    "action_reason": "managed-user-create-test",
                },
            )
            self.assertEqual(created.status_code, 201)
            created_body = created.json()
            self.assertEqual(created_body["username"], "managed_user_1")
            self.assertEqual(created_body["role"], "viewer")
            self.assertTrue(created_body["must_change_password"])
            self.assertTrue(created_body["is_active"])
            managed_user_id = int(created_body["id"])

            listed = self.client.get("/api/auth/users", headers=headers)
            self.assertEqual(listed.status_code, 200)
            users = listed.json()["users"]
            user_row = next(user for user in users if user["id"] == managed_user_id)
            self.assertEqual(user_row["username"], "managed_user_1")
            self.assertEqual(user_row["role"], "viewer")

            updated = self.client.patch(
                f"/api/auth/users/{managed_user_id}",
                headers=headers,
                json={
                    "role": "editor",
                    "is_active": False,
                    "must_change_password": False,
                    "deactivated_reason": "maintenance lock",
                    "action_reason": "managed-user-update-test",
                },
            )
            self.assertEqual(updated.status_code, 200)
            updated_body = updated.json()
            self.assertEqual(updated_body["role"], "editor")
            self.assertFalse(updated_body["is_active"])
            self.assertEqual(updated_body["deactivated_reason"], "maintenance lock")

            conn = db.get_db()
            try:
                create_audit = conn.execute(
                    """
                    SELECT detail
                    FROM audit_events
                    WHERE detail LIKE 'auth.user.create %'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                ).fetchone()
                self.assertIsNotNone(create_audit)
                update_audit = conn.execute(
                    """
                    SELECT detail
                    FROM audit_events
                    WHERE detail LIKE 'auth.user.update %'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                ).fetchone()
                self.assertIsNotNone(update_audit)
            finally:
                db.return_db(conn)

    def test_auth_user_management_requires_admin_role(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="admin_users_guard",
                password=strong_password("adminpass123"),
                role="admin",
            )
            create_user(
                conn,
                username="viewer_users_guard",
                password=strong_password("viewerpass123"),
                role="viewer",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
            clear=False,
        ):
            _, viewer_csrf = self._login_session("viewer_users_guard", "viewerpass123")
            viewer_headers = self._session_headers(viewer_csrf)

            denied_list = self.client.get("/api/auth/users")
            self.assertEqual(denied_list.status_code, 403)

            denied_create = self.client.post(
                "/api/auth/users",
                headers=viewer_headers,
                json={
                    "username": "should-not-create",
                    "password": strong_password("shouldnotpass123"),
                    "role": "viewer",
                },
            )
            self.assertEqual(denied_create.status_code, 403)

            denied_patch = self.client.patch(
                "/api/auth/users/999",
                headers=viewer_headers,
                json={"role": "editor"},
            )
            self.assertEqual(denied_patch.status_code, 403)

    def test_auth_user_management_blocks_last_active_admin_demotion_or_deactivation(self) -> None:
        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE auth_users SET is_active = 0 WHERE username = %s",
                ("test_admin",),
            )
            admin = create_user(
                conn,
                username="last_admin_guard",
                password=strong_password("adminpass123"),
                role="admin",
            )
            conn.commit()
            admin_user_id = int(admin["id"])
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
            clear=False,
        ):
            _, csrf = self._login_session("last_admin_guard", "adminpass123")
            headers = self._session_headers(csrf)

            demote = self.client.patch(
                f"/api/auth/users/{admin_user_id}",
                headers=headers,
                json={"role": "editor", "action_reason": "last-admin-demote-test"},
            )
            self.assertEqual(demote.status_code, 409)
            self.assertIn("last active admin", demote.json()["detail"])

            deactivate = self.client.patch(
                f"/api/auth/users/{admin_user_id}",
                headers=headers,
                json={
                    "is_active": False,
                    "deactivated_reason": "test",
                    "action_reason": "last-admin-deactivate-test",
                },
            )
            self.assertEqual(deactivate.status_code, 409)
            self.assertIn("last active admin", deactivate.json()["detail"])

    def test_password_policy_is_enforced_for_bootstrap_and_admin_create(self) -> None:
        conn = db.get_db()
        try:
            conn.execute("DELETE FROM garden_memberships")
            conn.execute("DELETE FROM auth_users")
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "AUTH_PASSWORD_MIN_LENGTH": "12",
            },
            clear=False,
        ):
            bootstrap_short = self.client.post(
                "/api/auth/bootstrap",
                json={"username": "policy_admin", "password": "short-pass", "role": "admin"},
            )
            self.assertEqual(bootstrap_short.status_code, 400)
            self.assertIn("at least 12", bootstrap_short.json()["detail"])

            bootstrap_ok = self.client.post(
                "/api/auth/bootstrap",
                json={
                    "username": "policy_admin",
                    "password": strong_password("policy-admin-pass"),
                    "role": "admin",
                },
            )
            self.assertEqual(bootstrap_ok.status_code, 201)

            _, csrf = self._login_session("policy_admin", "policy-admin-pass")
            headers = self._session_headers(csrf)

            create_short = self.client.post(
                "/api/auth/users",
                headers=headers,
                json={
                    "username": "policy_target",
                    "password": "short-pass",
                    "role": "viewer",
                    "action_reason": "password-policy-create-test",
                },
            )
            self.assertEqual(create_short.status_code, 400)
            self.assertIn("at least 12", create_short.json()["detail"])

    def test_auth_change_password_revokes_other_sessions(self) -> None:
        conn = db.get_db()
        try:
            created = create_user(
                conn,
                username="change_pw_admin",
                password=strong_password("old-password-123"),
                role="admin",
                must_change_password=True,
            )
            conn.commit()
            user_id = int(created["id"])
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "AUTH_PASSWORD_MIN_LENGTH": "12",
            },
            clear=False,
        ):
            client_one = self._new_client()
            client_two = self._new_client()
            _, csrf_one = self._login_session(
                "change_pw_admin",
                "old-password-123",
                client=client_one,
            )
            self._login_session(
                "change_pw_admin",
                "old-password-123",
                client=client_two,
            )

            short_change = client_one.post(
                "/api/auth/change-password",
                headers=self._session_headers(csrf_one),
                json={
                    "current_password": strong_password("old-password-123"),
                    "new_password": "short-pass",
                },
            )
            self.assertEqual(short_change.status_code, 400)
            self.assertIn("at least 12", short_change.json()["detail"])

            changed = client_one.post(
                "/api/auth/change-password",
                headers=self._session_headers(csrf_one),
                json={
                    "current_password": strong_password("old-password-123"),
                    "new_password": strong_password("new-password-12345"),
                },
            )
            self.assertEqual(changed.status_code, 200)
            self.assertGreaterEqual(int(changed.json()["revoked_sessions"]), 1)

            stale_session = client_two.get("/api/auth/me")
            self.assertEqual(stale_session.status_code, 401)

            old_login = self.client.post(
                "/api/auth/login",
                json={
                    "username": "change_pw_admin",
                    "password": strong_password("old-password-123"),
                },
            )
            self.assertEqual(old_login.status_code, 401)

            new_login = self.client.post(
                "/api/auth/login",
                json={
                    "username": "change_pw_admin",
                    "password": strong_password("new-password-12345"),
                },
            )
            self.assertEqual(new_login.status_code, 200)

        conn = db.get_db()
        try:
            user_row = conn.execute(
                "SELECT must_change_password FROM auth_users WHERE id = %s",
                (user_id,),
            ).fetchone()
            self.assertIsNotNone(user_row)
            assert user_row is not None
            self.assertEqual(int(user_row["must_change_password"]), 0)
        finally:
            db.return_db(conn)

    def test_forced_password_change_blocks_normal_api_until_changed(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="forced_pw_user",
                password=strong_password("old-forced-password"),
                role="editor",
                must_change_password=True,
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "AUTH_PASSWORD_MIN_LENGTH": "12",
            },
            clear=False,
        ):
            client = self._new_client()
            login = client.post(
                "/api/auth/login",
                json={
                    "username": "forced_pw_user",
                    "password": strong_password("old-forced-password"),
                },
            )
            self.assertEqual(login.status_code, 200)
            self.assertEqual(login.json()["status"], "password_change_required")
            self.assertTrue(login.json()["user"]["must_change_password"])
            csrf = client.cookies.get("gardenops_csrf", "")
            self.assertTrue(csrf)
            headers = self._session_headers(csrf)

            me_before = client.get("/api/auth/me", headers=headers)
            self.assertEqual(me_before.status_code, 200)
            self.assertTrue(me_before.json()["must_change_password"])

            blocked = client.get("/api/plots", headers=headers)
            self.assertEqual(blocked.status_code, 403)
            self.assertEqual(
                blocked.json()["detail"],
                "Password change is required before full access",
            )

            changed = client.post(
                "/api/auth/change-password",
                headers=headers,
                json={
                    "current_password": strong_password("old-forced-password"),
                    "new_password": strong_password("new-forced-password"),
                },
            )
            self.assertEqual(changed.status_code, 200)

            me_after = client.get("/api/auth/me", headers=headers)
            self.assertEqual(me_after.status_code, 200)
            self.assertFalse(me_after.json()["must_change_password"])

            allowed = client.get("/api/plots", headers=headers)
            self.assertEqual(allowed.status_code, 200)

    def test_admin_setting_must_change_password_revokes_existing_sessions(self) -> None:
        conn = db.get_db()
        try:
            target = create_user(
                conn,
                username="force_flag_target",
                password=strong_password("target-password-123"),
                role="editor",
            )
            create_user(
                conn,
                username="force_flag_admin",
                password=strong_password("admin-password-123"),
                role="admin",
            )
            conn.commit()
            target_id = int(target["id"])
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
            clear=False,
        ):
            target_client = self._new_client()
            self._login_session(
                "force_flag_target",
                "target-password-123",
                client=target_client,
            )
            admin_client = self._new_client()
            _, admin_csrf = self._login_session(
                "force_flag_admin",
                "admin-password-123",
                client=admin_client,
            )
            admin_headers = self._session_headers(admin_csrf)

            updated = admin_client.patch(
                f"/api/auth/users/{target_id}",
                headers=admin_headers,
                json={
                    "must_change_password": True,
                    "action_reason": "force-password-change-test",
                },
            )
            self.assertEqual(updated.status_code, 200)
            self.assertTrue(updated.json()["must_change_password"])

            stale = target_client.get("/api/auth/me")
            self.assertEqual(stale.status_code, 401)

    def test_auth_issue_reset_and_reset_password_revokes_sessions_and_blocks_replay(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="reset_admin",
                password=strong_password("admin-password-123"),
                role="admin",
            )
            target = create_user(
                conn,
                username="reset_target",
                password=strong_password("target-password-123"),
                role="viewer",
            )
            conn.commit()
            target_id = int(target["id"])
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "AUTH_PASSWORD_MIN_LENGTH": "12",
            },
            clear=False,
        ):
            admin_client = self._new_client()
            target_client = self._new_client()
            _, admin_csrf = self._login_session(
                "reset_admin",
                "admin-password-123",
                client=admin_client,
            )
            admin_headers = self._session_headers(admin_csrf)
            self._login_session(
                "reset_target",
                "target-password-123",
                client=target_client,
            )

            issued = admin_client.post(
                f"/api/auth/users/{target_id}/issue-reset",
                headers=admin_headers,
                json={
                    "expires_in_minutes": 30,
                    "must_change_password": True,
                    "action_reason": "issue-reset-token-test",
                },
            )
            self.assertEqual(issued.status_code, 200)
            issued_body = issued.json()
            reset_token = issued_body["reset_token"]
            self.assertTrue(reset_token)
            self.assertEqual(issued_body["user_id"], target_id)
            self.assertTrue(issued_body["must_change_password"])
            self.assertGreaterEqual(int(issued_body["revoked_sessions"]), 1)

            conn = db.get_db()
            try:
                pre_reset_user = conn.execute(
                    "SELECT must_change_password FROM auth_users WHERE id = %s",
                    (target_id,),
                ).fetchone()
                self.assertIsNotNone(pre_reset_user)
                assert pre_reset_user is not None
                self.assertEqual(int(pre_reset_user["must_change_password"]), 1)
            finally:
                db.return_db(conn)

            short_reset = self.client.post(
                "/api/auth/reset-password",
                json={"token": reset_token, "new_password": "short-pass"},
            )
            self.assertEqual(short_reset.status_code, 400)
            self.assertIn("at least 12", short_reset.json()["detail"])

            stale_after_issue = target_client.get("/api/auth/me")
            self.assertEqual(stale_after_issue.status_code, 401)

            reset_ok = self.client.post(
                "/api/auth/reset-password",
                json={"token": reset_token, "new_password": strong_password("target-password-999")},
            )
            self.assertEqual(reset_ok.status_code, 200)
            self.assertGreaterEqual(int(reset_ok.json()["revoked_sessions"]), 0)

            stale_session = target_client.get("/api/auth/me")
            self.assertEqual(stale_session.status_code, 401)

            replay = self.client.post(
                "/api/auth/reset-password",
                json={"token": reset_token, "new_password": strong_password("target-password-777")},
            )
            self.assertEqual(replay.status_code, 400)
            self.assertEqual(replay.json()["detail"], "Invalid or expired reset token")

            old_login = self.client.post(
                "/api/auth/login",
                json={
                    "username": "reset_target",
                    "password": strong_password("target-password-123"),
                },
            )
            self.assertEqual(old_login.status_code, 401)

            new_login = self.client.post(
                "/api/auth/login",
                json={
                    "username": "reset_target",
                    "password": strong_password("target-password-999"),
                },
            )
            self.assertEqual(new_login.status_code, 200)

        conn = db.get_db()
        try:
            post_reset_user = conn.execute(
                "SELECT must_change_password FROM auth_users WHERE id = %s",
                (target_id,),
            ).fetchone()
            self.assertIsNotNone(post_reset_user)
            assert post_reset_user is not None
            self.assertEqual(int(post_reset_user["must_change_password"]), 0)

            used_token = conn.execute(
                """
                SELECT used_at_ms
                FROM auth_password_reset_tokens
                WHERE user_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (target_id,),
            ).fetchone()
            self.assertIsNotNone(used_token)
            assert used_token is not None
            self.assertIsNotNone(used_token["used_at_ms"])
        finally:
            db.return_db(conn)

    def test_passwordless_user_requires_explicit_recovery_reset_purpose(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="passwordless_reset_admin",
                password=strong_password("admin-password-123"),
                role="admin",
            )
            target = create_user(
                conn,
                username="passwordless_reset_target",
                password=None,  # type: ignore[arg-type]
                role="viewer",
                password_auth_disabled=True,
            )
            now_ms = db.current_timestamp_ms()
            conn.execute(
                """
                INSERT INTO auth_passkeys (
                    user_id,
                    credential_id,
                    credential_public_key,
                    sign_count,
                    nickname,
                    transports,
                    credential_device_type,
                    credential_backed_up,
                    created_at_ms,
                    updated_at_ms,
                    last_used_at_ms
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
                """,
                (
                    int(target["id"]),
                    passkey_service.encode_public_key(b"passwordless-reset-credential"),
                    passkey_service.encode_public_key(b"public-key"),
                    1,
                    "Phone",
                    "internal",
                    "multi_device",
                    1,
                    now_ms,
                    now_ms,
                ),
            )
            conn.commit()
            target_id = int(target["id"])
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
            clear=False,
        ):
            _, admin_csrf = self._login_session(
                "passwordless_reset_admin",
                "admin-password-123",
            )
            admin_headers = self._session_headers(admin_csrf)

            issued_default = self.client.post(
                f"/api/auth/users/{target_id}/issue-reset",
                headers=admin_headers,
                json={"action_reason": "default-reset-passwordless-test"},
            )
            self.assertEqual(issued_default.status_code, 200, issued_default.text)
            blocked = self.client.post(
                "/api/auth/reset-password",
                json={
                    "token": issued_default.json()["reset_token"],
                    "new_password": strong_password("restored-password-123"),
                },
            )
            self.assertEqual(blocked.status_code, 400)
            self.assertEqual(
                blocked.json()["detail"],
                "Password reset is unavailable for this account",
            )

            issued_recovery = self.client.post(
                f"/api/auth/users/{target_id}/issue-reset",
                headers=admin_headers,
                json={
                    "purpose": "passwordless_recovery",
                    "action_reason": "passwordless-recovery-test",
                },
            )
            self.assertEqual(issued_recovery.status_code, 200, issued_recovery.text)
            self.assertEqual(issued_recovery.json()["purpose"], "passwordless_recovery")
            recovered = self.client.post(
                "/api/auth/reset-password",
                json={
                    "token": issued_recovery.json()["reset_token"],
                    "new_password": strong_password("restored-password-123"),
                },
            )
            self.assertEqual(recovered.status_code, 200, recovered.text)
            self.assertEqual(recovered.json()["revoked_passkeys"], 1)

            login = self.client.post(
                "/api/auth/login",
                json={
                    "username": "passwordless_reset_target",
                    "password": strong_password("restored-password-123"),
                },
            )
            self.assertEqual(login.status_code, 200, login.text)

        conn = db.get_db()
        try:
            row = conn.execute(
                """
                SELECT password_hash, password_auth_disabled
                FROM auth_users
                WHERE id = %s
                """,
                (target_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertIsNotNone(row["password_hash"])
            self.assertEqual(int(row["password_auth_disabled"]), 0)
            passkey_count = conn.execute(
                "SELECT COUNT(*) AS count FROM auth_passkeys WHERE user_id = %s",
                (target_id,),
            ).fetchone()
            self.assertEqual(int(passkey_count["count"]), 0)
            audit = conn.execute(
                """
                SELECT detail
                FROM audit_events
                WHERE detail LIKE 'auth.user.passwordless-recovery-reset %'
                ORDER BY id DESC
                LIMIT 1
                """,
            ).fetchone()
            self.assertIsNotNone(audit)
        finally:
            db.return_db(conn)

    def test_passwordless_recovery_token_requires_passwordless_target(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="recovery_scope_admin",
                password=strong_password("admin-password-123"),
                role="admin",
            )
            target = create_user(
                conn,
                username="recovery_scope_target",
                password=strong_password("target-password-123"),
                role="viewer",
            )
            conn.commit()
            target_id = int(target["id"])
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
            clear=False,
        ):
            _, admin_csrf = self._login_session("recovery_scope_admin", "admin-password-123")
            response = self.client.post(
                f"/api/auth/users/{target_id}/issue-reset",
                headers=self._session_headers(admin_csrf),
                json={
                    "purpose": "passwordless_recovery",
                    "action_reason": "passwordless-recovery-scope-test",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "Passwordless recovery is only available for passwordless accounts",
        )

    def test_admin_cannot_set_must_change_password_for_passwordless_user(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="passwordless_mcp_admin",
                password=strong_password("admin-password-123"),
                role="admin",
            )
            target = create_user(
                conn,
                username="passwordless_mcp_target",
                password=None,  # type: ignore[arg-type]
                role="viewer",
                password_auth_disabled=True,
            )
            conn.commit()
            target_id = int(target["id"])
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
            clear=False,
        ):
            _, admin_csrf = self._login_session("passwordless_mcp_admin", "admin-password-123")
            response = self.client.patch(
                f"/api/auth/users/{target_id}",
                headers=self._session_headers(admin_csrf),
                json={
                    "must_change_password": True,
                    "action_reason": "passwordless-must-change-test",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "Password change requirement is unavailable for passwordless accounts",
        )
        conn = db.get_db()
        try:
            row = conn.execute(
                "SELECT must_change_password FROM auth_users WHERE id = %s",
                (target_id,),
            ).fetchone()
            self.assertEqual(int(row["must_change_password"]), 0)
        finally:
            db.return_db(conn)

    def test_auth_reset_password_rejects_expired_token(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="expired_admin",
                password=strong_password("admin-password-123"),
                role="admin",
            )
            target = create_user(
                conn,
                username="expired_target",
                password=strong_password("target-password-123"),
                role="viewer",
            )
            conn.commit()
            target_id = int(target["id"])
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
            clear=False,
        ):
            _, admin_csrf = self._login_session("expired_admin", "admin-password-123")
            admin_headers = self._session_headers(admin_csrf)

            issued = self.client.post(
                f"/api/auth/users/{target_id}/issue-reset",
                headers=admin_headers,
                json={
                    "expires_in_minutes": 30,
                    "action_reason": "expired-reset-token-test",
                },
            )
            self.assertEqual(issued.status_code, 200)
            reset_token = issued.json()["reset_token"]

        conn = db.get_db()
        try:
            conn.execute(
                """
                UPDATE auth_password_reset_tokens
                SET expires_at_ms = %s
                WHERE user_id = %s
                """,
                (db.current_timestamp_ms() - 1_000, target_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
            clear=False,
        ):
            expired = self.client.post(
                "/api/auth/reset-password",
                json={"token": reset_token, "new_password": strong_password("target-password-999")},
            )
            self.assertEqual(expired.status_code, 400)
            self.assertEqual(expired.json()["detail"], "Invalid or expired reset token")

    def test_invalid_reset_password_attempts_raise_alert_and_token_limit(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="reset_alert_admin",
                password=strong_password("adminpass123"),
                role="admin",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "AUTH_RESET_PASSWORD_RATE_LIMIT": "50",
                "AUTH_RESET_PASSWORD_TOKEN_RATE_LIMIT": "1",
                "ALERT_INVALID_RESET_PASSWORD_ATTEMPTS_PER_5M": "1",
            },
            clear=False,
        ):
            admin_client = self._new_client()
            _, admin_csrf = self._login_session(
                "reset_alert_admin",
                "adminpass123",
                client=admin_client,
            )
            admin_headers = self._session_headers(admin_csrf)

            first = self.client.post(
                "/api/auth/reset-password",
                json={
                    "token": "bad-reset-token-1234567890",
                    "new_password": strong_password("new-password-123"),
                },
            )
            second = self.client.post(
                "/api/auth/reset-password",
                json={
                    "token": "bad-reset-token-1234567890",
                    "new_password": strong_password("new-password-456"),
                },
            )
            alerts = admin_client.get("/api/auth/security-alerts", headers=admin_headers)

        self.assertEqual(first.status_code, 400)
        self.assertEqual(first.json()["detail"], "Invalid or expired reset token")
        self.assertEqual(second.status_code, 429)
        self.assertEqual(alerts.status_code, 200)
        alert_names = {alert["name"] for alert in alerts.json().get("alerts", [])}
        self.assertIn("invalid_reset_password_attempts_per_5m", alert_names)

    def test_auth_user_deactivation_revokes_sessions_and_blocks_login(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="deactivate_admin",
                password=strong_password("admin-password-123"),
                role="admin",
            )
            target = create_user(
                conn,
                username="deactivate_target",
                password=strong_password("target-password-123"),
                role="editor",
            )
            conn.commit()
            target_id = int(target["id"])
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            admin_client = self._new_client()
            target_client = self._new_client()
            _, admin_csrf = self._login_session(
                "deactivate_admin",
                "admin-password-123",
                client=admin_client,
            )
            admin_headers = self._session_headers(admin_csrf)
            self._login_session(
                "deactivate_target",
                "target-password-123",
                client=target_client,
            )

            deactivated = admin_client.patch(
                f"/api/auth/users/{target_id}",
                headers=admin_headers,
                json={
                    "is_active": False,
                    "deactivated_reason": "security incident",
                    "action_reason": "deactivate-user-test",
                },
            )
            self.assertEqual(deactivated.status_code, 200)
            self.assertFalse(deactivated.json()["is_active"])

            stale_session = target_client.get("/api/auth/me")
            self.assertEqual(stale_session.status_code, 401)

            blocked_login = self.client.post(
                "/api/auth/login",
                json={
                    "username": "deactivate_target",
                    "password": strong_password("target-password-123"),
                },
            )
            self.assertEqual(blocked_login.status_code, 401)

            reactivated = admin_client.patch(
                f"/api/auth/users/{target_id}",
                headers=admin_headers,
                json={"is_active": True, "action_reason": "reactivate-user-test"},
            )
            self.assertEqual(reactivated.status_code, 200)
            self.assertTrue(reactivated.json()["is_active"])

            login_after = self.client.post(
                "/api/auth/login",
                json={
                    "username": "deactivate_target",
                    "password": strong_password("target-password-123"),
                },
            )
            self.assertEqual(login_after.status_code, 200)

    def test_auth_revoke_user_sessions_by_id_endpoint(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="revoke_admin",
                password=strong_password("admin-password-123"),
                role="admin",
            )
            target = create_user(
                conn,
                username="revoke_target",
                password=strong_password("target-password-123"),
                role="viewer",
            )
            conn.commit()
            target_id = int(target["id"])
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            admin_client = self._new_client()
            target_client_1 = self._new_client()
            target_client_2 = self._new_client()
            target_client_3 = self._new_client()
            _, admin_csrf = self._login_session(
                "revoke_admin",
                "admin-password-123",
                client=admin_client,
            )
            admin_headers = self._session_headers(admin_csrf)
            self._login_session(
                "revoke_target",
                "target-password-123",
                client=target_client_1,
            )
            self._login_session(
                "revoke_target",
                "target-password-123",
                client=target_client_2,
            )

            revoked = admin_client.post(
                f"/api/auth/users/{target_id}/revoke-sessions",
                headers=admin_headers,
                json={"action_reason": "test-revoke"},
            )
            self.assertEqual(revoked.status_code, 200)
            self.assertGreaterEqual(int(revoked.json()["revoked_sessions"]), 2)

            stale_one = target_client_1.get("/api/auth/me")
            self.assertEqual(stale_one.status_code, 401)
            stale_two = target_client_2.get("/api/auth/me")
            self.assertEqual(stale_two.status_code, 401)

            _, target_csrf = self._login_session(
                "revoke_target",
                "target-password-123",
                client=target_client_3,
            )
            target_headers = self._session_headers(target_csrf)
            denied = target_client_3.post(
                f"/api/auth/users/{target_id}/revoke-sessions",
                headers=target_headers,
                json={"action_reason": "test-revoke-denied"},
            )
            self.assertEqual(denied.status_code, 403)

    def test_auth_invitation_accept_creates_user_and_membership(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="accept_admin",
                password=strong_password("admin-password-123"),
                role="admin",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session("accept_admin", "admin-password-123")
            headers = self._session_headers(csrf)

            garden_slug = f"accept-garden-{os.urandom(4).hex()}"
            created_garden = self.client.post(
                "/api/gardens",
                headers=headers,
                json={"name": "Accept Garden", "slug": garden_slug},
            )
            self.assertEqual(created_garden.status_code, 201)
            garden_id = int(created_garden.json()["id"])

            headers = self._reauth_and_refresh_headers(
                self.client,
                headers,
                password=strong_password("admin-password-123"),
            )
            invitation = self.client.post(
                f"/api/gardens/{garden_id}/invitations",
                headers=headers,
                json={
                    "invitee_username": "accept_new_user",
                    "role": "viewer",
                    "action_reason": "accept-new-user-invitation-test",
                },
            )
            self.assertEqual(invitation.status_code, 201)
            invite_token = invitation.json()["invite_token"]

            accepted = self.client.post(
                "/api/auth/invitations/accept",
                json={"token": invite_token, "password": strong_password("accept-password-123")},
            )
            self.assertEqual(accepted.status_code, 200)
            accepted_body = accepted.json()
            self.assertTrue(accepted_body["created_user"])
            self.assertEqual(int(accepted_body["garden_id"]), garden_id)
            self.assertEqual(accepted_body["role"], "viewer")
            self.assertEqual(accepted_body["username"], "accept_new_user")
            self.assertEqual(accepted_body["invitation_scope"], "garden")

            invited_login = self.client.post(
                "/api/auth/login",
                json={
                    "username": "accept_new_user",
                    "password": strong_password("accept-password-123"),
                },
            )
            self.assertEqual(invited_login.status_code, 200)

            replay = self.client.post(
                "/api/auth/invitations/accept",
                json={"token": invite_token, "password": strong_password("accept-password-123")},
            )
            self.assertEqual(replay.status_code, 400)
            self.assertEqual(replay.json()["detail"], "Invalid or expired invitation token")

        conn = db.get_db()
        try:
            user_row = conn.execute(
                "SELECT id FROM auth_users WHERE username = 'accept_new_user'",
            ).fetchone()
            self.assertIsNotNone(user_row)
            assert user_row is not None
            user_id = int(user_row["id"])

            # Garden invitations grant membership to that specific garden.
            membership = conn.execute(
                """
                SELECT role
                FROM garden_memberships
                WHERE garden_id = %s AND user_id = %s
                """,
                (garden_id, user_id),
            ).fetchone()
            self.assertIsNotNone(membership)
            assert membership is not None
            self.assertEqual(str(membership["role"]), "viewer")

            # New garden invitees stay viewer-scoped globally.
            user_detail = conn.execute(
                "SELECT role FROM auth_users WHERE id = %s",
                (user_id,),
            ).fetchone()
            self.assertIsNotNone(user_detail)
            assert user_detail is not None
            self.assertEqual(str(user_detail["role"]), "viewer")

            invite_row = conn.execute(
                """
                SELECT accepted_at_ms, accepted_user_id
                FROM garden_invitations
                WHERE invitee_username = 'accept_new_user'
                ORDER BY id DESC
                LIMIT 1
                """,
            ).fetchone()
            self.assertIsNotNone(invite_row)
            assert invite_row is not None
            self.assertIsNotNone(invite_row["accepted_at_ms"])
            self.assertEqual(int(invite_row["accepted_user_id"]), user_id)

            audit_row = conn.execute(
                """
                SELECT garden_id
                FROM audit_events
                WHERE detail LIKE 'auth.invitation.accept %'
                ORDER BY id DESC
                LIMIT 1
                """,
            ).fetchone()
            self.assertIsNotNone(audit_row)
            assert audit_row is not None
            self.assertEqual(int(audit_row["garden_id"]), garden_id)
        finally:
            db.return_db(conn)

    def test_auth_user_invitation_accept_creates_editor_without_membership(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="personal_invite_admin",
                password=strong_password("admin-password-123"),
                role="admin",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session("personal_invite_admin", "admin-password-123")
            headers = self._session_headers(csrf)
            invitation = self.client.post(
                "/api/auth/user-invitations",
                headers=headers,
                json={
                    "invitee_username": "own_garden_editor",
                    "role": "editor",
                    "action_reason": "personal-user-invitation-test",
                },
            )
            self.assertEqual(invitation.status_code, 201)
            invite_token = invitation.json()["invite_token"]

            accepted = self.client.post(
                "/api/auth/invitations/accept",
                json={"token": invite_token, "password": strong_password("accept-password-123")},
            )
            self.assertEqual(accepted.status_code, 200)
            accepted_body = accepted.json()
            self.assertTrue(accepted_body["created_user"])
            self.assertIsNone(accepted_body["garden_id"])
            self.assertEqual(accepted_body["role"], "editor")
            self.assertEqual(accepted_body["invitation_scope"], "personal_garden")

        conn = db.get_db()
        try:
            user_row = conn.execute(
                "SELECT id, role FROM auth_users WHERE username = 'own_garden_editor'",
            ).fetchone()
            self.assertIsNotNone(user_row)
            assert user_row is not None
            user_id = int(user_row["id"])
            self.assertEqual(str(user_row["role"]), "editor")

            memberships = conn.execute(
                """
                SELECT gm.role, g.slug
                FROM garden_memberships gm
                JOIN gardens g ON g.id = gm.garden_id
                WHERE gm.user_id = %s
                ORDER BY gm.garden_id
                """,
                (user_id,),
            ).fetchone()
            self.assertIsNotNone(memberships)
            assert memberships is not None
            self.assertEqual(str(memberships["slug"]), "default")
            self.assertEqual(str(memberships["role"]), "editor")
        finally:
            db.return_db(conn)

    def test_personal_admin_invitation_marks_existing_sessions_mfa_required(self) -> None:
        conn = db.get_db()
        token = "personal-admin-invite-token"
        try:
            admin = create_user(
                conn,
                username="personal_promote_admin",
                password=strong_password("admin-password-123"),
                role="admin",
            )
            create_user(
                conn,
                username="personal_promote_target",
                password=strong_password("target-password-123"),
                role="viewer",
            )
            now_ms = db.current_timestamp_ms()
            conn.execute(
                """
                INSERT INTO auth_user_invitations (
                    invitee_username,
                    role,
                    token_hash,
                    created_by_user_id,
                    created_at_ms,
                    expires_at_ms
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    "personal_promote_target",
                    "admin",
                    hashlib.sha256(token.encode("utf-8")).hexdigest(),
                    int(admin["id"]),
                    now_ms,
                    now_ms + 60_000,
                ),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "AUTH_ADMIN_MFA_REQUIRED": "true",
            },
            clear=False,
        ):
            target_client = self._new_client()
            _, target_csrf = self._login_session(
                "personal_promote_target",
                "target-password-123",
                client=target_client,
            )
            target_headers = self._session_headers(target_csrf)

            accepted = self.client.post(
                "/api/auth/invitations/accept",
                json={
                    "token": token,
                    "password": strong_password("target-password-123"),
                },
            )
            self.assertEqual(accepted.status_code, 200)
            self.assertEqual(accepted.json()["role"], "admin")

            me = target_client.get("/api/auth/me", headers=target_headers)
            self.assertEqual(me.status_code, 200)
            self.assertEqual(me.json()["role"], "admin")
            self.assertTrue(me.json()["mfa_setup_required"])

            blocked = target_client.get("/api/auth/users", headers=target_headers)
            self.assertEqual(blocked.status_code, 403)
            self.assertEqual(
                blocked.json()["detail"],
                "Admin MFA setup is required before full access",
            )

    def test_auth_invitation_accept_existing_user_requires_password_and_rejects_expired(
        self,
    ) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="accept_existing_admin",
                password=strong_password("admin-password-123"),
                role="admin",
            )
            existing = create_user(
                conn,
                username="accept_existing_user",
                password=strong_password("existing-password-123"),
                role="viewer",
            )
            conn.commit()
            existing_id = int(existing["id"])
            conn.execute(
                "UPDATE auth_users SET password_hash = %s WHERE id = %s",
                (
                    _legacy_pbkdf2_hash_password(strong_password("existing-password-123")),
                    existing_id,
                ),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session("accept_existing_admin", "admin-password-123")
            headers = self._session_headers(csrf)

            garden_slug = f"accept-existing-{os.urandom(4).hex()}"
            created_garden = self.client.post(
                "/api/gardens",
                headers=headers,
                json={"name": "Accept Existing Garden", "slug": garden_slug},
            )
            self.assertEqual(created_garden.status_code, 201)
            garden_id = int(created_garden.json()["id"])

            headers = self._reauth_and_refresh_headers(
                self.client,
                headers,
                password=strong_password("admin-password-123"),
            )
            invitation = self.client.post(
                f"/api/gardens/{garden_id}/invitations",
                headers=headers,
                json={
                    "invitee_username": "accept_existing_user",
                    "role": "viewer",
                    "expires_in_minutes": 30,
                    "action_reason": "accept-existing-user-invitation-test",
                },
            )
            self.assertEqual(invitation.status_code, 201)
            invite_token = invitation.json()["invite_token"]

            wrong_password = self.client.post(
                "/api/auth/invitations/accept",
                json={"token": invite_token, "password": strong_password("bad-password-1")},
            )
            self.assertEqual(wrong_password.status_code, 401)
            self.assertEqual(wrong_password.json()["detail"], "Invalid invitation credentials")

            accepted = self.client.post(
                "/api/auth/invitations/accept",
                json={"token": invite_token, "password": strong_password("existing-password-123")},
            )
            self.assertEqual(accepted.status_code, 200)
            self.assertFalse(accepted.json()["created_user"])
            self.assertEqual(int(accepted.json()["user_id"]), existing_id)
            self.assertEqual(accepted.json()["role"], "viewer")

            next_invitation = self.client.post(
                f"/api/gardens/{garden_id}/invitations",
                headers=headers,
                json={
                    "invitee_username": "accept_existing_user",
                    "role": "viewer",
                    "expires_in_minutes": 30,
                    "action_reason": "accept-existing-user-expiry-test",
                },
            )
            self.assertEqual(next_invitation.status_code, 201)
            expired_token = next_invitation.json()["invite_token"]

        conn = db.get_db()
        try:
            conn.execute(
                """
                UPDATE garden_invitations
                SET expires_at_ms = %s
                WHERE token_hash = %s
                """,
                (
                    db.current_timestamp_ms() - 1_000,
                    hashlib.sha256(expired_token.encode("utf-8")).hexdigest(),
                ),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            expired = self.client.post(
                "/api/auth/invitations/accept",
                json={"token": expired_token, "password": strong_password("existing-password-123")},
            )
            self.assertEqual(expired.status_code, 400)
            self.assertEqual(expired.json()["detail"], "Invalid or expired invitation token")

        conn = db.get_db()
        try:
            membership = conn.execute(
                """
                SELECT gm.role, u.password_hash
                FROM garden_memberships gm
                JOIN auth_users u ON u.id = gm.user_id
                WHERE gm.garden_id = %s AND gm.user_id = %s
                """,
                (garden_id, existing_id),
            ).fetchone()
            self.assertIsNotNone(membership)
            assert membership is not None
            self.assertTrue(str(membership["password_hash"]).startswith("$argon2id$"))
            self.assertEqual(str(membership["role"]), "viewer")
        finally:
            db.return_db(conn)

    def test_lifecycle_rate_limit_applies_to_admin_user_update(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="ratelimit_admin",
                password=strong_password("admin-password-123"),
                role="admin",
            )
            target = create_user(
                conn,
                username="ratelimit_target",
                password=strong_password("target-password-123"),
                role="viewer",
            )
            conn.commit()
            target_id = int(target["id"])
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "AUTH_USER_UPDATE_RATE_LIMIT": "1",
            },
            clear=False,
        ):
            _, csrf = self._login_session("ratelimit_admin", "admin-password-123")
            headers = self._session_headers(csrf)

            first = self.client.patch(
                f"/api/auth/users/{target_id}",
                headers=headers,
                json={"must_change_password": True, "action_reason": "rate-limit-test-1"},
            )
            self.assertEqual(first.status_code, 200)

            second = self.client.patch(
                f"/api/auth/users/{target_id}",
                headers=headers,
                json={"must_change_password": False, "action_reason": "rate-limit-test-2"},
            )
            self.assertEqual(second.status_code, 429)
            self.assertIn("auth-user-update", second.json()["detail"])

    def test_invitation_accept_anti_enumeration_for_inactive_vs_bad_password(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="anti_enum_admin",
                password=strong_password("admin-password-123"),
                role="admin",
            )
            create_user(
                conn,
                username="anti_enum_active",
                password=strong_password("active-password-123"),
                role="viewer",
            )
            inactive_user = create_user(
                conn,
                username="anti_enum_inactive",
                password=strong_password("inactive-password-123"),
                role="viewer",
            )
            conn.execute(
                "UPDATE auth_users SET is_active = 0 WHERE id = %s",
                (int(inactive_user["id"]),),
            )
            conn.execute(
                "DELETE FROM auth_sessions WHERE user_id = %s",
                (int(inactive_user["id"]),),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session("anti_enum_admin", "admin-password-123")
            headers = self._session_headers(csrf)

            created = self.client.post(
                "/api/gardens",
                headers=headers,
                json={"name": "Anti Enum Garden", "slug": f"anti-enum-{os.urandom(3).hex()}"},
            )
            self.assertEqual(created.status_code, 201)
            garden_id = int(created.json()["id"])

            headers = self._reauth_and_refresh_headers(
                self.client,
                headers,
                password=strong_password("admin-password-123"),
            )
            active_invite = self.client.post(
                f"/api/gardens/{garden_id}/invitations",
                headers=headers,
                json={
                    "invitee_username": "anti_enum_active",
                    "role": "viewer",
                    "action_reason": "anti-enum-check",
                },
            )
            self.assertEqual(active_invite.status_code, 201)
            active_token = active_invite.json()["invite_token"]

            inactive_invite = self.client.post(
                f"/api/gardens/{garden_id}/invitations",
                headers=headers,
                json={
                    "invitee_username": "anti_enum_inactive",
                    "role": "viewer",
                    "action_reason": "anti-enum-check",
                },
            )
            self.assertEqual(inactive_invite.status_code, 201)
            inactive_token = inactive_invite.json()["invite_token"]

            wrong_password = self.client.post(
                "/api/auth/invitations/accept",
                json={"token": active_token, "password": strong_password("wrong-password")},
            )
            self.assertEqual(wrong_password.status_code, 401)
            self.assertEqual(wrong_password.json()["detail"], "Invalid invitation credentials")

            inactive_accept = self.client.post(
                "/api/auth/invitations/accept",
                json={
                    "token": inactive_token,
                    "password": strong_password("inactive-password-123"),
                },
            )
            self.assertEqual(inactive_accept.status_code, 401)
            self.assertEqual(inactive_accept.json()["detail"], "Invalid invitation credentials")

    def test_auth_status_reports_user_lifecycle_feature_flag_state(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "AUTH_USER_LIFECYCLE_ENABLED": "false",
            },
            clear=False,
        ):
            status = self.client.get("/api/auth/status")
            self.assertEqual(status.status_code, 200)
            self.assertIn("user_lifecycle_enabled", status.json())
            self.assertFalse(status.json()["user_lifecycle_enabled"])

    def test_user_lifecycle_endpoints_return_404_when_feature_flag_disabled(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="flag_off_admin",
                password=strong_password("admin-password-123"),
                role="admin",
            )
            target = create_user(
                conn,
                username="flag_off_target",
                password=strong_password("target-password-123"),
                role="viewer",
            )
            conn.commit()
            target_id = int(target["id"])
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "AUTH_USER_LIFECYCLE_ENABLED": "false",
            },
            clear=False,
        ):
            _, csrf = self._login_session("flag_off_admin", "admin-password-123")
            headers = self._session_headers(csrf)

            me = self.client.get("/api/auth/me")
            self.assertEqual(me.status_code, 200)

            created_garden = self.client.post(
                "/api/gardens",
                headers=headers,
                json={"name": "Flag Off Garden", "slug": f"flag-off-{os.urandom(3).hex()}"},
            )
            self.assertEqual(created_garden.status_code, 201)
            garden_id = int(created_garden.json()["id"])

            responses = [
                self.client.get("/api/auth/users", headers=headers),
                self.client.post(
                    "/api/auth/users",
                    headers=headers,
                    json={
                        "username": "flag-off-new-user",
                        "password": strong_password("new-user-password-123"),
                        "role": "viewer",
                    },
                ),
                self.client.patch(
                    f"/api/auth/users/{target_id}",
                    headers=headers,
                    json={"must_change_password": True},
                ),
                self.client.post(
                    f"/api/auth/users/{target_id}/revoke-sessions",
                    headers=headers,
                    json={"action_reason": "flag-off-test"},
                ),
                self.client.post(
                    "/api/auth/change-password",
                    headers=headers,
                    json={
                        "current_password": strong_password("admin-password-123"),
                        "new_password": strong_password("admin-password-456"),
                    },
                ),
                self.client.post(
                    f"/api/auth/users/{target_id}/issue-reset",
                    headers=headers,
                    json={"expires_in_minutes": 30},
                ),
                self.client.post(
                    "/api/auth/reset-password",
                    json={
                        "token": "x" * 12,
                        "new_password": strong_password("reset-password-123"),
                    },
                ),
                self.client.post(
                    "/api/auth/invitations/accept",
                    json={
                        "token": "y" * 12,
                        "password": strong_password("invite-password-123"),
                    },
                ),
                self.client.get(
                    f"/api/gardens/{garden_id}/invitations",
                    headers=headers,
                ),
                self.client.post(
                    f"/api/gardens/{garden_id}/invitations",
                    headers=headers,
                    json={
                        "invitee_username": "flag_off_invitee",
                        "role": "viewer",
                    },
                ),
                self.client.delete(
                    f"/api/gardens/{garden_id}/invitations/99999",
                    headers=headers,
                ),
            ]
            for response in responses:
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json().get("detail"), "User lifecycle is disabled")

    def test_auth_audit_events_requires_admin(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="viewer2",
                password=strong_password("viewerpass123"),
                role="viewer",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
            clear=False,
        ):
            self._login_session("viewer2", "viewerpass123")
            denied = self.client.get("/api/auth/audit-events")
            self.assertEqual(denied.status_code, 403)

    def test_auth_audit_events_supports_filters_and_pagination(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="admin2",
                password=strong_password("adminpass123"),
                role="admin",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
            },
            clear=False,
        ):
            _, csrf = self._login_session("admin2", "adminpass123")
            headers = self._session_headers(csrf)

            self.client.post("/api/snapshots", json={"name": "a1"}, headers=headers)
            self.client.patch(
                "/api/layout-state",
                json={"row": 9, "col": 6, "width": 12, "height": 8, "north_degrees": 1},
                headers=headers,
            )
            self.client.post("/api/snapshots", json={"name": "a2"}, headers=headers)

            page1 = self.client.get(
                "/api/auth/audit-events",
                params={"limit": 1, "offset": 0},
                headers=headers,
            )
            self.assertEqual(page1.status_code, 200)
            page1_data = page1.json()
            self.assertIn("events", page1_data)
            self.assertIn("total", page1_data)
            self.assertEqual(page1_data["limit"], 1)
            self.assertEqual(page1_data["offset"], 0)
            self.assertGreaterEqual(page1_data["total"], 3)
            self.assertEqual(len(page1_data["events"]), 1)
            self.assertIn("garden_id", page1_data["events"][0])

            filtered = self.client.get(
                "/api/auth/audit-events",
                params={"method": "PATCH", "path_prefix": "/api/layout-state", "limit": 20},
                headers=headers,
            )
            self.assertEqual(filtered.status_code, 200)
            filtered_events = filtered.json()["events"]
            self.assertGreaterEqual(len(filtered_events), 1)
            self.assertTrue(all(ev["method"] == "PATCH" for ev in filtered_events))
            self.assertTrue(
                all(str(ev["path"]).startswith("/api/layout-state") for ev in filtered_events)
            )

    def test_auth_audit_events_support_garden_id_filter(self) -> None:
        default_garden_id, second_garden_id, username, password = self._setup_admin_two_gardens()
        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session(username, password)
            default_headers = self._session_headers(csrf, garden_id=default_garden_id)
            second_headers = self._session_headers(csrf, garden_id=second_garden_id)

            created_default = self.client.post(
                "/api/snapshots",
                headers=default_headers,
                json={"name": "default-audit-snapshot"},
            )
            self.assertEqual(created_default.status_code, 201)

            created_second = self.client.post(
                "/api/snapshots",
                headers=second_headers,
                json={"name": "second-audit-snapshot"},
            )
            self.assertEqual(created_second.status_code, 201)

            second_only = self.client.get(
                "/api/auth/audit-events",
                params={"garden_id": second_garden_id, "limit": 20},
                headers=default_headers,
            )
            self.assertEqual(second_only.status_code, 200)
            second_events = second_only.json()["events"]
            self.assertGreaterEqual(len(second_events), 1)
            self.assertTrue(all(int(ev["garden_id"]) == second_garden_id for ev in second_events))

            default_only = self.client.get(
                "/api/auth/audit-events",
                params={"garden_id": default_garden_id, "limit": 20},
                headers=default_headers,
            )
            self.assertEqual(default_only.status_code, 200)
            default_events = default_only.json()["events"]
            self.assertGreaterEqual(len(default_events), 1)
            self.assertTrue(all(int(ev["garden_id"]) == default_garden_id for ev in default_events))

    def test_auth_security_metrics_and_alerts_admin_only(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="admin3",
                password=strong_password("adminpass123"),
                role="admin",
            )
            create_user(
                conn,
                username="viewer3",
                password=strong_password("viewerpass123"),
                role="viewer",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "ALERT_AUTH_FAILURES_PER_MINUTE": "1",
            },
            clear=False,
        ):
            admin_client = self._new_client()
            viewer_client = self._new_client()
            _, admin_csrf = self._login_session("admin3", "adminpass123", client=admin_client)
            self._session_headers(admin_csrf)
            self._login_session("viewer3", "viewerpass123", client=viewer_client)

            record_security_event("auth_failures")

            denied = viewer_client.get("/api/auth/security-metrics")
            self.assertEqual(denied.status_code, 403)

            metrics = admin_client.get("/api/auth/security-metrics")
            self.assertEqual(metrics.status_code, 200)
            metrics_payload = metrics.json()
            self.assertIn("counters", metrics_payload)
            self.assertIn("rates", metrics_payload)

            alerts = admin_client.get("/api/auth/security-alerts")
            self.assertEqual(alerts.status_code, 200)
            alert_names = {a["name"] for a in alerts.json().get("alerts", [])}
            self.assertIn("auth_failures_per_minute", alert_names)

    def test_admin_session_revocation_endpoints_and_emergency_read_only(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn, username="admin4", password=strong_password("adminpass123"), role="admin"
            )
            create_user(
                conn, username="user4", password=strong_password("userpass123"), role="editor"
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            admin_client = self._new_client()
            user_client = self._new_client()
            _, admin_csrf = self._login_session("admin4", "adminpass123", client=admin_client)
            admin_headers = self._session_headers(admin_csrf)
            self._login_session("user4", "userpass123", client=user_client)

            sessions = admin_client.get("/api/auth/sessions")
            self.assertEqual(sessions.status_code, 200)
            self.assertGreaterEqual(len(sessions.json()["sessions"]), 2)

            revoke_user = admin_client.post(
                "/api/auth/revoke-user-sessions",
                headers=admin_headers,
                json={
                    "username": "user4",
                    "action_reason": "incident-user-session-revoke",
                },
            )
            self.assertEqual(revoke_user.status_code, 200)
            self.assertGreaterEqual(revoke_user.json()["revoked"], 1)

            user_me = user_client.get("/api/auth/me")
            self.assertEqual(user_me.status_code, 401)

            enable_emergency = admin_client.patch(
                "/api/auth/emergency-read-only",
                headers=admin_headers,
                json={
                    "enabled": True,
                    "action_reason": "incident-read-only-enable",
                    "expires_in_minutes": 10,
                },
            )
            self.assertEqual(enable_emergency.status_code, 200)
            self.assertEqual(enable_emergency.json()["enabled"], True)
            self.assertIsNotNone(enable_emergency.json()["expires_at_ms"])

            blocked_mutation = admin_client.post(
                "/api/snapshots",
                headers=admin_headers,
                json={"name": "blocked-by-emergency"},
            )
            self.assertEqual(blocked_mutation.status_code, 503)

            disable_emergency = admin_client.patch(
                "/api/auth/emergency-read-only",
                headers=admin_headers,
                json={
                    "enabled": False,
                    "action_reason": "incident-read-only-disable",
                },
            )
            self.assertEqual(disable_emergency.status_code, 200)
            self.assertEqual(disable_emergency.json()["enabled"], False)
            self.assertIsNone(disable_emergency.json()["expires_at_ms"])

            allowed_mutation = admin_client.post(
                "/api/snapshots",
                headers=admin_headers,
                json={"name": "allowed-after-emergency"},
            )
            self.assertEqual(allowed_mutation.status_code, 201)

    def test_destructive_admin_routes_require_recent_reauth_and_reason(self) -> None:
        conn = db.get_db()
        try:
            admin = create_user(
                conn, username="admin5", password=strong_password("adminpass123"), role="admin"
            )
            create_user(
                conn, username="user5", password=strong_password("userpass123"), role="editor"
            )
            garden_id = int(
                conn.execute(
                    "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
                ).fetchone()["id"]
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                ON CONFLICT(plot_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id,
                    garden_id = excluded.garden_id
                """,
                ("B1", int(admin["id"]), garden_id),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            admin_client = self._new_client()
            _, admin_csrf = self._login_session("admin5", "adminpass123", client=admin_client)
            admin_headers = self._session_headers(admin_csrf)

            session_token = admin_client.cookies.get("gardenops_session", "")
            self.assertTrue(session_token)
            assert session_token is not None
            session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
            conn = db.get_db()
            try:
                conn.execute(
                    """
                    UPDATE auth_sessions
                    SET reauthenticated_at_ms = %s
                    WHERE token_hash = %s
                    """,
                    (db.current_timestamp_ms() - (2 * 60 * 60 * 1000), session_hash),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            stale = admin_client.post(
                "/api/auth/revoke-user-sessions",
                headers=admin_headers,
                json={
                    "username": "user5",
                    "action_reason": "stale-session-check",
                },
            )
            self.assertEqual(stale.status_code, 403)
            self.assertEqual(stale.json()["detail"], "Recent reauthentication required")

            bad_reauth = admin_client.post(
                "/api/auth/reauthenticate",
                headers=admin_headers,
                json={"current_password": strong_password("wrong-password")},
            )
            self.assertEqual(bad_reauth.status_code, 401)

            admin_headers = self._reauth_and_refresh_headers(
                admin_client,
                admin_headers,
                password=strong_password("adminpass123"),
            )

            missing_reason = admin_client.post(
                "/api/auth/revoke-user-sessions",
                headers=admin_headers,
                json={"username": "user5"},
            )
            self.assertEqual(missing_reason.status_code, 400)
            self.assertEqual(missing_reason.json()["detail"], "Action reason is required")

            revoke_user = admin_client.post(
                "/api/auth/revoke-user-sessions",
                headers=admin_headers,
                json={
                    "username": "user5",
                    "action_reason": "post-reauth-session-revoke",
                },
            )
            self.assertEqual(revoke_user.status_code, 200)
            self.assertGreaterEqual(revoke_user.json()["revoked"], 0)

            export_res = admin_client.get("/api/plots/export", headers=admin_headers)
            self.assertEqual(export_res.status_code, 200)
            exported = json.loads(export_res.content)

            import_missing_reason = admin_client.post(
                "/api/plots/import",
                headers=admin_headers,
                json=exported,
            )
            self.assertEqual(import_missing_reason.status_code, 400)
            self.assertEqual(import_missing_reason.json()["detail"], "Action reason is required")

            import_ok = admin_client.post(
                "/api/plots/import",
                headers={
                    **admin_headers,
                    "x-action-reason": "post-reauth-import",
                },
                json=exported,
            )
            self.assertEqual(import_ok.status_code, 200)

    def test_api_key_auth_cannot_call_destructive_admin_controls(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "hybrid",
                "AUTH_API_KEY": "shared-test-key",
            },
            clear=False,
        ):
            denied = self.client.post(
                "/api/auth/revoke-all-sessions",
                headers={"x-api-key": "shared-test-key"},
                json={"action_reason": "api-key-denied"},
            )
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(
                denied.json()["detail"],
                "Session-backed admin authentication required",
            )

    def test_admin_mfa_setup_flow_and_step_up_enforcement(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn, username="mfaadmin1", password=strong_password("adminpass123"), role="admin"
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "AUTH_ADMIN_MFA_REQUIRED": "true",
            },
            clear=False,
        ):
            admin_client = self._new_client()
            login = admin_client.post(
                "/api/auth/login",
                json={"username": "mfaadmin1", "password": strong_password("adminpass123")},
            )
            self.assertEqual(login.status_code, 200)
            self.assertEqual(login.json()["status"], "mfa_setup_required")

            csrf_token = admin_client.cookies.get("gardenops_csrf", "")
            self.assertTrue(csrf_token)
            admin_headers = self._session_headers(csrf_token)

            me_before = admin_client.get("/api/auth/me")
            self.assertEqual(me_before.status_code, 200)
            self.assertTrue(me_before.json()["mfa_setup_required"])
            self.assertFalse(me_before.json()["mfa_enabled"])

            blocked_users = admin_client.get("/api/auth/users", headers=admin_headers)
            self.assertEqual(blocked_users.status_code, 403)
            self.assertEqual(
                blocked_users.json()["detail"],
                "Admin MFA setup is required before full access",
            )

            start = admin_client.post("/api/auth/mfa/totp/start", headers=admin_headers)
            self.assertEqual(start.status_code, 200)
            secret = start.json()["secret"]
            self.assertTrue(secret)

            confirm = admin_client.post(
                "/api/auth/mfa/totp/confirm",
                headers=admin_headers,
                json={"code": self._totp_code(secret)},
            )
            self.assertEqual(confirm.status_code, 200)
            recovery_codes = confirm.json()["recovery_codes"]
            self.assertGreaterEqual(len(recovery_codes), 5)

            me_after = admin_client.get("/api/auth/me")
            self.assertEqual(me_after.status_code, 200)
            self.assertTrue(me_after.json()["mfa_enabled"])
            self.assertFalse(me_after.json()["mfa_setup_required"])
            self.assertTrue(me_after.json()["mfa_authenticated"])

            second_client = self._new_client()
            login_needs_mfa = second_client.post(
                "/api/auth/login",
                json={"username": "mfaadmin1", "password": strong_password("adminpass123")},
            )
            self.assertEqual(login_needs_mfa.status_code, 200)
            self.assertEqual(login_needs_mfa.json()["status"], "mfa_required")
            self.assertFalse(second_client.cookies.get("gardenops_session", ""))

            second_login = second_client.post(
                "/api/auth/login",
                json={
                    "username": "mfaadmin1",
                    "password": strong_password("adminpass123"),
                    "mfa_code": self._totp_code(secret),
                },
            )
            self.assertEqual(second_login.status_code, 200)
            self.assertEqual(second_login.json()["status"], "ok")

            second_csrf = second_client.cookies.get("gardenops_csrf", "")
            self.assertTrue(second_csrf)
            second_headers = self._session_headers(second_csrf)

            bad_reauth = second_client.post(
                "/api/auth/reauthenticate",
                headers=second_headers,
                json={"current_password": strong_password("adminpass123")},
            )
            self.assertEqual(bad_reauth.status_code, 401)
            self.assertEqual(
                bad_reauth.json()["detail"],
                "Current multi-factor authentication code is incorrect",
            )

            second_headers = self._reauth_and_refresh_headers(
                second_client,
                second_headers,
                password=strong_password("adminpass123"),
                mfa_code=self._totp_code(secret, offset=1),
            )

            revoke_all = second_client.post(
                "/api/auth/revoke-all-sessions",
                headers=second_headers,
                json={"action_reason": "mfa-step-up-validated"},
            )
            self.assertEqual(revoke_all.status_code, 200)

    def test_admin_mfa_recovery_codes_regeneration_and_disable(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn, username="mfaadmin2", password=strong_password("adminpass123"), role="admin"
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "AUTH_ADMIN_MFA_REQUIRED": "true",
            },
            clear=False,
        ):
            admin_client = self._new_client()
            initial_login = admin_client.post(
                "/api/auth/login",
                json={"username": "mfaadmin2", "password": strong_password("adminpass123")},
            )
            self.assertEqual(initial_login.status_code, 200)
            self.assertEqual(initial_login.json()["status"], "mfa_setup_required")

            csrf_token = admin_client.cookies.get("gardenops_csrf", "")
            admin_headers = self._session_headers(csrf_token)

            start = admin_client.post("/api/auth/mfa/totp/start", headers=admin_headers)
            secret = start.json()["secret"]
            confirm = admin_client.post(
                "/api/auth/mfa/totp/confirm",
                headers=admin_headers,
                json={"code": self._totp_code(secret)},
            )
            self.assertEqual(confirm.status_code, 200)
            original_recovery_codes = confirm.json()["recovery_codes"]
            self.assertGreaterEqual(len(original_recovery_codes), 5)

            recovery_client = self._new_client()
            recovery_login = recovery_client.post(
                "/api/auth/login",
                json={
                    "username": "mfaadmin2",
                    "password": strong_password("adminpass123"),
                    "recovery_code": original_recovery_codes[0],
                },
            )
            self.assertEqual(recovery_login.status_code, 200)
            self.assertEqual(recovery_login.json()["status"], "ok")

            reused_client = self._new_client()
            reused_recovery = reused_client.post(
                "/api/auth/login",
                json={
                    "username": "mfaadmin2",
                    "password": strong_password("adminpass123"),
                    "recovery_code": original_recovery_codes[0],
                },
            )
            self.assertEqual(reused_recovery.status_code, 200)
            self.assertEqual(reused_recovery.json()["status"], "mfa_required")

            admin_headers = self._reauth_and_refresh_headers(
                admin_client,
                admin_headers,
                password=strong_password("adminpass123"),
                mfa_code=self._totp_code(secret),
            )

            regenerated = admin_client.post(
                "/api/auth/mfa/recovery-codes/regenerate",
                headers=admin_headers,
                json={"action_reason": "rotate-break-glass-codes"},
            )
            self.assertEqual(regenerated.status_code, 200)
            new_recovery_codes = regenerated.json()["recovery_codes"]
            self.assertGreaterEqual(len(new_recovery_codes), 5)
            self.assertNotEqual(new_recovery_codes, original_recovery_codes)

            disabled = admin_client.post(
                "/api/auth/mfa/disable",
                headers=admin_headers,
                json={"action_reason": "replace-authenticator-device"},
            )
            self.assertEqual(disabled.status_code, 200)
            self.assertTrue(disabled.json()["mfa"]["setup_required"])

            me_after_disable = admin_client.get("/api/auth/me")
            self.assertEqual(me_after_disable.status_code, 200)
            self.assertFalse(me_after_disable.json()["mfa_enabled"])
            self.assertTrue(me_after_disable.json()["mfa_setup_required"])

            blocked_users = admin_client.get("/api/auth/users", headers=admin_headers)
            self.assertEqual(blocked_users.status_code, 403)
            self.assertEqual(
                blocked_users.json()["detail"],
                "Admin MFA setup is required before full access",
            )

    def test_emergency_read_only_auto_expires_and_writes_structured_audit(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn, username="admin6", password=strong_password("adminpass123"), role="admin"
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            admin_client = self._new_client()
            _, admin_csrf = self._login_session("admin6", "adminpass123", client=admin_client)
            admin_headers = self._session_headers(admin_csrf)

            enabled = admin_client.patch(
                "/api/auth/emergency-read-only",
                headers=admin_headers,
                json={
                    "enabled": True,
                    "action_reason": "incident-containment",
                    "expires_in_minutes": 5,
                },
            )
            self.assertEqual(enabled.status_code, 200)
            self.assertTrue(enabled.json()["enabled"])
            self.assertIsNotNone(enabled.json()["expires_at_ms"])

        conn = db.get_db()
        try:
            conn.execute(
                """
                UPDATE security_runtime_flags
                SET value = %s
                WHERE key = 'emergency_read_only_expires_at_ms'
                """,
                (str(db.current_timestamp_ms() - 1_000),),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            admin_client = self._new_client()
            _, admin_csrf = self._login_session("admin6", "adminpass123", client=admin_client)
            admin_headers = self._session_headers(admin_csrf)

            status = admin_client.get("/api/auth/emergency-read-only", headers=admin_headers)
            self.assertEqual(status.status_code, 200)
            self.assertFalse(status.json()["enabled"])
            self.assertIsNone(status.json()["expires_at_ms"])

            allowed_mutation = admin_client.post(
                "/api/snapshots",
                headers=admin_headers,
                json={"name": "allowed-after-auto-expiry"},
            )
            self.assertEqual(allowed_mutation.status_code, 201)

        conn = db.get_db()
        try:
            row = conn.execute(
                """
                SELECT detail
                FROM audit_events
                WHERE detail LIKE 'auth.emergency-read-only %'
                ORDER BY id DESC
                LIMIT 1
                """,
            ).fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            payload = json.loads(str(row["detail"]).split(" ", 1)[1])
            self.assertEqual(payload["action_reason"], "incident-containment")
            self.assertTrue(payload["enabled"])
            self.assertIsNotNone(payload["expires_at_ms"])
        finally:
            db.return_db(conn)

    def test_first_user_creation_backfills_ownership_for_existing_data(self) -> None:
        conn = db.get_db()
        try:
            conn.execute("DELETE FROM plot_ownership")
            conn.execute("DELETE FROM plant_ownership")
            conn.execute("DELETE FROM garden_memberships")
            conn.execute("DELETE FROM auth_users")
            created = create_user(
                conn,
                username="ownerseed",
                password=strong_password("ownerseed123"),
                role="admin",
            )
            conn.commit()
            owner_id = int(created["id"])
            membership = conn.execute(
                "SELECT garden_id FROM garden_memberships WHERE user_id = %s LIMIT 1",
                (owner_id,),
            ).fetchone()
            self.assertIsNotNone(membership)
            assert membership is not None
            garden_id = int(membership["garden_id"])
            plot_rows = conn.execute(
                "SELECT plot_id, owner_user_id, garden_id FROM plot_ownership ORDER BY plot_id",
            ).fetchall()
            plant_rows = conn.execute(
                "SELECT plt_id, owner_user_id, garden_id FROM plant_ownership ORDER BY plt_id",
            ).fetchall()
            self.assertGreaterEqual(len(plot_rows), 2)
            self.assertGreaterEqual(len(plant_rows), 2)
            self.assertTrue(all(int(r["owner_user_id"]) == owner_id for r in plot_rows))
            self.assertTrue(all(int(r["owner_user_id"]) == owner_id for r in plant_rows))
            self.assertTrue(all(int(r["garden_id"]) == garden_id for r in plot_rows))
            self.assertTrue(all(int(r["garden_id"]) == garden_id for r in plant_rows))
        finally:
            db.return_db(conn)

    def test_multiuser_ownership_blocks_cross_user_plot_and_plant_access(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn, username="admin5", password=strong_password("adminpass123"), role="admin"
            )
            create_user(
                conn, username="alice5", password=strong_password("alicepass123"), role="editor"
            )
            create_user(
                conn, username="bob5", password=strong_password("bobpass123"), role="editor"
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            admin_client = self._new_client()
            alice_client = self._new_client()
            bob_client = self._new_client()
            _, admin_csrf = self._login_session("admin5", "adminpass123", client=admin_client)
            self._session_headers(admin_csrf)
            _, alice_csrf = self._login_session("alice5", "alicepass123", client=alice_client)
            alice_headers = self._session_headers(alice_csrf)
            _, bob_csrf = self._login_session("bob5", "bobpass123", client=bob_client)
            bob_headers = self._session_headers(bob_csrf)

            create_plot = alice_client.post(
                "/api/plots",
                headers=alice_headers,
                json={
                    "plot_id": "A5",
                    "zone_code": "A",
                    "zone_name": "Alice",
                    "plot_number": 5,
                    "grid_row": 5,
                    "grid_col": 5,
                },
            )
            self.assertEqual(create_plot.status_code, 201)

            create_plant = alice_client.post(
                "/api/plants",
                headers=alice_headers,
                json={"plt_id": "ALICE-PLT-5", "name": "Alice Plant", "category": "frø"},
            )
            self.assertEqual(create_plant.status_code, 201)

            assign = alice_client.post(
                "/api/plots/A5/plants/ALICE-PLT-5",
                headers=alice_headers,
                json={"quantity": 1},
            )
            self.assertEqual(assign.status_code, 201)

            # Editors in the same garden CAN see each other's plots and plants
            bob_plots = bob_client.get("/api/plots")
            self.assertEqual(bob_plots.status_code, 200)
            bob_plot_ids = {p["plot_id"] for p in bob_plots.json()}
            self.assertIn("A5", bob_plot_ids)

            bob_plants = bob_client.get("/api/plants")
            self.assertEqual(bob_plants.status_code, 200)
            bob_plant_ids = {p["plt_id"] for p in bob_plants.json()}
            self.assertIn("ALICE-PLT-5", bob_plant_ids)

            bob_get_foreign_plot = bob_client.get("/api/plots/A5/plants")
            self.assertEqual(bob_get_foreign_plot.status_code, 200)

            bob_update_foreign_plot = bob_client.patch(
                "/api/plots/A5",
                headers=bob_headers,
                json={"notes": "should fail"},
            )
            self.assertEqual(bob_update_foreign_plot.status_code, 404)

            bob_update_foreign_plant = bob_client.patch(
                "/api/plants/ALICE-PLT-5",
                headers=bob_headers,
                json={"color": "blue"},
            )
            self.assertEqual(bob_update_foreign_plant.status_code, 404)

            bob_snapshot = bob_client.post(
                "/api/snapshots",
                headers=bob_headers,
                json={"name": "editor-should-not-save"},
            )
            self.assertEqual(bob_snapshot.status_code, 403)

            admin_plots = admin_client.get("/api/plots")
            self.assertEqual(admin_plots.status_code, 200)
            self.assertIn("A5", {p["plot_id"] for p in admin_plots.json()})

    def test_garden_membership_admin_authorization(self) -> None:
        conn = db.get_db()
        try:
            alice = create_user(
                conn, username="aliceadmin", password=strong_password("alicepass123"), role="editor"
            )
            create_user(
                conn, username="bobmember", password=strong_password("bobpass123"), role="editor"
            )
            c1 = conn.execute(
                "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                (f"alpha-{os.urandom(3).hex()}", "Alpha"),
            )
            garden_one = c1.fetchone()["id"]
            c2 = conn.execute(
                "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                (f"beta-{os.urandom(3).hex()}", "Beta"),
            )
            garden_two = c2.fetchone()["id"]
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (garden_one, int(alice["id"])),
            )
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'viewer')
                """,
                (garden_two, int(alice["id"])),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session("aliceadmin", "alicepass123")
            headers = self._session_headers(csrf)

            allow = self.client.post(
                f"/api/gardens/{garden_one}/memberships",
                headers=headers,
                json={"username": "bobmember", "role": "viewer"},
            )
            self.assertEqual(allow.status_code, 200)

            deny = self.client.post(
                f"/api/gardens/{garden_two}/memberships",
                headers=headers,
                json={"username": "bobmember", "role": "editor"},
            )
            self.assertEqual(deny.status_code, 404)

    def test_invitation_admin_authorization_and_cross_garden_404(self) -> None:
        """Only platform admins can manage garden invitations.

        Garden-level admins (editors with garden membership role 'admin')
        are NOT permitted to create, list, or revoke invitations.
        """
        conn = db.get_db()
        try:
            alice = create_user(
                conn, username="invitealice", password=strong_password("alicepass123"), role="admin"
            )
            create_user(
                conn, username="invitebob", password=strong_password("bobpass123"), role="editor"
            )
            editor_user = create_user(
                conn,
                username="inviteeditor",
                password=strong_password("editorpass123"),
                role="editor",
            )
            c1 = conn.execute(
                "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                (f"invite-alpha-{os.urandom(3).hex()}", "Invite Alpha"),
            )
            garden_one = c1.fetchone()["id"]
            c2 = conn.execute(
                "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                (f"invite-beta-{os.urandom(3).hex()}", "Invite Beta"),
            )
            garden_two = c2.fetchone()["id"]
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (garden_one, int(alice["id"])),
            )
            # Editor with garden-admin on garden_one — should still be denied
            conn.execute(
                """
                INSERT INTO garden_memberships (garden_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (garden_one, int(editor_user["id"])),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            # Platform admin must step up before creating invitations.
            _, csrf = self._login_session("invitealice", "alicepass123")
            headers = self._session_headers(csrf)

            session_token = self.client.cookies.get("gardenops_session", "")
            self.assertTrue(session_token)
            session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
            conn = db.get_db()
            try:
                conn.execute(
                    """
                    UPDATE auth_sessions
                    SET reauthenticated_at_ms = %s
                    WHERE token_hash = %s
                    """,
                    (db.current_timestamp_ms() - (2 * 60 * 60 * 1000), session_hash),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            stale_create = self.client.post(
                f"/api/gardens/{garden_one}/invitations",
                headers=headers,
                json={
                    "invitee_username": "invitebob",
                    "role": "viewer",
                    "action_reason": "stale-invitation-create",
                },
            )
            self.assertEqual(stale_create.status_code, 403)
            self.assertEqual(stale_create.json()["detail"], "Recent reauthentication required")

            headers = self._reauth_and_refresh_headers(
                self.client,
                headers,
                password=strong_password("alicepass123"),
            )

            missing_reason = self.client.post(
                f"/api/gardens/{garden_one}/invitations",
                headers=headers,
                json={"invitee_username": "invitebob", "role": "viewer"},
            )
            self.assertEqual(missing_reason.status_code, 400)
            self.assertEqual(missing_reason.json()["detail"], "Action reason is required")

            # Platform admin (alice) CAN create invitations after step-up.
            allow_create = self.client.post(
                f"/api/gardens/{garden_one}/invitations",
                headers=headers,
                json={
                    "invitee_username": "invitebob",
                    "role": "viewer",
                    "action_reason": "create-garden-one-invite",
                },
            )
            self.assertEqual(allow_create.status_code, 201)
            invitation_id = int(allow_create.json()["invitation"]["id"])

            # Platform admin can also create on garden_two (any garden)
            allow_create_two = self.client.post(
                f"/api/gardens/{garden_two}/invitations",
                headers=headers,
                json={
                    "invitee_username": "invitebob",
                    "role": "viewer",
                    "action_reason": "create-garden-two-invite",
                },
            )
            self.assertEqual(allow_create_two.status_code, 201)

            missing_revoke_reason = self.client.delete(
                f"/api/gardens/{garden_one}/invitations/{invitation_id}",
                headers=headers,
            )
            self.assertEqual(missing_revoke_reason.status_code, 400)
            self.assertEqual(
                missing_revoke_reason.json()["detail"],
                "Action reason is required",
            )

            # Non-platform-admin editor is denied even with garden-admin membership
            _, editor_csrf = self._login_session("inviteeditor", "editorpass123")
            editor_headers = self._session_headers(editor_csrf)

            deny_create = self.client.post(
                f"/api/gardens/{garden_one}/invitations",
                headers=editor_headers,
                json={"invitee_username": "invitebob", "role": "viewer"},
            )
            self.assertEqual(deny_create.status_code, 403)

            deny_list = self.client.get(
                f"/api/gardens/{garden_one}/invitations",
                headers=editor_headers,
            )
            self.assertEqual(deny_list.status_code, 403)

            deny_delete = self.client.delete(
                f"/api/gardens/{garden_one}/invitations/{invitation_id}",
                headers=editor_headers,
            )
            self.assertEqual(deny_delete.status_code, 403)

    def test_remote_access_requires_explicit_auth_or_override(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"AUTH_REQUIRED": "false", "ALLOW_INSECURE_REMOTE": "false"},
                clear=False,
            ),
            patch("gardenops.security.is_loopback_client", return_value=False),
        ):
            denied = self.client.get("/api/plots")
            self.assertEqual(denied.status_code, 503)

        with (
            patch.dict(
                os.environ,
                {"AUTH_REQUIRED": "false", "ALLOW_INSECURE_REMOTE": "true"},
                clear=False,
            ),
            patch("gardenops.security.is_loopback_client", return_value=False),
        ):
            allowed = self.client.get("/api/plots")
            self.assertEqual(allowed.status_code, 200)

    @staticmethod
    def _valid_production_runtime_env() -> dict[str, str]:
        return {
            "APP_ENV": "production",
            "INTERNET_EXPOSED": "false",
            "MULTI_INSTANCE": "false",
            "AUTH_REQUIRED": "true",
            "AUTH_MODE": "session",
            "AUTH_MFA_SECRET_KEY": "test-production-mfa-secret-32chars",
            "AUTH_SESSION_COOKIE_SECURE": "true",
            "AUTH_SESSION_COOKIE_SAMESITE": "lax",
            "ALLOW_INSECURE_REMOTE": "false",
            "CORS_ALLOW_ORIGINS": "https://gardenops.example.com",
            "ALLOWED_HOSTS": "gardenops.example.com",
            "RATE_LIMIT_BACKEND": "redis",
            "RATE_LIMIT_REDIS_URL": "redis://example.invalid:6379/0",
            "API_DOCS_ENABLED": "false",
            "CSP_REPORT_ONLY": "false",
        }

    @staticmethod
    def _valid_internet_exposed_runtime_env() -> dict[str, str]:
        return {
            "APP_ENV": "development",
            "INTERNET_EXPOSED": "true",
            "MULTI_INSTANCE": "false",
            "AUTH_REQUIRED": "true",
            "AUTH_MODE": "session",
            "AUTH_API_KEY": "",
            "AUTH_MFA_SECRET_KEY": "test-internet-mfa-secret-32chars-ok",
            "AUTH_SESSION_COOKIE_SECURE": "true",
            "AUTH_SESSION_COOKIE_SAMESITE": "lax",
            "ALLOW_INSECURE_REMOTE": "false",
            "CORS_ALLOW_ORIGINS": "https://gardenops.example.com",
            "ALLOWED_HOSTS": "gardenops.example.com",
            "TRUST_PROXY_HEADERS": "true",
            "TRUSTED_PROXY_CIDRS": "127.0.0.1/32",
            "RATE_LIMIT_BACKEND": "redis",
            "RATE_LIMIT_REDIS_URL": "redis://example.invalid:6379/0",
            "API_DOCS_ENABLED": "false",
            "CSP_REPORT_ONLY": "false",
        }

    def test_validate_runtime_security_config_requires_secure_prod_cors(self) -> None:
        with patch.dict(
            os.environ,
            self._valid_production_runtime_env(),
            clear=False,
        ):
            _validate_runtime_security_config()

        with patch.dict(
            os.environ,
            {
                **self._valid_production_runtime_env(),
                "CORS_ALLOW_ORIGINS": "*",
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                _validate_runtime_security_config()

    def test_validate_runtime_security_config_internet_exposed_requires_session_only_auth(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            self._valid_internet_exposed_runtime_env(),
            clear=False,
        ):
            _validate_runtime_security_config()

        with patch.dict(
            os.environ,
            {
                **self._valid_internet_exposed_runtime_env(),
                "AUTH_REQUIRED": "false",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "AUTH_REQUIRED=true"):
                _validate_runtime_security_config()

        for mode in ("hybrid", "api_key", ""):
            with self.subTest(auth_mode=mode):
                with patch.dict(
                    os.environ,
                    {
                        **self._valid_internet_exposed_runtime_env(),
                        "AUTH_MODE": mode,
                    },
                    clear=False,
                ):
                    with self.assertRaisesRegex(RuntimeError, "AUTH_MODE=session"):
                        _validate_runtime_security_config()

        with patch.dict(
            os.environ,
            {
                **self._valid_internet_exposed_runtime_env(),
                "AUTH_API_KEY": "legacy-shared-key",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "forbids AUTH_API_KEY"):
                _validate_runtime_security_config()

        with patch.dict(
            os.environ,
            {
                **self._valid_internet_exposed_runtime_env(),
                "ALLOW_INSECURE_REMOTE": "true",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "forbids ALLOW_INSECURE_REMOTE"):
                _validate_runtime_security_config()

    def test_validate_runtime_security_config_internet_exposed_requires_trusted_proxy_headers(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                **self._valid_internet_exposed_runtime_env(),
                "TRUST_PROXY_HEADERS": "false",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "TRUST_PROXY_HEADERS=true"):
                _validate_runtime_security_config()

    def test_validate_runtime_security_config_internet_exposed_requires_trusted_proxy_cidrs(
        self,
    ) -> None:
        base_env = self._valid_internet_exposed_runtime_env()
        with patch.dict(
            os.environ,
            {
                **base_env,
                "TRUSTED_PROXY_CIDRS": "127.0.0.1/32,::1/128",
            },
            clear=False,
        ):
            _validate_runtime_security_config()

        with patch.dict(
            os.environ,
            {
                **base_env,
                "TRUSTED_PROXY_CIDRS": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "TRUSTED_PROXY_CIDRS"):
                _validate_runtime_security_config()

        with patch.dict(
            os.environ,
            {
                **base_env,
                "TRUSTED_PROXY_CIDRS": "not-a-cidr",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "Invalid TRUSTED_PROXY_CIDRS"):
                _validate_runtime_security_config()

        with patch.dict(
            os.environ,
            {
                **base_env,
                "TRUSTED_PROXY_CIDRS": "0.0.0.0/0",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "catch-all"):
                _validate_runtime_security_config()

    def test_validate_runtime_security_config_requires_allowed_hosts(self) -> None:
        with patch.dict(
            os.environ,
            {
                **self._valid_production_runtime_env(),
                "ALLOWED_HOSTS": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "ALLOWED_HOSTS"):
                _validate_runtime_security_config()

        with patch.dict(
            os.environ,
            {
                **self._valid_internet_exposed_runtime_env(),
                "ALLOWED_HOSTS": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "ALLOWED_HOSTS"):
                _validate_runtime_security_config()

    def test_api_docs_are_disabled_by_default_for_production_and_public_exposure(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "test",
                "INTERNET_EXPOSED": "false",
                "API_DOCS_ENABLED": "",
            },
            clear=False,
        ):
            self.assertTrue(_api_docs_enabled())

        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "INTERNET_EXPOSED": "false",
                "API_DOCS_ENABLED": "",
            },
            clear=False,
        ):
            self.assertFalse(_api_docs_enabled())

        with patch.dict(
            os.environ,
            {
                "APP_ENV": "development",
                "INTERNET_EXPOSED": "true",
                "API_DOCS_ENABLED": "",
            },
            clear=False,
        ):
            self.assertFalse(_api_docs_enabled())

    def test_validate_runtime_security_config_internet_exposed_requires_enforced_csp(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                **self._valid_internet_exposed_runtime_env(),
                "CSP_REPORT_ONLY": "true",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "CSP_REPORT_ONLY=true"):
                _validate_runtime_security_config()

    def test_validate_runtime_security_config_forbids_public_api_docs(self) -> None:
        with patch.dict(
            os.environ,
            {
                **self._valid_production_runtime_env(),
                "API_DOCS_ENABLED": "true",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "API_DOCS_ENABLED=true"):
                _validate_runtime_security_config()

        with patch.dict(
            os.environ,
            {
                **self._valid_internet_exposed_runtime_env(),
                "API_DOCS_ENABLED": "true",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "API_DOCS_ENABLED=true"):
                _validate_runtime_security_config()

        with patch.dict(
            os.environ,
            {
                "APP_ENV": "development",
                "INTERNET_EXPOSED": "false",
                "MULTI_INSTANCE": "false",
                "API_DOCS_ENABLED": "true",
                "CSP_REPORT_ONLY": "true",
            },
            clear=False,
        ):
            _validate_runtime_security_config()

    def test_internet_exposed_requests_require_trusted_edge_proxy(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "development",
                "INTERNET_EXPOSED": "true",
                "TRUST_PROXY_HEADERS": "true",
                "TRUSTED_PROXY_CIDRS": "127.0.0.1/32",
                "ALLOWED_HOSTS": "gardenops.example.com",
            },
            clear=False,
        ):
            direct = _edge_proxy_violation_detail(self._edge_request())
            self.assertIsNotNone(direct)
            assert direct is not None
            self.assertIn("trusted edge proxy", direct)

            untrusted_proxy = _edge_proxy_violation_detail(
                self._edge_request(
                    client_host="203.0.113.4",
                    headers={
                        "x-forwarded-for": "198.51.100.7",
                        "x-forwarded-proto": "https",
                        "x-forwarded-host": "gardenops.example.com",
                    },
                ),
            )
            self.assertIsNotNone(untrusted_proxy)
            assert untrusted_proxy is not None
            self.assertIn("TRUSTED_PROXY_CIDRS", untrusted_proxy)

            wrong_host = _edge_proxy_violation_detail(
                self._edge_request(
                    headers={
                        "x-forwarded-for": "198.51.100.7",
                        "x-forwarded-proto": "https",
                        "x-forwarded-host": "evil.example",
                    },
                ),
            )
            self.assertIsNotNone(wrong_host)
            assert wrong_host is not None
            self.assertIn("ALLOWED_HOSTS", wrong_host)

            allowed = _edge_proxy_violation_detail(
                self._edge_request(
                    headers={
                        "x-forwarded-for": "198.51.100.7",
                        "x-forwarded-proto": "https",
                        "x-forwarded-host": "gardenops.example.com",
                    },
                ),
            )
            self.assertIsNone(allowed)

    def test_protected_auth_failures_still_enforce_edge_origin_first(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "development",
                "INTERNET_EXPOSED": "true",
                "TRUST_PROXY_HEADERS": "true",
                "TRUSTED_PROXY_CIDRS": "127.0.0.1/32",
                "ALLOWED_HOSTS": "gardenops.example.com",
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
            },
            clear=False,
        ):
            denied = self.client.get("/api/plots")

        self.assertEqual(denied.status_code, 403)
        self.assertIn("trusted edge proxy", denied.json()["detail"])

    def test_validate_runtime_security_config_requires_shared_rate_limit_backend(self) -> None:
        with patch.dict(
            os.environ,
            self._valid_internet_exposed_runtime_env(),
            clear=False,
        ):
            _validate_runtime_security_config()

        with patch.dict(
            os.environ,
            {
                **self._valid_internet_exposed_runtime_env(),
                "RATE_LIMIT_BACKEND": "memory",
                "RATE_LIMIT_REDIS_URL": "",
                "REDIS_URL": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "RATE_LIMIT_BACKEND=redis"):
                _validate_runtime_security_config()

        with patch.dict(
            os.environ,
            {
                "APP_ENV": "development",
                "MULTI_INSTANCE": "true",
                "INTERNET_EXPOSED": "false",
                "RATE_LIMIT_BACKEND": "memory",
                "RATE_LIMIT_REDIS_URL": "",
                "REDIS_URL": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "RATE_LIMIT_BACKEND=redis"):
                _validate_runtime_security_config()

        with patch.dict(
            os.environ,
            {
                "APP_ENV": "development",
                "MULTI_INSTANCE": "true",
                "INTERNET_EXPOSED": "false",
                "RATE_LIMIT_BACKEND": "redis",
                "RATE_LIMIT_REDIS_URL": "",
                "REDIS_URL": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "RATE_LIMIT_REDIS_URL or REDIS_URL"):
                _validate_runtime_security_config()

    def test_validate_runtime_security_config_production_requires_shared_rate_limit_backend(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            self._valid_production_runtime_env(),
            clear=False,
        ):
            _validate_runtime_security_config()

        with patch.dict(
            os.environ,
            {
                **self._valid_production_runtime_env(),
                "RATE_LIMIT_BACKEND": "memory",
                "RATE_LIMIT_REDIS_URL": "",
                "REDIS_URL": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "RATE_LIMIT_BACKEND=redis"):
                _validate_runtime_security_config()

    def test_rate_limit_backend_fails_closed_for_memory_backend_in_strict_modes(self) -> None:
        rate_limit_module.reset_rate_limits()
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "INTERNET_EXPOSED": "false",
                "MULTI_INSTANCE": "false",
                "RATE_LIMIT_BACKEND": "memory",
                "RATE_LIMIT_REDIS_URL": "",
                "REDIS_URL": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "RATE_LIMIT_BACKEND=redis"):
                rate_limit_module.ensure_backend_ready()
        rate_limit_module.reset_rate_limits()

    def test_validate_runtime_security_config_requires_env_mfa_secret_in_strict_modes(self) -> None:
        strict_env = self._valid_internet_exposed_runtime_env()
        strict_env["AUTH_MFA_SECRET_KEY"] = ""
        with patch.dict(os.environ, strict_env, clear=False):
            with self.assertRaisesRegex(RuntimeError, "AUTH_MFA_SECRET_KEY"):
                _validate_runtime_security_config()

        strict_env["AUTH_MFA_SECRET_KEY"] = "short-secret"
        with patch.dict(os.environ, strict_env, clear=False):
            with self.assertRaisesRegex(RuntimeError, "at least 32 characters"):
                _validate_runtime_security_config()

        strict_env["AUTH_MFA_SECRET_KEY"] = "generate-at-least-32-random-characters"
        with patch.dict(os.environ, strict_env, clear=False):
            with self.assertRaisesRegex(RuntimeError, "not a placeholder"):
                _validate_runtime_security_config()

        strict_env["AUTH_MFA_SECRET_KEY"] = "<generate-at-least-32-random-characters>"
        with patch.dict(os.environ, strict_env, clear=False):
            with self.assertRaisesRegex(RuntimeError, "not a placeholder"):
                _validate_runtime_security_config()

    def test_validate_runtime_security_config_requires_telemetry_privacy_salt(self) -> None:
        strict_env = {
            **self._valid_internet_exposed_runtime_env(),
            "SECURITY_TELEMETRY_WEBHOOK_URL": "https://telemetry.example.invalid/hooks",
            "SECURITY_TELEMETRY_BACKGROUND_EXPORT": "false",
            "SECURITY_TELEMETRY_PRIVACY_MODE": "minimized",
            "SECURITY_TELEMETRY_PRIVACY_SALT": "",
        }
        with patch.dict(os.environ, strict_env, clear=False):
            with self.assertRaisesRegex(RuntimeError, "SECURITY_TELEMETRY_PRIVACY_SALT"):
                _validate_runtime_security_config()

        strict_env["SECURITY_TELEMETRY_PRIVACY_SALT"] = "deployment-specific-salt-32chars-ok"
        with patch.dict(os.environ, strict_env, clear=False):
            _validate_runtime_security_config()

    @patch("gardenops.routers.ai.os.environ.get")
    def test_chat_no_api_key(self, mock_env: MagicMock) -> None:
        mock_env.return_value = ""
        response = self.client.post(
            "/api/ai/garden-chat",
            json={"message": "hello", "history": []},
        )
        self.assertEqual(response.status_code, 503)

    def test_ai_garden_context_is_scoped_to_active_garden(self) -> None:
        gid1, gid2, username, _password = self._setup_admin_two_gardens()
        conn = db.get_db()
        try:
            user_row = conn.execute(
                "SELECT id FROM auth_users WHERE username = %s",
                (username,),
            ).fetchone()
            assert user_row is not None
            uid = int(user_row["id"])
            conn.execute(
                """
                INSERT INTO plots (
                    plot_id, garden_id, zone_code, zone_name, plot_number,
                    grid_row, grid_col, sub_zone, notes, color
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                ("CTX-G1", gid1, "C", "Context One", 1, 11, 11, "", "private plot note", None),
            )
            conn.execute(
                """
                INSERT INTO plots (
                    plot_id, garden_id, zone_code, zone_name, plot_number,
                    grid_row, grid_col, sub_zone, notes, color
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                ("CTX-G2", gid2, "C", "Context Two", 1, 12, 12, "", "", None),
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s), (%s, %s, %s)
                """,
                ("CTX-G1", uid, gid1, "CTX-G2", uid, gid2),
            )
            conn.execute(
                "INSERT INTO plants (plt_id, name, category) VALUES (%s,%s,%s), (%s,%s,%s)",
                ("PLT-CTX-G1", "Scoped Rosemary", "urter", "PLT-CTX-G2", "Other Rosemary", "urter"),
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s), (%s, %s, %s)
                """,
                ("PLT-CTX-G1", uid, gid1, "PLT-CTX-G2", uid, gid2),
            )
            conn.execute(
                "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES (%s,%s,%s), (%s,%s,%s)",
                ("CTX-G1", "PLT-CTX-G1", 1, "CTX-G2", "PLT-CTX-G2", 1),
            )
            conn.commit()

            context = AuthContext(
                user_id=uid,
                username=username,
                role="admin",
                auth_type="session",
                garden_id=gid1,
                garden_role="admin",
            )
            summary = build_garden_context(conn, context)
        finally:
            db.return_db(conn)

        self.assertIn("Scoped Rosemary", summary)
        self.assertIn("CTX-G1", summary)
        self.assertNotIn("Other Rosemary", summary)
        self.assertNotIn("CTX-G2", summary)
        self.assertNotIn("private plot note", summary)

    def test_security_metrics_include_provider_budget_snapshot(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="ai_metrics_admin",
                password=strong_password("adminpass123"),
                role="admin",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        response_block = type("TextBlock", (), {"type": "text", "text": "Mulch helps."})()
        response_payload = type("AnthropicResponse", (), {"content": [response_block]})()
        mocked_client = MagicMock()
        mocked_client.messages.create.return_value = response_payload

        with (
            patch.dict(
                os.environ,
                {
                    "AUTH_REQUIRED": "true",
                    "AUTH_MODE": "session",
                    "AUTH_API_KEY": "",
                    "AI_PROVIDER": "anthropic",
                    "ANTHROPIC_API_KEY": "test-key",
                },
                clear=False,
            ),
            patch(
                "gardenops.services.ai_provider.Anthropic",
                return_value=mocked_client,
            ),
        ):
            client = self._new_client()
            _, csrf = self._login_session("ai_metrics_admin", "adminpass123", client=client)
            headers = self._session_headers(csrf)

            chat = client.post(
                "/api/ai/garden-chat",
                headers=headers,
                json={"message": "Give me one tip", "history": []},
            )
            self.assertEqual(chat.status_code, 200)

            metrics = client.get("/api/auth/security-metrics", headers=headers)

        self.assertEqual(metrics.status_code, 200)
        payload = metrics.json()
        self.assertIn("provider_limits", payload)
        features = {
            feature["feature"]: feature
            for feature in payload["provider_limits"].get("features", [])
        }
        self.assertIn("ai-garden-chat", features)
        self.assertEqual(features["ai-garden-chat"]["user_total_requests"], 1)
        self.assertIsNotNone(features["ai-garden-chat"]["top_user_scope"])

    def test_audit_events_enqueue_security_telemetry_when_enabled(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="telemetry_audit_admin",
                password=strong_password("adminpass123"),
                role="admin",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "SECURITY_TELEMETRY_WEBHOOK_URL": "https://telemetry.example.invalid/hooks",
                "SECURITY_TELEMETRY_BACKGROUND_EXPORT": "false",
            },
            clear=False,
        ):
            client = self._new_client()
            self._login_session("telemetry_audit_admin", "adminpass123", client=client)

            conn = db.get_db()
            try:
                row = conn.execute(
                    """
                    SELECT event_kind, payload_json
                    FROM security_telemetry_outbox
                    WHERE event_kind = 'audit_event'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                ).fetchone()
            finally:
                db.return_db(conn)

        self.assertIsNotNone(row)
        payload = json.loads(str(row["payload_json"]))
        self.assertEqual(payload["event_kind"], "audit_event")
        self.assertEqual(payload["payload"]["path"], "/api/auth/login")

    def test_external_log_redaction_strips_path_queries_and_secrets(self) -> None:
        redacted = redact_external_log_text(
            "GET /api/exports/tasks?token=secret&garden_id=42 "
            "Authorization: Bearer abcdefghijklmnop",
        )
        self.assertIn("/api/exports/tasks?[REDACTED]", redacted)
        self.assertIn("Bearer [REDACTED_TOKEN]", redacted)
        self.assertNotIn("token=secret", redacted)
        self.assertNotIn("abcdefghijklmnop", redacted)

    def test_security_telemetry_minimized_hashes_identifiers_and_redacts_text(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SECURITY_TELEMETRY_WEBHOOK_URL": "https://telemetry.example.invalid/hooks",
                "SECURITY_TELEMETRY_BACKGROUND_EXPORT": "false",
                "SECURITY_TELEMETRY_PRIVACY_MODE": "minimized",
                "SECURITY_TELEMETRY_PRIVACY_SALT": "test-telemetry-salt",
            },
            clear=False,
        ):
            enqueue_security_telemetry(
                "audit_event",
                {
                    "actor_user_id": 123,
                    "actor_username": "alice",
                    "garden_id": 45,
                    "remote_host": "203.0.113.9",
                    "path": "/api/exports/tasks?token=secret&garden_id=45",
                    "detail": "Authorization Bearer abcdefghijklmnop PASSWORD=hunter2",
                    "top_user_scope": "user-123",
                    "nested": [{"scope_id": "garden-45", "username": "bob"}],
                },
            )

            conn = db.get_db()
            try:
                row = conn.execute(
                    """
                    SELECT payload_json
                    FROM security_telemetry_outbox
                    WHERE event_kind = 'audit_event'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                ).fetchone()
            finally:
                db.return_db(conn)

        self.assertIsNotNone(row)
        envelope = json.loads(str(row["payload_json"]))
        payload = envelope["payload"]
        self.assertEqual(envelope["privacy_mode"], "minimized")
        self.assertEqual(payload["path"], "/api/exports/tasks?[REDACTED]")
        self.assertTrue(payload["actor_user_id"].startswith("sha256:"))
        self.assertTrue(payload["actor_username"].startswith("sha256:"))
        self.assertTrue(payload["garden_id"].startswith("sha256:"))
        self.assertTrue(payload["remote_host"].startswith("sha256:"))
        self.assertTrue(payload["top_user_scope"].startswith("sha256:"))
        self.assertTrue(payload["nested"][0]["scope_id"].startswith("sha256:"))
        self.assertTrue(payload["nested"][0]["username"].startswith("sha256:"))
        self.assertIn("Bearer [REDACTED_TOKEN]", payload["detail"])
        self.assertIn("PASSWORD=[REDACTED]", payload["detail"])
        serialized = json.dumps(envelope, sort_keys=True)
        for raw in (
            "alice",
            "bob",
            "203.0.113.9",
            "user-123",
            "garden-45",
            "token=secret",
            "abcdefghijklmnop",
            "hunter2",
        ):
            self.assertNotIn(raw, serialized)

    def test_security_telemetry_drain_posts_webhook_and_clears_outbox(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SECURITY_TELEMETRY_WEBHOOK_URL": "https://telemetry.example.invalid/hooks",
                "SECURITY_TELEMETRY_BEARER_TOKEN": "test-token",
                "SECURITY_TELEMETRY_BACKGROUND_EXPORT": "false",
            },
            clear=False,
        ):
            enqueue_security_telemetry(
                "audit_event",
                {"path": "/api/plots/import", "status_code": 200},
            )

            captured: dict[str, object] = {}

            def _fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
                captured["url"] = request.full_url
                captured["authorization"] = request.headers.get("Authorization")
                captured["body"] = request.data.decode("utf-8")
                captured["timeout"] = timeout
                return self._DummyResponse(status=202)

            class _FakeOpener:
                def open(self, request, timeout=0):  # type: ignore[no-untyped-def]
                    return _fake_urlopen(request, timeout=timeout)

            with patch(
                "gardenops.security_telemetry.urllib.request.build_opener",
                return_value=_FakeOpener(),
            ):
                result = drain_security_telemetry_once()

        self.assertEqual(result["delivered"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["pending"], 0)
        self.assertEqual(captured["url"], "https://telemetry.example.invalid/hooks")
        self.assertEqual(captured["authorization"], "Bearer test-token")
        self.assertEqual(captured["timeout"], 5)
        payload = json.loads(str(captured["body"]))
        self.assertEqual(payload["event_kind"], "audit_event")

    def test_security_telemetry_uses_taillight_log_format_when_configured(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SECURITY_TELEMETRY_WEBHOOK_URL": "",
                "SECURITY_TELEMETRY_BEARER_TOKEN": "",
                "SECURITY_TELEMETRY_WEBHOOK_FORMAT": "",
                "SECURITY_TELEMETRY_BACKGROUND_EXPORT": "false",
                "TAILLIGHT_URL": "https://logs.example.invalid/ingest",
                "TAILLIGHT_API_KEY": "taillight-token",
            },
            clear=False,
        ):
            enqueue_security_telemetry(
                "audit_event",
                {"path": "/api/plots/import", "status_code": 200},
            )

            captured: dict[str, object] = {}

            def _fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
                captured["url"] = request.full_url
                captured["authorization"] = request.headers.get("Authorization")
                captured["body"] = request.data.decode("utf-8")
                captured["timeout"] = timeout
                return self._DummyResponse(status=202)

            class _FakeOpener:
                def open(self, request, timeout=0):  # type: ignore[no-untyped-def]
                    return _fake_urlopen(request, timeout=timeout)

            with patch(
                "gardenops.security_telemetry.urllib.request.build_opener",
                return_value=_FakeOpener(),
            ):
                result = drain_security_telemetry_once()

        self.assertEqual(result["delivered"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["pending"], 0)
        self.assertEqual(captured["url"], "https://logs.example.invalid/ingest")
        self.assertEqual(captured["authorization"], "Bearer taillight-token")
        payload = json.loads(str(captured["body"]))
        self.assertEqual(len(payload["logs"]), 1)
        log_entry = payload["logs"][0]
        self.assertEqual(log_entry["service"], "gardenops")
        self.assertEqual(log_entry["component"], "security-telemetry")
        self.assertEqual(log_entry["attrs"]["event_kind"], "audit_event")

    def test_security_telemetry_rejects_http_webhooks_outside_test(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "SECURITY_TELEMETRY_WEBHOOK_URL": "http://telemetry.example.invalid/hooks",
                "SECURITY_TELEMETRY_BACKGROUND_EXPORT": "false",
            },
            clear=False,
        ):
            enqueue_security_telemetry(
                "audit_event",
                {"path": "/api/plots/import", "status_code": 200},
            )

            result = drain_security_telemetry_once()

        self.assertEqual(result["delivered"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["pending"], 1)

    def test_security_telemetry_rejects_redirecting_webhooks(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SECURITY_TELEMETRY_WEBHOOK_URL": "https://telemetry.example.invalid/hooks",
                "SECURITY_TELEMETRY_BACKGROUND_EXPORT": "false",
            },
            clear=False,
        ):
            enqueue_security_telemetry(
                "audit_event",
                {"path": "/api/plots/import", "status_code": 200},
            )

            def _redirecting_open(request, timeout=0):  # type: ignore[no-untyped-def]
                raise urllib.error.HTTPError(
                    request.full_url,
                    302,
                    "Found",
                    {"Location": "https://redirect.example.invalid/hooks"},
                    None,
                )

            class _RedirectingOpener:
                def open(self, request, timeout=0):  # type: ignore[no-untyped-def]
                    return _redirecting_open(request, timeout=timeout)

            with patch(
                "gardenops.security_telemetry.urllib.request.build_opener",
                return_value=_RedirectingOpener(),
            ):
                result = drain_security_telemetry_once()

        self.assertEqual(result["delivered"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["pending"], 1)

    def test_security_telemetry_drain_does_not_hold_write_lock_during_delivery(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="telemetry_lock_admin",
                password=strong_password("adminpass123"),
                role="admin",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "SECURITY_TELEMETRY_WEBHOOK_URL": "https://telemetry.example.invalid/hooks",
                "SECURITY_TELEMETRY_BACKGROUND_EXPORT": "false",
            },
            clear=False,
        ):
            enqueue_security_telemetry(
                "audit_event",
                {"path": "/api/auth/mfa/totp/confirm", "status_code": 200},
            )

            entered = threading.Event()
            release = threading.Event()
            result_holder: dict[str, dict[str, int]] = {}

            def _slow_deliver(_payload_json: str) -> None:
                entered.set()
                self.assertTrue(release.wait(timeout=2))

            def _run_drain() -> None:
                result_holder["result"] = drain_security_telemetry_once(limit=1)

            with patch("gardenops.security_telemetry._deliver_payload", side_effect=_slow_deliver):
                drain_thread = threading.Thread(target=_run_drain, daemon=True)
                drain_thread.start()
                self.assertTrue(entered.wait(timeout=1))

                writer = db.get_db()
                try:
                    writer.execute(
                        "UPDATE auth_users SET last_login_at = now()::text WHERE username = %s",
                        ("telemetry_lock_admin",),
                    )
                    writer.commit()
                finally:
                    writer.close()

                release.set()
                drain_thread.join(timeout=2)

        self.assertFalse(drain_thread.is_alive())
        self.assertEqual(result_holder["result"]["delivered"], 1)
        self.assertEqual(result_holder["result"]["failed"], 0)
        self.assertEqual(result_holder["result"]["pending"], 0)

    def test_security_metrics_include_telemetry_exporter_status(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="telemetry_status_admin",
                password=strong_password("adminpass123"),
                role="admin",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "SECURITY_TELEMETRY_WEBHOOK_URL": "https://telemetry.example.invalid/hooks",
                "SECURITY_TELEMETRY_BACKGROUND_EXPORT": "false",
                "SECURITY_TELEMETRY_SNAPSHOT_INTERVAL_SECONDS": "90",
                "SECURITY_TELEMETRY_POLL_SECONDS": "7",
            },
            clear=False,
        ):
            enqueue_security_telemetry(
                "audit_event",
                {"path": "/api/auth/revoke-all-sessions", "status_code": 200},
            )
            self.assertTrue(ensure_security_metrics_snapshot_enqueued(force=True))

            client = self._new_client()
            _, csrf = self._login_session("telemetry_status_admin", "adminpass123", client=client)
            headers = self._session_headers(csrf)

            metrics = client.get("/api/auth/security-metrics", headers=headers)

        self.assertEqual(metrics.status_code, 200)
        payload = metrics.json()
        exporter = payload["exporter"]
        self.assertTrue(exporter["enabled"])
        self.assertEqual(exporter["destination"], "https://telemetry.example.invalid")
        self.assertGreaterEqual(exporter["pending_count"], 1)
        self.assertEqual(exporter["snapshot_interval_seconds"], 90)
        self.assertEqual(exporter["poll_interval_seconds"], 7)

    def test_ai_plant_link_rejects_redirect_to_disallowed_host(self) -> None:
        def _redirect_then_open(request, timeout=0):  # type: ignore[no-untyped-def]
            raise urllib.error.HTTPError(
                request.full_url,
                302,
                "Found",
                {"Location": "https://example.invalid/not-allowed"},
                None,
            )

        class _RedirectingOpener:
            def open(self, request, timeout=0):  # type: ignore[no-untyped-def]
                return _redirect_then_open(request, timeout=timeout)

        with patch(
            "gardenops.routers.ai.urllib.request.build_opener",
            return_value=_RedirectingOpener(),
        ):
            validated = _validate_plant_link(
                "https://rhs.org.uk/plants/123/example/details",
                latin="Rosa canina",
            )

        self.assertEqual(validated, "")

    def test_ai_plant_link_allows_redirect_within_allowlist(self) -> None:
        html = b"<html><body><h1>Rosa canina</h1></body></html>"
        calls = {"count": 0}

        def _redirect_then_success(request, timeout=0):  # type: ignore[no-untyped-def]
            calls["count"] += 1
            if calls["count"] == 1:
                raise urllib.error.HTTPError(
                    request.full_url,
                    302,
                    "Found",
                    {"Location": "/plants/123/rosa-canina/details"},
                    None,
                )
            return self._DummyResponse(body=html)

        class _AllowlistedRedirectOpener:
            def open(self, request, timeout=0):  # type: ignore[no-untyped-def]
                return _redirect_then_success(request, timeout=timeout)

        with patch(
            "gardenops.routers.ai.urllib.request.build_opener",
            return_value=_AllowlistedRedirectOpener(),
        ):
            validated = _validate_plant_link(
                "https://rhs.org.uk/plants/123/example/details",
                latin="Rosa canina",
            )

        self.assertEqual(validated, "https://rhs.org.uk/plants/123/rosa-canina/details")

    def test_journal_viewer_cannot_write(self) -> None:
        """Viewer users cannot create, update, or delete journal entries."""
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            conn = db.get_db()
            create_user(
                conn, username="viewer_j", password=strong_password("viewerpass"), role="viewer"
            )
            create_user(
                conn, username="editor_j", password=strong_password("editorpass"), role="editor"
            )
            conn.commit()
            db.return_db(conn)

            client = self._new_client()
            _, editor_csrf = self._login_session("editor_j", "editorpass", client=client)
            editor_h = self._session_headers(editor_csrf)
            r = client.post(
                "/api/journal",
                headers=editor_h,
                json={
                    "event_type": "observed",
                    "occurred_on": "2026-03-10",
                },
            )
            self.assertEqual(r.status_code, 201)
            entry_id = r.json()["id"]

            viewer_client = self._new_client()
            _, viewer_csrf = self._login_session("viewer_j", "viewerpass", client=viewer_client)
            viewer_h = self._session_headers(viewer_csrf)

            r = viewer_client.get("/api/journal", headers=viewer_h)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["total"], 1)

            r = viewer_client.post(
                "/api/journal",
                headers=viewer_h,
                json={
                    "event_type": "planted",
                    "occurred_on": "2026-03-10",
                },
            )
            self.assertEqual(r.status_code, 403)

            r = viewer_client.patch(
                f"/api/journal/{entry_id}",
                headers=viewer_h,
                json={"title": "hack"},
            )
            self.assertEqual(r.status_code, 403)

            r = viewer_client.delete(f"/api/journal/{entry_id}", headers=viewer_h)
            self.assertEqual(r.status_code, 403)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

    def test_task_auth_viewer_cannot_write(self) -> None:
        """Viewer users get 403 on task mutations."""
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            self._create_test_user("viewer_t", "viewerpass", role="viewer")
            self._create_test_user("editor_t", "editorpass", role="editor")

            client = self._new_client()
            _, editor_csrf = self._login_session("editor_t", "editorpass", client=client)
            editor_h = self._session_headers(editor_csrf)
            r = client.post(
                "/api/tasks",
                headers=editor_h,
                json={
                    "task_type": "water",
                    "title": "Editor task",
                    "due_on": "2026-04-01",
                },
            )
            self.assertEqual(r.status_code, 201)
            task_id = r.json()["id"]

            viewer_client = self._new_client()
            _, viewer_csrf = self._login_session("viewer_t", "viewerpass", client=viewer_client)
            viewer_h = self._session_headers(viewer_csrf)

            # Viewer can read
            r = viewer_client.get("/api/tasks", headers=viewer_h)
            self.assertEqual(r.status_code, 200)

            # Viewer cannot create
            r = viewer_client.post(
                "/api/tasks",
                headers=viewer_h,
                json={
                    "task_type": "prune",
                    "title": "Viewer task",
                    "due_on": "2026-04-01",
                },
            )
            self.assertEqual(r.status_code, 403)

            # Viewer cannot update
            r = viewer_client.patch(
                f"/api/tasks/{task_id}",
                headers=viewer_h,
                json={"title": "hack"},
            )
            self.assertEqual(r.status_code, 403)

            # Viewer cannot delete
            r = viewer_client.delete(f"/api/tasks/{task_id}", headers=viewer_h)
            self.assertEqual(r.status_code, 403)

            # Viewer cannot perform actions
            r = viewer_client.post(
                f"/api/tasks/{task_id}/action",
                headers=viewer_h,
                json={"action": "complete"},
            )
            self.assertEqual(r.status_code, 403)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"
