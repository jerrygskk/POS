import ast
import json
import subprocess
import unittest
from pathlib import Path
from urllib.parse import urlparse

from lib.application_errors import ValidationError
from lib.desktop_bridge import DesktopBridge
from lib.product_rules import check_field_type
from lib.printing_service import PrintingFacade


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


class StaticDesktopContractTests(unittest.TestCase):
    def test_index_local_resources_are_relative_and_share_one_version(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        import re
        resources = re.findall(r'(?:src|href)="([^"]+)"', html)
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

    def test_formal_javascript_contains_no_network_transport_or_api_urls(self):
        forbidden = ("/api", "fetch(", "XMLHttpRequest", "axios", "window.open", "API._do", "_waitForBridge")
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
            "printing.barcode", {"variant_id": 7, "copies": 0})
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

    def test_labels_page_uses_guard_and_only_displays_store_barcode(self):
        script = r'''
const fs = require("fs"), vm = require("vm");
let guarded = false;
// catalog.list 才帶 barcodes；products.list 沒有,用它會讓店內條碼全顯示成「尚無」。
const catalog = [{name: "鋼化玻璃", variants: [
  {variant_id: 7, price: null, models: ["iPhone 16"],
   barcodes: [{source: "factory", barcode: "F"}, {source: "store", barcode: "S"}]}]}];
const context = { window: {PosPages: {}}, API: { listCatalog: async () => catalog } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const page = context.window.PosPages["page-labels"];
const state = { query: "玻璃", results: [], guard: async op => { guarded = true; return op(); },
                attrText: () => "亮面" };
state.storeBarcodes = page.methods.storeBarcodes;
state.priceText = page.methods.priceText;
(async () => {
  await page.methods.search.call(state);
  process.stdout.write(JSON.stringify({
    guarded,
    name: state.results[0].name,
    variant_id: state.results[0].variant_id,
    barcode: page.methods.storeBarcodes({barcodes:[{source:"factory",barcode:"F"},{source:"store",barcode:"S"}]}),
    nullPrice: page.methods.priceText({price:null}),
    price: page.methods.priceText({price:590}),
    row: page.methods.rowText.call(state, state.results[0])
  }));
})().catch(e => { console.error(e); process.exit(1); });
'''
        result = subprocess.run(
            ["node", "-e", script, str(STATIC / "js" / "labels.js")],
            cwd=ROOT, text=True, capture_output=True, check=True, encoding="utf-8")
        self.assertEqual(json.loads(result.stdout), {
            "guarded": True, "name": "鋼化玻璃", "variant_id": 7, "barcode": "S",
            "nullPrice": "", "price": "$590",
            "row": "鋼化玻璃｜亮面｜iPhone 16｜S"})


if __name__ == "__main__":
    unittest.main()
