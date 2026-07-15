from datetime import date, timedelta

import gardenops.db as db
from gardenops.router_helpers import generate_public_id
from tests.base import BaseApiTest


class TestGardenerReportsApi(BaseApiTest):
    def setUp(self) -> None:
        super().setUp()
        conn = db.get_db()
        default_garden = conn.execute(
            "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
        ).fetchone()
        assert default_garden is not None
        garden_id = int(default_garden["id"])
        owner_row = conn.execute("SELECT MIN(id) AS uid FROM auth_users").fetchone()
        owner_id = int(owner_row["uid"]) if owner_row and owner_row["uid"] else 1

        conn.execute("DELETE FROM harvest_entry_plants")
        conn.execute("DELETE FROM harvest_entry_plots")
        conn.execute("DELETE FROM harvest_entries")
        conn.execute("DELETE FROM weather_alert_plants")
        conn.execute("DELETE FROM weather_alerts")
        conn.execute("DELETE FROM garden_issue_plants")
        conn.execute("DELETE FROM garden_issue_plots")
        conn.execute("DELETE FROM garden_issues")
        conn.execute("DELETE FROM garden_task_plants")
        conn.execute("DELETE FROM garden_task_plots")
        conn.execute("DELETE FROM garden_tasks")
        conn.execute("DELETE FROM garden_journal_entry_plants")
        conn.execute("DELETE FROM garden_journal_entry_plots")
        conn.execute("DELETE FROM garden_journal_entries")
        conn.execute("DELETE FROM plant_media_covers")
        conn.execute("DELETE FROM media_links")
        conn.execute("DELETE FROM media_assets")
        conn.execute("DELETE FROM plot_plants")
        conn.execute("DELETE FROM plant_ownership")
        conn.execute("DELETE FROM plot_ownership")
        conn.execute("DELETE FROM plants")
        conn.execute("DELETE FROM plots")

        plots = [
            ("B1", "B", "Bed", 1, 1, 1, "", "", None),
            ("B2", "B", "Bed", 2, 1, 2, "", "", None),
            ("B3", "B", "Bed", 3, 1, 3, "", "", None),
            ("P1", "P", "Plen", 1, 2, 1, "", "", None),
        ]
        db.executemany(conn, "INSERT INTO plots VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", plots)

        today = date.today()
        current_month = str(today.month)
        next_month = str((today.month % 12) + 1)

        plants = [
            (
                "PLT-MARCH",
                "Hellebore",
                "Helleborus orientalis",
                "staude",
                current_month,
                "hvit",
                "H5",
                40,
                "halvskygge",
                "",
                "2022",
                0,
            ),
            (
                "PLT-MISSCARE",
                "Tulip",
                "Tulipa tarda",
                "løk",
                "mai",
                "gul",
                "",
                20,
                "",
                "",
                "",
                0,
            ),
            (
                "PLT-APRIL",
                "Lungwort",
                "Pulmonaria officinalis",
                "staude",
                next_month,
                "blå",
                "H6",
                30,
                "halvskygge",
                "",
                "2021",
                0,
            ),
        ]
        db.executemany(
            conn,
            """
            INSERT INTO plants (
                plt_id, name, latin, category, bloom_month, color,
                hardiness, height_cm, light, link, year_planted,
                deer_resistant
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            plants,
        )

        conn.commit()
        for plt_id in ("PLT-MARCH", "PLT-MISSCARE", "PLT-APRIL"):
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                """,
                (plt_id, owner_id, garden_id),
            )
        for plot_id in ("B1", "B2", "B3", "P1"):
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                """,
                (plot_id, owner_id, garden_id),
            )
        conn.commit()
        db.executemany(
            conn,
            "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES (%s, %s, %s)",
            [
                ("B1", "PLT-MARCH", 2),
                ("B3", "PLT-MISSCARE", 1),
                ("P1", "PLT-APRIL", 2),
            ],
        )

        now_ms = db.current_timestamp_ms()

        overdue_task_id = conn.execute(
            """
            INSERT INTO garden_tasks (
                garden_id, task_type, title, description, status, severity,
                due_on, rule_source, metadata_json, created_at_ms, updated_at_ms
            ) VALUES (%s, 'prune', 'Prune hellebore', '', 'pending', 'normal', %s, '', '{}', %s, %s)
            RETURNING id
            """,
            (garden_id, (today - timedelta(days=2)).isoformat(), now_ms, now_ms),
        ).fetchone()["id"]
        due_soon_task_id = conn.execute(
            """
            INSERT INTO garden_tasks (
                garden_id, task_type, title, description, status, severity,
                due_on, rule_source, metadata_json, created_at_ms, updated_at_ms
            ) VALUES (
                %s, 'observe_bloom', 'Check lungwort', '', 'pending',
                'normal', %s, '', '{}', %s, %s
            )
            RETURNING id
            """,
            (garden_id, (today + timedelta(days=3)).isoformat(), now_ms, now_ms),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
            (overdue_task_id, "PLT-MARCH"),
        )
        conn.execute(
            "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, %s)",
            (overdue_task_id, "B1"),
        )
        conn.execute(
            "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
            (due_soon_task_id, "PLT-APRIL"),
        )
        conn.execute(
            "INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, %s)",
            (due_soon_task_id, "P1"),
        )

        issue_id = conn.execute(
            """
            INSERT INTO garden_issues (
                public_id, garden_id, issue_type, title, description, severity, status,
                suspected_cause, treatment_plan, follow_up_on, metadata_json,
                created_at_ms, updated_at_ms
            ) VALUES (
                %s, %s, 'pest', 'Aphids on tulip', '', 'high', 'open', '', '', %s, '{}', %s, %s
            )
            RETURNING id
            """,
            (
                generate_public_id("iss"),
                garden_id,
                (today - timedelta(days=1)).isoformat(),
                now_ms,
                now_ms,
            ),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO garden_issue_plants (issue_id, plt_id) VALUES (%s, %s)",
            (issue_id, "PLT-MISSCARE"),
        )
        conn.execute(
            "INSERT INTO garden_issue_plots (issue_id, plot_id) VALUES (%s, %s)",
            (issue_id, "B3"),
        )

        recent_entry_id = conn.execute(
            """
            INSERT INTO garden_journal_entries (
                public_id, garden_id, event_type, occurred_on, title, notes,
                metadata_json, created_at_ms, updated_at_ms
            ) VALUES (%s, %s, 'observed', %s, 'Checked lungwort', '', '{}', %s, %s)
            RETURNING id
            """,
            (
                generate_public_id("jrn"),
                garden_id,
                (today - timedelta(days=20)).isoformat(),
                now_ms,
                now_ms,
            ),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO garden_journal_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
            (recent_entry_id, "PLT-APRIL"),
        )
        conn.execute(
            "INSERT INTO garden_journal_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
            (recent_entry_id, "P1"),
        )

        conn.execute(
            """
            INSERT INTO weather_alerts (
                garden_id, alert_type, severity, title, description,
                valid_from, valid_until, metadata_json, dismissed, created_at_ms
            ) VALUES (%s, 'frost_warning', 'normal', 'Cold night', '', %s, %s, '{}', 0, %s)
            """,
            (garden_id, today.isoformat(), (today + timedelta(days=1)).isoformat(), now_ms),
        )
        p_zone_alert_id = conn.execute(
            """
            INSERT INTO weather_alerts (
                garden_id, alert_type, severity, title, description,
                valid_from, valid_until, metadata_json, dismissed, created_at_ms
            ) VALUES (%s, 'rain_surplus', 'low', 'Wet lawn', '', %s, %s, '{}', 0, %s)
            RETURNING id
            """,
            (garden_id, today.isoformat(), (today + timedelta(days=1)).isoformat(), now_ms),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO weather_alert_plants (alert_id, plt_id) VALUES (%s, %s)",
            (p_zone_alert_id, "PLT-APRIL"),
        )

        harvest_id = conn.execute(
            """
            INSERT INTO harvest_entries (
                public_id, garden_id, occurred_on, quantity, unit, quality, notes,
                metadata_json, actor_user_id, created_at_ms, updated_at_ms
            ) VALUES (%s, %s, %s, 3, 'pieces', 'good', '', '{}', NULL, %s, %s)
            RETURNING id
            """,
            (generate_public_id("hrv"), garden_id, today.isoformat(), now_ms, now_ms),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO harvest_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
            (harvest_id, "PLT-APRIL"),
        )
        conn.execute(
            "INSERT INTO harvest_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
            (harvest_id, "P1"),
        )

        conn.execute(
            """
            INSERT INTO media_assets (
                asset_id, garden_id, storage_key, preview_storage_key,
                original_filename, mime_type, bytes, width, height,
                created_at_ms, actor_user_id
            ) VALUES (%s, %s, %s, %s, %s, 'image/jpeg', 128, 40, 40, %s, NULL)
            """,
            (
                "asset-cover",
                garden_id,
                "media/full/asset-cover.jpg",
                "media/preview/asset-cover.jpg",
                "cover.jpg",
                now_ms,
            ),
        )
        conn.execute(
            """
            INSERT INTO plant_media_covers (
                garden_id, plt_id, asset_id, set_at_ms, set_by_user_id
            ) VALUES (%s, %s, %s, %s, NULL)
            """,
            (garden_id, "PLT-APRIL", "asset-cover", now_ms),
        )
        conn.commit()
        db.return_db(conn)

    def test_statistics_reports_include_smart_sections(self) -> None:
        response = self.client.get("/api/statistics/reports")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn("available_zones", data)
        self.assertEqual(data["needs_attention"]["overdue_tasks_count"], 1)
        self.assertEqual(data["needs_attention"]["due_this_week_count"], 1)
        self.assertEqual(data["needs_attention"]["open_issues_count"], 1)
        self.assertEqual(data["needs_attention"]["overdue_follow_ups_count"], 1)
        self.assertEqual(data["needs_attention"]["active_weather_alerts_count"], 2)
        for count_key, ids_key in (
            ("overdue_tasks_count", "overdue_task_ids"),
            ("due_this_week_count", "due_this_week_task_ids"),
            ("open_issues_count", "open_issue_ids"),
            ("overdue_follow_ups_count", "overdue_follow_up_issue_ids"),
            ("active_weather_alerts_count", "active_weather_alert_ids"),
        ):
            self.assertEqual(
                data["needs_attention"][count_key], len(data["needs_attention"][ids_key])
            )
        self.assertTrue(
            all(value.startswith("tsk_") for value in data["needs_attention"]["overdue_task_ids"])
        )
        self.assertTrue(
            all(value.startswith("iss_") for value in data["needs_attention"]["open_issue_ids"])
        )

        self.assertIn("PLT-MARCH", data["bloom_now"]["plant_ids"])
        self.assertIn("PLT-APRIL", data["bloom_next"]["plant_ids"])
        self.assertEqual(sorted(data["plot_use"]["empty_plot_ids"]), ["B2"])
        self.assertEqual(sorted(data["plot_use"]["underused_plot_ids"]), ["B3"])
        self.assertIn("PLT-MISSCARE", data["missing_observations"]["plant_ids"])
        self.assertIn("PLT-MISSCARE", data["data_quality"]["missing_care_plant_ids"])
        self.assertIn("PLT-MISSCARE", data["data_quality"]["missing_year_plant_ids"])
        self.assertEqual(
            sorted(data["data_quality"]["missing_cover_plant_ids"]),
            ["PLT-MARCH", "PLT-MISSCARE"],
        )
        self.assertEqual(data["yield_summary"]["total_entries"], 1)
        self.assertEqual(data["yield_summary"]["harvested_plot_count"], 1)
        self.assertEqual(data["yield_summary"]["top_producers"][0]["plt_id"], "PLT-APRIL")

    def test_statistics_reports_ignore_non_observation_journal_entries_for_missing_observations(
        self,
    ) -> None:
        conn = db.get_db()
        try:
            garden_id = self._get_default_garden_id()
            now_ms = db.current_timestamp_ms()
            entry_id = conn.execute(
                """
                INSERT INTO garden_journal_entries (
                    public_id, garden_id, event_type, occurred_on, title, notes,
                    metadata_json, created_at_ms, updated_at_ms
                ) VALUES (%s, %s, 'watered', %s, 'Watered tulip', '', '{}', %s, %s)
                RETURNING id
                """,
                (
                    generate_public_id("jrn"),
                    garden_id,
                    date.today().isoformat(),
                    now_ms,
                    now_ms,
                ),
            ).fetchone()["id"]
            conn.execute(
                "INSERT INTO garden_journal_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
                (entry_id, "PLT-MISSCARE"),
            )
            conn.execute(
                "INSERT INTO garden_journal_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
                (entry_id, "B3"),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        response = self.client.get("/api/statistics/reports")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("PLT-MISSCARE", data["missing_observations"]["plant_ids"])

    def test_statistics_reports_zone_filter_scopes_results(self) -> None:
        bed = self.client.get("/api/statistics/reports?zone_code=B")
        self.assertEqual(bed.status_code, 200)
        bed_data = bed.json()
        self.assertEqual(bed_data["zone_code"], "B")
        self.assertEqual(bed_data["needs_attention"]["overdue_tasks_count"], 1)
        self.assertEqual(bed_data["needs_attention"]["due_this_week_count"], 0)
        self.assertEqual(bed_data["needs_attention"]["active_weather_alerts_count"], 1)
        self.assertEqual(bed_data["yield_summary"]["total_entries"], 0)
        self.assertEqual(sorted(bed_data["plot_use"]["empty_plot_ids"]), ["B2"])
        self.assertEqual(sorted(bed_data["plot_use"]["underused_plot_ids"]), ["B3"])
        self.assertIn("PLT-MARCH", bed_data["bloom_now"]["plant_ids"])

        lawn = self.client.get("/api/statistics/reports?zone_code=P")
        self.assertEqual(lawn.status_code, 200)
        lawn_data = lawn.json()
        self.assertEqual(lawn_data["zone_code"], "P")
        self.assertEqual(lawn_data["needs_attention"]["overdue_tasks_count"], 0)
        self.assertEqual(lawn_data["needs_attention"]["due_this_week_count"], 1)
        self.assertEqual(lawn_data["needs_attention"]["active_weather_alerts_count"], 2)
        self.assertEqual(lawn_data["yield_summary"]["total_entries"], 1)
        self.assertEqual(lawn_data["plot_use"]["empty_plot_ids"], [])
        self.assertEqual(lawn_data["plot_use"]["underused_plot_ids"], [])
        self.assertIn("PLT-APRIL", lawn_data["bloom_next"]["plant_ids"])

        self.assertTrue(
            set(bed_data["needs_attention"]["overdue_task_ids"]).isdisjoint(
                lawn_data["needs_attention"]["due_this_week_task_ids"],
            )
        )

    def test_statistics_reports_ignore_foreign_garden_links_in_zone_scope(self) -> None:
        conn = db.get_db()
        try:
            second_garden_id = int(
                conn.execute(
                    "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
                    ("reports-foreign", "Foreign Reports Garden"),
                ).fetchone()["id"]
            )
            conn.execute(
                "INSERT INTO plants (plt_id, name, category) VALUES (%s, %s, %s)",
                ("PLT-FOREIGN", "Foreign only", "other"),
            )
            conn.execute(
                """
                INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                ("PLT-FOREIGN", self._owner_id, second_garden_id),
            )
            conn.execute(
                "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES (%s, %s, %s)",
                ("B2", "PLT-FOREIGN", 1),
            )
            foreign_linked_task_id = int(
                conn.execute(
                    """
                    INSERT INTO garden_tasks (
                        garden_id, task_type, title, description, status, severity,
                        due_on, rule_source, metadata_json, created_at_ms, updated_at_ms
                    ) VALUES (%s, 'water', 'Foreign linked task', '', 'pending', 'normal',
                              %s, '', '{}', %s, %s)
                    RETURNING id
                    """,
                    (
                        self._get_default_garden_id(),
                        (date.today() - timedelta(days=1)).isoformat(),
                        db.current_timestamp_ms(),
                        db.current_timestamp_ms(),
                    ),
                ).fetchone()["id"]
            )
            conn.execute(
                "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
                (foreign_linked_task_id, "PLT-FOREIGN"),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        response = self.client.get("/api/statistics/reports?zone_code=B")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertIn("B2", data["plot_use"]["empty_plot_ids"])
        self.assertEqual(data["needs_attention"]["overdue_tasks_count"], 1)
        self.assertEqual(
            data["needs_attention"]["overdue_tasks_count"],
            len(data["needs_attention"]["overdue_task_ids"]),
        )

    def test_statistics_reports_reject_unknown_zone(self) -> None:
        response = self.client.get("/api/statistics/reports?zone_code=Z")
        self.assertEqual(response.status_code, 404)
        self.assertIn("Zone not found", response.json()["detail"])
