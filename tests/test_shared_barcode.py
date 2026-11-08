"""共用原廠碼、v17 升級與批次進貨回歸。"""
import sqlite3
from contextlib import closing

from tests.base import FacadeTestCase
from lib.db import get_conn, init_db
from lib.application_errors import ConflictError, NotFoundError, ValidationError
from lib.printing_service import PrintingRepository
from lib.product_rules import format_store_barcode


class SharedBarcodeTests(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.make_category_with_field("color", options=("red", "blue", "green"))

    def create(self, name, color="red", code="SHARED", store=False):
        barcodes = [{"barcode": code, "source": "factory"}]
        if store:
            barcodes.append({"source": "store"})
        return self.invoke("products.create", {"name": name, "category_id": self.cid,
            "variants": [{"attributes": {"color": color}, "price": 100, "barcodes": barcodes}]})["variant_ids"][0]

    def stores(self, vid):
        with closing(get_conn(self.db)) as conn, conn:
            return [r[0] for r in conn.execute("SELECT barcode FROM Barcode WHERE variant_id=? AND source='store' ORDER BY barcode", (vid,))]

    def test_sharing_assigns_store_once_and_replaces_deleted_only_store(self):
        a = self.create("Alpha")
        b = self.create("Beta", "blue", store=True)
        self.assertEqual(len(self.stores(a)), 1)
        self.assertEqual(len(self.stores(b)), 1)
        old = self.stores(b)[0]
        result = self.invoke("variants.update_editor", {"id": b, "deleted_barcodes": [old]})
        self.assertEqual(len(self.stores(b)), 1)
        self.assertNotEqual(self.stores(b)[0], old)
        self.assertEqual(result["store_barcodes"], self.stores(b))

    def test_editor_allows_shared_factory_rejects_same_owner_store_and_tl(self):
        a = self.create("Alpha")
        b = self.create("Beta", "blue", code="OTHER")
        self.invoke("variants.update_editor", {"id": b, "factory_barcodes": ["SHARED"]})
        self.assertEqual(len(self.stores(a)), 1)
        with self.assertRaisesRegex(ConflictError, "此條碼已在此子產品"):
            self.invoke("variants.update_editor", {"id": b, "factory_barcodes": ["SHARED"]})
        with closing(get_conn(self.db)) as conn, conn:
            conn.execute("INSERT INTO Barcode(barcode,variant_id,source) VALUES('CUSTOM-STORE',?,'store')", (a,))
        with self.assertRaisesRegex(ConflictError, "此條碼已存在"):
            self.invoke("variants.update_editor", {"id": b, "factory_barcodes": ["CUSTOM-STORE"]})
        with self.assertRaises(ValidationError):
            self.invoke("variants.update_editor", {"id": b, "factory_barcodes": ["TL123"]})
        with self.assertRaisesRegex(ConflictError, "此條碼已存在"):
            self.invoke("variants.create", {"product_id": self.invoke("catalog.list")[0]["product_id"],
                "fields": {"barcodes": [{"barcode": "SHARED", "source": "store"}]}})

    def test_scan_order_exact_fields_status_and_label_priority(self):
        b = self.create("Beta", "blue")
        a = self.create("Alpha")
        with closing(get_conn(self.db)) as conn, conn:
            conn.execute("UPDATE Variant SET active=0 WHERE variant_id=?", (b,))
            conn.execute("INSERT INTO VariantIssue(variant_id,issue_type) VALUES(?,'missing_required')", (b,))
        matches = self.invoke("barcodes.scan", {"code": "SHARED"})["matches"]
        self.assertEqual([r["variant_id"] for r in matches], [a, b])
        expected = {"variant_id", "product_id", "name", "price", "attributes", "attr_display", "models", "stock", "status", "store_barcode", "label_barcode"}
        for row in matches:
            self.assertEqual(set(row), expected)
            self.assertEqual(row["label_barcode"], self.stores(row["variant_id"])[0])
        self.assertEqual([r["status"] for r in matches], ["active", "issue"])

    def test_scan_single_null_category_inactive_parents_and_minimum_barcodes(self):
        a = self.create("Alpha", code="Z-FACTORY")
        self.invoke("variants.update_editor", {"id": a, "factory_barcodes": ["A-FACTORY"]})
        row = self.invoke("barcodes.scan", {"code": "Z-FACTORY"})["matches"][0]
        self.assertIsNone(row["store_barcode"])
        self.assertEqual(row["label_barcode"], "A-FACTORY")
        with closing(get_conn(self.db)) as conn, conn:
            conn.execute("UPDATE Category SET active=0 WHERE category_id=?", (self.cid,))
        self.assertEqual(self.invoke("barcodes.scan", {"code": "Z-FACTORY"})["matches"][0]["status"], "inactive")
        with closing(get_conn(self.db)) as conn, conn:
            conn.execute("UPDATE Product SET category_id=NULL WHERE product_id=(SELECT product_id FROM Variant WHERE variant_id=?)", (a,))
            conn.execute("INSERT INTO Barcode(barcode,variant_id,source) VALUES('STORE-Z',?,'store')", (a,))
            conn.execute("INSERT INTO Barcode(barcode,variant_id,source) VALUES('STORE-A',?,'store')", (a,))
        row = self.invoke("barcodes.scan", {"code": "Z-FACTORY"})["matches"][0]
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["label_barcode"], "STORE-A")
        with closing(get_conn(self.db)) as conn, conn:
            conn.execute("UPDATE Product SET active=0")
        self.assertEqual(self.invoke("barcodes.scan", {"code": "Z-FACTORY"})["matches"][0]["status"], "inactive")

    def test_print_ownership_checks_each_shared_owner_and_catalog_marks_shared(self):
        a = self.create("Alpha")
        b = self.create("Beta", "blue")
        c = self.create("Gamma", "green", "SOLO")
        with closing(get_conn(self.db)) as conn, conn:
            repo = PrintingRepository(conn)
            self.assertTrue(repo.label_content(a, "SHARED")[1])
            self.assertTrue(repo.label_content(b, "SHARED")[1])
            self.assertFalse(repo.label_content(c, "SHARED")[1])
        for product in self.invoke("catalog.list"):
            for variant in product["variants"]:
                for barcode in variant["barcodes"]:
                    self.assertEqual(barcode["shared"], barcode["barcode"] == "SHARED")

    def test_batch_notices_include_first_owner_and_store_collision_is_error(self):
        pid = self.invoke("products.create", {"name": "Batch", "category_id": self.cid})["product_id"]
        def drafts(codes):
            return [{"draft_id": str(i), "attributes": {"color": color},
                     "barcodes": [{"barcode": code, "source": "factory"}]}
                    for i, (code, color) in enumerate(zip(codes, ("red", "blue", "green")))]
        pre = self.invoke("variants.batch_precheck", {"product_id": pid, "drafts": drafts(["ONE", "DUP", "DUP"])})
        self.assertEqual([bool(r["notices"]) for r in pre["results"]], [False, True, True])
        self.assertTrue(all(not r["errors"] for r in pre["results"]))
        batch = drafts(["SHARED", "SHARED"])
        pre = self.invoke("variants.batch_precheck", {"product_id": pid, "drafts": batch})
        self.assertTrue(all(r["notices"] == [{"code": "shared_barcode", "message": "(已重複)"}] for r in pre["results"]))
        result = self.invoke("variants.batch_create", {"product_id": pid, "drafts": batch})
        self.assertTrue(all(len(self.stores(r["variant_id"])) == 1 for r in result["results"]))
        old = result["results"][0]["variant_id"]
        with closing(get_conn(self.db)) as conn, conn:
            conn.execute("INSERT INTO Barcode(barcode,variant_id,source) VALUES('CLASH',?,'store')", (old,))
        pre = self.invoke("variants.batch_precheck", {"product_id": pid, "drafts": drafts(["CLASH"])[0:1]})
        self.assertTrue(any(e["code"] == "duplicate_barcode" for e in pre["results"][0]["errors"]))
        with self.assertRaises(ValidationError):
            self.invoke("variants.batch_create", {"product_id": pid, "drafts": drafts(["CLASH"])})
        with closing(get_conn(self.db)) as conn, conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM Variant WHERE product_id=?", (pid,)).fetchone()[0], 2)



