"""Planting planner — suggests plants for empty or weak garden areas."""

from __future__ import annotations

import re
from collections.abc import Set as AbstractSet
from datetime import date

from gardenops.db import DbConn

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


def _parse_hardiness_level(raw: str) -> int:
    """Extract numeric hardiness from strings like 'H4', 'H6-H7'."""
    if not raw:
        return 0
    m = re.search(r"[Hh](\d+)", raw)
    return int(m.group(1)) if m else 0


def _normalize_category(raw: str) -> str:
    return (raw or "").strip().lower()


def _light_profiles(raw: str) -> set[str]:
    if not raw:
        return set()
    normalized = " ".join(re.findall(r"[a-zæøå0-9]+", raw.lower()))
    profiles: set[str] = set()
    if any(term in normalized for term in _SUNLIGHT_PARTIAL_TERMS):
        profiles.add("partial")
    if any(term in normalized for term in _SUNLIGHT_SUN_TERMS):
        profiles.add("sun")
    if any(term in normalized for term in _SUNLIGHT_SHADE_TERMS):
        profiles.add("shade")
    return profiles


def _score_light_match(
    *,
    candidate_light: str,
    plot_id: str,
    sunlit_plot_ids: AbstractSet[str] | None,
) -> tuple[int, list[str]]:
    if not sunlit_plot_ids:
        return 0, []
    profiles = _light_profiles(candidate_light)
    if not profiles:
        return 0, []

    if plot_id in sunlit_plot_ids:
        if "sun" in profiles:
            return 2, ["Matches the current sunlit snapshot for this plot"]
        if "partial" in profiles:
            return 1, ["Can handle mixed light in the current sun snapshot"]
        if "shade" in profiles:
            return -1, ["Prefers more shade than this plot currently gets"]
        return 0, []

    if "shade" in profiles:
        return 2, ["Fits the shadier snapshot for this plot"]
    if "partial" in profiles:
        return 1, ["Can handle the partial shade suggested by the current snapshot"]
    if "sun" in profiles:
        return -1, ["Prefers more direct sun than this plot currently gets"]
    return 0, []


def _load_recent_harvest_context(
    db: DbConn,
    garden_id: int,
    plot_ids: list[str],
) -> dict[str, dict[str, str]]:
    if not plot_ids:
        return {}
    placeholders = ",".join(["%s"] * len(plot_ids))
    rows = db.execute(
        f"""
        SELECT hep.plot_id, he.occurred_on, p.name, p.category, he.id
        FROM harvest_entry_plots hep
        JOIN harvest_entries he ON he.id = hep.entry_id
        LEFT JOIN harvest_entry_plants hp ON hp.entry_id = he.id
        LEFT JOIN plants p ON p.plt_id = hp.plt_id
        WHERE he.garden_id = %s AND hep.plot_id IN ({placeholders})
        ORDER BY hep.plot_id, he.occurred_on DESC, he.id DESC
        """,
        [garden_id, *plot_ids],
    ).fetchall()

    contexts: dict[str, dict[str, str]] = {}
    for row in rows:
        plot_id = str(row["plot_id"])
        if plot_id in contexts:
            continue
        occurred_on = str(row["occurred_on"] or "")
        try:
            if (date.today() - date.fromisoformat(occurred_on)).days > _RECENT_HARVEST_WINDOW_DAYS:
                continue
        except ValueError:
            continue
        contexts[plot_id] = {
            "occurred_on": occurred_on,
            "plant_name": str(row["name"] or "").strip(),
            "category": _normalize_category(str(row["category"] or "")),
        }
    return contexts


# ── Companion / conflict rules (category-level, simple v1) ──

COMPANIONS: dict[tuple[str, str], str] = {
    ("busker", "løk"): "Bulbs complement shrubs with different heights and seasons",
    ("frø", "busker"): "Annuals fill gaps between shrubs",
    ("løk", "frø"): "Bulbs and annuals provide layered seasonal interest",
    ("trær", "busker"): "Trees and shrubs create natural canopy layers",
    ("baerbusker", "frø"): "Berry bushes benefit from annual ground cover",
}

