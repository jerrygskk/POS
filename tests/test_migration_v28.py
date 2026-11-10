"""v27→v28 升級:原名重建 Sale／StockMovement,單一交易,失敗完整回復。

出 1.0.0 移除升級步驟時,連同 tests/fixtures/schema_v27.sql 一起刪除。
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from lib import db_schema, db_seed
from lib.db import get_conn, init_db, read_schema_version
from lib.desktop_application import DesktopFacade
from lib.runtime_paths import RuntimePaths

FIXTURE = Path(__file__).parent / "fixtures" / "schema_v27.sql"

# 四種異動來源(進貨、銷售、盤點、退貨／返新)都有,且有刪過的列讓流水號大於現有最大編號
DATA = """
INSERT INTO Setting VALUES('schema_version','27'),('payments','["現金","轉帳","行動支付"]'),
  ('admin_password_hash','x'),('next_store_barcode','5');
INSERT INTO Category(category_id,name) VALUES(1,'膜');
INSERT INTO DeviceType(device_type_id,name,sort) VALUES(1,'手機',1);
INSERT INTO Product(product_id,name,category_id) VALUES(1,'膜A',1);
INSERT INTO Variant(variant_id,product_id,price) VALUES(1,1,100),(2,1,200);
INSERT INTO Member(member_id,name) VALUES(1,'會員');
INSERT INTO Receipt(receipt_id,employee) VALUES(1,1);
INSERT INTO ReceiptItem(receipt_item_id,receipt_id,variant_id,qty) VALUES(1,1,1,10),(2,1,2,10);
INSERT INTO StockMovement(variant_id,qty,kind,receipt_item_id) VALUES(1,10,'purchase',1),(2,10,'purchase',2);
INSERT INTO Sale(sale_id,ts,payment,order_discount,member_discount,total,paid,change,employee,member_id) VALUES
  (1,'2026-11-01 10:00:00','現金',0,0,100,500,400,1,1),
  (2,'2026-11-02 23:59:59','轉帳',5,10,185,185,0,NULL,NULL),
  (7,'2026-11-03 09:00:00','現金',0,0,0,0,0,2,NULL);
DELETE FROM Sale WHERE sale_id=7;
INSERT INTO SaleItem(item_id,sale_id,variant_id,qty,unit_price,discount) VALUES(1,1,1,1,100,0),(2,2,2,1,200,0);
INSERT INTO StockMovement(variant_id,qty,kind,sale_item_id,ts) VALUES
  (1,-1,'sale',1,'2026-11-01 10:00:00'),(2,-1,'sale',2,'2026-11-02 23:59:59');
