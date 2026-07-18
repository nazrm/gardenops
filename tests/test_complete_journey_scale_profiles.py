from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gardenops.db import get_db, return_db
from scripts import seed_complete_journeys_e2e as seed
from scripts import seed_optimization_journeys_e2e as optimization_seed

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_COUNTS = {
    "small": {
        "gardens": 1,
        "garden_memberships": 3,
        "plots": 12,
        "plot_ownership": 12,
        "plants": 24,
        "plant_ownership": 24,
        "plot_plants": 24,
        "garden_tasks": 18,
        "garden_task_plants": 18,
        "garden_task_plots": 18,
        "garden_journal_entries": 12,
        "garden_journal_entry_plants": 12,
        "garden_issues": 4,
        "garden_issue_plants": 4,
        "media_assets": 6,
        "media_links": 6,
        "notification_events": 12,
        "harvest_entries": 4,
        "harvest_entry_plants": 4,
        "weather_cache": 4,
        "indoor_assignments": 1,
        "inventory_items": 12,
        "inventory_transactions": 36,
        "user_saved_views": 1,
    },
    "large": {
        "gardens": 1,
        "garden_memberships": 3,
        "plots": 600,
        "plot_ownership": 600,
        "plants": 900,
        "plant_ownership": 900,
        "plot_plants": 900,
        "garden_tasks": 900,
        "garden_task_plants": 900,
        "garden_task_plots": 900,
        "garden_journal_entries": 300,
        "garden_journal_entry_plants": 300,
        "garden_issues": 150,
        "garden_issue_plants": 150,
        "media_assets": 450,
        "media_links": 450,
        "notification_events": 900,
        "harvest_entries": 300,
        "harvest_entry_plants": 300,
        "weather_cache": 30,
        "indoor_assignments": 1,
        "inventory_items": 300,
        "inventory_transactions": 1800,
        "user_saved_views": 1,
    },
    "history-heavy": {
        "gardens": 1,
        "garden_memberships": 3,
        "plots": 60,
        "plot_ownership": 60,
        "plants": 120,
        "plant_ownership": 120,
        "plot_plants": 120,
        "garden_tasks": 2000,
        "garden_task_plants": 2000,
        "garden_task_plots": 2000,
        "garden_journal_entries": 1000,
        "garden_journal_entry_plants": 1000,
        "garden_issues": 300,
        "garden_issue_plants": 300,
        "media_assets": 120,
        "media_links": 120,
        "notification_events": 240,
        "harvest_entries": 600,
        "harvest_entry_plants": 600,
        "weather_cache": 300,
        "indoor_assignments": 1,
        "inventory_items": 240,
        "inventory_transactions": 2400,
        "user_saved_views": 1,
    },
    "multi-garden": {
        "gardens": 4,
        "garden_memberships": 12,
        "plots": 160,
        "plot_ownership": 160,
        "plants": 240,
        "plant_ownership": 240,
        "plot_plants": 240,
        "garden_tasks": 480,
        "garden_task_plants": 480,
        "garden_task_plots": 480,
        "garden_journal_entries": 96,
        "garden_journal_entry_plants": 96,
        "garden_issues": 32,
        "garden_issue_plants": 32,
        "media_assets": 48,
        "media_links": 48,
        "notification_events": 144,
        "harvest_entries": 48,
        "harvest_entry_plants": 48,
        "weather_cache": 48,
        "indoor_assignments": 4,
        "inventory_items": 120,
        "inventory_transactions": 480,
        "user_saved_views": 4,
    },
}


