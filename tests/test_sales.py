import unittest, datetime
from base import FacadeTestCase
from lib.application_errors import ApplicationError
from lib.db import db_conn

class TestSales(FacadeTestCase):
    def setUp(self):
        super().setUp()
        cid = self.create_category("膜類")
        product = self.invoke("products.create", {"name": "膜", "category_id": cid,
            "variants": [{"attributes": {}, "price": 500,
                          "barcodes": [{"barcode":"B1","source":"store"}]}]})
        self.vid = product["variant_ids"][0]
        self.invoke("stock.receive", {"variant_id": self.vid, "qty": 10})

    def _sale(self, **kw):
        body = {"payment": "現金", "paid": 1000,
                "items": [{"variant_id": self.vid, "qty": 2, "unit_price": 500}]}
        body.update(kw)
        return self.invoke("sales.checkout", body)

    def _stock(self):
        return self.invoke("stock.detail", {"variant_id": self.vid})["stock"]

    def test_checkout_math_and_stock(self):
        r = self._sale(order_discount=100, paid=900)
        self.assertEqual(r["total"], 900)   # 2*500-100
        self.assertEqual(r["change"], 0)
        self.assertEqual(self._stock(), 8)

    def test_item_discount(self):
        r = self._sale(items=[{"variant_id": self.vid, "qty": 1,
                               "unit_price": 500, "discount": 50}])
        self.assertEqual(r["total"], 450)

    def test_item_discount_cannot_exceed_subtotal(self):
        error = self.assert_application_error("validation_error", "sales.checkout", {
            "payment": "現金", "paid": 1000, "items": [
            {"variant_id": self.vid, "qty": 1,
             "unit_price": 500, "discount": 501},
            {"variant_id": self.vid, "qty": 2,
             "unit_price": 500, "discount": 0},
        ]})
        self.assertIn("單項折扣不可超過", error.message)
        self.assertEqual(self._stock(), 10)

    def test_negative_total_rejected(self):
        error = self.assert_application_error("validation_error", "sales.checkout", {
            "payment": "現金", "paid": 1000, "order_discount": 99999,
            "items": [{"variant_id": self.vid, "qty": 2, "unit_price": 500}],
        })
        self.assertIn("折扣後總額不可為負數", error.message)
        # 交易失敗庫存不動
        self.assertEqual(self._stock(), 10)

    def test_unknown_payment_rejected(self):
        self.assertIn("現金", self.invoke("payments.list"))
        error = self.assert_application_error("validation_error", "sales.checkout", {
            "payment": "不存在的付款方式", "paid": 1000,
            "items": [{"variant_id": self.vid, "qty": 2, "unit_price": 500}],
        })
        self.assertIn("付款方式未在設定中", error.message)
        self.assertEqual(self._stock(), 10)

    def test_summary(self):
        self._sale(); self._sale(payment="刷卡")
        today = datetime.date.today().isoformat()
        s = self.invoke("sales.summary", {"date": today})
        self.assertEqual(s["count"], 2)
        self.assertEqual(s["by_payment"]["現金"], 1000)

    def test_summary_date_range_and_payment(self):
        # 小結支援 date_from/date_to 區間與付款方式過濾(與明細清單一致)
        self._sale(); self._sale(payment="刷卡")
        today = datetime.date.today().isoformat()
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        s = self.invoke("sales.summary", {"date_from": today, "date_to": tomorrow})
        self.assertEqual(s["count"], 2)
        # 只算現金
        s2 = self.invoke("sales.summary", {
            "date_from": today, "date_to": tomorrow, "payment": "現金",
        })
        self.assertEqual(s2["count"], 1)
        self.assertEqual(s2["total"], 1000)

    def test_summary_date_range_takes_precedence_over_legacy_date(self):
        self._sale()
        today = datetime.date.today().isoformat()
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        s = self.invoke("sales.summary", {"date": tomorrow, "date_from": today})
        self.assertEqual(s["count"], 1)

    def test_invalid_filter_dates_raise_validation_error(self):
        for action in ("sales.list", "sales.summary", "sales.export"):
            with self.subTest(action=action):
                self.assert_application_error("validation_error", action, {
                    "date_from": "2026-02-31",
                })

    def test_export_csv(self):
        self._sale()
        exported = self.invoke("sales.export")
        self.assertEqual(exported["filename"], "sales.csv")
        self.assertTrue(exported["content"].startswith("\ufeff"))
        self.assertIn("銷售編號", exported["content"])

    def test_export_filters_by_payment(self):
        self._sale(payment="現金")
        self._sale(payment="刷卡")
        content = self.invoke("sales.export", {"payment": "現金"})["content"]
        self.assertIn("現金", content)
        self.assertNotIn("刷卡", content)

    def test_fixed_price_mismatch_is_rejected_without_writes(self):
        with db_conn(self.db) as conn:
            conn.execute("UPDATE Variant SET price=500 WHERE variant_id=?", (self.vid,))
            conn.commit()
        error = self.assert_application_error("validation_error", "sales.checkout", {
            "payment": "現金", "paid": 1000,
            "items": [{"variant_id": self.vid, "qty": 1, "unit_price": 499}],
        })
        self.assertIn("售價與系統不符", error.message)
        with db_conn(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM Sale").fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM StockMovement WHERE kind='sale'").fetchone()[0], 0)

    def test_null_price_accepts_manual_nonnegative_price(self):
        with db_conn(self.db) as conn:
            conn.execute("UPDATE Variant SET price=NULL WHERE variant_id=?", (self.vid,))
            conn.commit()
        result = self._sale(items=[{
            "variant_id": self.vid, "qty": 1, "unit_price": 777,
        }])
        self.assertEqual(result["total"], 777)

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
                with self.assertRaises(ApplicationError) as raised:
                    self._sale(items=items)
                self.assertEqual(raised.exception.code, "validation_error")
                self.assertIn("庫存不足", raised.exception.message)
        self.assertEqual(self._stock(), 2)
