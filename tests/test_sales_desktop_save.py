import tempfile
import unittest
import logging
from pathlib import Path

from lib.desktop_bridge import DesktopBridge


class ExportFacade:
    def invoke(self, action, payload):
        if action == "sales.export":
            return {"filename": "sales.csv", "content": "\ufeff資料"}
        raise AssertionError(action)


class FakeWindow:
    def __init__(self, selected):
        self.selected = selected
        self.calls = []

    def create_file_dialog(self, kind, **kwargs):
        self.calls.append((kind, kwargs))
        return self.selected


class SalesDesktopSaveTest(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("sales-save-test")
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False

    def test_save_success_uses_native_dialog_and_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "out.csv")
            window = FakeWindow(target)
            bridge = DesktopBridge(logger=self.logger, facade=ExportFacade(), window=window, save_dialog_type="SAVE")
            result = bridge.invoke("sales.export_save", {})
            self.assertTrue(result["ok"])
            self.assertEqual(result["data"], {"cancelled": False})
            self.assertEqual(Path(target).read_text(encoding="utf-8"), "\ufeff資料")
            self.assertEqual(window.calls[0], ("SAVE", {"save_filename": "sales.csv"}))

    def test_save_cancel_is_success(self):
        bridge = DesktopBridge(logger=self.logger, facade=ExportFacade(), window=FakeWindow(None), save_dialog_type="SAVE")
        self.assertEqual(bridge.invoke("sales.export_save", {}),
                         {"ok": True, "data": {"cancelled": True}})

    def test_save_dialog_list_or_tuple_uses_first_path_and_empty_is_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            for selected in ([str(Path(tmp) / "list.csv")],
                             (str(Path(tmp) / "tuple.csv"),)):
                with self.subTest(kind=type(selected).__name__):
                    bridge = DesktopBridge(logger=self.logger, facade=ExportFacade(),
                        window=FakeWindow(selected), save_dialog_type="SAVE")
                    self.assertEqual(bridge.invoke("sales.export_save", {})["data"],
                                     {"cancelled": False})
                    self.assertTrue(Path(selected[0]).is_file())
            for selected in ([], ()):
                with self.subTest(empty=type(selected).__name__):
                    bridge = DesktopBridge(logger=self.logger, facade=ExportFacade(),
                        window=FakeWindow(selected), save_dialog_type="SAVE")
                    self.assertEqual(bridge.invoke("sales.export_save", {})["data"],
                                     {"cancelled": True})

    def test_dialog_failure_returns_fixed_internal_error_even_when_logger_fails(self):
        class BrokenWindow:
            def create_file_dialog(self, *args, **kwargs):
                raise OSError("C:/private/customer.csv")
        class BrokenLogger:
            def exception(self, *args, **kwargs):
                raise RuntimeError("logger secret")
        bridge = DesktopBridge(logger=BrokenLogger(), facade=ExportFacade(),
            window=BrokenWindow(), save_dialog_type="SAVE")
        result = bridge.invoke("sales.export_save", {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "internal_error")
        self.assertNotIn("private", result["error"]["message"])
        self.assertNotIn("logger", result["error"]["message"])

    def test_save_failure_does_not_leak_path_or_exception(self):
        class BrokenWriter:
            def __call__(self, path, content):
                raise OSError("secret path")
        bridge = DesktopBridge(logger=self.logger, facade=ExportFacade(), window=FakeWindow("C:/secret.csv"),
                               save_dialog_type="SAVE", file_writer=BrokenWriter())
        result = bridge.invoke("sales.export_save", {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "internal_error")
        self.assertNotIn("secret", result["error"]["message"])


if __name__ == "__main__":
    unittest.main()
