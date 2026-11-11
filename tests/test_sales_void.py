"""作廢銷售單:庫存沖回、合計排除、與立帳／退貨／盤點的交互。"""
import datetime
import unittest

from base import FacadeTestCase
from lib.db import get_conn
from lib.desktop_application import DesktopFacade
from lib.desktop_bridge import DesktopBridge

TODAY = datetime.date.today().isoformat()


class SalesVoidTest(FacadeTestCase):
    def setUp(self):
        super().setUp()
        cid = self.create_category("作廢種類")
        made = self.invoke("products.create", {"name": "作廢商品", "category_id": cid, "variants": [
            {"price": 100, "barcodes": [{"barcode": "VD-1"}]}, {"price": 200, "barcodes": [{"barcode": "VD-2"}]}]})
        self.v1, self.v2 = made["variant_ids"]
        self.invoke("stock.receive", {"employee": 1, "items": [{"variant_id": self.v1, "qty": 10},
                                                               {"variant_id": self.v2, "qty": 10}]})
        self.conn = get_conn(self.db)
        self.addCleanup(self.conn.close)

    def checkout(self, payment="現金", items=None):
        items = items or [{"variant_id": self.v1, "qty": 2, "unit_price": 100}]
        total = sum(i["qty"] * i["unit_price"] for i in items)
        return self.invoke("sales.checkout", {"payment": payment, "paid": total, "employee": 1, "items": items})["sale_id"]

    def void(self, sale_id, total=200, code="duplicate", text=""):
        return self.invoke("sales.void", {"sale_id": sale_id, "reason_code": code, "reason_text": text,
                                          "expected_total": total})

    def void_error(self, code, sale_id, total=200, reason="duplicate", text=""):
        return self.assert_application_error(code, "sales.void", {
            "sale_id": sale_id, "reason_code": reason, "reason_text": text, "expected_total": total})

    def stock(self, vid):
        return self.invoke("stock.detail", {"variant_id": vid})["stock"]

    def test_void_restores_stock_and_marks_sale(self):
        sale = self.checkout(items=[{"variant_id": self.v1, "qty": 2, "unit_price": 100},
                                    {"variant_id": self.v2, "qty": 1, "unit_price": 200}])
        self.assertEqual((self.stock(self.v1), self.stock(self.v2)), (8, 9))
        self.void(sale, total=400, code="other", text="  客人改買別款  ")
        self.assertEqual((self.stock(self.v1), self.stock(self.v2)), (10, 10))
        row = self.invoke("sales.list", {})[0]
        self.assertEqual((row["sale_id"], row["void_reason"]), (sale, "其他：客人改買別款"))
        self.assertNotIn("void_employee", row)   # 不記是誰作廢的
        self.assertTrue(row["voided_at"].startswith(TODAY))
        moves = self.conn.execute("SELECT m.kind,m.qty,m.note,m.employee FROM StockMovement m JOIN SaleItem i "
                                  "ON i.item_id=m.sale_item_id WHERE i.sale_id=? ORDER BY m.move_id", (sale,)).fetchall()
        self.assertEqual([tuple(m) for m in moves], [("sale", -2, None, None), ("sale", -1, None, None),
                                                     ("void", 2, "作廢沖回", None), ("void", 1, "作廢沖回", None)])
        # 庫存明細:作廢異動不帶員工(不可帶出原結帳員工,那不是作廢的人)
        detail = self.invoke("stock.detail", {"variant_id": self.v1})["movements"]
        self.assertEqual({m["kind"]: m["employee"] for m in detail if m["kind"] in ("sale", "void")}, {"sale": 1, "void": None})

    def test_summary_export_and_dayclose_exclude_void(self):
        keep = self.checkout()
        gone = self.checkout()
        self.checkout("轉帳")
        self.void(gone)
        summary = self.invoke("sales.summary", {})
        self.assertEqual((summary["count"], summary["total"], summary["void_count"], summary["void_total"]), (2, 400, 1, 200))
        self.assertEqual(summary["by_payment"][0], {"payment": "現金", "count": 1, "total": 200})
        self.assertEqual([s["sale_id"] for s in self.invoke("sales.list", {})][-2:], [gone, keep])   # 作廢單照樣列出
        lines = self.invoke("sales.export", {})["content"].lstrip("﻿").splitlines()
        self.assertTrue(lines[0].endswith("總額,帳日,狀態,作廢原因"))
        by_id = {line.split(",")[0]: line for line in lines[1:]}
        self.assertTrue(by_id[str(gone)].endswith(f"200,{TODAY},已作廢,重複結帳"))
        self.assertTrue(by_id[str(keep)].endswith(f"200,{TODAY},,"))
        preview = self.invoke("dayclose.preview", {"business_date": TODAY})
        self.assertEqual((preview["cash_sales"], preview["void_count"], preview["void_total"]), (200, 1, 200))
        self.assertTrue(self.invoke("sales.recent")[1]["voided_at"])

    def test_cannot_void_twice_or_with_stale_total(self):
        sale = self.checkout()
        self.assertIn("內容已變更", self.void_error("conflict", sale, total=999).message)
        self.assertEqual(self.stock(self.v1), 8)
        self.void(sale)
        self.assertIn("已作廢", self.void_error("conflict", sale).message)
        self.assertEqual(self.stock(self.v1), 10)   # 沒有重複沖回
        self.void_error("not_found", 99999)

    def test_reason_validation(self):
        sale = self.checkout()
        self.void_error("validation_error", sale, reason="nope")
        self.void_error("validation_error", sale, reason="other", text="   ")
        self.void_error("validation_error", sale, reason="other", text="字" * 31)
        self.assert_application_error("validation_error", "sales.void", {   # 不再收員工代號
            "sale_id": sale, "reason_code": "qty", "reason_text": "", "employee": 1, "expected_total": 200})
        self.assertEqual(self.stock(self.v1), 8)
        self.void(sale, code="qty", text="忽略這段")
        self.assertEqual(self.invoke("sales.list", {})[0]["void_reason"], "數量錯誤")

    def test_closed_day_cannot_be_voided(self):
        sale = self.checkout()
        self.invoke("dayclose.close", {"business_date": TODAY, "counted_total": 200, "reason": "", "employee": 1,
                                       "expected": {"float_amount": 0, "cash_sales": 200}})
        self.assertIn("已立帳", self.void_error("conflict", sale).message)
        self.assertEqual(self.stock(self.v1), 8)
        later = self.checkout()   # 立帳後結的單算明天的帳,仍可作廢
        self.void(later)

    def test_sale_with_return_cannot_be_voided_until_return_cancelled(self):
        sale = self.checkout()
        rid = self.invoke("returns.create", {"employee": 1})["return_id"]
        self.invoke("returns.scan", {"return_id": rid, "variant_id": self.v1, "qty": 1})
        item = self.return_item_id(rid, self.v1)
        self.invoke("returns.set_sale", {"return_id": rid, "item_id": item, "sale_id": sale})
        self.assertIn("已有退貨單", self.void_error("conflict", sale).message)
        self.invoke("returns.cancel", {"return_id": rid, "employee": 1})
        self.void(sale)
        # 已作廢的銷售單不可再填為退貨的原銷售單
        rid2 = self.invoke("returns.create", {"employee": 1})["return_id"]
        self.invoke("returns.scan", {"return_id": rid2, "variant_id": self.v1, "qty": 1})
        error = self.assert_application_error("conflict", "returns.set_sale", {
            "return_id": rid2, "item_id": self.return_item_id(rid2, self.v1), "sale_id": sale})
        self.assertIn("已作廢", error.message)

    def test_void_during_stocktake_is_not_counted_twice(self):
        # 實物 10、誤結帳後系統 8、盤點填 10;作廢後核准,庫存是 10 不是 12
        sale = self.checkout()
        sid = self.invoke("stocktake.create", {"employee": 1})["session_id"]
        self.invoke("stocktake.scan", {"session_id": sid, "variant_id": self.v1, "qty": 10})
        self.invoke("stocktake.close", {"session_id": sid})
        self.void(sale)
        item = self.invoke("stocktake.detail", {"session_id": sid})["items"][0]
        self.assertEqual((item["system_qty"], item["counted_qty"], item["diff"]), (10, 10, 0))
        self.invoke("stocktake.approve", {"session_id": sid})
        self.assertEqual(self.stock(self.v1), 10)

    def test_void_after_stocktake_approved_only_adds_back(self):
        sale = self.checkout()
        sid = self.invoke("stocktake.create", {"employee": 1})["session_id"]
        self.invoke("stocktake.scan", {"session_id": sid, "variant_id": self.v1, "qty": 8})
        self.invoke("stocktake.close", {"session_id": sid})
        self.invoke("stocktake.approve", {"session_id": sid})
        self.void(sale)   # 已結案的盤點單不動(被鎖住也不該改),只加回庫存
        self.assertEqual(self.stock(self.v1), 10)
        self.assertEqual(self.conn.execute("SELECT system_qty FROM StocktakeItem WHERE session_id=?", (sid,)).fetchone()[0], 8)

    def test_employee_cannot_void(self):
        sale = self.checkout()
        bridge = DesktopBridge(facade=DesktopFacade(self.db))
        response = bridge.invoke("sales.void", {"sale_id": sale, "reason_code": "duplicate", "reason_text": "",
                                                "expected_total": 200})
        self.assertEqual(response["error"]["code"], "forbidden")
        self.assertEqual(self.stock(self.v1), 8)


if __name__ == "__main__":
    unittest.main()
