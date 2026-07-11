import unittest, tempfile, os
from unittest import mock
from lib import db_schema
from lib.db import get_conn, init_db, _get_schema_version


def _version(db):
    conn = get_conn(db)
    try:
        return _get_schema_version(conn)
    finally:
        conn.close()


class TestMigrations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pos.db")

    def test_new_db_has_latest_version(self):
        init_db(self.db)
        self.assertEqual(_version(self.db), db_schema.SCHEMA_VERSION)

    def test_legacy_db_gets_version_backfilled(self):
        # 模擬舊版 DB:建好後移除 schema_version
        init_db(self.db)
        conn = get_conn(self.db)
        conn.execute("DELETE FROM Setting WHERE key='schema_version'")
        conn.commit()
        conn.close()
        # 再跑 init_db 應補回版號
        init_db(self.db)
        self.assertEqual(_version(self.db), db_schema.SCHEMA_VERSION)

    def test_migrations_run_in_order(self):
        init_db(self.db)
        # 模擬停在初版的 DB
        conn = get_conn(self.db)
        conn.execute(
            "INSERT OR REPLACE INTO Setting(key,value) VALUES('schema_version','1')")
        conn.commit()
        conn.close()

        order = []

        def mk(v):
            def _m(conn):
                order.append(v)
                conn.execute(
                    "INSERT INTO Setting(key,value) VALUES(?,?)",
                    (f"mig_{v}", "done"))
            return _m

        fake = [(2, mk(2)), (3, mk(3))]
        with mock.patch.object(db_schema, "MIGRATIONS", fake), \
             mock.patch.object(db_schema, "SCHEMA_VERSION", 3):
            init_db(self.db)

        self.assertEqual(order, [2, 3])
        self.assertEqual(_version(self.db), 3)
        conn = get_conn(self.db)
        keys = {r["key"] for r in conn.execute(
            "SELECT key FROM Setting WHERE key LIKE 'mig_%'")}
        conn.close()
        self.assertEqual(keys, {"mig_2", "mig_3"})

    def test_migrations_below_current_skipped(self):
        init_db(self.db)
        conn = get_conn(self.db)
        conn.execute(
            "INSERT OR REPLACE INTO Setting(key,value) VALUES('schema_version','2')")
        conn.commit()
        conn.close()

        order = []

        def mk(v):
            def _m(conn):
                order.append(v)
            return _m

        fake = [(2, mk(2)), (3, mk(3))]
        with mock.patch.object(db_schema, "MIGRATIONS", fake), \
             mock.patch.object(db_schema, "SCHEMA_VERSION", 3):
            init_db(self.db)

        self.assertEqual(order, [3])  # 版號 2 已達,只跑升 3
        self.assertEqual(_version(self.db), 3)


if __name__ == "__main__":
    unittest.main()
