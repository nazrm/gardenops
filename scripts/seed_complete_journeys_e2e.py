#!/usr/bin/env python3
"""Seed and inspect the disposable complete-journey database."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from psycopg import sql

from gardenops.db import close_pool, get_db, return_db
from gardenops.routers.map_objects import snapshot_map_objects
from gardenops.routers.shademap import (
    get_shademap_calibration,
    get_shademap_state,
    list_shademap_obstacles,
)
from gardenops.security import generate_passkey_user_handle, hash_password

ADMIN_USERNAME = os.environ.get(
    "GARDENOPS_COMPLETE_JOURNEYS_E2E_USERNAME", "complete_journeys_e2e_admin"
)
ADMIN_PASSWORD = os.environ.get(
    "GARDENOPS_COMPLETE_JOURNEYS_E2E_PASSWORD",
    "CompleteJourneysE2E!Passphrase2026",
)
EDITOR_LOGIN = ("complete_journeys_e2e_editor", "CompleteJourneysEditorE2E!Passphrase2026")
VIEWER_LOGIN = ("complete_journeys_e2e_viewer", "CompleteJourneysViewerE2E!Passphrase2026")
ONBOARDING_LOGIN = (
    "complete_journeys_e2e_onboarding",
    "CompleteJourneysOnboardingE2E!Passphrase2026",
)
MOBILE_ONBOARDING_LOGIN = (
    "complete_journeys_e2e_onboarding_mobile",
    "CompleteJourneysMobileOnboardingE2E!Passphrase2026",
)

PHASE_ONE_LARGE_GARDEN_SLUG = "complete-journeys-phase-one-large"
PHASE_ONE_LARGE_GARDEN_NAME = "Complete Journeys Large Garden"
PHASE_ONE_INDOOR_PLOT_ID = "COMPLETE-PHASE-ONE-INDOOR"
PHASE_ONE_INDOOR_PLANT_ID = "COMPLETE-PHASE-ONE-BASIL"
PHASE_ONE_INDOOR_PLANT_NAME = "Complete Phase One Indoor Basil"
PHASE_ONE_BETA_INDOOR_PLOT_ID = "COMPLETE-PHASE-ONE-BETA-INDOOR"
PHASE_ONE_BETA_INDOOR_ROOM_LABEL = "Beta greenhouse shelf"
PHASE_ONE_VIEWER_GARDENS = {
    "alpha": {
        "latin": "Mentha spicata",
        "plant_id": "COMPLETE-P1-VIEWER-ALPHA-PLANT",
        "plant_name": "Complete Phase One Viewer Alpha Mint",
        "plot_id": "COMPLETE-P1-VIEWER-ALPHA-PLOT",
    },
    "beta": {
        "latin": "Salvia officinalis",
        "plant_id": "COMPLETE-P1-VIEWER-BETA-PLANT",
        "plant_name": "Complete Phase One Viewer Beta Sage",
        "plot_id": "COMPLETE-P1-VIEWER-BETA-PLOT",
    },
}
PHASE_ONE_MAP_UNIT_ID = "mapunit_complete_phase_one_alpha_bench"
PHASE_ONE_MAP_UNIT_NAME = "Complete Phase One Basil Bench"
PHASE_ONE_SAVED_VIEW_LABEL = "Complete Phase One Basil View"
PHASE_ONE_MOBILE_SNAPSHOT_NAME = "Complete Phase One Mobile Action Snapshot"
PHASE_ONE_BROWSER_PLANT_ID = "PLT-001"
PHASE_ONE_BROWSER_SAVED_VIEW_LABEL = "Phase 1 Browser Plant View"
PHASE_ONE_QUICK_ACTION_NOTE = "Phase 1 mobile quick action"
PHASE_ONE_QUICK_ACTION_QUANTITY = 1.0
PHASE_ONE_QUICK_ACTION_UNIT = "kg"
PHASE_ONE_QUICK_ACTION_QUALITY = "good"
PHASE_ONE_DESKTOP_ONBOARDING_GARDEN_NAME = "Phase 1 Onboarding Garden"
PHASE_ONE_MOBILE_ONBOARDING_GARDEN_NAME = "Phase 1 Mobile Onboarding Garden"
PHASE_ONE_DESKTOP_ONBOARDING_GARDEN_SLUG = "complete-journeys-e2e-onboarding-s-garden"
PHASE_ONE_MOBILE_ONBOARDING_GARDEN_SLUG = "complete-journeys-e2e-onboarding-mobile-s-garden"
PHASE_ONE_ONBOARDING_DEFAULT_GARDEN_NAME = "Default Garden"
PHASE_ONE_ONBOARDING_DEFAULT_GARDEN_SLUG = "default"
PHASE_ONE_ONBOARDING_ADDRESS = "Phase 1 onboarding address"
PHASE_ONE_ONBOARDING_GRID_COLS = 12
PHASE_ONE_ONBOARDING_GRID_ROWS = 12
PHASE_ONE_ONBOARDING_HOUSE = {
    "col": 2,
    "grid_cols": PHASE_ONE_ONBOARDING_GRID_COLS,
    "grid_rows": PHASE_ONE_ONBOARDING_GRID_ROWS,
    "height": 3,
    "north_degrees": 0,
    "row": 2,
    "width": 3,
}
PHASE_ONE_ONBOARDING_LATITUDE = 59.91
PHASE_ONE_ONBOARDING_LONGITUDE = 10.75
PHASE_TWO_NOW_MS = 1_783_857_600_000
PHASE_TWO_DATE = "2026-07-12"
PHASE_TWO_MANUAL_DATE = "2026-07-18"
PHASE_TWO_SNOOZE_CORRECTION_DUE_DATE = "2026-07-16"
PHASE_TWO_SNOOZE_CORRECTION_DEFAULT_DATE = "2026-07-19"
PHASE_TWO_OFFLINE_SNOOZE_DATE = "2026-07-13"
PHASE_TWO_OFFLINE_RESCHEDULE_DATE = "2026-07-20"
PHASE_TWO_ALPHA_PLOT_ID = "COMPLETE-P2-ALPHA-BED"
PHASE_TWO_BETA_PLOT_ID = "COMPLETE-P2-BETA-BED"
PHASE_TWO_CALENDAR_PUBLIC_ID = "calevt_complete_p2_seeded"
PHASE_TWO_CALENDAR_EVENT_ON = "2026-07-13"
PHASE_TWO_CALENDAR_DESCRIPTION = "Escaped comma, semicolon; backslash \\ and line\nbreak."
PHASE_TWO_NOTIFICATION_PUBLIC_ID = "note_complete_p2_admin"
PHASE_TWO_DELIVERY_ELIGIBLE_NOTIFICATION_PUBLIC_ID = "note_complete_p2_delivery_eligible"
PHASE_TWO_DELIVERY_INELIGIBLE_NOTIFICATION_PUBLIC_ID = "note_complete_p2_delivery_ineligible"
PHASE_TWO_DELIVERY_ELIGIBLE_TITLE = "Phase 2 delivery eligible issue notice"
PHASE_TWO_DELIVERY_ELIGIBLE_BODY = "Phase 2 eligible issue notice after saved preferences."
PHASE_TWO_DELIVERY_INELIGIBLE_TITLE = "Phase 2 delivery ineligible issue notice"
PHASE_TWO_DELIVERY_INELIGIBLE_BODY = "Phase 2 low-severity issue notice after saved preferences."
PHASE_TWO_TASKS = {
    "bloom_desktop": "tsk_complete_p2_bloom_desktop",
    "fertilize_grouped": "tsk_complete_p2_fertilize_grouped",
    "snooze_correction": "tsk_complete_p2_snooze_correction",
    "prune_desktop": "tsk_complete_p2_prune_desktop",
    "batch_a": "tsk_complete_p2_batch_a",
    "batch_b": "tsk_complete_p2_batch_b",
    "bloom_mobile": "tsk_complete_p2_bloom_mobile",
    "fertilize_mobile": "tsk_complete_p2_fertilize_mobile",
    "editor_prune": "tsk_complete_p2_editor_prune",
    "editor_offline": "tsk_complete_p2_editor_offline",
    "viewer_read_only": "tsk_complete_p2_viewer_read_only",
    "plot_drawer": "tsk_complete_p2_plot_drawer",
    "stale_generated_water": "tsk_complete_p2_stale_generated_water",
    "stale_manual_water": "tsk_complete_p2_stale_manual_water",
    "rain_outdoor": "tsk_complete_p2_rain_outdoor",
    "rain_indoor": "tsk_complete_p2_rain_indoor",
    "rain_unplaced": "tsk_complete_p2_rain_unplaced",
}
PHASE_TWO_PLANTS = {
    "bloom_desktop": ("COMPLETE-P2-BLOOM-DESKTOP", "Phase 2 Desktop Astrantia", "H5"),
    "fertilize_a": ("COMPLETE-P2-FERT-A", "Phase 2 Fertilize A", "H5"),
    "fertilize_b": ("COMPLETE-P2-FERT-B", "Phase 2 Fertilize B", "H5"),
    "snooze_correction": ("COMPLETE-P2-SNOOZE-CORRECTION", "Phase 2 Correction Aster", "H5"),
    "prune_desktop": ("COMPLETE-P2-PRUNE-DESKTOP", "Phase 2 Desktop Rose", "H5"),
    "batch_a": ("COMPLETE-P2-BATCH-A", "Phase 2 Batch Thyme", "H5"),
    "batch_b": ("COMPLETE-P2-BATCH-B", "Phase 2 Batch Sage", "H5"),
    "bloom_mobile": ("COMPLETE-P2-BLOOM-MOBILE", "Phase 2 Mobile Campanula", "H5"),
    "fertilize_mobile": ("COMPLETE-P2-FERT-MOBILE", "Phase 2 Mobile Tomato", "H1"),
    "editor_prune": ("COMPLETE-P2-EDITOR-PRUNE", "Phase 2 Editor Currant", "H5"),
    "editor_offline": ("COMPLETE-P2-EDITOR-OFFLINE", "Phase 2 Offline Parsley", "H5"),
    "viewer_read_only": ("COMPLETE-P2-VIEWER", "Phase 2 Viewer Lavender", "H5"),
    "plot_drawer": ("COMPLETE-P2-PLOT-DRAWER", "Phase 2 Plot Drawer Chive", "H5"),
    "stale_generated_water": ("COMPLETE-P2-STALE-WATER", "Phase 2 Stale Water Mint", "H5"),
    "stale_manual_water": ("COMPLETE-P2-MANUAL-WATER", "Phase 2 Manual Water Mint", "H5"),
    "rain_outdoor": ("COMPLETE-P2-RAIN-OUTDOOR", "Phase 2 Rain Outdoor Basil", "H1"),
    "rain_indoor": ("COMPLETE-P2-RAIN-INDOOR", "Phase 2 Rain Indoor Basil", "H1"),
    "rain_unplaced": ("COMPLETE-P2-RAIN-UNPLACED", "Phase 2 Rain Unplaced Basil", "H1"),
}
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _frozen_attention_clock() -> dict[str, Any]:
    frozen_date = os.environ.get("GARDENOPS_ATTENTION_FROZEN_DATE", "").strip()
    frozen_now_ms = os.environ.get("GARDENOPS_ATTENTION_FROZEN_NOW_MS", "").strip()
    if not frozen_date or not frozen_now_ms:
        raise RuntimeError("Complete journey E2E requires a frozen attention clock")
    try:
        expected_date = date.fromisoformat(frozen_date)
        now_ms = int(frozen_now_ms)
        observed_date = datetime.fromtimestamp(now_ms / 1000, tz=UTC).date()
    except (OSError, OverflowError, ValueError) as exc:
        raise RuntimeError("Complete journey attention clock is invalid") from exc
    if now_ms <= 0 or observed_date != expected_date:
        raise RuntimeError("Complete journey attention clock date and timestamp must agree")
    return {"attention_date": frozen_date, "attention_now_ms": now_ms}


def _git_state() -> dict[str, Any]:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        capture_output=True,
        check=True,
        cwd=REPOSITORY_ROOT,
    ).stdout
    index_diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--binary", "--cached", "HEAD"],
        capture_output=True,
        check=True,
        cwd=REPOSITORY_ROOT,
    ).stdout
    worktree_diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--binary", "HEAD"],
        capture_output=True,
        check=True,
        cwd=REPOSITORY_ROOT,
    ).stdout
    untracked_paths = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        check=True,
        cwd=REPOSITORY_ROOT,
    ).stdout.split(b"\0")
    fingerprint = sha256()
    for value in (status, index_diff, worktree_diff):
        fingerprint.update(value)
        fingerprint.update(b"\0")
    for relative_path in sorted(path for path in untracked_paths if path):
        candidate = REPOSITORY_ROOT / os.fsdecode(relative_path)
        fingerprint.update(relative_path)
        fingerprint.update(b"\0")
        try:
            candidate_stat = candidate.lstat()
            if stat.S_ISREG(candidate_stat.st_mode):
                kind = b"file"
                content = candidate.read_bytes()
            elif stat.S_ISLNK(candidate_stat.st_mode):
                kind = b"symlink"
                content = os.fsencode(os.readlink(candidate))
            else:
                kind = b"other"
                content = b""
        except OSError:
            kind = b"missing"
            content = b""
        fingerprint.update(kind)
        fingerprint.update(b"\0")
        fingerprint.update(content)
        fingerprint.update(b"\0")
    return {
        "dirty": bool(status.strip()),
        "sha": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
            cwd=REPOSITORY_ROOT,
        ).stdout.strip(),
        "worktree_fingerprint": fingerprint.hexdigest(),
    }


def _require_child_environment() -> None:
    if os.environ.get("GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD") != "1":
        raise RuntimeError("Complete journey E2E must run as the disposable runner child")
    if os.environ.get("APP_ENV") != "test":
        raise RuntimeError("Complete journey E2E requires APP_ENV=test")
    if os.environ.get("AUTH_REQUIRED") != "true" or os.environ.get("AUTH_MODE") != "session":
        raise RuntimeError("Complete journey E2E requires session authentication")
    if os.environ.get("GARDENOPS_COMPLETE_JOURNEYS_E2E_ALLOW_TRUNCATE") != "1":
        raise RuntimeError("Complete journey E2E truncation guard is required")
    required = (
        "DATABASE_URL",
        "GARDENOPS_DISPOSABLE_POSTGRES_URL",
        "GARDENOPS_DISPOSABLE_POSTGRES_MARKER",
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER",
    )
    if any(not os.environ.get(name) for name in required):
        raise RuntimeError("Complete journey E2E requires runner-issued disposable evidence")
    if os.environ["DATABASE_URL"] != os.environ["GARDENOPS_DISPOSABLE_POSTGRES_URL"]:
        raise RuntimeError("Complete journey DATABASE_URL must match the runner-issued URL")
    system_identifier = os.environ["GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER"]
    marker = os.environ["GARDENOPS_DISPOSABLE_POSTGRES_MARKER"]
    if not system_identifier.isdecimal() or not marker.startswith(f"{system_identifier}."):
        raise RuntimeError("Complete journey disposable marker is not bound to the runner cluster")
    _frozen_attention_clock()


def _configure_reused_seed_guard() -> None:
    os.environ["GARDENOPS_ALLOW_DESTRUCTIVE_E2E"] = "1"
    os.environ["GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_ALLOW_TRUNCATE"] = "1"
    os.environ["GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_USERNAME"] = ADMIN_USERNAME
    os.environ["GARDENOPS_OPTIMIZATION_JOURNEYS_E2E_PASSWORD"] = ADMIN_PASSWORD


def _insert_user(
    conn,
    *,
    username: str,
    password: str,
    role: str,
    subscription_tier: str = "home",
) -> int:
    row = conn.execute(
        """
        INSERT INTO auth_users (
            username, password_hash, password_auth_disabled, passkey_user_handle,
            role, is_active, must_change_password, subscription_tier
        )
        VALUES (%s, %s, 0, %s, %s, 1, 0, %s)
        RETURNING id
        """,
        (
            username,
            hash_password(password),
            generate_passkey_user_handle(),
            role,
            subscription_tier,
        ),
    ).fetchone()
    if not row:
        raise RuntimeError(f"Complete journey E2E failed to create {role} user")
    return int(row["id"])


def _seed_viewer_owned_garden_content(
    conn,
    *,
    garden_id: int,
    key: str,
    viewer_id: int,
) -> None:
    spec = PHASE_ONE_VIEWER_GARDENS[key]
    conn.execute(
        """
        INSERT INTO plots (
            plot_id, garden_id, zone_code, zone_name, plot_number,
            grid_row, grid_col, sub_zone, notes, color
        )
        VALUES (%s, %s, 'V', 'Viewer verification', 1, 9, 10, %s,
                'Disposable Phase 1 viewer-owned fixture', '#5f8c74')
        """,
        (spec["plot_id"], garden_id, spec["plant_name"]),
    )
    conn.execute(
        "INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id) VALUES (%s, %s, %s)",
        (spec["plot_id"], viewer_id, garden_id),
    )
    conn.execute(
        """
        INSERT INTO plants (
            plt_id, name, latin, category, bloom_month, color, hardiness,
            height_cm, light, link, care_watering, care_soil, care_planting,
            care_maintenance, care_notes, year_planted, seen_growing, seen_growing_date
        )
        VALUES (%s, %s, %s, 'herb', 'July', '#75a16a', 'H5', 45,
                'part sun', '', 'keep evenly moist', 'moist fertile soil',
                'contain vigorous roots', 'harvest regularly',
                'Disposable Phase 1 viewer-owned fixture', '2026', 1, '2026-07-01')
        """,
        (spec["plant_id"], spec["plant_name"], spec["latin"]),
    )
    conn.execute(
        "INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id) VALUES (%s, %s, %s)",
        (spec["plant_id"], viewer_id, garden_id),
    )
    conn.execute(
        """
        INSERT INTO plot_plants (
            plot_id, plt_id, quantity, seen_growing, seen_growing_date, room_label
        )
        VALUES (%s, %s, 1, 1, '2026-07-01', '')
        """,
        (spec["plot_id"], spec["plant_id"]),
    )


def _add_role_fixtures(
    conn,
    *,
    garden_ids: list[int],
    viewer_garden_ids: dict[str, int],
) -> None:
    editor_id = _insert_user(
        conn,
        username=EDITOR_LOGIN[0],
        password=EDITOR_LOGIN[1],
        role="editor",
        subscription_tier="pro",
    )
    viewer_id = _insert_user(
        conn,
        username=VIEWER_LOGIN[0],
        password=VIEWER_LOGIN[1],
        role="viewer",
        subscription_tier="pro",
    )
    for garden_id in garden_ids:
        for user_id, role in ((editor_id, "editor"), (viewer_id, "viewer")):
            conn.execute(
                "INSERT INTO garden_memberships (garden_id, user_id, role) VALUES (%s, %s, %s)",
                (garden_id, user_id, role),
            )
    if set(viewer_garden_ids) != set(PHASE_ONE_VIEWER_GARDENS):
        raise RuntimeError("Complete journey viewer garden fixtures are incomplete")
    for key, garden_id in viewer_garden_ids.items():
        _seed_viewer_owned_garden_content(
            conn,
            garden_id=garden_id,
            key=key,
            viewer_id=viewer_id,
        )


def _seed_phase_one_fixtures(conn, optimization_seed: Any) -> None:
    """Add deterministic Phase 1 records after the reusable base seed."""
    admin = conn.execute(
        "SELECT id FROM auth_users WHERE username = %s", (ADMIN_USERNAME,)
    ).fetchone()
    alpha = conn.execute(
        "SELECT id FROM gardens WHERE slug = %s", (optimization_seed.GARDEN_A_SLUG,)
    ).fetchone()
    beta = conn.execute(
        "SELECT id FROM gardens WHERE slug = %s", (optimization_seed.GARDEN_B_SLUG,)
    ).fetchone()
    if not admin or not alpha or not beta:
        raise RuntimeError("Complete journey Phase 1 base fixtures are missing")
    admin_id = int(admin["id"])
    alpha_id = int(alpha["id"])
    beta_id = int(beta["id"])

    _insert_user(conn, username=ONBOARDING_LOGIN[0], password=ONBOARDING_LOGIN[1], role="editor")
    _insert_user(
        conn,
        username=MOBILE_ONBOARDING_LOGIN[0],
        password=MOBILE_ONBOARDING_LOGIN[1],
        role="editor",
    )
    large = conn.execute(
        """
        INSERT INTO gardens (
            slug, name, grid_rows, grid_cols, latitude, longitude, address,
            onboarding_complete, owner_user_id
        )
        VALUES (%s, %s, 48, 64, 59.9139, 10.7522, 'Disposable Phase 1 fixture', 1, %s)
        RETURNING id
        """,
        (PHASE_ONE_LARGE_GARDEN_SLUG, PHASE_ONE_LARGE_GARDEN_NAME, admin_id),
    ).fetchone()
    if not large:
        raise RuntimeError("Failed to create Complete journey Phase 1 large garden")
    large_id = int(large["id"])
    conn.execute(
        "INSERT INTO garden_memberships (garden_id, user_id, role) VALUES (%s, %s, 'admin')",
        (large_id, admin_id),
    )
    conn.execute(
        """
        INSERT INTO layout_state (
            garden_id, house_row, house_col, house_width, house_height,
            north_degrees, grid_rows, grid_cols
        )
        VALUES (%s, 2, 2, 5, 4, 18, 48, 64)
        """,
        (large_id,),
    )
    conn.execute(
        """
        INSERT INTO plots (
            plot_id, garden_id, zone_code, zone_name, plot_number,
            grid_row, grid_col, sub_zone, notes, color
        )
        VALUES (%s, %s, 'I', 'Indoor growing', 1, NULL, NULL, 'Greenhouse shelf',
                'Disposable Phase 1 indoor fixture', '#6f91a6')
        """,
        (PHASE_ONE_INDOOR_PLOT_ID, alpha_id),
    )
    conn.execute(
        "INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id) VALUES (%s, %s, %s)",
        (PHASE_ONE_INDOOR_PLOT_ID, admin_id, alpha_id),
    )
    conn.execute(
        """
        INSERT INTO plots (
            plot_id, garden_id, zone_code, zone_name, plot_number,
            grid_row, grid_col, sub_zone, notes, color
        )
        VALUES (%s, %s, 'I', 'Indoor growing', 1, NULL, NULL, %s,
                'Disposable Phase 1 Beta indoor fixture', '#8796ad')
        """,
        (PHASE_ONE_BETA_INDOOR_PLOT_ID, beta_id, PHASE_ONE_BETA_INDOOR_ROOM_LABEL),
    )
    conn.execute(
        "INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id) VALUES (%s, %s, %s)",
        (PHASE_ONE_BETA_INDOOR_PLOT_ID, admin_id, beta_id),
    )
    conn.execute(
        """
        INSERT INTO plot_plants (
            plot_id, plt_id, quantity, seen_growing, seen_growing_date, room_label
        )
        VALUES (%s, %s, 1, 1, '2026-07-01', %s)
        """,
        (
            PHASE_ONE_BETA_INDOOR_PLOT_ID,
            optimization_seed._GARDEN_SPECS[1]["plant_id"],
            PHASE_ONE_BETA_INDOOR_ROOM_LABEL,
        ),
    )
    conn.execute(
        """
        INSERT INTO plants (
            plt_id, name, latin, category, bloom_month, color, hardiness,
            height_cm, light, link, care_watering, care_soil, care_planting,
            care_maintenance, care_notes, year_planted, seen_growing, seen_growing_date
        )
        VALUES (%s, %s, 'Ocimum basilicum', 'herb', 'July', '#7aa65d', 'H5', 35,
                'bright light', '', 'keep evenly moist', 'light potting mix',
                'pinch after six leaves', 'harvest often', 'Disposable Phase 1 indoor fixture',
                '2026', 1, '2026-07-01')
        """,
        (PHASE_ONE_INDOOR_PLANT_ID, PHASE_ONE_INDOOR_PLANT_NAME),
    )
    conn.execute(
        "INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id) VALUES (%s, %s, %s)",
        (PHASE_ONE_INDOOR_PLANT_ID, admin_id, alpha_id),
    )
    conn.execute(
        """
        INSERT INTO plot_plants (
            plot_id, plt_id, quantity, seen_growing, seen_growing_date, room_label
        )
        VALUES (%s, %s, 3, 1, '2026-07-01', 'Greenhouse shelf')
        """,
        (PHASE_ONE_INDOOR_PLOT_ID, PHASE_ONE_INDOOR_PLANT_ID),
    )
    map_object = conn.execute(
        "SELECT id FROM garden_map_objects WHERE public_id = %s AND garden_id = %s",
        (optimization_seed._GARDEN_SPECS[0]["object_id"], alpha_id),
    ).fetchone()
    if not map_object:
        raise RuntimeError("Complete journey Phase 1 Alpha map object is missing")
    now_ms = 1_783_483_200_000
    conn.execute(
        """
        INSERT INTO garden_map_object_units (
            public_id, garden_id, map_object_id, unit_type, name, shape_type,
            geometry_json, style_json, sort_order, created_at_ms, updated_at_ms
        )
        VALUES (%s, %s, %s, 'planter', %s, 'rectangle', %s, %s, 1, %s, %s)
        """,
        (
            PHASE_ONE_MAP_UNIT_ID,
            alpha_id,
            int(map_object["id"]),
            PHASE_ONE_MAP_UNIT_NAME,
            json.dumps({"x": 1, "y": 1, "width": 2, "height": 1}, sort_keys=True),
            json.dumps({"color": "#b7c98a"}, sort_keys=True),
            now_ms,
            now_ms,
        ),
    )
    conn.execute(
        """
        INSERT INTO user_saved_views (
            user_id, garden_id, view_type, label, filter_json, is_preset, sort_order,
            created_at_ms, updated_at_ms
        )
        VALUES (%s, %s, 'plants', %s, %s, 0, 1, %s, %s)
        """,
        (
            admin_id,
            alpha_id,
            PHASE_ONE_SAVED_VIEW_LABEL,
            json.dumps({"q": "Complete Phase One"}, sort_keys=True),
            now_ms,
            now_ms,
        ),
    )


def _seed_phase_two_task(
    conn,
    *,
    public_id: str,
    garden_id: int,
    actor_user_id: int,
    task_type: str,
    title: str,
    plant_ids: tuple[str, ...],
    plot_ids: tuple[str, ...],
    due_on: str = PHASE_TWO_DATE,
    rule_source: str = "",
    window_start_on: str | None = None,
    window_end_on: str | None = None,
) -> None:
    row = conn.execute(
        """
        INSERT INTO garden_tasks (
            public_id, garden_id, task_type, title, description, status, severity,
            due_on, snoozed_until, rule_source, metadata_json, created_by_user_id,
            completed_by_user_id, completed_at_ms, created_at_ms, updated_at_ms,
            window_start_on, window_end_on, window_kind
        )
        VALUES (
            %s, %s, %s, %s, %s, 'pending', 'normal',
            %s, NULL, %s, %s, %s,
            NULL, NULL, %s, %s, %s, %s,
            %s
        )
        RETURNING id
        """,
        (
            public_id,
            garden_id,
            task_type,
            title,
            f"Deterministic Phase 2 task fixture for {title}.",
            due_on,
            rule_source,
            json.dumps({"fixture": "complete_journeys_phase_2"}, sort_keys=True),
            actor_user_id,
            PHASE_TWO_NOW_MS,
            PHASE_TWO_NOW_MS,
            window_start_on,
            window_end_on,
            "recommended" if window_start_on is not None else None,
        ),
    ).fetchone()
    if not row:
        raise RuntimeError(f"Failed to create Complete journey Phase 2 task {public_id}")
    task_id = int(row["id"])
    for plant_id in plant_ids:
        conn.execute(
            "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
            (task_id, plant_id),
        )
    for plot_id in plot_ids:
        conn.execute(
            "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, %s)",
            (task_id, plot_id),
        )


def _reset_phase_two_weather_cache(
    conn,
    *,
    alpha_id: int,
    beta_id: int,
) -> dict[str, Any]:
    """Restore deterministic forecasts after earlier journeys invalidate the cache."""
    alpha_forecast = {
        "daily": {
            "time": [
                "2026-07-12",
                "2026-07-13",
                "2026-07-14",
                "2026-07-15",
                "2026-07-16",
                "2026-07-17",
                "2026-07-18",
            ],
            "temperature_2m_min": [-3.0, 12.0, 13.0, 14.0, 13.0, 12.0, 11.0],
            "temperature_2m_max": [20.0, 31.0, 32.0, 33.0, 24.0, 23.0, 22.0],
            "precipitation_sum": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "precipitation_probability_max": [0, 0, 0, 0, 0, 0, 0],
            "wind_speed_10m_max": [3.0, 3.0, 4.0, 4.0, 3.0, 2.0, 2.0],
        }
    }
    beta_forecast = {
        "daily": {
            "time": [
                "2026-07-12",
                "2026-07-13",
                "2026-07-14",
                "2026-07-15",
                "2026-07-16",
                "2026-07-17",
                "2026-07-18",
            ],
            "temperature_2m_min": [11.0, 12.0, 12.0, 13.0, 13.0, 12.0, 11.0],
            "temperature_2m_max": [18.0, 19.0, 20.0, 20.0, 19.0, 18.0, 18.0],
            "precipitation_sum": [6.0, 5.0, 5.0, 0.0, 0.0, 0.0, 0.0],
            "precipitation_probability_max": [90, 85, 80, 10, 10, 10, 10],
            "wind_speed_10m_max": [5.0, 5.0, 4.0, 3.0, 3.0, 2.0, 2.0],
        }
    }
    garden_ids = sorted([alpha_id, beta_id])
    for garden_id, forecast, latitude, longitude in (
        (alpha_id, alpha_forecast, 59.9139, 10.7522),
        (beta_id, beta_forecast, 59.9239, 10.7622),
    ):
        rows = conn.execute(
            "SELECT id FROM weather_cache WHERE garden_id = %s ORDER BY id",
            (garden_id,),
        ).fetchall()
        forecast_json = json.dumps(forecast, sort_keys=True)
        if len(rows) == 1:
            conn.execute(
                """
                UPDATE weather_cache
                SET fetched_at_ms = %s, forecast_json = %s, latitude = %s, longitude = %s
                WHERE id = %s
                """,
                (PHASE_TWO_NOW_MS, forecast_json, latitude, longitude, int(rows[0]["id"])),
            )
        else:
            conn.execute("DELETE FROM weather_cache WHERE garden_id = %s", (garden_id,))
            conn.execute(
                """
                INSERT INTO weather_cache (
                    garden_id, fetched_at_ms, forecast_json, latitude, longitude
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (garden_id, PHASE_TWO_NOW_MS, forecast_json, latitude, longitude),
            )
    return {
        "fetched_at_ms": PHASE_TWO_NOW_MS,
        "garden_ids": garden_ids,
        "weather_cache_rows": 2,
    }


