"""子產品批次建立(階段 5)服務層交易一致性與重複判定測試。"""

from base import ConnTestCase
from lib.db import get_conn
from lib.product_service import ProductFacade


class VariantBatchTests(ConnTestCase):
    def setUp(self):
        super().setUp()
        c = self.conn
        self.cid = c.execute("INSERT INTO Category(name) VALUES(?)", ("測試種類",)).lastrowid
        # 正式規格:顏色(select,必填)
        self.color_fid = c.execute(
            "INSERT INTO AttributeField(name,field_type) VALUES('顏色','select')").lastrowid
        c.execute("INSERT INTO CategoryField(category_id,field_id,sort,required,active) "
                  "VALUES(?,?,1,1,1)", (self.cid, self.color_fid))
        # 正式規格:長度(select,選填)
        self.len_fid = c.execute(
            "INSERT INTO AttributeField(name,field_type) VALUES('長度','select')").lastrowid
        c.execute("INSERT INTO CategoryField(category_id,field_id,sort,required,active) "
                  "VALUES(?,?,2,0,1)", (self.cid, self.len_fid))
        # 特性詞條(tags,固定欄)
        self.tag_fid = c.execute(
            "INSERT INTO AttributeField(name,field_type) VALUES('特性詞條','tags')").lastrowid
        c.execute("INSERT INTO CategoryField(category_id,field_id,sort,required,active) "
                  "VALUES(?,?,3,0,1)", (self.cid, self.tag_fid))
        # 既有選項
        self.red = c.execute("INSERT INTO AttributeOption(field_id,value,sort) VALUES(?,?,1)",
                             (self.color_fid, "紅")).lastrowid
        self.blue = c.execute("INSERT INTO AttributeOption(field_id,value,sort) VALUES(?,?,2)",
                              (self.color_fid, "藍")).lastrowid
        self.pid = c.execute("INSERT INTO Product(name,category_id) VALUES(?,?)",
                             ("大產品", self.cid)).lastrowid
        c.commit()
        self.conn.close()
        self.facade = ProductFacade(self.db)

    def _fresh(self):
        return get_conn(self.db)

    def _counter(self, conn):
        row = conn.execute("SELECT value FROM Setting WHERE key='next_store_barcode'").fetchone()
        return int(row["value"]) if row else None

    def _variant_count(self, conn):
        return conn.execute("SELECT COUNT(*) c FROM Variant").fetchone()["c"]

    # ---- 成功流程 ----

    def test_batch_create_writes_variants_attributes_models_barcodes(self):
        res = self.facade.invoke("variants.batch_create", {
            "product_id": self.pid, "drafts": [
                {"draft_id": "a", "attributes": {"顏色": "紅"},
                 "price": 100, "barcodes": [{"barcode": "F1", "source": "factory"}]},
                {"draft_id": "b", "attributes": {"顏色": "藍", "特性詞條": ["抗刮"]},
                 "price": 120, "barcodes": [{"source": "store"}]},
            ]})
        self.assertEqual(len(res["results"]), 2)
        self.assertEqual(res["results"][0]["draft_id"], "a")
        conn = self._fresh()
        self.assertEqual(self._variant_count(conn), 2)
        # 自取碼配置
        codes = {r["barcode"] for r in conn.execute("SELECT barcode FROM Barcode")}
        self.assertIn("F1", codes)
        self.assertTrue(any(c.startswith("TL") for c in codes))
        # 特性詞條有寫入(tags 自動建選項)
        self.assertTrue(conn.execute(
            "SELECT 1 FROM VariantAttribute WHERE field_id=?", (self.tag_fid,)).fetchone())
        conn.close()

    # ---- 必填 / model_mode ----

    def test_missing_required_field_rolls_back_whole_batch(self):
        try:
            self.facade.invoke("variants.batch_create", {
                "product_id": self.pid, "drafts": [
                    {"draft_id": "a", "attributes": {"顏色": "紅"}},
                    {"draft_id": "b", "attributes": {"長度": "1m"}},  # 缺必填顏色
                ]})
            self.fail("應該 raise")
        except Exception as exc:
            self.assertTrue(getattr(exc, "details", None))
            idxs = {d["index"] for d in exc.details}
            self.assertIn(1, idxs)
        conn = self._fresh()
        self.assertEqual(self._variant_count(conn), 0)
        conn.close()

    def test_model_mode_required_needs_models(self):
        conn = self._fresh()
        conn.execute("UPDATE Category SET model_mode='required' WHERE category_id=?", (self.cid,))
        conn.commit(); conn.close()
        try:
            self.facade.invoke("variants.batch_create", {
                "product_id": self.pid,
                "drafts": [{"draft_id": "a", "attributes": {"顏色": "紅"}}]})
            self.fail("應該 raise")
        except Exception as exc:
            self.assertTrue(getattr(exc, "details", None))

    # ---- C 規則重複判定 ----

    def test_duplicate_within_batch(self):
        try:
            self.facade.invoke("variants.batch_create", {
                "product_id": self.pid, "drafts": [
                    {"draft_id": "a", "attributes": {"顏色": "紅"}},
                    {"draft_id": "b", "attributes": {"顏色": "紅"}},  # 同簽章
                ]})
            self.fail("應該 raise")
        except Exception as exc:
            self.assertTrue(getattr(exc, "details", None))
        conn = self._fresh()
        self.assertEqual(self._variant_count(conn), 0)
        conn.close()

    def test_duplicate_against_db(self):
        self.facade.invoke("variants.batch_create", {
            "product_id": self.pid,
            "drafts": [{"draft_id": "a", "attributes": {"顏色": "紅"}}]})
        try:
            self.facade.invoke("variants.batch_create", {
                "product_id": self.pid,
                "drafts": [{"draft_id": "b", "attributes": {"顏色": "紅"}}]})
            self.fail("應該 raise")
        except Exception as exc:
            self.assertTrue(getattr(exc, "details", None))
        conn = self._fresh()
        self.assertEqual(self._variant_count(conn), 1)
        conn.close()

    def test_tags_and_price_do_not_participate_in_dedup(self):
        # 同規格、僅詞條/售價不同 → 仍視為重複
        try:
            self.facade.invoke("variants.batch_create", {
                "product_id": self.pid, "drafts": [
                    {"draft_id": "a", "attributes": {"顏色": "紅", "特性詞條": ["A"]}, "price": 100},
                    {"draft_id": "b", "attributes": {"顏色": "紅", "特性詞條": ["B"]}, "price": 200},
                ]})
            self.fail("應該 raise")
        except Exception as exc:
            self.assertTrue(getattr(exc, "details", None))

    def test_models_participate_in_dedup(self):
        conn = self._fresh()
        pb = conn.execute("INSERT INTO PhoneBrand(name) VALUES('iPhone')").lastrowid
        m1 = conn.execute("INSERT INTO PhoneModel(phone_brand_id,name) VALUES(?,?)", (pb, "15")).lastrowid
        m2 = conn.execute("INSERT INTO PhoneModel(phone_brand_id,name) VALUES(?,?)", (pb, "16")).lastrowid
        conn.commit(); conn.close()
        # 同規格但不同型號 → 不重複
        res = self.facade.invoke("variants.batch_create", {
            "product_id": self.pid, "drafts": [
                {"draft_id": "a", "attributes": {"顏色": "紅"}, "model_ids": [m1]},
                {"draft_id": "b", "attributes": {"顏色": "紅"}, "model_ids": [m2]},
            ]})
        self.assertEqual(len(res["results"]), 2)

    # ---- 選項新建 / 重新啟用 ----

    def test_new_option_created_and_used(self):
        res = self.facade.invoke("variants.batch_create", {
            "product_id": self.pid,
            "drafts": [{"draft_id": "a", "attributes": {"顏色": "褐色"}}]})
        self.assertEqual(len(res["created_option_ids"]), 1)
        conn = self._fresh()
        row = conn.execute("SELECT option_id,active FROM AttributeOption WHERE field_id=? AND value='褐色'",
                           (self.color_fid,)).fetchone()
        self.assertIsNotNone(row)
        self.assertTrue(row["active"])
        conn.close()

    def test_disabled_option_reactivated_reuses_option_id(self):
        conn = self._fresh()
        oid = conn.execute("INSERT INTO AttributeOption(field_id,value,sort,active) VALUES(?,?,9,0)",
                           (self.color_fid, "灰")).lastrowid
        conn.commit(); conn.close()
        res = self.facade.invoke("variants.batch_create", {
            "product_id": self.pid,
            "drafts": [{"draft_id": "a", "attributes": {"顏色": "灰"}}]})
        self.assertEqual(res["reactivated_option_ids"], [oid])
        conn = self._fresh()
        self.assertTrue(conn.execute("SELECT active FROM AttributeOption WHERE option_id=?",
                                     (oid,)).fetchone()["active"])
        # 沿用原 option_id
        self.assertTrue(conn.execute("SELECT 1 FROM VariantAttribute WHERE option_id=?",
                                     (oid,)).fetchone())
        conn.close()

    def test_failed_batch_rolls_back_option_reactivation_and_counter(self):
        conn = self._fresh()
        oid = conn.execute("INSERT INTO AttributeOption(field_id,value,sort,active) VALUES(?,?,9,0)",
                           (self.color_fid, "灰")).lastrowid
        before = self._counter(conn)
        conn.commit(); conn.close()
        try:
            self.facade.invoke("variants.batch_create", {
                "product_id": self.pid, "drafts": [
                    {"draft_id": "a", "attributes": {"顏色": "灰"}, "barcodes": [{"source": "store"}]},
                    {"draft_id": "b", "attributes": {"顏色": "灰"}},  # 與 a 重複 → 整批失敗
                ]})
            self.fail("應該 raise")
        except Exception:
            pass
        conn = self._fresh()
        # 重新啟用回復
        self.assertFalse(conn.execute("SELECT active FROM AttributeOption WHERE option_id=?",
                                      (oid,)).fetchone()["active"])
        # 新選項未殘留(無)、自取碼計數器回復
        self.assertEqual(self._counter(conn), before)
        self.assertEqual(self._variant_count(conn), 0)
        conn.close()

    # ---- 條碼 ----

    def test_manual_tl_barcode_rejected(self):
        try:
            self.facade.invoke("variants.batch_create", {
                "product_id": self.pid,
                "drafts": [{"draft_id": "a", "attributes": {"顏色": "紅"},
                            "barcodes": [{"barcode": "TL999", "source": "factory"}]}]})
            self.fail("應該 raise")
        except Exception as exc:
            self.assertEqual(exc.code, "validation_error")

    def test_duplicate_barcode_within_batch_and_against_db(self):
        self.facade.invoke("variants.batch_create", {
            "product_id": self.pid,
            "drafts": [{"draft_id": "a", "attributes": {"顏色": "紅"},
                        "barcodes": [{"barcode": "DUP", "source": "factory"}]}]})
        # 對 DB 重複
        try:
            self.facade.invoke("variants.batch_create", {
                "product_id": self.pid,
                "drafts": [{"draft_id": "b", "attributes": {"顏色": "藍"},
                            "barcodes": [{"barcode": "DUP", "source": "factory"}]}]})
            self.fail("應該 raise")
        except Exception as exc:
            self.assertTrue(getattr(exc, "details", None))

    # ---- 前置:停用大產品/種類 ----

    def test_inactive_product_rejected(self):
        conn = self._fresh()
        conn.execute("UPDATE Product SET active=0 WHERE product_id=?", (self.pid,))
        conn.commit(); conn.close()
        try:
            self.facade.invoke("variants.batch_create", {
                "product_id": self.pid,
                "drafts": [{"draft_id": "a", "attributes": {"顏色": "紅"}}]})
            self.fail("應該 raise")
        except Exception as exc:
            self.assertEqual(exc.code, "validation_error")

    # ---- 可重用使用次數查詢 ----

    def test_field_usage_ordering(self):
        # 紅用 2 次、藍用 1 次
        self.facade.invoke("variants.batch_create", {
            "product_id": self.pid,
            "drafts": [{"draft_id": "a", "attributes": {"顏色": "紅", "長度": "1m"}},
                       {"draft_id": "b", "attributes": {"顏色": "紅", "長度": "2m"}},
                       {"draft_id": "c", "attributes": {"顏色": "藍"}}]})
        usage = self.facade.invoke("variants.field_usage", {
            "category_id": self.cid, "field_id": self.color_fid})
        by = {u["value"]: u["usage_count"] for u in usage}
        self.assertEqual(by["紅"], 2)
        self.assertEqual(by["藍"], 1)
        # 排序:使用次數多者在前
        self.assertEqual(usage[0]["value"], "紅")
