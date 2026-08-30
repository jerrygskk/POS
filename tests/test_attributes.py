import unittest
from lib import product_rules
from lib.db import get_conn
from base import FacadeTestCase

class TestAttributes(FacadeTestCase):
    def test_seed_creates_no_shared_fields(self):
        # 規格欄一律由各種類自己建,種子不再留任何共用欄
        self.assertEqual(self.invoke("fields.list"), [])

    def test_rename_field(self):
        fid = self.create_field("商品描述")
        self.invoke("fields.update", {"id": fid, "fields": {"name": "描述"}})
        self.assertIn("描述", [f["name"] for f in self.invoke("fields.list")])

    def test_category_specific_field(self):
        cid = self.create_category("鋼化玻璃")
        self.create_field("版型", cid)
        # ?category_id 只回該種類專屬欄
        got = self.invoke("fields.list", {"category_id": cid})
        self.assertEqual([f["name"] for f in got], ["版型"])
        # ?common=1 只回共用欄(category_id NULL)
        common = self.invoke("fields.list", {"common": 1})
        self.assertTrue(all(f["category_id"] is None for f in common))
        self.assertNotIn("版型", [f["name"] for f in common])

    def test_options_by_field(self):
        fid = self.create_field("版型")
        self.create_options(fid, ("亮面", "霧面"))
        vals = [o["value"] for o in self.invoke("options.list", {"field_id": fid})]
        self.assertEqual(vals, ["亮面", "霧面"])

    def test_duplicate_option_idempotent(self):
        fid = self.create_field("顏色")
        self.invoke("options.create", {"field_id": fid, "value": "黑"})
        self.invoke("options.create", {"field_id": fid, "value": "黑"})
        opts = self.invoke("options.list", {"field_id": fid})
        self.assertEqual(len([o for o in opts if o["value"] == "黑"]), 1)

    def test_reactivate_inactive_option_restores_same_id_without_duplicate(self):
        fid = self.create_field("版型")
        oid = self._opt(fid, "亮面")
        self.invoke("options.update", {"id": oid, "fields": {"active": 0}})

        self.invoke("options.create", {"field_id": fid, "value": "亮面", "reactivate": True})
        opts = self.invoke("options.list", {"field_id": fid, "all": 1})
        matches = [o for o in opts if o["value"] == "亮面"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["option_id"], oid)
        self.assertEqual(matches[0]["active"], 1)

    def test_duplicate_inactive_option_without_reactivate_stays_inactive(self):
        fid = self.create_field("版型")
        oid = self._opt(fid, "亮面")
        self.invoke("options.update", {"id": oid, "fields": {"active": 0}})

        self.invoke("options.create", {"field_id": fid, "value": "亮面"})
        self.assertEqual(self.invoke("options.list", {"field_id": fid}), [])
        all_opts = self.invoke("options.list", {"field_id": fid, "all": 1})
        self.assertEqual(len(all_opts), 1)
        self.assertEqual(all_opts[0]["option_id"], oid)
        self.assertEqual(all_opts[0]["active"], 0)

    def _opt(self, fid, value):
        self.invoke("options.create", {"field_id": fid, "value": value})
        return next(o["option_id"] for o in
                    self.invoke("options.list", {"field_id": fid})
                    if o["value"] == value)

    def test_rename_option(self):
        fid = self.create_field("版型")
        oid = self._opt(fid, "亮面")
        self.invoke("options.update", {"id": oid, "fields": {"value": "高亮"}})
        vals = [o["value"] for o in self.invoke("options.list", {"field_id": fid})]
        self.assertIn("高亮", vals)
        self.assertNotIn("亮面", vals)

    def test_rename_option_conflict_409(self):
        fid = self.create_field("版型")
        self._opt(fid, "亮面")
        oid2 = self._opt(fid, "霧面")
        self.assert_application_error("conflict", "options.update", {"id": oid2, "fields": {"value": "亮面"}})

    def test_deactivate_option_hidden_from_fields(self):
        cid = self.create_category("保護貼")
        fid = self.create_field("版型", cid)
        oid = self._opt(fid, "亮面")
        self.invoke("options.update", {"id": oid, "fields": {"active": 0}})
        # 維護頁 all=1 仍看得到停用者
        allopts = self.invoke("options.list", {"field_id": fid, "all": 1})
        self.assertIn("亮面", [o["value"] for o in allopts])
        # 預設(建檔下拉)不回停用者
        self.assertEqual(self.invoke("options.list", {"field_id": fid}), [])
        # categories/{id}/fields 只回啟用選項
        fields = self.invoke("categories.fields", {"id": cid})
        opts = next(f["options"] for f in fields if f["field_id"] == fid)
        self.assertEqual(opts, [])

    def test_delete_option_removed_from_fields(self):
        cid = self.create_category("保護貼")
        fid = self.create_field("版型", cid)
        oid = self._opt(fid, "亮面")
        self.invoke("options.delete", {"id": oid})
        self.assertEqual(self.invoke("options.list", {"field_id": fid, "all": 1}), [])
        fields = self.invoke("categories.fields", {"id": cid})
        opts = next(f["options"] for f in fields if f["field_id"] == fid)
        self.assertEqual(opts, [])

    def test_referenced_delete_hides_option_preserves_attributes_and_clears_links(self):
        cid = self.create_category("保護貼")
        fid = self.create_field("版型", cid)
        oid = self._opt(fid, "亮面")
        mid = self.create_model(self.create_phone_brand("測試品牌"), "測試型號")
        self.invoke("categories.set_field", {"category_id": cid, "field_id": fid, "fields": {"default_option_id": oid}})
        self.invoke("options.set_models", {"id": oid, "model_ids": [mid]})
        self.invoke("products.create", {
            "name": "膜", "category_id": cid,
            "variants": [
                {"attributes": {"版型": "亮面"}, "barcodes": []},
                {"attributes": {"版型": "亮面"}, "barcodes": []},
            ],
        })

        listed = self.invoke("options.list", {"field_id": fid})
        self.assertEqual(listed[0]["usage_count"], 2)
        result = self.invoke("options.delete", {"id": oid})
        self.assertFalse(result["deleted"])
        self.assertEqual(self.invoke("options.list", {"field_id": fid}), [])
        hidden = self.invoke("options.list", {"field_id": fid, "all": 1})[0]
        self.assertEqual(hidden["active"], 0)
        self.assertEqual(hidden["usage_count"], 2)
        with get_conn(self.db) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM VariantAttribute WHERE option_id=?", (oid,)
            ).fetchone()[0], 2)
            self.assertIsNone(conn.execute(
                "SELECT default_option_id FROM CategoryField WHERE field_id=?", (fid,)
            ).fetchone()[0])
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM OptionModel WHERE option_id=?", (oid,)
            ).fetchone()[0], 0)

    def test_unreferenced_delete_removes_option_and_clears_default(self):
        cid = self.create_category("保護貼")
        fid = self.create_field("版型", cid)
        oid = self._opt(fid, "亮面")
        self.invoke("categories.set_field", {"category_id": cid, "field_id": fid, "fields": {"default_option_id": oid}})

        result = self.invoke("options.delete", {"id": oid})
        self.assertTrue(result["deleted"])
        self.assertEqual(self.invoke("options.list", {"field_id": fid, "all": 1}), [])
        with get_conn(self.db) as conn:
            self.assertIsNone(conn.execute(
                "SELECT default_option_id FROM CategoryField WHERE field_id=?", (fid,)
            ).fetchone()[0])
            self.assertIsNone(conn.execute(
                "SELECT option_id FROM AttributeOption WHERE option_id=?", (oid,)
            ).fetchone())

    def test_add_option_unknown_field_returns_404(self):
        self.assert_application_error("not_found", "options.create", {"field_id": 999999, "value": "不存在"})

    def test_invalid_field_type_rejected_on_add_and_patch(self):
        self.assert_application_error("validation_error", "fields.create", {"name": "壞欄", "field_type": "number"})
        fid = self.create_field("版型")
        self.assert_application_error("validation_error", "fields.update", {"id": fid, "fields": {"field_type": "number"}})

    def test_default_option_must_be_created_with_field_and_belong_to_field(self):
        fid = self.create_field("版型一")
        other_fid = self.create_field("版型二")
        other_oid = self._opt(other_fid, "亮面")

        self.assert_application_error("validation_error", "fields.create", {"name": "不應有預設", "default_option_id": other_oid})
        self.assert_application_error("validation_error", "categories.set_field", {"category_id": self.create_category("預設測試"), "field_id": fid, "fields": {"default_option_id": 999999}})
        self.assert_application_error("validation_error", "categories.set_field", {"category_id": self.create_category("預設測試二"), "field_id": fid, "fields": {"default_option_id": other_oid}})

    def test_default_option_can_be_cleared(self):
        """建檔預設帶入值改回「(無)」要真的清掉,不是被當成未帶參數略過。"""
        cid = self.create_category("清除預設")
        fid = self.create_field("版型", cid)
        oid = self._opt(fid, "滿版")
        self.invoke("categories.set_field",
                    {"category_id": cid, "field_id": fid, "fields": {"default_option_id": oid}})
        self.assertEqual(
            [f for f in self.invoke("categories.fields", {"id": cid})
             if f["name"] == "版型"][0]["default_value"], "滿版")
        self.invoke("categories.set_field",
                    {"category_id": cid, "field_id": fid, "fields": {"default_option_id": None}})
        self.assertIsNone(
            [f for f in self.invoke("categories.fields", {"id": cid})
             if f["name"] == "版型"][0]["default_value"])



