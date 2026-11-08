"""款式編輯與批次新增修正:規格撞號、選項正規化沿用、草稿內條碼重複。"""

from base import FacadeTestCase
from lib.db import get_conn


class TestVariantEditFixes(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.make_category_with_field("color", options=("Blue", "Red"))
        result = self.invoke("products.create", {
            "name": "HODA glass", "category_id": self.cid, "variants": [
                {"attributes": {"color": "Blue"}, "price": 590,
                 "barcodes": [{"barcode": "FX1", "source": "factory"}]},
                {"attributes": {"color": "Red"}, "price": 690,
                 "barcodes": [{"barcode": "FX2", "source": "factory"}]},
            ]})
        self.pid = result["product_id"]
        self.vid, self.vid2 = result["variant_ids"]

    def _edit(self, vid, attrs, **fields):
        return self.invoke("variants.update_editor", {
            "id": vid, "fields": {"attributes": attrs, **fields}, "model_ids": [],
            "deleted_barcodes": [], "factory_barcodes": [], "store_barcode_count": 0})

    def _attrs(self, vid):
        return next(v for v in self.invoke("catalog.list")[0]["variants"]
                    if v["variant_id"] == vid)["attributes"]

    def _option_values(self):
        with get_conn(self.db) as conn:
            return sorted(r["value"] for r in conn.execute(
                "SELECT value FROM AttributeOption WHERE field_id=?", (self.fid,)))

    def test_edit_to_same_spec_as_sibling_is_rejected(self):
        self.assert_application_error("conflict", "variants.update_editor", {
            "id": self.vid, "fields": {"attributes": {"color": "Red"}}, "model_ids": [],
            "deleted_barcodes": [], "factory_barcodes": [], "store_barcode_count": 0})
        self.assertEqual(self._attrs(self.vid), {"color": "Blue"})

    def test_edit_without_spec_change_still_saves(self):
        self._edit(self.vid, {"color": "Blue"}, price=600)

    def test_edit_option_case_and_width_reuses_existing(self):
        self._edit(self.vid, {"color": "ｂｌｕｅ "})
        self.assertEqual(self._attrs(self.vid), {"color": "Blue"})
        self.assertEqual(self._option_values(), ["Blue", "Red"])

    def test_edit_case_variant_of_sibling_spec_is_rejected(self):
        self.assert_application_error("conflict", "variants.update_editor", {
            "id": self.vid, "fields": {"attributes": {"color": "RED"}}, "model_ids": [],
            "deleted_barcodes": [], "factory_barcodes": [], "store_barcode_count": 0})

    def test_editor_keeps_existing_inactive_value_without_reactivating(self):
        with get_conn(self.db) as conn:
            conn.execute("UPDATE AttributeOption SET active=0 WHERE field_id=? AND value='Blue'",
                         (self.fid,))
            conn.commit()
        self._edit(self.vid, {"color": "Blue"}, price=600)
        with get_conn(self.db) as conn:
            self.assertEqual(conn.execute(
                "SELECT active FROM AttributeOption WHERE field_id=? AND value='Blue'",
                (self.fid,)).fetchone()["active"], 0)

    def test_batch_same_barcode_twice_in_one_draft(self):
        payload = {"product_id": self.pid, "drafts": [{
            "draft_id": "a", "attributes": {"color": "green"},
            "barcodes": [{"barcode": "DUP9", "source": "factory"},
                         {"barcode": "DUP9", "source": "factory"}]}]}
        pre = self.invoke("variants.batch_precheck", payload)
        errors = pre["results"][0]["errors"]
        self.assertEqual([e["code"] for e in errors], ["repeated_barcode"])
        self.assertIn("條碼重複", errors[0]["message"])
        self.assert_application_error("validation_error", "variants.batch_create", payload)


class TestLegacyCaseVariantOptions(FacadeTestCase):
    """舊資料同時有 Blue 與 BLUE 時,只改售價不得改掛,較早的 Blue 停用也不得誤擋。"""

    _edit = TestVariantEditFixes._edit

    def setUp(self):
        FacadeTestCase.setUp(self)
        self.make_category_with_field("color", options=("Blue", "Red"))
        result = self.invoke("products.create", {
            "name": "HODA glass", "category_id": self.cid, "variants": [
                {"attributes": {"color": "Blue"}, "price": 590},
                {"attributes": {"color": "Red"}, "price": 690}]})
        self.vid = result["variant_ids"][0]
        with get_conn(self.db) as conn:
            cur = conn.execute("INSERT INTO AttributeOption(field_id,value,sort) VALUES(?,?,99)", (self.fid, "BLUE"))
            upper = cur.lastrowid
            conn.execute("UPDATE VariantAttribute SET option_id=? WHERE variant_id=? AND field_id=?",
                         (upper, self.vid, self.fid))
            conn.execute("UPDATE AttributeOption SET active=0 WHERE field_id=? AND value='Blue'", (self.fid,))
            conn.commit()
        self.upper = upper

    def test_price_only_edit_keeps_legacy_upper_case_option(self):
        self._edit(self.vid, {"color": "BLUE"}, price=600)
        with get_conn(self.db) as conn:
            oid = conn.execute("SELECT option_id FROM VariantAttribute WHERE variant_id=? AND field_id=?",
                               (self.vid, self.fid)).fetchone()["option_id"]
        self.assertEqual(oid, self.upper)
