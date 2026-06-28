import json
import re
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

MapObjectType = Literal["patio", "terrace", "greenhouse", "shed", "pond", "path", "bed", "other"]
MapObjectShape = Literal["rectangle", "ellipse"]
MapObjectUnitType = Literal["pot", "planter", "raised_bed", "shelf", "other"]

SAFE_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
DEFAULT_INTERNAL_LAYOUT = {"rows": 6, "cols": 8}
MAX_MAP_OBJECTS_PER_GARDEN = 200
MAX_UNITS_PER_OBJECT = 100
PUBLIC_ID_TABLES = frozenset({"garden_map_objects", "garden_map_object_units"})


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


class CreateMapObjectUnitBody(StrictBaseModel):
    unit_type: MapObjectUnitType
    name: str = Field(min_length=1, max_length=120)
    shape_type: MapObjectShape
    geometry: MapObjectGeometryBody
    style: MapObjectStyleBody = Field(default_factory=MapObjectStyleBody)
    sort_order: int = Field(default=0, ge=-1000, le=1000)


class UpdateMapObjectUnitBody(StrictBaseModel):
    unit_type: MapObjectUnitType | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    shape_type: MapObjectShape | None = None
    geometry: MapObjectGeometryBody | None = None
    style: MapObjectStyleBody | None = None
    sort_order: int | None = Field(default=None, ge=-1000, le=1000)


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
) -> None:
    request.state.audited_by_handler = True
    detail = f"{event} {json.dumps(fields, sort_keys=True, separators=(',', ':'))}"
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
    if geometry["x"] + geometry["width"] - 1 > cols or geometry["y"] + geometry["height"] - 1 > rows:
        raise HTTPException(status_code=400, detail=f"{label} does not fit within the layout")


def _next_public_id(db: DbConn, *, table: str, prefix: str) -> str:
    if table not in PUBLIC_ID_TABLES:
        raise RuntimeError("Unsupported public id table")
    for _ in range(10):
        public_id = generate_public_id(prefix)
        row = db.execute(f"SELECT 1 FROM {table} WHERE public_id = %s LIMIT 1", (public_id,)).fetchone()
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


def _serialize_unit(row: dict[str, Any]) -> dict[str, object]:
    return {
        "public_id": str(row["public_id"]),
        "unit_type": str(row["unit_type"]),
        "name": str(row["name"]),
        "shape_type": str(row["shape_type"]),
        "geometry": _loads_dict(row["geometry_json"], {}),
        "style": _loads_dict(row["style_json"], {"color": "#7d9f7a"}),
        "sort_order": int(row["sort_order"]),
        "created_at_ms": int(row["created_at_ms"]),
        "updated_at_ms": int(row["updated_at_ms"]),
    }


def _serialize_object(row: dict[str, Any], units: list[dict[str, object]]) -> dict[str, object]:
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
        "units": units,
    }


def _export_unit(row: dict[str, Any]) -> dict[str, object]:
    unit = _serialize_unit(row)
    return {
        "public_id": unit["public_id"],
        "unit_type": unit["unit_type"],
        "name": unit["name"],
        "shape_type": unit["shape_type"],
        "geometry": unit["geometry"],
        "style": unit["style"],
        "sort_order": unit["sort_order"],
    }


def _export_object(row: dict[str, Any], units: list[dict[str, object]]) -> dict[str, object]:
    item = _serialize_object(row, units)
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
        "units": units,
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

    object_ids = [int(row["id"]) for row in object_rows]
    placeholders = ",".join("%s" for _ in object_ids)
    unit_rows = db.execute(
        f"""
        SELECT *
        FROM garden_map_object_units
        WHERE garden_id = %s AND map_object_id IN ({placeholders})
        ORDER BY sort_order, id
        """,
        [garden_id, *object_ids],
    ).fetchall()
    units_by_object: dict[int, list[dict[str, object]]] = {}
    for unit in unit_rows:
        unit_dict = dict(unit)
        units_by_object.setdefault(int(unit_dict["map_object_id"]), []).append(
            _export_unit(unit_dict),
        )
    return [
        _export_object(dict(row), units_by_object.get(int(row["id"]), []))
        for row in object_rows
    ]


