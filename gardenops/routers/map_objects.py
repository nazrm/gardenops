import json
import re
from hashlib import md5
from typing import Any, Literal, cast

import psycopg
from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, field_validator

from gardenops.audit import write_audit_event
from gardenops.db import DB, DbConn, current_timestamp_ms
from gardenops.events import notify_garden_modified
from gardenops.models import MapObjectImportItem, StrictBaseModel
from gardenops.rate_limit import enforce_rate_limit, env_int
from gardenops.router_helpers import auth_context as _auth_context
from gardenops.router_helpers import generate_public_id
from gardenops.router_helpers import is_local_admin_fallback as _is_local_admin_fallback
from gardenops.security import AuthContext

router = APIRouter()

MapObjectType = Literal[
    "patio",
    "terrace",
    "greenhouse",
    "balcony",
    "shed",
    "pond",
    "path",
    "bed",
    "other",
]
MapObjectShape = Literal["rectangle", "ellipse"]
ContainerType = Literal["pot", "planter", "raised_bed", "other"]
ContainerEnvironment = Literal["outdoor", "covered", "indoor"]

SAFE_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
DEFAULT_INTERNAL_LAYOUT = {"rows": 6, "cols": 8}
MAX_MAP_OBJECTS_PER_GARDEN = 200
MAX_UNITS_PER_IMPORT = 500
AREA_TYPES = frozenset({"patio", "terrace", "greenhouse", "balcony", "other"})
PUBLIC_ID_TABLES = frozenset({"garden_map_objects"})


class MapObjectGeometryBody(StrictBaseModel):
    x: int = Field(ge=1, le=100)
    y: int = Field(ge=1, le=100)
    width: int = Field(ge=1, le=100)
    height: int = Field(ge=1, le=100)


class MapObjectStyleBody(StrictBaseModel):
    color: str = "#7d9f7a"

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str) -> str:
        normalized = value.strip()
        if not SAFE_COLOR_RE.fullmatch(normalized):
            raise ValueError("Color must be a safe hex color")
        return normalized


class MapObjectInternalLayoutBody(StrictBaseModel):
    rows: int = Field(ge=1, le=100)
    cols: int = Field(ge=1, le=100)


class CreateMapObjectBody(StrictBaseModel):
    object_type: MapObjectType
    name: str = Field(min_length=1, max_length=120)
    shape_type: MapObjectShape
    geometry: MapObjectGeometryBody
    style: MapObjectStyleBody = Field(default_factory=MapObjectStyleBody)
    z_index: int = Field(default=0, ge=-1000, le=1000)
    has_internal_layout: bool = False
    internal_layout: MapObjectInternalLayoutBody | None = None


class UpdateMapObjectBody(StrictBaseModel):
    object_type: MapObjectType | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    shape_type: MapObjectShape | None = None
    geometry: MapObjectGeometryBody | None = None
    style: MapObjectStyleBody | None = None
    z_index: int | None = Field(default=None, ge=-1000, le=1000)
    has_internal_layout: bool | None = None
    internal_layout: MapObjectInternalLayoutBody | None = None


class CreateContainerBody(StrictBaseModel):
    name: str = Field(min_length=1, max_length=120)
    container_type: ContainerType
    parent_object_public_id: str | None = Field(default=None, min_length=1, max_length=80)
    environment: ContainerEnvironment | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Container name is required")
        return normalized


