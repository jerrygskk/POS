"""新增款式工作表前端邏輯的 Node 測試。"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class VariantBatchUiTests(unittest.TestCase):
    def _run(self, body):
        script = r'''
const fs = require("fs"), vm = require("vm");
const context = {
  window: { pywebview: { api: { invoke: async () => ({ ok: true, data: {} }) } } },
  PosConfirm: { ask: async () => true, notify: async () => true },
  console, setTimeout, clearTimeout,
};
context.window.PosConfirm = context.PosConfirm;
context.window.CatalogFields = {
  usageScope: () => ({}),
  loadFieldUsage: async () => {},
};
context.window.initFormAttrs = (fields, existing) => {
  const attrs = Object.assign({}, existing || {});
  for (const field of (fields || [])) {
    if (!(field.name in attrs)) attrs[field.name] = field.field_type === "multi" ? [] : "";
  }
  return attrs;
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);  // api.js
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);  // variant_batch_logic.js
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);  // variant_batch.js
vm.runInContext("window.__api = API", context);
const window = context.window;
const API = window.__api;
const page = window.PosPages["page-variant-batch"];

function mkState(extra) {
  const s = {
    guard: async fn => fn(), showError: msg => { s._error = msg; },
    goPage: () => {}, markSaved: () => { s._saved = true; },
  };
  for (const key of Object.keys(page.methods)) s[key] = page.methods[key].bind(s);
  Object.assign(s, page.data.call(s));
  Object.assign(s, extra || {});
  for (const key of Object.keys(page.computed || {})) {
    if (!(key in s)) Object.defineProperty(s, key, {
      get: page.computed[key].bind(s), configurable: true,
    });
  }
  return s;
}
const out = {};
function done() { process.stdout.write(JSON.stringify(out)); }
BODY
'''.replace("BODY", body)
        result = subprocess.run(
            ["node", "-e", script, str(STATIC / "js" / "api.js"),
             str(STATIC / "js" / "variant_batch_logic.js"),
             str(STATIC / "js" / "variant_batch.js")],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8")
        if result.returncode != 0:
            self.fail(result.stderr)
        return json.loads(result.stdout)

    def test_expand_axes_formula_and_diff_fields(self):
        out = self._run(r'''
const logic = window.VariantBatchLogic;
const fields = [
  {name:"顏色", field_type:"select"}, {name:"長度", field_type:"select"},
  {name:"特性詞條", field_type:"tags"},
];
const expanded = logic.expandAxes(fields, {"顏色":["黑","白"], "長度":["1m","2m"]});
out.count = expanded.count;
out.formula = logic.formulaText(expanded.axes);
out.emptyCount = logic.expandAxes([{name:"備註",field_type:"text"}], {"備註":"新品"}).count;
const rows = [
  {attrs:{"顏色":"黑","長度":"1m"}, price:100, barcode:"", model_ids:[]},
  {attrs:{"顏色":"白","長度":"1m"}, price:100, barcode:"", model_ids:[]},
];
out.diff = Array.from(logic.diffFieldNames(rows, fields));
done();
''')
        self.assertEqual(out["count"], 4)
        self.assertEqual(out["formula"], "2 個顏色 × 2 個長度＝4 筆")
        self.assertEqual(out["emptyCount"], 1)
        self.assertEqual(out["diff"], ["顏色"])

    def test_partition_precheck_keeps_structured_errors_and_skips_existing(self):
        out = self._run(r'''
const rows = [{draft_id:"d1",attrs:{}}, {draft_id:"d2",attrs:{}}];
const error = {code:"missing_required",field_id:7,message:"必填規格未填"};
const part = window.VariantBatchLogic.partitionPrecheck(rows, [
  {draft_id:"d1",existing_duplicate:true,related_variant_id:9,errors:[]},
  {draft_id:"d2",existing_duplicate:false,errors:[error]},
]);
out.kept = part.kept.map(row => row.draft_id);
out.skipped = part.skipped.map(item => [item.row.draft_id, item.related_variant_id]);
out.errors = part.errorsByDraftId;
done();
''')
        self.assertEqual(out["kept"], ["d2"])
        self.assertEqual(out["skipped"], [["d1", 9]])
        self.assertEqual(out["errors"]["d2"][0]["message"], "必填規格未填")

    def test_precheck_race_old_response_discarded(self):
        out = self._run(r'''
const calls = [];
API.batchPrecheckVariants = () => new Promise(resolve => calls.push(resolve));
const rows = [
  {draft_id:"d1",attrs:{"顏色":"黑"},price:null,model_ids:[],barcode:"",store:false},
  {draft_id:"d2",attrs:{"顏色":"白"},price:null,model_ids:[],barcode:"",store:false},
];
const s = mkState({productId:5,fields:[{field_id:1,name:"顏色",field_type:"select"}],drafts:rows});
(async () => {
  const first = s.runPrecheck();
  s.drafts[1].attrs["顏色"] = "藍";
  const second = s.runPrecheck();
  calls[1]({results:[
    {draft_id:"d1",existing_duplicate:false,errors:[]},
    {draft_id:"d2",existing_duplicate:false,errors:[
      {code:"missing_required",field_id:1,message:"顏色必填"}
    ]},
  ]});
  await second;
  calls[0]({results:[
    {draft_id:"d1",existing_duplicate:true,related_variant_id:99,errors:[]},
    {draft_id:"d2",existing_duplicate:false,errors:[]},
  ]});
  await first;
  out.errors = s.precheckErrors;
  out.skipped = s.skipped.length;
  out.drafts = s.drafts.map(row => row.draft_id);
  done();
})();
''')
        self.assertEqual(out["errors"]["d2"][0]["message"], "顏色必填")
        self.assertEqual(out["skipped"], 0)
        self.assertEqual(out["drafts"], ["d1", "d2"])

    def test_invalidate_precheck_blocks_stale_response(self):
        out = self._run(r'''
let resolveRequest;
API.batchPrecheckVariants = () => new Promise(resolve => { resolveRequest = resolve; });
const originalErrors = {d1:[{code:"old",message:"保留"}]};
const s = mkState({productId:5,fields:[],drafts:[
  {draft_id:"d1",attrs:{},price:null,model_ids:[],barcode:"",store:false},
],skipped:[{row:{draft_id:"gone"},related_variant_id:8}],precheckErrors:originalErrors});
(async () => {
  const pending = s.runPrecheck();
  s.invalidatePrecheck();
  resolveRequest({results:[{draft_id:"d1",existing_duplicate:true,related_variant_id:9,errors:[]}]});
  await pending;
  out.drafts = s.drafts.map(row => row.draft_id);
  out.skipped = s.skipped.map(item => item.row.draft_id);
  out.errors = s.precheckErrors;
  done();
})();
''')
        self.assertEqual(out["drafts"], ["d1"])
        self.assertEqual(out["skipped"], ["gone"])
        self.assertEqual(out["errors"]["d1"][0]["message"], "保留")

    def test_schedule_invalidates_inflight_before_timer_fires(self):
        out = self._run(r'''
let resolveRequest;
API.batchPrecheckVariants = () => new Promise(resolve => { resolveRequest = resolve; });
const originalErrors = {d1:[{code:"old",message:"尚未被覆蓋"}]};
const s = mkState({productId:5,fields:[],drafts:[
  {draft_id:"d1",attrs:{},price:null,model_ids:[],barcode:"",store:false},
],precheckErrors:originalErrors});
(async () => {
  const pending = s.runPrecheck();
  s.schedulePrecheck();
  resolveRequest({results:[{draft_id:"d1",existing_duplicate:false,errors:[]}]});
  await pending;
  clearTimeout(s._precheckTimer);
  out.errors = s.precheckErrors;
  out.drafts = s.drafts.map(row => row.draft_id);
  out.skipped = s.skipped;
  done();
})();
''')
        self.assertEqual(out["errors"]["d1"][0]["message"], "尚未被覆蓋")
        self.assertEqual(out["drafts"], ["d1"])
        self.assertEqual(out["skipped"], [])

    def test_generate_rows_applies_barcode_rules(self):
        out = self._run(r'''
API.batchPrecheckVariants = async (productId, drafts) => ({results:
  drafts.map(row => ({draft_id:row.draft_id,existing_duplicate:false,errors:[]}))});
const fields = [
  {field_id:1,name:"顏色",field_type:"select"},
  {field_id:2,name:"長度",field_type:"select"},
];
(async () => {
  const many = mkState({productId:5,fields});
  many.input.attrs = {"顏色":["黑","白"],"長度":["1m"]};
  many.input.barcode = "F9";
  await many.generatePreview();
  out.many = many.drafts.map(row => row.barcode);
  const one = mkState({productId:5,fields});
  one.input.attrs = {"顏色":["黑"],"長度":[]};
  one.input.barcode = "F9";
  await one.generatePreview();
  out.one = one.drafts.map(row => row.barcode);
  done();
})();
''')
        self.assertEqual(out["many"], ["", ""])
        self.assertEqual(out["one"], ["F9"])

    def test_generate_more_than_thirty_rows_requires_confirmation(self):
        out = self._run(r'''
let prompt = "";
context.PosConfirm.ask = async message => { prompt = message; return false; };
const s = mkState({productId:5,fields:[{field_id:1,name:"顏色",field_type:"select"}]});
s.input.attrs = {"顏色":Array.from({length:31}, (_,i) => "色"+(i+1))};
(async () => {
  await s.generatePreview();
  out.prompt = prompt;
  out.count = s.drafts.length;
  out.generating = s.generating;
  done();
})();
''')
        self.assertEqual(out["prompt"], "將產生 31 筆款式，確定展開？")
        self.assertEqual(out["count"], 0)
        self.assertFalse(out["generating"])

    def test_generate_preview_concurrent_calls_add_rows_only_once_and_release_guard(self):
        out = self._run(r'''
const requests = [];
API.batchPrecheckVariants = (productId, drafts) => new Promise(resolve => {
  requests.push({drafts, resolve});
});
const fields = [{field_id:1,name:"顏色",field_type:"select"}];
const s = mkState({productId:5,fields});
s.input.attrs = {"顏色":["黑","白"]};
(async () => {
  const first = s.generatePreview();
  const second = s.generatePreview();
  for (const request of requests) request.resolve({results:
    request.drafts.map(row => ({draft_id:row.draft_id,existing_duplicate:false,errors:[]}))});
  await Promise.all([first, second]);
  out.requestCount = requests.length;
  out.draftIds = s.drafts.map(row => row.draft_id);
  out.generating = s.generating;
  done();
})();
''')
        self.assertEqual(out["requestCount"], 1)
        self.assertEqual(out["draftIds"], ["d1", "d2"])
        self.assertFalse(out["generating"])

    def test_product_input_waits_for_successful_initialization(self):
        out = self._run(r'''
let releaseFields;
API.categoryFields = () => new Promise(resolve => { releaseFields = resolve; });
const product = {product_id:5, category_id:1};
const loading = mkState({catId:1, productId:5, products:[product]});
(async () => {
  const pending = loading.onProductChange();
  out.pendingReady = loading.productReady === true;
  releaseFields([{field_id:1,name:"顏色",field_type:"select"}]);
  await pending;
  out.completedReady = loading.productReady === true;

  API.categoryFields = async () => { throw new Error("載入失敗"); };
  const failed = mkState({catId:1, productId:5, products:[product]});
  failed.guard = async fn => { try { await fn(); } catch (_) {} };
  await failed.onProductChange();
  out.failedReady = failed.productReady === true;
  done();
})();
''')
        self.assertFalse(out["pendingReady"])
        self.assertTrue(out["completedReady"])
        self.assertFalse(out["failedReady"])

    def test_product_load_old_first_does_not_open_new_product_input(self):
        out = self._run(r'''
const requests = [];
API.categoryFields = () => new Promise(resolve => requests.push(resolve));
const products = [
  {product_id:5, category_id:1}, {product_id:6, category_id:1},
];
const s = mkState({catId:1, productId:5, products});
(async () => {
  const oldLoad = s.onProductChange();
  s.productId = 6;
  const currentLoad = s.onProductChange();
  requests[0]([{field_id:1,name:"舊欄位",field_type:"select"}]);
  await oldLoad;
  out.readyAfterOld = s.productReady;
  out.fieldsAfterOld = s.fields.map(field => field.name);
  requests[1]([{field_id:2,name:"新欄位",field_type:"select"}]);
  await currentLoad;
  out.readyAfterCurrent = s.productReady;
  out.fieldsAfterCurrent = s.fields.map(field => field.name);
  done();
})();
''')
        self.assertFalse(out["readyAfterOld"])
        self.assertEqual(out["fieldsAfterOld"], [])
        self.assertTrue(out["readyAfterCurrent"])
        self.assertEqual(out["fieldsAfterCurrent"], ["新欄位"])

    def test_product_load_old_late_does_not_overwrite_current_input(self):
        out = self._run(r'''
const requests = [];
API.categoryFields = () => new Promise(resolve => requests.push(resolve));
const products = [
  {product_id:5, category_id:1}, {product_id:6, category_id:1},
];
const s = mkState({catId:1, productId:5, products});
(async () => {
  const oldLoad = s.onProductChange();
  s.productId = 6;
  const currentLoad = s.onProductChange();
  requests[1]([{field_id:2,name:"新欄位",field_type:"select"}]);
  await currentLoad;
  s.input.store = true;
  requests[0]([{field_id:1,name:"舊欄位",field_type:"select"}]);
  await oldLoad;
  out.ready = s.productReady;
  out.fields = s.fields.map(field => field.name);
  out.store = s.input.store;
  done();
})();
''')
        self.assertTrue(out["ready"])
        self.assertEqual(out["fields"], ["新欄位"])
        self.assertTrue(out["store"])

    def test_product_load_stale_failure_does_not_replace_current_state(self):
        out = self._run(r'''
const requests = [];
API.categoryFields = () => new Promise((resolve, reject) => requests.push({resolve, reject}));
const products = [
  {product_id:5, category_id:1}, {product_id:6, category_id:1},
];
const s = mkState({catId:1, productId:5, products});
s.guard = async fn => { try { await fn(); } catch (err) { s._error = err.message; } };
(async () => {
  const oldLoad = s.onProductChange();
  s.productId = 6;
  const currentLoad = s.onProductChange();
  requests[1].resolve([{field_id:2,name:"新欄位",field_type:"select"}]);
  await currentLoad;
  requests[0].reject(new Error("舊產品載入失敗"));
  await oldLoad;
  out.error = s._error || null;
  out.ready = s.productReady;
  out.fields = s.fields.map(field => field.name);
  done();
})();
''')
        self.assertIsNone(out["error"])
        self.assertTrue(out["ready"])
        self.assertEqual(out["fields"], ["新欄位"])

    def test_product_load_is_invalidated_by_category_or_null_product(self):
        out = self._run(r'''
const requests = [];
API.categoryFields = () => new Promise(resolve => requests.push(resolve));
const product = {product_id:5, category_id:1};
(async () => {
  const categoryState = mkState({catId:1, productId:5, products:[product]});
  const categoryLoad = categoryState.onProductChange();
  await categoryState.onCategoryChange();
  requests[0]([{field_id:1,name:"舊欄位",field_type:"select"}]);
  await categoryLoad;
  out.categoryReady = categoryState.productReady;
  out.categoryFields = categoryState.fields;

  const nullState = mkState({catId:1, productId:5, products:[product]});
  const productLoad = nullState.onProductChange();
  nullState.productId = null;
  await nullState.onProductChange();
  requests[1]([{field_id:1,name:"舊欄位",field_type:"select"}]);
  await productLoad;
  out.nullReady = nullState.productReady;
  out.nullFields = nullState.fields;
  done();
})();
''')
        self.assertFalse(out["categoryReady"])
        self.assertEqual(out["categoryFields"], [])
        self.assertFalse(out["nullReady"])
        self.assertEqual(out["nullFields"], [])

    def test_duplicate_row_clears_barcode_keeps_store(self):
        out = self._run(r'''
const s = mkState({drafts:[
  {draft_id:"d1",attrs:{"顏色":"黑"},price:100,model_ids:[7],barcode:"F9",store:true},
]});
s.schedulePrecheck = () => {};
s.duplicateRowAt(0);
out.rows = s.drafts;
done();
''')
        self.assertEqual(len(out["rows"]), 2)
        self.assertEqual(out["rows"][1]["barcode"], "")
        self.assertTrue(out["rows"][1]["store"])
        self.assertEqual(out["rows"][1]["attrs"], {"顏色": "黑"})

    def test_commit_maps_structured_details(self):
        out = self._run(r'''
API.batchCreateVariants = async () => {
  const err = new Error("部分資料有誤");
  err.details = [{index:1,draft_id:"d2",errors:[
    {code:"duplicate_signature",field_id:null,related_draft_id:"d1",message:"重複"},
    {code:"missing_required",field_id:7,related_draft_id:null,message:"顏色必填"},
  ]}];
  throw err;
};
const s = mkState({productId:5,fields:[{field_id:7,name:"顏色",field_type:"select"}],drafts:[
  {draft_id:"d1",attrs:{"顏色":"黑"},price:null,model_ids:[],barcode:"",store:false},
  {draft_id:"d2",attrs:{"顏色":"黑"},price:null,model_ids:[],barcode:"",store:false},
]});
(async () => {
  await s.commitAll();
  out.errors = s.precheckErrors;
  out.status = s.rowStatus(s.drafts[1]);
  out.kept = s.drafts.length;
  done();
})();
''')
        self.assertEqual(out["errors"]["d2"][1]["message"], "顏色必填")
        self.assertEqual(out["status"], "與第 1 筆重複")
        self.assertEqual(out["kept"], 2)

    def test_template_is_workbook_without_edit_popup(self):
        html = (STATIC / "variant_batch.html").read_text(encoding="utf-8")
        self.assertIn('class="batch-table"', html)
        self.assertIn('class="batch-fixed-editor"', html)
        self.assertIn('class="batch-skipped"', html)
        self.assertIn('v-if="product && productReady"', html)
        self.assertIn('js/variant_batch_logic.js?v=156', html)
        self.assertNotIn("openEdit", html)
        self.assertNotIn("dialog-overlay", html)

    def test_batch_workspace_has_only_table_as_outer_scroller(self):
        css = (STATIC / "css" / "pos.css").read_text(encoding="utf-8")

        def declarations(selector):
            match = re.search(
                rf"{re.escape(selector)}\s*\{{([^}}]*)\}}",
                css,
                re.DOTALL,
            )
            self.assertIsNotNone(match, f"missing CSS rule for {selector}")
            return {
                name.strip(): value.strip()
                for item in match.group(1).split(";")
                if ":" in item
                for name, value in [item.split(":", 1)]
            }

        outer = declarations(".dialog-content.batch-content")
        table = declarations(".batch-table")

        self.assertEqual(outer.get("overflow"), "hidden")
        self.assertEqual(outer.get("overflow-y"), "hidden")
        self.assertEqual(table.get("overflow"), "auto")


if __name__ == "__main__":
    unittest.main()
