import ast
import json
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from lib.application_errors import ValidationError
from lib.desktop_bridge import DesktopBridge
from lib.product_rules import check_field_type
from lib.printing_service import PrintingFacade


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


class _LiteralResourceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        self.urls.extend(value for name, value in attrs if name in {"src", "href"})


def _literal_resource_urls(html):
    parser = _LiteralResourceParser()
    parser.feed(html)
    parser.close()
    return parser.urls


class StaticDesktopContractTests(unittest.TestCase):
    def _assert_local_resources_valid(self, html):
        resources = _literal_resource_urls(html)
        for value in resources:
            self.assertIsInstance(value, str)
        local = [value for value in resources if not urlparse(value).scheme]
        self.assertTrue(local)
        versions = set()
        for value in local:
            self.assertFalse(value.startswith("/"), value)
            path, _, query = value.partition("?")
            self.assertTrue((STATIC / path).is_file(), value)
            self.assertRegex(query, r"^v=\d+$", value)
            versions.add(query)
        self.assertEqual(len(versions), 1, versions)

    def test_index_local_resources_are_relative_and_share_one_version(self):
        self._assert_local_resources_valid((STATIC / "index.html").read_text(encoding="utf-8"))

    def test_resource_urls_collect_literal_attributes_and_exclude_vue_bindings(self):
        html = '''<img :src="q.previewUrl" v-bind:src="otherPreview">
            <script src="js/api.js?v=200"></script>
            <link href='css/pos.css?v=200'>
            <img src="images/example.png?v=200" data-src="not-a-resource">'''
        self.assertEqual(_literal_resource_urls(html), [
            "js/api.js?v=200", "css/pos.css?v=200", "images/example.png?v=200"])

    def test_resource_validation_still_rejects_invalid_literal_resources(self):
        self._assert_local_resources_valid('''<script src="js/api.js?v=200"></script>
            <link href='css/pos.css?v=200'><img :src="q.previewUrl" v-bind:src="preview">''')
        for html in (
            '<script src="js/does-not-exist.js?v=200"></script>',
            '<link href="css/does-not-exist.css?v=200">',
            '<script src="/js/api.js?v=200"></script>',
            '<script src="js/api.js"></script>',
            '<script src="js/api.js?v=200"></script><link href="css/pos.css?v=201">',
        ):
            with self.subTest(html=html):
                with self.assertRaises(AssertionError):
                    self._assert_local_resources_valid(html)

    def test_formal_javascript_contains_no_network_transport_or_api_urls(self):
        forbidden = ("/api", "fetch(", "XMLHttpRequest", "axios", "window.open(", "API._do", "_waitForBridge")
        for path in (STATIC / "js").glob("*.js"):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"{path.name}: {token}")

    def test_main_import_graph_has_no_retired_web_framework(self):
        seen = set()
        pending = [ROOT / "main.py"]
        while pending:
            path = pending.pop()
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    retired = "fast" + "api"
                    self.assertFalse(name == retired or name.startswith(retired + "."), (path, name))
                    candidate = ROOT.joinpath(*name.split(".")).with_suffix(".py")
                    if candidate.is_file():
                        pending.append(candidate)


class FrameworkNeutralRulesTests(unittest.TestCase):
    def test_invalid_field_type_raises_application_validation_error(self):
        with self.assertRaises(ValidationError):
            check_field_type("invalid")


class PrintingContractTests(unittest.TestCase):
    def test_printing_action_rejects_invalid_payload_at_the_bridge(self):
        result = DesktopBridge(facade=PrintingFacade("unused.db")).invoke(
            "printing.barcode", {"variant_id": 7, "barcode": "STORE-7", "copies": 0})
        self.assertEqual(result, {"ok": False, "error": {
            "code": "validation_error", "message": "張數必須是正整數"}})