def _seed_phase_two_fixtures(conn, optimization_seed: Any) -> None:
    """Add deterministic daily-attention fixtures after role memberships exist."""
    users = {
        str(row["username"]): int(row["id"])
        for row in conn.execute(
            "SELECT id, username FROM auth_users WHERE username = ANY(%s)",
            ([ADMIN_USERNAME, EDITOR_LOGIN[0], VIEWER_LOGIN[0]],),
        ).fetchall()
    }
    gardens = {
        str(row["slug"]): int(row["id"])
        for row in conn.execute(
            "SELECT id, slug FROM gardens WHERE slug = ANY(%s)",
            ([optimization_seed.GARDEN_A_SLUG, optimization_seed.GARDEN_B_SLUG],),
        ).fetchall()
    }
    if len(users) != 3 or len(gardens) != 2:
        raise RuntimeError("Complete journey Phase 2 users or gardens are missing")
    admin_id = users[ADMIN_USERNAME]
    alpha_id = gardens[optimization_seed.GARDEN_A_SLUG]
    beta_id = gardens[optimization_seed.GARDEN_B_SLUG]

    for plot_id, garden_id, zone_name in (
        (PHASE_TWO_ALPHA_PLOT_ID, alpha_id, "Phase 2 daily work"),
        (PHASE_TWO_BETA_PLOT_ID, beta_id, "Phase 2 rain work"),
    ):
        conn.execute(
            """
            INSERT INTO plots (
                plot_id, garden_id, zone_code, zone_name, plot_number,
                grid_row, grid_col, sub_zone, notes, color
            )
            VALUES (%s, %s, 'P2', %s, 1, 11, 11, '',
                    'Disposable Phase 2 attention fixture', '#5f8c74')
            """,
            (plot_id, garden_id, zone_name),
        )
        conn.execute(
            "INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id) VALUES (%s, %s, %s)",
            (plot_id, admin_id, garden_id),
        )

    alpha_plant_keys = {
        "bloom_desktop",
        "fertilize_a",
        "fertilize_b",
        "snooze_correction",
        "prune_desktop",
        "batch_a",
        "batch_b",
        "bloom_mobile",
        "fertilize_mobile",
        "editor_prune",
        "editor_offline",
        "viewer_read_only",
        "plot_drawer",
        "stale_generated_water",
        "stale_manual_water",
    }
    for key, (plant_id, name, hardiness) in PHASE_TWO_PLANTS.items():
        garden_id = alpha_id if key in alpha_plant_keys else beta_id
        conn.execute(
            """
            INSERT INTO plants (
                plt_id, name, latin, category, bloom_month, color, hardiness,
                height_cm, light, link, care_watering, care_soil, care_planting,
                care_maintenance, care_notes, year_planted, seen_growing,
                seen_growing_date
            )
            VALUES (%s, %s, '', 'perennial', 'July', '#6f936f', %s, 60,
                    'part sun', '', 'water regularly', '', '', '',
                    'Disposable Phase 2 attention fixture', '2026', 1, '2026-07-01')
            """,
            (plant_id, name, hardiness),
        )
        conn.execute(
            "INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id) VALUES (%s, %s, %s)",
            (plant_id, admin_id, garden_id),
        )

    for key in sorted(alpha_plant_keys):
        plant_id = PHASE_TWO_PLANTS[key][0]
        conn.execute(
            """
            INSERT INTO plot_plants (
                plot_id, plt_id, quantity, seen_growing, seen_growing_date, room_label
            )
            VALUES (%s, %s, 1, 1, '2026-07-01', '')
            """,
            (PHASE_TWO_ALPHA_PLOT_ID, plant_id),
        )
    conn.execute(
        """
        INSERT INTO plot_plants (
            plot_id, plt_id, quantity, seen_growing, seen_growing_date, room_label
        )
        VALUES (%s, %s, 1, 1, '2026-07-01', '')
        """,
        (PHASE_TWO_BETA_PLOT_ID, PHASE_TWO_PLANTS["rain_outdoor"][0]),
    )
    conn.execute(
        """
        INSERT INTO plot_plants (
            plot_id, plt_id, quantity, seen_growing, seen_growing_date, room_label
        )
        VALUES (%s, %s, 1, 1, '2026-07-01', %s)
        """,
        (
            PHASE_ONE_BETA_INDOOR_PLOT_ID,
            PHASE_TWO_PLANTS["rain_indoor"][0],
            PHASE_ONE_BETA_INDOOR_ROOM_LABEL,
        ),
    )

    alpha_tasks = (
        (
            "bloom_desktop",
            "observe_bloom",
            "Observe bloom: Phase 2 Desktop Astrantia",
            ("bloom_desktop",),
        ),
        (
            "fertilize_grouped",
            "fertilize",
            "Fertilize Phase 2 pair",
            ("fertilize_a", "fertilize_b"),
        ),
        ("prune_desktop", "prune", "Prune Phase 2 Desktop Rose", ("prune_desktop",)),
        ("batch_a", "weed", "Weed Phase 2 Batch Thyme", ("batch_a",)),
        ("batch_b", "weed", "Weed Phase 2 Batch Sage", ("batch_b",)),
        (
            "bloom_mobile",
            "observe_bloom",
            "Observe bloom: Phase 2 Mobile Campanula",
            ("bloom_mobile",),
        ),
        ("fertilize_mobile", "fertilize", "Fertilize Phase 2 Mobile Tomato", ("fertilize_mobile",)),
        ("editor_prune", "prune", "Prune Phase 2 Editor Currant", ("editor_prune",)),
        ("editor_offline", "deadhead", "Deadhead Phase 2 Offline Parsley", ("editor_offline",)),
        ("viewer_read_only", "prune", "Prune Phase 2 Viewer Lavender", ("viewer_read_only",)),
        ("plot_drawer", "deadhead", "Deadhead Phase 2 Plot Drawer Chive", ("plot_drawer",)),
    )
    for task_key, task_type, title, plant_keys in alpha_tasks:
        window_end = "2026-07-14" if task_type == "prune" else None
        _seed_phase_two_task(
            conn,
            public_id=PHASE_TWO_TASKS[task_key],
            garden_id=alpha_id,
            actor_user_id=admin_id,
            task_type=task_type,
            title=title,
            plant_ids=tuple(PHASE_TWO_PLANTS[key][0] for key in plant_keys),
            plot_ids=(PHASE_TWO_ALPHA_PLOT_ID,),
            window_start_on=PHASE_TWO_DATE if window_end else None,
            window_end_on=window_end,
        )

    # This task is outside the maintenance upcoming window. It isolates the immediate
    # one-week snooze plus transient Change date correction path from later offline actions.
    _seed_phase_two_task(
        conn,
        public_id=PHASE_TWO_TASKS["snooze_correction"],
        garden_id=alpha_id,
        actor_user_id=admin_id,
        task_type="fertilize",
        title="Fertilize Phase 2 correction aster",
        plant_ids=(PHASE_TWO_PLANTS["snooze_correction"][0],),
        plot_ids=(PHASE_TWO_ALPHA_PLOT_ID,),
        due_on=PHASE_TWO_SNOOZE_CORRECTION_DUE_DATE,
    )

    _seed_phase_two_task(
        conn,
        public_id=PHASE_TWO_TASKS["stale_generated_water"],
        garden_id=alpha_id,
        actor_user_id=admin_id,
        task_type="water",
        title="Water Phase 2 stale generated mint",
        plant_ids=(PHASE_TWO_PLANTS["stale_generated_water"][0],),
        plot_ids=(PHASE_TWO_ALPHA_PLOT_ID,),
        due_on="2026-06-20",
        rule_source="water:COMPLETE-P2-STALE-WATER:2026-06-20",
    )
    _seed_phase_two_task(
        conn,
        public_id=PHASE_TWO_TASKS["stale_manual_water"],
        garden_id=alpha_id,
        actor_user_id=admin_id,
        task_type="water",
        title="Water Phase 2 manual overdue mint",
        plant_ids=(PHASE_TWO_PLANTS["stale_manual_water"][0],),
        plot_ids=(PHASE_TWO_ALPHA_PLOT_ID,),
        due_on="2026-06-20",
    )
    for task_key, plant_key, plot_ids in (
        ("rain_outdoor", "rain_outdoor", (PHASE_TWO_BETA_PLOT_ID,)),
        ("rain_indoor", "rain_indoor", (PHASE_ONE_BETA_INDOOR_PLOT_ID,)),
        ("rain_unplaced", "rain_unplaced", ()),
    ):
        plant_id, name, _hardiness = PHASE_TWO_PLANTS[plant_key]
        _seed_phase_two_task(
            conn,
            public_id=PHASE_TWO_TASKS[task_key],
            garden_id=beta_id,
            actor_user_id=admin_id,
            task_type="water",
            title=f"Water {name}",
            plant_ids=(plant_id,),
            plot_ids=plot_ids,
            rule_source=f"water:{plant_id}:{PHASE_TWO_DATE}",
        )

    event = conn.execute(
        """
        INSERT INTO garden_calendar_events (
            public_id, garden_id, title, description, event_on,
            created_by_user_id, updated_by_user_id, created_at_ms, updated_at_ms
        )
        VALUES (%s, %s, 'Phase 2 seeded calendar event', %s, %s,
                %s, %s, %s, %s)
        RETURNING id
        """,
        (
            PHASE_TWO_CALENDAR_PUBLIC_ID,
            alpha_id,
            PHASE_TWO_CALENDAR_DESCRIPTION,
            PHASE_TWO_CALENDAR_EVENT_ON,
            admin_id,
            admin_id,
            PHASE_TWO_NOW_MS,
            PHASE_TWO_NOW_MS,
        ),
    ).fetchone()
    if not event:
        raise RuntimeError("Failed to create Complete journey Phase 2 calendar event")
    conn.execute(
        "INSERT INTO garden_calendar_event_plants (event_id, plt_id) VALUES (%s, %s)",
        (int(event["id"]), PHASE_TWO_PLANTS["bloom_desktop"][0]),
    )
    conn.execute(
        "INSERT INTO garden_calendar_event_plots (event_id, plot_id) VALUES (%s, %s)",
        (int(event["id"]), PHASE_TWO_ALPHA_PLOT_ID),
    )

    for username, user_id in users.items():
        conn.execute(
            """
            INSERT INTO user_notification_preferences (
                user_id, in_app_enabled, email_enabled, email_address, digest_frequency,
                quiet_hours_json, task_due_enabled, task_overdue_enabled, rules_json,
                created_at_ms, updated_at_ms
            )
            VALUES (%s, 1, %s, %s, 'daily', %s, 1, 1, '{}', %s, %s)
            """,
            (
                user_id,
                0,
                "complete-phase-2@example.invalid" if username == ADMIN_USERNAME else "",
                json.dumps({"start": "22:15", "end": "07:45"}, sort_keys=True),
                PHASE_TWO_NOW_MS,
                PHASE_TWO_NOW_MS,
            ),
        )
        conn.execute(
            """
            INSERT INTO user_attention_preferences (
                user_id, preset, rules_json, quiet_hours_json, show_no_action_history,
                metadata_json, created_at_ms, updated_at_ms
            )
            VALUES (%s, 'balanced', '{}', %s, 1, '{}', %s, %s)
            """,
            (
                user_id,
                json.dumps({"start": "22:15", "end": "07:45"}, sort_keys=True),
                PHASE_TWO_NOW_MS,
                PHASE_TWO_NOW_MS,
            ),
        )

    for public_id, garden_id, user_id, notification_type, title, body in (
        (
            PHASE_TWO_NOTIFICATION_PUBLIC_ID,
            alpha_id,
            admin_id,
            "issue_created",
            "Phase 2 attention conflict",
            "Alpha phase 2 scoped notification.",
        ),
        (
            "note_complete_p2_beta_conflict",
            beta_id,
            admin_id,
            "issue_created",
            "Phase 2 attention conflict",
            "Beta phase 2 scoped notification.",
        ),
        (
            "note_complete_p2_editor",
            alpha_id,
            users[EDITOR_LOGIN[0]],
            "system",
            "Phase 2 editor reminder",
            "Editor phase 2 scoped notification.",
        ),
    ):
        conn.execute(
            """
            INSERT INTO notification_events (
                public_id, garden_id, user_id, notification_type, notification_subtype,
                severity, title, body, target_type, target_id, metadata_json,
                dismissed, created_at_ms, expires_at_ms
            )
            VALUES (%s, %s, %s, %s, NULL, 'normal', %s,
                    %s, 'system', %s,
                    '{}', 0, %s, %s)
            """,
            (
                public_id,
                garden_id,
                user_id,
                notification_type,
                title,
                body,
                public_id,
                PHASE_TWO_NOW_MS,
                PHASE_TWO_NOW_MS + 604_800_000,
            ),
        )

    _reset_phase_two_weather_cache(conn, alpha_id=alpha_id, beta_id=beta_id)


