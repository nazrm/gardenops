from __future__ import annotations

from collections.abc import Iterable

from gardenops.db import DbConn, executemany


def observation_sort_key(raw_date: str | None) -> tuple[int, int, int]:
    if raw_date is None:
        return (0, 0, 0)
    text = str(raw_date).strip()
    if len(text) < 4 or not text[:4].isdigit():
        return (0, 0, 0)
    parts = text.split("-")
    year = int(parts[0])
    month = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    day = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    return (year, month, day)


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen[text] = None
    return list(seen.keys())


def _should_replace_seen_date(
    current_seen_date: str | None,
    next_seen_date: str,
) -> bool:
    return observation_sort_key(next_seen_date) >= observation_sort_key(current_seen_date)


def mark_seen_growing_from_observation(
    db: DbConn,
    *,
    garden_id: int,
    plant_ids: list[str],
    seen_date: str,
    plot_ids: list[str] | None = None,
) -> None:
    normalized_plant_ids = _dedupe(plant_ids)
    if not normalized_plant_ids:
        return

    plant_placeholders = ",".join(["%s"] * len(normalized_plant_ids))
    plant_rows = db.execute(
        f"""
        SELECT plt_id, seen_growing_date
        FROM plants
        WHERE plt_id IN ({plant_placeholders})
        """,
        normalized_plant_ids,
    ).fetchall()
    plant_updates = [
        (seen_date, str(row["plt_id"]))
        for row in plant_rows
        if _should_replace_seen_date(
            str(row["seen_growing_date"]) if row["seen_growing_date"] else None,
            seen_date,
        )
    ]
    if plant_updates:
        executemany(
            db,
            "UPDATE plants SET seen_growing = 1, seen_growing_date = %s WHERE plt_id = %s",
            plant_updates,
        )

    normalized_plot_ids = _dedupe(plot_ids or [])
    assignment_rows: list[dict] = []
    if normalized_plot_ids:
        plot_placeholders = ",".join(["%s"] * len(normalized_plot_ids))
        assignment_rows = db.execute(
            f"""
            SELECT pp.plot_id, pp.plt_id, pp.seen_growing_date
            FROM plot_plants pp
            LEFT JOIN plot_ownership po ON po.plot_id = pp.plot_id
            WHERE pp.plt_id IN ({plant_placeholders})
              AND pp.plot_id IN ({plot_placeholders})
              AND (po.garden_id = %s OR po.garden_id IS NULL)
            ORDER BY pp.plot_id, pp.plt_id
            """,
            [*normalized_plant_ids, *normalized_plot_ids, garden_id],
        ).fetchall()
    else:
        rows = db.execute(
            f"""
            SELECT pp.plot_id, pp.plt_id, pp.seen_growing_date
            FROM plot_plants pp
            LEFT JOIN plot_ownership po ON po.plot_id = pp.plot_id
            WHERE pp.plt_id IN ({plant_placeholders})
              AND (po.garden_id = %s OR po.garden_id IS NULL)
            ORDER BY pp.plt_id, pp.plot_id
            """,
            [*normalized_plant_ids, garden_id],
        ).fetchall()
        assignments_by_plant: dict[str, list[dict]] = {}
        for row in rows:
            assignments_by_plant.setdefault(str(row["plt_id"]), []).append(dict(row))
        for plant_rows_for_id in assignments_by_plant.values():
            if len(plant_rows_for_id) == 1:
                assignment_rows.extend(plant_rows_for_id)

    assignment_updates = [
        (
            seen_date,
            str(row["plot_id"]),
            str(row["plt_id"]),
        )
        for row in assignment_rows
        if _should_replace_seen_date(
            str(row["seen_growing_date"]) if row["seen_growing_date"] else None,
            seen_date,
        )
    ]
    if assignment_updates:
        executemany(
            db,
            "UPDATE plot_plants SET seen_growing = 1, seen_growing_date = %s "
            "WHERE plot_id = %s AND plt_id = %s",
            assignment_updates,
        )
