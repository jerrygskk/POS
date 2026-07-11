import unittest, tempfile, os, sqlite3
from lib.db import get_conn, init_db
from lib import db_schema

class TestSchema(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pos.db")
        init_db(self.db)

    def test_tables_exist(self):
        conn = get_conn(self.db)
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ["Product","Variant","Barcode","AttributeField","AttributeOption",
                  "StockMovement","Sale","SaleItem","StocktakeSession","StocktakeItem",
                  "Setting","Category","Brand","BrandCategory","PhoneBrand","PhoneModel",
                  "VariantModel","CategoryField","VariantAttribute","OptionModel"]:
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
        self.assertEqual(n, 2)  # 種子不重複:商品描述、顏色

    def test_default_fields(self):
        conn = get_conn(self.db)
        rows = [(r["name"], r["field_type"]) for r in conn.execute(
            "SELECT name, field_type FROM AttributeField ORDER BY sort")]
        self.assertEqual(rows, [("商品描述", "text"), ("顏色", "select")])

    def test_product_has_category_and_brand_id(self):
        conn = get_conn(self.db)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(Product)")}
        self.assertIn("category_id", cols)
        self.assertIn("brand_id", cols)
        self.assertNotIn("category", cols)  # 舊字串欄退場

    def test_seed_fields_are_shared(self):
        # 種子欄皆為共用欄(category_id NULL)
        conn = get_conn(self.db)
        n = conn.execute(
            "SELECT COUNT(*) c FROM AttributeField WHERE category_id IS NULL").fetchone()["c"]
        self.assertEqual(n, 2)

    def test_phonemodel_has_series_column(self):
        # 全新 DB 建表即含 series 欄
        conn = get_conn(self.db)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(PhoneModel)")}
        self.assertIn("series", cols)


class TestPhoneBrandMigration(unittest.TestCase):
    """v1→v2:舊 PhoneModel.brand 字串升級為 PhoneBrand FK,回填不失資料。"""

    def _make_old_db(self):
        db = os.path.join(tempfile.mkdtemp(), "old.db")
        conn = sqlite3.connect(db)
        conn.executescript("""
          CREATE TABLE PhoneModel(
            model_id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand TEXT NOT NULL, name TEXT NOT NULL,
            sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(brand, name));
          CREATE TABLE Product(product_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
          CREATE TABLE Variant(variant_id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER, attributes TEXT NOT NULL DEFAULT '{}');
          CREATE TABLE VariantModel(variant_id INTEGER, model_id INTEGER,
            PRIMARY KEY(variant_id, model_id));
          CREATE TABLE Setting(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        conn.execute("INSERT INTO PhoneModel(brand,name) VALUES('iPhone','15')")   # 1
        conn.execute("INSERT INTO PhoneModel(brand,name) VALUES('iPhone','16')")   # 2
        conn.execute("INSERT INTO PhoneModel(brand,name) VALUES('SAMSUNG','S24')") # 3
        conn.execute("INSERT INTO Product(name) VALUES('殼')")
        conn.execute("INSERT INTO Variant(product_id) VALUES(1)")
        conn.execute("INSERT INTO VariantModel(variant_id,model_id) VALUES(1,2)")  # 掛 iPhone 16
        conn.execute("INSERT INTO Setting(key,value) VALUES('schema_version','1')")
        conn.commit(); conn.close()
        return db

    def test_upgrade_backfills_and_preserves_fk(self):
        db = self._make_old_db()
        init_db(db)  # 開檔自動升級
        conn = get_conn(db)
        try:
            # 品牌去重回填
            brands = {r["name"] for r in conn.execute("SELECT name FROM PhoneBrand")}
            self.assertEqual(brands, {"iPhone", "SAMSUNG"})
            # brand 欄退場、phone_brand_id 上場
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(PhoneModel)")}
            self.assertNotIn("brand", cols)
            self.assertIn("phone_brand_id", cols)
            # 型號正確對到品牌
            ipid = conn.execute(
                "SELECT phone_brand_id FROM PhoneBrand WHERE name='iPhone'").fetchone()["phone_brand_id"]
            got = conn.execute(
                "SELECT phone_brand_id FROM PhoneModel WHERE name='16'").fetchone()["phone_brand_id"]
            self.assertEqual(got, ipid)
            # VariantModel FK 保留(model_id 不變、仍掛在 variant 1)
            vm = conn.execute(
                "SELECT model_id FROM VariantModel WHERE variant_id=1").fetchone()
            self.assertEqual(vm["model_id"], 2)
            # 版號升至最新
            ver = int(conn.execute(
                "SELECT value FROM Setting WHERE key='schema_version'").fetchone()["value"])
            self.assertEqual(ver, db_schema.SCHEMA_VERSION)
        finally:
            conn.close()


class TestVariantAttributeMigration(unittest.TestCase):
    """v2→v3:Variant.attributes JSON 退場,建 VariantAttribute/OptionModel。
    既有 JSON 資料丟棄,但變體列(及其 FK)須保留、遷移不炸。"""

    def _make_v2_db(self):
        db = os.path.join(tempfile.mkdtemp(), "v2.db")
        conn = sqlite3.connect(db)
        # 模擬 v2:Variant 仍含 attributes 欄;PhoneModel 已是 phone_brand_id 制
        conn.executescript("""
          CREATE TABLE Product(product_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
          CREATE TABLE Variant(variant_id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL, attributes TEXT NOT NULL DEFAULT '{}',
            price INTEGER, active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')));
          CREATE TABLE Barcode(barcode TEXT PRIMARY KEY, variant_id INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'store',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')));
          CREATE TABLE PhoneBrand(phone_brand_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE, sort INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1);
          CREATE TABLE PhoneModel(model_id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_brand_id INTEGER NOT NULL, name TEXT NOT NULL,
            sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(phone_brand_id, name));
          CREATE TABLE VariantModel(variant_id INTEGER NOT NULL, model_id INTEGER NOT NULL,
            PRIMARY KEY(variant_id, model_id));
          CREATE TABLE Setting(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        conn.execute("INSERT INTO Product(name) VALUES('膜')")
        conn.execute("INSERT INTO Variant(product_id,attributes,price) "
                     "VALUES(1,'{\"規格\":\"亮面\"}',590)")   # variant 1
        conn.execute("INSERT INTO Variant(product_id,attributes) VALUES(1,'{}')")  # 2
        conn.execute("INSERT INTO Barcode(barcode,variant_id) VALUES('B1',1)")
        conn.execute("INSERT INTO PhoneBrand(name) VALUES('iPhone')")
        conn.execute("INSERT INTO PhoneModel(phone_brand_id,name) VALUES(1,'15')")
        conn.execute("INSERT INTO VariantModel(variant_id,model_id) VALUES(1,1)")
        conn.execute("INSERT INTO Setting(key,value) VALUES('schema_version','2')")
        conn.commit(); conn.close()
        return db

    def test_v2_to_v3_upgrade(self):
        db = self._make_v2_db()
        init_db(db)  # 開檔自動升級
        conn = get_conn(db)
        try:
            # attributes 欄退場、新表上場
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(Variant)")}
            self.assertNotIn("attributes", cols)
            names = {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("VariantAttribute", names)
            self.assertIn("OptionModel", names)
            # 變體列保留(含價格),FK 參照不失
            vids = {r["variant_id"] for r in conn.execute("SELECT variant_id FROM Variant")}
            self.assertEqual(vids, {1, 2})
            price = conn.execute(
                "SELECT price FROM Variant WHERE variant_id=1").fetchone()["price"]
            self.assertEqual(price, 590)
            self.assertEqual(conn.execute(
                "SELECT variant_id FROM Barcode WHERE barcode='B1'").fetchone()["variant_id"], 1)
            self.assertEqual(conn.execute(
                "SELECT model_id FROM VariantModel WHERE variant_id=1").fetchone()["model_id"], 1)
            # 舊 JSON 丟棄:VariantAttribute 無資料
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) c FROM VariantAttribute").fetchone()["c"], 0)
            # 版號升至最新
            ver = int(conn.execute(
                "SELECT value FROM Setting WHERE key='schema_version'").fetchone()["value"])
            self.assertEqual(ver, db_schema.SCHEMA_VERSION)
        finally:
            conn.close()


class TestModelSeriesMigration(unittest.TestCase):
    """v5→v6:PhoneModel 加 series 欄;舊 DB 升級後有欄、既有型號資料保留。"""

    def _make_v5_db(self):
        db = os.path.join(tempfile.mkdtemp(), "v5.db")
        conn = sqlite3.connect(db)
        # 模擬 v5:PhoneModel 已有 alias 欄但無 series 欄
        conn.executescript("""
          CREATE TABLE PhoneBrand(phone_brand_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE, sort INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1);
          CREATE TABLE PhoneModel(model_id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_brand_id INTEGER NOT NULL, name TEXT NOT NULL, alias TEXT,
            sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(phone_brand_id, name));
          CREATE TABLE Setting(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        conn.execute("INSERT INTO PhoneBrand(name) VALUES('iPhone')")
        conn.execute("INSERT INTO PhoneModel(phone_brand_id,name,alias) "
                     "VALUES(1,'iPhone 17 Pro Max','17PM')")
        conn.execute("INSERT INTO Setting(key,value) VALUES('schema_version','5')")
        conn.commit(); conn.close()
        return db

    def test_v5_to_v6_upgrade(self):
        db = self._make_v5_db()
        init_db(db)  # 開檔自動升級
        conn = get_conn(db)
        try:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(PhoneModel)")}
            self.assertIn("series", cols)
            # 既有型號保留、新欄預設 NULL
            row = conn.execute(
                "SELECT alias, series FROM PhoneModel WHERE name='iPhone 17 Pro Max'").fetchone()
            self.assertEqual(row["alias"], "17PM")
            self.assertIsNone(row["series"])
            ver = int(conn.execute(
                "SELECT value FROM Setting WHERE key='schema_version'").fetchone()["value"])
            self.assertEqual(ver, db_schema.SCHEMA_VERSION)
        finally:
            conn.close()
