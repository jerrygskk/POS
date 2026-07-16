"""商品設定核心規格 A–F 新行為單元測試。

涵蓋:種類 model_mode 與上層停用開關、種類模板 CategoryField CRUD、特性詞條固定欄、
必填鎖定、欄型鎖定、§12.3 停用值差異驗證、effective_active 各入口、
廠牌 inline 沿用/新建與刪除守門、刪空種類連鎖清理。
"""
import unittest

from base import ApiTestCase
from lib.db import get_conn


class TestCategoryModelModeAndSwitch(ApiTestCase):
    # A:model_mode 讀寫
    def test_model_mode_read_write(self):
        cid = self.c.post("/api/categories",
                          json={"name": "手機殼", "model_mode": "required"}).json()["category_id"]
        row = next(x for x in self.c.get("/api/categories").json() if x["category_id"] == cid)
        self.assertEqual(row["model_mode"], "required")
        self.c.patch(f"/api/categories/{cid}", json={"model_mode": "hidden"})
        row = next(x for x in self.c.get("/api/categories").json() if x["category_id"] == cid)
        self.assertEqual(row["model_mode"], "hidden")

    def test_model_mode_default_hidden_and_bad_value_422(self):
        cid = self.c.post("/api/categories", json={"name": "充電線"}).json()["category_id"]
        row = next(x for x in self.c.get("/api/categories").json() if x["category_id"] == cid)
        self.assertEqual(row["model_mode"], "hidden")
        self.assertEqual(self.c.post("/api/categories",
                         json={"name": "X", "model_mode": "bogus"}).status_code, 422)

    # A:種類停用是上層開關,不改寫下層 active
    def test_category_disable_does_not_rewrite_lower_active(self):
        self.make_category_with_field("規格", options=("亮面",))
        r = self.create_product({"規格": "亮面"})
        pid, vid = r["product_id"], r["variant_ids"][0]
        self.c.patch(f"/api/categories/{self.cid}", json={"active": 0})
        with get_conn(self.db) as conn:
            self.assertEqual(conn.execute("SELECT active FROM Product WHERE product_id=?", (pid,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT active FROM Variant WHERE variant_id=?", (vid,)).fetchone()[0], 1)
        # 但有效啟用為 False
        self.assertFalse(self.c.get("/api/barcode/B1").json()["active"])
        # 重新啟用種類後恢復
        self.c.patch(f"/api/categories/{self.cid}", json={"active": 1})
        self.assertTrue(self.c.get("/api/barcode/B1").json()["active"])


class TestTemplateFields(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.cid = self.create_category("鋼化玻璃")
        self.fid = self.create_field("版型", self.cid)
        self.create_options(self.fid, ("滿版", "9分滿"))
        self.oid = next(o["option_id"] for o in
                        self.c.get(f"/api/options?field_id={self.fid}").json() if o["value"] == "滿版")

    # B:模板 CRUD(sort/required/default/active)
    def test_set_field_template_crud(self):
        r = self.c.put(f"/api/categories/{self.cid}/fields/{self.fid}",
                       json={"sort": 3, "required": 1, "default_option_id": self.oid})
        self.assertEqual(r.status_code, 200)
        with get_conn(self.db) as conn:
            row = conn.execute("SELECT sort,required,default_option_id,active FROM CategoryField "
                               "WHERE category_id=? AND field_id=?", (self.cid, self.fid)).fetchone()
        self.assertEqual((row[0], row[1], row[2], row[3]), (3, 1, self.oid, 1))
        # category_fields 反映 required 與 default
        f = next(x for x in self.c.get(f"/api/categories/{self.cid}/fields").json()
                 if x["field_id"] == self.fid)
        self.assertEqual(f["required"], 1)
        self.assertEqual(f["default_value"], "滿版")

    def test_set_field_default_must_belong_to_field(self):
        other_fid = self.create_field("材質", self.cid)
        self.create_options(other_fid, ("玻璃",))
        other_oid = self.c.get(f"/api/options?field_id={other_fid}").json()[0]["option_id"]
        r = self.c.put(f"/api/categories/{self.cid}/fields/{self.fid}",
                       json={"default_option_id": other_oid})
        self.assertEqual(r.status_code, 422)

    # B:特性詞條固定欄不可停用
    def test_feature_field_cannot_be_disabled(self):
        feat = self.create_field("特性詞條", self.cid, field_type="tags")
        r = self.c.put(f"/api/categories/{self.cid}/fields/{feat}", json={"active": 0})
        self.assertEqual(r.status_code, 422)
        # 全域欄位停用亦擋
        self.assertEqual(self.c.put(f"/api/fields/{feat}", json={"active": 0}).status_code, 422)

    # B:已有 Variant 的種類鎖 required 切換
    def test_required_locked_when_category_has_variant(self):
        # 尚無子產品:可切換
        self.assertEqual(self.c.put(f"/api/categories/{self.cid}/fields/{self.fid}",
                         json={"required": 1}).status_code, 200)
        self.c.post("/api/products", json={"name": "膜", "category_id": self.cid,
            "variants": [{"attributes": {"版型": "滿版"}, "barcodes": []}]})
        # 已有子產品:改變 required 回 422
        self.assertEqual(self.c.put(f"/api/categories/{self.cid}/fields/{self.fid}",
                         json={"required": 0}).status_code, 422)
        # 同值(不變)仍允許
        self.assertEqual(self.c.put(f"/api/categories/{self.cid}/fields/{self.fid}",
                         json={"required": 1}).status_code, 200)

    # B:欄位已被使用鎖 field_type 變更
    def test_field_type_locked_when_used(self):
        self.c.post("/api/products", json={"name": "膜", "category_id": self.cid,
            "variants": [{"attributes": {"版型": "滿版"}, "barcodes": []}]})
        self.assertEqual(self.c.put(f"/api/fields/{self.fid}",
                         json={"field_type": "multi"}).status_code, 422)

    # B:停用選項若為模板預設值,同交易清空 default
    def test_deactivate_default_option_clears_default(self):
        self.c.put(f"/api/categories/{self.cid}/fields/{self.fid}",
                   json={"default_option_id": self.oid})
        self.c.patch(f"/api/options/{self.oid}", json={"active": 0})
        with get_conn(self.db) as conn:
            self.assertIsNone(conn.execute(
                "SELECT default_option_id FROM CategoryField WHERE category_id=? AND field_id=?",
                (self.cid, self.fid)).fetchone()[0])


class TestProductAndBrand(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.cid = self.create_category("鋼化玻璃")

    # C:同種類正規化名稱查重
    def test_same_category_normalized_name_rejected(self):
        self.c.post("/api/products", json={"name": "HODA 膜", "category_id": self.cid,
            "variants": []})
        # 正規化後同名(大小寫/空白)→ 409
        r = self.c.post("/api/products", json={"name": "hoda 膜", "category_id": self.cid,
            "variants": []})
        self.assertEqual(r.status_code, 409)
        # 不同種類同名可
        cid2 = self.create_category("手機殼")
        self.assertEqual(self.c.post("/api/products", json={"name": "HODA 膜",
            "category_id": cid2, "variants": []}).status_code, 200)

    # C:新增大產品補建 BrandCategory
    def test_create_builds_brand_category(self):
        bid = self.c.post("/api/brands", json={"name": "HODA"}).json()["brand_id"]
        self.c.post("/api/products", json={"name": "膜", "category_id": self.cid,
            "brand_id": bid, "variants": []})
        with get_conn(self.db) as conn:
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM BrandCategory WHERE brand_id=? AND category_id=?",
                (bid, self.cid)).fetchone())

    # C:廠牌 inline 新增——同名沿用、否則新建並建 BrandCategory
    def test_brand_inline_reuse_and_create(self):
        bid = self.c.post("/api/brands", json={"name": "HODA"}).json()["brand_id"]
        # 正規化同名沿用既有廠牌
        self.c.post("/api/products", json={"name": "膜1", "category_id": self.cid,
            "brand_name": "hoda", "variants": []})
        with get_conn(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM Brand WHERE name='HODA'").fetchone()[0], 1)
            pid = conn.execute("SELECT product_id FROM Product WHERE name='膜1'").fetchone()[0]
            self.assertEqual(conn.execute("SELECT brand_id FROM Product WHERE product_id=?", (pid,)).fetchone()[0], bid)
        # 新名稱→建新廠牌+BrandCategory
        self.c.post("/api/products", json={"name": "膜2", "category_id": self.cid,
            "brand_name": "犀牛盾", "variants": []})
        with get_conn(self.db) as conn:
            new_bid = conn.execute("SELECT brand_id FROM Brand WHERE name='犀牛盾'").fetchone()[0]
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM BrandCategory WHERE brand_id=? AND category_id=?",
                (new_bid, self.cid)).fetchone())

    # C:被引用廠牌擋刪
    def test_brand_referenced_delete_conflict(self):
        bid = self.c.post("/api/brands", json={"name": "HODA"}).json()["brand_id"]
        self.c.post("/api/products", json={"name": "膜", "category_id": self.cid,
            "brand_id": bid, "variants": []})
        self.assertEqual(self.c.delete(f"/api/brands/{bid}").status_code, 409)


class TestDisabledValueDiff(ApiTestCase):
    """§12.3 停用值差異驗證。"""
    def setUp(self):
        super().setUp()
        self.make_category_with_field("規格", options=("亮面", "霧面"))
        r = self.create_product({"規格": "亮面"}, barcode="B1")
        self.pid, self.v0 = r["product_id"], r["variant_ids"][0]
        self.bright = next(o["option_id"] for o in
                           self.c.get(f"/api/options?field_id={self.fid}").json() if o["value"] == "亮面")
        # 亮面被 v0 引用→軟停用
        self.c.delete(f"/api/options/{self.bright}")

    def test_keep_existing_disabled_value_allowed(self):
        r = self.c.put(f"/api/variants/{self.v0}", json={"attributes": {"規格": "亮面"}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get("/api/barcode/B1").json()["attributes"]["規格"], "亮面")

    def test_change_disabled_to_enabled_allowed(self):
        r = self.c.put(f"/api/variants/{self.v0}", json={"attributes": {"規格": "霧面"}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get("/api/barcode/B1").json()["attributes"]["規格"], "霧面")

    def test_new_variant_with_disabled_value_rejected(self):
        r = self.c.post(f"/api/products/{self.pid}/variants",
                        json={"attributes": {"規格": "亮面"}, "barcodes": []})
        self.assertEqual(r.status_code, 422)

    def test_update_assigning_disabled_value_not_originally_present_rejected(self):
        # v1 原為霧面,改指停用的亮面→拒絕
        v1 = self.c.post(f"/api/products/{self.pid}/variants",
                         json={"attributes": {"規格": "霧面"}, "barcodes": []}).json()["variant_id"]
        r = self.c.put(f"/api/variants/{v1}", json={"attributes": {"規格": "亮面"}})
        self.assertEqual(r.status_code, 422)

    def test_multi_cannot_add_another_disabled_value(self):
        # 另建 multi 欄「材質」,選項 A/B/C;C 由他變體引用後軟停用
        mfid = self.create_field("材質", self.cid, field_type="multi")
        self.create_options(mfid, ("A", "B", "C"))
        keep = self.c.post(f"/api/products/{self.pid}/variants",
                           json={"attributes": {"材質": ["A", "B"]}, "barcodes": []}).json()["variant_id"]
        holder = self.c.post(f"/api/products/{self.pid}/variants",
                             json={"attributes": {"材質": ["C"]}, "barcodes": []}).json()["variant_id"]
        cid_opt = next(o["option_id"] for o in
                       self.c.get(f"/api/options?field_id={mfid}").json() if o["value"] == "C")
        self.c.delete(f"/api/options/{cid_opt}")  # C 被 holder 引用→軟停用
        # keep 想新增停用的 C → 拒絕
        r = self.c.put(f"/api/variants/{keep}", json={"attributes": {"材質": ["A", "B", "C"]}})
        self.assertEqual(r.status_code, 422)
        # 但保留原值 [A,B] 可
        self.assertEqual(self.c.put(f"/api/variants/{keep}",
                         json={"attributes": {"材質": ["A", "B"]}}).status_code, 200)


class TestEffectiveActiveEntrypoints(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.make_category_with_field("規格", options=("亮面",))
        r = self.create_product({"規格": "亮面"}, name="HODA膜", barcode="B1")
        self.pid, self.v0 = r["product_id"], r["variant_ids"][0]
        self.c.post("/api/stock/receive", json={"variant_id": self.v0, "qty": 10})

    def _disable_category(self):
        self.c.patch(f"/api/categories/{self.cid}", json={"active": 0})

    def test_scan_reflects_category_active(self):
        self._disable_category()
        self.assertFalse(self.c.get("/api/barcode/B1").json()["active"])

    def test_search_excludes_inactive_category(self):
        self._disable_category()
        self.assertEqual(self.c.get("/api/products?q=HODA").json(), [])

    def test_catalog_excludes_inactive_category(self):
        self._disable_category()
        self.assertEqual(self.c.get("/api/catalog").json(), [])
        self.assertEqual(len(self.c.get("/api/catalog?include_inactive=1").json()), 1)

    def test_sale_rejected_when_category_inactive(self):
        self._disable_category()
        r = self.c.post("/api/sales", json={"payment": "現金", "paid": 1000,
            "items": [{"variant_id": self.v0, "qty": 1, "unit_price": 100}]})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.c.get(f"/api/stock/{self.v0}").json()["stock"], 10)

    def test_child_creation_requires_active_category_and_product(self):
        self._disable_category()
        r = self.c.post(f"/api/products/{self.pid}/variants",
                        json={"attributes": {}, "barcodes": []})
        self.assertEqual(r.status_code, 422)
        # 種類啟用但大產品停用亦擋
        self.c.patch(f"/api/categories/{self.cid}", json={"active": 1})
        self.c.put(f"/api/products/{self.pid}", json={"active": 0})
        r = self.c.post(f"/api/products/{self.pid}/variants",
                        json={"attributes": {}, "barcodes": []})
        self.assertEqual(r.status_code, 422)


class TestDeleteEmptyCategoryCascade(ApiTestCase):
    # F:刪空種類連鎖清理
    def test_delete_empty_category_cascade(self):
        cid = self.create_category("鋼化玻璃")
        specific = self.create_field("版型", cid)          # 專屬欄
        self.create_options(specific, ("滿版",))
        # 共用欄「顏色」綁定本種類(另有一種類共用)
        color = [f for f in self.c.get("/api/fields?common=1").json() if f["name"] == "顏色"][0]["field_id"]
        other = self.create_category("手機殼")
        self.c.put(f"/api/categories/{other}/fields-common", json={"field_ids": [color]})
        self.c.put(f"/api/categories/{cid}/fields-common", json={"field_ids": [color]})
        bid = self.c.post("/api/brands", json={"name": "HODA"}).json()["brand_id"]
        self.c.put(f"/api/brands/{bid}/categories", json={"category_ids": [cid]})

        self.assertEqual(self.c.delete(f"/api/categories/{cid}").status_code, 200)
        with get_conn(self.db) as conn:
            # CategoryField、BrandCategory 該種類關聯清除
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM CategoryField WHERE category_id=?", (cid,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM BrandCategory WHERE category_id=?", (cid,)).fetchone()[0], 0)
            # 專屬且未使用的欄位與選項硬刪
            self.assertIsNone(conn.execute("SELECT 1 FROM AttributeField WHERE field_id=?", (specific,)).fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM AttributeOption WHERE field_id=?", (specific,)).fetchone()[0], 0)
            # 共用欄與其對他種類的綁定保留
            self.assertIsNotNone(conn.execute("SELECT 1 FROM AttributeField WHERE field_id=?", (color,)).fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM CategoryField WHERE category_id=? AND field_id=?", (other, color)).fetchone())
            # 廠牌本體保留
            self.assertIsNotNone(conn.execute("SELECT 1 FROM Brand WHERE brand_id=?", (bid,)).fetchone())


class TestFeatureFieldBindingPreserved(ApiTestCase):
    # 規格 §11.2:特性詞條為固定欄位,共用勾選(含空清單)不得解除其綁定
    def test_set_common_fields_keeps_feature_binding(self):
        cid = self.create_category("鋼化玻璃")
        # 特性詞條綁定本種類,並另掛一種類使其成為共用欄(binding≥2)
        feat = self.create_field("特性詞條", cid, field_type="tags")
        other = self.create_category("手機殼")
        self.c.put(f"/api/categories/{other}/fields/{feat}", json={"active": 1})
        # 以 set_field 於 other 建立綁定(共用)
        self.c.put(f"/api/categories/{other}/fields/{feat}", json={"sort": 0})
        # 以空清單呼叫共用勾選:特性詞條綁定仍在
        self.assertEqual(self.c.put(f"/api/categories/{cid}/fields-common",
                         json={"field_ids": []}).status_code, 200)
        with get_conn(self.db) as conn:
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM CategoryField WHERE category_id=? AND field_id=?",
                (cid, feat)).fetchone())
        # 模板顯示仍含特性詞條
        names = [f["name"] for f in self.c.get(f"/api/categories/{cid}/fields").json()]
        self.assertIn("特性詞條", names)


if __name__ == "__main__":
    unittest.main()
