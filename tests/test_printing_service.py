"""Label ownership, preview, printing, and payloads using temporary databases."""
import base64
from io import BytesIO
from unittest.mock import patch

from PIL import Image

from lib import printing_service
from tests.base import FacadeTestCase


class PrintingServiceTests(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.cid, self.fid = self.make_category_with_field("顏色", options=("透明",))

    def _create_variant(self, *, barcode, source="store", price=590):
        result = self.create_product({"顏色": "透明"}, name="玻璃保護貼", price=price,
                                     barcode=barcode, source=source)
        self.pid = result["product_id"]
        return result["variant_ids"][0]

    @patch("lib.printing_service.LabelPrinter.print")
    @patch("lib.printing_service.render_label", wraps=printing_service.render_label)
    def test_store_barcode_is_rendered_and_sent_to_printer(self, render, print_label):
        vid = self._create_variant(barcode="STORE-1")
        result = self.invoke("printing.barcode", {"variant_id": vid, "barcode": "STORE-1", "copies": 2})
        self.assertEqual(result, {"ok": True})
        render.assert_called_once_with("玻璃保護貼", "透明", "$590", "STORE-1", models=[])
        image, copies = print_label.call_args.args
        self.assertEqual(copies, 2)
        self.assertEqual(image.size, (320, 160))

    @patch("lib.printing_service.LabelPrinter.print")
    @patch("lib.printing_service.render_label")
    def test_factory_only_barcode_is_printed(self, render, print_label):
        vid = self._create_variant(barcode="4710000000000", source="factory")
        self.invoke("printing.barcode", {"variant_id": vid, "barcode": "4710000000000"})
        render.assert_called_once_with("玻璃保護貼", "透明", "$590", "4710000000000", models=[])
        print_label.assert_called_once_with(render.return_value, 1)

    @patch("lib.printing_service.LabelPrinter.print")
    @patch("lib.printing_service.render_label")
    def test_specified_factory_barcode_is_printed_even_with_store_barcode(self, render, print_label):
        vid = self._create_variant(barcode="STORE-FACTORY")
        self.invoke("barcodes.add", {"variant_id": vid, "barcode": "4710000000000", "source": "factory"})
        self.invoke("printing.barcode", {"variant_id": vid, "barcode": "4710000000000"})
        render.assert_called_once_with("玻璃保護貼", "透明", "$590", "4710000000000", models=[])
        print_label.assert_called_once_with(render.return_value, 1)

    @patch("lib.printing_service.LabelPrinter.print")
    @patch("lib.printing_service.render_label")
    def test_models_use_alias_and_configured_order(self, render, print_label):
        vid = self._create_variant(barcode="STORE-MODELS")
        bid = self.create_phone_brand("測試品牌")
        first = self.create_model(bid, "測試型號甲")
        second = self.create_model(bid, "測試型號乙")
        self.invoke("models.update", {"id": first, "fields": {"alias": "甲", "sort": 2}})
        self.invoke("models.update", {"id": second, "fields": {"sort": 1}})
        self.invoke("variants.set_models", {"id": vid, "model_ids": [first, second]})
        self.invoke("printing.barcode", {"variant_id": vid, "barcode": "STORE-MODELS", "copies": 1})
        render.assert_called_once_with("玻璃保護貼", "透明", "$590", "STORE-MODELS",
                                       models=["測試型號乙", "甲"])
        print_label.assert_called_once_with(render.return_value, 1)

    @patch("lib.printing_service.LabelPrinter.print")
    def test_barcode_owned_by_another_variant_is_rejected(self, print_label):
        self._create_variant(barcode="STORE-OWNER")
        result = self.invoke("products.create", {"name": "保護殼", "category_id": self.cid,
            "variants": [{"attributes": {"顏色": "透明"}, "price": 100, "barcodes": []}]})
        error = self.assert_application_error("validation_error", "printing.barcode",
                                              {"variant_id": result["variant_ids"][0], "barcode": "STORE-OWNER"})
        self.assertEqual(error.message, "此條碼已不屬於此子產品，請移除後重新加入。")
        print_label.assert_not_called()

    @patch("lib.printing_service.LabelPrinter.print")
    def test_nonexistent_barcode_is_rejected(self, print_label):
        vid = self._create_variant(barcode="STORE-EXISTS")
        error = self.assert_application_error("validation_error", "printing.barcode",
                                              {"variant_id": vid, "barcode": "MISSING"})
        self.assertEqual(error.message, "此條碼已不屬於此子產品，請移除後重新加入。")
        print_label.assert_not_called()

    @patch("lib.printing_service.LabelPrinter.print")
    def test_nonexistent_variant_is_not_found(self, print_label):
        error = self.assert_application_error("not_found", "printing.barcode",
                                              {"variant_id": 999999, "barcode": "MISSING"})
        self.assertEqual(error.message, "找不到子產品")
        print_label.assert_not_called()

    def _preview_image(self, payload):
        result = self.invoke("printing.preview", payload)
        self.assertTrue(result["image"].startswith("data:image/png;base64,"))
        return Image.open(BytesIO(base64.b64decode(result["image"].split(",", 1)[1])))

    @patch("lib.printing_service.LabelPrinter.print")
    def test_preview_is_png_at_label_dimensions_without_printing(self, print_label):
        vid = self._create_variant(barcode="PREVIEW-1")
        with self._preview_image({"variant_id": vid, "barcode": "PREVIEW-1"}) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (320, 160))
        print_label.assert_not_called()

    @patch("lib.printing_service.LabelPrinter.print")
    def test_preview_and_print_have_identical_pixels(self, print_label):
        vid = self._create_variant(barcode="SAME-IMAGE")
        payload = {"variant_id": vid, "barcode": "SAME-IMAGE"}
        self.invoke("printing.barcode", payload)
        printed, copies = print_label.call_args.args
        self.assertEqual(copies, 1)
        # A separate payload excludes the print action's defaulted copies key.
        with self._preview_image({"variant_id": vid, "barcode": "SAME-IMAGE"}) as preview:
            self.assertEqual(preview.convert("RGB").tobytes(), printed.convert("RGB").tobytes())

    @patch("lib.printing_service.render_label")
    def test_preview_rejects_unowned_barcode_before_rendering(self, render):
        vid = self._create_variant(barcode="PREVIEW-OWNED")
        error = self.assert_application_error("validation_error", "printing.preview",
                                              {"variant_id": vid, "barcode": "PREVIEW-OTHER"})
        self.assertEqual(error.message, "此條碼已不屬於此子產品，請移除後重新加入。")
        render.assert_not_called()

    @patch("lib.printing_service.LabelPrinter.print")
    @patch("lib.printing_service.render_label")
    def test_check_preserves_order_and_includes_content_only_for_owned_barcodes(self, render, print_label):
        vid = self._create_variant(barcode="CHECK-OWNED")
        bid = self.create_phone_brand("虛構品牌")
        mid = self.create_model(bid, "虛構型號")
        self.invoke("variants.set_models", {"id": vid, "model_ids": [mid]})
        other_vid = self.invoke("variants.create", {"product_id": self.pid, "fields": {}})["variant_id"]
        items = [{"variant_id": vid, "barcode": "CHECK-OWNED"},
                 {"variant_id": other_vid, "barcode": "CHECK-OWNED"},
                 {"variant_id": 999999, "barcode": "CHECK-OWNED"}]
        self.assertEqual(self.invoke("printing.check", {"items": items}), {"items": [
            {**items[0], "valid": True, "label": {"name": "玻璃保護貼", "specification": "透明",
                "price": "$590", "models": ["虛構型號"]}},
            {**items[1], "valid": False, "label": None},
            {**items[2], "valid": False, "label": None},
        ]})
        render.assert_not_called()
        print_label.assert_not_called()

    def test_check_reflects_latest_product_name(self):
        vid = self._create_variant(barcode="CHECK-UPDATE")
        payload = {"items": [{"variant_id": vid, "barcode": "CHECK-UPDATE"}]}
        before = self.invoke("printing.check", payload)
        self.assertEqual(before["items"][0]["label"]["name"], "玻璃保護貼")
        self.invoke("products.update", {"id": self.pid, "fields": {"name": "新版虛構商品"}})
        self.assertEqual(self.invoke("printing.check", payload)["items"][0]["label"]["name"], "新版虛構商品")

    def test_check_leaves_null_variant_price_blank(self):
        vid = self._create_variant(barcode="PRICE-NULL", price=None)
        checked = self.invoke("printing.check", {"items": [{"variant_id": vid, "barcode": "PRICE-NULL"}]})
        self.assertEqual(checked["items"][0]["label"]["price"], "")

    @patch("lib.printing_service.LabelPrinter.print")
    def test_busy_lock_rejects_printing_without_sending(self, print_label):
        vid = self._create_variant(barcode="BUSY")
        printing_service._PRINT_LOCK.acquire()
        try:
            error = self.assert_application_error("validation_error", "printing.barcode",
                                                  {"variant_id": vid, "barcode": "BUSY"})
            self.assertEqual(error.message, "標籤機正在列印中，請稍候。")
            print_label.assert_not_called()
        finally:
            printing_service._PRINT_LOCK.release()

    @patch("lib.printing_service.LabelPrinter.print")
    def test_printing_releases_lock_after_success(self, print_label):
        vid = self._create_variant(barcode="LOCK-SUCCESS")
        self.invoke("printing.barcode", {"variant_id": vid, "barcode": "LOCK-SUCCESS"})
        print_label.assert_called_once()
        self.assertFalse(printing_service._PRINT_LOCK.locked())

    @patch("lib.printing_service.LabelPrinter.print", side_effect=RuntimeError("printer failed"))
    def test_printing_releases_lock_after_printer_exception(self, print_label):
        vid = self._create_variant(barcode="LOCK-ERROR")
        with self.assertRaisesRegex(RuntimeError, "printer failed"):
            self.invoke("printing.barcode", {"variant_id": vid, "barcode": "LOCK-ERROR"})
        print_label.assert_called_once()
        self.assertFalse(printing_service._PRINT_LOCK.locked())

    def test_printing_rechecks_ownership_after_check(self):
        vid = self._create_variant(barcode="DELETED-AFTER-CHECK")
        payload = {"variant_id": vid, "barcode": "DELETED-AFTER-CHECK"}
        self.assertTrue(self.invoke("printing.check", {"items": [payload]})["items"][0]["valid"])
        self.invoke("barcodes.delete", {"code": "DELETED-AFTER-CHECK"})
        with patch("lib.printing_service.LabelPrinter.print") as print_label:
            self.assert_application_error("validation_error", "printing.barcode", payload)
            print_label.assert_not_called()
        self.assertFalse(printing_service._PRINT_LOCK.locked())

    @patch("lib.printing_service.LabelPrinter.print")
    def test_barcode_whitespace_is_not_normalized(self, print_label):
        vid = self._create_variant(barcode=" EXACT ")
        self.invoke("printing.barcode", {"variant_id": vid, "barcode": " EXACT "})
        print_label.assert_called_once()
        self.assert_application_error("validation_error", "printing.preview", {"variant_id": vid, "barcode": "EXACT"})
        checked = self.invoke("printing.check", {"items": [
            {"variant_id": vid, "barcode": " EXACT "}, {"variant_id": vid, "barcode": "EXACT"}]})
        self.assertEqual([item["valid"] for item in checked["items"]], [True, False])

    def test_copies_must_be_positive_integer(self):
        for copies in (0, -1, True, "2", None):
            with self.subTest(copies=copies):
                error = self.assert_application_error("validation_error", "printing.barcode",
                                                      {"variant_id": 1, "barcode": "CODE", "copies": copies})
                self.assertEqual(error.message, "張數必須是正整數")

    def test_barcode_payload_validation_for_all_actions(self):
        for action in ("printing.barcode", "printing.preview", "printing.check"):
            for barcode in (None, "  ", "", 1, True):
                for missing in (False, True) if barcode is None else (False,):
                    item = {"variant_id": 1, "barcode": barcode}
                    if missing:
                        del item["barcode"]
                    payload = {"items": [item]} if action == "printing.check" else item
                    with self.subTest(action=action, barcode=barcode, missing=missing):
                        error = self.assert_application_error("validation_error", action, payload)
                        self.assertEqual(error.message, "條碼必須是非空白字串")

    def test_variant_payload_validation_for_all_actions(self):
        for action in ("printing.barcode", "printing.preview", "printing.check"):
            for variant_id in (None, True, "1", 1.0):
                item = {"variant_id": variant_id, "barcode": "CODE"}
                payload = {"items": [item]} if action == "printing.check" else item
                with self.subTest(action=action, variant_id=variant_id):
                    error = self.assert_application_error("validation_error", action, payload)
                    self.assertEqual(error.message, "variant_id 必須是整數")

    def test_check_items_payload_validation(self):
        for items in ([], "x", None, (), {}):
            with self.subTest(items=items):
                error = self.assert_application_error("validation_error", "printing.check", {"items": items})
                self.assertEqual(error.message, "items 必須是非空的清單")
        for item in (1, "x", None, []):
            with self.subTest(item=item):
                error = self.assert_application_error("validation_error", "printing.check", {"items": [item]})
                self.assertEqual(error.message, "items 的每一項必須是物件")
        self.assert_application_error("validation_error", "printing.check",
                                      {"items": [{"variant_id": 1, "barcode": "CODE", "unexpected": 1}]})
