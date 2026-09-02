from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import unicodedata
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from gardenops.rate_limit import env_int
from gardenops.services.assistant_models import AssistantResult
from gardenops.services.integration_config import (
    MatrixRuntimeConfig,
    matrix_enabled,
    matrix_runtime_config,
)
from gardenops.services.media_store import media_upload_max_bytes

logger = logging.getLogger(__name__)
_REFERENCE_RE = re.compile(r"\bGO-[A-Z0-9]{6}\b", re.IGNORECASE)
_ASSISTANT_TOOLS = {
    "assistant_process_text",
    "assistant_analyze_capture",
    "assistant_continue",
    "assistant_get",
    "assistant_apply",
    "assistant_cancel",
}


@dataclass(frozen=True)
class ParsedCommand:
    kind: Literal["save", "cancel", "choice", "edit", "help", "status", "request"]
    text: str = ""
    reference: str = ""


@dataclass(frozen=True)
class QueuedEvent:
    room: Any
    event: Any


def extract_reference(text: str) -> str:
    match = _REFERENCE_RE.search(text)
    return match.group(0).upper() if match else ""


def reply_event_id(event: Any) -> str:
    content = getattr(event, "source", {}).get("content", {})
    relation = content.get("m.relates_to", {})
    if relation.get("rel_type") == "m.replace":
        return ""
    return str(relation.get("m.in_reply_to", {}).get("event_id") or "")


def is_edit_event(event: Any) -> bool:
    content = getattr(event, "source", {}).get("content", {})
    return content.get("m.relates_to", {}).get("rel_type") == "m.replace"


def strip_trigger(text: str, bot_user_id: str) -> str:
    stripped = text.strip()
    if stripped.casefold().startswith("!garden"):
        return stripped[len("!garden") :].lstrip(" :,-")
    if bot_user_id.casefold() in stripped.casefold():
        start = stripped.casefold().find(bot_user_id.casefold())
        return (stripped[:start] + stripped[start + len(bot_user_id) :]).strip(" :,-")
    return stripped


def is_triggered(
    event: Any,
    *,
    mode: str,
    bot_user_id: str,
    reply_to_bot: bool,
) -> bool:
    if mode == "all" or reply_to_bot:
        return True
    body = str(getattr(event, "body", "") or "").strip()
    if body.casefold().startswith("!garden") or bot_user_id.casefold() in body.casefold():
        return True
    mentions = (
        getattr(event, "source", {}).get("content", {}).get("m.mentions", {}).get("user_ids", [])
    )
    return bot_user_id in mentions


def parse_command(text: str) -> ParsedCommand:
    cleaned = text.strip()
    reference = extract_reference(cleaned)
    without_reference = _REFERENCE_RE.sub("", cleaned).strip()
    folded = without_reference.casefold()
    if folded == "save":
        return ParsedCommand("save", reference=reference)
    if folded == "cancel":
        return ParsedCommand("cancel", reference=reference)
    if folded.isdigit():
        return ParsedCommand("choice", text=folded, reference=reference)
    if folded.startswith("edit ") and without_reference[5:].strip():
        return ParsedCommand("edit", text=without_reference[5:].strip(), reference=reference)
    if folded == "help":
        return ParsedCommand("help")
    if folded == "status":
        return ParsedCommand("status", reference=reference)
    return ParsedCommand("request", text=cleaned)


def render_result(result: AssistantResult) -> str:
    lines = [result.message.strip()]
    if result.choices and not any(
        f"{index}." in result.message for index in range(1, len(result.choices) + 1)
    ):
        lines.extend(
            f"{index}. {choice.label}" + (f" - {choice.description}" if choice.description else "")
            for index, choice in enumerate(result.choices, 1)
        )
    if result.state in {"needs_input", "proposal"} and result.reference not in "\n".join(lines):
        lines.append(f"Ref: {result.reference}")
    return "\n".join(line for line in lines if line)


