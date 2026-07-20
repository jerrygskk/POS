"""階段 6:VariantIssue 容錯建立、重驗、啟用、待處理彙總與 effective_active。"""

from base import ConnTestCase
from lib.db import get_conn
from lib.application_errors import ValidationError
from lib.product_service import ProductFacade
from lib.variant_batch_service import VariantBatchService
from lib.variant_issue_service import VariantIssueService
from lib.sales_service import SalesRepository


class VariantIssueTests(ConnTestCase):
    def setUp(self):
        super().setUp()
        c = self.conn
        self.cid = c.execute("INSERT INTO Category(name) VALUES(?)", ("測試種類",)).lastrowid
        self.color_fid = c.execute(
            "INSERT INTO AttributeField(name,field_type) VALUES('顏色','select')").lastrowid
        c.execute("INSERT INTO CategoryField(category_id,field_id,sort,required,active) "
                  "VALUES(?,?,1,1,1)", (self.cid, self.color_fid))
        self.len_fid = c.execute(
            "INSERT INTO AttributeField(name,field_type) VALUES('長度','select')").lastrowid
        c.execute("INSERT INTO CategoryField(category_id,field_id,sort,required,active) "
                  "VALUES(?,?,2,0,1)", (self.cid, self.len_fid))
        self.red = c.execute("INSERT INTO AttributeOption(field_id,value,sort) VALUES(?,?,1)",
                             (self.color_fid, "紅")).lastrowid
        self.blue = c.execute("INSERT INTO AttributeOption(field_id,value,sort) VALUES(?,?,2)",
                              (self.color_fid, "藍")).lastrowid
        self.pid = c.execute("INSERT INTO Product(name,category_id) VALUES(?,?)",
                             ("大產品", self.cid)).lastrowid
        c.commit(); c.close()
        self.facade = ProductFacade(self.db)

    def _fresh(self):
        return get_conn(self.db)

    def _tolerant(self, drafts):
        conn = self._fresh()
        try:
            res = VariantBatchService(conn).tolerant_create(
                {"product_id": self.pid, "drafts": drafts})
            conn.commit()
            return res
        finally:
            conn.close()

    def test_issue_and_batch_services_share_whitespace_signature_rules(self):
        conn = self._fresh()
        try:
            text_fid = conn.execute(
                "INSERT INTO AttributeField(name,field_type) VALUES('備註規格','text')"
            ).lastrowid
            blank_fid = conn.execute(
                "INSERT INTO AttributeField(name,field_type) VALUES('空白規格','text')"
            ).lastrowid
            vid = conn.execute("INSERT INTO Variant(product_id) VALUES(?)", (self.pid,)).lastrowid
            conn.execute(
                "INSERT INTO VariantAttribute(variant_id,field_id,text_value) VALUES(?,?,?)",
                (vid, text_fid, "  保留空白  "),
            )
            conn.execute(
                "INSERT INTO VariantAttribute(variant_id,field_id,text_value) VALUES(?,?,?)",
                (vid, blank_fid, "   "),
            )
            issue_signature = VariantIssueService(conn)._signature(vid, None)
            batch_signatures = VariantBatchService(conn)._existing_signatures(self.pid, None)
            self.assertEqual(batch_signatures, {issue_signature: vid})
            self.assertIn((text_fid, "t", "  保留空白  "), issue_signature)
            self.assertFalse(any(part[0] == blank_fid for part in issue_signature))
        finally:
            conn.close()

    def _issues(self, conn, vid):
        return conn.execute("SELECT issue_type,field_id,source_value,related_variant_id "
                            "FROM VariantIssue WHERE variant_id=? ORDER BY issue_id", (vid,)).fetchall()

    # ---- 容錯建立 ----

    def test_tolerant_missing_required_creates_disabled_variant_with_issue(self):
        res = self._tolerant([{"draft_id": "a", "attributes": {"長度": "1m"}}])  # 缺必填顏色
        vid = res["results"][0]["variant_id"]
        conn = self._fresh()
        self.assertEqual(conn.execute("SELECT active FROM Variant WHERE variant_id=?", (vid,)).fetchone()["active"], 0)
        rows = self._issues(conn, vid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["issue_type"], "missing_required")
        self.assertEqual(rows[0]["field_id"], self.color_fid)
        conn.close()

    def test_tolerant_normal_draft_stays_active_no_issue(self):
        res = self._tolerant([{"draft_id": "a", "attributes": {"顏色": "紅"}}])
        vid = res["results"][0]["variant_id"]
        conn = self._fresh()
        self.assertEqual(conn.execute("SELECT active FROM Variant WHERE variant_id=?", (vid,)).fetchone()["active"], 1)
        self.assertEqual(len(self._issues(conn, vid)), 0)
        conn.close()

    def test_tolerant_duplicate_signature_flags_later_variant(self):
        res = self._tolerant([
            {"draft_id": "a", "attributes": {"顏色": "紅"}},
            {"draft_id": "b", "attributes": {"顏色": "紅"}}])
        first, second = res["results"][0]["variant_id"], res["results"][1]["variant_id"]
        conn = self._fresh()
        self.assertEqual(len(self._issues(conn, first)), 0)
        rows = self._issues(conn, second)
        self.assertEqual(rows[0]["issue_type"], "duplicate_signature")
        self.assertEqual(rows[0]["related_variant_id"], first)
        conn.close()

    def test_tolerant_duplicate_barcode_preserves_source_value(self):
        res = self._tolerant([
            {"draft_id": "a", "attributes": {"顏色": "紅"}, "barcodes": [{"barcode": "DUP", "source": "factory"}]},
            {"draft_id": "b", "attributes": {"顏色": "藍"}, "barcodes": [{"barcode": "DUP", "source": "factory"}]}])
        second = res["results"][1]["variant_id"]
        conn = self._fresh()
        rows = self._issues(conn, second)
        self.assertEqual(rows[0]["issue_type"], "duplicate_barcode")
        self.assertEqual(rows[0]["source_value"], "DUP")
        # 重複條碼未寫入正式欄位(仍只有第一筆擁有)
        owners = [r["variant_id"] for r in conn.execute("SELECT variant_id FROM Barcode WHERE barcode='DUP'")]
        self.assertEqual(owners, [res["results"][0]["variant_id"]])
        conn.close()

    def test_tolerant_structural_error_rejected(self):
        with self.assertRaises(ValidationError):
            self._tolerant([{"draft_id": "a", "attributes": {"不存在欄": "x"}}])

    # ---- 重驗 ----

    def test_revalidate_clears_resolved_and_allows_activation(self):
        vid = self._tolerant([{"draft_id": "a", "attributes": {"長度": "1m"}}])["results"][0]["variant_id"]
        # 補上必填顏色
        self.facade.invoke("variants.update_details", {
            "id": vid, "fields": {"attributes": {"顏色": "紅", "長度": "1m"}}})
        conn = self._fresh()
        self.assertEqual(len(self._issues(conn, vid)), 0)
        conn.close()

    def test_revalidate_keeps_remaining_issue(self):
        # 兩問題:缺顏色 + 條碼重複
        self._tolerant([{"draft_id": "x", "attributes": {"顏色": "藍"}, "barcodes": [{"barcode": "DUP", "source": "factory"}]}])
        vid = self._tolerant([{"draft_id": "a", "attributes": {"長度": "1m"}, "barcodes": [{"barcode": "DUP", "source": "factory"}]}])["results"][0]["variant_id"]
        conn = self._fresh(); self.assertEqual(len(self._issues(conn, vid)), 2); conn.close()
        # 只補顏色,條碼仍衝突
        self.facade.invoke("variants.update_details", {
            "id": vid, "fields": {"attributes": {"顏色": "紅"}}})
        conn = self._fresh()
        rows = self._issues(conn, vid)
        self.assertEqual([r["issue_type"] for r in rows], ["duplicate_barcode"])
        conn.close()

    def test_revalidate_resolvable_barcode_written(self):
        vid = self._tolerant([{"draft_id": "a", "attributes": {"顏色": "紅"},
                               "barcodes": [{"barcode": "GONE", "source": "factory"}]}])["results"][0]["variant_id"]
        # 手動塞一個佔用者再移除,模擬衝突消失:直接建立問題無衝突來源
        conn = self._fresh()
        # 人工把此筆改成 duplicate_barcode 問題(來源值目前無人佔用)
        conn.execute("DELETE FROM Barcode WHERE barcode='GONE'")
        conn.execute("UPDATE Variant SET active=0 WHERE variant_id=?", (vid,))
        conn.execute("INSERT INTO VariantIssue(variant_id,issue_type,source_value) VALUES(?,?,?)",
                     (vid, "duplicate_barcode", "GONE"))
        conn.commit(); conn.close()
        state = self.facade.invoke("variants.update_details", {"id": vid, "fields": {"price": 50}})
        conn = self._fresh()
        self.assertEqual(len(self._issues(conn, vid)), 0)
        # 條碼補寫回正式欄位
        self.assertTrue(conn.execute("SELECT 1 FROM Barcode WHERE barcode='GONE' AND variant_id=?", (vid,)).fetchone())
        conn.close()

    # ---- 啟用前完整驗證 ----

    def test_activate_rejects_when_issue_remains(self):
        vid = self._tolerant([{"draft_id": "a", "attributes": {"長度": "1m"}}])["results"][0]["variant_id"]
        with self.assertRaises(ValidationError):
            self.facade.invoke("variants.activate", {"id": vid})
        conn = self._fresh()
        self.assertEqual(conn.execute("SELECT active FROM Variant WHERE variant_id=?", (vid,)).fetchone()["active"], 0)
        conn.close()

    def test_activate_succeeds_after_cleared(self):
        vid = self._tolerant([{"draft_id": "a", "attributes": {"長度": "1m"}}])["results"][0]["variant_id"]
        self.facade.invoke("variants.update_details", {"id": vid, "fields": {"attributes": {"顏色": "紅"}}})
        self.facade.invoke("variants.activate", {"id": vid})
        conn = self._fresh()
        self.assertEqual(conn.execute("SELECT active FROM Variant WHERE variant_id=?", (vid,)).fetchone()["active"], 1)
        conn.close()

    def test_update_active_blocked_when_issue_remains(self):
        # 繞道:直接用 update 設 active=1,仍須被擋
        vid = self._tolerant([{"draft_id": "a", "attributes": {"長度": "1m"}}])["results"][0]["variant_id"]
        with self.assertRaises(ValidationError):
            self.facade.invoke("variants.update", {"id": vid, "fields": {"active": 1}})
        conn = self._fresh()
        self.assertEqual(conn.execute("SELECT active FROM Variant WHERE variant_id=?", (vid,)).fetchone()["active"], 0)
        conn.close()

    # ---- effective_active ----

    def test_variant_with_issue_not_sellable(self):
        vid = self._tolerant([{"draft_id": "a", "attributes": {"長度": "1m"},
                               "barcodes": [{"barcode": "SCANME", "source": "factory"}]}])["results"][0]["variant_id"]
        # 掃碼成交:active 應為 False
        scanned = self.facade.invoke("barcodes.scan", {"code": "SCANME"})
        self.assertFalse(scanned["active"])
        # 銷售層 variant_is_active 亦為 0
        conn = self._fresh()
        self.assertEqual(SalesRepository(conn).variant_is_active(vid)["ok"], 0)
        conn.close()
        # 補齊必填後可銷售
        self.facade.invoke("variants.update_details", {"id": vid, "fields": {"attributes": {"顏色": "紅"}}})
        self.facade.invoke("variants.activate", {"id": vid})
        self.assertTrue(self.facade.invoke("barcodes.scan", {"code": "SCANME"})["active"])

    # ---- 待處理彙總 ----

    def test_issues_summary_counts(self):
        self._tolerant([
            {"draft_id": "a", "attributes": {"長度": "1m"}},          # 缺必填
            {"draft_id": "b", "attributes": {"顏色": "紅"}},           # 正常
            {"draft_id": "c", "attributes": {"顏色": "紅"}}])          # 與 b 重複
        summary = self.facade.invoke("variants.issues", {})
        self.assertEqual(summary["pending_variant_count"], 2)
        self.assertEqual(summary["by_type"]["missing_required"], 1)
        self.assertEqual(summary["by_type"]["duplicate_signature"], 1)
        self.assertEqual(summary["by_category"][self.cid], 2)

    def test_catalog_pending_filter(self):
        self._tolerant([
            {"draft_id": "a", "attributes": {"長度": "1m"}},
            {"draft_id": "b", "attributes": {"顏色": "紅"}}])
        data = self.facade.invoke("catalog.list", {"pending": True})
        vids = [v for p in data for v in p["variants"]]
        self.assertEqual(len(vids), 1)
        self.assertTrue(vids[0]["issues"])
