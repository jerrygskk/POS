"""庫存來源:異動恰一來源、已入帳明細鎖定、合法更新照常、建單交易失敗全部回滾。

資料庫限制一律以原生 SQLite 直接寫入驗證,證明是資料庫本身在擋。
"""
import sqlite3
import unittest
from unittest.mock import patch

from base import FacadeTestCase
from lib import stock_ledger
from lib.application_errors import ConflictError
from lib.db import get_conn


class StockSourcesTest(FacadeTestCase):
    def setUp(self):
        super().setUp()
        cid = self.create_category("來源測試")
        made = self.invoke("products.create", {"name": "來源商品", "category_id": cid, "variants": [
            {"price": 100, "barcodes": [{"barcode": "SRC-1"}]},
            {"price": 200, "barcodes": [{"barcode": "SRC-2"}]}]})
        self.v1, self.v2 = made["variant_ids"]

    # ---- 共用 ----
    def stock(self, vid):
        return self.invoke("stock.detail", {"variant_id": vid})["stock"]

    def raw(self):
        conn = get_conn(self.db)
        self.addCleanup(conn.close)
        return conn

    def one(self, sql, args=()):
        conn = get_conn(self.db)
        try:
            return conn.execute(sql, args).fetchone()
        finally:
            conn.close()

    def counts(self, *tables):
        return [self.one(f"SELECT COUNT(*) FROM {t}")[0] for t in tables]

    def build_flow(self):
        """一般進貨(同款式兩列)→結帳→退貨入庫→盤點核准→返修兩批返新。回傳各來源 id。"""
        r = self.invoke("stock.receive", {"employee": 1, "items": [
            {"variant_id": self.v1, "qty": 5}, {"variant_id": self.v1, "qty": 2}, {"variant_id": self.v2, "qty": 5}]})
        self.assertEqual((self.stock(self.v1), self.stock(self.v2)), (7, 5))
        sale = self.invoke("sales.checkout", {"payment": "現金", "paid": 100, "employee": 2,
                                              "items": [{"variant_id": self.v1, "qty": 1, "unit_price": 100}]})
        self.assertEqual(self.stock(self.v1), 6)
        ret = self.invoke("returns.create", {"employee": 3})["return_id"]
        self.invoke("returns.scan", {"return_id": ret, "variant_id": self.v1, "qty": 1})
        self.invoke("returns.close", {"return_id": ret})
        self.invoke("returns.approve", self.approve_payload(ret))
        self.assertEqual(self.stock(self.v1), 7)
        sid = self.invoke("stocktake.create", {"employee": 4})["session_id"]
        self.invoke("stocktake.scan", {"session_id": sid, "variant_id": self.v2, "qty": 3})
        self.invoke("stocktake.close", {"session_id": sid})
        self.invoke("stocktake.approve", {"session_id": sid})
        self.assertEqual(self.stock(self.v2), 3)
        rep = self.invoke("returns.create", {"employee": 5})["return_id"]
        self.invoke("returns.scan", {"return_id": rep, "variant_id": self.v2, "qty": 2})
        rep_item = self.return_item_id(rep, self.v2)
        self.invoke("returns.close", {"return_id": rep})
        self.invoke("returns.set_kind", {"return_id": rep, "kind": "repair"})
        self.invoke("returns.approve", self.approve_payload(rep))
        for n in (1, 1):  # 分批返新合法
            self.invoke("returns.repair_receive", {"return_id": rep, "employee": 6, "items": [{"item_id": rep_item, "qty": n}]})
        self.assertEqual(self.stock(self.v2), 5)
        return {
            "receipt": r["receipt_id"],
            "receipt_item": self.one("SELECT MIN(receipt_item_id) FROM ReceiptItem")[0],
            "sale": sale["sale_id"],
            "sale_item": self.one("SELECT item_id FROM SaleItem WHERE sale_id=?", (sale["sale_id"],))[0],
            "return": ret, "return_item": self.return_item_id(ret, self.v1),
            "session": sid, "stocktake_item": self.one("SELECT id FROM StocktakeItem WHERE session_id=?", (sid,))[0],
            "repair": rep, "repair_item": rep_item,
        }

    # ---- 完整流程 ----
    def test_full_flow_writes_one_named_source_per_movement(self):
        ids = self.build_flow()
        rows = [tuple(r) for r in self.raw().execute(
            "SELECT kind, qty, receipt_item_id IS NOT NULL, sale_item_id IS NOT NULL, stocktake_item_id IS NOT NULL, "
            "return_item_id IS NOT NULL, employee FROM StockMovement ORDER BY move_id")]
        self.assertEqual(rows, [
            ("purchase", 5, 1, 0, 0, 0, None), ("purchase", 2, 1, 0, 0, 0, None), ("purchase", 5, 1, 0, 0, 0, None),
            ("sale", -1, 0, 1, 0, 0, None), ("return", 1, 0, 0, 0, 1, None), ("adjust", -2, 0, 0, 1, 0, None),
            ("repair", 1, 0, 0, 0, 1, 6), ("repair", 1, 0, 0, 0, 1, 6)])
        detail = self.invoke("returns.detail", {"return_id": ids["repair"]})
        self.assertEqual((detail["status"], detail["items"][0]["repaired_qty"]), ("closed", 2))
        moves = self.invoke("stock.detail", {"variant_id": self.v1})["movements"]
        by_kind = {m["kind"]: m for m in moves}
        self.assertEqual(by_kind["sale"]["sale_id"], ids["sale"])
        self.assertEqual(by_kind["return"]["return_id"], ids["return"])
        self.assertEqual(by_kind["purchase"]["receipt_id"], ids["receipt"])
        self.assertEqual((by_kind["purchase"]["employee"], by_kind["sale"]["employee"], by_kind["return"]["employee"]), (1, 2, 3))
        self.assertNotIn("ref_id", by_kind["sale"])
        receipts = self.invoke("stock.receipts", {})
        self.assertEqual([(r["receipt_id"], [i["qty"] for i in r["items"]]) for r in receipts], [(ids["receipt"], [5, 2, 5])])

    # ---- 來源限制(原生 SQLite) ----
    def test_database_rejects_bad_sources(self):
        ids = self.build_flow()
        conn = self.raw()
        conn.execute("INSERT INTO SaleItem(sale_id,variant_id,qty,unit_price) VALUES(?,?,1,100)", (ids["sale"], self.v1))
        fresh_sale_item = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        bad = {
            "來源不存在": ("INSERT INTO StockMovement(variant_id,qty,kind,receipt_item_id) VALUES(?,1,'purchase',99999)", (self.v1,)),
            "款式不同": ("INSERT INTO StockMovement(variant_id,qty,kind,sale_item_id) VALUES(?,-1,'sale',?)", (self.v2, fresh_sale_item)),
            "兩個來源": ("INSERT INTO StockMovement(variant_id,qty,kind,sale_item_id,return_item_id) VALUES(?,-1,'sale',?,?)",
                         (self.v1, fresh_sale_item, ids["return_item"])),
            "沒有來源": ("INSERT INTO StockMovement(variant_id,qty,kind) VALUES(?,1,'purchase')", (self.v1,)),
            "類型與來源不符": ("INSERT INTO StockMovement(variant_id,qty,kind,sale_item_id) VALUES(?,1,'purchase',?)", (self.v1, fresh_sale_item)),
            "銷售方向錯": ("INSERT INTO StockMovement(variant_id,qty,kind,sale_item_id) VALUES(?,1,'sale',?)", (self.v1, fresh_sale_item)),
            "盤點零差異": ("INSERT INTO StockMovement(variant_id,qty,kind,stocktake_item_id) VALUES(?,0,'adjust',?)", (self.v2, ids["stocktake_item"])),
            "非返新帶員工": ("INSERT INTO StockMovement(variant_id,qty,kind,sale_item_id,employee) VALUES(?,-1,'sale',?,1)", (self.v1, fresh_sale_item)),
            "返新缺員工": ("INSERT INTO StockMovement(variant_id,qty,kind,return_item_id) VALUES(?,1,'repair',?)", (self.v2, ids["repair_item"])),
            "進貨重複入帳": ("INSERT INTO StockMovement(variant_id,qty,kind,receipt_item_id) VALUES(?,5,'purchase',?)", (self.v1, ids["receipt_item"])),
            "銷售重複入帳": ("INSERT INTO StockMovement(variant_id,qty,kind,sale_item_id) VALUES(?,-1,'sale',?)", (self.v1, ids["sale_item"])),
            "盤點重複入帳": ("INSERT INTO StockMovement(variant_id,qty,kind,stocktake_item_id) VALUES(?,-2,'adjust',?)", (self.v2, ids["stocktake_item"])),
            "退貨入庫重複": ("INSERT INTO StockMovement(variant_id,qty,kind,return_item_id) VALUES(?,1,'return',?)", (self.v1, ids["return_item"])),
        }
        for name, (sql, args) in bad.items():
            with self.subTest(name), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(sql, args)
        # 同一返修品項再一筆返新:資料庫允許(上限由入帳模組在交易內檢查)
        conn.execute("INSERT INTO StockMovement(variant_id,qty,kind,return_item_id,employee) VALUES(?,1,'repair',?,1)",
                     (self.v2, ids["repair_item"]))
        conn.rollback()

    # ---- 已入帳鎖定(原生 SQLite) ----
    def test_posted_rows_reject_locked_updates_and_deletes(self):
        ids = self.build_flow()
        conn = self.raw()
        other_receipt = conn.execute("INSERT INTO Receipt(employee) VALUES(1)").lastrowid
        locked = [
            ("ReceiptItem", "receipt_item_id", ids["receipt_item"], {"receipt_id": other_receipt, "variant_id": self.v2, "qty": 9}),
            ("SaleItem", "item_id", ids["sale_item"], {"sale_id": ids["sale"], "variant_id": self.v2, "qty": 9, "unit_price": 1, "discount": 1}),
            ("Sale", "sale_id", ids["sale"], {"order_discount": 1, "member_discount": 1, "total": 1, "paid": 1, "change": 9}),
            ("StocktakeItem", "id", ids["stocktake_item"], {"session_id": 999, "variant_id": self.v1, "system_qty": 9, "counted_qty": 9}),
            ("ReturnItem", "id", ids["return_item"], {"return_id": ids["repair"], "variant_id": self.v2, "qty": 9, "disposition": "discard"}),
        ]
        for table, key, row_id, cols in locked:
            for col, value in cols.items():
                if table == "SaleItem" and col == "sale_id":
                    value = conn.execute("INSERT INTO Sale(payment,total,paid) VALUES('現金',0,0)").lastrowid
                with self.subTest(f"{table}.{col}"), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(f"UPDATE {table} SET {col}=? WHERE {key}=?", (value, row_id))
            if table != "Sale":
                with self.subTest(f"{table} 刪除"), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(f"DELETE FROM {table} WHERE {key}=?", (row_id,))
        move = conn.execute("SELECT move_id FROM StockMovement LIMIT 1").fetchone()[0]
        for col, value in {"variant_id": self.v2, "qty": 99, "kind": "adjust", "note": "x", "employee": 1,
                           "ts": "2000-01-01", "receipt_item_id": None}.items():
            with self.subTest(f"StockMovement.{col}"), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(f"UPDATE StockMovement SET {col}=? WHERE move_id=?", (value, move))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM StockMovement WHERE move_id=?", (move,))
        conn.rollback()

    def test_posted_rows_reject_insert_or_replace(self):
        # INSERT OR REPLACE 撞鍵時隱含刪除舊列,刪除觸發器不會觸發;須由新增前檢查擋下
        ids = self.build_flow()
        conn = self.raw()
        conn.execute("PRAGMA foreign_keys=ON")
        move = conn.execute("SELECT move_id,variant_id,receipt_item_id FROM StockMovement WHERE receipt_item_id=?", (ids["receipt_item"],)).fetchone()
        cases = {
            "進貨明細主鍵": ("INSERT OR REPLACE INTO ReceiptItem(receipt_item_id,receipt_id,variant_id,qty) "
                             "SELECT receipt_item_id,receipt_id,variant_id,8 FROM ReceiptItem WHERE receipt_item_id=?", (ids["receipt_item"],)),
            "銷售明細主鍵": ("INSERT OR REPLACE INTO SaleItem(item_id,sale_id,variant_id,qty,unit_price) "
                             "SELECT item_id,sale_id,variant_id,9,1 FROM SaleItem WHERE item_id=?", (ids["sale_item"],)),
            "銷售單主鍵": ("INSERT OR REPLACE INTO Sale(sale_id,payment,total,paid) VALUES(?,'現金',1,1)", (ids["sale"],)),
            "盤點明細主鍵": ("INSERT OR REPLACE INTO StocktakeItem(id,session_id,variant_id,system_qty,counted_qty) "
                             "SELECT id,session_id,variant_id,9,9 FROM StocktakeItem WHERE id=?", (ids["stocktake_item"],)),
            "盤點明細同單同款": ("INSERT OR REPLACE INTO StocktakeItem(session_id,variant_id,system_qty,counted_qty) "
                                 "SELECT session_id,variant_id,9,9 FROM StocktakeItem WHERE id=?", (ids["stocktake_item"],)),
            "退貨品項主鍵": ("INSERT OR REPLACE INTO ReturnItem(id,return_id,variant_id,qty,disposition) "
                             "SELECT id,return_id,variant_id,9,'restock' FROM ReturnItem WHERE id=?", (ids["return_item"],)),
            "退貨品項同單同款": ("INSERT OR REPLACE INTO ReturnItem(return_id,variant_id,qty,disposition) "
                                 "SELECT return_id,variant_id,9,'restock' FROM ReturnItem WHERE id=?", (ids["return_item"],)),
            "異動主鍵": ("INSERT OR REPLACE INTO StockMovement(move_id,variant_id,qty,kind,receipt_item_id) VALUES(?,?,9,'purchase',?)",
                         (move["move_id"], move["variant_id"], move["receipt_item_id"])),
            "異動同來源": ("INSERT OR REPLACE INTO StockMovement(variant_id,qty,kind,receipt_item_id) VALUES(?,9,'purchase',?)",
                           (move["variant_id"], move["receipt_item_id"])),
            "退貨入庫同來源": ("INSERT OR REPLACE INTO StockMovement(variant_id,qty,kind,return_item_id) VALUES(?,9,'return',?)",
                               (self.v1, ids["return_item"])),
        }
        for name, (sql, args) in cases.items():
            with self.subTest(name), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(sql, args)
        self.assertEqual(conn.execute("SELECT qty FROM ReceiptItem WHERE receipt_item_id=?", (ids["receipt_item"],)).fetchone()[0], 5)
        conn.rollback()

    def test_posted_rows_reject_update_or_replace(self):
        # 把未入帳的列改成撞上已入帳列的鍵:UPDATE OR REPLACE 會隱含刪除已入帳列,須擋下
        ids = self.build_flow()
        conn = self.raw()
        conn.execute("PRAGMA foreign_keys=ON")
        receipt = conn.execute("INSERT INTO Receipt(employee) VALUES(1)").lastrowid
        free_receipt_item = conn.execute("INSERT INTO ReceiptItem(receipt_id,variant_id,qty) VALUES(?,?,1)", (receipt, self.v1)).lastrowid
        free_sale = conn.execute("INSERT INTO Sale(payment,total,paid) VALUES('現金',0,0)").lastrowid
        free_sale_item = conn.execute("INSERT INTO SaleItem(sale_id,variant_id,qty,unit_price) VALUES(?,?,1,1)", (free_sale, self.v1)).lastrowid
        sid = conn.execute("SELECT session_id FROM StocktakeItem WHERE id=?", (ids["stocktake_item"],)).fetchone()[0]
        free_take = conn.execute("INSERT INTO StocktakeItem(session_id,variant_id,system_qty,counted_qty) VALUES(?,?,0,0)", (sid, self.v1)).lastrowid
        rid, rvid = conn.execute("SELECT return_id,variant_id FROM ReturnItem WHERE id=?", (ids["return_item"],)).fetchone()
        other_vid = self.v2 if rvid == self.v1 else self.v1
        free_ret = conn.execute("INSERT INTO ReturnItem(return_id,variant_id,qty) VALUES(?,?,1)", (rid, other_vid)).lastrowid
        cases = {
            "進貨明細主鍵": ("UPDATE OR REPLACE ReceiptItem SET receipt_item_id=? WHERE receipt_item_id=?", (ids["receipt_item"], free_receipt_item)),
            "銷售明細主鍵": ("UPDATE OR REPLACE SaleItem SET item_id=? WHERE item_id=?", (ids["sale_item"], free_sale_item)),
            "銷售單主鍵": ("UPDATE OR REPLACE Sale SET sale_id=? WHERE sale_id=?", (ids["sale"], free_sale)),
            "盤點明細主鍵": ("UPDATE OR REPLACE StocktakeItem SET id=? WHERE id=?", (ids["stocktake_item"], free_take)),
            "盤點明細同單同款": ("UPDATE OR REPLACE StocktakeItem SET variant_id=? WHERE id=?", (self.v2, free_take)),
            "退貨品項主鍵": ("UPDATE OR REPLACE ReturnItem SET id=? WHERE id=?", (ids["return_item"], free_ret)),
            "退貨品項同單同款": ("UPDATE OR REPLACE ReturnItem SET variant_id=? WHERE id=?", (rvid, free_ret)),
        }
        for name, (sql, args) in cases.items():
            with self.subTest(name), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(sql, args)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ReceiptItem WHERE receipt_item_id=?", (ids["receipt_item"],)).fetchone()[0], 1)
        conn.rollback()

    def test_allowed_updates_still_work(self):
        ids = self.build_flow()
        conn = self.raw()
        # 已入帳品項的非鎖定欄位、父單狀態照常可改
        conn.execute("UPDATE ReturnItem SET reason='改原因' WHERE id=?", (ids["return_item"],))
        conn.execute("UPDATE StocktakeItem SET note='改備註', needs_recount=1 WHERE id=?", (ids["stocktake_item"],))
        conn.execute("UPDATE Sale SET ts=datetime('now','localtime','-1 day'), payment='轉帳' WHERE sale_id=?", (ids["sale"],))
        conn.execute("UPDATE ReturnSession SET status='closed' WHERE return_id=?", (ids["return"],))
        # 未入帳明細照常可改可刪
        open_ret = conn.execute("INSERT INTO ReturnSession(employee) VALUES(1)").lastrowid
        item = conn.execute("INSERT INTO ReturnItem(return_id,variant_id,qty) VALUES(?,?,1)", (open_ret, self.v1)).lastrowid
        conn.execute("UPDATE ReturnItem SET qty=3, disposition='discard' WHERE id=?", (item,))
        conn.execute("DELETE FROM ReturnItem WHERE id=?", (item,))
        conn.commit()
        # 返修結案與撤回結案(閉合欄位不在鎖定範圍):已部分返新的品項
        rep = self.invoke("returns.create", {"employee": 1})["return_id"]
        self.invoke("returns.scan", {"return_id": rep, "variant_id": self.v1, "qty": 3})
        rep_item = self.return_item_id(rep, self.v1)
        self.invoke("returns.close", {"return_id": rep})
        self.invoke("returns.set_kind", {"return_id": rep, "kind": "repair"})
        self.invoke("returns.approve", self.approve_payload(rep))
        self.invoke("returns.repair_receive", {"return_id": rep, "employee": 1, "items": [{"item_id": rep_item, "qty": 1}]})
        self.invoke("returns.close_item", {"return_id": rep, "item_id": rep_item, "reason": "廠商不換"})
        self.assertEqual(self.invoke("returns.detail", {"return_id": rep})["status"], "closed")
        self.invoke("returns.reopen_item", {"return_id": rep, "item_id": rep_item})
        detail = self.invoke("returns.detail", {"return_id": rep})
        self.assertEqual((detail["status"], detail["items"][0]["closed_qty"], detail["items"][0]["repaired_qty"]), ("repairing", 0, 1))
        # 已返新 1 件後,再返新不能超過剩下的 2 件
        self.assert_application_error("conflict", "returns.repair_receive",
                                      {"return_id": rep, "employee": 1, "items": [{"item_id": rep_item, "qty": 3}]})

    def test_ledger_checks_source_state(self):
        sid = self.invoke("stocktake.create", {"employee": 1})["session_id"]
        self.invoke("stocktake.scan", {"session_id": sid, "variant_id": self.v1, "qty": 2})
        item = self.one("SELECT id FROM StocktakeItem WHERE session_id=?", (sid,))[0]
        ret = self.invoke("returns.create", {"employee": 1})["return_id"]
        self.invoke("returns.scan", {"return_id": ret, "variant_id": self.v1, "qty": 1})
        conn = self.raw()
        with self.assertRaises(ConflictError):
            stock_ledger.post(conn, "adjust", item)            # 盤點單未核准
        with self.assertRaises(ConflictError):
            stock_ledger.post(conn, "return", self.return_item_id(ret, self.v1))   # 退貨單未核准
        with self.assertRaises(ConflictError):
            stock_ledger.post(conn, "repair", self.return_item_id(ret, self.v1), 1, 1)  # 不是返修中
        conn.rollback()

    # ---- 建單交易中途失敗,全部回滾 ----
    def fail_on_second_post(self):
        real = stock_ledger.post
        calls = []

        def wrapped(*args, **kwargs):
            calls.append(args[1])
            if len(calls) == 2:
                raise RuntimeError("中途失敗")
            return real(*args, **kwargs)
        return patch("lib.stock_ledger.post", side_effect=wrapped)

    def test_failures_mid_transaction_leave_nothing(self):
        with self.fail_on_second_post(), self.assertRaises(RuntimeError):
            self.invoke("stock.receive", {"employee": 1, "items": [{"variant_id": self.v1, "qty": 5}, {"variant_id": self.v2, "qty": 5}]})
        self.assertEqual(self.counts("Receipt", "ReceiptItem", "StockMovement"), [0, 0, 0])

        self.invoke("stock.receive", {"employee": 1, "items": [{"variant_id": self.v1, "qty": 5}, {"variant_id": self.v2, "qty": 5}]})
        base_moves = self.counts("StockMovement")[0]
        with self.fail_on_second_post(), self.assertRaises(RuntimeError):
            self.invoke("sales.checkout", {"payment": "現金", "paid": 300, "employee": 1, "items": [
                {"variant_id": self.v1, "qty": 1, "unit_price": 100}, {"variant_id": self.v2, "qty": 1, "unit_price": 200}]})
        self.assertEqual(self.counts("Sale", "SaleItem", "StockMovement"), [0, 0, base_moves])

        sid = self.invoke("stocktake.create", {"employee": 1})["session_id"]
        for vid in (self.v1, self.v2):
            self.invoke("stocktake.scan", {"session_id": sid, "variant_id": vid, "qty": 1})
        self.invoke("stocktake.close", {"session_id": sid})
        with self.fail_on_second_post(), self.assertRaises(RuntimeError):
            self.invoke("stocktake.approve", {"session_id": sid})
        self.assertEqual(self.one("SELECT status FROM StocktakeSession WHERE session_id=?", (sid,))[0], "pending")
        self.assertEqual(self.counts("StockMovement"), [base_moves])

        ret = self.invoke("returns.create", {"employee": 1})["return_id"]
        for vid in (self.v1, self.v2):
            self.invoke("returns.scan", {"return_id": ret, "variant_id": vid, "qty": 1})
        self.invoke("returns.close", {"return_id": ret})
        with self.fail_on_second_post(), self.assertRaises(RuntimeError):
            self.invoke("returns.approve", self.approve_payload(ret))
        self.assertEqual(self.one("SELECT status FROM ReturnSession WHERE return_id=?", (ret,))[0], "pending")
        self.assertEqual(self.counts("StockMovement"), [base_moves])

        rep = self.invoke("returns.create", {"employee": 1})["return_id"]
        for vid in (self.v1, self.v2):
            self.invoke("returns.scan", {"return_id": rep, "variant_id": vid, "qty": 1})
        items = [self.return_item_id(rep, v) for v in (self.v1, self.v2)]
        self.invoke("returns.close", {"return_id": rep})
        self.invoke("returns.set_kind", {"return_id": rep, "kind": "repair"})
        self.invoke("returns.approve", self.approve_payload(rep))
        with self.fail_on_second_post(), self.assertRaises(RuntimeError):
            self.invoke("returns.repair_receive", {"return_id": rep, "employee": 1,
                                                   "items": [{"item_id": i, "qty": 1} for i in items]})
        self.assertEqual(self.counts("StockMovement"), [base_moves])
        self.assertEqual(self.one("SELECT status FROM ReturnSession WHERE return_id=?", (rep,))[0], "repairing")


if __name__ == "__main__":
    unittest.main()
