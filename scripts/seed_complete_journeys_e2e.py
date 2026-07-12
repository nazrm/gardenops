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
PHASE_ONE_MAP_UNIT_ID = "mapunit_complete_phase_one_alpha_bench"
PHASE_ONE_MAP_UNIT_NAME = "Complete Phase One Basil Bench"
PHASE_ONE_SAVED_VIEW_LABEL = "Complete Phase One Basil View"
PHASE_ONE_MOBILE_SNAPSHOT_NAME = "Complete Phase One Mobile Action Snapshot"
PHASE_ONE_BROWSER_PLANT_ID = "PLT-001"
PHASE_ONE_BROWSER_SAVED_VIEW_LABEL = "Phase 1 Browser Plant View"
PHASE_ONE_DESKTOP_ONBOARDING_GARDEN_NAME = "Phase 1 Onboarding Garden"
PHASE_ONE_MOBILE_ONBOARDING_GARDEN_NAME = "Phase 1 Mobile Onboarding Garden"
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


def _insert_user(conn, *, username: str, password: str, role: str) -> int:
    row = conn.execute(
        """
        INSERT INTO auth_users (
            username, password_hash, password_auth_disabled, passkey_user_handle,
            role, is_active, must_change_password, subscription_tier
        )
        VALUES (%s, %s, 0, %s, %s, 1, 0, 'home')
        RETURNING id
        """,
        (username, hash_password(password), generate_passkey_user_handle(), role),
    ).fetchone()
    if not row:
        raise RuntimeError(f"Complete journey E2E failed to create {role} user")
    return int(row["id"])


def _add_role_fixtures(conn, *, garden_ids: list[int]) -> None:
    editor_id = _insert_user(
        conn,
        username=EDITOR_LOGIN[0],
        password=EDITOR_LOGIN[1],
        role="editor",
    )
    viewer_id = _insert_user(
        conn,
        username=VIEWER_LOGIN[0],
        password=VIEWER_LOGIN[1],
        role="viewer",
    )
    for garden_id in garden_ids:
        for user_id, role in ((editor_id, "editor"), (viewer_id, "viewer")):
            conn.execute(
                "INSERT INTO garden_memberships (garden_id, user_id, role) VALUES (%s, %s, %s)",
                (garden_id, user_id, role),
            )


def _seed_phase_one_fixtures(conn, optimization_seed: Any) -> None:
    """Add deterministic Phase 1 records after the reusable base seed."""
    admin = conn.execute(
        "SELECT id FROM auth_users WHERE username = %s", (ADMIN_USERNAME,)
    ).fetchone()
    alpha = conn.execute(
        "SELECT id FROM gardens WHERE slug = %s", (optimization_seed.GARDEN_A_SLUG,)
    ).fetchone()
    if not admin or not alpha:
        raise RuntimeError("Complete journey Phase 1 base fixtures are missing")
    admin_id = int(admin["id"])
    alpha_id = int(alpha["id"])

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


