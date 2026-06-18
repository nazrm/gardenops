from __future__ import annotations

import re
import unittest
from pathlib import Path

from gardenops.admin_edge_policy import (
    ADMIN_EDGE_GENERIC_ROUTE_EXCEPTIONS,
    ADMIN_EDGE_LOCATION_RULES,
    ADMIN_EDGE_RATE_LIMIT_ZONES,
    ADMIN_EDGE_ROUTE_MANIFEST,
    admin_edge_bucket_for_path,
    materialize_path_template,
    nginx_location_header,
)


class TestAdminEdgePolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.nginx_config = (
            Path(__file__).resolve().parents[1] / "deploy" / "nginx.production.example.conf"
        ).read_text(encoding="utf-8")

    def _location_block(self, header: str) -> str:
        pattern = r"\s+".join(re.escape(tok) for tok in header.split())
        match = re.search(pattern, self.nginx_config)
        self.assertIsNotNone(match, f"Missing nginx location header: {header}")
        start = match.start()
        brace = self.nginx_config.find("{", start)
        depth = 1
        i = brace + 1
        while i < len(self.nginx_config) and depth > 0:
            ch = self.nginx_config[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        self.assertEqual(depth, 0, f"Unbalanced braces for: {header}")
        return self.nginx_config[start:i]

    def _api_route_keys(self) -> set[tuple[str, str]]:
        from gardenops.main import app

        route_keys: set[tuple[str, str]] = set()
        for route in app.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", None)
            if not path.startswith("/api/") or not methods:
                continue
            for method in methods:
                if method not in {"HEAD", "OPTIONS"}:
                    route_keys.add((method, path))
        return route_keys

    @staticmethod
    def _route_requires_admin_edge_decision(path: str) -> bool:
        exact_paths = {
            "/api/snapshots",
            "/api/plots/export",
            "/api/plots/import",
            "/api/auth/users",
            "/api/auth/user-invitations",
            "/api/auth/audit-events",
            "/api/auth/security-metrics",
            "/api/auth/security-alerts",
            "/api/auth/sessions",
            "/api/auth/revoke-user-sessions",
            "/api/auth/revoke-all-sessions",
            "/api/auth/emergency-read-only",
            "/api/gardens/{garden_id}",
        }
        prefixes = (
            "/api/admin/",
            "/api/snapshots/",
            "/api/auth/mfa/",
            "/api/auth/users/",
            "/api/auth/user-invitations/",
            "/api/gardens/{garden_id}/",
            "/api/media/plants/",
        )
        return path in exact_paths or any(path.startswith(prefix) for prefix in prefixes)

    def test_manifest_routes_resolve_to_expected_edge_bucket(self) -> None:
        mismatches: list[str] = []
        for route in ADMIN_EDGE_ROUTE_MANIFEST:
            concrete_path = materialize_path_template(route.path_template)
            actual_bucket = admin_edge_bucket_for_path(concrete_path)
            if actual_bucket != route.bucket:
                mismatches.append(
                    f"{route.method} {route.path_template}: expected "
                    f"{route.bucket}, got {actual_bucket}",
                )
        self.assertEqual([], mismatches)

    def test_route_surface_has_explicit_admin_edge_decisions(self) -> None:
        documented_routes = {
            (route.method, route.path_template) for route in ADMIN_EDGE_ROUTE_MANIFEST
        }
        documented_routes.update(
            (route.method, route.path_template) for route in ADMIN_EDGE_GENERIC_ROUTE_EXCEPTIONS
        )

        missing_decisions = [
            f"{method} {path}"
            for method, path in sorted(self._api_route_keys())
            if self._route_requires_admin_edge_decision(path)
            and (method, path) not in documented_routes
        ]
        self.assertEqual(
            [],
            missing_decisions,
            "Add each route to ADMIN_EDGE_ROUTE_MANIFEST or ADMIN_EDGE_GENERIC_ROUTE_EXCEPTIONS.",
        )

    def test_generic_route_exceptions_stay_on_standard_api_edge(self) -> None:
        for route in ADMIN_EDGE_GENERIC_ROUTE_EXCEPTIONS:
            with self.subTest(route=f"{route.method} {route.path_template}"):
                self.assertEqual("generic_api", route.decision)
                concrete_path = materialize_path_template(route.path_template)
                self.assertIsNone(admin_edge_bucket_for_path(concrete_path))

    def test_admin_edge_policy_does_not_capture_neighboring_paths(self) -> None:
        neighbors = (
            "/api/snapshots-metadata",
            "/api/auth/users-public",
            "/api/auth/user-invitations-extra",
            "/api/auth/audit-events-export",
            "/api/auth/security-metrics-export",
            "/api/auth/sessions-extra",
            "/api/auth/revoke-danger",
            "/api/auth/emergency-read-only-mode",
        )
        for path in neighbors:
            with self.subTest(path=path):
                self.assertIsNone(admin_edge_bucket_for_path(path))

    def test_production_nginx_template_tracks_admin_edge_policy(self) -> None:
        self.assertIn(
            "limit_req_zone $binary_remote_addr zone=gardenops_admin_read:10m rate=12r/m;",
            self.nginx_config,
        )
        self.assertIn(
            "limit_req_zone $binary_remote_addr zone=gardenops_admin_write:10m rate=6r/m;",
            self.nginx_config,
        )
        self.assertIn(
            "limit_req_zone $binary_remote_addr zone=gardenops_upload:10m rate=20r/m;",
            self.nginx_config,
        )

        self.assertIn("client_max_body_size 1m;", self.nginx_config)
        self.assertIn("upstream gardenops {", self.nginx_config)

        for rule in ADMIN_EDGE_LOCATION_RULES:
            header = nginx_location_header(rule)
            block = self._location_block(header)
            expected_zone = ADMIN_EDGE_RATE_LIMIT_ZONES[rule.bucket]
            with self.subTest(header=header):
                self.assertIn(f"limit_req zone={expected_zone}", block)
                self.assertIn("proxy_pass http://gardenops;", block)
                if rule.pattern == "/api/plots/import":
                    self.assertIn("client_max_body_size 8m;", block)
                else:
                    self.assertNotIn("client_max_body_size 8m;", block)

    def test_production_nginx_template_allows_larger_photo_and_upload_routes(self) -> None:
        for header in (
            "location = /api/ai/identify-plant {",
            "location = /api/ai/diagnose-plant {",
            "location = /api/media/upload {",
            "location = /api/plants/import-csv {",
        ):
            with self.subTest(header=header):
                block = self._location_block(header)
                self.assertIn("client_max_body_size 8m;", block)
                self.assertIn("proxy_pass http://gardenops;", block)

        generic_ai_block = self._location_block("location ^~ /api/ai/ {")
        self.assertNotIn("client_max_body_size 8m;", generic_ai_block)

        lidar_block = self._location_block("location ~ ^/api/gardens/[0-9]+/lidar$ {")
        self.assertIn("client_max_body_size 256m;", lidar_block)
        self.assertIn("limit_req zone=gardenops_upload", lidar_block)
        self.assertIn("proxy_pass http://gardenops;", lidar_block)

    def test_production_nginx_template_avoids_unbounded_admin_prefixes(self) -> None:
        forbidden_headers = (
            "location ^~ /api/snapshots {",
            "location ^~ /api/auth/users {",
            "location ^~ /api/auth/user-invitations {",
            "location ^~ /api/auth/audit-events {",
            "location ^~ /api/auth/security- {",
            "location ^~ /api/auth/sessions {",
            "location ^~ /api/auth/revoke- {",
            "location ^~ /api/auth/emergency-read-only {",
        )
        for header in forbidden_headers:
            with self.subTest(header=header):
                self.assertNotIn(header, self.nginx_config)

    def test_production_nginx_template_overwrites_spoofable_client_ip_headers(self) -> None:
        self.assertIn("proxy_set_header X-Real-IP $remote_addr;", self.nginx_config)
        self.assertIn("proxy_set_header X-Forwarded-For $remote_addr;", self.nginx_config)
        self.assertNotIn("$proxy_add_x_forwarded_for", self.nginx_config)

    def test_production_nginx_template_redacts_token_bearing_access_log_paths(self) -> None:
        self.assertIn("log_format gardenops_redacted", self.nginx_config)
        self.assertIn("/calendar/subscriptions/[redacted].ics", self.nginx_config)
        self.assertIn("$uri?[redacted]", self.nginx_config)
        self.assertIn(
            "access_log /var/log/nginx/gardenops-access.log gardenops_redacted;",
            self.nginx_config,
        )

    def test_public_templates_do_not_contain_private_host_literals(self) -> None:
        forbidden = (
            "Ha" + "geapp",
            "ha" + "geapp",
            "HA" + "GEAPP",
            "sko" + "hyllen",
            "lossless" + ".science",
        )
        for literal in forbidden:
            with self.subTest(literal=literal):
                self.assertNotIn(literal, self.nginx_config)


if __name__ == "__main__":
    unittest.main()