class JavascriptRuntimeContractTests(unittest.TestCase):
    def test_named_methods_use_bridge_and_fail_closed(self):
        script = r'''
const fs = require("fs"), vm = require("vm");
const calls = [];
const context = {
  window: { pywebview: { api: { invoke: async (action, payload) => {
    calls.push({action, payload});
    if (action === "barcodes.scan") return {ok:false,error:{code:"not_found",message:"查無條碼"}};
    return {ok:true,data:{value:1}};
  }}}},
  fetch: () => { throw new Error("network called"); },
  setTimeout, clearTimeout, console
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
(async () => {
  await vm.runInContext('API.listCategories({all:1})', context);
  await vm.runInContext('API.createProduct({name:"A"})', context);
  await vm.runInContext('API.receiveStock({variant_id:4,qty:2})', context);
  await vm.runInContext('API.listStocktakes()', context);
  await vm.runInContext('API.checkout({payment:"現金",items:[]})', context);
  await vm.runInContext('API.printBarcode(9)', context);
  let status = null;
  try { await vm.runInContext('API.scanBarcode("X")', context); } catch (e) { status = e.status; }
  let unknown = null;
  try { await vm.runInContext('API.invoke("unknown.action", {})', context); } catch (e) { unknown = e.message; }
  process.stdout.write(JSON.stringify({calls,status,unknown}));
})().catch(e => { console.error(e); process.exit(1); });
'''
        result = subprocess.run(
            ["node", "-e", script, str(STATIC / "js" / "api.js")],
            cwd=ROOT, text=True, capture_output=True, check=True, encoding="utf-8")
        data = json.loads(result.stdout)
        self.assertEqual([c["action"] for c in data["calls"]], [
            "categories.list", "products.create", "stock.receive",
            "stocktake.list", "sales.checkout", "printing.barcode", "barcodes.scan"])
        self.assertEqual(data["status"], 404)
        self.assertIn("不支援", data["unknown"])

    def test_stocktake_scan_ui_sends_default_quantity_one(self):
        script = r'''
const fs = require("fs"), vm = require("vm");
let scanned = null;
const context = {
  window: {PosPages: {}},
  API: {
    barcodeQuery: async code => ({code, data: {variant_id: 8}}),
    stocktakeScan: async payload => { scanned = payload; },
    stocktakeDetail: async () => ({items: []})
  }
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const page = context.window.PosPages["page-stocktake"];
const state = {
  current: 3, scanCode: "ABC", detail: null,
  guard: async operation => operation()
};
(async () => {
  await page.methods.onScan.call(state);
  process.stdout.write(JSON.stringify(scanned));
})().catch(e => { console.error(e); process.exit(1); });
'''
        result = subprocess.run(
            ["node", "-e", script, str(STATIC / "js" / "stocktake.js")],
            cwd=ROOT, text=True, capture_output=True, check=True, encoding="utf-8")
        self.assertEqual(json.loads(result.stdout), {
            "session_id": 3, "variant_id": 8, "qty": 1})

    def test_labels_page_uses_guard_catalog_and_printable_barcode_defaults(self):
        script = r'''
const fs = require("fs"), vm = require("vm");
let guardCalls = 0, guardDepth = 0;
const calls = [], errors = [];
const record = (action, payload) => calls.push({action, payload, guarded: guardDepth > 0});
// catalog.list 才帶 barcodes；products.list 沒有，改用它會讓可列印款式消失。
const catalog = [{name: "鋼化玻璃", variants: [
  {variant_id: 7, price: null, models: ["iPhone 16"],
   barcodes: [{source: "factory", barcode: "F"}, {source: "store", barcode: "S"}]},
  {variant_id: 8, price: 590, models: [], barcodes: [{source: "factory", barcode: "FACTORY-ONLY"}]},
  {variant_id: 9, price: null, models: [], barcodes: []}]}];
const label = {name: "鋼化玻璃", specification: "亮面", price: "", models: ["iPhone 16"]};
const context = {
  window: {PosPages: {}}, Vue: {reactive: value => value},
  API: {
    scanBarcode: async code => {
      record("barcodes.scan", code);
      throw Object.assign(new Error("查無條碼"), {status: 404});
    },
    listCatalog: async payload => { record("catalog.list", payload); return catalog; },
    checkLabels: async items => {
      record("printing.check", items);
      return {items: items.map(item => ({...item, valid: true, label}))};
    },
    previewLabel: async (variant_id, barcode) => {
      record("printing.preview", {variant_id, barcode});
      return {image: "data:image/png;base64,preview"};
    },
  },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const page = context.window.PosPages["page-labels"];
const state = {
  ...page.data(), ...page.methods, query: "玻璃", attrText: () => "亮面",
  showError: message => errors.push(message),
  guard: async op => {
    guardCalls++; guardDepth++;
    try { return await op(); }
    catch (error) { errors.push(error.message); }
    finally { guardDepth--; }
  },
};
(async () => {
  await state.submitInput();
  const rows = state.results;
  const formatted = rows.map(row => state.rowText(row));
  await state.pick(rows[0]);
  context.API.listCatalog = async payload => {
    record("catalog.list", payload); throw new Error("catalog failed");
  };
  state.query = "失敗";
  await state.submitInput();
  process.stdout.write(JSON.stringify({
    guardCalls, calls, errors,
    name: rows[0].name, variant_id: rows[0].variant_id,
    variant_ids: rows.map(row => row.variant_id),
    barcode: state.defaultBarcode(rows[0]),
    factoryBarcode: state.defaultBarcode(rows[1]),
    nullPrice: state.priceText({price:null}), price: state.priceText({price:590}),
    formatted, queue: state.q.rows, preview: state.q.previewUrl,
  }));
})().catch(e => { console.error(e); process.exit(1); });
'''
        result = subprocess.run(
            ["node", "-e", script, str(STATIC / "js" / "labels.js")],
            cwd=ROOT, text=True, capture_output=True, check=True, encoding="utf-8")
        data = json.loads(result.stdout)
        self.assertEqual(data["guardCalls"], 4)
        self.assertEqual(data["calls"], [
            {"action": "barcodes.scan", "payload": "玻璃", "guarded": True},
            {"action": "catalog.list", "payload": {"q": "玻璃"}, "guarded": True},
            {"action": "printing.check", "payload": [{"variant_id": 7, "barcode": "S"}], "guarded": True},
            {"action": "printing.preview", "payload": {"variant_id": 7, "barcode": "S"}, "guarded": True},
            {"action": "barcodes.scan", "payload": "失敗", "guarded": True},
            {"action": "catalog.list", "payload": {"q": "失敗"}, "guarded": True},
        ])
        self.assertEqual(data["errors"], ["catalog failed"])
        self.assertEqual(data["name"], "鋼化玻璃")
        self.assertEqual(data["variant_id"], 7)
        self.assertEqual(data["variant_ids"], [7, 8])
        self.assertEqual(data["barcode"], "S")
        self.assertEqual(data["factoryBarcode"], "FACTORY-ONLY")
        self.assertEqual(data["nullPrice"], "")
        self.assertEqual(data["price"], "$590")
        self.assertEqual(data["formatted"], ["鋼化玻璃｜亮面｜iPhone 16｜S", "鋼化玻璃｜亮面｜$590｜FACTORY-ONLY"])
        self.assertEqual(data["queue"], [{
            "key": "7|S", "variant_id": 7, "barcode": "S", "copies": 1, "status": "ready",
            "label": {"name": "鋼化玻璃", "specification": "亮面", "price": "", "models": ["iPhone 16"]},
        }])
        self.assertEqual(data["preview"], "data:image/png;base64,preview")


if __name__ == "__main__":
    unittest.main()
