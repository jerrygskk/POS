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
        self.assertEqual(self.invoke("stock.receive", {"employee": 1, "items": [{"variant_id": self.vid, "qty": 5}]})["items"][0]["stock"], 5)
        self.assertEqual(self.invoke("stock.receive", {"employee": 1, "items": [{"variant_id": self.vid, "qty": 3}]})["items"][0]["stock"], 8)

    def test_detail_lists_movements(self):
        self.invoke("stock.receive", {"employee": 1, "items": [{"variant_id": self.vid, "qty": 5}]})
        result = self.invoke("stock.detail", {"variant_id": self.vid})
        self.assertEqual(result["stock"], 5)
        self.assertEqual(result["movements"][0]["kind"], "purchase")

    def test_receipts_group_one_confirmation_as_one_receipt(self):
        self.invoke("stock.receive", {"employee": 2, "items": [
            {"variant_id": self.vid, "qty": 2}, {"variant_id": self.vid, "qty": 3}]})
        self.invoke("stock.receive", {"employee": 1, "items": [{"variant_id": self.vid, "qty": 1}]})
        receipts = self.invoke("stock.receipts", {})
        self.assertEqual([(r["employee"], len(r["items"])) for r in receipts], [(1, 1), (2, 2)])
        self.assertEqual([(it["qty"], it["stock"]) for it in receipts[1]["items"]], [(2, 6), (3, 6)])
        self.assertNotEqual(receipts[0]["receipt_id"], receipts[1]["receipt_id"])
        self.assertEqual(self.invoke("stock.receipts", {"date_from": "2000-01-01", "date_to": "2000-01-01"}), [])
        self.assert_application_error("validation_error", "stock.receipts", {"date_to": "bad"})

    def test_reject_zero_qty(self):
        self.assert_application_error("validation_error", "stock.receive", {"employee": 1, "items": [{"variant_id": self.vid, "qty": 0}]})

    def test_receive_unknown_variant_raises_not_found(self):
        self.assert_application_error("not_found", "stock.receive", {"employee": 1, "items": [{"variant_id": 999999, "qty": 1}]})