def replace_map_objects(
    db: DbConn,
    *,
    garden_id: int,
    map_objects: list[dict[str, Any]] | None,
    created_by_user_id: int | None,
) -> int:
    db.execute("DELETE FROM garden_map_objects WHERE garden_id = %s", (garden_id,))
    if map_objects is None:
        return 0
    if len(map_objects) > MAX_MAP_OBJECTS_PER_GARDEN:
        raise HTTPException(status_code=400, detail="Map object limit reached for this garden")

    grid_rows, grid_cols = _garden_size(db, garden_id)
    used_object_public_ids: set[str] = set()
    used_unit_public_ids: set[str] = set()
    now_ms = current_timestamp_ms()
    inserted = 0

    for raw_item in map_objects:
        item = MapObjectImportItem.model_validate(raw_item)
        geometry = item.geometry.model_dump()
        _validate_geometry_fits(geometry, rows=grid_rows, cols=grid_cols, label="Map object")
        if not item.has_internal_layout and item.units:
            raise HTTPException(
                status_code=400,
                detail="Nested units require a map object with an internal layout",
            )
        layout = _layout_dict(item.internal_layout)
        for unit in item.units:
            unit_geometry = unit.geometry.model_dump()
            _validate_geometry_fits(
                unit_geometry,
                rows=layout["rows"],
                cols=layout["cols"],
                label="Nested unit",
            )

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
        for unit in item.units:
            unit_public_id = _import_public_id(
                db,
                table="garden_map_object_units",
                prefix="mapunit",
                requested_public_id=unit.public_id,
                used=used_unit_public_ids,
            )
            db.execute(
                """
                INSERT INTO garden_map_object_units (
                    public_id, garden_id, map_object_id, unit_type, name, shape_type,
                    geometry_json, style_json, sort_order, created_at_ms, updated_at_ms
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    unit_public_id,
                    garden_id,
                    map_object_id,
                    unit.unit_type,
                    unit.name.strip(),
                    unit.shape_type,
                    _dump_json(cast(dict[str, object], unit.geometry.model_dump())),
                    _dump_json(cast(dict[str, object], unit.style.model_dump())),
                    unit.sort_order,
                    now_ms,
                    now_ms,
                ),
            )
        inserted += 1
    return inserted


def _object_row_by_public_id(
    db: DbConn,
    *,
    garden_id: int,
    object_public_id: str,
) -> dict[str, Any]:
    row = db.execute(
        """
        SELECT *
        FROM garden_map_objects
        WHERE garden_id = %s AND public_id = %s
        LIMIT 1
        """,
        (garden_id, object_public_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Map object not found")
    return dict(row)


def _object_units(db: DbConn, *, garden_id: int, map_object_id: int) -> list[dict[str, object]]:
    rows = db.execute(
        """
        SELECT *
        FROM garden_map_object_units
        WHERE garden_id = %s AND map_object_id = %s
        ORDER BY sort_order, id
        """,
        (garden_id, map_object_id),
    ).fetchall()
    return [_serialize_unit(dict(row)) for row in rows]


def _serialize_object_with_units(db: DbConn, row: dict[str, Any]) -> dict[str, object]:
    return _serialize_object(
        row,
        _object_units(db, garden_id=int(row["garden_id"]), map_object_id=int(row["id"])),
    )


@router.get("/gardens/{garden_id}/map-objects")
def list_map_objects(garden_id: int, db: DB, request: Request) -> dict[str, list[dict[str, object]]]:
    context = _auth_context(request)
    _require_member(db, context=context, garden_id=garden_id)
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
        return {"objects": []}

    object_ids = [int(row["id"]) for row in object_rows]
    placeholders = ",".join("%s" for _ in object_ids)
    unit_rows = db.execute(
        f"""
        SELECT *
        FROM garden_map_object_units
        WHERE garden_id = %s AND map_object_id IN ({placeholders})
        ORDER BY sort_order, id
        """,
        [garden_id, *object_ids],
    ).fetchall()
    units_by_object: dict[int, list[dict[str, object]]] = {}
    for unit in unit_rows:
        unit_dict = dict(unit)
        units_by_object.setdefault(int(unit_dict["map_object_id"]), []).append(
            _serialize_unit(unit_dict),
        )

    return {
        "objects": [
            _serialize_object(dict(row), units_by_object.get(int(row["id"]), []))
            for row in object_rows
        ],
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
    existing = _object_row_by_public_id(db, garden_id=garden_id, object_public_id=object_public_id)

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
        for unit in _object_units(db, garden_id=garden_id, map_object_id=int(existing["id"])):
            unit_geometry = cast(dict[str, int], unit["geometry"])
            _validate_geometry_fits(
                unit_geometry,
                rows=layout["rows"],
                cols=layout["cols"],
                label="Nested unit",
            )
        updates.append("internal_layout_json = %s")
        params.append(_dump_json(cast(dict[str, object], layout)))
    if not updates:
        return _serialize_object_with_units(db, existing)

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
    db.commit()
    notify_garden_modified()
    assert row is not None
    row_dict = dict(row)
    _audit_map_object_change(
        request,
        context,
        db=db,
        garden_id=garden_id,
        event="garden.map_object.update",
        fields={"garden_id": garden_id, "public_id": object_public_id},
    )
    return _serialize_object_with_units(db, row_dict)


@router.delete("/gardens/{garden_id}/map-objects/{object_public_id}")
def delete_map_object(
    garden_id: int,
    object_public_id: str,
    db: DB,
    request: Request,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_editor(db, context=context, garden_id=garden_id)
    existing = _object_row_by_public_id(db, garden_id=garden_id, object_public_id=object_public_id)
    unit_count_row = db.execute(
        """
        SELECT COUNT(*) AS c
        FROM garden_map_object_units
        WHERE garden_id = %s AND map_object_id = %s
        """,
        (garden_id, int(existing["id"])),
    ).fetchone()
    deleted_units = int(unit_count_row["c"] if unit_count_row else 0)
    db.execute(
        "DELETE FROM garden_map_objects WHERE garden_id = %s AND public_id = %s",
        (garden_id, object_public_id),
    )
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
    return {"status": "ok", "deleted_units": deleted_units}


@router.post("/gardens/{garden_id}/map-objects/{object_public_id}/units", status_code=201)
def create_map_object_unit(
    garden_id: int,
    object_public_id: str,
    body: CreateMapObjectUnitBody,
    db: DB,
    request: Request,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_editor(db, context=context, garden_id=garden_id)
    _enforce_map_object_rate_limit(request, bucket=f"map-object-unit-create:{garden_id}")
    parent = _object_row_by_public_id(db, garden_id=garden_id, object_public_id=object_public_id)
    if not bool(int(parent["has_internal_layout"])):
        raise HTTPException(status_code=400, detail="Map object does not have an internal layout")
    unit_count = db.execute(
        """
        SELECT COUNT(*) AS c
        FROM garden_map_object_units
        WHERE garden_id = %s AND map_object_id = %s
        """,
        (garden_id, int(parent["id"])),
    ).fetchone()
    if int(unit_count["c"] if unit_count else 0) >= MAX_UNITS_PER_OBJECT:
        raise HTTPException(status_code=400, detail="Nested unit limit reached for this map object")

    layout = cast(dict[str, int], _loads_dict(parent["internal_layout_json"], DEFAULT_INTERNAL_LAYOUT))
    geometry = _geometry_dict(body.geometry)
    _validate_geometry_fits(geometry, rows=layout["rows"], cols=layout["cols"], label="Nested unit")
    now_ms = current_timestamp_ms()
    public_id = _next_public_id(db, table="garden_map_object_units", prefix="mapunit")
    try:
        row = db.execute(
            """
            INSERT INTO garden_map_object_units (
                public_id, garden_id, map_object_id, unit_type, name, shape_type,
                geometry_json, style_json, sort_order, created_at_ms, updated_at_ms
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                public_id,
                garden_id,
                int(parent["id"]),
                body.unit_type,
                body.name.strip(),
                body.shape_type,
                _dump_json(cast(dict[str, object], geometry)),
                _dump_json(cast(dict[str, object], _style_dict(body.style))),
                body.sort_order,
                now_ms,
                now_ms,
            ),
        ).fetchone()
        db.commit()
    except psycopg.IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Nested unit conflict") from exc

    notify_garden_modified()
    assert row is not None
    _audit_map_object_change(
        request,
        context,
        db=db,
        garden_id=garden_id,
        event="garden.map_object_unit.create",
        fields={"garden_id": garden_id, "object_public_id": object_public_id, "public_id": public_id},
    )
    return _serialize_unit(dict(row))


