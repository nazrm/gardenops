#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${GARDENOPS_TEST_POSTGRES_URL:-}" ]]; then
  echo "GARDENOPS_TEST_POSTGRES_URL must point at a disposable test database." >&2
  exit 2
fi

export APP_ENV="${APP_ENV:-test}"
export AUTH_PASSWORD_HASH_FAST_FOR_TESTS="${AUTH_PASSWORD_HASH_FAST_FOR_TESTS:-true}"

uv run pytest \
  tests/test_test_environment.py \
  tests/test_db_unit.py \
  tests/test_feature_gates.py \
  tests/test_admin_edge_policy.py \
  tests/test_auth_endpoints.py::TestAuthStatus \
  tests/test_auth_endpoints.py::TestAuthLogout \
  tests/test_plots.py::TestPlots::test_version_endpoint_is_public_and_returns_dynamic_verbose_version \
  tests/test_plots.py::TestPlots::test_list_plots \
  tests/test_plants.py::TestPlants::test_search_plants_by_name \
  -q --tb=short
