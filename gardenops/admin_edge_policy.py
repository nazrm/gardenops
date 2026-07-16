from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

AdminEdgeBucket = Literal["admin_read", "admin_write"]
AdminEdgeDecision = Literal["admin_read", "admin_write", "generic_api"]
AdminEdgeMatchKind = Literal["exact", "prefix", "regex"]


@dataclass(frozen=True)
class AdminEdgeRoute:
    method: str
    path_template: str
    bucket: AdminEdgeBucket
    rationale: str


@dataclass(frozen=True)
class AdminEdgeLocationRule:
    match_kind: AdminEdgeMatchKind
    pattern: str
    bucket: AdminEdgeBucket
    rationale: str


@dataclass(frozen=True)
class AdminEdgeRouteDecision:
    method: str
    path_template: str
    decision: AdminEdgeDecision
    rationale: str


ADMIN_EDGE_RATE_LIMIT_ZONES: Final[dict[AdminEdgeBucket, str]] = {
    "admin_read": "gardenops_admin_read",
    "admin_write": "gardenops_admin_write",
}


# This is the launch-time admin-sensitive surface that must stay behind the
# stronger same-host edge policy on the current hostname.
ADMIN_EDGE_ROUTE_MANIFEST: Final[tuple[AdminEdgeRoute, ...]] = (
    AdminEdgeRoute(
        method="GET",
        path_template="/api/admin/system/health",
        bucket="admin_read",
        rationale="Admin diagnostics should not sit behind the public health policy.",
    ),
    AdminEdgeRoute(
        method="GET",
        path_template="/api/admin/provider-settings",
        bucket="admin_write",
        rationale=(
            "Provider secret status shares the exact path with destructive "
            "secret updates, so the path-only edge bucket stays strict."
        ),
    ),
    AdminEdgeRoute(
        method="PUT",
        path_template="/api/admin/provider-settings",
        bucket="admin_write",
        rationale="Provider secret updates are destructive platform-admin actions.",
    ),
    AdminEdgeRoute(
        method="POST",
        path_template="/api/snapshots",
        bucket="admin_write",
        rationale="Snapshot creation shares the same admin surface as snapshot restore/delete.",
    ),
    AdminEdgeRoute(
        method="GET",
        path_template="/api/snapshots",
        bucket="admin_write",
        rationale=(
            "Snapshot listing stays with the stricter snapshot prefix "
            "because the path also mutates."
        ),
    ),
    AdminEdgeRoute(
        method="POST",
        path_template="/api/snapshots/{snapshot_id}/restore",
        bucket="admin_write",
        rationale="Snapshot restore is a destructive admin action.",
    ),
    AdminEdgeRoute(
        method="DELETE",
        path_template="/api/snapshots/{snapshot_id}",
        bucket="admin_write",
        rationale="Snapshot delete is an admin mutation.",
    ),
    AdminEdgeRoute(
        method="GET",
        path_template="/api/plots/export",
        bucket="admin_read",
        rationale="Plot export exposes full garden state and is admin-only.",
    ),
    AdminEdgeRoute(
        method="POST",
        path_template="/api/plots/import",
        bucket="admin_write",
        rationale="Plot import is a destructive admin action.",
    ),
    AdminEdgeRoute(
        method="POST",
        path_template="/api/auth/mfa/totp/start",
        bucket="admin_write",
        rationale="Admin MFA enrollment should stay on the stricter admin path set.",
    ),
    AdminEdgeRoute(
        method="POST",
        path_template="/api/auth/mfa/totp/confirm",
        bucket="admin_write",
        rationale="Admin MFA confirmation returns recovery material and mutates auth state.",
    ),
    AdminEdgeRoute(
        method="POST",
        path_template="/api/auth/mfa/disable",
        bucket="admin_write",
        rationale="Disabling MFA is a destructive admin action.",
    ),
    AdminEdgeRoute(
        method="POST",
        path_template="/api/auth/mfa/recovery-codes/regenerate",
        bucket="admin_write",
        rationale="Regenerating admin recovery codes should sit on the strongest outer bucket.",
    ),
    AdminEdgeRoute(
        method="GET",
        path_template="/api/auth/users",
        bucket="admin_write",
        rationale=(
            "User listing shares the same path as user creation, "
            "so the exact path takes the stricter bucket."
        ),
    ),
    AdminEdgeRoute(
        method="POST",
        path_template="/api/auth/users",
        bucket="admin_write",
        rationale="User creation is an admin mutation.",
    ),
    AdminEdgeRoute(
        method="GET",
        path_template="/api/auth/user-invitations",
        bucket="admin_write",
        rationale=(
            "Invitation listing shares the same path as invitation creation and revoke flows."
        ),
    ),
    AdminEdgeRoute(
        method="POST",
        path_template="/api/auth/user-invitations",
        bucket="admin_write",
        rationale="User invitation creation is an admin mutation.",
    ),
    AdminEdgeRoute(
        method="DELETE",
        path_template="/api/auth/user-invitations/{invitation_id}",
        bucket="admin_write",
        rationale="User invitation revoke is an admin mutation.",
    ),
    AdminEdgeRoute(
        method="PATCH",
        path_template="/api/auth/users/{user_id}",
        bucket="admin_write",
        rationale="User updates sit on the stronger admin user-management prefix.",
    ),
    AdminEdgeRoute(
        method="DELETE",
        path_template="/api/auth/users/{user_id}",
        bucket="admin_write",
        rationale="User delete is an admin mutation.",
    ),
    AdminEdgeRoute(
        method="POST",
        path_template="/api/auth/users/{user_id}/revoke-sessions",
        bucket="admin_write",
        rationale="Per-user session revocation is an admin mutation.",
    ),
    AdminEdgeRoute(
        method="PUT",
        path_template="/api/auth/users/{user_id}/tier",
        bucket="admin_write",
        rationale="Tier changes sit on the stronger admin user-management prefix.",
    ),
    AdminEdgeRoute(
        method="POST",
        path_template="/api/auth/users/{user_id}/restart-onboarding",
        bucket="admin_write",
        rationale="Restarting onboarding is an admin mutation.",
    ),
    AdminEdgeRoute(
        method="POST",
        path_template="/api/auth/users/{user_id}/issue-reset",
        bucket="admin_write",
        rationale="Password reset issuance is an admin-sensitive flow.",
    ),
    AdminEdgeRoute(
        method="GET",
        path_template="/api/auth/audit-events",
        bucket="admin_read",
        rationale="Audit history is admin-only read access.",
    ),
    AdminEdgeRoute(
        method="GET",
        path_template="/api/auth/security-metrics",
        bucket="admin_read",
        rationale="Security metrics are admin-only read access.",
    ),
    AdminEdgeRoute(
        method="GET",
        path_template="/api/auth/security-alerts",
        bucket="admin_read",
        rationale="Security alerts are admin-only read access.",
    ),
    AdminEdgeRoute(
        method="GET",
        path_template="/api/auth/sessions",
        bucket="admin_read",
        rationale="Session inventory includes admin cross-user access.",
    ),
    AdminEdgeRoute(
        method="DELETE",
        path_template="/api/auth/sessions/{session_id}",
        bucket="admin_write",
        rationale=(
            "Session revocation can target another user's session when invoked by an admin."
        ),
    ),
    AdminEdgeRoute(
        method="POST",
        path_template="/api/auth/revoke-user-sessions",
        bucket="admin_write",
        rationale="Cross-user session revocation is a destructive admin action.",
    ),
    AdminEdgeRoute(
        method="POST",
        path_template="/api/auth/revoke-all-sessions",
        bucket="admin_write",
        rationale="Global session revocation is a destructive admin action.",
    ),
    AdminEdgeRoute(
        method="GET",
        path_template="/api/auth/emergency-read-only",
        bucket="admin_write",
        rationale=(
            "The exact path also carries the destructive toggle variant, "
            "so it stays on the write bucket."
        ),
    ),
    AdminEdgeRoute(
        method="PATCH",
        path_template="/api/auth/emergency-read-only",
        bucket="admin_write",
        rationale="Emergency read-only toggle is a destructive admin action.",
    ),
    AdminEdgeRoute(
        method="DELETE",
        path_template="/api/gardens/{garden_id}",
        bucket="admin_write",
        rationale="Garden deletion is a destructive admin action.",
    ),
    AdminEdgeRoute(
        method="GET",
        path_template="/api/gardens/{garden_id}/invitations",
        bucket="admin_write",
        rationale=(
            "Garden invitation listing shares the same admin-only path "
            "family as creation and revoke."
        ),
    ),
    AdminEdgeRoute(
        method="POST",
        path_template="/api/gardens/{garden_id}/invitations",
        bucket="admin_write",
        rationale="Garden invitation creation is an admin mutation.",
    ),
    AdminEdgeRoute(
        method="DELETE",
        path_template="/api/gardens/{garden_id}/invitations/{invitation_id}",
        bucket="admin_write",
        rationale="Garden invitation revoke is an admin mutation.",
    ),
    AdminEdgeRoute(
        method="GET",
        path_template="/api/media/plants/missing-covers",
        bucket="admin_read",
        rationale="Missing-cover review is a platform-admin read surface.",
    ),
    AdminEdgeRoute(
        method="POST",
        path_template="/api/media/plants/populate-missing-covers",
        bucket="admin_write",
        rationale="Cover population performs admin-triggered remote fetch and mutation work.",
    ),
)


