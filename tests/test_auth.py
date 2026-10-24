"""管理者密碼、閒置與跨視窗共用身分。"""
from unittest.mock import Mock

from base import ConnTestCase
from lib.auth import AdminSession, hash_password, verify_password, PASSIVE_ACTIONS
from lib.application_errors import ForbiddenError, ValidationError
from lib.db import init_db
from lib.desktop_application import DesktopFacade
from lib.desktop_bridge import DesktopBridge


class AuthTests(ConnTestCase):
    def setUp(self):
        super().setUp()
        self.now = 1000.0
        self.session = AdminSession(self.db, clock=lambda: self.now)
        self.facade = DesktopFacade(self.db)
        self.facade.session = self.session
        self.facade.stocktake.session = self.session
        self.bridge = DesktopBridge(facade=self.facade)

    def test_hash_round_trip_case_salt_and_format(self):
        first, second = hash_password("admin"), hash_password("admin")
        self.assertTrue(verify_password("admin", first))
        self.assertFalse(verify_password("Admin", first))
        self.assertNotEqual(first, second)
        algorithm, iterations, salt, digest = first.split("$")
        self.assertEqual((algorithm, int(iterations), len(bytes.fromhex(salt)), len(bytes.fromhex(digest))),
                         ("pbkdf2_sha256", 200_000, 16, 32))
        for bad in (None, 1, "", "sha256$1$00$00", "pbkdf2_sha256$x$00$00",
                    "pbkdf2_sha256$0$00$00", "pbkdf2_sha256$-1$00$00",
                    "pbkdf2_sha256$200000$not-hex$00", "pbkdf2_sha256$200000$00$no",
                    "pbkdf2_sha256$200000$00$00$extra"):
            with self.subTest(stored=bad):
                self.assertFalse(verify_password("admin", bad))

    def test_seed_password_is_case_sensitive_and_starts_as_employee(self):
        self.assertFalse(self.session.is_admin())
        self.assertFalse(self.session.login("Admin"))
        self.assertTrue(self.session.login("admin"))
        self.assertTrue(self.session.is_admin())

    def test_reinitialization_does_not_replace_changed_password(self):
        self.session.login("admin")
        self.session.change_password("admin", "Changed-pass")
        init_db(self.db)
        fresh = AdminSession(self.db)
        self.assertFalse(fresh.login("admin"))
        self.assertTrue(fresh.login("Changed-pass"))

    def test_idle_599_and_601_seconds(self):
        self.session.login("admin")
        self.now += 599
        self.assertTrue(self.session.is_admin())
        self.now += 2
        self.assertFalse(self.session.is_admin())
        with self.assertRaises(ForbiddenError):
            self.session.require_admin()

    def test_touch_extends_live_session_but_never_resurrects_expired_session(self):
        self.session.touch()
        self.assertFalse(self.session.is_admin())
        self.session.login("admin")
        self.now += 599
        self.session.touch()
        self.now += 599
        self.assertTrue(self.session.is_admin())
        self.now += 2
        self.session.touch()
        self.assertFalse(self.session.is_admin())

    def test_bridge_checks_expiration_before_touch_and_denies_without_write(self):
        self.session.login("admin")
        before = self.conn.execute("SELECT COUNT(*) FROM Category").fetchone()[0]
        self.now += 601
        response = self.bridge.invoke("categories.create", {"name": "must not exist"})
        self.assertEqual(response["error"]["code"], "forbidden")
        self.assertEqual(before, self.conn.execute("SELECT COUNT(*) FROM Category").fetchone()[0])
        self.assertFalse(self.session.is_admin())

    def test_passive_queries_never_extend_session(self):
        self.assertEqual(PASSIVE_ACTIONS, {"auth.status", "auth.logout", "stocktake.pending_count",
                                           "desktop.update.check", "desktop.update.start", "desktop.update.status",
                                           "desktop.update.restart", "desktop.update.busy"})
        self.session.login("admin")
        for _ in range(2):
            self.now += 300
            self.assertTrue(self.bridge.invoke("auth.status")["data"]["is_admin"])
            self.assertTrue(self.bridge.invoke("stocktake.pending_count")["ok"])
        self.now += 1
        self.assertFalse(self.bridge.invoke("auth.status")["data"]["is_admin"])
        self.assertEqual(self.bridge.invoke("stocktake.pending_count")["error"]["code"], "forbidden")

    def test_update_actions_do_not_touch_admin_session(self):
        # 員工(未登入)也能用線上更新;進度輪詢不延長管理者閒置計時
        for action in ("desktop.update.check", "desktop.update.start", "desktop.update.status",
                       "desktop.update.restart", "desktop.update.busy"):
            response = self.bridge.invoke(action)
            self.assertNotEqual((response.get("error") or {}).get("code"), "forbidden", action)
        self.session.login("admin")
        for _ in range(3):
            self.now += 300
            self.bridge.invoke("desktop.update.status")
        self.assertFalse(self.session.is_admin())

    def test_child_bridge_activity_keeps_main_bridge_logged_in(self):
        child = DesktopBridge(facade=self.facade)
        self.session.login("admin")
        for _ in range(3):
            self.now += 300
            self.assertTrue(child.invoke("auth.touch")["data"]["is_admin"])
        self.assertTrue(self.bridge.invoke("auth.status")["data"]["is_admin"])

    def test_change_password_errors_and_success_logout(self):
        self.session.login("admin")
        for old, new, message in (("wrong", "Changed", "舊密碼錯誤"),
                                  ("admin", " abc ", "新密碼至少 4 個字"),
                                  ("admin", "admin", "新密碼不可與舊密碼相同")):
            with self.subTest(message=message), self.assertRaisesRegex(ValidationError, message):
                self.session.change_password(old, new)
            self.assertTrue(self.session.is_admin())
        self.session.change_password("admin", "  Changed  ")
        self.assertFalse(self.session.is_admin())
        self.assertFalse(self.session.login("admin"))
        self.assertTrue(self.session.login("Changed"))

    def test_auth_payloads_are_validated(self):
        for action, payload in (("auth.login", {"password": 1}), ("auth.login", []),
                                ("auth.status", {"unexpected": 1}),
                                ("auth.touch", {"unexpected": 1})):
            with self.subTest(action=action, payload=payload):
                self.assertEqual(self.bridge.invoke(action, payload)["error"]["code"], "validation_error")
        self.session.login("admin")
        for payload in ({"old_password": "admin", "new_password": None},
                        {"old_password": False, "new_password": "Changed"}):
            self.assertEqual(self.bridge.invoke("auth.change_password", payload)["error"]["code"], "validation_error")

    def test_logout_closes_child_without_claiming_saved(self):
        child = Mock()
        bridge = DesktopBridge(facade=self.facade, child_window=child)
        self.session.login("admin")
        self.assertEqual(bridge.invoke("auth.logout")["data"], {"is_admin": False})
        child.close.assert_called_once_with(saved=False)
        self.assertFalse(self.session.is_admin())

    def test_password_change_requires_admin(self):
        response = self.bridge.invoke("auth.change_password", {"old_password": "admin", "new_password": "Changed"})
        self.assertEqual(response["error"]["code"], "forbidden")
