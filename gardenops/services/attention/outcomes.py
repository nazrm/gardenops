from __future__ import annotations

import json
from typing import Any

from gardenops.db import DbConn
from gardenops.router_helpers import generate_public_id


def _dump_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"))


def _dump_json_array(value: tuple[str, ...] | list[str]) -> str:
    return json.dumps(list(value), sort_keys=True, separators=(",", ":"))


def _parse_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    if not value:
        return ()
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return ()
        if isinstance(parsed, list):
            return tuple(str(item) for item in parsed)
    return ()


def upsert_attention_outcome(
    conn: DbConn,
    *,
    garden_id: int,
    provider: str,
    outcome_type: str,
    source_type: str,
    source_id: str,
    source_public_id: str,
    target_type: str,
    target_id: str,
    title: str,
    explanation: str,
    reason: str = "",
    plant_ids: tuple[str, ...] = (),
    plot_ids: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
    recovery_action: dict[str, Any] | None = None,
    occurred_at_ms: int,
    expires_at_ms: int,
) -> str:
    row = conn.execute(
        """
        INSERT INTO attention_outcomes
            (public_id, garden_id, provider, outcome_type, source_type, source_id,
             source_public_id, title, explanation, reason, target_type, target_id,
             plant_ids_json, plot_ids_json, recovery_action_json, metadata_json,
             occurred_at_ms, expires_at_ms, created_at_ms, updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT
            (garden_id, provider, outcome_type, source_type, source_public_id,
             target_type, target_id)
        DO UPDATE SET
            source_id = excluded.source_id,
            title = excluded.title,
            explanation = excluded.explanation,
            reason = excluded.reason,
            plant_ids_json = excluded.plant_ids_json,
            plot_ids_json = excluded.plot_ids_json,
            recovery_action_json = excluded.recovery_action_json,
            metadata_json = excluded.metadata_json,
            occurred_at_ms = excluded.occurred_at_ms,
            expires_at_ms = excluded.expires_at_ms,
            updated_at_ms = excluded.updated_at_ms
        RETURNING public_id
        """,
        (
            generate_public_id("attnout"),
            garden_id,
            provider,
            outcome_type,
            source_type,
            source_id,
            source_public_id,
            title,
            explanation,
            reason,
            target_type,
            target_id,
            _dump_json_array(plant_ids),
            _dump_json_array(plot_ids),
            _dump_json(recovery_action),
            _dump_json(metadata),
            occurred_at_ms,
            expires_at_ms,
            occurred_at_ms,
            occurred_at_ms,
        ),
    ).fetchone()
    assert row is not None
    return str(row["public_id"])


def read_active_attention_outcomes(
    conn: DbConn,
    *,
    garden_id: int,
    provider: str | None = None,
    outcome_types: tuple[str, ...] = (),
    target_type: str | None = None,
    now_ms: int,
) -> list[dict[str, Any]]:
    conditions = ["garden_id = %s", "expires_at_ms > %s"]
    params: list[Any] = [garden_id, now_ms]
    if provider is not None:
        conditions.append("provider = %s")
        params.append(provider)
    if outcome_types:
        conditions.append(f"outcome_type IN ({','.join(['%s'] * len(outcome_types))})")
        params.extend(outcome_types)
    if target_type is not None:
        conditions.append("target_type = %s")
        params.append(target_type)
    rows = conn.execute(
        f"""
        SELECT public_id, garden_id, provider, outcome_type, source_type, source_id,
               source_public_id, title, explanation, reason, target_type, target_id,
               plant_ids_json, plot_ids_json, recovery_action_json, metadata_json,
               occurred_at_ms, expires_at_ms, updated_at_ms
        FROM attention_outcomes
        WHERE {" AND ".join(conditions)}
        ORDER BY occurred_at_ms DESC, public_id ASC
        """,
        params,
    ).fetchall()
    return [
        {
            "public_id": str(row["public_id"]),
            "garden_id": int(row["garden_id"]),
            "provider": str(row["provider"]),
            "outcome_type": str(row["outcome_type"]),
            "source_type": str(row["source_type"]),
            "source_id": str(row["source_id"]),
            "source_public_id": str(row["source_public_id"]),
            "title": str(row["title"]),
            "explanation": str(row["explanation"]),
            "reason": str(row["reason"] or ""),
            "target_type": str(row["target_type"] or ""),
            "target_id": str(row["target_id"] or ""),
            "plant_ids": _parse_string_tuple(row["plant_ids_json"]),
            "plot_ids": _parse_string_tuple(row["plot_ids_json"]),
            "recovery_action": _parse_mapping(row["recovery_action_json"]),
            "metadata": _parse_mapping(row["metadata_json"]),
            "occurred_at_ms": int(row["occurred_at_ms"]),
            "expires_at_ms": int(row["expires_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"] or 0),
        }
        for row in rows
    ]
