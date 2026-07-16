import logging
from unittest.mock import patch

from base import ConnTestCase, make_client
from lib.db import get_conn
from lib.desktop_bridge import DesktopBridge
from lib.product_service import ProductFacade
from lib.application_errors import DatabaseError, InternalError


class TestProductLayers(ConnTestCase):
    def setUp(self):
        super().setUp()
        self.conn.close()
        self.facade = ProductFacade(self.db)
        self.bridge = DesktopBridge(logger=logging.getLogger(__name__), facade=self.facade)
        conn = get_conn(self.db)
        self.category_id = conn.execute(
            "INSERT INTO Category(name) VALUES(?)", ("測試種類",)
        ).lastrowid
        conn.commit()
        conn.close()

    def test_create_product_variant_and_scan_barcode(self):
        created = self.facade.invoke("products.create", {
            "name": "測試商品", "category_id": self.category_id,
            "default_price": 590,
            "variants": [{"attributes": {}, "barcodes": [{"barcode": "F001", "source": "factory"}]}],
        })
        hit = self.facade.invoke("barcodes.scan", {"code": "F001"})
        self.assertEqual(hit["variant_id"], created["variant_ids"][0])
        self.assertEqual(hit["price"], 590)

    def test_unknown_barcode_bridge_envelope_is_404(self):
        result = self.bridge.invoke("barcodes.scan", {"code": "NOPE"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "not_found")

    def test_malformed_payload_rejects_bool_as_integer(self):
        result = self.bridge.invoke("barcodes.add", {
            "variant_id": True, "source": "store",
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "validation_error")

    def test_catalog_and_search_are_available_through_facade(self):
        made = self.facade.invoke("products.create", {
            "name": "Alpha", "category_id": self.category_id,
            "default_price": 120, "variants": [{}],
        })
        rows = self.facade.invoke("products.list", {"q": "alp"})
        catalog = self.facade.invoke("catalog.list", {"q": "alp", "include_inactive": False})
        self.assertEqual(rows[0]["variant_id"], made["variant_ids"][0])
        self.assertEqual(catalog[0]["name"], "Alpha")

    def test_update_details_rolls_back_attributes_when_models_invalid(self):
        made = self.facade.invoke("products.create", {
            "name": "Atomic", "category_id": self.category_id, "variants": [{}],
        })
        vid = made["variant_ids"][0]
        result = self.bridge.invoke("variants.update_details", {
            "id": vid, "fields": {"price": 99, "attributes": {}},
            "model_ids": [999999],
        })
        self.assertFalse(result["ok"])
        conn = get_conn(self.db)
        self.assertIsNone(conn.execute("SELECT price FROM Variant WHERE variant_id=?", (vid,)).fetchone()["price"])
        conn.close()

    def test_delete_barcode_and_missing_barcode(self):
        made = self.facade.invoke("products.create", {
            "name": "Delete code", "category_id": self.category_id,
            "variants": [{"barcodes": [{"barcode": "DEL-1"}]}],
        })
        self.facade.invoke("barcodes.delete", {"code": "DEL-1"})
        missing = self.bridge.invoke("barcodes.scan", {"code": "DEL-1"})
        self.assertEqual(missing["error"]["code"], "not_found")
        self.assertEqual(made["variant_ids"], [1])

    def test_unknown_action_and_boolean_filters_are_rejected(self):
        self.assertEqual(self.bridge.invoke("products.list", {"category_id": True})["error"]["code"], "validation_error")
        self.assertEqual(self.bridge.invoke("products.nope", {})["error"]["code"], "validation_error")

    def test_store_barcode_sequence_rolls_back_when_later_barcode_conflicts(self):
        self.facade.invoke("products.create", {
            "name": "Seed", "category_id": self.category_id,
            "variants": [{"barcodes": [{"barcode": "DUP"}]}],
        })
        failed = self.bridge.invoke("products.create", {
            "name": "Rollback", "category_id": self.category_id,
            "variants": [{"barcodes": [{}, {"barcode": "DUP"}]}],
        })
        self.assertFalse(failed["ok"])
        made = self.facade.invoke("products.create", {
            "name": "After", "category_id": self.category_id,
            "variants": [{"barcodes": [{}]}],
        })
        conn = get_conn(self.db)
        code = conn.execute("SELECT barcode FROM Barcode WHERE variant_id=?", (made["variant_ids"][0],)).fetchone()[0]
        self.assertEqual(code, "TL100000001")
        self.assertIsNone(conn.execute("SELECT 1 FROM Product WHERE name='Rollback'").fetchone())
        conn.close()

    def test_delete_guards_and_missing_entities_have_stable_codes(self):
        made = self.facade.invoke("products.create", {
            "name": "Guard", "category_id": self.category_id, "variants": [{}],
        })
        vid = made["variant_ids"][0]
        conn = get_conn(self.db)
        conn.execute("INSERT INTO StockMovement(variant_id,qty,kind) VALUES(?,?,?)", (vid, 1, "purchase"))
        conn.commit(); conn.close()
        self.assertEqual(self.bridge.invoke("variants.delete", {"id": vid})["error"]["code"], "conflict")
        self.assertEqual(self.bridge.invoke("products.delete", {"id": made["product_id"]})["error"]["code"], "conflict")
        self.assertEqual(self.bridge.invoke("variants.delete", {"id": 999999})["error"]["code"], "not_found")
        self.assertEqual(self.bridge.invoke("products.delete", {"id": 999999})["error"]["code"], "not_found")
        self.assertEqual(self.bridge.invoke("barcodes.delete", {"code": "missing"})["error"]["code"], "not_found")

    def test_nested_unknown_fields_and_boolean_ids_do_not_write(self):
        bad = self.bridge.invoke("products.create", {
            "name": "Bad", "category_id": self.category_id,
            "variants": [{"model_ids": [True], "unknown": 1}],
        })
        self.assertEqual(bad["error"]["code"], "validation_error")
        conn = get_conn(self.db)
        self.assertIsNone(conn.execute("SELECT 1 FROM Product WHERE name='Bad'").fetchone())
        conn.close()

    def test_manual_tl_is_validation_error(self):
        made = self.facade.invoke("products.create", {
            "name": "TL", "category_id": self.category_id, "variants": [{}],
        })
        result = self.bridge.invoke("barcodes.add", {
            "variant_id": made["variant_ids"][0], "barcode": "TL123", "source": "store",
        })
        self.assertEqual(result["error"]["code"], "validation_error")

    def test_http_500_masks_internal_and_database_messages(self):
        for error in (InternalError("SQL SECRET internal"), DatabaseError("SQL SECRET database")):
            with self.subTest(error=type(error).__name__):
                with patch("api.products.ProductFacade") as facade_type:
                    facade_type.return_value.invoke.side_effect = error
                    response = make_client(self.db).get("/api/products")
                self.assertEqual(response.status_code, 500)
                self.assertNotIn("SQL SECRET", response.text)
