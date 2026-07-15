"""Seasonal workflow templates and scope resolution."""

from __future__ import annotations

from datetime import date

from gardenops.db import DbConn
from gardenops.services.task_generator import _bloom_months

WORKFLOW_TEMPLATES: dict[str, dict] = {
    "spring_prep": {
        "name": "Spring Preparation",
        "name_no": "V\u00e5rklargjoring",
        "months": [3, 4],
        "steps": [
            {
                "id": "assess_damage",
                "title": "Assess winter damage",
                "title_no": "Vurder vinterskader",
                "description": "Check plants for winter damage —"
                "broken branches, frost heave, bark splitting.",
                "description_no": "Sjekk planter for vinterskader —"
                "knekte grener, telehiv, barksprekker.",
                "task_type": "inspect_issue",
                "scope": "all",
            },
            {
                "id": "prune",
                "title": "Prune woody plants",
                "title_no": "Beskj\u00e6r vedaktige planter",
                "description": "Prune woody plants before spring growth starts."
                "Remove dead wood, shape the plant.",
                "description_no": "Beskjær vedaktige planter før vårveksten starter."
                "Fjern dødved, form planten.",
                "task_type": "prune",
                "scope": "woody",
            },
            {
                "id": "prepare_soil",
                "title": "Prepare soil",
                "title_no": "Klargj\u00f8r jord",
                "description": "Amend soil in empty plots with compost before planting season.",
                "description_no": "Forbedre jord i tomme bed med kompost før plantesesongen.",
                "task_type": "fertilize",
                "scope": "empty_plots",
            },
            {
                "id": "plan_plantings",
                "title": "Plan new plantings",
                "title_no": "Planlegg nye plantinger",
                "description": "Review gaps in bloom calendar and plan new plantings for the"
                "season.",
                "description_no": "Gjennomgå hull i blomstringskalenderen og planlegg nye"
                "plantinger.",
                "task_type": "sow",
                "scope": "none",
            },
            {
                "id": "watering_schedule",
                "title": "Set up watering schedule",
                "title_no": "Sett opp vanningsplan",
                "description": "Set up watering routine for moisture-sensitive plants before"
                "summer.",
                "description_no": "Sett opp vanningsrutine for fuktighetssensitive planter før"
                "sommeren.",
                "task_type": "water",
                "scope": "water_sensitive",
            },
        ],
    },
    "midsummer_check": {
        "name": "Midsummer Check",
        "name_no": "Midtsommersjekk",
        "months": [6, 7],
        "steps": [
            {
                "id": "review_growth",
                "title": "Review plant growth",
                "title_no": "Gjennomg\u00e5 plantevekst",
                "description": "Assess plant growth and record bloom timing for the season.",
                "description_no": "Vurder plantevekst og noter blomstringstidspunkt for sesongen.",
                "task_type": "observe_bloom",
                "scope": "all",
            },
            {
                "id": "deadhead",
                "title": "Deadhead spent flowers",
                "title_no": "Fjern visne blomster",
                "description": "Remove spent flowers to encourage continued blooming.",
                "description_no": "Fjern visne blomster for å fremme videre blomstring.",
                "task_type": "deadhead",
                "scope": "blooming",
            },
            {
                "id": "pest_check",
                "title": "Check for pests & disease",
                "title_no": "Sjekk skadedyr og sykdom",
                "description": "Inspect plants for pests and disease —"
                "early detection prevents spread.",
                "description_no": "Sjekk planter for skadedyr og sykdom —"
                "tidlig oppdagelse hindrer spredning.",
                "task_type": "inspect_issue",
                "scope": "all",
            },
            {
                "id": "harvest_check",
                "title": "Check harvest readiness",
                "title_no": "Sjekk innh\u00f8stingsklart",
                "description": "Check fruit and berry plants for harvest readiness.",
                "description_no": "Sjekk frukt- og bærplanter for innhøstingsklarhet.",
                "task_type": "harvest",
                "scope": "harvestable",
            },
            {
                "id": "fertilize",
                "title": "Mid-season fertilizing",
                "title_no": "Gj\u00f8dsling midt i sesongen",
                "description": "Mid-season feeding to sustain growth through summer.",
                "description_no": "Gjødsling midt i sesongen for å opprettholde vekst gjennom"
                "sommeren.",
                "task_type": "fertilize",
                "scope": "fertilize_sensitive",
            },
        ],
    },
    "end_of_season": {
        "name": "End of Season",
        "name_no": "Sesongavslutning",
        "months": [9, 10],
        "steps": [
            {
                "id": "final_harvest",
                "title": "Final harvest",
                "title_no": "Siste innh\u00f8sting",
                "description": "Harvest remaining produce before first frost.",
                "description_no": "Høst gjenværende avling før første frost.",
                "task_type": "harvest",
                "scope": "harvestable",
            },
            {
                "id": "cut_back",
                "title": "Cut back perennials",
                "title_no": "Klipp ned stauder",
                "description": "Cut back perennials after flowering to prepare for dormancy.",
                "description_no": "Klipp ned stauder etter blomstring for å forberede for dvale.",
                "task_type": "prune",
                "scope": "perennials",
            },
            {
                "id": "protect",
                "title": "Protect tender plants",
                "title_no": "Beskytt s\u00e5rbare planter",
                "description": "Cover or mulch frost-vulnerable plants before winter.",
                "description_no": "Dekk til eller muldek frostsårbare planter før vinteren.",
                "task_type": "protect",
                "scope": "frost_vulnerable",
            },
            {
                "id": "plant_bulbs",
                "title": "Plant spring bulbs",
                "title_no": "Plant v\u00e5rl\u00f8k",
                "description": "Plant spring-flowering bulbs now to root before winter.",
                "description_no": "Plant vårblomstrende løk nå for å slå rot før vinteren.",
                "task_type": "plant_out",
                "scope": "bulbs",
            },
            {
                "id": "plan_next",
                "title": "Plan next year",
                "title_no": "Planlegg neste \u00e5r",
                "description": "Review this season and plan improvements for next year.",
                "description_no": "Oppsummer sesongen og planlegg forbedringer til neste år.",
                "task_type": "observe_bloom",
                "scope": "none",
            },
        ],
    },
}