@router.patch("/gardens/{garden_id}/map-objects/{object_public_id}/units/{unit_public_id}")
def update_map_object_unit(
    garden_id: int,
    object_public_id: str,
    unit_public_id: str,
    body: UpdateMapObjectUnitBody,
    db: DB,
    request: Request,
) -> dict[str, object]:
    context = _auth_context(request)
    _require_editor(db, context=context, garden_id=garden_id)
    parent = _object_row_by_public_id(db, garden_id=garden_id, object_public_id=object_public_id)
    unit = db.execute(
        """
        SELECT *
        FROM garden_map_object_units
        WHERE garden_id = %s AND map_object_id = %s AND public_id = %s
        LIMIT 1
        """,
        (garden_id, int(parent["id"]), unit_public_id),
    ).fetchone()
    if not unit:
        raise HTTPException(status_code=404, detail="Nested unit not found")

    updates: list[str] = []
    params: list[object] = []
    if body.unit_type is not None:
        updates.append("unit_type = %s")
        params.append(body.unit_type)
    if body.name is not None:
        updates.append("name = %s")
        params.append(body.name.strip())
    if body.shape_type is not None:
        updates.append("shape_type = %s")
        params.append(body.shape_type)
    if body.geometry is not None:
        layout = cast(dict[str, int], _loads_dict(parent["internal_layout_json"], DEFAULT_INTERNAL_LAYOUT))
        geometry = _geometry_dict(body.geometry)
        _validate_geometry_fits(geometry, rows=layout["rows"], cols=layout["cols"], label="Nested unit")
        updates.append("geometry_json = %s")
        params.append(_dump_json(cast(dict[str, object], geometry)))
    if body.style is not None:
        updates.append("style_json = %s")
        params.append(_dump_json(cast(dict[str, object], _style_dict(body.style))))
    if body.sort_order is not None:
        updates.append("sort_order = %s")
        params.append(body.sort_order)
    if not updates:
        return _serialize_unit(dict(unit))

    updates.append("updated_at_ms = %s")
    params.append(current_timestamp_ms())
    params.extend([garden_id, int(parent["id"]), unit_public_id])
    row = db.execute(
        f"""
        UPDATE garden_map_object_units
        SET {", ".join(updates)}
        WHERE garden_id = %s AND map_object_id = %s AND public_id = %s
        RETURNING *
        """,
        params,
    ).fetchone()
    db.commit()
    notify_garden_modified()
    assert row is not None
    _audit_map_object_change(
        request,
        context,
        db=db,
        garden_id=garden_id,
        event="garden.map_object_unit.update",
        fields={"garden_id": garden_id, "object_public_id": object_public_id, "public_id": unit_public_id},
    )
    return _serialize_unit(dict(row))


@router.delete("/gardens/{garden_id}/map-objects/{object_public_id}/units/{unit_public_id}")
def delete_map_object_unit(
    garden_id: int,
    object_public_id: str,
    unit_public_id: str,
    db: DB,
    request: Request,
) -> dict[str, str]:
    context = _auth_context(request)
    _require_editor(db, context=context, garden_id=garden_id)
    parent = _object_row_by_public_id(db, garden_id=garden_id, object_public_id=object_public_id)
    cursor = db.execute(
        """
        DELETE FROM garden_map_object_units
        WHERE garden_id = %s AND map_object_id = %s AND public_id = %s
        """,
        (garden_id, int(parent["id"]), unit_public_id),
    )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Nested unit not found")
    db.commit()
    notify_garden_modified()
    _audit_map_object_change(
        request,
        context,
        db=db,
        garden_id=garden_id,
        event="garden.map_object_unit.delete",
        fields={"garden_id": garden_id, "object_public_id": object_public_id, "public_id": unit_public_id},
    )
    return {"status": "ok"}