@pytest.fixture(scope="module")
def scale_profiles() -> Iterator[tuple[Any, dict[str, Any]]]:
    conn = get_db()
    try:
        optimization_seed.truncate_public_tables(conn)
        seed._insert_user(
            conn,
            username=seed.ADMIN_USERNAME,
            password=seed.ADMIN_PASSWORD,
            role="admin",
            subscription_tier="pro",
        )
        seed._insert_user(
            conn,
            username=seed.EDITOR_LOGIN[0],
            password=seed.EDITOR_LOGIN[1],
            role="editor",
            subscription_tier="pro",
        )
        seed._insert_user(
            conn,
            username=seed.VIEWER_LOGIN[0],
            password=seed.VIEWER_LOGIN[1],
            role="viewer",
            subscription_tier="pro",
        )
        projection = seed._apply_scale_profiles(conn)
        conn.commit()
        yield conn, projection
    finally:
        conn.rollback()
        optimization_seed.truncate_public_tables(conn)
        conn.commit()
        return_db(conn)


def test_scale_profile_projection_has_exact_counts_and_slugs(
    scale_profiles: tuple[Any, dict[str, Any]],
) -> None:
    _conn, projection = scale_profiles

    assert projection["schema_version"] == 1
    assert set(projection["profiles"]) == set(EXPECTED_COUNTS)
    for profile, expected_counts in EXPECTED_COUNTS.items():
        state = projection["profiles"][profile]
        assert state["counts"] == expected_counts
        assert state["slugs"] == sorted(spec["slug"] for spec in seed.SCALE_PROFILE_SPECS[profile])


def test_scale_profile_memberships_are_isolated_to_existing_journey_roles(
    scale_profiles: tuple[Any, dict[str, Any]],
) -> None:
    conn, projection = scale_profiles
    slugs = [slug for profile in projection["profiles"].values() for slug in profile["slugs"]]
    rows = conn.execute(
        """
        SELECT garden.slug, users.username, membership.role
        FROM garden_memberships membership
        JOIN gardens garden ON garden.id = membership.garden_id
        JOIN auth_users users ON users.id = membership.user_id
        WHERE garden.slug = ANY(%s)
        ORDER BY garden.slug, membership.role, users.username
        """,
        (slugs,),
    ).fetchall()
    by_slug: dict[str, set[tuple[str, str]]] = {slug: set() for slug in slugs}
    for row in rows:
        by_slug[str(row["slug"])].add((str(row["username"]), str(row["role"])))

    expected = {
        (seed.ADMIN_USERNAME, "admin"),
        (seed.EDITOR_LOGIN[0], "editor"),
        (seed.VIEWER_LOGIN[0], "viewer"),
    }
    assert all(memberships == expected for memberships in by_slug.values())


def test_scale_profiles_are_idempotent_with_deterministic_public_identifiers(
    scale_profiles: tuple[Any, dict[str, Any]],
) -> None:
    conn, first_projection = scale_profiles

    second_projection = seed._apply_scale_profiles(conn)
    conn.commit()

    assert second_projection == first_projection
    assert second_projection["profiles"]["large"]["identifiers"] == {
        "first_plant": "SCALE-LARGE-G01-PLANT-0001",
        "first_task": "tsk_scale_large_g01_00001",
        "last_plant": "SCALE-LARGE-G01-PLANT-0900",
        "last_task": "tsk_scale_large_g01_00900",
    }
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM gardens WHERE slug LIKE %s",
        (f"{seed.SCALE_PROFILE_PREFIX}%",),
    ).fetchone()
    assert int(row["count"]) == 7


def test_large_scale_profile_uses_one_based_map_grid_coordinates(
    scale_profiles: tuple[Any, dict[str, Any]],
) -> None:
    conn, projection = scale_profiles
    large_slug = projection["profiles"]["large"]["slugs"][0]

    row = conn.execute(
        """
        SELECT
            MIN(plot.grid_row) AS min_row,
            MAX(plot.grid_row) AS max_row,
            MIN(plot.grid_col) AS min_col,
            MAX(plot.grid_col) AS max_col
        FROM plots AS plot
        JOIN gardens AS garden ON garden.id = plot.garden_id
        WHERE garden.slug = %s
        """,
        (large_slug,),
    ).fetchone()

    assert row is not None
    assert {
        "min_row": int(row["min_row"]),
        "max_row": int(row["max_row"]),
        "min_col": int(row["min_col"]),
        "max_col": int(row["max_col"]),
    } == {
        "min_row": 1,
        "max_row": 20,
        "min_col": 1,
        "max_col": 30,
    }


