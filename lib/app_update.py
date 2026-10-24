"""線上更新的背景下載狀態:查新版 → 下載 → 核對 → 換檔 → 重開。

純邏輯在 lib/selfupdate.py;本檔只管執行緒與狀態,由 DesktopBridge 以
desktop.update.* 對前端開放(前端 payload 一律忽略,網址與路徑只用這裡記住的)。
只在打包後的 exe 動作(exe 為 None 時一律回報無更新)。
"""
import logging
import os
import threading

from lib import selfupdate
from lib.application_errors import InternalError, ValidationError
from lib.version import __version__

UNWRITABLE = "程式所在資料夾無法寫入，請將程式放在可寫入的資料夾，或向維護者索取新版本。"
LOCKED = "另一個 POS 視窗正在更新，請稍候再試。"
RESTART_FAILED = "新版本已安裝完成，但無法自動重新啟動，請關閉程式後重新開啟。"


class AppUpdater:
    def __init__(self, exe=None, current=__version__, fetch=selfupdate.fetch_latest,
                 downloader=selfupdate.download, swapper=selfupdate.swap,
                 restarter=selfupdate.restart, logger=None, verifier=selfupdate.verify_file,
                 locker=selfupdate.acquire_update_lock, unlocker=selfupdate.release_update_lock,
                 writable=selfupdate.can_write, cleaner=selfupdate.cleanup):
        self.exe = exe
        self.current = current
        self._fetch, self._download, self._swap = fetch, downloader, swapper
        self._restart, self._verify = restarter, verifier
        self._lock_update, self._unlock_update = locker, unlocker
        self._writable, self._cleanup = writable, cleaner
        self._logger = logger or logging.getLogger("pos.update")
        self._state_lock = threading.Lock()
        self._info = None
        self._state = "idle"
        self._got = 0
        self._cancelled = False
        self._thread = None

    def startup_cleanup(self):
        if self.exe is None:
            return
        handle = self._lock_update(self.exe)
        if handle is None:
            return  # 別的 POS 正在更新,不動它的暫存檔
        try:
            self._cleanup(self.exe)
        finally:
            self._unlock_update(handle)

    def check(self):
        result = {"available": False, "version": None, "current": self.current}
        if self.exe is None:
            return result
        try:
            info = self._fetch()  # 已核對過格式;不符丟 ValueError
        except Exception as exc:
            self._logger.warning("查詢新版本失敗：%s", exc)
            return result
        if info is None or not selfupdate.is_newer(info["version"], self.current):
            return result
        with self._state_lock:
            self._info = info
        return {"available": True, "version": info["version"], "current": self.current}

    def start(self):
        with self._state_lock:
            if self._info is None:
                raise ValidationError("目前沒有可安裝的新版本")
            if self._state in ("downloading", "swapping", "done"):
                return {"state": self._state}
            if not self._writable(os.path.dirname(self.exe)):
                raise ValidationError(UNWRITABLE)
            handle = self._lock_update(self.exe)
            if handle is None:
                raise ValidationError(LOCKED)
            self._state, self._got, self._cancelled = "downloading", 0, False
            self._thread = threading.Thread(target=self._work, args=(handle, self._info), daemon=True)
            self._thread.start()
            return {"state": self._state}

    def _progress(self, got, total):  # selfupdate.download 回報 (已下載, 宣稱大小)
        with self._state_lock:
            self._got = got

    def _work(self, handle, info):
        new = selfupdate.new_path(self.exe)
        try:
            self._download(new, info, progress=self._progress, cancelled=lambda: self._cancelled)
            self._verify(new, info)
            # 「已取消」與「進入換檔」在同一把鎖內二選一:shutdown 要嘛看到 swapping 一路等完,
            # 要嘛保證這裡不會再換檔
            with self._state_lock:
                if self._cancelled:
                    raise InterruptedError("程式關閉，取消更新")
                self._state = "swapping"
            self._swap(self.exe, new)
            with self._state_lock:
                self._state = "done"
        except Exception as exc:
            try:
                if os.path.exists(new):
                    os.remove(new)
            except OSError:
                pass  # 下次開啟清理
            if not isinstance(exc, InterruptedError):
                self._logger.error("線上更新失敗：%s", exc, exc_info=exc)
            with self._state_lock:
                self._state = "failed"
        finally:
            self._unlock_update(handle)

    def status(self):
        with self._state_lock:
            total = self._info["size"] if self._info else 0
            return {"state": self._state, "got": self._got, "total": total}

    def restart(self, window):
        with self._state_lock:
            if self._state != "done":
                raise ValidationError("新版本尚未安裝完成")
        try:
            self._restart(self.exe)
        except Exception as exc:
            self._logger.error("重新啟動新版本失敗：%s", exc, exc_info=exc)
            raise InternalError(RESTART_FAILED) from exc
        window.destroy()

    def shutdown(self, timeout=3):
        with self._state_lock:
            self._cancelled = True
            state, thread = self._state, self._thread
        if thread is None:
            return
        if state == "swapping":
            thread.join()  # 換檔只是兩次改名,不得在 exe 改名後、新檔放回前結束行程
        elif state == "downloading":
            thread.join(timeout)  # 已設取消旗標,之後不會再進入換檔
