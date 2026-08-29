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
context.window.buildAttrPayload = (fields, attrs) => Object.assign({}, attrs || {});
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

    def test_empty_precheck_clears_current_skipped_summary_without_api_call(self):
        out = self._run(r'''
let apiCalls = 0;
API.batchPrecheckVariants = async () => { apiCalls++; return {results:[]}; };
const s = mkState({
  drafts:[],
  skipped:[{row:{draft_id:"gone"},related_variant_id:8}],
  showSkipped:true,
  precheckErrors:{gone:[{code:"old",message:"舊錯誤"}]},
});
(async () => {
  await s.runPrecheck();
  out.current = {
    apiCalls,
    errors:s.precheckErrors,
    skipped:s.skipped,
    showSkipped:s.showSkipped,
  };

  const stale = mkState({
    drafts:[],
    skipped:[{row:{draft_id:"keep"},related_variant_id:9}],
    showSkipped:true,
    precheckErrors:{keep:[{code:"old",message:"保留"}]},
  });
  const originalErrors = stale.precheckErrors;
  const originalSkipped = stale.skipped;
  Object.defineProperty(stale, "precheckSeq", {
    configurable:true,
    get() { return this._seq || 0; },
    set(value) { this._seq = value + 1; },
  });
  await stale.runPrecheck();
  out.stale = {
    errorsSame:stale.precheckErrors === originalErrors,
    skippedSame:stale.skipped === originalSkipped,
    showSkipped:stale.showSkipped,
  };
  done();
})();
''')
        self.assertEqual(out["current"], {
            "apiCalls": 0, "errors": {}, "skipped": [],
            "showSkipped": False,
        })
        self.assertEqual(out["stale"], {
            "errorsSame": True, "skippedSame": True, "showSkipped": True,
        })

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

    def test_build_payload_keeps_factory_and_store_barcodes(self):
        out = self._run(r'''
const s = mkState({fields:[]});
const rows = [
  {draft_id:"both",attrs:{},price:null,model_ids:[],barcode:"F-001",store:true},
  {draft_id:"factory",attrs:{},price:null,model_ids:[],barcode:"F-002",store:false},
  {draft_id:"store",attrs:{},price:null,model_ids:[],barcode:"",store:true},
  {draft_id:"none",attrs:{},price:null,model_ids:[],barcode:"",store:false},
];
out.barcodes = s.buildPayload(rows).map(row => [row.draft_id, row.barcodes]);
done();
''')
        self.assertEqual(out["barcodes"], [
            ["both", [
                {"barcode": "F-001", "source": "factory"},
                {"source": "store"},
            ]],
            ["factory", [{"barcode": "F-002", "source": "factory"}]],
            ["store", [{"source": "store"}]],
            ["none", []],
        ])

    def test_feature_tags_participate_in_logic_and_page_diff(self):
        out = self._run(r'''
const fields = [
  {field_id:1,name:"顏色",field_type:"select"},
  {field_id:2,name:"特性詞條",field_type:"tags"},
];
const rows = [
  {draft_id:"d1",attrs:{"顏色":"黑","特性詞條":["防摔"]},price:100,barcode:"",model_ids:[]},
  {draft_id:"d2",attrs:{"顏色":"黑","特性詞條":["磁吸"]},price:100,barcode:"",model_ids:[]},
];
out.logic = Array.from(window.VariantBatchLogic.diffFieldNames(rows, fields));
const s = mkState({fields,drafts:rows});
out.page = Array.from(s.diffFields);
done();
''')
        self.assertEqual(out["logic"], ["特性詞條"])
        self.assertEqual(out["page"], ["特性詞條"])

    def test_barcode_diff_includes_factory_barcode_and_store_flag(self):
        out = self._run(r'''
const logic = window.VariantBatchLogic;
const base = {attrs:{},price:null,model_ids:[],barcode:"",store:false};
out.store = Array.from(logic.diffFieldNames([
  base, {...base,store:true},
], []));
out.factory = Array.from(logic.diffFieldNames([
  base, {...base,barcode:"F-001"},
], []));
out.same = Array.from(logic.diffFieldNames([
  base, {...base},
], []));
done();
''')
        self.assertEqual(out["store"], ["__barcode"])
        self.assertEqual(out["factory"], ["__barcode"])
        self.assertEqual(out["same"], [])

    def test_barcode_errors_include_store_prefix_only(self):
        out = self._run(r'''
const draft = {draft_id:"d1"};
const s = mkState({precheckErrors:{d1:[
  {code:"duplicate_barcode",message:"條碼重複"},
  {code:"store_prefix_barcode",message:"條碼不可使用自取碼前綴"},
  {code:"missing_required",field_id:7,message:"顏色必填"},
]}});
out.codes = s.barcodeErrors(draft).map(error => error.code);
done();
''')
        self.assertEqual(out["codes"], [
            "duplicate_barcode", "store_prefix_barcode"])

    def test_reset_input_keeps_select_default_value(self):
        out = self._run(r'''
// 比照 api.js:initFormAttrs 對 select 預設值的處理(harness 的簡化替身沒有這段)
window.initFormAttrs = (fields, existing) => {
  const attrs = Object.assign({}, existing || {});
  for (const field of (fields || [])) {
    if (field.name in attrs) continue;
    if (field.field_type === "multi") attrs[field.name] = [];
    else if (field.field_type === "select" && field.default_value)
      attrs[field.name] = field.default_value;
    else attrs[field.name] = "";
  }
  return attrs;
};
const s = mkState({fields:[
  {field_id:1, name:"版型", field_type:"select", default_value:"滿版"},
  {field_id:2, name:"框色", field_type:"select"},
]});
s.resetInput();
out.attrs = s.input.attrs;
out.count = s.previewCount;
done();
''')
        self.assertEqual(out["attrs"]["版型"], ["滿版"])   # 建檔預設值仍帶入
        self.assertEqual(out["attrs"]["框色"], [])
        self.assertEqual(out["count"], 1)                  # 單一組合,不觸發展開

    def test_commit_maps_structured_details(self):
        out = self._run(r'''
let refreshCalls = 0;
API.batchCreateVariants = async () => {
  const err = new Error("部分資料有誤");
  err.details = [{index:1,draft_id:"d2",errors:[
    {code:"duplicate_signature",field_id:null,related_draft_id:"d1",message:"重複"},
    {code:"missing_required",field_id:7,related_draft_id:null,message:"顏色必填"},
  ]}];
  throw err;
};
API.listCatalog = async () => { refreshCalls++; return []; };
const s = mkState({productId:5,fields:[{field_id:7,name:"顏色",field_type:"select"}],drafts:[
  {draft_id:"d1",attrs:{"顏色":"黑"},price:null,model_ids:[],barcode:"",store:false},
  {draft_id:"d2",attrs:{"顏色":"黑"},price:null,model_ids:[],barcode:"",store:false},
]});
(async () => {
  await s.commitAll();
  out.errors = s.precheckErrors;
  out.status = s.rowStatus(s.drafts[1]);
  out.kept = s.drafts.length;
  out.refreshCalls = refreshCalls;
  out.submitting = s.submitting;
  done();
})();
''')
        self.assertEqual(out["errors"]["d2"][1]["message"], "顏色必填")
        self.assertEqual(out["status"], "與第 1 筆重複")
        self.assertEqual(out["kept"], 2)
        self.assertEqual(out["refreshCalls"], 0)
        self.assertFalse(out["submitting"])

    def test_existing_variant_text_explains_unavailable_variant(self):
        out = self._run(r'''
const active = {
  variant_id:37,
  attributes:{"顏色":"黑色","材質":"玻璃"},
};
const s = mkState({products:[{product_id:5,variants:[active]}]});
out.found = s.existingVariantText({related_variant_id:37});
out.missing = s.existingVariantText({related_variant_id:38});
done();
''')
        self.assertEqual(out["found"], "顏色：黑色｜材質：玻璃")
        self.assertEqual(
            out["missing"],
            "款式編號 38（目前為已停用或待處理，請至商品資料庫勾選"
            "「顯示已停用」或「待處理」後處理）",
        )

    def test_successful_commit_refreshes_catalog_without_resetting_selection(self):
        out = self._run(r'''
const refreshed = [{product_id:5,variants:[{
  variant_id:88,attributes:{"顏色":"白色"},
}]}];
let catalogArgs = null;
API.batchCreateVariants = async () => ({results:[{variant_id:88}]});
API.listCatalog = async args => { catalogArgs = args; return refreshed; };
const fields = [{field_id:1,name:"顏色",field_type:"select"}];
const s = mkState({
  catId:1,
  productId:5,
  products:[{product_id:5,variants:[]}],
  fields,
  drafts:[{draft_id:"d1",attrs:{"顏色":"白色"},price:null,
           model_ids:[],barcode:"",store:false}],
});
s.reloadUsage = async () => {};
(async () => {
  await s.commitAll();
  out.catalogArgs = catalogArgs;
  out.sameSnapshot = s.products === refreshed;
  out.productId = s.productId;
  out.fieldsSame = s.fields === fields;
  out.found = s.existingVariant({related_variant_id:88});
  done();
})();
''')
        self.assertEqual(out["catalogArgs"], {})
        self.assertTrue(out["sameSnapshot"])
        self.assertEqual(out["productId"], 5)
        self.assertTrue(out["fieldsSame"])
        self.assertEqual(out["found"]["variant_id"], 88)

    def test_successful_commit_runs_both_refreshes_and_reports_refresh_failure(self):
        out = self._run(r'''
const refreshed = [{product_id:5,variants:[{variant_id:88,attributes:{}}]}];
const message = "款式已建立，但畫面資料重新整理失敗，請關閉後重新開啟視窗。";
API.batchCreateVariants = async () => ({results:[{variant_id:88}]});
let catalogCalls = 0;
API.listCatalog = async () => { catalogCalls++; return refreshed; };
const first = mkState({productId:5,fields:[],drafts:[
  {draft_id:"d1",attrs:{},price:null,model_ids:[],barcode:"",store:false},
]});
first.reloadUsage = async () => { throw new Error("usage refresh failed"); };

const second = mkState({productId:5,fields:[],drafts:[
  {draft_id:"d2",attrs:{},price:null,model_ids:[],barcode:"",store:false},
]});
second.reloadUsage = async () => {};
API.listCatalog = async () => {
  catalogCalls++;
  const err = new Error("catalog refresh failed");
  err.details = [{draft_id:"d2",errors:[{code:"raw",message:"不可映射"}]}];
  throw err;
};

(async () => {
  API.listCatalog = async () => { catalogCalls++; return refreshed; };
  await first.commitAll();
  out.usageFailure = {
    catalogCalls,
    productsSame:first.products === refreshed,
    error:first._error,
    saved:first._saved,
    doneMsg:first.doneMsg,
    drafts:first.drafts,
    submitting:first.submitting,
  };

  API.listCatalog = async () => {
    catalogCalls++;
    const err = new Error("catalog refresh failed");
    err.details = [{draft_id:"d2",errors:[{code:"raw",message:"不可映射"}]}];
    throw err;
  };
  await second.commitAll();
  out.catalogFailure = {
    catalogCalls,
    error:second._error,
    saved:second._saved,
    doneMsg:second.doneMsg,
    drafts:second.drafts,
    errors:second.precheckErrors,
    submitting:second.submitting,
  };
  out.message = message;
  done();
})();
''')
        self.assertEqual(out["usageFailure"], {
            "catalogCalls": 1,
            "productsSame": True,
            "error": out["message"],
            "saved": True,
            "doneMsg": "已建立 1 筆款式。",
            "drafts": [],
            "submitting": False,
        })
        self.assertEqual(out["catalogFailure"], {
            "catalogCalls": 2,
            "error": out["message"],
            "saved": True,
            "doneMsg": "已建立 1 筆款式。",
            "drafts": [],
            "errors": {},
            "submitting": False,
        })

    def test_field_options_preserve_values_and_mark_only_inactive_usage(self):
        out = self._run(r'''
const field = {field_id:7,name:"顏色",field_type:"select"};
const s = mkState({fieldUsage:{7:[
  {value:"黑色",active:true},
  {value:"白色",active:false},
]}});
const known = {attrs:{"顏色":"白色"}};
const unknown = {attrs:{"顏色":"透明"}};
out.knownOptions = s.fieldOptions(field, known);
out.knownInactive = out.knownOptions.map(value => s.fieldOptionInactive(field, value));
out.unknownOptions = s.fieldOptions(field, unknown);
out.unknownInactive = out.unknownOptions.map(value => s.fieldOptionInactive(field, value));
done();
''')
        self.assertEqual(out["knownOptions"], ["黑色", "白色"])
        self.assertEqual(out["knownInactive"], [False, True])
        self.assertEqual(out["unknownOptions"], ["黑色", "白色", "透明"])
        self.assertEqual(out["unknownInactive"], [False, True, False])

    def test_template_is_workbook_without_edit_popup(self):
        html = (STATIC / "variant_batch.html").read_text(encoding="utf-8")
        self.assertIn('class="batch-table"', html)
        self.assertIn('class="batch-fixed-editor"', html)
        self.assertIn('class="batch-skipped"', html)
        self.assertIn('v-if="product && productReady"', html)
        self.assertRegex(html, r"js/variant_batch_logic\.js\?v=\d+")
        self.assertIn(
            '<th v-if="featureField && (!showDiffOnly || '
            'diffFields.has(featureField.name))">', html)
        self.assertIn(
            '<td v-if="featureField && (!showDiffOnly || '
            'diffFields.has(featureField.name))"', html)
        self.assertIn(
            "'cell-diff':diffFields.has(featureField.name)", html)
        self.assertIn(
            "fieldOptionInactive(f,value) ? '（停用，將重新啟用）' : ''",
            html,
        )
        self.assertNotIn("openEdit", html)
        self.assertNotIn("dialog-overlay", html)

    def test_escape_uses_single_outer_listener_and_fixed_editor_first(self):
        batch_source = (STATIC / "js" / "variant_batch.js").read_text(
            encoding="utf-8")
        window_source = (STATIC / "js" / "variant_batch_window.js").read_text(
            encoding="utf-8")
        html = (STATIC / "variant_batch.html").read_text(encoding="utf-8")
        self.assertNotIn('document.addEventListener("keydown"', batch_source)
        self.assertEqual(window_source.count(
            'document.addEventListener("keydown"'), 1)
        self.assertIn('<page-variant-batch ref="batchPage"', html)

        script = r'''
const fs = require("fs"), vm = require("vm");
const listeners = {};
let definition;
const closeCalls = [];
const context = {
  document: {
    addEventListener(name, handler) {
      (listeners[name] ||= []).push(handler);
    },
    removeEventListener() {},
  },
  window: {PosMixin:{}, PosPages:{}, PosComponents:{}},
  API: {invoke: async (action, payload) => {
    if (action === "desktop.child_window.context") return {context:{}};
    if (action === "desktop.child_window.close") closeCalls.push(payload);
    return {};
  }},
  Vue: {createApp(options) {
    definition = options;
    return {mixin(){}, component(){}, mount(){}};
  }},
  console, setTimeout, clearTimeout,
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
listeners.DOMContentLoaded[0]();
const state = Object.assign({}, definition.data());
for (const [name, method] of Object.entries(definition.methods))
  state[name] = method.bind(state);
let editorClosed = 0;
state.$refs = {batchPage:{
  fixedEditor:{draftId:"d1",fieldName:"特性詞條"},
  closeFixedEditor(){ editorClosed++; this.fixedEditor = null; },
}};
(async () => {
  await definition.mounted.call(state);
  const event = {key:"Escape",repeat:false,prevented:false,preventDefault(){this.prevented=true;}};
  listeners.keydown[0](event);
  await Promise.resolve();
  const afterEditor = {editorClosed,prevented:event.prevented,closeCalls:closeCalls.length};
  const repeated = {key:"Escape",repeat:true,prevented:false,preventDefault(){this.prevented=true;}};
  listeners.keydown[0](repeated);
  await Promise.resolve();
  const afterRepeat = {editorClosed,prevented:repeated.prevented,closeCalls:closeCalls.length};
  const event2 = {key:"Escape",repeat:false,prevented:false,preventDefault(){this.prevented=true;}};
  listeners.keydown[0](event2);
  await Promise.resolve();
  process.stdout.write(JSON.stringify({
    listenerCount:listeners.keydown.length,
    afterEditor,
    afterRepeat,
    afterWindow:{editorClosed,prevented:event2.prevented,closeCalls:closeCalls.length},
  }));
})();
'''
        result = subprocess.run(
            ["node", "-e", script,
             str(STATIC / "js" / "variant_batch_window.js")],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8")
        if result.returncode != 0:
            self.fail(result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["listenerCount"], 1)
        self.assertEqual(out["afterEditor"], {
            "editorClosed": 1, "prevented": True, "closeCalls": 0})
        self.assertEqual(out["afterRepeat"], {
            "editorClosed": 1, "prevented": False, "closeCalls": 0})
        self.assertEqual(out["afterWindow"], {
            "editorClosed": 1, "prevented": False, "closeCalls": 1})

    def test_all_shared_resource_versions_match(self):
        # 資源版號會持續往上 bump,測固定數字每次改 css/js 都要改測試;
        # 改測「四份共用同一個版號、且不低於 157」。
        seen = set()
        for filename in ["index.html", "variant_editor.html",
                         "variant_batch.html", "field_editor.html"]:
            html = (STATIC / filename).read_text(encoding="utf-8")
            versions = set(re.findall(r"\?v=(\d+)", html))
            self.assertEqual(len(versions), 1, filename)
            seen |= versions
        self.assertEqual(len(seen), 1, seen)
        self.assertGreaterEqual(int(seen.pop()), 157)

    def test_batch_workspace_has_only_table_as_outer_scroller(self):
        css = (STATIC / "css" / "pos.css").read_text(encoding="utf-8")
        html = (STATIC / "variant_batch.html").read_text(encoding="utf-8")

        def declarations(selector):
            match = re.search(
                rf"^[ \t]*{re.escape(selector)}\s*\{{([^}}]*)\}}",
                css,
                re.DOTALL | re.MULTILINE,
            )
            self.assertIsNotNone(match, f"missing CSS rule for {selector}")
            return {
                name.strip(): value.strip()
                for item in match.group(1).split(";")
                if ":" in item
                for name, value in [item.split(":", 1)]
            }

        outer = declarations(".dialog-content.batch-content")
        preview = declarations(".batch-content > .batch-preview")
        table = declarations(".batch-table")
        grid = declarations(".batch-table .dialog-table")
        cells = declarations(
            ".batch-table .dialog-table th, .batch-table .dialog-table td")
        actions = declarations(
            ".batch-table .dialog-table .batch-col-actions,\n"
            ".batch-table .dialog-table .batch-row-actions")
        models = declarations(
            ".batch-table .dialog-table .batch-col-model,\n"
            ".batch-table .dialog-table .batch-cell-model")
        prices = declarations(
            ".batch-table .dialog-table .batch-col-price,\n"
            ".batch-table .dialog-table .batch-cell-price")
        barcodes = declarations(
            ".batch-table .dialog-table .batch-col-barcode,\n"
            ".batch-table .dialog-table .batch-cell-barcode")
        statuses = declarations(
            ".batch-table .dialog-table .batch-col-status,\n"
            ".batch-table .dialog-table .batch-status")
        controls = declarations(
            ".batch-table input, .batch-table select, .batch-table button")
        cell_button = declarations(".dialog-shell .batch-cell-button")
        store_check = declarations(".batch-store-check")
        status = declarations(".batch-status")
        footer = declarations(".batch-footer")

        self.assertEqual(outer.get("overflow"), "hidden")
        self.assertEqual(outer.get("overflow-y"), "hidden")
        self.assertGreater(
            css.index(".batch-content > .batch-preview"),
            css.index(".batch-content > .dialog-section"),
        )
        self.assertEqual(preview.get("flex"), "1 1 180px")
        self.assertEqual(preview.get("min-height"), "140px")
        self.assertEqual(preview.get("min-width"), "0")
        self.assertEqual(table.get("flex"), "1 1 auto")
        self.assertEqual(table.get("min-height"), "0")
        self.assertEqual(table.get("min-width"), "0")
        self.assertEqual(table.get("overflow-y"), "auto")
        self.assertEqual(table.get("overflow-x"), "hidden")
        self.assertEqual(grid.get("width"), "100%")
        self.assertEqual(grid.get("min-width"), "0")
        self.assertEqual(grid.get("table-layout"), "fixed")
        self.assertNotEqual(grid.get("width"), "max-content")
        self.assertEqual(cells.get("min-width"), "0")
        self.assertEqual(cells.get("white-space"), "nowrap")
        self.assertEqual(cells.get("overflow"), "hidden")
        self.assertEqual(cells.get("text-overflow"), "ellipsis")
        self.assertNotEqual(cells.get("overflow-wrap"), "anywhere")
        for column, width in [
                (actions, "80px"), (models, "110px"), (prices, "80px"),
                (barcodes, "150px"), (statuses, "110px")]:
            self.assertEqual(column.get("width"), width)
            self.assertEqual(column.get("min-width"), "0")
        self.assertEqual(controls.get("min-width"), "0")
        self.assertEqual(controls.get("max-width"), "100%")
        self.assertEqual(controls.get("box-sizing"), "border-box")
        self.assertEqual(controls.get("white-space"), "nowrap")
        self.assertEqual(cell_button.get("overflow"), "hidden")
        self.assertEqual(cell_button.get("text-overflow"), "ellipsis")
        self.assertEqual(cell_button.get("white-space"), "nowrap")
        self.assertEqual(store_check.get("min-width"), "0")
        self.assertEqual(store_check.get("white-space"), "nowrap")
        self.assertEqual(store_check.get("overflow"), "hidden")
        self.assertEqual(status.get("white-space"), "nowrap")
        self.assertEqual(status.get("overflow"), "hidden")
        self.assertEqual(status.get("text-overflow"), "ellipsis")
        self.assertNotEqual(status.get("overflow-wrap"), "anywhere")
        self.assertIn('<th v-if="modelMode===\'required\'" class="batch-col-model">', html)
        self.assertIn('<td v-if="modelMode===\'required\'" class="batch-cell-model"', html)
        self.assertIn('<th class="batch-col-price">售價</th>', html)
        self.assertIn('<td class="batch-cell-price"', html)
        self.assertIn('<th class="batch-col-barcode">條碼</th>', html)
        self.assertIn('<td class="batch-cell-barcode"', html)
        self.assertEqual(footer.get("position"), "relative")
        self.assertEqual(footer.get("z-index"), "3")
        for selector in [".batch-content > .batch-input", ".batch-skipped",
                         ".batch-fixed-editor"]:
            section = declarations(selector)
            self.assertNotEqual(section.get("overflow"), "auto", selector)
            self.assertNotEqual(section.get("overflow-y"), "auto", selector)
        # 輸入區 section 本身不捲,但內容過高時由 body 內捲(VUE-18):
        # section 必須可收縮(flex 0 1)且 body 是 overflow-y:auto 的內部捲動容器。
        input_section = declarations(".batch-content > .batch-input")
        self.assertEqual(input_section.get("flex"), "0 1 auto")
        input_body = declarations(".batch-input-body")
        self.assertEqual(input_body.get("overflow-y"), "auto")
        self.assertEqual(input_body.get("min-height"), "0")


if __name__ == "__main__":
    unittest.main()