def _phase_one_fixture_state(conn, optimization_seed: Any) -> dict[str, Any]:
    alpha = conn.execute(
        "SELECT id FROM gardens WHERE slug = %s", (optimization_seed.GARDEN_A_SLUG,)
    ).fetchone()
    beta = conn.execute(
        "SELECT id FROM gardens WHERE slug = %s", (optimization_seed.GARDEN_B_SLUG,)
    ).fetchone()
    large = conn.execute(
        "SELECT id, name FROM gardens WHERE slug = %s", (PHASE_ONE_LARGE_GARDEN_SLUG,)
    ).fetchone()
    snapshot = conn.execute(
        "SELECT COUNT(*) AS count FROM layout_snapshots WHERE name = %s",
        (PHASE_ONE_MOBILE_SNAPSHOT_NAME,),
    ).fetchone()
    if not alpha or not beta or not large:
        raise RuntimeError("Complete journey Phase 1 base gardens are missing")
    alpha_id = int(alpha["id"])

    def viewer_payload(key: str, garden_id: int) -> dict[str, Any]:
        spec = PHASE_ONE_VIEWER_GARDENS[key]
        row = conn.execute(
            """
            SELECT plot_value.plot_id, plant_value.plt_id, plant_value.name
            FROM plots plot_value
            JOIN plot_ownership plot_owner
              ON plot_owner.plot_id = plot_value.plot_id
             AND plot_owner.garden_id = plot_value.garden_id
            JOIN auth_users owner ON owner.id = plot_owner.owner_user_id
            JOIN plot_plants assignment ON assignment.plot_id = plot_value.plot_id
            JOIN plants plant_value ON plant_value.plt_id = assignment.plt_id
            JOIN plant_ownership plant_owner
              ON plant_owner.plt_id = plant_value.plt_id
             AND plant_owner.garden_id = plot_value.garden_id
             AND plant_owner.owner_user_id = owner.id
            WHERE plot_value.garden_id = %s
              AND plot_value.plot_id = %s
              AND plant_value.plt_id = %s
              AND owner.username = %s
            """,
            (garden_id, spec["plot_id"], spec["plant_id"], VIEWER_LOGIN[0]),
        ).fetchone()
        if not row:
            raise RuntimeError(f"Complete journey viewer {key} content is missing")
        return {
            "garden_id": garden_id,
            "plant_id": str(row["plt_id"]),
            "plant_name": str(row["name"]),
            "plot_id": str(row["plot_id"]),
            "owner_username": VIEWER_LOGIN[0],
        }

    return {
        "indoor": {
            "garden_id": alpha_id,
            "plant_id": PHASE_ONE_INDOOR_PLANT_ID,
            "plant_name": PHASE_ONE_INDOOR_PLANT_NAME,
            "plot_id": PHASE_ONE_INDOOR_PLOT_ID,
            "quantity": 3,
            "room_label": "Greenhouse shelf",
            "seen_growing": True,
            "seen_growing_date": "2026-07-01",
            "owner_username": ADMIN_USERNAME,
        },
        "large_garden": {
            "id": int(large["id"]),
            "name": str(large["name"]),
            "slug": PHASE_ONE_LARGE_GARDEN_SLUG,
        },
        "map_unit": {
            "garden_id": alpha_id,
            "name": PHASE_ONE_MAP_UNIT_NAME,
            "public_id": PHASE_ONE_MAP_UNIT_ID,
        },
        "mobile_snapshot": {
            "count": int(snapshot["count"] if snapshot else 0),
            "garden_id": alpha_id,
            "name": PHASE_ONE_MOBILE_SNAPSHOT_NAME,
            "owner_username": ADMIN_USERNAME,
        },
        "onboarding": {
            "address": PHASE_ONE_ONBOARDING_ADDRESS,
            "desktop_garden_name": PHASE_ONE_DESKTOP_ONBOARDING_GARDEN_NAME,
            "desktop_garden_slug": PHASE_ONE_DESKTOP_ONBOARDING_GARDEN_SLUG,
            "desktop_username": ONBOARDING_LOGIN[0],
            "grid_cols": PHASE_ONE_ONBOARDING_GRID_COLS,
            "grid_rows": PHASE_ONE_ONBOARDING_GRID_ROWS,
            "house": PHASE_ONE_ONBOARDING_HOUSE,
            "latitude": PHASE_ONE_ONBOARDING_LATITUDE,
            "longitude": PHASE_ONE_ONBOARDING_LONGITUDE,
            "mobile_garden_slug": PHASE_ONE_MOBILE_ONBOARDING_GARDEN_SLUG,
            "mobile_garden_name": PHASE_ONE_MOBILE_ONBOARDING_GARDEN_NAME,
            "mobile_username": MOBILE_ONBOARDING_LOGIN[0],
        },
        "saved_view": {
            "garden_id": alpha_id,
            "label": PHASE_ONE_SAVED_VIEW_LABEL,
            "owner_username": ADMIN_USERNAME,
            "view_type": "plants",
        },
        "viewer": {
            "alpha": viewer_payload("alpha", alpha_id),
            "beta": viewer_payload("beta", int(beta["id"])),
        },
    }


