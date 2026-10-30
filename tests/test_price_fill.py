import unittest

from lib.db import db_conn
from base import FacadeTestCase


class PriceFillTest(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.make_category_with_field("顏色", options=("黑", "白"))
        made = self.invoke("products.create", {
            "name": "測試膜", "category_id": self.cid,
            "variants": [
                {"attributes": {"顏色": "黑"}, "price": None, "barcodes": []},
                {"attributes": {"顏色": "白"}, "price": 300, "barcodes": []},
            ]})
        self.pid = made["product_id"]
        self.black, self.white = made["variant_ids"]

    def price(self, vid):
        with db_conn(self.db) as conn:
            return conn.execute("SELECT price FROM Variant WHERE variant_id=?", (vid,)).fetchone()[0]

    def test_list_shows_active_variants_with_spec(self):
        rows = {r["variant_id"]: r for r in self.invoke("variants.price_list")}
        self.assertEqual(rows[self.black]["spec"], "黑")
        self.assertIsNone(rows[self.black]["price"])
        self.assertEqual(rows[self.white]["price"], 300)
        self.assertEqual(rows[self.black]["category"], "鋼化玻璃")

    def test_save_writes_all_prices(self):
        result = self.invoke("variants.price_save", {"changes": [
            {"variant_id": self.black, "price": 250}, {"variant_id": self.white, "price": 320}]})
        self.assertEqual(result, {"saved": 2})
        self.assertEqual((self.price(self.black), self.price(self.white)), (250, 320))

    def test_invalid_price_is_rejected_before_writing(self):
        for bad in (0, -5, "100", 1.5, None):
            with self.subTest(bad=bad):
                self.assert_application_error("validation_error", "variants.price_save", {"changes": [
                    {"variant_id": self.white, "price": 999}, {"variant_id": self.black, "price": bad}]})
        self.assertEqual(self.price(self.white), 300)

    def test_inactive_variant_rolls_back_whole_batch(self):
        self.invoke("variants.update", {"id": self.white, "fields": {"active": 0}})
        self.assert_application_error("validation_error", "variants.price_save", {"changes": [
            {"variant_id": self.black, "price": 250}, {"variant_id": self.white, "price": 320}]})
        self.assertIsNone(self.price(self.black))

    def test_duplicate_and_empty_changes_are_rejected(self):
        self.assert_application_error("validation_error", "variants.price_save", {"changes": []})
        self.assert_application_error("validation_error", "variants.price_save", {"changes": [
            {"variant_id": self.black, "price": 1}, {"variant_id": self.black, "price": 2}]})

    def test_employee_cannot_list_or_save(self):
        self.facade.session.logout()
        self.assert_application_error("forbidden", "variants.price_list")
        self.assert_application_error("forbidden", "variants.price_save", {"changes": [
            {"variant_id": self.black, "price": 250}]})
        self.assertIsNone(self.price(self.black))


if __name__ == "__main__":
    unittest.main()
