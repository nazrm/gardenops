from unittest import TestCase
from unittest.mock import Mock, patch

from psycopg.pq import TransactionStatus
from starlette.requests import Request as StarletteRequest

import gardenops.db as db


class TestDbPoolHelpers(TestCase):
    @staticmethod
    def _make_request() -> StarletteRequest:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "client": ("127.0.0.1", 5000),
        }
        return StarletteRequest(scope)

    def test_get_pool_opens_explicitly(self) -> None:
        fake_pool = Mock()

        with (
            patch("gardenops.db._pool", None),
            patch("gardenops.db._PgConnectionPool", return_value=fake_pool) as pool_cls,
            patch("gardenops.db._database_url", return_value="postgresql://example"),
        ):
            pool = db._get_pool()

        self.assertIs(pool, fake_pool)
        pool_cls.assert_called_once_with(
            "postgresql://example",
            min_size=2,
            max_size=10,
            open=True,
            kwargs={"row_factory": db.psycopg.rows.dict_row},
        )

    def test_return_db_rolls_back_open_transaction_before_pool_return(self) -> None:
        conn = Mock()
        conn.info.transaction_status = TransactionStatus.INTRANS
        pool = Mock()

        with patch("gardenops.db._get_pool", return_value=pool):
            db.return_db(conn)

        conn.rollback.assert_called_once_with()
        pool.putconn.assert_called_once_with(conn)

    def test_return_db_skips_rollback_for_idle_connection(self) -> None:
        conn = Mock()
        conn.info.transaction_status = TransactionStatus.IDLE
        pool = Mock()

        with patch("gardenops.db._get_pool", return_value=pool):
            db.return_db(conn)

        conn.rollback.assert_not_called()
        pool.putconn.assert_called_once_with(conn)

    def test_db_dep_stashes_request_connection_for_request_scope(self) -> None:
        request = self._make_request()
        conn = Mock()

        with (
            patch("gardenops.db.get_db", return_value=conn),
            patch(
                "gardenops.db.return_db",
            ) as return_db,
        ):
            dep = db.db_dep(request)
            yielded = next(dep)

            self.assertIs(yielded, conn)
            self.assertIs(db.request_scoped_db_conn(request), conn)

            with self.assertRaises(StopIteration):
                next(dep)

        return_db.assert_called_once_with(conn)
        self.assertIsNone(db.request_scoped_db_conn(request))
