import unittest
from base import ApiTestCase


class TestCatalog(ApiTestCase):
    def setUp(self):
        super().setUp()
        # 正規化後 select 值須為既有選項:先建專屬欄「規格」與選項
        self.make_category_with_field("規格", options=("亮面", "霧面", "超亮", "防窺"))

    def _create(self):
        return self.c.post("/api/products", json={
            "name": "HODA 鋼化玻璃", "category_id": self.cid, "default_price": 590,
            "variants": [
                {"attributes": {"規格": "亮面"},
                 "barcodes": [{"barcode": "FX100000001", "source": "factory"}]},
                {"attributes": {"規格": "霧面"}, "price": 690,
                 "barcodes": [{"barcode": "FX100000002", "source": "factory"}]},
            ]}).json()

    # 1. 巢狀分組結構、effective_price、stock
    def test_catalog_grouping(self):
        r = self._create()
        pid = r["product_id"]
        vid0 = r["variant_ids"][0]
        self.c.post("/api/stock/receive", json={"variant_id": vid0, "qty": 5})
        cat = self.c.get("/api/catalog").json()
        self.assertEqual(len(cat), 1)
        p = cat[0]
        self.assertEqual(p["product_id"], pid)
        self.assertEqual(p["name"], "HODA 鋼化玻璃")
        self.assertEqual(p["default_price"], 590)
        self.assertTrue(p["active"])
        self.assertEqual(len(p["variants"]), 2)
        v0, v1 = p["variants"]
        self.assertEqual(v0["attributes"]["規格"], "亮面")
        self.assertIsNone(v0["price"])
        self.assertEqual(v0["effective_price"], 590)     # 用款預設價
        self.assertEqual(v0["stock"], 5)
        self.assertTrue(v0["active"])
        self.assertEqual(v0["barcodes"][0]["barcode"], "FX100000001")
        self.assertEqual(v1["price"], 690)
        self.assertEqual(v1["effective_price"], 690)     # 用自身價

    # 2. 停用款預設不出現、include_inactive=1 才出現
    def test_inactive_product_hidden(self):
        r = self._create()
        pid = r["product_id"]
        self.c.put(f"/api/products/{pid}", json={"active": 0})
        self.assertEqual(len(self.c.get("/api/catalog").json()), 0)
        self.assertEqual(len(self.c.get("/api/catalog?include_inactive=1").json()), 1)

    # 停用單一變體:預設 catalog 不含該變體、include_inactive 才含
    def test_inactive_variant_hidden(self):
        r = self._create()
        vid1 = r["variant_ids"][1]
        self.c.put(f"/api/variants/{vid1}", json={"active": 0})
        cat = self.c.get("/api/catalog").json()
        self.assertEqual(len(cat[0]["variants"]), 1)
        cat2 = self.c.get("/api/catalog?include_inactive=1").json()
        self.assertEqual(len(cat2[0]["variants"]), 2)

    # 3. PUT products / variants 局部更新
    def test_put_product(self):
        r = self._create()
        pid = r["product_id"]
        self.c.put(f"/api/products/{pid}", json={"name": "新名稱", "default_price": None})
        p = self.c.get("/api/catalog").json()[0]
        self.assertEqual(p["name"], "新名稱")
        self.assertIsNone(p["default_price"])

    def test_put_variant(self):
        r = self._create()
        vid0 = r["variant_ids"][0]
        self.c.put(f"/api/variants/{vid0}", json={"price": 555, "attributes": {"規格": "超亮"}})
        p = self.c.get("/api/catalog").json()[0]
        v0 = next(v for v in p["variants"] if v["variant_id"] == vid0)
        self.assertEqual(v0["price"], 555)
        self.assertEqual(v0["attributes"]["規格"], "超亮")

    # 4. 停用變體後帶它銷售 → 422 且庫存不變
    def test_inactive_variant_cannot_sell(self):
        r = self._create()
        vid0 = r["variant_ids"][0]
        self.c.post("/api/stock/receive", json={"variant_id": vid0, "qty": 10})
        self.c.put(f"/api/variants/{vid0}", json={"active": 0})
        resp = self.c.post("/api/sales", json={"payment": "現金", "paid": 1000,
            "items": [{"variant_id": vid0, "qty": 1, "unit_price": 590}]})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(self.c.get(f"/api/stock/{vid0}").json()["stock"], 10)

    def test_inactive_product_cannot_sell(self):
        r = self._create()
        pid = r["product_id"]
        vid0 = r["variant_ids"][0]
        self.c.post("/api/stock/receive", json={"variant_id": vid0, "qty": 10})
        self.c.put(f"/api/products/{pid}", json={"active": 0})
        resp = self.c.post("/api/sales", json={"payment": "現金", "paid": 1000,
            "items": [{"variant_id": vid0, "qty": 1, "unit_price": 590}]})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(self.c.get(f"/api/stock/{vid0}").json()["stock"], 10)

    # 5. 停用品不出現在收銀搜尋
    def test_inactive_hidden_in_search(self):
        r = self._create()
        pid = r["product_id"]
        self.assertTrue(len(self.c.get("/api/products?q=HODA").json()) >= 1)
        self.c.put(f"/api/products/{pid}", json={"active": 0})
        self.assertEqual(len(self.c.get("/api/products?q=HODA").json()), 0)

    def test_inactive_variant_hidden_in_search(self):
        r = self._create()
        vid0 = r["variant_ids"][0]
        self.c.put(f"/api/variants/{vid0}", json={"active": 0})
        got = self.c.get("/api/products?q=HODA").json()
        self.assertEqual(len(got), 1)      # 只剩另一變體

    # barcode 掃描回傳帶 active
    def test_scan_active_flag(self):
        r = self._create()
        vid0 = r["variant_ids"][0]
        self.assertTrue(self.c.get("/api/barcode/FX100000001").json()["active"])
        self.c.put(f"/api/variants/{vid0}", json={"active": 0})
        resp = self.c.get("/api/barcode/FX100000001")
        self.assertEqual(resp.status_code, 200)   # 掃得到
        self.assertFalse(resp.json()["active"])   # 但 active=False

    # 6. 有紀錄變體 DELETE → 409;乾淨變體 DELETE → 200 且條碼消失
    def test_delete_variant_with_record_409(self):
        r = self._create()
        vid0 = r["variant_ids"][0]
        self.c.post("/api/stock/receive", json={"variant_id": vid0, "qty": 1})
        resp = self.c.delete(f"/api/variants/{vid0}")
        self.assertEqual(resp.status_code, 409)

    def test_delete_clean_variant(self):
        r = self._create()
        vid1 = r["variant_ids"][1]
        resp = self.c.delete(f"/api/variants/{vid1}")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        # 條碼一併消失
        self.assertEqual(self.c.get("/api/barcode/FX100000002").status_code, 404)
        # catalog 只剩 1 變體
        self.assertEqual(len(self.c.get("/api/catalog").json()[0]["variants"]), 1)

    def test_delete_product_with_record_409(self):
        r = self._create()
        vid0 = r["variant_ids"][0]
        pid = r["product_id"]
        self.c.post("/api/stock/receive", json={"variant_id": vid0, "qty": 1})
        self.assertEqual(self.c.delete(f"/api/products/{pid}").status_code, 409)

    def test_delete_clean_product(self):
        r = self._create()
        pid = r["product_id"]
        resp = self.c.delete(f"/api/products/{pid}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self.c.get("/api/catalog?include_inactive=1").json()), 0)
        self.assertEqual(self.c.get("/api/barcode/FX100000001").status_code, 404)

    # 7. DELETE /api/barcodes/{code}
    def test_delete_barcode(self):
        r = self._create()
        resp = self.c.delete("/api/barcodes/FX100000001")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.c.get("/api/barcode/FX100000001").status_code, 404)
        self.assertEqual(self.c.delete("/api/barcodes/NOPE").status_code, 404)

    # 8. POST /api/products/{pid}/variants 新增變體
    def test_add_variant(self):
        r = self._create()
        pid = r["product_id"]
        resp = self.c.post(f"/api/products/{pid}/variants", json={
            "attributes": {"規格": "防窺"}, "price": 790,
            "barcodes": [{"source": "store"}]}).json()
        self.assertIn("variant_id", resp)
        self.assertTrue(resp["barcodes"][0].startswith("TL"))
        cat = self.c.get("/api/catalog").json()[0]
        self.assertEqual(len(cat["variants"]), 3)

    # q 過濾:命中款名帶出其變體
    def test_catalog_q_filter(self):
        self._create()
        self.c.post("/api/products", json={"name": "其他品", "category_id": self.cid,
            "default_price": 100, "variants": [{"attributes": {}, "barcodes": []}]})
        got = self.c.get("/api/catalog?q=HODA").json()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["name"], "HODA 鋼化玻璃")


if __name__ == "__main__":
    unittest.main()
