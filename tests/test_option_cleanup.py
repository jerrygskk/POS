"""階段 5.5:零使用選項自動清理與手動清理。

- set_variant_attributes 覆寫使選項使用歸零 → 自動硬刪(default 引用除外)。
- delete_variant 使選項使用歸零 → 自動硬刪。
- settings options.cleanup 手動清理零使用選項(default 引用除外)。
"""

from base import ConnTestCase
from lib.db import get_conn
from lib.product_service import ProductFacade
from lib.settings_service import SettingsFacade


class OptionCleanupTests(ConnTestCase):
    def setUp(self):
        super().setUp()
        c = self.conn
        self.cid = c.execute("INSERT INTO Category(name) VALUES('種類')").lastrowid
        self.fid = c.execute(
            "INSERT INTO AttributeField(name,field_type) VALUES('顏色','select')").lastrowid
        c.execute("INSERT INTO CategoryField(category_id,field_id,sort,active) "
                  "VALUES(?,?,1,1)", (self.cid, self.fid))
        self.red = c.execute("INSERT INTO AttributeOption(field_id,value,sort) "
                             "VALUES(?,?,1)", (self.fid, "紅")).lastrowid
        self.blue = c.execute("INSERT INTO AttributeOption(field_id,value,sort) "
                              "VALUES(?,?,2)", (self.fid, "藍")).lastrowid
        # 長度欄:僅供區分同色子產品(避免 C 規則簽章重複)
        self.len_fid = c.execute(
            "INSERT INTO AttributeField(name,field_type) VALUES('長度','select')").lastrowid
        c.execute("INSERT INTO CategoryField(category_id,field_id,sort,active) "
                  "VALUES(?,?,2,1)", (self.cid, self.len_fid))
        for v in ("1m", "2m"):
            c.execute("INSERT INTO AttributeOption(field_id,value,sort) VALUES(?,?,0)",
                      (self.len_fid, v))
        self.pid = c.execute("INSERT INTO Product(name,category_id) VALUES('大產品',?)",
                             (self.cid,)).lastrowid
        c.commit()
        self.conn.close()
        self.products = ProductFacade(self.db)
        self.settings = SettingsFacade(self.db)

    def _fresh(self):
        return get_conn(self.db)

    def _opt_exists(self, conn, oid):
        return conn.execute("SELECT 1 FROM AttributeOption WHERE option_id=?",
                            (oid,)).fetchone() is not None

    def _make_variant(self, color, length="1m"):
        res = self.products.invoke("variants.batch_create", {
            "product_id": self.pid,
            "drafts": [{"draft_id": "d", "attributes": {"顏色": color, "長度": length}}]})
        return res["results"][0]["variant_id"]

    def test_update_to_zero_usage_auto_deletes(self):
        vid = self._make_variant("紅")
        # 改為藍 → 紅使用歸零 → 自動硬刪
        self.products.invoke("variants.update_details",
                             {"id": vid, "fields": {"attributes": {"顏色": "藍"}}})
        conn = self._fresh()
        self.assertFalse(self._opt_exists(conn, self.red))
        self.assertTrue(self._opt_exists(conn, self.blue))
        conn.close()

    def test_still_used_not_deleted(self):
        v1 = self._make_variant("紅", "1m")
        self._make_variant("紅", "2m")  # 第二筆仍用紅(長度不同以避簽章重複)
        self.products.invoke("variants.update_details",
                             {"id": v1, "fields": {"attributes": {"顏色": "藍", "長度": "1m"}}})
        conn = self._fresh()
        self.assertTrue(self._opt_exists(conn, self.red))  # 仍有另一子產品使用
        conn.close()

    def test_default_referenced_option_kept(self):
        # 紅設為種類模板 default_option_id
        self.settings.invoke("categories.set_field", {
            "category_id": self.cid, "field_id": self.fid,
            "fields": {"default_option_id": self.red}})
        vid = self._make_variant("紅")
        self.products.invoke("variants.update_details",
                             {"id": vid, "fields": {"attributes": {"顏色": "藍"}}})
        conn = self._fresh()
        self.assertTrue(self._opt_exists(conn, self.red))  # default 引用 → 不刪
        conn.close()

    def test_delete_variant_cleans_options(self):
        vid = self._make_variant("紅")
        self.products.invoke("variants.delete", {"id": vid})
        conn = self._fresh()
        self.assertFalse(self._opt_exists(conn, self.red))
        conn.close()

    def test_manual_cleanup_deletes_zero_usage_keeps_default(self):
        # 藍設為 default;紅、藍皆零使用
        self.settings.invoke("categories.set_field", {
            "category_id": self.cid, "field_id": self.fid,
            "fields": {"default_option_id": self.blue}})
        res = self.settings.invoke("options.cleanup", {"field_id": self.fid})
        self.assertEqual(res["deleted"], 1)  # 只刪紅
        conn = self._fresh()
        self.assertFalse(self._opt_exists(conn, self.red))
        self.assertTrue(self._opt_exists(conn, self.blue))  # default → 保留
        conn.close()

    def test_manual_cleanup_global(self):
        # 全域:顏色(紅/藍)+長度(1m/2m)共 4 個零使用選項,皆無 default
        res = self.settings.invoke("options.cleanup", {})
        self.assertEqual(res["deleted"], 4)
        conn = self._fresh()
        self.assertFalse(self._opt_exists(conn, self.red))
        self.assertFalse(self._opt_exists(conn, self.blue))
        conn.close()
