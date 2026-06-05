from __future__ import annotations

from fastapi import HTTPException, Request


async def read_body_limited(request: Request, max_bytes: int) -> bytes:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="Request body too large")
        chunks.append(chunk)
    return b"".join(chunks)


async def read_and_cache_body_limited(request: Request, max_bytes: int) -> bytes:
    body = await read_body_limited(request, max_bytes)
    request._body = body  # noqa: SLF001 - Starlette uses this cache for downstream consumers.
    return body
