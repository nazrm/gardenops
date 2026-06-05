from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request

from gardenops.db import DB, DbConn, current_timestamp_ms
from gardenops.models import StrictBaseModel
from gardenops.router_helpers import (
    active_garden_id as _active_garden_id,
)
from gardenops.router_helpers import (
    auth_context as _auth_context,
)
from gardenops.services.workflow_service import WORKFLOW_TEMPLATES, resolve_scope

router = APIRouter()


class StartWorkflowBody(StrictBaseModel):
    workflow_id: str
    selected_steps: list[str]


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


@router.post("/workflows/start")
def start_workflow(
    request: Request,
    db: DB,
    body: StartWorkflowBody,
) -> dict:
    """Start a seasonal workflow, creating tasks for selected steps."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)

    template = WORKFLOW_TEMPLATES.get(body.workflow_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Unknown workflow")

    selected = set(body.selected_steps)
    today = date.today()
    year = today.year
    now_ms = current_timestamp_ms()
    created = 0
    skipped = 0
    step_index = 0

    for step in template["steps"]:
        if step["id"] not in selected:
            continue

        rule_source = f"workflow:{body.workflow_id}:{step['id']}:{year}"
        if _rule_exists(db, garden_id, rule_source):
            skipped += 1
            step_index += 1
            continue

        due_on = (today + timedelta(days=3 * step_index)).isoformat()
        plant_ids, plot_ids = resolve_scope(db, garden_id, step["scope"])

        meta = json.dumps(
            {"description_no": step.get("description_no", "")},
        )
        wrow = db.execute(
            """
            INSERT INTO garden_tasks
                (garden_id, task_type, title, description, status,
                 severity, due_on, rule_source, metadata_json,
                 created_by_user_id, created_at_ms, updated_at_ms)
            VALUES (%s, %s, %s, %s, 'pending', 'normal', %s, %s, %s, %s, %s, %s) RETURNING id
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
        step_index += 1

    db.commit()
    return {
        "created": created,
        "skipped": skipped,
        "workflow_id": body.workflow_id,
    }