def _json_object(value: object, *, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Complete journey {label} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Complete journey {label} must be a JSON object")
    return parsed


def _garden_graph(conn, *, garden_id: int) -> dict[str, Any]:
    """Return the semantic garden graph that restore/import must preserve exactly."""
    garden = conn.execute(
        """
        SELECT
            garden_value.id,
            garden_value.slug,
            garden_value.name,
            garden_value.grid_rows,
            garden_value.grid_cols,
            garden_value.latitude,
            garden_value.longitude,
            garden_value.address,
            garden_value.onboarding_complete,
            owner.username AS owner_username
        FROM gardens garden_value
        LEFT JOIN auth_users owner ON owner.id = garden_value.owner_user_id
        WHERE garden_value.id = %s
        """,
        (garden_id,),
    ).fetchone()
    layout = conn.execute(
        """
        SELECT house_row, house_col, house_width, house_height, north_degrees, grid_rows, grid_cols
        FROM layout_state
        WHERE garden_id = %s
        """,
        (garden_id,),
    ).fetchone()
    if not garden or not layout:
        raise RuntimeError("Complete journey garden graph is missing its garden or layout")

    plot_rows = conn.execute(
        """
        SELECT
            plot_value.plot_id,
            plot_value.garden_id,
            plot_value.zone_code,
            plot_value.zone_name,
            plot_value.plot_number,
            plot_value.grid_row,
            plot_value.grid_col,
            plot_value.sub_zone,
            plot_value.notes,
            plot_value.color,
            owner.username AS owner_username
        FROM plots plot_value
        LEFT JOIN plot_ownership ownership
          ON ownership.plot_id = plot_value.plot_id
         AND ownership.garden_id = plot_value.garden_id
        LEFT JOIN auth_users owner ON owner.id = ownership.owner_user_id
        WHERE plot_value.garden_id = %s
        ORDER BY plot_value.plot_id
        """,
        (garden_id,),
    ).fetchall()
    plant_rows = conn.execute(
        """
        SELECT
            plant_value.plt_id,
            plant_value.name,
            plant_value.latin,
            plant_value.category,
            plant_value.bloom_month,
            plant_value.color,
            plant_value.hardiness,
            plant_value.height_cm,
            plant_value.light,
            plant_value.link,
            plant_value.year_planted,
            plant_value.seen_growing,
            plant_value.deer_resistant,
            plant_value.care_watering,
            plant_value.care_soil,
            plant_value.care_planting,
            plant_value.care_maintenance,
            plant_value.care_notes,
            plant_value.seen_growing_date,
            ownership.garden_id,
            owner.username AS owner_username
        FROM plant_ownership ownership
        JOIN plants plant_value ON plant_value.plt_id = ownership.plt_id
        LEFT JOIN auth_users owner ON owner.id = ownership.owner_user_id
        WHERE ownership.garden_id = %s
        ORDER BY plant_value.plt_id
        """,
        (garden_id,),
    ).fetchall()
    assignment_rows = conn.execute(
        """
        SELECT
            assignment.plot_id,
            assignment.plt_id,
            assignment.quantity,
            assignment.seen_growing,
            assignment.seen_growing_date,
            assignment.room_label,
            plot_value.garden_id AS plot_garden_id,
            plot_owner.username AS plot_owner_username,
            plant_ownership.garden_id AS plant_garden_id,
            plant_owner.username AS plant_owner_username
        FROM plot_plants assignment
        JOIN plots plot_value ON plot_value.plot_id = assignment.plot_id
        LEFT JOIN plot_ownership plot_ownership
          ON plot_ownership.plot_id = assignment.plot_id
         AND plot_ownership.garden_id = plot_value.garden_id
        LEFT JOIN auth_users plot_owner ON plot_owner.id = plot_ownership.owner_user_id
        LEFT JOIN plant_ownership plant_ownership
          ON plant_ownership.plt_id = assignment.plt_id
         AND plant_ownership.garden_id = plot_value.garden_id
        LEFT JOIN auth_users plant_owner ON plant_owner.id = plant_ownership.owner_user_id
        WHERE plot_value.garden_id = %s
        ORDER BY assignment.plot_id, assignment.plt_id
        """,
        (garden_id,),
    ).fetchall()
    map_object_rows = conn.execute(
        """
        SELECT
            object_value.id,
            object_value.public_id,
            object_value.object_type,
            object_value.name,
            object_value.shape_type,
            object_value.geometry_json,
            object_value.style_json,
            object_value.z_index,
            object_value.has_internal_layout,
            object_value.internal_layout_json,
            owner.username AS created_by_username
        FROM garden_map_objects object_value
        LEFT JOIN auth_users owner ON owner.id = object_value.created_by_user_id
        WHERE object_value.garden_id = %s
        ORDER BY object_value.z_index, object_value.id
        """,
        (garden_id,),
    ).fetchall()
    unit_rows = conn.execute(
        """
        SELECT
            unit_value.map_object_id,
            unit_value.public_id,
            unit_value.unit_type,
            unit_value.name,
            unit_value.shape_type,
            unit_value.geometry_json,
            unit_value.style_json,
            unit_value.sort_order
        FROM garden_map_object_units unit_value
        JOIN garden_map_objects object_value ON object_value.id = unit_value.map_object_id
        WHERE unit_value.garden_id = %s AND object_value.garden_id = %s
        ORDER BY object_value.z_index, object_value.id, unit_value.sort_order, unit_value.id
        """,
        (garden_id, garden_id),
    ).fetchall()
    units_by_object: dict[int, list[dict[str, Any]]] = {}
    for row in unit_rows:
        units_by_object.setdefault(int(row["map_object_id"]), []).append(
            {
                "geometry": _json_object(row["geometry_json"], label="map-unit geometry"),
                "name": str(row["name"]),
                "public_id": str(row["public_id"]),
                "shape_type": str(row["shape_type"]),
                "sort_order": int(row["sort_order"]),
                "style": _json_object(row["style_json"], label="map-unit style"),
                "unit_type": str(row["unit_type"]),
            }
        )

    return {
        "assignments": [
            {
                "plant_garden_id": (
                    int(row["plant_garden_id"]) if row["plant_garden_id"] is not None else None
                ),
                "plant_owner_username": (
                    str(row["plant_owner_username"])
                    if row["plant_owner_username"] is not None
                    else None
                ),
                "plot_garden_id": int(row["plot_garden_id"]),
                "plot_id": str(row["plot_id"]),
                "plot_owner_username": (
                    str(row["plot_owner_username"])
                    if row["plot_owner_username"] is not None
                    else None
                ),
                "plant_id": str(row["plt_id"]),
                "quantity": int(row["quantity"]),
                "room_label": str(row["room_label"] or ""),
                "seen_growing": bool(row["seen_growing"]),
                "seen_growing_date": str(row["seen_growing_date"] or ""),
            }
            for row in assignment_rows
        ],
        "garden": {
            "address": str(garden["address"] or ""),
            "grid_cols": int(garden["grid_cols"]),
            "grid_rows": int(garden["grid_rows"]),
            "id": int(garden["id"]),
            "latitude": float(garden["latitude"]) if garden["latitude"] is not None else None,
            "longitude": float(garden["longitude"]) if garden["longitude"] is not None else None,
            "name": str(garden["name"]),
            "onboarding_complete": bool(garden["onboarding_complete"]),
            "owner_username": (
                str(garden["owner_username"]) if garden["owner_username"] is not None else None
            ),
            "slug": str(garden["slug"]),
        },
        "layout": {
            "col": int(layout["house_col"]),
            "grid_cols": int(layout["grid_cols"]),
            "grid_rows": int(layout["grid_rows"]),
            "height": int(layout["house_height"]),
            "north_degrees": int(layout["north_degrees"]),
            "row": int(layout["house_row"]),
            "width": int(layout["house_width"]),
        },
        "map_objects": [
            {
                "created_by_username": (
                    str(row["created_by_username"])
                    if row["created_by_username"] is not None
                    else None
                ),
                "geometry": _json_object(row["geometry_json"], label="map-object geometry"),
                "has_internal_layout": bool(row["has_internal_layout"]),
                "internal_layout": _json_object(
                    row["internal_layout_json"], label="map-object internal layout"
                ),
                "name": str(row["name"]),
                "object_type": str(row["object_type"]),
                "public_id": str(row["public_id"]),
                "shape_type": str(row["shape_type"]),
                "style": _json_object(row["style_json"], label="map-object style"),
                "units": units_by_object.get(int(row["id"]), []),
                "z_index": int(row["z_index"]),
            }
            for row in map_object_rows
        ],
        "plants": [
            {
                "bloom_month": str(row["bloom_month"] or ""),
                "care_maintenance": str(row["care_maintenance"] or ""),
                "care_notes": str(row["care_notes"] or ""),
                "care_planting": str(row["care_planting"] or ""),
                "care_soil": str(row["care_soil"] or ""),
                "care_watering": str(row["care_watering"] or ""),
                "category": str(row["category"]),
                "color": str(row["color"] or ""),
                "deer_resistant": bool(row["deer_resistant"]),
                "garden_id": int(row["garden_id"]),
                "hardiness": str(row["hardiness"] or ""),
                "height_cm": int(row["height_cm"]) if row["height_cm"] is not None else None,
                "latin": str(row["latin"] or ""),
                "light": str(row["light"] or ""),
                "link": str(row["link"] or ""),
                "name": str(row["name"]),
                "owner_username": (
                    str(row["owner_username"]) if row["owner_username"] is not None else None
                ),
                "plant_id": str(row["plt_id"]),
                "seen_growing": bool(row["seen_growing"]),
                "seen_growing_date": str(row["seen_growing_date"] or ""),
                "year_planted": str(row["year_planted"] or ""),
            }
            for row in plant_rows
        ],
        "plots": [
            {
                "color": str(row["color"] or ""),
                "garden_id": int(row["garden_id"]),
                "grid_col": int(row["grid_col"]) if row["grid_col"] is not None else None,
                "grid_row": int(row["grid_row"]) if row["grid_row"] is not None else None,
                "notes": str(row["notes"] or ""),
                "owner_username": (
                    str(row["owner_username"]) if row["owner_username"] is not None else None
                ),
                "plot_id": str(row["plot_id"]),
                "plot_number": int(row["plot_number"]),
                "sub_zone": str(row["sub_zone"] or ""),
                "zone_code": str(row["zone_code"]),
                "zone_name": str(row["zone_name"]),
            }
            for row in plot_rows
        ],
    }


def _snapshot_payload_projection(
    conn: Any, *, garden_id: int, graph: dict[str, Any]
) -> dict[str, Any]:
    """Build the exact payload shape emitted by the snapshot endpoint."""
    return {
        "house": graph["layout"],
        "map_objects": snapshot_map_objects(conn, garden_id),
        "plots": [
            {
                "color": plot["color"],
                "grid_col": plot["grid_col"],
                "grid_row": plot["grid_row"],
                "notes": plot["notes"],
                "plot_id": plot["plot_id"],
                "plot_number": plot["plot_number"],
                "sub_zone": plot["sub_zone"],
                "zone_code": plot["zone_code"],
                "zone_name": plot["zone_name"],
            }
            for plot in graph["plots"]
        ],
        "schema_version": 1,
        "shademap": get_shademap_state(conn, garden_id=garden_id),
        "shademap_calibration": get_shademap_calibration(conn, garden_id=garden_id),
        "shademap_obstacles": list_shademap_obstacles(conn, garden_id=garden_id),
    }


def _semantic_table_rows(
    conn: Any,
    *,
    table: str,
    where_sql: str = "TRUE",
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    """Return a deterministic, complete semantic projection of selected table rows."""
    rows = conn.execute(
        sql.SQL(
            """
            SELECT to_jsonb(row_value)::text AS payload
            FROM {} AS row_value
            WHERE {}
            ORDER BY to_jsonb(row_value)::text
            """
        ).format(sql.Identifier(table), sql.SQL(where_sql)),
        params,
    ).fetchall()
    return [_json_object(row["payload"], label=f"{table} semantic row") for row in rows]


def _phase_one_stable_domain_projection(
    conn: Any, *, alpha_id: int, beta_id: int
) -> dict[str, list[dict[str, Any]]]:
    """Keep every non-retained Phase 1 row byte-for-byte semantic-equivalent."""
    onboarding_scope = (
        "garden_id NOT IN (SELECT id FROM gardens WHERE slug = %s OR name IN (%s, %s))"
    )
    onboarding_params = (
        PHASE_ONE_ONBOARDING_DEFAULT_GARDEN_SLUG,
        PHASE_ONE_DESKTOP_ONBOARDING_GARDEN_NAME,
        PHASE_ONE_MOBILE_ONBOARDING_GARDEN_NAME,
    )
    restored_scope = f"garden_id NOT IN (%s, %s) AND {onboarding_scope}"
    restored_params = (alpha_id, beta_id, *onboarding_params)
    phase_two_task_ids = list(PHASE_TWO_TASKS.values())
    phase_two_journal_source_scope = (
        "COALESCE(COALESCE(metadata_json, '{}')::jsonb ->> 'source_task_id', '') <> ALL(%s)"
    )
    journal_scope = (
        "entry_id NOT IN ("
        "SELECT entry.id FROM garden_journal_entries entry "
        "WHERE entry.notes = %s "
        "OR COALESCE(COALESCE(entry.metadata_json, '{}')::jsonb ->> 'source_task_id', '') "
        "= ANY(%s)"
        ")"
    )
    journal_params = (PHASE_ONE_QUICK_ACTION_NOTE, phase_two_task_ids)
    harvest_scope = "entry_id NOT IN (SELECT id FROM harvest_entries WHERE notes = %s)"
    return {
        "app_settings": _semantic_table_rows(
            conn,
            table="app_settings",
            where_sql="key NOT LIKE %s",
            params=(f"harvest_rollup:{alpha_id}:%",),
        ),
        "garden_journal_entries": _semantic_table_rows(
            conn,
            table="garden_journal_entries",
            where_sql=f"notes <> %s AND {phase_two_journal_source_scope}",
            params=(PHASE_ONE_QUICK_ACTION_NOTE, phase_two_task_ids),
        ),
        "garden_journal_entry_plants": _semantic_table_rows(
            conn,
            table="garden_journal_entry_plants",
            where_sql=journal_scope,
            params=journal_params,
        ),
        "garden_journal_entry_plots": _semantic_table_rows(
            conn,
            table="garden_journal_entry_plots",
            where_sql=journal_scope,
            params=journal_params,
        ),
        "garden_map_object_units": _semantic_table_rows(
            conn,
            table="garden_map_object_units",
            where_sql="garden_id NOT IN (%s, %s)",
            params=(alpha_id, beta_id),
        ),
        "garden_map_objects": _semantic_table_rows(
            conn,
            table="garden_map_objects",
            where_sql="garden_id NOT IN (%s, %s)",
            params=(alpha_id, beta_id),
        ),
        "garden_memberships": _semantic_table_rows(
            conn,
            table="garden_memberships",
            where_sql=onboarding_scope,
            params=onboarding_params,
        ),
        "gardens": _semantic_table_rows(
            conn,
            table="gardens",
            where_sql="id NOT IN (%s, %s) AND slug <> %s AND name NOT IN (%s, %s)",
            params=(alpha_id, beta_id, *onboarding_params),
        ),
        "harvest_entries": _semantic_table_rows(
            conn,
            table="harvest_entries",
            where_sql="notes <> %s",
            params=(PHASE_ONE_QUICK_ACTION_NOTE,),
        ),
        "harvest_entry_plants": _semantic_table_rows(
            conn,
            table="harvest_entry_plants",
            where_sql=harvest_scope,
            params=(PHASE_ONE_QUICK_ACTION_NOTE,),
        ),
        "harvest_entry_plots": _semantic_table_rows(
            conn,
            table="harvest_entry_plots",
            where_sql=harvest_scope,
            params=(PHASE_ONE_QUICK_ACTION_NOTE,),
        ),
        "layout_snapshots": _semantic_table_rows(
            conn,
            table="layout_snapshots",
            where_sql="name <> %s",
            params=(PHASE_ONE_MOBILE_SNAPSHOT_NAME,),
        ),
        "layout_state": _semantic_table_rows(
            conn,
            table="layout_state",
            where_sql=restored_scope,
            params=restored_params,
        ),
        "plot_ownership": _semantic_table_rows(
            conn,
            table="plot_ownership",
            where_sql=restored_scope,
            params=restored_params,
        ),
        "plots": _semantic_table_rows(
            conn,
            table="plots",
            where_sql=restored_scope,
            params=restored_params,
        ),
    }


def _entry_link_ids(conn: Any, *, table: str, entry_id: int, column: str) -> list[str]:
    rows = conn.execute(
        sql.SQL("SELECT {} FROM {} WHERE entry_id = %s ORDER BY {}").format(
            sql.Identifier(column), sql.Identifier(table), sql.Identifier(column)
        ),
        (entry_id,),
    ).fetchall()
    return [str(row[column]) for row in rows]


def _quick_action_records(conn: Any, *, alpha_id: int) -> dict[str, Any]:
    """Project the sole retained quick-action harvest, journal, links, and rollup."""
    harvest_rows = conn.execute(
        """
        SELECT
            harvest.id,
            harvest.public_id,
            harvest.garden_id,
            harvest.occurred_on,
            harvest.quantity,
            harvest.unit,
            harvest.quality,
            harvest.notes,
            harvest.metadata_json,
            actor.username AS actor_username
        FROM harvest_entries harvest
        LEFT JOIN auth_users actor ON actor.id = harvest.actor_user_id
        WHERE harvest.notes = %s
        ORDER BY harvest.id
        """,
        (PHASE_ONE_QUICK_ACTION_NOTE,),
    ).fetchall()
    journal_rows = conn.execute(
        """
        SELECT
            journal.id,
            journal.public_id,
            journal.garden_id,
            journal.event_type,
            journal.occurred_on,
            journal.title,
            journal.notes,
            journal.metadata_json,
            actor.username AS actor_username
        FROM garden_journal_entries journal
        LEFT JOIN auth_users actor ON actor.id = journal.actor_user_id
        WHERE journal.notes = %s
        ORDER BY journal.id
        """,
        (PHASE_ONE_QUICK_ACTION_NOTE,),
    ).fetchall()
    rollup_rows = conn.execute(
        """
        SELECT key, value
        FROM app_settings
        WHERE key LIKE %s
        ORDER BY key
        """,
        (f"harvest_rollup:{alpha_id}:%",),
    ).fetchall()
    return {
        "harvest_rollups": [
            {
                "key": str(row["key"]),
                "value": _json_object(row["value"], label="quick-action harvest rollup"),
            }
            for row in rollup_rows
        ],
        "harvests": [
            {
                "actor_username": (
                    str(row["actor_username"]) if row["actor_username"] is not None else None
                ),
                "garden_id": int(row["garden_id"]),
                "metadata": _json_object(
                    row["metadata_json"], label="quick-action harvest metadata"
                ),
                "notes": str(row["notes"] or ""),
                "occurred_on": str(row["occurred_on"]),
                "plant_ids": _entry_link_ids(
                    conn,
                    table="harvest_entry_plants",
                    entry_id=int(row["id"]),
                    column="plt_id",
                ),
                "plot_ids": _entry_link_ids(
                    conn,
                    table="harvest_entry_plots",
                    entry_id=int(row["id"]),
                    column="plot_id",
                ),
                "public_id": str(row["public_id"]),
                "quality": str(row["quality"]),
                "quantity": float(row["quantity"]),
                "unit": str(row["unit"]),
            }
            for row in harvest_rows
        ],
        "journals": [
            {
                "actor_username": (
                    str(row["actor_username"]) if row["actor_username"] is not None else None
                ),
                "event_type": str(row["event_type"]),
                "garden_id": int(row["garden_id"]),
                "metadata": _json_object(
                    row["metadata_json"], label="quick-action journal metadata"
                ),
                "notes": str(row["notes"] or ""),
                "occurred_on": str(row["occurred_on"]),
                "plant_ids": _entry_link_ids(
                    conn,
                    table="garden_journal_entry_plants",
                    entry_id=int(row["id"]),
                    column="plt_id",
                ),
                "plot_ids": _entry_link_ids(
                    conn,
                    table="garden_journal_entry_plots",
                    entry_id=int(row["id"]),
                    column="plot_id",
                ),
                "public_id": str(row["public_id"]),
                "title": str(row["title"] or ""),
            }
            for row in journal_rows
        ],
    }


def _onboarding_default_context(conn: Any) -> dict[str, Any]:
    """Capture the shared default context created for the two onboarding users."""
    garden_rows = conn.execute(
        """
        SELECT
            garden_value.id,
            garden_value.slug,
            garden_value.name,
            garden_value.grid_rows,
            garden_value.grid_cols,
            garden_value.latitude,
            garden_value.longitude,
            garden_value.address,
            garden_value.onboarding_complete,
            owner.username AS owner_username,
            (SELECT COUNT(*) FROM layout_state WHERE garden_id = garden_value.id) AS layout_count,
            (SELECT COUNT(*) FROM garden_map_objects WHERE garden_id = garden_value.id)
                AS map_object_count,
            (SELECT COUNT(*) FROM plots WHERE garden_id = garden_value.id) AS plot_count
        FROM gardens garden_value
        LEFT JOIN auth_users owner ON owner.id = garden_value.owner_user_id
        WHERE garden_value.slug = %s
        ORDER BY garden_value.id
        """,
        (PHASE_ONE_ONBOARDING_DEFAULT_GARDEN_SLUG,),
    ).fetchall()
    membership_rows = conn.execute(
        """
        SELECT membership.garden_id, membership.role, users.username
        FROM garden_memberships membership
        JOIN gardens garden_value ON garden_value.id = membership.garden_id
        JOIN auth_users users ON users.id = membership.user_id
        WHERE garden_value.slug = %s
        ORDER BY users.username
        """,
        (PHASE_ONE_ONBOARDING_DEFAULT_GARDEN_SLUG,),
    ).fetchall()
    return {
        "gardens": [
            {
                "address": str(row["address"] or ""),
                "grid_cols": int(row["grid_cols"]),
                "grid_rows": int(row["grid_rows"]),
                "id": int(row["id"]),
                "latitude": float(row["latitude"]) if row["latitude"] is not None else None,
                "layout_count": int(row["layout_count"]),
                "longitude": (float(row["longitude"]) if row["longitude"] is not None else None),
                "map_object_count": int(row["map_object_count"]),
                "name": str(row["name"]),
                "onboarding_complete": bool(row["onboarding_complete"]),
                "owner_username": (
                    str(row["owner_username"]) if row["owner_username"] is not None else None
                ),
                "plot_count": int(row["plot_count"]),
                "slug": str(row["slug"]),
            }
            for row in garden_rows
        ],
        "memberships": [
            {
                "garden_id": int(row["garden_id"]),
                "role": str(row["role"]),
                "username": str(row["username"]),
            }
            for row in membership_rows
        ],
    }


def _phase_one_runtime_state(conn, optimization_seed: Any) -> dict[str, Any]:
    onboarding_gardens = conn.execute(
        """
        SELECT u.username, g.slug, g.name, g.onboarding_complete, gm.role
        FROM auth_users u
        JOIN garden_memberships gm ON gm.user_id = u.id
        JOIN gardens g ON g.id = gm.garden_id
        WHERE u.username IN (%s, %s)
        ORDER BY u.username, g.slug
        """,
        (ONBOARDING_LOGIN[0], MOBILE_ONBOARDING_LOGIN[0]),
    ).fetchall()
    onboarding_target_rows = conn.execute(
        """
        SELECT
            g.id,
            g.name,
            g.slug,
            g.grid_rows,
            g.grid_cols,
            g.latitude,
            g.longitude,
            g.address,
            g.onboarding_complete,
            owner.username AS owner_username,
            member.username AS membership_username,
            gm.role AS membership_role,
            layout.house_row AS layout_row,
            layout.house_col AS layout_col,
            layout.house_width AS layout_width,
            layout.house_height AS layout_height,
            layout.north_degrees AS layout_north_degrees,
            layout.grid_rows AS layout_grid_rows,
            layout.grid_cols AS layout_grid_cols
        FROM gardens g
        LEFT JOIN auth_users owner ON owner.id = g.owner_user_id
        LEFT JOIN garden_memberships gm ON gm.garden_id = g.id
        LEFT JOIN auth_users member ON member.id = gm.user_id
        LEFT JOIN layout_state layout ON layout.garden_id = g.id
        WHERE g.name IN (%s, %s)
        ORDER BY g.id, member.username, gm.role
        """,
        (PHASE_ONE_DESKTOP_ONBOARDING_GARDEN_NAME, PHASE_ONE_MOBILE_ONBOARDING_GARDEN_NAME),
    ).fetchall()
    alpha = conn.execute(
        "SELECT id, address FROM gardens WHERE slug = %s",
        (optimization_seed.GARDEN_A_SLUG,),
    ).fetchone()
    beta = conn.execute(
        "SELECT id FROM gardens WHERE slug = %s",
        (optimization_seed.GARDEN_B_SLUG,),
    ).fetchone()
    if not alpha or not beta:
        raise RuntimeError("Complete journey Alpha/Beta gardens are missing")
    alpha_id = int(alpha["id"])
    beta_id = int(beta["id"])
    map_object = conn.execute(
        """
        SELECT geometry_json, style_json
        FROM garden_map_objects
        WHERE garden_id = %s AND public_id = %s
        """,
        (alpha_id, optimization_seed._GARDEN_SPECS[0]["object_id"]),
    ).fetchone()
    map_unit = conn.execute(
        """
        SELECT geometry_json, style_json, name
        FROM garden_map_object_units
        WHERE garden_id = %s AND public_id = %s
        """,
        (alpha_id, PHASE_ONE_MAP_UNIT_ID),
    ).fetchone()
    indoor = conn.execute(
        """
        SELECT
            pp.plot_id,
            pp.plt_id,
            pp.quantity,
            pp.seen_growing,
            pp.seen_growing_date,
            pp.room_label,
            plot_ownership.garden_id AS plot_garden_id,
            plant_ownership.garden_id AS plant_garden_id,
            plot_owner.username AS plot_owner_username,
            plant_owner.username AS plant_owner_username
        FROM plot_plants pp
        JOIN plot_ownership ON plot_ownership.plot_id = pp.plot_id
        JOIN auth_users plot_owner ON plot_owner.id = plot_ownership.owner_user_id
        JOIN plant_ownership
          ON plant_ownership.plt_id = pp.plt_id
         AND plant_ownership.garden_id = plot_ownership.garden_id
        JOIN auth_users plant_owner ON plant_owner.id = plant_ownership.owner_user_id
        WHERE pp.plot_id = %s AND pp.plt_id = %s
        """,
        (PHASE_ONE_INDOOR_PLOT_ID, PHASE_ONE_INDOOR_PLANT_ID),
    ).fetchone()
    saved_view_rows = conn.execute(
        """
        SELECT sv.garden_id, sv.is_preset, sv.label, sv.view_type, user_value.username
        FROM user_saved_views sv
        LEFT JOIN auth_users user_value ON user_value.id = sv.user_id
        WHERE sv.label = %s
        ORDER BY sv.id
        """,
        (PHASE_ONE_SAVED_VIEW_LABEL,),
    ).fetchall()
    mobile_snapshot_rows = conn.execute(
        """
        SELECT
            snapshot.garden_id,
            snapshot.public_id,
            snapshot.name,
            snapshot.data,
            owner.username AS garden_owner_username
        FROM layout_snapshots snapshot
        JOIN gardens garden_value ON garden_value.id = snapshot.garden_id
        LEFT JOIN auth_users owner ON owner.id = garden_value.owner_user_id
        WHERE snapshot.name = %s
        ORDER BY snapshot.garden_id, snapshot.public_id
        """,
        (PHASE_ONE_MOBILE_SNAPSHOT_NAME,),
    ).fetchall()
    mobile_harvest_rows = conn.execute(
        """
        SELECT harvest.garden_id, actor.username AS actor_username
        FROM harvest_entries harvest
        LEFT JOIN auth_users actor ON actor.id = harvest.actor_user_id
        WHERE harvest.notes = %s
        ORDER BY harvest.id
        """,
        (PHASE_ONE_QUICK_ACTION_NOTE,),
    ).fetchall()
    mobile_journal_rows = conn.execute(
        """
        SELECT journal.garden_id, actor.username AS actor_username
        FROM garden_journal_entries journal
        LEFT JOIN auth_users actor ON actor.id = journal.actor_user_id
        WHERE journal.notes = %s
        ORDER BY journal.id
        """,
        (PHASE_ONE_QUICK_ACTION_NOTE,),
    ).fetchall()
    counts = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM plants WHERE name LIKE 'Phase 1 Browser Mint%%') AS temp_plants,
            (SELECT COUNT(*) FROM user_saved_views
             WHERE label LIKE 'Phase 1 Browser Plant View%%') AS temp_views,
            (SELECT COUNT(*) FROM garden_map_objects
             WHERE name LIKE 'Phase 1 %%') AS temp_map_objects,
            (SELECT COUNT(*) FROM layout_snapshots WHERE name = %s) AS mobile_snapshots,
            (SELECT COUNT(*) FROM harvest_entries WHERE notes = %s) AS harvests,
            (SELECT COUNT(*) FROM garden_journal_entries WHERE notes = %s) AS journals,
            (SELECT COUNT(*) FROM garden_map_object_units
             WHERE garden_id = %s) AS alpha_map_unit_count
        """,
        (
            PHASE_ONE_MOBILE_SNAPSHOT_NAME,
            PHASE_ONE_QUICK_ACTION_NOTE,
            PHASE_ONE_QUICK_ACTION_NOTE,
            alpha_id,
        ),
    ).fetchone()
    lifecycle = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM plants WHERE plt_id = %s) AS plant_rows,
            (SELECT COUNT(*) FROM plant_ownership WHERE plt_id = %s) AS plant_ownership_rows,
            (SELECT COUNT(*) FROM plot_plants WHERE plt_id = %s) AS plant_assignment_rows,
            (SELECT COUNT(*) FROM user_saved_views WHERE label LIKE %s) AS saved_view_rows,
            (SELECT COUNT(*) FROM user_saved_views
             WHERE label LIKE %s AND garden_id = %s) AS saved_view_alpha_rows
        """,
        (
            PHASE_ONE_BROWSER_PLANT_ID,
            PHASE_ONE_BROWSER_PLANT_ID,
            PHASE_ONE_BROWSER_PLANT_ID,
            f"{PHASE_ONE_BROWSER_SAVED_VIEW_LABEL}%",
            f"{PHASE_ONE_BROWSER_SAVED_VIEW_LABEL}%",
            alpha_id,
        ),
    ).fetchone()
    cross_garden_links = conn.execute(
        """
        SELECT
            (
                SELECT COUNT(*)
                FROM plot_plants pp
                JOIN plot_ownership plot_owner ON plot_owner.plot_id = pp.plot_id
                LEFT JOIN plant_ownership plant_owner
                  ON plant_owner.plt_id = pp.plt_id
                 AND plant_owner.garden_id = plot_owner.garden_id
                WHERE plot_owner.garden_id IN (%s, %s)
                  AND plant_owner.plt_id IS NULL
            ) AS assignments_without_matching_ownership,
            (
                SELECT COUNT(*)
                FROM plot_plants pp
                JOIN plot_ownership plot_owner ON plot_owner.plot_id = pp.plot_id
                JOIN plant_ownership plant_owner ON plant_owner.plt_id = pp.plt_id
                WHERE plot_owner.garden_id IN (%s, %s)
                  AND plant_owner.garden_id IN (%s, %s)
                  AND plant_owner.garden_id <> plot_owner.garden_id
            ) AS assignments_with_cross_garden_ownership,
            (
                SELECT COUNT(*)
                FROM garden_map_object_units unit_value
                JOIN garden_map_objects object_value ON object_value.id = unit_value.map_object_id
                WHERE unit_value.garden_id IN (%s, %s)
                  AND unit_value.garden_id <> object_value.garden_id
            ) AS map_unit_parent_garden_mismatch,
            (
                SELECT COUNT(*)
                FROM user_saved_views saved_view
                LEFT JOIN garden_memberships membership
                  ON membership.garden_id = saved_view.garden_id
                 AND membership.user_id = saved_view.user_id
                WHERE saved_view.garden_id IN (%s, %s)
                  AND saved_view.user_id IS NOT NULL
                  AND membership.user_id IS NULL
            ) AS saved_views_without_membership,
            (
                SELECT COUNT(*)
                FROM layout_snapshots
                WHERE name = %s AND garden_id = %s
            ) AS mobile_snapshot_in_beta,
            (
                SELECT COUNT(*)
                FROM harvest_entries
                WHERE notes = %s AND garden_id = %s
            ) AS mobile_harvest_in_beta,
            (
                SELECT COUNT(*)
                FROM garden_journal_entries
                WHERE notes = %s AND garden_id = %s
            ) AS mobile_journal_in_beta
        """,
        (
            alpha_id,
            beta_id,
            alpha_id,
            beta_id,
            alpha_id,
            beta_id,
            alpha_id,
            beta_id,
            alpha_id,
            beta_id,
            PHASE_ONE_MOBILE_SNAPSHOT_NAME,
            beta_id,
            PHASE_ONE_QUICK_ACTION_NOTE,
            beta_id,
            PHASE_ONE_QUICK_ACTION_NOTE,
            beta_id,
        ),
    ).fetchone()
    lifecycle_audit = conn.execute(
        """
        SELECT
            COUNT(*) FILTER (
                WHERE method = 'POST' AND path = '/api/plants' AND status_code = 201
                  AND actor_username = %s AND garden_id = %s
            ) AS plant_create_count,
            COUNT(*) FILTER (
                WHERE method = 'PATCH' AND path LIKE '/api/plants/%%' AND status_code = 200
                  AND actor_username = %s AND garden_id = %s
            ) AS plant_update_count,
            COUNT(*) FILTER (
                WHERE method = 'DELETE' AND path LIKE '/api/plants/%%' AND status_code = 200
                  AND actor_username = %s AND garden_id = %s
            ) AS plant_delete_count,
            COUNT(*) FILTER (
                WHERE method = 'POST' AND path LIKE '/api/plots/%%/plants/%%' AND status_code = 201
                  AND actor_username = %s AND garden_id = %s
            ) AS assignment_create_count,
            COUNT(*) FILTER (
                WHERE method = 'DELETE' AND path LIKE '/api/plots/%%/plants/%%'
                  AND status_code = 204
                  AND actor_username = %s AND garden_id = %s
            ) AS assignment_delete_count,
            COUNT(*) FILTER (
                WHERE method = 'POST' AND path = '/api/saved-views' AND status_code = 201
                  AND actor_username = %s AND garden_id = %s
            ) AS saved_view_create_count,
            COUNT(*) FILTER (
                WHERE method = 'DELETE' AND path LIKE '/api/saved-views/%%' AND status_code = 200
                  AND actor_username = %s AND garden_id = %s
            ) AS saved_view_delete_count,
            COUNT(*) FILTER (
                WHERE method = 'POST'
                  AND path LIKE '/api/gardens/%%/map-objects/%%/units'
                  AND status_code = 201 AND actor_username = %s AND garden_id = %s
            ) AS nested_unit_create_count,
            COUNT(*) FILTER (
                WHERE method = 'PATCH'
                  AND path LIKE '/api/gardens/%%/map-objects/%%/units/%%'
                  AND status_code = 200 AND actor_username = %s AND garden_id = %s
            ) AS nested_unit_update_count,
            COUNT(*) FILTER (
                WHERE method = 'DELETE'
                  AND path LIKE '/api/gardens/%%/map-objects/%%/units/%%'
                  AND status_code = 200 AND actor_username = %s AND garden_id = %s
            ) AS nested_unit_direct_delete_count
        FROM audit_events
        """,
        (
            ADMIN_USERNAME,
            alpha_id,
            ADMIN_USERNAME,
            alpha_id,
            ADMIN_USERNAME,
            alpha_id,
            ADMIN_USERNAME,
            alpha_id,
            ADMIN_USERNAME,
            alpha_id,
            ADMIN_USERNAME,
            alpha_id,
            ADMIN_USERNAME,
            alpha_id,
            ADMIN_USERNAME,
            alpha_id,
            ADMIN_USERNAME,
            alpha_id,
            ADMIN_USERNAME,
            alpha_id,
        ),
    ).fetchone()
    alpha_graph = _garden_graph(conn, garden_id=alpha_id)
    beta_graph = _garden_graph(conn, garden_id=beta_id)
    onboarding_targets: dict[int, dict[str, Any]] = {}
    for row in onboarding_target_rows:
        garden_id = int(row["id"])
        target = onboarding_targets.setdefault(
            garden_id,
            {
                "address": str(row["address"] or ""),
                "grid_cols": int(row["grid_cols"]),
                "grid_rows": int(row["grid_rows"]),
                "id": garden_id,
                "latitude": float(row["latitude"]) if row["latitude"] is not None else None,
                "layout": {
                    "col": (int(row["layout_col"]) if row["layout_col"] is not None else None),
                    "grid_cols": (
                        int(row["layout_grid_cols"])
                        if row["layout_grid_cols"] is not None
                        else None
                    ),
                    "grid_rows": (
                        int(row["layout_grid_rows"])
                        if row["layout_grid_rows"] is not None
                        else None
                    ),
                    "height": (
                        int(row["layout_height"]) if row["layout_height"] is not None else None
                    ),
                    "north_degrees": (
                        int(row["layout_north_degrees"])
                        if row["layout_north_degrees"] is not None
                        else None
                    ),
                    "row": (int(row["layout_row"]) if row["layout_row"] is not None else None),
                    "width": (
                        int(row["layout_width"]) if row["layout_width"] is not None else None
                    ),
                },
                "longitude": (float(row["longitude"]) if row["longitude"] is not None else None),
                "memberships": [],
                "name": str(row["name"]),
                "onboarding_complete": bool(row["onboarding_complete"]),
                "owner_username": (
                    str(row["owner_username"]) if row["owner_username"] is not None else None
                ),
                "slug": str(row["slug"]),
            },
        )
        if row["membership_username"] is not None:
            target["memberships"].append(
                {
                    "role": str(row["membership_role"]),
                    "username": str(row["membership_username"]),
                }
            )
    onboarding_target_values = sorted(
        onboarding_targets.values(), key=lambda garden: (str(garden["name"]), int(garden["id"]))
    )
    onboarding_target_graphs = {
        str(garden["name"]): _garden_graph(conn, garden_id=int(garden["id"]))
        for garden in onboarding_target_values
    }
    alpha_snapshot_payload = _snapshot_payload_projection(
        conn,
        garden_id=alpha_id,
        graph=alpha_graph,
    )
    stable_domain_projection = _phase_one_stable_domain_projection(
        conn,
        alpha_id=alpha_id,
        beta_id=beta_id,
    )
    quick_action_records = _quick_action_records(conn, alpha_id=alpha_id)
    onboarding_default_context = _onboarding_default_context(conn)
    return {
        "alpha_address": str(alpha["address"] or ""),
        "alpha_id": alpha_id,
        "alpha_snapshot_payload": alpha_snapshot_payload,
        "alpha_map_object": (
            {
                "geometry": json.loads(str(map_object["geometry_json"])),
                "style": json.loads(str(map_object["style_json"])),
            }
            if map_object
            else None
        ),
        "alpha_map_unit": (
            {
                "geometry": json.loads(str(map_unit["geometry_json"])),
                "name": str(map_unit["name"]),
                "style": json.loads(str(map_unit["style_json"])),
            }
            if map_unit
            else None
        ),
        "beta_id": beta_id,
        "browser_lifecycle": {
            key: int(lifecycle[key] if lifecycle else 0)
            for key in (
                "plant_assignment_rows",
                "plant_ownership_rows",
                "plant_rows",
                "saved_view_alpha_rows",
                "saved_view_rows",
            )
        },
        "cross_garden_links": {
            key: int(cross_garden_links[key] if cross_garden_links else 0)
            for key in (
                "assignments_without_matching_ownership",
                "assignments_with_cross_garden_ownership",
                "map_unit_parent_garden_mismatch",
                "mobile_harvest_in_beta",
                "mobile_journal_in_beta",
                "mobile_snapshot_in_beta",
                "saved_views_without_membership",
            )
        },
        "harvest_count": int(counts["harvests"]),
        "indoor_assignment": (
            {
                "plant_garden_id": int(indoor["plant_garden_id"]),
                "plant_id": str(indoor["plt_id"]),
                "plant_owner_username": str(indoor["plant_owner_username"]),
                "plot_garden_id": int(indoor["plot_garden_id"]),
                "plot_id": str(indoor["plot_id"]),
                "plot_owner_username": str(indoor["plot_owner_username"]),
                "quantity": int(indoor["quantity"]),
                "room_label": str(indoor["room_label"] or ""),
                "seen_growing": bool(indoor["seen_growing"]),
                "seen_growing_date": str(indoor["seen_growing_date"] or ""),
            }
            if indoor
            else None
        ),
        "indoor_room_label": str(indoor["room_label"] or "") if indoor else None,
        "journal_count": int(counts["journals"]),
        "lifecycle_audit": {
            key: int(lifecycle_audit[key] if lifecycle_audit else 0)
            for key in (
                "assignment_create_count",
                "assignment_delete_count",
                "nested_unit_create_count",
                "nested_unit_direct_delete_count",
                "nested_unit_update_count",
                "plant_create_count",
                "plant_delete_count",
                "plant_update_count",
                "saved_view_create_count",
                "saved_view_delete_count",
            )
        },
        "mobile_harvests": [
            {
                "actor_username": (
                    str(row["actor_username"]) if row["actor_username"] is not None else None
                ),
                "garden_id": int(row["garden_id"]),
            }
            for row in mobile_harvest_rows
        ],
        "mobile_journals": [
            {
                "actor_username": (
                    str(row["actor_username"]) if row["actor_username"] is not None else None
                ),
                "garden_id": int(row["garden_id"]),
            }
            for row in mobile_journal_rows
        ],
        "mobile_snapshot_count": int(counts["mobile_snapshots"]),
        "mobile_snapshots": [
            {
                "garden_id": int(row["garden_id"]),
                "garden_owner_username": (
                    str(row["garden_owner_username"])
                    if row["garden_owner_username"] is not None
                    else None
                ),
                "name": str(row["name"]),
                "payload": _json_object(row["data"], label="mobile snapshot payload"),
                "public_id": str(row["public_id"]),
            }
            for row in mobile_snapshot_rows
        ],
        "onboarding_gardens": [
            {
                "name": str(row["name"]),
                "onboarding_complete": bool(row["onboarding_complete"]),
                "role": str(row["role"]),
                "slug": str(row["slug"]),
                "username": str(row["username"]),
            }
            for row in onboarding_gardens
        ],
        "onboarding_default_context": onboarding_default_context,
        "onboarding_target_gardens": onboarding_target_values,
        "onboarding_target_graphs": onboarding_target_graphs,
        "quick_action_records": quick_action_records,
        "seeded_saved_views": [
            {
                "garden_id": int(row["garden_id"]),
                "is_preset": bool(row["is_preset"]),
                "label": str(row["label"]),
                "owner_username": str(row["username"]) if row["username"] is not None else None,
                "view_type": str(row["view_type"]),
            }
            for row in saved_view_rows
        ],
        "restore_import_graphs": {
            "alpha": alpha_graph,
            "beta": beta_graph,
        },
        "stable_domain_projection": stable_domain_projection,
        "alpha_map_unit_count": int(counts["alpha_map_unit_count"]),
        "temp_map_object_count": int(counts["temp_map_objects"]),
        "temp_plant_count": int(counts["temp_plants"]),
        "temp_saved_view_count": int(counts["temp_views"]),
    }


