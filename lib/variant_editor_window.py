import copy
import json
import threading

from lib.application_errors import InternalError, ValidationError
from lib.desktop_bridge import DesktopBridge


class VariantEditorWindowCoordinator:
    """管理唯一的款式編輯子視窗與主視窗解鎖通知。"""

    def __init__(self, webview_module, static_dir, main_window, facade, logger=None):
        self._webview = webview_module
        self._static_dir = static_dir
        self._main_window = main_window
        self._facade = facade
        self._logger = logger
        self._window = None
        self._context = None
        self._committed = False
        self._lock = threading.RLock()

    def open(self, context):
        with self._lock:
            return self._open(context)

    def _open(self, context):
        if not isinstance(context, dict):
            raise ValidationError("款式編輯資料格式錯誤")
        if self._window is not None:
            self._window.restore()
            return {"opened": True, "reused": True}

        entry_point = self._static_dir / "variant_editor.html"
        if not entry_point.is_file():
            raise InternalError("找不到款式編輯頁面")

        self._context = copy.deepcopy(context)
        self._committed = False
        child_bridge = DesktopBridge(
            logger=self._logger,
            facade=self._facade,
            variant_editor=self,
        )
        try:
            window = self._webview.create_window(
                "款式修改",
                entry_point.resolve().as_uri(),
                js_api=child_bridge,
                width=720,
                height=760,
                min_size=(640, 520),
                resizable=True,
            )
            self._window = window
            window.events.closed += lambda *args: self._on_closed(window)
        except Exception:
            self._window = None
            self._context = None
            self._committed = False
            raise
        return {"opened": True, "reused": False}

    def context(self):
        with self._lock:
            if self._window is None or self._context is None:
                raise InternalError("款式編輯視窗尚未開啟")
            return copy.deepcopy(self._context)

    def update_editor(self, payload):
        with self._lock:
            if self._window is None:
                raise InternalError("款式編輯視窗尚未開啟")
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
        detail = json.dumps({"saved": bool(saved)})
        self._main_window.evaluate_js(
            'window.dispatchEvent(new CustomEvent('
            '"pos-variant-editor-closed", {detail: ' + detail + '}));'
        )
        self._window = None
        self._context = None
        self._committed = False
