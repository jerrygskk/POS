"""v29→v30 升級:StocktakeItem 加 snapshot_move_id,未結案盤點單退回重新清點;單一交易,失敗完整回復。

出 1.0.0 移除升級步驟時,連同 tests/fixtures/schema_v29.sql 一起刪除。
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import db_schema, db_seed
from lib.application_errors import ConflictError
from lib.db import init_db, read_schema_version
from lib.desktop_application import DesktopFacade

FIXTURE = Path(__file__).parent / "fixtures" / "schema_v29.sql"

# 盤點單:1 已結案(有盤點調整異動)、2 已作廢、3 待核准;明細有刪過的列讓流水號大於現有最大編號
DATA = """
INSERT INTO Setting VALUES('schema_version','29'),('admin_password_hash','x'),('next_store_barcode','5'),('cash_float','0');
INSERT INTO Category(category_id,name) VALUES(1,'膜');
INSERT INTO DeviceType(device_type_id,name,sort) VALUES(1,'手機',1);
INSERT INTO Product(product_id,name,category_id) VALUES(1,'膜A',1);
INSERT INTO Variant(variant_id,product_id,price) VALUES(1,1,100),(2,1,200);
INSERT INTO PaymentMethod(payment_id,name,sort,active,fee_rate,is_cash) VALUES(1,'現金',1,1,0,1);
INSERT INTO Receipt(receipt_id,employee) VALUES(1,1);
INSERT INTO ReceiptItem(receipt_item_id,receipt_id,variant_id,qty) VALUES(1,1,1,10),(2,1,2,10);
INSERT INTO StockMovement(variant_id,qty,kind,receipt_item_id) VALUES(1,10,'purchase',1),(2,10,'purchase',2);
INSERT INTO StocktakeSession(session_id,employee,status,submitted_at,approved_at,cancelled_at) VALUES
  (1,1,'closed','2026-11-03 09:00:00','2026-11-03 10:00:00',NULL),
  (2,1,'cancelled',NULL,NULL,'2026-11-04 10:00:00'),
  (3,2,'pending','2026-11-05 09:00:00',NULL,NULL);
INSERT INTO StocktakeItem(id,session_id,variant_id,system_qty,counted_qty,needs_recount,note) VALUES
  (1,1,1,10,9,0,'少一件'),(2,2,2,10,10,0,NULL),(3,3,1,9,9,0,NULL),(4,3,2,10,7,0,NULL),(9,2,1,0,0,0,NULL);
DELETE FROM StocktakeItem WHERE id=9;
INSERT INTO StockMovement(variant_id,qty,kind,stocktake_item_id,note) VALUES(1,-1,'adjust',1,'盤點調整');
"""
OLD_COLUMNS = "id,session_id,variant_id,system_qty,counted_qty,note"


def make_v29(path):
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


class MigrationV30Test(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.db = self.root / "pos.db"
        make_v29(self.db)

    def rows(self, sql):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def test_upgrade_adds_snapshot_and_sends_active_stocktake_back_to_recount(self):
        items = self.rows(f"SELECT {OLD_COLUMNS} FROM StocktakeItem ORDER BY id")
        moves = self.rows("SELECT * FROM StockMovement ORDER BY move_id")
        init_db(self.db, require_existing=True)
        self.assertEqual(read_schema_version(self.db), 30)
        self.assertEqual(self.rows(f"SELECT {OLD_COLUMNS} FROM StocktakeItem ORDER BY id"), items)
        self.assertEqual(self.rows("SELECT * FROM StockMovement ORDER BY move_id"), moves)
        self.assertEqual(self.rows("SELECT seq FROM sqlite_sequence WHERE name='StocktakeItem'"), [(9,)])
        # 只有未結案那張的明細標成需重新清點,並從待核准退回進行中;已結案、已作廢的不動
        self.assertEqual(self.rows("SELECT id,needs_recount FROM StocktakeItem ORDER BY id"),
                         [(1, 0), (2, 0), (3, 1), (4, 1)])
        self.assertEqual(self.rows("SELECT session_id,status,submitted_at FROM StocktakeSession ORDER BY session_id"),
                         [(1, "closed", "2026-11-03 09:00:00"), (2, "cancelled", None), (3, "open", None)])
        fresh = self.root / "fresh.db"
        init_db(fresh)
        self.assertEqual(objects(self.db), objects(fresh))   # 表、索引、trigger 與全新資料庫完全相同
        self.assertEqual(self.rows("PRAGMA foreign_key_check"), [])
        self.assertEqual(self.rows("PRAGMA integrity_check"), [("ok",)])
        self.assertEqual(self.rows("SELECT name FROM sqlite_temp_master"), [])

    def test_upgraded_stocktake_must_be_recounted_and_closed_item_stays_locked(self):
        init_db(self.db, require_existing=True)
        init_db(self.db, require_existing=True)   # 重開不再升級
        facade = DesktopFacade(self.db)
        # 測試資料的密碼雜湊是假的,直接給管理者身分
        facade.session._admin, facade.session._last_activity = True, facade.session._clock()
        with self.assertRaises(ConflictError):
            facade.invoke("stocktake.close", {"session_id": 3})
        # 掃描累加不算重盤:標記還在,仍送不出去
        facade.invoke("stocktake.scan", {"session_id": 3, "variant_id": 1, "qty": 1})
        self.assertEqual(self.rows("SELECT needs_recount FROM StocktakeItem WHERE id=3"), [(1,)])
        with self.assertRaises(ConflictError):
            facade.invoke("stocktake.close", {"session_id": 3})
        for vid, qty in ((1, 9), (2, 8)):
            facade.invoke("stocktake.set_counted", {"session_id": 3, "variant_id": vid, "counted_qty": qty})
        facade.invoke("stocktake.close", {"session_id": 3})
        facade.invoke("stocktake.approve", {"session_id": 3})
        self.assertEqual(facade.invoke("stock.detail", {"variant_id": 2})["stock"], 8)
        conn = sqlite3.connect(self.db)
        try:
            with self.assertRaises(sqlite3.IntegrityError):   # 已入帳明細連快照編號一起鎖
                conn.execute("UPDATE StocktakeItem SET snapshot_move_id=0 WHERE id=1")
        finally:
            conn.close()

    def test_failure_after_migration_rolls_everything_back(self):
        before = dump(self.db)
        with patch.object(db_seed, "seed", side_effect=RuntimeError("種子失敗")):
            with self.assertRaisesRegex(RuntimeError, "種子失敗"):
                init_db(self.db, require_existing=True)
        self.assertEqual(dump(self.db), before)
        self.assertEqual(read_schema_version(self.db), 29)
        init_db(self.db, require_existing=True)
        self.assertEqual(read_schema_version(self.db), db_schema.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
