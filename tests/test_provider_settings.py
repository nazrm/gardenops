from __future__ import annotations

import os
import unittest
from collections.abc import Sequence
from unittest.mock import patch

from cryptography.fernet import Fernet

from gardenops.platform_secrets import ConfigurationError
from gardenops.provider_settings import (
    AI_PROVIDER_SETTING,
    ANTHROPIC_MODEL_SETTING,
    apply_provider_settings_update,
    get_ai_runtime_config,
    get_provider_settings_summary,
)
from tests.base import BaseApiTest


class _Cursor:
    def __init__(
        self,
        row: dict[str, object] | None = None,
        rows: list[dict[str, object]] | None = None,
    ) -> None:
        self._row = row
        self._rows = rows or ([] if row is None else [row])

    def fetchone(self) -> dict[str, object] | None:
        return self._row

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows


class _FakeConn:
    def __init__(self) -> None:
        self.app_settings: dict[str, str] = {}
        self.app_secrets: dict[str, dict[str, object]] = {}
        self.users = {7: "platform-admin"}
        self.clock_ms = 10_000

    def execute(self, query: str, params: Sequence[object] | None = None) -> _Cursor:
        normalized = " ".join(query.lower().split())
        params = params or ()
        if normalized.startswith("select value from app_settings"):
            key = str(params[0])
            value = self.app_settings.get(key)
            return _Cursor({"value": value} if value is not None else None)
        if normalized.startswith("insert into app_settings"):
            self.app_settings[str(params[0])] = str(params[1])
            return _Cursor()
        if normalized.startswith("delete from app_settings"):
            self.app_settings.pop(str(params[0]), None)
            return _Cursor()
        if normalized.startswith("select encrypted_value"):
            return _Cursor(self.app_secrets.get(str(params[0])))
        if normalized.startswith("select key, value_last4"):
            return _Cursor(self.app_secrets.get(str(params[0])))
        if normalized.startswith("insert into public.app_secrets"):
            key = str(params[0])
            previous = self.app_secrets.get(key)
            self.clock_ms += 1
            self.app_secrets[key] = {
                "key": key,
                "encrypted_value": params[1],
                "value_last4": params[2],
                "created_at_ms": previous["created_at_ms"] if previous else self.clock_ms,
                "updated_at_ms": self.clock_ms,
                "updated_by_user_id": params[3],
            }
            return _Cursor(self.app_secrets[key])
        if normalized.startswith("delete from public.app_secrets"):
            self.app_secrets.pop(str(params[0]), None)
            return _Cursor()
        if normalized.startswith("select id, username from auth_users"):
            ids = {int(value) for value in params[0]}
            return _Cursor(
                rows=[
                    {"id": user_id, "username": username}
                    for user_id, username in self.users.items()
                    if user_id in ids
                ]
            )
        raise AssertionError(f"Unexpected query: {query}")


