import unittest
from base import ApiTestCase


class TestVariantAttributes(ApiTestCase):
    def setUp(self):
        super().setUp()
        # 專屬 select 欄「規格」+ 選項;共用 text 欄「商品描述」為種子
        self.make_category_with_field("規格", options=("亮面", "霧面"))

    def _opt_id(self, value):
        return [o for o in self.c.get(f"/api/options?field_id={self.fid}").json()
                if o["value"] == value][0]["option_id"]

    def _create(self, attrs):
        return self.create_product(attrs)

    # select 欄 round-trip:寫入值字串 → 存 option_id → 讀回同值
    def test_select_roundtrip(self):
        self._create({"規格": "亮面"})
        got = self.c.get("/api/barcode/B1").json()["attributes"]
        self.assertEqual(got["規格"], "亮面")
        # 內部確為 option_id(非 text_value)
        cat = self.c.get("/api/catalog").json()[0]
        self.assertEqual(cat["variants"][0]["attributes"]["規格"], "亮面")

    # text 欄 round-trip:自由文字存 text_value
    def test_text_roundtrip(self):
        self._create({"商品描述": "限量款"})
        got = self.c.get("/api/barcode/B1").json()["attributes"]
        self.assertEqual(got["商品描述"], "限量款")

    # select 值非既有選項 → 422
    def test_unknown_option_422(self):
        r = self.c.post("/api/products", json={
            "name": "膜", "category_id": self.cid,
            "variants": [{"attributes": {"規格": "不存在的值"}, "barcodes": []}]})
        self.assertEqual(r.status_code, 422)

    # 欄名不存在 → 422
    def test_unknown_field_422(self):
        r = self.c.post("/api/products", json={
            "name": "膜", "category_id": self.cid,
            "variants": [{"attributes": {"沒這欄": "x"}, "barcodes": []}]})
        self.assertEqual(r.status_code, 422)

    # 改選項值即生效:不掃變體資料,主檔改名後讀回新值
    def test_rename_option_takes_effect(self):
        self._create({"規格": "亮面"})
        oid = self._opt_id("亮面")
        self.c.patch(f"/api/options/{oid}", json={"value": "超亮面"})
        got = self.c.get("/api/barcode/B1").json()["attributes"]
        self.assertEqual(got["規格"], "超亮面")

    # 改欄名即生效:VariantAttribute 存 field_id,欄改名讀回新 key
    def test_rename_field_takes_effect(self):
        self._create({"規格": "亮面"})
        self.c.put(f"/api/fields/{self.fid}", json={"name": "面料"})
        got = self.c.get("/api/barcode/B1").json()["attributes"]
        self.assertIn("面料", got)
        self.assertNotIn("規格", got)
        self.assertEqual(got["面料"], "亮面")

    # 有 VariantAttribute 參照的選項刪除 → 軟隱藏,既有規格保留
    def test_delete_referenced_option_preserves_existing_attribute(self):
        self._create({"規格": "亮面"})
        oid = self._opt_id("亮面")
        self.assertEqual(self.c.delete(f"/api/options/{oid}").status_code, 200)
        self.assertEqual(self.c.get("/api/barcode/B1").json()["attributes"]["規格"],
                         "亮面")
        self.assertNotIn("亮面", [
            o["value"] for o in self.c.get(f"/api/options?field_id={self.fid}").json()
        ])
        # 未被參照的選項可刪
        oid2 = self._opt_id("霧面")
        self.assertEqual(self.c.delete(f"/api/options/{oid2}").status_code, 200)

    # PATCH 變體規格:改寫關聯列
    def test_patch_variant_attributes(self):
        r = self._create({"規格": "亮面"})
        vid = r["variant_ids"][0]
        self.c.put(f"/api/variants/{vid}", json={"attributes": {"規格": "霧面"}})
        self.assertEqual(self.c.get("/api/barcode/B1").json()["attributes"]["規格"], "霧面")
        # 清空規格
        self.c.put(f"/api/variants/{vid}", json={"attributes": {}})
        self.assertEqual(self.c.get("/api/barcode/B1").json()["attributes"], {})


class TestOptionModel(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.cid = self.c.post("/api/categories",
                               json={"name": "手機殼"}).json()["category_id"]
        self.fid = self.c.post("/api/fields",
            json={"name": "顏色", "category_id": self.cid}).json()["field_id"]
        ip = self.create_phone_brand("iPhone")
        self.m15 = self.create_model(ip, "15")
        self.m16 = self.create_model(ip, "16")
        # 三個選項:通用黑、限 15 的特別色、共用款(綁 15+16)
        self.o_black = self._add_opt("黑")
        self.o_special = self._add_opt("限定色")
        self.o_shared = self._add_opt("共用色")
        self.c.put(f"/api/options/{self.o_special}/models",
                   json={"model_ids": [self.m15]})
        self.c.put(f"/api/options/{self.o_shared}/models",
                   json={"model_ids": [self.m15, self.m16]})

    def _add_opt(self, value):
        self.c.post("/api/options", json={"field_id": self.fid, "value": value})
        return [o for o in self.c.get(f"/api/options?field_id={self.fid}").json()
                if o["value"] == value][0]["option_id"]

    def _values(self, model_ids=None):
        url = f"/api/options?field_id={self.fid}"
        for m in (model_ids or []):
            url += f"&model_ids={m}"
        return {o["value"] for o in self.c.get(url).json()}

    # 不帶 model_ids:回全部
    def test_no_filter_returns_all(self):
        self.assertEqual(self._values(), {"黑", "限定色", "共用色"})

    # 過濾 15:通用(黑)∪ 綁 15(限定色、共用色)
    def test_filter_model_15(self):
        self.assertEqual(self._values([self.m15]), {"黑", "限定色", "共用色"})

    # 過濾 16:通用(黑)∪ 綁 16(共用色);限定色(僅綁 15)不出現
    def test_filter_model_16(self):
        self.assertEqual(self._values([self.m16]), {"黑", "共用色"})

    # 多型號取聯集
    def test_filter_union(self):
        self.assertEqual(self._values([self.m15, self.m16]),
                         {"黑", "限定色", "共用色"})

    # 讀寫 option 限定型號
    def test_get_set_option_models(self):
        got = self.c.get(f"/api/options/{self.o_special}/models").json()
        self.assertEqual(got["model_ids"], [self.m15])
        # 全量替換為 16
        self.c.put(f"/api/options/{self.o_special}/models",
                   json={"model_ids": [self.m16]})
        self.assertEqual(
            self.c.get(f"/api/options/{self.o_special}/models").json()["model_ids"],
            [self.m16])
        # 清空 → 改回通用
        self.c.put(f"/api/options/{self.o_special}/models", json={"model_ids": []})
        self.assertEqual(
            self.c.get(f"/api/options/{self.o_special}/models").json()["model_ids"], [])
        self.assertIn("限定色", self._values([self.m16]))

    # 選項清單內附 model_ids(維護頁用)
    def test_list_options_inline_model_ids(self):
        opts = {o["value"]: o["model_ids"]
                for o in self.c.get(f"/api/options?field_id={self.fid}").json()}
        self.assertEqual(opts["黑"], [])
        self.assertEqual(set(opts["共用色"]), {self.m15, self.m16})


if __name__ == "__main__":
    unittest.main()
