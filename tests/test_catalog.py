import unittest

from base import FacadeTestCase
from lib import product_data
from lib.db import get_conn


class TestCatalog(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.make_category_with_field("color", options=("blue", "red", "green", "black"))

    def _create(self):
        return self.invoke("products.create", {"name": "HODA glass", "category_id": self.cid, "variants": [
            {"attributes": {"color": "blue"}, "price": 590, "barcodes": [{"barcode": "FX100000001", "source": "factory"}]},
            {"attributes": {"color": "red"}, "price": 690, "barcodes": [{"barcode": "FX100000002", "source": "factory"}]},
        ]})

    def _create_search_fixture(self):
        brand_id = self.invoke("brands.create", {"name": "犀牛盾"})["brand_id"]
        phone_brand_id = self.create_phone_brand("Apple")
        model_17 = self.create_model(phone_brand_id, "iPhone 17 Pro")
        model_16 = self.create_model(phone_brand_id, "iPhone 16")
        feature_id = self.create_field("特性", self.cid, field_type="multi")
        self.create_options(feature_id, ("磁吸", "軍規", "支架"))
        result = self.invoke("products.create", {
            "name": "RhinoShield SolidSuit", "category_id": self.cid,
            "brand_id": brand_id, "variants": [
                {"attributes": {"color": "blue", "特性": ["磁吸", "軍規"]},
                 "model_ids": [model_17], "barcodes": [
                     {"barcode": "RS-FACTORY-17", "source": "factory"},
                     {"source": "store"}]},
                {"attributes": {"color": "red", "特性": ["支架"]},
                 "model_ids": [model_16], "barcodes": [
                     {"barcode": "RS-FACTORY-16", "source": "factory"}]},
            ]})
        product = self.invoke("catalog.list")[0]
        first_variant = next(
            variant for variant in product["variants"]
            if variant["variant_id"] == result["variant_ids"][0])
        store_barcode = next(
            barcode["barcode"] for barcode in first_variant["barcodes"]
            if barcode["source"] == "store")
        return result, model_17, store_barcode

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

    def test_editor_update_commits_fields_models_and_barcodes_together(self):
        result = self._create(); vid = result["variant_ids"][0]
        phone_brand = self.create_phone_brand("Apple")
        model_id = self.create_model(phone_brand, "iPhone 17")

        updated = self.invoke("variants.update_editor", {
            "id": vid,
            "fields": {"attributes": {"color": "purple"}, "price": 777},
            "model_ids": [model_id],
            "deleted_barcodes": ["FX100000001"],
            "factory_barcodes": ["  FACTORY-NEW  "],
            "store_barcode_count": 1,
        })

        variant = next(v for v in self.invoke("catalog.list")[0]["variants"]
                       if v["variant_id"] == vid)
        self.assertTrue(updated["ok"])
        self.assertEqual(variant["price"], 777)
        self.assertEqual(variant["attributes"], {"color": "purple"})
        self.assertEqual(variant["models"], ["iPhone 17"])
        self.assertEqual({b["barcode"] for b in variant["barcodes"]} - {"FACTORY-NEW"},
                         set(updated["store_barcodes"]))

    def test_editor_update_rejects_blank_and_reserved_factory_barcodes(self):
        result = self._create(); vid = result["variant_ids"][0]
        for code in ("   ", "  TL123  "):
            with self.subTest(code=code):
                self.assert_application_error("validation_error", "variants.update_editor", {
                    "id": vid, "fields": {"price": 777}, "model_ids": [],
                    "deleted_barcodes": ["FX100000001"],
                    "factory_barcodes": [code], "store_barcode_count": 0,
                })
                variant = next(v for v in self.invoke("catalog.list")[0]["variants"]
                               if v["variant_id"] == vid)
                self.assertEqual(variant["price"], 590)
                self.assertEqual(variant["barcodes"], [
                    {"barcode": "FX100000001", "source": "factory"}])

    def test_editor_update_rolls_back_every_change_when_late_barcode_fails(self):
        result = self._create(); vid = result["variant_ids"][0]

        self.assert_application_error("conflict", "variants.update_editor", {
            "id": vid,
            "fields": {"attributes": {"color": "green"}, "price": 777},
            "model_ids": [],
            "deleted_barcodes": ["FX100000001"],
            "factory_barcodes": ["FACTORY-NEW", "FX100000002"],
            "store_barcode_count": 1,
        })

        variant = next(v for v in self.invoke("catalog.list")[0]["variants"]
                       if v["variant_id"] == vid)
        self.assertEqual(variant["price"], 590)
        self.assertEqual(variant["attributes"], {"color": "blue"})
        self.assertEqual(variant["models"], [])
        self.assertEqual(variant["barcodes"], [
            {"barcode": "FX100000001", "source": "factory"}])

    def _inactive_editor_option_fixture(self):
        result = self._create(); vid = result["variant_ids"][0]
        multi_id = self.create_field("功能", self.cid, field_type="multi")
        tags_id = self.create_field("特性詞條", self.cid, field_type="tags")
        self.create_options(multi_id, ("磁吸", "支架"))
        self.create_options(tags_id, ("軍規",))
        with get_conn(self.db) as conn:
            conn.execute(
                "UPDATE AttributeOption SET active=0 WHERE "
                "(field_id=? AND value IN ('red','black')) OR "
                "(field_id=? AND value='磁吸') OR "
                "(field_id=? AND value='軍規')",
                (self.fid, multi_id, tags_id))
            conn.commit()
        return result, vid, multi_id, tags_id

    def test_editor_update_reactivates_only_selected_inactive_options(self):
        _, vid, multi_id, tags_id = self._inactive_editor_option_fixture()

        self.invoke("variants.update_editor", {
            "id": vid,
            "fields": {"attributes": {
                "color": "red", "功能": ["磁吸"], "特性詞條": ["軍規"]}},
            "model_ids": [], "deleted_barcodes": [],
            "factory_barcodes": [], "store_barcode_count": 0,
        })

        variant = next(v for v in self.invoke("catalog.list")[0]["variants"]
                       if v["variant_id"] == vid)
        self.assertEqual(variant["attributes"], {
            "color": "red", "功能": ["磁吸"], "特性詞條": ["軍規"]})
        with get_conn(self.db) as conn:
            states = {(row["field_id"], row["value"]): row["active"] for row in conn.execute(
                "SELECT field_id,value,active FROM AttributeOption WHERE "
                "field_id IN (?,?,?)", (self.fid, multi_id, tags_id))}
        self.assertEqual(states[(self.fid, "red")], 1)
        self.assertEqual(states[(multi_id, "磁吸")], 1)
        self.assertEqual(states[(tags_id, "軍規")], 1)
        self.assertEqual(states[(self.fid, "black")], 0)

    def test_editor_update_rolls_back_option_reactivation_when_barcode_fails(self):
        _, vid, multi_id, tags_id = self._inactive_editor_option_fixture()

        self.assert_application_error("conflict", "variants.update_editor", {
            "id": vid,
            "fields": {"attributes": {
                "color": "red", "功能": ["磁吸"], "特性詞條": ["軍規"]}},
            "model_ids": [], "deleted_barcodes": [],
            "factory_barcodes": ["FX100000002"], "store_barcode_count": 0,
        })

        variant = next(v for v in self.invoke("catalog.list")[0]["variants"]
                       if v["variant_id"] == vid)
        self.assertEqual(variant["attributes"], {"color": "blue"})
        with get_conn(self.db) as conn:
            states = {(row["field_id"], row["value"]): row["active"] for row in conn.execute(
                "SELECT field_id,value,active FROM AttributeOption WHERE "
                "field_id IN (?,?,?)", (self.fid, multi_id, tags_id))}
        self.assertEqual(states[(self.fid, "red")], 0)
        self.assertEqual(states[(multi_id, "磁吸")], 0)
        self.assertEqual(states[(tags_id, "軍規")], 0)

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

    def test_catalog_search_combines_shared_brand_with_same_variant_model(self):
        result, _, _ = self._create_search_fixture()

        rows = self.invoke("catalog.list", {"q": "犀牛盾 17 Pro"})

        self.assertEqual([v["variant_id"] for v in rows[0]["variants"]],
                         [result["variant_ids"][0]])

    def test_catalog_search_does_not_combine_terms_from_different_variants(self):
        self._create_search_fixture()

        self.assertEqual(self.invoke("catalog.list", {"q": "磁吸 支架"}), [])

    def test_catalog_searches_each_value_of_multi_attributes(self):
        result, _, _ = self._create_search_fixture()

        rows = self.invoke("catalog.list", {"q": "軍規"})

        self.assertEqual([v["variant_id"] for v in rows[0]["variants"]],
                         [result["variant_ids"][0]])

    def test_catalog_searches_variant_model_display_value(self):
        result, model_17, _ = self._create_search_fixture()
        self.invoke("models.update", {"id": model_17, "fields": {"alias": "17PM"}})

        rows = self.invoke("catalog.list", {"q": "17pm"})

        self.assertEqual(rows[0]["variants"][0]["variant_id"], result["variant_ids"][0])

    def test_catalog_searches_factory_and_store_barcodes_case_insensitively(self):
        result, _, store_barcode = self._create_search_fixture()

        for query in ("rs-factory-17", store_barcode.lower()):
            with self.subTest(query=query):
                rows = self.invoke("catalog.list", {"q": query})
                self.assertEqual([v["variant_id"] for v in rows[0]["variants"]],
                                 [result["variant_ids"][0]])

    def test_catalog_search_ignores_surrounding_repeated_spaces_and_case(self):
        result, _, _ = self._create_search_fixture()

        rows = self.invoke("catalog.list", {"q": "  RHINOSHIELD   17 pro  "})

        self.assertEqual([v["variant_id"] for v in rows[0]["variants"]],
                         [result["variant_ids"][0]])

    def test_catalog_searches_each_shared_product_field(self):
        result, _, _ = self._create_search_fixture()

        for query in ("SolidSuit", "鋼化玻璃", "犀牛盾"):
            with self.subTest(query=query):
                rows = self.invoke("catalog.list", {"q": query})
                self.assertCountEqual([v["variant_id"] for v in rows[0]["variants"]],
                                      result["variant_ids"])

    def test_catalog_search_removes_products_without_a_matching_variant(self):
        self._create_search_fixture()

        self.assertEqual(self.invoke("catalog.list", {"q": "not-in-catalog"}), [])

    def test_catalog_search_preserves_all_variants_when_terms_match_shared_fields(self):
        result, _, _ = self._create_search_fixture()

        rows = self.invoke("catalog.list", {"q": "RhinoShield 鋼化 犀牛盾"})

        self.assertCountEqual([v["variant_id"] for v in rows[0]["variants"]],
                              result["variant_ids"])

    def test_catalog_blank_search_is_equivalent_to_no_search(self):
        self._create_search_fixture()

        self.assertEqual(self.invoke("catalog.list", {"q": "      "}),
                         self.invoke("catalog.list"))

    def test_catalog_barcode_query_is_limited_to_candidate_variant_ids(self):
        result = self._create()
        inactive_id = result["variant_ids"][1]
        self.invoke("variants.update", {"id": inactive_id, "fields": {"active": 0}})
        trace = []
        conn = get_conn(self.db)
        self.addCleanup(conn.close)
        conn.set_trace_callback(trace.append)

        rows = product_data.catalog(conn)

        barcode_queries = [sql for sql in trace if " FROM Barcode " in sql]
        self.assertEqual(len(rows[0]["variants"]), 1)
        self.assertEqual(len(barcode_queries), 1)
        self.assertIn("WHERE variant_id IN", barcode_queries[0])
        self.assertNotIn(str(inactive_id), barcode_queries[0])

    def test_catalog_pending_barcode_query_excludes_variants_without_issues(self):
        result = self._create()
        pending_id, normal_id = result["variant_ids"]
        trace = []
        conn = get_conn(self.db)
        self.addCleanup(conn.close)
        conn.execute(
            "INSERT INTO VariantIssue(variant_id,issue_type) "
            "VALUES(?,'duplicate_signature')", (pending_id,))
        conn.commit()
        conn.set_trace_callback(trace.append)

        rows = product_data.catalog(conn, pending=True)

        barcode_queries = [sql for sql in trace if " FROM Barcode " in sql]
        self.assertEqual([v["variant_id"] for v in rows[0]["variants"]], [pending_id])
        self.assertEqual(len(barcode_queries), 1)
        self.assertIn(f"IN ({pending_id})", barcode_queries[0])
        self.assertNotIn(str(normal_id), barcode_queries[0])

    def test_catalog_does_not_query_barcodes_without_candidate_variants(self):
        self.invoke("products.create", {
            "name": "empty", "category_id": self.cid, "variants": []})
        trace = []
        conn = get_conn(self.db)
        self.addCleanup(conn.close)
        conn.set_trace_callback(trace.append)

        rows = product_data.catalog(conn)

        self.assertEqual(rows[0]["variants"], [])
        self.assertFalse(any(" FROM Barcode " in sql for sql in trace))


if __name__ == "__main__":
    unittest.main()