class TestFieldUsageLeadScope(FacadeTestCase):
    """候選前排範圍:該廠牌用過 → 該產品用過 → 都沒有則不指定。"""

    def setUp(self):
        super().setUp()
        self.make_category_with_field("款式", "select", ("SolidX", "皮套", "透明"))
        self.brand_id = self.invoke("brands.create", {"name": "DEVILCASE"})["brand_id"]
        # DEVILCASE 用過 SolidX;無品牌皮套(廠牌欄留空)用過皮套
        self.invoke("products.create", {
            "name": "惡魔盾", "category_id": self.cid, "brand_id": self.brand_id,
            "variants": [{"attributes": {"款式": "SolidX"}, "price": 100,
                          "barcodes": [{"barcode": "D1", "source": "store"}]}]})
        self.leather_id = self.invoke("products.create", {
            "name": "多卡槽牛皮皮套", "category_id": self.cid,
            "variants": [{"attributes": {"款式": "皮套"}, "price": 100,
                          "barcodes": [{"barcode": "L1", "source": "store"}]}],
        })["product_id"]

    def usage(self, **scope):
        payload = {"category_id": self.cid, "field_id": self.fid}
        payload.update(scope)
        return {o["value"]: o for o in self.invoke("variants.field_usage", payload)}

    def test_brand_scope_leads_only_values_that_brand_used(self):
        got = self.usage(brand_id=self.brand_id, product_id=self.leather_id)
        self.assertTrue(got["SolidX"]["lead"])
        self.assertEqual(got["SolidX"]["lead_count"], 1)
        # 廠牌有紀錄就不退回產品:皮套與未使用的透明都留在「更多」
        self.assertFalse(got["皮套"]["lead"])
        self.assertFalse(got["透明"]["lead"])

    def test_product_scope_is_used_when_brand_is_empty(self):
        # 無品牌皮套:廠牌欄為空,前排改用該產品自己用過的值
        got = self.usage(brand_id=None, product_id=self.leather_id)
        self.assertTrue(got["皮套"]["lead"])
        self.assertFalse(got["SolidX"]["lead"])

    def test_no_scope_marks_nothing_as_lead_and_keeps_category_counts(self):
        got = self.usage()
        self.assertFalse(any(o["lead"] for o in got.values()))
        self.assertEqual(got["SolidX"]["usage_count"], 1)
        self.assertEqual(got["皮套"]["usage_count"], 1)
        self.assertEqual(got["透明"]["usage_count"], 0)

    def test_brand_without_history_falls_back_to_product(self):
        other = self.invoke("brands.create", {"name": "新廠牌"})["brand_id"]
        got = self.usage(brand_id=other, product_id=self.leather_id)
        self.assertTrue(got["皮套"]["lead"])
        self.assertFalse(got["SolidX"]["lead"])


