import unittest, tempfile, os, datetime
from fastapi.testclient import TestClient
from lib.db import init_db
from api import create_app

class TestSales(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pos.db")
        init_db(self.db)
        self.c = TestClient(create_app(self.db))
        cid = self.c.post("/api/categories", json={"name": "膜類"}).json()["category_id"]
        r = self.c.post("/api/products", json={"name": "膜", "category_id": cid,
            "default_price": 500,
            "variants": [{"attributes": {}, "barcodes": [{"barcode":"B1","source":"store"}]}]})
        self.vid = r.json()["variant_ids"][0]
        self.c.post("/api/stock/receive", json={"variant_id": self.vid, "qty": 10})

    def _sale(self, **kw):
        body = {"payment": "現金", "paid": 1000,
                "items": [{"variant_id": self.vid, "qty": 2, "unit_price": 500}]}
        body.update(kw)
        return self.c.post("/api/sales", json=body)

    def test_checkout_math_and_stock(self):
        r = self._sale(order_discount=100, paid=900).json()
        self.assertEqual(r["total"], 900)   # 2*500-100
        self.assertEqual(r["change"], 0)
        self.assertEqual(self.c.get(f"/api/stock/{self.vid}").json()["stock"], 8)

    def test_item_discount(self):
        r = self._sale(items=[{"variant_id": self.vid, "qty": 1,
                               "unit_price": 500, "discount": 50}]).json()
        self.assertEqual(r["total"], 450)

    def test_negative_total_rejected(self):
        r = self._sale(order_discount=99999)
        self.assertEqual(r.status_code, 422)
        # 交易失敗庫存不動
        self.assertEqual(self.c.get(f"/api/stock/{self.vid}").json()["stock"], 10)

    def test_summary(self):
        self._sale(); self._sale(payment="刷卡")
        today = datetime.date.today().isoformat()
        s = self.c.get(f"/api/sales/summary?date={today}").json()
        self.assertEqual(s["count"], 2)
        self.assertEqual(s["by_payment"]["現金"], 1000)

    def test_summary_date_range_and_payment(self):
        # 小結支援 date_from/date_to 區間與付款方式過濾(與明細清單一致)
        self._sale(); self._sale(payment="刷卡")
        today = datetime.date.today().isoformat()
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        s = self.c.get(f"/api/sales/summary?date_from={today}&date_to={tomorrow}").json()
        self.assertEqual(s["count"], 2)
        # 只算現金
        s2 = self.c.get(
            f"/api/sales/summary?date_from={today}&date_to={tomorrow}&payment=現金").json()
        self.assertEqual(s2["count"], 1)
        self.assertEqual(s2["total"], 1000)

    def test_export_csv(self):
        self._sale()
        r = self.c.get("/api/sales/export")
        self.assertEqual(r.status_code, 200)
        self.assertIn("csv", r.headers["content-type"])
