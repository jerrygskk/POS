from base import FacadeTestCase


class TestStock(FacadeTestCase):
    def setUp(self):
        super().setUp()
        category_id = self.create_category("stock category")
        product = self.invoke("products.create", {
            "name": "stock product",
            "category_id": category_id,
            "variants": [{
                "attributes": {},
                "barcodes": [{"barcode": "B1", "source": "store"}],
            }],
        })
        self.vid = product["variant_ids"][0]

    def test_receive_accumulates(self):
        self.assertEqual(self.invoke("stock.receive", {
            "variant_id": self.vid, "qty": 5,
        })["stock"], 5)
        self.assertEqual(self.invoke("stock.receive", {
            "variant_id": self.vid, "qty": 3,
        })["stock"], 8)

    def test_detail_lists_movements(self):
        self.invoke("stock.receive", {"variant_id": self.vid, "qty": 5})
        result = self.invoke("stock.detail", {"variant_id": self.vid})
        self.assertEqual(result["stock"], 5)
        self.assertEqual(result["movements"][0]["kind"], "purchase")

    def test_reject_zero_qty(self):
        self.assert_application_error("validation_error", "stock.receive", {
            "variant_id": self.vid, "qty": 0,
        })

    def test_receive_unknown_variant_raises_not_found(self):
        self.assert_application_error("not_found", "stock.receive", {
            "variant_id": 999999, "qty": 1,
        })
