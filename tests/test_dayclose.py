"""每日立帳:帳日判定、應有現金、短溢與原因、鎖帳。資料庫限制以原生 SQLite 直接寫入驗證。"""
import datetime
import sqlite3
import unittest

from base import FacadeTestCase
from lib.db import get_conn
from lib.desktop_application import DesktopFacade
from lib.desktop_bridge import DesktopBridge


def day(offset=0):
    return (datetime.date.today() + datetime.timedelta(days=offset)).isoformat()


class DayCloseTest(FacadeTestCase):
    def setUp(self):
        super().setUp()
        cid = self.create_category("立帳種類")
        made = self.invoke("products.create", {"name": "立帳商品", "category_id": cid,
                                               "variants": [{"price": 100, "barcodes": [{"barcode": "DC-1"}]}]})
        self.vid = made["variant_ids"][0]
        self.invoke("stock.receive", {"employee": 1, "items": [{"variant_id": self.vid, "qty": 50}]})
        self.conn = get_conn(self.db)
        self.addCleanup(self.conn.close)

    def checkout(self, payment="現金", qty=1):
        return self.invoke("sales.checkout", {"payment": payment, "paid": 100 * qty, "employee": 1,
                                              "items": [{"variant_id": self.vid, "qty": qty, "unit_price": 100}]})["sale_id"]

    def raw_sale(self, business_date, payment="現金", total=100, voided=False):
        """直接寫一張指定帳日的銷售單(沒有明細);回傳單號。"""
        void = ("'2026-01-01 00:00:00','重複結帳'" if voided else "NULL,NULL")
        sale_id = self.conn.execute(
            "INSERT INTO Sale(ts,payment,total,paid,business_date,voided_at,void_reason) "
            f"VALUES(?,?,?,?,?,{void})", (business_date + " 12:00:00", payment, total, total, business_date)).lastrowid
        self.conn.commit()
        return sale_id

    def close(self, business_date, counted, reason="", employee=2, expected=None):
        preview = self.invoke("dayclose.preview", {"business_date": business_date})
        return self.invoke("dayclose.close", {
            "business_date": business_date, "counted_total": counted, "reason": reason, "employee": employee,
            "expected": expected or {"float_amount": preview["float_amount"], "cash_sales": preview["cash_sales"]}})

    def sale_date(self, sale_id):
        return self.conn.execute("SELECT business_date FROM Sale WHERE sale_id=?", (sale_id,)).fetchone()[0]

    def blocked(self, sql, args=()):
        with self.assertRaises(sqlite3.IntegrityError, msg=sql):
            self.conn.execute(sql, args)
        self.conn.rollback()

    # ---- 帳日 ----
    def test_checkout_books_today_until_closed_then_next_day(self):
        self.assertEqual(self.invoke("dayclose.current"), {"business_date": day(), "today": day()})
        first = self.checkout()
        self.assertEqual(self.sale_date(first), day())
        self.close(day(), 100)
        self.assertEqual(self.invoke("dayclose.current"), {"business_date": day(1), "today": day()})
        second = self.checkout()
        self.assertEqual(self.sale_date(second), day(1))
        # 實際結帳時間照實記;清單依帳日篩選
        self.assertEqual(self.conn.execute("SELECT date(ts) FROM Sale WHERE sale_id=?", (second,)).fetchone()[0], day())
        today_rows = self.invoke("sales.list", {"date_from": day(), "date_to": day()})
        self.assertEqual([(s["sale_id"], s["business_date"], s["day_closed"]) for s in today_rows], [(first, day(), True)])
        next_rows = self.invoke("sales.list", {"date_from": day(1), "date_to": day(1)})
        self.assertEqual([(s["sale_id"], s["business_date"], s["day_closed"]) for s in next_rows], [(second, day(1), False)])
        self.assertEqual(self.invoke("sales.summary", {"date_from": day(1), "date_to": day(1)})["total"], 100)
        self.assertEqual(self.invoke("sales.summary", {"date": day()})["count"], 1)
        # 收銀頁最近幾筆仍依實際結帳日期
        self.assertEqual([s["sale_id"] for s in self.invoke("sales.recent")], [second, first])

    # ---- 預覽 ----
    def test_preview_counts_cash_only_and_excludes_void(self):
        self.invoke("dayclose.set_float", {"amount": 3000})
        d = day(-1)
        self.raw_sale(d, "現金", 100)
        self.raw_sale(d, "現金", 250)
        self.raw_sale(d, "轉帳", 400)
        self.raw_sale(d, "現金", 999, voided=True)
        self.raw_sale(day(-2), "現金", 70)
        p = self.invoke("dayclose.preview", {"business_date": d})
        self.assertEqual((p["float_amount"], p["float_setting"], p["cash_sales"], p["closed"], p["record"]),
                         (3000, 3000, 350, False, None))
        self.assertEqual(p["by_payment"], [{"payment": "現金", "count": 2, "total": 350},
                                           {"payment": "轉帳", "count": 1, "total": 400},
                                           {"payment": "LINE Pay", "count": 0, "total": 0},
                                           {"payment": "台灣Pay", "count": 0, "total": 0}])
        self.assertEqual((p["void_count"], p["void_total"]), (1, 999))
        self.assertEqual((p["unclosed"], p["unclosed_count"]), ([day(-2), d], 2))

    def test_unclosed_lists_only_recent_past_days(self):
        for offset in range(1, 10):
            self.raw_sale(day(-offset))
        self.raw_sale(day())   # 今天不算「過去」
        p = self.invoke("dayclose.preview", {"business_date": day()})
        self.assertEqual(p["unclosed"], [day(-o) for o in range(7, 0, -1)])
        self.assertEqual(p["unclosed_count"], 9)

    def test_day_without_sales_can_be_closed(self):
        record = self.close(day(-3), 0)
        self.assertEqual((record["cash_sales"], record["diff"], record["reason"]), (0, 0, None))

    # ---- 立帳 ----
    def test_diff_formula_and_reason_required(self):
        self.invoke("dayclose.set_float", {"amount": 3000})
        d = day(-1)
        self.raw_sale(d, "現金", 500)
        error = self.assertRaisesRegex(Exception, "有短溢時請填寫原因")
        with error:
            self.close(d, 3400)
        with self.assertRaisesRegex(Exception, "有短溢時請填寫原因"):
            self.close(d, 3400, reason="   ")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM DayClose").fetchone()[0], 0)
        record = self.close(d, 3400, reason="  少找一百  ", employee=4)
        self.assertEqual({k: record[k] for k in ("business_date", "employee", "float_amount", "counted_total",
                                                 "cash_sales", "diff", "reason")},
                         {"business_date": d, "employee": 4, "float_amount": 3000, "counted_total": 3400,
                          "cash_sales": 500, "diff": -100, "reason": "少找一百"})
        self.assertEqual(self.close(day(-2), 3050, reason="多收")["diff"], 50)
        self.assert_application_error("validation_error", "dayclose.close", {
            "business_date": day(-4), "counted_total": 1, "reason": "字" * 61, "employee": 1,
            "expected": {"float_amount": 3000, "cash_sales": 0}})

    def test_closing_twice_is_conflict(self):
        self.close(day(-1), 0)
        self.assert_application_error("conflict", "dayclose.close", {
            "business_date": day(-1), "counted_total": 0, "reason": "", "employee": 1,
            "expected": {"float_amount": 0, "cash_sales": 0}})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM DayClose").fetchone()[0], 1)

    def test_future_day_is_rejected(self):
        self.assert_application_error("validation_error", "dayclose.close", {
            "business_date": day(1), "counted_total": 0, "reason": "", "employee": 1,
            "expected": {"float_amount": 0, "cash_sales": 0}})

    def test_expected_amounts_must_match_current_values(self):
        d = day()
        preview = self.invoke("dayclose.preview", {"business_date": d})
        expected = {"float_amount": preview["float_amount"], "cash_sales": preview["cash_sales"]}
        self.checkout()   # 預覽後又結了一筆現金
        error = self.assert_application_error("conflict", "dayclose.close", {
            "business_date": d, "counted_total": 0, "reason": "", "employee": 1, "expected": expected})
        self.assertIn("金額已變動", error.message)
        self.invoke("dayclose.set_float", {"amount": 500})   # 預覽後預備金被改
        self.assert_application_error("conflict", "dayclose.close", {
            "business_date": d, "counted_total": 100, "reason": "", "employee": 1,
            "expected": {"float_amount": 0, "cash_sales": 100}})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM DayClose").fetchone()[0], 0)

    def test_previous_day_can_be_closed_later_without_touching_today(self):
        self.raw_sale(day(-1), "現金", 300)
        today_sale = self.checkout()
        self.close(day(-1), 300)
        self.assertEqual(self.sale_date(self.checkout()), day())   # 今天還沒點,仍記今天
        self.assertEqual(self.invoke("dayclose.preview", {"business_date": day()})["cash_sales"], 200)
        self.assertFalse(self.invoke("sales.list", {"date_from": day(), "date_to": day()})[-1]["day_closed"])
        self.assertEqual(self.sale_date(today_sale), day())

    def test_closed_day_shows_snapshot_not_current_setting(self):
        self.invoke("dayclose.set_float", {"amount": 1000})
        self.raw_sale(day(-1), "現金", 200)
        self.close(day(-1), 1200)
        self.invoke("dayclose.set_float", {"amount": 5000})
        p = self.invoke("dayclose.preview", {"business_date": day(-1)})
        self.assertEqual((p["closed"], p["float_amount"], p["float_setting"], p["cash_sales"]), (True, 1000, 5000, 200))
        self.assertEqual(p["record"]["counted_total"], 1200)
        self.assertEqual(p["unclosed"], [])

    def test_list_filters_by_business_date(self):
        for offset in (1, 2, 3):
            self.close(day(-offset), 0)
        self.assertEqual([r["business_date"] for r in self.invoke("dayclose.list", {})], [day(-1), day(-2), day(-3)])
        rows = self.invoke("dayclose.list", {"date_from": day(-2), "date_to": day(-2)})
        self.assertEqual([r["business_date"] for r in rows], [day(-2)])
        self.assert_application_error("validation_error", "dayclose.list", {"date_from": day(), "date_to": day(-1)})

    def test_list_shows_each_payment_total_per_day(self):
        d, other = day(-1), day(-2)
        self.raw_sale(d, "現金", 100)
        self.raw_sale(d, "現金", 250)
        self.raw_sale(d, "轉帳", 400)
        self.raw_sale(d, "LINE Pay", 30)
        self.raw_sale(d, "舊付款", 7)                 # 設定清單外的付款方式排最後
        self.raw_sale(d, "現金", 999, voided=True)    # 已作廢不計
        self.raw_sale(other, "轉帳", 60)
        self.raw_sale(day(-3), "現金", 5)             # 未立帳的帳日不列
        self.close(d, 350)
        self.close(other, 0)
        rows = self.invoke("dayclose.list", {})
        self.assertEqual([r["business_date"] for r in rows], [d, other])
        # 停用的台灣Pay 仍有一欄(金額 0);手續費率跟著付款方式,沒設定的是 0
        names = ["現金", "轉帳", "LINE Pay", "台灣Pay", "舊付款"]
        rates = (0, 0, 2.2, 0, 0)
        def expect(totals):
            return [{"payment": p, "total": t, "fee_rate": f} for p, t, f in zip(names, totals, rates)]
        self.assertEqual(rows[0]["by_payment"], expect((350, 400, 30, 0, 7)))
        self.assertEqual(rows[1]["by_payment"], expect((0, 60, 0, 0, 0)))
        self.assertEqual([r["sales_total"] for r in rows], [787, 60])
        self.assertEqual(rows[0]["by_payment"][0]["total"], rows[0]["cash_sales"])
        # 日期條件只影響列出的帳日,不影響當天金額
        only = self.invoke("dayclose.list", {"date_from": other, "date_to": other})
        self.assertEqual([(r["business_date"], r["sales_total"]) for r in only], [(other, 60)])
        self.assertEqual([b["payment"] for b in only[0]["by_payment"]], ["現金", "轉帳", "LINE Pay", "台灣Pay"])

    def test_set_float_validation(self):
        for amount in (-1, "100", True, 1.5, 10 ** 9):
            self.assert_application_error("validation_error", "dayclose.set_float", {"amount": amount})
        self.assertEqual(self.invoke("dayclose.set_float", {"amount": 0}), {"amount": 0})

    def test_employee_can_read_current_but_nothing_else(self):
        bridge = DesktopBridge(facade=DesktopFacade(self.db))   # 未登入＝員工
        self.assertTrue(bridge.invoke("dayclose.current", {})["ok"])
        for action, payload in (("dayclose.preview", {"business_date": day()}), ("dayclose.list", {}),
                                ("dayclose.set_float", {"amount": 9}),
                                ("dayclose.close", {"business_date": day(), "counted_total": 0, "reason": "",
                                                    "employee": 1, "expected": {"float_amount": 0, "cash_sales": 0}})):
            self.assertEqual(bridge.invoke(action, payload)["error"]["code"], "forbidden", action)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM DayClose").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT value FROM Setting WHERE key='cash_float'").fetchone()[0], "0")

    # ---- 資料庫層:鎖帳 ----
    def test_closed_day_sales_are_locked_except_employee(self):
        d, other = day(-1), day(-2)
        locked = self.raw_sale(d, "現金", 100)
        free = self.raw_sale(other, "現金", 100)
        self.close(d, 100)
        self.blocked("INSERT INTO Sale(payment,total,paid,business_date) VALUES('現金',1,1,?)", (d,))
        for column, value in (("payment", "轉帳"), ("total", 1), ("paid", 1), ("change", 1), ("order_discount", 1),
                              ("member_discount", 1), ("ts", "2026-01-01 00:00:00"), ("business_date", other),
                              ("member_id", None), ("voided_at", "x")):
            if column == "member_id":
                continue   # 原本就是 NULL,改成同值不算變動
            self.blocked(f"UPDATE Sale SET {column}=? WHERE sale_id=?", (value, locked))
        self.blocked("UPDATE Sale SET voided_at='2026-01-01',void_reason='x' WHERE sale_id=?", (locked,))
        self.blocked("UPDATE Sale SET business_date=? WHERE sale_id=?", (d, free))   # 把別張單移進已立帳帳日
        self.blocked("DELETE FROM Sale WHERE sale_id=?", (locked,))
        # 兩種 REPLACE 撞上已立帳單的主鍵
        self.blocked("INSERT OR REPLACE INTO Sale(sale_id,payment,total,paid,business_date) VALUES(?,'現金',1,1,?)",
                     (locked, other))
        self.blocked("UPDATE OR REPLACE Sale SET sale_id=? WHERE sale_id=?", (locked, free))
        # 員工代號不限期限;同值更新也不擋
        self.conn.execute("UPDATE Sale SET employee=5, payment=payment WHERE sale_id=?", (locked,))
        self.conn.commit()
        row = self.conn.execute("SELECT employee,payment,total,business_date FROM Sale WHERE sale_id=?", (locked,)).fetchone()
        self.assertEqual(tuple(row), (5, "現金", 100, d))
        # 未立帳的單不受影響
        self.conn.execute("UPDATE Sale SET payment='轉帳' WHERE sale_id=?", (free,))
        self.conn.execute("DELETE FROM Sale WHERE sale_id=?", (free,))
        self.conn.commit()

    def test_dayclose_rows_cannot_be_changed_replaced_or_deleted(self):
        self.close(day(-1), 0)
        self.blocked("UPDATE DayClose SET counted_total=5, diff=5, reason='x'")
        self.blocked("DELETE FROM DayClose")
        self.blocked("INSERT OR REPLACE INTO DayClose(business_date,employee,float_amount,counted_total,cash_sales,diff) "
                     "VALUES(?,1,0,0,0,0)", (day(-1),))
        insert = ("INSERT INTO DayClose(business_date,employee,float_amount,counted_total,cash_sales,diff,reason) "
                  "VALUES(?,?,?,?,?,?,?)")
        self.blocked(insert, (None, 1, 0, 0, 0, 0, None))             # 主鍵不可為 NULL
        self.blocked(insert, ("2026/01/01", 1, 0, 0, 0, 0, None))     # 日期格式
        self.blocked(insert, ("2026-01-01", 7, 0, 0, 0, 0, None))     # 員工代號
        self.blocked(insert, ("2026-01-01", 1, 0, 100, 0, 50, "x"))   # 短溢必須等於算式
        self.blocked(insert, ("2026-01-01", 1, 0, 100, 0, 100, " "))  # 有短溢必有原因
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM DayClose").fetchone()[0], 1)

    # ---- 資料庫層:作廢與更正的結構(功能在後續階段,限制先到位) ----
    def test_voided_sale_is_final(self):
        sale = self.raw_sale(day(-1))
        self.blocked("UPDATE Sale SET voided_at='2026-01-01' WHERE sale_id=?", (sale,))   # 作廢時間與原因須同時有值
        self.conn.execute("UPDATE Sale SET voided_at='2026-01-01',void_reason='重複結帳' WHERE sale_id=?", (sale,))
        self.conn.commit()
        self.blocked("UPDATE Sale SET voided_at=NULL,void_reason=NULL WHERE sale_id=?", (sale,))
        self.blocked("UPDATE Sale SET void_reason='改' WHERE sale_id=?", (sale,))
        self.blocked("UPDATE Sale SET voided_at='2027-01-01' WHERE sale_id=?", (sale,))
        self.blocked("UPDATE Sale SET payment='轉帳' WHERE sale_id=?", (sale,))
        self.blocked("DELETE FROM Sale WHERE sale_id=?", (sale,))
        self.blocked("INSERT OR REPLACE INTO Sale(sale_id,payment,total,paid,business_date) VALUES(?,'現金',1,1,?)",
                     (sale, day(-1)))
        other = self.raw_sale(day(-1))
        self.blocked("UPDATE OR REPLACE Sale SET sale_id=? WHERE sale_id=?", (sale, other))
        self.conn.execute("UPDATE Sale SET employee=6 WHERE sale_id=?", (sale,))   # 員工代號仍可更正
        self.conn.commit()

    def test_void_movement_allowed_once_per_sale_item(self):
        sale = self.checkout(qty=2)
        item = self.conn.execute("SELECT item_id FROM SaleItem WHERE sale_id=?", (sale,)).fetchone()[0]
        insert = "INSERT INTO StockMovement(variant_id,qty,kind,sale_item_id) VALUES(?,?,?,?)"
        self.blocked(insert, (self.vid, -2, "void", item))    # 作廢沖回必須是正數
        self.blocked(insert, (self.vid, -2, "sale", item))    # 同一明細的銷售異動仍只能一筆
        self.conn.execute(insert, (self.vid, 2, "void", item))
        self.conn.commit()
        self.blocked(insert, (self.vid, 2, "void", item))     # 只能沖回一次
        self.blocked("INSERT OR REPLACE INTO StockMovement(variant_id,qty,kind,sale_item_id) VALUES(?,?,?,?)",
                     (self.vid, 2, "void", item))
        self.assertEqual(self.invoke("stock.detail", {"variant_id": self.vid})["stock"], 50)

    def test_correction_rows_need_one_source_and_are_immutable(self):
        sale = self.raw_sale(day(-1))
        insert = "INSERT INTO Correction(sale_id,receipt_id,field,old_value,new_value) VALUES(?,?,?,?,?)"
        self.blocked(insert, (None, None, "employee", "1", "2"))      # 沒有來源
        self.blocked(insert, (sale, 1, "employee", "1", "2"))         # 兩個來源
        self.blocked(insert, (sale, None, "employee", "1", "1"))      # 改前改後相同
        self.blocked(insert, (sale, None, "total", "1", "2"))         # 不開放的欄位
        cid = self.conn.execute(insert, (sale, None, "payment", "現金", "轉帳")).lastrowid
        self.conn.execute(insert, (sale, None, "employee", None, "2"))   # 舊單員工可能是 NULL
        self.conn.commit()
        self.blocked("UPDATE Correction SET new_value='x' WHERE correction_id=?", (cid,))
        self.blocked("DELETE FROM Correction WHERE correction_id=?", (cid,))
        self.blocked("INSERT OR REPLACE INTO Correction(correction_id,sale_id,field,old_value,new_value) "
                     "VALUES(?,?,'payment','a','b')", (cid, sale))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM Correction").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
