from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

from gardenops.agent_api_policy import (
    agent_api_confirmation_required,
    agent_api_path_garden_id,
    agent_api_request_allowed,
)
from gardenops.agent_mcp_stdio import (
    AgentBridgeConfig,
    _build_url_opener,
    _load_token,
    _NoRedirectHandler,
    _read_staged_image,
    create_server,
    request_api,
    request_plant_identification,
)
from gardenops.security import AuthContext
from gardenops.services.integration_config import AssistantBinding, agent_api_auth_context
from tests.base import BaseApiTest

TOKEN = "gardenops-agent-test-token-0123456789abcdef"  # push-sanitizer: allow SECRET_ASSIGNMENT


class _Response:
    status = 200
    headers = {"content-type": "application/json"}

    def __init__(self, payload: bytes) -> None:
        self._payload = io.BytesIO(payload)

    def read(self, size: int = -1) -> bytes:
        return self._payload.read(size)

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *_args):  # type: ignore[no-untyped-def]
        return False


def _binding() -> AssistantBinding:
    context = AuthContext(
        user_id=7,
        username="test_gardener",
        role="admin",
        auth_type="session",
        garden_id=11,
        garden_role="admin",
        subscription_tier="pro",
    )
    return AssistantBinding(
        room_id="!garden:example.test",
        sender_id="@gardener:example.test",
        username="test_gardener",
        garden_slug="home",
        user_id=7,
        garden_id=11,
        context=context,
    )


class TestAgentApiPolicy(unittest.TestCase):
    def test_allows_normal_garden_reads_and_writes(self) -> None:
        self.assertTrue(agent_api_request_allowed("GET", "/api/gardens"))
        self.assertTrue(agent_api_request_allowed("GET", "/api/plants/search"))
        self.assertTrue(agent_api_request_allowed("POST", "/api/ai/identify-plant"))
        self.assertTrue(agent_api_request_allowed("GET", "/api/dashboard/today"))
        self.assertTrue(agent_api_request_allowed("POST", "/api/tasks/task_123/action"))
        self.assertTrue(agent_api_request_allowed("PATCH", "/api/plants/plt_123"))
        self.assertTrue(agent_api_request_allowed("DELETE", "/api/issues/issue_123"))
        self.assertEqual(agent_api_path_garden_id("/api/gardens/42/settings"), 42)
        self.assertIsNone(agent_api_path_garden_id("/api/plants"))

    def test_unknown_descendants_and_impure_notifications_are_closed(self) -> None:
        self.assertFalse(agent_api_request_allowed("GET", "/api/plants/future/route"))
        self.assertFalse(agent_api_request_allowed("POST", "/api/notifications/generate"))
        self.assertFalse(agent_api_request_allowed("GET", "/api/gardens/012/settings"))
        self.assertFalse(agent_api_request_allowed("GET", "/api/gardens/+12/settings"))

    def test_bulk_spatial_and_delete_changes_require_confirmation(self) -> None:
        protected = (
            ("POST", "/api/tasks/refresh-descriptions"),
            ("POST", "/api/tasks/generate"),
            ("POST", "/api/workflows/start"),
            ("PATCH", "/api/plots/plants/seen-growing"),
            ("POST", "/api/gardens/11/map-objects/map_1/containers/from-plots"),
            ("POST", "/api/notifications/read-all"),
            ("DELETE", "/api/journal/jrn_1"),
        )
        for method, path in protected:
            with self.subTest(method=method, path=path):
                self.assertTrue(agent_api_confirmation_required(method, path))
        self.assertFalse(agent_api_confirmation_required("POST", "/api/journal"))

    def test_blocks_security_platform_and_unsafe_routes(self) -> None:
        denied = (
            ("GET", "/api/auth/users"),
            ("GET", "/api/admin/provider-settings"),
            ("POST", "/api/gardens"),
            ("DELETE", "/api/gardens/1"),
            ("POST", "/api/plants/import-csv"),
            ("GET", "/api/exports/backup"),
            ("POST", "/api/calendar/subscriptions"),
            ("POST", "/api/notifications/run-maintenance"),
            ("GET", "/api/plants/../auth/users"),
            ("GET", "/api/plants%2f..%2fauth/users"),
            ("GET", "/api/plantsevil"),
        )
        for method, path in denied:
            with self.subTest(method=method, path=path):
                self.assertFalse(agent_api_request_allowed(method, path))

    def test_agent_auth_requires_authenticated_source_provenance(self) -> None:
        db = MagicMock()
        request = SimpleNamespace(
            method="GET",
            url=SimpleNamespace(path="/api/plants"),
            headers={"authorization": f"Bearer {TOKEN}"},
            client=SimpleNamespace(host="127.0.0.1"),
        )
        with (
            patch.dict(
                os.environ,
                {"MCP_ENABLED": "true", "MCP_BEARER_TOKEN": TOKEN},
            ),
            patch(
                "gardenops.services.integration_config.resolve_assistant_binding",
                return_value=_binding(),
            ),
        ):
            with self.assertRaisesRegex(Exception, "authenticated source provenance"):
                agent_api_auth_context(db, cast(Any, request))

        with patch.dict(
            os.environ,
            {"MCP_ENABLED": "false", "MCP_BEARER_TOKEN": TOKEN},
        ):
            self.assertIsNone(agent_api_auth_context(db, cast(Any, request)))

        request.client.host = "192.0.2.10"
        with (
            patch.dict(
                os.environ,
                {"MCP_ENABLED": "true", "MCP_BEARER_TOKEN": TOKEN},
            ),
            self.assertRaisesRegex(Exception, "loopback-only"),
        ):
            agent_api_auth_context(db, cast(Any, request))

        request.client.host = "127.0.0.1"
        request.url.path = "/api/auth/users"
        with (
            patch.dict(
                os.environ,
                {"MCP_ENABLED": "true", "MCP_BEARER_TOKEN": TOKEN},
            ),
            self.assertRaisesRegex(Exception, "path is not allowed"),
        ):
            agent_api_auth_context(db, cast(Any, request))


