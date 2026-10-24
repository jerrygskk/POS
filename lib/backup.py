import os, glob, sqlite3, datetime

KEEP = {"day": 7, "week": 4, "month": 12}
UPGRADE_KEEP = 5

def _snapshot(db_path, dest):
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(dest + ".tmp")
        src.backup(dst)
        dst.close()
        os.replace(dest + ".tmp", dest)
    finally:
        src.close()

def backup_before_upgrade(db_path, backup_dir, from_version, to_version):
    """資料庫升級格式前的備份;失敗丟例外,由呼叫端決定停止升級。回傳備份檔路徑。"""
    bdir = os.fspath(backup_dir)
    os.makedirs(bdir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(bdir, f"pos_upgrade_{stamp}_v{from_version}-v{to_version}.db")
    _snapshot(db_path, dest)
    files = sorted(glob.glob(os.path.join(bdir, "pos_upgrade_*.db")))
    for old in files[:-UPGRADE_KEEP]:
        os.remove(old)
    return dest

def run_auto_backup(db_path, backup_dir=None, on_error=None):
    try:
        if not os.path.exists(db_path):
            return False
        bdir = os.fspath(backup_dir) if backup_dir is not None else os.path.join(
            os.path.dirname(os.path.abspath(db_path)), "backups")
        os.makedirs(bdir, exist_ok=True)
        today = datetime.date.today()
        tags = {"day": today.strftime("%Y%m%d"),
                "week": f"{today.isocalendar()[0]}W{today.isocalendar()[1]:02d}",
                "month": today.strftime("%Y%m")}
        for kind, tag in tags.items():
            pattern = os.path.join(bdir, f"pos_{kind}_*.db")
            if any(tag in os.path.basename(p) for p in glob.glob(pattern)):
                continue  # 本期已備
            _snapshot(db_path, os.path.join(bdir, f"pos_{kind}_{tag}.db"))
            files = sorted(glob.glob(pattern))
            for old in files[:-KEEP[kind]]:
                os.remove(old)
        return True
    except Exception as exc:
        if on_error is not None:
            try:
                on_error("自動備份失敗", exc)
            except Exception:
                pass
        return False
