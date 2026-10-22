"""有父子資料的盤點表升級、兩個 DDL 失敗點的完整回滾。"""
import re
import sqlite3
from pathlib import Path
from unittest.mock import patch

from base import ConnTestCase
from lib import db_schema
from lib.db import get_conn, init_db
from lib.auth import AdminSession
from tools.make_standard_db import build


OLD_SESSIONS = """CREATE TABLE StocktakeSession(
  session_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  ended_at TEXT,
  employee INTEGER CHECK(employee BETWEEN 1 AND 6),
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
  note TEXT
);"""
OLD_ITEMS = """CREATE TABLE StocktakeItem(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES StocktakeSession(session_id),
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  system_qty INTEGER NOT NULL,
  counted_qty INTEGER NOT NULL DEFAULT 0,
  UNIQUE(session_id,variant_id)
);"""


class StocktakeApprovalMigrationTests(ConnTestCase):
    def make_old_database(self):
        self.conn.close()
        Path(self.db).unlink()
        conn = get_conn(self.db)
        schema = re.sub(r"CREATE TABLE IF NOT EXISTS StocktakeSession\(.*?\n\);", OLD_SESSIONS,
                        db_schema.SCHEMA, flags=re.S)
        schema = re.sub(r"CREATE TABLE IF NOT EXISTS StocktakeItem\(.*?\n\);", OLD_ITEMS,
                        schema, flags=re.S)
        schema = re.sub(r"CREATE UNIQUE INDEX IF NOT EXISTS idx_stocktake_one_active.*?;", "", schema, flags=re.S)
        conn.executescript(schema)
        conn.execute("INSERT INTO Setting(key,value) VALUES('schema_version',?)", (str(db_schema.BASE_VERSION + 16),))
        conn.execute("INSERT INTO Category(category_id,name) VALUES(1,'migration category')")
        conn.execute("INSERT INTO Product(product_id,name,category_id) VALUES(1,'migration product',1)")
        conn.execute("INSERT INTO Variant(variant_id,product_id) VALUES(1,1)")
        conn.execute("INSERT INTO StocktakeSession(session_id,started_at,ended_at,employee,status,note) VALUES(7,'old-start','old-end',1,'closed','closed note')")
        conn.execute("INSERT INTO StocktakeSession(session_id,started_at,employee,status,note) VALUES(11,'open-start',2,'open','open note')")
        conn.execute("INSERT INTO StocktakeItem(id,session_id,variant_id,system_qty,counted_qty) VALUES(21,7,1,10,8)")
        conn.execute("INSERT INTO StocktakeItem(id,session_id,variant_id,system_qty,counted_qty) VALUES(25,11,1,8,7)")
        conn.commit()
        self.conn = conn

    def snapshot(self):
        return {table: (self.conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0],
                        [tuple(row) for row in self.conn.execute(f"SELECT * FROM {table} ORDER BY 1")])
                for table in ("StocktakeSession", "StocktakeItem", "Setting")}

    def test_upgrade_preserves_ids_rows_constraints_and_next_ids(self):
        self.make_old_database()
        before = self.snapshot()
        init_db(self.db)
        for table in ("StocktakeSession", "StocktakeItem"):
            columns = [row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")]
            old_columns = ("session_id,started_at,ended_at,employee,status,note" if table == "StocktakeSession"
                           else "id,session_id,variant_id,system_qty,counted_qty")
            self.assertEqual([tuple(row) for row in self.conn.execute(f"SELECT {old_columns} FROM {table} ORDER BY 1")], before[table][1])
            self.assertIn("submitted_at" if table == "StocktakeSession" else "needs_recount", columns)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.conn.execute("UPDATE StocktakeSession SET status='pending' WHERE session_id=11")
        self.conn.commit()
        self.assertEqual(self.conn.execute("SELECT status FROM StocktakeSession WHERE session_id=11").fetchone()[0], "pending")
        self.conn.execute("UPDATE StocktakeSession SET status='closed' WHERE session_id=11")
        sid = self.conn.execute("INSERT INTO StocktakeSession(employee) VALUES(1)").lastrowid
        self.assertGreater(sid, 11)
        iid = self.conn.execute("INSERT INTO StocktakeItem(session_id,variant_id,system_qty) VALUES(?,1,0)", (sid,)).lastrowid
        self.assertGreater(iid, 25)
        self.assertEqual(self.conn.execute("SELECT needs_recount FROM StocktakeItem WHERE id=21").fetchone()[0], 0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO StocktakeItem(session_id,variant_id,system_qty) VALUES(?,1,0)", (sid,))
        self.conn.rollback()
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='idx_stocktake_one_active'").fetchone())

    def _assert_failure_rolls_back(self, failure_point):
        self.make_old_database()
        before = self.snapshot()
        real_get_conn = get_conn
        hit = []

        class FailingConnection:
            def __init__(self, conn):
                self.conn = conn

            def __getattr__(self, name):
                return getattr(self.conn, name)

            def execute(self, sql, parameters=()):
                result = self.conn.execute(sql, parameters)
                normalized = " ".join(sql.upper().replace('"', '').replace('[', '').replace(']', '').split())
                if failure_point(normalized):
                    hit.append(normalized)
                    raise RuntimeError("injected migration failure")
                return result

        with patch("lib.db.get_conn", side_effect=lambda path: FailingConnection(real_get_conn(path))):
            with self.assertRaisesRegex(RuntimeError, "injected migration failure"):
                init_db(self.db)
        self.assertEqual(len(hit), 1, "必須真的命中指定的 DDL 失敗點")
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.conn.execute("SELECT name FROM sqlite_master WHERE name IN ('StocktakeItem_mig','StocktakeSession_mig')").fetchall(), [])

    def test_failure_immediately_after_drop_child_restores_original_tables(self):
        self._assert_failure_rolls_back(lambda sql: sql == "DROP TABLE STOCKTAKEITEM")

    def test_failure_after_parent_rename_restores_original_tables(self):
        self._assert_failure_rolls_back(lambda sql: "ALTER TABLE STOCKTAKESESSION_MIG RENAME TO STOCKTAKESESSION" in sql)


class StandardDatabasePasswordTests(ConnTestCase):
    def test_standard_database_uses_initial_password_and_omits_source_password(self):
        source = AdminSession(self.db)
        source.login("admin")
        source.change_password("admin", "Source-store-password")
        source_hash = self.conn.execute("SELECT value FROM Setting WHERE key='admin_password_hash'").fetchone()[0]
        output = Path(self.tmp) / "standard.db"
        build(Path(self.db), output)
        target = AdminSession(str(output))
        self.assertFalse(target.login("Source-store-password"))
        self.assertTrue(target.login("admin"))
        target_conn = get_conn(str(output))
        try:
            self.assertNotEqual(source_hash, target_conn.execute("SELECT value FROM Setting WHERE key='admin_password_hash'").fetchone()[0])
        finally:
            target_conn.close()
