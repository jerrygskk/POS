import traceback
from lib.db import init_db, read_schema_version
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

def main(application_factory=None):
    paths = RuntimePaths.detect(module_file=__file__)
    prepare_runtime(paths)
    application_factory = application_factory or DesktopApplication
    try:
        application_factory(paths).run()
    except Exception as exc:
        try_log_runtime_error(paths, "桌面視窗啟動失敗", exc)
        raise

if __name__ == "__main__":
    main()
