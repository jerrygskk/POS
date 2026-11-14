"""v28→v29 升級:付款方式搬進 PaymentMethod、Sale 補編號、已立帳帳日補快照;單一交易,失敗完整回復。

出 1.0.0 移除升級步驟時,連同 tests/fixtures/schema_v28.sql 一起刪除。
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import db_schema, db_seed
from lib.db import get_conn, init_db, read_schema_version
from lib.desktop_application import DesktopFacade

FIXTURE = Path(__file__).parent / "fixtures" / "schema_v28.sql"

# 11-01、11-02 已立帳,11-03 未立帳;有清單外的舊名稱(行動支付)、已作廢的單、刪過的單號
DATA = """
INSERT INTO Setting VALUES('schema_version','28'),('payments','["現金","轉帳","LINE Pay","台灣Pay"]'),
  ('payments_disabled','["台灣Pay"]'),('payment_fees','{"LINE Pay": 2.2}'),
  ('admin_password_hash','x'),('next_store_barcode','5'),('cash_float','0');
INSERT INTO Category(category_id,name) VALUES(1,'膜');
INSERT INTO DeviceType(device_type_id,name,sort) VALUES(1,'手機',1);
INSERT INTO Product(product_id,name,category_id) VALUES(1,'膜A',1);
INSERT INTO Variant(variant_id,product_id,price) VALUES(1,1,100);
INSERT INTO Receipt(receipt_id,employee) VALUES(1,1);
INSERT INTO ReceiptItem(receipt_item_id,receipt_id,variant_id,qty) VALUES(1,1,1,10);
INSERT INTO StockMovement(variant_id,qty,kind,receipt_item_id) VALUES(1,10,'purchase',1);
INSERT INTO Sale(sale_id,ts,payment,total,paid,change,employee,business_date,voided_at,void_reason) VALUES
  (1,'2026-11-01 10:00:00','現金',100,500,400,1,'2026-11-01',NULL,NULL),
  (2,'2026-11-01 11:00:00','LINE Pay',1000,1000,0,1,'2026-11-01',NULL,NULL),
  (3,'2026-11-01 12:00:00','LINE Pay',500,500,0,1,'2026-11-01',NULL,NULL),
  (4,'2026-11-01 13:00:00','現金',999,999,0,1,'2026-11-01','2026-11-01 13:05:00','重複結帳'),
  (5,'2026-11-02 10:00:00','行動支付',300,300,0,2,'2026-11-02',NULL,NULL),
  (6,'2026-11-03 10:00:00','轉帳',200,200,0,NULL,'2026-11-03',NULL,NULL),
  (9,'2026-11-03 11:00:00','現金',0,0,0,2,'2026-11-03',NULL,NULL);
DELETE FROM Sale WHERE sale_id=9;
INSERT INTO SaleItem(item_id,sale_id,variant_id,qty,unit_price,discount) VALUES(1,1,1,1,100,0);
INSERT INTO StockMovement(variant_id,qty,kind,sale_item_id) VALUES(1,-1,'sale',1);
INSERT INTO DayClose(business_date,employee,float_amount,counted_total,cash_sales,diff) VALUES
  ('2026-11-01',1,0,100,100,0),('2026-11-02',1,0,0,0,0);
