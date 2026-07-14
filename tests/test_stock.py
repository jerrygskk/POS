import unittest
from base import ApiTestCase

class TestStock(ApiTestCase):
    def setUp(self):
        super().setUp()
        cid = self.c.post("/api/categories", json={"name": "測試種類"}).json()["category_id"]
        r = self.c.post("/api/products", json={"name": "測試品", "category_id": cid,
            "variants":
            [{"attributes": {}, "barcodes": [{"barcode": "B1", "source": "store"}]}]})
        self.vid = r.json()["variant_ids"][0]

    def test_receive_accumulates(self):
        self.assertEqual(self.c.post("/api/stock/receive",
            json={"variant_id": self.vid, "qty": 5}).json()["stock"], 5)
        self.assertEqual(self.c.post("/api/stock/receive",
            json={"variant_id": self.vid, "qty": 3}).json()["stock"], 8)

    def test_detail_lists_movements(self):
        self.c.post("/api/stock/receive", json={"variant_id": self.vid, "qty": 5})
        r = self.c.get(f"/api/stock/{self.vid}").json()
        self.assertEqual(r["stock"], 5)
        self.assertEqual(r["movements"][0]["kind"], "purchase")

    def test_reject_zero_qty(self):
        r = self.c.post("/api/stock/receive", json={"variant_id": self.vid, "qty": 0})
        self.assertEqual(r.status_code, 422)

    def test_receive_unknown_variant_returns_404(self):
        r = self.c.post("/api/stock/receive",
                        json={"variant_id": 999999, "qty": 1})
        self.assertEqual(r.status_code, 404)
