"""母版工具:唯讀讀 v24 主檔、轉成最新格式寫到新檔;失敗不留輸出、不動來源。"""
import hashlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from lib import db_schema
from lib.db import get_conn, init_db, read_schema_version
from tools import make_standard_db as tool

FIXTURE = Path(__file__).parent / "fixtures" / "schema_v24.sql"


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_v24(path):
    """建一份 v24 結構的來源庫:主檔、會員、少量交易與刪過的列(流水號大於現有最大編號)。"""
    conn = sqlite3.connect(path)
    conn.executescript(FIXTURE.read_text(encoding="utf-8"))
    sql = """
    INSERT INTO Setting VALUES('schema_version','24'),('payments','["現金","轉帳","行動支付"]'),
      ('next_store_barcode','42'),('admin_password_hash','來源密碼不可被帶走');
    INSERT INTO Category(category_id,name,sort,model_mode) VALUES(1,'鋼化玻璃',1,'required'),(2,'手機殼',2,'hidden');
    INSERT INTO AttributeField(field_id,name,field_type,active) VALUES
      (1,'材質','select',1),(2,'備註','text',1),(3,'顏色','select',1),(4,'特性詞條','tags',0),(5,'款式','select',1);
    INSERT INTO AttributeOption(option_id,field_id,value,sort,active) VALUES
      (10,1,'亮面',1,1),(11,1,'霧面',2,0),(12,3,'黑',1,1),(13,4,'磁吸',1,1),(14,5,'皮套',1,1);
    INSERT INTO CategoryField(category_id,field_id,sort,required,default_option_id,active) VALUES
      (1,1,2,1,10,1),(1,2,1,0,NULL,1),(1,4,3,0,NULL,1),(2,3,1,0,12,0),(2,5,2,1,NULL,1);
    INSERT INTO Brand(brand_id,name) VALUES(1,'HODA'),(2,'犀牛盾');
    INSERT INTO BrandCategory VALUES(1,1),(2,2);
    INSERT INTO PhoneBrand(phone_brand_id,name) VALUES(1,'iPhone');
    INSERT INTO DeviceType(device_type_id,name) VALUES(1,'手機'),(2,'平板');
    INSERT INTO PhoneModel(model_id,phone_brand_id,device_type_id,name,alias) VALUES(1,1,1,'17 Pro','i17 Pro');
    INSERT INTO OptionModel VALUES(10,1);
    INSERT INTO Product(product_id,name,category_id,brand_id) VALUES(1,'HODA 鋼化玻璃',1,1),(2,'犀牛盾 手機殼',2,2),(9,'已刪除',1,1);
    DELETE FROM Product WHERE product_id=9;
    INSERT INTO Variant(variant_id,product_id,price,active) VALUES(1,1,690,1),(2,1,NULL,0),(3,2,1290,1);
    INSERT INTO VariantAttribute VALUES(1,1,10,NULL),(1,2,NULL,'滿版'),(2,1,11,NULL),(3,3,12,NULL),(3,5,14,NULL);
    INSERT INTO VariantModel VALUES(1,1),(2,1);
    INSERT INTO Barcode(barcode,variant_id,source) VALUES('4710000000001',1,'factory'),('TL0000017',1,'store'),('4710000000001',2,'factory');
    INSERT INTO VariantIssue(variant_id,issue_type,field_id) VALUES(2,'missing_required',1);
    INSERT INTO Member(member_id,name) VALUES(1,'測試會員');
    INSERT INTO MemberPhone VALUES(1,'0900000000');
    INSERT INTO Sale(sale_id,payment,total,paid) VALUES(1,'現金',690,700);
    INSERT INTO SaleItem(sale_id,variant_id,qty,unit_price) VALUES(1,1,1,690);
    INSERT INTO StockMovement(variant_id,qty,kind,employee) VALUES(1,5,'purchase',1);
    INSERT INTO StockMovement(variant_id,qty,kind,ref_id) VALUES(1,-1,'sale',1);
    """
    conn.executescript(sql)
    conn.commit()
    conn.close()


class StandardDbTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.src = self.root / "src_v24.db"
        self.out = self.root / "out" / "pos_standard_new.db"
        make_v24(self.src)

    def build(self):
        return tool.build(self.src, self.out)

    def assert_failed_cleanly(self, fragment):
        before = _digest(self.src)
        with self.assertRaises(tool.ConversionError) as raised:
            self.build()
        self.assertIn(fragment, str(raised.exception))
        self.assertFalse(self.out.exists())
        self.assertFalse(self.out.with_name(self.out.name + "-journal").exists())
        self.assertEqual(_digest(self.src), before)

    def test_converts_master_data_and_reconciles(self):
        before = _digest(self.src)
        counts, settings = self.build()
        self.assertEqual(_digest(self.src), before)
        self.assertEqual(read_schema_version(self.out), db_schema.SCHEMA_VERSION)
        self.assertEqual(counts["AttributeField"], 5)
        self.assertEqual(counts["VariantAttribute"], 5)
        self.assertEqual(settings["next_store_barcode"], "42")
        conn = get_conn(self.out)
        try:
            fields = {r[0]: tuple(r[1:]) for r in conn.execute(
                "SELECT field_id,category_id,name,sort,required,default_option_id,active FROM AttributeField")}
            self.assertEqual(fields, {
                1: (1, "材質", 2, 1, 10, 1), 2: (1, "備註", 1, 0, None, 1),
                3: (2, "顏色", 1, 0, 12, 0),      # 種類層級停用 → 停用
                4: (1, "特性詞條", 3, 0, None, 0),  # 欄位本身停用 → 停用
                5: (2, "款式", 2, 1, None, 1),
            })
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='CategoryField'").fetchone()[0], 0)
            for table in tool.SKIP_TABLES:
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0], 0, table)
            self.assertEqual(conn.execute("SELECT seq FROM sqlite_sequence WHERE name='Product'").fetchone()[0], 9)
            self.assertEqual(conn.execute(
                "SELECT option_id FROM VariantAttribute WHERE variant_id=1 AND field_id=1").fetchone()[0], 10)
            password = conn.execute("SELECT value FROM Setting WHERE key='admin_password_hash'").fetchone()[0]
            self.assertNotEqual(password, "來源密碼不可被帶走")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()
        init_db(self.out, require_existing=True)   # 正式程式可直接開啟

    def test_existing_output_is_never_overwritten(self):
        self.out.parent.mkdir(parents=True)
        self.out.write_bytes(b"existing")
        with self.assertRaises(tool.ConversionError):
            self.build()
        self.assertEqual(self.out.read_bytes(), b"existing")

    def test_rejects_source_that_is_not_v24(self):
        conn = sqlite3.connect(self.src)
        conn.execute("UPDATE Setting SET value='23' WHERE key='schema_version'")
        conn.commit()
        conn.close()
        self.assert_failed_cleanly("v24")

    def _mutate(self, sql):
        conn = sqlite3.connect(self.src)
        conn.executescript(sql)
        conn.commit()
        conn.close()

    def test_field_linked_to_two_categories_stops(self):
        self._mutate("INSERT INTO CategoryField(category_id,field_id,sort) VALUES(2,1,9);")
        self.assert_failed_cleanly("掛了 2 個種類")

    def test_field_linked_to_no_category_stops(self):
        self._mutate("INSERT INTO AttributeField(field_id,name) VALUES(6,'孤兒欄');")
        self.assert_failed_cleanly("掛了 0 個種類")

    def test_normalized_duplicate_name_in_same_category_stops(self):
        self._mutate("INSERT INTO AttributeField(field_id,name,field_type) VALUES(6,'材質　','text');"
                     "INSERT INTO CategoryField(category_id,field_id,sort) VALUES(1,6,9);")
        self.assert_failed_cleanly("同名")

    def test_value_of_other_category_field_stops(self):
        self._mutate("INSERT INTO VariantAttribute VALUES(1,3,12,NULL);")
        self.assert_failed_cleanly("不屬於該商品的種類")

    def test_new_constraint_failure_aborts_and_removes_output(self):
        # 選項不屬於該欄:v24 沒擋,新格式複合外鍵會擋
        self._mutate("INSERT INTO VariantAttribute VALUES(3,5,12,NULL);")
        self.assert_failed_cleanly("外鍵檢查失敗")

    def test_duplicate_text_value_aborts_and_removes_output(self):
        self._mutate("INSERT INTO VariantAttribute VALUES(1,2,NULL,'另一筆');")
        self.assert_failed_cleanly("寫入新格式失敗")

    def test_two_values_on_select_field_stops(self):
        self._mutate("INSERT INTO VariantAttribute VALUES(1,1,11,NULL);")
        self.assert_failed_cleanly("只能填一個值")

    def test_option_value_on_text_field_stops(self):
        self._mutate("INSERT INTO AttributeOption(option_id,field_id,value,sort,active) VALUES(15,2,'選項',1,1);"
                     "INSERT INTO VariantAttribute VALUES(2,2,15,NULL);")
        self.assert_failed_cleanly("型態不符")

    def test_source_hash_change_removes_output(self):
        real = tool.file_hash
        calls = []
        def changed(path):
            calls.append(path)
            return real(path) + ("-changed" if len(calls) > 1 else "")
        with patch.object(tool, "file_hash", changed), self.assertRaises(tool.ConversionError) as raised:
            self.build()
        self.assertIn("被改變", str(raised.exception))
        self.assertFalse(self.out.exists())
        self.assertFalse(self.out.with_name(self.out.name + "-journal").exists())


if __name__ == "__main__":
    unittest.main()
