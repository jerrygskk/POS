import os, glob, sqlite3, datetime

KEEP = {"day": 7, "week": 4, "month": 12}

def _snapshot(db_path, dest):
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(dest + ".tmp")
        src.backup(dst)
        dst.close()
        os.replace(dest + ".tmp", dest)
    finally:
        src.close()

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