class TestDeleteCategoryField(FacadeTestCase):
    """設定頁模板列紅色 ✕:從此種類移除規格欄並清掉此種類的值。"""

    def setUp(self):
        super().setUp()
        self.make_category_with_field("版型", "select", ("皮套", "透明"))
        self.other = self.create_category("鏡頭貼")
        self.invoke("categories.set_field",
                    {"category_id": self.other, "field_id": self.fid, "fields": {}})
        self.create_product({"版型": "皮套"}, name="皮套", barcode="D1")

    def counts(self, category_id, field_id):
        rows = self.invoke("fields.list", {"category_id": category_id})
        row = next((r for r in rows if r["field_id"] == field_id), None)
        return row["cat_usage_count"] if row else None

    def test_usage_count_is_reported_per_category(self):
        self.assertEqual(self.counts(self.cid, self.fid), 1)
        self.assertEqual(self.counts(self.other, self.fid), 0)

    def test_remove_clears_only_this_category_values_and_keeps_field(self):
        result = self.invoke("categories.delete_field",
                             {"category_id": self.cid, "field_id": self.fid})
        self.assertEqual(result["removed_variant_count"], 1)
        self.assertFalse(result["field_deleted"])
        self.assertIsNone(self.counts(self.cid, self.fid))       # 掛勾已解除
        self.assertEqual(self.counts(self.other, self.fid), 0)   # 其他種類不受影響
        variants = self.invoke("catalog.list", {})[0]["variants"]
        self.assertEqual(variants[0]["attributes"], {})           # 此種類的值已清掉

    def test_field_itself_is_deleted_once_nobody_uses_it(self):
        self.invoke("categories.delete_field", {"category_id": self.cid, "field_id": self.fid})
        result = self.invoke("categories.delete_field",
                             {"category_id": self.other, "field_id": self.fid})
        self.assertTrue(result["field_deleted"])
        self.assertNotIn("版型", [f["name"] for f in self.invoke("fields.list", {})])
        self.assertEqual(self.invoke("options.list", {"field_id": self.fid, "all": 1}), [])

    def test_feature_field_removable_and_unlinked_field_rejected(self):
        """特性詞條每個種類各自一份,可從本種類移除;
        未掛在本種類的欄位仍回 not_found。"""
        feature = self.create_field("特性詞條", self.cid, "tags")
        result = self.invoke("categories.delete_field",
                             {"category_id": self.cid, "field_id": feature})
        self.assertTrue(result["field_deleted"])
        spare = self.create_field("備註", None, "text")
        self.assert_application_error(
            "not_found", "categories.delete_field",
            {"category_id": self.other, "field_id": spare})


class TestPinnedOptionOrder(unittest.TestCase):
    """玻璃貼材質(鍍膜)在候選選單固定次序,不隨使用次數浮動。"""

    def test_pinned_values_go_first_in_fixed_order(self):
        got = product_rules.sort_pinned_options(
            [{"value": v} for v in ("防窺", "藍光", "亮面", "霧面")])
        self.assertEqual([o["value"] for o in got], ["亮面", "霧面", "藍光", "防窺"])

    def test_other_values_keep_incoming_order_after_pinned(self):
        got = product_rules.sort_pinned_options(
            [{"value": v} for v in ("康寧", "防窺", "藍寶石", "亮面")])
        self.assertEqual(
            [o["value"] for o in got], ["亮面", "防窺", "康寧", "藍寶石"])

    def test_field_without_pinned_values_is_untouched(self):
        values = ("滿版", "9分滿")
        got = product_rules.sort_pinned_options([{"value": v} for v in values])
        self.assertEqual([o["value"] for o in got], list(values))


if __name__ == "__main__":
    unittest.main()
