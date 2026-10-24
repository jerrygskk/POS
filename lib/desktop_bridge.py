import logging
import math

from lib.application_errors import ApplicationError, InternalError, ValidationError
from lib.auth import ADMIN_ACTIONS, PASSIVE_ACTIONS, UPDATE_ACTIONS

MAX_DETAILS_DEPTH = 32


class DesktopBridge:
    """將應用操作結果轉為桌面前端使用的固定 envelope。"""

    def __init__(self, logger=None, facade=None, window=None, save_dialog_type=None,
                 file_writer=None, child_window=None, updater=None):
        self._logger = logger or logging.getLogger(__name__)
        self._facade = facade
        self._window = window
        self._save_dialog_type = save_dialog_type
        self._file_writer = file_writer or self._write_text
        self._child_window = child_window
        self._updater = updater

    @staticmethod
    def _write_text(path, content):
        with open(path, "w", encoding="utf-8", newline="") as output:
            output.write(content)

    def _set_window(self, window, save_dialog_type):
        self._window = window
        self._save_dialog_type = save_dialog_type

    def _set_child_window(self, child_window):
        self._child_window = child_window

    def _set_updater(self, updater):
        self._updater = updater

    def _update(self, action):
        # payload 一律不用:網址與路徑只用後端自己查到的
        if action == "desktop.update.busy":
            return {"busy": self._child_window is not None and self._child_window.is_open()}
        if self._updater is None:
            if action == "desktop.update.check":
                return {"available": False, "version": None, "current": None}
            raise InternalError("線上更新尚未初始化")
        if action == "desktop.update.check":
            return self._updater.check()
        if action == "desktop.update.start":
            return self._updater.start()
        if action == "desktop.update.status":
            return self._updater.status()
        self._updater.restart(self._window)
        return {"ok": True}

    def invoke(self, action, payload=None):
        return self._respond(lambda: self._invoke(action, payload))

    def _invoke(self, action, payload):
        if not isinstance(action, str):
            raise ValidationError("不支援的操作")
        if action in UPDATE_ACTIONS:
            return self._update(action)
        if action in ADMIN_ACTIONS and self._facade is None:
            raise InternalError("桌面服務尚未初始化")
        if self._facade is not None:
            if action not in PASSIVE_ACTIONS:
                self._facade.session.touch()
            # 這些操作可能先由 bridge 攔走，必須先檢查才能開窗或顯示匯出對話框。
            if action in ADMIN_ACTIONS:
                self._facade.session.require_admin()
        if action == "desktop.child_window.open":
            return self._require_child_window().open({} if payload is None else payload)
        if action == "desktop.child_window.context":
            return self._require_child_window().context()
        if action == "desktop.child_window.close":
            data = {} if payload is None else payload
            return self._require_child_window().close(bool(data.get("saved", False)))
        if action == "variants.update_editor" and self._child_window is not None:
            return self._child_window.update_editor({} if payload is None else payload)
        if self._facade is None:
            raise InternalError("桌面服務尚未初始化")
        if action == "sales.export_save":
            return self._export_sales(payload)
        result = self._facade.invoke(action, {} if payload is None else payload)
        if action == "auth.logout" and self._child_window is not None:
            self._child_window.close(saved=False)
        return result

    def _require_child_window(self):
        if self._child_window is None:
            raise InternalError("子視窗服務尚未初始化")
        return self._child_window

    def _export_sales(self, payload):
        if self._window is None or self._save_dialog_type is None:
            raise InternalError()
        exported = self._facade.invoke("sales.export", {} if payload is None else payload)
        selected = self._window.create_file_dialog(
            self._save_dialog_type, save_filename=exported["filename"])
        if not selected:
            return {"cancelled": True}
        path = selected[0] if isinstance(selected, (list, tuple)) else selected
        # 儲存對話框可能開很久，實際寫檔前重新確認身分仍有效。
        self._facade.session.require_admin()
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