class TestAgentMcpBridge(unittest.TestCase):
    def test_token_file_must_be_private_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token_path = os.path.join(directory, "token")
            with open(token_path, "w", encoding="utf-8") as handle:
                handle.write(TOKEN)
            os.chmod(token_path, 0o600)
            self.assertEqual(_load_token(token_path), TOKEN)
            os.chmod(token_path, 0o644)
            with self.assertRaisesRegex(RuntimeError, "group/world"):
                _load_token(token_path)

    def test_bridge_sends_bearer_and_offline_operation_identity(self) -> None:
        config = AgentBridgeConfig("http://127.0.0.1:8000/", TOKEN)
        captured: dict[str, object] = {}

        def fake_open(request, timeout):  # type: ignore[no-untyped-def]
            captured["url"] = request.full_url
            captured["authorization"] = request.headers["Authorization"]
            captured["operation"] = request.headers["X-offline-operation-id"]
            captured["method"] = request.method
            captured["body"] = request.data
            captured["timeout"] = timeout
            return _Response(b'{"status":"ok"}')

        with patch("gardenops.agent_mcp_stdio._URL_OPENER.open", side_effect=fake_open):
            result = request_api(
                config,
                method="POST",
                path="/api/journal",
                body={"title": "Pruned"},
                operation_id="5d242a20-61c3-4a13-871a-cb77465b630b",
            )
        self.assertTrue(result["ok"])
        self.assertEqual(captured["url"], "http://127.0.0.1:8000/api/journal")
        self.assertEqual(captured["authorization"], f"Bearer {TOKEN}")
        self.assertEqual(captured["operation"], "5d242a20-61c3-4a13-871a-cb77465b630b")
        self.assertEqual(captured["method"], "POST")

    def test_bridge_opener_disables_proxies_and_redirects(self) -> None:
        with (
            patch("gardenops.agent_mcp_stdio.urllib.request.ProxyHandler") as proxy,
            patch("gardenops.agent_mcp_stdio.urllib.request.build_opener") as build,
        ):
            _build_url_opener()
        proxy.assert_called_once_with({})
        self.assertIs(build.call_args.args[0], proxy.return_value)
        self.assertIsInstance(build.call_args.args[1], _NoRedirectHandler)

    def test_bridge_refuses_non_allowlisted_and_absolute_paths(self) -> None:
        config = AgentBridgeConfig("http://127.0.0.1:8000/", TOKEN)
        with self.assertRaises(PermissionError):
            request_api(config, method="GET", path="/api/auth/users")
        with self.assertRaises(ValueError):
            request_api(config, method="GET", path="https://example.test/api/plants")

    def test_identification_reads_only_supported_images_inside_media_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = os.path.join(directory, "media")
            os.mkdir(root)
            image_path = os.path.join(root, "plant.jpg")
            with open(image_path, "wb") as handle:
                handle.write(b"\xff\xd8\xfftest-image")
            config = AgentBridgeConfig(
                "http://127.0.0.1:8000/",
                TOKEN,
                media_root=Path(root),
            )
            payload, content_type = _read_staged_image(config, image_path)
            self.assertEqual(payload, b"\xff\xd8\xfftest-image")
            self.assertEqual(content_type, "image/jpeg")

            outside_path = os.path.join(directory, "outside.jpg")
            with open(outside_path, "wb") as handle:
                handle.write(b"\xff\xd8\xffoutside")
            with self.assertRaisesRegex(PermissionError, "configured Matrix media"):
                _read_staged_image(config, outside_path)

    def test_identification_recovers_unique_mistyped_generated_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            message_dir = Path(directory, "openclaw-staged-message")
            message_dir.mkdir()
            actual = message_dir / ("input-plant-photo---36e0d9e4-2150-4c93-81cd-9e157c283dc3.jpg")
            actual.write_bytes(b"\xff\xd8\xfftest-image")
            mistyped = message_dir / (
                "input-plant-photo---36e0d9e4-2150-4c91-89ff-1e5b6ca08974.jpg"
            )
            config = AgentBridgeConfig(
                "http://127.0.0.1:8000/",
                TOKEN,
                media_root=Path(directory),
            )

            payload, content_type = _read_staged_image(config, str(mistyped))

            self.assertEqual(payload, b"\xff\xd8\xfftest-image")
            self.assertEqual(content_type, "image/jpeg")

    def test_identification_does_not_guess_ambiguous_staged_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            message_dir = Path(directory, "openclaw-staged-message")
            message_dir.mkdir()
            for generated_id in (
                "36e0d9e4-2150-4c93-81cd-9e157c283dc3",
                "4ca42b72-fbe8-4997-8876-b0f0c037812b",
            ):
                (message_dir / f"input-plant-photo---{generated_id}.jpg").write_bytes(
                    b"\xff\xd8\xfftest-image"
                )
            mistyped = message_dir / (
                "input-plant-photo---36e0d9e4-2150-4c91-89ff-1e5b6ca08974.jpg"
            )
            config = AgentBridgeConfig(
                "http://127.0.0.1:8000/",
                TOKEN,
                media_root=Path(directory),
            )

            with self.assertRaises(FileNotFoundError):
                _read_staged_image(config, str(mistyped))

    def test_identification_posts_raw_image_to_gardenops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = os.path.join(directory, "plant.png")
            with open(image_path, "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\nimage")
            config = AgentBridgeConfig(
                "http://127.0.0.1:8000/",
                TOKEN,
                media_root=Path(directory),
            )
            captured: dict[str, object] = {}

            def fake_open(request, timeout):  # type: ignore[no-untyped-def]
                captured["url"] = request.full_url
                captured["authorization"] = request.headers["Authorization"]
                captured["content_type"] = request.headers["Content-type"]
                captured["body"] = request.data
                captured["timeout"] = timeout
                return _Response(b'{"candidates":[]}')

            with patch("gardenops.agent_mcp_stdio._URL_OPENER.open", side_effect=fake_open):
                result = request_plant_identification(
                    config,
                    image_path=image_path,
                    organ="flower",
                )
            self.assertTrue(result["ok"])
            self.assertEqual(
                captured["url"],
                "http://127.0.0.1:8000/api/ai/identify-plant?organ=flower",
            )
            self.assertEqual(captured["authorization"], f"Bearer {TOKEN}")
            self.assertEqual(captured["content_type"], "image/png")
            self.assertEqual(captured["body"], b"\x89PNG\r\n\x1a\nimage")

    def test_mcp_surface_fails_closed_without_source_provenance(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "authenticated source provenance"):
            create_server(AgentBridgeConfig("http://127.0.0.1:8000/", TOKEN))


