import unittest, tempfile, os, glob
from lib.db import init_db
from lib.backup import backup_before_upgrade, run_auto_backup

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
