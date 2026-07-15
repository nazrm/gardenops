"""Query-scaling and concurrency coverage for generated task workflows."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from starlette.requests import Request

import gardenops.db as db
from gardenops.routers.tasks import refresh_descriptions
from gardenops.security import AuthContext
from gardenops.services.task_generator import (
    _TASK_GENERATION_LOCK_SEED,
    _existing_rule_sources,
    _generation_candidate_rule_sources,
    _monthly_task_generation_lock_name,
    generate_tasks,
)
from tests.base import DbTestBase

_JULY_1_2026_MS = 1_782_864_000_000


class _ReadCountingConnection:
    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self.read_query_count = 0

    def execute(self, query: Any, params: Any = None) -> Any:
        if str(query).lstrip().upper().startswith("SELECT"):
            self.read_query_count += 1
        return self._connection.execute(query, params)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


class _LockObservingConnection:
    def __init__(self, connection: Any, on_lock_attempt: Callable[[], None]) -> None:
        self._connection = connection
        self._on_lock_attempt = on_lock_attempt

    def execute(self, query: Any, params: Any = None) -> Any:
        if "pg_advisory_xact_lock" in str(query):
            self._on_lock_attempt()
        return self._connection.execute(query, params)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


class TestTaskQueryScaling(DbTestBase):
    def _create_outdoor_plot(self) -> None:
        self.conn.execute(
            """
            INSERT INTO plots
                (plot_id, garden_id, zone_code, zone_name, plot_number,
                 grid_row, grid_col, sub_zone, notes)
            VALUES ('TASK-SCALE-OUT', %s, 'T', 'Task scale', 1, 1, 1, '', '')
            """,
            (self.garden_id,),
        )
        self.conn.commit()

    def _add_watering_plant(self, number: int) -> None:
        plant_id = f"TASK-SCALE-{number}"
        self._insert_plant(
            plant_id,
            f"Watering plant {number}",
            care_watering="water regularly",
        )
        self.conn.execute(
            "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES (%s, %s, 1)",
            ("TASK-SCALE-OUT", plant_id),
        )
        self.conn.commit()

    def _create_rain_alert(self) -> None:
        self.conn.execute(
            """
            INSERT INTO weather_alerts
                (garden_id, alert_type, severity, title, description,
                 valid_from, valid_until, metadata_json, created_at_ms)
            VALUES (%s, 'rain_surplus', 'normal', 'Task scale rain', '',
                    '2026-07-01', '2026-07-22', '{}', 1)
            """,
            (self.garden_id,),
        )
        self.conn.commit()

    def _refresh_request(self) -> Request:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/tasks/refresh-descriptions",
                "headers": [],
                "client": ("127.0.0.1", 0),
                "state": {},
            },
        )
        request.state.auth_context = AuthContext(
            user_id=None,
            username="task-query-test",
            role="admin",
            auth_type="none",
            garden_id=self.garden_id,
            garden_role="admin",
        )
        return request

    def test_generate_tasks_read_queries_stay_flat_as_plant_count_grows(self) -> None:
        self._create_outdoor_plot()
        self._add_watering_plant(1)

        first_connection = _ReadCountingConnection(self.conn)
        first_result = generate_tasks(
            first_connection,
            self.garden_id,
            7,
            2026,
            self._owner_id,
            now_ms=_JULY_1_2026_MS,
        )
        self.assertEqual(first_result["created"], 4)

        for number in range(2, 10):
            self._add_watering_plant(number)

        many_connection = _ReadCountingConnection(self.conn)
        many_result = generate_tasks(
            many_connection,
            self.garden_id,
            7,
            2026,
            self._owner_id,
            now_ms=_JULY_1_2026_MS,
        )

        self.assertEqual(many_result["created"], 32)
        self.assertEqual(
            first_connection.read_query_count,
            many_connection.read_query_count,
        )

    def test_existing_rule_lookup_does_not_load_historical_task_volume(self) -> None:
        historical_rows = [
            (
                self.garden_id,
                f"water:HISTORICAL-{number}:2020-07-01",
                number,
                number,
            )
            for number in range(250)
        ]
        db.executemany(
            self.conn,
            """
            INSERT INTO garden_tasks (
                garden_id, task_type, title, description, status, severity,
                due_on, rule_source, metadata_json, created_at_ms, updated_at_ms
            )
            VALUES (%s, 'water', 'Historical', '', 'completed', 'normal',
                    '2020-07-01', %s, '{}', %s, %s)
            """,
            historical_rows,
        )
        self.conn.execute(
            """
            INSERT INTO garden_tasks (
                garden_id, task_type, title, description, status, severity,
                due_on, rule_source, metadata_json, created_at_ms, updated_at_ms
            )
            VALUES (%s, 'water', 'Current', '', 'pending', 'normal',
                    '2026-07-01', 'water:TASK-SCALE-CURRENT:2026-07-01', '{}', 1, 1)
            """,
            (self.garden_id,),
        )

        candidates = _generation_candidate_rule_sources(
            ["TASK-SCALE-CURRENT"],
            7,
            2026,
        )
        existing = _existing_rule_sources(self.conn, self.garden_id, candidates)

        self.assertEqual(existing, {"water:TASK-SCALE-CURRENT:2026-07-01"})

    def test_rain_suppressed_generation_reads_stay_flat_as_plant_count_grows(self) -> None:
        self._create_outdoor_plot()
        self._create_rain_alert()
        self._add_watering_plant(1)

        first_connection = _ReadCountingConnection(self.conn)
        first_result = generate_tasks(
            first_connection,
            self.garden_id,
            7,
            2026,
            self._owner_id,
            now_ms=_JULY_1_2026_MS,
        )
        self.assertEqual(first_result["created"], 0)

        for number in range(2, 10):
            self._add_watering_plant(number)

        many_connection = _ReadCountingConnection(self.conn)
        many_result = generate_tasks(
            many_connection,
            self.garden_id,
            7,
            2026,
            self._owner_id,
            now_ms=_JULY_1_2026_MS,
        )

        self.assertEqual(many_result["created"], 0)
        self.assertEqual(
            first_connection.read_query_count,
            many_connection.read_query_count,
        )

    def test_refresh_descriptions_read_queries_stay_flat_as_task_count_grows(self) -> None:
        self._create_outdoor_plot()
        self._add_watering_plant(1)
        generate_tasks(
            self.conn,
            self.garden_id,
            7,
            2026,
            self._owner_id,
            now_ms=_JULY_1_2026_MS,
        )

        first_connection = _ReadCountingConnection(self.conn)
        first_result = refresh_descriptions(self._refresh_request(), first_connection)
        self.assertEqual(first_result["updated"], 4)

        for number in range(2, 10):
            self._add_watering_plant(number)
        generate_tasks(
            self.conn,
            self.garden_id,
            7,
            2026,
            self._owner_id,
            now_ms=_JULY_1_2026_MS,
        )

        many_connection = _ReadCountingConnection(self.conn)
        many_result = refresh_descriptions(self._refresh_request(), many_connection)

        self.assertEqual(many_result["updated"], 36)
        self.assertEqual(
            first_connection.read_query_count,
            many_connection.read_query_count,
        )

    def test_monthly_generation_lock_serializes_two_connections(self) -> None:
        self._create_outdoor_plot()
        self._add_watering_plant(1)
        lock_name = _monthly_task_generation_lock_name(self.garden_id, 7, 2026)
        lock_holder = db.get_db()
        lock_holder.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, %s))",
            (lock_name, _TASK_GENERATION_LOCK_SEED),
        )

        start = threading.Barrier(3)
        lock_attempts = threading.Event()
        attempt_guard = threading.Lock()
        attempt_count = 0

        def record_lock_attempt() -> None:
            nonlocal attempt_count
            with attempt_guard:
                attempt_count += 1
                if attempt_count == 2:
                    lock_attempts.set()

        def generate_from_fresh_connection() -> dict[str, int]:
            connection = db.get_db()
            try:
                observed = _LockObservingConnection(connection, record_lock_attempt)
                start.wait(timeout=5)
                return generate_tasks(
                    observed,
                    self.garden_id,
                    7,
                    2026,
                    self._owner_id,
                    now_ms=_JULY_1_2026_MS,
                )
            finally:
                db.return_db(connection)

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(generate_from_fresh_connection)
                second = executor.submit(generate_from_fresh_connection)
                try:
                    start.wait(timeout=5)
                    self.assertTrue(lock_attempts.wait(timeout=2))
                    row = self.conn.execute(
                        "SELECT COUNT(*) AS count FROM garden_tasks WHERE garden_id = %s",
                        (self.garden_id,),
                    ).fetchone()
                    assert row is not None
                    self.assertEqual(int(row["count"]), 0)
                finally:
                    lock_holder.commit()
                results = [first.result(timeout=10), second.result(timeout=10)]
        finally:
            db.return_db(lock_holder)

        self.assertEqual(sorted(result["created"] for result in results), [0, 4])
        rows = self.conn.execute(
            """
            SELECT rule_source
            FROM garden_tasks
            WHERE garden_id = %s
            ORDER BY rule_source
            """,
            (self.garden_id,),
        ).fetchall()
        self.assertEqual(
            [str(row["rule_source"]) for row in rows],
            [
                "water:TASK-SCALE-1:2026-07-01",
                "water:TASK-SCALE-1:2026-07-08",
                "water:TASK-SCALE-1:2026-07-15",
                "water:TASK-SCALE-1:2026-07-22",
            ],
        )