_WOODY_CATEGORIES = {"busker", "baerbusker", "traer", "tr\u00e6r", "tr\u00e6r"}
_HARVESTABLE_CATEGORIES = {"baerbusker", "fr\u00f8", "tr\u00e6r"}
_PERENNIAL_CATEGORIES = {"busker", "baerbusker"}
_WATER_KEYWORDS = {"regular", "often", "jevnlig", "ofte", "mye"}
_HARDINESS_FROST_VULNERABLE = {"H1", "H2", "H3", "H4", "H5"}
WORKFLOW_SCOPES = {
    "none",
    "all",
    "empty_plots",
    "woody",
    "blooming",
    "harvestable",
    "water_sensitive",
    "fertilize_sensitive",
    "frost_vulnerable",
    "bulbs",
    "perennials",
}


def validated_workflow_steps(template: dict, selected_steps: list[str]) -> list[dict]:
    """Return selected template steps in template order after strict validation."""
    if not selected_steps:
        raise ValueError("At least one workflow step is required")
    if len(selected_steps) != len(set(selected_steps)):
        raise ValueError("Workflow steps must be unique")

    steps = template.get("steps")
    if not isinstance(steps, list) or not steps:
        raise RuntimeError("Workflow template has no usable steps")

    step_by_id: dict[str, dict] = {}
    for raw_step in steps:
        if not isinstance(raw_step, dict):
            raise RuntimeError("Workflow template contains an invalid step")
        step_id = raw_step.get("id")
        if not isinstance(step_id, str) or not step_id or step_id in step_by_id:
            raise RuntimeError("Workflow template contains an invalid step id")
        required_text = ("title", "task_type", "scope")
        if any(
            not isinstance(raw_step.get(key), str) or not raw_step[key] for key in required_text
        ):
            raise RuntimeError(f"Workflow step '{step_id}' is not usable")
        if raw_step["scope"] not in WORKFLOW_SCOPES:
            raise RuntimeError(f"Workflow step '{step_id}' has an unsupported scope")
        step_by_id[step_id] = raw_step

    unknown = sorted(set(selected_steps) - set(step_by_id))
    if unknown:
        raise ValueError(f"Unknown workflow step: {unknown[0]}")
    selected = set(selected_steps)
    return [step for step in steps if step["id"] in selected]