CONFLICTS: dict[tuple[str, str], str] = {
    ("trær", "trær"): "Multiple trees in one plot compete for root space",
}

_RECENT_HARVEST_WINDOW_DAYS = 400
_SUNLIGHT_SUN_TERMS = (
    "direct sun",
    "full sun",
    "fullsol",
    "sol",
    "sun",
)
_SUNLIGHT_PARTIAL_TERMS = (
    "partial shade",
    "part shade",
    "partial sun",
    "part sun",
    "halvskygge",
    "dappled",
)
_SUNLIGHT_SHADE_TERMS = (
    "deep shade",
    "full shade",
    "helskygge",
    "shade",
    "skygge",
)


def check_companions(
    db: DbConn,
    garden_id: int,
    plot_id: str,
    candidate_plt_id: str,
) -> dict:
    """Check companion/conflict for a candidate in a given plot.

    Returns {"companions": [...], "conflicts": [...]}.
    """
    # Get the candidate's category
    candidate = db.execute(
        "SELECT category FROM plants WHERE plt_id = %s",
        (candidate_plt_id,),
    ).fetchone()
    if not candidate:
        return {"companions": [], "conflicts": []}

    candidate_cat = (candidate["category"] or "").lower().strip()

    # Get existing plant categories in this plot
    existing = db.execute(
        """
        SELECT DISTINCT p.category
        FROM plot_plants pp
        JOIN plants p ON p.plt_id = pp.plt_id
        JOIN plot_ownership pwo ON pwo.plot_id = pp.plot_id
        WHERE pp.plot_id = %s AND pwo.garden_id = %s
        """,
        (plot_id, garden_id),
    ).fetchall()

    companions: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []

    for row in existing:
        existing_cat = (row["category"] or "").lower().strip()
        pair = (min(candidate_cat, existing_cat), max(candidate_cat, existing_cat))

        if pair in COMPANIONS:
            companions.append({"description": COMPANIONS[pair]})
        if pair in CONFLICTS:
            conflicts.append({"description": CONFLICTS[pair]})

    return {"companions": companions, "conflicts": conflicts}


