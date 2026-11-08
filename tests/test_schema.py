import unittest, tempfile, os, sqlite3
from lib.db import get_conn, init_db
from lib import db_schema
from base import ConnTestCase

class TestSchema(ConnTestCase):
    def test_tables_exist(self):
        conn = get_conn(self.db)
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ["Product","Variant","Barcode","AttributeField","AttributeOption",
                  "StockMovement","Sale","SaleItem","StocktakeSession","StocktakeItem",
                  "Setting","Category","Brand","BrandCategory","PhoneBrand","PhoneModel",
                  "VariantModel","VariantAttribute","OptionModel"]:
            self.assertIn(t, names)

    def test_variant_has_no_attributes_column(self):
        # 正規化後 attributes JSON 欄退場
        conn = get_conn(self.db)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(Variant)")}
        self.assertNotIn("attributes", cols)

    def test_init_idempotent(self):
        init_db(self.db)  # 第二次不炸
        conn = get_conn(self.db)
        n = conn.execute("SELECT COUNT(*) c FROM AttributeField").fetchone()["c"]
        self.assertEqual(n, 0)   # 種子不建規格欄

    def test_seed_creates_no_fields(self):
        # 規格欄一律由各種類建立時自己建一份(詞彙互不相干),種子不留共用主檔
        conn = get_conn(self.db)
        rows = [(r["name"], r["field_type"]) for r in conn.execute(
            "SELECT name, field_type FROM AttributeField ORDER BY field_id")]
        self.assertEqual(rows, [])

    def test_product_has_category_and_brand_id(self):
        conn = get_conn(self.db)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(Product)")}
        self.assertIn("category_id", cols)
        self.assertIn("brand_id", cols)
        self.assertNotIn("category", cols)  # 舊字串欄退場

    def test_attributefield_belongs_to_category(self):
        # 規格欄直接隸屬種類,排序／必填／預設帶入都在欄位本身;沒有另一張對照表
        conn = get_conn(self.db)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(AttributeField)")}
        self.assertTrue({"category_id", "sort", "required", "default_option_id", "active"} <= cols)
        names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("CategoryField", names)

    def test_phonemodel_has_device_type_and_no_series(self):
        # 全新 DB:型號掛裝置類型、系列欄已移除;種子補一個「手機」
        conn = get_conn(self.db)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(PhoneModel)")}
        self.assertIn("device_type_id", cols)
        self.assertNotIn("series", cols)
        self.assertEqual([r["name"] for r in conn.execute("SELECT name FROM DeviceType")], ["手機"])


