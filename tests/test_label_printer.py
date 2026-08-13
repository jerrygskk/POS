"""NIIMBOT protocol unit tests without serial hardware."""
import unittest
from unittest.mock import patch

from PIL import Image

from lib.application_errors import ValidationError
from lib.label_printer import (LabelPrinter, PrinterReportedError, build_packet, parse_packet,
                               translate_printer_error)


class FakeSerial:
    def __init__(self, incoming=b"", close_error=None):
        self.incoming = bytearray(incoming)
        self.close_error = close_error
        self.writes = []
    @property
    def in_waiting(self):
        return len(self.incoming)
    def read(self, size):
        data = bytes(self.incoming[:size])
        del self.incoming[:size]
        return data
    def reset_input_buffer(self):
        self.incoming.clear()
    def write(self, data): self.writes.append(data)
    def close(self):
        if self.close_error: raise self.close_error


class LabelPrinterProtocolTests(unittest.TestCase):
    def test_build_packet_uses_documented_frame_and_xor_checksum(self):
        self.assertEqual(build_packet(0x21, b"\x05"), bytes.fromhex("555521010525aaaa"))

    def test_parse_packet_returns_type_and_payload(self):
        self.assertEqual(parse_packet(bytes.fromhex("555521010525aaaa")), (0x21, b"\x05"))

    def test_parse_packet_rejects_bad_checksum(self):
        with self.assertRaisesRegex(ValueError, "checksum"):
            parse_packet(bytes.fromhex("555521010524aaaa"))

    def test_machine_error_is_translated_to_validation_error(self):
        with self.assertRaisesRegex(Exception, "標籤機沒有回應"):
            translate_printer_error(ValueError("printer error"))

    def test_request_ignores_async_progress_before_expected_response(self):
        progress = build_packet(0xD3, b"\x01")
        response = build_packet(0x31, b"\x01")
        printer = LabelPrinter(sleep=lambda _: None)
        printer._serial = FakeSerial(progress + response)
        self.assertEqual(printer._request(0x21, b"\x05", 16), b"\x01")

    def test_request_allows_progress_for_every_image_row_before_expected_response(self):
        progress = build_packet(0xD3, b"\x01")
        response = build_packet(0xE4, b"\x01")
        printer = LabelPrinter(sleep=lambda _: None)
        printer._serial = FakeSerial(progress * 160 + response)
        self.assertEqual(printer._request(0xE3, b"\x01"), b"\x01")

    def test_request_deadline_stops_unbounded_progress_packets(self):
        times = iter((0, 0, 0, 2))
        printer = LabelPrinter(sleep=lambda _: None, monotonic=lambda: next(times), request_timeout=1)
        printer._serial = FakeSerial()
        with patch.object(printer, "_read_packet", return_value=(0xD3, b"\x01")):
            with self.assertRaisesRegex(ValueError, "timeout"):
                printer._request(0xE3, b"\x01")

    def test_short_heartbeat_is_translated_to_validation_error(self):
        printer = LabelPrinter()
        with patch.object(printer, "_request", return_value=b"\0" * 9):
            with self.assertRaisesRegex(ValueError, "heartbeat"):
                printer._heartbeat()

    def test_close_failure_preserves_primary_error_and_clears_serial(self):
        printer = LabelPrinter()
        printer._serial = FakeSerial(close_error=OSError("close failed"))
        with patch.object(printer, "_open"), patch.object(printer, "_heartbeat", side_effect=ValueError("primary")):
            with self.assertRaisesRegex(ValidationError, "標籤機沒有回應"):
                printer.print(Image.new("RGB", (8, 1)), 1)
        self.assertIsNone(printer._serial)

    def test_start_print_retries_while_printer_still_busy(self):
        """前一個工作收尾時機器會以回應內容 0 拒絕新工作，必須等到它接受。"""
        printer = LabelPrinter(sleep=lambda _: None)
        replies = iter((b"\x00", b"\x00", b"\x01"))
        with patch.object(printer, "_request", side_effect=lambda *a, **k: next(replies)):
            printer._start_print()

    def test_start_print_gives_up_when_printer_keeps_refusing(self):
        times = iter((0, 0, 1, 20))
        printer = LabelPrinter(sleep=lambda _: None, monotonic=lambda: next(times))
        with patch.object(printer, "_request", return_value=b"\x00"):
            with self.assertRaisesRegex(ValueError, "rejected"):
                printer._start_print()

    def test_print_sends_one_page_per_copy(self):
        """設定張數不會讓機器自行重複列印，每張都要自己送一次頁面。"""
        printer = LabelPrinter(sleep=lambda _: None)
        sent = []
        with patch.object(printer, "_open"), patch.object(printer, "_heartbeat"), \
             patch.object(printer, "_send_image"), patch.object(printer, "_end_page_print"), \
             patch.object(printer, "_wait_for_completion"), \
             patch.object(printer, "_request",
                          side_effect=lambda code, *a, **k: sent.append(code) or b"\x01"):
            printer.print(Image.new("RGB", (8, 1)), 3)
        self.assertEqual(sent.count(0x03), 3)
        self.assertEqual(sent.count(0xF3), 1)

    def test_wait_for_completion_waits_for_every_copy(self):
        printer = LabelPrinter(sleep=lambda _: None)
        statuses = iter(((0, 0, 0), (1, 100, 100), (2, 100, 100)))
        with patch.object(printer, "get_print_status", side_effect=lambda: next(statuses)):
            printer._wait_for_completion(2)

    def test_failure_still_ends_the_job_so_the_printer_is_not_left_busy(self):
        """未收工的工作會讓機器回絕下一次列印，症狀看起來像卡紙。"""
        printer = LabelPrinter(sleep=lambda _: None)
        serial = FakeSerial()
        printer._serial = serial
        with patch.object(printer, "_open"), \
             patch.object(printer, "_heartbeat", side_effect=ValueError("boom")):
            with self.assertRaises(ValidationError):
                printer.print(Image.new("RGB", (8, 1)), 1)
        self.assertIn(build_packet(0xF3, b"\x01"), serial.writes)

    def test_reported_error_codes_map_to_actionable_messages(self):
        with self.assertRaisesRegex(ValidationError, "上蓋未關閉"):
            translate_printer_error(PrinterReportedError(1))
        with self.assertRaisesRegex(ValidationError, "卡紙"):
            translate_printer_error(PrinterReportedError(8))
        with self.assertRaisesRegex(ValidationError, "沒有回應"):
            translate_printer_error(PrinterReportedError(99))

    def test_get_print_status_parses_only_the_first_four_response_bytes(self):
        printer = LabelPrinter()
        with patch.object(printer, "_request", return_value=b"\x00\x50\x02\x03" + b"extra"):
            self.assertEqual(printer.get_print_status(), (80, 2, 3))