class ProviderSettingsTests(unittest.TestCase):
    def test_update_stores_openai_and_anthropic_keys_without_plaintext_summary(self) -> None:
        conn = _FakeConn()
        fernet_key = Fernet.generate_key().decode()

        with patch.dict("os.environ", {"APP_SECRETS_ENCRYPTION_KEY": fernet_key}, clear=False):
            summary, changes = apply_provider_settings_update(
                conn,
                settings={
                    AI_PROVIDER_SETTING: "anthropic",
                    ANTHROPIC_MODEL_SETTING: "claude-test",
                },
                secret_values={
                    "openai_api_key": "openai secret 1234",
                    "anthropic_api_key": "anthropic secret 5678",
                },
                clear_secret_keys=(),
                actor_user_id=7,
            )

        self.assertEqual(conn.app_settings[AI_PROVIDER_SETTING], "anthropic")
        self.assertEqual(conn.app_settings[ANTHROPIC_MODEL_SETTING], "claude-test")
        self.assertEqual(changes.set_secrets, ("openai_api_key", "anthropic_api_key"))
        secrets = summary["secrets"]
        assert isinstance(secrets, dict)
        openai = secrets["openai_api_key"]
        anthropic = secrets["anthropic_api_key"]
        assert isinstance(openai, dict)
        assert isinstance(anthropic, dict)
        self.assertEqual(openai["source"], "db")
        self.assertEqual(openai["last4"], "1234")
        self.assertEqual(openai["updated_by_username"], "platform-admin")
        self.assertEqual(anthropic["last4"], "5678")
        self.assertNotIn("openai secret", repr(summary))
        self.assertNotIn("anthropic secret", repr(summary))

    def test_runtime_config_prefers_database_secret_over_env(self) -> None:
        conn = _FakeConn()
        fernet_key = Fernet.generate_key().decode()

        with patch.dict(
            "os.environ",
            {
                "APP_SECRETS_ENCRYPTION_KEY": fernet_key,
                "OPENAI_API_KEY": "env-openai-key",
            },
            clear=False,
        ):
            apply_provider_settings_update(
                conn,
                settings={AI_PROVIDER_SETTING: "openai"},
                secret_values={"openai_api_key": "database-openai-key"},
                clear_secret_keys=(),
                actor_user_id=7,
            )
            config = get_ai_runtime_config(conn)

        self.assertEqual(config.provider, "openai")
        self.assertEqual(config.openai_api_key, "database-openai-key")

    def test_runtime_config_does_not_swallow_database_secret_decrypt_errors(self) -> None:
        conn = _FakeConn()
        conn.app_settings[AI_PROVIDER_SETTING] = "openai"
        conn.app_secrets["openai_api_key"] = {
            "key": "openai_api_key",
            "encrypted_value": b"not-a-fernet-token",
            "value_last4": "fake",
            "updated_at_ms": 1,
            "updated_by_user_id": 7,
        }
        fernet_key = Fernet.generate_key().decode()

        with (
            patch.dict(
                "os.environ",
                {
                    "APP_SECRETS_ENCRYPTION_KEY": fernet_key,
                    "AI_PROVIDER": "openai",
                    "OPENAI_API_KEY": "env-openai-key",
                },
                clear=False,
            ),
            patch("gardenops.provider_settings.get_db", return_value=conn),
            patch("gardenops.provider_settings.return_db"),
        ):
            with self.assertRaises(ConfigurationError):
                get_ai_runtime_config()

    def test_runtime_config_does_not_decrypt_secret_when_database_disables_ai(self) -> None:
        conn = _FakeConn()
        conn.app_settings[AI_PROVIDER_SETTING] = "disabled"
        conn.app_secrets["openai_api_key"] = {
            "key": "openai_api_key",
            "encrypted_value": b"not-a-fernet-token",
            "value_last4": "fake",
            "updated_at_ms": 1,
            "updated_by_user_id": 7,
        }
        fernet_key = Fernet.generate_key().decode()

        with patch.dict(
            "os.environ",
            {
                "APP_SECRETS_ENCRYPTION_KEY": fernet_key,
                "AI_PROVIDER": "openai",
                "OPENAI_API_KEY": "env-openai-key",
            },
            clear=False,
        ):
            config = get_ai_runtime_config(conn)

        self.assertEqual(config.provider, "disabled")
        self.assertIsNone(config.openai_api_key)
        self.assertIsNone(config.anthropic_api_key)

    def test_update_rejects_empty_secret_value(self) -> None:
        conn = _FakeConn()
        fernet_key = Fernet.generate_key().decode()

        with patch.dict("os.environ", {"APP_SECRETS_ENCRYPTION_KEY": fernet_key}, clear=False):
            with self.assertRaises(ValueError):
                apply_provider_settings_update(
                    conn,
                    settings={},
                    secret_values={"openai_api_key": "   "},
                    clear_secret_keys=(),
                    actor_user_id=7,
                )

    def test_secret_rotation_and_delete_return_only_redacted_metadata(self) -> None:
        conn = _FakeConn()
        fernet_key = Fernet.generate_key().decode()
        environment = {
            "APP_SECRETS_ENCRYPTION_KEY": fernet_key,
            "OPENAI_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
            "PLANTNET_API_KEY": "",
            "SHADEMAP": "",
            "SHADEMAP_API_KEY": "",
            "SHADEMAP_KEY": "",
        }

        with patch.dict("os.environ", environment, clear=False):
            first_summary, _ = apply_provider_settings_update(
                conn,
                settings={},
                secret_values={
                    "openai_api_key": "first-secret-1111"
                },  # push-sanitizer: allow SECRET_ASSIGNMENT - fixed test fixture
                clear_secret_keys=(),
                actor_user_id=7,
            )
            rotated_summary, rotated_changes = apply_provider_settings_update(
                conn,
                settings={},
                secret_values={
                    "openai_api_key": "rotated-secret-2222"
                },  # push-sanitizer: allow SECRET_ASSIGNMENT - fixed test fixture
                clear_secret_keys=(),
                actor_user_id=7,
            )
            deleted_summary, deleted_changes = apply_provider_settings_update(
                conn,
                settings={},
                secret_values={},
                clear_secret_keys=("openai_api_key",),
                actor_user_id=7,
            )

        first = first_summary["secrets"]["openai_api_key"]
        rotated = rotated_summary["secrets"]["openai_api_key"]
        deleted = deleted_summary["secrets"]["openai_api_key"]
        self.assertEqual(first["last4"], "1111")
        self.assertEqual(rotated["last4"], "2222")
        self.assertEqual(rotated_changes.set_secrets, ("openai_api_key",))
        self.assertFalse(deleted["configured"])
        self.assertEqual(deleted["source"], "none")
        self.assertIsNone(deleted["last4"])
        self.assertEqual(deleted_changes.cleared_secrets, ("openai_api_key",))
        combined = repr((first_summary, rotated_summary, deleted_summary))
        self.assertNotIn("first-secret", combined)
        self.assertNotIn("rotated-secret", combined)

    def test_summary_reports_env_fallback_without_plaintext(self) -> None:
        conn = _FakeConn()

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "env-ant-key-9999"}, clear=False):
            summary = get_provider_settings_summary(conn)

        secrets = summary["secrets"]
        assert isinstance(secrets, dict)
        anthropic = secrets["anthropic_api_key"]
        assert isinstance(anthropic, dict)
        self.assertEqual(anthropic["source"], "env")
        self.assertEqual(anthropic["last4"], "9999")
        self.assertNotIn("env-ant-key", repr(summary))


class ProviderSettingsAuthorizationTests(BaseApiTest):
    def test_editor_and_viewer_cannot_read_or_mutate_provider_secrets(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        try:
            for role in ("editor", "viewer"):
                username = f"provider_{role}"
                password = f"provider-{role}-pass"
                self._create_test_user(username, password, role)
                client, headers = self._authenticated_client(username, password)

                read_response = client.get("/api/admin/provider-settings", headers=headers)
                write_response = client.put(
                    "/api/admin/provider-settings",
                    headers=headers,
                    json={
                        "openai_api_key": "must-not-be-stored",
                        "action_reason": f"deny-{role}",
                    },
                )

                self.assertEqual(read_response.status_code, 403)
                self.assertEqual(write_response.status_code, 403)
                self.assertNotIn("must-not-be-stored", write_response.text)
        finally:
            os.environ["AUTH_REQUIRED"] = "false"


if __name__ == "__main__":
    unittest.main()
