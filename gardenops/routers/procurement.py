from __future__ import annotations

import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field

from gardenops.db import DB, DbConn, current_timestamp_ms
from gardenops.models import StrictBaseModel
from gardenops.router_helpers import (
    active_garden_id as _active_garden_id,
)
from gardenops.router_helpers import (
    auth_context as _auth_context,
)
from gardenops.router_helpers import (
    dump_metadata as _dump_metadata,
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

ProcurementStatus = Literal["wanted", "ordered", "shipped", "received", "cancelled"]
InventoryType = Literal[
    "seed", "bulb", "tuber", "division", "bare_root", "nursery", "cutting", "other"
]

VALID_TRANSITIONS: dict[str, set[str]] = {
    "wanted": {"ordered", "cancelled"},
    "ordered": {"shipped", "cancelled"},
    "shipped": {"received", "cancelled"},
    "received": set(),
    "cancelled": {"wanted"},
}


class CreateProcurementBody(StrictBaseModel):
    label: str = Field(max_length=200)
    inventory_type: InventoryType = "other"
    linked_plt_id: str | None = None
    linked_plot_id: str | None = None
    vendor_name: str = Field(default="", max_length=200)
    vendor_url: str = Field(default="", max_length=500)
    status: ProcurementStatus = "wanted"
    cost_minor: int = Field(default=0, ge=0)
    currency: str = Field(default="NOK", max_length=10)
    quantity: Decimal = Field(default=Decimal(1), gt=0, max_digits=20, decimal_places=6)
    unit: str = Field(default="pieces", max_length=50)
    ordered_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    expected_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    notes: str = Field(default="", max_length=2000)


class UpdateProcurementBody(StrictBaseModel):
    label: str | None = Field(default=None, max_length=200)
    inventory_type: InventoryType | None = None
    linked_plt_id: str | None = None
    linked_plot_id: str | None = None
    vendor_name: str | None = Field(default=None, max_length=200)
    vendor_url: str | None = Field(default=None, max_length=500)
    status: ProcurementStatus | None = None
    cost_minor: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=10)
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=20, decimal_places=6)
    unit: str | None = Field(default=None, max_length=50)
    ordered_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    expected_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    received_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    notes: str | None = Field(default=None, max_length=2000)


class TransitionBody(StrictBaseModel):
    to_status: ProcurementStatus
    ordered_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    received_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


# ── Serialization ──


def _serialize_procurement(row: dict) -> dict:
    metadata: dict = {}
    try:
        metadata = json.loads(row.get("metadata_json") or "{}")
    except (
        json.JSONDecodeError,
        TypeError,
    ):
        pass
    return {
        "id": str(row["public_id"]),
        "garden_id": int(row["garden_id"]),
        "label": str(row["label"] or ""),
        "inventory_type": str(row["inventory_type"]),
        "linked_plt_id": str(row["linked_plt_id"]) if row["linked_plt_id"] else None,
        "linked_plot_id": str(row["linked_plot_id"]) if row.get("linked_plot_id") else None,
        "vendor_name": str(row["vendor_name"] or ""),
        "vendor_url": str(row["vendor_url"] or ""),
        "status": str(row["status"]),
        "cost_minor": int(row["cost_minor"]),
        "currency": str(row["currency"] or "NOK"),
        "quantity": _quantity_json(row["quantity"]),
        "unit": str(row["unit"] or "pieces"),
        "ordered_on": str(row["ordered_on"]) if row["ordered_on"] else None,
        "expected_on": str(row["expected_on"]) if row["expected_on"] else None,
        "received_on": str(row["received_on"]) if row["received_on"] else None,
        "notes": str(row["notes"] or ""),
        "metadata": metadata,
        "created_by_user_id": (
            int(row["created_by_user_id"]) if row["created_by_user_id"] else None
        ),
        "created_at_ms": int(row["created_at_ms"]),
        "updated_at_ms": int(row["updated_at_ms"]),
    }


