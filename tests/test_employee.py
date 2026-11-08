"""結帳／進貨／盤點的操作員工代號(1~6)。"""
import sqlite3

from base import FacadeTestCase
from lib import db_schema
from lib.db import get_conn, init_db


class TestEmployee(FacadeTestCase):
    def setUp(self):
        super().setUp()
        cid = self.create_category("員工測試")
        product = self.invoke("products.create", {"name": "膜", "category_id": cid,
            "variants": [{"attributes": {}, "price": 100,
                          "barcodes": [{"barcode": "E1", "source": "store"}]}]})
        self.vid = product["variant_ids"][0]

    def _one(self, sql):
        conn = get_conn(self.db)
        try:
            return conn.execute(sql).fetchone()[0]
        finally:
            conn.close()

    def test_each_operation_records_employee(self):
        self.invoke("stock.receive", {"employee": 2, "items": [{"variant_id": self.vid, "qty": 5}]})
        self.invoke("sales.checkout", {"payment": "現金", "paid": 100, "employee": 3,
            "items": [{"variant_id": self.vid, "qty": 1, "unit_price": 100}]})
        sid = self.invoke("stocktake.create", {"employee": 4})["session_id"]
        self.assertEqual(self._one("SELECT employee FROM Receipt"), 2)   # 一般進貨員工記在進貨單
        self.assertEqual(self.invoke("sales.list", {})[0]["employee"], 3)
        self.assertEqual(self.invoke("stocktake.detail", {"session_id": sid})["employee"], 4)
        self.assertIn(",3,,現金,", self.invoke("sales.export", {})["content"])  # 員工後為會員欄(一般客人空白)

    def test_missing_or_out_of_range_employee_is_rejected(self):
        payloads = {
            "stock.receive": {"items": [{"variant_id": self.vid, "qty": 1}]},
            "sales.checkout": {"payment": "現金", "paid": 100,
                "items": [{"variant_id": self.vid, "qty": 1, "unit_price": 100}]},
            "stocktake.create": {},
        }
        for action, payload in payloads.items():
            for bad in (None, 0, 7, True, "1"):
                body = dict(payload)
                if bad is not None:
                    body["employee"] = bad
                with self.subTest(action=action, employee=bad):
                    error = self.assert_application_error("validation_error", action, body)
                    self.assertIn("員工代號", error.message)
        self.assertEqual(self._one("SELECT COUNT(*) FROM StockMovement"), 0)
        self.assertEqual(self._one("SELECT COUNT(*) FROM StocktakeSession"), 0)


