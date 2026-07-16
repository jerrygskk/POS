"""玻璃規格模型:multi/tags 讀寫、tags 自動建選項、預設選項帶入、
唯一索引擋重覆、v3→v4 migration。樣本為虛構資料,不含真實人名。"""
import unittest, tempfile, os, sqlite3
from lib.db import get_conn, init_db
from lib import db_schema
from base import ApiTestCase


class TestMultiTagsApi(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.cid = self.c.post("/api/categories",
                               json={"name": "鋼化玻璃"}).json()["category_id"]
        # 規格 multi + 四基礎選項
        self.spec = self.c.post("/api/fields", json={
            "name": "規格", "category_id": self.cid,
            "field_type": "multi"}).json()["field_id"]
        for v in ["亮面", "霧面", "藍光", "防窺"]:
            self.c.post("/api/options", json={"field_id": self.spec, "value": v})
        # 特性詞條 tags(自動長)
        self.tags = self.c.post("/api/fields", json={
            "name": "特性詞條", "category_id": self.cid,
            "field_type": "tags"}).json()["field_id"]
        # 版型 select + 預設滿版
        self.layout = self.c.post("/api/fields", json={
            "name": "版型", "category_id": self.cid,
            "field_type": "select"}).json()["field_id"]
        for v in ["滿版", "9分滿"]:
            self.c.post("/api/options", json={"field_id": self.layout, "value": v})
        oid = [o for o in self.c.get(f"/api/options?field_id={self.layout}").json()
               if o["value"] == "滿版"][0]["option_id"]
        self.c.put(f"/api/fields/{self.layout}", json={"default_option_id": oid})

    def _create(self, attrs):
        return self.create_product(attrs)

    def _tags_values(self):
        return {o["value"]
                for o in self.c.get(f"/api/options?field_id={self.tags}").json()}

    def test_multi_roundtrip(self):
        self._create({"規格": ["霧面", "藍光"]})
        got = self.c.get("/api/barcode/B1").json()
        self.assertEqual(got["attributes"]["規格"], ["霧面", "藍光"])
        self.assertEqual(got["attr_display"], "霧面+藍光")

    def test_multi_unknown_option_422(self):
        r = self.c.post("/api/products", json={
            "name": "膜", "category_id": self.cid,
            "variants": [{"attributes": {"規格": ["不存在"]}, "barcodes": []}]})
        self.assertEqual(r.status_code, 422)

    def test_multi_dedup(self):
        self._create({"規格": ["亮面", "亮面"]})
        self.assertEqual(self.c.get("/api/barcode/B1").json()["attributes"]["規格"],
                         ["亮面"])

    def test_tags_autocreate_option(self):
        self._create({"規格": ["藍光"], "特性詞條": ["SGS認證", "無色偏"]})
        self.assertEqual(self._tags_values(), {"SGS認證", "無色偏"})
        got = self.c.get("/api/barcode/B1").json()
        self.assertEqual(got["attributes"]["特性詞條"], ["SGS認證", "無色偏"])
        self.assertEqual(got["attr_display"], "藍光｜SGS認證, 無色偏")

    def test_tags_rename_takes_effect(self):
        self._create({"規格": ["藍光"], "特性詞條": ["SGS"]})
        oid = [o for o in self.c.get(f"/api/options?field_id={self.tags}").json()
               if o["value"] == "SGS"][0]["option_id"]
        self.c.patch(f"/api/options/{oid}", json={"value": "SGS認證"})
        self.assertEqual(
            self.c.get("/api/barcode/B1").json()["attributes"]["特性詞條"],
            ["SGS認證"])

    def test_default_option_exposed(self):
        fields = self.c.get(f"/api/categories/{self.cid}/fields").json()
        lf = [f for f in fields if f["name"] == "版型"][0]
        self.assertEqual(lf["default_value"], "滿版")
        # multi 欄型與選項也帶出(供建檔勾選)
        sf = [f for f in fields if f["name"] == "規格"][0]
        self.assertEqual(sf["field_type"], "multi")
        self.assertEqual({o["value"] for o in sf["options"]},
                         {"亮面", "霧面", "藍光", "防窺"})

    def test_display_order_base_tag_layout(self):
        # 不論寫入順序,顯示遵守 基礎→詞條→版型(欄 sort);
        # 值=預設選項(版型=滿版)不顯示,非預設(9分滿)才顯示
        self._create({"版型": "滿版", "特性詞條": ["低藍光"],
                      "規格": ["藍光", "防窺"]})
        self.assertEqual(self.c.get("/api/barcode/B1").json()["attr_display"],
                         "藍光+防窺｜低藍光")

    def test_display_non_default_layout_shown(self):
        self._create({"版型": "9分滿", "規格": ["亮面"]})
        self.assertEqual(self.c.get("/api/barcode/B1").json()["attr_display"],
                         "亮面｜9分滿")

    def test_variant_patch_multi(self):
        r = self._create({"規格": ["亮面"]})
        vid = r["variant_ids"][0]
        self.c.put(f"/api/variants/{vid}", json={"attributes": {"規格": ["霧面", "防窺"]}})
        self.assertEqual(self.c.get("/api/barcode/B1").json()["attributes"]["規格"],
                         ["霧面", "防窺"])


class TestVaUniqueIndex(unittest.TestCase):
    def test_duplicate_option_row_blocked(self):
        db = os.path.join(tempfile.mkdtemp(), "pos.db")
        init_db(db)
        conn = get_conn(db)
        try:
            conn.execute("INSERT INTO Product(name) VALUES('p')")
            conn.execute("INSERT INTO Variant(product_id) VALUES(1)")
            conn.execute("INSERT INTO AttributeField(name,field_type) VALUES('規格','multi')")
            fid = conn.execute(
                "SELECT field_id FROM AttributeField WHERE name='規格'").fetchone()[0]
            conn.execute("INSERT INTO AttributeOption(field_id,value) VALUES(?,'亮面')",
                         (fid,))
            oid = conn.execute(
                "SELECT option_id FROM AttributeOption WHERE value='亮面'").fetchone()[0]
            conn.execute("INSERT INTO VariantAttribute(variant_id,field_id,option_id) "
                         "VALUES(1,?,?)", (fid, oid))
            # 同 (variant,field,option) 重覆寫入 → 唯一索引擋下
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO VariantAttribute(variant_id,field_id,option_id)"
                             " VALUES(1,?,?)", (fid, oid))
        finally:
            conn.close()


