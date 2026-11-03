"""schema v21:銷售單加會員折扣,舊單保留且為 0。"""
from pathlib import Path

from base import ConnTestCase
from lib import db_schema
from lib.db import get_conn, init_db


class MemberDiscountMigrationTests(ConnTestCase):
    def test_upgrade_keeps_old_sale_with_zero(self):
        self.conn.close()
        Path(self.db).unlink()
        column = "  member_discount INTEGER NOT NULL DEFAULT 0,  -- 會員折扣(元,客人以點數折換)\n"
        self.assertIn(column, db_schema.SCHEMA)
        conn = get_conn(self.db)
        conn.executescript(db_schema.SCHEMA.replace(column, ""))
        conn.execute("INSERT INTO Setting(key,value) VALUES('schema_version',?)", (str(db_schema.BASE_VERSION + 19),))
        conn.execute("INSERT INTO Sale(sale_id,payment,order_discount,total,paid) VALUES(5,'現金',30,70,70)")
        conn.commit(); conn.close()

        init_db(self.db)
        self.conn = get_conn(self.db)
        self.assertEqual(self.conn.execute("SELECT value FROM Setting WHERE key='schema_version'").fetchone()[0],
                         str(db_schema.SCHEMA_VERSION))
        self.assertEqual(tuple(self.conn.execute("SELECT sale_id,order_discount,member_discount,total FROM Sale").fetchone()),
                         (5, 30, 0, 70))