def test_small_scale_profile_exercises_the_actionable_tasks_path(
    scale_profiles: tuple[Any, dict[str, Any]],
) -> None:
    conn, projection = scale_profiles
    small_slug = projection["profiles"]["small"]["slugs"][0]

    rows = conn.execute(
        """
        SELECT DISTINCT task.due_on
        FROM garden_tasks AS task
        JOIN gardens AS garden ON garden.id = task.garden_id
        WHERE garden.slug = %s
        ORDER BY task.due_on
        """,
        (small_slug,),
    ).fetchall()

    assert [str(row["due_on"]) for row in rows] == ["2026-07-12"]


def test_scale_profiles_include_explicit_indoor_assignment_and_saved_view(
    scale_profiles: tuple[Any, dict[str, Any]],
) -> None:
    conn, projection = scale_profiles

    for profile in projection["profiles"].values():
        for fixture in profile["fixtures"]:
            row = conn.execute(
                """
                SELECT plot.zone_code, assignment.plt_id, assignment.quantity,
                       assignment.room_label, saved.view_type, saved.label,
                       saved.filter_json
                FROM plots AS plot
                JOIN plot_plants AS assignment ON assignment.plot_id = plot.plot_id
                JOIN gardens AS garden ON garden.id = plot.garden_id
                JOIN user_saved_views AS saved ON saved.garden_id = garden.id
                WHERE garden.slug = %s AND plot.plot_id = %s
                """,
                (fixture["slug"], fixture["indoor_plot_id"]),
            ).fetchone()

            assert row is not None
            assert str(row["zone_code"]) == "I"
            assert str(row["plt_id"]) == fixture["indoor_plant_id"]
            assert int(row["quantity"]) == 3
            assert str(row["room_label"]) == f"Scale {fixture['content_marker']} Propagation Shelf"
            assert str(row["view_type"]) == "plants"
            assert str(row["label"]) == fixture["saved_view_label"]
            assert json.loads(str(row["filter_json"])) == {
                "plot_id": fixture["indoor_plot_id"],
                "q": f"Scale {fixture['content_marker']}",
            }


def test_scale_profiles_include_deterministic_inventory_ledger_volume(
    scale_profiles: tuple[Any, dict[str, Any]],
) -> None:
    conn, projection = scale_profiles

    for profile_name, profile in projection["profiles"].items():
        expected_depth = seed.SCALE_PROFILE_SPECS[profile_name][0]["ledger_per_item"]
        for fixture in profile["fixtures"]:
            row = conn.execute(
                """
                SELECT item.public_id, COUNT(tx.id) AS transaction_count,
                       COALESCE(SUM(tx.delta), 0) AS quantity
                FROM inventory_items AS item
                JOIN gardens AS garden ON garden.id = item.garden_id
                LEFT JOIN inventory_transactions AS tx
                  ON tx.item_id = item.id AND tx.garden_id = item.garden_id
                WHERE garden.slug = %s AND item.public_id = %s
                GROUP BY item.public_id
                """,
                (fixture["slug"], fixture["inventory_item_id"]),
            ).fetchone()

            assert row is not None
            assert str(row["public_id"]) == fixture["inventory_item_id"]
            assert int(row["transaction_count"]) == expected_depth
            assert float(row["quantity"]) > 0


