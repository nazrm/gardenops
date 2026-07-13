"""Platform-admin provider settings endpoints."""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from gardenops.audit import write_audit_event
from gardenops.db import DB
from gardenops.models import StrictBaseModel
from gardenops.platform_secrets import (
    ANTHROPIC_API_KEY,
    OPENAI_API_KEY,
    PLANTNET_API_KEY,
    SHADEMAP_API_KEY,
    ConfigurationError,
)
from gardenops.provider_settings import (
    AI_PROVIDER_SETTING,
    ANTHROPIC_MODEL_SETTING,
    OPENAI_FAST_MODEL_SETTING,
    OPENAI_MODEL_SETTING,
    apply_provider_settings_update,
    configuration_error_response_detail,
    get_provider_settings_summary,
)
from gardenops.routers.auth import _require_admin_context, enforce_destructive_admin_controls
from gardenops.security_metrics import record_security_event

router = APIRouter()


class ProviderSettingsUpdateBody(StrictBaseModel):
    ai_provider: Literal["disabled", "openai", "anthropic"] | None = None
    openai_model: str | None = Field(default=None, max_length=120)
    openai_fast_model: str | None = Field(default=None, max_length=120)
    anthropic_model: str | None = Field(default=None, max_length=120)
    openai_api_key: str | None = Field(default=None, max_length=500)
    anthropic_api_key: str | None = Field(default=None, max_length=500)
    plantnet_api_key: str | None = Field(default=None, max_length=500)
    shademap_api_key: str | None = Field(default=None, max_length=500)
    clear_openai_api_key: bool = False
    clear_anthropic_api_key: bool = False
    clear_plantnet_api_key: bool = False
    clear_shademap_api_key: bool = False
    action_reason: str = Field(default="", max_length=400)


_SECRET_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("openai_api_key", "clear_openai_api_key", OPENAI_API_KEY),
    ("anthropic_api_key", "clear_anthropic_api_key", ANTHROPIC_API_KEY),
    ("plantnet_api_key", "clear_plantnet_api_key", PLANTNET_API_KEY),
    ("shademap_api_key", "clear_shademap_api_key", SHADEMAP_API_KEY),
)


@router.get("/admin/provider-settings")
def admin_provider_settings(request: Request, db: DB) -> dict[str, object]:
    _require_admin_context(request)
    return get_provider_settings_summary(db)


def _request_remote_host(request: Request) -> str:
    return request.client.host if request.client else ""


def _detail(
    *,
    changed_settings: tuple[str, ...],
    set_secrets: tuple[str, ...],
    cleared_secrets: tuple[str, ...],
    action_reason: str,
) -> str:
    return "provider_settings.updated " + json.dumps(
        {
            "changed_settings": list(changed_settings),
            "set_secrets": list(set_secrets),
            "cleared_secrets": list(cleared_secrets),
            "action_reason": action_reason,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


@router.put("/admin/provider-settings")
def update_admin_provider_settings(
    body: ProviderSettingsUpdateBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context, action_reason = enforce_destructive_admin_controls(
        request,
        body_reason=body.action_reason,
    )

    secret_values: dict[str, str | None] = {}
    clear_secret_keys: list[str] = []
    for value_field, clear_field, secret_key in _SECRET_FIELDS:
        value_was_sent = value_field in body.model_fields_set
        raw_value = getattr(body, value_field)
        clear_value = bool(getattr(body, clear_field))
        if clear_value and value_was_sent and raw_value is not None:
            raise HTTPException(
                status_code=422,
                detail=f"{value_field} cannot be set and cleared in the same request",
            )
        if value_was_sent and raw_value is not None:
            if not raw_value.strip():
                raise HTTPException(status_code=422, detail=f"{value_field} must not be empty")
            secret_values[secret_key] = raw_value
        if clear_value:
            clear_secret_keys.append(secret_key)

    settings: dict[str, str | None] = {}
    if "ai_provider" in body.model_fields_set:
        settings[AI_PROVIDER_SETTING] = body.ai_provider
    if "openai_model" in body.model_fields_set:
        settings[OPENAI_MODEL_SETTING] = body.openai_model
    if "openai_fast_model" in body.model_fields_set:
        settings[OPENAI_FAST_MODEL_SETTING] = body.openai_fast_model
    if "anthropic_model" in body.model_fields_set:
        settings[ANTHROPIC_MODEL_SETTING] = body.anthropic_model

    try:
        summary, changes = apply_provider_settings_update(
            db,
            settings=settings,
            secret_values=secret_values,
            clear_secret_keys=clear_secret_keys,
            actor_user_id=context.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail=configuration_error_response_detail(exc),
        ) from exc

    db.commit()
    record_security_event("destructive_admin_actions")
    record_security_event("destructive_admin_actions_provider_settings")
    request.state.audited_by_handler = True
    write_audit_event(
        method=request.method,
        path=request.url.path,
        status_code=200,
        remote_host=_request_remote_host(request),
        detail=_detail(
            changed_settings=changes.changed_settings,
            set_secrets=changes.set_secrets,
            cleared_secrets=changes.cleared_secrets,
            action_reason=action_reason,
        ),
        auth_context=context,
        db=db,
    )
    return summary
