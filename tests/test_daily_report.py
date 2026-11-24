"""日報表獨立工具:POS 唯讀取數、手填資料存取、計算、PDF 產出。"""
import os
import shutil
import sqlite3
import tempfile
import unittest

from tests.base import fresh_db

from daily_report import calc, pdf_export
from daily_report.pos_reader import PosReadError, read_month
from daily_report.store import Store, StoreError


def add_sale(conn, date, total, employee, voided=False):
    conn.execute(
        "INSERT INTO Sale(payment, payment_id, total, paid, employee, business_date, voided_at, void_reason) "
        "VALUES('現金', 1, ?, ?, ?, ?, ?, ?)",
        (total, total, employee, date, "2026-11-01 10:00:00" if voided else None, "測試" if voided else None))


def close_day(conn, date, cash_sales, refund=0, count=1):
    if cash_sales:
        conn.execute("INSERT INTO DayClosePayment(business_date, payment_id, name, count, total, fee_rate) "
                     "VALUES(?, 1, '現金', ?, ?, 0)", (date, count, cash_sales))
    conn.execute("INSERT INTO DayClose(business_date, employee, float_amount, counted_total, cash_sales, "
                 "cash_refund, diff) VALUES(?, 1, 0, ?, ?, ?, 0)",
                 (date, cash_sales - refund, cash_sales, refund))


def lines(*amounts):
    return [{"text": f"項目{i}", "amount": a} for i, a in enumerate(amounts)]


class PosReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = fresh_db(self.tmp)
        conn = sqlite3.connect(self.db)
        with conn:
            add_sale(conn, "2026-11-01", 500, 1)
            add_sale(conn, "2026-11-01", 300, 2)
            add_sale(conn, "2026-11-01", 200, None)
            add_sale(conn, "2026-11-01", 999, 1, voided=True)
            close_day(conn, "2026-11-01", 1000, refund=100, count=3)
            close_day(conn, "2026-11-02", 0)          # 有立帳、沒有銷售
            add_sale(conn, "2026-11-03", 700, 3)      # 尚未立帳
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_closed_days_only_with_refund_deducted(self):
        days = read_month(self.db, 2026, 11)
        self.assertEqual(set(days), {"2026-11-01", "2026-11-02"})
        day = days["2026-11-01"]
        self.assertEqual(day["revenue"], 900)
        self.assertEqual(day["employees"], [500, 300, 0, 0, 0, 0])
        self.assertEqual(days["2026-11-02"]["revenue"], 0)

    def test_read_only_and_rejects_other_files(self):
        before = os.path.getmtime(self.db)
        read_month(self.db, 2026, 11)
        self.assertEqual(before, os.path.getmtime(self.db))
        other = os.path.join(self.tmp, "other.db")
        sqlite3.connect(other).close()
        with self.assertRaises(PosReadError):
            read_month(other, 2026, 11)
        with self.assertRaises(PosReadError):
            read_month(os.path.join(self.tmp, "missing.db"), 2026, 11)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "daily_report.db"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_round_trip_skips_blank_lines(self):
        self.store.save_day("2026-11-05", {
            "weather": "雨", "payable": 3000,
            "goods": [{"text": "甲廠", "amount": 1200}, {"text": "", "amount": None}],
            "expense": [{"text": "收回", "amount": -50}], "owed": [], "note": [{"text": "垃圾袋", "amount": 30}]})
        entry = self.store.month_entries(2026, 11)["2026-11-05"]
        self.assertEqual(entry["weather"], "雨")
        self.assertEqual(entry["payable"], 3000)
        self.assertEqual(entry["goods"], [{"text": "甲廠", "amount": 1200}])
        self.assertEqual(entry["expense"][0]["amount"], -50)

    def test_resave_replaces_and_invalid_keeps_old(self):
        self.store.save_day("2026-11-05", {"goods": lines(100, 200)})
        with self.assertRaises(StoreError):
            self.store.save_day("2026-11-05", {"goods": lines(-1)})
        with self.assertRaises(StoreError):
            self.store.save_day("2026-11-05", {"owed": lines(*range(1, 8))})
        self.store.save_day("2026-11-06", {"owed": [{"text": "大成配件 12000", "amount": 5}]})
        self.assertEqual(self.store.month_entries(2026, 11)["2026-11-06"]["owed"],
                         [{"text": "大成配件 12000", "amount": None}])
        self.assertEqual(len(self.store.month_entries(2026, 11)["2026-11-05"]["goods"]), 2)
        self.store.save_day("2026-11-05", {"goods": lines(5)})
        self.assertEqual(self.store.month_entries(2026, 11)["2026-11-05"]["goods"][0]["amount"], 5)

    def test_setting(self):
        self.assertIsNone(self.store.get_setting("pos_db_path"))
        self.store.set_setting("pos_db_path", "D:/x.db")
        self.assertEqual(self.store.get_setting("pos_db_path"), "D:/x.db")


