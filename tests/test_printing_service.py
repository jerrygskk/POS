"""Label service data selection and payload validation using a temporary DB."""
from unittest.mock import patch

from lib.printing_service import PrintingFacade
from tests.base import FacadeTestCase


class PrintingServiceTests(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.cid, self.fid = self.make_category_with_field("顏色", options=("透明",))

    def _create_variant(self, *, barcode, source="store", price=590):
        result = self.create_product({"顏色": "透明"}, name="玻璃保護貼", price=price,
                                     barcode=barcode, source=source)
        return result["variant_ids"][0]

    @patch("lib.printing_service.LabelPrinter.print")
    def test_store_barcode_is_rendered_and_sent_to_printer(self, print_label):
        vid = self._create_variant(barcode="STORE-1")
        result = self.invoke("printing.barcode", {"variant_id": vid, "copies": 2})
        self.assertEqual(result, {"ok": True})
        image, copies = print_label.call_args.args
        self.assertEqual(copies, 2)
        self.assertEqual(image.size, (320, 160))

    def test_factory_only_barcode_is_rejected(self):
        vid = self._create_variant(barcode="4710000000000", source="factory")
        error = self.assert_application_error("validation_error", "printing.barcode", {"variant_id": vid})
        self.assertEqual(error.message, "原廠條碼商品不需列印標籤。")

    def test_variant_without_barcode_is_rejected(self):
        result = self.invoke("products.create", {"name": "保護殼", "category_id": self.cid,
            "variants": [{"attributes": {"顏色": "透明"}, "price": 100, "barcodes": []}]})
        error = self.assert_application_error("validation_error", "printing.barcode",
                                              {"variant_id": result["variant_ids"][0]})
        self.assertEqual(error.message, "此子產品尚未建立店內條碼。")

    def test_copies_must_be_positive_integer(self):
        for copies in (0, -1, True, "2"):
            with self.subTest(copies=copies):
                self.assert_application_error("validation_error", "printing.barcode",
                                              {"variant_id": 1, "copies": copies})