def _phase_two_runtime_state(conn, optimization_seed: Any) -> dict[str, Any]:
    gardens = {
        str(row["slug"]): int(row["id"])
        for row in conn.execute(
            "SELECT id, slug FROM gardens WHERE slug = ANY(%s)",
            ([optimization_seed.GARDEN_A_SLUG, optimization_seed.GARDEN_B_SLUG],),
        ).fetchall()
    }
    alpha_id = gardens[optimization_seed.GARDEN_A_SLUG]
    beta_id = gardens[optimization_seed.GARDEN_B_SLUG]
    task_rows = conn.execute(
        """
        SELECT
            task_value.id, task_value.public_id, task_value.garden_id,
            task_value.task_type, task_value.title, task_value.status,
            task_value.due_on, task_value.snoozed_until, task_value.rule_source,
            task_value.metadata_json, task_value.window_start_on,
            task_value.window_end_on, task_value.window_kind,
            task_value.completed_at_ms, task_value.created_at_ms, task_value.updated_at_ms,
            completed_by.username AS completed_by_username
        FROM garden_tasks task_value
        LEFT JOIN auth_users completed_by ON completed_by.id = task_value.completed_by_user_id
        WHERE task_value.public_id = ANY(%s)
        ORDER BY task_value.public_id
        """,
        (list(PHASE_TWO_TASKS.values()),),
    ).fetchall()
    task_ids = [int(row["id"]) for row in task_rows]
    task_plants: dict[int, list[str]] = {task_id: [] for task_id in task_ids}
    task_plots: dict[int, list[str]] = {task_id: [] for task_id in task_ids}
    if task_ids:
        for row in conn.execute(
            """
            SELECT task_id, plt_id
            FROM garden_task_plants
            WHERE task_id = ANY(%s)
            ORDER BY task_id, plt_id
            """,
            (task_ids,),
        ).fetchall():
            task_plants[int(row["task_id"])].append(str(row["plt_id"]))
        for row in conn.execute(
            """
            SELECT task_id, plot_id
            FROM garden_task_plots
            WHERE task_id = ANY(%s)
            ORDER BY task_id, plot_id
            """,
            (task_ids,),
        ).fetchall():
            task_plots[int(row["task_id"])].append(str(row["plot_id"]))

    phase_two_plant_ids = [value[0] for value in PHASE_TWO_PLANTS.values()]
    plant_observation_rows = conn.execute(
        """
        SELECT plt_id, seen_growing, seen_growing_date
        FROM plants
        WHERE plt_id = ANY(%s)
        ORDER BY plt_id
        """,
        (phase_two_plant_ids,),
    ).fetchall()
    assignment_observation_rows = conn.execute(
        """
        SELECT plot_id, plt_id, seen_growing, seen_growing_date
        FROM plot_plants
        WHERE plt_id = ANY(%s)
        ORDER BY plot_id, plt_id
        """,
        (phase_two_plant_ids,),
    ).fetchall()

    journal_rows = conn.execute(
        """
        SELECT
            entry.id, entry.public_id, entry.garden_id, entry.event_type,
            entry.occurred_on, entry.title,
            entry.metadata_json, actor.username AS actor_username
        FROM garden_journal_entries entry
        LEFT JOIN auth_users actor ON actor.id = entry.actor_user_id
        WHERE entry.garden_id IN (%s, %s)
          AND COALESCE(entry.metadata_json, '{}')::jsonb ->> 'source_task_id' = ANY(%s)
        ORDER BY entry.id
        """,
        (alpha_id, beta_id, list(PHASE_TWO_TASKS.values())),
    ).fetchall()
    journal_ids = [int(row["id"]) for row in journal_rows]
    journal_plants: dict[int, list[str]] = {entry_id: [] for entry_id in journal_ids}
    journal_plots: dict[int, list[str]] = {entry_id: [] for entry_id in journal_ids}
    if journal_ids:
        for row in conn.execute(
            """
            SELECT entry_id, plt_id
            FROM garden_journal_entry_plants
            WHERE entry_id = ANY(%s)
            ORDER BY entry_id, plt_id
            """,
            (journal_ids,),
        ).fetchall():
            journal_plants[int(row["entry_id"])].append(str(row["plt_id"]))
        for row in conn.execute(
            """
            SELECT entry_id, plot_id
            FROM garden_journal_entry_plots
            WHERE entry_id = ANY(%s)
            ORDER BY entry_id, plot_id
            """,
            (journal_ids,),
        ).fetchall():
            journal_plots[int(row["entry_id"])].append(str(row["plot_id"]))

    calendar_rows = conn.execute(
        """
        SELECT event.id, event.public_id, event.garden_id, event.title,
               event.description, event.event_on, creator.username AS creator_username,
               updater.username AS updater_username
        FROM garden_calendar_events event
        JOIN auth_users creator ON creator.id = event.created_by_user_id
        JOIN auth_users updater ON updater.id = event.updated_by_user_id
        WHERE event.public_id = %s OR event.title LIKE 'Phase 2 Browser%%'
        ORDER BY event.public_id
        """,
        (PHASE_TWO_CALENDAR_PUBLIC_ID,),
    ).fetchall()
    calendar_ids = [int(row["id"]) for row in calendar_rows]
    calendar_plants: dict[int, list[str]] = {event_id: [] for event_id in calendar_ids}
    calendar_plots: dict[int, list[str]] = {event_id: [] for event_id in calendar_ids}
    if calendar_ids:
        for row in conn.execute(
            """
            SELECT event_id, plt_id
            FROM garden_calendar_event_plants
            WHERE event_id = ANY(%s)
            ORDER BY event_id, plt_id
            """,
            (calendar_ids,),
        ).fetchall():
            calendar_plants[int(row["event_id"])].append(str(row["plt_id"]))
        for row in conn.execute(
            """
            SELECT event_id, plot_id
            FROM garden_calendar_event_plots
            WHERE event_id = ANY(%s)
            ORDER BY event_id, plot_id
            """,
            (calendar_ids,),
        ).fetchall():
            calendar_plots[int(row["event_id"])].append(str(row["plot_id"]))

    subscription_rows = conn.execute(
        """
        SELECT subscription.public_id, subscription.garden_id,
               owner.username AS owner_username, creator.username AS creator_username,
               subscription.label, subscription.preset_key, subscription.token_hint,
               length(subscription.token_hash) AS token_hash_length,
               subscription.scope_json, subscription.revoked_at_ms
        FROM calendar_subscriptions subscription
        JOIN auth_users owner ON owner.id = subscription.owner_user_id
        JOIN auth_users creator ON creator.id = subscription.created_by_user_id
        WHERE subscription.label LIKE 'Phase 2%%'
        ORDER BY subscription.public_id
        """
    ).fetchall()
    preference_rows = conn.execute(
        """
        SELECT users.username,
               legacy.in_app_enabled, legacy.email_enabled, legacy.email_address,
               legacy.digest_frequency, legacy.quiet_hours_json AS legacy_quiet_hours_json,
               legacy.rules_json AS notification_rules_json,
               attention.preset, attention.rules_json AS attention_rules_json,
               attention.quiet_hours_json AS attention_quiet_hours_json,
               attention.show_no_action_history, attention.metadata_json
        FROM auth_users users
        LEFT JOIN user_notification_preferences legacy ON legacy.user_id = users.id
        LEFT JOIN user_attention_preferences attention ON attention.user_id = users.id
        WHERE users.username = ANY(%s)
        ORDER BY users.username
        """,
        ([ADMIN_USERNAME, EDITOR_LOGIN[0], VIEWER_LOGIN[0]],),
    ).fetchall()
    notification_rows = conn.execute(
        """
        SELECT event.public_id, event.garden_id, users.username,
               event.notification_type, event.notification_subtype, event.severity,
               event.title, event.target_type, event.target_id, event.dismissed,
               event.read_at_ms, event.emailed_at_ms, event.cleared_at_ms,
               event.clear_reason, event.metadata_json, event.body,
               event.created_at_ms, event.expires_at_ms
        FROM notification_events event
        LEFT JOIN auth_users users ON users.id = event.user_id
        WHERE event.public_id LIKE 'note_complete_p2%%'
           OR event.target_id = ANY(%s)
        ORDER BY event.public_id
        """,
        (list(PHASE_TWO_TASKS.values()),),
    ).fetchall()
    weather_rows = conn.execute(
        """
        SELECT alert.id, alert.garden_id, alert.alert_type, alert.severity,
               alert.title, alert.valid_from, alert.valid_until,
               alert.metadata_json, alert.dismissed, alert.created_at_ms
        FROM weather_alerts alert
        WHERE alert.garden_id IN (%s, %s)
        ORDER BY alert.garden_id, alert.alert_type, alert.valid_from, alert.id
        """,
        (alpha_id, beta_id),
    ).fetchall()
    weather_ids = [int(row["id"]) for row in weather_rows]
    weather_plants: dict[int, list[str]] = {alert_id: [] for alert_id in weather_ids}
    if weather_ids:
        for row in conn.execute(
            """
            SELECT alert_id, plt_id
            FROM weather_alert_plants
            WHERE alert_id = ANY(%s)
            ORDER BY alert_id, plt_id
            """,
            (weather_ids,),
        ).fetchall():
            weather_plants[int(row["alert_id"])].append(str(row["plt_id"]))
    outcome_rows = conn.execute(
        """
        SELECT public_id, garden_id, provider, outcome_type, source_type,
               source_id, source_public_id, target_type, target_id,
               plant_ids_json, plot_ids_json, recovery_action_json,
               metadata_json, occurred_at_ms, expires_at_ms
        FROM attention_outcomes
        WHERE garden_id IN (%s, %s)
          AND (
              target_id = ANY(%s)
              OR source_public_id LIKE 'water:COMPLETE-P2%%'
          )
        ORDER BY public_id
        """,
        (alpha_id, beta_id, list(PHASE_TWO_TASKS.values())),
    ).fetchall()
    item_state_rows = conn.execute(
        """
        SELECT users.username, state.garden_id, state.item_id, state.user_state,
               state.snoozed_until_ms, state.reason, state.metadata_json
        FROM user_attention_item_state state
        JOIN auth_users users ON users.id = state.user_id
        WHERE state.garden_id IN (%s, %s)
          AND (state.item_id LIKE 'attn:weather:alert:%%'
               OR state.item_id LIKE 'attn:task:tsk_complete_p2%%')
        ORDER BY users.username, state.garden_id, state.item_id
        """,
        (alpha_id, beta_id),
    ).fetchall()
    operation_rows = conn.execute(
        """
        SELECT operation_id, garden_id, endpoint, target_type, target_id,
               result_id, request_fingerprint
        FROM offline_create_operations
        WHERE operation_id LIKE 'phase2-%%'
           OR target_id = ANY(%s)
        ORDER BY operation_id
        """,
        (list(PHASE_TWO_TASKS.values()),),
    ).fetchall()
    maintenance_rows = _phase_two_maintenance_semantic_rows(conn, alpha_id)
    explicit_notification_ids = {
        PHASE_TWO_NOTIFICATION_PUBLIC_ID,
        "note_complete_p2_beta_conflict",
        "note_complete_p2_editor",
        PHASE_TWO_DELIVERY_ELIGIBLE_NOTIFICATION_PUBLIC_ID,
        PHASE_TWO_DELIVERY_INELIGIBLE_NOTIFICATION_PUBLIC_ID,
    }
    maintenance_created = {
        "tasks": [
            row
            for row in maintenance_rows["tasks"]
            if row["created_at_ms"] == PHASE_TWO_NOW_MS
            and row["public_id"] not in set(PHASE_TWO_TASKS.values())
        ],
        "notifications": [
            row
            for row in maintenance_rows["notifications"]
            if row["created_at_ms"] == PHASE_TWO_NOW_MS
            and row["public_id"] not in explicit_notification_ids
        ],
        "weather_alerts": [
            row
            for row in maintenance_rows["weather_alerts"]
            if row["created_at_ms"] == PHASE_TWO_NOW_MS
        ],
    }

    return {
        "calendar_events": [
            {
                "creator_username": str(row["creator_username"]),
                "description": str(row["description"]),
                "event_on": str(row["event_on"]),
                "garden_id": int(row["garden_id"]),
                "plant_ids": calendar_plants[int(row["id"])],
                "plot_ids": calendar_plots[int(row["id"])],
                "public_id": str(row["public_id"]),
                "title": str(row["title"]),
                "updater_username": str(row["updater_username"]),
            }
            for row in calendar_rows
        ],
        "calendar_subscriptions": [
            {
                "creator_username": str(row["creator_username"]),
                "garden_id": int(row["garden_id"]),
                "label": str(row["label"]),
                "owner_username": str(row["owner_username"]),
                "preset_key": str(row["preset_key"]),
                "public_id": str(row["public_id"]),
                "revoked": row["revoked_at_ms"] is not None,
                "scope": _json_object(row["scope_json"], label="Phase 2 calendar scope"),
                "token_hash_length": int(row["token_hash_length"]),
                "token_hint": str(row["token_hint"]),
            }
            for row in subscription_rows
        ],
        "item_states": [
            {
                "garden_id": int(row["garden_id"]),
                "item_id": str(row["item_id"]),
                "metadata": _json_object(row["metadata_json"], label="Phase 2 attention state"),
                "reason": str(row["reason"]),
                "snoozed_until_ms": (
                    int(row["snoozed_until_ms"]) if row["snoozed_until_ms"] is not None else None
                ),
                "user_state": str(row["user_state"]),
                "username": str(row["username"]),
            }
            for row in item_state_rows
        ],
        "journal": [
            {
                "actor_username": (
                    str(row["actor_username"]) if row["actor_username"] is not None else None
                ),
                "event_type": str(row["event_type"]),
                "garden_id": int(row["garden_id"]),
                "metadata": _json_object(row["metadata_json"], label="Phase 2 journal metadata"),
                "occurred_on": str(row["occurred_on"]),
                "plant_ids": journal_plants[int(row["id"])],
                "plot_ids": journal_plots[int(row["id"])],
                "public_id": str(row["public_id"]),
                "title": str(row["title"]),
            }
            for row in journal_rows
        ],
        "maintenance_created": maintenance_created,
        "notifications": [
            {
                "body": str(row["body"]),
                "clear_reason": (
                    str(row["clear_reason"]) if row["clear_reason"] is not None else None
                ),
                "cleared": row["cleared_at_ms"] is not None,
                "created_at_ms": int(row["created_at_ms"]),
                "dismissed": bool(row["dismissed"]),
                "emailed": row["emailed_at_ms"] is not None,
                "expires_at_ms": (
                    int(row["expires_at_ms"]) if row["expires_at_ms"] is not None else None
                ),
                "garden_id": int(row["garden_id"]),
                "metadata": _json_object(
                    row["metadata_json"] or "{}", label="Phase 2 notification metadata"
                ),
                "notification_subtype": str(row["notification_subtype"] or ""),
                "notification_type": str(row["notification_type"]),
                "public_id": str(row["public_id"]),
                "read": row["read_at_ms"] is not None,
                "severity": str(row["severity"] or "normal"),
                "target_id": str(row["target_id"] or ""),
                "target_type": str(row["target_type"] or ""),
                "title": str(row["title"]),
                "username": str(row["username"]) if row["username"] is not None else None,
            }
            for row in notification_rows
        ],
        "offline_operations": [dict(row) for row in operation_rows],
        "outcomes": [
            {
                "expires_at_ms": int(row["expires_at_ms"]),
                "garden_id": int(row["garden_id"]),
                "metadata": _json_object(row["metadata_json"], label="Phase 2 outcome metadata"),
                "occurred_at_ms": int(row["occurred_at_ms"]),
                "outcome_type": str(row["outcome_type"]),
                "plant_ids": json.loads(str(row["plant_ids_json"])),
                "plot_ids": json.loads(str(row["plot_ids_json"])),
                "provider": str(row["provider"]),
                "public_id": str(row["public_id"]),
                "recovery_action": _json_object(
                    row["recovery_action_json"], label="Phase 2 recovery action"
                ),
                "source_id": str(row["source_id"]),
                "source_public_id": str(row["source_public_id"]),
                "source_type": str(row["source_type"]),
                "target_id": str(row["target_id"]),
                "target_type": str(row["target_type"]),
            }
            for row in outcome_rows
        ],
        "plant_observations": {
            "assignments": [
                {
                    "plant_id": str(row["plt_id"]),
                    "plot_id": str(row["plot_id"]),
                    "seen_growing": bool(row["seen_growing"]),
                    "seen_growing_date": str(row["seen_growing_date"] or ""),
                }
                for row in assignment_observation_rows
            ],
            "plants": [
                {
                    "plant_id": str(row["plt_id"]),
                    "seen_growing": bool(row["seen_growing"]),
                    "seen_growing_date": str(row["seen_growing_date"] or ""),
                }
                for row in plant_observation_rows
            ],
        },
        "preferences": [
            {
                "attention_metadata": _json_object(
                    row["metadata_json"] or "{}", label="Phase 2 attention metadata"
                ),
                "attention_quiet_hours": _json_object(
                    row["attention_quiet_hours_json"] or "{}", label="Phase 2 attention quiet hours"
                ),
                "attention_rules": _json_object(
                    row["attention_rules_json"] or "{}", label="Phase 2 attention rules"
                ),
                "digest_frequency": str(row["digest_frequency"] or ""),
                "email_address": str(row["email_address"] or ""),
                "email_enabled": bool(row["email_enabled"]),
                "in_app_enabled": bool(row["in_app_enabled"]),
                "legacy_quiet_hours": _json_object(
                    row["legacy_quiet_hours_json"] or "{}", label="Phase 2 legacy quiet hours"
                ),
                "notification_rules": _json_object(
                    row["notification_rules_json"] or "{}", label="Phase 2 notification rules"
                ),
                "preset": str(row["preset"] or ""),
                "show_no_action_history": bool(row["show_no_action_history"]),
                "username": str(row["username"]),
            }
            for row in preference_rows
        ],
        "tasks": [
            {
                "completed_at_ms": (
                    int(row["completed_at_ms"]) if row["completed_at_ms"] is not None else None
                ),
                "completed_by_username": (
                    str(row["completed_by_username"])
                    if row["completed_by_username"] is not None
                    else None
                ),
                "created_at_ms": int(row["created_at_ms"]),
                "due_on": str(row["due_on"]),
                "garden_id": int(row["garden_id"]),
                "metadata": _json_object(row["metadata_json"], label="Phase 2 task metadata"),
                "plant_ids": task_plants[int(row["id"])],
                "plot_ids": task_plots[int(row["id"])],
                "public_id": str(row["public_id"]),
                "rule_source": str(row["rule_source"] or ""),
                "snoozed_until": (
                    str(row["snoozed_until"]) if row["snoozed_until"] is not None else None
                ),
                "status": str(row["status"]),
                "task_type": str(row["task_type"]),
                "title": str(row["title"]),
                "window_end_on": (
                    str(row["window_end_on"]) if row["window_end_on"] is not None else None
                ),
                "window_kind": str(row["window_kind"] or ""),
                "window_start_on": (
                    str(row["window_start_on"]) if row["window_start_on"] is not None else None
                ),
                "updated_at_ms": int(row["updated_at_ms"]),
            }
            for row in task_rows
        ],
        "weather_alerts": [
            {
                "alert_type": str(row["alert_type"]),
                "created_at_ms": int(row["created_at_ms"]),
                "dismissed": bool(row["dismissed"]),
                "garden_id": int(row["garden_id"]),
                "id": int(row["id"]),
                "metadata": _json_object(row["metadata_json"], label="Phase 2 weather metadata"),
                "plant_ids": weather_plants[int(row["id"])],
                "severity": str(row["severity"]),
                "title": str(row["title"]),
                "valid_from": str(row["valid_from"]),
                "valid_until": str(row["valid_until"]),
            }
            for row in weather_rows
        ],
    }


