"""tests 共用基底:連線型與 Desktop Facade 型測試 setUp。"""
import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import init_db, get_conn
from lib.desktop_application import DesktopFacade
from lib.application_errors import ApplicationError
from lib.desktop_bridge import DesktopBridge
class ConnTestCase(unittest.TestCase):
    """建 tmpdir + db + init_db + get_conn,供 self.conn;tearDown 關閉。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "pos.db")
        init_db(self.db)
        self.conn = get_conn(self.db)

    def tearDown(self):
        self.conn.close()


class FacadeTestCase(unittest.TestCase):
    """以 DesktopFacade action 建立測試資料，不模擬 HTTP transport。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "pos.db")
        init_db(self.db)
        self.facade = DesktopFacade(self.db)
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
