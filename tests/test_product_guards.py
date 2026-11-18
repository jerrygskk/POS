"""商品資料的防呆:負售價、刪規格欄造成重複款式、款式刪除條件、同名設定項目。所有資料只寫暫存庫。"""
import sqlite3

from base import FacadeTestCase
from lib.db import get_conn


class ProductGuardTest(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.facade.session.login("admin")

    def product(self, variants=2, cid=None):
        cid = cid or self.create_category("防呆種類")
        made = self.invoke("products.create", {"name": "p", "category_id": cid, "variants": [
            {"attributes": {}, "price": 100, "barcodes": [{"barcode": f"G{i}", "source": "store"}]}
            for i in range(variants)]})
        return made["product_id"], made["variant_ids"]

    # ---- 售價 ----
    def test_negative_price_rejected_everywhere(self):
        pid, (vid, _) = self.product()
        cid = self.create_category("其他")
        self.assert_application_error("validation_error", "variants.update", {"id": vid, "fields": {"price": -1}})
        self.assert_application_error("validation_error", "variants.create",
                                      {"product_id": pid, "fields": {"attributes": {}, "price": -1}})
        self.assert_application_error("validation_error", "products.create",
                                      {"name": "x", "category_id": cid, "variants": [{"attributes": {}, "price": -5}]})
        self.assert_application_error("validation_error", "variants.batch_create",
                                      {"product_id": pid, "drafts": [{"attributes": {}, "price": -5}]})
        self.invoke("variants.update", {"id": vid, "fields": {"price": 0}})
        with get_conn(self.db) as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE Variant SET price=-1 WHERE variant_id=?", (vid,))

    # ---- 刪規格欄 ----
    def test_delete_field_blocked_when_variants_would_become_identical(self):
        cid, fid = self.make_category_with_field("顏色", options=("黑", "白"))
        self.invoke("products.create", {"name": "膜", "category_id": cid, "variants": [
            {"attributes": {"顏色": "黑"}, "price": 1}, {"attributes": {"顏色": "白"}, "price": 1}]})
        self.assert_application_error("conflict", "categories.delete_field", {"category_id": cid, "field_id": fid})
        other = self.create_field("材質", cid)
        self.create_options(other, ["亮"])
        self.invoke("categories.delete_field", {"category_id": cid, "field_id": other})

    # ---- 刪款式 ----
    def test_variant_in_stocktake_or_return_gives_clear_reason(self):
        _, (v1, v2) = self.product()
        sid = self.invoke("stocktake.create", {"employee": 1})["session_id"]
        self.invoke("stocktake.scan", {"session_id": sid, "variant_id": v1, "qty": 1})
        with self.assertRaisesRegex(Exception, "盤點單"):
            self.invoke("variants.delete", {"id": v1})
        rid = self.invoke("returns.create", {"kind": "normal", "employee": 1})["return_id"]
        self.invoke("returns.scan", {"return_id": rid, "variant_id": v2, "qty": 1})
        with self.assertRaisesRegex(Exception, "退貨單"):
            self.invoke("variants.delete", {"id": v2})

    def test_variant_with_pending_issue_can_be_deleted(self):
        _, (v1, v2) = self.product()
        with get_conn(self.db) as conn:
            conn.execute("INSERT INTO VariantIssue(variant_id,issue_type,related_variant_id) VALUES(?,?,?)",
                         (v2, "duplicate_signature", v1))
            conn.commit()
        # 刪被指向的那筆:另一筆的重複標記應隨之重驗消失
        self.invoke("variants.delete", {"id": v1})
        with get_conn(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM VariantIssue").fetchone()[0], 0)
        self.invoke("variants.delete", {"id": v2})

    def test_required_field_then_delete_product(self):
        cid, fid = self.make_category_with_field("顏色", options=("黑",))
        pid, _ = self.product(1, cid)
        self.invoke("categories.set_field", {"category_id": cid, "field_id": fid, "fields": {"required": 1}})
        self.invoke("products.delete", {"id": pid})

    # ---- 同名 ----
    def test_duplicate_and_blank_names_have_clear_messages(self):
        self.invoke("categories.create", {"name": "殼"})
        with self.assertRaisesRegex(Exception, "已有同名種類"):
            self.invoke("categories.create", {"name": " 殼 "})
        self.invoke("brands.create", {"name": "B"})
        other = self.invoke("brands.create", {"name": "C"})["brand_id"]
        with self.assertRaisesRegex(Exception, "已有同名廠牌"):
            self.invoke("brands.update", {"id": other, "fields": {"name": "B"}})
        self.assert_application_error("validation_error", "categories.create", {"name": "  "})