INSERT INTO StocktakeSession(session_id,employee,status,approved_at) VALUES(1,1,'closed','2026-11-03 10:00:00');
INSERT INTO StocktakeItem(id,session_id,variant_id,system_qty,counted_qty) VALUES(1,1,1,9,8);
INSERT INTO StockMovement(variant_id,qty,kind,stocktake_item_id,note) VALUES(1,-1,'adjust',1,'盤點調整');
INSERT INTO ReturnSession(return_id,employee,status,kind) VALUES(1,1,'closed','normal'),(2,1,'repairing','repair');
INSERT INTO ReturnItem(id,return_id,variant_id,qty,sale_id,disposition) VALUES(1,1,1,1,1,'restock'),(2,2,2,2,2,NULL);
INSERT INTO StockMovement(variant_id,qty,kind,return_item_id,note) VALUES(1,1,'return',1,'退貨加回');
INSERT INTO StockMovement(variant_id,qty,kind,return_item_id,note,employee) VALUES(2,1,'repair',2,'返新進貨',3);
UPDATE sqlite_sequence SET seq=60 WHERE name='StockMovement';
"""


def make_v27(path):
    conn = sqlite3.connect(path)
    conn.executescript(FIXTURE.read_text(encoding="utf-8"))
    conn.executescript(DATA)
    conn.commit()
    conn.close()


def dump(path):
    conn = sqlite3.connect(path)
    try:
        return list(conn.iterdump()), conn.execute("SELECT name,seq FROM sqlite_sequence ORDER BY name").fetchall()
    finally:
        conn.close()


def objects(path):
    """資料表、索引、trigger 的名稱與定義(不含 SQLite 自動建立的)。"""
    conn = sqlite3.connect(path)
    try:
        return sorted(conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall())
    finally:
        conn.close()


class MigrationV28Test(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.db = self.root / "pos.db"
        make_v27(self.db)

    def rows(self, sql):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def test_versions(self):
        self.assertEqual((db_schema.BASE_VERSION, db_schema.MIN_SUPPORTED_VERSION, db_schema.SCHEMA_VERSION), (27, 27, 28))

    def test_schema_statements_cover_every_object(self):
        fresh = self.root / "fresh.db"
        init_db(fresh)
        statements = db_schema.statements()
        self.assertEqual(len(statements), len(objects(fresh)))
        for statement in statements:
            code = [l for l in statement.splitlines() if not l.strip().startswith("--")]
            self.assertTrue(code[0].startswith("CREATE "), statement[:60])

    def test_upgrade_keeps_rows_keys_sequences_and_rebuilds_objects(self):
        sales = self.rows("SELECT * FROM Sale ORDER BY sale_id")
        moves = self.rows("SELECT * FROM StockMovement ORDER BY move_id")
        others = {t: self.rows(f"SELECT * FROM {t}") for t in
                  ("SaleItem", "ReturnItem", "ReceiptItem", "StocktakeItem", "Variant", "Member")}
        init_db(self.db, require_existing=True)   # 提交成功
        self.assertEqual(read_schema_version(self.db), 28)
        self.assertEqual(self.rows(
            "SELECT sale_id,ts,payment,order_discount,member_discount,total,paid,change,employee,member_id "
            "FROM Sale ORDER BY sale_id"), sales)
        self.assertEqual(self.rows("SELECT sale_id,business_date,voided_at,void_reason FROM Sale ORDER BY sale_id"),
                         [(1, "2026-11-01", None, None), (2, "2026-11-02", None, None)])
        self.assertEqual(self.rows("SELECT * FROM StockMovement ORDER BY move_id"), moves)
        for table, before in others.items():
            self.assertEqual(self.rows(f"SELECT * FROM {table}"), before, table)
        self.assertEqual(dict(self.rows("SELECT name,seq FROM sqlite_sequence WHERE name IN ('Sale','StockMovement')")),
                         {"Sale": 7, "StockMovement": 60})
        fresh = self.root / "fresh.db"
        init_db(fresh)
        self.assertEqual(objects(self.db), objects(fresh))   # 表、索引、trigger 與全新資料庫完全相同
        self.assertEqual(self.rows("PRAGMA foreign_key_check"), [])
        self.assertEqual(self.rows("PRAGMA integrity_check"), [("ok",)])
        self.assertEqual(self.rows("SELECT value FROM Setting WHERE key='cash_float'"), [("0",)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM DayClose"), [(0,)])
        self.assertEqual(self.rows("SELECT name FROM sqlite_temp_master"), [])

    def test_upgraded_database_reopens_and_works(self):
        init_db(self.db, require_existing=True)
        init_db(self.db, require_existing=True)   # 關閉後重開,不再升級也不出錯
        facade = DesktopFacade(self.db)
        self.assertEqual(facade.invoke("stock.detail", {"variant_id": 1})["stock"], 9)
        sold = facade.invoke("sales.checkout", {"payment": "現金", "paid": 100, "employee": 1,
                                                "items": [{"variant_id": 1, "qty": 1, "unit_price": 100}]})
        self.assertEqual(sold["sale_id"], 8)   # 流水號接續,不重用刪過的 7
        self.assertEqual(facade.invoke("stock.detail", {"variant_id": 1})["stock"], 8)
        old = facade.invoke("sales.list", {"date_from": "2026-11-01", "date_to": "2026-11-02"})
        self.assertEqual([(s["sale_id"], s["business_date"]) for s in old], [(2, "2026-11-02"), (1, "2026-11-01")])
        # 已入帳明細與異動的鎖定仍在
        conn = get_conn(self.db)
        try:
            for sql in ("DELETE FROM StockMovement WHERE move_id=1", "UPDATE SaleItem SET qty=5 WHERE item_id=1",
                        "UPDATE Sale SET total=1 WHERE sale_id=1"):
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(sql)
        finally:
            conn.close()

    def test_failure_after_migration_rolls_everything_back(self):
        before = dump(self.db)
        with patch.object(db_seed, "seed", side_effect=RuntimeError("種子失敗")):
            with self.assertRaisesRegex(RuntimeError, "種子失敗"):
                init_db(self.db, require_existing=True)
        self.assertEqual(dump(self.db), before)   # 版號、表、索引、trigger、資料、流水號全部與升級前相同
        self.assertEqual(read_schema_version(self.db), 27)
        init_db(self.db, require_existing=True)   # 之後仍可正常升級
        self.assertEqual(read_schema_version(self.db), 28)

    def test_foreign_key_violation_rolls_back(self):
        conn = sqlite3.connect(self.db)   # 未開外鍵檢查:造一筆指向不存在銷售單的明細
        conn.execute("INSERT INTO SaleItem(item_id,sale_id,variant_id,qty,unit_price) VALUES(9,999,1,1,100)")
        conn.commit()
        conn.close()
        before = dump(self.db)
        with self.assertRaises(Exception):
            init_db(self.db, require_existing=True)
        self.assertEqual(dump(self.db), before)

    def test_sale_with_unparsable_time_stops_upgrade_and_lists_ids(self):
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE Sale SET ts='時間寫壞' WHERE sale_id=2")
        conn.commit()
        conn.close()
        before = dump(self.db)
        with self.assertRaisesRegex(RuntimeError, "單號 2"):
            init_db(self.db, require_existing=True)
        self.assertEqual(dump(self.db), before)

    def test_startup_backs_up_before_upgrading(self):
        paths = RuntimePaths.from_root(self.root)
        main.prepare_runtime(paths)
        backups = sorted(paths.backup_dir.glob("pos_upgrade_*_v27-v28.db"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(read_schema_version(backups[0]), 27)
        self.assertEqual(read_schema_version(paths.db_path), 28)


if __name__ == "__main__":
    unittest.main()
