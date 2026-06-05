"""Tests for the read-only backend integrity audit command."""

from __future__ import annotations

import json
from unittest.mock import patch

from scripts import check_backend_integrity
from tests.base import DbTestBase


class BackendIntegrityAuditTests(DbTestBase):
    def _findings_by_id(self) -> dict[str, dict[str, object]]:
        report = check_backend_integrity.collect_report(self.conn)
        findings = report["findings"]
        assert isinstance(findings, list)
        return {str(finding["id"]): finding for finding in findings}

    def _insert_plot(self, plot_id: str, *, row: int = 1, col: int = 1) -> None:
        self.conn.execute(
            """
            INSERT INTO plots (
                plot_id, garden_id, zone_code, zone_name, plot_number, grid_row, grid_col
            )
            VALUES (%s, %s, 'B', 'Bed', 1, %s, %s)
            """,
            (plot_id, self.garden_id, row, col),
        )
        self.conn.execute(
            """
            INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
            VALUES (%s, %s, %s)
            """,
            (plot_id, self._owner_id, self.garden_id),
        )

    def test_clean_seed_has_no_blocking_findings(self) -> None:
        report = check_backend_integrity.collect_report(self.conn)

        self.assertTrue(report["ok"])
        self.assertEqual(report["blocking_findings"], 0)
        findings = report["findings"]
        self.assertIsInstance(findings, list)
        self.assertTrue(findings)
        self.assertTrue(
            all(not finding["blocking"] or finding["count"] == 0 for finding in findings),
        )

    def test_detects_layout_ownership_owner_and_stale_reference_blockers(self) -> None:
        self.conn.execute("DROP INDEX IF EXISTS ux_plots_garden_grid_cell")
        self._insert_plot("DUP-A", row=10, col=10)
        self._insert_plot("DUP-B", row=10, col=10)
        self.conn.execute(
            """
            INSERT INTO plots (
                plot_id, garden_id, zone_code, zone_name, plot_number, grid_row, grid_col
            )
            VALUES ('UNOWNED-PLOT', %s, 'B', 'Bed', 2, 11, 11)
            """,
            (self.garden_id,),
        )
        self._insert_plot("MISMATCH-PLOT", row=12, col=12)
        other_garden = self.conn.execute(
            """
            INSERT INTO gardens (slug, name)
            VALUES ('integrity-mismatch', 'Integrity Mismatch')
            RETURNING id
            """,
        ).fetchone()
        assert other_garden is not None
        self.conn.execute(
            "UPDATE plots SET garden_id = %s WHERE plot_id = 'MISMATCH-PLOT'",
            (other_garden["id"],),
        )
        self.conn.execute(
            """
            INSERT INTO plants (plt_id, name, category)
            VALUES ('UNOWNED-PLANT', 'Unowned Plant', 'busker')
            """,
        )
        self.conn.execute(
            """
            UPDATE auth_users
            SET is_active = 0
            WHERE id = %s
            """,
            (self._owner_id,),
        )
        self.conn.execute(
            "UPDATE gardens SET owner_user_id = %s WHERE id = %s",
            (self._owner_id, self.garden_id),
        )
        task = self.conn.execute(
            """
            INSERT INTO garden_tasks (
                garden_id, task_type, title, status, severity, due_on,
                created_by_user_id, created_at_ms, updated_at_ms
            )
            VALUES (%s, 'water', 'Water', 'pending', 'normal', '2026-05-12', %s, 1, 1)
            RETURNING id
            """,
            (self.garden_id, self._owner_id),
        ).fetchone()
        assert task is not None
        self.conn.execute(
            "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, 'MISSING-PLOT')",
            (task["id"],),
        )
        self.conn.execute(
            """
            INSERT INTO media_assets (
                asset_id, garden_id, storage_key, preview_storage_key,
                original_filename, mime_type, bytes, width, height, created_at_ms,
                actor_user_id
            )
            VALUES ('asset-integrity', %s, 'media/original', 'media/preview',
                'photo.jpg', 'image/jpeg', 1, 1, 1, 1, %s)
            """,
            (self.garden_id, self._owner_id),
        )
        self.conn.execute(
            """
            INSERT INTO media_links (asset_id, target_type, target_id)
            VALUES ('asset-integrity', 'plot', 'MISSING-PLOT')
            """,
        )
        self.conn.execute(
            """
            INSERT INTO shademap_state (
                garden_id, selected_plot_id, analysis_timestamp_ms
            )
            VALUES (%s, 'MISSING-PLOT', 1)
            """,
            (self.garden_id,),
        )

        findings = self._findings_by_id()

        expected_blockers = {
            "duplicate_layout_cells",
            "plot_missing_garden_ownership",
            "plot_garden_ownership_mismatch",
            "plant_missing_garden_ownership",
            "garden_owner_not_active_user",
            "stale_plot_reference.garden_task_plots",
            "stale_plot_reference.media_links_plot",
            "stale_plot_reference.shademap_state",
        }
        for finding_id in expected_blockers:
            with self.subTest(finding_id=finding_id):
                finding = findings[finding_id]
                self.assertTrue(finding["blocking"])
                self.assertGreaterEqual(int(finding["count"]), 1)

    def test_deferrable_fk_backed_plot_references_are_audited_before_commit(self) -> None:
        event = self.conn.execute(
            """
            INSERT INTO garden_calendar_events (
                garden_id, title, event_on, created_by_user_id, updated_by_user_id,
                created_at_ms, updated_at_ms
            )
            VALUES (%s, 'Manual', '2026-05-12', %s, %s, 1, 1)
            RETURNING id
            """,
            (self.garden_id, self._owner_id, self._owner_id),
        ).fetchone()
        assert event is not None
        self.conn.execute("SET CONSTRAINTS ALL DEFERRED")
        self.conn.execute(
            """
            INSERT INTO garden_calendar_event_plots (event_id, plot_id)
            VALUES (%s, 'MISSING-CALENDAR-PLOT')
            """,
            (event["id"],),
        )

        findings = self._findings_by_id()

        finding = findings["stale_plot_reference.garden_calendar_event_plots"]
        self.assertTrue(finding["blocking"])
        self.assertEqual(finding["count"], 1)

    def test_user_hard_delete_summary_is_nonblocking(self) -> None:
        self._insert_plot("OWNED-PLOT", row=12, col=12)
        self.conn.execute(
            "UPDATE gardens SET owner_user_id = %s WHERE id = %s",
            (self._owner_id, self.garden_id),
        )

        findings = self._findings_by_id()

        finding = findings["user_hard_delete_ownership_summary"]
        self.assertFalse(finding["blocking"])
        self.assertEqual(finding["severity"], "info")
        self.assertGreaterEqual(int(finding["count"]), 1)

    def test_schema_signature_reports_missing_declared_objects(self) -> None:
        with patch.object(
            check_backend_integrity,
            "REQUIRED_COLUMNS",
            {"auth_users": ("definitely_missing_integrity_column",)},
        ):
            findings = self._findings_by_id()

        finding = findings["schema_signature_missing"]
        self.assertTrue(finding["blocking"])
        self.assertEqual(finding["count"], 1)
        self.assertIn("definitely_missing_integrity_column", json.dumps(finding))
