import re as _re
from collections import defaultdict
from datetime import date

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field, field_validator

from gardenops.db import DB, DbConn, executemany
from gardenops.events import notify_garden_modified
from gardenops.models import StrictBaseModel
from gardenops.router_helpers import (
    active_garden_id as _active_garden_id,
)
from gardenops.router_helpers import (
    auth_context as _auth_context,
)
from gardenops.router_helpers import (
    effective_role,
    is_owner_or_admin,
)
from gardenops.router_helpers import (
    is_local_admin_fallback as _is_local_admin_fallback,
)
from gardenops.security import AuthContext, create_user, has_write_access, is_auth_required
from gardenops.services.garden_layout_lock import lock_garden_layout
from gardenops.services.media_store import unlink_storage_keys
from gardenops.services.planting_planner import check_companions
from gardenops.services.plot_references import (
    delete_plot_references,
    load_plot_delete_impact,
    rename_plot_references,
)

router = APIRouter()


def _sanitize_room_label(v: str | None) -> str | None:
    if v is None:
        return None
    return _re.sub(r"[\x00-\x1f\x7f]", "", v)


class AddPlantBody(StrictBaseModel):
    quantity: int = Field(default=1, ge=1)
    room_label: str | None = Field(default=None, max_length=50)

    @field_validator("room_label", mode="before")
    @classmethod
    def strip_control_chars(cls, v: str | None) -> str | None:
        return _sanitize_room_label(v)


class UpdateQuantityBody(StrictBaseModel):
    quantity: int = Field(ge=1)
    room_label: str | None = Field(default=None, max_length=50)

    @field_validator("room_label", mode="before")
    @classmethod
    def strip_control_chars(cls, v: str | None) -> str | None:
        return _sanitize_room_label(v)


class CreatePlotBody(StrictBaseModel):
    plot_id: str
    zone_code: str
    zone_name: str
    plot_number: int
    grid_row: int = Field(ge=1, le=100)
    grid_col: int = Field(ge=1, le=100)
    sub_zone: str = ""
    notes: str = ""
    color: str | None = None


class UpdatePlotBody(StrictBaseModel):
    grid_row: int | None = Field(default=None, ge=1, le=100)
    grid_col: int | None = Field(default=None, ge=1, le=100)
    zone_code: str | None = None
    zone_name: str | None = None
    plot_number: int | None = None
    sub_zone: str | None = None
    notes: str | None = None
    color: str | None = None
    new_plot_id: str | None = None


class BatchMoveItem(StrictBaseModel):
    plot_id: str
    grid_row: int = Field(ge=1, le=100)
    grid_col: int = Field(ge=1, le=100)


class BatchMoveBody(StrictBaseModel):
    moves: list[BatchMoveItem] = Field(min_length=1)


def _effective_role(context: AuthContext) -> str:
    return effective_role(context)


def _is_owner_or_admin(context: AuthContext, owner_user_id: int | None) -> bool:
    return is_owner_or_admin(context, owner_user_id)


def _owner_user_for_plot_write(db: DbConn, *, garden_id: int, context: AuthContext) -> int:
    if context.user_id is not None:
        return int(context.user_id)
    row = db.execute(
        """
        SELECT gm.user_id
        FROM garden_memberships gm
        JOIN auth_users u ON u.id = gm.user_id
        WHERE gm.garden_id = %s AND u.is_active = 1
        ORDER BY CASE gm.role
            WHEN 'admin' THEN 0
            WHEN 'editor' THEN 1
            ELSE 2
        END, gm.user_id
        LIMIT 1
        """,
        (garden_id,),
    ).fetchone()
    if row:
        return int(row["user_id"])
    if not is_auth_required():
        fallback = db.execute(
            """
            SELECT id
            FROM auth_users
            WHERE username = '__local_admin__' AND is_active = 1
            LIMIT 1
            """,
        ).fetchone()
        if fallback:
            fallback_user_id = int(fallback["id"])
        else:
            created = create_user(
                db,
                username="__local_admin__",
                password="local-admin-bootstrap",
                role="admin",
            )
            fallback_user_id = int(created["id"])
        db.execute(
            """
            INSERT INTO garden_memberships (garden_id, user_id, role)
            VALUES (%s, %s, 'admin')
            ON CONFLICT(garden_id, user_id) DO UPDATE SET
                role = excluded.role
            """,
            (garden_id, fallback_user_id),
        )
        return fallback_user_id
    raise HTTPException(
        status_code=409,
        detail="No active garden member is available to own plots",
    )


