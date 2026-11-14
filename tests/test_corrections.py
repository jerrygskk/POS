"""更正付款方式與更正操作員工:條件、歷程、與立帳／作廢的交互。"""
import datetime
import unittest

from base import FacadeTestCase
from lib.db import get_conn
from lib.desktop_application import DesktopFacade
from lib.desktop_bridge import DesktopBridge

TODAY = datetime.date.today().isoformat()


class CorrectionsTest(FacadeTestCase):
    def setUp(self):
        super().setUp()
        cid = self.create_category("更正種類")
        made = self.invoke("products.create", {"name": "更正商品", "category_id": cid,
                                               "variants": [{"price": 100, "barcodes": [{"barcode": "CR-1"}]}]})
        self.vid = made["variant_ids"][0]
        self.receipt = self.invoke("stock.receive", {"employee": 1, "items": [{"variant_id": self.vid, "qty": 20}]})["receipt_id"]
        self.conn = get_conn(self.db)
        self.addCleanup(self.conn.close)

    def checkout(self, payment="現金"):
        return self.invoke("sales.checkout", {"payment": payment, "paid": 100, "employee": 1,
                                              "items": [{"variant_id": self.vid, "qty": 1, "unit_price": 100}]})["sale_id"]

    def set_payment(self, sale, payment, expected="現金"):
        return self.invoke("sales.set_payment", {"sale_id": sale, "payment": payment,
                                                 "expected_payment": expected})

    def payment_error(self, code, sale, payment="轉帳", expected="現金"):
        return self.assert_application_error(code, "sales.set_payment", {
            "sale_id": sale, "payment": payment, "expected_payment": expected})

    def set_employee(self, doc_type, doc_id, employee, expected):
        return self.invoke("corrections.set_employee", {"doc_type": doc_type, "doc_id": doc_id, "employee": employee,
                                                        "expected_employee": expected})

    def close_today(self):
        p = self.invoke("dayclose.preview", {"business_date": TODAY})
        self.invoke("dayclose.close", {"business_date": TODAY, "counted_total": p["float_amount"] + p["cash_sales"],
                                       "reason": "", "employee": 1,
                                       "expected": {"float_amount": p["float_amount"], "cash_sales": p["cash_sales"]}})

    def sale_row(self, sale):
        return next(s for s in self.invoke("sales.list", {}) if s["sale_id"] == sale)

    # ---- 付款方式 ----
    def test_payment_correction_changes_totals_and_keeps_history(self):
        sale = self.checkout("轉帳")
        self.checkout("現金")
        self.assertEqual(self.invoke("dayclose.preview", {"business_date": TODAY})["cash_sales"], 100)
        before = tuple(self.conn.execute("SELECT paid,change,total FROM Sale WHERE sale_id=?", (sale,)).fetchone())
        self.set_payment(sale, "現金", expected="轉帳")
        self.assertEqual(self.invoke("dayclose.preview", {"business_date": TODAY})["cash_sales"], 200)
        by_payment = {b["payment"]: b["total"] for b in self.invoke("sales.summary", {})["by_payment"]}
        self.assertEqual((by_payment["現金"], by_payment["轉帳"]), (200, 0))
        self.set_payment(sale, "LINE Pay", expected="現金")
        row = self.sale_row(sale)
        self.assertEqual(row["payment"], "LINE Pay")
        self.assertEqual([(c["field"], c["old_value"], c["new_value"]) for c in row["corrections"]],
                         [("payment", "轉帳", "現金"), ("payment", "現金", "LINE Pay")])
        self.assertEqual(set(row["corrections"][0]), {"ts", "field", "old_value", "new_value"})   # 不記是誰更正的
        self.assertTrue(row["corrections"][0]["ts"].startswith(TODAY))
        # 實收與找零保留原值
        self.assertEqual(tuple(self.conn.execute("SELECT paid,change,total FROM Sale WHERE sale_id=?", (sale,)).fetchone()), before)

    def test_payment_correction_conditions(self):
        sale = self.checkout()
        self.assertIn("內容已變更", self.payment_error("conflict", sale, expected="轉帳").message)
        self.payment_error("validation_error", sale, payment="現金")       # 與目前相同
        self.payment_error("validation_error", sale, payment="刷卡")       # 不在設定清單
        self.payment_error("not_found", 99999)
        self.assertEqual(self.sale_row(sale)["corrections"], [])
        voided = self.checkout()
        self.invoke("sales.void", {"sale_id": voided, "reason_code": "duplicate", "reason_text": "",
                                   "expected_total": 100})
        self.assertIn("已作廢", self.payment_error("conflict", voided).message)
        self.close_today()
        self.assertIn("已立帳", self.payment_error("conflict", sale).message)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM Correction").fetchone()[0], 0)

    # ---- 員工 ----
    def test_employee_correction_on_all_four_documents(self):
        sale = self.checkout()
        rid = self.invoke("returns.create", {"employee": 1})["return_id"]
        self.invoke("returns.scan", {"return_id": rid, "variant_id": self.vid, "qty": 1})
        self.invoke("returns.close", {"return_id": rid})
        self.invoke("returns.approve", self.approve_payload(rid))
        sid = self.invoke("stocktake.create", {"employee": 1})["session_id"]
        self.invoke("stocktake.scan", {"session_id": sid, "variant_id": self.vid, "qty": 20})
        self.invoke("stocktake.close", {"session_id": sid})
        self.invoke("stocktake.approve", {"session_id": sid})
        stock = self.invoke("stock.detail", {"variant_id": self.vid})["stock"]
        for doc_type, doc_id in (("sale", sale), ("return", rid), ("receipt", self.receipt), ("stocktake", sid)):
            self.set_employee(doc_type, doc_id, 2, 1)
        self.assertEqual(self.sale_row(sale)["employee"], 2)
        lists = {"sale": self.sale_row(sale), "return": self.invoke("returns.history", {"kind": "normal"})[0],
                 "receipt": self.invoke("stock.receipts", {})[0], "stocktake": self.invoke("stocktake.history", {})[0]}
        for doc_type, row in lists.items():
            self.assertEqual(row["employee"], 2, doc_type)
            self.assertEqual([(c["field"], c["old_value"], c["new_value"]) for c in row["corrections"]],
                             [("employee", "1", "2")], doc_type)
        self.assertEqual(self.invoke("returns.detail", {"return_id": rid})["corrections"][0]["new_value"], "2")
        self.assertEqual(self.invoke("stock.detail", {"variant_id": self.vid})["stock"], stock)   # 不動庫存

    def test_employee_correction_has_no_time_limit(self):
        sale = self.checkout()
        voided = self.checkout()
        self.invoke("sales.void", {"sale_id": voided, "reason_code": "duplicate", "reason_text": "",
                                   "expected_total": 100})
        self.close_today()
        self.set_employee("sale", sale, 3, 1)       # 立帳後
        self.set_employee("sale", voided, 3, 1)     # 已作廢的單
        self.assertEqual((self.sale_row(sale)["employee"], self.sale_row(voided)["employee"]), (3, 3))

    def test_employee_correction_from_null(self):
        self.conn.execute("INSERT INTO Sale(sale_id,payment,total,paid,business_date) VALUES(500,'現金',1,1,'2026-01-01')")
        self.conn.commit()
        self.set_employee("sale", 500, 2, None)
        row = self.conn.execute("SELECT old_value,new_value FROM Correction WHERE sale_id=500").fetchone()
        self.assertEqual(tuple(row), (None, "2"))

    def test_employee_correction_conditions(self):
        sale = self.checkout()
        base = {"doc_type": "sale", "doc_id": sale, "employee": 2, "expected_employee": 1}
        self.assert_application_error("conflict", "corrections.set_employee", {**base, "expected_employee": 5})
        self.assert_application_error("validation_error", "corrections.set_employee", {**base, "employee": 1})
        self.assert_application_error("validation_error", "corrections.set_employee", {**base, "employee": 7})
        self.assert_application_error("validation_error", "corrections.set_employee", {**base, "by_employee": 1})   # 不再收操作人
        self.assert_application_error("validation_error", "corrections.set_employee", {**base, "doc_type": "member"})
        self.assert_application_error("not_found", "corrections.set_employee", {**base, "doc_id": 99999})
        missing = dict(base)
        del missing["expected_employee"]
        self.assert_application_error("validation_error", "corrections.set_employee", missing)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM Correction").fetchone()[0], 0)
        self.assertEqual(self.sale_row(sale)["employee"], 1)

    def test_employee_cannot_correct(self):
        sale = self.checkout()
        bridge = DesktopBridge(facade=DesktopFacade(self.db))
        for action, payload in (
                ("sales.set_payment", {"sale_id": sale, "payment": "轉帳", "expected_payment": "現金"}),
                ("corrections.set_employee", {"doc_type": "sale", "doc_id": sale, "employee": 2, "expected_employee": 1})):
            self.assertEqual(bridge.invoke(action, payload)["error"]["code"], "forbidden", action)
        row = self.sale_row(sale)
        self.assertEqual((row["payment"], row["employee"], row["corrections"]), ("現金", 1, []))


if __name__ == "__main__":
    unittest.main()
