"""Platform-managed provider settings and secret resolution."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from gardenops.db import DbConn, get_db, return_db
from gardenops.platform_secrets import (
    ANTHROPIC_API_KEY,
    OPENAI_API_KEY,
    PLANTNET_API_KEY,
    SHADEMAP_API_KEY,
    ConfigurationError,
    SecretMetadata,
    clear_database_secret,
    get_database_secret,
    secret_metadata_with_env_fallback,
    secrets_encryption_configured,
    set_database_secret,
)
from gardenops.services.ai_provider_defaults import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_OPENAI_FAST_MODEL,
    DEFAULT_OPENAI_MODEL,
)

AiProviderSetting = Literal["disabled", "openai", "anthropic"]
SecretSource = Literal["db", "env", "none"]

AI_PROVIDER_SETTING = "ai_provider"
OPENAI_MODEL_SETTING = "openai_model"
OPENAI_FAST_MODEL_SETTING = "openai_fast_model"
ANTHROPIC_MODEL_SETTING = "anthropic_model"

SUPPORTED_AI_PROVIDERS: frozenset[str] = frozenset({"disabled", "openai", "anthropic"})

OPENAI_API_KEY_ENVS = ("OPENAI_API_KEY",)
ANTHROPIC_API_KEY_ENVS = ("ANTHROPIC_API_KEY",)
PLANTNET_API_KEY_ENVS = ("PLANTNET_API_KEY",)
SHADEMAP_API_KEY_ENVS = ("SHADEMAP", "SHADEMAP_API_KEY", "SHADEMAP_KEY")

SECRET_ENV_NAMES: dict[str, tuple[str, ...]] = {
    OPENAI_API_KEY: OPENAI_API_KEY_ENVS,
    ANTHROPIC_API_KEY: ANTHROPIC_API_KEY_ENVS,
    PLANTNET_API_KEY: PLANTNET_API_KEY_ENVS,
    SHADEMAP_API_KEY: SHADEMAP_API_KEY_ENVS,
}


@dataclass(frozen=True)
class AiRuntimeConfig:
    provider: AiProviderSetting
    openai_api_key: str | None
    anthropic_api_key: str | None
    openai_model: str
    openai_fast_model: str
    anthropic_model: str


@dataclass(frozen=True)
class ProviderSettingsChanges:
    changed_settings: tuple[str, ...]
    set_secrets: tuple[str, ...]
    cleared_secrets: tuple[str, ...]


def _setting_value(conn: DbConn, key: str) -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key = %s", (key,)).fetchone()
    return str(row["value"]).strip() if row and row["value"] is not None else ""


def _upsert_setting(conn: DbConn, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        (key, value),
    )


def _delete_setting(conn: DbConn, key: str) -> None:
    conn.execute("DELETE FROM app_settings WHERE key = %s", (key,))


def _env_value(names: Iterable[str]) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _resolved_setting(
    conn: DbConn,
    key: str,
    *,
    env_name: str,
    default: str,
) -> str:
    db_value = _setting_value(conn, key)
    if db_value:
        return db_value
    env_value = os.environ.get(env_name, "").strip()
    return env_value or default


def _selected_provider(conn: DbConn) -> AiProviderSetting:
    raw = _setting_value(conn, AI_PROVIDER_SETTING) or os.environ.get("AI_PROVIDER", "")
    provider = raw.strip().lower() or "disabled"
    if provider not in SUPPORTED_AI_PROVIDERS:
        return "disabled"
    return provider  # type: ignore[return-value]


def env_ai_provider_value() -> str:
    return os.environ.get("AI_PROVIDER", "").strip().lower()


def _env_selected_provider() -> AiProviderSetting:
    provider = env_ai_provider_value() or "disabled"
    if provider not in SUPPORTED_AI_PROVIDERS:
        return "disabled"
    return provider  # type: ignore[return-value]


def validate_ai_provider(value: str) -> AiProviderSetting:
    provider = value.strip().lower()
    if provider not in SUPPORTED_AI_PROVIDERS:
        allowed = ", ".join(sorted(SUPPORTED_AI_PROVIDERS))
        raise ValueError(f"ai_provider must be one of: {allowed}")
    return provider  # type: ignore[return-value]


def _secret_value(conn: DbConn, key: str, env_names: tuple[str, ...]) -> str | None:
    database_value = get_database_secret(conn, key)
    if database_value is not None and database_value.strip():
        return database_value.strip()
    env_value = _env_value(env_names)
    return env_value or None


def _with_connection[T](conn: DbConn | None, callback: Callable[[DbConn], T]) -> T:
    if conn is not None:
        return callback(conn)
    owned = get_db()
    try:
        return callback(owned)
    finally:
        return_db(owned)


def _env_ai_runtime_config() -> AiRuntimeConfig:
    return AiRuntimeConfig(
        provider=_env_selected_provider(),
        openai_api_key=_env_value(OPENAI_API_KEY_ENVS) or None,
        anthropic_api_key=_env_value(ANTHROPIC_API_KEY_ENVS) or None,
        openai_model=os.environ.get("OPENAI_MODEL", "").strip() or DEFAULT_OPENAI_MODEL,
        openai_fast_model=os.environ.get("OPENAI_FAST_MODEL", "").strip()
        or DEFAULT_OPENAI_FAST_MODEL,
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_ANTHROPIC_MODEL,
    )


def get_ai_runtime_config(conn: DbConn | None = None) -> AiRuntimeConfig:
    def load(active_conn: DbConn) -> AiRuntimeConfig:
        return AiRuntimeConfig(
            provider=_selected_provider(active_conn),
            openai_api_key=_secret_value(active_conn, OPENAI_API_KEY, OPENAI_API_KEY_ENVS),
            anthropic_api_key=_secret_value(
                active_conn,
                ANTHROPIC_API_KEY,
                ANTHROPIC_API_KEY_ENVS,
            ),
            openai_model=_resolved_setting(
                active_conn,
                OPENAI_MODEL_SETTING,
                env_name="OPENAI_MODEL",
                default=DEFAULT_OPENAI_MODEL,
            ),
            openai_fast_model=_resolved_setting(
                active_conn,
                OPENAI_FAST_MODEL_SETTING,
                env_name="OPENAI_FAST_MODEL",
                default=DEFAULT_OPENAI_FAST_MODEL,
            ),
            anthropic_model=_resolved_setting(
                active_conn,
                ANTHROPIC_MODEL_SETTING,
                env_name="ANTHROPIC_MODEL",
                default=DEFAULT_ANTHROPIC_MODEL,
            ),
        )

    try:
        return _with_connection(conn, load)
    except RuntimeError:
        if conn is not None:
            raise
        return _env_ai_runtime_config()


def get_plantnet_api_key(conn: DbConn | None = None) -> str | None:
    try:
        return _with_connection(
            conn,
            lambda active_conn: _secret_value(active_conn, PLANTNET_API_KEY, PLANTNET_API_KEY_ENVS),
        )
    except RuntimeError:
        if conn is not None:
            raise
        return _env_value(PLANTNET_API_KEY_ENVS) or None


def get_shademap_api_key(conn: DbConn | None = None) -> str | None:
    try:
        return _with_connection(
            conn,
            lambda active_conn: _secret_value(active_conn, SHADEMAP_API_KEY, SHADEMAP_API_KEY_ENVS),
        )
    except RuntimeError:
        if conn is not None:
            raise
        return _env_value(SHADEMAP_API_KEY_ENVS) or None


def _updated_by_usernames(conn: DbConn, metadata: Iterable[SecretMetadata]) -> dict[int, str]:
    user_ids = sorted(
        {item.updated_by_user_id for item in metadata if item.updated_by_user_id is not None},
    )
    if not user_ids:
        return {}
    rows = conn.execute(
        "SELECT id, username FROM auth_users WHERE id = ANY(%s)",
        (user_ids,),
    ).fetchall()
    return {int(row["id"]): str(row["username"]) for row in rows}


def _secret_status_dict(
    metadata: SecretMetadata,
    usernames: Mapping[int, str],
) -> dict[str, object]:
    updated_by = metadata.updated_by_user_id
    return {
        "configured": metadata.configured,
        "source": metadata.source,
        "last4": metadata.last4,
        "updated_at_ms": metadata.updated_at_ms,
        "updated_by_user_id": updated_by,
        "updated_by_username": usernames.get(updated_by) if updated_by is not None else None,
    }


def get_provider_settings_summary(conn: DbConn) -> dict[str, object]:
    secret_metadata = {
        key: secret_metadata_with_env_fallback(conn, key, env_names)
        for key, env_names in SECRET_ENV_NAMES.items()
    }
    usernames = _updated_by_usernames(conn, secret_metadata.values())
    return {
        "ai_provider": _selected_provider(conn),
        "models": {
            "openai_model": _resolved_setting(
                conn,
                OPENAI_MODEL_SETTING,
                env_name="OPENAI_MODEL",
                default=DEFAULT_OPENAI_MODEL,
            ),
            "openai_fast_model": _resolved_setting(
                conn,
                OPENAI_FAST_MODEL_SETTING,
                env_name="OPENAI_FAST_MODEL",
                default=DEFAULT_OPENAI_FAST_MODEL,
            ),
            "anthropic_model": _resolved_setting(
                conn,
                ANTHROPIC_MODEL_SETTING,
                env_name="ANTHROPIC_MODEL",
                default=DEFAULT_ANTHROPIC_MODEL,
            ),
        },
        "secrets": {
            key: _secret_status_dict(metadata, usernames)
            for key, metadata in secret_metadata.items()
        },
        "secrets_encryption_configured": secrets_encryption_configured(),
    }


def _apply_optional_setting(
    conn: DbConn,
    *,
    key: str,
    value: str | None,
    changed: list[str],
    validator: Callable[[str], str] | None = None,
) -> None:
    if value is None:
        return
    normalized = value.strip()
    if validator is not None:
        normalized = validator(normalized)
    if normalized:
        _upsert_setting(conn, key, normalized)
    else:
        _delete_setting(conn, key)
    changed.append(key)


def apply_provider_settings_update(
    conn: DbConn,
    *,
    settings: Mapping[str, str | None],
    secret_values: Mapping[str, str | None],
    clear_secret_keys: Iterable[str],
    actor_user_id: int | None,
) -> tuple[dict[str, object], ProviderSettingsChanges]:
    changed_settings: list[str] = []
    set_secrets: list[str] = []
    cleared_secrets: list[str] = []

    _apply_optional_setting(
        conn,
        key=AI_PROVIDER_SETTING,
        value=settings.get(AI_PROVIDER_SETTING),
        changed=changed_settings,
        validator=validate_ai_provider,
    )
    _apply_optional_setting(
        conn,
        key=OPENAI_MODEL_SETTING,
        value=settings.get(OPENAI_MODEL_SETTING),
        changed=changed_settings,
    )
    _apply_optional_setting(
        conn,
        key=OPENAI_FAST_MODEL_SETTING,
        value=settings.get(OPENAI_FAST_MODEL_SETTING),
        changed=changed_settings,
    )
    _apply_optional_setting(
        conn,
        key=ANTHROPIC_MODEL_SETTING,
        value=settings.get(ANTHROPIC_MODEL_SETTING),
        changed=changed_settings,
    )

    for key, value in secret_values.items():
        if value is None:
            continue
        if not value.strip():
            raise ValueError(f"{key} must not be empty")
        set_database_secret(conn, key, value, updated_by_user_id=actor_user_id)
        set_secrets.append(key)

    for key in clear_secret_keys:
        clear_database_secret(conn, key)
        cleared_secrets.append(key)

    return (
        get_provider_settings_summary(conn),
        ProviderSettingsChanges(
            changed_settings=tuple(changed_settings),
            set_secrets=tuple(set_secrets),
            cleared_secrets=tuple(cleared_secrets),
        ),
    )


def configuration_error_response_detail(exc: ConfigurationError) -> str:
    message = str(exc).strip()
    return message or "Platform secret encryption is not configured"
