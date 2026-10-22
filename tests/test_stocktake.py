from concurrent.futures import ThreadPoolExecutor
import sqlite3
from unittest.mock import patch

from base import FacadeTestCase
from lib.application_errors import ApplicationError
from lib.db import get_conn


class TestStocktake(FacadeTestCase):
    def setUp(self):
        super().setUp()
        category_id = self.create_category("stocktake category")
        product = self.invoke("products.create", {
            "name": "stocktake product",
            "category_id": category_id,
            "variants": [
                {"attributes": {}, "barcodes": [{"barcode": "A1", "source": "store"}]},
                {"attributes": {}, "barcodes": [{"barcode": "A2", "source": "store"}]},
            ],
        })
        self.v1, self.v2 = product["variant_ids"]
        self.invoke("stock.receive", {"employee": 1, "items": [{"variant_id": self.v1, "qty": 5}]})
        self.invoke("stock.receive", {"employee": 1, "items": [{"variant_id": self.v2, "qty": 3}]})
        self.sid = self.invoke("stocktake.create", {"employee": 1,
        })["session_id"]

    def test_scan_snapshot_and_accumulate(self):
        result = self.invoke("stocktake.scan", {
            "session_id": self.sid, "variant_id": self.v1, "qty": 1,
        })
        self.assertEqual((result["system_qty"], result["counted_qty"]), (5, 1))
        result = self.invoke("stocktake.scan", {
            "session_id": self.sid, "variant_id": self.v1, "qty": 1,
        })
        self.assertEqual(result["counted_qty"], 2)

    def test_close_submits_without_changing_stock_then_approve_adjusts_only_diff(self):
        self.invoke("stocktake.scan", {
            "session_id": self.sid, "variant_id": self.v1, "qty": 4,
        })
        self.invoke("stocktake.close", {"session_id": self.sid})
        detail = self.invoke("stocktake.detail", {"session_id": self.sid})
        self.assertEqual(detail["status"], "pending")
        self.assertIsNotNone(detail["submitted_at"])
        self.assertIsNone(detail["approved_at"])
        self.assertIsNone(detail["ended_at"])
        self.assertEqual(self.stock(), 5)
        self.invoke("stocktake.approve", {"session_id": self.sid})
        detail = self.invoke("stocktake.detail", {"session_id": self.sid})
        self.assertEqual(detail["status"], "closed")
        self.assertIsNotNone(detail["approved_at"])
        self.assertEqual(detail["ended_at"], detail["approved_at"])
        self.assertEqual(self.invoke("stock.detail", {
            "variant_id": self.v1,
        })["stock"], 4)
        self.assertEqual(self.invoke("stock.detail", {
            "variant_id": self.v2,
        })["stock"], 3)

    def test_close_twice_raises_conflict(self):
        self.invoke("stocktake.close", {"session_id": self.sid})
        self.assert_application_error("conflict", "stocktake.close", {
            "session_id": self.sid,
        })

    def test_concurrent_approve_creates_one_adjustment(self):
        self.invoke("stocktake.scan", {
            "session_id": self.sid, "variant_id": self.v1, "qty": 4,
        })
        self.invoke("stocktake.close", {"session_id": self.sid})

        def close_session():
            try:
                return self.invoke("stocktake.approve", {"session_id": self.sid})
            except ApplicationError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: close_session(), range(2)))

        self.assertEqual(1, sum(response == {"ok": True} for response in responses))
        errors = [response for response in responses if isinstance(response, ApplicationError)]
        self.assertEqual(["conflict"], [error.code for error in errors])
        conn = get_conn(self.db)
        try:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM StockMovement "
                "WHERE kind='adjust' AND ref_id=?", (self.sid,)
            ).fetchone()["n"]
        finally:
            conn.close()
        self.assertEqual(count, 1)

    def stock(self):
        return self.invoke("stock.detail", {"variant_id": self.v1})["stock"]

    def item(self):
        return self.invoke("stocktake.detail", {"session_id": self.sid})["items"][0]

    def scan(self, qty):
        return self.invoke("stocktake.scan", {"session_id": self.sid, "variant_id": self.v1, "qty": qty})

    def set_counted(self, qty):
        return self.invoke("stocktake.set_counted", {"session_id": self.sid, "variant_id": self.v1, "counted_qty": qty})

    def submit(self):
        return self.invoke("stocktake.close", {"session_id": self.sid})

    def sell(self, qty):
        return self.invoke("sales.checkout", {"employee": 1, "payment": "現金", "paid": qty * 10,
            "items": [{"variant_id": self.v1, "qty": qty, "unit_price": 10}]})

    def test_employee_approve_denied_and_both_roles_can_withdraw(self):
        self.scan(4)
        self.submit()
        self.facade.session.logout()
        self.assert_application_error("forbidden", "stocktake.approve", {"session_id": self.sid})
        self.invoke("stocktake.withdraw", {"session_id": self.sid})
        self.assertEqual(self.invoke("stocktake.detail", {"session_id": self.sid})["status"], "open")
        self.submit()
        self.facade.session.login("admin")
        self.invoke("stocktake.withdraw", {"session_id": self.sid})
        self.assertIsNone(self.invoke("stocktake.detail", {"session_id": self.sid})["submitted_at"])
        self.assertEqual(self.stock(), 5)

    def test_one_unfinished_session_and_create_after_approval(self):
        for status in ("open", "pending"):
            with self.subTest(status=status):
                error = self.assert_application_error("conflict", "stocktake.create", {"employee": 1})
                self.assertEqual(error.message, "尚有未完成的盤點單，請先完成或等待管理者核准")
            if status == "open":
                self.submit()
        self.invoke("stocktake.approve", {"session_id": self.sid})
        self.assertGreater(self.invoke("stocktake.create", {"employee": 1})["session_id"], self.sid)

    def test_snapshot_difference_survives_sales_before_approval(self):
        self.scan(3)  # 系統 5、差 -2
        self.submit()
        self.sell(1)
        self.invoke("stocktake.approve", {"session_id": self.sid})
        self.assertEqual(self.stock(), 2)  # 5 - 1 - 2

    def test_withdraw_rebases_snapshot_and_preserves_original_difference(self):
        self._withdraw_example(None, 6)

    def test_withdraw_recount_uses_current_inventory_baseline(self):
        self._withdraw_example(5, 5)

    def _withdraw_example(self, recounted, expected):
        self.invoke("stock.receive", {"employee": 1, "items": [{"variant_id": self.v1, "qty": 5}]})
        self.scan(8)
        self.submit()
        self.sell(2)
        self.invoke("stocktake.withdraw", {"session_id": self.sid})
        self.assertEqual((self.item()["system_qty"], self.item()["counted_qty"]), (8, 6))
        if recounted is not None:
            self.set_counted(recounted)
        self.submit()
        self.invoke("stocktake.approve", {"session_id": self.sid})
        self.assertEqual(self.stock(), expected)

    def _negative_rebase(self):
        self.scan(1)
        self.submit()
        self.sell(3)
        self.invoke("stocktake.withdraw", {"session_id": self.sid})
        self.assertEqual((self.item()["system_qty"], self.item()["counted_qty"], self.item()["needs_recount"]), (2, 0, 1))

    def test_negative_rebase_requires_confirmation_then_uses_new_count(self):
        self._negative_rebase()
        error = self.assert_application_error("conflict", "stocktake.close", {"session_id": self.sid})
        self.assertEqual(error.message, "有 1 項需重新清點，請確認數量後再結案")
        self.set_counted(1)
        self.assertEqual(self.item()["needs_recount"], 0)
        self.submit()
        self.invoke("stocktake.approve", {"session_id": self.sid})
        self.assertEqual(self.stock(), 1)

    def test_same_zero_count_clears_recount_flag(self):
        self._negative_rebase()
        self.set_counted(0)
        self.assertEqual(self.item()["needs_recount"], 0)
        self.submit()
        self.invoke("stocktake.approve", {"session_id": self.sid})
        self.assertEqual(self.stock(), 0)

    def test_scanning_recount_item_clears_flag(self):
        self._negative_rebase()
        self.scan(1)
        self.assertEqual(self.item()["needs_recount"], 0)
        self.submit()

    def test_pending_employee_cannot_edit_but_admin_correction_is_used(self):
        self.scan(4)
        self.submit()
        self.facade.session.logout()
        self.assert_application_error("forbidden", "stocktake.set_counted", {
            "session_id": self.sid, "variant_id": self.v1, "counted_qty": 2})
        self.assertEqual(self.item()["counted_qty"], 4)
        self.facade.session.login("admin")
        self.set_counted(2)
        self.invoke("stocktake.approve", {"session_id": self.sid})
        self.assertEqual(self.stock(), 2)

    def test_closed_session_cannot_change_or_apply_adjustment_twice(self):
        self.scan(4)
        self.submit()
        self.invoke("stocktake.approve", {"session_id": self.sid})
        for action, fields in (("set_counted", {"variant_id": self.v1, "counted_qty": 99}),
                               ("scan", {"variant_id": self.v1, "qty": 1}),
                               ("close", {}), ("withdraw", {}), ("approve", {})):
            with self.subTest(action=action):
                self.assert_application_error("conflict", "stocktake." + action, {"session_id": self.sid, **fields})
                self.assertEqual(self.stock(), 4)
        conn = get_conn(self.db)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM StockMovement WHERE kind='adjust' AND ref_id=?", (self.sid,)).fetchone()[0], 1)
        finally:
            conn.close()

    def test_pending_count_tracks_submission_withdrawal_and_approval(self):
        self.assertEqual(self.invoke("stocktake.pending_count"), {"count": 0})
        self.submit()
        self.assertEqual(self.invoke("stocktake.pending_count"), {"count": 1})
        self.invoke("stocktake.withdraw", {"session_id": self.sid})
        self.assertEqual(self.invoke("stocktake.pending_count"), {"count": 0})
        self.submit()
        self.invoke("stocktake.approve", {"session_id": self.sid})
        self.assertEqual(self.invoke("stocktake.pending_count"), {"count": 0})

    def test_approve_rejects_corrupt_recount_flag_without_changing_stock(self):
        self.scan(4)
        self.submit()
        conn = get_conn(self.db)
        try:
            conn.execute("UPDATE StocktakeItem SET needs_recount=1 WHERE session_id=?", (self.sid,))
            conn.commit()
        finally:
            conn.close()
        self.assert_application_error("conflict", "stocktake.approve", {"session_id": self.sid})
        self.assertEqual(self.stock(), 5)

    def test_unique_index_blocks_two_independent_creators_after_both_prechecks(self):
        self.submit()
        self.invoke("stocktake.approve", {"session_id": self.sid})
        first, second = get_conn(self.db), get_conn(self.db)
        try:
            sql = "SELECT 1 FROM StocktakeSession WHERE status IN ('open','pending') LIMIT 1"
            self.assertIsNone(first.execute(sql).fetchone())
            self.assertIsNone(second.execute(sql).fetchone())
            first.execute("INSERT INTO StocktakeSession(employee) VALUES(1)")
            first.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "idx_stocktake_one_active"):
                second.execute("INSERT INTO StocktakeSession(employee) VALUES(2)")
        finally:
            first.close()
            second.close()

    def test_create_translates_unique_index_race_to_friendly_conflict(self):
        from lib.stocktake_service import StocktakeRepository
        original = StocktakeRepository.one

        def hide_active(repository, sql, parameters=()):
            if "SELECT 1 FROM StocktakeSession" in sql and "pending" in sql:
                return None
            return original(repository, sql, parameters)

        with patch.object(StocktakeRepository, "one", hide_active):
            error = self.assert_application_error("conflict", "stocktake.create", {"employee": 1})
        self.assertEqual(error.message, "尚有未完成的盤點單，請先完成或等待管理者核准")

    def test_manual_set(self):
        self.invoke("stocktake.scan", {
            "session_id": self.sid, "variant_id": self.v1, "qty": 1,
        })
        self.invoke("stocktake.set_counted", {
            "session_id": self.sid, "variant_id": self.v1, "counted_qty": 7,
        })
        detail = self.invoke("stocktake.detail", {"session_id": self.sid})
        item = [item for item in detail["items"] if item["variant_id"] == self.v1][0]
        self.assertEqual(item["counted_qty"], 7)
        self.assertEqual(item["diff"], 2)

    def test_manual_set_unscanned_raises_not_found(self):
        self.assert_application_error("not_found", "stocktake.set_counted", {
            "session_id": self.sid, "variant_id": self.v2, "counted_qty": 3,
        })

    def test_negative_counts_rejected(self):
        self.assert_application_error("validation_error", "stocktake.scan", {
            "session_id": self.sid, "variant_id": self.v1, "qty": -1,
        })
        self.invoke("stocktake.scan", {
            "session_id": self.sid, "variant_id": self.v1, "qty": 1,
        })
        self.assert_application_error("validation_error", "stocktake.set_counted", {
            "session_id": self.sid, "variant_id": self.v1, "counted_qty": -1,
        })
