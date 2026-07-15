from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal, NoReturn

from fastapi import HTTPException, Request

from gardenops.db import DbConn

OFFLINE_OPERATION_HEADER: Final = "x-offline-operation-id"
OFFLINE_OPERATION_MAX_LENGTH: Final = 128
OFFLINE_OPERATION_RETENTION_MS: Final = 30 * 24 * 60 * 60 * 1000
OFFLINE_OPERATION_REQUEST_CONFLICT_DETAIL: Final = (
    "Offline operation ID was already used for a different request"
)
OFFLINE_OPERATION_CONFLICT_DETAIL: Final = "Offline operation conflict"
OFFLINE_OPERATION_TARGET_GONE_DETAIL: Final = "Offline operation target no longer exists"

type OfflineOperationEndpoint = Literal[
    "journal",
    "issues",
    "harvest",
    "task_action",
    "media_upload",
]
type OfflineOperationTargetType = Literal[
    "journal_entry",
    "issue",
    "harvest_entry",
    "task",
    "plant",
    "plot",
]
# Retain the public alias while operations now also cover task actions and media uploads.
type OfflineCreateEndpoint = OfflineOperationEndpoint

JOURNAL_ENDPOINT: Final[OfflineOperationEndpoint] = "journal"
ISSUES_ENDPOINT: Final[OfflineOperationEndpoint] = "issues"
HARVEST_ENDPOINT: Final[OfflineOperationEndpoint] = "harvest"
TASK_ACTION_ENDPOINT: Final[OfflineOperationEndpoint] = "task_action"
MEDIA_UPLOAD_ENDPOINT: Final[OfflineOperationEndpoint] = "media_upload"

JOURNAL_TARGET: Final[OfflineOperationTargetType] = "journal_entry"
ISSUE_TARGET: Final[OfflineOperationTargetType] = "issue"
HARVEST_TARGET: Final[OfflineOperationTargetType] = "harvest_entry"
TASK_TARGET: Final[OfflineOperationTargetType] = "task"

_ENDPOINT_TARGET_TYPES: Final[dict[str, frozenset[str]]] = {
    JOURNAL_ENDPOINT: frozenset({JOURNAL_TARGET}),
    ISSUES_ENDPOINT: frozenset({ISSUE_TARGET}),
    HARVEST_ENDPOINT: frozenset({HARVEST_TARGET}),
    TASK_ACTION_ENDPOINT: frozenset({TASK_TARGET}),
    MEDIA_UPLOAD_ENDPOINT: frozenset(
        {JOURNAL_TARGET, ISSUE_TARGET, HARVEST_TARGET, "plant", "plot"}
    ),
}


@dataclass(frozen=True, slots=True)
class OfflineOperation:
    garden_id: int
    endpoint: OfflineOperationEndpoint
    operation_id: str
    request_fingerprint: str


@dataclass(frozen=True, slots=True)
class OfflineOperationReplay:
    target_type: OfflineOperationTargetType
    target_id: str
    result_id: str


@dataclass(frozen=True, slots=True)
class PreparedOfflineOperation:
    operation: OfflineOperation | None
    replay: OfflineOperationReplay | None

    @property
    def replay_target_id(self) -> str | None:
        """Compatibility shorthand for endpoints whose target is also the result."""
        return self.replay.result_id if self.replay is not None else None


@dataclass(frozen=True, slots=True)
class OfflineOperationReservation:
    is_owner: bool
    target_type: OfflineOperationTargetType
    target_id: str
    result_id: str


def canonical_request_fingerprint(payload: Mapping[str, object]) -> str:
    canonical_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def media_request_fingerprint(
    *,
    target_type: str,
    target_id: str,
    original_filename: str,
    content_type: str,
    payload: bytes,
) -> str:
    """Fingerprint binary uploads without retaining file contents in Postgres."""
    return canonical_request_fingerprint(
        {
            "content_sha256": hashlib.sha256(payload).hexdigest(),
            "content_type": content_type,
            "original_filename": original_filename,
            "target_id": target_id,
            "target_type": target_type,
        }
    )


def read_operation_id(request: Request) -> str | None:
    operation_id = request.headers.get(OFFLINE_OPERATION_HEADER, "").strip()
    if not operation_id:
        return None
    if len(operation_id) > OFFLINE_OPERATION_MAX_LENGTH:
        raise HTTPException(status_code=400, detail="Offline operation ID is too long")
    return operation_id


def raise_operation_target_gone() -> NoReturn:
    raise HTTPException(status_code=410, detail=OFFLINE_OPERATION_TARGET_GONE_DETAIL)


def _validate_endpoint(endpoint: OfflineOperationEndpoint) -> None:
    if endpoint not in _ENDPOINT_TARGET_TYPES:
        raise ValueError(f"Unsupported offline idempotency endpoint: {endpoint}")


