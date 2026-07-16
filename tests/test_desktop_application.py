import tempfile
import unittest
import inspect
import types
from pathlib import Path
from unittest.mock import Mock, patch

import main
from lib.desktop_application import DesktopApplication
from lib.desktop_bridge import DesktopBridge
from lib.runtime_paths import RuntimePaths


class FakeWebview:
    def __init__(self):
        self.create_window_calls = []
        self.start_calls = []
        self.window = object()

    def create_window(self, *args, **kwargs):
        self.create_window_calls.append((args, kwargs))
        return self.window

    def start(self, *args, **kwargs):
        self.start_calls.append((args, kwargs))


class DesktopApplicationTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "static").mkdir()
        (self.root / "static" / "index.html").write_text("POS", encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
