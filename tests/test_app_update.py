"""線上更新背景下載狀態、跨行程鎖與關閉程式時的等待;全部注入假物件,不連網、不碰真 exe。"""
import functools
import hashlib
import io
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.error import URLError

from lib import selfupdate
from lib.app_update import AppUpdater, LOCKED, RESTART_FAILED, UNWRITABLE
from lib.application_errors import InternalError, ValidationError
from lib.desktop_bridge import DesktopBridge

INFO = {"version": "0.1.2", "sha256": "0" * 64, "size": 100, "url": "u"}


def wait_until(predicate, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class AppUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.exe = str(self.dir / "POS.exe")
        Path(self.exe).write_bytes(b"old")
        self.logger = Mock()
        self.order = []

    def make(self, **kw):
        defaults = dict(
            exe=self.exe, current="0.1.1", fetch=lambda: dict(INFO), logger=self.logger,
            downloader=lambda dest, info, progress=None, cancelled=None: Path(dest).write_bytes(b"new"),
            verifier=lambda path, info: None,
            swapper=lambda exe, new: self.order.append("swap"),
            restarter=Mock(), locker=lambda exe: "handle",
            unlocker=lambda handle: self.order.append("unlock"),
            writable=lambda folder: True, cleaner=Mock())
        defaults.update(kw)
        return AppUpdater(**defaults)

    def finish(self, updater):
        self.assertTrue(wait_until(lambda: updater.status()["state"] in ("done", "failed")))
        updater._thread.join(2)

    def test_not_frozen_never_available(self):
        fetch = Mock()
        self.assertFalse(self.make(exe=None, fetch=fetch).check()["available"])
        fetch.assert_not_called()

    def test_check_quiet_on_error(self):
        def fail():
            raise URLError("offline")
        self.assertFalse(self.make(fetch=fail).check()["available"])
        self.logger.warning.assert_called()

    def test_check_finds_newer(self):
        self.assertEqual(self.make().check(), {"available": True, "version": "0.1.2", "current": "0.1.1"})

    def test_check_same_version_not_available(self):
        self.assertFalse(self.make(current="0.1.2").check()["available"])
        self.assertFalse(self.make(fetch=lambda: None).check()["available"])

    def test_start_requires_check(self):
        with self.assertRaisesRegex(ValidationError, "沒有可安裝"):
            self.make().start()

    def test_start_unwritable_folder_raises(self):
        updater = self.make(writable=lambda folder: False)
        updater.check()
        with self.assertRaisesRegex(ValidationError, UNWRITABLE):
            updater.start()

    def test_start_twice_runs_one_download(self):
        gate, calls = threading.Event(), []
        def downloader(dest, info, progress=None, cancelled=None):
            calls.append(dest)
            gate.wait(2)
        updater = self.make(downloader=downloader)
        updater.check()
        updater.start()
        updater.start()
        gate.set()
        self.finish(updater)
        self.assertEqual(len(calls), 1)

    def test_download_failure_state_failed(self):
        def downloader(dest, info, progress=None, cancelled=None):
            raise ValueError("bad hash")
        swapper = Mock()
        updater = self.make(downloader=downloader, swapper=swapper)
        updater.check(); updater.start(); self.finish(updater)
        self.assertEqual(updater.status()["state"], "failed")
        swapper.assert_not_called()
        self.logger.error.assert_called()

    def test_verify_failure_skips_swap(self):
        def verifier(path, info):
            raise ValueError("tampered")
        swapper = Mock()
        updater = self.make(verifier=verifier, swapper=swapper)
        updater.check(); updater.start(); self.finish(updater)
        self.assertEqual(updater.status()["state"], "failed")
        swapper.assert_not_called()
        self.assertFalse(os.path.exists(self.exe + ".new"))

    def test_lock_held_through_swap(self):
        updater = self.make(locker=lambda exe: None)
        updater.check()
        with self.assertRaisesRegex(ValidationError, LOCKED):
            updater.start()
        updater = self.make()
        updater.check(); updater.start(); self.finish(updater)
        self.assertEqual(self.order, ["swap", "unlock"])
        self.order.clear()
        updater = self.make(verifier=Mock(side_effect=ValueError("x")))
        updater.check(); updater.start(); self.finish(updater)
        self.assertEqual(self.order, ["unlock"])

    def test_startup_cleanup_skips_when_locked(self):
        cleaner = Mock()
        self.make(locker=lambda exe: None, cleaner=cleaner).startup_cleanup()
        cleaner.assert_not_called()
        self.make(cleaner=cleaner).startup_cleanup()
        cleaner.assert_called_once_with(self.exe)
        self.assertEqual(self.order, ["unlock"])

    def test_restart_only_after_done(self):
        restarter, window = Mock(), Mock()
        updater = self.make(restarter=restarter)
        with self.assertRaises(ValidationError):
            updater.restart(window)
        updater.check(); updater.start(); self.finish(updater)
        updater.restart(window)
        restarter.assert_called_once_with(self.exe)
        window.destroy.assert_called_once()

    def test_restart_failure_keeps_window(self):
        window = Mock()
        updater = self.make(restarter=Mock(side_effect=OSError("no")))
        updater.check(); updater.start(); self.finish(updater)
        with self.assertRaisesRegex(InternalError, RESTART_FAILED):
            updater.restart(window)
        window.destroy.assert_not_called()

    def test_shutdown_waits_for_swap(self):
        gate, entered = threading.Event(), threading.Event()
        def swapper(exe, new):
            entered.set(); gate.wait(5)
        updater = self.make(swapper=swapper)
        updater.check(); updater.start()
        self.assertTrue(entered.wait(2))
        closer = threading.Thread(target=updater.shutdown, kwargs={"timeout": 0.1})
        closer.start()
        closer.join(0.5)
        self.assertTrue(closer.is_alive())
        gate.set()
        closer.join(2)
        self.assertFalse(closer.is_alive())
        self.assertEqual(updater.status()["state"], "done")

    def test_shutdown_during_verify_never_swaps(self):
        gate, entered = threading.Event(), threading.Event()
        def verifier(path, info):
            entered.set(); gate.wait(5)
        swapper = Mock()
        updater = self.make(verifier=verifier, swapper=swapper)
        updater.check(); updater.start()
        self.assertTrue(entered.wait(2))
        started = time.monotonic()
        updater.shutdown(timeout=0.1)
        self.assertLess(time.monotonic() - started, 1)
        gate.set()
        updater._thread.join(2)
        swapper.assert_not_called()
        self.assertNotEqual(updater.status()["state"], "done")
        self.assertFalse(os.path.exists(self.exe + ".new"))
        self.assertIn("unlock", self.order)

    def test_shutdown_cancels_download(self):
        def downloader(dest, info, progress=None, cancelled=None):
            while not cancelled():
                time.sleep(0.01)
            raise InterruptedError()
        swapper = Mock()
        updater = self.make(downloader=downloader, swapper=swapper)
        updater.check(); updater.start()
        started = time.monotonic()
        updater.shutdown(timeout=1)
        self.assertLess(time.monotonic() - started, 1)
        swapper.assert_not_called()


class RealPipelineTests(unittest.TestCase):
    """搭配真正的 selfupdate 下載／核對／換檔一起跑,只把網路換成假回應(曾因進度回呼參數不符,實際下載必定失敗)。"""

    def test_real_download_verify_swap(self):
        folder = Path(tempfile.mkdtemp())
        exe = str(folder / "POS.exe")
        Path(exe).write_bytes(b"old")
        data = b"n" * (selfupdate.CHUNK * 2 + 7)
        info = {"version": "0.1.2", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "url": "u"}
        updater = AppUpdater(
            exe=exe, current="0.1.1", fetch=lambda: dict(info), logger=Mock(),
            downloader=functools.partial(selfupdate.download, opener=lambda url, headers=None: io.BytesIO(data)),
            locker=lambda e: "h", unlocker=lambda h: None)
        updater.check()
        updater.start()
        self.assertTrue(wait_until(lambda: updater.status()["state"] in ("done", "failed")))
        updater._thread.join(2)
        self.assertEqual(updater.status(), {"state": "done", "got": len(data), "total": len(data)})
        self.assertEqual(Path(exe).read_bytes(), data)
        self.assertEqual(Path(exe + ".old").read_bytes(), b"old")


class BridgeUpdateActionTests(unittest.TestCase):
    def test_bridge_update_actions(self):
        updater = Mock()
        updater.check.return_value = {"available": True}
        updater.start.return_value = {"state": "downloading"}
        updater.status.return_value = {"state": "idle", "got": 0, "total": 0}
        window = Mock()
        bridge = DesktopBridge(updater=updater, window=window)
        evil = {"url": "https://evil.example/x.exe", "path": "C:/x"}
        self.assertEqual(bridge.invoke("desktop.update.check", evil), {"ok": True, "data": {"available": True}})
        self.assertTrue(bridge.invoke("desktop.update.start", evil)["ok"])
        self.assertTrue(bridge.invoke("desktop.update.status", evil)["ok"])
        self.assertTrue(bridge.invoke("desktop.update.restart", evil)["ok"])
        updater.check.assert_called_once_with()
        updater.start.assert_called_once_with()
        updater.status.assert_called_once_with()
        updater.restart.assert_called_once_with(window)
        none = DesktopBridge()
        self.assertFalse(none.invoke("desktop.update.check")["data"]["available"])
        self.assertFalse(none.invoke("desktop.update.start")["ok"])
        self.assertEqual(none.invoke("desktop.update.busy")["data"], {"busy": False})

    def test_busy_reflects_child_window(self):
        child = Mock()
        child.is_open.return_value = True
        bridge = DesktopBridge(child_window=child)
        self.assertEqual(bridge.invoke("desktop.update.busy", {"x": 1})["data"], {"busy": True})


if __name__ == "__main__":
    unittest.main()
