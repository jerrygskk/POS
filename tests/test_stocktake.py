import unittest
from base import ApiTestCase

class TestStocktake(ApiTestCase):
    def setUp(self):
        super().setUp()
        cid = self.c.post("/api/categories", json={"name": "測試種類"}).json()["category_id"]
        r = self.c.post("/api/products", json={"name": "品A", "category_id": cid,
            "variants":
            [{"attributes": {}, "barcodes": [{"barcode":"A1","source":"store"}]},
             {"attributes": {}, "barcodes": [{"barcode":"A2","source":"store"}]}]})
        self.v1, self.v2 = r.json()["variant_ids"]
        self.c.post("/api/stock/receive", json={"variant_id": self.v1, "qty": 5})
        self.c.post("/api/stock/receive", json={"variant_id": self.v2, "qty": 3})
        self.sid = self.c.post("/api/stocktake", json={"operator": "測試"}).json()["session_id"]

    def test_scan_snapshot_and_accumulate(self):
        r = self.c.post(f"/api/stocktake/{self.sid}/scan", json={"variant_id": self.v1}).json()
        self.assertEqual((r["system_qty"], r["counted_qty"]), (5, 1))
        r = self.c.post(f"/api/stocktake/{self.sid}/scan", json={"variant_id": self.v1}).json()
        self.assertEqual(r["counted_qty"], 2)

    def test_close_adjusts_only_diff(self):
        # v1 實盤 4(差 -1);v2 沒盤 → 不動
        self.c.post(f"/api/stocktake/{self.sid}/scan",
                    json={"variant_id": self.v1, "qty": 4})
        self.c.post(f"/api/stocktake/{self.sid}/close")
        self.assertEqual(self.c.get(f"/api/stock/{self.v1}").json()["stock"], 4)
        self.assertEqual(self.c.get(f"/api/stock/{self.v2}").json()["stock"], 3)

    def test_close_twice_409(self):
        self.c.post(f"/api/stocktake/{self.sid}/close")
        self.assertEqual(self.c.post(f"/api/stocktake/{self.sid}/close").status_code, 409)

    def test_manual_set(self):
        self.c.post(f"/api/stocktake/{self.sid}/scan", json={"variant_id": self.v1})
        self.c.put(f"/api/stocktake/{self.sid}/items/{self.v1}", json={"counted_qty": 7})
        d = self.c.get(f"/api/stocktake/{self.sid}").json()
        item = [i for i in d["items"] if i["variant_id"] == self.v1][0]
        self.assertEqual(item["counted_qty"], 7)
        self.assertEqual(item["diff"], 2)

    def test_manual_set_unscanned_404(self):
        # 對尚未掃描(無 StocktakeItem 列)的變體設實盤量:須回 404,
        # 不可影響 0 列卻回 ok(否則前端誤以為已存,實際靜默漏寫)
        r = self.c.put(f"/api/stocktake/{self.sid}/items/{self.v2}",
                       json={"counted_qty": 3})
        self.assertEqual(r.status_code, 404)
