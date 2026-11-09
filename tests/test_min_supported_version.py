"""最低支援版本:過舊或讀不到版號的資料庫,在任何寫入之前拒絕開啟。"""
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import main
from lib import db_schema
from lib.db import DatabaseTooOldError, get_conn, init_db, read_schema_version
from lib.runtime_paths import RuntimePaths


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _legacy_db(path, version):
    """舊格式資料庫的樣子:有資料表、有(或沒有)版號列;結構不重要,拒絕發生在讀版號之後。"""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE CategoryField(category_id INTEGER, field_id INTEGER)")
    conn.execute("CREATE TABLE Setting(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    if version is not None:
        conn.execute("INSERT INTO Setting VALUES('schema_version',?)", (str(version),))
    conn.commit()
    conn.close()


class MinSupportedVersionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.db = self.root / "pos.db"

    def assert_rejected_without_writing(self):
        before, mtime = _digest(self.db), self.db.stat().st_mtime_ns
        with self.assertRaises(DatabaseTooOldError):
            init_db(self.db, require_existing=True)
        self.assertEqual(_digest(self.db), before)
        self.assertEqual(self.db.stat().st_mtime_ns, mtime)
        self.assertEqual([p.name for p in self.root.iterdir()], ["pos.db"])

    def test_min_supported_is_above_v24(self):
        self.assertGreater(db_schema.MIN_SUPPORTED_VERSION, 24)
        self.assertLessEqual(db_schema.MIN_SUPPORTED_VERSION, db_schema.SCHEMA_VERSION)

    def test_v24_database_is_rejected_before_any_write(self):
        _legacy_db(self.db, 24)
        self.assert_rejected_without_writing()

    def test_database_with_tables_but_no_version_is_rejected(self):
        _legacy_db(self.db, None)
        self.assert_rejected_without_writing()

    def test_unparsable_version_is_rejected(self):
        _legacy_db(self.db, "invalid")
        self.assert_rejected_without_writing()

    def test_main_rejects_unparsable_version_without_error_log(self):
        paths = RuntimePaths.from_root(self.root)
        _legacy_db(paths.db_path, "invalid")
        before = _digest(paths.db_path)
        dialog, app = Mock(), Mock()
        with patch.object(RuntimePaths, "detect", return_value=paths):
            with self.assertRaises(SystemExit):
                main.main(application_factory=app, error_dialog=dialog)
        self.assertIn("資料庫版本過舊", dialog.call_args.args[1])
        self.assertEqual(_digest(paths.db_path), before)
        self.assertEqual(sorted(p.name for p in self.root.iterdir() if p.is_file()), ["pos.db"])

    def test_database_without_setting_table_is_rejected(self):
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE Product(product_id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        self.assert_rejected_without_writing()

    def test_new_database_is_created_at_latest_version(self):
        init_db(self.db)
        self.assertEqual(read_schema_version(self.db), db_schema.SCHEMA_VERSION)

    def test_empty_file_is_treated_as_new(self):
        self.db.write_bytes(b"")
        init_db(self.db)
        self.assertEqual(read_schema_version(self.db), db_schema.SCHEMA_VERSION)

    def test_reopening_latest_runs_no_upgrade_and_keeps_version(self):
        init_db(self.db)
        step = Mock()
        with patch.object(db_schema, "MIGRATIONS", [(db_schema.SCHEMA_VERSION, step)]):
            init_db(self.db, require_existing=True)
        step.assert_not_called()
        self.assertEqual(read_schema_version(self.db), db_schema.SCHEMA_VERSION)
        conn = get_conn(self.db)
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM Setting WHERE key='schema_version'").fetchone()[0], 1)
        finally:
            conn.close()

    def test_main_shows_dialog_and_leaves_old_database_untouched(self):
        paths = RuntimePaths.from_root(self.root)
        _legacy_db(paths.db_path, 24)
        before = _digest(paths.db_path)
        dialog, app = Mock(), Mock()
        with patch.object(RuntimePaths, "detect", return_value=paths):
            with self.assertRaises(SystemExit):
                main.main(application_factory=app, error_dialog=dialog)
        self.assertIn("資料庫版本過舊", dialog.call_args.args[1])
        app.assert_not_called()
        self.assertEqual(_digest(paths.db_path), before)
        self.assertFalse(paths.backup_dir.exists() and any(paths.backup_dir.iterdir()))


if __name__ == "__main__":
    unittest.main()