def _phase_one_fixture_state(conn, optimization_seed: Any) -> dict[str, Any]:
    alpha = conn.execute(
        "SELECT id FROM gardens WHERE slug = %s", (optimization_seed.GARDEN_A_SLUG,)
    ).fetchone()
    large = conn.execute(
        "SELECT id, name FROM gardens WHERE slug = %s", (PHASE_ONE_LARGE_GARDEN_SLUG,)
    ).fetchone()
    snapshot = conn.execute(
        "SELECT COUNT(*) AS count FROM layout_snapshots WHERE name = %s",
        (PHASE_ONE_MOBILE_SNAPSHOT_NAME,),
    ).fetchone()
    if not alpha or not large:
        raise RuntimeError("Complete journey Phase 1 base gardens are missing")
    alpha_id = int(alpha["id"])
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
        },
        "onboarding": {
            "desktop_garden_name": PHASE_ONE_DESKTOP_ONBOARDING_GARDEN_NAME,
            "desktop_username": ONBOARDING_LOGIN[0],
            "mobile_garden_name": PHASE_ONE_MOBILE_ONBOARDING_GARDEN_NAME,
            "mobile_username": MOBILE_ONBOARDING_LOGIN[0],
        },
        "saved_view": {
            "garden_id": alpha_id,
            "label": PHASE_ONE_SAVED_VIEW_LABEL,
            "owner_username": ADMIN_USERNAME,
            "view_type": "plants",
        },
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
            g.onboarding_complete,
            owner.username AS owner_username,
            member.username AS membership_username,
            gm.role AS membership_role
        FROM gardens g
        LEFT JOIN auth_users owner ON owner.id = g.owner_user_id
        LEFT JOIN garden_memberships gm ON gm.garden_id = g.id
        LEFT JOIN auth_users member ON member.id = gm.user_id
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
        SELECT garden_id, public_id
        FROM layout_snapshots
        WHERE name = %s
        ORDER BY garden_id, public_id
        """,
        (PHASE_ONE_MOBILE_SNAPSHOT_NAME,),
    ).fetchall()
    mobile_harvest_rows = conn.execute(
        """
        SELECT harvest.garden_id, actor.username AS actor_username
        FROM harvest_entries harvest
        LEFT JOIN auth_users actor ON actor.id = harvest.actor_user_id
        WHERE harvest.notes = 'Phase 1 mobile quick action'
        ORDER BY harvest.id
        """
    ).fetchall()
    mobile_journal_rows = conn.execute(
        """
        SELECT journal.garden_id, actor.username AS actor_username
        FROM garden_journal_entries journal
        LEFT JOIN auth_users actor ON actor.id = journal.actor_user_id
        WHERE journal.notes = 'Phase 1 mobile quick action'
        ORDER BY journal.id
        """
    ).fetchall()
    counts = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM plants WHERE name LIKE 'Phase 1 Browser Mint%%') AS temp_plants,
            (SELECT COUNT(*) FROM user_saved_views
             WHERE label = 'Phase 1 Browser Plant View') AS temp_views,
            (SELECT COUNT(*) FROM garden_map_objects
             WHERE name LIKE 'Phase 1 %%') AS temp_map_objects,
            (SELECT COUNT(*) FROM layout_snapshots WHERE name = %s) AS mobile_snapshots,
            (SELECT COUNT(*) FROM harvest_entries
             WHERE notes = 'Phase 1 mobile quick action') AS harvests,
            (SELECT COUNT(*) FROM garden_journal_entries
             WHERE notes = 'Phase 1 mobile quick action') AS journals,
            (SELECT COUNT(*) FROM garden_map_object_units
             WHERE garden_id = %s) AS alpha_map_unit_count
        """,
        (PHASE_ONE_MOBILE_SNAPSHOT_NAME, alpha_id),
    ).fetchone()
    lifecycle = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM plants WHERE plt_id = %s) AS plant_rows,
            (SELECT COUNT(*) FROM plant_ownership WHERE plt_id = %s) AS plant_ownership_rows,
            (SELECT COUNT(*) FROM plot_plants WHERE plt_id = %s) AS plant_assignment_rows,
            (SELECT COUNT(*) FROM user_saved_views WHERE label = %s) AS saved_view_rows,
            (SELECT COUNT(*) FROM user_saved_views
             WHERE label = %s AND garden_id = %s) AS saved_view_alpha_rows
        """,
        (
            PHASE_ONE_BROWSER_PLANT_ID,
            PHASE_ONE_BROWSER_PLANT_ID,
            PHASE_ONE_BROWSER_PLANT_ID,
            PHASE_ONE_BROWSER_SAVED_VIEW_LABEL,
            PHASE_ONE_BROWSER_SAVED_VIEW_LABEL,
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
                WHERE notes = 'Phase 1 mobile quick action' AND garden_id = %s
            ) AS mobile_harvest_in_beta,
            (
                SELECT COUNT(*)
                FROM garden_journal_entries
                WHERE notes = 'Phase 1 mobile quick action' AND garden_id = %s
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
            beta_id,
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
    app_setting_rows = conn.execute("SELECT key FROM app_settings ORDER BY key").fetchall()
    onboarding_targets: dict[int, dict[str, Any]] = {}
    for row in onboarding_target_rows:
        garden_id = int(row["id"])
        target = onboarding_targets.setdefault(
            garden_id,
            {
                "id": garden_id,
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
    return {
        "alpha_address": str(alpha["address"] or ""),
        "alpha_id": alpha_id,
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
        "app_setting_keys": [str(row["key"]) for row in app_setting_rows],
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
            {"garden_id": int(row["garden_id"]), "public_id": str(row["public_id"])}
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
        "onboarding_target_gardens": sorted(
            onboarding_targets.values(), key=lambda garden: (str(garden["name"]), int(garden["id"]))
        ),
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
        "alpha_map_unit_count": int(counts["alpha_map_unit_count"]),
        "temp_map_object_count": int(counts["temp_map_objects"]),
        "temp_plant_count": int(counts["temp_plants"]),
        "temp_saved_view_count": int(counts["temp_views"]),
    }


def _count(conn, table: str) -> int:
    allowed = {
        "auth_users",
        "garden_memberships",
        "garden_map_objects",
        "garden_tasks",
        "gardens",
        "layout_state",
        "notification_events",
        "plant_ownership",
        "plants",
        "plot_ownership",
        "plot_plants",
        "plots",
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
            ) AS expected_phase_one_snapshot_count,
            COUNT(*) FILTER (
                WHERE method = 'POST'
                  AND path LIKE '/api/gardens/%%/map-objects'
                  AND status_code = 403
                  AND actor_username = %s
            ) AS expected_phase_one_viewer_denial_count
        FROM audit_events
        """,
        (ADMIN_USERNAME, VIEWER_LOGIN[0]),
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
        "total_count": total,
        "expected_phase_one_snapshot_count": int(
            row["expected_phase_one_snapshot_count"] if row else 0
        ),
        "expected_phase_one_viewer_denial_count": int(
            row["expected_phase_one_viewer_denial_count"] if row else 0
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
        "auth_users",
        "garden_memberships",
        "garden_map_objects",
        "garden_tasks",
        "gardens",
        "layout_state",
        "notification_events",
        "plant_ownership",
        "plants",
        "plot_ownership",
        "plot_plants",
        "plots",
        "weather_alerts",
    )
    return {
        "database_snapshot": {
            "audit_state": _audit_state(conn),
            "auth_state": _auth_state(conn),
            "domain_counts": {table: _count(conn, table) for table in tables},
            "domain_tables": _domain_table_state(conn),
            "phase_one_state": _phase_one_runtime_state(conn, optimization_seed),
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


def main() -> None:
    _require_child_environment()
    _configure_reused_seed_guard()
    from scripts import seed_optimization_journeys_e2e as optimization_seed

    database_url = os.environ.get("DATABASE_URL", "")
    optimization_seed.require_optimization_journeys_e2e_database(database_url)
    snapshot_only = sys.argv[1:] == ["--snapshot"]
    output_path = Path(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == "--output" else None
    if sys.argv[1:] and not snapshot_only and output_path is None:
        raise SystemExit("Usage: seed_complete_journeys_e2e.py [--snapshot | --output PATH]")

    conn = None
    try:
        conn = get_db()
        try:
            optimization_seed.verify_optimization_journeys_e2e_database_marker(conn)
            if not snapshot_only:
                optimization_seed.seed(conn)
                conn.execute(
                    "DELETE FROM gardens WHERE slug = %s",
                    (optimization_seed.DELETE_TARGET_SLUG,),
                )
                _seed_phase_one_fixtures(conn, optimization_seed)
                garden_rows = conn.execute(
                    "SELECT id FROM gardens WHERE slug = ANY(%s) ORDER BY id",
                    (
                        [
                            optimization_seed.GARDEN_A_SLUG,
                            optimization_seed.GARDEN_B_SLUG,
                            PHASE_ONE_LARGE_GARDEN_SLUG,
                        ],
                    ),
                ).fetchall()
                _add_role_fixtures(conn, garden_ids=[int(row["id"]) for row in garden_rows])
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
