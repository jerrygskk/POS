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
                             ("產品", self.cid)).lastrowid
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

    def _table_counts(self, conn):
        return [conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
                for table in ("Variant", "AttributeOption", "Barcode", "VariantAttribute")]

    # ---- 驗證段 / 結構化錯誤 ----

    def test_validate_batch_dry_creates_nothing(self):
        from lib.variant_batch_service import VariantBatchService
        conn = self._fresh()
        before = [tuple(r) for r in conn.execute(
            "SELECT option_id,value,active FROM AttributeOption ORDER BY option_id")]
        svc = VariantBatchService(conn)
        resolved, _ = svc._validate_batch({"product_id": self.pid, "drafts": [
            {"draft_id": "d1", "attributes": {"顏色": "全新色值X"},
             "model_ids": [], "barcodes": [{"source": "store"}]}]}, dry=True)
        after = [tuple(r) for r in conn.execute(
            "SELECT option_id,value,active FROM AttributeOption ORDER BY option_id")]
        self.assertEqual(before, after)
        self.assertEqual(resolved[0]["errors"], [])
        conn.close()

    def test_errors_are_structured(self):
        try:
            self.facade.invoke("variants.batch_create", {"product_id": self.pid, "drafts": [
                {"barcodes": [{"source": "store"}], "draft_id": "d1", "attributes": {"長度": "1m"}}]})
            self.fail("應整批拒絕")
        except Exception as exc:
            err = exc.details[0]["errors"][0]
            self.assertEqual(err["code"], "missing_required")
            self.assertEqual(err["field_id"], self.color_fid)
            self.assertIn("顏色", err["message"])

    def test_duplicate_within_batch_carries_related_draft_id(self):
        try:
            self.facade.invoke("variants.batch_create", {"product_id": self.pid, "drafts": [
                {"barcodes": [{"source": "store"}], "draft_id": "a", "attributes": {"顏色": "紅"}},
                {"barcodes": [{"source": "store"}], "draft_id": "b", "attributes": {"顏色": "紅"}}]})
            self.fail("應整批拒絕")
        except Exception as exc:
            errs = [e for d in exc.details for e in d["errors"]
                    if e["code"] == "duplicate_signature"]
            self.assertEqual(errs[0]["related_draft_id"], "a")
            self.assertIsNone(errs[0]["related_variant_id"])

    def test_precheck_reports_without_writing(self):
        self.facade.invoke("variants.batch_create", {"product_id": self.pid, "drafts": [
            {"barcodes": [{"source": "store"}], "draft_id": "base", "attributes": {"顏色": "紅"}}]})
        conn = self._fresh()
        before = self._table_counts(conn)
        res = self.facade.invoke("variants.batch_precheck", {"product_id": self.pid, "drafts": [
            {"barcodes": [{"source": "store"}], "draft_id": "d1", "attributes": {"顏色": "紅"}},
            {"barcodes": [{"source": "store"}], "draft_id": "d2", "attributes": {"顏色": "全新色值Y"}}]})
        self.assertEqual(self._table_counts(conn), before)
        conn.close()
        self.assertTrue(res["results"][0]["existing_duplicate"])
        self.assertIsNotNone(res["results"][0]["related_variant_id"])
        self.assertFalse(res["results"][1]["existing_duplicate"])
        self.assertEqual(res["results"][1]["errors"], [])
        self.assertEqual(res["summary"],
                         {"total": 2, "invalid": 1, "existing_duplicates": 1})

    def test_precheck_matches_batch_create_verdict(self):
        ok_payload = {"product_id": self.pid, "drafts": [
            {"barcodes": [{"source": "store"}], "draft_id": "d1", "attributes": {"顏色": "紅"}, "price": 100}]}
        bad_payload = {"product_id": self.pid, "drafts": [
            {"barcodes": [{"source": "store"}], "draft_id": "d1", "attributes": {"長度": "1m"}}]}
        self.assertEqual(self.facade.invoke(
            "variants.batch_precheck", ok_payload)["summary"]["invalid"], 0)
        self.assertTrue(self.facade.invoke(
            "variants.batch_precheck", bad_payload)["summary"]["invalid"])
        self.facade.invoke("variants.batch_create", ok_payload)
        with self.assertRaises(Exception):
            self.facade.invoke("variants.batch_create", bad_payload)

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
                    {"barcodes": [{"source": "store"}], "draft_id": "a", "attributes": {"顏色": "紅"}},
                    {"barcodes": [{"source": "store"}], "draft_id": "b", "attributes": {"長度": "1m"}},  # 缺必填顏色
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
                "drafts": [{"barcodes": [{"source": "store"}], "draft_id": "a", "attributes": {"顏色": "紅"}}]})
            self.fail("應該 raise")
        except Exception as exc:
            self.assertTrue(getattr(exc, "details", None))

    def test_batch_create_requires_a_barcode_or_store_code(self):
        """每筆至少要有一組條碼:沒有廠商條碼就得配自取碼。"""
        no_code = {"product_id": self.pid, "drafts": [
            {"draft_id": "a", "attributes": {"顏色": "紅"}, "barcodes": []}]}
        res = self.facade.invoke("variants.batch_precheck", no_code)
        self.assertEqual([e["code"] for e in res["results"][0]["errors"]],
                         ["missing_barcode"])
        with self.assertRaises(Exception):
            self.facade.invoke("variants.batch_create", no_code)
        conn = self._fresh()
        self.assertEqual(self._variant_count(conn), 0)
        conn.close()
        # 只勾自取碼即可通過
        self.facade.invoke("variants.batch_create", {"product_id": self.pid, "drafts": [
            {"draft_id": "a", "attributes": {"顏色": "紅"},
             "barcodes": [{"source": "store"}]}]})
        conn = self._fresh()
        self.assertEqual(self._variant_count(conn), 1)
        conn.close()

    def test_model_usage_marks_lead_by_product_then_brand(self):
        """型號候選前排:這個產品用過的優先,產品沒紀錄退回同廠牌用過的。"""
        from lib.product_data import model_usage_in_category
        conn = self._fresh()
        pb = conn.execute("INSERT INTO PhoneBrand(name) VALUES('iPhone')").lastrowid
        m1 = conn.execute("INSERT INTO PhoneModel(phone_brand_id,name) VALUES(?,?)",
                          (pb, "15")).lastrowid
        m2 = conn.execute("INSERT INTO PhoneModel(phone_brand_id,name) VALUES(?,?)",
                          (pb, "16")).lastrowid
        m3 = conn.execute("INSERT INTO PhoneModel(phone_brand_id,name) VALUES(?,?)",
                          (pb, "17")).lastrowid
        bid = conn.execute("INSERT INTO Brand(name) VALUES('某廠')").lastrowid
        conn.execute("UPDATE Product SET brand_id=? WHERE product_id=?", (bid, self.pid))
        other = conn.execute(
            "INSERT INTO Product(name,category_id,brand_id) VALUES(?,?,?)",
            ("同廠牌另一個產品", self.cid, bid)).lastrowid
        v1 = conn.execute("INSERT INTO Variant(product_id) VALUES(?)", (self.pid,)).lastrowid
        conn.execute("INSERT INTO VariantModel(variant_id,model_id) VALUES(?,?)", (v1, m1))
        v2 = conn.execute("INSERT INTO Variant(product_id) VALUES(?)", (other,)).lastrowid
        conn.execute("INSERT INTO VariantModel(variant_id,model_id) VALUES(?,?)", (v2, m2))
        conn.commit()

        by_product = {r["model_id"]: r for r in
                      model_usage_in_category(conn, self.cid, brand_id=bid,
                                              product_id=self.pid)}
        self.assertTrue(by_product[m1]["lead"])
        self.assertFalse(by_product[m2]["lead"])   # 產品有紀錄就不退回廠牌
        self.assertFalse(by_product[m3]["lead"])
        self.assertEqual(by_product[m2]["usage_count"], 1)  # 種類次數仍算得到

        # 全新產品(自己沒紀錄)退回同廠牌用過的型號
        fresh = conn.execute(
            "INSERT INTO Product(name,category_id,brand_id) VALUES(?,?,?)",
            ("全新產品", self.cid, bid)).lastrowid
        by_brand = {r["model_id"]: r for r in
                    model_usage_in_category(conn, self.cid, brand_id=bid,
                                            product_id=fresh)}
        self.assertTrue(by_brand[m1]["lead"] and by_brand[m2]["lead"])
        self.assertFalse(by_brand[m3]["lead"])

        # 都沒有紀錄時不指定前排(前端全部展開)
        none_lead = model_usage_in_category(conn, self.cid)
        self.assertFalse(any(r["lead"] for r in none_lead))
        conn.close()

    # ---- C 規則重複判定 ----

    def test_duplicate_within_batch(self):
        try:
            self.facade.invoke("variants.batch_create", {
                "product_id": self.pid, "drafts": [
                    {"barcodes": [{"source": "store"}], "draft_id": "a", "attributes": {"顏色": "紅"}},
                    {"barcodes": [{"source": "store"}], "draft_id": "b", "attributes": {"顏色": "紅"}},  # 同簽章
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
            "drafts": [{"barcodes": [{"source": "store"}], "draft_id": "a", "attributes": {"顏色": "紅"}}]})
        try:
            self.facade.invoke("variants.batch_create", {
                "product_id": self.pid,
                "drafts": [{"barcodes": [{"source": "store"}], "draft_id": "b", "attributes": {"顏色": "紅"}}]})
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
                    {"barcodes": [{"source": "store"}], "draft_id": "a", "attributes": {"顏色": "紅", "特性詞條": ["A"]}, "price": 100},
                    {"barcodes": [{"source": "store"}], "draft_id": "b", "attributes": {"顏色": "紅", "特性詞條": ["B"]}, "price": 200},
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
                {"barcodes": [{"source": "store"}], "draft_id": "a", "attributes": {"顏色": "紅"}, "model_ids": [m1]},
                {"barcodes": [{"source": "store"}], "draft_id": "b", "attributes": {"顏色": "紅"}, "model_ids": [m2]},
            ]})
        self.assertEqual(len(res["results"]), 2)

    # ---- 選項新建 / 重新啟用 ----

    def test_new_option_created_and_used(self):
        res = self.facade.invoke("variants.batch_create", {
            "product_id": self.pid,
            "drafts": [{"barcodes": [{"source": "store"}], "draft_id": "a", "attributes": {"顏色": "褐色"}}]})
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
            "drafts": [{"barcodes": [{"source": "store"}], "draft_id": "a", "attributes": {"顏色": "灰"}}]})
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
                    {"barcodes": [{"source": "store"}], "draft_id": "b", "attributes": {"顏色": "灰"}},  # 與 a 重複 → 整批失敗
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

    # ---- 前置:停用產品/種類 ----

    def test_inactive_product_rejected(self):
        conn = self._fresh()
        conn.execute("UPDATE Product SET active=0 WHERE product_id=?", (self.pid,))
        conn.commit(); conn.close()
        try:
            self.facade.invoke("variants.batch_create", {
                "product_id": self.pid,
                "drafts": [{"barcodes": [{"source": "store"}], "draft_id": "a", "attributes": {"顏色": "紅"}}]})
            self.fail("應該 raise")
        except Exception as exc:
            self.assertEqual(exc.code, "validation_error")

    # ---- 可重用使用次數查詢 ----

    def test_field_usage_ordering(self):
        # 紅用 2 次、藍用 1 次
        self.facade.invoke("variants.batch_create", {
            "product_id": self.pid,
            "drafts": [{"barcodes": [{"source": "store"}], "draft_id": "a", "attributes": {"顏色": "紅", "長度": "1m"}},
                       {"barcodes": [{"source": "store"}], "draft_id": "b", "attributes": {"顏色": "紅", "長度": "2m"}},
                       {"barcodes": [{"source": "store"}], "draft_id": "c", "attributes": {"顏色": "藍"}}]})
        usage = self.facade.invoke("variants.field_usage", {
            "category_id": self.cid, "field_id": self.color_fid})
        by = {u["value"]: u["usage_count"] for u in usage}
        self.assertEqual(by["紅"], 2)
        self.assertEqual(by["藍"], 1)
        # 排序:使用次數多者在前
        self.assertEqual(usage[0]["value"], "紅")
