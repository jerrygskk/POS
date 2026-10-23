"""tests 共用基底:連線型與 Desktop Facade 型測試 setUp。"""
import atexit
import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lib.db as _db

_real_get_conn = _db.get_conn


def _fast_get_conn(db_path):
    # 測試不驗斷電耐久性，存檔不等硬碟寫完以縮短整輪時間；正式程式不受影響。
    conn = _real_get_conn(db_path)
    conn.execute("PRAGMA synchronous=OFF")
    return conn


_db.get_conn = _fast_get_conn

from lib.db import init_db, get_conn
from lib.desktop_application import DesktopFacade
from lib.application_errors import ApplicationError
from lib.desktop_bridge import DesktopBridge
from lib.auth import hash_password

_TEMPLATE_DB = None


def _template_db():
    """整輪測試只跑一次 init_db,之後各測試複製檔案;密碼改低迭代以免每次登入耗時。"""
    global _TEMPLATE_DB
    if _TEMPLATE_DB is None:
        folder = tempfile.mkdtemp()
        atexit.register(shutil.rmtree, folder, True)
        path = os.path.join(folder, "template.db")
        init_db(path)
        conn = get_conn(path)
        conn.execute("UPDATE Setting SET value=? WHERE key='admin_password_hash'",
                     (hash_password("admin", iterations=1),))
        conn.commit()
        conn.close()
        _TEMPLATE_DB = path
    return _TEMPLATE_DB


def fresh_db(folder):
    db = os.path.join(folder, "pos.db")
    shutil.copyfile(_template_db(), db)
    return db


class ConnTestCase(unittest.TestCase):
    """建 tmpdir + db(複製初始化範本)+ get_conn,供 self.conn;tearDown 關閉。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = fresh_db(self.tmp)
        self.conn = get_conn(self.db)

    def tearDown(self):
        self.conn.close()


class FacadeTestCase(unittest.TestCase):
    """以 DesktopFacade action 建立測試資料，不模擬 HTTP transport。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = fresh_db(self.tmp)
        self.facade = DesktopFacade(self.db)
        # 既有領域測試需要建檔；權限測試另建員工身分的 facade。
        self.facade.session.login("admin")
        self.bridge = DesktopBridge(facade=self.facade)

    def invoke(self, action, payload=None):
        return self.facade.invoke(action, {} if payload is None else payload)

    def assert_application_error(self, code, action, payload=None):
        with self.assertRaises(ApplicationError) as raised:
            self.invoke(action, payload)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def invoke_envelope(self, action, payload=None):
        return self.bridge.invoke(action, payload)

    def assert_envelope_error(self, code, action, payload=None):
        response = self.invoke_envelope(action, payload)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], code)
        return response["error"]

    def create_category(self, name):
        return self.invoke("categories.create", {"name": name})["category_id"]

    def create_field(self, name, category_id=None, field_type="select"):
        payload = {"name": name, "field_type": field_type}
        if category_id is not None:
            payload["category_id"] = category_id
        return self.invoke("fields.create", payload)["field_id"]

    def create_options(self, field_id, values):
        for value in values:
            self.invoke("options.create", {"field_id": field_id, "value": value})

    def create_phone_brand(self, name):
        return self.invoke("phone_brands.create", {"name": name})["phone_brand_id"]

    def create_model(self, phone_brand_id, name):
        return self.invoke("models.create", {
            "phone_brand_id": phone_brand_id, "name": name,
        })["model_id"]

    def make_category_with_field(self, name, field_type="select", options=(),
                                 category="鋼化玻璃"):
        self.cid = self.create_category(category)
        self.fid = self.create_field(name, self.cid, field_type)
        self.create_options(self.fid, options)
        return self.cid, self.fid

    def create_product(self, attrs, name="膜", price=100, barcode="B1",
                       source="store"):
        return self.invoke("products.create", {
            "name": name, "category_id": self.cid,
            "variants": [{"attributes": attrs, "price": price,
                          "barcodes": [{"barcode": barcode, "source": source}]}],
        })
