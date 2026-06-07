"""Encrypted platform secret storage helpers."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from cryptography.fernet import Fernet, InvalidToken

from gardenops.db import DbConn

OPENAI_API_KEY = "openai_api_key"
ANTHROPIC_API_KEY = "anthropic_api_key"
PLANTNET_API_KEY = "plantnet_api_key"
SHADEMAP_API_KEY = "shademap_api_key"

MANAGED_SECRET_KEYS = frozenset(
    {
        OPENAI_API_KEY,
        ANTHROPIC_API_KEY,
        PLANTNET_API_KEY,
        SHADEMAP_API_KEY,
    },
)

_APP_SECRETS_ENCRYPTION_KEY_ENV = "APP_SECRETS_ENCRYPTION_KEY"


class ConfigurationError(RuntimeError):
    """Raised when encrypted platform secrets cannot be safely used."""


class UnknownSecretKeyError(ValueError):
    """Raised when code attempts to write an unmanaged secret key."""


@dataclass(frozen=True)
class SecretMetadata:
    key: str
    configured: bool
    source: Literal["db", "env", "none"]
    last4: str | None
    updated_at_ms: int | None
    updated_by_user_id: int | None


def _validate_secret_key(key: str) -> None:
    if key not in MANAGED_SECRET_KEYS:
        raise UnknownSecretKeyError(f"Unknown platform secret key: {key}")


def _metadata_none(key: str) -> SecretMetadata:
    return SecretMetadata(
        key=key,
        configured=False,
        source="none",
        last4=None,
        updated_at_ms=None,
        updated_by_user_id=None,
    )


def _load_fernet() -> Fernet:
    raw_key = os.environ.get(_APP_SECRETS_ENCRYPTION_KEY_ENV, "").strip()
    if not raw_key:
        raise ConfigurationError(
            f"{_APP_SECRETS_ENCRYPTION_KEY_ENV} is required to use encrypted platform secrets",
        )
    try:
        return Fernet(raw_key.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            f"{_APP_SECRETS_ENCRYPTION_KEY_ENV} must be a valid Fernet key",
        ) from exc


def secrets_encryption_configured() -> bool:
    """Return whether the platform secret encryption key is present and valid."""

    try:
        _load_fernet()
    except ConfigurationError:
        return False
    return True


def _last4_non_whitespace(value: str) -> str:
    compact = "".join(value.split())
    return compact[-4:]


def _metadata_from_row(row: Mapping[str, Any], key: str | None = None) -> SecretMetadata:
    updated_by_user_id = row.get("updated_by_user_id")
    updated_at_ms = row.get("updated_at_ms")
    last4 = row.get("value_last4")
    row_key = key if key is not None else row.get("key")
    return SecretMetadata(
        key=str(row_key),
        configured=True,
        source="db",
        last4=str(last4) if last4 is not None else None,
        updated_at_ms=int(updated_at_ms) if updated_at_ms is not None else None,
        updated_by_user_id=int(updated_by_user_id) if updated_by_user_id is not None else None,
    )


def get_database_secret(conn: DbConn, key: str) -> str | None:
    """Return the decrypted database secret value for key, or None when unset."""

    _validate_secret_key(key)
    row = conn.execute(
        """
        SELECT encrypted_value
        FROM public.app_secrets
        WHERE key = %s
        """,
        (key,),
    ).fetchone()
    if row is None:
        return None

    encrypted_value = row["encrypted_value"]
    if isinstance(encrypted_value, memoryview):
        encrypted_bytes = encrypted_value.tobytes()
    elif isinstance(encrypted_value, bytes):
        encrypted_bytes = encrypted_value
    else:
        encrypted_bytes = bytes(encrypted_value)

    try:
        decrypted = _load_fernet().decrypt(encrypted_bytes)
        return decrypted.decode("utf-8")
    except InvalidToken as exc:
        raise ConfigurationError("Database platform secret could not be decrypted") from exc
    except UnicodeDecodeError as exc:
        raise ConfigurationError("Database platform secret is not valid UTF-8") from exc


def set_database_secret(
    conn: DbConn,
    key: str,
    value: str,
    updated_by_user_id: int | None = None,
) -> SecretMetadata:
    """Encrypt and upsert a database secret value, returning safe metadata."""

    _validate_secret_key(key)
    if not value.strip():
        raise ValueError("Secret value must not be empty; use clear_database_secret instead")

    encrypted_value = _load_fernet().encrypt(value.encode("utf-8"))
    value_last4 = _last4_non_whitespace(value)
    row = conn.execute(
        """
        INSERT INTO public.app_secrets (
            key,
            encrypted_value,
            value_last4,
            updated_by_user_id
        )
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (key) DO UPDATE
        SET encrypted_value = EXCLUDED.encrypted_value,
            encryption_key_id = 'app',
            value_last4 = EXCLUDED.value_last4,
            updated_at_ms = ((extract(epoch FROM now()) * 1000)::bigint),
            updated_by_user_id = EXCLUDED.updated_by_user_id
        RETURNING key, value_last4, updated_at_ms, updated_by_user_id
        """,
        (key, encrypted_value, value_last4, updated_by_user_id),
    ).fetchone()
    assert row is not None
    return _metadata_from_row(row, key)


def clear_database_secret(conn: DbConn, key: str) -> None:
    """Delete a database-managed secret row."""

    _validate_secret_key(key)
    _load_fernet()
    conn.execute(
        """
        DELETE FROM public.app_secrets
        WHERE key = %s
        """,
        (key,),
    )


def database_secret_metadata(conn: DbConn, key: str) -> SecretMetadata:
    """Return database secret metadata without decrypting or exposing plaintext."""

    _validate_secret_key(key)
    row = conn.execute(
        """
        SELECT key, value_last4, updated_at_ms, updated_by_user_id
        FROM public.app_secrets
        WHERE key = %s
        """,
        (key,),
    ).fetchone()
    if row is None:
        return _metadata_none(key)
    return _metadata_from_row(row, key)


def secret_metadata_with_env_fallback(
    conn: DbConn,
    key: str,
    env_names: Iterable[str],
) -> SecretMetadata:
    """Return database metadata first, then safe env fallback metadata."""

    metadata = database_secret_metadata(conn, key)
    if metadata.configured:
        return metadata

    for env_name in env_names:
        env_value = os.environ.get(env_name, "")
        if env_value.strip():
            return SecretMetadata(
                key=key,
                configured=True,
                source="env",
                last4=_last4_non_whitespace(env_value),
                updated_at_ms=None,
                updated_by_user_id=None,
            )
    return metadata
