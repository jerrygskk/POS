"""Product-settings desktop action tests."""
import unittest

from base import FacadeTestCase
from lib.application_errors import ConflictError, ValidationError
from lib.db import get_conn


class TestCategoryModelModeAndSwitch(FacadeTestCase):
    def test_model_mode_read_write(self):
        category_id = self.invoke("categories.create", {
            "name": "case", "model_mode": "required",
        })["category_id"]
        row = next(item for item in self.invoke("categories.list", {})
                   if item["category_id"] == category_id)
        self.assertEqual(row["model_mode"], "required")
        self.invoke("categories.update", {"id": category_id, "fields": {"model_mode": "hidden"}})
        row = next(item for item in self.invoke("categories.list", {})
                   if item["category_id"] == category_id)
        self.assertEqual(row["model_mode"], "hidden")

    def test_model_mode_defaults_hidden_and_rejects_bad_value(self):
        category_id = self.create_category("cable")
        row = next(item for item in self.invoke("categories.list", {})
                   if item["category_id"] == category_id)
        self.assertEqual(row["model_mode"], "hidden")
        with self.assertRaises(ValidationError) as raised:
            self.invoke("categories.create", {"name": "bad", "model_mode": "bogus"})
        self.assertEqual(raised.exception.code, "validation_error")

    def test_category_disable_does_not_rewrite_lower_active(self):
        self.make_category_with_field("finish", options=("gloss",))
        created = self.create_product({"finish": "gloss"})
        product_id, variant_id = created["product_id"], created["variant_ids"][0]
        self.invoke("categories.update", {"id": self.cid, "fields": {"active": 0}})
        with get_conn(self.db) as conn:
            self.assertEqual(conn.execute("SELECT active FROM Product WHERE product_id=?", (product_id,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT active FROM Variant WHERE variant_id=?", (variant_id,)).fetchone()[0], 1)
        self.assertFalse(self.invoke("barcodes.scan", {"code": "B1"})["active"])
        self.invoke("categories.update", {"id": self.cid, "fields": {"active": 1}})
        self.assertTrue(self.invoke("barcodes.scan", {"code": "B1"})["active"])


class TestTemplateFields(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.cid = self.create_category("template")
        self.fid = self.create_field("size", self.cid)
        self.create_options(self.fid, ("full", "nine-tenths"))
        self.oid = next(item["option_id"] for item in self.invoke("options.list", {"field_id": self.fid})
                        if item["value"] == "full")

    def test_sets_field_template_crud_values(self):
        self.assertEqual(self.invoke("categories.set_field", {
            "category_id": self.cid, "field_id": self.fid,
            "fields": {"sort": 3, "required": 1, "default_option_id": self.oid},
        }), {"ok": True})
        with get_conn(self.db) as conn:
            row = conn.execute("SELECT sort,required,default_option_id,active FROM CategoryField WHERE category_id=? AND field_id=?", (self.cid, self.fid)).fetchone()
        self.assertEqual(tuple(row), (3, 1, self.oid, 1))
        field = next(item for item in self.invoke("categories.fields", {"id": self.cid})
                     if item["field_id"] == self.fid)
        self.assertEqual(field["required"], 1)
        self.assertEqual(field["default_value"], "full")

    def test_set_field_default_must_belong_to_field(self):
        other_field_id = self.create_field("material", self.cid)
        self.create_options(other_field_id, ("glass",))
        other_option_id = self.invoke("options.list", {"field_id": other_field_id})[0]["option_id"]
        with self.assertRaises(ValidationError) as raised:
            self.invoke("categories.set_field", {
                "category_id": self.cid, "field_id": self.fid,
                "fields": {"default_option_id": other_option_id},
            })
        self.assertEqual(raised.exception.code, "validation_error")

    def test_feature_field_cannot_be_disabled(self):
        feature_id = self.create_field("特性詞條", self.cid, field_type="tags")
        for action, payload in (
            ("categories.set_field", {"category_id": self.cid, "field_id": feature_id, "fields": {"active": 0}}),
            ("fields.update", {"id": feature_id, "fields": {"active": 0}}),
        ):
            with self.subTest(action=action):
                with self.assertRaises(ValidationError) as raised:
                    self.invoke(action, payload)
                self.assertEqual(raised.exception.code, "validation_error")

    def test_required_is_locked_when_category_has_variant(self):
        self.invoke("categories.set_field", {"category_id": self.cid, "field_id": self.fid, "fields": {"required": 1}})
        self.invoke("products.create", {"name": "product", "category_id": self.cid,
                                         "variants": [{"attributes": {"size": "full"}, "barcodes": []}]})
        with self.assertRaises(ValidationError) as raised:
            self.invoke("categories.set_field", {"category_id": self.cid, "field_id": self.fid, "fields": {"required": 0}})
        self.assertEqual(raised.exception.code, "validation_error")
        self.assertEqual(self.invoke("categories.set_field", {"category_id": self.cid, "field_id": self.fid, "fields": {"required": 1}}), {"ok": True})

    def test_field_type_is_locked_when_used(self):
        self.invoke("products.create", {"name": "product", "category_id": self.cid,
                                         "variants": [{"attributes": {"size": "full"}, "barcodes": []}]})
        with self.assertRaises(ValidationError) as raised:
            self.invoke("fields.update", {"id": self.fid, "fields": {"field_type": "multi"}})
        self.assertEqual(raised.exception.code, "validation_error")

    def test_deactivating_default_option_clears_default(self):
        self.invoke("categories.set_field", {"category_id": self.cid, "field_id": self.fid,
                                              "fields": {"default_option_id": self.oid}})
        self.invoke("options.update", {"id": self.oid, "fields": {"active": 0}})
        with get_conn(self.db) as conn:
            default_id = conn.execute("SELECT default_option_id FROM CategoryField WHERE category_id=? AND field_id=?", (self.cid, self.fid)).fetchone()[0]
        self.assertIsNone(default_id)


class TestProductAndBrand(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.cid = self.create_category("template")

    def test_same_category_normalized_name_is_rejected(self):
        self.invoke("products.create", {"name": "HODA case", "category_id": self.cid, "variants": []})
        with self.assertRaises(ConflictError) as raised:
            self.invoke("products.create", {"name": "hoda case", "category_id": self.cid, "variants": []})
        self.assertEqual(raised.exception.code, "conflict")
        other_category_id = self.create_category("other")
        created = self.invoke("products.create", {
            "name": "HODA case", "category_id": other_category_id, "variants": [],
        })
        self.assertIn("product_id", created)
        self.assertEqual(created["variant_ids"], [])

    def test_product_create_builds_brand_category(self):
        brand_id = self.invoke("brands.create", {"name": "HODA"})["brand_id"]
        self.invoke("products.create", {"name": "product", "category_id": self.cid, "brand_id": brand_id, "variants": []})
        with get_conn(self.db) as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM BrandCategory WHERE brand_id=? AND category_id=?", (brand_id, self.cid)).fetchone())

    def test_product_brand_name_reuses_and_creates_brands(self):
        brand_id = self.invoke("brands.create", {"name": "HODA"})["brand_id"]
        self.invoke("products.create", {"name": "first", "category_id": self.cid, "brand_name": "hoda", "variants": []})
        with get_conn(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM Brand WHERE name='HODA'").fetchone()[0], 1)
            product_id = conn.execute("SELECT product_id FROM Product WHERE name='first'").fetchone()[0]
            self.assertEqual(conn.execute("SELECT brand_id FROM Product WHERE product_id=?", (product_id,)).fetchone()[0], brand_id)
        self.invoke("products.create", {"name": "second", "category_id": self.cid, "brand_name": "new brand", "variants": []})
        with get_conn(self.db) as conn:
            new_brand_id = conn.execute("SELECT brand_id FROM Brand WHERE name='new brand'").fetchone()[0]
            self.assertIsNotNone(conn.execute("SELECT 1 FROM BrandCategory WHERE brand_id=? AND category_id=?", (new_brand_id, self.cid)).fetchone())

    def test_referenced_brand_delete_conflicts(self):
        brand_id = self.invoke("brands.create", {"name": "HODA"})["brand_id"]
        self.invoke("products.create", {"name": "product", "category_id": self.cid, "brand_id": brand_id, "variants": []})
        with self.assertRaises(ConflictError) as raised:
            self.invoke("brands.delete", {"id": brand_id})
        self.assertEqual(raised.exception.code, "conflict")


class TestDisabledValueDiff(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.make_category_with_field("finish", options=("gloss", "matte"))
        created = self.create_product({"finish": "gloss"}, barcode="B1")
        self.pid, self.v0 = created["product_id"], created["variant_ids"][0]
        self.bright = next(item["option_id"] for item in self.invoke("options.list", {"field_id": self.fid}) if item["value"] == "gloss")
        self.invoke("options.delete", {"id": self.bright})

    def test_keeps_existing_disabled_value(self):
        self.assertEqual(self.invoke("variants.update", {"id": self.v0, "fields": {"attributes": {"finish": "gloss"}}}), {"ok": True})
        self.assertEqual(self.invoke("barcodes.scan", {"code": "B1"})["attributes"]["finish"], "gloss")

    def test_changes_disabled_value_to_enabled_value(self):
        self.assertEqual(self.invoke("variants.update", {"id": self.v0, "fields": {"attributes": {"finish": "matte"}}}), {"ok": True})
        self.assertEqual(self.invoke("barcodes.scan", {"code": "B1"})["attributes"]["finish"], "matte")

    def test_new_variant_with_disabled_value_is_rejected(self):
        with self.assertRaises(ValidationError) as raised:
            self.invoke("variants.create", {"product_id": self.pid, "fields": {"attributes": {"finish": "gloss"}, "barcodes": []}})
        self.assertEqual(raised.exception.code, "validation_error")

    def test_update_to_disabled_value_not_originally_present_is_rejected(self):
        variant_id = self.invoke("variants.create", {"product_id": self.pid, "fields": {"attributes": {"finish": "matte"}, "barcodes": []}})["variant_id"]
        with self.assertRaises(ValidationError) as raised:
            self.invoke("variants.update", {"id": variant_id, "fields": {"attributes": {"finish": "gloss"}}})
        self.assertEqual(raised.exception.code, "validation_error")

    def test_multi_value_cannot_add_disabled_value(self):
        multi_field_id = self.create_field("material", self.cid, field_type="multi")
        self.create_options(multi_field_id, ("A", "B", "C"))
        keep = self.invoke("variants.create", {"product_id": self.pid, "fields": {"attributes": {"material": ["A", "B"]}, "barcodes": []}})["variant_id"]
        self.invoke("variants.create", {"product_id": self.pid, "fields": {"attributes": {"material": ["C"]}, "barcodes": []}})
        option_id = next(item["option_id"] for item in self.invoke("options.list", {"field_id": multi_field_id}) if item["value"] == "C")
        self.invoke("options.delete", {"id": option_id})
        with self.assertRaises(ValidationError) as raised:
            self.invoke("variants.update", {"id": keep, "fields": {"attributes": {"material": ["A", "B", "C"]}}})
        self.assertEqual(raised.exception.code, "validation_error")
        self.assertEqual(self.invoke("variants.update", {"id": keep, "fields": {"attributes": {"material": ["A", "B"]}}}), {"ok": True})


class TestEffectiveActiveEntrypoints(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.make_category_with_field("finish", options=("gloss",))
        created = self.create_product({"finish": "gloss"}, name="HODA case", barcode="B1")
        self.pid, self.v0 = created["product_id"], created["variant_ids"][0]
        self.invoke("stock.receive", {"variant_id": self.v0, "qty": 10})

    def _disable_category(self):
        self.invoke("categories.update", {"id": self.cid, "fields": {"active": 0}})

    def test_scan_reflects_category_active(self):
        self._disable_category()
        self.assertFalse(self.invoke("barcodes.scan", {"code": "B1"})["active"])

    def test_search_excludes_inactive_category(self):
        self._disable_category()
        self.assertEqual(self.invoke("products.list", {"q": "HODA"}), [])

    def test_catalog_excludes_inactive_category(self):
        self._disable_category()
        self.assertEqual(self.invoke("catalog.list", {}), [])
        self.assertEqual(len(self.invoke("catalog.list", {"include_inactive": True})), 1)

    def test_sale_is_rejected_when_category_inactive(self):
        self._disable_category()
        with self.assertRaises(ValidationError) as raised:
            self.invoke("sales.checkout", {"payment": "cash", "paid": 1000,
                                             "items": [{"variant_id": self.v0, "qty": 1, "unit_price": 100}]})
        self.assertEqual(raised.exception.code, "validation_error")
        self.assertEqual(self.invoke("stock.detail", {"variant_id": self.v0})["stock"], 10)

    def test_child_creation_requires_active_category_and_product(self):
        self._disable_category()
        payload = {"product_id": self.pid, "fields": {"attributes": {}, "barcodes": []}}
        with self.assertRaises(ValidationError) as raised:
            self.invoke("variants.create", payload)
        self.assertEqual(raised.exception.code, "validation_error")
        self.invoke("categories.update", {"id": self.cid, "fields": {"active": 1}})
        self.invoke("products.update", {"id": self.pid, "fields": {"active": 0}})
        with self.assertRaises(ValidationError) as raised:
            self.invoke("variants.create", payload)
        self.assertEqual(raised.exception.code, "validation_error")


class TestDeleteEmptyCategoryCascade(FacadeTestCase):
    def test_delete_empty_category_cascades_only_its_data(self):
        category_id = self.create_category("template")
        specific_field_id = self.create_field("size", category_id)
        self.create_options(specific_field_id, ("full",))
        common_field_id = next(item["field_id"] for item in self.invoke("fields.list", {"common": 1}) if item["name"] == "顏色")
        other_category_id = self.create_category("other")
        self.invoke("categories.set_common_fields", {"id": other_category_id, "field_ids": [common_field_id]})
        self.invoke("categories.set_common_fields", {"id": category_id, "field_ids": [common_field_id]})
        brand_id = self.invoke("brands.create", {"name": "HODA"})["brand_id"]
        self.invoke("brands.set_categories", {"id": brand_id, "category_ids": [category_id]})

        self.assertEqual(self.invoke("categories.delete", {"id": category_id}), {"ok": True})
        with get_conn(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM CategoryField WHERE category_id=?", (category_id,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM BrandCategory WHERE category_id=?", (category_id,)).fetchone()[0], 0)
            self.assertIsNone(conn.execute("SELECT 1 FROM AttributeField WHERE field_id=?", (specific_field_id,)).fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM AttributeOption WHERE field_id=?", (specific_field_id,)).fetchone()[0], 0)
            self.assertIsNotNone(conn.execute("SELECT 1 FROM AttributeField WHERE field_id=?", (common_field_id,)).fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM CategoryField WHERE category_id=? AND field_id=?", (other_category_id, common_field_id)).fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM Brand WHERE brand_id=?", (brand_id,)).fetchone())


class TestFeatureFieldBindingPreserved(FacadeTestCase):
    def test_set_common_fields_keeps_feature_binding(self):
        category_id = self.create_category("template")
        feature_id = self.create_field("特性詞條", category_id, field_type="tags")
        other_category_id = self.create_category("other")
        self.invoke("categories.set_field", {"category_id": other_category_id, "field_id": feature_id, "fields": {"active": 1}})
        self.invoke("categories.set_field", {"category_id": other_category_id, "field_id": feature_id, "fields": {"sort": 0}})
        self.assertEqual(self.invoke("categories.set_common_fields", {"id": category_id, "field_ids": []}), {"ok": True})
        with get_conn(self.db) as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM CategoryField WHERE category_id=? AND field_id=?", (category_id, feature_id)).fetchone())
        names = [item["name"] for item in self.invoke("categories.fields", {"id": category_id})]
        self.assertIn("特性詞條", names)


if __name__ == "__main__":
    unittest.main()
