import tempfile
import unittest
from pathlib import Path

from lib.application_errors import ValidationError
from lib.db import db_conn, init_db
from lib.sales_service import SalesFacade


class SalesLayersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "pos.db"
        init_db(self.db)
        with db_conn(self.db) as conn:
            conn.execute("INSERT INTO Category(name) VALUES('測試種類')")
            cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("INSERT INTO Product(name,category_id,active) VALUES('測試商品',?,1)", (cid,))
            pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("INSERT INTO Variant(product_id,price,active) VALUES(?,500,1)", (pid,))
            self.vid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()
        self.facade = SalesFacade(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def checkout(self, **changes):
        payload = {"payment": "現金", "order_discount": 100, "paid": 900,
                   "items": [{"variant_id": self.vid, "qty": 2,
                              "unit_price": 500, "discount": 0}]}
        payload.update(changes)
        return self.facade.invoke("sales.checkout", payload)

    def test_checkout_and_queries_share_service(self):
        result = self.checkout()
        self.assertEqual(result["total"], 900)
        self.assertEqual(self.facade.invoke("sales.list", {})[0]["sale_id"], result["sale_id"])
        self.assertEqual(self.facade.invoke("sales.summary", {})["total"], 900)
        with db_conn(self.db) as conn:
            stock = conn.execute("SELECT SUM(qty) FROM StockMovement WHERE variant_id=?", (self.vid,)).fetchone()[0]
        self.assertEqual(stock, -2)

    def test_malformed_nested_payload_has_no_write(self):
        bad_values = [True, "2", 1.5]
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.checkout(items=[{"variant_id": self.vid, "qty": value,
                                      "unit_price": 500, "discount": 0}])
        with db_conn(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM Sale").fetchone()[0], 0)

    def test_checkout_rolls_back_when_stock_write_fails(self):
        with db_conn(self.db) as conn:
            conn.execute("CREATE TRIGGER fail_stock BEFORE INSERT ON StockMovement BEGIN SELECT RAISE(ABORT, 'fail'); END")
            conn.commit()
        with self.assertRaises(Exception):
            self.checkout()
        with db_conn(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM Sale").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM SaleItem").fetchone()[0], 0)

    def test_csv_uses_same_payment_filter_and_bom(self):
        self.checkout()
        self.checkout(payment="刷卡")
        exported = self.facade.invoke("sales.export", {"payment": "現金"})
        self.assertTrue(exported["content"].startswith("\ufeff"))
        self.assertIn("現金", exported["content"])
        self.assertNotIn("刷卡", exported["content"])
        self.assertEqual(exported["filename"], "sales.csv")

    def test_filters_reject_non_iso_or_impossible_calendar_dates(self):
        for action in ("sales.list", "sales.summary", "sales.export"):
            for value in ("not-a-date", "2026-99-99", "2026-02-31",
                          "2026-7-1", "2026-07-01T00:00:00"):
                with self.subTest(action=action, value=value), self.assertRaises(ValidationError):
                    self.facade.invoke(action, {"date_from": value})

    def test_legacy_date_is_validated_even_when_range_takes_precedence(self):
        with self.assertRaises(ValidationError):
            self.facade.invoke("sales.summary", {
                "date": "not-a-date", "date_from": "2026-07-01"
            })


if __name__ == "__main__":
    unittest.main()
