from __future__ import annotations

import json
import os
from datetime import date
from typing import Any, Literal, cast

from fastapi import HTTPException

from gardenops.audit import write_required_audit_event
from gardenops.db import DbConn, current_timestamp_ms
from gardenops.rate_limit import (
    acquire_concurrency_slot,
    provider_limit_profile,
    reserve_daily_provider_budget,
)
from gardenops.router_helpers import generate_public_id
from gardenops.services.ai_provider import (
    AIProviderError,
    AIProviderNotConfigured,
    AIProviderRateLimited,
    AIProviderTimeout,
    diagnose_plant_with_ai,
    interpret_garden_message_with_ai,
)
from gardenops.services.assistant_models import (
    AssistantChoice,
    AssistantIntent,
    AssistantProposal,
    AssistantRecord,
    AssistantResult,
    CaptureAnalysis,
)
from gardenops.services.assistant_resolution import (
    ResolvedGardenTarget,
    resolve_garden_target,
    target_from_choice,
)
from gardenops.services.capture_analysis import (
    analyze_capture,
    capture_image_bytes,
)
from gardenops.services.domain_commands import (
    CommandResult,
    complete_task_command,
    create_harvest_entry_command,
    create_issue_command,
    create_journal_entry_command,
    link_existing_media_command,
)
from gardenops.services.garden_qa import answer_garden_question, build_garden_context
from gardenops.services.integration_config import (
    AssistantBinding,
    assert_source_binding,
)
from gardenops.services.media_store import enqueue_media_cleanup_jobs

_ACTIVE_STATES = frozenset({"processing", "needs_input", "proposal"})


def _request_ttl_ms() -> int:
    try:
        days = int(os.environ.get("MATRIX_CAPTURE_TTL_DAYS", "7"))
    except ValueError:
        days = 7
    return max(1, min(days, 30)) * 24 * 60 * 60 * 1000


