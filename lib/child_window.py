import copy
import json
import threading

from lib.application_errors import InternalError, ValidationError
from lib.desktop_bridge import DesktopBridge

# 可開啟的子視窗頁面白名單。頁面檔名不由前端指定,避免任意本機檔被載入。
CHILD_PAGES = {
    "variant_editor": {
        "file": "variant_editor.html",
        "title": "款式修改",
        "size": (720, 760),
        "min_size": (640, 520),
    },
    "variant_batch": {
        "file": "variant_batch.html",
        "title": "新增款式",
        "size": (980, 820),
        "min_size": (760, 560),
    },
}


class ChildWindowCoordinator:
    """管理唯一的子視窗(款式修改／新增款式)與主視窗解鎖通知。"""

    def __init__(self, webview_module, static_dir, main_window, facade, logger=None):
        self._webview = webview_module
        self._static_dir = static_dir
        self._main_window = main_window
        self._facade = facade
        self._logger = logger
        self._window = None
        self._page = None
        self._context = None
        self._committed = False
        self._lock = threading.RLock()

    def open(self, payload):
        with self._lock:
            return self._open(payload)

    def _open(self, payload):
        if not isinstance(payload, dict):
            raise ValidationError("子視窗開啟資料格式錯誤")
        page = payload.get("page")
        if page not in CHILD_PAGES:
            raise ValidationError("未知的子視窗頁面")
        context = payload.get("context")
        if context is None:
            context = {}
        if not isinstance(context, dict):
            raise ValidationError("子視窗脈絡格式錯誤")
        if self._window is not None:
            self._window.restore()
            return {"opened": True, "reused": True, "page": self._page}

        spec = CHILD_PAGES[page]
        entry_point = self._static_dir / spec["file"]
        if not entry_point.is_file():
            raise InternalError("找不到子視窗頁面")

        self._page = page
        self._context = copy.deepcopy(context)
        self._committed = False
        child_bridge = DesktopBridge(
            logger=self._logger,
            facade=self._facade,
            child_window=self,
        )
        width, height = spec["size"]
        try:
            window = self._webview.create_window(
                spec["title"],
                entry_point.resolve().as_uri(),
                js_api=child_bridge,
                width=width,
                height=height,
                min_size=spec["min_size"],
                resizable=True,
            )
            self._window = window
            window.events.closed += lambda *args: self._on_closed(window)
        except Exception:
            self._window = None
            self._page = None
            self._context = None
            self._committed = False
            raise
        return {"opened": True, "reused": False, "page": page}

    def context(self):
        with self._lock:
            if self._window is None or self._context is None:
                raise InternalError("子視窗尚未開啟")
            return {"page": self._page, "context": copy.deepcopy(self._context)}

    def update_editor(self, payload):
        with self._lock:
            if self._window is None:
                raise InternalError("子視窗尚未開啟")
            result = self._facade.invoke("variants.update_editor", payload)
            self._committed = True
            return result

    def close(self, saved=False):
        with self._lock:
            if self._window is None:
                return {"closed": False}
            window = self._window
            self._finish(bool(saved) or self._committed)
            window.destroy()
            return {"closed": True}

    def _on_closed(self, window):
        with self._lock:
            if window is self._window:
                self._finish(self._committed)

    def _finish(self, saved):
        if self._window is None:
            return
        detail = json.dumps({"saved": bool(saved), "page": self._page})
        self._main_window.evaluate_js(
            'window.dispatchEvent(new CustomEvent('
            '"pos-child-window-closed", {detail: ' + detail + '}));'
        )
        self._window = None
        self._page = None
        self._context = None
        self._committed = False
