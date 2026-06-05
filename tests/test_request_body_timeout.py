"""Tests for API request body buffering protections."""

import asyncio
import time
import unittest
from unittest.mock import patch

from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse


def _post_request(path: str, receive):  # type: ignore[no-untyped-def]
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 5000),
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return StarletteRequest(scope, receive)


class RequestBodyTimeoutTests(unittest.TestCase):
    def test_no_content_length_body_buffering_uses_request_timeout(self) -> None:
        async def run() -> None:
            from gardenops.main import auth_guard

            async def slow_receive() -> dict[str, object]:
                await asyncio.sleep(1)
                return {"type": "http.request", "body": b"{}", "more_body": False}

            async def call_next(_request: StarletteRequest) -> JSONResponse:
                self.fail("call_next should not run after body buffering times out")

            request = _post_request("/api/auth/login", slow_receive)
            with (
                patch("gardenops.main._request_timeout_seconds", return_value=0.05),
                patch("gardenops.main.is_emergency_read_only", return_value=False),
                patch("gardenops.main.write_audit_event"),
            ):
                started = time.monotonic()
                response = await auth_guard(request, call_next)
                elapsed = time.monotonic() - started

            self.assertEqual(response.status_code, 504)
            self.assertLess(elapsed, 0.5)

        asyncio.run(run())

    def test_no_content_length_body_buffering_replays_body_to_handler(self) -> None:
        async def run() -> None:
            from gardenops.main import auth_guard

            messages = iter(
                [
                    {"type": "http.request", "body": b'{"u"', "more_body": True},
                    {"type": "http.request", "body": b":1}", "more_body": False},
                ]
            )

            async def receive() -> dict[str, object]:
                return next(messages)

            async def call_next(request: StarletteRequest) -> JSONResponse:
                body = await request.body()
                return JSONResponse({"length": len(body), "body": body.decode("utf-8")})

            request = _post_request("/api/auth/login", receive)
            with (
                patch("gardenops.main._request_timeout_seconds", return_value=1),
                patch("gardenops.main.is_emergency_read_only", return_value=False),
                patch("gardenops.main.write_audit_event"),
            ):
                response = await auth_guard(request, call_next)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.body, b'{"length":7,"body":"{\\"u\\":1}"}')

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