def _default_target_type(endpoint: OfflineOperationEndpoint) -> OfflineOperationTargetType:
    target_types = _ENDPOINT_TARGET_TYPES.get(endpoint)
    if target_types is None:
        _validate_endpoint(endpoint)
    if target_types is not None and len(target_types) == 1:
        return next(iter(target_types))  # type: ignore[return-value]
    raise ValueError(f"Offline idempotency endpoint requires an explicit target type: {endpoint}")


def _validate_target_type(
    endpoint: OfflineOperationEndpoint,
    target_type: OfflineOperationTargetType,
) -> None:
    _validate_endpoint(endpoint)
    if target_type not in _ENDPOINT_TARGET_TYPES[endpoint]:
        raise ValueError(
            f"Unsupported offline idempotency target type for {endpoint}: {target_type}"
        )


def _raise_request_conflict() -> NoReturn:
    raise HTTPException(status_code=409, detail=OFFLINE_OPERATION_REQUEST_CONFLICT_DETAIL)


def _lookup_operation_replay(
    db: DbConn,
    *,
    operation: OfflineOperation,
    now_ms: int,
) -> OfflineOperationReplay | None:
    row = db.execute(
        """
        SELECT target_type, target_id, result_id, request_fingerprint
        FROM offline_create_operations
        WHERE garden_id = %s
          AND endpoint = %s
          AND operation_id = %s
          AND expires_at_ms > %s
        """,
        (
            operation.garden_id,
            operation.endpoint,
            operation.operation_id,
            now_ms,
        ),
    ).fetchone()
    if not row:
        return None
    if str(row["request_fingerprint"]) != operation.request_fingerprint:
        _raise_request_conflict()
    return OfflineOperationReplay(
        target_type=str(row["target_type"]),  # type: ignore[arg-type]
        target_id=str(row["target_id"]),
        result_id=str(row["result_id"]),
    )


def _prune_expired_operations(db: DbConn, *, now_ms: int) -> None:
    db.execute(
        "DELETE FROM offline_create_operations WHERE expires_at_ms <= %s",
        (now_ms,),
    )


def prepare_operation(
    db: DbConn,
    *,
    request: Request,
    garden_id: int,
    endpoint: OfflineOperationEndpoint,
    request_payload: Mapping[str, object],
    now_ms: int,
    operation_namespace: str | None = None,
) -> PreparedOfflineOperation:
    _validate_endpoint(endpoint)
    operation_id = read_operation_id(request)
    if operation_id is None:
        return PreparedOfflineOperation(operation=None, replay=None)
    if operation_namespace:
        operation_id = f"{operation_namespace}:{operation_id}"
        if len(operation_id) > OFFLINE_OPERATION_MAX_LENGTH:
            raise HTTPException(status_code=400, detail="Namespaced operation ID is too long")

    operation = OfflineOperation(
        garden_id=garden_id,
        endpoint=endpoint,
        operation_id=operation_id,
        request_fingerprint=canonical_request_fingerprint(request_payload),
    )
    replay = _lookup_operation_replay(db, operation=operation, now_ms=now_ms)
    if replay is None:
        _prune_expired_operations(db, now_ms=now_ms)
    return PreparedOfflineOperation(operation=operation, replay=replay)


def reserve_operation(
    db: DbConn,
    *,
    operation: OfflineOperation,
    target_id: str | int,
    created_at_ms: int,
    target_type: OfflineOperationTargetType | None = None,
    result_id: str | int | None = None,
) -> OfflineOperationReservation:
    resolved_target_type = target_type or _default_target_type(operation.endpoint)
    _validate_target_type(operation.endpoint, resolved_target_type)
    resolved_target_id = str(target_id)
    resolved_result_id = str(result_id if result_id is not None else target_id)
    row = db.execute(
        """
        INSERT INTO offline_create_operations (
            garden_id, endpoint, operation_id, request_fingerprint,
            target_type, target_id, result_id, created_at_ms, expires_at_ms
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (garden_id, endpoint, operation_id) DO NOTHING
        RETURNING id
        """,
        (
            operation.garden_id,
            operation.endpoint,
            operation.operation_id,
            operation.request_fingerprint,
            resolved_target_type,
            resolved_target_id,
            resolved_result_id,
            created_at_ms,
            created_at_ms + OFFLINE_OPERATION_RETENTION_MS,
        ),
    ).fetchone()
    if row:
        return OfflineOperationReservation(
            is_owner=True,
            target_type=resolved_target_type,
            target_id=resolved_target_id,
            result_id=resolved_result_id,
        )

    # The uniqueness contender may have committed while this request was waiting.
    # Roll back the current transaction before reading that winner's durable record.
    db.rollback()
    winner = _lookup_operation_replay(
        db,
        operation=operation,
        now_ms=created_at_ms,
    )
    if winner is None:
        raise HTTPException(status_code=409, detail=OFFLINE_OPERATION_CONFLICT_DETAIL)
    return OfflineOperationReservation(
        is_owner=False,
        target_type=winner.target_type,
        target_id=winner.target_id,
        result_id=winner.result_id,
    )
