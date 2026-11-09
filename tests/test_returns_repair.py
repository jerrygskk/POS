"""返修退貨:審核規則、返新進貨、結案與撤回、紀錄查詢分區、schema v24 升級。"""
import sqlite3
from pathlib import Path

from base import ConnTestCase
from lib import db_schema
from lib.db import get_conn, init_db
from test_returns import ReturnsBase


class TestRepair(ReturnsBase):
    def setUp(self):
        super().setUp()
        self.rid = self.invoke("returns.create", {"employee": 2})["return_id"]
        self.invoke("returns.scan", {"return_id": self.rid, "variant_id": self.v1, "qty": 2})
        self.invoke("returns.scan", {"return_id": self.rid, "variant_id": self.v2, "qty": 1})
        self.invoke("returns.scan", {"return_id": self.rid, "variant_id": self.v3, "qty": 1})
        self.invoke("returns.close", {"return_id": self.rid})

    def disp(self, vid, value):
        self.invoke("returns.set_disposition", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, vid), "disposition": value})

    def items(self):
        return {i["variant_id"]: i for i in self.invoke("returns.detail", {"return_id": self.rid})["items"]}

    def set_kind(self, kind):
        return self.invoke("returns.set_kind", {"return_id": self.rid, "kind": kind})

    def start_repair(self):
        self.set_kind("repair")
        self.invoke("returns.approve", self.approve_payload(self.rid))

    def receive(self, vid, qty, employee=1):
        return self.invoke("returns.repair_receive", {"return_id": self.rid, "employee": employee,
                                                      "items": [{"item_id": self.return_item_id(self.rid, vid), "qty": qty}]})

    def status(self):
        return self.invoke("returns.detail", {"return_id": self.rid})["status"]

    def test_set_kind_switches_whole_sheet(self):
        self.disp(self.v3, "reject")
        self.set_kind("repair")
        detail = self.invoke("returns.detail", {"return_id": self.rid})
        self.assertEqual((detail["kind"], {i["disposition"] for i in detail["items"]}), ("repair", {None}))
        self.assertNotIn("is_repair", detail)
        # 返修單不逐項選處理方式
        self.assert_application_error("conflict", "returns.set_disposition",
                                      {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v2), "disposition": "reject"})
        self.set_kind("normal")
        detail = self.invoke("returns.detail", {"return_id": self.rid})
        self.assertEqual((detail["kind"], {i["disposition"] for i in detail["items"]}), ("normal", {"restock"}))
        self.assert_application_error("validation_error", "returns.set_disposition",
                                      {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v2), "disposition": "repair"})
        self.assert_application_error("validation_error", "returns.set_kind", {"return_id": self.rid, "kind": "other"})

    def test_set_kind_only_while_pending(self):
        self.invoke("returns.withdraw", {"return_id": self.rid})
        self.assert_application_error("conflict", "returns.set_kind", {"return_id": self.rid, "kind": "repair"})
        empty = self.invoke("returns.create", {"employee": 1})["return_id"]
        self.assert_application_error("conflict", "returns.set_kind", {"return_id": empty, "kind": "repair"})

    def test_set_kind_requires_admin_even_after_confirm(self):
        self.as_employee()
        self.assert_application_error("forbidden", "returns.set_kind", {"return_id": self.rid, "kind": "repair"})
        self.as_admin()
        # 外層檢查時是管理者,進交易後已閒置登出(確認框期間):交易內再查一次仍擋下且不寫入
        from lib.returns_service import ReturnsFacade
        from lib.application_errors import ForbiddenError
        answers = iter([True, False])

        class FlakySession:
            def is_admin(self):
                return next(answers, False)
        with self.assertRaises(ForbiddenError):
            ReturnsFacade(self.db, session=FlakySession()).invoke("returns.set_kind", {"return_id": self.rid, "kind": "repair"})
        self.assertEqual(self.invoke("returns.detail", {"return_id": self.rid})["kind"], "normal")

    def test_empty_pending_sheet_can_switch(self):
        # 抽回、刪光品項是進行中,不能送審;直接在資料庫造一張空的待核准單,切換仍一致
        empty = self.invoke("returns.create", {"employee": 1})["return_id"]
        with get_conn(self.db) as conn:
            conn.execute("UPDATE ReturnSession SET status='pending' WHERE return_id=?", (empty,))
            conn.commit()
        self.invoke("returns.set_kind", {"return_id": empty, "kind": "repair"})
        self.assertEqual(self.invoke("returns.detail", {"return_id": empty})["kind"], "repair")

    def test_database_rejects_bad_dispositions_and_close_fields(self):
        conn = get_conn(self.db)
        self.addCleanup(conn.close)
        item = self.return_item_id(self.rid, self.v1)
        for sql, args in (
            ("UPDATE ReturnItem SET disposition='repair' WHERE id=?", (item,)),                      # 舊值已移除
            ("UPDATE ReturnItem SET disposition=NULL, closed_qty=3, closed_reason='x', closed_at='t' WHERE id=?", (item,)),  # 超過數量
            ("UPDATE ReturnItem SET disposition=NULL, closed_qty=1 WHERE id=?", (item,)),            # 結案缺理由與時間
            ("UPDATE ReturnItem SET closed_qty=1, closed_reason='x', closed_at='t' WHERE id=?", (item,)),  # 一般品項不能結案
            ("UPDATE ReturnSession SET kind='other' WHERE return_id=?", (self.rid,)),
        ):
            with self.subTest(sql), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(sql, args)
        conn.rollback()

    def test_service_rejects_kind_and_disposition_mismatch(self):
        # 資料庫只擋單筆;單據種類與品項不一致由服務在同一交易擋下
        with get_conn(self.db) as conn:
            conn.execute("UPDATE ReturnSession SET kind='repair' WHERE return_id=?", (self.rid,))
            conn.commit()
        self.assert_application_error("conflict", "returns.approve", self.approve_payload(self.rid))

    def test_approve_repair_keeps_stock_and_marks_repairing(self):
        self.start_repair()
        self.assertEqual(self.status(), "repairing")
        self.assertEqual([self.stock(v) for v in (self.v1, self.v2, self.v3)], [5, 5, 5])

    def test_partial_receive_then_complete(self):
        self.start_repair()
        self.assertEqual(self.receive(self.v1, 1), {"done": False})
        self.assertEqual(self.stock(self.v1), 6)
        self.assertEqual(self.items()[self.v1]["repaired_qty"], 1)
        self.assert_application_error("conflict", "returns.repair_receive", {"return_id": self.rid, "employee": 1,
                                      "items": [{"item_id": self.return_item_id(self.rid, self.v1), "qty": 2}]})
        self.receive(self.v1, 1)
        self.receive(self.v3, 1)
        self.assertEqual(self.receive(self.v2, 1), {"done": True})
        detail = self.invoke("returns.detail", {"return_id": self.rid})
        self.assertEqual(detail["status"], "closed")
        self.assertIsNotNone(detail["completed_at"])
        self.assert_application_error("conflict", "returns.repair_receive", {"return_id": self.rid, "employee": 1,
                                      "items": [{"item_id": self.return_item_id(self.rid, self.v1), "qty": 1}]})

    def test_close_remaining_then_reopen(self):
        self.start_repair()
        self.receive(self.v1, 1)
        self.invoke("returns.close_item", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v1), "reason": " 廠商不保固 "})
        it = self.items()[self.v1]
        self.assertEqual((it["closed_qty"], it["closed_reason"], it["repaired_qty"]), (1, "廠商不保固", 1))
        self.assert_application_error("conflict", "returns.close_item", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v1), "reason": "x"})
        self.invoke("returns.close_item", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v2), "reason": "停產"})
        self.receive(self.v3, 1)
        self.assertEqual(self.status(), "closed")
        self.invoke("returns.reopen_item", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v2)})
        self.assertEqual(self.status(), "repairing")
        self.assertEqual(self.items()[self.v2]["closed_qty"], 0)
        self.assert_application_error("conflict", "returns.reopen_item", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v2)})
        self.assert_application_error("validation_error", "returns.close_item", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v2), "reason": "  "})

    def test_reason_limited_to_30_chars(self):
        rid = self.invoke("returns.create", {"employee": 1})["return_id"]
        self.invoke("returns.scan", {"return_id": rid, "variant_id": self.v1, "qty": 1})
        self.invoke("returns.set_reason", {"return_id": rid, "item_id": self.return_item_id(rid, self.v1), "reason": "字" * 30})
        self.assert_application_error("validation_error", "returns.set_reason",
                                      {"return_id": rid, "item_id": self.return_item_id(rid, self.v1), "reason": "字" * 31})

    def test_close_and_reopen_require_admin(self):
        self.start_repair()
        self.as_employee()
        self.assert_application_error("forbidden", "returns.close_item", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v1), "reason": "x"})
        self.assert_application_error("forbidden", "returns.reopen_item", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v1)})
        self.receive(self.v1, 1)  # 返新進貨店員可做

    def test_history_splits_normal_and_repair(self):
        normal = self.invoke("returns.create", {"employee": 1})["return_id"]
        self.invoke("returns.scan", {"return_id": normal, "variant_id": self.v1, "qty": 1})
        cancelled = self.invoke("returns.create", {"employee": 1})["return_id"]
        self.invoke("returns.cancel", {"return_id": cancelled, "employee": 1})
        self.start_repair()
        ids = lambda kind: [r["return_id"] for r in self.invoke("returns.history", {"kind": kind})]
        self.assertEqual(ids("normal"), [normal])
        self.assertEqual(ids("repair"), [self.rid])
        # 返修中不論日期都列出
        self.assertEqual([r["return_id"] for r in self.invoke("returns.history",
                         {"kind": "repair", "date_from": "2000-01-01", "date_to": "2000-01-02"})], [self.rid])


