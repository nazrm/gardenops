"""Shared pytest configuration and environment setup.

Module-level env vars are set here before any test module imports
the app, since CORS middleware and other config lock in at import time.
"""

import os
import tempfile

_TEST_LOGS = tempfile.TemporaryDirectory(prefix="gardenops-test-logs-")

os.environ["APP_ENV"] = "test"
os.environ["AUTH_REQUIRED"] = "false"
os.environ["RATE_LIMIT_BACKEND"] = "memory"
os.environ["INTERNET_EXPOSED"] = "false"
os.environ["ALLOWED_HOSTS"] = "localhost,127.0.0.1,[::1],::1,testserver,testclient"
os.environ["GARDENOPS_LOGS_DIR"] = _TEST_LOGS.name
os.environ["CORS_ALLOW_ORIGINS"] = "http://localhost:5173"
os.environ["AUTH_PASSWORD_MIN_LENGTH"] = "8"
os.environ["AUTH_PASSWORD_CHECK_HIBP"] = "false"
os.environ["AUTH_PASSWORD_REQUIRE_LOWER"] = "false"
os.environ["AUTH_PASSWORD_REQUIRE_UPPER"] = "false"
os.environ["AUTH_PASSWORD_REQUIRE_DIGIT"] = "false"
os.environ["AUTH_PASSWORD_REQUIRE_SYMBOL"] = "false"
os.environ["AUTH_PASSWORD_DISALLOW_USERNAME"] = "false"
os.environ["AUTH_PASSWORD_HASH_FAST_FOR_TESTS"] = "true"
# Test runs must not ship warnings or failures to the live Taillight stream.
os.environ["TAILLIGHT_URL"] = ""
os.environ["TAILLIGHT_API_KEY"] = ""

import gardenops.db as db  # noqa: E402


def pytest_configure(config):
    """Run migrations once per test session."""
    url = os.environ.get("GARDENOPS_TEST_POSTGRES_URL")
    if not url:
        raise RuntimeError(
            "GARDENOPS_TEST_POSTGRES_URL must be set for tests. "
            "Tests TRUNCATE all tables — never point this at the production database. "
            "Create a dedicated test database: CREATE DATABASE gardenops_test OWNER gardenops;"
        )
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("APP_ENV", "test")
    db.run_migrations()


def pytest_unconfigure(config):
    """Close the shared Postgres pool before interpreter shutdown."""
    db.close_pool()
    _TEST_LOGS.cleanup()
