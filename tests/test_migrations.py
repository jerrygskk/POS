import unittest, tempfile, os, sqlite3
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
        # 模擬真實舊版 DB:無 schema_version,且結構為舊式(含 category_id 的
        # AttributeField)。不可用「全新 v13 DB 再刪版號」模擬——那會讓凍結的
        # v3→v4 遷移在全域化後的 AttributeField 上重跑而失敗,並非真實舊版樣態。
        conn = sqlite3.connect(self.db)
        conn.executescript("""
          CREATE TABLE AttributeField(
            field_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            category_id INTEGER, field_type TEXT NOT NULL DEFAULT 'select',
            sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(category_id, name));
          CREATE TABLE Setting(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        conn.commit()
        conn.close()
        # init_db:無版號視為初版,跑完整 migration 並補回版號至最新
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