def test_history_heavy_profile_spans_multiple_seasons_for_live_lists(
    scale_profiles: tuple[Any, dict[str, Any]],
) -> None:
    conn, projection = scale_profiles
    slug = projection["profiles"]["history-heavy"]["slugs"][0]

    row = conn.execute(
        """
        SELECT
            MAX(task.created_at_ms) - MIN(task.created_at_ms) AS task_span_ms,
            (SELECT MAX(entry.created_at_ms) - MIN(entry.created_at_ms)
             FROM garden_journal_entries AS entry WHERE entry.garden_id = garden.id)
                AS journal_span_ms,
            (SELECT MAX(issue.created_at_ms) - MIN(issue.created_at_ms)
             FROM garden_issues AS issue WHERE issue.garden_id = garden.id)
                AS issue_span_ms,
            (SELECT MAX(note.created_at_ms) - MIN(note.created_at_ms)
             FROM notification_events AS note WHERE note.garden_id = garden.id)
                AS notification_span_ms,
            (SELECT MAX(weather.fetched_at_ms) - MIN(weather.fetched_at_ms)
             FROM weather_cache AS weather WHERE weather.garden_id = garden.id)
                AS weather_span_ms
        FROM gardens AS garden
        JOIN garden_tasks AS task ON task.garden_id = garden.id
        WHERE garden.slug = %s
        GROUP BY garden.id
        """,
        (slug,),
    ).fetchone()

    assert row is not None
    one_year_ms = 365 * 86_400_000
    assert all(int(row[column]) > one_year_ms for column in row.keys())


def test_history_heavy_profile_has_a_current_visible_inbox_notification(
    scale_profiles: tuple[Any, dict[str, Any]],
) -> None:
    conn, projection = scale_profiles
    fixture = projection["profiles"]["history-heavy"]["fixtures"][0]

    row = conn.execute(
        """
        SELECT event.dismissed, event.notification_type, event.title, event.created_at_ms,
               auth_user.username
        FROM notification_events AS event
        JOIN gardens AS garden ON garden.id = event.garden_id
        JOIN auth_users AS auth_user ON auth_user.id = event.user_id
        WHERE garden.slug = %s AND event.public_id = %s
        """,
        (
            fixture["slug"],
            "note_scale_history_heavy_g01_00000",
        ),
    ).fetchone()

    assert row is not None
    assert str(row["username"]) == seed.ADMIN_USERNAME
    assert str(row["notification_type"]) == "system"
    assert str(row["title"]) == "Scale History Heavy G01 inbox proof"
    assert int(row["dismissed"]) == 0
    assert int(row["created_at_ms"]) == seed.SCALE_PROFILE_NOW_MS


def test_multi_garden_profile_keeps_overlapping_names_but_distinct_content(
    scale_profiles: tuple[Any, dict[str, Any]],
) -> None:
    conn, projection = scale_profiles
    fixtures = projection["profiles"]["multi-garden"]["fixtures"]
    rows = conn.execute(
        """
        SELECT garden.name, garden.slug, MIN(plant.name) AS first_plant_name,
               MIN(plot.plot_id) AS first_plot_id
        FROM gardens AS garden
        JOIN plant_ownership AS ownership ON ownership.garden_id = garden.id
        JOIN plants AS plant ON plant.plt_id = ownership.plt_id
        JOIN plots AS plot ON plot.garden_id = garden.id AND plot.zone_code <> 'I'
        WHERE garden.slug = ANY(%s)
        GROUP BY garden.id, garden.name, garden.slug
        ORDER BY garden.slug
        """,
        ([fixture["slug"] for fixture in fixtures],),
    ).fetchall()

    assert {str(row["name"]) for row in rows} == {"Complete Journeys Shared Garden"}
    assert len({str(row["first_plant_name"]) for row in rows}) == 4
    assert [str(row["slug"]) for row in rows] == [fixture["slug"] for fixture in fixtures]
    assert [str(row["first_plot_id"]) for row in rows] == [
        "SCALE-MULTI_GARDEN-G01-PLOT-0001",
        "SCALE-MULTI_GARDEN-G02-PLOT-0001",
        "SCALE-MULTI_GARDEN-G03-PLOT-0001",
        "SCALE-MULTI_GARDEN-G04-PLOT-0001",
    ]


def test_apply_scale_profiles_cli_refuses_without_runner_child_guard() -> None:
    env = os.environ.copy()
    env.pop("GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD", None)
    result = subprocess.run(
        [sys.executable, "scripts/seed_complete_journeys_e2e.py", "--apply-scale-profiles"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "must run as the disposable runner child" in result.stderr
