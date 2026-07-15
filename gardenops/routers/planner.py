"""Planner router – suggestions, companions, garden profile, goal persistence."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Body, Query, Request

from gardenops.db import DB
from gardenops.router_helpers import (
    active_garden_id as _active_garden_id,
)
from gardenops.router_helpers import (
    auth_context as _auth_context,
)
from gardenops.services.planting_planner import (
    _build_garden_profile,
    check_companions,
    get_planting_suggestions,
)

router = APIRouter()

PlannerGoal = Literal["shade", "color", "edible", "deer", "low_maintenance"]
PlannerGoalInput = PlannerGoal | Literal[""] | None


# ── Endpoints ──


@router.get("/planner/suggestions")
def planner_suggestions(
    request: Request,
    conn: DB,
    plot_id: str | None = Query(None),
    goal: PlannerGoal | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
    sunlit_plot_ids: str | None = Query(None),
) -> dict:
    """Get planting suggestions for empty plots."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    sunlit_ids = {
        item.strip() for item in (sunlit_plot_ids or "").split(",") if item.strip()
    } or None
    return get_planting_suggestions(
        conn,
        garden_id,
        target_plot_id=plot_id,
        goal=goal,
        limit=limit,
        sunlit_plot_ids=sunlit_ids,
    )


@router.get("/planner/companions")
def planner_companions(
    request: Request,
    conn: DB,
    plot_id: str = Query(...),
    plt_id: str = Query(...),
) -> dict:
    """Check companion/conflict for a candidate in a given plot."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    return check_companions(conn, garden_id, plot_id, plt_id)


@router.get("/planner/garden-profile")
def planner_garden_profile(
    request: Request,
    conn: DB,
) -> dict:
    """Garden analysis: bloom coverage, category distribution, gaps."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    return _build_garden_profile(conn, garden_id)


@router.get("/planner/goal")
def get_planner_goal(request: Request, conn: DB) -> dict:
    """Get saved planner goal for current user/garden."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    user_id = context.user_id
    key = f"planner_goal:{user_id}:{garden_id}"
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = %s",
        (key,),
    ).fetchone()
    return {"goal": row["value"] if row else None}


@router.put("/planner/goal")
def save_planner_goal(
    request: Request,
    conn: DB,
    goal: PlannerGoalInput = Body(None, embed=True),
) -> dict:
    """Save planner goal for current user/garden."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    user_id = context.user_id
    key = f"planner_goal:{user_id}:{garden_id}"
    normalized_goal: PlannerGoal | None = goal or None
    if normalized_goal:
        conn.execute(
            """INSERT INTO app_settings (key, value)
               VALUES (%s, %s)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, normalized_goal),
        )
    else:
        conn.execute("DELETE FROM app_settings WHERE key = %s", (key,))
    conn.commit()
    return {"status": "ok", "goal": normalized_goal}
