import unittest
from base import ApiTestCase
from lib.product_rules import FIELD_TYPES, check_field_type, next_store_barcode
from lib.application_errors import ValidationError
from base import ConnTestCase


class TestProductRules(ConnTestCase):
    def test_next_store_barcode_uses_default_and_increments(self):
        self.assertEqual(next_store_barcode(self.conn), "TL100000001")
        self.assertEqual(next_store_barcode(self.conn), "TL100000002")
        value = self.conn.execute(
            "SELECT value FROM Setting WHERE key='next_store_barcode'"
        ).fetchone()["value"]
        self.assertEqual(value, "100000003")

    def test_next_store_barcode_rolls_back_with_same_connection(self):
        self.assertEqual(next_store_barcode(self.conn), "TL100000001")
        self.conn.rollback()
        self.assertEqual(next_store_barcode(self.conn), "TL100000001")

    def test_runtime_field_types_are_shared(self):
        self.assertEqual(FIELD_TYPES, {"select", "text", "multi", "tags"})
        for field_type in FIELD_TYPES:
            check_field_type(field_type)
        with self.assertRaises(ValidationError) as ctx:
            check_field_type("number")
        self.assertEqual(ctx.exception.code, "validation_error")

class TestProducts(ApiTestCase):
    def setUp(self):
        super().setUp()
        # 正規化後 select 值須為既有選項:先建專屬欄「規格」與選項
        self.make_category_with_field("規格", options=("亮面", "霧面", "超亮", "防窺"))

    def _create(self):
        return self.c.post("/api/products", json={
            "name": "HODA 鋼化玻璃", "category_id": self.cid,
            "variants": [
                {"attributes": {"規格": "亮面"}, "price": 590,
                 "barcodes": [{"barcode": "FX100000001", "source": "factory"}]},
                {"attributes": {"規格": "霧面"},
                 "price": 690, "barcodes": []},
            ]}).json()

    def test_create_and_scan(self):
        r = self._create()
        self.assertEqual(len(r["variant_ids"]), 2)
        hit = self.c.get("/api/barcode/FX100000001").json()
        self.assertEqual(hit["price"], 590)          # 售價存於子產品
        self.assertEqual(hit["attributes"]["規格"], "亮面")
        self.assertEqual(hit["stock"], 0)

    def test_variant_price_overrides(self):
        r = self._create()
        v2 = r["variant_ids"][1]
        b = self.c.post(f"/api/variants/{v2}/barcodes",
                        json={"source": "store"}).json()["barcode"]
        self.assertTrue(b.startswith("TL"))
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
        self.assertTrue(b1.startswith("TL") and b2.startswith("TL"))
        self.assertEqual(int(b2[2:]) - int(b1[2:]), 1)

    def test_manual_tl_barcode_rejected(self):
        # TL 為系統自取碼保留字頭,手動輸入一律 422
        r = self._create()
        v = r["variant_ids"][0]
        resp = self.c.post(f"/api/variants/{v}/barcodes",
                           json={"barcode": "TL999999999", "source": "factory"})
        self.assertEqual(resp.status_code, 422)
        resp = self.c.post("/api/products", json={
            "name": "X", "category_id": self.cid,
            "variants": [{"attributes": {}, "barcodes":
                [{"barcode": "TL123", "source": "store"}]}]})
        self.assertEqual(resp.status_code, 422)

    def test_store_barcode_not_reused_after_delete(self):
        # 流水號單調遞增:刪除後號碼不回收
        r = self._create()
        v = r["variant_ids"][0]
        b1 = self.c.post(f"/api/variants/{v}/barcodes", json={"source":"store"}).json()["barcode"]
        self.assertEqual(self.c.delete(f"/api/barcodes/{b1}").status_code, 200)
        b2 = self.c.post(f"/api/variants/{v}/barcodes", json={"source":"store"}).json()["barcode"]
        self.assertEqual(int(b2[2:]), int(b1[2:]) + 1)

    def test_add_barcode_unknown_variant_returns_404(self):
        r = self.c.post("/api/variants/999999/barcodes",
                        json={"source": "store"})
        self.assertEqual(r.status_code, 404)

    def test_variant_model_unknown_id_returns_422(self):
        v = self._create()["variant_ids"][0]
        r = self.c.put(f"/api/variants/{v}/models", json={"model_ids": [999999]})
        self.assertEqual(r.status_code, 422)
