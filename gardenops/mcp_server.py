from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from mcp.server import MCPServer
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from gardenops.db import DbConn, get_db, return_db
from gardenops.router_helpers import generate_public_id
from gardenops.services.assistant import (
    analyze_matrix_capture,
    apply_request,
    cancel_request,
    continue_request,
    expire_and_cleanup_requests,
    get_request,
    process_text,
)
from gardenops.services.assistant_models import (
    AnalyzeCaptureInput,
    AssistantResult,
    ContinueInput,
    ProcessTextInput,
    RequestEventInput,
)
from gardenops.services.integration_config import (
    AssistantBinding,
    integration_token_matches,
    mcp_enabled,
    resolve_assistant_binding,
)


class StaticBearerMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").casefold(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        authorization = headers.get("authorization", "").strip()
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.casefold() != "bearer" or not integration_token_matches(token):
            response = JSONResponse(
                status_code=401,
                content={"detail": "Invalid integration credentials"},
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


@dataclass(frozen=True)
class MCPRuntime:
    server: MCPServer[Any]
    app: ASGIApp


def _error_result(message: str, *, retryable: bool = False) -> AssistantResult:
    request_id = generate_public_id("asst")
    suffix = "".join(ch for ch in request_id.rsplit("_", 1)[-1] if ch.isalnum()).upper()
    return AssistantResult(
        state="error",
        request_id=request_id,
        reference=f"GO-{suffix[:6].ljust(6, '0')}",
        message=message,
        retryable=retryable,
    )


def _run_tool(
    operation: Callable[[DbConn, AssistantBinding], AssistantResult],
) -> AssistantResult:
    if not mcp_enabled():
        return _error_result("MCP integration is disabled")
    db = get_db()
    try:
        binding = resolve_assistant_binding(db)
        expire_and_cleanup_requests(db)
        db.commit()
        result = operation(db, binding)
        db.commit()
        return AssistantResult.model_validate(result)
    except HTTPException as exc:
        db.rollback()
        return _error_result(str(exc.detail), retryable=exc.status_code >= 500)
    except (PermissionError, RuntimeError, ValueError) as exc:
        db.rollback()
        return _error_result(str(exc))
    except Exception:
        db.rollback()
        return _error_result("GardenOps could not complete the request", retryable=True)
    finally:
        return_db(db)


def create_mcp_runtime() -> MCPRuntime | None:
    if not mcp_enabled():
        return None
    server: MCPServer[Any] = MCPServer(
        name="GardenOps Assistant",
        instructions="Private, proposal-first garden assistant for one configured Matrix room.",
    )

    @server.tool(structured_output=True)
    async def assistant_process_text(
        source_room_id: str,
        source_event_id: str,
        source_sender_id: str,
        text: str,
        occurred_on: str,
    ) -> AssistantResult:
        data = ProcessTextInput.model_validate(locals())
        return _run_tool(
            lambda db, binding: process_text(
                db,
                binding,
                **data.model_dump(),
            )
        )

    @server.tool(structured_output=True)
    async def assistant_analyze_capture(
        source_room_id: str,
        source_event_id: str,
        source_sender_id: str,
        capture_asset_id: str,
        caption: str,
        occurred_on: str,
    ) -> AssistantResult:
        data = AnalyzeCaptureInput.model_validate(locals())
        values = data.model_dump()
        return _run_tool(
            lambda db, binding: analyze_matrix_capture(
                db,
                binding,
                **values,
            )
        )

    @server.tool(structured_output=True)
    async def assistant_continue(
        request_id: str,
        source_event_id: str,
        text: str,
    ) -> AssistantResult:
        data = ContinueInput.model_validate(locals())
        return _run_tool(
            lambda db, binding: continue_request(
                db,
                binding,
                **data.model_dump(),
            )
        )

    @server.tool(structured_output=True)
    async def assistant_get(request_id: str) -> AssistantResult:
        data = RequestEventInput(request_id=request_id)
        return _run_tool(
            lambda db, binding: get_request(
                db,
                binding,
                request_id=data.request_id,
            )
        )

    @server.tool(structured_output=True)
    async def assistant_apply(
        request_id: str,
        source_event_id: str,
    ) -> AssistantResult:
        data = RequestEventInput.model_validate(locals())
        if not data.source_event_id:
            return _error_result("source_event_id is required")
        return _run_tool(
            lambda db, binding: apply_request(
                db,
                binding,
                **data.model_dump(),
            )
        )

    @server.tool(structured_output=True)
    async def assistant_cancel(
        request_id: str,
        source_event_id: str,
    ) -> AssistantResult:
        data = RequestEventInput.model_validate(locals())
        if not data.source_event_id:
            return _error_result("source_event_id is required")
        return _run_tool(
            lambda db, binding: cancel_request(
                db,
                binding,
                **data.model_dump(),
            )
        )

    stream_app = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        host="127.0.0.1",
    )
    mcp_route = stream_app.routes[0]
    if not isinstance(mcp_route, Route):
        raise RuntimeError("MCP SDK did not provide an HTTP route")
    endpoint = mcp_route.endpoint
    return MCPRuntime(server=server, app=StaticBearerMiddleware(endpoint))
