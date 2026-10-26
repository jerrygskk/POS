"""schema v20:庫存流水加退貨種類、盤點加作廢與品項備註，舊資料與流水號保留。"""
import re
from pathlib import Path

from base import ConnTestCase
from lib import db_schema
from lib.db import get_conn, init_db


def _old(schema):
    schema = schema.replace("CHECK(kind IN ('purchase','sale','adjust','return'))",
                            "CHECK(kind IN ('purchase','sale','adjust'))")
    schema = schema.replace("  cancelled_at TEXT,\n  employee INTEGER CHECK(employee BETWEEN 1 AND 6),  -- 盤點員工代號\n"
                            "  cancelled_by INTEGER CHECK(cancelled_by BETWEEN 1 AND 6),  -- 作廢員工代號\n"
                            "  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','pending','closed','cancelled')),",
                            "  employee INTEGER CHECK(employee BETWEEN 1 AND 6),\n"
                            "  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','pending','closed')),")
    schema = schema.replace("  note TEXT,                      -- 差異原因(選填)\n", "")
    return re.sub(r"CREATE TABLE IF NOT EXISTS Return(Session|Item)\(.*?\n\);", "", schema, flags=re.S)


class ReturnsMigrationTests(ConnTestCase):
    def test_upgrade_keeps_rows_and_allows_new_states(self):
        self.conn.close()
        Path(self.db).unlink()
        conn = get_conn(self.db)
        old = _old(db_schema.SCHEMA)
        self.assertNotIn("'return'", old)
        self.assertNotIn("cancelled", old)
        conn.executescript(old)
        conn.execute("INSERT INTO Setting(key,value) VALUES('schema_version',?)", (str(db_schema.BASE_VERSION + 18),))
        conn.execute("INSERT INTO Category(category_id,name) VALUES(1,'c')")
        conn.execute("INSERT INTO Product(product_id,name,category_id) VALUES(1,'p',1)")
        conn.execute("INSERT INTO Variant(variant_id,product_id) VALUES(1,1)")
        conn.execute("INSERT INTO StockMovement(move_id,variant_id,qty,kind,employee) VALUES(40,1,9,'purchase',2)")
        conn.execute("INSERT INTO StocktakeSession(session_id,employee,status,submitted_at) VALUES(7,1,'pending','t')")
        conn.execute("INSERT INTO StocktakeItem(id,session_id,variant_id,system_qty,counted_qty,needs_recount) VALUES(30,7,1,9,8,1)")
        conn.commit(); conn.close()

        init_db(self.db)
        self.conn = get_conn(self.db)
        c = self.conn
        self.assertEqual(c.execute("SELECT value FROM Setting WHERE key='schema_version'").fetchone()[0],
                         str(db_schema.SCHEMA_VERSION))
        self.assertEqual(tuple(c.execute("SELECT move_id,qty,kind,employee FROM StockMovement").fetchone()), (40, 9, "purchase", 2))
        self.assertEqual(tuple(c.execute("SELECT session_id,status,submitted_at FROM StocktakeSession").fetchone()), (7, "pending", "t"))
        self.assertEqual(tuple(c.execute("SELECT id,counted_qty,needs_recount,note FROM StocktakeItem").fetchone()), (30, 8, 1, None))
        c.execute("INSERT INTO StockMovement(variant_id,qty,kind) VALUES(1,1,'return')")
        self.assertGreater(c.execute("SELECT MAX(move_id) FROM StockMovement").fetchone()[0], 40)
        c.execute("UPDATE StocktakeSession SET status='cancelled' WHERE session_id=7")
        c.execute("INSERT INTO ReturnSession(employee) VALUES(1)")
        self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertIsNotNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='idx_stocktake_one_active'").fetchone())
        self.assertIsNotNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='idx_move_variant'").fetchone())
