from __future__ import annotations

import json
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field

from gardenops.db import DB, DbConn, current_timestamp_ms
from gardenops.models import StrictBaseModel
from gardenops.offline_idempotency import (
    JOURNAL_ENDPOINT,
    JOURNAL_TARGET,
    prepare_operation,
    raise_operation_target_gone,
    read_operation_id,
    reserve_operation,
)
from gardenops.router_helpers import (
    active_garden_id as _active_garden_id,
)
from gardenops.router_helpers import (
    auth_context as _auth_context,
)
from gardenops.router_helpers import (
    generate_public_id as _generate_public_id,
)
from gardenops.router_helpers import (
    is_local_admin_fallback as _is_local_admin_fallback,
)
from gardenops.router_helpers import (
    parse_metadata as _parse_metadata,
)
from gardenops.router_helpers import (
    require_write as _require_write,
)
from gardenops.router_helpers import (
    validate_date as _validate_date,
)
from gardenops.security import AuthContext

router = APIRouter()

INVENTORY_PLANT_OPERATION_NAMESPACE = "inventory-plant"

InventoryType = Literal[
    "seed",
    "bulb",
    "tuber",
    "division",
    "bare_root",
    "nursery",
    "cutting",
    "other",
]

TransactionReason = Literal[
    "purchased",
    "harvested",
    "sowed",
    "planted",
    "divided",
    "gifted",
    "disposed",
    "adjusted",
    "",
]


class CreateInventoryItemBody(StrictBaseModel):
    plt_id: str | None = None
    label: str = Field(default="", max_length=200)
    inventory_type: InventoryType = "seed"
    unit: str = Field(default="pcs", max_length=40)


class UpdateInventoryItemBody(StrictBaseModel):
    plt_id: str | None = None
    label: str | None = Field(default=None, max_length=200)
    inventory_type: InventoryType | None = None
    unit: str | None = Field(default=None, max_length=40)


class AddTransactionBody(StrictBaseModel):
    delta: Decimal = Field(max_digits=20, decimal_places=6)
    reason: TransactionReason = ""
    source_name: str = Field(default="", max_length=200)
    cost_minor: int | None = None
    occurred_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    storage_location: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)
    journal_entry_id: str | None = None


class PlantFromStockBody(StrictBaseModel):
    quantity: Decimal = Field(gt=0, max_digits=20, decimal_places=6)
    plot_id: str = Field(min_length=1, max_length=120)
    occurred_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    notes: str = Field(default="", max_length=2000)


def _validate_linked_plant(
    db: DbConn,
    context: AuthContext,
    plt_id: str | None,
) -> str | None:
    normalized = (plt_id or "").strip()
    if not normalized:
        return None
    if _is_local_admin_fallback(context):
        row = db.execute(
            "SELECT 1 FROM plants WHERE plt_id = %s",
            (normalized,),
        ).fetchone()
    else:
        row = db.execute(
            """
            SELECT 1
            FROM plant_ownership
            WHERE garden_id = %s AND plt_id = %s
            """,
            (_active_garden_id(context), normalized),
        ).fetchone()
    if not row:
        raise HTTPException(404, f"Plant {normalized} not found in active garden")
    return normalized


