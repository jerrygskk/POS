import unittest, tempfile, os, glob
from unittest.mock import patch
from lib.db import init_db
from lib import backup
from lib.backup import backup_after_close, backup_before_upgrade, offsite_ready, run_auto_backup

class TestBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pos.db")
        init_db(self.db)

    def test_creates_daily(self):
        run_auto_backup(self.db)
        self.assertEqual(len(glob.glob(os.path.join(self.tmp, "backups", "pos_day_*.db"))), 1)

    def test_same_day_no_duplicate(self):
        run_auto_backup(self.db); run_auto_backup(self.db)
        self.assertEqual(len(glob.glob(os.path.join(self.tmp, "backups", "pos_day_*.db"))), 1)

    def test_prune_keeps_7(self):
        bdir = os.path.join(self.tmp, "backups"); os.makedirs(bdir)
        for d in range(1, 10):
            open(os.path.join(bdir, f"pos_day_202601{d:02d}.db"), "w").close()
        run_auto_backup(self.db)
        self.assertLessEqual(
            len(glob.glob(os.path.join(bdir, "pos_day_*.db"))), 7)

    def test_failure_silent(self):
        run_auto_backup(os.path.join(self.tmp, "no_such.db"))  # 不拋例外


class TestOffsiteBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pos.db")
        self.main = os.path.join(self.tmp, "backups")
        self.offsite = os.path.join(self.tmp, "offsite")
        init_db(self.db)

    def names(self, folder):
        return sorted(name.split("_")[1] for name in os.listdir(folder))

    def test_offsite_runs_same_rotation(self):
        os.makedirs(self.offsite)
        for d in range(1, 10):
            open(os.path.join(self.offsite, f"pos_day_202601{d:02d}.db"), "w").close()
        self.assertTrue(run_auto_backup(self.db, self.main, extra_dir=self.offsite))
        self.assertEqual(self.names(self.main), ["day", "month", "week"])
        self.assertEqual(len(glob.glob(os.path.join(self.offsite, "pos_day_*.db"))), 7)
        self.assertEqual(len(glob.glob(os.path.join(self.offsite, "pos_week_*.db"))), 1)
        self.assertEqual(len(glob.glob(os.path.join(self.offsite, "pos_month_*.db"))), 1)

    def test_missing_offsite_passes_silently_and_is_not_created(self):
        errors = []
        self.assertTrue(run_auto_backup(self.db, self.main, lambda *a: errors.append(a), self.offsite))
        self.assertEqual(errors, [])
        self.assertFalse(os.path.exists(self.offsite))
        self.assertEqual(backup_after_close(self.db, self.main, "2026-11-18", self.offsite), [])
        self.assertTrue(os.path.isfile(os.path.join(self.main, "pos_close_2026-11-18.db")))

    def test_offsite_write_failure_keeps_main_backup_and_is_logged(self):
        os.makedirs(self.offsite)
        real, errors = backup._snapshot, []
        def snapshot(db, dest):
            if dest.startswith(self.offsite):
                raise OSError("disk full")
            real(db, dest)
        with patch("lib.backup._snapshot", side_effect=snapshot):
            self.assertTrue(run_auto_backup(self.db, self.main, lambda m, e: errors.append(m), self.offsite))
            failed = backup_after_close(self.db, self.main, "2026-11-18", self.offsite)
        self.assertEqual(errors, ["異地備份失敗"])
        self.assertEqual(failed, ["異地備份資料夾"])
        self.assertEqual(self.names(self.main), ["close", "day", "month", "week"])

    def test_unreachable_network_host_is_skipped_without_touching_folder(self):
        with patch("lib.backup.socket.create_connection", side_effect=OSError), \
                patch("lib.backup.os.path.isdir") as isdir:
            self.assertFalse(offsite_ready(r"\\10.0.0.1\share\pos"))
        isdir.assert_not_called()


class TestUpgradeBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pos.db")
        init_db(self.db)

    def test_upgrade_backup_named_and_pruned(self):
        bdir = os.path.join(self.tmp, "backups"); os.makedirs(bdir)
        for d in range(1, 6):
            open(os.path.join(bdir, f"pos_upgrade_2026010{d}-000000_v1-v2.db"), "w").close()
        path = backup_before_upgrade(self.db, bdir, 18, 19)
        files = sorted(glob.glob(os.path.join(bdir, "pos_upgrade_*.db")))
        self.assertEqual(len(files), 5)
        self.assertIn(path, files)
        self.assertTrue(path.endswith("_v18-v19.db"))
