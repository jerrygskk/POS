"""日報表：讀 POS 已立帳的營業額與個績，加上老闆手填的開支與貨款，產生日報與月總表 PDF。"""
import ctypes
import datetime
import logging
import sys
import traceback
from pathlib import Path

# 開發時直接執行本檔要能 import daily_report；打包後不可加：上一層是系統 Temp，
# 會讓同名的零散 .py(例如 clr.py)蓋掉套件，pywebview 因此開不起來
if __package__ in (None, "") and not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daily_report import calc, pdf_export
from daily_report.pos_reader import PosReadError, read_month
from daily_report.store import Store, StoreError

DB_SETTING = "pos_db_path"


def runtime_dirs():
    """(資料夾放 daily_report.db 與 error.log, 前端檔案資料夾)。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent, Path(sys._MEIPASS) / "static"
    here = Path(__file__).resolve().parent
    return here, here.parent / "static"


def _parse_date(text):
    try:
        return datetime.date.fromisoformat(text)
    except (TypeError, ValueError):
        raise StoreError("日期格式錯誤") from None


class Bridge:
    """前端呼叫的介面；回傳一律 {ok, data} 或 {ok: False, error}。"""

    def __init__(self, store, logger):
        self._store = store
        self._logger = logger
        self._window = None
        self._dirty = False

    def _respond(self, func, *args):
        try:
            return {"ok": True, "data": func(*args)}
        except (StoreError, PosReadError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            self._logger.exception("日報表執行失敗")
            return {"ok": False, "error": "發生未預期的錯誤，詳細記錄已寫入 error.log"}

    def _month(self, date):
        db_path = self._store.get_setting(DB_SETTING)
        pos_days, pos_error = {}, ""
        if not db_path:
            pos_error = "尚未選擇 POS 資料庫"
        else:
            try:
                pos_days = read_month(db_path, date.year, date.month)
            except PosReadError as exc:
                pos_error = str(exc)
        entries = self._store.month_entries(date.year, date.month)
        return calc.build_month(date.year, date.month, pos_days, entries), pos_error

    def _day(self, text):
        date = _parse_date(text)
        rows, pos_error = self._month(date)
        return {"row": rows[date.day - 1], "pos_error": pos_error,
                "db_path": self._store.get_setting(DB_SETTING) or ""}

    def load(self, date):
        return self._respond(self._day, date)

    def today(self):
        return self._respond(lambda: datetime.date.today().isoformat())

    def choose_db(self):
        def run():
            picked = self._window.create_file_dialog(
                10, file_types=("POS 資料庫 (*.db)", "所有檔案 (*.*)"))
            if not picked:
                return {"cancelled": True}
            path = picked[0] if isinstance(picked, (list, tuple)) else picked
            read_month(path, 2000, 1)  # 先確認是 POS 資料庫才記住
            self._store.set_setting(DB_SETTING, str(Path(path).resolve()))
            return {"cancelled": False}
        return self._respond(run)

    def save(self, date, data):
        def run():
            _parse_date(date)
            self._store.save_day(date, data)
            self._dirty = False
            return self._day(date)
        return self._respond(run)

    def set_dirty(self, dirty):
        self._dirty = bool(dirty)
        return {"ok": True, "data": None}

    def _save_pdf(self, filename, build):
        picked = self._window.create_file_dialog(30, save_filename=filename, file_types=("PDF (*.pdf)",))
        if not picked:
            return {"cancelled": True}
        path = picked[0] if isinstance(picked, (list, tuple)) else picked
        build(path)
        return {"cancelled": False, "path": str(path)}

    def export_day(self, date):
        def run():
            day = _parse_date(date)
            rows, _ = self._month(day)
            top, bottom = pdf_export.pair_of(rows, day.day)
            name = top["date"] + (f"至{bottom['date'][-2:]}" if bottom else "")
            return self._save_pdf(f"日報表_{name}.pdf",
                                  lambda path: pdf_export.day_pages_pdf(path, [(top, bottom)]))
        return self._respond(run)

    def export_days(self, date):
        def run():
            day = _parse_date(date)
            rows, _ = self._month(day)
            # 整月只印有資料的日子所在的那幾面，月底還沒到的日子不印空白頁
            pairs = [pdf_export.pair_of(rows, d) for d in range(1, len(rows) + 1, 2)]
            pairs = [p for p in pairs if p[0]["has_data"] or (p[1] and p[1]["has_data"])]
            if not pairs:
                raise StoreError("本月還沒有任何資料可以匯出")
            return self._save_pdf(f"日報表_{day:%Y-%m}.pdf", lambda path: pdf_export.day_pages_pdf(path, pairs))
        return self._respond(run)

    def export_month(self, date):
        def run():
            day = _parse_date(date)
            rows, _ = self._month(day)
            return self._save_pdf(f"月總表_{day:%Y-%m}.pdf",
                                  lambda path: pdf_export.month_pdf(path, day.year, day.month, rows))
        return self._respond(run)

    def on_closing(self):
        if not self._dirty:
            return True
        # MB_OKCANCEL | MB_ICONWARNING；回傳 1＝確定
        answer = ctypes.windll.user32.MessageBoxW(
            None, "目前的修改尚未儲存，確定要關閉並放棄修改？", "日報表", 0x01 | 0x30)
        return answer == 1


def main():
    data_dir, static_dir = runtime_dirs()
    logger = logging.getLogger("daily_report")
    logger.setLevel(logging.ERROR)
    logger.addHandler(logging.FileHandler(data_dir / "error.log", encoding="utf-8", delay=True))
    try:
        store = Store(data_dir / "daily_report.db")
    except Exception as exc:
        logger.error("".join(traceback.format_exception(exc)))
        ctypes.windll.user32.MessageBoxW(None, f"無法開啟日報表資料檔：{exc}", "日報表", 0x10)
        raise SystemExit(1)
    import webview
    bridge = Bridge(store, logger)
    window = webview.create_window(
        "日報表", (static_dir / "daily_report.html").resolve().as_uri(),
        js_api=bridge, width=1180, height=760, min_size=(940, 600), maximized=True)
    bridge._window = window
    window.events.closing += bridge.on_closing
    webview.start(gui="edgechromium")


if __name__ == "__main__":
    main()
