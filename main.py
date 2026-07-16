import traceback
from lib.db import init_db
from lib.backup import run_auto_backup
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
