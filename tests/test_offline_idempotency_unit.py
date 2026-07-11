from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from gardenops.offline_idempotency import (
    JOURNAL_ENDPOINT,
    JOURNAL_TARGET,
    MEDIA_UPLOAD_ENDPOINT,
    OfflineCreateEndpoint,
    OfflineOperation,
    canonical_request_fingerprint,
    media_request_fingerprint,
    prepare_operation,
    reserve_operation,
)


def _request(operation_id: str = "offline-unit-operation") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/journal",
            "headers": [(b"x-offline-operation-id", operation_id.encode("ascii"))],
        }
    )


def _cursor(row: dict[str, object] | None) -> MagicMock:
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    return cursor


def test_canonical_request_fingerprint_ignores_mapping_order() -> None:
    first = canonical_request_fingerprint(
        {"title": "Bloom", "metadata": {"source": "offline", "count": 2}}
    )
    second = canonical_request_fingerprint(
        {"metadata": {"count": 2, "source": "offline"}, "title": "Bloom"}
    )

    assert first == second
    assert len(first) == 64
    assert first != canonical_request_fingerprint({"title": "Different"})


def test_media_fingerprint_binds_target_metadata_and_binary_payload() -> None:
    first = media_request_fingerprint(
        target_type="journal_entry",
        target_id="jrn_abc",
        original_filename="bloom.png",
        content_type="image/png",
        payload=b"first image bytes",
    )
    same = media_request_fingerprint(
        target_type="journal_entry",
        target_id="jrn_abc",
        original_filename="bloom.png",
        content_type="image/png",
        payload=b"first image bytes",
    )
    changed = media_request_fingerprint(
        target_type="journal_entry",
        target_id="jrn_abc",
        original_filename="bloom.png",
        content_type="image/png",
        payload=b"different image bytes",
    )

    assert first == same
    assert first != changed


def test_prepare_operation_returns_matching_replay_and_rejects_payload_change() -> None:
    payload = {"event_type": "observed", "occurred_on": "2026-07-10"}
    fingerprint = canonical_request_fingerprint(payload)
    db = MagicMock()
    db.execute.return_value = _cursor(
        {
            "target_type": JOURNAL_TARGET,
            "target_id": "jrn_42",
            "result_id": "jrn_42",
            "request_fingerprint": fingerprint,
        }
    )

    prepared = prepare_operation(
        db,
        request=_request(),
        garden_id=7,
        endpoint=JOURNAL_ENDPOINT,
        request_payload=payload,
        now_ms=100,
    )

    assert prepared.replay is not None
    assert prepared.replay.target_id == "jrn_42"
    assert prepared.replay.result_id == "jrn_42"
    assert prepared.operation is not None

    db.execute.return_value = _cursor(
        {
            "target_type": JOURNAL_TARGET,
            "target_id": "jrn_42",
            "result_id": "jrn_42",
            "request_fingerprint": fingerprint,
        }
    )
    with pytest.raises(HTTPException) as exc_info:
        prepare_operation(
            db,
            request=_request(),
            garden_id=7,
            endpoint=JOURNAL_ENDPOINT,
            request_payload={**payload, "title": "Changed"},
            now_ms=100,
        )

    assert exc_info.value.status_code == 409


def test_invalid_endpoint_is_rejected_before_database_access() -> None:
    db = MagicMock()

    with pytest.raises(ValueError, match="Unsupported offline idempotency endpoint"):
        prepare_operation(
            db,
            request=_request(),
            garden_id=7,
            endpoint=cast(OfflineCreateEndpoint, "tasks"),
            request_payload={},
            now_ms=100,
        )

    db.execute.assert_not_called()


def test_media_reservation_requires_an_explicit_target_type() -> None:
    db = MagicMock()
    operation = OfflineOperation(
        garden_id=7,
        endpoint=MEDIA_UPLOAD_ENDPOINT,
        operation_id="offline-unit-operation",
        request_fingerprint="a" * 64,
    )

    with pytest.raises(ValueError, match="requires an explicit target type"):
        reserve_operation(
            db,
            operation=operation,
            target_id="asset_42",
            created_at_ms=100,
        )

    db.execute.assert_not_called()


def test_reservation_loser_rolls_back_before_rejecting_different_payload() -> None:
    db = MagicMock()
    db.execute.side_effect = [
        _cursor(None),
        _cursor(
            {
                "target_type": JOURNAL_TARGET,
                "target_id": "jrn_99",
                "result_id": "jrn_99",
                "request_fingerprint": "b" * 64,
            }
        ),
    ]
    operation = OfflineOperation(
        garden_id=7,
        endpoint=JOURNAL_ENDPOINT,
        operation_id="offline-unit-operation",
        request_fingerprint="a" * 64,
    )

    with pytest.raises(HTTPException) as exc_info:
        reserve_operation(
            db,
            operation=operation,
            target_id="jrn_101",
            created_at_ms=100,
        )

    assert exc_info.value.status_code == 409
    assert [call[0] for call in db.method_calls] == ["execute", "rollback", "execute"]


def test_reservation_loser_returns_matching_winner_after_rollback() -> None:
    db = MagicMock()
    db.execute.side_effect = [
        _cursor(None),
        _cursor(
            {
                "target_type": JOURNAL_TARGET,
                "target_id": "jrn_99",
                "result_id": "jrn_99",
                "request_fingerprint": "a" * 64,
            }
        ),
    ]
    operation = OfflineOperation(
        garden_id=7,
        endpoint=JOURNAL_ENDPOINT,
        operation_id="offline-unit-operation",
        request_fingerprint="a" * 64,
    )

    reservation = reserve_operation(
        db,
        operation=operation,
        target_id="jrn_101",
        created_at_ms=100,
    )

    assert not reservation.is_owner
    assert reservation.target_type == JOURNAL_TARGET
    assert reservation.target_id == "jrn_99"
    assert reservation.result_id == "jrn_99"
    db.rollback.assert_called_once_with()