def _require_plot_access(
    db: DbConn,
    plot_id: str,
    context: AuthContext,
    *,
    read_only: bool = False,
) -> None:
    garden_id = _active_garden_id(context)
    row = db.execute(
        """
        SELECT po.owner_user_id, po.garden_id
        FROM plots p
        LEFT JOIN plot_ownership po ON po.plot_id = p.plot_id
        WHERE p.plot_id = %s
        """,
        (plot_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Plot not found")
    if row["garden_id"] is None:
        if _is_local_admin_fallback(context):
            return
        raise HTTPException(status_code=404, detail="Plot not found")
    if int(row["garden_id"]) != garden_id:
        raise HTTPException(status_code=404, detail="Plot not found")
    # Editors can read any plot in their garden
    if read_only and _effective_role(context) in {"admin", "editor"}:
        return
    if not _is_owner_or_admin(context, row["owner_user_id"]):
        raise HTTPException(status_code=404, detail="Plot not found")


def _require_plot_in_garden_or_unowned(
    db: DbConn,
    plot_id: str,
    context: AuthContext,
) -> None:
    """Allow access if plot is in this garden, unowned, or doesn't exist (custom assignment).

    Reject if the plot exists and belongs to a different garden.
    """
    garden_id = _active_garden_id(context)
    row = db.execute(
        """
        SELECT po.garden_id
        FROM plots p
        LEFT JOIN plot_ownership po ON po.plot_id = p.plot_id
        WHERE p.plot_id = %s
        """,
        (plot_id,),
    ).fetchone()
    if not row:
        return  # Plot doesn't exist in DB — custom assignment, allowed
    if row["garden_id"] is None:
        return  # No ownership record — unowned, allowed
    if int(row["garden_id"]) != garden_id:
        raise HTTPException(status_code=404, detail="Plot not found")


def _require_plant_access(db: DbConn, plt_id: str, context: AuthContext) -> None:
    garden_id = _active_garden_id(context)
    plant_exists = db.execute(
        "SELECT 1 FROM plants WHERE plt_id = %s",
        (plt_id,),
    ).fetchone()
    if not plant_exists:
        raise HTTPException(status_code=404, detail="Plant not found")
    row = db.execute(
        """
        SELECT po.owner_user_id, po.garden_id
        FROM plant_ownership po
        WHERE po.plt_id = %s AND po.garden_id = %s
        """,
        (plt_id, garden_id),
    ).fetchone()
    if not row:
        if _is_local_admin_fallback(context):
            return
        raise HTTPException(status_code=404, detail="Plant not found")
    if not _is_owner_or_admin(context, row["owner_user_id"]):
        raise HTTPException(status_code=404, detail="Plant not found")


def _plot_exists_in_scope(
    db: DbConn,
    plot_id: str,
    context: AuthContext,
) -> bool:
    garden_id = _active_garden_id(context)
    if _is_local_admin_fallback(context):
        row = db.execute(
            "SELECT 1 FROM plots WHERE plot_id = %s",
            (plot_id,),
        ).fetchone()
    elif _effective_role(context) in {"admin", "editor"}:
        row = db.execute(
            """
            SELECT 1
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE p.plot_id = %s AND po.garden_id = %s
            """,
            (plot_id, garden_id),
        ).fetchone()
    else:
        row = db.execute(
            """
            SELECT 1
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE p.plot_id = %s AND po.garden_id = %s AND po.owner_user_id = %s
            """,
            (plot_id, garden_id, context.user_id),
        ).fetchone()
    return bool(row)


def _visible_plot_ids_for_plants(
    db: DbConn,
    *,
    plt_ids: list[str],
    context: AuthContext,
) -> dict[str, list[str]]:
    if not plt_ids:
        return {}
    garden_id = _active_garden_id(context)
    placeholders = ",".join(["%s"] * len(plt_ids))
    if _is_local_admin_fallback(context):
        rows = db.execute(
            f"""
            SELECT pp.plt_id, pp.plot_id
            FROM plot_plants pp
            LEFT JOIN plot_ownership po ON po.plot_id = pp.plot_id
            WHERE pp.plt_id IN ({placeholders})
              AND (po.garden_id = %s OR po.garden_id IS NULL)
            ORDER BY pp.plot_id
            """,
            [*plt_ids, garden_id],
        ).fetchall()
    elif _effective_role(context) in {"admin", "editor"}:
        rows = db.execute(
            f"""
            SELECT pp.plt_id, pp.plot_id
            FROM plot_plants pp
            JOIN plot_ownership po ON po.plot_id = pp.plot_id
            WHERE pp.plt_id IN ({placeholders})
              AND po.garden_id = %s
            ORDER BY pp.plot_id
            """,
            [*plt_ids, garden_id],
        ).fetchall()
    else:
        rows = db.execute(
            f"""
            SELECT pp.plt_id, pp.plot_id
            FROM plot_plants pp
            JOIN plot_ownership po ON po.plot_id = pp.plot_id
            WHERE pp.plt_id IN ({placeholders})
              AND po.garden_id = %s
              AND po.owner_user_id = %s
            ORDER BY pp.plot_id
            """,
            [*plt_ids, garden_id, context.user_id],
        ).fetchall()
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        grouped[str(row["plt_id"])].append(str(row["plot_id"]))
    return grouped


def _assert_cell_free(
    db: DbConn,
    row: int,
    col: int,
    context: AuthContext,
    *,
    exclude_plot_id: str | None = None,
) -> None:
    if _is_local_admin_fallback(context):
        existing = db.execute(
            "SELECT plot_id FROM plots "
            "WHERE grid_row = %s AND grid_col = %s AND grid_row IS NOT NULL",
            (row, col),
        ).fetchone()
    elif _effective_role(context) in {"admin", "editor"}:
        existing = db.execute(
            """
            SELECT p.plot_id
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE p.grid_row = %s AND p.grid_col = %s
              AND po.garden_id = %s AND p.grid_row IS NOT NULL
            """,
            (row, col, _active_garden_id(context)),
        ).fetchone()
    else:
        existing = db.execute(
            """
            SELECT p.plot_id
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE p.grid_row = %s AND p.grid_col = %s
              AND po.garden_id = %s AND po.owner_user_id = %s
              AND p.grid_row IS NOT NULL
            """,
            (row, col, _active_garden_id(context), context.user_id),
        ).fetchone()
    if existing and existing["plot_id"] != exclude_plot_id:
        raise HTTPException(
            status_code=409,
            detail=f"Grid cell ({row}, {col}) is already occupied by {existing['plot_id']}",
        )


@router.get("/plots")
def list_plots(db: DB, request: Request, exclude_indoor: bool = Query(default=False)) -> list[dict]:
    """List all plots with their plant counts."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    where_extra = ""
    params: list[object] = []
    if _is_local_admin_fallback(context):
        where_extra = " AND (po.garden_id = %s OR po.garden_id IS NULL)"
        params.append(garden_id)
    elif _effective_role(context) in {"admin", "editor"}:
        where_extra = " AND po.garden_id = %s"
        params.append(garden_id)
    else:
        where_extra = " AND po.garden_id = %s AND po.owner_user_id = %s"
        params.append(garden_id)
        params.append(context.user_id)
    if exclude_indoor:
        where_extra += " AND p.grid_row IS NOT NULL"
    rows = db.execute(
        """
        SELECT p.*, COUNT(pown.plt_id) AS plant_count,
            SUM(CASE WHEN pl.category = 'trær'
                THEN 1 ELSE 0 END) AS _tree_count,
            SUM(CASE WHEN pl.category IN ('busker', 'baerbusker')
                THEN 1 ELSE 0 END) AS _bush_count
        FROM plots p
        LEFT JOIN plot_ownership po ON po.plot_id = p.plot_id
        LEFT JOIN plot_plants pp ON pp.plot_id = p.plot_id
        LEFT JOIN plant_ownership pown
            ON pown.plt_id = pp.plt_id
            AND (pown.garden_id = po.garden_id
                 OR po.garden_id IS NULL)
        LEFT JOIN plants pl ON pl.plt_id = pown.plt_id
        WHERE 1=1
    """
        + where_extra
        + """
        GROUP BY p.plot_id, po.owner_user_id
        ORDER BY p.zone_code, p.plot_number
    """,
        params,
    ).fetchall()
    plot_ids = [str(r["plot_id"]) for r in rows]
    cat_map: dict[str, set[str]] = {pid: set() for pid in plot_ids}
    if plot_ids:
        cat_rows = db.execute(
            f"""
            SELECT pp.plot_id, pl.category
            FROM plot_plants pp
            JOIN plants pl ON pl.plt_id = pp.plt_id
            WHERE pp.plot_id IN ({",".join("%s" for _ in plot_ids)})
              AND pl.category IS NOT NULL
              AND pl.category != ''
            """,
            plot_ids,
        ).fetchall()
        for cr in cat_rows:
            cat_map[str(cr["plot_id"])].add(str(cr["category"]))
    result = []
    for r in rows:
        d = dict(r)
        d["has_tree"] = bool(d.pop("_tree_count"))
        d["has_bush"] = bool(d.pop("_bush_count"))
        d["categories"] = sorted(cat_map.get(str(r["plot_id"]), set()))
        result.append(d)
    return result


@router.get("/plots/alerts")
def get_plot_alerts(db: DB, request: Request) -> dict:
    """Per-plot alert indicators for the map."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    today_iso = date.today().isoformat()

    task_plots = db.execute(
        "SELECT DISTINCT tp.plot_id "
        "FROM garden_task_plots tp "
        "JOIN garden_tasks t ON t.id = tp.task_id "
        "JOIN plots p ON p.plot_id = tp.plot_id "
        "WHERE t.garden_id = %s AND t.status = 'pending' "
        "AND t.due_on <= %s AND p.grid_row IS NOT NULL",
        (garden_id, today_iso),
    ).fetchall()

    issue_plots = db.execute(
        "SELECT DISTINCT ip.plot_id "
        "FROM garden_issue_plots ip "
        "JOIN garden_issues i ON i.id = ip.issue_id "
        "JOIN plots p ON p.plot_id = ip.plot_id "
        "WHERE i.garden_id = %s "
        "AND i.status IN ('open', 'monitoring', 'treating') "
        "AND p.grid_row IS NOT NULL",
        (garden_id,),
    ).fetchall()

    frost_plots = db.execute(
        "SELECT DISTINCT pp.plot_id "
        "FROM weather_alert_plants wap "
        "JOIN weather_alerts wa ON wa.id = wap.alert_id "
        "JOIN plot_plants pp ON pp.plt_id = wap.plt_id "
        "JOIN plots p ON p.plot_id = pp.plot_id "
        "WHERE wa.garden_id = %s AND wa.dismissed = 0 "
        "AND wa.valid_until >= %s AND p.grid_row IS NOT NULL",
        (garden_id, today_iso),
    ).fetchall()

    # Indoor alerts (separate section)
    indoor_task_plots = db.execute(
        "SELECT DISTINCT tp.plot_id "
        "FROM garden_task_plots tp "
        "JOIN garden_tasks t ON t.id = tp.task_id "
        "JOIN plots p ON p.plot_id = tp.plot_id "
        "WHERE t.garden_id = %s AND t.status = 'pending' "
        "AND t.due_on <= %s AND p.grid_row IS NULL",
        (garden_id, today_iso),
    ).fetchall()

    indoor_issue_plots = db.execute(
        "SELECT DISTINCT ip.plot_id "
        "FROM garden_issue_plots ip "
        "JOIN garden_issues i ON i.id = ip.issue_id "
        "JOIN plots p ON p.plot_id = ip.plot_id "
        "WHERE i.garden_id = %s "
        "AND i.status IN ('open', 'monitoring', 'treating') "
        "AND p.grid_row IS NULL",
        (garden_id,),
    ).fetchall()

    return {
        "task_plots": [r["plot_id"] for r in task_plots],
        "issue_plots": [r["plot_id"] for r in issue_plots],
        "frost_plots": [r["plot_id"] for r in frost_plots],
        "indoor_alerts": {
            "tasks": [r["plot_id"] for r in indoor_task_plots],
            "issues": [r["plot_id"] for r in indoor_issue_plots],
        },
    }


@router.get("/plots/{plot_id}/plant-alerts")
def get_plot_plant_alerts(
    plot_id: str,
    db: DB,
    request: Request,
) -> dict:
    """Per-plant alert types for plants in a specific plot."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    _require_plot_access(db, plot_id, context, read_only=True)
    today_iso = date.today().isoformat()

    task_plants = db.execute(
        "SELECT DISTINCT gtp2.plt_id "
        "FROM garden_task_plots tp "
        "JOIN garden_tasks t ON t.id = tp.task_id "
        "JOIN garden_task_plants gtp2 ON gtp2.task_id = t.id "
        "JOIN plot_plants pp ON pp.plt_id = gtp2.plt_id "
        "  AND pp.plot_id = tp.plot_id "
        "WHERE tp.plot_id = %s AND t.garden_id = %s "
        "  AND t.status = 'pending' AND t.due_on <= %s",
        (plot_id, garden_id, today_iso),
    ).fetchall()

    issue_plants = db.execute(
        "SELECT DISTINCT gip.plt_id "
        "FROM garden_issue_plots ip "
        "JOIN garden_issues i ON i.id = ip.issue_id "
        "JOIN garden_issue_plants gip ON gip.issue_id = i.id "
        "JOIN plot_plants pp ON pp.plt_id = gip.plt_id "
        "  AND pp.plot_id = ip.plot_id "
        "WHERE ip.plot_id = %s AND i.garden_id = %s "
        "  AND i.status IN ('open', 'monitoring', 'treating')",
        (plot_id, garden_id),
    ).fetchall()

    weather_plants = db.execute(
        "SELECT DISTINCT wap.plt_id "
        "FROM weather_alert_plants wap "
        "JOIN weather_alerts wa ON wa.id = wap.alert_id "
        "JOIN plot_plants pp ON pp.plt_id = wap.plt_id "
        "  AND pp.plot_id = %s "
        "WHERE wa.garden_id = %s AND wa.dismissed = 0 "
        "  AND wa.valid_until >= %s",
        (plot_id, garden_id, today_iso),
    ).fetchall()

    alerts: dict[str, list[str]] = {}
    for r in task_plants:
        alerts.setdefault(r["plt_id"], []).append("task")
    for r in issue_plants:
        alerts.setdefault(r["plt_id"], []).append("issue")
    for r in weather_plants:
        alerts.setdefault(r["plt_id"], []).append("weather")
    return {"plant_alerts": alerts}


@router.get("/plots/{plot_id}/room-labels")
def get_room_labels(plot_id: str, db: DB, request: Request) -> list[str]:
    """Return distinct non-null room labels for a plot (for autocomplete)."""
    context = _auth_context(request)
    _require_plot_in_garden_or_unowned(db, plot_id, context)
    rows = db.execute(
        "SELECT DISTINCT room_label FROM plot_plants "
        "WHERE plot_id = %s AND room_label IS NOT NULL "
        "ORDER BY room_label",
        (plot_id,),
    ).fetchall()
    return [r["room_label"] for r in rows]


@router.get("/plots/{plot_id}/plants")
def list_plot_plants(plot_id: str, db: DB, request: Request) -> list[dict]:
    """List all plants assigned to a specific plot, including their other plot assignments."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    _require_plot_access(db, plot_id, context, read_only=True)
    if _is_local_admin_fallback(context):
        owner_filter = " AND (pown.garden_id = %s OR pown.garden_id IS NULL)"
        params: list[object] = [plot_id, garden_id]
    elif _effective_role(context) in {"admin", "editor"}:
        owner_filter = " AND pown.garden_id = %s"
        params = [plot_id, garden_id]
    else:
        owner_filter = " AND pown.garden_id = %s AND pown.owner_user_id = %s"
        params = [plot_id, garden_id, context.user_id]
    rows = db.execute(
        f"""
        SELECT pl.*, pp.quantity, pp.room_label
        FROM plants pl
        LEFT JOIN plant_ownership pown ON pown.plt_id = pl.plt_id
        JOIN plot_plants pp ON pp.plt_id = pl.plt_id
        WHERE pp.plot_id = %s
        {owner_filter}
        ORDER BY pl.name
    """,
        params,
    ).fetchall()
    plot_ids_by_plant = _visible_plot_ids_for_plants(
        db,
        plt_ids=[str(row["plt_id"]) for row in rows],
        context=context,
    )
    result = []
    for r in rows:
        d = dict(r)
        d["plot_ids"] = plot_ids_by_plant.get(str(d["plt_id"]), [])
        d["deer_resistant"] = bool(d.get("deer_resistant"))
        result.append(d)
    return result


@router.post("/plots/{plot_id}/plants/{plt_id}", status_code=201)
def add_plant_to_plot(
    plot_id: str,
    plt_id: str,
    body: AddPlantBody,
    db: DB,
    request: Request,
) -> dict:
    """Add a plant to a plot, or update quantity if already present."""
    context = _auth_context(request)
    _require_plant_access(db, plt_id, context)
    _require_plot_in_garden_or_unowned(db, plot_id, context)
    garden_id = _active_garden_id(context)
    # Only store room_label for indoor plots (zone_code = 'I')
    zone = db.execute("SELECT zone_code FROM plots WHERE plot_id = %s", (plot_id,)).fetchone()
    effective_room_label = body.room_label if zone and zone["zone_code"] == "I" else None
    db.execute(
        """
        INSERT INTO plot_plants (plot_id, plt_id, quantity, room_label)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT(plot_id, plt_id) DO UPDATE
            SET quantity = excluded.quantity,
                room_label = excluded.room_label
    """,
        (plot_id, plt_id, body.quantity, effective_room_label),
    )
    db.commit()
    notify_garden_modified()

    warnings = check_companions(db, garden_id, plot_id, plt_id)
    return {
        "status": "ok",
        "plot_id": plot_id,
        "plt_id": plt_id,
        "quantity": body.quantity,
        "room_label": effective_room_label,
        "companion_warnings": warnings.get("conflicts", []),
    }


@router.patch("/plots/{plot_id}/plants/{plt_id}")
def update_plant_quantity(
    plot_id: str,
    plt_id: str,
    body: UpdateQuantityBody,
    db: DB,
    request: Request,
) -> dict:
    """Update the quantity of a plant in a plot."""
    context = _auth_context(request)
    _require_plant_access(db, plt_id, context)
    _require_plot_in_garden_or_unowned(db, plot_id, context)
    row = db.execute(
        "SELECT 1 FROM plot_plants WHERE plot_id = %s AND plt_id = %s", (plot_id, plt_id)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Plant not in plot")
    zone = db.execute("SELECT zone_code FROM plots WHERE plot_id = %s", (plot_id,)).fetchone()
    effective_room_label = body.room_label if zone and zone["zone_code"] == "I" else None
    db.execute(
        "UPDATE plot_plants SET quantity = %s, room_label = %s WHERE plot_id = %s AND plt_id = %s",
        (body.quantity, effective_room_label, plot_id, plt_id),
    )
    db.commit()
    notify_garden_modified()
    return {"status": "ok", "plot_id": plot_id, "plt_id": plt_id, "quantity": body.quantity}


@router.post("/plots/{from_plot_id}/plants/{plt_id}/move/{to_plot_id}")
def move_plant_between_plots(
    from_plot_id: str,
    plt_id: str,
    to_plot_id: str,
    db: DB,
    request: Request,
) -> dict:
    """Move a plant assignment atomically between plots."""
    context = _auth_context(request)
    _require_plant_access(db, plt_id, context)
    _require_plot_access(db, from_plot_id, context)
    _require_plot_access(db, to_plot_id, context)
    if from_plot_id == to_plot_id:
        return {
            "status": "ok",
            "from_plot_id": from_plot_id,
            "to_plot_id": to_plot_id,
            "plt_id": plt_id,
            "quantity": 0,
        }

    try:
        src = db.execute(
            "SELECT quantity FROM plot_plants WHERE plot_id = %s AND plt_id = %s",
            (from_plot_id, plt_id),
        ).fetchone()
        if not src:
            raise HTTPException(status_code=404, detail="Plant not in source plot")

        qty = int(src["quantity"])
        db.execute(
            """
            INSERT INTO plot_plants (plot_id, plt_id, quantity)
            VALUES (%s, %s, %s)
            ON CONFLICT(plot_id, plt_id)
            DO UPDATE SET quantity = plot_plants.quantity + excluded.quantity
            """,
            (to_plot_id, plt_id, qty),
        )
        db.execute(
            "DELETE FROM plot_plants WHERE plot_id = %s AND plt_id = %s",
            (from_plot_id, plt_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    notify_garden_modified()
    return {
        "status": "ok",
        "from_plot_id": from_plot_id,
        "to_plot_id": to_plot_id,
        "plt_id": plt_id,
        "quantity": qty,
    }


@router.delete("/plots/{plot_id}/plants/{plt_id}", status_code=204)
def remove_plant_from_plot(plot_id: str, plt_id: str, db: DB, request: Request) -> None:
    """Remove a plant assignment from a plot."""
    context = _auth_context(request)
    _require_plant_access(db, plt_id, context)
    _require_plot_in_garden_or_unowned(db, plot_id, context)
    row = db.execute(
        "SELECT 1 FROM plot_plants WHERE plot_id = %s AND plt_id = %s", (plot_id, plt_id)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Plant not in plot")
    db.execute("DELETE FROM plot_plants WHERE plot_id = %s AND plt_id = %s", (plot_id, plt_id))
    db.commit()
    notify_garden_modified()


@router.post("/plots", status_code=201)
def create_plot(body: CreatePlotBody, db: DB, request: Request) -> dict:
    """Create a new plot at the specified grid position."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    if body.zone_code == "I":
        raise HTTPException(status_code=400, detail="Zone code 'I' is reserved for indoor plants")
    lock_garden_layout(db, garden_id)
    existing = db.execute("SELECT 1 FROM plots WHERE plot_id = %s", (body.plot_id,)).fetchone()
    if existing:
        raise HTTPException(status_code=400, detail="Plot ID already exists")
    _assert_cell_free(db, body.grid_row, body.grid_col, context)
    owner_user_id = _owner_user_for_plot_write(db, garden_id=garden_id, context=context)

    try:
        db.execute(
            """INSERT INTO plots (plot_id, garden_id, zone_code, zone_name, plot_number,
               grid_row, grid_col, sub_zone, notes, color)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                body.plot_id,
                garden_id,
                body.zone_code,
                body.zone_name,
                body.plot_number,
                body.grid_row,
                body.grid_col,
                body.sub_zone,
                body.notes,
                body.color,
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
            (body.plot_id, owner_user_id, garden_id),
        )
        db.commit()
    except psycopg.IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Plot coordinate conflict") from exc
    notify_garden_modified()
    return {"status": "ok", **dict(body)}


@router.patch("/plots/{plot_id}")
def update_plot(plot_id: str, body: UpdatePlotBody, db: DB, request: Request) -> dict:
    """Update one or more fields of an existing plot."""
    context = _auth_context(request)
    _require_plot_access(db, plot_id, context)
    existing = db.execute("SELECT * FROM plots WHERE plot_id = %s", (plot_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Plot not found")

    updates = []
    params: list[str | int | None] = []

    is_indoor = existing["grid_row"] is None
    if is_indoor and (body.grid_row is not None or body.grid_col is not None):
        raise HTTPException(status_code=400, detail="Cannot assign grid position to indoor plot")
    if body.zone_code == "I" and existing["zone_code"] != "I":
        raise HTTPException(status_code=400, detail="Zone code 'I' is reserved for indoor plants")
    if not is_indoor:
        target_row = body.grid_row if body.grid_row is not None else int(existing["grid_row"])
        target_col = body.grid_col if body.grid_col is not None else int(existing["grid_col"])
        if body.grid_row is not None or body.grid_col is not None:
            lock_garden_layout(db, _active_garden_id(context))
            _assert_cell_free(db, target_row, target_col, context, exclude_plot_id=plot_id)

    if body.grid_row is not None:
        updates.append("grid_row = %s")
        params.append(body.grid_row)
    if body.grid_col is not None:
        updates.append("grid_col = %s")
        params.append(body.grid_col)
    if body.zone_code is not None:
        updates.append("zone_code = %s")
        params.append(body.zone_code)
    if body.zone_name is not None:
        updates.append("zone_name = %s")
        params.append(body.zone_name)
    if body.plot_number is not None:
        updates.append("plot_number = %s")
        params.append(body.plot_number)
    if body.sub_zone is not None:
        updates.append("sub_zone = %s")
        params.append(body.sub_zone)
    if body.notes is not None:
        updates.append("notes = %s")
        params.append(body.notes)
    if body.color is not None:
        val = body.color if body.color != "" else None
        updates.append("color = %s")
        params.append(val)

    rename = body.new_plot_id and body.new_plot_id != plot_id
    if rename:
        dup = db.execute(
            "SELECT 1 FROM plots WHERE plot_id = %s",
            (body.new_plot_id,),
        ).fetchone()
        if dup:
            raise HTTPException(
                status_code=400,
                detail="New plot ID already exists",
            )
        updates.append("plot_id = %s")
        params.append(body.new_plot_id)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        if rename:
            db.execute("SET CONSTRAINTS ALL DEFERRED")
            rename_plot_references(
                db,
                garden_id=_active_garden_id(context),
                old_plot_id=plot_id,
                new_plot_id=str(body.new_plot_id),
            )

        params.append(plot_id)
        db.execute(
            f"UPDATE plots SET {', '.join(updates)} WHERE plot_id = %s",
            params,
        )
        db.commit()
    except psycopg.IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Plot coordinate conflict") from exc
    except Exception:
        db.rollback()
        raise

    final_id = body.new_plot_id if rename else plot_id
    updated = db.execute(
        "SELECT * FROM plots WHERE plot_id = %s",
        (final_id,),
    ).fetchone()
    assert updated is not None
    notify_garden_modified()
    return {"status": "ok", **dict(updated)}


@router.post("/plots/batch-move")
def batch_move_plots(body: BatchMoveBody, db: DB, request: Request) -> dict:
    """Move multiple plots to new grid positions in a single operation."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    lock_garden_layout(db, garden_id)
    seen_targets: set[tuple[int, int]] = set()
    seen_plot_ids: set[str] = set()
    moving_ids = {m.plot_id for m in body.moves}

    if _is_local_admin_fallback(context):
        current_rows = db.execute(
            "SELECT plot_id, grid_row, grid_col FROM plots",
        ).fetchall()
    elif _effective_role(context) in {"admin", "editor"}:
        current_rows = db.execute(
            """
            SELECT p.plot_id, p.grid_row, p.grid_col
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE po.garden_id = %s
            """,
            (garden_id,),
        ).fetchall()
    else:
        current_rows = db.execute(
            """
            SELECT p.plot_id, p.grid_row, p.grid_col
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE po.garden_id = %s AND po.owner_user_id = %s
            """,
            (garden_id, context.user_id),
        ).fetchall()
    current_by_plot = {
        row["plot_id"]: (int(row["grid_row"]), int(row["grid_col"]))
        for row in current_rows
        if row["grid_row"] is not None
    }
    occupied = {(coords[0], coords[1]): pid for pid, coords in current_by_plot.items()}

    for item in body.moves:
        if item.plot_id not in current_by_plot:
            raise HTTPException(
                status_code=404,
                detail=f"Plot {item.plot_id} not found",
            )
        if item.plot_id in seen_plot_ids:
            raise HTTPException(
                status_code=409,
                detail=f"Plot {item.plot_id} appears more than once in this move",
            )
        seen_plot_ids.add(item.plot_id)

        target = (item.grid_row, item.grid_col)
        if target in seen_targets:
            raise HTTPException(
                status_code=409,
                detail=f"Multiple plots target the same cell ({item.grid_row}, {item.grid_col})",
            )
        seen_targets.add(target)

        at_target = occupied.get(target)
        if at_target and at_target != item.plot_id and at_target not in moving_ids:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Grid cell ({item.grid_row}, {item.grid_col})"
                    f" is already occupied by {at_target}"
                ),
            )

    try:
        for item in body.moves:
            db.execute(
                "UPDATE plots SET grid_row = NULL, grid_col = NULL WHERE plot_id = %s",
                (item.plot_id,),
            )
        for item in body.moves:
            db.execute(
                "UPDATE plots SET grid_row = %s, grid_col = %s WHERE plot_id = %s",
                (item.grid_row, item.grid_col, item.plot_id),
            )
        db.commit()
    except psycopg.IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Plot coordinate conflict") from exc
    except Exception:
        db.rollback()
        raise
    notify_garden_modified()
    return {"status": "ok", "moved": len(body.moves)}


@router.delete("/plots/{plot_id}", status_code=204)
def delete_plot(plot_id: str, db: DB, request: Request) -> None:
    """Delete a plot and all its plant assignments."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    _require_plot_access(db, plot_id, context)
    existing = db.execute(
        "SELECT plot_id, zone_code FROM plots WHERE plot_id = %s",
        (plot_id,),
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Plot not found")
    if existing["zone_code"] == "I":
        raise HTTPException(status_code=400, detail="Cannot delete the indoor plants collection")
    result = delete_plot_references(db, garden_id=garden_id, plot_id=plot_id)
    db.commit()
    for storage_key, preview_storage_key in result.media_storage_pairs:
        unlink_storage_keys(storage_key, preview_storage_key)
    notify_garden_modified()


@router.get("/plots/{plot_id}/delete-impact")
def get_plot_delete_impact(plot_id: str, db: DB, request: Request) -> dict[str, object]:
    """Return the records that plot deletion would remove or detach."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    _require_plot_access(db, plot_id, context)
    return load_plot_delete_impact(db, garden_id=garden_id, plot_id=plot_id)


class SeenGrowingUpdate(StrictBaseModel):
    plot_id: str = Field(min_length=1, max_length=40)
    plt_id: str = Field(min_length=1, max_length=40)
    seen_growing: bool | None = None
    seen_growing_date: str | None = Field(default=None, max_length=10)

    @field_validator("seen_growing_date")
    @classmethod
    def validate_date(cls, v: str | None) -> str | None:
        from gardenops.routers.plants import _validate_seen_growing_date

        return _validate_seen_growing_date(v)


class BulkSeenGrowingBody(StrictBaseModel):
    updates: list[SeenGrowingUpdate] = Field(max_length=100)


@router.patch("/plots/plants/seen-growing")
def bulk_update_seen_growing(body: BulkSeenGrowingBody, db: DB, request: Request) -> dict:
    context = _auth_context(request)
    if not has_write_access(context):
        raise HTTPException(status_code=403, detail="Write access required")

    validated_plots: set[str] = set()
    for update in body.updates:
        if update.plot_id not in validated_plots:
            _require_plot_access(db, update.plot_id, context)
            validated_plots.add(update.plot_id)

    # Validate all rows exist (all-or-nothing)
    requested_pairs = {(u.plot_id, u.plt_id) for u in body.updates}
    if requested_pairs:
        pair_clauses = " OR ".join("(plot_id = %s AND plt_id = %s)" for _ in requested_pairs)
        pair_params = [v for pair in requested_pairs for v in pair]
        found = {
            (row["plot_id"], row["plt_id"])
            for row in db.execute(
                f"SELECT plot_id, plt_id FROM plot_plants WHERE {pair_clauses}",
                pair_params,
            ).fetchall()
        }
    else:
        found = set()
    missing = [
        {"plot_id": u.plot_id, "plt_id": u.plt_id}
        for u in body.updates
        if (u.plot_id, u.plt_id) not in found
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Plot-plant rows not found: {missing}",
        )

    # Validate date constraints
    for u in body.updates:
        if u.seen_growing is False and u.seen_growing_date is not None:
            if len(u.seen_growing_date) != 4:
                raise HTTPException(
                    status_code=400,
                    detail=f"Not-seen date must be year only (YYYY), got: {u.seen_growing_date}",
                )
        if u.seen_growing is None and u.seen_growing_date is not None:
            raise HTTPException(
                status_code=400,
                detail="Cannot set date when seen_growing is null",
            )

    # Apply updates
    executemany(
        db,
        "UPDATE plot_plants SET seen_growing = %s, seen_growing_date = %s"
        " WHERE plot_id = %s AND plt_id = %s",
        [
            (
                None if u.seen_growing is None else int(u.seen_growing),
                u.seen_growing_date,
                u.plot_id,
                u.plt_id,
            )
            for u in body.updates
        ],
    )
    db.commit()
    return {"status": "ok", "updated": len(body.updates)}
