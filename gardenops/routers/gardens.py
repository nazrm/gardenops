import json
import os
import re
import secrets
from hashlib import sha256
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field

from gardenops.audit import write_audit_event
from gardenops.branding import app_user_agent
from gardenops.constants import GRID_COLS, GRID_ROWS
from gardenops.db import DB, DbConn, current_timestamp_ms, ensure_indoor_plot
from gardenops.events import notify_garden_modified
from gardenops.models import LayoutExportBody, LayoutStateBody, StrictBaseModel
from gardenops.rate_limit import enforce_rate_limit, env_int
from gardenops.request_body import read_body_limited
from gardenops.router_helpers import auth_context as _auth_context
from gardenops.router_helpers import is_local_admin_fallback as _is_local_admin_fallback
from gardenops.routers.auth import enforce_destructive_admin_controls
from gardenops.security import AuthContext, user_lifecycle_enabled
from gardenops.security_metrics import record_security_event
from gardenops.services.garden_layout_lock import lock_garden_layout
from gardenops.services.lidar_terrain import (
    clear_uploaded_terrain,
    lidar_upload_max_bytes,
    local_terrain_storage_info,
    save_uploaded_terrain,
)
from gardenops.services.media_store import unlink_storage_keys
from gardenops.services.plot_references import delete_plots_for_replacement

router = APIRouter()

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class GardenSettingsBody(StrictBaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    grid_rows: int | None = Field(default=None, ge=5, le=100)
    grid_cols: int | None = Field(default=None, ge=5, le=100)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    address: str | None = Field(default=None, max_length=500)
    onboarding_complete: bool | None = None


class CreateZoneBody(StrictBaseModel):
    zone_code: str = Field(min_length=1, max_length=20)
    zone_name: str = Field(min_length=1, max_length=120)
    start_row: int = Field(ge=1, le=100)
    start_col: int = Field(ge=1, le=100)
    end_row: int = Field(ge=1, le=100)
    end_col: int = Field(ge=1, le=100)
    color: str | None = None


class CompleteOnboardingBody(StrictBaseModel):
    name: str = Field(min_length=1, max_length=120)
    grid_rows: int = Field(ge=5, le=100)
    grid_cols: int = Field(ge=5, le=100)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    address: str = Field(default="", max_length=500)
    mode: Literal["manual", "import"] = "manual"
    house: LayoutStateBody | None = None
    zones: list[CreateZoneBody] = Field(default_factory=list, max_length=200)
    imported_layout: LayoutExportBody | None = None


class CreateGardenBody(StrictBaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=80)


class UpsertGardenMembershipBody(StrictBaseModel):
    username: str = Field(min_length=1, max_length=80)
    role: Literal["viewer", "editor", "admin"]
    action_reason: str = Field(default="", max_length=400)


class CreateGardenInvitationBody(StrictBaseModel):
    invitee_username: str = Field(min_length=1, max_length=80)
    role: Literal["viewer"] = "viewer"
    expires_in_minutes: int | None = Field(default=None, ge=5, le=30 * 24 * 60)
    action_reason: str = Field(default="", max_length=400)


def _is_platform_admin(context: AuthContext) -> bool:
    return context.role == "admin"


def _require_platform_admin(context: AuthContext) -> None:
    if not _is_platform_admin(context) and not (
        context.user_id is None and context.role == "admin"
    ):
        raise HTTPException(status_code=403, detail="Platform admin required")


def _remote_host(request: Request) -> str:
    return request.client.host if request.client and request.client.host else "unknown"


def _audit_membership_change(
    request: Request,
    context: AuthContext,
    detail: str,
    *,
    garden_id: int | None = None,
    db: DbConn | None = None,
) -> None:
    request.state.audited_by_handler = True
    write_audit_event(
        method=request.method,
        path=request.url.path,
        status_code=200,
        remote_host=_remote_host(request),
        detail=detail,
        auth_context=context,
        garden_id=garden_id,
        db=db,
    )


def _normalize_action_reason(
    request: Request,
    *,
    body_reason: str = "",
) -> str:
    reason = body_reason.strip() or request.headers.get("x-action-reason", "").strip()
    if not reason:
        return "unspecified"
    return reason[:400]


def _lifecycle_detail(event: str, **fields: object) -> str:
    return f"{event} {json.dumps(fields, sort_keys=True, separators=(',', ':'))}"


def _enforce_lifecycle_rate_limit(
    request: Request,
    *,
    bucket: str,
    env_name: str,
    default_limit: int = 20,
) -> None:
    enforce_rate_limit(
        request,
        bucket=bucket,
        limit=env_int(env_name, default_limit),
        window_seconds=60,
    )


def _geocode_query(query: str) -> list[dict[str, object]]:
    params = urlencode(
        {
            "format": "jsonv2",
            "limit": "5",
            "addressdetails": "0",
            "q": query,
        },
    )
    request = UrlRequest(
        f"https://nominatim.openstreetmap.org/search?{params}",
        headers={
            "Accept": "application/json",
            "User-Agent": app_user_agent("garden-geocoder"),
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=8.0) as response:  # noqa: S310 - fixed public host.
            payload = response.read(200_000)
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail="Location lookup failed") from exc
    except URLError as exc:
        raise HTTPException(status_code=502, detail="Location lookup unavailable") from exc
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502, detail="Location lookup returned invalid data"
        ) from exc
    if not isinstance(parsed, list):
        raise HTTPException(status_code=502, detail="Location lookup returned invalid data")

    results: list[dict[str, object]] = []
    for item in parsed[:5]:
        if not isinstance(item, dict):
            continue
        try:
            latitude = float(str(item.get("lat", "")))
            longitude = float(str(item.get("lon", "")))
        except ValueError:
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue
        display_name = str(item.get("display_name") or "").strip()
        if not display_name:
            continue
        results.append(
            {
                "display_name": display_name[:500],
                "latitude": latitude,
                "longitude": longitude,
            },
        )
    return results


def _require_user_lifecycle_enabled() -> None:
    if not user_lifecycle_enabled():
        raise HTTPException(status_code=404, detail="User lifecycle is disabled")


