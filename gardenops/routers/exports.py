from __future__ import annotations

import csv
import io
import json
from datetime import date
from html import escape as _esc
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from gardenops.branding import app_slug
from gardenops.db import DB, DbConn
from gardenops.router_helpers import (
    active_garden_id as _active_garden_id,
)
from gardenops.router_helpers import (
    auth_context as _auth_context,
)
from gardenops.router_helpers import (
    is_local_admin_fallback as _is_local_admin_fallback,
)
from gardenops.router_helpers import (
    validate_date as _validate_date,
)
from gardenops.routers.plants import list_plants as _list_plants
from gardenops.security import AuthContext
from gardenops.services.gardener_reports import _build_scope

router = APIRouter()

ExportFormat = Literal["csv", "json", "html"]


def _export_filename(kind: str) -> str:
    return f"{app_slug()}-{kind}-{_today_iso()}.csv"


# ── CSV helper ──


_CSV_FORMULA_PREFIXES = {"=", "+", "-", "@", "\t", "\r", "\n"}


def _sanitize_csv_value(value: object) -> str:
    text = "" if value is None else str(value)
    if text and text[0] in _CSV_FORMULA_PREFIXES:
        return f"'{text}"
    return text


def _csv_response(
    rows: list[dict],
    columns: list[str],
    filename: str,
) -> Response:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: _sanitize_csv_value(row.get(column)) for column in columns})
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


