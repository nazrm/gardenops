from __future__ import annotations

import json
import os
import stat
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit
from uuid import UUID, uuid4

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from gardenops.agent_api_policy import (
    agent_api_confirmation_required,
    agent_api_request_allowed,
)

_MAX_RESPONSE_BYTES = 1_048_576
_MAX_REQUEST_BYTES = 262_144
_MAX_IMAGE_BYTES = 5 * 1024 * 1024


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _build_url_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirectHandler(),
    )


_URL_OPENER = _build_url_opener()


@dataclass(frozen=True, slots=True)
class AgentBridgeConfig:
    base_url: str
    token: str
    timeout_seconds: float = 45.0
    media_root: Path | None = None


def _load_token(path_value: str) -> str:
    path = Path(path_value)
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise RuntimeError("GardenOps MCP token path must be a regular file")
        if details.st_mode & 0o077:
            raise RuntimeError("GardenOps MCP token file must not be group/world accessible")
        token = os.read(descriptor, 4096).decode("utf-8").strip()
    finally:
        os.close(descriptor)
    if len(token) < 32:
        raise RuntimeError("GardenOps MCP token is missing or too short")
    return token


def load_config() -> AgentBridgeConfig:
    raw_url = os.environ.get("GARDENOPS_API_URL", "http://127.0.0.1:8000").strip()
    parsed = urlsplit(raw_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/")
    ):
        raise RuntimeError("GARDENOPS_API_URL must be an origin-only loopback HTTP URL")
    token_file = os.environ.get("GARDENOPS_MCP_TOKEN_FILE", "").strip()
    if not token_file:
        raise RuntimeError("GARDENOPS_MCP_TOKEN_FILE must be configured")
    media_root_value = os.environ.get("GARDENOPS_MCP_MEDIA_ROOT", "").strip()
    media_root = Path(media_root_value).resolve(strict=True) if media_root_value else None
    if media_root is not None and not media_root.is_dir():
        raise RuntimeError("GARDENOPS_MCP_MEDIA_ROOT must be a directory")
    return AgentBridgeConfig(
        base_url=raw_url.rstrip("/") + "/",
        token=_load_token(token_file),
        media_root=media_root,
    )


