"""tests 共用基底:client 型 / conn 型測試 setUp,及匯入測試共用 helper。

- ApiTestCase:建 tmpdir + db + init_db + TestClient,供 self.c。
- ConnTestCase:建 tmpdir + db + init_db + get_conn,供 self.conn(tearDown 關閉)。
- make_client / create_product / make_category_with_field:建檔便捷方法。
- raw_row:匯入測試組一列 Excel 原值 dict。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from lib.db import init_db, get_conn
from api import create_app
from tools.import_excel import (
    COL_CODE, COL_CATEGORY, COL_BRAND, COL_SPEC, COL_DESC, COL_CAT1,
    COL_CAT2, COL_PHONE_BRAND, COL_PHONE_MODEL, COL_NOTE,
)


def make_client(db):
    """建 TestClient(create_app(db))。"""
    return TestClient(create_app(db))


def raw_row(**kw):
    """組一列 Excel 原值 dict(未提供的欄為 None)。"""
    base = {c: None for c in (
        COL_CODE, COL_CATEGORY, COL_BRAND, COL_SPEC, COL_DESC, COL_CAT1,
        COL_CAT2, COL_PHONE_BRAND, COL_PHONE_MODEL, COL_NOTE)}
    base.update(kw)
    return base


class ApiTestCase(unittest.TestCase):
    """建 tmpdir + db + init_db + TestClient(create_app),供 self.c。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pos.db")
        init_db(self.db)
        self.c = make_client(self.db)

    def make_category_with_field(self, name, field_type="select", options=(),
                                 category="鋼化玻璃"):
        """建一種類 + 一屬性欄 + 選項;設定 self.cid、self.fid 並回傳 (cid, fid)。"""
        self.cid = self.c.post(
            "/api/categories", json={"name": category}).json()["category_id"]
        self.fid = self.c.post("/api/fields", json={
            "name": name, "category_id": self.cid,
            "field_type": field_type}).json()["field_id"]
        for v in options:
            self.c.post("/api/options", json={"field_id": self.fid, "value": v})
        return self.cid, self.fid

    def create_product(self, attrs, name="膜", price=100, barcode="B1",
                       source="store"):
        """建單變體款(self.cid 種類),回傳 API json。"""
        return self.c.post("/api/products", json={
            "name": name, "category_id": self.cid, "default_price": price,
            "variants": [{"attributes": attrs,
                          "barcodes": [{"barcode": barcode, "source": source}]}]
        }).json()


class ConnTestCase(unittest.TestCase):
    """建 tmpdir + db + init_db + get_conn,供 self.conn;tearDown 關閉。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pos.db")
        init_db(self.db)
        self.conn = get_conn(self.db)

    def tearDown(self):
        self.conn.close()
