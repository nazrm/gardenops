from __future__ import annotations

import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from gardenops.platform_secrets import (
    ConfigurationError,
    SecretMetadata,
    UnknownSecretKeyError,
    clear_database_secret,
    database_secret_metadata,
    get_database_secret,
    secret_metadata_with_env_fallback,
    set_database_secret,
)

ROOT = Path(__file__).resolve().parents[1]


class _Cursor:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, object] | None:
        return self._row


class _FakeConn:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}
        self.clock_ms = 1_000

    def execute(self, query: str, params: Sequence[object] | None = None) -> _Cursor:
        normalized = " ".join(query.lower().split())
        params = params or ()
        if normalized.startswith("select encrypted_value"):
            key = str(params[0])
            return _Cursor(self.rows.get(key))
        if normalized.startswith("select key"):
            key = str(params[0])
            return _Cursor(self.rows.get(key))
        if normalized.startswith("insert into public.app_secrets"):
            key = str(params[0])
            encrypted_value = params[1]
            value_last4 = params[2]
            updated_by_user_id = params[3] if len(params) > 3 else None
            previous = self.rows.get(key)
            created_at_ms = previous["created_at_ms"] if previous else self.clock_ms
            self.clock_ms += 1
            self.rows[key] = {
                "key": key,
                "encrypted_value": encrypted_value,
                "encryption_key_id": "app",
                "value_last4": value_last4,
                "created_at_ms": created_at_ms,
                "updated_at_ms": self.clock_ms,
                "updated_by_user_id": updated_by_user_id,
            }
            return _Cursor(self.rows[key])
        if normalized.startswith("delete from public.app_secrets"):
            self.rows.pop(str(params[0]), None)
            return _Cursor()
        raise AssertionError(f"Unexpected query: {query}")


class PlatformSecretsTests(unittest.TestCase):
    def test_set_and_get_database_secret_round_trips_encrypted_value(self) -> None:
        conn = _FakeConn()
        fernet_key = Fernet.generate_key().decode()

        with patch.dict("os.environ", {"APP_SECRETS_ENCRYPTION_KEY": fernet_key}, clear=False):
            first = set_database_secret(conn, "openai_api_key", "openai test key 1234")
            second = set_database_secret(
                conn,
                "openai_api_key",
                "updated test key 5678",
                updated_by_user_id=42,
            )
            decrypted = get_database_secret(conn, "openai_api_key")

        encrypted_value = conn.rows["openai_api_key"]["encrypted_value"]
        self.assertIsInstance(encrypted_value, bytes)
        self.assertNotIn(b"updated test key 5678", encrypted_value)
        self.assertEqual(decrypted, "updated test key 5678")
        self.assertEqual(first.last4, "1234")
        self.assertEqual(second.last4, "5678")
        self.assertEqual(second.source, "db")
        self.assertEqual(second.updated_by_user_id, 42)

    def test_metadata_never_contains_plaintext(self) -> None:
        conn = _FakeConn()
        fernet_key = Fernet.generate_key().decode()
        plaintext = "metadata secret value ABCD"

        with patch.dict("os.environ", {"APP_SECRETS_ENCRYPTION_KEY": fernet_key}, clear=False):
            set_metadata = set_database_secret(conn, "anthropic_api_key", plaintext)
            lookup_metadata = database_secret_metadata(conn, "anthropic_api_key")

        self.assertEqual(set_metadata, lookup_metadata)
        self.assertIsInstance(lookup_metadata, SecretMetadata)
        self.assertEqual(lookup_metadata.key, "anthropic_api_key")
        self.assertTrue(lookup_metadata.configured)
        self.assertEqual(lookup_metadata.last4, "ABCD")
        self.assertNotIn(plaintext, repr(lookup_metadata))
        self.assertNotIn("metadata secret value", repr(lookup_metadata))

    def test_clear_database_secret_removes_secret(self) -> None:
        conn = _FakeConn()
        fernet_key = Fernet.generate_key().decode()

        with patch.dict("os.environ", {"APP_SECRETS_ENCRYPTION_KEY": fernet_key}, clear=False):
            set_database_secret(conn, "plantnet_api_key", "plantnet test key 0000")
            clear_database_secret(conn, "plantnet_api_key")
            metadata = database_secret_metadata(conn, "plantnet_api_key")
            decrypted = get_database_secret(conn, "plantnet_api_key")

        self.assertIsNone(decrypted)
        self.assertEqual(metadata.source, "none")
        self.assertFalse(metadata.configured)
        self.assertIsNone(metadata.last4)

    def test_write_requires_encryption_key(self) -> None:
        conn = _FakeConn()

        with patch.dict("os.environ", {"APP_SECRETS_ENCRYPTION_KEY": ""}, clear=False):
            with self.assertRaises(ConfigurationError):
                set_database_secret(conn, "shademap_api_key", "shademap test key 9999")

        with patch.dict(
            "os.environ",
            {"APP_SECRETS_ENCRYPTION_KEY": "not-a-fernet-key"},
            clear=False,
        ):
            with self.assertRaises(ConfigurationError):
                set_database_secret(conn, "shademap_api_key", "shademap test key 9999")

        self.assertEqual(conn.rows, {})

    def test_unknown_secret_key_is_rejected(self) -> None:
        conn = _FakeConn()
        fernet_key = Fernet.generate_key().decode()

        with patch.dict("os.environ", {"APP_SECRETS_ENCRYPTION_KEY": fernet_key}, clear=False):
            with self.assertRaises(UnknownSecretKeyError):
                set_database_secret(conn, "unexpected_api_key", "test key 1234")
            with self.assertRaises(ValueError):
                set_database_secret(conn, "openai_api_key", "   ")

        self.assertEqual(conn.rows, {})

    def test_env_fallback_metadata_uses_env_without_exposing_value(self) -> None:
        conn = _FakeConn()
        env_value = "env fallback secret WXYZ"

        with patch.dict("os.environ", {"OPENAI_API_KEY": env_value}, clear=False):
            metadata = secret_metadata_with_env_fallback(
                conn,
                "openai_api_key",
                ("OPENAI_API_KEY", "OPENAI_FALLBACK"),
            )

        self.assertEqual(
            metadata,
            SecretMetadata(
                key="openai_api_key",
                configured=True,
                source="env",
                last4="WXYZ",
                updated_at_ms=None,
                updated_by_user_id=None,
            ),
        )
        self.assertNotIn(env_value, repr(metadata))
        self.assertNotIn("env fallback secret", repr(metadata))

    def test_platform_secret_migration_uses_bigint_fk_with_stable_name(self) -> None:
        migration_sql = (ROOT / "migrations" / "0014_platform_provider_secrets.sql").read_text(
            encoding="utf-8"
        )

        self.assertIn("updated_by_user_id bigint", migration_sql)
        self.assertIn("CONSTRAINT app_secrets_updated_by_user_id_fkey", migration_sql)
        self.assertIn(
            "FOREIGN KEY (updated_by_user_id) REFERENCES public.auth_users(id) ON DELETE SET NULL",
            migration_sql,
        )
        self.assertNotIn("updated_by_user_id uuid", migration_sql)


if __name__ == "__main__":
    unittest.main()