def _all_plot_ids(db: DbConn, garden_id: int) -> list[str]:
    rows = db.execute(
        """
        SELECT po.plot_id
        FROM plot_ownership po
        WHERE po.garden_id = %s
        """,
        (garden_id,),
    ).fetchall()
    return [str(r["plot_id"]) for r in rows]


def _plants_with_plots(
    db: DbConn,
    garden_id: int,
    plant_ids: list[str],
) -> list[str]:
    """Return plot_ids containing any of the given plant_ids."""
    if not plant_ids:
        return []
    placeholders = ",".join(["%s"] * len(plant_ids))
    rows = db.execute(
        f"""
        SELECT DISTINCT pp.plot_id
        FROM plot_plants pp
        JOIN plot_ownership po ON po.plot_id = pp.plot_id
        WHERE po.garden_id = %s AND pp.plt_id IN ({placeholders})
        """,
        [garden_id, *plant_ids],
    ).fetchall()
    return [str(r["plot_id"]) for r in rows]


def resolve_scope(
    db: DbConn,
    garden_id: int,
    scope: str,
) -> tuple[list[str], list[str]]:
    """Return (plant_ids, plot_ids) for a workflow step scope."""
    if scope == "none":
        return [], []

    if scope == "all":
        return [], _all_plot_ids(db, garden_id)

    if scope == "empty_plots":
        rows = db.execute(
            """
            SELECT po.plot_id
            FROM plot_ownership po
            WHERE po.garden_id = %s
              AND po.plot_id NOT IN (
                  SELECT DISTINCT pp.plot_id FROM plot_plants pp
              )
            """,
            (garden_id,),
        ).fetchall()
        return [], [str(r["plot_id"]) for r in rows]

    plants = db.execute(
        """
        SELECT p.plt_id, p.category, p.bloom_month,
               p.care_watering, p.care_maintenance, p.hardiness
        FROM plants p
        JOIN plant_ownership po ON po.plt_id = p.plt_id
        WHERE po.garden_id = %s
        """,
        (garden_id,),
    ).fetchall()

    plant_ids: list[str] = []
    current_month = date.today().month

    for plant in plants:
        plt_id = str(plant["plt_id"])
        category = str(plant["category"] or "").lower()
        bloom_raw = str(plant["bloom_month"] or "")
        care_watering = str(plant["care_watering"] or "").lower()
        care_maint = str(plant["care_maintenance"] or "").lower()
        hardiness = str(plant["hardiness"] or "").upper().strip()

        matched = False
        if scope == "woody":
            matched = category in _WOODY_CATEGORIES
        elif scope == "blooming":
            matched = current_month in _bloom_months(bloom_raw)
        elif scope == "harvestable":
            matched = category in _HARVESTABLE_CATEGORIES
        elif scope == "water_sensitive":
            matched = any(kw in care_watering for kw in _WATER_KEYWORDS)
        elif scope == "fertilize_sensitive":
            matched = "fertiliz" in care_maint or "gj\u00f8dsl" in care_maint
        elif scope == "frost_vulnerable":
            matched = hardiness in _HARDINESS_FROST_VULNERABLE
        elif scope == "bulbs":
            matched = category == "l\u00f8k"
        elif scope == "perennials":
            matched = category in _PERENNIAL_CATEGORIES

        if matched:
            plant_ids.append(plt_id)

    plot_ids = _plants_with_plots(db, garden_id, plant_ids)
    return plant_ids, plot_ids