def _normalize_slug(raw: str) -> str:
    slug = _SLUG_RE.sub("-", raw.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        raise HTTPException(status_code=400, detail="Garden slug cannot be empty")
    if len(slug) > 80:
        slug = slug[:80].rstrip("-")
    if not slug:
        raise HTTPException(status_code=400, detail="Garden slug cannot be empty")
    return slug


def _normalize_garden_name(raw: str) -> str:
    name = raw.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Garden name cannot be empty")
    return name


def _require_garden_exists(db: DbConn, garden_id: int) -> None:
    exists = db.execute(
        "SELECT 1 FROM gardens WHERE id = %s LIMIT 1",
        (garden_id,),
    ).fetchone()
    if not exists:
        raise HTTPException(status_code=404, detail="Garden not found")


def _invalidate_garden_terrain_state(db: DbConn, garden_id: int) -> None:
    db.execute(
        "DELETE FROM shademap_cache WHERE garden_id = %s AND cache_kind = 'terrain-tile'",
        (garden_id,),
    )
    db.execute("DELETE FROM plot_elevations WHERE garden_id = %s", (garden_id,))


def _require_membership_admin(
    db: DbConn,
    *,
    context: AuthContext,
    garden_id: int,
) -> None:
    _require_membership_role(db, context=context, garden_id=garden_id, allowed_roles=("admin",))


def _require_membership_editor(
    db: DbConn,
    *,
    context: AuthContext,
    garden_id: int,
) -> None:
    _require_membership_role(
        db,
        context=context,
        garden_id=garden_id,
        allowed_roles=("admin", "editor"),
    )


def _require_membership_role(
    db: DbConn,
    *,
    context: AuthContext,
    garden_id: int,
    allowed_roles: tuple[str, ...],
) -> None:
    _require_garden_exists(db, garden_id)
    if _is_local_admin_fallback(context):
        return
    if _is_platform_admin(context):
        return
    if context.user_id is None:
        raise HTTPException(status_code=404, detail="Garden not found")
    membership = db.execute(
        """
        SELECT role
        FROM garden_memberships
        WHERE garden_id = %s AND user_id = %s
        LIMIT 1
        """,
        (garden_id, context.user_id),
    ).fetchone()
    if not membership or str(membership["role"]) not in allowed_roles:
        raise HTTPException(status_code=404, detail="Garden not found")


def _garden_admin_count(db: DbConn, garden_id: int) -> int:
    row = db.execute(
        """
        SELECT COUNT(*) AS c
        FROM garden_memberships gm
        JOIN auth_users u ON u.id = gm.user_id
        WHERE gm.garden_id = %s AND gm.role = 'admin' AND u.is_active = 1
        """,
        (garden_id,),
    ).fetchone()
    return int(row["c"] if row else 0)


def _revoke_calendar_subscriptions_for_user(
    db: DbConn,
    *,
    garden_id: int,
    user_id: int,
    now_ms: int,
) -> None:
    db.execute(
        """
        UPDATE calendar_subscriptions
        SET revoked_at_ms = COALESCE(revoked_at_ms, %s),
            updated_at_ms = %s
        WHERE garden_id = %s
          AND owner_user_id = %s
          AND revoked_at_ms IS NULL
        """,
        (now_ms, now_ms, garden_id, user_id),
    )


def _managed_nondefault_garden_count(db: DbConn, user_id: int) -> int:
    row = db.execute(
        """
        SELECT COUNT(*) AS c
        FROM gardens g
        WHERE g.owner_user_id = %s
          AND g.slug <> 'default'
        """,
        (user_id,),
    ).fetchone()
    return int(row["c"] if row else 0)


def _preferred_plot_owner_user_id(
    db: DbConn,
    *,
    garden_id: int,
    preferred_user_id: int | None,
) -> int:
    from gardenops.main import _owner_user_for_garden

    return _owner_user_for_garden(
        db,
        garden_id=garden_id,
        preferred_user_id=preferred_user_id,
    )


def _default_onboarding_house_state(*, grid_rows: int, grid_cols: int) -> dict[str, int]:
    from gardenops.main import _default_house_state

    return _default_house_state(grid_rows=grid_rows, grid_cols=grid_cols)


def _current_house_state(
    db: DbConn,
    *,
    garden_id: int,
    grid_rows: int,
    grid_cols: int,
) -> dict[str, int]:
    row = db.execute(
        """
        SELECT house_row, house_col, house_width, house_height, north_degrees
        FROM layout_state
        WHERE garden_id = %s
        LIMIT 1
        """,
        (garden_id,),
    ).fetchone()
    if not row:
        return _default_onboarding_house_state(
            grid_rows=grid_rows,
            grid_cols=grid_cols,
        )
    return {
        "row": int(row["house_row"]),
        "col": int(row["house_col"]),
        "width": int(row["house_width"]),
        "height": int(row["house_height"]),
        "north_degrees": int(row["north_degrees"]),
        "grid_rows": grid_rows,
        "grid_cols": grid_cols,
    }


def _house_overlaps_zone(
    *,
    house_row: int,
    house_col: int,
    house_width: int,
    house_height: int,
    start_row: int,
    start_col: int,
    end_row: int,
    end_col: int,
) -> bool:
    house_end_row = house_row + house_height - 1
    house_end_col = house_col + house_width - 1
    return not (
        end_row < house_row
        or start_row > house_end_row
        or end_col < house_col
        or start_col > house_end_col
    )


def _validate_zone_bounds(
    *,
    start_row: int,
    start_col: int,
    end_row: int,
    end_col: int,
    grid_rows: int,
    grid_cols: int,
) -> None:
    if start_row > end_row or start_col > end_col:
        raise HTTPException(status_code=400, detail="Invalid zone bounds")
    if end_row > grid_rows or end_col > grid_cols:
        raise HTTPException(
            status_code=400,
            detail=f"Zone exceeds garden grid ({grid_cols}x{grid_rows})",
        )


def _validate_zone_against_house(
    *,
    zone_code: str,
    start_row: int,
    start_col: int,
    end_row: int,
    end_col: int,
    house: dict[str, int],
) -> None:
    if _house_overlaps_zone(
        house_row=int(house["row"]),
        house_col=int(house["col"]),
        house_width=int(house["width"]),
        house_height=int(house["height"]),
        start_row=start_row,
        start_col=start_col,
        end_row=end_row,
        end_col=end_col,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Zone {zone_code} overlaps the house placeholder or structure",
        )


def _normalized_zone_identity(zone_code: str, zone_name: str) -> tuple[str, str]:
    normalized_code = zone_code.strip()
    normalized_name = zone_name.strip()
    if not normalized_code or not normalized_name:
        raise HTTPException(status_code=400, detail="Zone code and name are required")
    return normalized_code, normalized_name


def _clear_garden_plot_state(db: DbConn, *, garden_id: int) -> list[tuple[str, str]]:
    lock_garden_layout(db, garden_id)
    plot_ids = [
        str(row["plot_id"])
        for row in db.execute(
            "SELECT plot_id FROM plot_ownership WHERE garden_id = %s",
            (garden_id,),
        ).fetchall()
    ]
    media_storage_pairs: list[tuple[str, str]] = []
    if plot_ids:
        result = delete_plots_for_replacement(
            db,
            garden_id=garden_id,
            plot_ids=plot_ids,
        )
        media_storage_pairs.extend(result.media_storage_pairs)
    db.execute(
        "DELETE FROM shademap_cache WHERE garden_id = %s "
        "AND cache_kind IN ('terrain-tile', 'features')",
        (garden_id,),
    )
    return media_storage_pairs


def _delete_garden_related_state(db: DbConn, *, garden_id: int) -> dict[str, int]:
    plant_ids = [
        str(row["plt_id"])
        for row in db.execute(
            "SELECT plt_id FROM plant_ownership WHERE garden_id = %s",
            (garden_id,),
        ).fetchall()
    ]
    plot_count_row = db.execute(
        "SELECT COUNT(*) AS cnt FROM plot_ownership WHERE garden_id = %s",
        (garden_id,),
    ).fetchone()
    snapshot_count_row = db.execute(
        "SELECT COUNT(*) AS cnt FROM layout_snapshots WHERE garden_id = %s",
        (garden_id,),
    ).fetchone()
    _clear_garden_plot_state(db, garden_id=garden_id)
    db.execute("DELETE FROM plot_elevations WHERE garden_id = %s", (garden_id,))
    db.execute("DELETE FROM plot_elevation_overrides WHERE garden_id = %s", (garden_id,))
    db.execute("DELETE FROM layout_snapshots WHERE garden_id = %s", (garden_id,))
    db.execute("DELETE FROM shademap_obstacles WHERE garden_id = %s", (garden_id,))
    db.execute("DELETE FROM shademap_cache WHERE garden_id = %s", (garden_id,))
    db.execute("DELETE FROM plant_ownership WHERE garden_id = %s", (garden_id,))
    orphaned = (
        db.execute(
            """
        SELECT plt_id FROM plants
        WHERE plt_id NOT IN (SELECT plt_id FROM plant_ownership)
          AND plt_id IN ({})
        """.format(",".join(["%s"] * len(plant_ids))),
            plant_ids,
        ).fetchall()
        if plant_ids
        else []
    )
    orphan_ids = [str(row["plt_id"]) for row in orphaned]
    if orphan_ids:
        ph = ",".join(["%s"] * len(orphan_ids))
        db.execute(f"DELETE FROM plot_plants WHERE plt_id IN ({ph})", orphan_ids)
        db.execute(f"DELETE FROM plants WHERE plt_id IN ({ph})", orphan_ids)
    deleted_plants = len(orphan_ids)
    db.execute("DELETE FROM gardens WHERE id = %s", (garden_id,))
    return {
        "plots_deleted": int(plot_count_row["cnt"]) if plot_count_row else 0,
        "snapshots_deleted": int(snapshot_count_row["cnt"]) if snapshot_count_row else 0,
        "plants_deleted": deleted_plants,
    }


def _next_zone_plot_number(
    db: DbConn,
    *,
    zone_code: str,
    garden_id: int,
) -> int:
    # Use the global max to avoid plot_id collisions across gardens.
    # A garden-only max could generate "B1" which already exists in another garden.
    row = db.execute(
        """
        SELECT MAX(p.plot_number) AS mx
        FROM plots p
        WHERE p.zone_code = %s
        """,
        (zone_code,),
    ).fetchone()
    garden_row = db.execute(
        """
        SELECT MAX(p.plot_number) AS mx
        FROM plots p
        JOIN plot_ownership po ON po.plot_id = p.plot_id
        WHERE p.zone_code = %s AND po.garden_id = %s
        """,
        (zone_code, garden_id),
    ).fetchone()
    global_max = int(row["mx"]) if row and row["mx"] is not None else 0
    garden_max = int(garden_row["mx"]) if garden_row and garden_row["mx"] is not None else 0
    return max(global_max, garden_max) + 1


def _create_zone_plots(
    db: DbConn,
    *,
    garden_id: int,
    zone_code: str,
    zone_name: str,
    start_row: int,
    start_col: int,
    end_row: int,
    end_col: int,
    color: str | None,
    owner_user_id: int,
) -> list[dict[str, int | str]]:
    lock_garden_layout(db, garden_id)
    next_num = _next_zone_plot_number(
        db,
        zone_code=zone_code,
        garden_id=garden_id,
    )
    occupied = {
        (int(row["grid_row"]), int(row["grid_col"]))
        for row in db.execute(
            """
            SELECT p.grid_row, p.grid_col
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE po.garden_id = %s
              AND p.grid_row BETWEEN %s AND %s
              AND p.grid_col BETWEEN %s AND %s
            """,
            (garden_id, start_row, end_row, start_col, end_col),
        ).fetchall()
    }
    created: list[dict[str, int | str]] = []
    for r in range(start_row, end_row + 1):
        for c in range(start_col, end_col + 1):
            if (r, c) in occupied:
                continue
            plot_id = f"{zone_code}{next_num}"
            db.execute(
                """
                INSERT INTO plots
                    (plot_id, garden_id, zone_code, zone_name,
                     plot_number, grid_row, grid_col, color)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    plot_id,
                    garden_id,
                    zone_code,
                    zone_name,
                    next_num,
                    r,
                    c,
                    color,
                ),
            )
            db.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                ON CONFLICT(plot_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id,
                    garden_id = excluded.garden_id
                """,
                (plot_id, owner_user_id, garden_id),
            )
            created.append(
                {
                    "plot_id": plot_id,
                    "grid_row": r,
                    "grid_col": c,
                    "plot_number": next_num,
                }
            )
            next_num += 1
    return created


def _derive_import_grid(
    body: CompleteOnboardingBody,
) -> tuple[int, int]:
    if body.imported_layout is None:
        return body.grid_rows, body.grid_cols
    house = body.imported_layout.house
    max_plot_row = max((plot.grid_row for plot in body.imported_layout.plots), default=0)
    max_plot_col = max((plot.grid_col for plot in body.imported_layout.plots), default=0)
    house_rows = (
        house.grid_rows
        if house is not None and house.grid_rows is not None
        else (house.row + house.height - 1 if house is not None else 0)
    )
    house_cols = (
        house.grid_cols
        if house is not None and house.grid_cols is not None
        else (house.col + house.width - 1 if house is not None else 0)
    )
    grid_rows = house_rows or max_plot_row or body.grid_rows or GRID_ROWS
    grid_cols = house_cols or max_plot_col or body.grid_cols or GRID_COLS
    return (
        max(5, min(int(grid_rows), 100)),
        max(5, min(int(grid_cols), 100)),
    )


def _invitation_ttl_minutes(requested: int | None) -> int:
    raw = (
        str(requested)
        if requested is not None
        else os.environ.get(
            "AUTH_GARDEN_INVITATION_TTL_MINUTES",
            "",
        ).strip()
    )
    try:
        parsed = int(raw) if raw else 7 * 24 * 60
    except ValueError:
        parsed = 7 * 24 * 60
    return max(5, min(parsed, 30 * 24 * 60))


def _serialize_invitation(row: dict[str, Any], *, now_ms: int) -> dict[str, object]:
    accepted_at_ms = row["accepted_at_ms"]
    revoked_at_ms = row["revoked_at_ms"]
    expires_at_ms = int(row["expires_at_ms"])
    if accepted_at_ms is not None:
        status = "accepted"
    elif revoked_at_ms is not None:
        status = "revoked"
    elif expires_at_ms <= now_ms:
        status = "expired"
    else:
        status = "pending"
    return {
        "id": int(row["id"]),
        "garden_id": int(row["garden_id"]),
        "invitee_username": str(row["invitee_username"]),
        "role": str(row["role"]),
        "created_by_user_id": (
            int(row["created_by_user_id"]) if row["created_by_user_id"] is not None else None
        ),
        "created_at_ms": int(row["created_at_ms"]),
        "expires_at_ms": expires_at_ms,
        "accepted_at_ms": accepted_at_ms,
        "accepted_user_id": (
            int(row["accepted_user_id"]) if row["accepted_user_id"] is not None else None
        ),
        "revoked_at_ms": revoked_at_ms,
        "status": status,
    }


@router.get("/gardens")
def list_gardens(request: Request, db: DB) -> list[dict[str, object]]:
    context = _auth_context(request)
    if _is_local_admin_fallback(context):
        rows = db.execute(
            """
            SELECT id, slug, name, created_at, onboarding_complete
            FROM gardens
            WHERE slug <> 'default'
            ORDER BY id
            """,
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "slug": str(row["slug"]),
                "name": str(row["name"]),
                "created_at": str(row["created_at"]),
                "role": "admin",
                "active": context.garden_id == int(row["id"]),
                "onboarding_complete": bool(row["onboarding_complete"]),
            }
            for row in rows
        ]

    if _is_platform_admin(context):
        rows = db.execute(
            """
            SELECT g.id, g.slug, g.name, g.created_at, g.owner_user_id,
                   g.onboarding_complete, gm.role AS membership_role
            FROM gardens g
            LEFT JOIN garden_memberships gm
              ON gm.garden_id = g.id AND gm.user_id = %s
            WHERE g.slug <> 'default'
            ORDER BY g.id
            """,
            (context.user_id,),
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "slug": str(row["slug"]),
                "name": str(row["name"]),
                "created_at": str(row["created_at"]),
                "role": str(row["membership_role"] or "admin"),
                "active": context.garden_id == int(row["id"]),
                "onboarding_complete": bool(row["onboarding_complete"]),
                "owned_by_current_user": (
                    context.user_id is not None
                    and row["owner_user_id"] is not None
                    and int(row["owner_user_id"]) == int(context.user_id)
                ),
            }
            for row in rows
        ]

    if context.user_id is None:
        return []
    rows = db.execute(
        """
        SELECT g.id, g.slug, g.name, g.created_at,
               g.onboarding_complete, g.owner_user_id, gm.role
        FROM garden_memberships gm
        JOIN gardens g ON g.id = gm.garden_id
        WHERE gm.user_id = %s
          AND g.slug <> 'default'
        ORDER BY g.id
        """,
        (context.user_id,),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "slug": str(row["slug"]),
            "name": str(row["name"]),
            "created_at": str(row["created_at"]),
            "role": str(row["role"]),
            "active": context.garden_id == int(row["id"]),
            "onboarding_complete": bool(row["onboarding_complete"]),
            "owned_by_current_user": (
                context.user_id is not None
                and row["owner_user_id"] is not None
                and int(row["owner_user_id"]) == int(context.user_id)
            ),
        }
        for row in rows
    ]


