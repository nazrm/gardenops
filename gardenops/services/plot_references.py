"""Central plot reference updates for rename, delete, and layout replacement."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

from gardenops.db import DbConn
from gardenops.services.media_store import collect_orphaned_media_storage_keys


@dataclass(frozen=True)
class PlotReferenceResult:
    counts: dict[str, int]
    media_storage_pairs: list[tuple[str, str]]


def _rowcount(cursor) -> int:
    return int(cursor.rowcount if cursor.rowcount is not None else 0)


def _count(db: DbConn, sql: str, params: tuple[object, ...]) -> int:
    row = db.execute(sql, params).fetchone()
    return int(row["c"] or 0) if row else 0


def _require_plot_owned_by_garden(db: DbConn, *, plot_id: str, garden_id: int) -> None:
    row = db.execute(
        """
        SELECT garden_id
        FROM plot_ownership
        WHERE plot_id = %s
        LIMIT 1
        """,
        (plot_id,),
    ).fetchone()
    if row and int(row["garden_id"]) != garden_id:
        raise HTTPException(status_code=404, detail="Plot not found in active garden")
    plot = db.execute(
        "SELECT 1 FROM plots WHERE plot_id = %s LIMIT 1",
        (plot_id,),
    ).fetchone()
    if not plot:
        raise HTTPException(status_code=404, detail="Plot not found")


def _ensure_new_plot_id_available(
    db: DbConn,
    *,
    old_plot_id: str,
    new_plot_id: str,
) -> None:
    if new_plot_id == old_plot_id:
        return
    row = db.execute(
        "SELECT 1 FROM plots WHERE plot_id = %s LIMIT 1",
        (new_plot_id,),
    ).fetchone()
    if row:
        raise HTTPException(status_code=400, detail="New plot ID already exists")


def rename_plot_references(
    db: DbConn,
    *,
    garden_id: int,
    old_plot_id: str,
    new_plot_id: str,
) -> PlotReferenceResult:
    _require_plot_owned_by_garden(db, plot_id=old_plot_id, garden_id=garden_id)
    _ensure_new_plot_id_available(db, old_plot_id=old_plot_id, new_plot_id=new_plot_id)
    counts: dict[str, int] = {}

    counts["plot_plants"] = _rowcount(
        db.execute(
            "UPDATE plot_plants SET plot_id = %s WHERE plot_id = %s",
            (new_plot_id, old_plot_id),
        ),
    )
    counts["plot_ownership"] = _rowcount(
        db.execute(
            """
            UPDATE plot_ownership
            SET plot_id = %s
            WHERE plot_id = %s AND garden_id = %s
            """,
            (new_plot_id, old_plot_id, garden_id),
        ),
    )
    counts["plot_elevations"] = _rowcount(
        db.execute(
            """
            UPDATE plot_elevations
            SET plot_id = %s
            WHERE plot_id = %s AND garden_id = %s
            """,
            (new_plot_id, old_plot_id, garden_id),
        ),
    )
    counts["plot_elevation_overrides"] = _rowcount(
        db.execute(
            """
            UPDATE plot_elevation_overrides
            SET plot_id = %s
            WHERE plot_id = %s AND garden_id = %s
            """,
            (new_plot_id, old_plot_id, garden_id),
        ),
    )
    counts["garden_issue_plots"] = _rowcount(
        db.execute(
            """
            UPDATE garden_issue_plots
            SET plot_id = %s
            WHERE plot_id = %s
              AND issue_id IN (SELECT id FROM garden_issues WHERE garden_id = %s)
            """,
            (new_plot_id, old_plot_id, garden_id),
        ),
    )
    counts["garden_task_plots"] = _rowcount(
        db.execute(
            """
            UPDATE garden_task_plots
            SET plot_id = %s
            WHERE plot_id = %s
              AND task_id IN (SELECT id FROM garden_tasks WHERE garden_id = %s)
            """,
            (new_plot_id, old_plot_id, garden_id),
        ),
    )
    counts["garden_journal_entry_plots"] = _rowcount(
        db.execute(
            """
            UPDATE garden_journal_entry_plots
            SET plot_id = %s
            WHERE plot_id = %s
              AND entry_id IN (SELECT id FROM garden_journal_entries WHERE garden_id = %s)
            """,
            (new_plot_id, old_plot_id, garden_id),
        ),
    )
    counts["harvest_entry_plots"] = _rowcount(
        db.execute(
            """
            UPDATE harvest_entry_plots
            SET plot_id = %s
            WHERE plot_id = %s
              AND entry_id IN (SELECT id FROM harvest_entries WHERE garden_id = %s)
            """,
            (new_plot_id, old_plot_id, garden_id),
        ),
    )
    counts["garden_calendar_event_plots"] = _rowcount(
        db.execute(
            """
            UPDATE garden_calendar_event_plots
            SET plot_id = %s
            WHERE plot_id = %s
              AND event_id IN (SELECT id FROM garden_calendar_events WHERE garden_id = %s)
            """,
            (new_plot_id, old_plot_id, garden_id),
        ),
    )
    counts["media_links"] = _rowcount(
        db.execute(
            """
            UPDATE media_links
            SET target_id = %s
            WHERE target_type = 'plot'
              AND target_id = %s
              AND asset_id IN (SELECT asset_id FROM media_assets WHERE garden_id = %s)
            """,
            (new_plot_id, old_plot_id, garden_id),
        ),
    )
    counts["shademap_obstacles"] = _rowcount(
        db.execute(
            """
            UPDATE shademap_obstacles
            SET linked_plot_id = %s
            WHERE linked_plot_id = %s AND garden_id = %s
            """,
            (new_plot_id, old_plot_id, garden_id),
        ),
    )
    counts["shademap_state"] = _rowcount(
        db.execute(
            """
            UPDATE shademap_state
            SET selected_plot_id = %s
            WHERE selected_plot_id = %s AND garden_id = %s
            """,
            (new_plot_id, old_plot_id, garden_id),
        ),
    )
    return PlotReferenceResult(counts=counts, media_storage_pairs=[])


def load_plot_delete_impact(
    db: DbConn,
    *,
    garden_id: int,
    plot_id: str,
) -> dict[str, object]:
    _require_plot_owned_by_garden(db, plot_id=plot_id, garden_id=garden_id)
    counts = {
        "plot_plants": _count(
            db,
            "SELECT COUNT(*) AS c FROM plot_plants WHERE plot_id = %s",
            (plot_id,),
        ),
        "garden_issue_plots": _count(
            db,
            """
            SELECT COUNT(*) AS c
            FROM garden_issue_plots
            WHERE plot_id = %s
              AND issue_id IN (SELECT id FROM garden_issues WHERE garden_id = %s)
            """,
            (plot_id, garden_id),
        ),
        "garden_task_plots": _count(
            db,
            """
            SELECT COUNT(*) AS c
            FROM garden_task_plots
            WHERE plot_id = %s
              AND task_id IN (SELECT id FROM garden_tasks WHERE garden_id = %s)
            """,
            (plot_id, garden_id),
        ),
        "garden_journal_entry_plots": _count(
            db,
            """
            SELECT COUNT(*) AS c
            FROM garden_journal_entry_plots
            WHERE plot_id = %s
              AND entry_id IN (SELECT id FROM garden_journal_entries WHERE garden_id = %s)
            """,
            (plot_id, garden_id),
        ),
        "harvest_entry_plots": _count(
            db,
            """
            SELECT COUNT(*) AS c
            FROM harvest_entry_plots
            WHERE plot_id = %s
              AND entry_id IN (SELECT id FROM harvest_entries WHERE garden_id = %s)
            """,
            (plot_id, garden_id),
        ),
        "garden_calendar_event_plots": _count(
            db,
            """
            SELECT COUNT(*) AS c
            FROM garden_calendar_event_plots
            WHERE plot_id = %s
              AND event_id IN (SELECT id FROM garden_calendar_events WHERE garden_id = %s)
            """,
            (plot_id, garden_id),
        ),
        "plot_elevations": _count(
            db,
            """
            SELECT COUNT(*) AS c
            FROM plot_elevations
            WHERE plot_id = %s AND garden_id = %s
            """,
            (plot_id, garden_id),
        ),
        "plot_elevation_overrides": _count(
            db,
            """
            SELECT COUNT(*) AS c
            FROM plot_elevation_overrides
            WHERE plot_id = %s AND garden_id = %s
            """,
            (plot_id, garden_id),
        ),
        "shademap_obstacles": _count(
            db,
            """
            SELECT COUNT(*) AS c
            FROM shademap_obstacles
            WHERE linked_plot_id = %s AND garden_id = %s
            """,
            (plot_id, garden_id),
        ),
        "shademap_state": _count(
            db,
            """
            SELECT COUNT(*) AS c
            FROM shademap_state
            WHERE selected_plot_id = %s AND garden_id = %s
            """,
            (plot_id, garden_id),
        ),
        "media_links": _count(
            db,
            """
            SELECT COUNT(*) AS c
            FROM media_links
            WHERE target_type = 'plot'
              AND target_id = %s
              AND asset_id IN (SELECT asset_id FROM media_assets WHERE garden_id = %s)
            """,
            (plot_id, garden_id),
        ),
        "media_assets_removed": _count(
            db,
            """
            SELECT COUNT(DISTINCT a.asset_id) AS c
            FROM media_assets a
            JOIN media_links l ON l.asset_id = a.asset_id
            WHERE a.garden_id = %s
              AND l.target_type = 'plot'
              AND l.target_id = %s
              AND NOT EXISTS (
                  SELECT 1
                  FROM media_links other
                  WHERE other.asset_id = a.asset_id
                    AND NOT (other.target_type = 'plot' AND other.target_id = %s)
              )
            """,
            (garden_id, plot_id, plot_id),
        ),
        "plot_ownership": _count(
            db,
            """
            SELECT COUNT(*) AS c
            FROM plot_ownership
            WHERE plot_id = %s AND garden_id = %s
            """,
            (plot_id, garden_id),
        ),
        "plots": _count(
            db,
            "SELECT COUNT(*) AS c FROM plots WHERE plot_id = %s",
            (plot_id,),
        ),
    }
    dependent_keys = set(counts) - {"plot_ownership", "plots"}
    total_dependent_references = sum(counts[key] for key in sorted(dependent_keys))
    return {
        "plot_id": plot_id,
        "counts": counts,
        "total_dependent_references": total_dependent_references,
        "has_dependents": total_dependent_references > 0,
    }


def delete_plot_references(
    db: DbConn,
    *,
    garden_id: int,
    plot_id: str,
) -> PlotReferenceResult:
    _require_plot_owned_by_garden(db, plot_id=plot_id, garden_id=garden_id)
    counts: dict[str, int] = {}

    counts["plot_plants"] = _rowcount(
        db.execute("DELETE FROM plot_plants WHERE plot_id = %s", (plot_id,)),
    )
    counts["garden_issue_plots"] = _rowcount(
        db.execute(
            """
            DELETE FROM garden_issue_plots
            WHERE plot_id = %s
              AND issue_id IN (SELECT id FROM garden_issues WHERE garden_id = %s)
            """,
            (plot_id, garden_id),
        ),
    )
    counts["garden_task_plots"] = _rowcount(
        db.execute(
            """
            DELETE FROM garden_task_plots
            WHERE plot_id = %s
              AND task_id IN (SELECT id FROM garden_tasks WHERE garden_id = %s)
            """,
            (plot_id, garden_id),
        ),
    )
    counts["garden_journal_entry_plots"] = _rowcount(
        db.execute(
            """
            DELETE FROM garden_journal_entry_plots
            WHERE plot_id = %s
              AND entry_id IN (SELECT id FROM garden_journal_entries WHERE garden_id = %s)
            """,
            (plot_id, garden_id),
        ),
    )
    counts["harvest_entry_plots"] = _rowcount(
        db.execute(
            """
            DELETE FROM harvest_entry_plots
            WHERE plot_id = %s
              AND entry_id IN (SELECT id FROM harvest_entries WHERE garden_id = %s)
            """,
            (plot_id, garden_id),
        ),
    )
    counts["garden_calendar_event_plots"] = _rowcount(
        db.execute(
            """
            DELETE FROM garden_calendar_event_plots
            WHERE plot_id = %s
              AND event_id IN (SELECT id FROM garden_calendar_events WHERE garden_id = %s)
            """,
            (plot_id, garden_id),
        ),
    )
    counts["plot_elevations"] = _rowcount(
        db.execute(
            "DELETE FROM plot_elevations WHERE plot_id = %s AND garden_id = %s",
            (plot_id, garden_id),
        ),
    )
    counts["plot_elevation_overrides"] = _rowcount(
        db.execute(
            "DELETE FROM plot_elevation_overrides WHERE plot_id = %s AND garden_id = %s",
            (plot_id, garden_id),
        ),
    )
    counts["shademap_obstacles"] = _rowcount(
        db.execute(
            """
            UPDATE shademap_obstacles
            SET linked_plot_id = NULL
            WHERE linked_plot_id = %s AND garden_id = %s
            """,
            (plot_id, garden_id),
        ),
    )
    counts["shademap_state"] = _rowcount(
        db.execute(
            """
            UPDATE shademap_state
            SET selected_plot_id = NULL
            WHERE selected_plot_id = %s AND garden_id = %s
            """,
            (plot_id, garden_id),
        ),
    )
    media_storage_pairs = collect_orphaned_media_storage_keys(
        db,
        garden_id=garden_id,
        target_type="plot",
        target_id=plot_id,
    )
    counts["media_storage_pairs"] = len(media_storage_pairs)
    counts["plot_ownership"] = _rowcount(
        db.execute(
            "DELETE FROM plot_ownership WHERE plot_id = %s AND garden_id = %s",
            (plot_id, garden_id),
        ),
    )
    counts["plots"] = _rowcount(
        db.execute("DELETE FROM plots WHERE plot_id = %s", (plot_id,)),
    )
    return PlotReferenceResult(counts=counts, media_storage_pairs=media_storage_pairs)


def delete_plots_for_replacement(
    db: DbConn,
    *,
    garden_id: int,
    plot_ids: list[str],
) -> PlotReferenceResult:
    combined_counts: dict[str, int] = {}
    media_storage_pairs: list[tuple[str, str]] = []
    for plot_id in sorted(set(plot_ids)):
        result = delete_plot_references(db, garden_id=garden_id, plot_id=plot_id)
        media_storage_pairs.extend(result.media_storage_pairs)
        for key, count in result.counts.items():
            combined_counts[key] = combined_counts.get(key, 0) + count
    return PlotReferenceResult(
        counts=combined_counts,
        media_storage_pairs=media_storage_pairs,
    )
