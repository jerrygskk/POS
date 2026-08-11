from concurrent.futures import ThreadPoolExecutor

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
        self.invoke("stock.receive", {"variant_id": self.v1, "qty": 5})
        self.invoke("stock.receive", {"variant_id": self.v2, "qty": 3})
        self.sid = self.invoke("stocktake.create", {
            "operator": "operator",
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

    def test_close_adjusts_only_diff(self):
        self.invoke("stocktake.scan", {
            "session_id": self.sid, "variant_id": self.v1, "qty": 4,
        })
        self.invoke("stocktake.close", {"session_id": self.sid})
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

    def test_concurrent_close_creates_one_adjustment(self):
        self.invoke("stocktake.scan", {
            "session_id": self.sid, "variant_id": self.v1, "qty": 4,
        })

        def close_session():
            try:
                return self.invoke("stocktake.close", {"session_id": self.sid})
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
