from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import Field

from gardenops.db import DB, DbConn, current_timestamp_ms, get_db, return_db
from gardenops.models import StrictBaseModel
from gardenops.rate_limit import enforce_rate_limit, env_int
from gardenops.request_body import read_body_limited
from gardenops.router_helpers import (
    active_garden_id as _active_garden_id,
)
from gardenops.router_helpers import (
    auth_context as _auth_context,
)
from gardenops.router_helpers import (
    effective_role as _effective_role,
)
from gardenops.router_helpers import (
    is_local_admin_fallback as _is_local_admin_fallback,
)
from gardenops.router_helpers import (
    is_owner_or_admin as _is_owner_or_admin,
)
from gardenops.router_helpers import (
    require_write as _require_write,
)
from gardenops.routers.auth import enforce_destructive_admin_controls
from gardenops.security import AuthContext
from gardenops.security_metrics import record_security_event
from gardenops.services.media_store import (
    PreparedMediaAsset,
    collect_orphaned_media_storage_keys,
    media_garden_max_assets,
    media_garden_max_bytes,
    media_upload_max_bytes,
    persist_prepared_media,
    prepare_media_asset,
    resolve_storage_key,
    unlink_storage_keys,
)
from gardenops.services.plant_cover_import import discover_cover_from_plant_link

router = APIRouter()
TargetType = Literal["journal_entry", "plant", "plot", "issue", "harvest_entry"]
MissingCoverStatusCode = Literal["missing_latin", "missing_link", "remote_error"]


class CreateMediaLinkBody(StrictBaseModel):
    target_type: TargetType
    target_id: str = Field(min_length=1, max_length=120)


class ListMediaSummariesBody(StrictBaseModel):
    target_type: TargetType
    target_ids: list[str] = Field(min_length=1, max_length=120)


class SetPlantCoverBody(StrictBaseModel):
    asset_id: str = Field(min_length=1, max_length=120)


class PopulateMissingPlantCoversBody(StrictBaseModel):
    cursor: str = Field(default="", max_length=120)
    max_plants: int = Field(default=15, ge=1, le=25)
    action_reason: str = Field(default="", max_length=400)


def _require_platform_admin(context: AuthContext) -> None:
    if context.role != "admin":
        raise HTTPException(status_code=403, detail="Platform admin required")


def _canonical_target_id(target_type: TargetType, target_id: str) -> str:
    raw = str(target_id).strip()
    if not raw:
        raise HTTPException(status_code=422, detail="Target ID is required")
    return raw[:120]


def _can_read_owned_target(context: AuthContext, owner_user_id: int | None) -> bool:
    if _is_local_admin_fallback(context) or _effective_role(context) in {"admin", "editor"}:
        return True
    return _is_owner_or_admin(context, owner_user_id)


def _readable_media_link_sql(
    context: AuthContext,
    *,
    garden_id: int,
    link_alias: str = "l",
) -> tuple[str, list[object]]:
    elevated = (
        1
        if _is_local_admin_fallback(context)
        or _effective_role(context)
        in {
            "admin",
            "editor",
        }
        else 0
    )
    user_id = int(context.user_id) if context.user_id is not None else -1
    return (
        f"""
        (
            {link_alias}.target_type NOT IN ('plant', 'plot')
            OR %s = 1
            OR (
                {link_alias}.target_type = 'plant'
                AND EXISTS (
                    SELECT 1
                    FROM plant_ownership po
                    WHERE po.plt_id = {link_alias}.target_id
                      AND po.garden_id = %s
                      AND (%s = 1 OR po.owner_user_id = %s)
                )
            )
            OR (
                {link_alias}.target_type = 'plot'
                AND EXISTS (
                    SELECT 1
                    FROM plot_ownership plo
                    WHERE plo.plot_id = {link_alias}.target_id
                      AND plo.garden_id = %s
                      AND (%s = 1 OR plo.owner_user_id = %s)
                )
            )
        )
        """,
        [elevated, garden_id, elevated, user_id, garden_id, elevated, user_id],
    )


def _media_link_is_readable(
    db: DbConn,
    *,
    context: AuthContext,
    garden_id: int,
    target_type: str,
    target_id: str,
) -> bool:
    if target_type == "plant":
        row = db.execute(
            """
            SELECT owner_user_id
            FROM plant_ownership
            WHERE plt_id = %s AND garden_id = %s
            """,
            (target_id, garden_id),
        ).fetchone()
        return bool(row and _can_read_owned_target(context, row["owner_user_id"]))
    if target_type == "plot":
        row = db.execute(
            """
            SELECT owner_user_id
            FROM plot_ownership
            WHERE plot_id = %s AND garden_id = %s
            """,
            (target_id, garden_id),
        ).fetchone()
        return bool(row and _can_read_owned_target(context, row["owner_user_id"]))
    return True


def _filter_readable_target_ids(
    db: DbConn,
    *,
    context: AuthContext,
    garden_id: int,
    target_type: TargetType,
    target_ids: list[str],
) -> list[str]:
    return [
        target_id
        for target_id in target_ids
        if _media_link_is_readable(
            db,
            context=context,
            garden_id=garden_id,
            target_type=target_type,
            target_id=target_id,
        )
    ]