def _phase_two_fixture_state(conn, optimization_seed: Any) -> dict[str, Any]:
    runtime = _phase_two_runtime_state(conn, optimization_seed)
    return {
        "calendar": {
            "event_public_id": PHASE_TWO_CALENDAR_PUBLIC_ID,
            "seeded_description": PHASE_TWO_CALENDAR_DESCRIPTION,
            "seeded_event_on": PHASE_TWO_CALENDAR_EVENT_ON,
            "seeded_title": "Phase 2 seeded calendar event",
        },
        "date": PHASE_TWO_DATE,
        "manual_date": PHASE_TWO_MANUAL_DATE,
        "snooze_correction": {
            "default_date": PHASE_TWO_SNOOZE_CORRECTION_DEFAULT_DATE,
            "due_date": PHASE_TWO_SNOOZE_CORRECTION_DUE_DATE,
        },
        "offline": {
            "reschedule_date": PHASE_TWO_OFFLINE_RESCHEDULE_DATE,
            "snooze_date": PHASE_TWO_OFFLINE_SNOOZE_DATE,
        },
        "notification_fixture": {
            "body": "Alpha phase 2 scoped notification.",
            "public_id": PHASE_TWO_NOTIFICATION_PUBLIC_ID,
        },
        "preference_delivery": {
            "eligible": {
                "body": PHASE_TWO_DELIVERY_ELIGIBLE_BODY,
                "public_id": PHASE_TWO_DELIVERY_ELIGIBLE_NOTIFICATION_PUBLIC_ID,
                "severity": "high",
                "title": PHASE_TWO_DELIVERY_ELIGIBLE_TITLE,
            },
            "ineligible": {
                "body": PHASE_TWO_DELIVERY_INELIGIBLE_BODY,
                "public_id": PHASE_TWO_DELIVERY_INELIGIBLE_NOTIFICATION_PUBLIC_ID,
                "severity": "low",
                "title": PHASE_TWO_DELIVERY_INELIGIBLE_TITLE,
            },
            "occurred_at_ms": PHASE_TWO_NOW_MS,
        },
        "notification_public_id": PHASE_TWO_NOTIFICATION_PUBLIC_ID,
        "plant_ids": {key: value[0] for key, value in PHASE_TWO_PLANTS.items()},
        "plant_names": {key: value[1] for key, value in PHASE_TWO_PLANTS.items()},
        "plot_ids": {
            "alpha": PHASE_TWO_ALPHA_PLOT_ID,
            "beta": PHASE_TWO_BETA_PLOT_ID,
            "beta_indoor": PHASE_ONE_BETA_INDOOR_PLOT_ID,
        },
        "seeded_state": runtime,
        "task_ids": PHASE_TWO_TASKS,
        "task_titles": {row["public_id"]: row["title"] for row in runtime["tasks"]},
    }


