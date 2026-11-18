"""銷售與退貨明細保留成交當下的品名、規格:商品事後改名或刪規格欄,舊單內容不變。所有資料只寫暫存庫。"""
import sqlite3

from base import FacadeTestCase, PAY
from lib.db import get_conn


class SaleSnapshotTest(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.facade.session.login("admin")
        self.cid, self.fid = self.make_category_with_field("顏色", options=("黑",))
        made = self.invoke("products.create", {"name": "舊名", "category_id": self.cid, "variants": [
            {"attributes": {"顏色": "黑"}, "price": 100, "barcodes": [{"barcode": "SNAP-1", "source": "store"}]}]})
        self.pid, self.vid = made["product_id"], made["variant_ids"][0]
        self.invoke("stock.receive", {"employee": 1, "items": [{"variant_id": self.vid, "qty": 5}]})
        self.sale_id = self.invoke("sales.checkout", {"payment_id": PAY["現金"], "paid": 100, "employee": 1,
            "items": [{"variant_id": self.vid, "qty": 1, "unit_price": 100}]})["sale_id"]

    def item(self):
        return self.invoke("sales.list", {})[0]["items"][0]

    def test_rename_and_field_delete_keep_sold_content(self):
        self.assertEqual((self.item()["name"], self.item()["attr_display"]), ("舊名", "黑"))
        self.invoke("products.update", {"id": self.pid, "fields": {"name": "新名"}})
        self.invoke("categories.delete_field", {"category_id": self.cid, "field_id": self.fid})
        self.assertEqual((self.item()["name"], self.item()["attr_display"]), ("舊名", "黑"))
        csv = self.invoke("sales.export", {})["content"]
        self.assertIn("舊名", csv)
        self.assertNotIn("新名", csv)

    def test_sale_return_uses_sale_snapshot_and_scan_return_uses_current(self):
        self.invoke("products.update", {"id": self.pid, "fields": {"name": "新名"}})
        rid = self.invoke("returns.create", {"employee": 1, "kind": "normal"})["return_id"]
        self.invoke("returns.load_sale", {"return_id": rid, "sale_id": self.sale_id})
        line = self.invoke("returns.detail", {"return_id": rid})["sale_lines"][0]
        self.assertEqual((line["name"], line["attr_display"]), ("舊名", "黑"))
        self.invoke("returns.pick", {"return_id": rid, "sale_item_id": line["item_id"], "qty": 1})
        self.assertEqual(self.invoke("returns.detail", {"return_id": rid})["items"][0]["name"], "舊名")
        scan = self.invoke("returns.create", {"employee": 1, "kind": "normal"})["return_id"]
        self.invoke("returns.scan", {"return_id": scan, "variant_id": self.vid, "qty": 1})
        self.assertEqual(self.invoke("returns.detail", {"return_id": scan})["items"][0]["name"], "新名")

    def test_booked_snapshot_is_locked(self):
        with get_conn(self.db) as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE SaleItem SET name='竄改' WHERE sale_id=?", (self.sale_id,))
