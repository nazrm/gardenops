from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gardenops.matrix_bot import (
    MatrixBot,
    extract_reference,
    is_triggered,
    parse_command,
    render_result,
    reply_event_id,
    strip_trigger,
)
from gardenops.services.assistant_models import AssistantChoice, AssistantResult
from gardenops.services.integration_config import MatrixRuntimeConfig


class Event:
    def __init__(self, body: str, content: dict | None = None) -> None:
        self.body = body
        self.source = {"content": content or {}}


class Room:
    room_id = "!garden:example.org"


class FakeMatrix:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.referenced_event = None

    async def room_send(self, _room_id, _event_type, content, **_kwargs):  # type: ignore[no-untyped-def]
        self.sent.append(str(content["body"]))
        return SimpleNamespace(event_id=f"$bot-{len(self.sent)}")

    async def room_get_event(self, _room_id, _event_id):  # type: ignore[no-untyped-def]
        return SimpleNamespace(event=self.referenced_event)


class FakeMCP:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, arguments):  # type: ignore[no-untyped-def]
        self.calls.append((name, arguments))
        return SimpleNamespace(
            is_error=False,
            structured_content={
                "state": "applied" if name == "assistant_apply" else "proposal",
                "request_id": "asst_a1b2c3-test",
                "reference": "GO-A1B2C3",
                "message": "Saved" if name == "assistant_apply" else "Ready",
                "choices": [],
                "proposal": {},
                "records": [],
                "retryable": False,
            },
        )


class TestMatrixBot(unittest.TestCase):
    @staticmethod
    def _config() -> MatrixRuntimeConfig:
        return MatrixRuntimeConfig(
            homeserver_url="https://matrix.example.org",
            user_id="@gardenops:example.org",
            access_token="secret",
            device_id="DEVICE",
            store_path="/tmp/matrix-test",
            e2ee=True,
            room_id="!garden:example.org",
            allowed_sender="@owner:example.org",
            gardenops_username="owner",
            garden_slug="default",
            trigger_mode="mention",
            timezone="Europe/Oslo",
            capture_ttl_days=7,
            sync_timeout_ms=30_000,
            max_pending_events=1,
            mcp_url="http://127.0.0.1:8000/mcp",
            mcp_token="secret",
        )

    def test_commands_are_deterministic(self) -> None:
        self.assertEqual(parse_command("save GO-A1B2C3").kind, "save")
        self.assertEqual(parse_command("cancel GO-A1B2C3").kind, "cancel")
        self.assertEqual(parse_command("2").kind, "choice")
        self.assertEqual(parse_command("edit quantity is 3 kg").text, "quantity is 3 kg")
        self.assertEqual(extract_reference("status go-a1b2c3"), "GO-A1B2C3")
        self.assertEqual(parse_command("please save this").kind, "request")

    def test_trigger_and_reply_rules(self) -> None:
        bot_id = "@gardenops:example.org"
        self.assertTrue(
            is_triggered(
                Event("!garden what needs water?"),
                mode="mention",
                bot_user_id=bot_id,
                reply_to_bot=False,
            )
        )
        self.assertFalse(
            is_triggered(
                Event("what needs water?"),
                mode="mention",
                bot_user_id=bot_id,
                reply_to_bot=False,
            )
        )
        self.assertTrue(
            is_triggered(
                Event("1"),
                mode="mention",
                bot_user_id=bot_id,
                reply_to_bot=True,
            )
        )
        reply = Event(
            "1",
            {"m.relates_to": {"m.in_reply_to": {"event_id": "$bot-response"}}},
        )
        self.assertEqual(reply_event_id(reply), "$bot-response")
        self.assertEqual(strip_trigger("!garden: status", bot_id), "status")

    def test_needs_input_render_always_includes_reference_and_choices(self) -> None:
        result = AssistantResult(
            state="needs_input",
            request_id="asst_123",
            reference="GO-A1B2C3",
            message="Which plant?",
            choices=[AssistantChoice(value="PLT-1|B1", label="Rose - B1")],
        )
        rendered = render_result(result)
        self.assertIn("1. Rose - B1", rendered)
        self.assertTrue(rendered.endswith("Ref: GO-A1B2C3"))

    def test_event_ingress_accepts_only_exact_binding_and_stays_bounded(self) -> None:
        bot = MatrixBot(self._config(), object(), object())  # type: ignore[arg-type]

        async def enqueue() -> None:
            accepted = Event("!garden status")
            accepted.sender = "@owner:example.org"
            await bot.on_event(Room(), accepted)
            wrong_sender = Event("!garden status")
            wrong_sender.sender = "@other:example.org"
            await bot.on_event(Room(), wrong_sender)
            overflow = Event("!garden another")
            overflow.sender = "@owner:example.org"
            await bot.on_event(Room(), overflow)

        asyncio.run(enqueue())
        self.assertEqual(bot.queue.qsize(), 1)

    def test_explicit_save_reference_routes_without_a_mention(self) -> None:
        matrix = FakeMatrix()
        mcp = FakeMCP()
        bot = MatrixBot(self._config(), matrix, mcp)  # type: ignore[arg-type]
        event = Event("save GO-A1B2C3")
        event.sender = "@owner:example.org"
        event.event_id = "$save"

        asyncio.run(bot._handle(Room(), event))

        self.assertEqual(mcp.calls[0][0], "assistant_apply")
        self.assertEqual(mcp.calls[0][1]["request_id"], "GO-A1B2C3")
        self.assertEqual(mcp.calls[0][1]["source_event_id"], "$save")

    def test_reply_to_pre_restart_bot_message_recovers_visible_reference(self) -> None:
        matrix = FakeMatrix()
        matrix.referenced_event = SimpleNamespace(
            sender="@gardenops:example.org",
            body="Which plant?\nRef: GO-A1B2C3",
        )
        mcp = FakeMCP()
        bot = MatrixBot(self._config(), matrix, mcp)  # type: ignore[arg-type]
        event = Event(
            "2",
            {"m.relates_to": {"m.in_reply_to": {"event_id": "$old-bot-message"}}},
        )
        event.sender = "@owner:example.org"
        event.event_id = "$choice"

        asyncio.run(bot._handle(Room(), event))

        self.assertEqual(mcp.calls[0][0], "assistant_continue")
        self.assertEqual(mcp.calls[0][1]["request_id"], "GO-A1B2C3")
        self.assertEqual(mcp.calls[0][1]["text"], "2")

    def test_image_event_uploads_then_calls_capture_analysis(self) -> None:
        matrix = FakeMatrix()
        mcp = FakeMCP()
        bot = MatrixBot(self._config(), matrix, mcp)  # type: ignore[arg-type]
        image_type = type("RoomMessageImage", (Event,), {})
        event = image_type("!garden This is flowering")
        event.sender = "@owner:example.org"
        event.event_id = "$image"
        event.server_timestamp = 1_788_329_600_000
        with (
            patch.object(
                bot,
                "_download_image",
                AsyncMock(return_value=(b"image", "image/jpeg", "flower.jpg")),
            ),
            patch.object(bot, "_upload_capture", AsyncMock(return_value="media_1")),
        ):
            asyncio.run(bot._handle(Room(), event))

        self.assertEqual(mcp.calls[0][0], "assistant_analyze_capture")
        self.assertEqual(mcp.calls[0][1]["capture_asset_id"], "media_1")
        self.assertEqual(mcp.calls[0][1]["caption"], "This is flowering")


if __name__ == "__main__":
    unittest.main()