class TestAgentApiEndToEnd(BaseApiTest):
    def _agent_env(self) -> dict[str, str]:
        return {
            "AUTH_REQUIRED": "true",
            "AUTH_MODE": "session",
            "MCP_ENABLED": "true",
            "MCP_BEARER_TOKEN": TOKEN,
            "MATRIX_ROOM_ID": "!garden:example.test",
            "MATRIX_ALLOWED_SENDER": "@gardener:example.test",
            "MATRIX_GARDENOPS_USERNAME": "test_admin",
            "MATRIX_GARDEN_SLUG": "default",
        }

    def test_agent_token_cannot_read_or_write_without_source_provenance(self) -> None:
        headers = {"authorization": f"Bearer {TOKEN}"}
        with patch.dict(os.environ, self._agent_env()):
            plants = self.client.get("/api/plants?limit=10", headers=headers)
            self.assertEqual(plants.status_code, 403, plants.text)

            created = self.client.post(
                "/api/journal",
                headers={
                    **headers,
                    "x-offline-operation-id": "5d242a20-61c3-4a13-871a-cb77465b630b",
                },
                json={
                    "event_type": "observed",
                    "occurred_on": "2026-09-03",
                    "title": "Agent observation",
                    "notes": "Created through the bounded MCP principal",
                    "plant_ids": ["PLT-TEST"],
                    "plot_ids": [],
                },
            )
            self.assertEqual(created.status_code, 403, created.text)

    def test_agent_token_cannot_reach_platform_or_cross_garden_routes(self) -> None:
        headers = {"authorization": f"Bearer {TOKEN}"}
        with patch.dict(os.environ, self._agent_env()):
            users = self.client.get("/api/auth/users", headers=headers)
            self.assertEqual(users.status_code, 403)
            cross_garden = self.client.get(
                "/api/plants",
                headers={**headers, "x-garden-id": "999999999"},
            )
            self.assertEqual(cross_garden.status_code, 403)
            gardens = self.client.get("/api/gardens", headers=headers)
            self.assertEqual(gardens.status_code, 403, gardens.text)
            create_garden = self.client.post(
                "/api/gardens",
                headers=headers,
                json={"name": "Forbidden", "slug": "forbidden"},
            )
            self.assertEqual(create_garden.status_code, 403)
            cross_garden_url = self.client.get(
                "/api/gardens/999999999/settings",
                headers=headers,
            )
            self.assertEqual(cross_garden_url.status_code, 403)
            noncanonical_garden_url = self.client.get(
                f"/api/gardens/0{self._get_default_garden_id()}/settings",
                headers=headers,
            )
            self.assertEqual(noncanonical_garden_url.status_code, 403)

    def test_agent_loopback_transport_works_behind_production_edge_policy(self) -> None:
        production_env = {
            **self._agent_env(),
            "APP_ENV": "production",
            "INTERNET_EXPOSED": "true",
            "TRUST_PROXY_HEADERS": "true",
        }
        with patch.dict(os.environ, production_env):
            accepted = self.client.get(
                "/api/plants?limit=10",
                headers={"authorization": f"Bearer {TOKEN}"},
            )
            self.assertEqual(accepted.status_code, 403, accepted.text)

            rejected = self.client.get(
                "/api/plants?limit=10",
                headers={"authorization": "Bearer wrong-token"},
            )
            self.assertEqual(rejected.status_code, 403, rejected.text)

    def test_agent_token_can_reach_plant_identification_without_writing(self) -> None:
        with patch.dict(os.environ, self._agent_env()):
            response = self.client.post(
                "/api/ai/identify-plant?organ=flower",
                headers={
                    "authorization": f"Bearer {TOKEN}",
                    "content-type": "image/jpeg",
                },
                content=b"not-an-image",
            )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("source provenance", response.json()["detail"].lower())


if __name__ == "__main__":
    unittest.main()