class BatchReceiveTests(FacadeTestCase):
    def setUp(self):
        super().setUp()
        cid=self.create_category("BatchReceive")
        self.a,self.b=self.invoke("products.create", {"name":"BatchReceive","category_id":cid,"variants":[{},{}]})["variant_ids"]

    def test_multiple_items_duplicates_and_employee(self):
        result=self.invoke("stock.receive", {"employee":3,"items":[{"variant_id":self.a,"qty":2},{"variant_id":self.b,"qty":4},{"variant_id":self.a,"qty":1}]})
        self.assertEqual(result,{"receipt_id":result["receipt_id"],"items":[{"variant_id":self.a,"stock":3},{"variant_id":self.b,"stock":4},{"variant_id":self.a,"stock":3}]})
        with closing(get_conn(self.db)) as conn, conn:
            # 一張進貨單、員工記在單上;同款式兩列各自保留數量
            self.assertEqual([tuple(r) for r in conn.execute("SELECT receipt_id,employee FROM Receipt")], [(result["receipt_id"],3)])
            self.assertEqual([tuple(r) for r in conn.execute("SELECT i.variant_id,i.qty,m.qty,m.employee FROM ReceiptItem i JOIN StockMovement m ON m.receipt_item_id=i.receipt_item_id ORDER BY i.receipt_item_id")], [(self.a,2,2,None),(self.b,4,4,None),(self.a,1,1,None)])

    def test_unknown_item_rolls_back_all(self):
        with self.assertRaises(NotFoundError):
            self.invoke("stock.receive", {"employee":1,"items":[{"variant_id":self.a,"qty":2},{"variant_id":999999,"qty":1}]})
        with closing(get_conn(self.db)) as conn, conn:
            self.assertEqual([conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("StockMovement","ReceiptItem","Receipt")],[0,0,0])

    def test_payload_validation(self):
        for payload in ({"employee":1,"items":[]}, {"items":[{"variant_id":self.a,"qty":1}]},
                        {"employee":1,"items":[True]}, {"employee":1,"items":[{"variant_id":True,"qty":1}]},
                        {"employee":1,"items":[{"variant_id":self.a,"qty":0}]},
                        {"employee":1,"items":[{"variant_id":self.a,"qty":True}]},
                        {"employee":1,"items":[{"variant_id":self.a,"qty":1,"extra":1}]},
                        {"employee":1,"items":[{"variant_id":self.a,"qty":1}],"note":"removed"}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                self.invoke("stock.receive",payload)
