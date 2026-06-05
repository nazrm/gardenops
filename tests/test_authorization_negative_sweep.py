from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest

import gardenops.db as db
from gardenops.routers import auth, media, saved_views, shademap, statistics
from gardenops.security import AuthContext
from tests.base import BaseApiTest


class TestAuthorizationNegativeSweep(BaseApiTest):
    def _create_foreign_garden(self, slug: str, name: str) -> int:
        conn = db.get_db()
        try:
            cursor = conn.execute(
                "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                (slug, name),
            )
            conn.commit()
            return cursor.fetchone()["id"]
        finally:
            db.return_db(conn)

    @staticmethod
    def _request(
        path: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
    ) -> StarletteRequest:
        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [
                (key.lower().encode("latin-1"), value.encode("latin-1"))
                for key, value in (headers or {}).items()
            ],
            "client": ("127.0.0.1", 5000),
        }
        return StarletteRequest(scope)

    def _user_context(self, user: dict) -> AuthContext:
        default_garden_id = self._get_default_garden_id()
        role = str(user["role"])
        return AuthContext(
            user_id=int(user["id"]),
            username=str(user["username"]),
            role=role,
            auth_type="session",
            garden_id=default_garden_id,
            garden_role=role,
            subscription_tier="pro",
        )

    def _request_with_context(
        self,
        path: str,
        *,
        context: AuthContext,
        method: str = "GET",
        headers: dict[str, str] | None = None,
    ) -> StarletteRequest:
        request = self._request(path, method=method, headers=headers)
        request.state.auth_context = context
        return request

    def test_garden_scoped_routes_reject_nonmember_garden_selection(self) -> None:
        user = self._create_test_user("authz_scope_editor", "scopepass", "editor")
        context = self._user_context(user)
        foreign_garden_id = self._create_foreign_garden(
            "authz-foreign-garden",
            "Authorization Foreign Garden",
        )
        foreign_headers = {"x-garden-id": str(foreign_garden_id)}
        conn = db.get_db()
        try:
            cases = [
                (
                    "/api/saved-views",
                    lambda request: saved_views.list_saved_views(request, conn),
                ),
                (
                    "/api/statistics/actions",
                    lambda request: statistics.get_statistics_actions(conn, request),
                ),
                (
                    "/api/exports/backup",
                    lambda request: statistics.export_backup(request, conn),
                ),
                (
                    "/api/shademap/state",
                    lambda request: shademap.get_shademap_state_api(request, conn),
                ),
                (
                    "/api/media",
                    lambda request: media.list_media_assets(
                        request,
                        conn,
                        target_type="plant",
                        target_id="PLT-TEST",
                    ),
                ),
            ]

            for path, call in cases:
                with self.subTest(route=path):
                    request = self._request_with_context(
                        path,
                        context=context,
                        headers=foreign_headers,
                    )
                    with self.assertRaises(HTTPException) as exc:
                        call(request)
                    self.assertEqual(exc.exception.status_code, 404)
                    self.assertEqual(exc.exception.detail, "Garden not found")
        finally:
            db.return_db(conn)

    def test_media_cover_admin_routes_reject_non_platform_admin_role(self) -> None:
        user = self._create_test_user("authz_media_editor", "mediapass", "editor")
        context = self._user_context(user)
        conn = db.get_db()
        try:
            request = self._request_with_context(
                "/api/media/plants/missing-covers",
                context=context,
            )
            with self.assertRaises(HTTPException) as report_exc:
                media.list_missing_plant_covers(request, conn, limit=10)
            self.assertEqual(report_exc.exception.status_code, 403)
            self.assertEqual(report_exc.exception.detail, "Platform admin required")

            request = self._request_with_context(
                "/api/media/plants/populate-missing-covers",
                context=context,
                method="POST",
            )
            with self.assertRaises(HTTPException) as populate_exc:
                media.populate_missing_plant_covers(
                    media.PopulateMissingPlantCoversBody(max_plants=1),
                    request,
                    conn,
                )
            self.assertEqual(populate_exc.exception.status_code, 403)
            self.assertEqual(populate_exc.exception.detail, "Platform admin required")
        finally:
            db.return_db(conn)

    def test_admin_operational_routes_reject_non_admin_role(self) -> None:
        user = self._create_test_user("authz_ops_editor", "opspass", "editor")
        context = self._user_context(user)
        conn = db.get_db()
        try:
            cases = [
                (
                    "/api/auth/audit-events",
                    "GET",
                    lambda request: auth.auth_audit_events(request, conn),
                ),
                (
                    "/api/auth/security-metrics",
                    "GET",
                    auth.auth_security_metrics,
                ),
                (
                    "/api/auth/security-alerts",
                    "GET",
                    auth.auth_security_alerts,
                ),
                (
                    "/api/auth/sessions",
                    "GET",
                    lambda request: auth.auth_sessions(request, conn),
                ),
                (
                    "/api/auth/emergency-read-only",
                    "GET",
                    lambda request: auth.auth_get_emergency_read_only(request, conn),
                ),
                (
                    "/api/auth/revoke-user-sessions",
                    "POST",
                    lambda request: auth.auth_revoke_user_sessions(
                        auth.RevokeUserSessionsBody(
                            username="nonexistent",
                            action_reason="authorization regression check",
                        ),
                        request,
                        conn,
                    ),
                ),
                (
                    "/api/auth/revoke-all-sessions",
                    "POST",
                    lambda request: auth.auth_revoke_all_sessions(
                        auth.RevokeAllSessionsBody(
                            action_reason="authorization regression check",
                        ),
                        request,
                        conn,
                    ),
                ),
            ]

            for path, method, call in cases:
                with self.subTest(route=path):
                    request = self._request_with_context(
                        path,
                        context=context,
                        method=method,
                    )
                    with self.assertRaises(HTTPException) as exc:
                        call(request)
                    self.assertEqual(exc.exception.status_code, 403)
                    self.assertEqual(exc.exception.detail, "Admin role required")
        finally:
            db.return_db(conn)