def _json_object(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(str(raw or "{}"))
    except TypeError, json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _reference(request_id: str) -> str:
    token = "".join(ch for ch in request_id.rsplit("_", 1)[-1] if ch.isalnum()).upper()
    return f"GO-{token[:6].ljust(6, '0')}"


def _resolve_request_id(
    db: DbConn,
    binding: AssistantBinding,
    request_id_or_reference: str,
) -> str:
    value = request_id_or_reference.strip()
    if not value.upper().startswith("GO-"):
        return value
    token = value[3:].strip().lower()  # push-sanitizer: allow SECRET_ASSIGNMENT - request ref
    if len(token) != 6 or not token.isalnum():
        raise HTTPException(status_code=404, detail="Assistant request not found")
    rows = db.execute(
        """
        SELECT public_id
        FROM assistant_requests
        WHERE garden_id = %s AND actor_user_id = %s
          AND source_room_id = %s AND source_sender_id = %s
          AND public_id LIKE %s ESCAPE '\\'
        ORDER BY created_at_ms DESC
        LIMIT 2
        """,
        (
            binding.garden_id,
            binding.user_id,
            binding.room_id,
            binding.sender_id,
            f"asst\\_{token}%",
        ),
    ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="Assistant request not found")
    if len(rows) > 1:
        raise HTTPException(status_code=409, detail="Assistant reference is ambiguous")
    return str(rows[0]["public_id"])


def _result_from_row(row: dict[str, Any]) -> AssistantResult:
    if str(row.get("state") or "") == "expired":
        request_id = str(row["public_id"])
        return AssistantResult(
            state="error",
            request_id=request_id,
            reference=_reference(request_id),
            message="This request has expired. Start a new request to continue.",
        )
    stored = _json_object(row.get("result_json"))
    if stored:
        return AssistantResult.model_validate(stored)
    state_map = {
        "answered": "answer",
        "needs_input": "needs_input",
        "proposal": "proposal",
        "applied": "applied",
        "cancelled": "cancelled",
    }
    result_state = state_map.get(str(row.get("state") or ""), "error")
    return AssistantResult(
        state=cast(Any, result_state),
        request_id=str(row["public_id"]),
        reference=_reference(str(row["public_id"])),
        message=str(row.get("error_detail") or "Assistant request is unavailable"),
        retryable=str(row.get("state") or "") == "failed",
    )


def _save_result(
    db: DbConn,
    *,
    request_id: str,
    db_state: str,
    request_kind: str,
    result: AssistantResult,
    payload: dict[str, Any],
    source_event_id: str = "",
    error_detail: str = "",
    applied_at_ms: int | None = None,
) -> AssistantResult:
    now_ms = current_timestamp_ms()
    db.execute(
        """
        UPDATE assistant_requests
        SET state = %s, request_kind = %s, payload_json = %s, result_json = %s,
            error_detail = %s, updated_at_ms = %s,
            last_source_event_id = CASE WHEN %s <> '' THEN %s ELSE last_source_event_id END,
            applied_at_ms = COALESCE(%s, applied_at_ms)
        WHERE public_id = %s
        """,
        (
            db_state,
            request_kind,
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            result.model_dump_json(),
            error_detail[:1000],
            now_ms,
            source_event_id,
            source_event_id,
            applied_at_ms,
            request_id,
        ),
    )
    return result


def _error_result(
    db: DbConn,
    *,
    request_id: str,
    request_kind: str,
    payload: dict[str, Any],
    message: str,
    retryable: bool,
    source_event_id: str = "",
) -> AssistantResult:
    result = AssistantResult(
        state="error",
        request_id=request_id,
        reference=_reference(request_id),
        message=message,
        retryable=retryable,
    )
    return _save_result(
        db,
        request_id=request_id,
        db_state="failed",
        request_kind=request_kind,
        result=result,
        payload=payload,
        source_event_id=source_event_id,
        error_detail=message,
    )


def _interpret(
    db: DbConn,
    binding: AssistantBinding,
    *,
    text: str,
    occurred_on: str,
) -> AssistantIntent:
    context = build_garden_context(db, binding.context)
    limits = provider_limit_profile("ai-garden-chat")
    with acquire_concurrency_slot(bucket="ai-garden-chat", limit=int(limits["concurrency_limit"])):
        reserve_daily_provider_budget(
            db,
            feature="ai-garden-chat",
            user_id=binding.user_id,
            garden_id=binding.garden_id,
            user_limit=int(limits["user_limit"]),
            garden_limit=int(limits["garden_limit"]),
        )
        return interpret_garden_message_with_ai(text, context, occurred_on)


def _target_for_intent(
    db: DbConn,
    binding: AssistantBinding,
    *,
    intent: AssistantIntent,
    input_text: str,
    image_analysis: CaptureAnalysis | None,
) -> ResolvedGardenTarget:
    queries: list[str] = []
    if intent.plant_query.strip():
        queries.append(intent.plant_query)
    if image_analysis is not None:
        queries.extend(
            candidate.latin or candidate.name for candidate in image_analysis.plant_candidates
        )
    taxonomy_refs = (
        [
            reference
            for candidate in image_analysis.plant_candidates
            if image_analysis is not None
            for reference in candidate.taxonomy_refs
        ]
        if image_analysis is not None
        else []
    )
    queries.append(input_text)
    best_not_found = ResolvedGardenTarget(status="not_found")
    for query in queries:
        if not query.strip():
            continue
        result = resolve_garden_target(
            db,
            binding.context,
            plant_query=query,
            plot_query=intent.plot_query or input_text,
            taxonomy_refs=taxonomy_refs,
        )
        if result.status != "not_found":
            return result
        best_not_found = result
    return best_not_found


def _needs_input(
    db: DbConn,
    *,
    request_id: str,
    request_kind: str,
    payload: dict[str, Any],
    message: str,
    choices: list[AssistantChoice] | tuple[AssistantChoice, ...] = (),
    continuation_kind: str,
    source_event_id: str = "",
) -> AssistantResult:
    payload["continuation_kind"] = continuation_kind
    payload["choices"] = [choice.model_dump(mode="json") for choice in choices]
    result = AssistantResult(
        state="needs_input",
        request_id=request_id,
        reference=_reference(request_id),
        message=f"{message}\nRef: {_reference(request_id)}",
        choices=list(choices),
    )
    return _save_result(
        db,
        request_id=request_id,
        db_state="needs_input",
        request_kind=request_kind,
        result=result,
        payload=payload,
        source_event_id=source_event_id,
    )


def _proposal_result(
    db: DbConn,
    *,
    request_id: str,
    kind: Literal["journal", "harvest", "issue", "task_completion"],
    summary: str,
    fields: dict[str, Any],
    payload: dict[str, Any],
    source_event_id: str = "",
) -> AssistantResult:
    proposal = AssistantProposal(kind=kind, summary=summary, fields=fields)
    payload["proposal"] = proposal.model_dump(mode="json")
    result = AssistantResult(
        state="proposal",
        request_id=request_id,
        reference=_reference(request_id),
        message=(
            f"Ready to save\n\n{summary}\n\n"
            f"Reply `save` to apply or `cancel` to discard.\nRef: {_reference(request_id)}"
        ),
        proposal=proposal,
    )
    return _save_result(
        db,
        request_id=request_id,
        db_state="proposal",
        request_kind=kind,
        result=result,
        payload=payload,
        source_event_id=source_event_id,
    )


def _task_candidates(
    db: DbConn,
    binding: AssistantBinding,
    *,
    intent: AssistantIntent,
    target: ResolvedGardenTarget,
) -> list[dict[str, Any]]:
    conditions = ["t.garden_id = %s", "t.status IN ('pending', 'snoozed')"]
    params: list[object] = [binding.garden_id]
    if target.plant_id:
        conditions.append("t.id IN (SELECT task_id FROM garden_task_plants WHERE plt_id = %s)")
        params.append(target.plant_id)
    if target.plot_id:
        conditions.append("t.id IN (SELECT task_id FROM garden_task_plots WHERE plot_id = %s)")
        params.append(target.plot_id)
    task_query = (intent.task_query or intent.notes).strip()
    type_tokens = {
        "prun": "prune",
        "fertiliz": "fertilize",
        "fertilis": "fertilize",
        "water": "water",
        "bloom": "observe_bloom",
        "harvest": "harvest",
    }
    task_type = next(
        (value for token, value in type_tokens.items() if token in task_query.casefold()), ""
    )
    if task_type:
        conditions.append("t.task_type = %s")
        params.append(task_type)
    rows = db.execute(
        f"""
        SELECT t.public_id, t.task_type, t.title, t.description, t.due_on, t.updated_at_ms
        FROM garden_tasks t
        WHERE {" AND ".join(conditions)}
        ORDER BY t.due_on, t.created_at_ms
        LIMIT 20
        """,  # noqa: S608
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _diagnosis_fields(
    db: DbConn,
    binding: AssistantBinding,
    *,
    capture_asset_id: str,
    intent: AssistantIntent,
    target: ResolvedGardenTarget,
) -> dict[str, Any]:
    image_bytes = capture_image_bytes(db, binding.context, asset_id=capture_asset_id)
    prompt = "\n".join(
        value
        for value in (
            f"Plant: {target.plant_name} ({target.latin})" if target.plant_name else "",
            f"Location: {target.plot_label}" if target.plot_label else "",
            f"Symptoms: {intent.symptoms or intent.notes}"
            if intent.symptoms or intent.notes
            else "",
        )
        if value
    )
    limits = provider_limit_profile("ai-diagnose")
    with acquire_concurrency_slot(bucket="ai-diagnose", limit=int(limits["concurrency_limit"])):
        reserve_daily_provider_budget(
            db,
            feature="ai-diagnose",
            user_id=binding.user_id,
            garden_id=binding.garden_id,
            user_limit=int(limits["user_limit"]),
            garden_limit=int(limits["garden_limit"]),
        )
        diagnoses = diagnose_plant_with_ai(image_bytes, prompt or "Diagnose this garden plant.")
    if not diagnoses:
        return {
            "issue_type": intent.issue_type or "other",
            "title": intent.title or "Plant health observation",
            "description": intent.symptoms or intent.notes,
            "severity": intent.severity,
            "suspected_cause": "",
            "treatment_plan": "",
        }
    diagnosis = diagnoses[0]
    confidence = str(diagnosis.get("confidence") or "low")
    severity = {"high": "high", "medium": "normal", "low": "low"}.get(confidence, intent.severity)
    cause = str(diagnosis.get("likely_cause") or "")[:1000]
    return {
        "issue_type": diagnosis.get("issue_type") or intent.issue_type or "other",
        "title": intent.title or cause or "Plant health issue",
        "description": str(diagnosis.get("description") or intent.symptoms or intent.notes)[:4000],
        "severity": severity,
        "suspected_cause": cause,
        "treatment_plan": str(diagnosis.get("suggested_treatment") or "")[:2000],
    }


def _advance_to_result(
    db: DbConn,
    binding: AssistantBinding,
    *,
    request_id: str,
    intent: AssistantIntent,
    input_text: str,
    occurred_on: str,
    capture_asset_id: str = "",
    image_analysis: CaptureAnalysis | None = None,
    payload: dict[str, Any] | None = None,
    selected_target: tuple[str, str] | None = None,
    selected_task_id: str = "",
    source_event_id: str = "",
) -> AssistantResult:
    payload = dict(payload or {})
    payload.update(
        {
            "schema_version": 1,
            "intent": intent.model_dump(mode="json"),
            "input_text": input_text,
            "occurred_on": occurred_on,
        }
    )
    if image_analysis is not None:
        payload["capture_analysis"] = image_analysis.model_dump(mode="json")

    if intent.intent == "question":
        answer = answer_garden_question(
            db,
            binding.context,
            message=input_text,
            history=[],
        )
        result = AssistantResult(
            state="answer",
            request_id=request_id,
            reference=_reference(request_id),
            message=answer,
        )
        return _save_result(
            db,
            request_id=request_id,
            db_state="answered",
            request_kind="question",
            result=result,
            payload=payload,
            source_event_id=source_event_id,
        )

    if intent.intent == "unknown":
        return _needs_input(
            db,
            request_id=request_id,
            request_kind="unknown",
            payload=payload,
            message="What would you like to record or ask about?",
            continuation_kind="reinterpret",
            source_event_id=source_event_id,
        )

    target = _target_for_intent(
        db,
        binding,
        intent=intent,
        input_text=input_text,
        image_analysis=image_analysis,
    )
    if selected_target is not None:
        plant_id, plot_id = selected_target
        selected = resolve_garden_target(
            db,
            binding.context,
            plant_query=plant_id,
            plot_query=plot_id,
        )
        if selected.status == "resolved":
            target = selected
    if target.status in {"ambiguous_plant", "ambiguous_location"}:
        prompt = (
            "Which plant did you mean?"
            if target.status == "ambiguous_plant"
            else f"Where is {target.plant_name} growing?"
        )
        return _needs_input(
            db,
            request_id=request_id,
            request_kind=intent.intent,
            payload=payload,
            message=prompt
            + "\n"
            + "\n".join(
                f"{index}. {choice.label}" for index, choice in enumerate(target.choices, 1)
            ),
            choices=target.choices,
            continuation_kind="target_choice",
            source_event_id=source_event_id,
        )
    if target.status == "not_found":
        return _needs_input(
            db,
            request_id=request_id,
            request_kind=intent.intent,
            payload=payload,
            message="Which plant is this about? I could not find a unique match in this garden.",
            continuation_kind="reinterpret",
            source_event_id=source_event_id,
        )
    payload["resolved_target"] = {
        "plant_id": target.plant_id,
        "plant_name": target.plant_name,
        "latin": target.latin,
        "plot_id": target.plot_id,
        "plot_label": target.plot_label,
    }

    occurred = intent.occurred_on or occurred_on
    if intent.intent == "journal":
        if intent.event_type is None:
            return _needs_input(
                db,
                request_id=request_id,
                request_kind="journal",
                payload=payload,
                message="What happened to the plant?",
                continuation_kind="reinterpret",
                source_event_id=source_event_id,
            )
        fields = {
            "schema_version": 1,
            "event_type": intent.event_type,
            "occurred_on": occurred,
            "title": intent.title,
            "notes": intent.notes or input_text,
            "plant_ids": [target.plant_id],
            "plot_ids": [target.plot_id] if target.plot_id else [],
            "metadata": {
                "source": "matrix_assistant",
                "matrix_event_id": payload.get("source_event_id", ""),
            },
        }
        return _proposal_result(
            db,
            request_id=request_id,
            kind="journal",
            summary=(
                f"Record {target.plant_name} as {intent.event_type.replace('_', ' ')}"
                + (f" in {target.plot_label}" if target.plot_label else "")
                + f" on {occurred}."
            ),
            fields=fields,
            payload=payload,
            source_event_id=source_event_id,
        )

    if intent.intent == "harvest":
        missing = [
            label
            for value, label in ((intent.quantity, "quantity"), (intent.unit, "unit"))
            if value is None
        ]
        if missing:
            return _needs_input(
                db,
                request_id=request_id,
                request_kind="harvest",
                payload=payload,
                message=f"What {' and '.join(missing)} should I record for the harvest?",
                continuation_kind="reinterpret",
                source_event_id=source_event_id,
            )
        fields = {
            "schema_version": 1,
            "occurred_on": occurred,
            "quantity": intent.quantity,
            "unit": intent.unit,
            "quality": intent.quality,
            "notes": intent.notes or input_text,
            "plant_ids": [target.plant_id],
            "plot_ids": [target.plot_id] if target.plot_id else [],
        }
        return _proposal_result(
            db,
            request_id=request_id,
            kind="harvest",
            summary=(
                f"Record a harvest of {intent.quantity:g} {intent.unit} from {target.plant_name}"
                + (f" in {target.plot_label}" if target.plot_label else "")
                + f" on {occurred}."
            ),
            fields=fields,
            payload=payload,
            source_event_id=source_event_id,
        )

    if intent.intent == "issue":
        issue_fields: dict[str, Any] = {
            "issue_type": intent.issue_type or "other",
            "title": intent.title or "Plant health issue",
            "description": intent.symptoms or intent.notes or input_text,
            "severity": intent.severity,
            "suspected_cause": "",
            "treatment_plan": "",
        }
        if capture_asset_id:
            issue_fields.update(
                _diagnosis_fields(
                    db,
                    binding,
                    capture_asset_id=capture_asset_id,
                    intent=intent,
                    target=target,
                )
            )
        fields = {
            "schema_version": 1,
            **issue_fields,
            "follow_up_on": None,
            "plant_ids": [target.plant_id],
            "plot_ids": [target.plot_id] if target.plot_id else [],
        }
        return _proposal_result(
            db,
            request_id=request_id,
            kind="issue",
            summary=(
                f"Report {fields['title']} for {target.plant_name}"
                + (f" in {target.plot_label}" if target.plot_label else "")
                + "."
            ),
            fields=fields,
            payload=payload,
            source_event_id=source_event_id,
        )

    tasks = _task_candidates(db, binding, intent=intent, target=target)
    if selected_task_id:
        tasks = [task for task in tasks if str(task["public_id"]) == selected_task_id]
    if not tasks:
        return _needs_input(
            db,
            request_id=request_id,
            request_kind="task_completion",
            payload=payload,
            message="I could not find a matching open task in this garden.",
            continuation_kind="reinterpret",
            source_event_id=source_event_id,
        )
    if len(tasks) > 1:
        choices = [
            AssistantChoice(
                value=str(task["public_id"]),
                label=str(task["title"] or task["task_type"]),
                description=f"Due {task['due_on']}",
            )
            for task in tasks
        ]
        return _needs_input(
            db,
            request_id=request_id,
            request_kind="task_completion",
            payload=payload,
            message="Which task did you complete?\n"
            + "\n".join(
                f"{index}. {choice.label} ({choice.description})"
                for index, choice in enumerate(choices, 1)
            ),
            choices=choices,
            continuation_kind="task_choice",
            source_event_id=source_event_id,
        )
    task = tasks[0]
    task_type = str(task["task_type"])
    normalized_input = " ".join(input_text.casefold().split())
    completion_outcome = (
        "not_seen_blooming_this_season"
        if task_type == "observe_bloom"
        and any(
            phrase in normalized_input
            for phrase in (
                "did not bloom",
                "didn't bloom",
                "has not bloomed",
                "no bloom",
                "not seen blooming",
            )
        )
        else "done"
    )
    fields = {
        "schema_version": 1,
        "task_id": str(task["public_id"]),
        "expected_updated_at_ms": int(task["updated_at_ms"]),
        "completed_plant_ids": [target.plant_id]
        if task_type in {"observe_bloom", "prune", "fertilize"}
        else None,
        "selected_plot_ids": [target.plot_id] if target.plot_id else [],
        "completion_outcome": completion_outcome,
        "notes": intent.notes or input_text,
        "occurred_on": occurred,
    }
    return _proposal_result(
        db,
        request_id=request_id,
        kind="task_completion",
        summary=f"Complete task '{task['title'] or task_type}' for {target.plant_name}.",
        fields=fields,
        payload=payload,
        source_event_id=source_event_id,
    )


def _new_request(
    db: DbConn,
    binding: AssistantBinding,
    *,
    room_id: str,
    event_id: str,
    sender_id: str,
    input_text: str,
    capture_asset_id: str = "",
) -> tuple[str, AssistantResult | None]:
    assert_source_binding(binding, room_id=room_id, sender_id=sender_id)
    existing = db.execute(
        """
        SELECT * FROM assistant_requests
        WHERE source_channel = 'matrix' AND source_room_id = %s AND source_event_id = %s
        """,
        (room_id, event_id),
    ).fetchone()
    if existing:
        return str(existing["public_id"]), _result_from_row(dict(existing))
    request_id = generate_public_id("asst")
    now_ms = current_timestamp_ms()
    db.execute(
        """
        INSERT INTO assistant_requests
            (public_id, garden_id, actor_user_id, source_channel, source_room_id,
             source_event_id, source_sender_id, request_kind, state, input_text,
             capture_asset_id, payload_json, result_json, created_at_ms, updated_at_ms,
             expires_at_ms, last_source_event_id)
        VALUES (%s, %s, %s, 'matrix', %s, %s, %s, 'unknown', 'processing',
                %s, %s, '{}', '{}', %s, %s, %s, %s)
        """,
        (
            request_id,
            binding.garden_id,
            binding.user_id,
            room_id,
            event_id,
            sender_id,
            input_text,
            capture_asset_id or None,
            now_ms,
            now_ms,
            now_ms + _request_ttl_ms(),
            event_id,
        ),
    )
    return request_id, None


def process_text(
    db: DbConn,
    binding: AssistantBinding,
    *,
    source_room_id: str,
    source_event_id: str,
    source_sender_id: str,
    text: str,
    occurred_on: str,
) -> AssistantResult:
    request_id, existing = _new_request(
        db,
        binding,
        room_id=source_room_id,
        event_id=source_event_id,
        sender_id=source_sender_id,
        input_text=text,
    )
    if existing is not None:
        return existing
    payload = {"schema_version": 1, "source_event_id": source_event_id}
    try:
        intent = _interpret(db, binding, text=text, occurred_on=occurred_on)
        return _advance_to_result(
            db,
            binding,
            request_id=request_id,
            intent=intent,
            input_text=text,
            occurred_on=occurred_on,
            payload=payload,
        )
    except (AIProviderTimeout, AIProviderRateLimited) as exc:
        return _error_result(
            db,
            request_id=request_id,
            request_kind="unknown",
            payload=payload,
            message=exc.detail,
            retryable=True,
        )
    except (AIProviderError, AIProviderNotConfigured) as exc:
        return _error_result(
            db,
            request_id=request_id,
            request_kind="unknown",
            payload=payload,
            message=exc.detail,
            retryable=False,
        )


def analyze_matrix_capture(
    db: DbConn,
    binding: AssistantBinding,
    *,
    source_room_id: str,
    source_event_id: str,
    source_sender_id: str,
    capture_asset_id: str,
    caption: str,
    occurred_on: str,
) -> AssistantResult:
    request_id, existing = _new_request(
        db,
        binding,
        room_id=source_room_id,
        event_id=source_event_id,
        sender_id=source_sender_id,
        input_text=caption,
        capture_asset_id=capture_asset_id,
    )
    if existing is not None:
        return existing
    payload = {"schema_version": 1, "source_event_id": source_event_id}
    try:
        analysis = analyze_capture(
            db,
            binding.context,
            asset_id=capture_asset_id,
            caption=caption,
        )
        intent = _interpret(
            db,
            binding,
            text=caption or "Record what is visible in this garden photo.",
            occurred_on=occurred_on,
        )
        if intent.intent == "unknown" and analysis.issue_candidate is not None:
            intent = intent.model_copy(
                update={"intent": "issue", "symptoms": analysis.issue_candidate.value}
            )
        elif intent.intent == "unknown" and analysis.event_candidate is not None:
            event_type = analysis.event_candidate.value
            if event_type in {
                "planted",
                "moved",
                "divided",
                "pruned",
                "watered",
                "fertilized",
                "bloomed",
                "died",
                "observed",
            }:
                intent = AssistantIntent.model_validate(
                    {
                        **intent.model_dump(mode="json"),
                        "intent": "journal",
                        "event_type": event_type,
                    }
                )
        return _advance_to_result(
            db,
            binding,
            request_id=request_id,
            intent=intent,
            input_text=caption or "Garden photo",
            occurred_on=occurred_on,
            capture_asset_id=capture_asset_id,
            image_analysis=analysis,
            payload=payload,
        )
    except (AIProviderTimeout, AIProviderRateLimited) as exc:
        return _error_result(
            db,
            request_id=request_id,
            request_kind="unknown",
            payload=payload,
            message=exc.detail,
            retryable=True,
        )
    except (AIProviderError, AIProviderNotConfigured) as exc:
        return _error_result(
            db,
            request_id=request_id,
            request_kind="unknown",
            payload=payload,
            message=exc.detail,
            retryable=False,
        )


def get_request(db: DbConn, binding: AssistantBinding, *, request_id: str) -> AssistantResult:
    request_id = _resolve_request_id(db, binding, request_id)
    row = db.execute(
        "SELECT * FROM assistant_requests WHERE public_id = %s AND garden_id = %s",
        (request_id, binding.garden_id),
    ).fetchone()
    if not row or int(row["actor_user_id"]) != binding.user_id:
        raise HTTPException(status_code=404, detail="Assistant request not found")
    if (
        str(row["source_room_id"]) != binding.room_id
        or str(row["source_sender_id"]) != binding.sender_id
    ):
        raise HTTPException(status_code=403, detail="Assistant request source is not authorized")
    return _result_from_row(dict(row))


def continue_request(
    db: DbConn,
    binding: AssistantBinding,
    *,
    request_id: str,
    source_event_id: str,
    text: str,
) -> AssistantResult:
    request_id = _resolve_request_id(db, binding, request_id)
    row_raw = db.execute(
        "SELECT * FROM assistant_requests WHERE public_id = %s AND garden_id = %s FOR UPDATE",
        (request_id, binding.garden_id),
    ).fetchone()
    if not row_raw or int(row_raw["actor_user_id"]) != binding.user_id:
        raise HTTPException(status_code=404, detail="Assistant request not found")
    row = dict(row_raw)
    if (
        str(row["source_room_id"]) != binding.room_id
        or str(row["source_sender_id"]) != binding.sender_id
    ):
        raise HTTPException(status_code=403, detail="Assistant request source is not authorized")
    if str(row.get("last_source_event_id") or "") == source_event_id:
        return _result_from_row(row)
    state = str(row["state"])
    if state not in {"needs_input", "proposal"}:
        raise HTTPException(status_code=409, detail=f"Cannot continue a request in state {state}")
    payload = _json_object(row["payload_json"])
    original_text = str(payload.get("input_text") or row.get("input_text") or "")
    occurred_on = str(payload.get("occurred_on") or date.today().isoformat())
    if text.strip().casefold() in {"save", "cancel"}:
        raise HTTPException(status_code=409, detail="Use the explicit save or cancel command")

    if state == "needs_input" and text.strip().isdigit():
        choices = [AssistantChoice.model_validate(choice) for choice in payload.get("choices", [])]
        index = int(text.strip()) - 1
        if index < 0 or index >= len(choices):
            raise HTTPException(status_code=422, detail="Choice number is out of range")
        intent = AssistantIntent.model_validate(payload["intent"])
        continuation_kind = str(payload.get("continuation_kind") or "")
        selected_target = None
        selected_task = ""
        if continuation_kind == "target_choice":
            selected_target = target_from_choice(choices[index].value)
        elif continuation_kind == "task_choice":
            selected_task = choices[index].value
        else:
            raise HTTPException(status_code=422, detail="This request does not accept a choice")
        return _advance_to_result(
            db,
            binding,
            request_id=request_id,
            intent=intent,
            input_text=original_text,
            occurred_on=occurred_on,
            capture_asset_id=str(row.get("capture_asset_id") or ""),
            image_analysis=(
                CaptureAnalysis.model_validate(payload["capture_analysis"])
                if payload.get("capture_analysis")
                else None
            ),
            payload=payload,
            selected_target=selected_target,
            selected_task_id=selected_task,
            source_event_id=source_event_id,
        )

    edit_text = text.strip()
    combined = f"{original_text}\nUser clarification or edit: {edit_text}"[:2000]
    try:
        intent = _interpret(db, binding, text=combined, occurred_on=occurred_on)
        return _advance_to_result(
            db,
            binding,
            request_id=request_id,
            intent=intent,
            input_text=combined,
            occurred_on=occurred_on,
            capture_asset_id=str(row.get("capture_asset_id") or ""),
            image_analysis=(
                CaptureAnalysis.model_validate(payload["capture_analysis"])
                if payload.get("capture_analysis")
                else None
            ),
            payload=payload,
            source_event_id=source_event_id,
        )
    except (AIProviderTimeout, AIProviderRateLimited) as exc:
        return _error_result(
            db,
            request_id=request_id,
            request_kind=str(row["request_kind"]),
            payload=payload,
            message=exc.detail,
            retryable=True,
            source_event_id=source_event_id,
        )
    except (AIProviderError, AIProviderNotConfigured) as exc:
        return _error_result(
            db,
            request_id=request_id,
            request_kind=str(row["request_kind"]),
            payload=payload,
            message=exc.detail,
            retryable=False,
            source_event_id=source_event_id,
        )


def _records_from_command(command: CommandResult) -> list[AssistantRecord]:
    labels = {
        "journal_entry": "Journal entry",
        "harvest_entry": "Harvest entry",
        "issue": "Garden issue",
        "task": "Garden task",
    }
    return [
        AssistantRecord(type=cast(Any, record_type), id=record_id, label=labels[record_type])
        for record_type, record_id in command.records
    ] or [
        AssistantRecord(
            type=command.primary_type,
            id=command.primary_id,
            label=labels[command.primary_type],
        )
    ]


def _apply_command(
    db: DbConn,
    binding: AssistantBinding,
    *,
    proposal: AssistantProposal,
) -> tuple[CommandResult, list[str]]:
    fields = dict(proposal.fields)
    fields.pop("schema_version", None)
    plant_ids = [str(value) for value in fields.get("plant_ids") or []]
    if proposal.kind == "journal":
        command = create_journal_entry_command(
            db,
            binding.context,
            event_type=fields["event_type"],
            occurred_on=fields["occurred_on"],
            title=str(fields.get("title") or ""),
            notes=str(fields.get("notes") or ""),
            metadata=_json_object(fields.get("metadata")),
            plant_ids=plant_ids,
            plot_ids=[str(value) for value in fields.get("plot_ids") or []],
        )
    elif proposal.kind == "harvest":
        command = create_harvest_entry_command(
            db,
            binding.context,
            occurred_on=fields["occurred_on"],
            quantity=float(fields["quantity"]),
            unit=fields["unit"],
            quality=fields.get("quality") or "good",
            notes=str(fields.get("notes") or ""),
            plant_ids=plant_ids,
            plot_ids=[str(value) for value in fields.get("plot_ids") or []],
        )
    elif proposal.kind == "issue":
        command = create_issue_command(
            db,
            binding.context,
            issue_type=fields["issue_type"],
            title=str(fields.get("title") or ""),
            description=str(fields.get("description") or ""),
            severity=fields.get("severity") or "normal",
            suspected_cause=str(fields.get("suspected_cause") or ""),
            treatment_plan=str(fields.get("treatment_plan") or ""),
            follow_up_on=fields.get("follow_up_on"),
            plant_ids=plant_ids,
            plot_ids=[str(value) for value in fields.get("plot_ids") or []],
        )
    else:
        plant_ids = [str(value) for value in fields.get("completed_plant_ids") or []]
        command = complete_task_command(
            db,
            binding.context,
            task_public_id=str(fields["task_id"]),
            expected_updated_at_ms=int(fields["expected_updated_at_ms"]),
            completed_plant_ids=plant_ids or None,
            completion_outcome=fields.get("completion_outcome"),
            notes=str(fields.get("notes") or ""),
            occurred_on=str(fields.get("occurred_on") or date.today().isoformat()),
            selected_plot_ids=[str(value) for value in fields.get("selected_plot_ids") or []],
        )
    return command, plant_ids


def apply_request(
    db: DbConn,
    binding: AssistantBinding,
    *,
    request_id: str,
    source_event_id: str,
) -> AssistantResult:
    request_id = _resolve_request_id(db, binding, request_id)
    row_raw = db.execute(
        "SELECT * FROM assistant_requests WHERE public_id = %s AND garden_id = %s FOR UPDATE",
        (request_id, binding.garden_id),
    ).fetchone()
    if not row_raw or int(row_raw["actor_user_id"]) != binding.user_id:
        raise HTTPException(status_code=404, detail="Assistant request not found")
    row = dict(row_raw)
    if (
        str(row["source_room_id"]) != binding.room_id
        or str(row["source_sender_id"]) != binding.sender_id
    ):
        raise HTTPException(status_code=403, detail="Assistant request source is not authorized")
    if str(row["state"]) == "applied":
        return _result_from_row(row)
    now_ms = current_timestamp_ms()
    if int(row["expires_at_ms"]) <= now_ms:
        db.execute(
            """
            UPDATE assistant_requests
            SET state = 'expired', updated_at_ms = %s
            WHERE public_id = %s
            """,
            (now_ms, request_id),
        )
        raise HTTPException(status_code=409, detail="Assistant request has expired")
    if str(row["state"]) != "proposal":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot apply a request in state {row['state']}",
        )
    payload = _json_object(row["payload_json"])
    proposal = AssistantProposal.model_validate(payload.get("proposal"))
    command, plant_ids = _apply_command(db, binding, proposal=proposal)
    capture_id = str(row.get("capture_asset_id") or "")
    if capture_id:
        targets: list[tuple[Any, str]] = []
        for record_type, record_id in command.records:
            if record_type in {"journal_entry", "harvest_entry", "issue"}:
                targets.append((record_type, record_id))
        targets.extend(("plant", plant_id) for plant_id in plant_ids)
        if targets:
            link_existing_media_command(
                db,
                binding.context,
                asset_id=capture_id,
                targets=targets,
            )
            db.execute(
                "DELETE FROM media_links WHERE asset_id = %s AND target_type = 'matrix_capture'",
                (capture_id,),
            )
    records = _records_from_command(command)
    detail = json.dumps(
        {
            "assistant_request_id": request_id,
            "matrix_event_id": source_event_id,
            "records": [record.model_dump(mode="json") for record in records],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    write_required_audit_event(
        method="POST",
        path="/mcp",
        status_code=200,
        remote_host="127.0.0.1",
        detail=detail,
        auth_context=binding.context,
        garden_id=binding.garden_id,
        db=db,
    )
    result = AssistantResult(
        state="applied",
        request_id=request_id,
        reference=_reference(request_id),
        message="Saved: " + ", ".join(f"{record.type} {record.id}" for record in records),
        proposal=proposal,
        records=records,
    )
    return _save_result(
        db,
        request_id=request_id,
        db_state="applied",
        request_kind=proposal.kind,
        result=result,
        payload=payload,
        source_event_id=source_event_id,
        applied_at_ms=now_ms,
    )


def _remove_temporary_capture(db: DbConn, *, capture_asset_id: str) -> None:
    row = db.execute(
        "SELECT storage_key, preview_storage_key FROM media_assets WHERE asset_id = %s",
        (capture_asset_id,),
    ).fetchone()
    db.execute(
        "DELETE FROM media_links WHERE asset_id = %s AND target_type = 'matrix_capture'",
        (capture_asset_id,),
    )
    remaining = db.execute(
        "SELECT 1 FROM media_links WHERE asset_id = %s LIMIT 1", (capture_asset_id,)
    ).fetchone()
    if row and not remaining:
        storage_pair = (str(row["storage_key"]), str(row["preview_storage_key"]))
        enqueue_media_cleanup_jobs(db, [storage_pair])
        db.execute("DELETE FROM media_assets WHERE asset_id = %s", (capture_asset_id,))


def cancel_request(
    db: DbConn,
    binding: AssistantBinding,
    *,
    request_id: str,
    source_event_id: str,
) -> AssistantResult:
    request_id = _resolve_request_id(db, binding, request_id)
    row_raw = db.execute(
        "SELECT * FROM assistant_requests WHERE public_id = %s AND garden_id = %s FOR UPDATE",
        (request_id, binding.garden_id),
    ).fetchone()
    if not row_raw or int(row_raw["actor_user_id"]) != binding.user_id:
        raise HTTPException(status_code=404, detail="Assistant request not found")
    row = dict(row_raw)
    if (
        str(row["source_room_id"]) != binding.room_id
        or str(row["source_sender_id"]) != binding.sender_id
    ):
        raise HTTPException(status_code=403, detail="Assistant request source is not authorized")
    if str(row["state"]) == "cancelled":
        return _result_from_row(row)
    if str(row["state"]) not in _ACTIVE_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel a request in state {row['state']}",
        )
    capture_id = str(row.get("capture_asset_id") or "")
    if capture_id:
        _remove_temporary_capture(db, capture_asset_id=capture_id)
    result = AssistantResult(
        state="cancelled",
        request_id=request_id,
        reference=_reference(request_id),
        message="Cancelled.",
    )
    return _save_result(
        db,
        request_id=request_id,
        db_state="cancelled",
        request_kind=str(row["request_kind"]),
        result=result,
        payload=_json_object(row["payload_json"]),
        source_event_id=source_event_id,
    )


def expire_and_cleanup_requests(db: DbConn, *, now_ms: int | None = None) -> int:
    timestamp = current_timestamp_ms() if now_ms is None else now_ms
    db.execute(
        """
        UPDATE assistant_requests
        SET state = 'expired', updated_at_ms = %s
        WHERE state IN ('processing', 'needs_input', 'proposal') AND expires_at_ms <= %s
        """,
        (timestamp, timestamp),
    )
    rows = db.execute(
        """
        SELECT public_id, capture_asset_id
        FROM assistant_requests
        WHERE state IN ('expired', 'cancelled', 'failed') AND capture_asset_id IS NOT NULL
        FOR UPDATE
        """
    ).fetchall()
    for row in rows:
        capture_id = str(row["capture_asset_id"] or "")
        if capture_id:
            _remove_temporary_capture(db, capture_asset_id=capture_id)
    return len(rows)
