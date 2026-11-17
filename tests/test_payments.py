"""付款方式:編號與名稱一起寫、設定頁的新增／排序／停用／改比例、立帳快照與資料庫層保護。"""
import datetime
import sqlite3
import unittest

from base import FacadeTestCase
from lib.db import get_conn
from lib.desktop_application import DesktopFacade
from lib.desktop_bridge import DesktopBridge


def day(offset=0):
    return (datetime.date.today() + datetime.timedelta(days=offset)).isoformat()


class PaymentTests(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.make_category_with_field("顏色", options=["黑"])
        self.vid = self.create_product({"顏色": "黑"}, price=100)["variant_ids"][0]
        self.invoke("stock.receive", {"employee": 1, "items": [{"variant_id": self.vid, "qty": 50}]})
        self.conn = get_conn(self.db)
        self.addCleanup(self.conn.close)

    def checkout(self, payment_id=1):
        return self.invoke("sales.checkout", {"payment_id": payment_id, "paid": 100, "employee": 1,
                                              "items": [{"variant_id": self.vid, "qty": 1, "unit_price": 100}]})["sale_id"]

    def raw_sale(self, business_date, payment_id, total):
        name = self.conn.execute("SELECT name FROM PaymentMethod WHERE payment_id=?", (payment_id,)).fetchone()[0]
        self.conn.execute("INSERT INTO Sale(ts,payment_id,payment,total,paid,business_date) VALUES(?,?,?,?,?,?)",
                          (business_date + " 12:00:00", payment_id, name, total, total, business_date))
        self.conn.commit()

    def close(self, business_date):
        p = self.invoke("dayclose.preview", {"business_date": business_date})
        self.invoke("dayclose.close", {"business_date": business_date, "counted_total": p["float_amount"] + p["cash_sales"],
                                       "reason": "", "employee": 1,
                                       "expected": {"float_amount": p["float_amount"], "cash_sales": p["cash_sales"], "cash_refund": 0}})

    def blocked(self, sql, args=()):
        with self.assertRaises(sqlite3.IntegrityError, msg=sql):
            self.conn.execute(sql, args)
        self.conn.rollback()

    def names(self):
        return [p["name"] for p in self.invoke("payments.list")]

    # ---- 結帳、更正、篩選 ----
    def test_list_returns_every_method_with_flags(self):
        self.assertEqual(self.invoke("payments.list"), [
            {"payment_id": 1, "name": "現金", "active": True, "fee_rate": 0, "is_cash": True, "used": False},
            {"payment_id": 2, "name": "轉帳", "active": True, "fee_rate": 0, "is_cash": False, "used": False},
            {"payment_id": 3, "name": "LINE Pay", "active": True, "fee_rate": 220, "is_cash": False, "used": False},
            {"payment_id": 4, "name": "台灣Pay", "active": False, "fee_rate": 0, "is_cash": False, "used": False}])

    def test_checkout_writes_id_and_name_together_and_rejects_unusable(self):
        sale = self.checkout(3)
        self.assertEqual(tuple(self.conn.execute("SELECT payment_id,payment,paid,change FROM Sale WHERE sale_id=?", (sale,)).fetchone()),
                         (3, "LINE Pay", 100, 0))
        for bad in (4, 999, "1", True, None):   # 停用、不存在、不是整數
            self.assert_application_error("validation_error", "sales.checkout", {
                "payment_id": bad, "paid": 100, "employee": 1, "items": [{"variant_id": self.vid, "qty": 1, "unit_price": 100}]})
        self.assert_application_error("validation_error", "sales.checkout", {   # 不再收名稱
            "payment": "現金", "paid": 100, "employee": 1, "items": [{"variant_id": self.vid, "qty": 1, "unit_price": 100}]})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM Sale").fetchone()[0], 1)

    def test_disabled_method_keeps_history_filter_and_totals(self):
        sale = self.checkout(2)
        self.checkout(1)
        self.invoke("payments.update", {"payment_id": 2, "active": False})
        self.assert_application_error("validation_error", "sales.checkout", {
            "payment_id": 2, "paid": 100, "employee": 1, "items": [{"variant_id": self.vid, "qty": 1, "unit_price": 100}]})
        self.assertEqual([s["sale_id"] for s in self.invoke("sales.list", {"payment_id": 2})], [sale])   # 舊單仍篩得到
        by_payment = {b["payment_id"]: (b["payment"], b["count"], b["total"]) for b in self.invoke("sales.summary", {})["by_payment"]}
        self.assertEqual(by_payment[2], ("轉帳", 1, 100))
        self.assertEqual(self.invoke("sales.summary", {"payment_id": 2})["total"], 100)
        self.assertIn("轉帳", self.invoke("sales.export", {"payment_id": 2})["content"])
        # 更正:不能改成停用的;防舊畫面比編號
        other = self.checkout(1)
        self.assert_application_error("validation_error", "sales.set_payment",
                                      {"sale_id": other, "payment_id": 2, "expected_payment_id": 1})
        self.assert_application_error("conflict", "sales.set_payment",
                                      {"sale_id": other, "payment_id": 3, "expected_payment_id": 2})
        self.invoke("sales.set_payment", {"sale_id": other, "payment_id": 3, "expected_payment_id": 1})
        self.assertEqual(tuple(self.conn.execute("SELECT payment_id,payment FROM Sale WHERE sale_id=?", (other,)).fetchone()),
                         (3, "LINE Pay"))

    # ---- 設定頁 ----
    def test_create_appends_last_and_validates_name_and_rate(self):
        created = self.invoke("payments.create", {"name": "  街口  ", "fee_rate": 150})
        self.assertEqual(self.invoke("payments.list")[-1],
                         {"payment_id": created["payment_id"], "name": "街口", "active": True, "fee_rate": 150, "is_cash": False, "used": False})
        for name in ("街口", " 街口 ", "台灣Pay", "台灣ｐａｙ", "line pay", "", "   ", "一二三四五六七八九十一", 5, None):
            self.assert_application_error("validation_error", "payments.create", {"name": name, "fee_rate": 0})
        for rate in (-1, 10001, 2.2, "220", True, None):
            self.assert_application_error("validation_error", "payments.create", {"name": "新的", "fee_rate": rate})
        self.assert_application_error("validation_error", "payments.create", {"name": "新的"})
        self.assertEqual(self.names(), ["現金", "轉帳", "LINE Pay", "台灣Pay", "街口"])

    def test_update_rate_and_active_and_cash_is_fixed(self):
        self.invoke("payments.update", {"payment_id": 3, "fee_rate": 250})
        self.invoke("payments.update", {"payment_id": 4, "active": True, "fee_rate": 10000})
        rows = {p["payment_id"]: p for p in self.invoke("payments.list")}
        self.assertEqual((rows[3]["fee_rate"], rows[4]["active"], rows[4]["fee_rate"]), (250, True, 10000))
        for body in ({"payment_id": 1, "active": False}, {"payment_id": 1, "fee_rate": 1},   # 現金不可停用、不抽成
                     {"payment_id": 3}, {"payment_id": 3, "active": 0}, {"payment_id": 3, "fee_rate": 10001},
                     {"payment_id": 3, "sort": 1}, {"payment_id": "3", "active": True}):
            self.assert_application_error("validation_error", "payments.update", body)
        self.assert_application_error("not_found", "payments.update", {"payment_id": 999, "active": True})
        self.invoke("payments.update", {"payment_id": 1, "active": True, "fee_rate": 0})   # 現金送原值不算錯
        self.assertTrue(self.invoke("payments.list")[0]["active"])

    def test_sort_needs_every_id(self):
        self.invoke("payments.sort", {"ids": [3, 1, 4, 2]})
        self.assertEqual(self.names(), ["LINE Pay", "現金", "台灣Pay", "轉帳"])
        self.assertEqual([b["payment"] for b in self.invoke("sales.summary", {})["by_payment"]],
                         ["LINE Pay", "現金", "轉帳"])   # 報表欄位跟著排;停用且沒交易的台灣Pay 不列
        for ids in ([1, 2, 3], [1, 2, 3, 4, 5], [1, 1, 2, 3], [1, 2, 3, "4"], "1234", []):
            self.assert_application_error("validation_error", "payments.sort", {"ids": ids})
        self.assertEqual(self.names(), ["LINE Pay", "現金", "台灣Pay", "轉帳"])

    def test_rename_changes_list_only_and_keeps_names_unique(self):
        sale = self.checkout(3)
        self.invoke("payments.update", {"payment_id": 3, "name": " 賴付 "})
        self.invoke("payments.update", {"payment_id": 1, "name": "現鈔"})        # 現金靠記號認,可以改名
        self.assertEqual(self.names(), ["現鈔", "轉帳", "賴付", "台灣Pay"])
        # 舊單上留的是當時的名稱;篩選與合計靠編號,欄名是新名稱
        self.assertEqual(self.conn.execute("SELECT payment FROM Sale WHERE sale_id=?", (sale,)).fetchone()[0], "LINE Pay")
        self.assertEqual([s["sale_id"] for s in self.invoke("sales.list", {"payment_id": 3})], [sale])
        self.assertEqual({b["payment_id"]: b["payment"] for b in self.invoke("sales.summary", {})["by_payment"]}[3], "賴付")
        self.assertEqual(self.checkout(1), sale + 1)                            # 改名後現金照常結帳
        for name in ("轉帳", "台灣ｐａｙ", "", "   ", "一二三四五六七八九十一", 5):      # 與別人同名(含停用)、空白、太長
            self.assert_application_error("validation_error", "payments.update", {"payment_id": 3, "name": name})
        self.invoke("payments.update", {"payment_id": 3, "name": "賴付"})        # 改成自己原本的名稱不算重複
        self.invoke("payments.update", {"payment_id": 2, "name": "LINE Pay"})    # 別人放掉的舊名稱可以再用
        self.assertEqual(self.names(), ["現鈔", "LINE Pay", "賴付", "台灣Pay"])

    def test_delete_only_unused_non_cash(self):
        self.checkout(2)
        d = day(-1)
        self.raw_sale(d, 3, 100)
        self.close(d)
        self.assertEqual([p["used"] for p in self.invoke("payments.list")], [False, True, True, False])
        for payment_id in (1, 2, 3):                               # 現金、有銷售單、已立帳
            self.assert_application_error("validation_error", "payments.delete", {"payment_id": payment_id})
        self.assert_application_error("not_found", "payments.delete", {"payment_id": 999})
        self.assert_application_error("validation_error", "payments.delete", {"payment_id": "4"})
        self.invoke("payments.delete", {"payment_id": 4})
        self.assertEqual(self.names(), ["現金", "轉帳", "LINE Pay"])
        self.assertEqual(self.invoke("payments.create", {"name": "台灣Pay", "fee_rate": 0})["payment_id"], 5)   # 編號不重用

    def test_employee_cannot_change_payment_settings(self):
        bridge = DesktopBridge(facade=DesktopFacade(self.db))   # 未登入＝員工
        self.assertTrue(bridge.invoke("payments.list", {})["ok"])
        for action, payload in (("payments.create", {"name": "街口", "fee_rate": 0}),
                                ("payments.update", {"payment_id": 2, "active": False}),
                                ("payments.update", {"payment_id": 2, "name": "匯款"}),
                                ("payments.delete", {"payment_id": 4}),
                                ("payments.sort", {"ids": [4, 3, 2, 1]})):
            self.assertEqual(bridge.invoke(action, payload)["error"]["code"], "forbidden", action)
        self.assertEqual(self.names(), ["現金", "轉帳", "LINE Pay", "台灣Pay"])
        self.assertTrue(self.invoke("payments.list")[1]["active"])

    def test_database_keeps_exactly_one_cash_and_fixed_ids(self):
        self.blocked("DELETE FROM PaymentMethod WHERE payment_id=1")
        self.blocked("UPDATE PaymentMethod SET is_cash=0 WHERE payment_id=1")
        self.blocked("UPDATE PaymentMethod SET is_cash=1 WHERE payment_id=2")
        self.blocked("INSERT INTO PaymentMethod(name,is_cash) VALUES('現金二',1)")
        self.blocked("UPDATE PaymentMethod SET active=0 WHERE payment_id=1")
        self.blocked("UPDATE PaymentMethod SET fee_rate=1 WHERE payment_id=1")
        self.blocked("UPDATE PaymentMethod SET payment_id=99 WHERE payment_id=2")
        self.blocked("INSERT INTO PaymentMethod(name) VALUES('轉帳')")
        self.blocked("INSERT INTO PaymentMethod(name) VALUES('  ')")
        self.blocked("INSERT INTO PaymentMethod(name,fee_rate) VALUES('超過',10001)")
        # REPLACE 會先刪掉撞到的舊列、不經過刪除保護:撞編號、撞名稱、撞現金標記都要擋
        self.blocked("INSERT OR REPLACE INTO PaymentMethod(payment_id,name,is_cash) VALUES(1,'現金',0)")
        self.blocked("INSERT OR REPLACE INTO PaymentMethod(name,is_cash) VALUES('現金',0)")
        self.blocked("INSERT OR REPLACE INTO PaymentMethod(name,is_cash) VALUES('現金二',1)")
        self.blocked("INSERT OR REPLACE INTO PaymentMethod(payment_id,name) VALUES(2,'別的')")
        self.assertEqual(self.names(), ["現金", "轉帳", "LINE Pay", "台灣Pay"])
        self.checkout(2)
        self.blocked("DELETE FROM PaymentMethod WHERE payment_id=2")   # 有銷售單用過的刪不掉
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM PaymentMethod WHERE is_cash=1").fetchone()[0], 1)

    # ---- 立帳快照 ----
    def test_close_snapshots_traded_methods_and_later_rate_change_keeps_old_days(self):
        d1, d2 = day(-2), day(-1)
        self.raw_sale(d1, 1, 300)
        self.raw_sale(d1, 3, 20000)
        self.close(d1)
        self.assertEqual([tuple(r) for r in self.conn.execute(
            "SELECT payment_id,name,count,total,fee_rate FROM DayClosePayment WHERE business_date=? ORDER BY payment_id", (d1,))],
            [(1, "現金", 1, 300, 0), (3, "LINE Pay", 1, 20000, 220)])   # 沒交易的不存
        self.assertEqual(self.conn.execute("SELECT cash_sales FROM DayClose WHERE business_date=?", (d1,)).fetchone()[0], 300)
        # 月中改比例、又新增一個付款方式
        self.invoke("payments.update", {"payment_id": 3, "fee_rate": 250})
        new = self.invoke("payments.create", {"name": "街口", "fee_rate": 100})["payment_id"]
        self.raw_sale(d2, 3, 10000)
        self.raw_sale(d2, new, 500)
        self.close(d2)
        rows = {r["business_date"]: r for r in self.invoke("dayclose.list", {})}
        def cells(record):
            return [(b["payment"], b["total"], b["fee_rate"]) for b in record["by_payment"]]
        # 兩天欄位與順序相同:舊的一天沒有街口也補一欄 0;停用且這段期間沒交易的台灣Pay 不列
        self.assertEqual(cells(rows[d1]), [("現金", 300, 0), ("轉帳", 0, 0), ("LINE Pay", 20000, 220), ("街口", 0, 0)])
        self.assertEqual(cells(rows[d2]), [("現金", 0, 0), ("轉帳", 0, 0), ("LINE Pay", 10000, 250), ("街口", 500, 100)])
        self.assertEqual((rows[d1]["sales_total"], rows[d2]["sales_total"]), (20300, 10500))
        # 停用後:有金額的期間欄位與金額還在;查不到它金額的期間就不列那一欄
        self.invoke("payments.update", {"payment_id": new, "active": False})
        self.assertEqual(cells(self.invoke("dayclose.list", {})[0])[3], ("街口", 500, 100))
        self.assertEqual(cells(self.invoke("dayclose.list", {"date_from": d1, "date_to": d1})[0]),
                         [("現金", 300, 0), ("轉帳", 0, 0), ("LINE Pay", 20000, 220)])
        # 改名:報表欄名跟著新名稱,同一個付款方式仍只有一欄
        self.invoke("payments.update", {"payment_id": 3, "name": "LP"})
        self.assertEqual([c[0] for c in cells(self.invoke("dayclose.list", {})[0])], ["現金", "轉帳", "LP", "街口"])

    def test_snapshot_rows_cannot_be_appended_changed_or_orphaned(self):
        d = day(-1)
        self.raw_sale(d, 1, 100)
        self.close(d)
        self.blocked("INSERT INTO DayClosePayment VALUES(?,2,'轉帳',1,50,0)", (d,))             # 立帳後追加
        self.blocked("INSERT OR REPLACE INTO DayClosePayment VALUES(?,1,'現金',9,999,0)", (d,))  # 蓋掉既有的
        self.blocked("UPDATE DayClosePayment SET total=1")
        self.blocked("DELETE FROM DayClosePayment")
        self.blocked("INSERT INTO DayClosePayment VALUES('2026-01-01',1,'現金',0,0,0)")          # 筆數須大於 0
        # 沒有主檔的明細:寫得進去,但提交時外鍵擋下,不留殘件
        self.conn.execute("INSERT INTO DayClosePayment VALUES('2026-01-01',1,'現金',1,10,0)")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.commit()
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM DayClosePayment").fetchone()[0], 1)
        self.blocked("DELETE FROM PaymentMethod WHERE payment_id=1")

    def test_failed_close_leaves_no_snapshot(self):
        d = day(-1)
        self.raw_sale(d, 3, 100)
        self.assert_application_error("validation_error", "dayclose.close", {   # 有短溢卻沒填原因
            "business_date": d, "counted_total": 5, "reason": "", "employee": 1, "expected": {"float_amount": 0, "cash_sales": 0, "cash_refund": 0}})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM DayClosePayment").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM DayClose").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
