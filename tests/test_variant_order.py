"""資料庫頁變體列排序:依材質組合分節。
單材質依材質序、複合材質排全部單材質後;同材質組合內素身先、帶詞條依詞條序跟在後。
樣本皆虛構,不含真實人名。"""
import unittest, tempfile, os
from fastapi.testclient import TestClient
from lib.db import init_db
from api import create_app


class TestVariantOrder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pos.db")
        init_db(self.db)
        self.c = TestClient(create_app(self.db))
        self.cid = self.c.post("/api/categories",
            json={"name": "鋼化玻璃"}).json()["category_id"]
        # multi 欄「規格」:亮面/霧面/藍光/防窺(建立順序=sort)
        self.f_spec = self.c.post("/api/fields",
            json={"name": "規格", "category_id": self.cid,
                  "field_type": "multi"}).json()["field_id"]
        for v in ("亮面", "霧面", "藍光", "防窺"):
            self.c.post("/api/options", json={"field_id": self.f_spec, "value": v})
        # tags 欄「特性詞條」:抗AR/藍寶石(建立順序=sort)
        self.f_tag = self.c.post("/api/fields",
            json={"name": "特性詞條", "category_id": self.cid,
                  "field_type": "tags"}).json()["field_id"]
        for v in ("抗AR", "藍寶石"):
            self.c.post("/api/options", json={"field_id": self.f_tag, "value": v})

    def _build(self, specs):
        """specs: [( [材質...], [詞條...] ), ...] 依給定順序亂序建檔"""
        variants = []
        for spec, tags in specs:
            attrs = {"規格": spec}
            if tags:
                attrs["特性詞條"] = tags
            variants.append({"attributes": attrs, "barcodes": []})
        r = self.c.post("/api/products", json={"name": "玻璃貼",
            "category_id": self.cid, "variants": variants})
        assert r.status_code == 200, r.text

    def _catalog_specs(self):
        cat = self.c.get("/api/catalog").json()
        return [v["attr_display"] for v in cat[0]["variants"]]

    def test_tags_follow_same_material_ar_block_last(self):
        # 一般詞條跟著自己的材質;抗AR 整塊移到素身後,塊內主材質序照舊
        self._build([
            (["藍光"], ["藍寶石"]),
            (["防窺"], []),
            (["防窺"], ["抗AR"]),
            (["亮面"], ["藍寶石"]),
            (["亮面"], ["抗AR"]),
            (["霧面"], []),
            (["亮面"], []),
            (["藍光"], []),
        ])
        self.assertEqual(self._catalog_specs(), [
            "亮面", "亮面｜藍寶石",
            "霧面",
            "藍光", "藍光｜藍寶石",
            "防窺",
            "亮面｜抗AR", "防窺｜抗AR",
        ])

    def test_combo_material_after_all_singles(self):
        self._build([
            (["霧面", "防窺"], []),
            (["防窺"], ["抗AR"]),
            (["霧面", "藍光"], ["抗AR"]),
            (["亮面"], []),
            (["霧面", "藍光"], []),
            (["防窺"], []),
        ])
        self.assertEqual(self._catalog_specs(), [
            "亮面",
            "防窺",
            "防窺｜抗AR",           # 單材質的 AR 塊
            "霧面+藍光",
            "霧面+防窺",
            "霧面+藍光｜抗AR",      # 複合材質的 AR 塊
        ])

    def test_no_attr_variant_first(self):
        self._build([
            (["亮面"], []),
            ([], []),
        ])
        self.assertEqual(self._catalog_specs(), ["", "亮面"])


if __name__ == "__main__":
    unittest.main()
