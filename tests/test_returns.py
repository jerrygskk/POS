"""退貨單兩段式流程、處理方式與作廢；盤點單作廢與差異原因。"""
from base import FacadeTestCase
from lib import product_data
from lib.db import get_conn


class ReturnsBase(FacadeTestCase):
    def setUp(self):
        super().setUp()
        category_id = self.create_category("return category")
        product = self.invoke("products.create", {
            "name": "return product", "category_id": category_id,
            "variants": [
                {"attributes": {}, "barcodes": [{"barcode": "R1", "source": "store"}]},
                {"attributes": {}, "barcodes": [{"barcode": "R2", "source": "store"}]},
                {"attributes": {}, "barcodes": [{"barcode": "R3", "source": "store"}]},
            ],
        })
        self.v1, self.v2, self.v3 = product["variant_ids"]
        self.invoke("stock.receive", {"employee": 1, "items": [
            {"variant_id": v, "qty": 5} for v in (self.v1, self.v2, self.v3)]})

    def stock(self, vid):
        conn = get_conn(self.db)
        try: return product_data.stock_of(conn, vid)
        finally: conn.close()

    def as_employee(self):
        self.facade.session.logout()

    def as_admin(self):
        self.facade.session.login("admin")


class TestReturns(ReturnsBase):
    def setUp(self):
        super().setUp()
        self.rid = self.invoke("returns.create", {"employee": 2})["return_id"]

    def fill(self):
        for vid in (self.v1, self.v2, self.v3):
            self.invoke("returns.scan", {"return_id": self.rid, "variant_id": vid, "qty": 1})
        self.invoke("returns.scan", {"return_id": self.rid, "variant_id": self.v1, "qty": 1})

    def test_scan_accumulates_and_edits_while_open(self):
        self.fill()
        self.invoke("returns.set_qty", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v2), "qty": 3})
        self.invoke("returns.set_reason", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v2), "reason": "  瑕疵 "})
        self.invoke("returns.remove_item", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v3)})
        items = {i["variant_id"]: i for i in self.invoke("returns.detail", {"return_id": self.rid})["items"]}
        self.assertEqual((items[self.v1]["qty"], items[self.v2]["qty"]), (2, 3))
        self.assertEqual(items[self.v2]["reason"], "瑕疵")
        self.assertEqual(items[self.v1]["disposition"], "restock")
        self.assertNotIn(self.v3, items)

    def test_approve_applies_only_restock(self):
        self.fill()
        self.invoke("returns.close", {"return_id": self.rid})
        self.assertEqual(self.stock(self.v1), 5)
        self.invoke("returns.set_disposition", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v2), "disposition": "discard"})
        self.invoke("returns.set_disposition", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v3), "disposition": "reject"})
        self.invoke("returns.approve", {"return_id": self.rid})
        self.assertEqual([self.stock(v) for v in (self.v1, self.v2, self.v3)], [7, 5, 5])
        detail = self.invoke("returns.detail", {"return_id": self.rid})
        self.assertEqual(detail["status"], "closed")
        self.assertIsNotNone(detail["approved_at"])
        self.assert_application_error("conflict", "returns.approve", {"return_id": self.rid})
        history = self.invoke("returns.history", {})
        self.assertEqual([h["return_id"] for h in history], [self.rid])

    def test_no_edit_after_submit_and_withdraw_reopens(self):
        self.fill()
        self.invoke("returns.close", {"return_id": self.rid})
        self.assert_application_error("conflict", "returns.scan", {"return_id": self.rid, "variant_id": self.v1, "qty": 1})
        self.assert_application_error("conflict", "returns.set_qty", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v1), "qty": 1})
        self.invoke("returns.withdraw", {"return_id": self.rid})
        self.invoke("returns.scan", {"return_id": self.rid, "variant_id": self.v1, "qty": 1})
        self.assert_application_error("conflict", "returns.set_disposition",
                                      {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v1), "disposition": "discard"})

    def test_empty_return_cannot_submit(self):
        self.assert_application_error("conflict", "returns.close", {"return_id": self.rid})

    def test_sale_id_per_item_optional_but_must_exist(self):
        self.fill()
        self.assert_application_error("not_found", "returns.set_sale", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v1), "sale_id": 999})
        sale = self.invoke("sales.checkout", {"payment": self.invoke("payments.list")[0], "paid": 0, "employee": 1,
                                              "items": [{"variant_id": self.v1, "qty": 1, "unit_price": 0}]})
        self.invoke("returns.set_sale", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v1), "sale_id": sale["sale_id"]})
        items = {i["variant_id"]: i for i in self.invoke("returns.detail", {"return_id": self.rid})["items"]}
        self.assertEqual((items[self.v1]["sale_id"], items[self.v2]["sale_id"]), (sale["sale_id"], None))
        self.invoke("returns.set_sale", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v1), "sale_id": None})
        self.assertIsNone(self.invoke("returns.detail", {"return_id": self.rid})["items"][0]["sale_id"])
        self.assert_application_error("validation_error", "returns.set_sale", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v1)})

    def test_cancel_rules(self):
        self.fill()
        self.as_employee()
        self.invoke("returns.close", {"return_id": self.rid})
        self.assert_application_error("forbidden", "returns.cancel", {"return_id": self.rid, "employee": 2})
        self.as_admin()
        self.invoke("returns.cancel", {"return_id": self.rid, "employee": 3})
        detail = self.invoke("returns.detail", {"return_id": self.rid})
        self.assertEqual((detail["status"], detail["cancelled_by"]), ("cancelled", 3))
        self.assertEqual(self.stock(self.v1), 5)
        self.assert_application_error("conflict", "returns.cancel", {"return_id": self.rid, "employee": 3})
        # 紀錄查詢不列作廢單
        self.assertNotIn(self.rid, [r["return_id"] for r in self.invoke("returns.history", {})])
        # 進行中員工可作廢;已核准不可作廢
        self.as_employee()
        rid = self.invoke("returns.create", {"employee": 1})["return_id"]
        self.invoke("returns.cancel", {"return_id": rid, "employee": 1})
        self.as_admin()
        rid = self.invoke("returns.create", {"employee": 1})["return_id"]
        self.invoke("returns.scan", {"return_id": rid, "variant_id": self.v1, "qty": 1})
        self.invoke("returns.close", {"return_id": rid})
        self.invoke("returns.approve", {"return_id": rid})
        self.assert_application_error("conflict", "returns.cancel", {"return_id": rid, "employee": 1})

    def test_employee_cannot_review(self):
        self.fill()
        self.invoke("returns.close", {"return_id": self.rid})
        self.as_employee()
        for action, payload in (("returns.approve", {"return_id": self.rid}),
                                ("returns.set_disposition", {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v1), "disposition": "discard"}),
                                ("returns.pending_count", {})):
            with self.subTest(action=action):
                self.assertEqual(self.invoke_envelope(action, payload)["error"]["code"], "forbidden")
        self.assertEqual(self.stock(self.v1), 5)
        self.invoke("returns.withdraw", {"return_id": self.rid})

    def test_pending_count_and_validation(self):
        self.fill()
        self.invoke("returns.close", {"return_id": self.rid})
        self.assertEqual(self.invoke("returns.pending_count"), {"count": 1})
        self.assert_application_error("validation_error", "returns.create", {})
        self.assert_application_error("validation_error", "returns.scan", {"return_id": self.rid, "variant_id": self.v1, "qty": 0})
        self.assert_application_error("validation_error", "returns.set_disposition",
                                      {"return_id": self.rid, "item_id": self.return_item_id(self.rid, self.v1), "disposition": "x"})

    def test_returns_do_not_block_stocktake(self):
        sid = self.invoke("stocktake.create", {"employee": 1})["session_id"]
        self.invoke("stocktake.scan", {"session_id": sid, "variant_id": self.v1, "qty": 5})
        self.invoke("stocktake.close", {"session_id": sid})
        self.fill()
        self.invoke("returns.close", {"return_id": self.rid})
        self.invoke("returns.approve", {"return_id": self.rid})
        self.invoke("stocktake.withdraw", {"session_id": sid})
        item = self.invoke("stocktake.detail", {"session_id": sid})["items"][0]
        self.assertEqual((item["system_qty"], item["counted_qty"]), (7, 7))


