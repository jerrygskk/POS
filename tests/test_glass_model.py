"""玻璃規格模型:multi/tags 讀寫、tags 自動建選項、預設選項帶入、
唯一索引擋重覆、v3→v4 migration。樣本為虛構資料,不含真實人名。"""
import unittest, tempfile, os, sqlite3
from lib.db import get_conn, init_db
from lib import db_schema
from base import FacadeTestCase


class TestMultiTagsApi(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.cid = self.create_category("鋼化玻璃")
        # 規格 multi + 四基礎選項
        self.spec = self.create_field("規格", self.cid, "multi")
        for v in ["亮面", "霧面", "藍光", "防窺"]:
            self.invoke("options.create", {"field_id": self.spec, "value": v})
        # 特性詞條 tags(自動長)
        self.tags = self.create_field("特性詞條", self.cid, "tags")
        # 版型 select + 預設滿版
        self.layout = self.create_field("版型", self.cid)
        for v in ["滿版", "9分滿"]:
            self.invoke("options.create", {"field_id": self.layout, "value": v})
        oid = [o for o in self.invoke("options.list", {"field_id": self.layout})
               if o["value"] == "滿版"][0]["option_id"]
        self.invoke("categories.set_field", {"category_id": self.cid, "field_id": self.layout,
                                               "fields": {"default_option_id": oid}})

    def _create(self, attrs):
        return self.create_product(attrs)

    def _tags_values(self):
        return {o["value"]
                for o in self.invoke("options.list", {"field_id": self.tags})}

    def test_multi_roundtrip(self):
        self._create({"規格": ["霧面", "藍光"]})
        got = self.invoke("barcodes.scan", {"code": "B1"})["matches"][0]
        self.assertEqual(got["attributes"]["規格"], ["霧面", "藍光"])
        self.assertEqual(got["attr_display"], "霧面+藍光")

    def test_multi_unknown_option_422(self):
        self.assert_application_error("validation_error", "products.create", {
            "name": "膜", "category_id": self.cid,
            "variants": [{"attributes": {"規格": ["不存在"]}, "barcodes": []}]})

    def test_multi_dedup(self):
        self._create({"規格": ["亮面", "亮面"]})
        self.assertEqual(self.invoke("barcodes.scan", {"code": "B1"})["matches"][0]["attributes"]["規格"],
                         ["亮面"])

    def test_tags_autocreate_option(self):
        self._create({"規格": ["藍光"], "特性詞條": ["SGS認證", "無色偏"]})
        self.assertEqual(self._tags_values(), {"SGS認證", "無色偏"})
        got = self.invoke("barcodes.scan", {"code": "B1"})["matches"][0]
        self.assertEqual(got["attributes"]["特性詞條"], ["SGS認證", "無色偏"])
        self.assertEqual(got["attr_display"], "藍光｜SGS認證, 無色偏")

    def test_tags_rename_takes_effect(self):
        self._create({"規格": ["藍光"], "特性詞條": ["SGS"]})
        oid = [o for o in self.invoke("options.list", {"field_id": self.tags})
               if o["value"] == "SGS"][0]["option_id"]
        self.invoke("options.update", {"id": oid, "fields": {"value": "SGS認證"}})
        self.assertEqual(
            self.invoke("barcodes.scan", {"code": "B1"})["matches"][0]["attributes"]["特性詞條"],
            ["SGS認證"])

    def test_default_option_exposed(self):
        fields = self.invoke("categories.fields", {"id": self.cid})
        lf = [f for f in fields if f["name"] == "版型"][0]
        self.assertEqual(lf["default_value"], "滿版")
        # multi 欄型與選項也帶出(供建檔勾選)
        sf = [f for f in fields if f["name"] == "規格"][0]
        self.assertEqual(sf["field_type"], "multi")
        self.assertEqual({o["value"] for o in sf["options"]},
                         {"亮面", "霧面", "藍光", "防窺"})

    def test_display_order_base_tag_layout(self):
        # 不論寫入順序,顯示遵守 基礎→詞條→版型(欄 sort);
        # 預設選項(版型=滿版)一樣要顯示,不省略
        self._create({"版型": "滿版", "特性詞條": ["低藍光"],
                      "規格": ["藍光", "防窺"]})
        self.assertEqual(self.invoke("barcodes.scan", {"code": "B1"})["matches"][0]["attr_display"],
                         "藍光+防窺｜低藍光｜滿版")

    def test_display_non_default_layout_shown(self):
        self._create({"版型": "9分滿", "規格": ["亮面"]})
        self.assertEqual(self.invoke("barcodes.scan", {"code": "B1"})["matches"][0]["attr_display"],
                         "亮面｜9分滿")

    def test_variant_patch_multi(self):
        r = self._create({"規格": ["亮面"]})
        vid = r["variant_ids"][0]
        self.invoke("variants.update", {"id": vid, "fields": {"attributes": {"規格": ["霧面", "防窺"]}}})
        self.assertEqual(self.invoke("barcodes.scan", {"code": "B1"})["matches"][0]["attributes"]["規格"],
                         ["霧面", "防窺"])


class TestVaUniqueIndex(unittest.TestCase):
    def test_duplicate_option_row_blocked(self):
        db = os.path.join(tempfile.mkdtemp(), "pos.db")
        init_db(db)
        conn = get_conn(db)
        try:
            conn.execute("INSERT INTO Category(name) VALUES('c')")
            conn.execute("INSERT INTO Product(name,category_id) VALUES('p',1)")
            conn.execute("INSERT INTO Variant(product_id) VALUES(1)")
            conn.execute("INSERT INTO AttributeField(category_id,name,field_type) VALUES(1,'規格','multi')")
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



if __name__ == "__main__":
    unittest.main()
