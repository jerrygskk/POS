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
    "field_editor": {
        "file": "field_editor.html",
        "title": "規格設定",
        "size": (620, 700),
        "min_size": (520, 480),
    },
}

# 夾住子視窗尺寸時保留給工作列、標題列與邊框的邏輯像素。
SCREEN_MARGIN = (60, 80)


def fit_size(size, min_size, work_size):
    """把想要的視窗尺寸夾進可視工作區,但不低於最小尺寸。

    work_size 為工作區的邏輯像素 (寬, 高);量不到時傳 None,原尺寸照用。
    """
    if not work_size:
        return tuple(size)
    out = []
    for want, low, avail, margin in zip(size, min_size, work_size, SCREEN_MARGIN):
        limit = avail - margin
        if limit < low:
            limit = low
        out.append(min(want, limit))
    return tuple(out)


def fit_position(size, work_size):
    """算子視窗左上角座標:水平置中、垂直落在中央偏上。

    垂直用剩餘空間的 1/3(而非 1/2),視覺重心比正中央高一點;
    量不到工作區回 None,交給作業系統預設位置。
    """
    if not work_size:
        return None
    width, height = size
    avail_w, avail_h = work_size
    x = max(0, (avail_w - width) // 2)
    y = max(0, (avail_h - height) // 3)
    return (x, y)


def screen_work_size():
    """回傳 Windows 工作區的邏輯像素尺寸(已扣工作列);量不到回 None。"""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
        rect = wintypes.RECT()
        # SPI_GETWORKAREA = 0x0030
        if not user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return None
        dc = user32.GetDC(0)
        try:
            dpi = gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
        finally:
            user32.ReleaseDC(0, dc)
        scale = (dpi or 96) / 96.0
        if scale <= 0:
            scale = 1.0
        width = int((rect.right - rect.left) / scale)
        height = int((rect.bottom - rect.top) / scale)
        if width <= 0 or height <= 0:
            return None
        return (width, height)
    except Exception:
        return None


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
        # 視窗標題可由開啟端指定(如「新增規格」/「修改規格」),未給則用頁面預設
        title = payload.get("title")
        title = str(title).strip() if title is not None else ""
        if not title:
            title = spec["title"]
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
        work_size = screen_work_size()
        width, height = fit_size(spec["size"], spec["min_size"], work_size)
        position = fit_position((width, height), work_size)
        extra = {}
        if position is not None:
            extra["x"], extra["y"] = position
        try:
            window = self._webview.create_window(
                title,
                entry_point.resolve().as_uri(),
                js_api=child_bridge,
                width=width,
                height=height,
                min_size=spec["min_size"],
                resizable=True,
                **extra,
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
