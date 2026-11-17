"""第 2 組修正：返修單紀錄分區、抽回後新品項處理方式、結案理由長度、紀錄查詢批次撈取。"""
from test_returns import ReturnsBase
from lib.application_errors import ConflictError, ValidationError
from lib.db import get_conn
from lib.returns_service import ReturnsRepository, ReturnsService


class TestFixG2Returns(ReturnsBase):
    def make_repair(self, approve=True):
        rid = self.invoke("returns.create", {"kind":"repair","employee": 2})["return_id"]
        self.invoke("returns.scan", {"return_id": rid, "variant_id": self.v1, "qty": 2})
        self.submit_return(rid)
        self.as_admin()
        if approve: self.invoke("returns.approve", self.approve_payload(rid))
        return rid

    def ids(self, kind):
        return [r["return_id"] for r in self.invoke("returns.history", {"kind": kind})]

    def test_unapproved_repair_listed_in_normal(self):
        rid = self.make_repair(approve=False)
        self.assertIn(rid, self.ids("normal")); self.assertNotIn(rid, self.ids("repair"))
        self.invoke("returns.withdraw", {"return_id": rid})
        self.assertIn(rid, self.ids("normal")); self.assertNotIn(rid, self.ids("repair"))

    def test_approved_repair_moves_to_repair(self):
        rid = self.make_repair()
        self.assertNotIn(rid, self.ids("normal")); self.assertIn(rid, self.ids("repair"))

    def test_scan_after_withdraw_keeps_repair_mode(self):
        rid = self.make_repair(approve=False)
        self.invoke("returns.withdraw", {"return_id": rid})
        self.invoke("returns.scan", {"return_id": rid, "variant_id": self.v2, "qty": 1})
        items = {i["variant_id"]: i["disposition"] for i in self.invoke("returns.detail", {"return_id": rid})["items"]}
        self.assertIsNone(items[self.v2])   # 依單據種類:返修單品項沒有個別處理方式
        self.submit_return(rid)
        self.invoke("returns.approve", self.approve_payload(rid))
        self.assertEqual(self.invoke("returns.detail", {"return_id": rid})["status"], "repairing")

    def test_normal_scan_still_restock(self):
        rid = self.invoke("returns.create", {"kind":"normal","employee": 2})["return_id"]
        self.invoke("returns.scan", {"return_id": rid, "variant_id": self.v1, "qty": 1})
        self.assertEqual(self.invoke("returns.detail", {"return_id": rid})["items"][0]["disposition"], "restock")

    def test_close_reason_max_length(self):
        rid = self.make_repair()
        with self.assertRaises(ValidationError):
            self.invoke("returns.close_item", {"return_id": rid, "item_id": self.return_item_id(rid, self.v1), "reason": "字" * 31})
        self.invoke("returns.close_item", {"return_id": rid, "item_id": self.return_item_id(rid, self.v1), "reason": " " + "字" * 30 + " "})

    def test_batched_history_keeps_items_and_repair_counts(self):
        r1 = self.make_repair()
        self.invoke("returns.repair_receive", {"return_id": r1, "employee": 1, "items": [{"item_id": self.return_item_id(r1, self.v1), "qty": 1}]})
        r2 = self.invoke("returns.create", {"kind":"normal","employee": 2})["return_id"]
        for v in (self.v2, self.v3, self.v1):
            self.invoke("returns.scan", {"return_id": r2, "variant_id": v, "qty": 1})
        self.invoke("returns.set_reason", {"return_id": r2, "item_id": self.return_item_id(r2, self.v2), "reason": "瑕疵"})
        self.invoke("returns.create", {"kind":"normal","employee": 2})
        for kind in ("normal", "repair"):
            got = self.invoke("returns.history", {"kind": kind})
            self.assertTrue(got)
            for r in got:
                self.assertEqual(r, self.invoke("returns.detail", {"return_id": r["return_id"]}))

        repair = self.invoke("returns.detail", {"return_id":r1})
        self.assertEqual([(i["variant_id"],i["qty"],i["repaired_qty"]) for i in repair["items"]], [(self.v1,2,1)])
        normal = self.invoke("returns.detail", {"return_id":r2})
        self.assertEqual([i["variant_id"] for i in normal["items"]], [self.v2,self.v3,self.v1])
        self.assertEqual(normal["items"][0]["reason"], "瑕疵")