def _count(conn, table: str) -> int:
    allowed = {
        "attention_outcomes",
        "auth_users",
        "calendar_subscriptions",
        "garden_calendar_events",
        "garden_journal_entries",
        "garden_memberships",
        "garden_map_objects",
        "garden_tasks",
        "gardens",
        "layout_state",
        "notification_events",
        "offline_create_operations",
        "plant_ownership",
        "plants",
        "plot_ownership",
        "plot_plants",
        "plots",
        "user_attention_item_state",
        "user_attention_preferences",
        "user_notification_preferences",
        "weather_alerts",
    }
    if table not in allowed:
        raise RuntimeError(f"Unsupported complete journey snapshot table: {table}")
    row = conn.execute(f'SELECT COUNT(*) AS count FROM "{table}"').fetchone()
    return int(row["count"] if row else 0)


def _domain_table_state(conn) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename <> 'schema_migrations'
          AND tablename NOT IN ('audit_events', 'auth_sessions', 'auth_users')
        ORDER BY tablename
        """
    ).fetchall()
    state: dict[str, dict[str, Any]] = {}
    for row in rows:
        table = str(row["tablename"])
        result = conn.execute(
            sql.SQL(
                """
                SELECT
                    COUNT(*) AS count,
                    md5(COALESCE(
                        string_agg(to_jsonb(row_value)::text, E'\\n'
                            ORDER BY to_jsonb(row_value)::text),
                        ''
                    )) AS digest
                FROM {} AS row_value
                """
            ).format(sql.Identifier(table))
        ).fetchone()
        state[table] = {
            "count": int(result["count"] if result else 0),
            "digest": str(result["digest"] if result else ""),
        }
    return state


def _auth_state(conn) -> dict[str, Any]:
    user = conn.execute(
        "SELECT id, last_login_at FROM auth_users WHERE username = %s",
        (ADMIN_USERNAME,),
    ).fetchone()
    if not user:
        raise RuntimeError("Complete journey fixture administrator is missing")
    session = conn.execute(
        "SELECT COUNT(*) AS count FROM auth_sessions WHERE user_id = %s",
        (int(user["id"]),),
    ).fetchone()
    digest = conn.execute(
        """
        SELECT md5(COALESCE(
            string_agg(
                jsonb_set(
                    to_jsonb(user_value),
                    '{last_login_at}',
                    CASE
                        WHEN username IN (%s, %s, %s, %s, %s) THEN 'null'::jsonb
                        ELSE COALESCE(to_jsonb(last_login_at), 'null'::jsonb)
                    END
                )::text,
                E'\\n' ORDER BY jsonb_set(
                    to_jsonb(user_value),
                    '{last_login_at}',
                    CASE
                        WHEN username IN (%s, %s, %s, %s, %s) THEN 'null'::jsonb
                        ELSE COALESCE(to_jsonb(last_login_at), 'null'::jsonb)
                    END
                )::text
            ),
            ''
        )) AS digest
        FROM auth_users AS user_value
        """,
        (
            ADMIN_USERNAME,
            EDITOR_LOGIN[0],
            VIEWER_LOGIN[0],
            ONBOARDING_LOGIN[0],
            MOBILE_ONBOARDING_LOGIN[0],
            ADMIN_USERNAME,
            EDITOR_LOGIN[0],
            VIEWER_LOGIN[0],
            ONBOARDING_LOGIN[0],
            MOBILE_ONBOARDING_LOGIN[0],
        ),
    ).fetchone()
    session_rows = conn.execute(
        """
        SELECT users.username, COUNT(sessions.token_hash) AS count
        FROM auth_users AS users
        LEFT JOIN auth_sessions AS sessions ON sessions.user_id = users.id
        GROUP BY users.username
        ORDER BY users.username
        """
    ).fetchall()
    invalid_session = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM auth_sessions
        WHERE expires_at_ms <= created_at_ms
           OR expires_at_ms - created_at_ms <> 43200000
           OR last_seen_at_ms < created_at_ms
           OR length(token_hash) <> 64
           OR (reauthenticated_at_ms <> 0 AND reauthenticated_at_ms < created_at_ms)
           OR (mfa_authenticated_at_ms <> 0 AND mfa_authenticated_at_ms < created_at_ms)
        """
    ).fetchone()
    invalid_reasons = conn.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE expires_at_ms <= created_at_ms) AS nonpositive_lifetime,
            COUNT(*) FILTER (WHERE expires_at_ms - created_at_ms <> 43200000) AS lifetime_not_12h,
            COUNT(*) FILTER (WHERE last_seen_at_ms < created_at_ms) AS last_seen_before_created,
            COUNT(*) FILTER (WHERE length(token_hash) <> 64) AS invalid_token_hash_length,
            COUNT(*) FILTER (
                WHERE reauthenticated_at_ms <> 0 AND reauthenticated_at_ms < created_at_ms
            ) AS reauthenticated_before_created,
            COUNT(*) FILTER (
                WHERE mfa_authenticated_at_ms <> 0 AND mfa_authenticated_at_ms < created_at_ms
            ) AS mfa_before_created
        FROM auth_sessions
        """
    ).fetchone()
    return {
        "admin_last_login_at": (
            str(user["last_login_at"]) if user["last_login_at"] is not None else None
        ),
        "admin_session_count": int(session["count"] if session else 0),
        "invalid_session_count": int(invalid_session["count"] if invalid_session else 0),
        "invalid_session_reasons": {
            key: int(invalid_reasons[key] if invalid_reasons else 0)
            for key in (
                "invalid_token_hash_length",
                "last_seen_before_created",
                "lifetime_not_12h",
                "mfa_before_created",
                "nonpositive_lifetime",
                "reauthenticated_before_created",
            )
        },
        "session_user_counts": {str(row["username"]): int(row["count"]) for row in session_rows},
        "users_expected_digest": str(digest["digest"] if digest else ""),
    }


def _audit_state(conn) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_count,
            COUNT(*) FILTER (
                WHERE method = 'POST'
                  AND path = '/api/auth/login'
                  AND status_code = 200
                  AND actor_user_id IS NULL
                  AND actor_username = 'anonymous'
                  AND actor_role = 'anonymous'
                  AND actor_auth_type = 'none'
                  AND garden_id IS NULL
                  AND remote_host IN ('127.0.0.1', '::1')
                  AND detail = ''
            ) AS expected_login_count,
            COUNT(*) FILTER (
                WHERE method = 'POST'
                  AND path = '/api/snapshots'
                  AND status_code = 201
                  AND actor_username = %s
            ) AS expected_phase_one_snapshot_count
        FROM audit_events
        """,
        (ADMIN_USERNAME,),
    ).fetchone()
    total = int(row["total_count"] if row else 0)
    expected = int(row["expected_login_count"] if row else 0)
    event_rows = conn.execute(
        """
        SELECT method, path, status_code, COUNT(*) AS count
        FROM audit_events
        GROUP BY method, path, status_code
        ORDER BY method, path, status_code
        """
    ).fetchall()

    def normalized_path(path: str) -> str:
        value = re.sub(r"/gardens/\d+", "/gardens/{garden_id}", path)
        value = re.sub(r"/(?:mapobj|mapunit|snap)_[a-z0-9]+", "/{public_id}", value)
        value = re.sub(r"/saved-views/\d+", "/saved-views/{saved_view_id}", value)
        value = value.replace("/PLT-001", "/{created_plant_id}")
        return value

    record_rows = conn.execute(
        """
        SELECT id, occurred_at_ms, actor_username, actor_role, actor_auth_type,
               garden_id, method, path, status_code
        FROM audit_events
        ORDER BY id
        """
    ).fetchall()

    normalized_events: dict[tuple[str, str, int], int] = {}
    for event in event_rows:
        key = (
            str(event["method"]),
            normalized_path(str(event["path"])),
            int(event["status_code"]),
        )
        normalized_events[key] = normalized_events.get(key, 0) + int(event["count"])

    return {
        "events": [
            {"count": count, "method": method, "path": path, "status_code": status_code}
            for (method, path, status_code), count in sorted(normalized_events.items())
        ],
        "expected_login_count": expected,
        "records": [
            {
                "actor_auth_type": str(record["actor_auth_type"]),
                "actor_role": str(record["actor_role"]),
                "actor_username": str(record["actor_username"]),
                "garden_id": int(record["garden_id"]) if record["garden_id"] is not None else None,
                "id": int(record["id"]),
                "method": str(record["method"]),
                "occurred_at_ms": int(record["occurred_at_ms"]),
                "path": normalized_path(str(record["path"])),
                "status_code": int(record["status_code"]),
            }
            for record in record_rows
        ],
        "total_count": total,
        "expected_phase_one_snapshot_count": int(
            row["expected_phase_one_snapshot_count"] if row else 0
        ),
    }


def _snapshot(conn, optimization_seed: Any) -> dict[str, Any]:
    garden_rows = conn.execute(
        "SELECT id, slug, name FROM gardens WHERE slug = ANY(%s) ORDER BY slug",
        ([optimization_seed.GARDEN_A_SLUG, optimization_seed.GARDEN_B_SLUG],),
    ).fetchall()
    gardens_by_slug = {str(row["slug"]): row for row in garden_rows}

    def garden_payload(spec: dict[str, str], notification_title: str) -> dict[str, Any]:
        row = gardens_by_slug.get(spec["slug"])
        if not row:
            raise RuntimeError(f"Missing complete journey garden {spec['slug']}")
        return {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "notification_title": notification_title,
            "object_label": spec["object_name"],
            "object_public_id": spec["object_id"],
            "plot_id": spec["plot_id"],
            "plant_name": spec["plant_name"],
            "slug": spec["slug"],
        }

    tables = (
        "attention_outcomes",
        "auth_users",
        "calendar_subscriptions",
        "garden_calendar_events",
        "garden_journal_entries",
        "garden_memberships",
        "garden_map_objects",
        "garden_tasks",
        "gardens",
        "layout_state",
        "notification_events",
        "offline_create_operations",
        "plant_ownership",
        "plants",
        "plot_ownership",
        "plot_plants",
        "plots",
        "user_attention_item_state",
        "user_attention_preferences",
        "user_notification_preferences",
        "weather_alerts",
    )
    return {
        "database_snapshot": {
            "audit_state": _audit_state(conn),
            "auth_state": _auth_state(conn),
            "domain_counts": {table: _count(conn, table) for table in tables},
            "domain_tables": _domain_table_state(conn),
            "phase_one_state": _phase_one_runtime_state(conn, optimization_seed),
            "phase_two_state": _phase_two_runtime_state(conn, optimization_seed),
        },
        "gardens": {
            "alpha": garden_payload(
                optimization_seed._GARDEN_SPECS[0],
                optimization_seed.GARDEN_A_NOTIFICATION,
            ),
            "beta": garden_payload(
                optimization_seed._GARDEN_SPECS[1],
                optimization_seed.GARDEN_B_NOTIFICATION,
            ),
        },
        "clock": _frozen_attention_clock(),
        "git": _git_state(),
        "phase_one": _phase_one_fixture_state(conn, optimization_seed),
        "phase_two": _phase_two_fixture_state(conn, optimization_seed),
        "roles": {
            "admin": ADMIN_USERNAME,
            "editor": EDITOR_LOGIN[0],
            "onboarding": ONBOARDING_LOGIN[0],
            "onboarding_mobile": MOBILE_ONBOARDING_LOGIN[0],
            "viewer": VIEWER_LOGIN[0],
        },
        "suite": "complete-journeys-e2e",
    }


