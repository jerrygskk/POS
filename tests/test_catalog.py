import unittest

from base import FacadeTestCase


class TestCatalog(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.make_category_with_field("color", options=("blue", "red", "green", "black"))

    def _create(self):
        return self.invoke("products.create", {"name": "HODA glass", "category_id": self.cid, "variants": [
            {"attributes": {"color": "blue"}, "price": 590, "barcodes": [{"barcode": "FX100000001", "source": "factory"}]},
            {"attributes": {"color": "red"}, "price": 690, "barcodes": [{"barcode": "FX100000002", "source": "factory"}]},
        ]})

    def test_catalog_grouping(self):
        result = self._create(); vid = result["variant_ids"][0]
        self.invoke("stock.receive", {"variant_id": vid, "qty": 5})
        product = self.invoke("catalog.list")[0]
        self.assertEqual(product["product_id"], result["product_id"]); self.assertNotIn("default_price", product)
        self.assertEqual(len(product["variants"]), 2); first, second = product["variants"]
        self.assertEqual((first["attributes"]["color"], first["price"], first["effective_price"], first["stock"]), ("blue", 590, 590, 5))
        self.assertTrue(first["active"]); self.assertEqual(first["barcodes"][0]["barcode"], "FX100000001")
        self.assertEqual((second["price"], second["effective_price"]), (690, 690))

    def test_inactive_product_hidden(self):
        result = self._create(); self.invoke("products.update", {"id": result["product_id"], "fields": {"active": 0}})
        self.assertEqual(self.invoke("catalog.list"), [])
        self.assertEqual(len(self.invoke("catalog.list", {"include_inactive": True})), 1)

    def test_inactive_variant_hidden(self):
        result = self._create(); self.invoke("variants.update", {"id": result["variant_ids"][1], "fields": {"active": 0}})
        self.assertEqual(len(self.invoke("catalog.list")[0]["variants"]), 1)
        self.assertEqual(len(self.invoke("catalog.list", {"include_inactive": True})[0]["variants"]), 2)

    def test_put_product(self):
        result = self._create(); self.invoke("products.update", {"id": result["product_id"], "fields": {"name": "new glass", "note": "memo"}})
        product = self.invoke("catalog.list")[0]; self.assertEqual((product["name"], product["note"]), ("new glass", "memo"))

    def test_put_variant(self):
        result = self._create(); vid = result["variant_ids"][0]
        self.invoke("variants.update", {"id": vid, "fields": {"price": 555, "attributes": {"color": "green"}}})
        variant = next(v for v in self.invoke("catalog.list")[0]["variants"] if v["variant_id"] == vid)
        self.assertEqual((variant["price"], variant["attributes"]["color"]), (555, "green"))

    def test_inactive_variant_cannot_sell(self):
        result = self._create(); vid = result["variant_ids"][0]
        self.invoke("stock.receive", {"variant_id": vid, "qty": 10}); self.invoke("variants.update", {"id": vid, "fields": {"active": 0}})
        self.assert_application_error("validation_error", "sales.checkout", {"payment": "cash", "paid": 1000, "items": [{"variant_id": vid, "qty": 1, "unit_price": 590}]})
        self.assertEqual(self.invoke("stock.detail", {"variant_id": vid})["stock"], 10)

    def test_inactive_product_cannot_sell(self):
        result = self._create(); vid = result["variant_ids"][0]
        self.invoke("stock.receive", {"variant_id": vid, "qty": 10}); self.invoke("products.update", {"id": result["product_id"], "fields": {"active": 0}})
        self.assert_application_error("validation_error", "sales.checkout", {"payment": "cash", "paid": 1000, "items": [{"variant_id": vid, "qty": 1, "unit_price": 590}]})
        self.assertEqual(self.invoke("stock.detail", {"variant_id": vid})["stock"], 10)

    def test_inactive_hidden_in_search(self):
        result = self._create(); self.assertTrue(self.invoke("products.list", {"q": "HODA"}))
        self.invoke("products.update", {"id": result["product_id"], "fields": {"active": 0}}); self.assertEqual(self.invoke("products.list", {"q": "HODA"}), [])

    def test_inactive_variant_hidden_in_search(self):
        result = self._create(); self.invoke("variants.update", {"id": result["variant_ids"][0], "fields": {"active": 0}})
        self.assertEqual(len(self.invoke("products.list", {"q": "HODA"})), 1)

    def test_scan_active_flag(self):
        result = self._create(); vid = result["variant_ids"][0]
        self.assertTrue(self.invoke("barcodes.scan", {"code": "FX100000001"})["active"])
        self.invoke("variants.update", {"id": vid, "fields": {"active": 0}})
        self.assertFalse(self.invoke("barcodes.scan", {"code": "FX100000001"})["active"])

    def test_delete_variant_with_record_raises_conflict(self):
        result = self._create(); vid = result["variant_ids"][0]; self.invoke("stock.receive", {"variant_id": vid, "qty": 1})
        self.assert_application_error("conflict", "variants.delete", {"id": vid})

    def test_delete_clean_variant(self):
        result = self._create(); vid = result["variant_ids"][1]
        self.assertTrue(self.invoke("variants.delete", {"id": vid})["ok"])
        self.assert_application_error("not_found", "barcodes.scan", {"code": "FX100000002"})
        self.assertEqual(len(self.invoke("catalog.list")[0]["variants"]), 1)

    def test_delete_product_with_record_raises_conflict(self):
        result = self._create(); self.invoke("stock.receive", {"variant_id": result["variant_ids"][0], "qty": 1})
        self.assert_application_error("conflict", "products.delete", {"id": result["product_id"]})

    def test_delete_clean_product(self):
        result = self._create(); self.assertTrue(self.invoke("products.delete", {"id": result["product_id"]})["ok"])
        self.assertEqual(self.invoke("catalog.list", {"include_inactive": True}), [])
        self.assert_application_error("not_found", "barcodes.scan", {"code": "FX100000001"})

    def test_delete_barcode(self):
        self._create(); self.assertTrue(self.invoke("barcodes.delete", {"code": "FX100000001"})["ok"])
        self.assert_application_error("not_found", "barcodes.scan", {"code": "FX100000001"})
        self.assert_application_error("not_found", "barcodes.delete", {"code": "NOPE"})

    def test_add_variant(self):
        result = self._create(); added = self.invoke("variants.create", {"product_id": result["product_id"], "fields": {"attributes": {"color": "black"}, "price": 790, "barcodes": [{"source": "store"}]}})
        self.assertTrue(added["barcodes"][0].startswith("TL")); self.assertEqual(len(self.invoke("catalog.list")[0]["variants"]), 3)

    def test_catalog_q_filter(self):
        self._create(); self.invoke("products.create", {"name": "other", "category_id": self.cid, "variants": [{"attributes": {}, "barcodes": []}]})
        rows = self.invoke("catalog.list", {"q": "HODA"}); self.assertEqual((len(rows), rows[0]["name"]), (1, "HODA glass"))


if __name__ == "__main__":
    unittest.main()
