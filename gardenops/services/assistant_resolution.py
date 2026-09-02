from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from gardenops.db import DbConn
from gardenops.security import AuthContext
from gardenops.services.assistant_models import AssistantChoice
from gardenops.services.rhs_plant_resolver import normalize_botanical_name

ResolutionStatus = Literal["resolved", "ambiguous_plant", "ambiguous_location", "not_found"]


@dataclass(frozen=True)
class ResolvedGardenTarget:
    status: ResolutionStatus
    plant_id: str = ""
    plant_name: str = ""
    latin: str = ""
    plot_id: str = ""
    plot_label: str = ""
    choices: tuple[AssistantChoice, ...] = ()


def _location_label(row: dict) -> str:
    display = str(row.get("display_name") or "").strip()
    zone = str(row.get("zone_name") or "").strip()
    plot_id = str(row.get("plot_id") or "").strip()
    if display and zone and normalize_botanical_name(display) != normalize_botanical_name(zone):
        return f"{display} ({zone})"
    return display or zone or plot_id


def _choice(row: dict) -> AssistantChoice:
    plant_id = str(row["plt_id"])
    plot_id = str(row.get("plot_id") or "")
    name = str(row.get("name") or plant_id)
    latin = str(row.get("latin") or "")
    location = _location_label(row)
    label = name if not location else f"{name} - {location}"
    return AssistantChoice(
        value=f"{plant_id}|{plot_id}",
        label=label,
        description=latin,
    )


def _rows_for_garden(db: DbConn, context: AuthContext) -> list[dict]:
    if context.garden_id is None:
        return []
    rows = db.execute(
        """
        SELECT p.plt_id, p.name, COALESCE(p.latin, '') AS latin,
               pp.plot_id, COALESCE(pl.display_name, '') AS display_name,
               COALESCE(pl.zone_name, '') AS zone_name
        FROM plants p
        JOIN plant_ownership po
          ON po.plt_id = p.plt_id AND po.garden_id = %s
        LEFT JOIN plot_plants pp ON pp.plt_id = p.plt_id
        LEFT JOIN plots pl
          ON pl.plot_id = pp.plot_id AND pl.garden_id = po.garden_id
        ORDER BY p.name, p.plt_id, pp.plot_id
        """,
        (int(context.garden_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _plant_ids_for_taxonomy(
    db: DbConn,
    context: AuthContext,
    taxonomy_refs: list[str],
) -> set[str]:
    values = [value.strip() for value in taxonomy_refs if value.strip()]
    if not values or context.garden_id is None:
        return set()
    rows = db.execute(
        """
        SELECT DISTINCT r.plt_id
        FROM plant_external_references r
        JOIN plant_ownership po ON po.plt_id = r.plt_id
        WHERE po.garden_id = %s
          AND r.verification_status = 'verified'
          AND (r.external_id = ANY(%s) OR r.external_entity_id = ANY(%s))
        """,
        (int(context.garden_id), values, values),
    ).fetchall()
    return {str(row["plt_id"]) for row in rows}


def resolve_garden_target(
    db: DbConn,
    context: AuthContext,
    *,
    plant_query: str,
    plot_query: str = "",
    taxonomy_refs: list[str] | None = None,
) -> ResolvedGardenTarget:
    rows = _rows_for_garden(db, context)
    query = normalize_botanical_name(plant_query)
    if not query and not taxonomy_refs:
        return ResolvedGardenTarget(status="not_found")

    plant_rows: dict[str, list[dict]] = {}
    for row in rows:
        plant_rows.setdefault(str(row["plt_id"]), []).append(row)

    if plant_query in plant_rows:
        candidates = {plant_query}
    else:
        candidates = set()

    exact_latin = {
        plant_id
        for plant_id, entries in plant_rows.items()
        if query and normalize_botanical_name(str(entries[0].get("latin") or "")) == query
    }
    candidates = candidates or exact_latin
    if not candidates:
        candidates = _plant_ids_for_taxonomy(db, context, taxonomy_refs or [])
    if not candidates:
        candidates = {
            plant_id
            for plant_id, entries in plant_rows.items()
            if query and normalize_botanical_name(str(entries[0].get("name") or "")) == query
        }
    if not candidates and query:
        partial = {
            plant_id
            for plant_id, entries in plant_rows.items()
            if any(
                query in normalized or normalized in query
                for normalized in (
                    normalize_botanical_name(str(entries[0].get("name") or "")),
                    normalize_botanical_name(str(entries[0].get("latin") or "")),
                )
                if normalized
            )
        }
        if len(partial) == 1:
            candidates = partial
        elif len(partial) > 1:
            choices = tuple(_choice(plant_rows[plant_id][0]) for plant_id in sorted(partial))
            return ResolvedGardenTarget(status="ambiguous_plant", choices=choices)
    if not candidates:
        return ResolvedGardenTarget(status="not_found")
    if len(candidates) > 1:
        choices = tuple(_choice(plant_rows[plant_id][0]) for plant_id in sorted(candidates))
        return ResolvedGardenTarget(status="ambiguous_plant", choices=choices)

    plant_id = next(iter(candidates))
    entries = plant_rows[plant_id]
    first = entries[0]
    placements = [entry for entry in entries if entry.get("plot_id")]
    selected: dict | None = None
    plot_normalized = normalize_botanical_name(plot_query)
    if plot_normalized:
        exact_locations = [
            entry
            for entry in placements
            if any(
                location and (plot_normalized == location or location in plot_normalized)
                for location in {
                    normalize_botanical_name(str(entry.get("plot_id") or "")),
                    normalize_botanical_name(str(entry.get("display_name") or "")),
                    normalize_botanical_name(str(entry.get("zone_name") or "")),
                }
            )
        ]
        if len(exact_locations) == 1:
            selected = exact_locations[0]
    if selected is None and len(placements) == 1:
        selected = placements[0]
    if selected is None and len(placements) > 1:
        return ResolvedGardenTarget(
            status="ambiguous_location",
            plant_id=plant_id,
            plant_name=str(first.get("name") or plant_id),
            latin=str(first.get("latin") or ""),
            choices=tuple(_choice(entry) for entry in placements[:20]),
        )
    selected = selected or first
    return ResolvedGardenTarget(
        status="resolved",
        plant_id=plant_id,
        plant_name=str(first.get("name") or plant_id),
        latin=str(first.get("latin") or ""),
        plot_id=str(selected.get("plot_id") or ""),
        plot_label=_location_label(selected),
    )


def target_from_choice(value: str) -> tuple[str, str]:
    plant_id, separator, plot_id = value.partition("|")
    if not separator or not plant_id:
        raise ValueError("Invalid assistant choice")
    return plant_id, plot_id
