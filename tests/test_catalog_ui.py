"""商品資料庫頁前端狀態與模板契約測試。"""

import json
import shutil
import subprocess
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


@unittest.skipUnless(
    shutil.which("node"), "Node.js is required for catalog UI runtime tests"
)
class CatalogUiRuntimeTests(unittest.TestCase):
    def _run(self, body):
        script = r'''
const fs = require("fs"), vm = require("vm");
const context = {window: {PosPages: {}, PosDesktopLock: {lock(){}, unlock(){}}},
  API: {}, console, setTimeout, clearTimeout};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const window = context.window;
const page = window.PosPages["page-catalog"];

function mkState(extra) {
  const errors = [];
  const s = {
    showError: message => errors.push(message),
    goPage: () => {},
    guard: async operation => {
      try { return await operation(); }
      catch (error) { errors.push(error.message); }
    },
  };
  Object.assign(s, page.data.call(s));
  for (const [name, method] of Object.entries(page.methods)) s[name] = method.bind(s);
  for (const [name, getter] of Object.entries(page.computed || {}))
    Object.defineProperty(s, name, {get: getter.bind(s), configurable: true});
  Object.assign(s, extra || {});
  s._errors = errors;
  return s;
}
const API = context.API;
const out = {};
function done() { process.stdout.write(JSON.stringify(out)); }
BODY
'''.replace("BODY", body)
        result = subprocess.run(
            ["node", "-e", script, str(STATIC / "js" / "catalog.js")],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8",
        )
        if result.returncode != 0:
            self.fail(result.stderr)
        return json.loads(result.stdout)

    def test_display_count_sums_variants_and_keyword_filters_empty_groups(self):
        out = self._run(r'''
const products = [
  {product_id:1, active:true, variants:[]},
  {product_id:2, active:false, variants:[]},
  {product_id:3, active:true, variants:[{variant_id:31},{variant_id:32}]},
];
        let s = mkState({products, q:""});
        out.count = s.displayVariantCount;
        out.noKeyword = s.displayProducts.map(p => p.product_id);
        s.q = "玻璃";
        out.typedOnly = s.displayProducts.map(p => p.product_id);
        s = mkState({products, q:"玻璃", appliedQ:"玻璃"});
        out.keyword = s.displayProducts.map(p => p.product_id);
        done();
''')
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["noKeyword"], [1, 3])
        self.assertEqual(out["typedOnly"], [1, 3])
        self.assertEqual(out["keyword"], [3])

    def test_group_rowspan_is_independent_of_editor_state(self):
        out = self._run(r'''
const variants = [
  {variant_id:1, models:["17 Pro"]},
  {variant_id:2, models:["17 Pro"]},
  {variant_id:3, models:["17 Pro"]},
];
for (const id of [1, 2, 3]) {
  out[id] = window.groupVariantsByModel(variants, {"17 Pro":0}, id)
    .map(row => [row.v.variant_id, row.showModel, row.rowspan]);
}
done();
''')
        expected = [[1, True, 3], [2, False, 0], [3, False, 0]]
        self.assertEqual(out["1"], expected)
        self.assertEqual(out["2"], expected)
        self.assertEqual(out["3"], expected)

    def test_open_editor_locks_before_bridge_and_unlocks_only_on_failure(self):
        out = self._run(r'''
const order = [];
window.PosDesktopLock = {
  lock: () => order.push("lock"), unlock: () => order.push("unlock")};
API.invoke = async (action, payload) => {
  order.push(action);
  out.payload = payload;
  if (payload.context.variant.variant_id === 9) throw new Error("開啟失敗");
};
const s = mkState();
(async () => {
  await s.openVariantEditor({product_id:1}, {variant_id:2});
  out.successOrder = order.slice();
  await s.openVariantEditor({product_id:1}, {variant_id:9});
  out.failureOrder = order.slice();
  out.errors = s._errors;
  done();
})();
''')
        self.assertEqual(out["successOrder"], [
            "lock", "desktop.child_window.open"])
        self.assertEqual(out["failureOrder"], [
            "lock", "desktop.child_window.open",
            "lock", "desktop.child_window.open", "unlock"])
        self.assertEqual(out["payload"], {
            "page": "variant_editor",
            "context": {"product": {"product_id": 1}, "variant": {"variant_id": 9}}})
        self.assertEqual(out["errors"], ["開啟失敗"])

    def test_add_variant_opens_batch_child_window_with_product_context(self):
        out = self._run(r'''
const payloads = [];
API.invoke = async (action, payload) => { payloads.push([action, payload]); };
const s = mkState();
(async () => {
  await s.openAddVariant({product_id: 5, category_id: 3});
  await s.openAddVariant(null);
  out.payloads = payloads;
  done();
})();
''')
        self.assertEqual(out["payloads"], [
            ["desktop.child_window.open",
             {"page": "variant_batch",
              "context": {"category_id": 3, "product_id": 5}}],
            ["desktop.child_window.open",
             {"page": "variant_batch", "context": {}}],
        ])

    def test_child_window_close_refreshes_only_after_successful_save(self):
        out = self._run(r'''
let reloads = 0;
const s = mkState({reload: async () => { reloads++; }});
(async () => {
  await s.onChildWindowClosed({detail:{saved:false}});
  out.afterCancel = reloads;
  await s.onChildWindowClosed({detail:{saved:true}});
  out.afterSave = reloads;
  done();
})();
''')
        self.assertEqual(out, {"afterCancel": 0, "afterSave": 1})

    def test_desktop_lock_uses_native_inert_without_visible_ui(self):
        script = r'''
const fs = require("fs"), vm = require("vm");
const listeners = {}, removed = [], registrations = [], scrollCalls = [];
const root = {inert:false};
const add = (name, fn, options) => {
  listeners[name] = fn; registrations.push([name, options || null]);
};
const context = {
  window: {
    scrollX:12, scrollY:34,
    addEventListener:add,
    removeEventListener:(name, fn, options) => removed.push([name, options || null]),
    scrollTo:(x, y) => { scrollCalls.push([x, y]); },
  },
  document: {
    querySelector: sel => sel === "#app" ? root : null,
    addEventListener: () => {},
  },
  Vue: {}, console, setTimeout, clearTimeout,
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
context.window.PosDesktopLock.lock();
const locked = root.inert;
const wheel = {prevented:false, preventDefault(){ this.prevented = true; }};
listeners.wheel(wheel);
const pageDown = {key:"PageDown", prevented:false,
  preventDefault(){ this.prevented = true; }};
listeners.keydown(pageDown);
const letter = {key:"a", prevented:false, preventDefault(){ this.prevented = true; }};
listeners.keydown(letter);
context.window.scrollX = 200; context.window.scrollY = 300;
listeners.scroll();
listeners["pos-child-window-closed"]({detail:{saved:false}});
process.stdout.write(JSON.stringify({
  locked, unlocked:!root.inert, wheelPrevented:wheel.prevented,
  pageDownPrevented:pageDown.prevented, letterPrevented:letter.prevented,
  registrations, removed, scrollCalls,
}));
'''
        result = subprocess.run(
            ["node", "-e", script, str(STATIC / "js" / "app.js")],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8",
        )
        if result.returncode != 0:
            self.fail(result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["locked"])
        self.assertTrue(data["unlocked"])
        self.assertTrue(data["wheelPrevented"])
        self.assertTrue(data["pageDownPrevented"])
        self.assertFalse(data["letterPrevented"])
        self.assertIn(["wheel", {"capture": True, "passive": False}],
                      data["registrations"])
        self.assertIn(["touchmove", {"capture": True, "passive": False}],
                      data["registrations"])
        self.assertIn(["keydown", {"capture": True}], data["registrations"])
        self.assertIn(["scroll", {"capture": True}], data["registrations"])
        self.assertEqual({item[0] for item in data["removed"]},
                         {"wheel", "touchmove", "keydown", "scroll"})
        self.assertGreaterEqual(data["scrollCalls"].count([12, 34]), 2)
        source = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("overlay", source.lower())

    def test_api_allows_desktop_child_window_action(self):
        api_script = r'''
const fs = require("fs"), vm = require("vm");
const calls = [];
const context = {window:{pywebview:{api:{invoke:async (action, payload) => {
  calls.push([action, payload]); return {ok:true,data:{}};
}}}}, console, setTimeout, clearTimeout};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
vm.runInContext('API.invoke("desktop.child_window.open", {page:"variant_editor"})', context)
  .then(() => process.stdout.write(JSON.stringify(calls)))
  .catch(error => { console.error(error.message); process.exit(1); });
'''
        result = subprocess.run(
            ["node", "-e", api_script, str(STATIC / "js" / "api.js")],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8",
        )
        if result.returncode != 0:
            self.fail(result.stderr)
        self.assertEqual(json.loads(result.stdout), [[
            "desktop.child_window.open", {"page": "variant_editor"}]])

    def test_refresh_uses_all_filters_for_inactive_lookup_and_counts_variants(self):
        out = self._run(r'''
const calls = [];
API.listCatalog = async params => {
  calls.push({...params});
  if (!params.include_inactive) return [{product_id:1, active:true, variants:[]}];
  return [
    {product_id:1, variants:[{variant_id:1}]},
    {product_id:2, variants:[{variant_id:2},{variant_id:3}]},
  ];
};
API.variantIssues = async () => ({pending_variant_count:7});
const s = mkState({q:"  玻璃  ", fCategory:2, fBrand:3, fModel:4, pending:true});
(async () => {
  await s.refresh();
  out.calls = calls;
  out.products = s.products.map(p => p.product_id);
  out.pendingCount = s.pendingCount;
  out.appliedQ = s.appliedQ ?? null;
  out.inactiveMatchCount = s.inactiveMatchCount;
  out.lookupFailed = s.inactiveLookupFailed;
  done();
})();
''')
        self.assertEqual(out["calls"], [
            {"q": "  玻璃  ", "include_inactive": False, "category_id": 2,
             "brand_id": 3, "model_id": 4, "pending": True},
            {"q": "  玻璃  ", "include_inactive": True, "category_id": 2,
             "brand_id": 3, "model_id": 4, "pending": True},
        ])
        self.assertEqual(out["products"], [1])
        self.assertEqual(out["pendingCount"], 7)
        self.assertEqual(out["appliedQ"], "  玻璃  ")
        self.assertEqual(out["inactiveMatchCount"], 3)
        self.assertFalse(out["lookupFailed"])

    def test_refresh_also_looks_up_inactive_data_without_any_filter(self):
        out = self._run(r'''
let calls = 0;
API.listCatalog = async params => {
  calls++;
  return params.include_inactive ? [{variants:[{variant_id:8}]}] : [];
};
API.variantIssues = async () => ({});
const s = mkState();
(async () => {
  await s.refresh();
  out.calls = calls;
  out.inactiveMatchCount = s.inactiveMatchCount;
  done();
})();
''')
        self.assertEqual(out, {"calls": 2, "inactiveMatchCount": 1})

    def test_include_inactive_skips_supplementary_lookup(self):
        out = self._run(r'''
let calls = 0;
API.listCatalog = async () => { calls++; return []; };
API.variantIssues = async () => ({});
const s = mkState({includeInactive:true});
(async () => {
  await s.refresh();
  out.calls = calls;
  out.inactiveMatchCount = s.inactiveMatchCount;
  done();
})();
''')
        self.assertEqual(out["calls"], 1)
        self.assertIsNone(out["inactiveMatchCount"])

    def test_show_inactive_action_checks_filter_and_reloads(self):
        out = self._run(r'''
let reloads = 0;
const s = mkState({reload: async () => { reloads++; }});
(async () => {
  await s.showInactiveResults();
  out.includeInactive = s.includeInactive;
  out.reloads = reloads;
  done();
})();
''')
        self.assertEqual(out, {"includeInactive": True, "reloads": 1})

    def test_inactive_lookup_failure_keeps_first_result_and_reports_error(self):
        out = self._run(r'''
API.listCatalog = async params => {
  if (params.include_inactive) throw new Error("補查失敗");
  return [{product_id:5, active:true, variants:[]}];
};
API.variantIssues = async () => ({pending_variant_count:2});
const s = mkState();
(async () => {
  await s.refresh();
  out.productIds = s.products.map(p => p.product_id);
  out.pendingCount = s.pendingCount;
  out.inactiveMatchCount = s.inactiveMatchCount;
  out.lookupFailed = s.inactiveLookupFailed;
  out.errors = s._errors;
  done();
})();
''')
        self.assertEqual(out["productIds"], [5])
        self.assertEqual(out["pendingCount"], 2)
        self.assertIsNone(out["inactiveMatchCount"])
        self.assertTrue(out["lookupFailed"])
        self.assertEqual(out["errors"], ["補查失敗"])

    def test_latest_refresh_wins_for_products_pending_count_and_empty_state(self):
        out = self._run(r'''
let resolveOldCatalog, resolveOldIssues;
const oldCatalog = new Promise(resolve => { resolveOldCatalog = resolve; });
const oldIssues = new Promise(resolve => { resolveOldIssues = resolve; });
API.listCatalog = params => {
  if (params.q === "舊") return oldCatalog;
  if (params.q === "失敗") return Promise.reject(new Error("主查詢失敗"));
  return Promise.resolve([{product_id:2, variants:[{variant_id:2}]}]);
};
API.variantIssues = () => s.q === "舊" ? oldIssues : Promise.resolve({pending_variant_count:22});
const s = mkState({q:"舊"});
(async () => {
  const oldRefresh = s.refresh();
  s.q = "新";
  await s.refresh();
  resolveOldCatalog([{product_id:1, variants:[]}]);
  resolveOldIssues({pending_variant_count:11});
  await oldRefresh;
  s.q = "失敗";
  await s.refresh();
  out.productIds = s.products.map(p => p.product_id);
  out.pendingCount = s.pendingCount;
  out.appliedQ = s.appliedQ ?? null;
  out.inactiveMatchCount = s.inactiveMatchCount;
  out.lookupFailed = s.inactiveLookupFailed;
  done();
})();
''')
        self.assertEqual(out["productIds"], [2])
        self.assertEqual(out["pendingCount"], 22)
        self.assertEqual(out["appliedQ"], "新")
        self.assertIsNone(out["inactiveMatchCount"])
        self.assertFalse(out["lookupFailed"])

    def test_stale_supplementary_lookup_cannot_overwrite_new_refresh(self):
        out = self._run(r'''
let resolveOldInactive;
const oldInactive = new Promise(resolve => { resolveOldInactive = resolve; });
API.listCatalog = params => {
  if (params.q === "舊" && !params.include_inactive) return Promise.resolve([]);
  if (params.q === "舊") return oldInactive;
  return Promise.resolve([{product_id:2, variants:[{variant_id:2}]}]);
};
API.variantIssues = async () => ({pending_variant_count:3});
const s = mkState({q:"舊"});
(async () => {
  const oldRefresh = s.refresh();
  await new Promise(resolve => setTimeout(resolve, 0));
  s.q = "新";
  await s.refresh();
  resolveOldInactive([{variants:[{variant_id:8},{variant_id:9}]}]);
  await oldRefresh;
  out.productIds = s.products.map(p => p.product_id);
  out.inactiveMatchCount = s.inactiveMatchCount;
  done();
})();
''')
        self.assertEqual(out["productIds"], [2])
        self.assertIsNone(out["inactiveMatchCount"])


class CatalogTemplateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        cls.html = html
        cls.css = (STATIC / "css" / "pos.css").read_text(encoding="utf-8")
        cls.catalog_js = (STATIC / "js" / "catalog.js").read_text(
            encoding="utf-8")
        cls.catalog = html.split('<template id="tpl-catalog">', 1)[1].split(
            '<template id="tpl-settings">', 1)[0]

    def test_catalog_is_one_table_without_expand_mechanism(self):
        self.assertEqual(self.catalog.count("<table"), 1)
        self.assertNotIn("expanded", self.catalog)
        self.assertNotIn("toggleExpand", self.catalog)
        self.assertIn('class="catalog-product-row"', self.catalog)
        self.assertNotIn("editVariant", self.catalog)
        self.assertNotIn("saveVariant", self.catalog)
        self.assertIn('@click="openVariantEditor(p, v)"', self.catalog)

    def test_product_header_and_variant_columns_are_separate(self):
        self.assertIn('<div class="scan-row catalog-filter-row">', self.catalog)
        self.assertIn('<div class="catalog-table-scroll">', self.catalog)
        self.assertIn('<td colspan="6" class="catalog-product-info">', self.catalog)
        self.assertIn('<td colspan="2" class="catalog-product-actions">', self.catalog)
        self.assertIn('<col class="catalog-col-model">', self.catalog)
        self.assertIn('<col class="catalog-col-spec">', self.catalog)
        self.assertIn('class="catalog-variant-row"', self.catalog)
        self.assertIn(
            '<button class="btn-sm" @click="editInSettings">修改規格</button>',
            self.catalog,
        )
        expected = (
            "<th>型號</th><th>規格</th><th>庫存</th><th>條碼</th>\n"
            "      <th>自身售價</th><th>有效售價</th><th>狀態</th><th>操作</th>"
        )
        self.assertIn(expected, self.catalog)
        self.assertIn(".catalog-table-scroll { overflow-x: auto;", self.css)
        self.assertIn(
            ".catalog-filter-row { max-width: 100%; overflow-x: auto; flex-wrap: nowrap; }",
            self.css,
        )
        self.assertIn(".catalog-table { min-width: 1240px;", self.css)
        self.assertIn(".catalog-table .catalog-variant-row > td { vertical-align: top; }", self.css)
        self.assertIn(".catalog-table .catalog-product-row > td { vertical-align: middle;", self.css)
        self.assertIn(".catalog-col-model { width: 220px; }", self.css)
        self.assertIn(".catalog-col-spec { width: auto; }", self.css)
        # 型號格一行一個型號:每個型號自己一個 div,分隔號留行尾,單一型號不折行
        self.assertIn('class="catalog-model-line"', self.catalog)
        self.assertIn('v-for="(m, mi) in modelLines(p, v)"', self.catalog)
        self.assertIn("mi === modelLines(p, v).length - 1 ? '' : '、'", self.catalog)
        self.assertNotIn("v.models.join('、')", self.catalog)
        # 型號顯示依種類設定:不使用適用型號的種類一律「—」(資料不動)
        self.assertIn('return this.usesModel(p) ? (v.models || []) : [];', self.catalog_js)
        self.assertIn('c.model_mode === "required"', self.catalog_js)
        self.assertIn(
            ".catalog-model-cell .catalog-model-line"
            " { white-space: nowrap; line-height: 1.45; }",
            self.css,
        )
        self.assertNotIn("table.sub th:nth-child(1)", self.css)
        self.assertIn(".catalog-product-actions { text-align: right; white-space: nowrap; }", self.css)
        self.assertIn('placeholder="搜尋名稱、規格或條碼"', self.catalog)
        self.assertIn('class="catalog-search-input"', self.catalog)
        self.assertIn('v-model="q" @keyup.enter="reload"', self.catalog)
        self.assertIn('<button class="primary" @click="reload">', self.catalog)
        self.assertIn(
            ".catalog-search-input { flex: 1 0 320px; min-width: 320px; }",
            self.css)
        self.assertNotIn("至設定修改", self.catalog)

        info = self.catalog.split(
            '<td colspan="6" class="catalog-product-info">', 1)[1].split(
                "</td>", 1)[0]
        self.assertIn("{{ p.name }}", info)
        self.assertIn('v-if="!p.active"', info)
        self.assertIn("已停用", info)
        self.assertNotIn("種類", info)
        self.assertNotIn("廠牌", info)
        self.assertNotIn("p.active ?", info)

    def test_add_variant_entry_is_only_for_active_products_and_uses_ui_term(self):
        # 新增款式一律開子視窗:行內快速新增表單已移除,列上只留入口按鈕
        self.assertIn('<tr v-if="p.active">', self.catalog)
        self.assertIn('@click="openAddVariant(p)"', self.catalog)
        self.assertIn('@click="openAddVariant(null)"', self.catalog)
        self.assertNotIn("addingFor", self.catalog)
        self.assertNotIn("newVariant", self.catalog)
        self.assertIn("新增款式", self.catalog)
        self.assertNotIn("新增子產品", self.catalog)

    def test_catalog_page_visible_copy_does_not_use_internal_product_terms(self):
        for term in ("大產品", "子產品"):
            self.assertNotIn(term, self.catalog)
        for copy in ("與其他子產品重複", "確定刪除此子產品"):
            self.assertNotIn(copy, self.catalog_js)

    def test_empty_result_messages_and_show_inactive_button_match_plan(self):
        self.assertIn("查無啟用中的商品；另有 {{ inactiveMatchCount }} 筆已停用的商品資料符合條件。", self.catalog)
        self.assertIn('>顯示已停用</button>', self.catalog)
        self.assertIn("查無符合的商品，請確認關鍵字或至設定新增。", self.catalog)
        self.assertIn("inactiveLookupFailed", self.catalog)

    def test_catalog_uses_display_products_and_bumped_resource_version(self):
        self.assertIn('v-for="p in displayProducts"', self.catalog)
        # 資源版號會持續往上 bump,測固定數字每次都要改;改測「只有一個版號、且不低於 74」
        versions = set(re.findall(r"\?v=(\d+)", self.html))
        self.assertEqual(len(versions), 1, f"index.html 版號不一致:{versions}")
        self.assertGreaterEqual(int(versions.pop()), 74)


if __name__ == "__main__":
    unittest.main()