@router.post("/gardens", status_code=201)
def create_garden(
    body: CreateGardenBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _auth_context(request)
    if not (_is_platform_admin(context) or context.role == "editor"):
        raise HTTPException(status_code=403, detail="Editor or admin role required")
    if context.role == "editor":
        if context.user_id is None:
            raise HTTPException(status_code=403, detail="Editor or admin role required")
        if _managed_nondefault_garden_count(db, context.user_id) > 0:
            raise HTTPException(status_code=409, detail="Editors can only create one own garden")

    garden_name = _normalize_garden_name(body.name)
    slug = _normalize_slug(body.slug if body.slug else garden_name)
    try:
        row = db.execute(
            """
            INSERT INTO gardens (slug, name, owner_user_id)
            VALUES (%s, %s, %s) RETURNING id
            """,
            (slug, garden_name, context.user_id),
        ).fetchone()
    except psycopg.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Garden slug already exists") from exc

    assert row is not None
    garden_id = int(row["id"])
    if context.user_id is not None:
        db.execute(
            """
            INSERT INTO garden_memberships (garden_id, user_id, role)
            VALUES (%s, %s, 'admin')
            ON CONFLICT(garden_id, user_id) DO UPDATE SET
                role = excluded.role
            """,
            (garden_id, context.user_id),
        )
    db.commit()
    ensure_indoor_plot(db, garden_id, owner_user_id=context.user_id)
    _audit_membership_change(
        request,
        context,
        f"garden.create garden_id={garden_id} slug={slug}",
        garden_id=garden_id,
        db=db,
    )
    return {
        "id": garden_id,
        "slug": slug,
        "name": garden_name,
        "role": "admin",
        "owned_by_current_user": True,
    }


@router.delete("/gardens/{garden_id}")
def delete_garden(
    garden_id: int,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context, action_reason = enforce_destructive_admin_controls(request)
    garden = db.execute(
        """
        SELECT id, slug, name
        FROM gardens
        WHERE id = %s
        LIMIT 1
        """,
        (garden_id,),
    ).fetchone()
    if not garden:
        raise HTTPException(status_code=404, detail="Garden not found")
    slug = str(garden["slug"])
    name = str(garden["name"])
    if slug == "default":
        raise HTTPException(status_code=409, detail="Default garden cannot be deleted")
    stats = _delete_garden_related_state(db, garden_id=garden_id)
    db.commit()
    notify_garden_modified()
    record_security_event("destructive_admin_actions")
    record_security_event("destructive_admin_actions_delete_garden")
    _audit_membership_change(
        request,
        context,
        _lifecycle_detail(
            "garden.delete",
            garden_id=garden_id,
            garden_name=name,
            slug=slug,
            action_reason=action_reason,
            **stats,
        ),
        garden_id=None,
        db=db,
    )
    return {
        "status": "ok",
        "garden_id": garden_id,
        "garden_name": name,
        **stats,
    }


@router.get("/gardens/{garden_id}/memberships")
def list_garden_memberships(
    garden_id: int,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_membership_admin(db, context=context, garden_id=garden_id)
    rows = db.execute(
        """
        SELECT gm.user_id, u.username, gm.role, gm.created_at
        FROM garden_memberships gm
        JOIN auth_users u ON u.id = gm.user_id
        WHERE gm.garden_id = %s
        ORDER BY CASE gm.role
            WHEN 'admin' THEN 0
            WHEN 'editor' THEN 1
            ELSE 2
        END, u.username
        """,
        (garden_id,),
    ).fetchall()
    return {
        "garden_id": garden_id,
        "memberships": [
            {
                "user_id": int(row["user_id"]),
                "username": str(row["username"]),
                "role": str(row["role"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ],
    }


@router.post("/gardens/{garden_id}/memberships")
def upsert_garden_membership(
    garden_id: int,
    body: UpsertGardenMembershipBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _auth_context(request)
    _enforce_lifecycle_rate_limit(
        request,
        bucket="garden-membership-upsert",
        env_name="GARDEN_MEMBERSHIP_UPSERT_RATE_LIMIT",
    )
    action_reason = _normalize_action_reason(
        request,
        body_reason=body.action_reason,
    )
    _require_membership_admin(db, context=context, garden_id=garden_id)

    user = db.execute(
        """
        SELECT id, username
        FROM auth_users
        WHERE username = %s AND is_active = 1
        LIMIT 1
        """,
        (body.username.strip(),),
    ).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user_id = int(user["id"])
    previous = db.execute(
        """
        SELECT role
        FROM garden_memberships
        WHERE garden_id = %s AND user_id = %s
        LIMIT 1
        """,
        (garden_id, user_id),
    ).fetchone()
    previous_role = str(previous["role"]) if previous else None
    demoting_last_admin = (
        previous_role == "admin"
        and body.role != "admin"
        and _garden_admin_count(db, garden_id) <= 1
    )
    if demoting_last_admin:
        raise HTTPException(status_code=409, detail="Garden must retain at least one admin")

    db.execute(
        """
        INSERT INTO garden_memberships (garden_id, user_id, role)
        VALUES (%s, %s, %s)
        ON CONFLICT(garden_id, user_id) DO UPDATE SET
            role = excluded.role
        """,
        (garden_id, user_id, body.role),
    )
    if previous_role in {"editor", "admin"} and body.role == "viewer":
        _revoke_calendar_subscriptions_for_user(
            db,
            garden_id=garden_id,
            user_id=user_id,
            now_ms=current_timestamp_ms(),
        )
    db.commit()
    _audit_membership_change(
        request,
        context,
        _lifecycle_detail(
            "garden.membership.upsert",
            garden_id=garden_id,
            user_id=user_id,
            username=str(user["username"]),
            role=body.role,
            previous_role=previous_role,
            action_reason=action_reason,
        ),
        garden_id=garden_id,
        db=db,
    )
    return {
        "garden_id": garden_id,
        "user_id": user_id,
        "username": str(user["username"]),
        "role": body.role,
    }


@router.delete("/gardens/{garden_id}/memberships/{user_id}")
def delete_garden_membership(
    garden_id: int,
    user_id: int,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _auth_context(request)
    _enforce_lifecycle_rate_limit(
        request,
        bucket="garden-membership-remove",
        env_name="GARDEN_MEMBERSHIP_REMOVE_RATE_LIMIT",
    )
    action_reason = _normalize_action_reason(request)
    _require_membership_admin(db, context=context, garden_id=garden_id)

    membership = db.execute(
        """
        SELECT gm.role, u.username, u.is_active
        FROM garden_memberships gm
        JOIN auth_users u ON u.id = gm.user_id
        WHERE gm.garden_id = %s AND gm.user_id = %s
        LIMIT 1
        """,
        (garden_id, user_id),
    ).fetchone()
    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found")

    if (
        str(membership["role"]) == "admin"
        and int(membership["is_active"]) == 1
        and _garden_admin_count(db, garden_id) <= 1
    ):
        raise HTTPException(status_code=409, detail="Garden must retain at least one admin")

    db.execute(
        "DELETE FROM garden_memberships WHERE garden_id = %s AND user_id = %s",
        (garden_id, user_id),
    )
    _revoke_calendar_subscriptions_for_user(
        db,
        garden_id=garden_id,
        user_id=user_id,
        now_ms=current_timestamp_ms(),
    )
    db.commit()
    _audit_membership_change(
        request,
        context,
        _lifecycle_detail(
            "garden.membership.remove",
            garden_id=garden_id,
            user_id=user_id,
            username=str(membership["username"]),
            role=str(membership["role"]),
            action_reason=action_reason,
        ),
        garden_id=garden_id,
        db=db,
    )
    return {"status": "ok"}


@router.get("/gardens/{garden_id}/invitations")
def list_garden_invitations(
    garden_id: int,
    request: Request,
    db: DB,
) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    context = _auth_context(request)
    _require_platform_admin(context)
    _require_garden_exists(db, garden_id)
    rows = db.execute(
        """
        SELECT
            id,
            garden_id,
            invitee_username,
            role,
            created_by_user_id,
            created_at_ms,
            expires_at_ms,
            accepted_at_ms,
            accepted_user_id,
            revoked_at_ms
        FROM garden_invitations
        WHERE garden_id = %s
        ORDER BY created_at_ms DESC, id DESC
        """,
        (garden_id,),
    ).fetchall()
    now_ms = current_timestamp_ms()
    return {
        "garden_id": garden_id,
        "invitations": [_serialize_invitation(row, now_ms=now_ms) for row in rows],
    }


@router.post("/gardens/{garden_id}/invitations", status_code=201)
def create_garden_invitation(
    garden_id: int,
    body: CreateGardenInvitationBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    _enforce_lifecycle_rate_limit(
        request,
        bucket="garden-invitation-create",
        env_name="GARDEN_INVITATION_CREATE_RATE_LIMIT",
    )
    context, action_reason = enforce_destructive_admin_controls(
        request,
        body_reason=body.action_reason,
    )
    _require_garden_exists(db, garden_id)
    invitee_username = body.invitee_username.strip()
    if not invitee_username:
        raise HTTPException(status_code=400, detail="Invitee username is required")

    now_ms = current_timestamp_ms()
    ttl_minutes = _invitation_ttl_minutes(body.expires_in_minutes)
    expires_at_ms = now_ms + (ttl_minutes * 60 * 1000)
    token = secrets.token_urlsafe(48)
    token_hash = sha256(token.encode("utf-8")).hexdigest()

    # Keep only one active invite per garden/user pair.
    db.execute(
        """
        UPDATE garden_invitations
        SET revoked_at_ms = %s
        WHERE garden_id = %s
          AND invitee_username = %s
          AND accepted_at_ms IS NULL
          AND revoked_at_ms IS NULL
          AND expires_at_ms > %s
        """,
        (
            now_ms,
            garden_id,
            invitee_username,
            now_ms,
        ),
    )
    row = db.execute(
        """
        INSERT INTO garden_invitations (
            garden_id,
            invitee_username,
            role,
            token_hash,
            created_by_user_id,
            created_at_ms,
            expires_at_ms
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (
            garden_id,
            invitee_username,
            body.role,
            token_hash,
            context.user_id,
            now_ms,
            expires_at_ms,
        ),
    ).fetchone()
    assert row is not None
    invitation_id = int(row["id"])
    invitation = db.execute(
        """
        SELECT
            id,
            garden_id,
            invitee_username,
            role,
            created_by_user_id,
            created_at_ms,
            expires_at_ms,
            accepted_at_ms,
            accepted_user_id,
            revoked_at_ms
        FROM garden_invitations
        WHERE id = %s
        LIMIT 1
        """,
        (invitation_id,),
    ).fetchone()
    if not invitation:
        raise HTTPException(status_code=500, detail="Failed to create invitation")
    db.commit()
    _audit_membership_change(
        request,
        context,
        _lifecycle_detail(
            "garden.invitation.create",
            garden_id=garden_id,
            invitation_id=invitation_id,
            invitee_username=invitee_username,
            role=body.role,
            ttl_minutes=ttl_minutes,
            action_reason=action_reason,
        ),
        garden_id=garden_id,
        db=db,
    )
    return {
        "status": "ok",
        "invite_token": token,
        "invitation": _serialize_invitation(invitation, now_ms=now_ms),
    }


@router.delete("/gardens/{garden_id}/invitations/{invitation_id}")
def revoke_garden_invitation(
    garden_id: int,
    invitation_id: int,
    request: Request,
    db: DB,
) -> dict[str, object]:
    _require_user_lifecycle_enabled()
    _enforce_lifecycle_rate_limit(
        request,
        bucket="garden-invitation-revoke",
        env_name="GARDEN_INVITATION_REVOKE_RATE_LIMIT",
    )
    context, action_reason = enforce_destructive_admin_controls(request)
    _require_garden_exists(db, garden_id)
    invitation = db.execute(
        """
        SELECT
            id,
            invitee_username,
            role,
            accepted_at_ms,
            revoked_at_ms
        FROM garden_invitations
        WHERE id = %s AND garden_id = %s
        LIMIT 1
        """,
        (invitation_id, garden_id),
    ).fetchone()
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")
    if invitation["accepted_at_ms"] is not None:
        raise HTTPException(status_code=409, detail="Invitation already accepted")

    revoked_at_ms = invitation["revoked_at_ms"]
    if revoked_at_ms is None:
        revoked_at_ms = current_timestamp_ms()
        db.execute(
            "UPDATE garden_invitations SET revoked_at_ms = %s WHERE id = %s",
            (revoked_at_ms, invitation_id),
        )
        db.commit()
    _audit_membership_change(
        request,
        context,
        _lifecycle_detail(
            "garden.invitation.revoke",
            garden_id=garden_id,
            invitation_id=invitation_id,
            invitee_username=str(invitation["invitee_username"]),
            role=str(invitation["role"]),
            action_reason=action_reason,
        ),
        garden_id=garden_id,
        db=db,
    )
    return {
        "status": "ok",
        "invitation_id": invitation_id,
        "revoked_at_ms": int(revoked_at_ms),
    }


@router.get("/gardens/{garden_id}/settings")
def get_garden_settings(
    garden_id: int,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_garden_exists(db, garden_id)
    # Any member can read settings.
    if not _is_platform_admin(context) and not _is_local_admin_fallback(context):
        if context.user_id is None:
            raise HTTPException(status_code=404, detail="Garden not found")
        membership = db.execute(
            "SELECT 1 FROM garden_memberships WHERE garden_id = %s AND user_id = %s LIMIT 1",
            (garden_id, context.user_id),
        ).fetchone()
        if not membership:
            raise HTTPException(status_code=404, detail="Garden not found")
    row = db.execute(
        """
        SELECT name, grid_rows, grid_cols, latitude, longitude,
               address, onboarding_complete
        FROM gardens WHERE id = %s LIMIT 1
        """,
        (garden_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Garden not found")
    return {
        "garden_id": garden_id,
        "name": str(row["name"]),
        "grid_rows": int(row["grid_rows"]),
        "grid_cols": int(row["grid_cols"]),
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "address": str(row["address"] or ""),
        "onboarding_complete": bool(row["onboarding_complete"]),
    }


@router.get("/gardens/{garden_id}/geocode")
def geocode_garden_location(
    garden_id: int,
    request: Request,
    db: DB,
    q: str = Query(..., min_length=2, max_length=500),
) -> dict[str, object]:
    context = _auth_context(request)
    _require_membership_editor(db, context=context, garden_id=garden_id)
    enforce_rate_limit(
        request,
        bucket="garden-geocode",
        limit=env_int("GARDEN_GEOCODE_RATE_LIMIT", 20),
        window_seconds=60,
    )
    query = q.strip()
    if len(query) < 2:
        raise HTTPException(status_code=400, detail="Location query is too short")
    return {"results": _geocode_query(query)}


@router.get("/gardens/{garden_id}/lidar")
def get_garden_lidar_status(
    garden_id: int,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_garden_exists(db, garden_id)
    if not _is_platform_admin(context) and not _is_local_admin_fallback(context):
        if context.user_id is None:
            raise HTTPException(status_code=404, detail="Garden not found")
        membership = db.execute(
            "SELECT 1 FROM garden_memberships WHERE garden_id = %s AND user_id = %s LIMIT 1",
            (garden_id, context.user_id),
        ).fetchone()
        if not membership:
            raise HTTPException(status_code=404, detail="Garden not found")
    return {"garden_id": garden_id, **local_terrain_storage_info(garden_id)}


@router.post("/gardens/{garden_id}/lidar", status_code=201)
async def upload_garden_lidar(
    garden_id: int,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_membership_editor(db, context=context, garden_id=garden_id)
    enforce_rate_limit(
        request,
        bucket="garden-lidar-upload",
        limit=env_int("GARDEN_LIDAR_UPLOAD_RATE_LIMIT", 4),
        window_seconds=60,
    )
    max_bytes = lidar_upload_max_bytes()
    content_length_raw = request.headers.get("content-length", "").strip()
    if content_length_raw:
        try:
            if int(content_length_raw) > max_bytes:
                raise HTTPException(status_code=413, detail="LiDAR upload exceeds size limit")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header") from None
    payload = await read_body_limited(request, max_bytes)
    filename = request.headers.get("x-upload-filename", "").strip()
    try:
        status = save_uploaded_terrain(garden_id, payload, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _invalidate_garden_terrain_state(db, garden_id)
    db.commit()
    notify_garden_modified()
    _audit_membership_change(
        request,
        context,
        _lifecycle_detail("garden.lidar.upload", garden_id=garden_id, filename=filename[:120]),
        garden_id=garden_id,
        db=db,
    )
    return {"garden_id": garden_id, **status}


@router.delete("/gardens/{garden_id}/lidar")
def delete_garden_lidar(
    garden_id: int,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_membership_editor(db, context=context, garden_id=garden_id)
    status = clear_uploaded_terrain(garden_id)
    _invalidate_garden_terrain_state(db, garden_id)
    db.commit()
    notify_garden_modified()
    _audit_membership_change(
        request,
        context,
        _lifecycle_detail("garden.lidar.delete", garden_id=garden_id),
        garden_id=garden_id,
        db=db,
    )
    return {"garden_id": garden_id, **status}


@router.patch("/gardens/{garden_id}/settings")
def update_garden_settings(
    garden_id: int,
    body: GardenSettingsBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_membership_editor(db, context=context, garden_id=garden_id)
    _enforce_lifecycle_rate_limit(
        request,
        bucket="garden-settings-update",
        env_name="GARDEN_SETTINGS_UPDATE_RATE_LIMIT",
    )
    row = db.execute(
        """
        SELECT name, grid_rows, grid_cols, latitude, longitude,
               address, onboarding_complete
        FROM gardens WHERE id = %s
        LIMIT 1
        """,
        (garden_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Garden not found")

    provided = body.model_fields_set
    updates: list[str] = []
    params: list[object] = []
    has_new_rows = "grid_rows" in provided and body.grid_rows is not None
    has_new_cols = "grid_cols" in provided and body.grid_cols is not None
    if has_new_rows:
        assert body.grid_rows is not None
        next_grid_rows = int(body.grid_rows)
    else:
        next_grid_rows = int(row["grid_rows"])
    if has_new_cols:
        assert body.grid_cols is not None
        next_grid_cols = int(body.grid_cols)
    else:
        next_grid_cols = int(row["grid_cols"])

    if "name" in provided:
        if body.name is None:
            raise HTTPException(status_code=400, detail="Garden name cannot be empty")
        normalized_name = _normalize_garden_name(body.name)
        updates.append("name = %s")
        params.append(normalized_name)
    if "grid_rows" in provided:
        if body.grid_rows is None:
            raise HTTPException(status_code=400, detail="Grid rows are required")
        updates.append("grid_rows = %s")
        params.append(body.grid_rows)
    if "grid_cols" in provided:
        if body.grid_cols is None:
            raise HTTPException(status_code=400, detail="Grid columns are required")
        updates.append("grid_cols = %s")
        params.append(body.grid_cols)
    if "latitude" in provided:
        updates.append("latitude = %s")
        params.append(body.latitude)
    if "longitude" in provided:
        updates.append("longitude = %s")
        params.append(body.longitude)
    if "address" in provided:
        updates.append("address = %s")
        params.append(body.address.strip() if body.address else "")
    if "onboarding_complete" in provided:
        updates.append("onboarding_complete = %s")
        params.append(1 if body.onboarding_complete else 0)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    if "grid_rows" in provided or "grid_cols" in provided:
        from gardenops.main import _ensure_garden_plots_fit_grid, set_layout_state

        _ensure_garden_plots_fit_grid(
            db,
            garden_id=garden_id,
            grid_rows=next_grid_rows,
            grid_cols=next_grid_cols,
        )
        house = _current_house_state(
            db,
            garden_id=garden_id,
            grid_rows=next_grid_rows,
            grid_cols=next_grid_cols,
        )
        house_exceeds_rows = house["row"] + house["height"] - 1 > next_grid_rows
        house_exceeds_cols = house["col"] + house["width"] - 1 > next_grid_cols
        if house_exceeds_rows or house_exceeds_cols:
            raise HTTPException(
                status_code=400,
                detail="Grid is too small for the current house layout",
            )
        set_layout_state(
            db,
            {
                "row": house["row"],
                "col": house["col"],
                "width": house["width"],
                "height": house["height"],
                "north_degrees": house["north_degrees"],
                "grid_rows": next_grid_rows,
                "grid_cols": next_grid_cols,
            },
            garden_id=garden_id,
        )
        _invalidate_garden_terrain_state(db, garden_id)

    if {"latitude", "longitude", "address"} & provided:
        _invalidate_garden_terrain_state(db, garden_id)
        db.execute("DELETE FROM weather_cache WHERE garden_id = %s", (garden_id,))

    params.append(garden_id)
    db.execute(
        f"UPDATE gardens SET {', '.join(updates)} WHERE id = %s",  # noqa: S608
        params,
    )
    db.commit()
    notify_garden_modified()
    _audit_membership_change(
        request,
        context,
        _lifecycle_detail(
            "garden.settings.update",
            garden_id=garden_id,
            fields=[u.split(" = ")[0] for u in updates],
        ),
        garden_id=garden_id,
        db=db,
    )
    return get_garden_settings(garden_id, request, db)


@router.post("/gardens/{garden_id}/zones", status_code=201)
def create_zone(
    garden_id: int,
    body: CreateZoneBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_membership_editor(db, context=context, garden_id=garden_id)
    _enforce_lifecycle_rate_limit(
        request,
        bucket="garden-zone-create",
        env_name="GARDEN_ZONE_CREATE_RATE_LIMIT",
    )

    garden = db.execute(
        "SELECT grid_rows, grid_cols FROM gardens WHERE id = %s LIMIT 1",
        (garden_id,),
    ).fetchone()
    if not garden:
        raise HTTPException(status_code=404, detail="Garden not found")
    g_rows = int(garden["grid_rows"])
    g_cols = int(garden["grid_cols"])
    zone_code, zone_name = _normalized_zone_identity(body.zone_code, body.zone_name)
    _validate_zone_bounds(
        start_row=body.start_row,
        start_col=body.start_col,
        end_row=body.end_row,
        end_col=body.end_col,
        grid_rows=g_rows,
        grid_cols=g_cols,
    )
    house = _current_house_state(
        db,
        garden_id=garden_id,
        grid_rows=g_rows,
        grid_cols=g_cols,
    )
    _validate_zone_against_house(
        zone_code=zone_code or body.zone_code,
        start_row=body.start_row,
        start_col=body.start_col,
        end_row=body.end_row,
        end_col=body.end_col,
        house=house,
    )
    owner_user_id = _preferred_plot_owner_user_id(
        db,
        garden_id=garden_id,
        preferred_user_id=context.user_id,
    )
    created = _create_zone_plots(
        db,
        garden_id=garden_id,
        zone_code=zone_code,
        zone_name=zone_name,
        start_row=body.start_row,
        start_col=body.start_col,
        end_row=body.end_row,
        end_col=body.end_col,
        color=body.color,
        owner_user_id=owner_user_id,
    )
    requested_cells = (body.end_row - body.start_row + 1) * (body.end_col - body.start_col + 1)
    db.commit()
    notify_garden_modified()
    _audit_membership_change(
        request,
        context,
        _lifecycle_detail(
            "garden.zone.create",
            garden_id=garden_id,
            zone_code=zone_code,
            zone_name=zone_name,
            plots_created=len(created),
            requested_cells=requested_cells,
        ),
        garden_id=garden_id,
        db=db,
    )
    return {
        "zone_code": zone_code,
        "zone_name": zone_name,
        "plots_created": len(created),
        "requested_cells": requested_cells,
        "skipped_cells": requested_cells - len(created),
        "plots": created,
    }


@router.post("/gardens/{garden_id}/complete-onboarding")
@router.patch("/gardens/{garden_id}/complete-onboarding")
@router.put("/gardens/{garden_id}/complete-onboarding")
def complete_garden_onboarding(
    garden_id: int,
    body: CompleteOnboardingBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    # Keep onboarding completion compatible with older shipped frontend bundles.
    # The current client uses POST, but stale tabs/cached assets may still send
    # PATCH or PUT and would otherwise fail with a misleading 405.
    context = _auth_context(request)
    _require_membership_editor(db, context=context, garden_id=garden_id)
    _enforce_lifecycle_rate_limit(
        request,
        bucket="garden-onboarding-complete",
        env_name="GARDEN_ONBOARDING_COMPLETE_RATE_LIMIT",
        default_limit=10,
    )

    if body.mode == "import" and body.imported_layout is None:
        raise HTTPException(status_code=400, detail="Imported layout is required for import mode")
    normalized_name = _normalize_garden_name(body.name)

    grid_rows, grid_cols = (
        _derive_import_grid(body) if body.mode == "import" else (body.grid_rows, body.grid_cols)
    )
    owner_user_id = _preferred_plot_owner_user_id(
        db,
        garden_id=garden_id,
        preferred_user_id=context.user_id,
    )

    if body.mode == "manual":
        house = (
            body.house.model_dump(exclude_none=True)
            if body.house is not None
            else _default_onboarding_house_state(
                grid_rows=grid_rows,
                grid_cols=grid_cols,
            )
        )
        house["grid_rows"] = grid_rows
        house["grid_cols"] = grid_cols
        for zone in body.zones:
            zone_code, _ = _normalized_zone_identity(zone.zone_code, zone.zone_name)
            _validate_zone_bounds(
                start_row=zone.start_row,
                start_col=zone.start_col,
                end_row=zone.end_row,
                end_col=zone.end_col,
                grid_rows=grid_rows,
                grid_cols=grid_cols,
            )
            _validate_zone_against_house(
                zone_code=zone_code,
                start_row=zone.start_row,
                start_col=zone.start_col,
                end_row=zone.end_row,
                end_col=zone.end_col,
                house=house,
            )

    db.commit()
    media_storage_pairs: list[tuple[str, str]] = []
    try:
        existing_garden = db.execute(
            """
            SELECT onboarding_complete
            FROM gardens
            WHERE id = %s
            FOR UPDATE
            """,
            (garden_id,),
        ).fetchone()
        if existing_garden is None:
            raise HTTPException(status_code=404, detail="Garden not found")
        if bool(int(existing_garden["onboarding_complete"])):
            raise HTTPException(status_code=409, detail="Garden onboarding is already complete")

        db.execute(
            """
            UPDATE gardens
            SET name = %s, grid_rows = %s, grid_cols = %s, latitude = %s, longitude = %s,
                address = %s, onboarding_complete = 1
            WHERE id = %s
            """,
            (
                normalized_name,
                grid_rows,
                grid_cols,
                body.latitude,
                body.longitude,
                body.address.strip(),
                garden_id,
            ),
        )
        if body.mode == "import":
            from gardenops.main import restore_snapshot_data

            imported_layout = body.imported_layout
            assert imported_layout is not None
            plots_created = restore_snapshot_data(
                db,
                [item.model_dump(exclude_none=True) for item in imported_layout.plots],
                garden_id=garden_id,
                owner_user_id=owner_user_id,
                house=(
                    imported_layout.house.model_dump(exclude_none=True)
                    if imported_layout.house is not None
                    else None
                ),
                shademap=(
                    imported_layout.shademap.model_dump(exclude_none=True)
                    if imported_layout.shademap is not None
                    else None
                ),
                shademap_calibration=(
                    imported_layout.shademap_calibration.model_dump(exclude_none=True)
                    if imported_layout.shademap_calibration is not None
                    else None
                ),
                shademap_obstacles=(
                    [
                        item.model_dump(exclude_none=True)
                        for item in imported_layout.shademap_obstacles
                    ]
                    if imported_layout.shademap_obstacles is not None
                    else None
                ),
                manage_transaction=False,
                media_storage_pairs_out=media_storage_pairs,
            )
        else:
            from gardenops.main import set_layout_state

            media_storage_pairs.extend(_clear_garden_plot_state(db, garden_id=garden_id))
            house = (
                body.house.model_dump(exclude_none=True)
                if body.house is not None
                else _default_onboarding_house_state(
                    grid_rows=grid_rows,
                    grid_cols=grid_cols,
                )
            )
            house["grid_rows"] = grid_rows
            house["grid_cols"] = grid_cols
            set_layout_state(db, house, garden_id=garden_id)
            plots_created = 0
            for zone in body.zones:
                zone_code, zone_name = _normalized_zone_identity(zone.zone_code, zone.zone_name)
                created = _create_zone_plots(
                    db,
                    garden_id=garden_id,
                    zone_code=zone_code,
                    zone_name=zone_name,
                    start_row=zone.start_row,
                    start_col=zone.start_col,
                    end_row=zone.end_row,
                    end_col=zone.end_col,
                    color=zone.color,
                    owner_user_id=owner_user_id,
                )
                plots_created += len(created)
        db.commit()
        for storage_key, preview_storage_key in media_storage_pairs:
            unlink_storage_keys(storage_key, preview_storage_key)
    except Exception:
        db.rollback()
        raise

    ensure_indoor_plot(db, garden_id, owner_user_id=context.user_id)

    notify_garden_modified()
    _audit_membership_change(
        request,
        context,
        _lifecycle_detail(
            "garden.onboarding.complete",
            garden_id=garden_id,
            mode=body.mode,
            plots_created=plots_created,
            grid_rows=grid_rows,
            grid_cols=grid_cols,
        ),
        garden_id=garden_id,
        db=db,
    )
    return {
        **get_garden_settings(garden_id, request, db),
        "mode": body.mode,
        "plots_created": plots_created,
    }
