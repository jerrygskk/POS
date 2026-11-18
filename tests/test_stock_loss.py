"""商品報損:只扣庫存、限管理者、不影響立帳金額。"""
import sqlite3

from base import FacadeTestCase, add_stock
from lib.db import get_conn
from lib.desktop_application import DesktopFacade


class StockLossTests(FacadeTestCase):
    def setUp(self):
        super().setUp()
        conn = get_conn(self.db)
        conn.execute("INSERT INTO Category(category_id,name) VALUES(1,'報損種類')")
        conn.execute("INSERT INTO Product(product_id,name,category_id) VALUES(1,'測試保護貼',1)")
        conn.execute("INSERT INTO Variant(variant_id,product_id,price) VALUES(1,1,100)")
        add_stock(conn, 1, 5)
        conn.commit()
        conn.close()

    def stock(self):
        return self.invoke("stock.detail", {"variant_id": 1})["stock"]

    def create(self, qty=2, reason=" 摔壞 "):
        return self.invoke("stockloss.create", {"variant_id": 1, "qty": qty, "reason": reason})

    def test_loss_reduces_stock_and_is_listed(self):
        self.assertEqual(self.create()["stock"], 3)
        self.assertEqual(self.stock(), 3)
        row = self.invoke("stockloss.history", {})[0]
        self.assertEqual((row["name"], row["qty"], row["reason"]), ("測試保護貼", 2, "摔壞"))
        move = self.invoke("stock.detail", {"variant_id": 1})["movements"][0]
        self.assertEqual((move["kind"], move["qty"]), ("loss", -2))

    def test_loss_does_not_change_day_close_amounts(self):
        date = self.invoke("dayclose.current", {})["business_date"]
        before = self.invoke("dayclose.preview", {"business_date": date})
        self.create()
        after = self.invoke("dayclose.preview", {"business_date": date})
        for key in ("float_amount", "cash_sales", "cash_refund", "by_payment"):
            self.assertEqual(before[key], after[key])

    def test_rejects_qty_over_stock_and_blank_reason(self):
        self.assert_application_error("conflict", "stockloss.create", {"variant_id": 1, "qty": 6, "reason": "摔壞"})
        self.assert_application_error("validation_error", "stockloss.create", {"variant_id": 1, "qty": 1, "reason": "  "})
        self.assertEqual(self.stock(), 5)
        self.assertEqual(self.invoke("stockloss.history", {}), [])

    def test_rejects_item_on_active_stocktake_until_finished(self):
        sid = self.invoke("stocktake.create", {"employee": 1})["session_id"]
        self.invoke("stocktake.scan", {"session_id": sid, "variant_id": 1, "qty": 5})
        self.assert_application_error("conflict", "stockloss.create", {"variant_id": 1, "qty": 1, "reason": "摔壞"})
        self.invoke("stocktake.close", {"session_id": sid})
        self.assert_application_error("conflict", "stockloss.create", {"variant_id": 1, "qty": 1, "reason": "摔壞"})
        self.invoke("stocktake.approve", {"session_id": sid})
        self.assertEqual(self.create(1)["stock"], 4)

    def test_employee_cannot_create_or_view(self):
        self.create(1)
        employee = DesktopFacade(self.db)
        for action, payload in (("stockloss.create", {"variant_id": 1, "qty": 1, "reason": "摔壞"}),
                                ("stockloss.history", {})):
            with self.assertRaises(Exception) as raised:
                employee.invoke(action, payload)
            self.assertEqual(raised.exception.code, "forbidden")
        self.assertEqual(self.stock(), 4)

    def test_record_cannot_be_changed_or_deleted(self):
        loss_id = self.create(1)["loss_id"]
        conn = get_conn(self.db)
        self.addCleanup(conn.close)
        for sql in ("UPDATE StockLoss SET qty=2 WHERE loss_id=?", "DELETE FROM StockLoss WHERE loss_id=?"):
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(sql, (loss_id,))