def _validate_path(method: str, value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1536:
        raise ValueError("path must contain 1 to 1536 characters")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment or len(parsed.query) > 1024:
        raise ValueError("path must be a local API path with an optional bounded query")
    if not agent_api_request_allowed(method, parsed.path):
        raise PermissionError("This GardenOps API method/path is not allowlisted")
    return value


def _decode_response(response: Any) -> dict[str, Any]:
    payload = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("GardenOps response exceeded the 1 MiB MCP limit")
    content_type = str(response.headers.get("content-type", "")).split(";", 1)[0].strip()
    text = payload.decode("utf-8", errors="replace")
    if content_type == "application/json" or content_type.endswith("+json"):
        try:
            data: Any = json.loads(text) if text else None
        except json.JSONDecodeError:
            data = {"raw": text}
    else:
        data = {"text": text, "content_type": content_type or "text/plain"}
    return {"status_code": int(response.status), "data": data}


def request_api(
    config: AgentBridgeConfig,
    *,
    method: str,
    path: str,
    body: dict[str, Any] | list[Any] | None = None,
    operation_id: str = "",
) -> dict[str, Any]:
    normalized_method = method.strip().upper()
    normalized_path = _validate_path(normalized_method, path)
    encoded_body: bytes | None = None
    if body is not None:
        encoded_body = json.dumps(
            body,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded_body) > _MAX_REQUEST_BYTES:
            raise ValueError("request body exceeds the 256 KiB MCP limit")
    request_id = str(uuid4())
    headers = {
        "accept": "application/json, text/plain;q=0.8, text/csv;q=0.7",
        "authorization": f"Bearer {config.token}",
        "user-agent": "gardenops-openclaw-mcp/1.0",
        "x-request-id": request_id,
    }
    if encoded_body is not None:
        headers["content-type"] = "application/json"
    if operation_id:
        headers["x-offline-operation-id"] = operation_id
    request = urllib.request.Request(
        urljoin(config.base_url, normalized_path.lstrip("/")),
        data=encoded_body,
        headers=headers,
        method=normalized_method,
    )
    try:
        with _URL_OPENER.open(request, timeout=config.timeout_seconds) as response:
            result = _decode_response(response)
    except urllib.error.HTTPError as exc:
        result = _decode_response(exc)
    return {
        "ok": 200 <= int(result["status_code"]) < 300,
        "request_id": request_id,
        **result,
    }


def _read_staged_image(config: AgentBridgeConfig, image_path: str) -> tuple[bytes, str]:
    if config.media_root is None:
        raise RuntimeError("GARDENOPS_MCP_MEDIA_ROOT must be configured")
    requested = Path(image_path)
    if not requested.is_absolute():
        raise ValueError("image_path must be absolute")
    root = config.media_root.resolve(strict=True)
    try:
        relative = requested.relative_to(root)
    except ValueError as exc:
        raise PermissionError(
            "image_path must be inside the configured Matrix media directory"
        ) from exc
    if any(part in {".", ".."} for part in relative.parts):
        raise PermissionError("image_path must be inside the configured Matrix media directory")
    cursor = root
    for part in relative.parts[:-1]:
        cursor = cursor / part
        if stat.S_ISLNK(os.lstat(cursor).st_mode):
            raise PermissionError("image_path may not contain symbolic links")

    if not requested.exists():
        name_prefix, separator, generated_suffix = requested.name.rpartition("---")
        generated_id, extension = os.path.splitext(generated_suffix)
        try:
            UUID(generated_id)
        except ValueError:
            separator = ""
        if separator and name_prefix.startswith("input-"):
            candidates: list[Path] = []
            for entry in os.scandir(requested.parent):
                candidate_prefix, candidate_separator, candidate_suffix = entry.name.rpartition(
                    "---"
                )
                candidate_id, candidate_extension = os.path.splitext(candidate_suffix)
                if (
                    candidate_separator
                    and candidate_prefix == name_prefix
                    and candidate_extension.lower() == extension.lower()
                    and entry.is_file(follow_symlinks=False)
                ):
                    try:
                        UUID(candidate_id)
                    except ValueError:
                        continue
                    candidates.append(Path(entry.path))
            if len(candidates) == 1:
                requested = candidates[0]

    if stat.S_ISLNK(os.lstat(requested).st_mode):
        raise PermissionError("image_path may not contain symbolic links")
    resolved = requested.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise PermissionError("image_path must be inside the configured Matrix media directory")

    descriptor = os.open(resolved, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise ValueError("image_path must reference a regular file")
        if details.st_size <= 0 or details.st_size > _MAX_IMAGE_BYTES:
            raise ValueError("image must contain 1 byte to 5 MiB")
        chunks: list[bytes] = []
        remaining = _MAX_IMAGE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(payload) != details.st_size:
        raise RuntimeError("image changed while it was being read")

    if payload.startswith(b"\xff\xd8\xff"):
        content_type = "image/jpeg"
    elif payload.startswith(b"\x89PNG\r\n\x1a\n"):
        content_type = "image/png"
    elif len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        content_type = "image/webp"
    else:
        raise ValueError("image must be JPEG, PNG, or WebP")
    return payload, content_type


def request_plant_identification(
    config: AgentBridgeConfig,
    *,
    image_path: str,
    organ: str,
) -> dict[str, Any]:
    payload, content_type = _read_staged_image(config, image_path)
    path = "/api/ai/identify-plant?" + urllib.parse.urlencode({"organ": organ})
    _validate_path("POST", path)
    request_id = str(uuid4())
    request = urllib.request.Request(
        urljoin(config.base_url, path.lstrip("/")),
        data=payload,
        headers={
            "accept": "application/json",
            "authorization": f"Bearer {config.token}",
            "content-type": content_type,
            "user-agent": "gardenops-openclaw-mcp/1.0",
            "x-request-id": request_id,
        },
        method="POST",
    )
    try:
        with _URL_OPENER.open(request, timeout=config.timeout_seconds) as response:
            result = _decode_response(response)
    except urllib.error.HTTPError as exc:
        result = _decode_response(exc)
    return {
        "ok": 200 <= int(result["status_code"]) < 300,
        "request_id": request_id,
        **result,
    }


def _operation_id(value: str) -> str:
    if not value:
        return str(uuid4())
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ValueError("operation_id must be a UUID") from exc


def _annotations(
    title: str,
    *,
    read_only: bool,
    destructive: bool,
    idempotent: bool,
    open_world: bool = False,
) -> ToolAnnotations:
    return ToolAnnotations.model_validate(
        {
            "title": title,
            "readOnlyHint": read_only,
            "destructiveHint": destructive,
            "idempotentHint": idempotent,
            "openWorldHint": open_world,
        }
    )


def create_server(config: AgentBridgeConfig) -> MCPServer[Any]:
    server: MCPServer[Any] = MCPServer(
        name="GardenOps",
        version="1.0.0",
        instructions=(
            "Use GardenOps as the source of truth. Read before writing. Use public IDs and current "
            "updated_at_ms revisions returned by reads. Never invent IDs. Only write when the user "
            "has clearly requested the change; deletes and bulk changes require explicit "
            "confirmation."
        ),
    )

    @server.tool(
        structured_output=True,
        annotations=_annotations(
            "GardenOps capabilities",
            read_only=True,
            destructive=False,
            idempotent=True,
        ),
    )
    async def garden_capabilities() -> dict[str, Any]:
        """Return the supported GardenOps API families and safe operating rules."""
        return {
            "schema_version": 1,
            "read_tool": "garden_read",
            "write_tool": "garden_write",
            "identify_tool": "garden_identify_plant",
            "api_families": [
                "dashboard and attention",
                "plants, plots, placements, map objects, and containers",
                "tasks and workflows",
                "journal and harvest",
                "issues",
                "inventory and procurement",
                "calendar and weather",
                "notifications and saved views",
                "planner, reports, and non-backup exports",
            ],
            "common_reads": [
                "GET /api/dashboard/today",
                "GET /api/attention/today",
                "GET /api/plants?limit=100",
                "GET /api/plants/search?q=<name>",
                "GET /api/plots",
                "GET /api/tasks?view=week&limit=100",
                "GET /api/journal?limit=50",
                "GET /api/harvest?limit=50",
                "GET /api/issues?limit=50",
                "GET /api/inventory",
                "GET /api/procurement",
                "GET /api/calendar/events",
                "GET /api/weather/summary",
            ],
            "common_writes": [
                {
                    "method": "POST",
                    "path": "/api/journal",
                    "body": {
                        "event_type": "observed",
                        "occurred_on": "YYYY-MM-DD",
                        "title": "...",
                        "notes": "...",
                        "plant_ids": [],
                        "plot_ids": [],
                    },
                },
                {
                    "method": "POST",
                    "path": "/api/tasks/<task_id>/action",
                    "body": {
                        "action": "complete|snooze|reschedule|reopen",
                        "expected_updated_at_ms": 0,
                    },
                },
                {
                    "method": "POST",
                    "path": "/api/harvest",
                    "body": {
                        "occurred_on": "YYYY-MM-DD",
                        "quantity": 1,
                        "unit": "kg",
                        "plant_ids": [],
                        "plot_ids": [],
                    },
                },
                {
                    "method": "POST",
                    "path": "/api/issues",
                    "body": {
                        "issue_type": "disease",
                        "title": "...",
                        "description": "...",
                        "severity": "normal",
                        "plant_ids": [],
                        "plot_ids": [],
                    },
                },
                {
                    "method": "POST",
                    "path": "/api/plots/<plot_id>/plants/<plant_id>",
                    "body": {"quantity": 1},
                },
            ],
            "rules": [
                "Call garden_read first to resolve public IDs and revisions.",
                "Use garden_write only for an explicit user-requested change.",
                "Set confirmed=true only after explicit confirmation for delete or bulk changes.",
                "On HTTP 409, reread instead of blindly retrying.",
                "Reuse operation_id only when retrying the exact same lost request.",
                "Authentication, users, memberships, admin operations, imports, backup restore, "
                "calendar feed tokens, and maintenance jobs are unavailable.",
            ],
        }

    @server.tool(
        structured_output=True,
        annotations=_annotations(
            "Read GardenOps",
            read_only=True,
            destructive=False,
            idempotent=True,
        ),
    )
    async def garden_read(path: str) -> dict[str, Any]:
        """GET one allowlisted GardenOps API path, including a bounded query string."""
        return request_api(config, method="GET", path=path)

    @server.tool(
        structured_output=True,
        annotations=_annotations(
            "Identify a plant with GardenOps PlantNet",
            read_only=False,
            destructive=False,
            idempotent=False,
            open_world=True,
        ),
    )
    async def garden_identify_plant(
        image_path: str,
        organ: Literal["auto", "leaf", "flower", "fruit", "bark", "habit", "other"] = "auto",
    ) -> dict[str, Any]:
        """Identify one staged Matrix image with GardenOps, using PlantNet first."""
        return request_plant_identification(
            config,
            image_path=image_path,
            organ=organ,
        )

    @server.tool(
        structured_output=True,
        annotations=_annotations(
            "Change GardenOps",
            read_only=False,
            destructive=True,
            idempotent=False,
        ),
    )
    async def garden_write(
        method: Literal["POST", "PUT", "PATCH", "DELETE"],
        path: str,
        body: dict[str, Any] | list[Any] | None = None,
        confirmed: bool = False,
        operation_id: str = "",
    ) -> dict[str, Any]:
        """Call one allowlisted GardenOps mutation; confirm deletes and bulk actions explicitly."""
        parsed_path = urlsplit(path).path
        requires_confirmation = agent_api_confirmation_required(method, parsed_path)
        if requires_confirmation and not confirmed:
            return {
                "ok": False,
                "status_code": 409,
                "data": {"detail": "Explicit confirmation is required for delete and bulk actions"},
            }
        resolved_operation_id = _operation_id(operation_id)
        result = request_api(
            config,
            method=method,
            path=path,
            body=body,
            operation_id=resolved_operation_id,
        )
        return {"operation_id": resolved_operation_id, **result}

    return server


def main() -> None:
    create_server(load_config()).run("stdio")


if __name__ == "__main__":
    main()
