import unittest
from lib.db import get_conn
from base import FacadeTestCase

class TestAttributes(FacadeTestCase):
    def test_seed_common_fields(self):
        # 種子只留兩個共用欄:商品描述、顏色
        names = [f["name"] for f in self.invoke("fields.list")]
        self.assertIn("商品描述", names)
        self.assertIn("顏色", names)

    def test_rename_field(self):
        fid = self.invoke("fields.list")[0]["field_id"]
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
        fid = self.invoke("fields.list")[0]["field_id"]
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
        fid = self.invoke("fields.list")[0]["field_id"]
        self.assert_application_error("validation_error", "fields.update", {"id": fid, "fields": {"field_type": "number"}})

    def test_default_option_must_be_created_with_field_and_belong_to_field(self):
        fid = self.create_field("版型一")
        other_fid = self.create_field("版型二")
        other_oid = self._opt(other_fid, "亮面")

        self.assert_application_error("validation_error", "fields.create", {"name": "不應有預設", "default_option_id": other_oid})
        self.assert_application_error("validation_error", "categories.set_field", {"category_id": self.create_category("預設測試"), "field_id": fid, "fields": {"default_option_id": 999999}})
        self.assert_application_error("validation_error", "categories.set_field", {"category_id": self.create_category("預設測試二"), "field_id": fid, "fields": {"default_option_id": other_oid}})


if __name__ == "__main__":
    unittest.main()
