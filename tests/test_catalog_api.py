import unittest, tempfile, os
from fastapi.testclient import TestClient
from lib.db import init_db
from api import create_app


class TestCatalogApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pos.db")
        init_db(self.db)
        self.c = TestClient(create_app(self.db))

    # ---- 種類 CRUD ----
    def test_category_crud(self):
        cid = self.c.post("/api/categories", json={"name": "手機殼"}).json()["category_id"]
        self.assertIn("手機殼", [x["name"] for x in self.c.get("/api/categories").json()])
        self.c.patch(f"/api/categories/{cid}", json={"name": "保護殼"})
        self.assertIn("保護殼", [x["name"] for x in self.c.get("/api/categories").json()])

    def test_category_inactive_hidden_from_list(self):
        cid = self.c.post("/api/categories", json={"name": "淘汰種類"}).json()["category_id"]
        self.c.patch(f"/api/categories/{cid}", json={"active": 0})
        # 預設只回 active
        self.assertNotIn(cid, [x["category_id"] for x in self.c.get("/api/categories").json()])
        # ?all=1 看得到
        self.assertIn(cid, [x["category_id"] for x in self.c.get("/api/categories?all=1").json()])

    def test_category_delete_with_product_409(self):
        cid = self.c.post("/api/categories", json={"name": "鋼化玻璃"}).json()["category_id"]
        self.c.post("/api/products", json={"name": "膜", "category_id": cid,
            "variants": [{"attributes": {}, "barcodes": []}]})
        self.assertEqual(self.c.delete(f"/api/categories/{cid}").status_code, 409)

    def test_category_delete_clean(self):
        cid = self.c.post("/api/categories", json={"name": "空種類"}).json()["category_id"]
        self.assertEqual(self.c.delete(f"/api/categories/{cid}").status_code, 200)
        self.assertNotIn(cid, [x["category_id"] for x in self.c.get("/api/categories?all=1").json()])

    def test_category_delete_with_default_option(self):
        # 種類專屬 select 欄設了預設選項:刪種類須先解除 default_option_id 參照,
        # 否則刪選項時觸發 FK 循環參照回 500(回歸測試)
        cid = self.c.post("/api/categories", json={"name": "有預設選項"}).json()["category_id"]
        fid = self.c.post("/api/fields", json={"name": "版型", "category_id": cid}).json()["field_id"]
        self.c.post("/api/options", json={"field_id": fid, "value": "亮面"})
        oid = self.c.get(f"/api/options?field_id={fid}").json()[0]["option_id"]
        self.c.patch(f"/api/fields/{fid}", json={"default_option_id": oid})
        self.assertEqual(self.c.delete(f"/api/categories/{cid}").status_code, 200)
        self.assertNotIn(cid, [x["category_id"] for x in self.c.get("/api/categories?all=1").json()])

    def test_build_with_inactive_category_422(self):
        cid = self.c.post("/api/categories", json={"name": "停用種類"}).json()["category_id"]
        self.c.patch(f"/api/categories/{cid}", json={"active": 0})
        r = self.c.post("/api/products", json={"name": "X", "category_id": cid,
            "variants": [{"attributes": {}, "barcodes": []}]})
        self.assertEqual(r.status_code, 422)

    def test_build_with_missing_category_422(self):
        r = self.c.post("/api/products", json={"name": "X", "category_id": 999,
            "variants": [{"attributes": {}, "barcodes": []}]})
        self.assertEqual(r.status_code, 422)

    # ---- 廠牌 CRUD + 掛種類 + 過濾 ----
    def test_brand_crud_and_delete_409(self):
        bid = self.c.post("/api/brands", json={"name": "HODA"}).json()["brand_id"]
        cid = self.c.post("/api/categories", json={"name": "鋼化玻璃"}).json()["category_id"]
        self.c.post("/api/products", json={"name": "膜", "category_id": cid,
            "brand_id": bid, "variants": [{"attributes": {}, "barcodes": []}]})
        self.assertEqual(self.c.delete(f"/api/brands/{bid}").status_code, 409)

    def test_brand_filter_by_category(self):
        b1 = self.c.post("/api/brands", json={"name": "HODA"}).json()["brand_id"]
        b2 = self.c.post("/api/brands", json={"name": "犀牛盾"}).json()["brand_id"]
        glass = self.c.post("/api/categories", json={"name": "鋼化玻璃"}).json()["category_id"]
        case = self.c.post("/api/categories", json={"name": "手機殼"}).json()["category_id"]
        # HODA 掛鋼化玻璃;犀牛盾 掛手機殼
        self.c.put(f"/api/brands/{b1}/categories", json={"category_ids": [glass]})
        self.c.put(f"/api/brands/{b2}/categories", json={"category_ids": [case]})
        got = [x["name"] for x in self.c.get(f"/api/brands?category_id={glass}").json()]
        self.assertEqual(got, ["HODA"])
        # 停用的廠牌不入該過濾清單
        self.c.patch(f"/api/brands/{b1}", json={"active": 0})
        self.assertEqual(self.c.get(f"/api/brands?category_id={glass}").json(), [])

    def test_build_with_inactive_brand_422(self):
        cid = self.c.post("/api/categories", json={"name": "鋼化玻璃"}).json()["category_id"]
        bid = self.c.post("/api/brands", json={"name": "舊廠"}).json()["brand_id"]
        self.c.patch(f"/api/brands/{bid}", json={"active": 0})
        r = self.c.post("/api/products", json={"name": "X", "category_id": cid,
            "brand_id": bid, "variants": [{"attributes": {}, "barcodes": []}]})
        self.assertEqual(r.status_code, 422)

    # ---- 手機品牌 CRUD + 停用不入建檔下拉 + 刪除 409 ----
    def _add_phone_brand(self, name):
        return self.c.post("/api/phone-brands", json={"name": name}).json()["phone_brand_id"]

    def test_phone_brand_crud(self):
        pbid = self._add_phone_brand("iPhone")
        self.assertIn("iPhone", [x["name"] for x in self.c.get("/api/phone-brands").json()])
        self.c.patch(f"/api/phone-brands/{pbid}", json={"name": "Apple"})
        self.assertIn("Apple", [x["name"] for x in self.c.get("/api/phone-brands").json()])

    def test_phone_brand_inactive_hidden_and_models_dropped(self):
        pbid = self._add_phone_brand("iPhone")
        self.c.post("/api/models", json={"phone_brand_id": pbid, "name": "15"})
        # 停用品牌
        self.c.patch(f"/api/phone-brands/{pbid}", json={"active": 0})
        # 品牌預設清單不含停用者;all=1 看得到
        self.assertNotIn(pbid, [x["phone_brand_id"] for x in self.c.get("/api/phone-brands").json()])
        self.assertIn(pbid, [x["phone_brand_id"] for x in self.c.get("/api/phone-brands?all=1").json()])
        # 停用品牌之型號不入建檔下拉(all=0),但 all=1 仍在
        self.assertEqual(self.c.get("/api/models").json(), [])
        self.assertEqual(len(self.c.get("/api/models?all=1").json()), 1)

    def test_phone_brand_delete_with_model_409(self):
        pbid = self._add_phone_brand("iPhone")
        self.c.post("/api/models", json={"phone_brand_id": pbid, "name": "15"})
        self.assertEqual(self.c.delete(f"/api/phone-brands/{pbid}").status_code, 409)

    def test_phone_brand_delete_clean(self):
        pbid = self._add_phone_brand("空品牌")
        self.assertEqual(self.c.delete(f"/api/phone-brands/{pbid}").status_code, 200)

    # ---- 型號 CRUD + phone_brand_id 過濾 + 帶品牌名稱 + 409 ----
    def test_model_crud_and_brand_filter(self):
        ip = self._add_phone_brand("iPhone")
        sam = self._add_phone_brand("SAMSUNG")
        m1 = self.c.post("/api/models", json={"phone_brand_id": ip, "name": "15"}).json()["model_id"]
        self.c.post("/api/models", json={"phone_brand_id": sam, "name": "S24"})
        rows = self.c.get(f"/api/models?phone_brand_id={ip}").json()
        self.assertEqual([x["name"] for x in rows], ["15"])
        self.assertEqual(m1, rows[0]["model_id"])
        # 回傳帶品牌名稱方便前端
        self.assertEqual(rows[0]["brand_name"], "iPhone")

    def test_model_alias_roundtrip_and_display(self):
        cid = self.c.post("/api/categories", json={"name": "手機殼"}).json()["category_id"]
        pbid = self._add_phone_brand("iPhone")
        mid = self.c.post("/api/models", json={
            "phone_brand_id": pbid, "name": "iPhone 17 Pro Max",
            "alias": "17PM"}).json()["model_id"]
        rows = self.c.get("/api/models").json()
        self.assertEqual(rows[0]["alias"], "17PM")
        # 變體型號顯示:有別名用別名
        self.c.post("/api/products", json={"name": "殼", "category_id": cid,
            "variants": [{"attributes": {}, "model_ids": [mid],
                          "barcodes": [{"barcode": "BA1", "source": "store"}]}]})
        self.assertEqual(self.c.get("/api/barcode/BA1").json().get("models") or
                         self.c.get("/api/catalog").json()[0]["variants"][0]["models"],
                         ["17PM"])
        # 清空別名回全名
        self.c.patch(f"/api/models/{mid}", json={"alias": None})
        self.assertEqual(self.c.get("/api/catalog").json()[0]["variants"][0]["models"],
                         ["iPhone 17 Pro Max"])

    def test_model_series_roundtrip(self):
        pbid = self._add_phone_brand("iPhone")
        # 建檔帶系列
        mid = self.c.post("/api/models", json={
            "phone_brand_id": pbid, "name": "17 Pro Max",
            "series": "17 系列"}).json()["model_id"]
        rows = self.c.get("/api/models").json()
        self.assertEqual(rows[0]["series"], "17 系列")
        # 更新系列
        self.c.patch(f"/api/models/{mid}", json={"series": "17 Pro 系列"})
        self.assertEqual(self.c.get("/api/models").json()[0]["series"], "17 Pro 系列")
        # 空字串存 NULL
        self.c.patch(f"/api/models/{mid}", json={"series": "  "})
        self.assertIsNone(self.c.get("/api/models").json()[0]["series"])
        # 未帶系列建檔 → NULL
        m2 = self.c.post("/api/models", json={
            "phone_brand_id": pbid, "name": "16"}).json()["model_id"]
        row2 = next(r for r in self.c.get("/api/models").json() if r["model_id"] == m2)
        self.assertIsNone(row2["series"])

    def test_model_add_with_missing_brand_422(self):
        r = self.c.post("/api/models", json={"phone_brand_id": 999, "name": "15"})
        self.assertEqual(r.status_code, 422)

    def test_model_delete_with_variant_409(self):
        cid = self.c.post("/api/categories", json={"name": "手機殼"}).json()["category_id"]
        pbid = self._add_phone_brand("iPhone")
        mid = self.c.post("/api/models", json={"phone_brand_id": pbid, "name": "15"}).json()["model_id"]
        self.c.post("/api/products", json={"name": "殼", "category_id": cid,
            "variants": [{"attributes": {}, "model_ids": [mid], "barcodes": []}]})
        self.assertEqual(self.c.delete(f"/api/models/{mid}").status_code, 409)

    def test_model_delete_with_option_binding_409(self):
        # 選項限定型號(特別色)也算參照,刪型號須回 409 而非 500(回歸測試)
        cid = self.c.post("/api/categories", json={"name": "手機殼"}).json()["category_id"]
        pbid = self._add_phone_brand("iPhone")
        mid = self.c.post("/api/models", json={"phone_brand_id": pbid, "name": "15"}).json()["model_id"]
        fid = self.c.post("/api/fields", json={"name": "顏色A", "category_id": cid}).json()["field_id"]
        self.c.post("/api/options", json={"field_id": fid, "value": "限定色"})
        oid = self.c.get(f"/api/options?field_id={fid}").json()[0]["option_id"]
        self.c.put(f"/api/options/{oid}/models", json={"model_ids": [mid]})
        self.assertEqual(self.c.delete(f"/api/models/{mid}").status_code, 409)

    # ---- categories/{id}/fields 專屬+共用合併 ----
    def test_category_fields_merge(self):
        cid = self.c.post("/api/categories", json={"name": "鋼化玻璃"}).json()["category_id"]
        # 專屬欄「版型」含選項
        fid = self.c.post("/api/fields", json={"name": "版型", "category_id": cid}).json()["field_id"]
        self.c.post("/api/options", json={"field_id": fid, "value": "亮面"})
        # 啟用共用欄「顏色」
        color = [f for f in self.c.get("/api/fields?common=1").json()
                 if f["name"] == "顏色"][0]["field_id"]
        self.c.put(f"/api/categories/{cid}/fields-common", json={"field_ids": [color]})
        fields = self.c.get(f"/api/categories/{cid}/fields").json()
        names = [f["name"] for f in fields]
        self.assertIn("版型", names)
        self.assertIn("顏色", names)
        # 商品描述(未勾選共用欄)不應出現
        self.assertNotIn("商品描述", names)
        vt = [f for f in fields if f["name"] == "版型"][0]
        self.assertEqual([o["value"] for o in vt["options"]], ["亮面"])
        self.assertFalse(vt["shared"])
        self.assertTrue([f for f in fields if f["name"] == "顏色"][0]["shared"])

    # ---- 變體掛型號後以 model_id 篩選查得 ----
    def test_variant_model_filter(self):
        cid = self.c.post("/api/categories", json={"name": "手機殼"}).json()["category_id"]
        ip = self._add_phone_brand("iPhone")
        m15 = self.c.post("/api/models", json={"phone_brand_id": ip, "name": "15"}).json()["model_id"]
        m16 = self.c.post("/api/models", json={"phone_brand_id": ip, "name": "16"}).json()["model_id"]
        # 共用欄「顏色」補選項(正規化後 select 值須為既有選項)
        color = [f for f in self.c.get("/api/fields?common=1").json()
                 if f["name"] == "顏色"][0]["field_id"]
        for v in ("黑", "白"):
            self.c.post("/api/options", json={"field_id": color, "value": v})
        r = self.c.post("/api/products", json={"name": "共用殼", "category_id": cid,
            "default_price": 100,
            "variants": [
                {"attributes": {"顏色": "黑"}, "model_ids": [m15, m16],
                 "barcodes": [{"barcode": "C1", "source": "store"}]},
                {"attributes": {"顏色": "白"}, "model_ids": [m16],
                 "barcodes": [{"barcode": "C2", "source": "store"}]},
            ]}).json()
        v0 = r["variant_ids"][0]
        # catalog 依 model_id=15 篩選 → 只回掛 15 的變體
        cat = self.c.get(f"/api/catalog?model_id={m15}").json()
        self.assertEqual(len(cat), 1)
        self.assertEqual(len(cat[0]["variants"]), 1)
        self.assertEqual(cat[0]["variants"][0]["variant_id"], v0)
        self.assertIn("15", cat[0]["variants"][0]["models"])
        # 收銀搜尋亦支援 model_id 篩選
        got = self.c.get(f"/api/products?model_id={m15}").json()
        self.assertEqual([g["variant_id"] for g in got], [v0])
        # PUT 整組替換:把 v0 改成只掛 16 → 以 15 篩選查不到
        self.c.put(f"/api/variants/{v0}/models", json={"model_ids": [m16]})
        self.assertEqual(len(self.c.get(f"/api/catalog?model_id={m15}").json()), 0)

    def test_catalog_returns_names(self):
        cid = self.c.post("/api/categories", json={"name": "鋼化玻璃"}).json()["category_id"]
        bid = self.c.post("/api/brands", json={"name": "HODA"}).json()["brand_id"]
        self.c.post("/api/products", json={"name": "膜", "category_id": cid,
            "brand_id": bid, "variants": [{"attributes": {}, "barcodes": []}]})
        p = self.c.get("/api/catalog").json()[0]
        self.assertEqual(p["category_name"], "鋼化玻璃")
        self.assertEqual(p["brand_name"], "HODA")


if __name__ == "__main__":
    unittest.main()