class TestV3toV4Migration(unittest.TestCase):
    """v3→v4:AttributeField 加 default_option_id、欄型放寬 multi/tags;
    VariantAttribute 移除複合 PK 改唯一索引,允許同欄多筆。既有資料保留。"""

    def _make_v3(self):
        db = os.path.join(tempfile.mkdtemp(), "v3.db")
        conn = sqlite3.connect(db)
        conn.executescript("""
          CREATE TABLE Product(product_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
          CREATE TABLE Variant(variant_id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL);
          CREATE TABLE AttributeField(field_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, category_id INTEGER,
            field_type TEXT NOT NULL DEFAULT 'select'
              CHECK(field_type IN ('select','text')),
            sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(category_id, name));
          CREATE TABLE AttributeOption(option_id INTEGER PRIMARY KEY AUTOINCREMENT,
            field_id INTEGER NOT NULL, value TEXT NOT NULL,
            sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(field_id, value));
          CREATE TABLE VariantAttribute(variant_id INTEGER NOT NULL,
            field_id INTEGER NOT NULL, option_id INTEGER, text_value TEXT,
            PRIMARY KEY(variant_id, field_id),
            CHECK((option_id IS NULL) <> (text_value IS NULL)));
          CREATE TABLE Setting(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        conn.execute("INSERT INTO Product(name) VALUES('p')")
        conn.execute("INSERT INTO Variant(product_id) VALUES(1)")
        conn.execute("INSERT INTO AttributeField(name,category_id,field_type) "
                     "VALUES('規格',NULL,'select')")
        conn.execute("INSERT INTO AttributeOption(field_id,value) VALUES(1,'亮面')")
        conn.execute("INSERT INTO AttributeOption(field_id,value) VALUES(1,'霧面')")
        conn.execute("INSERT INTO VariantAttribute(variant_id,field_id,option_id) "
                     "VALUES(1,1,1)")
        conn.execute("INSERT INTO Setting(key,value) VALUES('schema_version','3')")
        conn.commit()
        conn.close()
        return db

    def test_upgrade(self):
        db = self._make_v3()
        init_db(db)   # 開檔自動升級 v3→v4
        conn = get_conn(db)
        try:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(AttributeField)")}
            # 升級鏈跑到 v13:AttributeField 全域化,default_option_id 已移至 CategoryField
            self.assertNotIn("default_option_id", cols)
            # 既有 VariantAttribute 資料保留
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM VariantAttribute").fetchone()[0], 1)
            # multi 欄型可用 + 同 (variant,field) 多筆 option 允許
            conn.execute("INSERT INTO VariantAttribute(variant_id,field_id,option_id) "
                         "VALUES(1,1,2)")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM VariantAttribute "
                "WHERE variant_id=1 AND field_id=1").fetchone()[0], 2)
            conn.execute("UPDATE AttributeField SET field_type='tags' WHERE field_id=1")
            ver = int(conn.execute(
                "SELECT value FROM Setting WHERE key='schema_version'").fetchone()[0])
            self.assertEqual(ver, db_schema.SCHEMA_VERSION)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
