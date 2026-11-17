"""退款金額、額度保留、帳日與資料庫鎖定。所有資料只寫暫存庫。"""
import datetime
import sqlite3

from base import FacadeTestCase, PAY
from lib.db import get_conn


class RefundTest(FacadeTestCase):
    def setUp(self):
        super().setUp()
        cid = self.create_category("退款測試")
        self.vid = self.invoke("products.create", {"name": "測試商品", "category_id": cid,
            "variants": [{"price": 100, "barcodes": [{"barcode": "REFUND-1"}]}]})["variant_ids"][0]
        self.invoke("stock.receive", {"employee": 1, "items": [{"variant_id": self.vid, "qty": 100}]})
        self.today = datetime.date.today().isoformat()

    def create(self, kind="normal"):
        return self.invoke("returns.create", {"employee": 1, "kind": kind})["return_id"]

    def scan(self, rid, amount=60, qty=1):
        self.invoke("returns.scan", {"return_id": rid, "variant_id": self.vid, "qty": qty})
        item = self.invoke("returns.detail", {"return_id": rid})["items"][0]["id"]
        if amount is not None:
            self.invoke("returns.set_refund", {"return_id": rid, "item_id": item, "amount": amount})
        return item

    def approve(self, rid):
        detail = self.invoke("returns.detail", {"return_id": rid})
        body = {"return_id": rid, "kind": detail["kind"], "items": [
            {"item_id": i["id"], "qty": i["qty"], "disposition": i["disposition"],
             "refund": i["refund_approved"]} for i in detail["items"]]}
        if detail["kind"] == "normal":
            body["business_date"] = self.invoke("dayclose.current")["business_date"]
        return self.invoke("returns.approve", body)

    def close_day(self, counted=0):
        p = self.invoke("dayclose.preview", {"business_date": self.today})
        return self.invoke("dayclose.close", {"business_date": self.today, "employee": 1,
            "counted_total": counted, "reason": "測試差額", "expected": {
                k: p[k] for k in ("float_amount", "cash_sales", "cash_refund")}})

    def sale(self):
        return self.invoke("sales.checkout", {"payment_id": PAY["轉帳"], "employee": 1,
            "order_discount": 1, "paid": 299, "items": [
                {"variant_id": self.vid, "qty": 2, "unit_price": 100},
                {"variant_id": self.vid, "qty": 1, "unit_price": 100}]})["sale_id"]

    def test_scan_requires_amount_and_rejects_over_price(self):
        rid = self.create()
        item = self.scan(rid, None)
        self.assert_application_error("conflict", "returns.close", {"return_id": rid})
        self.assert_application_error("conflict", "returns.set_refund", {"return_id": rid, "item_id": item, "amount": 101})
        self.invoke("returns.set_refund", {"return_id": rid, "item_id": item, "amount": 100})
        self.invoke("returns.close", {"return_id": rid})
        self.approve(rid)
        self.assertEqual(self.invoke("returns.detail", {"return_id": rid})["refund_amount"], 100)

    def test_cash_refund_pending_block_and_snapshot(self):
        rid = self.create()
        item = self.scan(rid)
        self.invoke("returns.close", {"return_id": rid})
        self.assert_application_error("conflict", "dayclose.close", {"business_date": self.today,
            "counted_total": 0, "reason": "", "employee": 1,
            "expected": {"float_amount": 0, "cash_sales": 0, "cash_refund": 0}})
        self.invoke("returns.set_refund", {"return_id": rid, "item_id": item, "amount": 50})
        self.approve(rid)
        p = self.invoke("dayclose.preview", {"business_date": self.today})
        self.assertEqual((p["cash_refund"], p["refund_count"], p["pending_returns"]), (50, 1, 0))
        closed = self.close_day()
        self.assertEqual((closed["cash_refund"], closed["diff"]), (50, 50))
        self.assertEqual(self.invoke("dayclose.list", {})[0]["cash_refund"], 50)
        summary = self.invoke("sales.summary", {"date": self.today})
        self.assertEqual((summary["refund_total"], summary["net_total"]), (50, -50))

    def test_approved_discard_is_locked_without_stock_movement(self):
        rid = self.create()
        item = self.scan(rid)
        self.invoke("returns.close", {"return_id": rid})
        self.invoke("returns.set_disposition", {"return_id": rid, "item_id": item, "disposition": "discard"})
        self.approve(rid)
        conn = get_conn(self.db)
        try:
            for sql, args in [
                ("UPDATE ReturnSession SET refund_amount=1 WHERE return_id=?", (rid,)),
                ("DELETE FROM ReturnSession WHERE return_id=?", (rid,)),
                ("UPDATE ReturnItem SET refund_approved=1 WHERE id=?", (item,)),
                ("UPDATE ReturnItem SET qty=2 WHERE id=?", (item,)),
                ("DELETE FROM ReturnItem WHERE id=?", (item,)),
                ("INSERT OR REPLACE INTO ReturnItem(id,return_id,variant_id,qty) VALUES(?,?,?,1)", (item, rid, self.vid)),
                ("INSERT OR REPLACE INTO ReturnSession(return_id,employee) VALUES(?,1)", (rid,))]:
                with self.subTest(sql=sql):
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(sql, args)
                    conn.rollback()
        finally:
            conn.close()

    def test_sale_lines_reserve_separately_and_last_piece_gets_remainder(self):
        sale = self.sale()
        rid = self.create()
        self.invoke("returns.load_sale", {"return_id": rid, "sale_id": sale})
        lines = self.invoke("returns.detail", {"return_id": rid})["sale_lines"]
        self.assertEqual([i["refund_limit"] for i in lines], [199, 100])
        self.invoke("returns.pick", {"return_id": rid, "sale_item_id": lines[0]["item_id"], "qty": 1})
        item = self.invoke("returns.detail", {"return_id": rid})["items"][0]
        self.assertEqual(item["refund_request"], 100)
        self.invoke("returns.close", {"return_id": rid})
        self.approve(rid)
        r2 = self.create()
        self.invoke("returns.load_sale", {"return_id": r2, "sale_id": sale})
        lines = self.invoke("returns.detail", {"return_id": r2})["sale_lines"]
        self.assertEqual([(i["returnable_qty"], i["refund_limit"]) for i in lines], [(1, 99), (1, 100)])
        self.assert_application_error("conflict", "returns.scan", {"return_id": r2, "variant_id": self.vid, "qty": 1})

    def test_repair_has_no_money_and_employee_cannot_review(self):
        rid = self.create("repair")
        item = self.scan(rid, None)
        self.assert_application_error("conflict", "returns.set_refund", {"return_id": rid, "item_id": item, "amount": 0})
        self.invoke("returns.close", {"return_id": rid})
        self.facade.session.logout()
        self.assert_application_error("conflict", "returns.set_kind", {"return_id": rid, "kind": "normal"})
        self.assert_application_error("forbidden", "returns.set_refund", {"return_id": rid, "item_id": item, "amount": 0})

    def test_rejected_line_releases_reservation_and_cannot_be_reclaimed(self):
        sale = self.sale()
        a = self.create()
        self.invoke("returns.load_sale", {"return_id":a,"sale_id":sale})
        line = self.invoke("returns.detail", {"return_id":a})["sale_lines"][0]["item_id"]
        self.invoke("returns.pick", {"return_id":a,"sale_item_id":line,"qty":2})
        item = self.invoke("returns.detail", {"return_id":a})["items"][0]["id"]
        self.invoke("returns.close", {"return_id":a})
        self.invoke("returns.set_disposition", {"return_id":a,"item_id":item,"disposition":"reject"})
        b = self.create()
        self.invoke("returns.load_sale", {"return_id":b,"sale_id":sale})
        self.invoke("returns.pick", {"return_id":b,"sale_item_id":line,"qty":2})
        self.assert_application_error("conflict","returns.set_disposition",{"return_id":a,"item_id":item,"disposition":"restock"})
        self.invoke("returns.withdraw", {"return_id":a})
        self.invoke("returns.close", {"return_id":a})
        self.approve(a)
        self.assertEqual(self.invoke("returns.detail", {"return_id":a})["refund_amount"],0)

    def test_stale_refund_or_business_date_cannot_be_approved(self):
        rid = self.create()
        item = self.scan(rid)
        self.invoke("returns.close", {"return_id":rid})
        shown = self.approve_payload(rid)
        self.invoke("returns.set_refund", {"return_id":rid,"item_id":item,"amount":40})
        self.assert_application_error("conflict","returns.approve",shown)
        shown = self.approve_payload(rid)
        shown["business_date"] = "2000-01-01"
        self.assert_application_error("conflict","returns.approve",shown)
        self.assertEqual(self.invoke("returns.detail", {"return_id":rid})["status"],"pending")

    def test_stale_dayclose_does_not_ignore_new_refund(self):
        body = {"business_date":self.today,"employee":1,"reason":"","counted_total":0,
                "expected":{"float_amount":0,"cash_sales":0,"cash_refund":0}}
        rid=self.create();self.scan(rid)
        self.invoke("returns.close", {"return_id":rid});self.approve(rid)
        self.assert_application_error("conflict","dayclose.close",body)

    def test_cash_rename_and_filtered_summary(self):
        self.invoke("payments.update", {"payment_id":1,"name":"錢櫃收款"})
        rid=self.create();self.scan(rid)
        self.invoke("returns.close", {"return_id":rid});self.approve(rid)
        summary=self.invoke("sales.summary",{})
        cash=next(b for b in summary["by_payment"] if b["payment_id"]==1)
        self.assertEqual((cash["payment"],cash["cash_net"]),("錢櫃收款",-60))
        for filters in ({"payment_id":1},{"member_phone":"123"}):
            filtered=self.invoke("sales.summary",filters)
            self.assertFalse(filtered["refunds_included"])
            self.assertNotIn("refund_total",filtered)

    def test_next_day_refund_history_uses_business_date(self):
        self.close_day()
        rid=self.create();self.scan(rid)
        self.invoke("returns.close", {"return_id":rid});self.approve(rid)
        tomorrow=(datetime.date.today()+datetime.timedelta(days=1)).isoformat()
        detail=self.invoke("returns.detail", {"return_id":rid})
        self.assertEqual(detail["business_date"],tomorrow)
        self.assertEqual(self.invoke("returns.history",{"date_from":self.today,"date_to":self.today}),[])
        self.assertEqual(self.invoke("returns.history",{"date_from":tomorrow,"date_to":tomorrow})[0]["return_id"],rid)

    def test_unpriced_return_and_price_change_after_submission(self):
        conn=get_conn(self.db)
        try:
            conn.execute("UPDATE Variant SET price=NULL WHERE variant_id=?",(self.vid,));conn.commit()
            rid=self.create();item=self.scan(rid,500)
            self.invoke("returns.close", {"return_id":rid})
            conn.execute("UPDATE Variant SET price=10 WHERE variant_id=?",(self.vid,));conn.commit()
            self.approve(rid)
            self.assertEqual(self.invoke("returns.detail", {"return_id":rid})["refund_amount"],500)
        finally: conn.close()

    def test_switching_kind_clears_prices_and_is_locked_after_submit(self):
        rid=self.create();item=self.scan(rid)
        for kind in ("repair","normal"):
            self.invoke("returns.set_kind", {"return_id":rid,"kind":kind})
        detail=self.invoke("returns.detail", {"return_id":rid})
        self.assertIsNone(detail["items"][0]["refund_request"])
        self.assert_application_error("conflict","returns.close",{"return_id":rid})
        self.invoke("returns.set_refund", {"return_id":rid,"item_id":item,"amount":60})
        self.invoke("returns.close", {"return_id":rid})
        # 送出後管理者也不能改種類:改成返修會清掉已退給客人的金額
        self.assert_application_error("conflict","returns.set_kind",{"return_id":rid,"kind":"repair"})
        self.assertEqual(self.invoke("returns.detail", {"return_id":rid})["items"][0]["refund_request"],60)

    def test_booked_item_cannot_be_moved_or_replaced_from_another_sheet(self):
        rid=self.create();item=self.scan(rid)
        self.invoke("returns.close", {"return_id":rid});self.approve(rid)
        other=self.create();free=self.scan(other,10)
        conn=get_conn(self.db)
        try:
            for sql,args in [
                ("UPDATE ReturnItem SET return_id=? WHERE id=?",(other,item)),
                ("UPDATE ReturnItem SET return_id=? WHERE id=?",(rid,free)),
                ("UPDATE OR REPLACE ReturnItem SET id=? WHERE id=?",(item,free)),
                ("UPDATE OR REPLACE ReturnItem SET return_id=? WHERE id=?",(rid,free)),
                ("UPDATE OR REPLACE ReturnSession SET return_id=? WHERE return_id=?",(rid,other))]:
                with self.subTest(sql=sql):
                    with self.assertRaises(sqlite3.IntegrityError): conn.execute(sql,args)
                    conn.rollback()
        finally:conn.close()

