import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from lib.backup import run_auto_backup
from lib.db import get_conn, init_db
from lib.runtime_paths import RuntimePaths


class RuntimePathsTest(unittest.TestCase):
    def test_development_paths_are_next_to_main_module(self):
        root = Path(tempfile.mkdtemp())
        paths = RuntimePaths.detect(module_file=root / "main.py", frozen=False)
        self.assertEqual(paths.root_dir, root)
        self.assertEqual(paths.db_path, root / "pos.db")
        self.assertEqual(paths.error_log_path, root / "error.log")
        self.assertEqual(paths.backup_dir, root / "backups")
        self.assertEqual(paths.static_dir, root / "static")

    def test_frozen_paths_are_next_to_executable(self):
        root = Path(tempfile.mkdtemp())
        paths = RuntimePaths.detect(
            module_file=root / "ignored" / "main.py",
            executable=root / "POS.exe",
            frozen=True,
        )
        self.assertEqual(paths.root_dir, root)
        self.assertEqual(paths.db_path, root / "pos.db")

    def test_frozen_static_resources_are_loaded_from_meipass(self):
        root = Path(tempfile.mkdtemp())
        bundle = root / "bundle"
        paths = RuntimePaths.detect(
            module_file=root / "ignored" / "main.py",
            executable=root / "POS.exe",
            frozen=True,
            bundle_dir=bundle,
        )
        self.assertEqual(paths.static_dir, bundle / "static")


class StartupTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.paths = RuntimePaths.from_root(self.root)

    def test_production_startup_rejects_missing_database_without_creating_it(self):
        with self.assertRaisesRegex(FileNotFoundError, "pos.db"):
            main.prepare_runtime(self.paths)
        self.assertFalse(self.paths.db_path.exists())
        self.assertTrue(self.paths.error_log_path.exists())

    def test_database_initialization_failure_is_logged_and_raised(self):
        self.paths.db_path.write_bytes(b"not sqlite")
        with self.assertRaises(Exception):
            main.prepare_runtime(self.paths)
        log = self.paths.error_log_path.read_text(encoding="utf-8")
        self.assertIn("資料庫初始化失敗", log)

    def test_backup_failure_is_logged_but_does_not_stop_startup(self):
        init_db(self.paths.db_path)
        with patch("main.run_auto_backup", side_effect=OSError("disk full")):
            main.prepare_runtime(self.paths)
        log = self.paths.error_log_path.read_text(encoding="utf-8")
        self.assertIn("自動備份失敗", log)
        self.assertIn("disk full", log)

    def test_backup_and_error_log_failures_do_not_stop_startup(self):
        init_db(self.paths.db_path)
        with patch("main.run_auto_backup", side_effect=OSError("disk full")), \
                patch("main.log_runtime_error", side_effect=OSError("log unwritable")):
            main.prepare_runtime(self.paths)

    def test_error_log_failure_does_not_hide_database_initialization_error(self):
        self.paths.db_path.write_bytes(b"not sqlite")
        with patch("main.log_runtime_error", side_effect=OSError("log unwritable")):
            with self.assertRaisesRegex(Exception, "file is not a database"):
                main.prepare_runtime(self.paths)

    def test_backup_accepts_explicit_directory(self):
        init_db(self.paths.db_path)
        custom = self.root / "custom-backups"
        run_auto_backup(self.paths.db_path, custom)
        self.assertEqual(len(list(custom.glob("pos_day_*.db"))), 1)

    def test_backup_failure_can_be_reported(self):
        init_db(self.paths.db_path)
        messages = []
        with patch("lib.backup._snapshot", side_effect=OSError("disk full")):
            result = run_auto_backup(
                self.paths.db_path,
                self.paths.backup_dir,
                on_error=lambda message, exc: messages.append((message, str(exc))),
            )
        self.assertFalse(result)
        self.assertEqual(messages, [("自動備份失敗", "disk full")])


class DatabaseInitializationTest(unittest.TestCase):
    def test_connections_wait_three_seconds_for_database_locks(self):
        db_path = Path(tempfile.mkdtemp()) / "pos.db"
        conn = get_conn(db_path)
        try:
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 3000)
        finally:
            conn.close()

    def test_require_existing_does_not_create_database(self):
        db_path = Path(tempfile.mkdtemp()) / "pos.db"
        with self.assertRaises(FileNotFoundError):
            init_db(db_path, require_existing=True)
        self.assertFalse(db_path.exists())
