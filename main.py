import traceback
from lib.db import (DatabaseTooNewError, DatabaseTooOldError, check_supported_version, init_db,
                    read_schema_version)
from lib.db_schema import SCHEMA_VERSION
from lib.backup import backup_before_upgrade, run_auto_backup
from lib.desktop_application import DesktopApplication
from lib.runtime_paths import RuntimePaths

def log_runtime_error(paths, message, exc):
    paths.root_dir.mkdir(parents=True, exist_ok=True)
    with paths.error_log_path.open("a", encoding="utf-8") as log:
        log.write(f"{message}: {exc}\n")
        log.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        log.write("\n")


def try_log_runtime_error(paths, message, exc):
    try:
        log_runtime_error(paths, message, exc)
    except Exception:
        pass


def prepare_runtime(paths):
    # 升級資料庫格式前先備份;讀不到版號就不動資料庫
    if paths.db_path.is_file():
        try:
            version = read_schema_version(paths.db_path)
        except Exception as exc:
            try_log_runtime_error(paths, "資料庫初始化失敗", exc)
            raise
        # 過新／過舊都要在升級前備份之前擋下:被拒絕的資料庫不留下任何新檔
        check_supported_version(version)
        if version is not None and version < SCHEMA_VERSION:
            try:
                backup_before_upgrade(paths.db_path, paths.backup_dir, version, SCHEMA_VERSION)
            except Exception as exc:
                try_log_runtime_error(paths, "升級前備份失敗，已停止升級資料庫", exc)
                raise
    try:
        init_db(paths.db_path, require_existing=True)
    except Exception as exc:
        try_log_runtime_error(paths, "資料庫初始化失敗", exc)
        raise

    try:
        run_auto_backup(
            paths.db_path,
            paths.backup_dir,
            on_error=lambda message, exc: try_log_runtime_error(paths, message, exc),
        )
    except Exception as exc:
        try_log_runtime_error(paths, "自動備份失敗", exc)

def show_error_dialog(title, message):
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)


def main(application_factory=None, error_dialog=show_error_dialog):
    paths = RuntimePaths.detect(module_file=__file__)
    try:
        prepare_runtime(paths)
    except FileNotFoundError:
        error_dialog(
            "POS 無法啟動",
            f"找不到資料庫檔案：\n{paths.db_path}\n\n請將資料庫檔案 pos.db 放到程式所在資料夾後再重新啟動。",
        )
        raise SystemExit(1)
    except DatabaseTooNewError:
        error_dialog("POS 無法啟動", "資料庫版本比程式新，請更新程式後再開啟。")
        raise SystemExit(1)
    except DatabaseTooOldError:
        error_dialog("POS 無法啟動", "資料庫版本過舊，本程式已不支援，請改用新版標準資料庫。")
        raise SystemExit(1)
    except Exception as exc:
        # prepare_runtime 已寫入 error.log，這裡只給使用者看得懂的簡短原因
        error_dialog(
            "POS 無法啟動",
            f"資料庫準備失敗：{exc}\n\n詳細記錄已寫入：\n{paths.error_log_path}",
        )
        raise SystemExit(1)
    application_factory = application_factory or DesktopApplication
    try:
        application_factory(paths).run()
    except Exception as exc:
        try_log_runtime_error(paths, "桌面視窗啟動失敗", exc)
        raise

if __name__ == "__main__":
    main()