# Garden-scoped management routes still require application auth and
# authorization, but intentionally remain on the standard API edge bucket.
ADMIN_EDGE_GENERIC_ROUTE_EXCEPTIONS: Final[tuple[AdminEdgeRouteDecision, ...]] = (
    AdminEdgeRouteDecision(
        method="GET",
        path_template="/api/gardens/{garden_id}/memberships",
        decision="generic_api",
        rationale="Garden membership reads are garden-scoped rather than platform-admin.",
    ),
    AdminEdgeRouteDecision(
        method="POST",
        path_template="/api/gardens/{garden_id}/memberships",
        decision="generic_api",
        rationale="Garden membership changes rely on application garden-owner authorization.",
    ),
    AdminEdgeRouteDecision(
        method="DELETE",
        path_template="/api/gardens/{garden_id}/memberships/{user_id}",
        decision="generic_api",
        rationale="Garden member removal relies on application garden-owner authorization.",
    ),
    AdminEdgeRouteDecision(
        method="GET",
        path_template="/api/gardens/{garden_id}/settings",
        decision="generic_api",
        rationale="Garden settings reads are garden-scoped rather than platform-admin.",
    ),
    AdminEdgeRouteDecision(
        method="PATCH",
        path_template="/api/gardens/{garden_id}/settings",
        decision="generic_api",
        rationale="Garden settings updates rely on application garden-owner authorization.",
    ),
    AdminEdgeRouteDecision(
        method="GET",
        path_template="/api/gardens/{garden_id}/map-objects",
        decision="generic_api",
        rationale="Map object reads are garden-scoped rather than platform-admin.",
    ),
    AdminEdgeRouteDecision(
        method="POST",
        path_template="/api/gardens/{garden_id}/map-objects",
        decision="generic_api",
        rationale="Map object creation relies on garden editor authorization.",
    ),
    AdminEdgeRouteDecision(
        method="PATCH",
        path_template="/api/gardens/{garden_id}/map-objects/{object_public_id}",
        decision="generic_api",
        rationale="Map object updates rely on garden editor authorization.",
    ),
    AdminEdgeRouteDecision(
        method="DELETE",
        path_template="/api/gardens/{garden_id}/map-objects/{object_public_id}",
        decision="generic_api",
        rationale="Map object deletion relies on garden editor authorization.",
    ),
    AdminEdgeRouteDecision(
        method="POST",
        path_template="/api/gardens/{garden_id}/map-objects/{object_public_id}/units",
        decision="generic_api",
        rationale="Nested map unit creation relies on garden editor authorization.",
    ),
    AdminEdgeRouteDecision(
        method="PATCH",
        path_template="/api/gardens/{garden_id}/map-objects/{object_public_id}/units/{unit_public_id}",
        decision="generic_api",
        rationale="Nested map unit updates rely on garden editor authorization.",
    ),
    AdminEdgeRouteDecision(
        method="DELETE",
        path_template="/api/gardens/{garden_id}/map-objects/{object_public_id}/units/{unit_public_id}",
        decision="generic_api",
        rationale="Nested map unit deletion relies on garden editor authorization.",
    ),
    AdminEdgeRouteDecision(
        method="GET",
        path_template="/api/gardens/{garden_id}/geocode",
        decision="generic_api",
        rationale="Garden geocoding is editor-scoped and app-rate-limited.",
    ),
    AdminEdgeRouteDecision(
        method="GET",
        path_template="/api/gardens/{garden_id}/lidar",
        decision="generic_api",
        rationale="Garden LIDAR status reads are garden-scoped rather than platform-admin.",
    ),
    AdminEdgeRouteDecision(
        method="POST",
        path_template="/api/gardens/{garden_id}/lidar",
        decision="generic_api",
        rationale=(
            "Garden LIDAR uploads rely on editor authorization and a dedicated "
            "upload edge body limit rather than the admin edge buckets."
        ),
    ),
    AdminEdgeRouteDecision(
        method="DELETE",
        path_template="/api/gardens/{garden_id}/lidar",
        decision="generic_api",
        rationale="Garden LIDAR removal is scoped to editors of the selected garden.",
    ),
    AdminEdgeRouteDecision(
        method="POST",
        path_template="/api/gardens/{garden_id}/zones",
        decision="generic_api",
        rationale="Garden zone creation is a garden-scoped management operation.",
    ),
    AdminEdgeRouteDecision(
        method="PUT",
        path_template="/api/gardens/{garden_id}/complete-onboarding",
        decision="generic_api",
        rationale="Garden onboarding completion is scoped to the selected garden.",
    ),
    AdminEdgeRouteDecision(
        method="PATCH",
        path_template="/api/gardens/{garden_id}/complete-onboarding",
        decision="generic_api",
        rationale="Garden onboarding completion is scoped to the selected garden.",
    ),
    AdminEdgeRouteDecision(
        method="POST",
        path_template="/api/gardens/{garden_id}/complete-onboarding",
        decision="generic_api",
        rationale="Garden onboarding completion is scoped to the selected garden.",
    ),
    AdminEdgeRouteDecision(
        method="POST",
        path_template="/api/media/plants/{plt_id}/cover",
        decision="generic_api",
        rationale="Plant-cover selection is a garden/content management operation.",
    ),
)


