import logging

from lib.desktop_bridge import DesktopBridge
from lib.settings_service import SettingsFacade
from lib.product_service import ProductFacade
from lib.stock_service import StockFacade
from lib.sales_service import SalesFacade
from lib.stocktake_service import StocktakeFacade
from lib.printing_service import PrintingFacade


class DesktopFacade:
    def __init__(self, db_path):
        self.settings = SettingsFacade(db_path)
        self.products = ProductFacade(db_path)
        self.stock = StockFacade(db_path)
        self.sales = SalesFacade(db_path)
        self.stocktake = StocktakeFacade(db_path)
        self.printing = PrintingFacade()

    def invoke(self, action, payload=None):
        if action in ProductFacade.ACTIONS:
            return self.products.invoke(action, payload)
        if action in StockFacade.ACTIONS:
            return self.stock.invoke(action, payload)
        if action in SalesFacade.ACTIONS:
            return self.sales.invoke(action, payload)
        if action in StocktakeFacade.ACTIONS:
            return self.stocktake.invoke(action, payload)
        if action in PrintingFacade.ACTIONS:
            return self.printing.invoke(action, payload)
        return self.settings.invoke(action, payload)


class DesktopApplication:
    """建立並執行 POS 的單一 pywebview 桌面視窗。"""

    def __init__(self, paths, bridge=None, webview_module=None):
        self.paths = paths
        if bridge is None:
            logger = logging.getLogger(f"pos.desktop.{id(self)}")
            logger.setLevel(logging.ERROR)
            logger.propagate = False
            logger.addHandler(logging.FileHandler(paths.error_log_path, encoding="utf-8"))
            bridge = DesktopBridge(logger=logger, facade=DesktopFacade(paths.db_path))
        self.bridge = bridge
        self._webview_module = webview_module

    def _webview(self):
        if self._webview_module is None:
            import webview
            self._webview_module = webview
        return self._webview_module

    def run(self):
        entry_point = self.paths.static_dir / "index.html"
        if not entry_point.is_file():
            raise FileNotFoundError(f"找不到桌面前端入口：{entry_point}")

        webview = self._webview()
        window = webview.create_window(
            "POS",
            entry_point.resolve().as_uri(),
            js_api=self.bridge,
        )
        self.bridge._set_window(window, getattr(webview, "SAVE_DIALOG", "save"))
        webview.start(gui="edgechromium")
        return window
