"""第 5 組修正：資料庫版號較新時停下、啟動失敗要跳訊息、條碼整數點寬、張數上限、換檔失敗保留救回檔。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import main
from lib import db_schema, label_layout, printing_service
from lib.app_update import AppUpdater
from lib.application_errors import ValidationError
from lib.db import DatabaseTooNewError, get_conn, init_db
from lib.runtime_paths import RuntimePaths
from tests.base import FacadeTestCase


def _make_db(path, version):
    init_db(path)
    conn = get_conn(path)
    conn.execute("UPDATE Setting SET value=? WHERE key='schema_version'", (str(version),))
    conn.commit()
    conn.close()


class NewerDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.paths = RuntimePaths.from_root(self.root)

    def test_init_db_refuses_newer_database_without_writing(self):
        db = self.root / "pos.db"
        _make_db(db, db_schema.SCHEMA_VERSION + 1)
        before = db.read_bytes()
        with self.assertRaises(DatabaseTooNewError):
            init_db(db, require_existing=True)
        self.assertEqual(db.read_bytes(), before)

    def test_main_shows_dialog_and_exits_for_newer_database(self):
        _make_db(self.paths.db_path, db_schema.SCHEMA_VERSION + 1)
        dialog, app = Mock(), Mock()
        with patch.object(RuntimePaths, "detect", return_value=self.paths):
            with self.assertRaises(SystemExit):
                main.main(application_factory=app, error_dialog=dialog)
        self.assertIn("資料庫版本比程式新，請更新程式後再開啟", dialog.call_args.args[1])
        app.assert_not_called()

    def test_main_shows_dialog_for_other_startup_failure(self):
        self.paths.db_path.write_bytes(b"not sqlite")
        dialog, app = Mock(), Mock()
        with patch.object(RuntimePaths, "detect", return_value=self.paths):
            with self.assertRaises(SystemExit):
                main.main(application_factory=app, error_dialog=dialog)
        message = dialog.call_args.args[1]
        self.assertIn(str(self.paths.error_log_path), message)
        self.assertTrue(self.paths.error_log_path.exists())
        app.assert_not_called()


class BarcodeWidthTest(unittest.TestCase):
    def _bar_widths(self, image):
        row = [image.getpixel((x, 0))[0] < 128 for x in range(image.width)]
        widths, run = [], 0
        for index, black in enumerate(row + [False]):
            if black:
                run += 1
            elif run:
                widths.append(run)
                run = 0
        return widths

    def test_short_barcode_uses_two_dots_per_module(self):
        code = "4710000000001"
        self.assertEqual(label_layout.barcode_module_width(code), 2)
        image = label_layout._barcode_image(code, 10)
        self.assertEqual(image.size, (296, 10))
        self.assertTrue(all(w % 2 == 0 for w in self._bar_widths(image)))
        self.assertEqual(sum(self._bar_widths(image)),
                         2 * label_layout._barcode_modules(code).count("1"))

    def test_medium_barcode_falls_back_to_one_dot(self):
        code = "12345678901234567890123456"  # 178 單位
        self.assertEqual(label_layout.barcode_module_width(code), 1)

    def test_overlong_barcode_cannot_print(self):
        self.assertEqual(label_layout.barcode_module_width("ABCDEFGHIJKLMNOPQRSTUVWXYZABCD"), 0)


class PrintingLimitTest(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.make_category_with_field("顏色", options=("透明",))

    def _variant(self, barcode):
        return self.create_product({"顏色": "透明"}, name="玻璃保護貼", price=590,
                                   barcode=barcode, source="store")["variant_ids"][0]

    @patch("lib.printing_service.LabelPrinter.print")
    def test_copies_over_limit_rejected(self, print_label):
        vid = self._variant("LIMIT-1")
        with self.assertRaises(ValidationError):
            self.invoke("printing.barcode", {"variant_id": vid, "barcode": "LIMIT-1", "copies": 100})
        self.invoke("printing.barcode", {"variant_id": vid, "barcode": "LIMIT-1", "copies": 99})
        print_label.assert_called_once()

    @patch("lib.printing_service.LabelPrinter.print")
    def test_overlong_barcode_flagged_and_rejected(self, print_label):
        code = "ABCDEFGHIJKLMNOPQRSTUVWXYZABCD"
        vid = self._variant(code)
        item = self.invoke("printing.check", {"items": [{"variant_id": vid, "barcode": code}]})["items"][0]
        self.assertTrue(item["valid"])
        self.assertTrue(item["too_long"])
        with self.assertRaisesRegex(ValidationError, "條碼過長"):
            self.invoke("printing.barcode", {"variant_id": vid, "barcode": code})
        print_label.assert_not_called()


class SwapFailureKeepsRescueFileTest(unittest.TestCase):
    def test_new_file_kept_when_exe_missing(self):
        folder = Path(tempfile.mkdtemp())
        exe = folder / "POS.exe"
        exe.write_bytes(b"old")

        def swapper(exe_path, new):
            Path(exe_path).replace(exe_path + ".old")
            raise OSError("both renames failed")

        logger = Mock()
        updater = AppUpdater(
            exe=str(exe), current="0.1.1",
            fetch=lambda: {"version": "0.1.2", "sha256": "0" * 64, "size": 3, "url": "u"},
            logger=logger,
            downloader=lambda dest, info, progress=None, cancelled=None: Path(dest).write_bytes(b"new"),
            verifier=lambda path, info: None, swapper=swapper, restarter=Mock(),
            locker=lambda e: "h", unlocker=lambda h: None, writable=lambda f: True, cleaner=Mock())
        updater.check()
        updater.start()
        updater._thread.join(5)
        self.assertEqual(updater.status()["state"], "failed")
        news = [p for p in folder.iterdir() if p.read_bytes() == b"new"]
        self.assertEqual(len(news), 1)
        logged = " ".join(str(a) for call in logger.error.call_args_list for a in call.args)
        self.assertIn("改名回", logged)


if __name__ == "__main__":
    unittest.main()
