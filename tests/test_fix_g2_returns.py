"""第 2 組修正：返修單紀錄分區、抽回後新品項處理方式、結案理由長度、紀錄查詢批次撈取。"""
from test_returns import ReturnsBase
from lib.application_errors import ConflictError, ValidationError
from lib.db import get_conn
from lib.returns_service import ReturnsRepository, ReturnsService


class TestFixG2Returns(ReturnsBase):
    def make_repair(self, approve=True):
        rid = self.invoke("returns.create", {"employee": 2})["return_id"]
        self.invoke("returns.scan", {"return_id": rid, "variant_id": self.v1, "qty": 2})
        self.invoke("returns.close", {"return_id": rid})
        self.as_admin()
        self.invoke("returns.set_kind", {"return_id": rid, "kind": "repair"})
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
        self.invoke("returns.close", {"return_id": rid})
        self.invoke("returns.approve", self.approve_payload(rid))
        self.assertEqual(self.invoke("returns.detail", {"return_id": rid})["status"], "repairing")

    def test_normal_scan_still_restock(self):
        rid = self.invoke("returns.create", {"employee": 2})["return_id"]
        self.invoke("returns.scan", {"return_id": rid, "variant_id": self.v1, "qty": 1})
        self.assertEqual(self.invoke("returns.detail", {"return_id": rid})["items"][0]["disposition"], "restock")

    def test_close_reason_max_length(self):
        rid = self.make_repair()
        with self.assertRaises(ValidationError):
            self.invoke("returns.close_item", {"return_id": rid, "item_id": self.return_item_id(rid, self.v1), "reason": "字" * 31})
        self.invoke("returns.close_item", {"return_id": rid, "item_id": self.return_item_id(rid, self.v1), "reason": " " + "字" * 30 + " "})

    def test_batched_history_matches_old_per_detail(self):
        r1 = self.make_repair()
        self.invoke("returns.repair_receive", {"return_id": r1, "employee": 1, "items": [{"item_id": self.return_item_id(r1, self.v1), "qty": 1}]})
        r2 = self.invoke("returns.create", {"employee": 2})["return_id"]
        for v in (self.v2, self.v3, self.v1):
            self.invoke("returns.scan", {"return_id": r2, "variant_id": v, "qty": 1})
        self.invoke("returns.set_reason", {"return_id": r2, "item_id": self.return_item_id(r2, self.v2), "reason": "瑕疵"})
        self.invoke("returns.create", {"employee": 2})
        for kind in ("normal", "repair"):
            got = self.invoke("returns.history", {"kind": kind})
            self.assertTrue(got)
            conn = get_conn(self.db)
            try:
                svc = ReturnsService(ReturnsRepository(conn))
                old = [_old_detail(svc, r["return_id"]) for r in got]
            finally: conn.close()
            self.assertEqual(got, old)
            for r in got:
                self.assertEqual(r, self.invoke("returns.detail", {"return_id": r["return_id"]}))


def _old_detail(svc, rid):
    """修正前的逐張 detail 寫法,作為回傳內容比對基準。"""
    from lib import product_data
    repo = svc.repo
    session = repo.session(rid)
    rows = repo.all("SELECT ri.*,p.name FROM ReturnItem ri JOIN Variant v ON ri.variant_id=v.variant_id "
                    "JOIN Product p ON v.product_id=p.product_id WHERE ri.return_id=? ORDER BY ri.id", (rid,))
    vids = [r["variant_id"] for r in rows]
    attrs = product_data.attrs_by_variant(repo.connection, vids)
    disp = product_data.display_attrs(repo.connection, vids)
    models = product_data.display_models_by_variant(repo.connection, vids)
    repaired = repo.repaired(rid)
    codes = repo.label_codes(vids)
    items = [{"id": r["id"], "variant_id": r["variant_id"], "name": r["name"], "attributes": attrs.get(r["variant_id"], {}),
              "attr_display": disp.get(r["variant_id"], ""), "models": models.get(r["variant_id"], []), "qty": r["qty"], "sale_id": r["sale_id"], "reason": r["reason"],
              "disposition": r["disposition"], "closed_qty": r["closed_qty"], "closed_reason": r["closed_reason"],
              "closed_at": r["closed_at"], "repaired_qty": repaired.get(r["id"], 0),
              "label_barcode": codes.get(r["variant_id"])} for r in rows]
    return {**dict(session), "items": items}
