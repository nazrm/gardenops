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
        SELECT p.plt_id, p.seen_growing_date
        FROM plants p
        WHERE p.plt_id IN ({plant_placeholders})
          AND NOT EXISTS (
              SELECT 1
              FROM plant_ownership po
              WHERE po.plt_id = p.plt_id
                AND po.garden_id <> %s
          )
        """,
        [*normalized_plant_ids, garden_id],
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


def _latest_plant_bloom_date(
    db: DbConn,
    *,
    garden_id: int,
    plant_id: str,
) -> str | None:
    row = db.execute(
        """
        SELECT MAX(e.occurred_on) AS occurred_on
        FROM garden_journal_entries e
        JOIN garden_journal_entry_plants ep ON ep.entry_id = e.id
        WHERE e.garden_id = %s
          AND e.event_type = 'bloomed'
          AND ep.plt_id = %s
        """,
        (garden_id, plant_id),
    ).fetchone()
    return str(row["occurred_on"]) if row and row["occurred_on"] else None


def _latest_assignment_bloom_date(
    db: DbConn,
    *,
    garden_id: int,
    plant_id: str,
    plot_id: str,
) -> str | None:
    row = db.execute(
        """
        SELECT MAX(e.occurred_on) AS occurred_on
        FROM garden_journal_entries e
        JOIN garden_journal_entry_plants ep ON ep.entry_id = e.id
        WHERE e.garden_id = %s
          AND e.event_type = 'bloomed'
          AND ep.plt_id = %s
          AND (
              EXISTS (
                  SELECT 1
                  FROM garden_journal_entry_plots eplot
                  WHERE eplot.entry_id = e.id AND eplot.plot_id = %s
              )
              OR (
                  NOT EXISTS (
                      SELECT 1
                      FROM garden_journal_entry_plots eplot
                      WHERE eplot.entry_id = e.id
                  )
                  AND 1 = (
                      SELECT COUNT(*)
                      FROM plot_plants candidate
                      LEFT JOIN plot_ownership ownership
                        ON ownership.plot_id = candidate.plot_id
                      WHERE candidate.plt_id = ep.plt_id
                        AND (ownership.garden_id = %s OR ownership.garden_id IS NULL)
                  )
              )
          )
        """,
        (garden_id, plant_id, plot_id, garden_id),
    ).fetchone()
    return str(row["occurred_on"]) if row and row["occurred_on"] else None


def reconcile_seen_growing_after_bloom_change(
    db: DbConn,
    *,
    garden_id: int,
    previous_plant_ids: list[str],
    previous_plot_ids: list[str],
    previous_seen_date: str,
) -> None:
    """Reconcile state that can be attributed to a changed bloom observation.

    There is no source foreign key on the legacy seen-growing columns. To avoid
    overwriting manual state, only values whose date exactly matches the changed
    observation are treated as derived from it. A newer independent value is
    therefore preserved.
    """
    plant_ids = _dedupe(previous_plant_ids)
    if not plant_ids:
        return

    placeholders = ",".join(["%s"] * len(plant_ids))
    plant_rows = db.execute(
        f"""
        SELECT p.plt_id, p.seen_growing_date
        FROM plants p
        WHERE p.plt_id IN ({placeholders})
          AND NOT EXISTS (
              SELECT 1
              FROM plant_ownership po
              WHERE po.plt_id = p.plt_id AND po.garden_id <> %s
          )
        """,
        [*plant_ids, garden_id],
    ).fetchall()
    for row in plant_rows:
        if str(row["seen_growing_date"] or "") != previous_seen_date:
            continue
        plant_id = str(row["plt_id"])
        latest = _latest_plant_bloom_date(db, garden_id=garden_id, plant_id=plant_id)
        db.execute(
            "UPDATE plants SET seen_growing = %s, seen_growing_date = %s WHERE plt_id = %s",
            (1 if latest else None, latest, plant_id),
        )

    assignment_rows: list[dict] = []
    plot_ids = _dedupe(previous_plot_ids)
    if plot_ids:
        plot_placeholders = ",".join(["%s"] * len(plot_ids))
        assignment_rows = db.execute(
            f"""
            SELECT pp.plot_id, pp.plt_id, pp.seen_growing_date
            FROM plot_plants pp
            LEFT JOIN plot_ownership po ON po.plot_id = pp.plot_id
            WHERE pp.plt_id IN ({placeholders})
              AND pp.plot_id IN ({plot_placeholders})
              AND (po.garden_id = %s OR po.garden_id IS NULL)
            """,
            [*plant_ids, *plot_ids, garden_id],
        ).fetchall()
    else:
        rows = db.execute(
            f"""
            SELECT pp.plot_id, pp.plt_id, pp.seen_growing_date
            FROM plot_plants pp
            LEFT JOIN plot_ownership po ON po.plot_id = pp.plot_id
            WHERE pp.plt_id IN ({placeholders})
              AND (po.garden_id = %s OR po.garden_id IS NULL)
            ORDER BY pp.plt_id, pp.plot_id
            """,
            [*plant_ids, garden_id],
        ).fetchall()
        by_plant: dict[str, list[dict]] = {}
        for row in rows:
            by_plant.setdefault(str(row["plt_id"]), []).append(dict(row))
        assignment_rows = [items[0] for items in by_plant.values() if len(items) == 1]

    for row in assignment_rows:
        if str(row["seen_growing_date"] or "") != previous_seen_date:
            continue
        plant_id = str(row["plt_id"])
        plot_id = str(row["plot_id"])
        latest = _latest_assignment_bloom_date(
            db,
            garden_id=garden_id,
            plant_id=plant_id,
            plot_id=plot_id,
        )
        db.execute(
            """
            UPDATE plot_plants
            SET seen_growing = %s, seen_growing_date = %s
            WHERE plot_id = %s AND plt_id = %s
            """,
            (1 if latest else None, latest, plot_id, plant_id),
        )