def _project_export_row(row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    return {column: row.get(column) for column in columns}


def _project_export_rows(rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    return [_project_export_row(row, columns) for row in rows]


def _public_id_export_row(row: Any, columns: list[str]) -> dict[str, Any]:
    mapped = dict(row)
    mapped["id"] = str(mapped.get("public_id") or "")
    return _project_export_row(mapped, columns)


# ── HTML printable helper ──

_PRINT_CSS = """
body { font-family: system-ui, sans-serif; max-width: 900px;
  margin: 0 auto; padding: 20px; color: #222; }
h1 { font-size: 1.4rem; border-bottom: 2px solid #333; padding-bottom: 8px; }
h2 { font-size: 1.1rem; margin-top: 24px; color: #555; }
table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 0.85rem; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #ddd; }
th { background: #f5f5f5; font-weight: 600; }
tr:nth-child(even) { background: #fafafa; }
.badge { display: inline-block; padding: 2px 8px;
  border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
.badge-pending { background: #fff3cd; color: #856404; }
.badge-completed { background: #d4edda; color: #155724; }
.badge-overdue { background: #f8d7da; color: #721c24; }
.meta { color: #888; font-size: 0.8rem; margin-top: 4px; }
@media print {
  body { padding: 0; }
  h1 { font-size: 1.2rem; }
  table { page-break-inside: auto; }
  tr { page-break-inside: avoid; }
}
"""


def _html_response(title: str, body_html: str) -> Response:
    html = (
        "<!DOCTYPE html><html><head>"
        f"<meta charset='utf-8'><title>{_esc(title)}</title>"
        f"<style>{_PRINT_CSS}</style>"
        "</head><body>"
        f"<h1>{_esc(title)}</h1>"
        f"{body_html}"
        f"<p class='meta'>Generated {_esc(_today_iso())}</p>"
        "</body></html>"
    )
    return Response(content=html, media_type="text/html; charset=utf-8")


def _html_table(rows: list[dict], columns: list[str], headers: list[str] | None = None) -> str:
    hdrs = headers or columns
    parts = ["<table><thead><tr>"]
    for h in hdrs:
        parts.append(f"<th>{_esc(h)}</th>")
    parts.append("</tr></thead><tbody>")
    for row in rows:
        parts.append("<tr>")
        for col in columns:
            val = row.get(col, "")
            parts.append(f"<td>{_esc(str(val) if val is not None else '')}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _today_iso() -> str:
    return date.today().isoformat()


def _split_query_values(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _plant_presence_status(plant: dict[str, Any]) -> str:
    return str(plant.get("presence_status") or "present")


def _plant_observed_this_year(plant: dict[str, Any]) -> bool:
    return bool(
        plant.get("observed_this_year")
        or plant.get("seen_growing_is_current_year")
        or plant.get("bloomed_this_year")
    )


def _matches_plant_export_filters(
    plant: dict[str, Any],
    *,
    q: str,
    category: str,
    presence: str,
    focused_plant_ids: set[str],
) -> bool:
    if focused_plant_ids and str(plant["plt_id"]) not in focused_plant_ids:
        return False
    if category and str(plant.get("category") or "") != category:
        return False
    status = _plant_presence_status(plant)
    if presence == "current" and status == "gone":
        return False
    if presence == "gone" and status != "gone":
        return False
    if presence == "unobserved" and _plant_observed_this_year(plant):
        return False
    if not q:
        return True
    haystack = " ".join(
        [
            str(plant.get("plt_id") or ""),
            str(plant.get("name") or ""),
            str(plant.get("latin") or ""),
            str(plant.get("hardiness") or ""),
            status,
            str(plant.get("last_not_seen_year") or ""),
            "unobserved this season" if not _plant_observed_this_year(plant) else "",
        ]
    ).lower()
    return q.lower() in haystack


def _entity_scope_clause(
    *,
    entity_alias: str,
    entity_id_column: str,
    plot_link_table: str,
    plot_link_column: str,
    plant_link_table: str,
    plant_link_column: str,
    plot_ids: list[str],
    plant_ids: list[str],
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if plot_ids:
        placeholders = ",".join(["%s"] * len(plot_ids))
        clauses.append(
            f"{entity_alias}.{entity_id_column} IN "
            f"(SELECT {plot_link_column} FROM {plot_link_table} WHERE plot_id IN ({placeholders}))"
        )
        params.extend(plot_ids)
    if plant_ids:
        placeholders = ",".join(["%s"] * len(plant_ids))
        clauses.append(
            f"{entity_alias}.{entity_id_column} IN "
            f"(SELECT {plant_link_column} FROM {plant_link_table} WHERE plt_id IN ({placeholders}))"
        )
        params.extend(plant_ids)
    if not clauses:
        return " AND 1 = 0", []
    return f" AND ({' OR '.join(clauses)})", params


# ── Endpoints ──


PLANT_COLUMNS = [
    "plt_id",
    "name",
    "latin",
    "category",
    "bloom_month",
    "color",
    "hardiness",
    "height_cm",
    "light",
    "year_planted",
    "deer_resistant",
]


@router.get("/exports/plants")
def export_plants(
    request: Request,
    db: DB,
    format: ExportFormat = Query(default="csv"),
    q: str | None = Query(default=None),
    category: str | None = Query(default=None),
    presence: str | None = Query(default=None),
    plt_ids: str | None = Query(default=None),
) -> Response:
    focused_plant_ids = set(_split_query_values(plt_ids))
    plants = [
        plant
        for plant in _list_plants(
            db=db,
            request=request,
            q="",
            category="",
        )
        if _matches_plant_export_filters(
            plant,
            q=(q or "").strip(),
            category=(category or "").strip(),
            presence=(presence or "all").strip(),
            focused_plant_ids=focused_plant_ids,
        )
    ]

    export_rows = _project_export_rows(plants, PLANT_COLUMNS)

    if format == "html":
        return _html_response(
            "Plant Inventory",
            _html_table(
                export_rows,
                PLANT_COLUMNS,
                [
                    "ID",
                    "Name",
                    "Latin",
                    "Category",
                    "Bloom",
                    "Color",
                    "Hardiness",
                    "Height (cm)",
                    "Light",
                    "Planted",
                    "Deer resistant",
                ],
            ),
        )
    if format == "json":
        return Response(
            content=json.dumps({"plants": export_rows}, default=str),
            media_type="application/json",
        )

    filename = _export_filename("plants")
    return _csv_response(export_rows, PLANT_COLUMNS, filename)


TASK_COLUMNS = [
    "id",
    "task_type",
    "title",
    "status",
    "severity",
    "due_on",
    "created_at_ms",
    "plant_ids",
    "plot_ids",
]


@router.get("/exports/tasks")
def export_tasks(
    request: Request,
    db: DB,
    format: ExportFormat = Query(default="csv"),
    status: str | None = Query(default=None),
    task_type: str | None = Query(default=None),
) -> Response:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)

    conditions = ["t.garden_id = %s"]
    params: list = [garden_id]
    if status:
        conditions.append("t.status = %s")
        params.append(status)
    if task_type:
        types = [tt.strip() for tt in task_type.split(",") if tt.strip()]
        if types:
            ph = ",".join(["%s"] * len(types))
            conditions.append(f"t.task_type IN ({ph})")
            params.extend(types)

    where = " AND ".join(conditions)
    rows = db.execute(
        f"""
        SELECT t.id, t.public_id, t.task_type, t.title, t.status, t.severity,
               t.due_on, t.created_at_ms
        FROM garden_tasks t
        WHERE {where}
        ORDER BY t.due_on
        """,
        params,
    ).fetchall()

    task_ids = [int(r["id"]) for r in rows]
    plant_map, plot_map = _load_task_links(db, task_ids)

    tasks = []
    for r in rows:
        d = dict(r)
        tid = int(r["id"])
        d["id"] = str(r["public_id"])
        d["plant_ids"] = ",".join(plant_map.get(tid, []))
        d["plot_ids"] = ",".join(plot_map.get(tid, []))
        tasks.append(_project_export_row(d, TASK_COLUMNS))

    if format == "html":
        return _html_response(
            "Task List",
            _html_table(
                tasks,
                TASK_COLUMNS,
                [
                    "ID",
                    "Type",
                    "Title",
                    "Status",
                    "Severity",
                    "Due",
                    "Created",
                    "Plants",
                    "Plots",
                ],
            ),
        )
    if format == "json":
        return Response(
            content=json.dumps({"tasks": tasks}, default=str),
            media_type="application/json",
        )

    filename = _export_filename("tasks")
    return _csv_response(tasks, TASK_COLUMNS, filename)


def _load_task_links(
    db: DbConn,
    task_ids: list[int],
) -> tuple[dict[int, list[str]], dict[int, list[str]]]:
    if not task_ids:
        return {}, {}
    placeholders = ",".join(["%s"] * len(task_ids))
    plant_map: dict[int, list[str]] = {tid: [] for tid in task_ids}
    for r in db.execute(
        f"SELECT task_id, plt_id FROM garden_task_plants WHERE task_id IN ({placeholders})",
        task_ids,
    ).fetchall():
        plant_map[int(r["task_id"])].append(str(r["plt_id"]))

    plot_map: dict[int, list[str]] = {tid: [] for tid in task_ids}
    for r in db.execute(
        f"SELECT task_id, plot_id FROM garden_task_plots WHERE task_id IN ({placeholders})",
        task_ids,
    ).fetchall():
        plot_map[int(r["task_id"])].append(str(r["plot_id"]))

    return plant_map, plot_map


JOURNAL_COLUMNS = [
    "id",
    "event_type",
    "occurred_on",
    "title",
    "notes",
    "created_at_ms",
]


@router.get("/exports/journal")
def export_journal(
    request: Request,
    db: DB,
    format: ExportFormat = Query(default="csv"),
    event_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
) -> Response:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    conditions = ["e.garden_id = %s"]
    params: list[object] = [garden_id]

    event_types = _split_query_values(event_type)
    if event_types:
        placeholders = ",".join(["%s"] * len(event_types))
        conditions.append(f"e.event_type IN ({placeholders})")
        params.extend(event_types)
    if q:
        like = f"%{q.strip()}%"
        conditions.append(
            """
            (
                e.title ILIKE %s
                OR e.notes ILIKE %s
                OR e.id IN (
                    SELECT jep.entry_id
                    FROM garden_journal_entry_plants jep
                    JOIN plants p ON p.plt_id = jep.plt_id
                    WHERE jep.plt_id ILIKE %s
                       OR p.name ILIKE %s
                       OR p.latin ILIKE %s
                )
                OR e.id IN (
                    SELECT entry_id
                    FROM garden_journal_entry_plots
                    WHERE plot_id ILIKE %s
                )
            )
            """
        )
        params.extend([like, like, like, like, like, like])
    if actor:
        conditions.append("u.username ILIKE %s")
        params.append(f"%{actor.strip()}%")
    if date_from:
        _validate_date(date_from)
        conditions.append("e.occurred_on >= %s")
        params.append(date_from)
    if date_to:
        _validate_date(date_to)
        conditions.append("e.occurred_on <= %s")
        params.append(date_to)
    where = " AND ".join(conditions)
    rows = db.execute(
        """
        SELECT e.id, e.public_id, e.event_type, e.occurred_on, e.title,
               e.notes, e.created_at_ms
        FROM garden_journal_entries e
        LEFT JOIN auth_users u ON u.id = e.actor_user_id
        WHERE """
        + where
        + """
        ORDER BY e.occurred_on DESC, e.created_at_ms DESC
        """,
        params,
    ).fetchall()

    entries = [_public_id_export_row(r, JOURNAL_COLUMNS) for r in rows]

    if format == "json":
        return Response(
            content=json.dumps({"journal": entries}, default=str),
            media_type="application/json",
        )

    filename = _export_filename("journal")
    return _csv_response(entries, JOURNAL_COLUMNS, filename)


HARVEST_COLUMNS = [
    "id",
    "occurred_on",
    "quantity",
    "unit",
    "quality",
    "notes",
    "created_at_ms",
]


@router.get("/exports/harvest")
def export_harvest(
    request: Request,
    db: DB,
    format: ExportFormat = Query(default="csv"),
    year: int | None = Query(default=None),
    quality: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
) -> Response:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)

    conditions = ["garden_id = %s"]
    params: list[object] = [garden_id]
    if year is not None:
        conditions.append("occurred_on >= %s")
        conditions.append("occurred_on <= %s")
        params.append(f"{year}-01-01")
        params.append(f"{year}-12-31")
    if quality:
        conditions.append("quality = %s")
        params.append(quality)
    if date_from:
        _validate_date(date_from)
        conditions.append("occurred_on >= %s")
        params.append(date_from)
    if date_to:
        _validate_date(date_to)
        conditions.append("occurred_on <= %s")
        params.append(date_to)

    where = " AND ".join(conditions)
    rows = db.execute(
        f"""
        SELECT id, public_id, occurred_on, quantity, unit, quality, notes, created_at_ms
        FROM harvest_entries
        WHERE {where}
        ORDER BY occurred_on DESC
        """,
        params,
    ).fetchall()

    entries = [_public_id_export_row(r, HARVEST_COLUMNS) for r in rows]

    if format == "html":
        return _html_response(
            "Harvest Summary",
            _html_table(
                entries,
                HARVEST_COLUMNS,
                [
                    "ID",
                    "Date",
                    "Quantity",
                    "Unit",
                    "Quality",
                    "Notes",
                    "Created",
                ],
            ),
        )
    if format == "json":
        return Response(
            content=json.dumps({"harvest": entries}, default=str),
            media_type="application/json",
        )

    filename = _export_filename("harvest")
    return _csv_response(entries, HARVEST_COLUMNS, filename)


INVENTORY_COLUMNS = [
    "id",
    "plt_id",
    "label",
    "inventory_type",
    "unit",
    "quantity",
    "procurement_count",
    "recent_vendor_name",
    "recent_procurement_status",
    "recent_procurement_received_on",
    "created_at_ms",
]


def _serialize_inventory_export_rows(
    db: DbConn,
    garden_id: int,
    item_rows: list[dict[str, Any]],
) -> list[dict]:
    if not item_rows:
        return []

    items = [dict(row) for row in item_rows]
    history_by_item_id: dict[str, list[dict]] = {str(item["public_id"]): [] for item in items}
    items_by_plant: dict[str, list[dict]] = {}
    for item in items:
        plt_id = str(item["plt_id"]) if item["plt_id"] else ""
        if not plt_id:
            continue
        items_by_plant.setdefault(plt_id, []).append(item)

    procurement_rows = db.execute(
        """
        SELECT public_id, linked_plt_id, label, inventory_type, unit, vendor_name,
               status, received_on, metadata_json
        FROM procurement_items
        WHERE garden_id = %s
        ORDER BY
            COALESCE(received_on, expected_on, ordered_on, '') DESC,
            updated_at_ms DESC,
            id DESC
        """,
        (garden_id,),
    ).fetchall()

    for row in procurement_rows:
        procurement = dict(row)
        attached_item_ids: set[str] = set()

        try:
            metadata = json.loads(procurement.get("metadata_json") or "{}")
        except (
            TypeError,
            json.JSONDecodeError,
        ):
            metadata = {}
        metadata_item_id = metadata.get("inventory_item_id")
        if isinstance(metadata_item_id, str) and metadata_item_id in history_by_item_id:
            attached_item_ids.add(metadata_item_id)
        elif isinstance(metadata_item_id, int):
            for item in items:
                if int(item["id"]) == metadata_item_id:
                    attached_item_ids.add(str(item["public_id"]))
                    break

        linked_plant_id = str(procurement["linked_plt_id"]) if procurement["linked_plt_id"] else ""
        if linked_plant_id and linked_plant_id in items_by_plant:
            for item in items_by_plant[linked_plant_id]:
                if (
                    str(item["label"] or "") == str(procurement["label"] or "")
                    and str(item["inventory_type"]) == str(procurement["inventory_type"])
                    and str(item["unit"]) == str(procurement["unit"] or "pieces")
                ):
                    attached_item_ids.add(str(item["public_id"]))

        if not attached_item_ids:
            continue

        entry = {
            "id": str(procurement["public_id"]),
            "vendor_name": str(procurement["vendor_name"] or ""),
            "status": str(procurement["status"] or ""),
            "received_on": (
                str(procurement["received_on"]) if procurement["received_on"] else None
            ),
        }
        for item_id in attached_item_ids:
            history_by_item_id[item_id].append(entry)

    exports: list[dict] = []
    for item in items:
        history = history_by_item_id.get(str(item["public_id"]), [])
        recent = history[0] if history else {}
        exports.append(
            {
                "id": str(item["public_id"]),
                "plt_id": str(item["plt_id"]) if item["plt_id"] else "",
                "label": str(item["label"] or ""),
                "inventory_type": str(item["inventory_type"]),
                "unit": str(item["unit"] or ""),
                "quantity": int(item["_qty"] or 0),
                "procurement_count": len(history),
                "recent_vendor_name": str(recent.get("vendor_name") or ""),
                "recent_procurement_status": str(recent.get("status") or ""),
                "recent_procurement_received_on": (
                    str(recent.get("received_on")) if recent.get("received_on") else ""
                ),
                "created_at_ms": int(item["created_at_ms"]),
            }
        )
    return exports


@router.get("/exports/inventory")
def export_inventory(
    request: Request,
    db: DB,
    format: ExportFormat = Query(default="csv"),
    inventory_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
) -> Response:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)

    conditions = ["i.garden_id = %s"]
    params: list[object] = [garden_id]
    if inventory_type:
        conditions.append("i.inventory_type = %s")
        params.append(inventory_type)
    if q:
        like = f"%{q.strip()}%"
        conditions.append(
            "(i.label ILIKE %s OR COALESCE(i.plt_id, '') ILIKE %s OR COALESCE(p.name, '') ILIKE %s)"
        )
        params.extend([like, like, like])

    where = " AND ".join(conditions)
    rows = db.execute(
        f"""
        SELECT i.id, i.public_id, i.plt_id, i.label, i.inventory_type,
               i.unit, i.created_at_ms,
               COALESCE(sq.qty, 0) AS _qty
        FROM inventory_items i
        LEFT JOIN plants p ON p.plt_id = i.plt_id
        LEFT JOIN (
            SELECT item_id, SUM(delta) AS qty
            FROM inventory_transactions
            GROUP BY item_id
        ) sq ON sq.item_id = i.id
        WHERE {where}
        ORDER BY i.label, i.id
        """,
        params,
    ).fetchall()

    items = _serialize_inventory_export_rows(db, garden_id, rows)

    if format == "json":
        return Response(
            content=json.dumps({"inventory": items}, default=str),
            media_type="application/json",
        )

    filename = _export_filename("inventory")
    return _csv_response(items, INVENTORY_COLUMNS, filename)


ISSUE_COLUMNS = [
    "id",
    "issue_type",
    "title",
    "severity",
    "status",
    "suspected_cause",
    "treatment_plan",
    "follow_up_on",
    "created_at_ms",
]


@router.get("/exports/issues")
def export_issues(
    request: Request,
    db: DB,
    format: ExportFormat = Query(default="csv"),
    status: str | None = Query(default=None),
    issue_type: str | None = Query(default=None),
    severity: str | None = Query(default=None),
) -> Response:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)

    conditions = ["garden_id = %s"]
    params: list[object] = [garden_id]
    if status:
        statuses = _split_query_values(status)
        if statuses:
            placeholders = ",".join(["%s"] * len(statuses))
            conditions.append(f"status IN ({placeholders})")
            params.extend(statuses)
    if issue_type:
        issue_types = _split_query_values(issue_type)
        if issue_types:
            placeholders = ",".join(["%s"] * len(issue_types))
            conditions.append(f"issue_type IN ({placeholders})")
            params.extend(issue_types)
    if severity:
        severities = _split_query_values(severity)
        if severities:
            placeholders = ",".join(["%s"] * len(severities))
            conditions.append(f"severity IN ({placeholders})")
            params.extend(severities)

    where = " AND ".join(conditions)
    rows = db.execute(
        f"""
        SELECT id, public_id, issue_type, title, severity, status,
               suspected_cause, treatment_plan, follow_up_on, created_at_ms
        FROM garden_issues
        WHERE {where}
        ORDER BY created_at_ms DESC
        """,
        params,
    ).fetchall()

    entries = [_public_id_export_row(r, ISSUE_COLUMNS) for r in rows]

    if format == "json":
        return Response(
            content=json.dumps({"issues": entries}, default=str),
            media_type="application/json",
        )

    filename = _export_filename("issues")
    return _csv_response(entries, ISSUE_COLUMNS, filename)


PROCUREMENT_COLUMNS = [
    "id",
    "label",
    "inventory_type",
    "linked_plt_id",
    "vendor_name",
    "vendor_url",
    "status",
    "cost_minor",
    "currency",
    "quantity",
    "unit",
    "ordered_on",
    "expected_on",
    "received_on",
    "notes",
    "created_at_ms",
    "updated_at_ms",
]


@router.get("/exports/procurement")
def export_procurement(
    request: Request,
    db: DB,
    format: ExportFormat = Query(default="csv"),
    status: str | None = Query(default=None),
    inventory_type: str | None = Query(default=None),
) -> Response:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)

    conditions = ["garden_id = %s"]
    params: list[object] = [garden_id]
    if status:
        conditions.append("status = %s")
        params.append(status)
    if inventory_type:
        conditions.append("inventory_type = %s")
        params.append(inventory_type)

    where = " AND ".join(conditions)
    rows = db.execute(
        f"""
        SELECT id, public_id, label, inventory_type, linked_plt_id, vendor_name,
               vendor_url, status, cost_minor, currency, quantity, unit,
               ordered_on, expected_on, received_on, notes, created_at_ms,
               updated_at_ms
        FROM procurement_items
        WHERE {where}
        ORDER BY
            CASE status
                WHEN 'wanted' THEN 0
                WHEN 'ordered' THEN 1
                WHEN 'shipped' THEN 2
                WHEN 'received' THEN 3
                WHEN 'cancelled' THEN 4
            END,
            updated_at_ms DESC
        """,
        params,
    ).fetchall()

    items = [_public_id_export_row(row, PROCUREMENT_COLUMNS) for row in rows]

    if format == "json":
        return Response(
            content=json.dumps({"procurement": items}, default=str),
            media_type="application/json",
        )

    filename = _export_filename("procurement")
    return _csv_response(items, PROCUREMENT_COLUMNS, filename)