def _build_garden_profile(
    db: DbConn,
    garden_id: int,
) -> dict:
    """Analyze the existing garden to build a profile."""
    # Count plots
    total_plots_row = db.execute(
        "SELECT COUNT(*) AS cnt FROM plot_ownership WHERE garden_id = %s",
        (garden_id,),
    ).fetchone()
    assert total_plots_row is not None
    total_plots = total_plots_row["cnt"]

    planted_plots_row = db.execute(
        """
        SELECT COUNT(DISTINCT pp.plot_id) AS cnt
        FROM plot_plants pp
        JOIN plot_ownership pwo ON pwo.plot_id = pp.plot_id
        WHERE pwo.garden_id = %s
        """,
        (garden_id,),
    ).fetchone()
    assert planted_plots_row is not None
    planted_plots = planted_plots_row["cnt"]

    empty_plots = total_plots - planted_plots

    # Category distribution
    cat_rows = db.execute(
        """
        SELECT p.category, COUNT(*) AS cnt
        FROM plants p
        JOIN plant_ownership po ON po.plt_id = p.plt_id
        JOIN plot_plants pp ON pp.plt_id = p.plt_id
        JOIN plot_ownership pwo ON pwo.plot_id = pp.plot_id
        WHERE po.garden_id = %s AND pwo.garden_id = %s
        GROUP BY p.category
        ORDER BY cnt DESC
        """,
        (garden_id, garden_id),
    ).fetchall()
    categories: dict[str, int] = {}
    for r in cat_rows:
        cat = r["category"] or "unknown"
        categories[cat] = r["cnt"]

    # Bloom coverage
    bloom_rows = db.execute(
        """
        SELECT DISTINCT p.bloom_month
        FROM plants p
        JOIN plant_ownership po ON po.plt_id = p.plt_id
        JOIN plot_plants pp ON pp.plt_id = p.plt_id
        JOIN plot_ownership pwo ON pwo.plot_id = pp.plot_id
        WHERE po.garden_id = %s AND pwo.garden_id = %s
          AND p.bloom_month IS NOT NULL AND p.bloom_month != ''
        """,
        (garden_id, garden_id),
    ).fetchall()
    bloom_coverage: set[int] = set()
    for r in bloom_rows:
        bloom_coverage |= _bloom_months(r["bloom_month"])
    bloom_gaps = sorted(set(range(1, 13)) - bloom_coverage)

    # Color distribution
    color_rows = db.execute(
        """
        SELECT p.color, COUNT(*) AS cnt
        FROM plants p
        JOIN plant_ownership po ON po.plt_id = p.plt_id
        JOIN plot_plants pp ON pp.plt_id = p.plt_id
        JOIN plot_ownership pwo ON pwo.plot_id = pp.plot_id
        WHERE po.garden_id = %s AND pwo.garden_id = %s
          AND p.color IS NOT NULL AND p.color != ''
        GROUP BY p.color
        ORDER BY cnt DESC
        """,
        (garden_id, garden_id),
    ).fetchall()
    colors: dict[str, int] = {}
    for r in color_rows:
        colors[r["color"]] = r["cnt"]

    # Hardiness range
    hardiness_rows = db.execute(
        """
        SELECT p.hardiness
        FROM plants p
        JOIN plant_ownership po ON po.plt_id = p.plt_id
        JOIN plot_plants pp ON pp.plt_id = p.plt_id
        JOIN plot_ownership pwo ON pwo.plot_id = pp.plot_id
        WHERE po.garden_id = %s AND pwo.garden_id = %s
          AND p.hardiness IS NOT NULL AND p.hardiness != ''
        """,
        (garden_id, garden_id),
    ).fetchall()
    h_levels = [_parse_hardiness_level(r["hardiness"]) for r in hardiness_rows]
    h_levels = [h for h in h_levels if h > 0]
    h_min = f"H{min(h_levels)}" if h_levels else ""
    h_max = f"H{max(h_levels)}" if h_levels else ""

    # Deer resistance
    deer_rows = db.execute(
        """
        SELECT
            SUM(CASE WHEN p.deer_resistant = 1 THEN 1 ELSE 0 END) AS resistant,
            SUM(CASE WHEN p.deer_resistant = 0 THEN 1 ELSE 0 END) AS vulnerable
        FROM plants p
        JOIN plant_ownership po ON po.plt_id = p.plt_id
        JOIN plot_plants pp ON pp.plt_id = p.plt_id
        JOIN plot_ownership pwo ON pwo.plot_id = pp.plot_id
        WHERE po.garden_id = %s AND pwo.garden_id = %s
        """,
        (garden_id, garden_id),
    ).fetchone()
    assert deer_rows is not None
    deer_resistant_count = deer_rows["resistant"] or 0
    deer_vulnerable_count = deer_rows["vulnerable"] or 0

    return {
        "total_plots": total_plots,
        "empty_plots": empty_plots,
        "planted_plots": planted_plots,
        "bloom_coverage": sorted(bloom_coverage),
        "bloom_gaps": bloom_gaps,
        "categories": categories,
        "colors": colors,
        "hardiness_range": {"min": h_min, "max": h_max},
        "deer_resistant_count": deer_resistant_count,
        "deer_vulnerable_count": deer_vulnerable_count,
    }


