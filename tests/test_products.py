import unittest, tempfile, os
from fastapi.testclient import TestClient
from lib.db import init_db, get_conn
from api import create_app

def make_client(self):
    self.tmp = tempfile.mkdtemp()
    self.db = os.path.join(self.tmp, "pos.db")
    init_db(self.db)
    return TestClient(create_app(self.db))

class TestProducts(unittest.TestCase):
    def setUp(self):
        self.c = make_client(self)
        self.cid = self.c.post("/api/categories",
                               json={"name": "鋼化玻璃"}).json()["category_id"]
        # 正規化後 select 值須為既有選項:先建專屬欄「規格」與選項
        self.fid = self.c.post("/api/fields",
            json={"name": "規格", "category_id": self.cid}).json()["field_id"]
        for v in ("亮面", "霧面", "超亮", "防窺"):
            self.c.post("/api/options", json={"field_id": self.fid, "value": v})

    def _create(self):
        return self.c.post("/api/products", json={
            "name": "HODA 鋼化玻璃", "category_id": self.cid, "default_price": 590,
            "variants": [
                {"attributes": {"規格": "亮面"},
                 "barcodes": [{"barcode": "TL100000001", "source": "store"}]},
                {"attributes": {"規格": "霧面"},
                 "price": 690, "barcodes": []},
            ]}).json()

    def test_create_and_scan(self):
        r = self._create()
        self.assertEqual(len(r["variant_ids"]), 2)
        hit = self.c.get("/api/barcode/TL100000001").json()
        self.assertEqual(hit["price"], 590)          # 用款預設價
        self.assertEqual(hit["attributes"]["規格"], "亮面")
        self.assertEqual(hit["stock"], 0)

    def test_variant_price_overrides(self):
        r = self._create()
        v2 = r["variant_ids"][1]
        b = self.c.post(f"/api/variants/{v2}/barcodes",
                        json={"source": "store"}).json()["barcode"]
        self.assertTrue(b.startswith("SP"))
        self.assertEqual(self.c.get(f"/api/barcode/{b}").json()["price"], 690)

    def test_unknown_barcode_404(self):
        self.assertEqual(self.c.get("/api/barcode/NOPE").status_code, 404)

    def test_null_price_allowed(self):
        r = self.c.post("/api/products", json={
            "name": "無價品", "category_id": self.cid,
            "variants": [{"attributes": {}, "barcodes":
                [{"barcode": "X1", "source": "factory"}]}]})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(self.c.get("/api/barcode/X1").json()["price"])

    def test_store_barcode_sequence(self):
        r = self._create()
        v = r["variant_ids"][0]
        b1 = self.c.post(f"/api/variants/{v}/barcodes", json={"source":"store"}).json()["barcode"]
        b2 = self.c.post(f"/api/variants/{v}/barcodes", json={"source":"store"}).json()["barcode"]
        self.assertEqual(int(b2[2:]) - int(b1[2:]), 1)