@router.get("/exports/seasonal-summary", response_model=None)
def seasonal_summary(
    request: Request,
    db: DB,
    format: ExportFormat = Query(default="json"),
    zone_code: str | None = Query(default=None),
) -> Response | dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    try:
        scope = _build_scope(db, garden_id=garden_id, zone_code=zone_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    scoped_plant_ids = scope.plant_ids if scope.zone_code else None
    scoped_plot_ids = scope.plot_ids if scope.zone_code else None
    bloom_calendar = _build_bloom_calendar(
        db,
        context,
        garden_id,
        plant_ids=scoped_plant_ids,
    )
    task_summary = _build_task_summary(
        db,
        garden_id,
        plot_ids=scoped_plot_ids,
        plant_ids=scoped_plant_ids,
    )
    harvest_summary = _build_harvest_summary(
        db,
        garden_id,
        plot_ids=scoped_plot_ids,
        plant_ids=scoped_plant_ids,
    )
    issue_summary = _build_issue_summary(
        db,
        garden_id,
        plot_ids=scoped_plot_ids,
        plant_ids=scoped_plant_ids,
    )

    if format == "html":
        return _html_response(
            "Seasonal Summary",
            _build_seasonal_summary_html(
                bloom_calendar,
                task_summary,
                harvest_summary,
                issue_summary,
            ),
        )

    return {
        "bloom_calendar": bloom_calendar,
        "task_summary": task_summary,
        "harvest_summary": harvest_summary,
        "issue_summary": issue_summary,
    }


def _build_bloom_calendar(
    db: DbConn,
    context: AuthContext,
    garden_id: int,
    plant_ids: list[str] | None = None,
) -> list[dict]:
    normalized_plant_ids = plant_ids or []
    if _is_local_admin_fallback(context):
        sql = (
            "SELECT plt_id, name, bloom_month FROM plants "
            "WHERE bloom_month IS NOT NULL AND bloom_month != ''"
        )
        params: list[object] = []
        if normalized_plant_ids:
            placeholders = ",".join(["%s"] * len(normalized_plant_ids))
            sql += f" AND plt_id IN ({placeholders})"
            params.extend(normalized_plant_ids)
        rows = db.execute(sql, params).fetchall()
    else:
        sql = """
            SELECT p.plt_id, p.name, p.bloom_month
            FROM plants p
            JOIN plant_ownership po ON po.plt_id = p.plt_id
            WHERE po.garden_id = %s
              AND p.bloom_month IS NOT NULL
              AND p.bloom_month != ''
            """
        params: list[object] = [garden_id]
        if normalized_plant_ids:
            placeholders = ",".join(["%s"] * len(normalized_plant_ids))
            sql += f" AND p.plt_id IN ({placeholders})"
            params.extend(normalized_plant_ids)
        rows = db.execute(sql, params).fetchall()

    months: dict[int, list[str]] = {}
    for r in rows:
        raw = str(r["bloom_month"])
        for part in raw.replace(",", " ").split():
            part = part.strip()
            if not part:
                continue
            try:
                month_num = int(part)
            except ValueError:
                continue
            if 1 <= month_num <= 12:
                months.setdefault(month_num, []).append(
                    str(r["name"] or r["plt_id"]),
                )

    return [{"month": m, "plants": sorted(names)} for m, names in sorted(months.items())]


def _build_task_summary(
    db: DbConn,
    garden_id: int,
    *,
    plot_ids: list[str] | None = None,
    plant_ids: list[str] | None = None,
) -> dict[str, int]:
    conditions = ["t.garden_id = %s"]
    params: list[object] = [garden_id]
    if plot_ids is not None or plant_ids is not None:
        scope_sql, scope_params = _entity_scope_clause(
            entity_alias="t",
            entity_id_column="id",
            plot_link_table="garden_task_plots",
            plot_link_column="task_id",
            plant_link_table="garden_task_plants",
            plant_link_column="task_id",
            plot_ids=plot_ids or [],
            plant_ids=plant_ids or [],
        )
        conditions.append(scope_sql[5:])
        params.extend(scope_params)
    where = " AND ".join(conditions)
    rows = db.execute(
        f"SELECT status, COUNT(*) AS c FROM garden_tasks t WHERE {where} GROUP BY status",
        params,
    ).fetchall()
    result: dict[str, int] = {}
    for r in rows:
        result[str(r["status"])] = int(r["c"])
    return result


def _build_harvest_summary(
    db: DbConn,
    garden_id: int,
    *,
    plot_ids: list[str] | None = None,
    plant_ids: list[str] | None = None,
) -> list[dict]:
    month_expr = "CAST(EXTRACT(MONTH FROM he.occurred_on::date) AS INTEGER)"
    conditions = ["he.garden_id = %s"]
    params: list[object] = [garden_id]
    if plot_ids is not None or plant_ids is not None:
        scope_sql, scope_params = _entity_scope_clause(
            entity_alias="he",
            entity_id_column="id",
            plot_link_table="harvest_entry_plots",
            plot_link_column="entry_id",
            plant_link_table="harvest_entry_plants",
            plant_link_column="entry_id",
            plot_ids=plot_ids or [],
            plant_ids=plant_ids or [],
        )
        conditions.append(scope_sql[5:])
        params.extend(scope_params)
    where = " AND ".join(conditions)
    rows = db.execute(
        f"""
        SELECT {month_expr} AS month,
               SUM(he.quantity) AS total_qty,
               COUNT(*) AS entries
        FROM harvest_entries he
        WHERE {where}
        GROUP BY month
        ORDER BY month
        """,
        params,
    ).fetchall()
    return [
        {
            "month": int(r["month"]),
            "total_qty": float(r["total_qty"]),
            "entries": int(r["entries"]),
        }
        for r in rows
    ]


def _build_issue_summary(
    db: DbConn,
    garden_id: int,
    *,
    plot_ids: list[str] | None = None,
    plant_ids: list[str] | None = None,
) -> dict[str, int]:
    conditions = ["i.garden_id = %s"]
    params: list[object] = [garden_id]
    if plot_ids is not None or plant_ids is not None:
        scope_sql, scope_params = _entity_scope_clause(
            entity_alias="i",
            entity_id_column="id",
            plot_link_table="garden_issue_plots",
            plot_link_column="issue_id",
            plant_link_table="garden_issue_plants",
            plant_link_column="issue_id",
            plot_ids=plot_ids or [],
            plant_ids=plant_ids or [],
        )
        conditions.append(scope_sql[5:])
        params.extend(scope_params)
    where = " AND ".join(conditions)
    rows = db.execute(
        f"SELECT status, COUNT(*) AS c FROM garden_issues i WHERE {where} GROUP BY status",
        params,
    ).fetchall()
    result: dict[str, int] = {}
    for r in rows:
        result[str(r["status"])] = int(r["c"])
    return result


_MONTH_NAMES_EN = [
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def _build_seasonal_summary_html(
    bloom_calendar: list[dict],
    task_summary: dict[str, int],
    harvest_summary: list[dict],
    issue_summary: dict[str, int],
) -> str:
    parts: list[str] = []

    # Bloom calendar — 12-month grid
    parts.append("<h2>Bloom Calendar</h2>")
    if bloom_calendar:
        parts.append("<table><thead><tr><th>Month</th><th>Plants</th></tr></thead><tbody>")
        for entry in bloom_calendar:
            m = int(entry["month"])
            name = _MONTH_NAMES_EN[m] if 1 <= m <= 12 else str(m)
            plants = ", ".join(_esc(p) for p in entry["plants"])
            parts.append(f"<tr><td>{_esc(name)}</td><td>{plants}</td></tr>")
        parts.append("</tbody></table>")
    else:
        parts.append("<p>No bloom data available.</p>")

    # Task summary
    parts.append("<h2>Tasks</h2>")
    if task_summary:
        parts.append("<table><thead><tr><th>Status</th><th>Count</th></tr></thead><tbody>")
        for status, count in sorted(task_summary.items()):
            parts.append(f"<tr><td>{_esc(status)}</td><td>{count}</td></tr>")
        parts.append("</tbody></table>")
    else:
        parts.append("<p>No tasks.</p>")

    # Harvest summary
    parts.append("<h2>Harvest</h2>")
    if harvest_summary:
        parts.append(
            "<table><thead><tr><th>Month</th><th>Entries</th><th>Total qty</th></tr></thead><tbody>"
        )
        for entry in harvest_summary:
            m = int(entry["month"])
            name = _MONTH_NAMES_EN[m] if 1 <= m <= 12 else str(m)
            parts.append(
                f"<tr><td>{_esc(name)}</td><td>{entry['entries']}</td>"
                f"<td>{entry['total_qty']:.1f}</td></tr>"
            )
        parts.append("</tbody></table>")
    else:
        parts.append("<p>No harvest entries.</p>")

    # Issue summary
    parts.append("<h2>Issues</h2>")
    if issue_summary:
        parts.append("<table><thead><tr><th>Status</th><th>Count</th></tr></thead><tbody>")
        for status, count in sorted(issue_summary.items()):
            parts.append(f"<tr><td>{_esc(status)}</td><td>{count}</td></tr>")
        parts.append("</tbody></table>")
    else:
        parts.append("<p>No issues.</p>")

    return "".join(parts)
