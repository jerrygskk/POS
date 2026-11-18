import os, glob, socket, sqlite3, datetime

KEEP = {"day": 7, "week": 4, "month": 12}
UPGRADE_KEEP = 5

def _snapshot(db_path, dest):
    tmp = dest + ".tmp"
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
        os.replace(tmp, dest)
    except Exception:
        # 異地位置空間不足時,殘檔留著會讓下一次更寫不進去
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
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

OFFSITE_TIMEOUT = 3  # 秒
CLOSE_KEEP = 30


def _unc_host(path):
    text = path.strip().replace("/", "\\")
    return text[2:].split("\\", 1)[0] or None if text.startswith("\\\\") else None


def offsite_ready(path):
    """異地位置現在能不能用。網路路徑先試連檔案分享服務:主機離線時 Windows 要等 20～40 秒
    才放棄,直接碰資料夾會拖慢開機與立帳。不自動建立資料夾:隨身碟沒插時不能建到別的磁碟。"""
    if not path:
        return False
    host = _unc_host(path)
    if host:
        try:
            socket.create_connection((host, 445), timeout=OFFSITE_TIMEOUT).close()
        except OSError:
            return False
    return os.path.isdir(path)


def backup_after_close(db_path, backup_dir, business_date, extra_dir=None):
    """立帳成功後存一份快照(同帳日重存只留最新),異地位置連得上就再存一份。

    啟動備份「今天已備過」就跳過,當天營業內容要等隔天開機才進備份,所以立帳另存一份。
    異地位置連不上直接略過;連得上卻寫入失敗(如空間不足)才回報。
    回傳失敗的位置名稱清單,空清單＝全部成功。"""
    failed = []
    name = f"pos_close_{business_date}.db"
    targets = [(backup_dir, "備份資料夾")]
    if offsite_ready(extra_dir):
        targets.append((extra_dir, "異地備份資料夾"))
    for folder, label in targets:
        try:
            folder = os.fspath(folder)
            if folder == os.fspath(backup_dir):
                os.makedirs(folder, exist_ok=True)
            _snapshot(db_path, os.path.join(folder, name))
            for old in sorted(glob.glob(os.path.join(folder, "pos_close_*.db")))[:-CLOSE_KEEP]:
                os.remove(old)
        except Exception:
            failed.append(label)
    return failed


def _run_gfs(db_path, bdir):
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


def run_auto_backup(db_path, backup_dir=None, on_error=None, extra_dir=None):
    """本機與異地各跑一套日／週／月輪替,兩處互不影響。回傳值只代表本機備份是否成功。"""
    def report(message, exc):
        if on_error is not None:
            try:
                on_error(message, exc)
            except Exception:
                pass

    if not os.path.exists(db_path):
        return False
    done = True
    try:
        bdir = os.fspath(backup_dir) if backup_dir is not None else os.path.join(
            os.path.dirname(os.path.abspath(db_path)), "backups")
        os.makedirs(bdir, exist_ok=True)
        _run_gfs(db_path, bdir)
    except Exception as exc:
        report("自動備份失敗", exc)
        done = False
    try:
        if offsite_ready(extra_dir):
            _run_gfs(db_path, os.fspath(extra_dir))
    except Exception as exc:
        report("異地備份失敗", exc)
    return done
