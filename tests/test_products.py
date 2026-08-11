import unittest

from base import ConnTestCase, FacadeTestCase
from lib.application_errors import ValidationError
from lib.product_rules import FIELD_TYPES, allow_keys, check_field_type, is_int, next_store_barcode


class TestProductRules(ConnTestCase):
    def test_next_store_barcode_uses_default_and_increments(self):
        self.assertEqual(next_store_barcode(self.conn), "TL100000001"); self.assertEqual(next_store_barcode(self.conn), "TL100000002")
        self.assertEqual(self.conn.execute("SELECT value FROM Setting WHERE key='next_store_barcode'").fetchone()["value"], "100000003")

    def test_next_store_barcode_rolls_back_with_same_connection(self):
        self.assertEqual(next_store_barcode(self.conn), "TL100000001"); self.conn.rollback(); self.assertEqual(next_store_barcode(self.conn), "TL100000001")

    def test_runtime_field_types_are_shared(self):
        self.assertEqual(FIELD_TYPES, {"select", "text", "multi", "tags"})
        for field_type in FIELD_TYPES: check_field_type(field_type)
        with self.assertRaises(ValidationError) as raised: check_field_type("number")
        self.assertEqual(raised.exception.code, "validation_error")

    def test_shared_payload_primitives_reject_boolean_and_unknown_keys(self):
        self.assertTrue(is_int(1)); self.assertFalse(is_int(True)); allow_keys({"name": "x"}, {"name"}, "unknown")
        with self.assertRaisesRegex(ValidationError, "unknown"): allow_keys({"extra": 1}, {"name"}, "unknown")


class TestProducts(FacadeTestCase):
    def setUp(self):
        super().setUp(); self.make_category_with_field("color", options=("blue", "red", "green", "black"))

    def _create(self):
        return self.invoke("products.create", {"name": "HODA glass", "category_id": self.cid, "variants": [
            {"attributes": {"color": "blue"}, "price": 590, "barcodes": [{"barcode": "FX100000001", "source": "factory"}]},
            {"attributes": {"color": "red"}, "price": 690, "barcodes": []},
        ]})

    def test_create_and_scan(self):
        result = self._create(); self.assertEqual(len(result["variant_ids"]), 2); hit = self.invoke("barcodes.scan", {"code": "FX100000001"})
        self.assertEqual((hit["price"], hit["attributes"]["color"], hit["stock"]), (590, "blue", 0))

    def test_variant_price_overrides(self):
        result = self._create(); barcode = self.invoke("barcodes.add", {"variant_id": result["variant_ids"][1], "source": "store"})["barcode"]
        self.assertTrue(barcode.startswith("TL")); self.assertEqual(self.invoke("barcodes.scan", {"code": barcode})["price"], 690)

    def test_unknown_barcode_raises_not_found(self):
        self.assert_application_error("not_found", "barcodes.scan", {"code": "NOPE"})

    def test_null_price_allowed(self):
        self.invoke("products.create", {"name": "no price", "category_id": self.cid, "variants": [{"attributes": {}, "barcodes": [{"barcode": "X1", "source": "factory"}]}]})
        self.assertIsNone(self.invoke("barcodes.scan", {"code": "X1"})["price"])

    def test_store_barcode_sequence(self):
        result = self._create(); vid = result["variant_ids"][0]; first = self.invoke("barcodes.add", {"variant_id": vid, "source": "store"})["barcode"]; second = self.invoke("barcodes.add", {"variant_id": vid, "source": "store"})["barcode"]
        self.assertTrue(first.startswith("TL") and second.startswith("TL")); self.assertEqual(int(second[2:]) - int(first[2:]), 1)

    def test_manual_tl_barcode_rejected(self):
        result = self._create(); vid = result["variant_ids"][0]
        self.assert_application_error("validation_error", "barcodes.add", {"variant_id": vid, "barcode": "TL999999999", "source": "factory"})
        self.assert_application_error("validation_error", "products.create", {"name": "X", "category_id": self.cid, "variants": [{"attributes": {}, "barcodes": [{"barcode": "TL123", "source": "store"}]}]})

    def test_store_barcode_not_reused_after_delete(self):
        result = self._create(); vid = result["variant_ids"][0]; first = self.invoke("barcodes.add", {"variant_id": vid, "source": "store"})["barcode"]
        self.assertTrue(self.invoke("barcodes.delete", {"code": first})["ok"]); second = self.invoke("barcodes.add", {"variant_id": vid, "source": "store"})["barcode"]
        self.assertEqual(int(second[2:]), int(first[2:]) + 1)

    def test_add_barcode_unknown_variant_raises_not_found(self):
        self.assert_application_error("not_found", "barcodes.add", {"variant_id": 999999, "source": "store"})

    def test_variant_model_unknown_id_raises_validation_error(self):
        vid = self._create()["variant_ids"][0]; self.assert_application_error("validation_error", "variants.set_models", {"id": vid, "model_ids": [999999]})
