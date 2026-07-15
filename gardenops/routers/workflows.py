from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from gardenops.db import DB, DbConn, current_timestamp_ms
from gardenops.models import StrictBaseModel
from gardenops.router_helpers import (
    active_garden_id as _active_garden_id,
)
from gardenops.router_helpers import (
    auth_context as _auth_context,
)
from gardenops.security import has_write_access
from gardenops.services.workflow_service import (
    WORKFLOW_TEMPLATES,
    resolve_scope,
    validated_workflow_steps,
)

router = APIRouter()


class StartWorkflowBody(StrictBaseModel):
    workflow_id: str
    selected_steps: list[str] = Field(min_length=1, max_length=50)


@router.get("/workflows/available")
def list_available_workflows(request: Request) -> dict:
    """Return workflows whose month range includes the current month."""
    _auth_context(request)
    current_month = date.today().month
    workflows: list[dict] = []
    for wf_id, template in WORKFLOW_TEMPLATES.items():
        if current_month not in template["months"]:
            continue
        steps = [{"id": s["id"], "title": s["title"]} for s in template["steps"]]
        workflows.append(
            {
                "id": wf_id,
                "name": template["name"],
                "step_count": len(template["steps"]),
                "steps": steps,
            }
        )
    return {"workflows": workflows}


def _task_for_rule(
    db: DbConn,
    garden_id: int,
    rule_source: str,
) -> dict | None:
    row = db.execute(
        "SELECT id, public_id FROM garden_tasks WHERE garden_id = %s AND rule_source = %s",
        (garden_id, rule_source),
    ).fetchone()
    return dict(row) if row is not None else None


def _validate_target_links(
    db: DbConn,
    garden_id: int,
    plant_ids: list[str],
    plot_ids: list[str],
) -> tuple[list[str], list[str]]:
    unique_plants = list(dict.fromkeys(plant_ids))
    unique_plots = list(dict.fromkeys(plot_ids))
    if unique_plants:
        placeholders = ",".join(["%s"] * len(unique_plants))
        rows = db.execute(
            f"""
            SELECT DISTINCT plt_id
            FROM plant_ownership
            WHERE garden_id = %s AND plt_id IN ({placeholders})
            """,
            [garden_id, *unique_plants],
        ).fetchall()
        if {str(row["plt_id"]) for row in rows} != set(unique_plants):
            raise RuntimeError("Workflow resolved a plant outside the active garden")
    if unique_plots:
        placeholders = ",".join(["%s"] * len(unique_plots))
        rows = db.execute(
            f"""
            SELECT DISTINCT plot_id
            FROM plot_ownership
            WHERE garden_id = %s AND plot_id IN ({placeholders})
            """,
            [garden_id, *unique_plots],
        ).fetchall()
        if {str(row["plot_id"]) for row in rows} != set(unique_plots):
            raise RuntimeError("Workflow resolved a plot outside the active garden")
    return unique_plants, unique_plots


@router.post("/workflows/start")
def start_workflow(
    request: Request,
    db: DB,
    body: StartWorkflowBody,
) -> dict:
    """Start a seasonal workflow, creating tasks for selected steps."""
    context = _auth_context(request)
    if not has_write_access(context):
        raise HTTPException(status_code=403, detail="Forbidden: write access required")
    garden_id = _active_garden_id(context)

    template = WORKFLOW_TEMPLATES.get(body.workflow_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Unknown workflow")

    try:
        selected_steps = validated_workflow_steps(template, body.selected_steps)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    today = date.today()
    year = today.year
    now_ms = current_timestamp_ms()
    created = 0
    skipped = 0
    task_results: list[dict] = []

    lock_key = f"workflow:{garden_id}:{body.workflow_id}:{year}"
    db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_key,))

    resolved_steps: list[tuple[dict, list[str], list[str]]] = []
    try:
        for step in selected_steps:
            plant_ids, plot_ids = resolve_scope(db, garden_id, step["scope"])
            valid_plants, valid_plots = _validate_target_links(
                db,
                garden_id,
                plant_ids,
                plot_ids,
            )
            resolved_steps.append((step, valid_plants, valid_plots))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    for step_index, (step, plant_ids, plot_ids) in enumerate(resolved_steps):
        rule_source = f"workflow:{body.workflow_id}:{step['id']}:{year}"
        existing = _task_for_rule(db, garden_id, rule_source)
        if existing is not None:
            skipped += 1
            task_results.append(
                {
                    "step_id": step["id"],
                    "rule_source": rule_source,
                    "task_id": str(existing["public_id"]),
                }
            )
            continue

        due_on = (today + timedelta(days=3 * step_index)).isoformat()

        meta = json.dumps(
            {"description_no": step.get("description_no", "")},
        )
        wrow = db.execute(
            """
            INSERT INTO garden_tasks
                (garden_id, task_type, title, description, status,
                 severity, due_on, rule_source, metadata_json,
                 created_by_user_id, created_at_ms, updated_at_ms)
            VALUES (%s, %s, %s, %s, 'pending', 'normal', %s, %s, %s, %s, %s, %s)
            RETURNING id, public_id
            """,
            (
                garden_id,
                step["task_type"],
                step["title"],
                step.get("description", ""),
                due_on,
                rule_source,
                meta,
                context.user_id,
                now_ms,
                now_ms,
            ),
        ).fetchone()
        assert wrow is not None
        task_id = int(wrow["id"])

        for plt_id in plant_ids:
            db.execute(
                "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
                (task_id, plt_id),
            )
        for plot_id in plot_ids:
            db.execute(
                "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, %s)",
                (task_id, plot_id),
            )

        created += 1
        task_results.append(
            {
                "step_id": step["id"],
                "rule_source": rule_source,
                "task_id": str(wrow["public_id"]),
            }
        )

    db.commit()
    return {
        "status": "ok",
        "created": created,
        "skipped": skipped,
        "workflow_id": body.workflow_id,
        "year": year,
        "tasks": task_results,
    }
