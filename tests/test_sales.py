import unittest, datetime
from unittest.mock import patch
from base import ApiTestCase
from lib.db import db_conn
from api.sales import _build_sale_filters, _load_sale_rows

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

    def test_sale_filter_builder_uses_shared_boundaries_and_arguments(self):
        sql, args = _build_sale_filters("2026-07-01", "2026-07-31", "?暸?")
        self.assertEqual(
            sql,
            " AND date(s.ts)>=? AND date(s.ts)<=? AND s.payment=?",
        )
        self.assertEqual(args, ["2026-07-01", "2026-07-31", "?暸?"])

    def test_sale_row_loader_returns_rows_and_display_data_together(self):
        self._sale()
        with db_conn(self.db) as conn:
            rows, attrs, display = _load_sale_rows(conn, "", "", "")
        self.assertEqual(len(rows), 1)
        self.assertEqual(attrs.get(rows[0]["variant_id"], {}), {})
        self.assertEqual(display.get(rows[0]["variant_id"], ""), "")

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

    def test_export_csv_does_not_load_attributes(self):
        self._sale()
        with patch("api.sales.attrs_by_variant") as attrs:
            r = self.c.get("/api/sales/export")
        self.assertEqual(r.status_code, 200)
        attrs.assert_not_called()

    def test_export_filters_by_payment(self):
        self._sale(payment="現金")
        self._sale(payment="刷卡")
        r = self.c.get("/api/sales/export?payment=%E7%8F%BE%E9%87%91")
        self.assertEqual(r.status_code, 200)
        content = r.content.decode("utf-8-sig")
        self.assertIn("現金", content)
        self.assertNotIn("刷卡", content)
