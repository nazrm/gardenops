from __future__ import annotations

import asyncio
import os
import time
import unittest
from unittest.mock import patch

from mcp import Client
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from gardenops.mcp_server import StaticBearerMiddleware, _run_tool_async, create_mcp_runtime
from gardenops.services.assistant_models import AssistantIntent
from gardenops.services.integration_config import validate_integration_config
from tests.base import BaseApiTest

TOKEN = "mcp-test-token-0123456789abcdef0123"  # push-sanitizer: allow SECRET_ASSIGNMENT


class TestMCPAssistant(unittest.TestCase):
    def test_sync_tool_work_does_not_block_event_loop(self) -> None:
        async def exercise() -> None:
            def slow_operation(_operation, **_kwargs):  # type: ignore[no-untyped-def]
                time.sleep(0.1)
                return "done"

            with patch("gardenops.mcp_server._run_tool", side_effect=slow_operation):
                task = asyncio.create_task(_run_tool_async(lambda _db, _binding: None))  # type: ignore[arg-type]
                await asyncio.sleep(0.01)
                self.assertFalse(task.done())
                self.assertEqual(await task, "done")

        asyncio.run(exercise())

    def test_disabled_runtime_is_not_created(self) -> None:
        with patch.dict(os.environ, {"MCP_ENABLED": "false"}, clear=False):
            self.assertIsNone(create_mcp_runtime())

    def test_bearer_middleware_rejects_missing_and_wrong_tokens(self) -> None:
        app = Starlette(
            routes=[
                Route(
                    "/mcp",
                    endpoint=StaticBearerMiddleware(PlainTextResponse("ok")),
                    methods=["POST"],
                )
            ]
        )
        with (
            patch.dict(
                os.environ,
                {"MCP_ENABLED": "true", "MCP_BEARER_TOKEN": TOKEN},
                clear=False,
            ),
            TestClient(app) as client,
        ):
            self.assertEqual(client.post("/mcp").status_code, 401)
            self.assertEqual(
                client.post("/mcp", headers={"Authorization": "Bearer wrong"}).status_code,
                401,
            )
            accepted = client.post("/mcp", headers={"Authorization": f"Bearer {TOKEN}"})
            self.assertEqual(accepted.status_code, 200)
            self.assertEqual(accepted.history, [])

    def test_exact_six_tool_surface_has_structured_outputs(self) -> None:
        async def inspect_tools() -> None:
            with patch.dict(
                os.environ,
                {"MCP_ENABLED": "true", "MCP_BEARER_TOKEN": TOKEN},
                clear=False,
            ):
                runtime = create_mcp_runtime()
                assert runtime is not None
                async with Client(runtime.server) as client:
                    tools = await client.list_tools()
            self.assertEqual(
                {tool.name for tool in tools.tools},
                {
                    "assistant_process_text",
                    "assistant_analyze_capture",
                    "assistant_continue",
                    "assistant_get",
                    "assistant_apply",
                    "assistant_cancel",
                },
            )
            self.assertTrue(all(tool.output_schema for tool in tools.tools))

        asyncio.run(inspect_tools())


class TestMCPWorkflow(BaseApiTest):
    def test_client_runs_without_transport_settings_and_replays_safely(self) -> None:
        async def exercise() -> None:
            runtime = create_mcp_runtime()
            assert runtime is not None
            args = {
                "source_client_id": "local-assistant",
                "source_actor_id": "owner",
                "source_event_id": "question-1",
                "text": "What is growing?",
                "occurred_on": "2026-10-20",
            }
            async with Client(runtime.server) as client:
                first = await client.call_tool("assistant_process_text", args)
                self.assertFalse(first.is_error)
                result = first.structured_content
                assert result is not None
                self.assertEqual(result["state"], "answer")
                self.assertEqual(result["message"], "Your garden answer")
                repeated = await client.call_tool("assistant_process_text", args)
                self.assertEqual(repeated.structured_content, result)
                fetched = await client.call_tool(
                    "assistant_get", {"request_id": result["request_id"]}
                )
                self.assertEqual(fetched.structured_content, result)
                rejected = await client.call_tool(
                    "assistant_process_text", {**args, "source_client_id": "other-client"}
                )
                assert rejected.structured_content is not None
                self.assertEqual(rejected.structured_content["state"], "error")

        with (
            patch.dict(
                os.environ,
                {
                    "MCP_ENABLED": "true",
                    "MCP_BEARER_TOKEN": TOKEN,
                    "MCP_CLIENT_ID": "local-assistant",
                    "MCP_ACTOR_ID": "owner",
                    "MCP_USERNAME": "test_admin",
                    "MCP_GARDEN_SLUG": "default",
                },
            ),
            patch(
                "gardenops.services.assistant._interpret",
                return_value=AssistantIntent(
                    intent="question",
                    confidence=1,
                ),
            ),
            patch(
                "gardenops.services.assistant.answer_garden_question",
                return_value="Your garden answer",
            ) as answer,
        ):
            validate_integration_config()
            asyncio.run(exercise())
            answer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