class UpdateContainerBody(StrictBaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    container_type: ContainerType | None = None
    parent_object_public_id: str | None = Field(default=None, min_length=1, max_length=80)
    environment: ContainerEnvironment | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Container name is required")
        return normalized


class ContainerImportItem(StrictBaseModel):
    plot_id: str | None = Field(default=None, min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=120)
    container_type: ContainerType
    environment: ContainerEnvironment = "outdoor"
    parent_object_public_id: str | None = Field(default=None, min_length=1, max_length=80)
    archived_at_ms: int | None = Field(default=None, ge=0)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Container name is required")
        return normalized


def _remote_host(request: Request) -> str:
    return request.client.host if request.client and request.client.host else "unknown"


def _is_platform_admin(context: AuthContext) -> bool:
    return context.role == "admin"


def _require_garden_exists(db: DbConn, garden_id: int) -> None:
    row = db.execute("SELECT 1 FROM gardens WHERE id = %s LIMIT 1", (garden_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Garden not found")


def _membership_role(db: DbConn, *, context: AuthContext, garden_id: int) -> str:
    _require_garden_exists(db, garden_id)
    if _is_local_admin_fallback(context) or _is_platform_admin(context):
        return "admin"
    if context.user_id is None:
        raise HTTPException(status_code=404, detail="Garden not found")
    row = db.execute(
        """
        SELECT role
        FROM garden_memberships
        WHERE garden_id = %s AND user_id = %s
        LIMIT 1
        """,
        (garden_id, context.user_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Garden not found")
    return str(row["role"])


def _require_member(db: DbConn, *, context: AuthContext, garden_id: int) -> None:
    _membership_role(db, context=context, garden_id=garden_id)


def _require_editor(db: DbConn, *, context: AuthContext, garden_id: int) -> None:
    role = _membership_role(db, context=context, garden_id=garden_id)
    if role not in {"admin", "editor"}:
        raise HTTPException(status_code=403, detail="Editor role required")


def _audit_map_object_change(
    request: Request,
    context: AuthContext,
    *,
    db: DbConn,
    garden_id: int,
    event: str,
    fields: dict[str, object],
    status_code: int = 200,
) -> None:
    request.state.audited_by_handler = True
    detail = f"{event} {json.dumps(fields, sort_keys=True, separators=(',', ':'))}"
    write_audit_event(
        method=request.method,
        path=request.url.path,
        status_code=status_code,
        remote_host=_remote_host(request),
        detail=detail,
        auth_context=context,
        garden_id=garden_id,
        db=db,
    )


def _enforce_map_object_rate_limit(request: Request, *, bucket: str) -> None:
    enforce_rate_limit(
        request,
        bucket=bucket,
        limit=env_int("GARDEN_MAP_OBJECT_RATE_LIMIT", 60),
        window_seconds=60,
    )


def _garden_size(db: DbConn, garden_id: int) -> tuple[int, int]:
    row = db.execute(
        "SELECT grid_rows, grid_cols FROM gardens WHERE id = %s LIMIT 1",
        (garden_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Garden not found")
    return int(row["grid_rows"]), int(row["grid_cols"])


def _geometry_dict(value: MapObjectGeometryBody) -> dict[str, int]:
    return value.model_dump()


def _style_dict(value: MapObjectStyleBody) -> dict[str, str]:
    return value.model_dump()


def _layout_dict(value: MapObjectInternalLayoutBody | None) -> dict[str, int]:
    if value is None:
        return dict(DEFAULT_INTERNAL_LAYOUT)
    return value.model_dump()


def _loads_dict(raw: object, fallback: dict[str, object]) -> dict[str, object]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return dict(fallback)
    return parsed if isinstance(parsed, dict) else dict(fallback)


def _dump_json(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _validate_geometry_fits(
    geometry: dict[str, int],
    *,
    rows: int,
    cols: int,
    label: str,
) -> None:
    if (
        geometry["x"] + geometry["width"] - 1 > cols
        or geometry["y"] + geometry["height"] - 1 > rows
    ):
        raise HTTPException(status_code=400, detail=f"{label} does not fit within the layout")


def _next_public_id(db: DbConn, *, table: str, prefix: str) -> str:
    if table not in PUBLIC_ID_TABLES:
        raise RuntimeError("Unsupported public id table")
    for _ in range(10):
        public_id = generate_public_id(prefix)
        row = db.execute(
            f"SELECT 1 FROM {table} WHERE public_id = %s LIMIT 1",
            (public_id,),
        ).fetchone()
        if not row:
            return public_id
    raise HTTPException(status_code=500, detail="Could not allocate public id")


def _next_public_id_excluding(
    db: DbConn,
    *,
    table: str,
    prefix: str,
    used: set[str],
) -> str:
    for _ in range(20):
        public_id = _next_public_id(db, table=table, prefix=prefix)
        if public_id not in used:
            used.add(public_id)
            return public_id
    raise HTTPException(status_code=500, detail="Could not allocate public id")


def _public_id_available(db: DbConn, *, table: str, public_id: str) -> bool:
    if table not in PUBLIC_ID_TABLES:
        raise RuntimeError("Unsupported public id table")
    row = db.execute(f"SELECT 1 FROM {table} WHERE public_id = %s LIMIT 1", (public_id,)).fetchone()
    return row is None


def _import_public_id(
    db: DbConn,
    *,
    table: str,
    prefix: str,
    requested_public_id: str | None,
    used: set[str],
) -> str:
    if (
        requested_public_id
        and requested_public_id not in used
        and _public_id_available(db, table=table, public_id=requested_public_id)
    ):
        used.add(requested_public_id)
        return requested_public_id
    return _next_public_id_excluding(db, table=table, prefix=prefix, used=used)


def _serialize_container(
    row: dict[str, Any],
    *,
    can_edit: bool | None = None,
    can_archive: bool | None = None,
) -> dict[str, object]:
    display_name = str(row.get("display_name") or row.get("zone_name") or row["plot_id"])
    parent_public_id = row.get("parent_object_public_id")
    archived_at_ms = row.get("archived_at_ms")
    serialized: dict[str, object] = {
        "plot_id": str(row["plot_id"]),
        "name": display_name,
        "display_name": display_name,
        "container_type": str(row["container_type"]),
        "environment": str(row.get("environment") or "outdoor"),
        "parent_object_public_id": (
            str(parent_public_id) if parent_public_id is not None else None
        ),
        "parent_map_object_public_id": (
            str(parent_public_id) if parent_public_id is not None else None
        ),
        "parent_object_name": (
            str(row["parent_object_name"]) if row.get("parent_object_name") is not None else None
        ),
        "plant_count": int(row.get("plant_count") or 0),
        "plant_quantity": int(row.get("plant_quantity") or 0),
        "archived": archived_at_ms is not None,
        "archived_at_ms": int(archived_at_ms) if archived_at_ms is not None else None,
    }
    if can_edit is not None:
        serialized["can_edit"] = can_edit
    if can_archive is not None:
        serialized["can_archive"] = can_archive
    return serialized


def _canonical_container_rows(
    db: DbConn,
    *,
    garden_id: int,
    include_archived: bool = False,
    plot_id: str | None = None,
    for_update: bool = False,
) -> list[dict[str, Any]]:
    where = ["p.garden_id = %s", "p.plot_kind = 'container'"]
    params: list[object] = [garden_id]
    if not include_archived:
        where.append("p.archived_at_ms IS NULL")
    if plot_id is not None:
        where.append("p.plot_id = %s")
        params.append(plot_id)
    lock_clause = "FOR UPDATE OF p" if for_update else ""
    rows = [
        dict(row)
        for row in db.execute(
            f"""
            SELECT p.*,
                   o.public_id AS parent_object_public_id,
                   o.name AS parent_object_name
            FROM plots p
            LEFT JOIN garden_map_objects o
              ON o.id = p.parent_map_object_id
             AND o.garden_id = p.garden_id
            WHERE {" AND ".join(where)}
            ORDER BY p.display_name, p.plot_id
            {lock_clause}
            """,
            params,
        ).fetchall()
    ]
    if not rows:
        return []

    counts = db.execute(
        """
        SELECT plot_id,
               COUNT(DISTINCT plt_id) FILTER (WHERE quantity > 0) AS plant_count,
               COALESCE(SUM(quantity) FILTER (WHERE quantity > 0), 0) AS plant_quantity
        FROM plot_plants
        WHERE plot_id = ANY(%s)
        GROUP BY plot_id
        """,
        [[str(row["plot_id"]) for row in rows]],
    ).fetchall()
    count_by_plot = {str(row["plot_id"]): dict(row) for row in counts}
    for row in rows:
        count = count_by_plot.get(str(row["plot_id"]), {})
        row["plant_count"] = int(count.get("plant_count") or 0)
        row["plant_quantity"] = int(count.get("plant_quantity") or 0)
    return rows


def _serialize_object(
    row: dict[str, Any],
    containers: list[dict[str, object]],
) -> dict[str, object]:
    plant_count = sum(int(container["plant_count"]) for container in containers)
    plant_quantity = sum(int(container["plant_quantity"]) for container in containers)
    return {
        "public_id": str(row["public_id"]),
        "object_type": str(row["object_type"]),
        "name": str(row["name"]),
        "shape_type": str(row["shape_type"]),
        "geometry": _loads_dict(row["geometry_json"], {}),
        "style": _loads_dict(row["style_json"], {"color": "#7d9f7a"}),
        "z_index": int(row["z_index"]),
        "has_internal_layout": bool(int(row["has_internal_layout"])),
        "internal_layout": _loads_dict(row["internal_layout_json"], DEFAULT_INTERNAL_LAYOUT),
        "created_at_ms": int(row["created_at_ms"]),
        "updated_at_ms": int(row["updated_at_ms"]),
        "container_count": len(containers),
        "plant_count": plant_count,
        "plant_quantity": plant_quantity,
        "containers": containers,
    }


def _export_container(row: dict[str, Any]) -> dict[str, object]:
    container = _serialize_container(row)
    return {
        "plot_id": container["plot_id"],
        "display_name": container["display_name"],
        "container_type": container["container_type"],
        "environment": container["environment"],
        "parent_object_public_id": container["parent_object_public_id"],
        "archived_at_ms": container["archived_at_ms"],
    }


def _export_object(
    row: dict[str, Any],
    containers: list[dict[str, object]],
) -> dict[str, object]:
    item = _serialize_object(row, containers)
    return {
        "public_id": item["public_id"],
        "object_type": item["object_type"],
        "name": item["name"],
        "shape_type": item["shape_type"],
        "geometry": item["geometry"],
        "style": item["style"],
        "z_index": item["z_index"],
        "has_internal_layout": item["has_internal_layout"],
        "internal_layout": item["internal_layout"],
        "containers": [
            {
                "plot_id": container["plot_id"],
                "display_name": container["display_name"],
                "container_type": container["container_type"],
                "environment": container["environment"],
                "parent_object_public_id": item["public_id"],
                "archived_at_ms": container["archived_at_ms"],
            }
            for container in containers
        ],
    }


def snapshot_map_objects(db: DbConn, garden_id: int) -> list[dict[str, object]]:
    object_rows = db.execute(
        """
        SELECT *
        FROM garden_map_objects
        WHERE garden_id = %s
        ORDER BY z_index, id
        """,
        (garden_id,),
    ).fetchall()
    if not object_rows:
        return []

    containers_by_object: dict[int, list[dict[str, object]]] = {}
    for container in _canonical_container_rows(
        db,
        garden_id=garden_id,
        include_archived=True,
    ):
        parent_id = container.get("parent_map_object_id")
        if parent_id is not None:
            containers_by_object.setdefault(int(parent_id), []).append(container)
    return [
        _export_object(
            dict(row),
            [
                _serialize_container(container)
                for container in containers_by_object.get(int(row["id"]), [])
            ],
        )
        for row in object_rows
    ]


def snapshot_containers(db: DbConn, garden_id: int) -> list[dict[str, object]]:
    """Return the canonical container records for v2 layout exporters."""
    return [
        _export_container(row)
        for row in _canonical_container_rows(
            db,
            garden_id=garden_id,
            include_archived=True,
        )
    ]


def _normalized_container_import(
    raw: object,
    *,
    parent_object_public_id: str | None = None,
) -> ContainerImportItem:
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="Invalid container import item")
    payload = dict(raw)
    if "display_name" not in payload:
        payload["display_name"] = payload.pop("name", None)
    if parent_object_public_id is not None and "parent_object_public_id" not in payload:
        payload["parent_object_public_id"] = parent_object_public_id
    allowed = {
        "plot_id",
        "display_name",
        "container_type",
        "environment",
        "parent_object_public_id",
        "archived_at_ms",
    }
    payload = {key: value for key, value in payload.items() if key in allowed}
    try:
        return ContainerImportItem.model_validate(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid container import item") from exc


def _parse_map_object_import(
    raw_item: object,
) -> tuple[MapObjectImportItem, list[ContainerImportItem]]:
    if not isinstance(raw_item, dict):
        raise HTTPException(status_code=400, detail="Invalid map object import item")
    raw = dict(raw_item)
    raw_containers = raw.pop("containers", None)
    try:
        item = MapObjectImportItem.model_validate(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid map object import item") from exc

    containers: list[ContainerImportItem] = []
    if raw_containers is not None:
        if not isinstance(raw_containers, list):
            raise HTTPException(status_code=400, detail="Containers must be a list")
        for raw_container in raw_containers:
            normalized = _normalized_container_import(
                raw_container,
                parent_object_public_id=item.public_id,
            )
            if (
                isinstance(raw_container, dict)
                and "environment" not in raw_container
                and item.object_type == "greenhouse"
            ):
                normalized = normalized.model_copy(update={"environment": "covered"})
            containers.append(normalized)
    else:
        # Schema-v1 units were layout records. Translate only their useful
        # identity fields; geometry and appearance are intentionally dropped.
        for unit in item.units:
            containers.append(
                ContainerImportItem(
                    plot_id=(
                        f"CONT-{md5(unit.public_id.encode('utf-8')).hexdigest()}"
                        if unit.public_id
                        else None
                    ),
                    display_name=unit.name.strip(),
                    container_type="other" if unit.unit_type == "shelf" else unit.unit_type,
                    environment="covered" if item.object_type == "greenhouse" else "outdoor",
                    parent_object_public_id=item.public_id,
                ),
            )
    return item, containers


def _next_container_plot_id(
    db: DbConn,
    *,
    requested: str | None = None,
    reuse_existing_container: bool = False,
) -> str:
    if requested and len(requested) <= 40:
        existing = db.execute(
            "SELECT garden_id, plot_kind FROM plots WHERE plot_id = %s LIMIT 1",
            (requested,),
        ).fetchone()
        if existing is None:
            return requested
        if reuse_existing_container and str(existing["plot_kind"]) == "container":
            return requested
    for _ in range(10):
        plot_id = generate_public_id("container")
        if not db.execute("SELECT 1 FROM plots WHERE plot_id = %s LIMIT 1", (plot_id,)).fetchone():
            return plot_id
    raise HTTPException(status_code=500, detail="Could not allocate container id")


def _container_owner_user_id(
    db: DbConn,
    *,
    garden_id: int,
    preferred_user_id: int | None,
) -> int:
    if preferred_user_id is not None:
        return int(preferred_user_id)
    row = db.execute(
        """
        SELECT gm.user_id
        FROM garden_memberships gm
        JOIN auth_users u ON u.id = gm.user_id
        WHERE gm.garden_id = %s AND u.is_active = 1
        ORDER BY CASE gm.role WHEN 'admin' THEN 0 WHEN 'editor' THEN 1 ELSE 2 END,
                 gm.user_id
        LIMIT 1
        """,
        (garden_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=409, detail="No active garden member can own a container")
    return int(row["user_id"])


def _insert_or_update_imported_container(
    db: DbConn,
    *,
    garden_id: int,
    owner_user_id: int,
    item: ContainerImportItem,
    parent_map_object_id: int | None,
    allow_existing: bool,
) -> None:
    if allow_existing and item.plot_id:
        requested_row = db.execute(
            "SELECT garden_id, plot_kind FROM plots WHERE plot_id = %s LIMIT 1",
            (item.plot_id,),
        ).fetchone()
        if requested_row is not None and (
            int(requested_row["garden_id"]) != garden_id
            or str(requested_row["plot_kind"]) != "container"
        ):
            raise HTTPException(status_code=409, detail=f"Container ID conflict: {item.plot_id}")
    plot_id = _next_container_plot_id(
        db,
        requested=item.plot_id,
        reuse_existing_container=allow_existing,
    )
    existing = db.execute(
        "SELECT garden_id, plot_kind FROM plots WHERE plot_id = %s FOR UPDATE",
        (plot_id,),
    ).fetchone()
    if existing is not None:
        if not allow_existing or int(existing["garden_id"]) != garden_id:
            raise HTTPException(status_code=409, detail=f"Container ID conflict: {plot_id}")
        if str(existing["plot_kind"]) != "container":
            raise HTTPException(status_code=409, detail=f"Plot ID conflict: {plot_id}")
        db.execute(
            """
            UPDATE plots
            SET display_name = %s,
                container_type = %s,
                parent_map_object_id = %s,
                environment = %s,
                archived_at_ms = %s
            WHERE plot_id = %s AND garden_id = %s
            """,
            (
                item.display_name,
                item.container_type,
                parent_map_object_id,
                item.environment,
                item.archived_at_ms,
                plot_id,
                garden_id,
            ),
        )
    else:
        db.execute(
            """
            INSERT INTO plots (
                plot_id, garden_id, zone_code, zone_name, plot_number,
                grid_row, grid_col, sub_zone, notes, color,
                plot_kind, display_name, container_type, parent_map_object_id,
                environment, archived_at_ms
            )
            VALUES (%s, %s, 'C', 'Containers', 0, NULL, NULL, '', '', NULL,
                    'container', %s, %s, %s, %s, %s)
            """,
            (
                plot_id,
                garden_id,
                item.display_name,
                item.container_type,
                parent_map_object_id,
                item.environment,
                item.archived_at_ms,
            ),
        )
    db.execute(
        """
        INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (plot_id) DO UPDATE SET
            owner_user_id = excluded.owner_user_id,
            garden_id = excluded.garden_id
        """,
        (plot_id, owner_user_id, garden_id),
    )


def replace_map_objects(
    db: DbConn,
    *,
    garden_id: int,
    map_objects: list[dict[str, Any]] | None,
    created_by_user_id: int | None,
) -> int:
    if map_objects is None:
        return 0
    if len(map_objects) > MAX_MAP_OBJECTS_PER_GARDEN:
        raise HTTPException(status_code=400, detail="Map object limit reached for this garden")

    grid_rows, grid_cols = _garden_size(db, garden_id)
    parsed_items = [_parse_map_object_import(raw_item) for raw_item in map_objects]
    total_containers = sum(len(containers) for _, containers in parsed_items)
    if total_containers > MAX_UNITS_PER_IMPORT:
        raise HTTPException(
            status_code=400,
            detail=f"Container limit reached for this import ({MAX_UNITS_PER_IMPORT} max)",
        )
    for item, _ in parsed_items:
        geometry = item.geometry.model_dump()
        _validate_geometry_fits(geometry, rows=grid_rows, cols=grid_cols, label="Map object")

    # Existing canonical containers survive an area replacement. Remember
    # their public parent so a recreated area can receive them again.
    existing_container_parents = db.execute(
        """
        SELECT p.plot_id, parent.public_id AS parent_public_id
        FROM plots p
        JOIN garden_map_objects parent
          ON parent.id = p.parent_map_object_id
         AND parent.garden_id = p.garden_id
        WHERE p.garden_id = %s
          AND p.plot_kind = 'container'
          AND p.parent_map_object_id IS NOT NULL
        """,
        (garden_id,),
    ).fetchall()
    db.execute(
        """
        UPDATE plots
        SET parent_map_object_id = NULL
        WHERE garden_id = %s
          AND plot_kind = 'container'
          AND parent_map_object_id IS NOT NULL
        """,
        (garden_id,),
    )
    db.execute("DELETE FROM garden_map_objects WHERE garden_id = %s", (garden_id,))
    used_object_public_ids: set[str] = set()
    imported_area_ids: dict[str, int] = {}
    imported_area_ids_in_order: list[int] = []
    now_ms = current_timestamp_ms()
    inserted = 0

    for item, _ in parsed_items:
        geometry = item.geometry.model_dump()
        layout = _layout_dict(item.internal_layout)

        object_public_id = _import_public_id(
            db,
            table="garden_map_objects",
            prefix="mapobj",
            requested_public_id=item.public_id,
            used=used_object_public_ids,
        )
        object_row = db.execute(
            """
            INSERT INTO garden_map_objects (
                public_id, garden_id, object_type, name, shape_type,
                geometry_json, style_json, z_index, has_internal_layout,
                internal_layout_json, created_by_user_id, created_at_ms, updated_at_ms
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                object_public_id,
                garden_id,
                item.object_type,
                item.name.strip(),
                item.shape_type,
                _dump_json(cast(dict[str, object], geometry)),
                _dump_json(cast(dict[str, object], item.style.model_dump())),
                item.z_index,
                1 if item.has_internal_layout else 0,
                _dump_json(cast(dict[str, object], layout)),
                created_by_user_id,
                now_ms,
                now_ms,
            ),
        ).fetchone()
        assert object_row is not None
        map_object_id = int(object_row["id"])
        imported_area_ids[item.public_id or object_public_id] = map_object_id
        imported_area_ids[object_public_id] = map_object_id
        imported_area_ids_in_order.append(map_object_id)
        inserted += 1

    for row in existing_container_parents:
        parent_map_object_id = imported_area_ids.get(str(row["parent_public_id"]))
        if parent_map_object_id is None:
            continue
        db.execute(
            """
            UPDATE plots
            SET parent_map_object_id = %s
            WHERE garden_id = %s AND plot_id = %s AND plot_kind = 'container'
            """,
            (parent_map_object_id, garden_id, str(row["plot_id"])),
        )

    owner_user_id = (
        _container_owner_user_id(
            db,
            garden_id=garden_id,
            preferred_user_id=created_by_user_id,
        )
        if total_containers
        else 0
    )
    for area_index, (raw_item, (_, containers)) in enumerate(
        zip(map_objects, parsed_items, strict=True),
    ):
        implicit_parent = imported_area_ids_in_order[area_index]
        for container in containers:
            requested_parent = container.parent_object_public_id
            if requested_parent is None:
                parent_map_object_id = implicit_parent
            else:
                parent_map_object_id = imported_area_ids.get(requested_parent)
                if parent_map_object_id is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Container parent not found in import: {requested_parent}",
                    )
            _insert_or_update_imported_container(
                db,
                garden_id=garden_id,
                owner_user_id=owner_user_id,
                item=container,
                parent_map_object_id=parent_map_object_id,
                allow_existing=(
                    isinstance(raw_item, dict) and ("containers" in raw_item or "units" in raw_item)
                ),
            )
    return inserted


def _object_row_by_public_id(
    db: DbConn,
    *,
    garden_id: int,
    object_public_id: str,
    for_update: bool = False,
) -> dict[str, Any]:
    lock_clause = "FOR UPDATE" if for_update else ""
    row = db.execute(
        f"""
        SELECT *
        FROM garden_map_objects
        WHERE garden_id = %s AND public_id = %s
        LIMIT 1
        {lock_clause}
        """,
        (garden_id, object_public_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Map object not found")
    return dict(row)


def _container_response(
    db: DbConn,
    *,
    garden_id: int,
    plot_id: str,
    include_archived: bool = True,
    for_update: bool = False,
    role: str | None = None,
) -> dict[str, object]:
    rows = _canonical_container_rows(
        db,
        garden_id=garden_id,
        include_archived=include_archived,
        plot_id=plot_id,
        for_update=for_update,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Container not found")
    archived = rows[0].get("archived_at_ms") is not None
    return _serialize_container(
        rows[0],
        can_edit=(role in {"admin", "editor"} and not archived) if role is not None else None,
        can_archive=(role == "admin" and not archived) if role is not None else None,
    )


def _area_parent_id(
    db: DbConn,
    *,
    garden_id: int,
    object_public_id: str,
    for_update: bool = False,
) -> int:
    row = _object_row_by_public_id(
        db,
        garden_id=garden_id,
        object_public_id=object_public_id,
        for_update=for_update,
    )
    if str(row["object_type"]) not in AREA_TYPES:
        raise HTTPException(status_code=400, detail="Containers require an area parent")
    return int(row["id"])


@router.get("/gardens/{garden_id}/map-objects")
def list_map_objects(
    garden_id: int,
    db: DB,
    request: Request,
) -> dict[str, object]:
    context = _auth_context(request)
    role = _membership_role(db, context=context, garden_id=garden_id)
    object_rows = db.execute(
        """
        SELECT *
        FROM garden_map_objects
        WHERE garden_id = %s
        ORDER BY z_index, id
        """,
        (garden_id,),
    ).fetchall()
    containers = _canonical_container_rows(db, garden_id=garden_id)
    containers_by_object: dict[int, list[dict[str, object]]] = {}
    serialized_containers = [
        _serialize_container(
            container,
            can_edit=role in {"admin", "editor"},
            can_archive=role == "admin",
        )
        for container in containers
    ]
    for container, serialized in zip(containers, serialized_containers, strict=True):
        parent_id = container.get("parent_map_object_id")
        if parent_id is not None:
            containers_by_object.setdefault(int(parent_id), []).append(serialized)

    return {
        "objects": [
            _serialize_object(
                dict(row),
                containers_by_object.get(int(row["id"]), []),
            )
            for row in object_rows
        ],
        "containers": serialized_containers,
    }


@router.post("/gardens/{garden_id}/map-objects", status_code=201)
def create_map_object(
    garden_id: int,
    body: CreateMapObjectBody,
    db: DB,
    request: Request,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_editor(db, context=context, garden_id=garden_id)
    _enforce_map_object_rate_limit(request, bucket=f"map-object-create:{garden_id}")
    db.execute("SELECT id FROM gardens WHERE id = %s FOR UPDATE", (garden_id,))
    count_row = db.execute(
        "SELECT COUNT(*) AS c FROM garden_map_objects WHERE garden_id = %s",
        (garden_id,),
    ).fetchone()
    if int(count_row["c"] if count_row else 0) >= MAX_MAP_OBJECTS_PER_GARDEN:
        raise HTTPException(status_code=400, detail="Map object limit reached for this garden")

    grid_rows, grid_cols = _garden_size(db, garden_id)
    geometry = _geometry_dict(body.geometry)
    _validate_geometry_fits(geometry, rows=grid_rows, cols=grid_cols, label="Map object")
    style = _style_dict(body.style)
    internal_layout = _layout_dict(body.internal_layout)
    now_ms = current_timestamp_ms()
    public_id = _next_public_id(db, table="garden_map_objects", prefix="mapobj")
    try:
        row = db.execute(
            """
            INSERT INTO garden_map_objects (
                public_id, garden_id, object_type, name, shape_type,
                geometry_json, style_json, z_index, has_internal_layout,
                internal_layout_json, created_by_user_id, created_at_ms, updated_at_ms
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                public_id,
                garden_id,
                body.object_type,
                body.name.strip(),
                body.shape_type,
                _dump_json(cast(dict[str, object], geometry)),
                _dump_json(cast(dict[str, object], style)),
                body.z_index,
                1 if body.has_internal_layout else 0,
                _dump_json(cast(dict[str, object], internal_layout)),
                context.user_id,
                now_ms,
                now_ms,
            ),
        ).fetchone()
        db.commit()
    except psycopg.IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Map object conflict") from exc

    notify_garden_modified()
    row_dict = dict(row)
    _audit_map_object_change(
        request,
        context,
        db=db,
        garden_id=garden_id,
        event="garden.map_object.create",
        fields={"garden_id": garden_id, "public_id": public_id},
        status_code=201,
    )
    return _serialize_object(row_dict, [])


@router.patch("/gardens/{garden_id}/map-objects/{object_public_id}")
def update_map_object(
    garden_id: int,
    object_public_id: str,
    body: UpdateMapObjectBody,
    db: DB,
    request: Request,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_editor(db, context=context, garden_id=garden_id)
    existing = _object_row_by_public_id(
        db,
        garden_id=garden_id,
        object_public_id=object_public_id,
        for_update=True,
    )

    updates: list[str] = []
    params: list[object] = []
    if body.object_type is not None:
        updates.append("object_type = %s")
        params.append(body.object_type)
    if body.name is not None:
        updates.append("name = %s")
        params.append(body.name.strip())
    if body.shape_type is not None:
        updates.append("shape_type = %s")
        params.append(body.shape_type)
    if body.geometry is not None:
        grid_rows, grid_cols = _garden_size(db, garden_id)
        geometry = _geometry_dict(body.geometry)
        _validate_geometry_fits(geometry, rows=grid_rows, cols=grid_cols, label="Map object")
        updates.append("geometry_json = %s")
        params.append(_dump_json(cast(dict[str, object], geometry)))
    if body.style is not None:
        updates.append("style_json = %s")
        params.append(_dump_json(cast(dict[str, object], _style_dict(body.style))))
    if body.z_index is not None:
        updates.append("z_index = %s")
        params.append(body.z_index)
    if body.has_internal_layout is not None:
        updates.append("has_internal_layout = %s")
        params.append(1 if body.has_internal_layout else 0)
    if body.internal_layout is not None:
        layout = _layout_dict(body.internal_layout)
        updates.append("internal_layout_json = %s")
        params.append(_dump_json(cast(dict[str, object], layout)))
    if not updates:
        containers = [
            _serialize_container(container)
            for container in _canonical_container_rows(
                db,
                garden_id=garden_id,
            )
            if container.get("parent_map_object_id") == int(existing["id"])
        ]
        return _serialize_object(existing, containers)

    updates.append("updated_at_ms = %s")
    params.append(current_timestamp_ms())
    params.extend([garden_id, object_public_id])
    row = db.execute(
        f"""
        UPDATE garden_map_objects
        SET {", ".join(updates)}
        WHERE garden_id = %s AND public_id = %s
        RETURNING *
        """,
        params,
    ).fetchone()
    if row is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Map object not found")
    db.commit()
    notify_garden_modified()
    row_dict = dict(row)
    _audit_map_object_change(
        request,
        context,
        db=db,
        garden_id=garden_id,
        event="garden.map_object.update",
        fields={"garden_id": garden_id, "public_id": object_public_id},
    )
    containers = [
        _serialize_container(container)
        for container in _canonical_container_rows(
            db,
            garden_id=garden_id,
        )
        if container.get("parent_map_object_id") == int(row_dict["id"])
    ]
    return _serialize_object(row_dict, containers)


@router.delete("/gardens/{garden_id}/map-objects/{object_public_id}")
def delete_map_object(
    garden_id: int,
    object_public_id: str,
    db: DB,
    request: Request,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_editor(db, context=context, garden_id=garden_id)
    existing = _object_row_by_public_id(
        db,
        garden_id=garden_id,
        object_public_id=object_public_id,
        for_update=True,
    )
    unparented_rows = db.execute(
        """
        UPDATE plots
        SET parent_map_object_id = NULL
        WHERE garden_id = %s AND parent_map_object_id = %s
        RETURNING plot_id
        """,
        (garden_id, int(existing["id"])),
    ).fetchall()
    unparented_count = len(unparented_rows)
    deleted = db.execute(
        """
        DELETE FROM garden_map_objects
        WHERE garden_id = %s AND public_id = %s
        RETURNING id
        """,
        (garden_id, object_public_id),
    ).fetchone()
    if deleted is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Map object not found")
    db.commit()
    notify_garden_modified()
    _audit_map_object_change(
        request,
        context,
        db=db,
        garden_id=garden_id,
        event="garden.map_object.delete",
        fields={"garden_id": garden_id, "public_id": object_public_id},
    )
    return {"status": "ok", "unparented_containers": unparented_count}


@router.get("/gardens/{garden_id}/containers/{plot_id}")
def get_container(
    garden_id: int,
    plot_id: str,
    db: DB,
    request: Request,
) -> dict[str, object]:
    context = _auth_context(request)
    role = _membership_role(db, context=context, garden_id=garden_id)
    return _container_response(
        db,
        garden_id=garden_id,
        plot_id=plot_id,
        include_archived=True,
        role=role,
    )


@router.post("/gardens/{garden_id}/containers", status_code=201)
def create_container(
    garden_id: int,
    body: CreateContainerBody,
    db: DB,
    request: Request,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_editor(db, context=context, garden_id=garden_id)
    _enforce_map_object_rate_limit(request, bucket=f"container-create:{garden_id}")
    db.execute("SELECT id FROM gardens WHERE id = %s FOR UPDATE", (garden_id,))

    parent_map_object_id: int | None = None
    if body.parent_object_public_id is not None:
        parent = _object_row_by_public_id(
            db,
            garden_id=garden_id,
            object_public_id=body.parent_object_public_id,
            for_update=True,
        )
        if str(parent["object_type"]) not in AREA_TYPES:
            raise HTTPException(status_code=400, detail="Containers require an area parent")
        parent_map_object_id = int(parent["id"])
        default_environment: ContainerEnvironment = (
            "covered" if str(parent["object_type"]) == "greenhouse" else "outdoor"
        )
    else:
        default_environment = "outdoor"

    plot_id = _next_container_plot_id(db)
    owner_user_id = _container_owner_user_id(
        db,
        garden_id=garden_id,
        preferred_user_id=context.user_id,
    )
    try:
        db.execute(
            """
            INSERT INTO plots (
                plot_id, garden_id, zone_code, zone_name, plot_number,
                grid_row, grid_col, sub_zone, notes, color,
                plot_kind, display_name, container_type, parent_map_object_id,
                environment, archived_at_ms
            )
            VALUES (%s, %s, 'C', 'Containers', 0, NULL, NULL, '', '', NULL,
                    'container', %s, %s, %s, %s, NULL)
            """,
            (
                plot_id,
                garden_id,
                body.name,
                body.container_type,
                parent_map_object_id,
                body.environment or default_environment,
            ),
        )
        db.execute(
            """
            INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
            VALUES (%s, %s, %s)
            """,
            (plot_id, owner_user_id, garden_id),
        )
        db.commit()
    except psycopg.IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Container conflict") from exc

    notify_garden_modified()
    _audit_map_object_change(
        request,
        context,
        db=db,
        garden_id=garden_id,
        event="garden.container.create",
        fields={
            "garden_id": garden_id,
            "plot_id": plot_id,
            "parent_object_public_id": body.parent_object_public_id,
        },
        status_code=201,
    )
    return _container_response(
        db,
        garden_id=garden_id,
        plot_id=plot_id,
        role=_membership_role(db, context=context, garden_id=garden_id),
    )


@router.patch("/gardens/{garden_id}/containers/{plot_id}")
def update_container(
    garden_id: int,
    plot_id: str,
    body: UpdateContainerBody,
    db: DB,
    request: Request,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_editor(db, context=context, garden_id=garden_id)
    rows = _canonical_container_rows(
        db,
        garden_id=garden_id,
        include_archived=True,
        plot_id=plot_id,
        for_update=True,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Container not found")
    existing = rows[0]
    if existing.get("archived_at_ms") is not None:
        raise HTTPException(status_code=410, detail="Container is archived")

    updates: list[str] = []
    params: list[object] = []
    if body.name is not None:
        updates.append("display_name = %s")
        params.append(body.name)
    if body.container_type is not None:
        updates.append("container_type = %s")
        params.append(body.container_type)

    parent_changed = "parent_object_public_id" in body.model_fields_set
    parent_map_object_id: int | None = (
        int(existing["parent_map_object_id"])
        if existing.get("parent_map_object_id") is not None
        else None
    )
    if parent_changed:
        if body.parent_object_public_id is None:
            parent_map_object_id = None
        else:
            parent_map_object_id = _area_parent_id(
                db,
                garden_id=garden_id,
                object_public_id=body.parent_object_public_id,
                for_update=True,
            )
        updates.append("parent_map_object_id = %s")
        params.append(parent_map_object_id)
    if body.environment is not None:
        updates.append("environment = %s")
        params.append(body.environment)
    elif parent_changed:
        if body.parent_object_public_id is None:
            updates.append("environment = %s")
            params.append("outdoor")
        else:
            parent = _object_row_by_public_id(
                db,
                garden_id=garden_id,
                object_public_id=body.parent_object_public_id,
            )
            updates.append("environment = %s")
            params.append("covered" if parent["object_type"] == "greenhouse" else "outdoor")

    if updates:
        params.extend([plot_id, garden_id])
        db.execute(
            f"""
            UPDATE plots
            SET {", ".join(updates)}
            WHERE plot_id = %s AND garden_id = %s AND plot_kind = 'container'
            """,
            params,
        )
        db.commit()
        notify_garden_modified()
        _audit_map_object_change(
            request,
            context,
            db=db,
            garden_id=garden_id,
            event="garden.container.update",
            fields={"garden_id": garden_id, "plot_id": plot_id},
        )
    return _container_response(
        db,
        garden_id=garden_id,
        plot_id=plot_id,
        role=_membership_role(db, context=context, garden_id=garden_id),
    )


@router.delete("/gardens/{garden_id}/containers/{plot_id}")
def archive_container(
    garden_id: int,
    plot_id: str,
    db: DB,
    request: Request,
) -> dict[str, object]:
    context = _auth_context(request)
    role = _membership_role(db, context=context, garden_id=garden_id)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Garden admin required")

    rows = _canonical_container_rows(
        db,
        garden_id=garden_id,
        include_archived=True,
        plot_id=plot_id,
        for_update=True,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Container not found")
    existing = rows[0]
    if existing.get("archived_at_ms") is not None:
        return {
            "status": "already_archived",
            "plot_id": plot_id,
            "archived_at_ms": int(existing["archived_at_ms"]),
        }
    plant_count = int(existing.get("plant_count") or 0)
    plant_quantity = int(existing.get("plant_quantity") or 0)
    if plant_quantity > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Move plants before archiving this container",
                "plant_count": plant_count,
                "plant_quantity": plant_quantity,
            },
        )

    archived_at_ms = current_timestamp_ms()
    db.execute(
        """
        UPDATE plots
        SET archived_at_ms = %s, parent_map_object_id = NULL
        WHERE plot_id = %s AND garden_id = %s AND plot_kind = 'container'
        """,
        (archived_at_ms, plot_id, garden_id),
    )
    db.commit()
    notify_garden_modified()
    _audit_map_object_change(
        request,
        context,
        db=db,
        garden_id=garden_id,
        event="garden.container.archive",
        fields={"garden_id": garden_id, "plot_id": plot_id},
    )
    return {"status": "archived", "plot_id": plot_id, "archived_at_ms": archived_at_ms}


def _legacy_unit_mutation() -> None:
    raise HTTPException(
        status_code=410,
        detail="Nested map units are retired; use canonical containers",
    )


@router.post("/gardens/{garden_id}/map-objects/{object_public_id}/units", status_code=201)
def create_map_object_unit(
    garden_id: int,
    object_public_id: str,
) -> None:
    _legacy_unit_mutation()


@router.patch("/gardens/{garden_id}/map-objects/{object_public_id}/units/{unit_public_id}")
def update_map_object_unit(
    garden_id: int,
    object_public_id: str,
    unit_public_id: str,
) -> None:
    _legacy_unit_mutation()


@router.delete("/gardens/{garden_id}/map-objects/{object_public_id}/units/{unit_public_id}")
def delete_map_object_unit(
    garden_id: int,
    object_public_id: str,
    unit_public_id: str,
) -> None:
    _legacy_unit_mutation()
