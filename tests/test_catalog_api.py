import unittest

from base import FacadeTestCase


class TestCatalogApi(FacadeTestCase):
    def _add_phone_brand(self, name):
        return self.invoke("phone_brands.create", {"name": name})["phone_brand_id"]

    def _product(self, category_id, **fields):
        payload = {"name": "product", "category_id": category_id, "variants": [{"attributes": {}, "barcodes": []}]}
        payload.update(fields)
        return self.invoke("products.create", payload)

    def test_category_crud(self):
        cid = self.create_category("old"); self.assertIn("old", [x["name"] for x in self.invoke("categories.list")])
        self.invoke("categories.update", {"id": cid, "fields": {"name": "new"}}); self.assertIn("new", [x["name"] for x in self.invoke("categories.list")])

    def test_category_inactive_hidden_from_list(self):
        cid = self.create_category("hidden"); self.invoke("categories.update", {"id": cid, "fields": {"active": 0}})
        self.assertNotIn(cid, [x["category_id"] for x in self.invoke("categories.list")]); self.assertIn(cid, [x["category_id"] for x in self.invoke("categories.list", {"all": True})])

    def test_category_delete_with_product_raises_conflict(self):
        cid = self.create_category("cat"); self._product(cid); self.assert_application_error("conflict", "categories.delete", {"id": cid})

    def test_category_delete_clean(self):
        cid = self.create_category("clean"); self.assertTrue(self.invoke("categories.delete", {"id": cid})["ok"]); self.assertNotIn(cid, [x["category_id"] for x in self.invoke("categories.list", {"all": True})])

    def test_category_delete_with_default_option(self):
        cid = self.create_category("defaults"); fid = self.create_field("size", cid); self.invoke("options.create", {"field_id": fid, "value": "large"})
        oid = self.invoke("options.list", {"field_id": fid})[0]["option_id"]
        self.invoke("fields.update", {"id": fid, "fields": {"default_option_id": oid}}); self.assertTrue(self.invoke("categories.delete", {"id": cid})["ok"])
        self.assertNotIn(cid, [x["category_id"] for x in self.invoke("categories.list", {"all": True})])

    def test_build_with_inactive_category_raises_validation_error(self):
        cid = self.create_category("inactive"); self.invoke("categories.update", {"id": cid, "fields": {"active": 0}})
        self.assert_application_error("validation_error", "products.create", {"name": "X", "category_id": cid, "variants": [{"attributes": {}, "barcodes": []}]})

    def test_build_with_missing_category_raises_validation_error(self):
        self.assert_application_error("validation_error", "products.create", {"name": "X", "category_id": 999, "variants": [{"attributes": {}, "barcodes": []}]})

    def test_brand_crud_and_delete_raises_conflict(self):
        bid = self.invoke("brands.create", {"name": "HODA"})["brand_id"]; cid = self.create_category("cat"); self._product(cid, brand_id=bid)
        self.assert_application_error("conflict", "brands.delete", {"id": bid})

    def test_brand_filter_by_category(self):
        b1 = self.invoke("brands.create", {"name": "HODA"})["brand_id"]; b2 = self.invoke("brands.create", {"name": "other"})["brand_id"]
        glass = self.create_category("glass"); case = self.create_category("case")
        self.invoke("brands.set_categories", {"id": b1, "category_ids": [glass]}); self.invoke("brands.set_categories", {"id": b2, "category_ids": [case]})
        self.assertEqual([x["name"] for x in self.invoke("brands.list", {"category_id": glass})], ["HODA"]); self.assertEqual([x["name"] for x in self.invoke("brands.list", {"category_id": case})], ["other"])

    def test_brand_zero_ref_hard_delete_clears_brandcategory(self):
        bid = self.invoke("brands.create", {"name": "orphan"})["brand_id"]; cid = self.create_category("cat")
        self.invoke("brands.set_categories", {"id": bid, "category_ids": [cid]}); self.assertTrue(self.invoke("brands.delete", {"id": bid})["ok"]); self.assertEqual(self.invoke("brands.list", {"category_id": cid}), [])

    def test_phone_brand_crud(self):
        pbid = self._add_phone_brand("iPhone"); self.assertIn("iPhone", [x["name"] for x in self.invoke("phone_brands.list")])
        self.invoke("phone_brands.update", {"id": pbid, "fields": {"name": "Apple"}}); self.assertIn("Apple", [x["name"] for x in self.invoke("phone_brands.list")])

    def test_phone_brand_inactive_hidden_and_models_dropped(self):
        pbid = self._add_phone_brand("iPhone"); self.create_model(pbid, "15"); self.invoke("phone_brands.update", {"id": pbid, "fields": {"active": 0}})
        self.assertNotIn(pbid, [x["phone_brand_id"] for x in self.invoke("phone_brands.list")]); self.assertIn(pbid, [x["phone_brand_id"] for x in self.invoke("phone_brands.list", {"all": True})])
        self.assertEqual(self.invoke("models.list"), []); self.assertEqual(len(self.invoke("models.list", {"all": True})), 1)

    def test_phone_brand_delete_with_model_raises_conflict(self):
        pbid = self._add_phone_brand("iPhone"); self.create_model(pbid, "15"); self.assert_application_error("conflict", "phone_brands.delete", {"id": pbid})

    def test_phone_brand_delete_clean(self):
        pbid = self._add_phone_brand("clean"); self.assertTrue(self.invoke("phone_brands.delete", {"id": pbid})["ok"])

    def test_model_crud_and_brand_filter(self):
        ip = self._add_phone_brand("iPhone"); samsung = self._add_phone_brand("SAMSUNG"); mid = self.create_model(ip, "15"); self.create_model(samsung, "S24")
        rows = self.invoke("models.list", {"phone_brand_id": ip}); self.assertEqual((len(rows), rows[0]["model_id"], rows[0]["brand_name"]), (1, mid, "iPhone"))

    def test_model_alias_roundtrip_and_display(self):
        cid = self.create_category("case"); pbid = self._add_phone_brand("iPhone"); mid = self.invoke("models.create", {"phone_brand_id": pbid, "name": "iPhone 17 Pro Max", "alias": "17PM"})["model_id"]
        self.assertEqual(self.invoke("models.list")[0]["alias"], "17PM"); self.invoke("products.create", {"name": "case", "category_id": cid, "variants": [{"attributes": {}, "model_ids": [mid], "barcodes": [{"barcode": "BA1", "source": "store"}]}]})
        self.assertEqual(self.invoke("catalog.list")[0]["variants"][0]["models"], ["17PM"]); self.invoke("models.update", {"id": mid, "fields": {"alias": None}}); self.assertEqual(self.invoke("catalog.list")[0]["variants"][0]["models"], ["iPhone 17 Pro Max"])

    def test_model_series_roundtrip(self):
        pbid = self._add_phone_brand("iPhone"); mid = self.invoke("models.create", {"phone_brand_id": pbid, "name": "17 Pro Max", "series": "17"})["model_id"]
        self.assertEqual(self.invoke("models.list")[0]["series"], "17"); self.invoke("models.update", {"id": mid, "fields": {"series": "17 Pro"}}); self.assertEqual(self.invoke("models.list")[0]["series"], "17 Pro")
        self.invoke("models.update", {"id": mid, "fields": {"series": "  "}}); self.assertIsNone(self.invoke("models.list")[0]["series"]); mid2 = self.create_model(pbid, "16")
        self.assertIsNone(next(row for row in self.invoke("models.list") if row["model_id"] == mid2)["series"])

    def test_model_add_with_missing_brand_raises_validation_error(self):
        self.assert_application_error("validation_error", "models.create", {"phone_brand_id": 999, "name": "15"})

    def test_model_delete_with_variant_raises_conflict(self):
        cid = self.create_category("case"); pbid = self._add_phone_brand("iPhone"); mid = self.create_model(pbid, "15"); self.invoke("products.create", {"name": "case", "category_id": cid, "variants": [{"attributes": {}, "model_ids": [mid], "barcodes": []}]})
        self.assert_application_error("conflict", "models.delete", {"id": mid})

    def test_model_delete_with_option_binding_raises_conflict(self):
        cid = self.create_category("case"); pbid = self._add_phone_brand("iPhone"); mid = self.create_model(pbid, "15"); fid = self.create_field("size", cid); self.invoke("options.create", {"field_id": fid, "value": "one"})
        oid = self.invoke("options.list", {"field_id": fid})[0]["option_id"]; self.invoke("options.set_models", {"id": oid, "model_ids": [mid]}); self.assert_application_error("conflict", "models.delete", {"id": mid})

    def test_category_fields_merge(self):
        cid = self.create_category("glass"); fid = self.create_field("size", cid); self.invoke("options.create", {"field_id": fid, "value": "large"}); common = self.invoke("fields.list", {"common": True})[0]["field_id"]
        other = self.create_category("case"); self.invoke("categories.set_common_fields", {"id": other, "field_ids": [common]}); self.invoke("categories.set_common_fields", {"id": cid, "field_ids": [common]})
        fields = self.invoke("categories.fields", {"id": cid}); own = next(field for field in fields if field["field_id"] == fid); shared = next(field for field in fields if field["field_id"] == common)
        self.assertEqual([option["value"] for option in own["options"]], ["large"]); self.assertFalse(own["shared"]); self.assertTrue(shared["shared"])

    def test_category_common_fields_unknown_field_raises_validation_error(self):
        cid = self.create_category("validation"); self.assert_application_error("validation_error", "categories.set_common_fields", {"id": cid, "field_ids": [999999]})

    def test_variant_model_filter(self):
        cid = self.create_category("case"); ip = self._add_phone_brand("iPhone"); m15 = self.create_model(ip, "15"); m16 = self.create_model(ip, "16")
        fid = self.create_field("color", cid); self.invoke("options.create", {"field_id": fid, "value": "black"}); self.invoke("options.create", {"field_id": fid, "value": "white"})
        result = self.invoke("products.create", {"name": "case", "category_id": cid, "variants": [{"attributes": {"color": "black"}, "model_ids": [m15, m16], "barcodes": [{"barcode": "C1", "source": "store"}]}, {"attributes": {"color": "white"}, "model_ids": [m16], "barcodes": [{"barcode": "C2", "source": "store"}]}]})
        vid = result["variant_ids"][0]; catalog = self.invoke("catalog.list", {"model_id": m15}); self.assertEqual((len(catalog), len(catalog[0]["variants"]), catalog[0]["variants"][0]["variant_id"]), (1, 1, vid)); self.assertEqual([row["variant_id"] for row in self.invoke("products.list", {"model_id": m15})], [vid])
        self.invoke("variants.set_models", {"id": vid, "model_ids": [m16]}); self.assertEqual(self.invoke("catalog.list", {"model_id": m15}), [])

    def test_catalog_returns_names(self):
        cid = self.create_category("glass"); bid = self.invoke("brands.create", {"name": "HODA"})["brand_id"]; self._product(cid, brand_id=bid)
        product = self.invoke("catalog.list")[0]; self.assertEqual((product["category_name"], product["brand_name"]), ("glass", "HODA"))


if __name__ == "__main__":
    unittest.main()