def _fetch_item(db: DbConn, item_id: int, garden_id: int) -> dict:
    row = db.execute(
        "SELECT * FROM inventory_items WHERE id = %s AND garden_id = %s",
        (item_id, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Inventory item not found")
    return dict(row)


def _fetch_item_by_public_id(db: DbConn, item_id: str, garden_id: int) -> dict:
    row = db.execute(
        "SELECT * FROM inventory_items WHERE public_id = %s AND garden_id = %s",
        (item_id, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Inventory item not found")
    return dict(row)


def _decimal_string(value: object) -> str:
    quantity = Decimal(str(value or 0))
    text = format(quantity, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _item_quantity(db: DbConn, item_id: int) -> Decimal:
    row = db.execute(
        "SELECT COALESCE(SUM(delta), 0) AS qty FROM inventory_transactions WHERE item_id = %s",
        (item_id,),
    ).fetchone()
    return Decimal(str(row["qty"])) if row else Decimal(0)


def _serialize_item(
    row: dict,
    qty: Decimal | int | float,
    procurement_history: list[dict] | None = None,
) -> dict:
    return {
        "id": str(row["public_id"]),
        "garden_id": int(row["garden_id"]),
        "plt_id": row["plt_id"],
        "label": str(row["label"] or ""),
        "inventory_type": str(row["inventory_type"]),
        "unit": str(row["unit"]),
        "quantity": _decimal_string(qty),
        "created_at_ms": int(row["created_at_ms"]),
        "procurement_history": procurement_history or [],
    }


def _serialize_tx(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "item_id": str(row["item_public_id"]),
        "delta": _decimal_string(row["delta"]),
        "reason": str(row["reason"] or ""),
        "source_name": str(row["source_name"] or ""),
        "cost_minor": (int(row["cost_minor"]) if row["cost_minor"] is not None else None),
        "occurred_on": str(row["occurred_on"]),
        "storage_location": str(row["storage_location"] or ""),
        "notes": str(row["notes"] or ""),
        "actor_user_id": (int(row["actor_user_id"]) if row["actor_user_id"] else None),
        "actor_username": (str(row["actor_username"]) if row.get("actor_username") else None),
        "journal_entry_id": (
            str(row["journal_entry_public_id"]) if row.get("journal_entry_public_id") else None
        ),
        "created_at_ms": int(row["created_at_ms"]),
    }


def _resolve_journal_entry_id(
    db: DbConn,
    *,
    garden_id: int,
    journal_entry_id: str | None,
) -> int | None:
    normalized = (journal_entry_id or "").strip()
    if not normalized:
        return None
    row = db.execute(
        """
        SELECT id
        FROM garden_journal_entries
        WHERE public_id = %s AND garden_id = %s
        """,
        (normalized, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Journal entry not found")
    return int(row["id"])


def _serialize_procurement_history(row: dict) -> dict:
    return {
        "id": str(row["public_id"]),
        "label": str(row["label"] or ""),
        "vendor_name": str(row["vendor_name"] or ""),
        "vendor_url": str(row["vendor_url"] or ""),
        "status": str(row["status"]),
        "quantity": _decimal_string(row["quantity"]),
        "unit": str(row["unit"] or "pieces"),
        "cost_minor": int(row["cost_minor"] or 0),
        "currency": str(row["currency"] or "NOK"),
        "ordered_on": str(row["ordered_on"]) if row["ordered_on"] else None,
        "expected_on": str(row["expected_on"]) if row["expected_on"] else None,
        "received_on": str(row["received_on"]) if row["received_on"] else None,
        "updated_at_ms": int(row["updated_at_ms"]),
    }


def _load_procurement_history(
    db: DbConn,
    garden_id: int,
    items: list[dict],
) -> dict[str, list[dict]]:
    if not items:
        return {}

    history_map: dict[str, list[dict]] = {str(item["public_id"]): [] for item in items}
    items_by_plant: dict[str, list[dict]] = {}
    for item in items:
        plt_id = str(item["plt_id"]) if item["plt_id"] else ""
        if not plt_id:
            continue
        items_by_plant.setdefault(plt_id, []).append(item)

    public_ids = list(history_map)
    internal_ids = [str(int(item["id"])) for item in items]
    plant_ids = sorted(items_by_plant)
    conditions: list[str] = []
    params: list[object] = [garden_id]
    if public_ids:
        placeholders = ",".join(["%s"] * len(public_ids))
        conditions.append(
            f"NULLIF(metadata_json, '')::jsonb ->> 'inventory_item_id' IN ({placeholders})",
        )
        params.extend(public_ids)
    if internal_ids:
        placeholders = ",".join(["%s"] * len(internal_ids))
        conditions.append(
            "("
            f"NULLIF(metadata_json, '')::jsonb ->> 'inventory_item_id' IN ({placeholders})"
            f" OR receipt_inventory_item_id IN ({placeholders}))",
        )
        params.extend(internal_ids)
        params.extend(int(item_id) for item_id in internal_ids)
    if plant_ids:
        placeholders = ",".join(["%s"] * len(plant_ids))
        conditions.append(f"linked_plt_id IN ({placeholders})")
        params.extend(plant_ids)
    if not conditions:
        return history_map

    rows = db.execute(
        f"""
        SELECT *
        FROM procurement_items
        WHERE garden_id = %s
          AND ({" OR ".join(conditions)})
        ORDER BY
            COALESCE(received_on, expected_on, ordered_on, '') DESC,
            updated_at_ms DESC,
            id DESC
        """,
        params,
    ).fetchall()

    for row in rows:
        procurement = dict(row)
        metadata = _parse_metadata(procurement.get("metadata_json"))
        attached_item_ids: set[str] = set()

        metadata_item_id = metadata.get("inventory_item_id")
        if isinstance(metadata_item_id, str) and metadata_item_id in history_map:
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

        serialized = _serialize_procurement_history(procurement)
        for item_id in attached_item_ids:
            history_map[item_id].append(serialized)

    return history_map


# ── Item CRUD ──────────────────────────────────────────────


@router.get("/inventory")
def list_inventory_items(
    request: Request,
    db: DB,
    plt_id: str | None = Query(default=None),
    inventory_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)

    conds = ["i.garden_id = %s"]
    params: list[object] = [garden_id]

    if plt_id:
        conds.append("i.plt_id = %s")
        params.append(plt_id)
    if inventory_type:
        types = [t.strip() for t in inventory_type.split(",") if t.strip()]
        if types:
            ph = ",".join(["%s"] * len(types))
            conds.append(f"i.inventory_type IN ({ph})")
            params.extend(types)
    if q:
        like = f"%{q.strip()}%"
        conds.append(
            "(i.label ILIKE %s OR COALESCE(i.plt_id, '') ILIKE %s OR COALESCE(p.name, '') ILIKE %s)"
        )
        params.extend([like, like, like])

    where = " AND ".join(conds)

    total_row = db.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM inventory_items i
        LEFT JOIN plants p ON p.plt_id = i.plt_id
        WHERE {where}
        """,
        params,
    ).fetchone()
    total = int(total_row["c"]) if total_row else 0

    rows = db.execute(
        f"""
        WITH page_items AS (
            SELECT i.*
            FROM inventory_items i
            LEFT JOIN plants p ON p.plt_id = i.plt_id
            WHERE {where}
            ORDER BY i.label, i.id
            LIMIT %s OFFSET %s
        ),
        page_qty AS (
            SELECT item_id, SUM(delta) AS qty
            FROM inventory_transactions
            WHERE item_id IN (SELECT id FROM page_items)
            GROUP BY item_id
        )
        SELECT page_items.*,
               COALESCE(page_qty.qty, 0) AS _qty
        FROM page_items
        LEFT JOIN page_qty ON page_qty.item_id = page_items.id
        ORDER BY page_items.label, page_items.id
        """,
        [*params, limit, offset],
    ).fetchall()

    row_dicts = [dict(r) for r in rows]
    procurement_history = _load_procurement_history(db, garden_id, row_dicts)

    items = []
    for d in row_dicts:
        qty = Decimal(str(d.pop("_qty", 0)))
        items.append(_serialize_item(d, qty, procurement_history.get(str(d["public_id"]), [])))

    return {"items": items, "total": total}


@router.get("/inventory/{item_id}")
def get_inventory_item(request: Request, db: DB, item_id: str) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    row = _fetch_item_by_public_id(db, item_id, garden_id)
    internal_item_id = int(row["id"])
    qty = _item_quantity(db, internal_item_id)
    procurement_history = _load_procurement_history(db, garden_id, [row])
    return _serialize_item(row, qty, procurement_history.get(str(row["public_id"]), []))


@router.post("/inventory", status_code=201)
def create_inventory_item(request: Request, db: DB, body: CreateInventoryItemBody) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    linked_plant_id = _validate_linked_plant(db, context, body.plt_id)

    now_ms = current_timestamp_ms()
    row = db.execute(
        """
        INSERT INTO inventory_items
            (garden_id, plt_id, label, inventory_type, unit,
             created_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING public_id
        """,
        (
            garden_id,
            linked_plant_id,
            body.label,
            body.inventory_type,
            body.unit,
            now_ms,
        ),
    ).fetchone()
    assert row is not None
    db.commit()
    return {"status": "ok", "id": str(row["public_id"])}


@router.patch("/inventory/{item_id}")
def update_inventory_item(
    request: Request,
    db: DB,
    item_id: str,
    body: UpdateInventoryItemBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    row = _fetch_item_by_public_id(db, item_id, garden_id)
    internal_item_id = int(row["id"])

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {"status": "ok"}

    allowed = {"plt_id", "label", "inventory_type", "unit"}
    for col in updates:
        if col not in allowed:
            raise HTTPException(400, f"Invalid field: {col}")
    if "plt_id" in updates:
        updates["plt_id"] = _validate_linked_plant(db, context, updates["plt_id"])

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values())
    values.append(internal_item_id)
    db.execute(
        f"UPDATE inventory_items SET {set_clause} "  # noqa: S608
        f"WHERE id = %s",
        values,
    )
    db.commit()
    return {"status": "ok"}


@router.delete("/inventory/{item_id}")
def delete_inventory_item(
    request: Request,
    db: DB,
    item_id: str,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    locked_row = db.execute(
        """
        SELECT *
        FROM inventory_items
        WHERE public_id = %s AND garden_id = %s
        FOR UPDATE
        """,
        (item_id, garden_id),
    ).fetchone()
    if not locked_row:
        raise HTTPException(404, "Inventory item not found")
    row = dict(locked_row)
    ledger = db.execute(
        """
        SELECT 1
        FROM inventory_transactions
        WHERE item_id = %s AND garden_id = %s
        LIMIT 1
        """,
        (int(row["id"]), garden_id),
    ).fetchone()
    if ledger:
        raise HTTPException(
            status_code=409,
            detail="Inventory items with ledger history cannot be deleted",
        )
    receipt = db.execute(
        """
        SELECT 1
        FROM procurement_items
        WHERE receipt_inventory_item_id = %s AND garden_id = %s
        LIMIT 1
        """,
        (int(row["id"]), garden_id),
    ).fetchone()
    if receipt:
        raise HTTPException(
            status_code=409,
            detail="Inventory received through procurement cannot be deleted",
        )

    db.execute("DELETE FROM inventory_items WHERE id = %s", (int(row["id"]),))
    db.commit()
    return {"status": "ok"}


def _validate_planting_plot(
    db: DbConn,
    *,
    context: AuthContext,
    garden_id: int,
    plot_id: str,
) -> str:
    normalized = plot_id.strip()
    if _is_local_admin_fallback(context):
        row = db.execute("SELECT 1 FROM plots WHERE plot_id = %s", (normalized,)).fetchone()
    else:
        row = db.execute(
            """
            SELECT 1
            FROM plot_ownership
            WHERE garden_id = %s AND plot_id = %s
            """,
            (garden_id, normalized),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Plot not found in active garden")
    return normalized


def _plant_from_stock_response(
    db: DbConn,
    *,
    garden_id: int,
    journal_entry_id: str,
) -> dict:
    row = db.execute(
        """
        SELECT t.id AS transaction_id, j.public_id AS journal_entry_id
        FROM garden_journal_entries j
        JOIN inventory_transactions t ON t.journal_entry_id = j.id
        WHERE j.public_id = %s AND j.garden_id = %s
        """,
        (journal_entry_id, garden_id),
    ).fetchone()
    if not row:
        raise_operation_target_gone()
    return {
        "status": "ok",
        "transaction_id": int(row["transaction_id"]),
        "journal_entry_id": str(row["journal_entry_id"]),
    }


@router.post("/inventory/{item_id}/plant", status_code=201)
def plant_from_stock(
    request: Request,
    db: DB,
    item_id: str,
    body: PlantFromStockBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    operation_id = read_operation_id(request)
    if operation_id is None:
        raise HTTPException(status_code=400, detail="Plant operation ID is required")
    try:
        UUID(operation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Plant operation ID must be a UUID") from exc

    _validate_date(body.occurred_on)
    item = _fetch_item_by_public_id(db, item_id, garden_id)
    plant_id = _validate_linked_plant(db, context, item.get("plt_id"))
    if plant_id is None:
        raise HTTPException(status_code=409, detail="Inventory item must be linked to a plant")
    plot_id = _validate_planting_plot(
        db,
        context=context,
        garden_id=garden_id,
        plot_id=body.plot_id,
    )

    now_ms = current_timestamp_ms()
    prepared_operation = prepare_operation(
        db,
        request=request,
        garden_id=garden_id,
        endpoint=JOURNAL_ENDPOINT,
        request_payload={"item_id": item_id, **body.model_dump(mode="json")},
        now_ms=now_ms,
        operation_namespace=INVENTORY_PLANT_OPERATION_NAMESPACE,
    )
    if prepared_operation.replay_target_id is not None:
        return _plant_from_stock_response(
            db,
            garden_id=garden_id,
            journal_entry_id=prepared_operation.replay_target_id,
        )

    journal_public_id = _generate_public_id("jrn")
    assert prepared_operation.operation is not None
    reservation = reserve_operation(
        db,
        operation=prepared_operation.operation,
        target_type=JOURNAL_TARGET,
        target_id=journal_public_id,
        created_at_ms=now_ms,
    )
    if not reservation.is_owner:
        return _plant_from_stock_response(
            db,
            garden_id=garden_id,
            journal_entry_id=reservation.result_id,
        )

    locked_item = db.execute(
        """
        SELECT id, label, unit, plt_id
        FROM inventory_items
        WHERE public_id = %s AND garden_id = %s
        FOR UPDATE
        """,
        (item_id, garden_id),
    ).fetchone()
    if not locked_item:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    if str(locked_item["plt_id"] or "") != plant_id:
        raise HTTPException(
            status_code=409,
            detail="Inventory plant link changed; retry the command",
        )
    if _item_quantity(db, int(locked_item["id"])) < body.quantity:
        raise HTTPException(status_code=409, detail="Insufficient inventory stock")

    db.execute(
        """
        INSERT INTO plot_plants (plot_id, plt_id, quantity, room_label)
        VALUES (%s, %s, 1, NULL)
        ON CONFLICT (plot_id, plt_id) DO NOTHING
        """,
        (plot_id, plant_id),
    )
    journal_row = db.execute(
        """
        INSERT INTO garden_journal_entries
            (public_id, garden_id, event_type, occurred_on, title, notes,
             metadata_json, actor_user_id, created_at_ms, updated_at_ms)
        VALUES (%s, %s, 'planted', %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            journal_public_id,
            garden_id,
            body.occurred_on,
            f"Planted {_decimal_string(body.quantity)} {locked_item['unit']} from stock",
            body.notes,
            json.dumps(
                {"inventory_item_id": item_id, "inventory_operation_id": operation_id},
                sort_keys=True,
                separators=(",", ":"),
            ),
            context.user_id,
            now_ms,
            now_ms,
        ),
    ).fetchone()
    assert journal_row is not None
    journal_id = int(journal_row["id"])
    db.execute(
        "INSERT INTO garden_journal_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
        (journal_id, plant_id),
    )
    db.execute(
        "INSERT INTO garden_journal_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
        (journal_id, plot_id),
    )
    tx_row = db.execute(
        """
        INSERT INTO inventory_transactions
            (item_id, garden_id, delta, reason, source_name, cost_minor,
             occurred_on, storage_location, notes,
             actor_user_id, journal_entry_id, created_at_ms)
        VALUES (%s, %s, %s, 'planted', '', NULL, %s, '', %s, %s, %s, %s)
        RETURNING id
        """,
        (
            int(locked_item["id"]),
            garden_id,
            -body.quantity,
            body.occurred_on,
            body.notes,
            context.user_id,
            journal_id,
            now_ms,
        ),
    ).fetchone()
    assert tx_row is not None
    db.commit()
    return {
        "status": "ok",
        "transaction_id": int(tx_row["id"]),
        "journal_entry_id": journal_public_id,
    }


# ── Transactions ───────────────────────────────────────────


@router.get("/inventory/{item_id}/transactions")
def list_transactions(
    request: Request,
    db: DB,
    item_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    row = _fetch_item_by_public_id(db, item_id, garden_id)
    internal_item_id = int(row["id"])

    total_row = db.execute(
        "SELECT COUNT(*) AS c FROM inventory_transactions WHERE item_id = %s",
        (internal_item_id,),
    ).fetchone()
    total = int(total_row["c"]) if total_row else 0

    rows = db.execute(
        """
        SELECT t.*, u.username AS actor_username, j.public_id AS journal_entry_public_id,
               i.public_id AS item_public_id
        FROM inventory_transactions t
        LEFT JOIN auth_users u ON u.id = t.actor_user_id
        LEFT JOIN garden_journal_entries j ON j.id = t.journal_entry_id
        JOIN inventory_items i ON i.id = t.item_id
        WHERE t.item_id = %s
        ORDER BY t.occurred_on DESC, t.created_at_ms DESC
        LIMIT %s OFFSET %s
        """,
        (internal_item_id, limit, offset),
    ).fetchall()

    return {
        "transactions": [_serialize_tx(dict(r)) for r in rows],
        "total": total,
    }


@router.post("/inventory/{item_id}/transactions", status_code=201)
def add_transaction(
    request: Request,
    db: DB,
    item_id: str,
    body: AddTransactionBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    row = _fetch_item_by_public_id(db, item_id, garden_id)
    internal_item_id = int(row["id"])
    _validate_date(body.occurred_on)
    if body.delta == 0:
        raise HTTPException(status_code=422, detail="Transaction delta must not be zero")
    journal_entry_id = _resolve_journal_entry_id(
        db,
        garden_id=garden_id,
        journal_entry_id=body.journal_entry_id,
    )

    locked_item = db.execute(
        """
        SELECT id
        FROM inventory_items
        WHERE id = %s AND garden_id = %s
        FOR UPDATE
        """,
        (internal_item_id, garden_id),
    ).fetchone()
    if not locked_item:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    current_quantity = _item_quantity(db, internal_item_id)
    if current_quantity + body.delta < 0:
        raise HTTPException(status_code=409, detail="Transaction would make stock negative")

    now_ms = current_timestamp_ms()
    row = db.execute(
        """
        INSERT INTO inventory_transactions
            (item_id, garden_id, delta, reason, source_name, cost_minor,
             occurred_on, storage_location, notes,
             actor_user_id, journal_entry_id, created_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (
            internal_item_id,
            garden_id,
            body.delta,
            body.reason,
            body.source_name,
            body.cost_minor,
            body.occurred_on,
            body.storage_location,
            body.notes,
            context.user_id,
            journal_entry_id,
            now_ms,
        ),
    ).fetchone()
    assert row is not None
    db.commit()
    return {"status": "ok", "id": int(row["id"])}
