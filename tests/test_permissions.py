"""逐項固定管理者權限表，並從真正 bridge 邊界驗證。"""
from unittest.mock import Mock

from base import ConnTestCase
from lib.auth import ADMIN_ACTIONS
from lib.desktop_application import DesktopFacade
from lib.desktop_bridge import DesktopBridge


EXPECTED_ADMIN_ACTIONS = frozenset({
    "categories.create", "categories.update", "categories.delete", "categories.sort",
    "categories.set_common_fields", "categories.set_field", "categories.delete_field",
    "brands.create", "brands.update", "brands.delete", "brands.sort", "brands.set_categories",
    "phone_brands.create", "phone_brands.update", "phone_brands.delete", "phone_brands.sort",
    "models.create", "models.update", "models.delete", "models.sort",
    "fields.create", "fields.update",
    "options.create", "options.update", "options.delete", "options.set_models", "options.cleanup",
    "products.create", "products.update", "products.delete",
    "variants.create", "variants.update", "variants.set_models", "variants.update_details",
    "variants.update_editor", "variants.delete", "variants.batch_create", "variants.activate", "variants.issues",
    "sales.export", "sales.export_save", "stocktake.approve", "stocktake.pending_count",
    "desktop.child_window.open",
})


class PermissionTests(ConnTestCase):
    def setUp(self):
        super().setUp()
        self.facade = DesktopFacade(self.db)
        self.child = Mock()
        self.bridge = DesktopBridge(facade=self.facade, child_window=self.child)
        self.conn.execute("INSERT INTO Category(category_id,name) VALUES(1,'permission category')")
        self.conn.execute("INSERT INTO Product(product_id,name,category_id) VALUES(1,'permission product',1)")
        self.conn.execute("INSERT INTO Variant(variant_id,product_id,price) VALUES(1,1,10)")
        self.conn.execute("INSERT INTO StockMovement(variant_id,qty,kind) VALUES(1,10,'purchase')")
        self.conn.execute("INSERT INTO StocktakeSession(session_id,employee,status) VALUES(1,1,'pending')")
        self.conn.execute("INSERT INTO StocktakeItem(session_id,variant_id,system_qty,counted_qty) VALUES(1,1,10,8)")
        self.conn.commit()

    def test_admin_actions_match_every_explicit_decision(self):
        self.assertEqual(EXPECTED_ADMIN_ACTIONS, ADMIN_ACTIONS)

    def test_employee_cannot_call_any_admin_action_and_database_is_unchanged(self):
        before = list(self.conn.iterdump())
        payloads = {"products.delete": {"id": 1}, "categories.create": {"name": "blocked category"},
                    "stocktake.approve": {"session_id": 1},
                    "desktop.child_window.open": {"page": "variant_editor", "context": {}},
                    "variants.update_editor": {"id": 1, "fields": {"price": 99}}}
        for action in sorted(EXPECTED_ADMIN_ACTIONS):
            with self.subTest(action=action):
                response = self.bridge.invoke(action, payloads.get(action, {}))
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["code"], "forbidden")
                self.assertEqual(before, list(self.conn.iterdump()))
        self.child.open.assert_not_called()
        self.child.update_editor.assert_not_called()

    def test_employee_can_checkout_receive_and_submit_stocktake(self):
        self.assertTrue(self.bridge.invoke("stock.receive", {"employee": 1, "items": [{"variant_id": 1, "qty": 2}]})["ok"])
        self.assertTrue(self.bridge.invoke("sales.checkout", {"employee": 1, "payment": "現金", "paid": 10,
            "items": [{"variant_id": 1, "qty": 1, "unit_price": 10}]})["ok"])
        self.assertTrue(self.bridge.invoke("stocktake.withdraw", {"session_id": 1})["ok"])
        self.assertTrue(self.bridge.invoke("stocktake.close", {"session_id": 1})["ok"])

    def test_child_bridge_shares_main_login_and_logout(self):
        # 用無協調器的 bridge 直接走真正的 update_editor 交易。
        child = DesktopBridge(facade=self.facade)
        self.assertTrue(self.bridge.invoke("auth.login", {"password": "admin"})["ok"])
        payload = {"id": 1, "fields": {"price": 25}}
        self.assertTrue(child.invoke("variants.update_editor", payload)["ok"])
        self.assertEqual(self.conn.execute("SELECT price FROM Variant WHERE variant_id=1").fetchone()[0], 25)
        self.bridge.invoke("auth.logout")
        payload["fields"]["price"] = 999
        self.assertEqual(child.invoke("variants.update_editor", payload)["error"]["code"], "forbidden")
        self.assertEqual(self.conn.execute("SELECT price FROM Variant WHERE variant_id=1").fetchone()[0], 25)

    def test_employee_export_never_opens_save_dialog(self):
        window = Mock()
        bridge = DesktopBridge(facade=self.facade, window=window, save_dialog_type="SAVE")
        self.assertEqual(bridge.invoke("sales.export_save")["error"]["code"], "forbidden")
        window.create_file_dialog.assert_not_called()
