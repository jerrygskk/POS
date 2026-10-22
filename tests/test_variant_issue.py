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
                             ("產品", self.cid)).lastrowid
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

    def test_tolerant_shared_barcode_keeps_both_owners_without_issue(self):
        res = self._tolerant([
            {"draft_id": "a", "attributes": {"顏色": "紅"}, "barcodes": [{"barcode": "DUP", "source": "factory"}]},
            {"draft_id": "b", "attributes": {"顏色": "藍"}, "barcodes": [{"barcode": "DUP", "source": "factory"}]}])
        conn = self._fresh()
        try:
            for result in res["results"]:
                self.assertEqual(list(self._issues(conn, result["variant_id"])), [])
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM Barcode WHERE variant_id=? AND source='store'",
                                             (result["variant_id"],)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM Barcode WHERE barcode='DUP'").fetchone()[0], 2)
        finally:
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

    def test_revalidate_keeps_remaining_required_issue(self):
        vid = self._tolerant([{"draft_id": "a", "attributes": {"長度": "1m"}}])["results"][0]["variant_id"]
        self.facade.invoke("variants.update_details", {"id": vid, "fields": {"price": 50}})
        conn = self._fresh()
        try:
            self.assertEqual([r["issue_type"] for r in self._issues(conn, vid)], ["missing_required"])
        finally:
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
        scanned = self.facade.invoke("barcodes.scan", {"code": "SCANME"})["matches"][0]
        self.assertEqual(scanned["status"], "issue")
        # 銷售層 variant_is_active 亦為 0
        conn = self._fresh()
        self.assertEqual(SalesRepository(conn).variant_is_active(vid)["ok"], 0)
        conn.close()
        # 補齊必填後可銷售
        self.facade.invoke("variants.update_details", {"id": vid, "fields": {"attributes": {"顏色": "紅"}}})
        self.facade.invoke("variants.activate", {"id": vid})
        self.assertEqual(self.facade.invoke("barcodes.scan", {"code": "SCANME"})["matches"][0]["status"], "active")

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
