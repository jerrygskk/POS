import logging
import math

from lib.application_errors import ApplicationError, InternalError

MAX_DETAILS_DEPTH = 32


class DesktopBridge:
    """將應用操作結果轉為桌面前端使用的固定 envelope。"""

    def __init__(self, logger=None, facade=None, window=None, save_dialog_type=None,
                 file_writer=None):
        self._logger = logger or logging.getLogger(__name__)
        self._facade = facade
        self._window = window
        self._save_dialog_type = save_dialog_type
        self._file_writer = file_writer or self._write_text

    @staticmethod
    def _write_text(path, content):
        with open(path, "w", encoding="utf-8", newline="") as output:
            output.write(content)

    def _set_window(self, window, save_dialog_type):
        self._window = window
        self._save_dialog_type = save_dialog_type

    def invoke(self, action, payload=None):
        if self._facade is None:
            return self._respond(lambda: (_ for _ in ()).throw(
                InternalError("桌面服務尚未初始化")))
        if action == "sales.export_save":
            return self._respond(lambda: self._export_sales(payload))
        return self._respond(
            lambda: self._facade.invoke(action, {} if payload is None else payload)
        )

    def _export_sales(self, payload):
        if self._window is None or self._save_dialog_type is None:
            raise InternalError()
        exported = self._facade.invoke("sales.export", {} if payload is None else payload)
        selected = self._window.create_file_dialog(
            self._save_dialog_type, save_filename=exported["filename"])
        if not selected:
            return {"cancelled": True}
        path = selected[0] if isinstance(selected, (list, tuple)) else selected
        try:
            self._file_writer(path, exported["content"])
        except Exception:
            try:
                self._logger.exception("銷售紀錄匯出失敗")
            except Exception:
                pass
            raise InternalError() from None
        return {"cancelled": False}

    def _respond(self, operation):
        try:
            return {"ok": True, "data": operation()}
        except ApplicationError as exc:
            return {"ok": False, "error": self._error_payload(exc)}
        except Exception:
            try:
                self._logger.exception("DesktopBridge 執行失敗")
            except Exception:
                pass
            return {"ok": False, "error": self._error_payload(InternalError())}

    @staticmethod
    def _error_payload(error):
        payload = {"code": error.code, "message": error.message}
        if error.details is not None:
            payload["details"] = DesktopBridge._json_safe(error.details)
        return payload

    @staticmethod
    def _json_safe(value, active_ids=None, depth=0):
        if depth >= MAX_DETAILS_DEPTH:
            return "[無法序列化]"
        if value is None or isinstance(value, (bool, int, str)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else "[無法序列化]"

        active_ids = set() if active_ids is None else active_ids
        value_id = id(value)
        if value_id in active_ids:
            return "[無法序列化]"

        if isinstance(value, list):
            active_ids.add(value_id)
            try:
                return [
                    DesktopBridge._json_safe(item, active_ids, depth + 1)
                    for item in value
                ]
            finally:
                active_ids.remove(value_id)

        if isinstance(value, dict):
            active_ids.add(value_id)
            try:
                return {
                    key: DesktopBridge._json_safe(item, active_ids, depth + 1)
                    for key, item in value.items()
                    if isinstance(key, str)
                }
            finally:
                active_ids.remove(value_id)

        return "[無法序列化]"