def _write_json_exclusive(output_path: Path, payload: dict[str, Any]) -> None:
    artifact_raw = os.environ.get("GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR", "")
    if not artifact_raw:
        raise RuntimeError("Complete journey artifact directory is required")
    artifact_dir = Path(artifact_raw).resolve(strict=True)
    if (
        output_path.name != "fixture.json"
        or output_path.parent.resolve(strict=True) != artifact_dir
    ):
        raise RuntimeError(
            "Complete journey fixture output must be fixture.json in the artifact directory"
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(output_path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"), sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


def _phase_two_maintenance_semantic_rows(conn, garden_id: int) -> dict[str, list[dict[str, Any]]]:
    """Capture the full semantic rows maintenance may create or alter for one garden."""
    task_rows = conn.execute(
        """
        SELECT id, public_id, garden_id, task_type, title, status, severity, due_on,
               snoozed_until, rule_source, metadata_json, completed_at_ms,
               created_at_ms, updated_at_ms
        FROM garden_tasks
        WHERE garden_id = %s
        ORDER BY id
        """,
        (garden_id,),
    ).fetchall()
    task_ids = [int(row["id"]) for row in task_rows]
    task_plants: dict[int, list[str]] = {task_id: [] for task_id in task_ids}
    task_plots: dict[int, list[str]] = {task_id: [] for task_id in task_ids}
    if task_ids:
        for row in conn.execute(
            """
            SELECT task_id, plt_id
            FROM garden_task_plants
            WHERE task_id = ANY(%s)
            ORDER BY task_id, plt_id
            """,
            (task_ids,),
        ).fetchall():
            task_plants[int(row["task_id"])].append(str(row["plt_id"]))
        for row in conn.execute(
            """
            SELECT task_id, plot_id
            FROM garden_task_plots
            WHERE task_id = ANY(%s)
            ORDER BY task_id, plot_id
            """,
            (task_ids,),
        ).fetchall():
            task_plots[int(row["task_id"])].append(str(row["plot_id"]))

    notification_rows = conn.execute(
        """
        SELECT event.id, event.public_id, event.garden_id, users.username,
               event.notification_type, event.notification_subtype, event.severity,
               event.title, event.body, event.target_type, event.target_id,
               event.metadata_json, event.dismissed, event.read_at_ms, event.emailed_at_ms,
               event.cleared_at_ms, event.clear_reason, event.created_at_ms, event.expires_at_ms
        FROM notification_events event
        LEFT JOIN auth_users users ON users.id = event.user_id
        WHERE event.garden_id = %s
        ORDER BY event.id
        """,
        (garden_id,),
    ).fetchall()
    weather_rows = conn.execute(
        """
        SELECT id, garden_id, alert_type, severity, title, description, valid_from,
               valid_until, metadata_json, dismissed, created_at_ms
        FROM weather_alerts
        WHERE garden_id = %s
        ORDER BY id
        """,
        (garden_id,),
    ).fetchall()
    weather_ids = [int(row["id"]) for row in weather_rows]
    weather_plants: dict[int, list[str]] = {alert_id: [] for alert_id in weather_ids}
    if weather_ids:
        for row in conn.execute(
            """
            SELECT alert_id, plt_id
            FROM weather_alert_plants
            WHERE alert_id = ANY(%s)
            ORDER BY alert_id, plt_id
            """,
            (weather_ids,),
        ).fetchall():
            weather_plants[int(row["alert_id"])].append(str(row["plt_id"]))

    return {
        "tasks": [
            {
                "created_at_ms": int(row["created_at_ms"]),
                "completed_at_ms": (
                    int(row["completed_at_ms"]) if row["completed_at_ms"] is not None else None
                ),
                "due_on": str(row["due_on"]),
                "garden_id": int(row["garden_id"]),
                "metadata": _json_object(row["metadata_json"], label="maintenance task metadata"),
                "plant_ids": task_plants[int(row["id"])],
                "plot_ids": task_plots[int(row["id"])],
                "public_id": str(row["public_id"]),
                "row_id": int(row["id"]),
                "rule_source": str(row["rule_source"] or ""),
                "severity": str(row["severity"]),
                "snoozed_until": str(row["snoozed_until"])
                if row["snoozed_until"] is not None
                else None,
                "status": str(row["status"]),
                "task_type": str(row["task_type"]),
                "title": str(row["title"]),
                "updated_at_ms": int(row["updated_at_ms"]),
            }
            for row in task_rows
        ],
        "notifications": [
            {
                "body": str(row["body"]),
                "clear_reason": str(row["clear_reason"] or ""),
                "cleared_at_ms": int(row["cleared_at_ms"])
                if row["cleared_at_ms"] is not None
                else None,
                "created_at_ms": int(row["created_at_ms"]),
                "dismissed": bool(row["dismissed"]),
                "emailed_at_ms": int(row["emailed_at_ms"])
                if row["emailed_at_ms"] is not None
                else None,
                "expires_at_ms": int(row["expires_at_ms"])
                if row["expires_at_ms"] is not None
                else None,
                "garden_id": int(row["garden_id"]),
                "metadata": _json_object(
                    row["metadata_json"] or "{}", label="maintenance notification metadata"
                ),
                "notification_subtype": str(row["notification_subtype"] or ""),
                "notification_type": str(row["notification_type"]),
                "public_id": str(row["public_id"]),
                "read_at_ms": int(row["read_at_ms"]) if row["read_at_ms"] is not None else None,
                "row_id": int(row["id"]),
                "severity": str(row["severity"] or "normal"),
                "target_id": str(row["target_id"] or ""),
                "target_type": str(row["target_type"] or ""),
                "title": str(row["title"]),
                "username": str(row["username"]) if row["username"] is not None else None,
            }
            for row in notification_rows
        ],
        "weather_alerts": [
            {
                "alert_type": str(row["alert_type"]),
                "created_at_ms": int(row["created_at_ms"]),
                "description": str(row["description"]),
                "dismissed": bool(row["dismissed"]),
                "garden_id": int(row["garden_id"]),
                "metadata": _json_object(
                    row["metadata_json"], label="maintenance weather metadata"
                ),
                "plant_ids": weather_plants[int(row["id"])],
                "row_id": int(row["id"]),
                "severity": str(row["severity"]),
                "title": str(row["title"]),
                "valid_from": str(row["valid_from"]),
                "valid_until": str(row["valid_until"]),
            }
            for row in weather_rows
        ],
    }


def _phase_two_maintenance_delta(
    before: dict[str, list[dict[str, Any]]], after: dict[str, list[dict[str, Any]]]
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Separate every maintenance-created row from every maintenance mutation."""
    delta: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for table in ("tasks", "notifications", "weather_alerts"):
        before_by_id = {int(row["row_id"]): row for row in before[table]}
        after_by_id = {int(row["row_id"]): row for row in after[table]}
        if not set(before_by_id).issubset(after_by_id):
            raise RuntimeError(f"Phase 2 maintenance deleted existing {table}")
        created = [after_by_id[row_id] for row_id in sorted(set(after_by_id) - set(before_by_id))]
        mutated = [
            {
                "after": after_by_id[row_id],
                "before": before_by_id[row_id],
            }
            for row_id in sorted(before_by_id)
            if json.dumps(before_by_id[row_id], sort_keys=True, separators=(",", ":"))
            != json.dumps(after_by_id[row_id], sort_keys=True, separators=(",", ":"))
        ]
        delta[table] = {"created": created, "mutated_existing": mutated}
    return delta


def _run_phase_two_maintenance(conn, optimization_seed: Any) -> dict[str, Any]:
    from gardenops.services.notification_service import run_notification_maintenance_for_garden

    alpha = conn.execute(
        "SELECT id FROM gardens WHERE slug = %s",
        (optimization_seed.GARDEN_A_SLUG,),
    ).fetchone()
    if not alpha:
        raise RuntimeError("Complete journey Phase 2 maintenance garden is missing")
    deliveries: list[dict[str, int]] = []

    def record_delivery(recipient: str, subject: str, body: str) -> None:
        deliveries.append(
            {
                "body_length": len(body),
                "recipient_length": len(recipient),
                "subject_length": len(subject),
            }
        )

    before = _phase_two_maintenance_semantic_rows(conn, int(alpha["id"]))
    summary = run_notification_maintenance_for_garden(
        conn,
        garden_id=int(alpha["id"]),
        email_sender=record_delivery,
        now_ms=PHASE_TWO_NOW_MS,
    )
    after = _phase_two_maintenance_semantic_rows(conn, int(alpha["id"]))
    maintenance_created = _phase_two_maintenance_delta(before, after)
    conn.commit()
    return {
        "delivery_count": len(deliveries),
        "deliveries": deliveries,
        "garden_id": int(alpha["id"]),
        "maintenance_semantic_state": {
            "frozen_now_ms": PHASE_TWO_NOW_MS,
            "maintenance_created": maintenance_created,
        },
        "summary": summary,
    }


def _run_phase_two_preference_delivery(conn, optimization_seed: Any) -> dict[str, Any]:
    """Create post-save fixtures and run the deterministic delivery boundary once."""
    from gardenops.services.notification_service import (
        get_unread_count,
        run_notification_maintenance_for_garden,
    )

    target = conn.execute(
        """
        SELECT garden.id AS garden_id, users.id AS user_id,
               legacy.email_enabled, legacy.digest_frequency,
               legacy.rules_json AS notification_rules_json,
               attention.rules_json AS attention_rules_json
        FROM gardens garden
        JOIN auth_users users ON users.username = %s
        JOIN user_notification_preferences legacy ON legacy.user_id = users.id
        JOIN user_attention_preferences attention ON attention.user_id = users.id
        WHERE garden.slug = %s
        """,
        (ADMIN_USERNAME, optimization_seed.GARDEN_A_SLUG),
    ).fetchone()
    if not target:
        raise RuntimeError("Complete journey Phase 2 preference delivery target is missing")
    notification_rules = _json_object(
        target["notification_rules_json"], label="Phase 2 saved notification rules"
    )
    attention_rules = _json_object(
        target["attention_rules_json"], label="Phase 2 saved attention rules"
    )
    issue_notification_rule = notification_rules.get("issue_created")
    issue_due_attention_rule = attention_rules.get("issue_follow_up_due")
    issue_overdue_attention_rule = attention_rules.get("issue_follow_up_overdue")
    expected_issue_attention_rule = {
        "digest": True,
        "inbox": True,
        "min_severity": "normal",
        "panel": True,
    }
    if (
        not bool(target["email_enabled"])
        or str(target["digest_frequency"]) != "weekly"
        or not isinstance(issue_notification_rule, dict)
        or issue_notification_rule
        != {
            "email_enabled": True,
            "in_app_enabled": True,
            "min_severity": "normal",
        }
        or issue_due_attention_rule != expected_issue_attention_rule
        or issue_overdue_attention_rule != expected_issue_attention_rule
    ):
        raise RuntimeError("Phase 2 browser did not save the required delivery preferences")

    existing = conn.execute(
        "SELECT public_id FROM notification_events WHERE public_id = ANY(%s)",
        (
            [
                PHASE_TWO_DELIVERY_ELIGIBLE_NOTIFICATION_PUBLIC_ID,
                PHASE_TWO_DELIVERY_INELIGIBLE_NOTIFICATION_PUBLIC_ID,
            ],
        ),
    ).fetchall()
    if existing:
        raise RuntimeError("Phase 2 preference delivery fixtures were created more than once")
    created_at_ms = PHASE_TWO_NOW_MS
    expires_at_ms = created_at_ms + 7 * 86_400_000
    for public_id, severity, title, body in (
        (
            PHASE_TWO_DELIVERY_ELIGIBLE_NOTIFICATION_PUBLIC_ID,
            "high",
            PHASE_TWO_DELIVERY_ELIGIBLE_TITLE,
            PHASE_TWO_DELIVERY_ELIGIBLE_BODY,
        ),
        (
            PHASE_TWO_DELIVERY_INELIGIBLE_NOTIFICATION_PUBLIC_ID,
            "low",
            PHASE_TWO_DELIVERY_INELIGIBLE_TITLE,
            PHASE_TWO_DELIVERY_INELIGIBLE_BODY,
        ),
    ):
        conn.execute(
            """
            INSERT INTO notification_events (
                public_id, garden_id, user_id, notification_type, notification_subtype,
                severity, title, body, target_type, target_id, metadata_json,
                dismissed, created_at_ms, expires_at_ms
            )
            VALUES (%s, %s, %s, 'issue_created', NULL, %s, %s, %s, 'issue', %s,
                    %s, 0, %s, %s)
            """,
            (
                public_id,
                int(target["garden_id"]),
                int(target["user_id"]),
                severity,
                title,
                body,
                public_id,
                json.dumps(
                    {"fixture": "complete_journeys_phase_2", "preference_delivery": True},
                    sort_keys=True,
                ),
                created_at_ms,
                expires_at_ms,
            ),
        )

    deliveries: list[dict[str, int]] = []

    def record_delivery(recipient: str, subject: str, body: str) -> None:
        deliveries.append(
            {
                "body_length": len(body),
                "recipient_length": len(recipient),
                "subject_length": len(subject),
            }
        )

    summary = run_notification_maintenance_for_garden(
        conn,
        garden_id=int(target["garden_id"]),
        email_sender=record_delivery,
        now_ms=PHASE_TWO_NOW_MS,
    )
    conn.commit()
    delivery_rows = conn.execute(
        """
        SELECT public_id, notification_type, notification_subtype, severity,
               target_type, target_id, created_at_ms, emailed_at_ms,
               read_at_ms, dismissed, cleared_at_ms
        FROM notification_events
        WHERE garden_id = %s
          AND user_id = %s
          AND emailed_at_ms = %s
        ORDER BY public_id
        """,
        (int(target["garden_id"]), int(target["user_id"]), PHASE_TWO_NOW_MS),
    ).fetchall()
    fixture_rows = conn.execute(
        """
        SELECT public_id, notification_type, notification_subtype, severity,
               target_type, target_id, created_at_ms, emailed_at_ms,
               read_at_ms, dismissed, cleared_at_ms
        FROM notification_events
        WHERE public_id = ANY(%s)
        ORDER BY public_id
        """,
        (
            [
                PHASE_TWO_DELIVERY_ELIGIBLE_NOTIFICATION_PUBLIC_ID,
                PHASE_TWO_DELIVERY_INELIGIBLE_NOTIFICATION_PUBLIC_ID,
            ],
        ),
    ).fetchall()
    if len(fixture_rows) != 2:
        raise RuntimeError("Phase 2 preference delivery fixture rows are incomplete")
    return {
        "delivery_badge_count": get_unread_count(
            conn,
            int(target["garden_id"]),
            int(target["user_id"]),
            now_ms=PHASE_TWO_NOW_MS,
        ),
        "delivery_count": len(deliveries),
        "deliveries": deliveries,
        "delivery_notifications": [dict(row) for row in delivery_rows],
        "garden_id": int(target["garden_id"]),
        "preference_delivery_rows": [dict(row) for row in fixture_rows],
        "summary": summary,
        "triggered_at_ms": PHASE_TWO_NOW_MS,
    }


def _prepare_phase_two(conn, optimization_seed: Any) -> dict[str, Any]:
    gardens = {
        str(row["slug"]): int(row["id"])
        for row in conn.execute(
            "SELECT id, slug FROM gardens WHERE slug = ANY(%s)",
            ([optimization_seed.GARDEN_A_SLUG, optimization_seed.GARDEN_B_SLUG],),
        ).fetchall()
    }
    if len(gardens) != 2:
        raise RuntimeError("Complete journey Phase 2 preparation gardens are missing")
    evidence = _reset_phase_two_weather_cache(
        conn,
        alpha_id=gardens[optimization_seed.GARDEN_A_SLUG],
        beta_id=gardens[optimization_seed.GARDEN_B_SLUG],
    )
    conn.commit()
    return evidence


def main() -> None:
    _require_child_environment()
    _configure_reused_seed_guard()
    from scripts import seed_optimization_journeys_e2e as optimization_seed

    database_url = os.environ.get("DATABASE_URL", "")
    optimization_seed.require_optimization_journeys_e2e_database(database_url)
    snapshot_only = sys.argv[1:] == ["--snapshot"]
    prepare_phase_two = sys.argv[1:] == ["--prepare-phase-two"]
    phase_two_maintenance = sys.argv[1:] == ["--phase-two-maintenance"]
    phase_two_preference_delivery = sys.argv[1:] == ["--phase-two-preference-delivery"]
    output_path = Path(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == "--output" else None
    if (
        sys.argv[1:]
        and not snapshot_only
        and not prepare_phase_two
        and not phase_two_maintenance
        and not phase_two_preference_delivery
        and output_path is None
    ):
        raise SystemExit(
            "Usage: seed_complete_journeys_e2e.py "
            "[--snapshot | --prepare-phase-two | --phase-two-maintenance "
            "| --phase-two-preference-delivery | --output PATH]"
        )

    conn = None
    try:
        conn = get_db()
        try:
            optimization_seed.verify_optimization_journeys_e2e_database_marker(conn)
            if prepare_phase_two:
                print(
                    json.dumps(
                        _prepare_phase_two(conn, optimization_seed),
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
                return
            if phase_two_maintenance:
                print(
                    json.dumps(
                        _run_phase_two_maintenance(conn, optimization_seed),
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
                return
            if phase_two_preference_delivery:
                print(
                    json.dumps(
                        _run_phase_two_preference_delivery(conn, optimization_seed),
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
                return
            if not snapshot_only:
                optimization_seed.seed(conn)
                conn.execute(
                    "DELETE FROM gardens WHERE slug = %s",
                    (optimization_seed.DELETE_TARGET_SLUG,),
                )
                _seed_phase_one_fixtures(conn, optimization_seed)
                garden_rows = conn.execute(
                    "SELECT id, slug FROM gardens WHERE slug = ANY(%s) ORDER BY id",
                    (
                        [
                            optimization_seed.GARDEN_A_SLUG,
                            optimization_seed.GARDEN_B_SLUG,
                            PHASE_ONE_LARGE_GARDEN_SLUG,
                        ],
                    ),
                ).fetchall()
                garden_ids_by_slug = {str(row["slug"]): int(row["id"]) for row in garden_rows}
                _add_role_fixtures(
                    conn,
                    garden_ids=list(garden_ids_by_slug.values()),
                    viewer_garden_ids={
                        "alpha": garden_ids_by_slug[optimization_seed.GARDEN_A_SLUG],
                        "beta": garden_ids_by_slug[optimization_seed.GARDEN_B_SLUG],
                    },
                )
                _seed_phase_two_fixtures(conn, optimization_seed)
                conn.commit()
            result = _snapshot(conn, optimization_seed)
            if output_path is not None:
                _write_json_exclusive(output_path, result)
            else:
                print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        except Exception:
            conn.rollback()
            raise
    finally:
        if conn is not None:
            return_db(conn)
        close_pool()


if __name__ == "__main__":
    main()
