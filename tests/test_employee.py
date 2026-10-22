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
        self.assertEqual(self._one("SELECT employee FROM StockMovement WHERE kind='purchase'"), 2)
        self.assertEqual(self.invoke("sales.list", {})[0]["employee"], 3)
        self.assertEqual(self.invoke("stocktake.detail", {"session_id": sid})["employee"], 4)
        self.assertIn(",3,現金,", self.invoke("sales.export", {})["content"])

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


class TestEmployeeMigration(FacadeTestCase):
    def test_upgrade_adds_employee_and_drops_stocktake_operator(self):
        conn = sqlite3.connect(self.db)
        for table in ("Sale", "StockMovement", "StocktakeSession"):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN employee")
        conn.execute("ALTER TABLE StocktakeSession ADD COLUMN operator TEXT")
        conn.execute("UPDATE Setting SET value=? WHERE key='schema_version'",
                     ("15",))
        conn.commit()
        conn.close()
        init_db(self.db)
        conn = sqlite3.connect(self.db)
        try:
            for table in ("Sale", "StockMovement", "StocktakeSession"):
                cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                self.assertIn("employee", cols)
                self.assertNotIn("operator", cols)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO StocktakeSession(employee) VALUES(7)")
        finally:
            conn.close()