def get_planting_suggestions(
    db: DbConn,
    garden_id: int,
    target_plot_id: str | None = None,
    goal: str | None = None,
    limit: int = 10,
    sunlit_plot_ids: AbstractSet[str] | None = None,
) -> dict:
    """Generate planting suggestions for empty/weak plots.

    Returns {
        "plots": [{ "plot_id": ..., "zone_code": ..., "suggestions": [...] }],
        "bloom_gaps": [month numbers without coverage],
        "garden_stats": { "total_plots": N, "empty_plots": N, "planted_plots": N },
    }
    """
    profile = _build_garden_profile(db, garden_id)

    # 1. Find target plots
    if target_plot_id:
        target_rows = db.execute(
            """
            SELECT pl.plot_id, pl.zone_code, pl.zone_name
            FROM plots pl
            JOIN plot_ownership pwo ON pwo.plot_id = pl.plot_id
            WHERE pwo.garden_id = %s AND pl.plot_id = %s
            """,
            (garden_id, target_plot_id),
        ).fetchall()
    else:
        target_rows = db.execute(
            """
            SELECT pl.plot_id, pl.zone_code, pl.zone_name
            FROM plots pl
            JOIN plot_ownership pwo ON pwo.plot_id = pl.plot_id
            LEFT JOIN plot_plants pp ON pp.plot_id = pl.plot_id
            WHERE pwo.garden_id = %s
            GROUP BY pl.plot_id, pl.zone_code, pl.zone_name
            HAVING COUNT(pp.plt_id) <= 1
            ORDER BY COUNT(pp.plt_id), pl.zone_code, pl.plot_id
            LIMIT 20
            """,
            (garden_id,),
        ).fetchall()

    # 2. Find candidate plants (owned but not placed in any plot)
    candidates = db.execute(
        """
        SELECT p.plt_id, p.name, p.latin, p.category, p.bloom_month,
               p.color, p.hardiness, p.height_cm, p.light, p.deer_resistant
        FROM plants p
        JOIN plant_ownership po ON po.plt_id = p.plt_id
        LEFT JOIN plot_plants pp ON pp.plt_id = p.plt_id
        WHERE po.garden_id = %s AND pp.plot_id IS NULL
        """,
        (garden_id,),
    ).fetchall()

    if not candidates:
        # Try all garden plants (even placed ones) as candidates
        candidates = db.execute(
            """
            SELECT p.plt_id, p.name, p.latin, p.category, p.bloom_month,
                   p.color, p.hardiness, p.height_cm, p.light, p.deer_resistant
            FROM plants p
            JOIN plant_ownership po ON po.plt_id = p.plt_id
            WHERE po.garden_id = %s
            """,
            (garden_id,),
        ).fetchall()

    bloom_gaps = set(profile["bloom_gaps"])
    existing_categories = {
        _normalize_category(category) for category in profile["categories"].keys()
    }
    existing_colors = set(profile["colors"].keys())
    recent_harvest_context = _load_recent_harvest_context(
        db,
        garden_id,
        [str(row["plot_id"]) for row in target_rows],
    )

    # 3. Score candidates for each target plot
    plot_results: list[dict] = []
    for plot_row in target_rows:
        # Get categories already in this specific plot's zone
        zone_code = plot_row["zone_code"]
        plot_id = str(plot_row["plot_id"])
        zone_cats = db.execute(
            """
            SELECT p.category, COUNT(*) AS cnt
            FROM plot_plants pp
            JOIN plants p ON p.plt_id = pp.plt_id
            JOIN plots pl ON pl.plot_id = pp.plot_id
            JOIN plot_ownership pwo ON pwo.plot_id = pl.plot_id
            WHERE pwo.garden_id = %s AND pl.zone_code = %s
            GROUP BY p.category
            ORDER BY cnt DESC
            """,
            (garden_id, zone_code),
        ).fetchall()
        zone_dominant = _normalize_category(zone_cats[0]["category"] if zone_cats else "")
        harvest_context = recent_harvest_context.get(plot_id)

        scored: list[dict] = []
        for c in candidates:
            score = 0
            reasons: list[str] = []

            cat = _normalize_category(c["category"] or "")
            c_bloom = _bloom_months(c["bloom_month"] or "")
            c_color = (c["color"] or "").strip()
            c_hardiness = _parse_hardiness_level(c["hardiness"] or "")
            c_light = (c["light"] or "").lower().strip()
            c_deer = bool(c["deer_resistant"])

            # +3 if fills a bloom gap
            gap_fills = c_bloom & bloom_gaps
            if gap_fills:
                score += 3
                month_names = {
                    1: "Jan",
                    2: "Feb",
                    3: "Mar",
                    4: "Apr",
                    5: "May",
                    6: "Jun",
                    7: "Jul",
                    8: "Aug",
                    9: "Sep",
                    10: "Oct",
                    11: "Nov",
                    12: "Dec",
                }
                gap_str = ", ".join(month_names.get(m, str(m)) for m in sorted(gap_fills))
                reasons.append(f"Fills bloom gap: {gap_str}")

            # +2 if adds a missing category
            if cat and cat not in existing_categories:
                score += 2
                reasons.append(f"Adds {cat} category")

            # +2 if deer_resistant and goal includes "deer"
            if c_deer:
                if goal == "deer":
                    score += 2
                    reasons.append("Deer resistant (goal match)")
                else:
                    score += 1
                    reasons.append("Deer resistant")

            # +1 if adds color diversity
            if c_color and c_color not in existing_colors:
                score += 1
                reasons.append(f"Adds {c_color} color")

            # +1 if hardiness appropriate for Norway (H4+)
            if c_hardiness >= 4:
                score += 1
                reasons.append("Hardy for Norway")

            # -1 if same category as zone dominant
            if cat and cat == zone_dominant:
                score -= 1
                reasons.append(f"Zone already has many {cat}")

            if harvest_context and cat:
                last_category = harvest_context["category"]
                last_name = harvest_context["plant_name"] or "the last harvested crop"
                if last_category and cat == last_category:
                    score -= 2
                    reasons.append(f"Recent {last_name} harvest suggests rotating away from {cat}")
                elif last_category:
                    score += 2
                    reasons.append(f"Rotates after recent {last_name} harvest")

            # Goal-specific scoring
            light_profiles = _light_profiles(c_light)
            if goal == "shade" and ("shade" in light_profiles or "partial" in light_profiles):
                score += 2
                reasons.append("Shade tolerant (goal match)")
            elif goal == "color" and c_color:
                score += 1
                reasons.append("Adds color variety (goal match)")
            elif goal == "edible" and cat in ("baerbusker", "frukt", "grønnsaker", "urter"):
                score += 2
                reasons.append("Edible plant (goal match)")
            elif goal == "low_maintenance" and c_hardiness >= 5:
                score += 2
                reasons.append("Low maintenance / very hardy (goal match)")

            light_score, light_reasons = _score_light_match(
                candidate_light=c_light,
                plot_id=plot_id,
                sunlit_plot_ids=sunlit_plot_ids,
            )
            score += light_score
            reasons.extend(light_reasons)

            # Companion / conflict scoring
            companion_info = check_companions(
                db,
                garden_id,
                plot_id,
                c["plt_id"],
            )
            if companion_info["companions"]:
                n = len(companion_info["companions"])
                score += 2
                reasons.append(f"Good companion with {n} existing plant(s)")
            if companion_info["conflicts"]:
                score -= 2
                reasons.append("Conflicts with existing plants")

            if score > 0 and reasons:
                scored.append(
                    {
                        "plt_id": c["plt_id"],
                        "name": c["name"] or "",
                        "latin": c["latin"] or "",
                        "category": c["category"] or "",
                        "bloom_month": c["bloom_month"] or "",
                        "color": c["color"] or "",
                        "hardiness": c["hardiness"] or "",
                        "height_cm": c["height_cm"],
                        "light": c["light"] or "",
                        "deer_resistant": c_deer,
                        "score": score,
                        "reasons": reasons,
                    }
                )

        # Sort by score descending, take top N
        scored.sort(key=lambda x: x["score"], reverse=True)
        top = scored[:limit]

        plot_results.append(
            {
                "plot_id": plot_id,
                "zone_code": plot_row["zone_code"],
                "zone_name": plot_row["zone_name"] or "",
                "suggestions": top,
            }
        )

    return {
        "plots": plot_results,
        "bloom_gaps": sorted(bloom_gaps),
        "garden_stats": {
            "total_plots": profile["total_plots"],
            "empty_plots": profile["empty_plots"],
            "planted_plots": profile["planted_plots"],
        },
    }