INSERT INTO Correction(sale_id,field,old_value,new_value) VALUES(6,'payment','現金','轉帳');
"""


def make_v28(path):
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
    conn = sqlite3.connect(path)
    try:
        return sorted(conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall())
    finally:
        conn.close()


class MigrationV29Test(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.db = self.root / "pos.db"
        make_v28(self.db)

    def rows(self, sql):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def test_upgrade_moves_payments_fills_ids_and_snapshots(self):
        old_columns = "sale_id,ts,payment,order_discount,member_discount,total,paid,change,employee,member_id,business_date,voided_at,void_reason"
        sales = self.rows(f"SELECT {old_columns} FROM Sale ORDER BY sale_id")
        others = {t: self.rows(f"SELECT * FROM {t}") for t in ("SaleItem", "StockMovement", "DayClose", "Correction")}
        init_db(self.db, require_existing=True)
        self.assertEqual(read_schema_version(self.db), 29)
        # 設定清單照原順序搬入;舊單上清單外的名稱建成停用項目,排最後;現金恰好一筆
        self.assertEqual(self.rows("SELECT payment_id,name,sort,active,fee_rate,is_cash FROM PaymentMethod ORDER BY payment_id"),
                         [(1, "現金", 1, 1, 0, 1), (2, "轉帳", 2, 1, 0, 0), (3, "LINE Pay", 3, 1, 220, 0),
                          (4, "台灣Pay", 4, 0, 0, 0), (5, "行動支付", 5, 0, 0, 0)])
        self.assertEqual(self.rows("SELECT key FROM Setting WHERE key LIKE 'payment%'"), [])
        # 銷售單原欄位不變,編號依名稱補上,流水號接續
        self.assertEqual(self.rows(f"SELECT {old_columns} FROM Sale ORDER BY sale_id"), sales)
        self.assertEqual(self.rows("SELECT sale_id,payment_id FROM Sale ORDER BY sale_id"),
                         [(1, 1), (2, 3), (3, 3), (4, 1), (5, 5), (6, 2)])
        self.assertEqual(self.rows("SELECT seq FROM sqlite_sequence WHERE name='Sale'"), [(9,)])
        for table, before in others.items():
            self.assertEqual(self.rows(f"SELECT * FROM {table}"), before, table)
        # 已立帳帳日補快照(不含作廢、只有有交易的),比例是升級當下的值;未立帳的 11-03 不補
        self.assertEqual(self.rows("SELECT business_date,payment_id,name,count,total,fee_rate FROM DayClosePayment "
                                   "ORDER BY business_date,payment_id"),
                         [("2026-11-01", 1, "現金", 1, 100, 0), ("2026-11-01", 3, "LINE Pay", 2, 1500, 220),
                          ("2026-11-02", 5, "行動支付", 1, 300, 0)])
        fresh = self.root / "fresh.db"
        init_db(fresh)
        self.assertEqual(objects(self.db), objects(fresh))   # 表、索引、trigger 與全新資料庫完全相同
        self.assertEqual(self.rows("PRAGMA foreign_key_check"), [])
        self.assertEqual(self.rows("PRAGMA integrity_check"), [("ok",)])
        self.assertEqual(self.rows("SELECT name FROM sqlite_temp_master"), [])

    def test_upgraded_database_reopens_and_works(self):
        init_db(self.db, require_existing=True)
        init_db(self.db, require_existing=True)   # 重開不再升級,種子也不重建付款方式
        self.assertEqual(self.rows("SELECT COUNT(*) FROM PaymentMethod"), [(5,)])
        facade = DesktopFacade(self.db)
        # 測試資料的密碼雜湊是假的,直接給管理者身分
        facade.session._admin, facade.session._last_activity = True, facade.session._clock()
        records = {r["business_date"]: r for r in facade.invoke("dayclose.list", {})}
        self.assertEqual([(b["payment"], b["total"], b["fee_rate"]) for b in records["2026-11-01"]["by_payment"]],
                         [("現金", 100, 0), ("轉帳", 0, 0), ("LINE Pay", 1500, 220), ("行動支付", 0, 0)])
        self.assertEqual(records["2026-11-02"]["sales_total"], 300)
        # 舊名稱的單用編號篩得到;未立帳的單可更正成啟用中的付款方式
        self.assertEqual(facade.invoke("sales.summary", {"payment_id": 5})["total"], 300)
        facade.invoke("sales.set_payment", {"sale_id": 6, "payment_id": 3, "expected_payment_id": 2})
        self.assertEqual(self.rows("SELECT payment_id,payment FROM Sale WHERE sale_id=6"), [(3, "LINE Pay")])
        conn = get_conn(self.db)
        try:
            for sql in ("UPDATE Sale SET payment_id=2 WHERE sale_id=1",       # 已立帳的單連編號一起鎖
                        "UPDATE Sale SET payment_id=2 WHERE sale_id=4",       # 已作廢
                        "DELETE FROM DayClosePayment",
                        "INSERT INTO DayClosePayment VALUES('2026-11-01',2,'轉帳',1,1,0)"):
                with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                    conn.execute(sql)
        finally:
            conn.close()

    def test_missing_cash_and_broken_settings_still_produce_one_cash(self):
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE Setting SET value='[\"轉帳\", \" 轉帳 \", 5]' WHERE key='payments'")
        conn.execute("UPDATE Setting SET value='壞掉' WHERE key='payment_fees'")
        conn.execute("DELETE FROM Setting WHERE key='payments_disabled'")
        conn.commit()
        conn.close()
        init_db(self.db, require_existing=True)
        self.assertEqual(self.rows("SELECT name,active,fee_rate,is_cash FROM PaymentMethod ORDER BY sort,payment_id"),
                         [("現金", 1, 0, 1), ("轉帳", 1, 0, 0), ("LINE Pay", 0, 0, 0), ("行動支付", 0, 0, 0)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM Sale WHERE payment_id IS NULL"), [(0,)])

    def test_failure_after_migration_rolls_everything_back(self):
        before = dump(self.db)
        with patch.object(db_seed, "seed", side_effect=RuntimeError("種子失敗")):
            with self.assertRaisesRegex(RuntimeError, "種子失敗"):
                init_db(self.db, require_existing=True)
        self.assertEqual(dump(self.db), before)
        self.assertEqual(read_schema_version(self.db), 28)
        init_db(self.db, require_existing=True)
        self.assertEqual(read_schema_version(self.db), db_schema.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
