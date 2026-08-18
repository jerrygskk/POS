import tempfile
import unittest
import inspect
import threading
import types
from pathlib import Path
from unittest.mock import Mock, patch

import main
from lib import child_window
from lib.desktop_application import DesktopApplication
from lib.desktop_bridge import DesktopBridge
from lib.runtime_paths import RuntimePaths


class FakeWebview:
    def __init__(self):
        self.create_window_calls = []
        self.start_calls = []
        self.windows = []
        self.window = None

    def create_window(self, *args, **kwargs):
        self.create_window_calls.append((args, kwargs))
        window = FakeWindow()
        self.windows.append(window)
        if self.window is None:
            self.window = window
        return window

    def start(self, *args, **kwargs):
        self.start_calls.append((args, kwargs))


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        for handler in list(self.handlers):
            handler()


class FakeWindow:
    def __init__(self):
        self.events = types.SimpleNamespace(closed=FakeEvent())
        self.restore_calls = 0
        self.destroy_calls = 0
        self.js_calls = []
        self.evaluate_errors = 0

    def restore(self):
        self.restore_calls += 1

    def destroy(self):
        self.destroy_calls += 1
        self.events.closed.fire()

    def evaluate_js(self, script):
        if self.evaluate_errors:
            self.evaluate_errors -= 1
            raise RuntimeError("dispatch failed")
        self.js_calls.append(script)


class DesktopApplicationTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "static").mkdir()
        (self.root / "static" / "index.html").write_text("POS", encoding="utf-8")
        (self.root / "static" / "variant_editor.html").write_text(
            "editor", encoding="utf-8")
        (self.root / "static" / "variant_batch.html").write_text(
            "batch", encoding="utf-8")
        self.paths = RuntimePaths.from_root(self.root)

    def test_creates_one_local_window_with_bridge_and_starts_webview2(self):
        webview = FakeWebview()
        bridge = DesktopBridge()
        application = DesktopApplication(
            self.paths,
            bridge=bridge,
            webview_module=webview,
        )

        window = application.run()

        self.assertIs(window, webview.window)
        self.assertEqual(len(webview.create_window_calls), 1)
        args, kwargs = webview.create_window_calls[0]
        self.assertEqual(args[0], "POS")
        self.assertEqual(args[1], self.paths.static_dir.joinpath("index.html").as_uri())
        self.assertIs(kwargs["js_api"], bridge)
        self.assertEqual((kwargs["width"], kwargs["height"]), (1024, 768))
        self.assertEqual(kwargs.get("x", 0), 0)   # 主視窗靠左
        self.assertEqual(webview.start_calls, [((), {"gui": "edgechromium"})])

    def test_pywebview_discovers_only_invoke_on_bridge(self):
        from webview.util import inject_pywebview

        get_functions_code = next(
            constant for constant in inject_pywebview.__code__.co_consts
            if inspect.iscode(constant) and constant.co_name == "get_functions"
        )
        get_args = lambda func: list(inspect.getfullargspec(func).args)
        recursive_cell = types.CellType()
        closure = (types.CellType([]), types.CellType(get_args), recursive_cell)
        get_functions = types.FunctionType(
            get_functions_code,
            inject_pywebview.__globals__,
            closure=closure,
        )
        get_functions.__defaults__ = ("", None)
        recursive_cell.cell_contents = get_functions

        self.assertEqual({"invoke"}, set(get_functions(DesktopBridge())))

    def test_missing_static_entry_point_stops_before_window_creation(self):
        (self.paths.static_dir / "index.html").unlink()
        webview = FakeWebview()

        with self.assertRaisesRegex(FileNotFoundError, "index.html"):
            DesktopApplication(self.paths, webview_module=webview).run()

        self.assertEqual(webview.create_window_calls, [])
        self.assertEqual(webview.start_calls, [])

    def test_default_bridge_logs_internal_errors_to_runtime_error_log(self):
        application = DesktopApplication(self.paths, webview_module=FakeWebview())
        application.bridge._respond(lambda: (_ for _ in ()).throw(RuntimeError("private detail")))

        log = self.paths.error_log_path.read_text(encoding="utf-8")
        self.assertIn("private detail", log)

    def test_child_window_is_singleton_and_child_bridge_only_exposes_invoke(self):
        webview = FakeWebview()
        bridge = DesktopBridge()
        application = DesktopApplication(
            self.paths, bridge=bridge, webview_module=webview)
        application.run()
        original = {"product": {"product_id": 10}, "variant": {"variant_id": 20}}

        first = bridge.invoke("desktop.child_window.open",
                              {"page": "variant_editor", "context": original})
        second = bridge.invoke("desktop.child_window.open", {
            "page": "variant_editor",
            "context": {"product": {"product_id": 11},
                        "variant": {"variant_id": 21}}})

        self.assertTrue(first["ok"])
        self.assertEqual(first["data"],
                         {"opened": True, "reused": False, "page": "variant_editor"})
        self.assertEqual(second["data"],
                         {"opened": True, "reused": True, "page": "variant_editor"})
        self.assertEqual(len(webview.create_window_calls), 2)
        args, kwargs = webview.create_window_calls[1]
        self.assertEqual(args, (
            "款式修改", self.paths.static_dir.joinpath("variant_editor.html").as_uri()))
        child_bridge = kwargs["js_api"]
        self.assertIsInstance(child_bridge, DesktopBridge)
        self.assertEqual(
            child_bridge.invoke("desktop.child_window.context")["data"],
            {"page": "variant_editor", "context": original})
        self.assertEqual(webview.windows[1].restore_calls, 1)

        from webview.util import inject_pywebview
        get_functions_code = next(
            constant for constant in inject_pywebview.__code__.co_consts
            if inspect.iscode(constant) and constant.co_name == "get_functions"
        )
        get_args = lambda func: list(inspect.getfullargspec(func).args)
        recursive_cell = types.CellType()
        closure = (types.CellType([]), types.CellType(get_args), recursive_cell)
        get_functions = types.FunctionType(
            get_functions_code, inject_pywebview.__globals__, closure=closure)
        get_functions.__defaults__ = ("", None)
        recursive_cell.cell_contents = get_functions
        self.assertEqual({"invoke"}, set(get_functions(child_bridge)))

    def test_child_window_close_and_native_x_dispatch_exactly_once(self):
        webview = FakeWebview()
        bridge = DesktopBridge()
        DesktopApplication(
            self.paths, bridge=bridge, webview_module=webview).run()
        context = {"product": {"product_id": 1}, "variant": {"variant_id": 2}}

        bridge.invoke("desktop.child_window.open",
                      {"page": "variant_editor", "context": context})
        child = webview.windows[1]
        result = child and bridge.invoke(
            "desktop.child_window.close", {"saved": True})
        child.events.closed.fire()

        self.assertEqual(result["data"], {"closed": True})
        self.assertEqual(child.destroy_calls, 1)
        self.assertEqual(len(webview.windows[0].js_calls), 1)
        self.assertIn('"saved": true', webview.windows[0].js_calls[0])

        bridge.invoke("desktop.child_window.open",
                      {"page": "variant_editor", "context": context})
        x_child = webview.windows[2]
        x_child.events.closed.fire()
        x_child.events.closed.fire()
        self.assertEqual(len(webview.windows[0].js_calls), 2)
        self.assertIn('"saved": false', webview.windows[0].js_calls[1])

    def test_concurrent_child_window_open_creates_only_one_child(self):
        class BlockingWebview(FakeWebview):
            def __init__(self):
                super().__init__()
                self.child_entered = threading.Event()
                self.release_child = threading.Event()

            def create_window(self, *args, **kwargs):
                if self.window is not None:
                    self.child_entered.set()
                    self.release_child.wait(2)
                return super().create_window(*args, **kwargs)

        webview = BlockingWebview()
        bridge = DesktopBridge()
        DesktopApplication(self.paths, bridge=bridge, webview_module=webview).run()
        context = {"product": {"product_id": 1}, "variant": {"variant_id": 2}}
        results = []
        first = threading.Thread(target=lambda: results.append(
            bridge.invoke("desktop.child_window.open", {"page": "variant_editor", "context": context})))
        second = threading.Thread(target=lambda: results.append(
            bridge.invoke("desktop.child_window.open", {"page": "variant_editor", "context": context})))

        first.start()
        self.assertTrue(webview.child_entered.wait(1))
        second.start()
        webview.release_child.set()
        first.join(2); second.join(2)

        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(len(webview.create_window_calls), 2)
        data = [result["data"] for result in results]
        self.assertIn({"opened": True, "reused": False,
                       "page": "variant_editor"}, data)
        self.assertIn({"opened": True, "reused": True,
                       "page": "variant_editor"}, data)

    def test_variant_editor_create_failure_does_not_poison_next_open(self):
        class FailOnceWebview(FakeWebview):
            def __init__(self):
                super().__init__()
                self.fail_child = True

            def create_window(self, *args, **kwargs):
                if self.window is not None and self.fail_child:
                    self.fail_child = False
                    raise RuntimeError("create failed")
                return super().create_window(*args, **kwargs)

        webview = FailOnceWebview()
        bridge = DesktopBridge()
        DesktopApplication(self.paths, bridge=bridge, webview_module=webview).run()
        context = {"product": {"product_id": 1}, "variant": {"variant_id": 2}}

        failed = bridge.invoke("desktop.child_window.open", {"page": "variant_editor", "context": context})
        retried = bridge.invoke("desktop.child_window.open", {"page": "variant_editor", "context": context})

        self.assertFalse(failed["ok"])
        self.assertTrue(retried["ok"])
        self.assertEqual(retried["data"],
                         {"opened": True, "reused": False, "page": "variant_editor"})
        self.assertEqual(len(webview.create_window_calls), 2)

    def test_native_x_after_committed_update_dispatches_saved_true(self):
        class Facade:
            def invoke(self, action, payload):
                self.call = (action, payload)
                return {"ok": True}

        facade = Facade()
        webview = FakeWebview()
        bridge = DesktopBridge(facade=facade)
        DesktopApplication(self.paths, bridge=bridge, webview_module=webview).run()
        context = {"product": {"product_id": 1}, "variant": {"variant_id": 2}}
        bridge.invoke("desktop.child_window.open", {"page": "variant_editor", "context": context})
        child = webview.windows[1]
        child_bridge = webview.create_window_calls[1][1]["js_api"]

        result = child_bridge.invoke("variants.update_editor", {"id": 2})
        webview.windows[0].evaluate_errors = 1
        failed_close = child_bridge.invoke(
            "desktop.child_window.close", {"saved": True})
        child.events.closed.fire()

        self.assertTrue(result["ok"])
        self.assertFalse(failed_close["ok"])
        self.assertEqual(facade.call, ("variants.update_editor", {"id": 2}))
        self.assertEqual(len(webview.windows[0].js_calls), 1)
        self.assertIn('"saved": true', webview.windows[0].js_calls[0])


class MainDesktopOrchestrationTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.paths = RuntimePaths.from_root(self.root)

    def test_main_prepares_runtime_then_runs_desktop_application(self):
        application = Mock()
        factory = Mock(return_value=application)

        with patch("main.RuntimePaths.detect", return_value=self.paths), \
                patch("main.prepare_runtime") as prepare:
            main.main(application_factory=factory)

        prepare.assert_called_once_with(self.paths)
        factory.assert_called_once_with(self.paths)
        application.run.assert_called_once_with()

    def test_desktop_failure_is_logged_and_raised(self):
        application = Mock()
        application.run.side_effect = RuntimeError("WebView2 unavailable")

        with patch("main.RuntimePaths.detect", return_value=self.paths), \
                patch("main.prepare_runtime"), \
                patch("main.DesktopApplication", return_value=application):
            with self.assertRaisesRegex(RuntimeError, "WebView2 unavailable"):
                main.main()

        log = self.paths.error_log_path.read_text(encoding="utf-8")
        self.assertIn("桌面視窗啟動失敗", log)
        self.assertIn("WebView2 unavailable", log)


class ChildWindowFitSizeTests(unittest.TestCase):
    """子視窗尺寸夾進可視工作區:避免在小螢幕或高縮放環境開出畫面外。"""

    def test_size_kept_when_work_area_is_large_enough(self):
        self.assertEqual(
            child_window.fit_size((720, 760), (640, 520), (1920, 1200)),
            (720, 760))

    def test_size_shrinks_to_work_area_minus_margin(self):
        # 1920x1080 在 125% 縮放下邏輯可視高度約 816,高度須讓出工作列與標題列
        self.assertEqual(
            child_window.fit_size((980, 820), (760, 560), (1536, 816)),
            (980, 736))

    def test_min_size_wins_over_tiny_work_area(self):
        self.assertEqual(
            child_window.fit_size((720, 760), (640, 520), (600, 500)),
            (640, 520))

    def test_unknown_work_area_keeps_requested_size(self):
        self.assertEqual(
            child_window.fit_size((720, 760), (640, 520), None), (720, 760))

    def test_position_centers_horizontally_and_sits_above_middle(self):
        # 水平置中;垂直取剩餘空間 1/3,比正中央(28)高
        self.assertEqual(
            child_window.fit_position((720, 736), (1536, 816)), (408, 26))

    def test_position_never_negative_and_none_without_work_area(self):
        self.assertEqual(child_window.fit_position((900, 900), (800, 700)), (0, 0))
        self.assertIsNone(child_window.fit_position((720, 736), None))


if __name__ == "__main__":
    unittest.main()