def _capture_max_bytes() -> int:
    return min(
        media_upload_max_bytes(),
        env_int("MAX_AI_PHOTO_BODY_BYTES", 5 * 1024 * 1024),
    )


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, ValueError) and "Image" in str(exc):
        return str(exc)
    if isinstance(exc, httpx2.HTTPStatusError):
        status = exc.response.status_code
        if status == 413:
            return "That image is too large for GardenOps."
        if status == 429:
            return "GardenOps is busy. Please try again shortly."
        if status in {401, 403}:
            return "GardenOps integration authorization failed."
    if isinstance(exc, httpx2.TimeoutException):
        return "GardenOps did not respond in time. Please try again."
    return "GardenOps could not process that message. Please try again."


class MatrixBot:
    def __init__(self, config: MatrixRuntimeConfig, matrix_client: Any, mcp_client: Client) -> None:
        self.config = config
        self.matrix = matrix_client
        self.mcp = mcp_client
        self.queue: asyncio.Queue[QueuedEvent] = asyncio.Queue(maxsize=config.max_pending_events)
        self.request_by_reference: dict[str, str] = {}
        self.request_by_reply_event: dict[str, str] = {}

    async def on_event(self, room: Any, event: Any) -> None:
        if str(getattr(room, "room_id", "")) != self.config.room_id:
            return
        if str(getattr(event, "sender", "")) != self.config.allowed_sender:
            return
        if str(getattr(event, "sender", "")) == self.config.user_id or is_edit_event(event):
            return
        await self.queue.put(QueuedEvent(room=room, event=event))

    async def consume(self) -> None:
        while True:
            item = await self.queue.get()
            try:
                await self._handle(item.room, item.event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Matrix event processing failed (%s)", type(exc).__name__)
                try:
                    await self._send(_friendly_error(exc))
                except Exception as send_exc:
                    logger.error("Matrix error response failed (%s)", type(send_exc).__name__)
            finally:
                self.queue.task_done()

    async def _handle(self, room: Any, event: Any) -> None:
        reply_request = await self._request_from_reply(event)
        body = strip_trigger(str(getattr(event, "body", "") or ""), self.config.user_id)
        command = parse_command(body)
        explicitly_addressed_command = command.kind in {"help", "status"} or bool(
            command.reference and command.kind in {"save", "cancel", "choice", "edit"}
        )
        if not explicitly_addressed_command and not is_triggered(
            event,
            mode=self.config.trigger_mode,
            bot_user_id=self.config.user_id,
            reply_to_bot=bool(reply_request),
        ):
            return
        event_id = str(getattr(event, "event_id", ""))
        if command.kind == "help":
            await self._send(
                "Ask a garden question or describe an observation, harvest, issue, or completed "
                "task. You can also add, move, or remove a plant. Reply with a choice number, "
                "`edit ...`, `save`, or `cancel`."
            )
            return
        if command.kind == "status" and not command.reference and not reply_request:
            await self._send("GardenOps is connected and ready.")
            return

        request_id = (
            reply_request
            or self.request_by_reference.get(command.reference, "")
            or command.reference
        )
        if command.kind in {"save", "cancel", "choice", "edit", "status"}:
            if not request_id:
                await self._send("Reply to a GardenOps message or include its GO reference.")
                return
            tool = {
                "save": "assistant_apply",
                "cancel": "assistant_cancel",
                "choice": "assistant_continue",
                "edit": "assistant_continue",
                "status": "assistant_get",
            }[command.kind]
            arguments: dict[str, Any] = {"request_id": request_id}
            if command.kind != "status":
                arguments["source_event_id"] = event_id
            if command.kind in {"choice", "edit"}:
                arguments["text"] = command.text
            result = await self._call(tool, arguments)
        elif self._is_image(event):
            payload, mime_type, filename = await self._download_image(event)
            asset_id = await self._upload_capture(
                payload,
                mime_type=mime_type,
                filename=filename,
                event_id=event_id,
            )
            result = await self._call(
                "assistant_analyze_capture",
                {
                    "source_room_id": self.config.room_id,
                    "source_event_id": event_id,
                    "source_sender_id": self.config.allowed_sender,
                    "capture_asset_id": asset_id,
                    "caption": body,
                    "occurred_on": self._occurred_on(event),
                },
            )
        else:
            if not command.text:
                await self._send("Add a question or garden observation after the trigger.")
                return
            result = await self._call(
                "assistant_process_text",
                {
                    "source_room_id": self.config.room_id,
                    "source_event_id": event_id,
                    "source_sender_id": self.config.allowed_sender,
                    "text": command.text,
                    "occurred_on": self._occurred_on(event),
                },
            )
        sent_event_id = await self._send(render_result(result))
        self.request_by_reference[result.reference] = result.request_id
        if sent_event_id:
            self.request_by_reply_event[sent_event_id] = result.request_id

    async def _request_from_reply(self, event: Any) -> str:
        reply_to = reply_event_id(event)
        if not reply_to:
            return ""
        cached = self.request_by_reply_event.get(reply_to, "")
        if cached:
            return cached
        try:
            response = await self.matrix.room_get_event(self.config.room_id, reply_to)
        except Exception:
            return ""
        referenced = getattr(response, "event", None)
        if referenced is None or str(getattr(referenced, "sender", "")) != self.config.user_id:
            return ""
        body = str(getattr(referenced, "body", "") or "")
        if not body:
            body = str(getattr(referenced, "source", {}).get("content", {}).get("body", ""))
        reference = extract_reference(body)
        if not reference:
            return ""
        request_id = self.request_by_reference.get(reference, reference)
        self.request_by_reply_event[reply_to] = request_id
        return request_id

    @staticmethod
    def _is_image(event: Any) -> bool:
        return event.__class__.__name__ in {"RoomMessageImage", "RoomEncryptedImage"}

    def _occurred_on(self, event: Any) -> str:
        timestamp = int(getattr(event, "server_timestamp", 0) or 0)
        try:
            timezone = ZoneInfo(self.config.timezone)
        except ZoneInfoNotFoundError:
            timezone = UTC
        instant = datetime.fromtimestamp(timestamp / 1000, UTC) if timestamp else datetime.now(UTC)
        return instant.astimezone(timezone).date().isoformat()

    async def _call(self, name: str, arguments: dict[str, Any]) -> AssistantResult:
        for attempt in range(2):
            try:
                response = await self.mcp.call_tool(name, arguments)
                if response.is_error or not response.structured_content:
                    raise RuntimeError("GardenOps MCP returned an invalid result")
                return AssistantResult.model_validate(response.structured_content)
            except Exception:
                if attempt:
                    raise
                await asyncio.sleep(0.5)
        raise RuntimeError("GardenOps MCP returned an invalid result")

    async def _send(self, body: str) -> str:
        response = await self.matrix.room_send(
            self.config.room_id,
            "m.room.message",
            {"msgtype": "m.text", "body": body},
            # The worker has no interactive device-verification flow. The room and
            # sender allowlists remain the authorization boundary for this MVP.
            ignore_unverified_devices=True,
        )
        return str(getattr(response, "event_id", "") or "")

    async def _download_image(self, event: Any) -> tuple[bytes, str, str]:
        info = getattr(event, "source", {}).get("content", {}).get("info", {})
        declared_size = int(info.get("size") or 0)
        max_bytes = _capture_max_bytes()
        if declared_size > max_bytes:
            raise ValueError("Image exceeds GardenOps upload size limit")
        response = await self.matrix.download(str(getattr(event, "url", "")))
        payload = bytes(getattr(response, "body", b""))
        if event.__class__.__name__ == "RoomEncryptedImage":
            from nio.crypto.attachments import decrypt_attachment

            payload = decrypt_attachment(
                payload,
                str(getattr(event, "key", {}).get("k") or ""),
                str(getattr(event, "hashes", {}).get("sha256") or ""),
                str(getattr(event, "iv", "")),
            )
        if not payload or len(payload) > max_bytes:
            raise ValueError("Image is empty or exceeds GardenOps upload size limit")
        mime_type = str(
            getattr(event, "mimetype", "")
            or info.get("mimetype")
            or getattr(response, "content_type", "")
            or "application/octet-stream"
        )
        return payload, mime_type, str(getattr(event, "body", "") or "matrix-image")

    async def _upload_capture(
        self,
        payload: bytes,
        *,
        mime_type: str,
        filename: str,
        event_id: str,
    ) -> str:
        parsed = urlsplit(self.config.mcp_url)
        url = urlunsplit(
            (parsed.scheme, parsed.netloc, "/api/integrations/matrix/captures", "", "")
        )
        async with httpx2.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.post(
                url,
                content=payload,
                headers={
                    "Authorization": f"Bearer {self.config.mcp_token}",
                    "Content-Type": mime_type,
                    "X-Matrix-Room-Id": self.config.room_id,
                    "X-Matrix-Event-Id": event_id,
                    "X-Matrix-Sender": self.config.allowed_sender,
                    "X-Original-Filename": _ascii_filename(filename),
                },
            )
        response.raise_for_status()
        return str(response.json()["capture_asset_id"])


def _ascii_filename(filename: str) -> str:
    name = Path(filename).name.replace("\r", "").replace("\n", "")
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", normalized).strip(".-")
    return (cleaned or "matrix-image")[:255]


def _restore_matrix_login(matrix: Any, config: MatrixRuntimeConfig) -> None:
    matrix.restore_login(config.user_id, config.device_id, config.access_token)


async def _verify_mcp(client: Client) -> None:
    last_error: Exception | None = None
    for delay in (0, 1, 2, 4, 8):
        if delay:
            await asyncio.sleep(delay)
        try:
            result = await client.list_tools(cache_mode="refresh")
            names = {tool.name for tool in result.tools}
            if not _ASSISTANT_TOOLS.issubset(names):
                raise RuntimeError("GardenOps assistant tools are unavailable")
            return
        except Exception as exc:
            last_error = exc
    raise RuntimeError("GardenOps MCP is unavailable") from last_error


async def run() -> None:
    if not matrix_enabled():
        raise RuntimeError("MATRIX_ENABLED must be true to run the Matrix worker")
    config = matrix_runtime_config()
    try:
        ZoneInfo(config.timezone)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError("MATRIX_TIMEZONE is not a valid timezone") from exc
    Path(config.store_path).mkdir(parents=True, exist_ok=True)

    from nio import (
        AsyncClient,
        AsyncClientConfig,
        RoomEncryptedImage,
        RoomMessageImage,
        RoomMessageText,
        SyncError,
    )

    matrix_config = AsyncClientConfig(
        encryption_enabled=config.e2ee,
        store_sync_tokens=True,
    )
    matrix = AsyncClient(
        config.homeserver_url,
        config.user_id,
        config.device_id,
        config.store_path,
        matrix_config,
    )
    _restore_matrix_login(matrix, config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    async with AsyncExitStack() as stack:
        stack.push_async_callback(matrix.close)
        http_client = await stack.enter_async_context(
            httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {config.mcp_token}"},
                timeout=90,
                follow_redirects=False,
            )
        )
        transport = streamable_http_client(
            config.mcp_url,
            http_client=http_client,
            terminate_on_close=False,
        )
        mcp_client = await stack.enter_async_context(Client(transport))
        await _verify_mcp(mcp_client)
        initial = await matrix.sync(timeout=0, full_state=True)
        if isinstance(initial, SyncError):
            raise RuntimeError("Initial Matrix sync failed")
        bot = MatrixBot(config, matrix, mcp_client)
        for event_type in (RoomMessageText, RoomMessageImage, RoomEncryptedImage):
            matrix.add_event_callback(bot.on_event, event_type)
        consumer = asyncio.create_task(bot.consume(), name="matrix-event-consumer")
        sync_task = asyncio.create_task(
            matrix.sync_forever(timeout=config.sync_timeout_ms),
            name="matrix-sync",
        )
        stop_task = asyncio.create_task(stop.wait(), name="matrix-stop")
        try:
            done, _ = await asyncio.wait(
                {sync_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if sync_task in done and not stop.is_set():
                await sync_task
        finally:
            matrix.stop_sync_forever()
            stop_task.cancel()
            sync_task.cancel()
            for task in (stop_task, sync_task):
                with suppress(asyncio.CancelledError):
                    await task
            try:
                await asyncio.wait_for(bot.queue.join(), timeout=75)
            except TimeoutError:
                logger.warning("Timed out draining the Matrix event queue during shutdown")
            consumer.cancel()
            with suppress(asyncio.CancelledError):
                await consumer


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