def _fetch_item_by_internal_id(db: DbConn, item_id: int, garden_id: int) -> dict:
    row = db.execute(
        "SELECT * FROM procurement_items WHERE id = %s AND garden_id = %s",
        (item_id, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Procurement item not found")
    return dict(row)


def _fetch_item(db: DbConn, item_id: str, garden_id: int) -> dict:
    row = db.execute(
        "SELECT * FROM procurement_items WHERE public_id = %s AND garden_id = %s",
        (item_id, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Procurement item not found")
    return dict(row)


def _inventory_public_id_from_internal_id(
    db: DbConn,
    *,
    garden_id: int,
    item_id: int,
) -> str | None:
    row = db.execute(
        "SELECT public_id FROM inventory_items WHERE id = %s AND garden_id = %s",
        (item_id, garden_id),
    ).fetchone()
    return str(row["public_id"]) if row else None


def _quantity_json(value: object) -> int | float:
    quantity = Decimal(str(value))
    if quantity == quantity.to_integral_value():
        return int(quantity)
    return float(quantity)


def _receipt_cost_minor(unit_cost_minor: int, quantity: Decimal) -> int:
    return int((Decimal(unit_cost_minor) * quantity).quantize(Decimal(1), ROUND_HALF_UP))


def _validate_linked_plant_id(
    db: DbConn,
    *,
    context: AuthContext,
    garden_id: int,
    linked_plt_id: str | None,
) -> str | None:
    normalized = (linked_plt_id or "").strip()
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
            (garden_id, normalized),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Plant not found in active garden")
    return normalized


def _validate_linked_plot_id(
    db: DbConn,
    *,
    context: AuthContext,
    garden_id: int,
    linked_plot_id: str | None,
) -> str | None:
    normalized = (linked_plot_id or "").strip()
    if not normalized:
        return None
    if _is_local_admin_fallback(context):
        row = db.execute(
            "SELECT 1 FROM plots WHERE plot_id = %s",
            (normalized,),
        ).fetchone()
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
        raise HTTPException(status_code=404, detail="Plot not found in active garden")
    return normalized


def _find_existing_inventory_item(
    db: DbConn,
    *,
    garden_id: int,
    label: str,
    inventory_type: str,
    unit: str,
    linked_plt_id: str | None,
) -> dict | None:
    row = db.execute(
        """
        SELECT id, public_id
        FROM inventory_items
        WHERE garden_id = %s
          AND label = %s
          AND inventory_type = %s
          AND unit = %s
          AND COALESCE(plt_id, '') = COALESCE(%s, '')
        ORDER BY id
        LIMIT 1
        """,
        (garden_id, label, inventory_type, unit, linked_plt_id),
    ).fetchone()
    return dict(row) if row else None


def _ensure_received_inventory(
    db: DbConn,
    *,
    context: AuthContext,
    item_row: dict,
    received_on: str,
) -> tuple[str, int]:
    durable_tx_id = item_row.get("receipt_inventory_transaction_id")
    durable_item_id = item_row.get("receipt_inventory_item_id")
    if durable_tx_id is not None and durable_item_id is not None:
        public_id = _inventory_public_id_from_internal_id(
            db,
            garden_id=int(item_row["garden_id"]),
            item_id=int(durable_item_id),
        )
        if public_id:
            return public_id, int(durable_tx_id)

    metadata = _parse_metadata(item_row.get("metadata_json"))
    existing_tx_id = metadata.get("inventory_transaction_id")
    existing_item_id = metadata.get("inventory_item_id")
    if existing_tx_id and existing_item_id:
        internal_item_id: int | None = None
        if isinstance(existing_item_id, str):
            inventory_row = db.execute(
                """
                SELECT id, public_id
                FROM inventory_items
                WHERE public_id = %s AND garden_id = %s
                """,
                (existing_item_id, int(item_row["garden_id"])),
            ).fetchone()
            if inventory_row:
                internal_item_id = int(inventory_row["id"])
                existing_item_id = str(inventory_row["public_id"])
        elif isinstance(existing_item_id, int):
            internal_item_id = existing_item_id
            existing_item_id = _inventory_public_id_from_internal_id(
                db,
                garden_id=int(item_row["garden_id"]),
                item_id=existing_item_id,
            )
        if internal_item_id is not None and existing_item_id:
            transaction = db.execute(
                """
                SELECT actor_user_id, created_at_ms
                FROM inventory_transactions
                WHERE id = %s AND item_id = %s AND garden_id = %s
                """,
                (int(existing_tx_id), internal_item_id, int(item_row["garden_id"])),
            ).fetchone()
            if transaction:
                db.execute(
                    """
                    UPDATE procurement_items
                    SET receipt_inventory_item_id = %s,
                        receipt_inventory_transaction_id = %s,
                        received_by_user_id = %s,
                        received_at_ms = %s
                    WHERE id = %s AND garden_id = %s
                    """,
                    (
                        internal_item_id,
                        int(existing_tx_id),
                        transaction["actor_user_id"],
                        int(transaction["created_at_ms"]),
                        int(item_row["id"]),
                        int(item_row["garden_id"]),
                    ),
                )
                return str(existing_item_id), int(existing_tx_id)

    garden_id = int(item_row["garden_id"])
    inventory_item = _find_existing_inventory_item(
        db,
        garden_id=garden_id,
        label=str(item_row["label"] or ""),
        inventory_type=str(item_row["inventory_type"]),
        unit=str(item_row["unit"] or "pieces"),
        linked_plt_id=str(item_row["linked_plt_id"]) if item_row["linked_plt_id"] else None,
    )
    now_ms = current_timestamp_ms()
    if inventory_item is None:
        irow = db.execute(
            """
            INSERT INTO inventory_items
                (garden_id, plt_id, label, inventory_type, unit, created_at_ms)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, public_id
            """,
            (
                garden_id,
                item_row["linked_plt_id"],
                item_row["label"],
                item_row["inventory_type"],
                item_row["unit"],
                now_ms,
            ),
        ).fetchone()
        assert irow is not None
        inventory_item = {"id": int(irow["id"]), "public_id": str(irow["public_id"])}

    transaction_notes = str(item_row["notes"] or "")
    quantity = Decimal(str(item_row["quantity"]))

    trow = db.execute(
        """
        INSERT INTO inventory_transactions
            (item_id, garden_id, delta, reason, source_name, cost_minor,
             occurred_on, storage_location, notes,
             actor_user_id, journal_entry_id, created_at_ms)
        VALUES (%s, %s, %s, 'purchased', %s, %s, %s, '', %s, %s, NULL, %s) RETURNING id
        """,
        (
            int(inventory_item["id"]),
            garden_id,
            quantity,
            str(item_row["vendor_name"] or ""),
            _receipt_cost_minor(int(item_row["cost_minor"]), quantity),
            received_on,
            transaction_notes,
            context.user_id,
            now_ms,
        ),
    ).fetchone()
    assert trow is not None
    transaction_id = int(trow["id"])

    metadata["inventory_item_id"] = str(inventory_item["public_id"])
    metadata["inventory_transaction_id"] = transaction_id
    db.execute(
        """
        UPDATE procurement_items
        SET metadata_json = %s,
            receipt_inventory_item_id = %s,
            receipt_inventory_transaction_id = %s,
            received_by_user_id = %s,
            received_at_ms = %s,
            updated_at_ms = %s
        WHERE id = %s AND garden_id = %s
        """,
        (
            _dump_metadata(metadata),
            int(inventory_item["id"]),
            transaction_id,
            context.user_id,
            now_ms,
            now_ms,
            int(item_row["id"]),
            garden_id,
        ),
    )
    return str(inventory_item["public_id"]), transaction_id


# ── Endpoints ──


@router.get("/procurement/summary")
def procurement_summary(
    request: Request,
    db: DB,
) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)

    rows = db.execute(
        """
        SELECT status, COUNT(*) AS c, SUM(ROUND(cost_minor * quantity)) AS total_cost
        FROM procurement_items
        WHERE garden_id = %s
        GROUP BY status
        """,
        (garden_id,),
    ).fetchall()

    counts: dict[str, int] = {
        "wanted": 0,
        "ordered": 0,
        "shipped": 0,
        "received": 0,
        "cancelled": 0,
    }
    total_cost = 0
    total = 0
    for r in rows:
        s = str(r["status"])
        c = int(r["c"])
        counts[s] = c
        total += c
        total_cost += int(r["total_cost"] or 0)

    return {
        **counts,
        "total": total,
        "total_cost_minor": total_cost,
        "currency": "NOK",
    }


@router.get("/procurement")
def list_procurement(
    request: Request,
    db: DB,
    status: str | None = Query(default=None),
    inventory_type: str | None = Query(default=None),
    vendor_name: str | None = Query(default=None),
    linked_plt_id: str | None = Query(default=None),
    inventory_item_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
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
    if vendor_name:
        conditions.append("vendor_name ILIKE %s")
        params.append(f"%{vendor_name}%")
    if linked_plt_id:
        conditions.append("linked_plt_id = %s")
        params.append(linked_plt_id)
    if q:
        conditions.append("(label ILIKE %s OR vendor_name ILIKE %s OR notes ILIKE %s)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if inventory_item_id is not None:
        conditions.append(
            """(
                NULLIF(metadata_json, '')::jsonb ->> 'inventory_item_id' = %s
                OR receipt_inventory_item_id = (
                    SELECT id FROM inventory_items
                    WHERE public_id = %s AND garden_id = %s
                )
            )""",
        )
        params.extend([inventory_item_id, inventory_item_id, garden_id])

    where = " AND ".join(conditions)

    total_row = db.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM procurement_items
        WHERE {where}
        """,
        params,
    ).fetchone()
    total = int(total_row["c"] or 0) if total_row else 0

    rows = db.execute(
        f"""
        SELECT * FROM procurement_items
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
        LIMIT %s OFFSET %s
        """,
        [*params, limit, offset],
    ).fetchall()

    items = [_serialize_procurement(dict(r)) for r in rows]
    return {"items": items, "total": total}


@router.get("/procurement/{item_id}")
def get_procurement(
    request: Request,
    db: DB,
    item_id: str,
) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    row = _fetch_item(db, item_id, garden_id)
    return _serialize_procurement(row)


@router.post("/procurement", status_code=201)
def create_procurement(
    request: Request,
    db: DB,
    body: CreateProcurementBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)

    if body.status == "received":
        raise HTTPException(
            status_code=422,
            detail="Received items must use the transition endpoint",
        )

    if body.ordered_on:
        _validate_date(body.ordered_on)
    if body.expected_on:
        _validate_date(body.expected_on)
    linked_plt_id = _validate_linked_plant_id(
        db,
        context=context,
        garden_id=garden_id,
        linked_plt_id=body.linked_plt_id,
    )
    linked_plot_id = _validate_linked_plot_id(
        db,
        context=context,
        garden_id=garden_id,
        linked_plot_id=body.linked_plot_id,
    )

    now = current_timestamp_ms()
    user_id = context.user_id

    row = db.execute(
        """
        INSERT INTO procurement_items (
            garden_id, label, inventory_type, linked_plt_id,
            linked_plot_id,
            vendor_name, vendor_url, status,
            cost_minor, currency, quantity, unit,
            ordered_on, expected_on,
            notes, metadata_json,
            created_by_user_id, created_at_ms, updated_at_ms
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '{}', %s, %s, %s)
        RETURNING public_id
        """,
        (
            garden_id,
            body.label,
            body.inventory_type,
            linked_plt_id,
            linked_plot_id,
            body.vendor_name,
            body.vendor_url,
            body.status,
            body.cost_minor,
            body.currency,
            body.quantity,
            body.unit,
            body.ordered_on,
            body.expected_on,
            body.notes,
            user_id,
            now,
            now,
        ),
    ).fetchone()
    assert row is not None
    db.commit()
    return {"status": "ok", "id": str(row["public_id"])}


@router.patch("/procurement/{item_id}")
def update_procurement(
    request: Request,
    db: DB,
    item_id: str,
    body: UpdateProcurementBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)

    row = db.execute(
        """
        SELECT * FROM procurement_items
        WHERE public_id = %s AND garden_id = %s
        FOR UPDATE
        """,
        (item_id, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Procurement item not found")
    row = dict(row)
    internal_item_id = int(row["id"])

    updates: list[str] = []
    params: list[object] = []
    data = body.model_dump(exclude_unset=True)
    if "status" in data:
        raise HTTPException(
            status_code=422,
            detail="Status changes must use the transition endpoint",
        )
    if str(row["status"]) == "received" or row.get("receipt_inventory_transaction_id"):
        raise HTTPException(
            status_code=409,
            detail="Received procurement items are immutable",
        )

    for field_name, value in data.items():
        if field_name in ("ordered_on", "expected_on", "received_on") and value:
            _validate_date(str(value))
        if field_name == "linked_plt_id":
            value = _validate_linked_plant_id(
                db,
                context=context,
                garden_id=garden_id,
                linked_plt_id=str(value) if value is not None else None,
            )
        if field_name == "linked_plot_id":
            value = _validate_linked_plot_id(
                db,
                context=context,
                garden_id=garden_id,
                linked_plot_id=str(value) if value is not None else None,
            )
        updates.append(f"{field_name} = %s")
        params.append(value)

    if not updates:
        return {"status": "ok"}

    updates.append("updated_at_ms = %s")
    params.append(current_timestamp_ms())
    params.append(internal_item_id)
    params.append(garden_id)

    db.execute(
        f"""
        UPDATE procurement_items
        SET {", ".join(updates)}
        WHERE id = %s AND garden_id = %s
        """,
        params,
    )
    db.commit()
    return {"status": "ok"}


@router.post("/procurement/{item_id}/transition")
def transition_procurement(
    request: Request,
    db: DB,
    item_id: str,
    body: TransitionBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)

    row = db.execute(
        """
        SELECT * FROM procurement_items
        WHERE public_id = %s AND garden_id = %s
        FOR UPDATE
        """,
        (item_id, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Procurement item not found")
    row = dict(row)
    internal_item_id = int(row["id"])
    current_status = str(row["status"])
    target = body.to_status

    if current_status == target:
        if target == "received" and row.get("receipt_inventory_transaction_id") is None:
            received_on = str(row["received_on"] or body.received_on or date.today().isoformat())
            _validate_date(received_on)
            _ensure_received_inventory(
                db,
                context=context,
                item_row=row,
                received_on=received_on,
            )
            db.commit()
        return {"status": "ok"}
    if current_status == "received" or row.get("receipt_inventory_transaction_id"):
        raise HTTPException(
            status_code=409,
            detail="Received procurement items cannot change status",
        )

    allowed = VALID_TRANSITIONS.get(current_status, set())
    if target not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot transition from '{current_status}' to '{target}'",
        )

    updates: list[str] = ["status = %s", "updated_at_ms = %s"]
    now = current_timestamp_ms()
    params: list[object] = [target, now]

    if target == "ordered":
        ordered_on = body.ordered_on or date.today().isoformat()
        _validate_date(ordered_on)
        updates.append("ordered_on = %s")
        params.append(ordered_on)
    elif target == "received":
        received_on = body.received_on or date.today().isoformat()
        _validate_date(received_on)
        updates.append("received_on = %s")
        params.append(received_on)

    params.extend([internal_item_id, garden_id])
    db.execute(
        f"""
        UPDATE procurement_items
        SET {", ".join(updates)}
        WHERE id = %s AND garden_id = %s
        """,
        params,
    )
    if target == "received":
        refreshed = _fetch_item_by_internal_id(db, internal_item_id, garden_id)
        received_on = str(refreshed["received_on"] or date.today().isoformat())
        _ensure_received_inventory(
            db,
            context=context,
            item_row=refreshed,
            received_on=received_on,
        )
    db.commit()
    return {"status": "ok"}


@router.delete("/procurement/{item_id}")
def delete_procurement(
    request: Request,
    db: DB,
    item_id: str,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)

    row = db.execute(
        """
        SELECT * FROM procurement_items
        WHERE public_id = %s AND garden_id = %s
        FOR UPDATE
        """,
        (item_id, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Procurement item not found")
    row = dict(row)
    if str(row["status"]) == "received" or row.get("receipt_inventory_transaction_id"):
        raise HTTPException(
            status_code=409,
            detail="Received procurement items cannot be deleted",
        )
    db.execute(
        "DELETE FROM procurement_items WHERE id = %s AND garden_id = %s",
        (int(row["id"]), garden_id),
    )
    db.commit()
    return {"status": "ok"}