class TestStocktakeCancelAndNote(ReturnsBase):
    def setUp(self):
        super().setUp()
        self.sid = self.invoke("stocktake.create", {"employee": 1})["session_id"]
        self.invoke("stocktake.scan", {"session_id": self.sid, "variant_id": self.v1, "qty": 3})

    def test_note_saved_and_trimmed_only_while_open(self):
        self.invoke("stocktake.set_note", {"session_id": self.sid, "variant_id": self.v1, "note": " 破損 "})
        self.assertEqual(self.invoke("stocktake.detail", {"session_id": self.sid})["items"][0]["note"], "破損")
        self.invoke("stocktake.set_note", {"session_id": self.sid, "variant_id": self.v1, "note": ""})
        self.assertIsNone(self.invoke("stocktake.detail", {"session_id": self.sid})["items"][0]["note"])
        self.assert_application_error("not_found", "stocktake.set_note", {"session_id": self.sid, "variant_id": self.v2, "note": "x"})
        self.invoke("stocktake.close", {"session_id": self.sid})
        self.assert_application_error("conflict", "stocktake.set_note", {"session_id": self.sid, "variant_id": self.v1, "note": "x"})

    def test_cancel_frees_active_slot_and_keeps_record(self):
        self.invoke("stocktake.cancel", {"session_id": self.sid, "employee": 2})
        self.assertEqual(self.stock(self.v1), 5)
        new_sid = self.invoke("stocktake.create", {"employee": 1})["session_id"]
        self.assertNotEqual(new_sid, self.sid)
        history = self.invoke("stocktake.history", {})
        self.assertEqual([(h["session_id"], h["status"], h["cancelled_by"]) for h in history],
                         [(self.sid, "cancelled", 2)])

    def test_pending_cancel_needs_admin_and_closed_cannot_cancel(self):
        self.invoke("stocktake.close", {"session_id": self.sid})
        self.as_employee()
        self.assertEqual(self.invoke_envelope("stocktake.cancel", {"session_id": self.sid, "employee": 1})["error"]["code"],
                         "forbidden")
        self.as_admin()
        self.invoke("stocktake.approve", {"session_id": self.sid})
        self.assert_application_error("conflict", "stocktake.cancel", {"session_id": self.sid, "employee": 1})
        self.assertEqual(self.stock(self.v1), 3)


class DisplayModelsTest(FacadeTestCase):
    def test_scan_and_return_show_models_only_when_category_uses_them(self):
        category_id = self.create_category("model category")
        self.invoke("categories.update", {"id": category_id, "fields": {"model_mode": "required"}})
        model_id = self.create_model(self.create_phone_brand("虛構品牌"), "虛構機型")
        vid = self.invoke("products.create", {"name": "殼", "category_id": category_id, "variants": [
            {"attributes": {}, "model_ids": [model_id],
             "barcodes": [{"barcode": "M1", "source": "store"}]}]})["variant_ids"][0]
        rid = self.invoke("returns.create", {"employee": 1})["return_id"]
        self.invoke("returns.scan", {"return_id": rid, "variant_id": vid, "qty": 1})
        def shown():
            return (self.invoke("barcodes.scan", {"code": "M1"})["matches"][0]["models"],
                    self.invoke("returns.detail", {"return_id": rid})["items"][0]["models"])
        self.assertEqual(shown(), (["虛構機型"], ["虛構機型"]))
        self.invoke("categories.update", {"id": category_id, "fields": {"model_mode": "hidden"}})
        self.assertEqual(shown(), ([], []))
