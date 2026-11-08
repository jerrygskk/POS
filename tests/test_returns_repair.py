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
        self.invoke("returns.set_disposition", {"return_id": self.rid, "variant_id": vid, "disposition": value})

    def items(self):
        return {i["variant_id"]: i for i in self.invoke("returns.detail", {"return_id": self.rid})["items"]}

    def start_repair(self):
        self.disp(self.v1, "repair")
        self.invoke("returns.approve", {"return_id": self.rid})

    def receive(self, vid, qty, employee=1):
        return self.invoke("returns.repair_receive", {"return_id": self.rid, "employee": employee,
                                                      "items": [{"variant_id": vid, "qty": qty}]})

    def status(self):
        return self.invoke("returns.detail", {"return_id": self.rid})["status"]

    def test_repair_switches_whole_sheet(self):
        self.disp(self.v3, "reject")
        self.disp(self.v1, "repair")
        self.assertEqual({i["disposition"] for i in self.items().values()}, {"repair"})
        self.disp(self.v2, "reject")
        self.assertEqual({i["disposition"] for i in self.items().values()}, {"reject"})

    def test_approve_rejects_mixed_sheet(self):
        for other in ("restock", "reject"):
            with get_conn(self.db) as conn:
                conn.execute("UPDATE ReturnItem SET disposition=? WHERE return_id=?", (other, self.rid))
                conn.execute("UPDATE ReturnItem SET disposition='repair' WHERE return_id=? AND variant_id=?", (self.rid, self.v1))
                conn.commit()
            self.assert_application_error("conflict", "returns.approve", {"return_id": self.rid})

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
                                      "items": [{"variant_id": self.v1, "qty": 2}]})
        self.receive(self.v1, 1)
        self.receive(self.v3, 1)
        self.assertEqual(self.receive(self.v2, 1), {"done": True})
        detail = self.invoke("returns.detail", {"return_id": self.rid})
        self.assertEqual(detail["status"], "closed")
        self.assertIsNotNone(detail["completed_at"])
        self.assert_application_error("conflict", "returns.repair_receive", {"return_id": self.rid, "employee": 1,
                                      "items": [{"variant_id": self.v1, "qty": 1}]})

    def test_close_remaining_then_reopen(self):
        self.start_repair()
        self.receive(self.v1, 1)
        self.invoke("returns.close_item", {"return_id": self.rid, "variant_id": self.v1, "reason": " 廠商不保固 "})
        it = self.items()[self.v1]
        self.assertEqual((it["closed_qty"], it["closed_reason"], it["repaired_qty"]), (1, "廠商不保固", 1))
        self.assert_application_error("conflict", "returns.close_item", {"return_id": self.rid, "variant_id": self.v1, "reason": "x"})
        self.invoke("returns.close_item", {"return_id": self.rid, "variant_id": self.v2, "reason": "停產"})
        self.receive(self.v3, 1)
        self.assertEqual(self.status(), "closed")
        self.invoke("returns.reopen_item", {"return_id": self.rid, "variant_id": self.v2})
        self.assertEqual(self.status(), "repairing")
        self.assertEqual(self.items()[self.v2]["closed_qty"], 0)
        self.assert_application_error("conflict", "returns.reopen_item", {"return_id": self.rid, "variant_id": self.v2})
        self.assert_application_error("validation_error", "returns.close_item", {"return_id": self.rid, "variant_id": self.v2, "reason": "  "})

    def test_reason_limited_to_30_chars(self):
        rid = self.invoke("returns.create", {"employee": 1})["return_id"]
        self.invoke("returns.scan", {"return_id": rid, "variant_id": self.v1, "qty": 1})
        self.invoke("returns.set_reason", {"return_id": rid, "variant_id": self.v1, "reason": "字" * 30})
        self.assert_application_error("validation_error", "returns.set_reason",
                                      {"return_id": rid, "variant_id": self.v1, "reason": "字" * 31})

    def test_close_and_reopen_require_admin(self):
        self.start_repair()
        self.as_employee()
        self.assert_application_error("forbidden", "returns.close_item", {"return_id": self.rid, "variant_id": self.v1, "reason": "x"})
        self.assert_application_error("forbidden", "returns.reopen_item", {"return_id": self.rid, "variant_id": self.v1})
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