def entry(**kw):
    base = {"weather": "", "payable": None, "goods": [], "owed": [], "note": [], "expense": []}
    base.update(kw)
    return base


class CalcTest(unittest.TestCase):
    def rows(self):
        pos = {"2026-11-01": {"revenue": 1000, "employees": [600, 400, 0, 0, 0, 0]},
               "2026-11-03": {"revenue": 500, "employees": [0, 500, 0, 0, 0, 0]}}
        entries = {"2026-11-01": entry(expense=lines(100), goods=lines(2000), note=lines(30)),
                   "2026-11-02": entry(expense=lines(200), goods=lines(500)),
                   "2026-11-03": entry(expense=lines(50, -20))}
        return calc.build_month(2026, 11, pos, entries)

    def test_daily_and_cumulative(self):
        rows = self.rows()
        self.assertEqual(len(rows), 30)
        d1, d2, d3 = rows[:3]
        self.assertEqual((d1["balance"], d1["remain"], d1["cum_cash"]), (900, 900, 900))
        self.assertEqual(d1["note_total"], 30)
        # 未立帳:營業額空白、餘額空白,累計現流照扣開支
        self.assertIsNone(d2["revenue"])
        self.assertIsNone(d2["balance"])
        self.assertEqual((d2["cum_revenue"], d2["cum_expense"], d2["cum_cash"]), (1000, 300, 700))
        self.assertEqual((d3["expense"], d3["balance"], d3["cum_cash"]), (30, 470, 1170))
        self.assertEqual(d3["cum_employees"][:2], [600, 900])
        self.assertEqual(d1["emp_active"], [True, True, False, False, False, False])

    def test_employee_blank_until_first_sale(self):
        pos = {"2026-11-01": {"revenue": 100, "employees": [100, 0, 0, 0, 0, 0]},
               "2026-11-02": {"revenue": 300, "employees": [100, 0, 200, 0, 0, 0]}}
        d1, d2 = calc.build_month(2026, 11, pos, {})[:2]
        self.assertFalse(d1["emp_active"][2])
        self.assertTrue(d2["emp_active"][2])

    def test_goods_cumulative_before_and_including_today(self):
        d1, d2, d3 = self.rows()[:3]
        self.assertEqual((d1["cum_goods"], d1["goods_total"]), (0, 2000))
        self.assertEqual((d2["cum_goods"], d2["goods_total"]), (2000, 2500))
        self.assertEqual((d3["cum_goods"], d3["goods_total"]), (2500, 2500))
        self.assertEqual(d1["weekday"], "星期日")


class PdfTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pdfs_written(self):
        full = entry(weather="晴", payable=99999,
                     goods=lines(*[123456] * 12), owed=lines(*[50000] * 6),
                     note=lines(*[30] * 12), expense=lines(*[-1500] * 12))
        pos = {"2026-11-01": {"revenue": 1234567, "employees": [1, 2, 3, 4, 5, 6]}}
        rows = calc.build_month(2026, 11, pos, {"2026-11-01": full})
        day, month = os.path.join(self.tmp, "d.pdf"), os.path.join(self.tmp, "m.pdf")
        pdf_export.day_pages_pdf(day, [pdf_export.pair_of(rows, 1), pdf_export.pair_of(rows, 30)])
        pdf_export.month_pdf(month, 2026, 11, rows)
        for path in (day, month):
            with open(path, "rb") as f:
                self.assertTrue(f.read(5) == b"%PDF-")


class PairTest(unittest.TestCase):
    def test_fixed_pairs(self):
        rows = calc.build_month(2026, 12, {}, {})
        self.assertEqual([r["date"] for r in pdf_export.pair_of(rows, 20)], ["2026-12-19", "2026-12-20"])
        self.assertEqual([r["date"] for r in pdf_export.pair_of(rows, 19)], ["2026-12-19", "2026-12-20"])
        top, bottom = pdf_export.pair_of(rows, 31)
        self.assertEqual((top["date"], bottom), ("2026-12-31", None))


if __name__ == "__main__":
    unittest.main()
