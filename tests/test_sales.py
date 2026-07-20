import unittest, datetime
from base import ApiTestCase
from lib.db import db_conn

class TestSales(ApiTestCase):
    def setUp(self):
        super().setUp()
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

    def test_item_discount_cannot_exceed_subtotal(self):
        r = self._sale(items=[
            {"variant_id": self.vid, "qty": 1,
             "unit_price": 500, "discount": 501},
            {"variant_id": self.vid, "qty": 2,
             "unit_price": 500, "discount": 0},
        ])
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.c.get(f"/api/stock/{self.vid}").json()["stock"], 10)

    def test_negative_total_rejected(self):
        r = self._sale(order_discount=99999)
        self.assertEqual(r.status_code, 422)
        # 交易失敗庫存不動
        self.assertEqual(self.c.get(f"/api/stock/{self.vid}").json()["stock"], 10)

    def test_unknown_payment_rejected(self):
        r = self._sale(payment="不存在的付款方式")
        self.assertEqual(r.status_code, 422)
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

    def test_summary_date_range_takes_precedence_over_legacy_date(self):
        self._sale()
        today = datetime.date.today().isoformat()
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        s = self.c.get(
            f"/api/sales/summary?date={tomorrow}&date_from={today}"
        ).json()
        self.assertEqual(s["count"], 1)

    def test_invalid_filter_dates_return_422(self):
        for path in ("/api/sales", "/api/sales/summary", "/api/sales/export"):
            with self.subTest(path=path):
                response = self.c.get(path + "?date_from=2026-02-31")
                self.assertEqual(response.status_code, 422)

    def test_export_csv(self):
        self._sale()
        r = self.c.get("/api/sales/export")
        self.assertEqual(r.status_code, 200)
        self.assertIn("csv", r.headers["content-type"])

    def test_export_filters_by_payment(self):
        self._sale(payment="現金")
        self._sale(payment="刷卡")
        r = self.c.get("/api/sales/export?payment=%E7%8F%BE%E9%87%91")
        self.assertEqual(r.status_code, 200)
        content = r.content.decode("utf-8-sig")
        self.assertIn("現金", content)
        self.assertNotIn("刷卡", content)

    def test_fixed_price_mismatch_is_rejected_without_writes(self):
        with db_conn(self.db) as conn:
            conn.execute("UPDATE Variant SET price=500 WHERE variant_id=?", (self.vid,))
            conn.commit()
        response = self._sale(items=[{
            "variant_id": self.vid, "qty": 1, "unit_price": 499,
        }])
        self.assertEqual(response.status_code, 422)
        self.assertIn("售價與系統不符", response.text)
        with db_conn(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM Sale").fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM StockMovement WHERE kind='sale'").fetchone()[0], 0)

    def test_null_price_accepts_manual_nonnegative_price(self):
        response = self._sale(items=[{
            "variant_id": self.vid, "qty": 1, "unit_price": 777,
        }])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 777)

    def test_insufficient_stock_counts_duplicate_variant_lines(self):
        with db_conn(self.db) as conn:
            conn.execute("DELETE FROM StockMovement WHERE variant_id=?", (self.vid,))
            conn.execute(
                "INSERT INTO StockMovement(variant_id,qty,kind) VALUES(?,2,'purchase')",
                (self.vid,),
            )
            conn.commit()
        for items in (
            [{"variant_id": self.vid, "qty": 3, "unit_price": 500}],
            [{"variant_id": self.vid, "qty": 2, "unit_price": 500},
             {"variant_id": self.vid, "qty": 1, "unit_price": 500}],
        ):
            with self.subTest(items=items):
                response = self._sale(items=items)
                self.assertEqual(response.status_code, 422)
                self.assertIn("庫存不足", response.text)
        self.assertEqual(self.c.get(f"/api/stock/{self.vid}").json()["stock"], 2)
