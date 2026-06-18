"""Automatic garden task generation based on plant attributes and season."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from gardenops.db import DbConn, DbRow, current_timestamp_ms
from gardenops.services.ai_provider import (
    AIProviderError,
    AIProviderNotConfigured,
    generate_task_descriptions_with_ai,
    is_ai_provider_configured,
)
from gardenops.services.task_windows import derive_recommended_window_strings

_logger = logging.getLogger(__name__)

_MONTH_NAMES: dict[str, int] = {
    "jan": 1,
    "januar": 1,
    "feb": 2,
    "februar": 2,
    "mar": 3,
    "mars": 3,
    "apr": 4,
    "april": 4,
    "mai": 5,
    "may": 5,
    "jun": 6,
    "juni": 6,
    "jul": 7,
    "juli": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "okt": 10,
    "oktober": 10,
    "oct": 10,
    "nov": 11,
    "november": 11,
    "des": 12,
    "desember": 12,
    "dec": 12,
}


def _parse_month(s: str) -> int:
    t = s.strip().lower()
    if t.isdigit():
        n = int(t)
        return n if 1 <= n <= 12 else 0
    return _MONTH_NAMES.get(t, 0)


def _bloom_months(raw: str) -> set[int]:
    if not raw:
        return set()
    parts = re.split(r"[-\u2013,]", raw)
    months = [_parse_month(p) for p in parts]
    months = [m for m in months if m]
    if len(months) == 2 and months[0] <= months[1]:
        return set(range(months[0], months[1] + 1))
    return set(months)


_HARVEST_OFFSETS: dict[str, int] = {
    "baerbusker": 2,  # berry bushes: ~2 months after bloom
    "frø": 3,  # planted from seed: ~3 months after sow
    "trær": 3,  # fruit trees: ~3 months after bloom
}


_EN_MONTHS: dict[int, str] = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}

_NO_MONTHS: dict[int, str] = {
    1: "januar",
    2: "februar",
    3: "mars",
    4: "april",
    5: "mai",
    6: "juni",
    7: "juli",
    8: "august",
    9: "september",
    10: "oktober",
    11: "november",
    12: "desember",
}

_TASK_DESCRIPTION_BATCH_SIZE = 12
_AI_TASK_DESCRIPTION_TYPES = {"prune"}
_WORK_ORDER_SOURCE_PREFIX = "work_order"
_WORK_ORDER_GROUP_TYPES = {"prune", "fertilize"}


def _generated_description_metadata(
    description_no: str,
    extra: dict[str, Any] | None = None,
) -> str:
    metadata: dict[str, Any] = {
        "description_no": description_no,
        "description_generated": True,
        "description_source": "care_instructions",
    }
    if extra:
        metadata.update(extra)
    return json.dumps(metadata)


def _plant_context_from_row(plant: DbRow | Mapping[str, object]) -> dict[str, str]:
    return {
        "plt_id": str(plant["plt_id"]),
        "name": str(plant["name"] or plant["plt_id"]),
        "category": str(plant["category"] or "").lower(),
        "bloom_month": str(plant["bloom_month"] or ""),
        "light": str(plant["light"] or ""),
        "hardiness": str(plant["hardiness"] or ""),
        "care_watering": str(plant["care_watering"] or ""),
        "care_soil": str(plant["care_soil"] or ""),
        "care_planting": str(plant["care_planting"] or ""),
        "care_maintenance": str(plant["care_maintenance"] or ""),
        "care_notes": str(plant["care_notes"] or ""),
    }


def _care_blob(plant: dict[str, str]) -> str:
    return " ".join(
        (
            plant.get("category", ""),
            plant.get("light", ""),
            plant.get("hardiness", ""),
            plant.get("care_watering", ""),
            plant.get("care_soil", ""),
            plant.get("care_planting", ""),
            plant.get("care_maintenance", ""),
            plant.get("care_notes", ""),
        )
    ).lower()


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _prune_reason(plant: dict[str, str]) -> tuple[str, str]:
    care_text = _care_blob(plant)
    if plant.get("category", "") in {"baerbusker", "b\u00e6rbusker"} or _contains_any(
        care_text,
        ("berry", "berries", "fruit", "frukt", "b\u00e6r", "cane", "canes", "skudd"),
    ):
        return (
            (
                "removing old or crowded canes improves airflow and pushes energy"
                " into healthy new fruiting shoots"
            ),
            (
                "n\u00e5r gamle eller tette skudd fjernes f\u00e5r planten bedre lufting"
                " og kan bruke energien p\u00e5 friske, b\u00e6rb\u00e6rende skudd"
            ),
        )
    if _contains_any(
        care_text,
        ("dead", "deadwood", "diseased", "syk", "syke", "d\u00f8d", "d\u00f8dved", "sopp"),
    ):
        return (
            (
                "taking out dead or diseased wood lowers infection pressure"
                " and keeps the structure healthier"
            ),
            (
                "\u00e5 fjerne d\u00f8de eller syke greiner reduserer smittepresset"
                " og holder planten sunnere bygd opp"
            ),
        )
    return (
        (
            "cutting out weak or crossing growth improves light, airflow,"
            " and structure before the next flush"
        ),
        (
            "n\u00e5r svake eller kryssende greiner tas bort f\u00e5r planten bedre lys,"
            " lufting og struktur f\u00f8r neste vekstperiode"
        ),
    )


def _fertilize_reason(plant: dict[str, str]) -> tuple[str, str]:
    care_text = _care_blob(plant)
    if _contains_any(care_text, ("bloom", "flower", "blomst", "blomstr")):
        return (
            "feeding now supports bud formation and steadier flowering",
            "gj\u00f8dsling n\u00e5 st\u00f8tter knoppsetting og jevnere blomstring",
        )
    if _contains_any(care_text, ("fruit", "frukt", "berry", "b\u00e6r", "harvest", "h\u00f8st")):
        return (
            "feeding now supports strong flowering and fruit set",
            "gj\u00f8dsling n\u00e5 st\u00f8tter god blomstring og bedre fruktsetting",
        )
    return (
        "feeding now supports strong new growth while the plant is actively growing",
        "gj\u00f8dsling n\u00e5 st\u00f8tter kraftig ny vekst mens planten er i aktiv vekst",
    )


def _water_reason(plant: dict[str, str]) -> tuple[str, str]:
    care_text = _care_blob(plant)
    if _contains_any(
        care_text,
        ("establish", "establishment", "first season", "etabler", "f\u00f8rste vekstsesong"),
    ):
        return (
            "steady moisture helps roots establish deeply and prevents early stress",
            (
                "jevn fuktighet hjelper r\u00f8ttene \u00e5 etablere seg godt"
                " og hindrer tidlig stress"
            ),
        )
    if _contains_any(care_text, ("evenly moist", "jevnt fukt", "regular", "jevnlig", "ofte")):
        return (
            "steady moisture reduces drought stress and helps the plant hold leaves and flowers",
            (
                "jevn fuktighet reduserer t\u00f8rkestress og hjelper planten"
                " \u00e5 holde p\u00e5 bladverk og blomster"
            ),
        )
    return (
        "watering now reduces drought stress during active growth",
        "vanning n\u00e5 reduserer t\u00f8rkestress i perioden med aktiv vekst",
    )


def _chunk_task_specs(
    specs: list[dict[str, Any]],
    size: int = _TASK_DESCRIPTION_BATCH_SIZE,
) -> list[list[dict[str, Any]]]:
    return [specs[idx : idx + size] for idx in range(0, len(specs), size)]


def _uses_ai_task_description(spec: dict[str, Any]) -> bool:
    if spec.get("work_order"):
        return False
    task_type = str(spec.get("task_type") or "").strip().lower()
    return task_type in _AI_TASK_DESCRIPTION_TYPES


def _normalize_generated_description(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    normalized = " ".join(value.strip().split())
    if not normalized:
        return fallback
    return normalized[:4000]


def generate_task_description_overrides(
    task_specs: list[dict[str, Any]],
    *,
    preferred_locale: str = "en",
) -> dict[str, tuple[str, str]]:
    eligible_specs = [spec for spec in task_specs if _uses_ai_task_description(spec)]
    if not eligible_specs or not is_ai_provider_configured():
        return {}

    try:
        overrides: dict[str, tuple[str, str]] = {}
        for batch in _chunk_task_specs(eligible_specs):
            prompt_items = [
                {
                    "task_key": spec["task_key"],
                    "task_type": spec["task_type"],
                    "plant_name": spec["plant"]["name"],
                    "due_on": spec["due_on"],
                    "category": spec["plant"].get("category", ""),
                    "light": spec["plant"].get("light", ""),
                    "hardiness": spec["plant"].get("hardiness", ""),
                    "care_watering": spec["plant"].get("care_watering", ""),
                    "care_soil": spec["plant"].get("care_soil", ""),
                    "care_planting": spec["plant"].get("care_planting", ""),
                    "care_maintenance": spec["plant"].get("care_maintenance", ""),
                    "care_notes": spec["plant"].get("care_notes", ""),
                    "fallback_en": spec["fallback_en"],
                    "fallback_no": spec["fallback_no"],
                }
                for spec in batch
            ]
            raw_tasks = generate_task_descriptions_with_ai(
                prompt_items,
                preferred_locale=preferred_locale,
            )
            expected = {str(spec["task_key"]): spec for spec in batch}
            for item in raw_tasks:
                task_key = str(item.get("task_key", "")).strip()
                spec = expected.get(task_key)
                if spec is None or task_key in overrides:
                    continue
                overrides[task_key] = (
                    _normalize_generated_description(
                        item.get("description_en"),
                        spec["fallback_en"],
                    ),
                    _normalize_generated_description(
                        item.get("description_no"),
                        spec["fallback_no"],
                    ),
                )
        return overrides
    except AIProviderError, AIProviderNotConfigured, Exception:  # noqa: BLE001
        _logger.warning("AI task description generation failed; using deterministic fallback")
        return {}


def _rule_exists(
    db: DbConn,
    garden_id: int,
    rule_source: str,
) -> bool:
    row = db.execute(
        "SELECT 1 FROM garden_tasks WHERE garden_id = %s AND rule_source = %s",
        (garden_id, rule_source),
    ).fetchone()
    return row is not None


def _delete_pending_rule_tasks(
    db: DbConn,
    *,
    garden_id: int,
    task_type: str,
    rule_source_like: str,
) -> int:
    rows = db.execute(
        """
        SELECT t.id
        FROM garden_tasks t
        WHERE t.garden_id = %s
          AND t.task_type = %s
          AND t.status IN ('pending', 'snoozed')
          AND t.rule_source LIKE %s
        """,
        (garden_id, task_type, rule_source_like),
    ).fetchall()
    task_ids = [int(row["id"]) for row in rows]
    if not task_ids:
        return 0
    task_placeholders = ",".join(["%s"] * len(task_ids))
    delete_params: list[object] = [*task_ids]
    db.execute(
        f"DELETE FROM garden_task_plants WHERE task_id IN ({task_placeholders})",
        delete_params,
    )
    db.execute(
        f"DELETE FROM garden_task_plots WHERE task_id IN ({task_placeholders})",
        delete_params,
    )
    db.execute(
        f"DELETE FROM garden_tasks WHERE id IN ({task_placeholders})",
        delete_params,
    )
    return len(task_ids)


def _rain_covers_date(
    db: DbConn,
    garden_id: int,
    date_str: str,
) -> bool:
    """Check if an active rain_surplus alert covers the given date."""
    row = db.execute(
        """
        SELECT 1 FROM weather_alerts
        WHERE garden_id = %s AND alert_type = 'rain_surplus'
          AND dismissed = 0
          AND valid_from <= %s AND valid_until >= %s
        LIMIT 1
        """,
        (garden_id, date_str, date_str),
    ).fetchone()
    return row is not None


def _create_task(
    db: DbConn,
    garden_id: int,
    task_type: str,
    title: str,
    due_on: str,
    rule_source: str,
    plt_id: str,
    actor_user_id: int | None,
    now_ms: int,
    severity: str = "normal",
    description: str = "",
    metadata_json: str = "{}",
) -> int:
    return _create_task_for_plants(
        db,
        garden_id,
        task_type,
        title,
        due_on,
        rule_source,
        [plt_id],
        actor_user_id,
        now_ms,
        severity=severity,
        description=description,
        metadata_json=metadata_json,
    )


def _create_task_for_plants(
    db: DbConn,
    garden_id: int,
    task_type: str,
    title: str,
    due_on: str,
    rule_source: str,
    plant_ids: list[str],
    actor_user_id: int | None,
    now_ms: int,
    severity: str = "normal",
    description: str = "",
    metadata_json: str = "{}",
) -> int:
    normalized_plant_ids = list(dict.fromkeys(plant_ids))
    if not normalized_plant_ids:
        raise ValueError("At least one plant id is required to create a generated task")
    window_start_on, window_end_on, window_kind = (None, None, None)
    derived_window = derive_recommended_window_strings(task_type, due_on)
    if derived_window is not None:
        window_start_on, window_end_on = derived_window
        window_kind = "recommended"
    row = db.execute(
        """
        INSERT INTO garden_tasks
            (garden_id, task_type, title, description, status, severity,
             due_on, window_start_on, window_end_on, window_kind,
             rule_source, metadata_json,
             created_by_user_id, created_at_ms, updated_at_ms)
        VALUES (
            %s, %s, %s, %s, 'pending', %s,
            %s, %s, %s, %s,
            %s, %s,
            %s, %s, %s
        ) RETURNING id
        """,
        (
            garden_id,
            task_type,
            title,
            description,
            severity,
            due_on,
            window_start_on,
            window_end_on,
            window_kind,
            rule_source,
            metadata_json,
            actor_user_id,
            now_ms,
            now_ms,
        ),
    ).fetchone()
    assert row is not None
    task_id = int(row["id"])
    for plant_id in normalized_plant_ids:
        db.execute(
            "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
            (task_id, plant_id),
        )
    return task_id


def _iso_week_key(date_str: str) -> str:
    due_date = date.fromisoformat(date_str)
    iso_year, iso_week, _ = due_date.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _work_order_rule_source(task_type: str, week_key: str) -> str:
    return f"{_WORK_ORDER_SOURCE_PREFIX}:{task_type}:{week_key}"


def _plant_names_for_summary(plant_contexts: list[dict[str, str]]) -> list[str]:
    return sorted({plant["name"] for plant in plant_contexts})


def _summarize_names(names: list[str], *, max_names: int = 8) -> str:
    if len(names) <= max_names:
        return ", ".join(names)
    shown = ", ".join(names[:max_names])
    return f"{shown}, and {len(names) - max_names} more"


def _work_order_text(
    task_type: str,
    plant_contexts: list[dict[str, str]],
) -> tuple[str, str, str]:
    count = len(plant_contexts)
    names = _plant_names_for_summary(plant_contexts)
    names_text = _summarize_names(names)
    if task_type == "prune":
        if count == 1:
            title = f"Prune: {names[0]}"
            description_en = (
                f"Prune {names[0]} this week. Why: pruning in this window improves "
                "airflow, structure, and fruiting wood before the next growth period. "
                "Complete this work order when the plant is done."
            )
            description_no = (
                f"Beskjær {names[0]} denne uken. Hvorfor: beskjæring i dette "
                "tidsvinduet gir bedre lufting, struktur og fruktved før neste "
                "vekstperiode. Fullfør arbeidsordren når planten er ferdig."
            )
        else:
            title = f"Prune {count} plants"
            description_en = (
                f"Prune these {count} plants this week: {names_text}. "
                "Why: pruning in this window improves airflow, structure, and "
                "fruiting wood before the next growth period. "
                "Complete this work order when all listed plants are done."
            )
            description_no = (
                f"Beskjær disse {count} plantene denne uken: {names_text}. "
                "Hvorfor: beskjæring i dette tidsvinduet gir bedre lufting, "
                "struktur og fruktved før neste vekstperiode. "
                "Fullfør arbeidsordren når alle plantene på listen er ferdige."
            )
        return title, description_en, description_no

    if count == 1:
        title = f"Fertilize: {names[0]}"
        description_en = (
            f"Fertilize {names[0]} this week. Why: feeding during active growth "
            "supports strong new growth, flowering, and fruit set. Complete this "
            "work order when the plant is done."
        )
        description_no = (
            f"Gjødsle {names[0]} denne uken. Hvorfor: gjødsling i aktiv vekst "
            "støtter kraftig ny vekst, blomstring og fruktsetting. Fullfør "
            "arbeidsordren når planten er ferdig."
        )
    else:
        title = f"Fertilize {count} plants"
        description_en = (
            f"Fertilize these {count} plants this week: {names_text}. "
            "Why: feeding during active growth supports strong new growth, "
            "flowering, and fruit set. "
            "Complete this work order when all listed plants are done."
        )
        description_no = (
            f"Gjødsle disse {count} plantene denne uken: {names_text}. "
            "Hvorfor: gjødsling i aktiv vekst støtter kraftig ny vekst, "
            "blomstring og fruktsetting. "
            "Fullfør arbeidsordren når alle plantene på listen er ferdige."
        )
    if count == 1:
        return title, description_en, description_no
    return title, description_en, description_no


def _work_order_metadata(
    *,
    description_no: str,
    task_type: str,
    week_key: str,
    due_on: str,
    plant_ids: list[str],
) -> str:
    return _generated_description_metadata(
        description_no,
        {
            "description_source": "work_order",
            "work_order": True,
            "grouped_task_type": task_type,
            "group_key": f"{task_type}:{week_key}",
            "week_key": week_key,
            "due_on": due_on,
            "plant_count": len(plant_ids),
        },
    )


def _delete_legacy_grouped_tasks_for_month(
    db: DbConn,
    *,
    garden_id: int,
    target_month: int,
    target_year: int,
) -> int:
    removed = 0
    if target_month in (3, 10):
        removed += _delete_pending_rule_tasks(
            db,
            garden_id=garden_id,
            task_type="prune",
            rule_source_like=f"seasonal_prune:%:{target_year}-{target_month:02d}",
        )
    if target_month in (4, 5):
        for day in (1, 15):
            removed += _delete_pending_rule_tasks(
                db,
                garden_id=garden_id,
                task_type="fertilize",
                rule_source_like=f"fertilize:%:{target_year}-{target_month:02d}-{day:02d}",
            )
    return removed


def generate_tasks(
    db: DbConn,
    garden_id: int,
    target_month: int,
    target_year: int,
    actor_user_id: int | None,
    preferred_locale: str = "en",
) -> dict[str, int]:
    """Generate seasonal tasks for a given month.

    Returns ``{"created": N, "skipped": N}``.
    """
    now_ms = current_timestamp_ms()
    due_on = f"{target_year}-{target_month:02d}-01"
    created = 0
    skipped = 0
    created_specs: list[dict[str, Any]] = []
    work_order_candidates: dict[tuple[str, str], dict[str, Any]] = {}

    plants = db.execute(
        """
        SELECT p.plt_id, p.name, p.category,
               p.bloom_month, p.light, p.hardiness,
               p.care_watering, p.care_soil, p.care_planting,
               p.care_maintenance, p.care_notes
        FROM plants p
        JOIN plant_ownership po ON po.plt_id = p.plt_id
        WHERE po.garden_id = %s
        """,
        (garden_id,),
    ).fetchall()

    plant_contexts = [_plant_context_from_row(plant) for plant in plants]
    _delete_pending_rule_tasks(
        db,
        garden_id=garden_id,
        task_type="sow",
        rule_source_like="sow:%",
    )
    _delete_legacy_grouped_tasks_for_month(
        db,
        garden_id=garden_id,
        target_month=target_month,
        target_year=target_year,
    )

    for plant_ctx in plant_contexts:
        plt_id = plant_ctx["plt_id"]
        name = plant_ctx["name"]
        category = plant_ctx["category"]
        bloom_raw = plant_ctx["bloom_month"]
        care_watering = plant_ctx["care_watering"].lower()
        care_maintenance = plant_ctx["care_maintenance"].lower()

        # Rule 1: Bloom observation
        if target_month in _bloom_months(bloom_raw):
            rule = f"bloom_observe:{plt_id}:{target_year}-{target_month:02d}"
            if _rule_exists(db, garden_id, rule):
                skipped += 1
            else:
                desc_en, desc_no = _infer_descriptions_for_rule(
                    plant_ctx,
                    "bloom_observe",
                    target_month,
                )
                task_id = _create_task(
                    db,
                    garden_id,
                    "observe_bloom",
                    f"Observe bloom: {name}",
                    due_on,
                    rule,
                    plt_id,
                    actor_user_id,
                    now_ms,
                    description=desc_en,
                    metadata_json=_generated_description_metadata(desc_no),
                )
                created_specs.append(
                    {
                        "task_key": str(task_id),
                        "task_id": task_id,
                        "task_type": "observe_bloom",
                        "due_on": due_on,
                        "plant": plant_ctx,
                        "fallback_en": desc_en,
                        "fallback_no": desc_no,
                    }
                )
                created += 1

        # Rule 2: Seasonal pruning (March/October)
        if category in ("busker", "baerbusker", "traer", "tr\u00e6r") and target_month in (3, 10):
            rule = f"seasonal_prune:{plt_id}:{target_year}-{target_month:02d}"
            week_key = _iso_week_key(due_on)
            group_rule = _work_order_rule_source("prune", week_key)
            if _rule_exists(db, garden_id, group_rule) or _rule_exists(db, garden_id, rule):
                skipped += 1
            else:
                bucket = work_order_candidates.setdefault(
                    ("prune", week_key),
                    {
                        "task_type": "prune",
                        "due_on": due_on,
                        "week_key": week_key,
                        "plant_contexts": [],
                    },
                )
                bucket["plant_contexts"].append(plant_ctx)

        # Rule 3: Fertilize biweekly (April/May)
        if target_month in (4, 5) and (
            "fertiliz" in care_maintenance or "gj\u00f8dsl" in care_maintenance
        ):
            for day in (1, 15):
                fert_date = f"{target_year}-{target_month:02d}-{day:02d}"
                rule = f"fertilize:{plt_id}:{fert_date}"
                week_key = _iso_week_key(fert_date)
                group_rule = _work_order_rule_source("fertilize", week_key)
                if _rule_exists(db, garden_id, group_rule) or _rule_exists(db, garden_id, rule):
                    skipped += 1
                else:
                    bucket = work_order_candidates.setdefault(
                        ("fertilize", week_key),
                        {
                            "task_type": "fertilize",
                            "due_on": fert_date,
                            "week_key": week_key,
                            "plant_contexts": [],
                        },
                    )
                    if fert_date < str(bucket["due_on"]):
                        bucket["due_on"] = fert_date
                    bucket["plant_contexts"].append(plant_ctx)

        # Rule 4: Weekly watering (June-August)
        # Skip watering if a rain_surplus alert covers the due date.
        if target_month in (6, 7, 8) and any(
            kw in care_watering for kw in ("regular", "often", "jevnlig", "ofte", "mye")
        ):
            for day in (1, 8, 15, 22):
                water_date = f"{target_year}-{target_month:02d}-{day:02d}"
                rule = f"water:{plt_id}:{water_date}"
                if _rule_exists(db, garden_id, rule):
                    skipped += 1
                elif _rain_covers_date(db, garden_id, water_date):
                    _logger.info(
                        "Skipped watering task for %s on %s — rain alert active",
                        plt_id,
                        water_date,
                    )
                    skipped += 1
                else:
                    desc_en, desc_no = _infer_descriptions_for_rule(
                        plant_ctx,
                        "water",
                        target_month,
                    )
                    task_id = _create_task(
                        db,
                        garden_id,
                        "water",
                        f"Water: {name}",
                        water_date,
                        rule,
                        plt_id,
                        actor_user_id,
                        now_ms,
                        description=desc_en,
                        metadata_json=_generated_description_metadata(desc_no),
                    )
                    created_specs.append(
                        {
                            "task_key": str(task_id),
                            "task_id": task_id,
                            "task_type": "water",
                            "due_on": water_date,
                            "plant": plant_ctx,
                            "fallback_en": desc_en,
                            "fallback_no": desc_no,
                        }
                    )
                    created += 1

        # Rule 5: Planting out bulbs (September-October)
        if category == "l\u00f8k" and target_month in (9, 10):
            rule = f"plant_out:{plt_id}:{target_year}-{target_month:02d}"
            if _rule_exists(db, garden_id, rule):
                skipped += 1
            else:
                desc_en, desc_no = _infer_descriptions_for_rule(
                    plant_ctx,
                    "plant_out",
                    target_month,
                )
                task_id = _create_task(
                    db,
                    garden_id,
                    "plant_out",
                    f"Plant out: {name}",
                    due_on,
                    rule,
                    plt_id,
                    actor_user_id,
                    now_ms,
                    description=desc_en,
                    metadata_json=_generated_description_metadata(desc_no),
                )
                created_specs.append(
                    {
                        "task_key": str(task_id),
                        "task_id": task_id,
                        "task_type": "plant_out",
                        "due_on": due_on,
                        "plant": plant_ctx,
                        "fallback_en": desc_en,
                        "fallback_no": desc_no,
                    }
                )
                created += 1

        # Rule 6: Harvest window check
        offset = _HARVEST_OFFSETS.get(category)
        if offset:
            for bm in _bloom_months(bloom_raw):
                harvest_month = bm + offset
                if harvest_month > 12:
                    harvest_month -= 12
                if harvest_month == target_month:
                    h_date = date(target_year, target_month, 15).isoformat()
                    rule = f"harvest_check:{plt_id}:{h_date}"
                    if _rule_exists(db, garden_id, rule):
                        skipped += 1
                    else:
                        desc_en, desc_no = _infer_descriptions_for_rule(
                            plant_ctx,
                            "harvest_check",
                            target_month,
                        )
                        task_id = _create_task(
                            db,
                            garden_id,
                            "harvest",
                            f"Harvest check: {name}",
                            h_date,
                            rule,
                            plt_id,
                            actor_user_id,
                            now_ms,
                            severity="low",
                            description=desc_en,
                            metadata_json=_generated_description_metadata(desc_no),
                        )
                        created_specs.append(
                            {
                                "task_key": str(task_id),
                                "task_id": task_id,
                                "task_type": "harvest",
                                "due_on": h_date,
                                "plant": plant_ctx,
                                "fallback_en": desc_en,
                                "fallback_no": desc_no,
                            }
                        )
                        created += 1
                    break

    for _, bucket in sorted(
        work_order_candidates.items(),
        key=lambda item: (str(item[1]["due_on"]), str(item[1]["task_type"])),
    ):
        task_type = str(bucket["task_type"])
        if task_type not in _WORK_ORDER_GROUP_TYPES:
            continue
        week_key = str(bucket["week_key"])
        group_rule = _work_order_rule_source(task_type, week_key)
        if _rule_exists(db, garden_id, group_rule):
            skipped += len(bucket["plant_contexts"])
            continue
        plant_contexts = sorted(
            bucket["plant_contexts"],
            key=lambda plant: (plant["name"], plant["plt_id"]),
        )
        plant_ids = [plant["plt_id"] for plant in plant_contexts]
        title, desc_en, desc_no = _work_order_text(task_type, plant_contexts)
        _create_task_for_plants(
            db,
            garden_id,
            task_type,
            title,
            str(bucket["due_on"]),
            group_rule,
            plant_ids,
            actor_user_id,
            now_ms,
            description=desc_en,
            metadata_json=_work_order_metadata(
                description_no=desc_no,
                task_type=task_type,
                week_key=week_key,
                due_on=str(bucket["due_on"]),
                plant_ids=plant_ids,
            ),
        )
        created += 1

    overrides = generate_task_description_overrides(
        created_specs,
        preferred_locale=preferred_locale,
    )
    for spec in created_specs:
        override = overrides.get(spec["task_key"])
        if override is None:
            continue
        desc_en, desc_no = override
        db.execute(
            """
            UPDATE garden_tasks
            SET description = %s,
                metadata_json = %s,
                updated_at_ms = %s
            WHERE id = %s
            """,
            (
                desc_en,
                _generated_description_metadata(desc_no),
                now_ms,
                spec["task_id"],
            ),
        )

    db.commit()
    return {"created": created, "skipped": skipped}


def _lookup_plant_context(db: DbConn, plt_id: str) -> dict[str, str]:
    row = db.execute(
        """
        SELECT plt_id, name, category, bloom_month, light, hardiness,
               care_watering, care_soil, care_planting, care_maintenance, care_notes
        FROM plants
        WHERE plt_id = %s
        """,
        (plt_id,),
    ).fetchone()
    if row is None:
        return {
            "plt_id": plt_id,
            "name": plt_id,
            "category": "",
            "bloom_month": "",
            "light": "",
            "hardiness": "",
            "care_watering": "",
            "care_soil": "",
            "care_planting": "",
            "care_maintenance": "",
            "care_notes": "",
        }
    return _plant_context_from_row(row)


def _lookup_task_plant_contexts(db: DbConn, task_id: int) -> list[dict[str, str]]:
    rows = db.execute(
        """
        SELECT p.plt_id, p.name, p.category, p.bloom_month, p.light, p.hardiness,
               p.care_watering, p.care_soil, p.care_planting, p.care_maintenance,
               p.care_notes
        FROM garden_task_plants gtp
        JOIN plants p ON p.plt_id = gtp.plt_id
        WHERE gtp.task_id = %s
        ORDER BY p.name, p.plt_id
        """,
        (task_id,),
    ).fetchall()
    return [_plant_context_from_row(row) for row in rows]


def _infer_bloom(plant: dict[str, str], month: int) -> tuple[str, str]:
    name = plant["name"]
    mo_en = _EN_MONTHS.get(month, "")
    mo_no = _NO_MONTHS.get(month, "")
    en = (
        f"Observe {name} in {mo_en}. Why: bloom timing helps you tune pruning,"
        " feeding, and next season's task timing."
    )
    no = (
        f"Observer {name} i {mo_no}. Hvorfor: blomstringstidspunktet hjelper deg"
        " \u00e5 justere beskj\u00e6ring, gj\u00f8dsling og neste sesongs oppgaveplan."
    )
    return en, no


def _infer_prune(plant: dict[str, str], month: int) -> tuple[str, str]:
    name = plant["name"]
    reason_en, reason_no = _prune_reason(plant)
    if month == 3:
        en = f"Prune {name} before spring growth starts. Why: {reason_en}."
        no = f"Beskj\u00e6r {name} f\u00f8r v\u00e5rveksten starter. Hvorfor: {reason_no}."
    else:
        en = f"Prune {name} after leaf drop. Why: {reason_en} before winter weather sets in."
        no = (
            f"Beskj\u00e6r {name} etter l\u00f8vfall."
            f" Hvorfor: {reason_no} f\u00f8r vinterv\u00e6ret setter inn."
        )
    return en, no


def _infer_fertilize(plant: dict[str, str], month: int) -> tuple[str, str]:
    name = plant["name"]
    mo_en = _EN_MONTHS.get(month, "")
    mo_no = _NO_MONTHS.get(month, "")
    reason_en, reason_no = _fertilize_reason(plant)
    en = f"Feed {name} in {mo_en}. Why: {reason_en}."
    no = f"Gi {name} gj\u00f8dsel i {mo_no}. Hvorfor: {reason_no}."
    return en, no


def _infer_water(plant: dict[str, str], month: int) -> tuple[str, str]:
    name = plant["name"]
    mo_en = _EN_MONTHS.get(month, "")
    mo_no = _NO_MONTHS.get(month, "")
    reason_en, reason_no = _water_reason(plant)
    en = f"Water {name} regularly in {mo_en}. Why: {reason_en}. Check soil moisture before soaking."
    no = (
        f"Vann {name} jevnlig i {mo_no}. Hvorfor: {reason_no}."
        " Sjekk jordfuktigheten f\u00f8r du vanner godt."
    )
    return en, no


def _infer_sow(plant: dict[str, str], month: int) -> tuple[str, str]:
    name = plant["name"]
    mo_en = _EN_MONTHS.get(month, "")
    mo_no = _NO_MONTHS.get(month, "")
    en = (
        f"Start {name} indoors in {mo_en}. Why: an early start gives roots and shoots"
        " time to establish before summer."
    )
    no = (
        f"S\u00e5 {name} innend\u00f8rs i {mo_no}. Hvorfor: en tidlig start gir"
        " r\u00f8tter og skudd tid til \u00e5 etablere seg f\u00f8r sommeren."
    )
    return en, no


def _infer_plant_out(plant: dict[str, str], month: int) -> tuple[str, str]:
    name = plant["name"]
    mo_en = _EN_MONTHS.get(month, "")
    mo_no = _NO_MONTHS.get(month, "")
    en = (
        f"Plant {name} out in {mo_en}. Why: early rooting before winter improves"
        " spring growth and flowering."
    )
    no = (
        f"Plant {name} ut i {mo_no}. Hvorfor: tidlig roting f\u00f8r vinteren"
        " gir bedre vekst og blomstring til v\u00e5ren."
    )
    return en, no


def _infer_harvest(
    plant: dict[str, str],
    month: int,
) -> tuple[str, str]:
    name = plant["name"]
    offset = _HARVEST_OFFSETS.get(plant.get("category", ""), 0)
    mo_en = _EN_MONTHS.get(month, "")
    mo_no = _NO_MONTHS.get(month, "")
    if offset:
        en = (
            f"Check {name} for harvest readiness in {mo_en}."
            f" Why: it usually matures about {offset} month"
            f"{'' if offset == 1 else 's'} after bloom, and picking at peak ripeness"
            " protects flavour and reduces losses."
        )
        no = (
            f"Sjekk om {name} er klar for h\u00f8sting i {mo_no}."
            f" Hvorfor: den modner vanligvis rundt {offset} m\u00e5ned"
            f"{'' if offset == 1 else 'er'} etter blomstring, og h\u00f8sting p\u00e5 topp"
            " modenhet gir bedre smak og mindre svinn."
        )
    else:
        en = (
            f"Check {name} for harvest readiness in {mo_en}."
            " Why: picking at the right time keeps quality high and limits spoilage."
        )
        no = (
            f"Sjekk om {name} er klar for h\u00f8sting i {mo_no}."
            " Hvorfor: h\u00f8sting til riktig tid gir bedre kvalitet og mindre svinn."
        )
    return en, no


def _infer_auto_frost_protect(plant: dict[str, str]) -> tuple[str, str]:
    name = plant["name"]
    return (
        (
            f"Protect {name} from frost now. Why: covering or moving it early"
            " helps prevent cold damage to tender growth."
        ),
        (
            f"Beskytt {name} mot frost nå. Hvorfor: tidlig tildekking eller flytting"
            " reduserer kuldeskader på ømfintlig vekst."
        ),
    )


def _infer_auto_heat_protect(plant: dict[str, str]) -> tuple[str, str]:
    name = plant["name"]
    return (
        (
            f"Give {name} shade and extra water in the heat. Why: lowering heat"
            " stress helps the plant hold moisture and avoid scorch."
        ),
        (
            f"Gi {name} skygge og ekstra vann i varmen. Hvorfor: mindre varmestress"
            " hjelper planten å holde på fuktighet og unngå sviskader."
        ),
    )


def _infer_auto_dry_water(plant: dict[str, str]) -> tuple[str, str]:
    name = plant["name"]
    return (
        (
            f"Water {name} regularly during the dry spell. Why: steady moisture"
            " reduces drought stress before the roots dry back."
        ),
        (
            f"Vann {name} jevnlig i tørkeperioden. Hvorfor: jevn fuktighet"
            " reduserer tørkestress før røttene tørker tilbake."
        ),
    )


def _infer_auto_rain_drainage(plant: dict[str, str]) -> tuple[str, str]:
    name = plant["name"]
    return (
        (
            f"Check drainage around {name} after heavy rain. Why: draining standing"
            " water quickly lowers the risk of root stress and rot."
        ),
        (
            f"Sjekk dreneringen rundt {name} etter kraftig regn. Hvorfor: rask avledning"
            " av stående vann reduserer risikoen for rotstress og råte."
        ),
    )


def _infer_descriptions_for_rule(
    plant: dict[str, str],
    rule_type: str,
    month: int,
) -> tuple[str, str]:
    if rule_type == "bloom_observe":
        return _infer_bloom(plant, month)
    if rule_type == "seasonal_prune":
        return _infer_prune(plant, month)
    if rule_type == "fertilize":
        return _infer_fertilize(plant, month)
    if rule_type == "water":
        return _infer_water(plant, month)
    if rule_type == "sow":
        return _infer_sow(plant, month)
    if rule_type == "plant_out":
        return _infer_plant_out(plant, month)
    if rule_type == "harvest_check":
        return _infer_harvest(plant, month)
    return ("", "")


def _parse_month_from_date(date_str: str) -> int:
    """Extract month number from a YYYY-MM or YYYY-MM-DD string."""
    parts = date_str.split("-")
    if len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    return 0


def infer_task_description(
    db: DbConn,
    task_row: dict,
) -> tuple[str, str]:
    """Infer bilingual descriptions for a task from its rule_source.

    Args:
        db: Database connection.
        task_row: Dict with at least a ``rule_source`` key.

    Returns:
        ``(description_en, description_no)`` tuple. Both empty strings
        if the rule source is unrecognized.
    """
    rule = str(task_row.get("rule_source") or "")
    if not rule:
        return ("", "")

    parts = rule.split(":")

    # auto:issue_followup:{id}
    if rule.startswith("auto:issue_followup:"):
        return (
            "Follow up on reported issue \u2014 check current condition.",
            "F\u00f8lg opp rapportert problem \u2014 sjekk n\u00e5v\u00e6rende tilstand.",
        )

    # auto:frost_protect:{alert_id}:{plt_id}
    if len(parts) >= 4 and parts[0] == "auto":
        auto_type = parts[1]
        plt_id = parts[3]
        plant = _lookup_plant_context(db, plt_id)
        if auto_type == "frost_protect":
            return _infer_auto_frost_protect(plant)
        if auto_type == "heat_protect":
            return _infer_auto_heat_protect(plant)
        if auto_type == "dry_water":
            return _infer_auto_dry_water(plant)
        if auto_type == "rain_drainage":
            return _infer_auto_rain_drainage(plant)

    # auto:escalation:{id}:{date}
    if rule.startswith("auto:escalation:"):
        return (
            "Escalated: task still incomplete past deadline.",
            "Eskalert: oppgave fortsatt ufullf\u00f8rt etter fristen.",
        )

    # workflow:{wf_id}:{step_id}:{year}
    if parts[0] == "workflow":
        return ("", "")

    # work_order:{task_type}:{iso_week}
    if parts[0] == _WORK_ORDER_SOURCE_PREFIX and len(parts) >= 3:
        task_type = parts[1]
        if task_type not in _WORK_ORDER_GROUP_TYPES or "id" not in task_row:
            return ("", "")
        plant_contexts = _lookup_task_plant_contexts(db, int(task_row["id"]))
        if not plant_contexts:
            return ("", "")
        _, description_en, description_no = _work_order_text(task_type, plant_contexts)
        return description_en, description_no

    # Plant-based rules: type:{plt_id}:{date}
    if len(parts) < 3:
        return ("", "")

    rule_type = parts[0]
    plt_id = parts[1]
    date_str = parts[2]
    month = _parse_month_from_date(date_str)
    plant = _lookup_plant_context(db, plt_id)
    return _infer_descriptions_for_rule(plant, rule_type, month)