def _asset_has_readable_link(
    db: DbConn,
    *,
    context: AuthContext,
    garden_id: int,
    asset_id: str,
) -> bool:
    rows = db.execute(
        """
        SELECT target_type, target_id
        FROM media_links
        WHERE asset_id = %s
        """,
        (asset_id,),
    ).fetchall()
    return any(
        _media_link_is_readable(
            db,
            context=context,
            garden_id=garden_id,
            target_type=str(row["target_type"]),
            target_id=str(row["target_id"]),
        )
        for row in rows
    )


def _validate_media_target(
    db: DbConn,
    *,
    context: AuthContext,
    target_type: TargetType,
    target_id: str,
) -> str:
    garden_id = _active_garden_id(context)
    canonical_id = _canonical_target_id(target_type, target_id)
    allow_global = _is_local_admin_fallback(context)
    if target_type == "journal_entry":
        row = db.execute(
            """
            SELECT 1
            FROM garden_journal_entries
            WHERE public_id = %s AND garden_id = %s
            """,
            (canonical_id, garden_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Journal entry not found in active garden")
        return canonical_id
    if target_type == "issue":
        row = db.execute(
            """
            SELECT 1
            FROM garden_issues
            WHERE public_id = %s AND garden_id = %s
            """,
            (canonical_id, garden_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Issue not found in active garden")
        return canonical_id
    if target_type == "harvest_entry":
        row = db.execute(
            """
            SELECT 1
            FROM harvest_entries
            WHERE public_id = %s AND garden_id = %s
            """,
            (canonical_id, garden_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Harvest entry not found in active garden")
        return canonical_id
    if target_type == "plant":
        row = db.execute(
            """
            SELECT owner_user_id
            FROM plant_ownership
            WHERE plt_id = %s AND garden_id = %s
            """,
            (canonical_id, garden_id),
        ).fetchone()
        if row and _can_read_owned_target(context, row["owner_user_id"]):
            return canonical_id
        if allow_global:
            global_row = db.execute(
                "SELECT 1 FROM plants WHERE plt_id = %s",
                (canonical_id,),
            ).fetchone()
            if global_row:
                return canonical_id
        raise HTTPException(status_code=404, detail="Plant not found in active garden")
    row = db.execute(
        """
        SELECT owner_user_id
        FROM plot_ownership
        WHERE plot_id = %s AND garden_id = %s
        """,
        (canonical_id, garden_id),
    ).fetchone()
    if row and _can_read_owned_target(context, row["owner_user_id"]):
        return canonical_id
    if allow_global:
        global_row = db.execute(
            "SELECT 1 FROM plots WHERE plot_id = %s",
            (canonical_id,),
        ).fetchone()
        if global_row:
            return canonical_id
    raise HTTPException(status_code=404, detail="Plot not found in active garden")


def _garden_media_usage(db: DbConn, garden_id: int) -> tuple[int, int]:
    row = db.execute(
        """
        SELECT COUNT(*) AS asset_count, COALESCE(SUM(bytes), 0) AS total_bytes
        FROM media_assets
        WHERE garden_id = %s
        """,
        (garden_id,),
    ).fetchone()
    if not row:
        return 0, 0
    return int(row["asset_count"] or 0), int(row["total_bytes"] or 0)


def _enforce_media_quota(
    db: DbConn,
    *,
    garden_id: int,
    incoming_asset: PreparedMediaAsset,
) -> None:
    asset_count, total_bytes = _garden_media_usage(db, garden_id)
    if asset_count >= media_garden_max_assets():
        record_security_event("media_quota_rejections")
        raise HTTPException(status_code=413, detail="Garden media asset quota exceeded")
    if total_bytes + incoming_asset.bytes > media_garden_max_bytes():
        record_security_event("media_quota_rejections")
        raise HTTPException(status_code=413, detail="Garden media byte quota exceeded")


def _fetch_asset_row(
    db: DbConn,
    *,
    garden_id: int,
    asset_id: str,
) -> dict[str, Any]:
    row = db.execute(
        """
        SELECT *
        FROM media_assets
        WHERE asset_id = %s AND garden_id = %s
        """,
        (asset_id, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Media asset not found")
    return row


def _plant_cover_asset_map(
    db: DbConn,
    *,
    garden_id: int,
    plt_ids: list[str],
) -> dict[str, str]:
    if not plt_ids:
        return {}
    placeholders = ",".join(["%s"] * len(plt_ids))
    rows = db.execute(
        f"""
        SELECT c.plt_id, c.asset_id
        FROM plant_media_covers c
        JOIN media_assets a
          ON a.asset_id = c.asset_id
         AND a.garden_id = c.garden_id
        JOIN media_links l
          ON l.asset_id = c.asset_id
         AND l.target_type = 'plant'
         AND l.target_id = c.plt_id
        WHERE c.garden_id = %s AND c.plt_id IN ({placeholders})
        """,  # noqa: S608
        [garden_id, *plt_ids],
    ).fetchall()
    return {str(row["plt_id"]): str(row["asset_id"]) for row in rows}


def _plant_cover_asset_id(
    db: DbConn,
    *,
    garden_id: int,
    plt_id: str,
) -> str | None:
    return _plant_cover_asset_map(db, garden_id=garden_id, plt_ids=[plt_id]).get(plt_id)


def _clear_plant_cover(
    db: DbConn,
    *,
    garden_id: int,
    plt_id: str,
    asset_id: str | None = None,
) -> None:
    if asset_id:
        db.execute(
            """
            DELETE FROM plant_media_covers
            WHERE garden_id = %s AND plt_id = %s AND asset_id = %s
            """,
            (garden_id, plt_id, asset_id),
        )
        return
    db.execute(
        """
        DELETE FROM plant_media_covers
        WHERE garden_id = %s AND plt_id = %s
        """,
        (garden_id, plt_id),
    )


def _clear_plant_cover_import_status(
    db: DbConn,
    *,
    garden_id: int,
    plt_id: str,
) -> None:
    db.execute(
        """
        DELETE FROM plant_cover_import_status
        WHERE garden_id = %s AND plt_id = %s
        """,
        (garden_id, plt_id),
    )


def _upsert_plant_cover_import_status(
    db: DbConn,
    *,
    garden_id: int,
    plt_id: str,
    status_code: MissingCoverStatusCode,
    detail: str,
) -> None:
    db.execute(
        """
        INSERT INTO plant_cover_import_status (
            garden_id,
            plt_id,
            status_code,
            detail,
            attempted_at_ms
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT(garden_id, plt_id) DO UPDATE SET
            status_code = excluded.status_code,
            detail = excluded.detail,
            attempted_at_ms = excluded.attempted_at_ms
        """,
        (
            garden_id,
            plt_id,
            status_code,
            detail.strip()[:500],
            current_timestamp_ms(),
        ),
    )


def _set_plant_cover(
    db: DbConn,
    *,
    garden_id: int,
    plt_id: str,
    asset_id: str,
    actor_user_id: int | None,
) -> None:
    linked = db.execute(
        """
        SELECT 1
        FROM media_links l
        JOIN media_assets a ON a.asset_id = l.asset_id
        WHERE a.garden_id = %s
          AND l.asset_id = %s
          AND l.target_type = 'plant'
          AND l.target_id = %s
        LIMIT 1
        """,
        (garden_id, asset_id, plt_id),
    ).fetchone()
    if not linked:
        raise HTTPException(status_code=404, detail="Media asset is not linked to this plant")
    db.execute(
        """
        INSERT INTO plant_media_covers (
            garden_id,
            plt_id,
            asset_id,
            set_at_ms,
            set_by_user_id
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT(garden_id, plt_id) DO UPDATE SET
            asset_id = excluded.asset_id,
            set_at_ms = excluded.set_at_ms,
            set_by_user_id = excluded.set_by_user_id
        """,
        (garden_id, plt_id, asset_id, current_timestamp_ms(), actor_user_id),
    )
    _clear_plant_cover_import_status(db, garden_id=garden_id, plt_id=plt_id)


def _latest_plant_asset_row(
    db: DbConn,
    *,
    garden_id: int,
    plt_id: str,
) -> dict[str, Any] | None:
    return db.execute(
        """
        SELECT a.*
        FROM media_links l
        JOIN media_assets a ON a.asset_id = l.asset_id
        WHERE a.garden_id = %s
          AND l.target_type = 'plant'
          AND l.target_id = %s
        ORDER BY a.created_at_ms DESC, a.asset_id DESC
        LIMIT 1
        """,
        (garden_id, plt_id),
    ).fetchone()


def _insert_prepared_asset_link(
    db: DbConn,
    *,
    garden_id: int,
    prepared: PreparedMediaAsset,
    actor_user_id: int | None,
    target_type: TargetType,
    target_id: str,
    auto_set_plant_cover: bool = False,
) -> None:
    db.execute(
        """
        INSERT INTO media_assets (
            asset_id,
            garden_id,
            storage_key,
            preview_storage_key,
            original_filename,
            mime_type,
            bytes,
            width,
            height,
            created_at_ms,
            actor_user_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            prepared.asset_id,
            garden_id,
            prepared.storage_key,
            prepared.preview_storage_key,
            prepared.original_filename,
            prepared.mime_type,
            prepared.bytes,
            prepared.width,
            prepared.height,
            current_timestamp_ms(),
            actor_user_id,
        ),
    )
    db.execute(
        """
        INSERT INTO media_links (asset_id, target_type, target_id, sort_order)
        VALUES (%s, %s, %s, 0)
        """,
        (prepared.asset_id, target_type, target_id),
    )
    if target_type == "plant" and auto_set_plant_cover:
        if _plant_cover_asset_id(db, garden_id=garden_id, plt_id=target_id) is None:
            _set_plant_cover(
                db,
                garden_id=garden_id,
                plt_id=target_id,
                asset_id=prepared.asset_id,
                actor_user_id=actor_user_id,
            )


def _asset_links_by_asset_id(
    db: DbConn,
    asset_ids: list[str],
    *,
    context: AuthContext | None = None,
    garden_id: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    if not asset_ids:
        return {}
    if context is not None and garden_id is None:
        garden_id = _active_garden_id(context)
    placeholders = ",".join(["%s"] * len(asset_ids))
    rows = db.execute(
        f"""
        SELECT asset_id, target_type, target_id, sort_order
        FROM media_links
        WHERE asset_id IN ({placeholders})
        ORDER BY asset_id, sort_order, target_type, target_id
        """,  # noqa: S608
        asset_ids,
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if (
            context is not None
            and garden_id is not None
            and not _media_link_is_readable(
                db,
                context=context,
                garden_id=garden_id,
                target_type=str(row["target_type"]),
                target_id=str(row["target_id"]),
            )
        ):
            continue
        asset_id = str(row["asset_id"])
        grouped.setdefault(asset_id, []).append(row)
    return grouped


def _serialize_asset_payload(
    row: dict[str, Any],
    *,
    links: list[dict[str, Any]],
    is_cover: bool = False,
) -> dict[str, object]:
    asset_id = str(row["asset_id"])
    return {
        "asset_id": asset_id,
        "mime_type": str(row["mime_type"]),
        "bytes": int(row["bytes"]),
        "width": int(row["width"]),
        "height": int(row["height"]),
        "created_at_ms": int(row["created_at_ms"]),
        "actor_user_id": int(row["actor_user_id"]) if row["actor_user_id"] is not None else None,
        "original_filename": str(row["original_filename"] or ""),
        "preview_url": f"/api/media/{asset_id}/preview",
        "original_url": f"/api/media/{asset_id}",
        "is_cover": bool(is_cover),
        "targets": [
            {
                "target_type": str(link["target_type"]),
                "target_id": str(link["target_id"]),
                "sort_order": int(link["sort_order"]),
            }
            for link in links
        ],
    }


def _serialize_asset_row(
    db: DbConn,
    row: dict[str, Any],
    *,
    context: AuthContext | None = None,
    garden_id: int | None = None,
    is_cover: bool = False,
) -> dict[str, object]:
    asset_id = str(row["asset_id"])
    links_by_asset_id = _asset_links_by_asset_id(
        db,
        [asset_id],
        context=context,
        garden_id=garden_id,
    )
    return _serialize_asset_payload(
        row,
        links=links_by_asset_id.get(asset_id, []),
        is_cover=is_cover,
    )


def _media_file_response_data(
    request: Request,
    *,
    asset_id: str,
    preview: bool,
) -> tuple[str, Path]:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    conn = get_db()
    try:
        row = _fetch_asset_row(conn, garden_id=garden_id, asset_id=asset_id)
        if not _asset_has_readable_link(
            conn,
            context=context,
            garden_id=garden_id,
            asset_id=asset_id,
        ):
            raise HTTPException(status_code=404, detail="Media asset not found")
        storage_key_field = "preview_storage_key" if preview else "storage_key"
        path = resolve_storage_key(str(row[storage_key_field]))
        if not path.exists():
            detail = "Media preview not found" if preview else "Media asset file not found"
            raise HTTPException(status_code=404, detail=detail)
        return str(row["mime_type"]), path
    finally:
        return_db(conn)


def _canonicalize_target_ids(target_type: TargetType, target_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    canonical_ids: list[str] = []
    for raw in target_ids:
        canonical_id = _canonical_target_id(target_type, raw)
        if canonical_id in seen:
            continue
        seen.add(canonical_id)
        canonical_ids.append(canonical_id)
    return canonical_ids


@router.get("/media")
def list_media_assets(
    request: Request,
    db: DB,
    target_type: Annotated[TargetType | None, Query()] = None,
    target_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    params: list[object] = [garden_id]
    where = ["a.garden_id = %s"]
    canonical_target_id: str | None = None
    cover_asset_id: str | None = None
    if target_type is not None:
        if not target_id:
            raise HTTPException(
                status_code=422,
                detail="target_id is required when target_type is set",
            )
        canonical_target_id = _validate_media_target(
            db,
            context=context,
            target_type=target_type,
            target_id=target_id,
        )
        where.append("l.target_type = %s")
        where.append("l.target_id = %s")
        params.extend([target_type, canonical_target_id])
        if target_type == "plant":
            cover_asset_id = _plant_cover_asset_id(
                db,
                garden_id=garden_id,
                plt_id=canonical_target_id,
            )
    readable_sql, readable_params = _readable_media_link_sql(
        context,
        garden_id=garden_id,
        link_alias="l",
    )
    where.append(readable_sql)
    params.extend(readable_params)

    where_sql = " AND ".join(where)
    total_row = db.execute(
        f"""
        SELECT COUNT(DISTINCT a.asset_id) AS c
        FROM media_assets a
        JOIN media_links l ON l.asset_id = a.asset_id
        WHERE {where_sql}
        """,
        params,
    ).fetchone()
    total = int(total_row["c"] or 0) if total_row else 0
    cover_select = ""
    order_by_sql = "_sort_priority, a.created_at_ms DESC, a.asset_id DESC"
    list_params: list[object] = [*params]
    if target_type == "plant" and canonical_target_id and cover_asset_id:
        cover_select = ", CASE WHEN a.asset_id = %s THEN 0 ELSE 1 END"
        list_params = [cover_asset_id, *params]
    else:
        cover_select = ", 1"
    rows = db.execute(
        f"""
        SELECT DISTINCT a.*{cover_select} AS _sort_priority
        FROM media_assets a
        JOIN media_links l ON l.asset_id = a.asset_id
        WHERE {where_sql}
        ORDER BY {order_by_sql}
        LIMIT %s OFFSET %s
        """,
        [*list_params, limit, offset],
    ).fetchall()
    links_by_asset_id = _asset_links_by_asset_id(
        db,
        [str(row["asset_id"]) for row in rows],
        context=context,
        garden_id=garden_id,
    )
    return {
        "items": [
            _serialize_asset_payload(
                row,
                links=links_by_asset_id.get(str(row["asset_id"]), []),
                is_cover=bool(
                    target_type == "plant"
                    and canonical_target_id
                    and str(row["asset_id"]) == cover_asset_id
                ),
            )
            for row in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/media/summaries")
def list_media_summaries(
    body: ListMediaSummariesBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    canonical_ids = _canonicalize_target_ids(body.target_type, body.target_ids)
    canonical_ids = _filter_readable_target_ids(
        db,
        context=context,
        garden_id=garden_id,
        target_type=body.target_type,
        target_ids=canonical_ids,
    )
    if not canonical_ids:
        return {"target_type": body.target_type, "items": []}
    placeholders = ",".join(["%s"] * len(canonical_ids))
    asset_columns = """
        a.asset_id,
        a.mime_type,
        a.bytes,
        a.width,
        a.height,
        a.created_at_ms,
        a.actor_user_id,
        a.original_filename
    """
    if body.target_type == "plant":
        rows = db.execute(
            f"""
            SELECT DISTINCT ON (l.target_id)
                l.target_id,
                {asset_columns},
                CASE WHEN c.asset_id = a.asset_id THEN 1 ELSE 0 END AS is_cover
            FROM media_links l
            JOIN media_assets a ON a.asset_id = l.asset_id
            LEFT JOIN plant_media_covers c
              ON c.garden_id = a.garden_id
             AND c.plt_id = l.target_id
            WHERE a.garden_id = %s
              AND l.target_type = 'plant'
              AND l.target_id IN ({placeholders})
            ORDER BY
                l.target_id,
                CASE WHEN c.asset_id = a.asset_id THEN 0 ELSE 1 END,
                a.created_at_ms DESC,
                a.asset_id DESC
            """,  # noqa: S608
            [garden_id, *canonical_ids],
        ).fetchall()
    else:
        rows = db.execute(
            f"""
            SELECT DISTINCT ON (l.target_id)
                l.target_id,
                {asset_columns},
                0 AS is_cover
            FROM media_links l
            JOIN media_assets a ON a.asset_id = l.asset_id
            WHERE a.garden_id = %s
              AND l.target_type = %s
              AND l.target_id IN ({placeholders})
            ORDER BY l.target_id, a.created_at_ms DESC, a.asset_id DESC
            """,  # noqa: S608
            [garden_id, body.target_type, *canonical_ids],
        ).fetchall()
    links_by_asset_id = _asset_links_by_asset_id(
        db,
        [str(row["asset_id"]) for row in rows],
        context=context,
        garden_id=garden_id,
    )
    items = [
        {
            "target_id": str(row["target_id"]),
            "asset": _serialize_asset_payload(
                row,
                links=links_by_asset_id.get(str(row["asset_id"]), []),
                is_cover=bool(row["is_cover"]),
            ),
        }
        for row in rows
    ]
    return {
        "target_type": body.target_type,
        "items": items,
    }


@router.get("/media/plants/missing-covers")
def list_missing_plant_covers(
    request: Request,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_platform_admin(context)
    garden_id = _active_garden_id(context)
    allow_global = _is_local_admin_fallback(context)
    ownership_predicate = "(%s = 1 OR o.plt_id IS NOT NULL)"
    params: list[object] = [garden_id, garden_id, 1 if allow_global else 0]
    total_row = db.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM plants p
        LEFT JOIN plant_media_covers c
          ON c.garden_id = %s
         AND c.plt_id = p.plt_id
        LEFT JOIN plant_ownership o
          ON o.garden_id = %s
         AND o.plt_id = p.plt_id
        WHERE {ownership_predicate}
          AND c.plt_id IS NULL
        """,
        params,
    ).fetchone()
    total = int(total_row["c"] or 0) if total_row else 0
    rows = db.execute(
        f"""
        SELECT
            p.plt_id,
            COALESCE(p.name, '') AS name,
            COALESCE(p.latin, '') AS latin,
            COALESCE(p.link, '') AS link,
            s.status_code,
            COALESCE(s.detail, '') AS status_detail,
            s.attempted_at_ms,
            EXISTS(
                SELECT 1
                FROM media_links l
                JOIN media_assets a ON a.asset_id = l.asset_id
                WHERE a.garden_id = %s
                  AND l.target_type = 'plant'
                  AND l.target_id = p.plt_id
            ) AS has_existing_media
        FROM plants p
        LEFT JOIN plant_media_covers c
          ON c.garden_id = %s
         AND c.plt_id = p.plt_id
        LEFT JOIN plant_ownership o
          ON o.garden_id = %s
         AND o.plt_id = p.plt_id
        LEFT JOIN plant_cover_import_status s
          ON s.garden_id = %s
         AND s.plt_id = p.plt_id
        WHERE {ownership_predicate}
          AND c.plt_id IS NULL
        ORDER BY
            CASE
                WHEN s.status_code IS NOT NULL THEN 0
                WHEN TRIM(COALESCE(p.latin, '')) = '' THEN 1
                WHEN TRIM(COALESCE(p.link, '')) = '' THEN 2
                ELSE 3
            END,
            COALESCE(s.attempted_at_ms, 0) DESC,
            p.plt_id
        LIMIT %s OFFSET %s
        """,
        [
            garden_id,
            garden_id,
            garden_id,
            garden_id,
            1 if allow_global else 0,
            limit,
            offset,
        ],
    ).fetchall()
    items: list[dict[str, object]] = []
    for row in rows:
        link = str(row["link"] or "").strip()
        latin_name = str(row["latin"] or "").strip()
        status_code = str(row["status_code"] or "").strip()
        if not status_code:
            if bool(int(row["has_existing_media"] or 0)):
                status_code = "existing_media_needs_cover"
            elif not latin_name:
                status_code = "missing_latin"
            elif not link:
                status_code = "missing_link"
            else:
                status_code = "ready_remote_import"
        items.append(
            {
                "plant_id": str(row["plt_id"]),
                "name": str(row["name"] or ""),
                "latin": latin_name,
                "link": link,
                "reason_code": status_code,
                "status_detail": str(row["status_detail"] or ""),
                "attempted_at_ms": (
                    int(row["attempted_at_ms"]) if row["attempted_at_ms"] is not None else None
                ),
                "has_existing_media": bool(int(row["has_existing_media"] or 0)),
            }
        )
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/media/upload", status_code=201)
async def upload_media_asset(
    request: Request,
    db: DB,
    target_type: TargetType = Query(...),
    target_id: str = Query(..., min_length=1, max_length=120),
) -> dict[str, object]:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    _validate_media_target(db, context=context, target_type=target_type, target_id=target_id)
    enforce_rate_limit(
        request,
        bucket="media_uploads",
        limit=env_int("MEDIA_UPLOAD_RATE_LIMIT", 12),
        window_seconds=60,
    )
    content_length_raw = request.headers.get("content-length", "").strip()
    if content_length_raw:
        try:
            if int(content_length_raw) > media_upload_max_bytes():
                record_security_event("media_upload_rejections")
                raise HTTPException(status_code=413, detail="Image exceeds upload size limit")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header") from None
    payload = await read_body_limited(request, media_upload_max_bytes())
    declared_content_type = request.headers.get("content-type", "").strip().lower()
    original_filename = request.headers.get("x-upload-filename", "").strip()
    try:
        prepared = prepare_media_asset(
            payload=payload,
            declared_content_type=declared_content_type,
            original_filename=original_filename,
        )
    except HTTPException as exc:
        record_security_event("media_upload_rejections")
        raise exc
    _enforce_media_quota(db, garden_id=garden_id, incoming_asset=prepared)
    persist_prepared_media(prepared)
    canonical_target_id = _canonical_target_id(target_type, target_id)
    try:
        _insert_prepared_asset_link(
            db,
            garden_id=garden_id,
            prepared=prepared,
            actor_user_id=context.user_id,
            target_type=target_type,
            target_id=canonical_target_id,
            auto_set_plant_cover=True,
        )
        db.commit()
    except Exception:
        unlink_storage_keys(prepared.storage_key, prepared.preview_storage_key)
        db.rollback()
        raise
    record_security_event("media_uploads_total")
    row = _fetch_asset_row(db, garden_id=garden_id, asset_id=prepared.asset_id)
    return _serialize_asset_row(db, row, context=context, garden_id=garden_id)


@router.get("/media/{asset_id}/preview")
def get_media_preview(asset_id: str, request: Request) -> FileResponse:
    mime_type, path = _media_file_response_data(request, asset_id=asset_id, preview=True)
    return FileResponse(
        path,
        media_type=mime_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/media/{asset_id}")
def get_media_original(asset_id: str, request: Request) -> FileResponse:
    mime_type, path = _media_file_response_data(request, asset_id=asset_id, preview=False)
    return FileResponse(
        path,
        media_type=mime_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/media/{asset_id}")
def delete_media_asset(asset_id: str, request: Request, db: DB) -> dict[str, object]:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    row = _fetch_asset_row(db, garden_id=garden_id, asset_id=asset_id)
    if not _asset_has_readable_link(
        db,
        context=context,
        garden_id=garden_id,
        asset_id=asset_id,
    ):
        raise HTTPException(status_code=404, detail="Media asset not found")
    db.execute(
        "DELETE FROM media_assets WHERE asset_id = %s AND garden_id = %s",
        (asset_id, garden_id),
    )
    db.commit()
    unlink_storage_keys(str(row["storage_key"]), str(row["preview_storage_key"]))
    return {"status": "ok", "asset_id": asset_id}


@router.delete("/media/{asset_id}/links")
def remove_media_link(
    asset_id: str,
    request: Request,
    db: DB,
    target_type: TargetType = Query(...),
    target_id: str = Query(..., min_length=1, max_length=120),
) -> dict[str, object]:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    row = _fetch_asset_row(db, garden_id=garden_id, asset_id=asset_id)
    if not _asset_has_readable_link(
        db,
        context=context,
        garden_id=garden_id,
        asset_id=asset_id,
    ):
        raise HTTPException(status_code=404, detail="Media asset not found")
    canonical_target_id = _validate_media_target(
        db,
        context=context,
        target_type=target_type,
        target_id=target_id,
    )
    deleted_link = db.execute(
        """
        DELETE FROM media_links
        WHERE asset_id = %s AND target_type = %s AND target_id = %s
        """,
        (asset_id, target_type, canonical_target_id),
    )
    if deleted_link.rowcount == 0:
        raise HTTPException(status_code=404, detail="Media link not found")
    if target_type == "plant":
        _clear_plant_cover(
            db,
            garden_id=garden_id,
            plt_id=canonical_target_id,
            asset_id=asset_id,
        )
    remaining = db.execute(
        "SELECT 1 FROM media_links WHERE asset_id = %s LIMIT 1",
        (asset_id,),
    ).fetchone()
    deleted_asset = remaining is None
    if deleted_asset:
        db.execute(
            "DELETE FROM media_assets WHERE asset_id = %s AND garden_id = %s",
            (asset_id, garden_id),
        )
    db.commit()
    if deleted_asset:
        unlink_storage_keys(str(row["storage_key"]), str(row["preview_storage_key"]))
    return {
        "status": "ok",
        "asset_id": asset_id,
        "target_type": target_type,
        "target_id": canonical_target_id,
        "deleted_asset": deleted_asset,
    }


@router.post("/media/{asset_id}/links")
def add_media_link(
    asset_id: str,
    body: CreateMediaLinkBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    _fetch_asset_row(db, garden_id=garden_id, asset_id=asset_id)
    if not _asset_has_readable_link(
        db,
        context=context,
        garden_id=garden_id,
        asset_id=asset_id,
    ):
        raise HTTPException(status_code=404, detail="Media asset not found")
    canonical_target_id = _validate_media_target(
        db,
        context=context,
        target_type=body.target_type,
        target_id=body.target_id,
    )
    db.execute(
        """
        INSERT INTO media_links (asset_id, target_type, target_id, sort_order)
        VALUES (%s, %s, %s, 0)
        ON CONFLICT(asset_id, target_type, target_id) DO NOTHING
        """,
        (asset_id, body.target_type, canonical_target_id),
    )
    db.commit()
    row = _fetch_asset_row(db, garden_id=garden_id, asset_id=asset_id)
    return _serialize_asset_row(db, row, context=context, garden_id=garden_id)


@router.post("/media/plants/{plt_id}/cover")
def set_plant_cover(
    plt_id: str,
    body: SetPlantCoverBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    canonical_plt_id = _validate_media_target(
        db,
        context=context,
        target_type="plant",
        target_id=plt_id,
    )
    _fetch_asset_row(db, garden_id=garden_id, asset_id=body.asset_id)
    _set_plant_cover(
        db,
        garden_id=garden_id,
        plt_id=canonical_plt_id,
        asset_id=body.asset_id,
        actor_user_id=context.user_id,
    )
    db.commit()
    row = _fetch_asset_row(db, garden_id=garden_id, asset_id=body.asset_id)
    return {
        "status": "ok",
        "plant_id": canonical_plt_id,
        "asset": _serialize_asset_row(
            db,
            row,
            context=context,
            garden_id=garden_id,
            is_cover=True,
        ),
    }


@router.post("/media/plants/populate-missing-covers")
def populate_missing_plant_covers(
    body: PopulateMissingPlantCoversBody,
    request: Request,
    db: DB,
) -> dict[str, object]:
    context, _action_reason = enforce_destructive_admin_controls(
        request,
        body_reason=body.action_reason,
    )
    garden_id = _active_garden_id(context)
    allow_global = _is_local_admin_fallback(context)
    enforce_rate_limit(
        request,
        bucket="media_cover_imports",
        limit=env_int("MEDIA_COVER_IMPORT_RATE_LIMIT", 20),
        window_seconds=60,
    )
    cursor = body.cursor.strip()
    total_before_row = db.execute(
        """
        SELECT COUNT(*) AS c
        FROM plants p
        LEFT JOIN plant_media_covers c
          ON c.garden_id = %s
         AND c.plt_id = p.plt_id
        LEFT JOIN plant_ownership o
          ON o.garden_id = %s
         AND o.plt_id = p.plt_id
        WHERE (%s = 1 OR o.plt_id IS NOT NULL)
          AND c.plt_id IS NULL
        """,
        (garden_id, garden_id, 1 if allow_global else 0),
    ).fetchone()
    total_before = int(total_before_row["c"] or 0) if total_before_row else 0
    batch_rows = db.execute(
        """
        SELECT p.plt_id, p.name, COALESCE(p.latin, '') AS latin, COALESCE(p.link, '') AS link
        FROM plants p
        LEFT JOIN plant_media_covers c
          ON c.garden_id = %s
         AND c.plt_id = p.plt_id
        LEFT JOIN plant_ownership o
          ON o.plt_id = p.plt_id
         AND o.garden_id = %s
        WHERE c.plt_id IS NULL
          AND (%s = 1 OR o.plt_id IS NOT NULL)
          AND p.plt_id > %s
        ORDER BY p.plt_id
        LIMIT %s
        """,
        (garden_id, garden_id, 1 if allow_global else 0, cursor, body.max_plants),
    ).fetchall()
    adopted_existing = 0
    imported_remote = 0
    skipped = 0
    items: list[dict[str, object]] = []
    last_processed = cursor
    for plant_row in batch_rows:
        plt_id = str(plant_row["plt_id"])
        latin_name = str(plant_row["latin"] or "").strip()
        plant_link = str(plant_row["link"] or "").strip()
        last_processed = plt_id
        existing_row = _latest_plant_asset_row(db, garden_id=garden_id, plt_id=plt_id)
        if existing_row is not None:
            _set_plant_cover(
                db,
                garden_id=garden_id,
                plt_id=plt_id,
                asset_id=str(existing_row["asset_id"]),
                actor_user_id=context.user_id,
            )
            db.commit()
            adopted_existing += 1
            items.append(
                {
                    "plant_id": plt_id,
                    "status": "adopted_existing",
                    "detail": "Used the latest existing plant photo as cover",
                }
            )
            continue
        if not latin_name:
            _upsert_plant_cover_import_status(
                db,
                garden_id=garden_id,
                plt_id=plt_id,
                status_code="missing_latin",
                detail="Missing latin name",
            )
            db.commit()
            skipped += 1
            items.append(
                {
                    "plant_id": plt_id,
                    "status": "skipped",
                    "detail": "Missing latin name",
                }
            )
            continue
        if not plant_link:
            _upsert_plant_cover_import_status(
                db,
                garden_id=garden_id,
                plt_id=plt_id,
                status_code="missing_link",
                detail="Missing plant link",
            )
            db.commit()
            skipped += 1
            items.append(
                {
                    "plant_id": plt_id,
                    "status": "skipped",
                    "detail": "Missing plant link",
                }
            )
            continue
        try:
            prepared_import = discover_cover_from_plant_link(plant_link, latin_name)
            _enforce_media_quota(
                db,
                garden_id=garden_id,
                incoming_asset=prepared_import.prepared_asset,
            )
            persist_prepared_media(prepared_import.prepared_asset)
            try:
                _insert_prepared_asset_link(
                    db,
                    garden_id=garden_id,
                    prepared=prepared_import.prepared_asset,
                    actor_user_id=context.user_id,
                    target_type="plant",
                    target_id=plt_id,
                    auto_set_plant_cover=False,
                )
                _set_plant_cover(
                    db,
                    garden_id=garden_id,
                    plt_id=plt_id,
                    asset_id=prepared_import.prepared_asset.asset_id,
                    actor_user_id=context.user_id,
                )
                db.commit()
            except Exception:
                unlink_storage_keys(
                    prepared_import.prepared_asset.storage_key,
                    prepared_import.prepared_asset.preview_storage_key,
                )
                db.rollback()
                raise
            imported_remote += 1
            items.append(
                {
                    "plant_id": plt_id,
                    "status": "imported_remote",
                    "detail": prepared_import.source_image_url,
                }
            )
        except HTTPException as exc:
            _upsert_plant_cover_import_status(
                db,
                garden_id=garden_id,
                plt_id=plt_id,
                status_code="remote_error",
                detail=str(exc.detail),
            )
            db.commit()
            skipped += 1
            items.append(
                {
                    "plant_id": plt_id,
                    "status": "skipped",
                    "detail": str(exc.detail),
                }
            )
    has_more = False
    if last_processed:
        next_row = db.execute(
            """
            SELECT p.plt_id
            FROM plants p
            LEFT JOIN plant_media_covers c
              ON c.garden_id = %s
             AND c.plt_id = p.plt_id
            LEFT JOIN plant_ownership o
              ON o.plt_id = p.plt_id
             AND o.garden_id = %s
            WHERE c.plt_id IS NULL
              AND (%s = 1 OR o.plt_id IS NOT NULL)
              AND p.plt_id > %s
            ORDER BY p.plt_id
            LIMIT 1
            """,
            (garden_id, garden_id, 1 if allow_global else 0, last_processed),
        ).fetchone()
        has_more = next_row is not None
    remaining_row = db.execute(
        """
        SELECT COUNT(*) AS c
        FROM plants p
        LEFT JOIN plant_media_covers c
          ON c.garden_id = %s
         AND c.plt_id = p.plt_id
        LEFT JOIN plant_ownership o
          ON o.garden_id = %s
         AND o.plt_id = p.plt_id
        WHERE (%s = 1 OR o.plt_id IS NOT NULL)
          AND c.plt_id IS NULL
        """,
        (garden_id, garden_id, 1 if allow_global else 0),
    ).fetchone()
    remaining = int(remaining_row["c"] or 0) if remaining_row else 0
    return {
        "status": "ok",
        "cursor": last_processed if has_more and last_processed else None,
        "has_more": has_more,
        "processed": len(batch_rows),
        "total_without_cover_before": total_before,
        "remaining_without_cover": remaining,
        "adopted_existing": adopted_existing,
        "imported_remote": imported_remote,
        "skipped": skipped,
        "items": items,
    }


def collect_media_cleanup_for_target(
    db: DbConn,
    *,
    garden_id: int,
    target_type: TargetType,
    target_id: str,
) -> list[tuple[str, str]]:
    return collect_orphaned_media_storage_keys(
        db,
        garden_id=garden_id,
        target_type=target_type,
        target_id=_canonical_target_id(target_type, target_id),
    )
