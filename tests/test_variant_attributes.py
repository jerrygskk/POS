import unittest
from base import FacadeTestCase


class TestVariantAttributes(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.make_category_with_field("規格", options=("亮面", "霧面"))
        desc = next(f["field_id"] for f in self.invoke("fields.list", {"common": 1})
                    if f["name"] == "商品描述")
        self.invoke("categories.set_common_fields", {"id": self.cid, "field_ids": [desc]})

    def _opt_id(self, value):
        return next(o["option_id"] for o in self.invoke("options.list", {"field_id": self.fid}) if o["value"] == value)

    def _create(self, attrs):
        return self.create_product(attrs)

    def test_select_roundtrip(self):
        self._create({"規格": "亮面"})
        self.assertEqual(self.invoke("barcodes.scan", {"code": "B1"})["attributes"]["規格"], "亮面")
        self.assertEqual(self.invoke("catalog.list")[0]["variants"][0]["attributes"]["規格"], "亮面")

    def test_text_roundtrip(self):
        self._create({"商品描述": "限量款"})
        self.assertEqual(self.invoke("barcodes.scan", {"code": "B1"})["attributes"]["商品描述"], "限量款")

    def test_unknown_option_422(self):
        self.assert_application_error("validation_error", "products.create", {"name": "膜", "category_id": self.cid, "variants": [{"attributes": {"規格": "不存在的值"}, "barcodes": []}]})

    def test_unknown_field_422(self):
        self.assert_application_error("validation_error", "products.create", {"name": "膜", "category_id": self.cid, "variants": [{"attributes": {"沒這欄": "x"}, "barcodes": []}]})

    def test_rename_option_takes_effect(self):
        self._create({"規格": "亮面"})
        self.invoke("options.update", {"id": self._opt_id("亮面"), "fields": {"value": "超亮面"}})
        self.assertEqual(self.invoke("barcodes.scan", {"code": "B1"})["attributes"]["規格"], "超亮面")

    def test_rename_field_takes_effect(self):
        self._create({"規格": "亮面"})
        self.invoke("fields.update", {"id": self.fid, "fields": {"name": "面料"}})
        got = self.invoke("barcodes.scan", {"code": "B1"})["attributes"]
        self.assertIn("面料", got); self.assertNotIn("規格", got); self.assertEqual(got["面料"], "亮面")

    def test_delete_referenced_option_preserves_existing_attribute(self):
        self._create({"規格": "亮面"})
        self.invoke("options.delete", {"id": self._opt_id("亮面")})
        self.assertEqual(self.invoke("barcodes.scan", {"code": "B1"})["attributes"]["規格"], "亮面")
        self.assertNotIn("亮面", [o["value"] for o in self.invoke("options.list", {"field_id": self.fid})])
        self.invoke("options.delete", {"id": self._opt_id("霧面")})

    def test_patch_variant_attributes(self):
        vid = self._create({"規格": "亮面"})["variant_ids"][0]
        self.invoke("variants.update", {"id": vid, "fields": {"attributes": {"規格": "霧面"}}})
        self.assertEqual(self.invoke("barcodes.scan", {"code": "B1"})["attributes"]["規格"], "霧面")
        self.invoke("variants.update", {"id": vid, "fields": {"attributes": {}}})
        self.assertEqual(self.invoke("barcodes.scan", {"code": "B1"})["attributes"], {})


class TestOptionModel(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.cid = self.create_category("手機殼")
        self.fid = self.create_field("顏色", self.cid)
        ip = self.create_phone_brand("iPhone")
        self.m15, self.m16 = self.create_model(ip, "15"), self.create_model(ip, "16")
        self.o_black, self.o_special, self.o_shared = (self._add_opt(v) for v in ("黑", "限定色", "共用色"))
        self.invoke("options.set_models", {"id": self.o_special, "model_ids": [self.m15]})
        self.invoke("options.set_models", {"id": self.o_shared, "model_ids": [self.m15, self.m16]})

    def _add_opt(self, value):
        self.invoke("options.create", {"field_id": self.fid, "value": value})
        return next(o["option_id"] for o in self.invoke(
            "options.list", {"field_id": self.fid}) if o["value"] == value)

    def _values(self, model_ids=None):
        return {o["value"] for o in self.invoke("options.list", {"field_id": self.fid, "model_ids": model_ids or []})}

    def test_no_filter_returns_all(self): self.assertEqual(self._values(), {"黑", "限定色", "共用色"})
    def test_filter_model_15(self): self.assertEqual(self._values([self.m15]), {"黑", "限定色", "共用色"})
    def test_filter_model_16(self): self.assertEqual(self._values([self.m16]), {"黑", "共用色"})
    def test_filter_union(self): self.assertEqual(self._values([self.m15, self.m16]), {"黑", "限定色", "共用色"})

    def test_get_set_option_models(self):
        self.assertEqual(self.invoke("options.models", {"id": self.o_special})["model_ids"], [self.m15])
        self.invoke("options.set_models", {"id": self.o_special, "model_ids": [self.m16]})
        self.assertEqual(self.invoke("options.models", {"id": self.o_special})["model_ids"], [self.m16])
        self.invoke("options.set_models", {"id": self.o_special, "model_ids": []})
        self.assertEqual(self.invoke("options.models", {"id": self.o_special})["model_ids"], [])
        self.assertIn("限定色", self._values([self.m16]))

    def test_list_options_inline_model_ids(self):
        opts = {o["value"]: o["model_ids"] for o in self.invoke("options.list", {"field_id": self.fid})}
        self.assertEqual(opts["黑"], []); self.assertEqual(set(opts["共用色"]), {self.m15, self.m16})


if __name__ == "__main__":
    unittest.main()