ADMIN_EDGE_LOCATION_RULES: Final[tuple[AdminEdgeLocationRule, ...]] = (
    AdminEdgeLocationRule(
        match_kind="prefix",
        pattern="/api/admin/",
        bucket="admin_read",
        rationale="Admin-only diagnostics prefix.",
    ),
    AdminEdgeLocationRule(
        match_kind="exact",
        pattern="/api/admin/provider-settings",
        bucket="admin_write",
        rationale="Provider settings reads and writes share a destructive admin path.",
    ),
    AdminEdgeLocationRule(
        match_kind="exact",
        pattern="/api/snapshots",
        bucket="admin_write",
        rationale="Snapshot list/create path.",
    ),
    AdminEdgeLocationRule(
        match_kind="prefix",
        pattern="/api/snapshots/",
        bucket="admin_write",
        rationale="Snapshot restore/delete subpaths.",
    ),
    AdminEdgeLocationRule(
        match_kind="exact",
        pattern="/api/plots/export",
        bucket="admin_read",
        rationale="Admin export path.",
    ),
    AdminEdgeLocationRule(
        match_kind="exact",
        pattern="/api/plots/import",
        bucket="admin_write",
        rationale="Destructive import path.",
    ),
    AdminEdgeLocationRule(
        match_kind="prefix",
        pattern="/api/auth/mfa/",
        bucket="admin_write",
        rationale="Admin MFA enrollment/disable/recovery paths.",
    ),
    AdminEdgeLocationRule(
        match_kind="exact",
        pattern="/api/auth/users",
        bucket="admin_write",
        rationale="User list/create path.",
    ),
    AdminEdgeLocationRule(
        match_kind="prefix",
        pattern="/api/auth/users/",
        bucket="admin_write",
        rationale="User-management subpaths.",
    ),
    AdminEdgeLocationRule(
        match_kind="exact",
        pattern="/api/auth/user-invitations",
        bucket="admin_write",
        rationale="Invitation list/create path.",
    ),
    AdminEdgeLocationRule(
        match_kind="prefix",
        pattern="/api/auth/user-invitations/",
        bucket="admin_write",
        rationale="Invitation revoke subpaths.",
    ),
    AdminEdgeLocationRule(
        match_kind="exact",
        pattern="/api/auth/audit-events",
        bucket="admin_read",
        rationale="Audit history read path.",
    ),
    AdminEdgeLocationRule(
        match_kind="exact",
        pattern="/api/auth/security-metrics",
        bucket="admin_read",
        rationale="Security metrics read path.",
    ),
    AdminEdgeLocationRule(
        match_kind="exact",
        pattern="/api/auth/security-alerts",
        bucket="admin_read",
        rationale="Security alerts read path.",
    ),
    AdminEdgeLocationRule(
        match_kind="exact",
        pattern="/api/auth/sessions",
        bucket="admin_read",
        rationale="Session inventory includes admin cross-user access.",
    ),
    AdminEdgeLocationRule(
        match_kind="prefix",
        pattern="/api/auth/sessions/",
        bucket="admin_write",
        rationale="Per-session revocation path.",
    ),
    AdminEdgeLocationRule(
        match_kind="exact",
        pattern="/api/auth/revoke-user-sessions",
        bucket="admin_write",
        rationale="Cross-user session revoke path.",
    ),
    AdminEdgeLocationRule(
        match_kind="exact",
        pattern="/api/auth/revoke-all-sessions",
        bucket="admin_write",
        rationale="Global session revoke path.",
    ),
    AdminEdgeLocationRule(
        match_kind="exact",
        pattern="/api/auth/emergency-read-only",
        bucket="admin_write",
        rationale="Emergency-read-only status/toggle path.",
    ),
    AdminEdgeLocationRule(
        match_kind="regex",
        pattern=r"^/api/gardens/[0-9]+(/invitations(/[0-9]+)?)?$",
        bucket="admin_write",
        rationale="Garden deletion and invitation list/create/revoke paths.",
    ),
    AdminEdgeLocationRule(
        match_kind="exact",
        pattern="/api/media/plants/missing-covers",
        bucket="admin_read",
        rationale="Missing-cover review path.",
    ),
    AdminEdgeLocationRule(
        match_kind="exact",
        pattern="/api/media/plants/populate-missing-covers",
        bucket="admin_write",
        rationale="Cover import mutation path.",
    ),
)


def materialize_path_template(path_template: str) -> str:
    return re.sub(r"\{[^/]+\}", "1", path_template)


def location_matches_path(rule: AdminEdgeLocationRule, path: str) -> bool:
    if rule.match_kind == "exact":
        return path == rule.pattern
    if rule.match_kind == "prefix":
        return path.startswith(rule.pattern)
    return re.fullmatch(rule.pattern, path) is not None


def admin_edge_bucket_for_path(path: str) -> AdminEdgeBucket | None:
    for match_kind in ("exact", "prefix", "regex"):
        for rule in ADMIN_EDGE_LOCATION_RULES:
            if rule.match_kind == match_kind and location_matches_path(rule, path):
                return rule.bucket
    return None


def nginx_location_header(rule: AdminEdgeLocationRule) -> str:
    if rule.match_kind == "exact":
        return f"location = {rule.pattern} {{"
    if rule.match_kind == "prefix":
        return f"location ^~ {rule.pattern} {{"
    return f"location ~ {rule.pattern} {{"
