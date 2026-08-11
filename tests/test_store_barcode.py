"""店內自取碼：檢查碼、格式化、解析、取號與掃描把關。"""
import unittest

from base import ConnTestCase, FacadeTestCase
from lib.application_errors import ValidationError
from lib.product_rules import (STORE_BARCODE_MAX_SERIAL, format_store_barcode,
                               has_store_barcode_prefix, mod10_check_digit,
                               next_store_barcode, parse_store_barcode)


class CheckDigitTests(unittest.TestCase):
    def test_matches_ean13_reference_codes(self):
        """與實際 EAN-13 條碼比對：前 12 位算出的檢查碼須等於末位。"""
        for code in ("4711592494257", "4710007834695", "4711508794495"):
            with self.subTest(code=code):
                self.assertEqual(mod10_check_digit(code[:-1]), int(code[-1]))

    def test_known_serials(self):
        self.assertEqual(mod10_check_digit("000001"), 7)
        self.assertEqual(mod10_check_digit("000000"), 0)


class FormatTests(unittest.TestCase):
    def test_format_is_prefix_serial_check(self):
        self.assertEqual(format_store_barcode(1), "TL0000017")
        self.assertEqual(len(format_store_barcode(1)), 9)

    def test_serial_is_zero_padded_to_six_digits(self):
        self.assertTrue(format_store_barcode(42).startswith("TL000042"))

    def test_max_serial_allowed(self):
        code = format_store_barcode(STORE_BARCODE_MAX_SERIAL)
        self.assertEqual(parse_store_barcode(code), STORE_BARCODE_MAX_SERIAL)

    def test_out_of_range_serial_rejected(self):
        for serial in (0, -1, STORE_BARCODE_MAX_SERIAL + 1):
            with self.subTest(serial=serial):
                with self.assertRaises(ValidationError):
                    format_store_barcode(serial)

    def test_round_trip(self):
        for serial in (1, 2, 175, 405, 999999):
            with self.subTest(serial=serial):
                self.assertEqual(parse_store_barcode(format_store_barcode(serial)), serial)


class ParseTests(unittest.TestCase):
    def test_accepts_lowercase_and_surrounding_space(self):
        code = format_store_barcode(405)
        self.assertEqual(parse_store_barcode("  " + code.lower() + " "), 405)

    def test_rejects_wrong_check_digit(self):
        code = format_store_barcode(1)
        wrong = code[:-1] + str((int(code[-1]) + 1) % 10)
        self.assertIsNone(parse_store_barcode(wrong))

    def test_rejects_single_digit_typo(self):
        """任一位數字打錯都必須被檢查碼擋下。"""
        code = format_store_barcode(123456)
        for pos in range(2, 8):
            for digit in "0123456789":
                if digit == code[pos]:
                    continue
                with self.subTest(pos=pos, digit=digit):
                    self.assertIsNone(parse_store_barcode(code[:pos] + digit + code[pos + 1:]))

    def test_rejects_bad_shapes(self):
        for value in (None, 123, "", "TL", "TL123456", "TL12345678",
                      "TL00000A7", "XX0000017", "0000017"):
            with self.subTest(value=value):
                self.assertIsNone(parse_store_barcode(value))

    def test_prefix_helper(self):
        self.assertTrue(has_store_barcode_prefix(" tl0000017 "))
        self.assertTrue(has_store_barcode_prefix("TL9"))          # 字頭保留，格式不論
        self.assertFalse(has_store_barcode_prefix("4711592494257"))
        self.assertFalse(has_store_barcode_prefix(None))


class CounterTests(ConnTestCase):
    def test_counter_starts_at_one_and_advances(self):
        self.assertEqual(next_store_barcode(self.conn), format_store_barcode(1))
        self.assertEqual(next_store_barcode(self.conn), format_store_barcode(2))
        self.assertEqual(next_store_barcode(self.conn), format_store_barcode(3))

    def test_counter_persists_next_serial(self):
        next_store_barcode(self.conn)
        row = self.conn.execute(
            "SELECT value FROM Setting WHERE key='next_store_barcode'").fetchone()
        self.assertEqual(int(row["value"]), 2)

    def test_rollback_returns_the_number(self):
        """交易回滾時計數器一併回復，號碼不會被吃掉。"""
        first = next_store_barcode(self.conn)
        self.conn.rollback()
        self.assertEqual(next_store_barcode(self.conn), first)

    def test_exhausted_counter_reports_clear_error(self):
        self.conn.execute(
            "INSERT OR REPLACE INTO Setting(key,value) VALUES('next_store_barcode',?)",
            (str(STORE_BARCODE_MAX_SERIAL + 1),))
        with self.assertRaises(ValidationError):
            next_store_barcode(self.conn)


class ScanGuardTests(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.cid = self.create_category("鋼化玻璃")
        product = self.invoke("products.create", {
            "name": "測試膜", "category_id": self.cid,
            "variants": [{"attributes": {}, "price": 100}]})
        self.variant_id = product["variant_ids"][0]
        self.code = self.invoke("barcodes.add", {"variant_id": self.variant_id})["barcode"]

    def test_generated_code_is_new_format_and_scannable(self):
        self.assertIsNotNone(parse_store_barcode(self.code))
        self.assertEqual(self.invoke("barcodes.scan", {"code": self.code})["variant_id"],
                         self.variant_id)

    def test_lowercase_scan_still_reports_not_found_not_format_error(self):
        """小寫輸入格式有效，僅是資料庫查無；錯誤語意不可與格式錯誤混用。"""
        self.assert_application_error("not_found", "barcodes.scan",
                                      {"code": self.code.lower()})

    def test_malformed_store_barcode_is_validation_error(self):
        broken = self.code[:-1] + str((int(self.code[-1]) + 1) % 10)
        self.assert_application_error("validation_error", "barcodes.scan", {"code": broken})

    def test_unknown_factory_barcode_is_still_not_found(self):
        self.assert_application_error("not_found", "barcodes.scan",
                                      {"code": "4711592494257"})

    def test_manual_store_prefix_barcode_still_rejected(self):
        self.assert_application_error(
            "validation_error", "barcodes.add",
            {"variant_id": self.variant_id, "barcode": format_store_barcode(999)})
