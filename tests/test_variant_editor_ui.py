"""獨立款式編輯視窗的模板、主題與前端狀態測試。"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
HTML = STATIC / "variant_editor.html"
JS = STATIC / "js" / "variant_editor.js"
ATTR_JS = STATIC / "js" / "attrfields.js"
MODEL_JS = STATIC / "js" / "modelpicker.js"
CSS = STATIC / "css" / "dialog-theme.css"


class VariantEditorTemplateTests(unittest.TestCase):
    def _source(self, path):
        self.assertTrue(path.is_file(), f"missing {path.name}")
        return path.read_text(encoding="utf-8")

    def test_template_reuses_complete_shared_editing_components(self):
        html = self._source(HTML)
        self.assertIn('<form class="dialog-shell" @submit.prevent="save">', html)
        self.assertEqual(html.count('<section class="dialog-section"'), 4)
        # 適用型號依種類設定顯示(model_mode=required 才出現)
        self.assertIn('<section class="dialog-section" v-if="usesModel">', html)
        self.assertNotIn("<fieldset", html)
        for title in ("規格", "適用手機型號", "售價", "條碼"):
            self.assertIn(f'<h2>{title}</h2>', html)
        self.assertIn('<div class="dialog-content">', html)
        self.assertIn('<footer class="dialog-footer">', html)
        self.assertIn("<attr-fields", html)
        self.assertIn("<model-picker", html)
        self.assertIn('v-model.number="price"', html)
        self.assertIn('v-model="factoryBarcode"', html)
        self.assertIn('@click="addFactoryBarcode"', html)
        self.assertIn('@click="addStoreBarcode"', html)
        self.assertIn('@click="removeBarcode(b)"', html)
        self.assertIn('class="dialog-error"', html)
        self.assertIn(':disabled="saving || !ready"', html)
        self.assertIn('@keydown.enter="handleFactoryBarcodeEnter"', html)
        for shared in ("catalogfields.js", "variant_batch.js", "modelpicker.js",
                       "attrfields.js"):
            self.assertIn(shared, html)

    def test_dialog_theme_matches_police_doc_dialog_tokens_and_checkbox_contract(self):
        css = self._source(CSS)
        for token in (
            'font-size: 14pt', '"Microsoft JhengHei"', '#1c1c1e',
            'background: #ffffff', 'border: 1px solid #cccccc',
            'border-radius: 4px', '#D0ECF5', '#B8D8E8',
            '#F2F2F7', '#E5E5EA', 'border-radius: 6px', 'min-width: 80px',
        ):
            self.assertIn(token, css)
        self.assertIn('.dialog-shell input[type="checkbox"] {', css)
        self.assertIn('appearance: none', css)
        self.assertIn('width: 18px', css)
        self.assertIn('height: 18px', css)
        self.assertIn('flex: 0 0 18px', css)
        self.assertIn('vertical-align: middle', css)
        self.assertIn('input[type="checkbox"]:checked', css)
        self.assertIn('input[type="checkbox"]:disabled', css)
        self.assertIn('#8fa8c8', css)
        self.assertIn('#d1d1d6', css)
        self.assertIn('overflow-y: auto', css)
        self.assertIn('flex: 0 0 auto', css)
        self.assertIn('.dialog-section + .dialog-section {', css)
        self.assertIn('border-top: 1px solid #cccccc', css)
        self.assertIn('.dialog-shell .chip-box,', css)
        self.assertIn('.dialog-shell .tag-selector { border: 0;', css)
        self.assertIn('.dialog-shell .tag-chip,', css)
        self.assertIn('width: max-content', css)
        self.assertIn('.dialog-shell .tag-x {', css)
        self.assertIn('min-width: 0 !important', css)
        self.assertIn('.dialog-shell .tag-selector button.chip:not(.tag-chip)', css)
        self.assertIn('background: #ffffff', css)
        self.assertIn('.dialog-shell .tag-count {', css)
        self.assertIn('color: #636366', css)
        self.assertIn('.dialog-shell .model-picker .chip', css)
        self.assertIn('.dialog-shell .attr-row {', css)
        self.assertIn('grid-template-columns: 7em minmax(0, 1fr)', css)
        self.assertIn('align-items: start', css)
        self.assertIn('border: 1px solid #e5e5ea', css)
        self.assertIn('.dialog-shell .attr-row > .attr-name {', css)
        self.assertIn('.dialog-shell .attr-row > .chip-box {', css)

        attrs = self._source(ATTR_JS)
        tags_branch = attrs.split("f.field_type === 'tags' && tagsStyle === 'chips'", 1)[1]
        tags_branch = tags_branch.split("v-else-if=\"f.field_type === 'tags'\"", 1)[0]
        self.assertIn("<opt-picker", tags_branch)
        self.assertIn(':multiple="true"', tags_branch)
        self.assertIn(':as-list="false"', tags_branch)
        self.assertNotIn('toggleTag(', tags_branch)
        editor = self._source(JS)
        self.assertIn('["select", "multi", "tags"]', editor)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_model_picker_keeps_authoritative_order_across_interleaved_series(self):
        script = r'''
const fs=require("fs"),vm=require("vm");
const context={window:{PosComponents:{}}}; vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1],"utf8"),context);
const c=context.window.PosComponents["model-picker"];
const s={models:[
 {model_id:1,brand_name:"Apple",series:"Pro",name:"A"},
 {model_id:2,brand_name:"Apple",series:"Air",name:"B"},
 {model_id:3,brand_name:"Apple",series:"Pro",name:"C"},
 {model_id:4,brand_name:"Samsung",series:null,name:"D"}], model_ids:[3,1]};
const groups=c.computed.filteredGroups.call(s);
const selected=c.computed.selectedModels.call(s);
process.stdout.write(JSON.stringify({
 sections:groups.map(g=>[g.brand,g.sections.map(x=>[x.series,x.items.map(m=>m.model_id)])]),
 selected:selected.map(m=>m.model_id)}));
'''
        result = subprocess.run(
            ["node", "-e", script, str(MODEL_JS)], cwd=ROOT,
            text=True, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "sections": [["Apple", [["Pro", [1]], ["Air", [2]], ["Pro", [3]]]],
                         ["Samsung", [["", [4]]]]],
            "selected": [1, 3],
        })

    def test_checkbox_box_is_exactly_18_square_in_real_dom_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / "checkbox.html"
            html.write_text(
                "<style>" + self._source(STATIC / "css" / "pos.css") +
                self._source(CSS) + "</style>" +
                '<div class="dialog-shell"><label class="chip">'
                '<input id="box" type="checkbox">整段可點</label></div>' +
                '<output id="result"></output><script>'
                'const box=document.getElementById("box"),r=box.getBoundingClientRect(),'
                's=getComputedStyle(box);document.getElementById("result").textContent='
                '`CHECKBOX_RECT=${r.width}x${r.height};PADDING=${s.padding}`;'
                '</script>', encoding="utf-8")
            probe = r'''
import json, sys, webview
window = webview.create_window("checkbox-probe", sys.argv[1], hidden=True)
def inspect():
    window.events.loaded.wait()
    value = window.evaluate_js(''' + repr('''(() => {
      const box=document.getElementById("box"), r=box.getBoundingClientRect();
      return {width:r.width,height:r.height,padding:getComputedStyle(box).padding};
    })()''') + r''')
    print("CHECKBOX_RECT=" + json.dumps(value), flush=True)
    window.destroy()
webview.start(inspect, gui="edgechromium")
'''
            result = subprocess.run(
                [sys.executable, "-c", probe, html.as_uri()], text=True,
                capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        measured = re.search(r"CHECKBOX_RECT=(\{[^\r\n]+\})", result.stdout)
        self.assertIsNotNone(measured, result.stdout)
        self.assertEqual(json.loads(measured.group(1)), {
            "width": 18, "height": 18, "padding": "0px"})


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class VariantEditorRuntimeTests(unittest.TestCase):
    def _run(self, body):
        self.assertTrue(JS.is_file(), f"missing {JS.name}")
        script = r'''
const fs = require("fs"), vm = require("vm");
const context = {
  window:{PosComponents:{}}, API:{}, console, setTimeout, clearTimeout,
  document:{addEventListener:()=>{}},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const config = context.window.VariantEditorApp;
function mkState(extra) {
  const s = {};
  Object.assign(s, config.data.call(s));
  for (const [name, method] of Object.entries(config.methods)) s[name] = method.bind(s);
  for (const [name, getter] of Object.entries(config.computed || {}))
    Object.defineProperty(s, name, {get:getter.bind(s), configurable:true});
  Object.assign(s, extra || {});
  return s;
}
const API = context.API, window = context.window, out = {};
function done(){ process.stdout.write(JSON.stringify(out)); }
BODY
'''.replace("BODY", body)
        result = subprocess.run(
            ["node", "-e", script, str(JS)], cwd=ROOT,
            text=True, capture_output=True, encoding="utf-8",
        )
        if result.returncode != 0:
            self.fail(result.stderr)
        return json.loads(result.stdout)

    def test_load_initializes_fields_models_selected_values_and_barcodes(self):
        out = self._run(r'''
API.invoke = async action => ({
  page:"variant_editor",
  context:{
    product:{product_id:3,category_id:5,name:"殼"},
    variant:{variant_id:7,attributes:{顏色:"紅"},price:490,
      models:["17 Pro"],barcodes:[{barcode:"B1",source:"factory"}]},
  },
});
API.categoryFields = async () => [{field_id:11,name:"顏色",field_type:"select"}];
API.listCategories = async () => [{category_id:5, model_mode:"required"}];
API.listModels = async () => [
  {model_id:21,name:"iPhone 17 Pro",alias:"17 Pro",brand_name:"iPhone"},
  {model_id:22,name:"iPhone 17",alias:"17",brand_name:"iPhone"},
];
window.CatalogFields = {
  loadFieldsWithOptions: async (fields, into) => { into[11]=[{value:"紅"}]; },
  loadFieldUsage: async (cid, fields, into) => { into[11]=[{value:"紅",active:true}]; },
  usageScope: product => ({brand_id:product.brand_id ?? null, product_id:product.product_id}),
};
window.initFormAttrs = (fields, attrs) => ({...attrs});
const s = mkState();
(async()=>{
  await s.load();
  out.product = s.product;
  out.variantId = s.variant.variant_id;
  out.attrs = s.attrs;
  out.modelIds = s.modelIds;
  out.price = s.price;
  out.barcodes = s.barcodes;
  out.ready = s.ready;
  out.usesModel = s.usesModel;
  done();
})();
''')
        self.assertEqual(out["product"]["product_id"], 3)
        self.assertEqual(out["variantId"], 7)
        self.assertEqual(out["attrs"], {"顏色": "紅"})
        self.assertEqual(out["modelIds"], [21])
        self.assertEqual(out["price"], 490)
        self.assertEqual(out["barcodes"], [
            {"barcode": "B1", "source": "factory", "existing": True}])
        self.assertTrue(out["ready"])
        self.assertTrue(out["usesModel"])

    def test_each_load_dependency_failure_keeps_editor_unready_and_save_is_a_noop(self):
        for failure in ("context", "fields", "models", "categories", "options", "usage"):
            with self.subTest(failure=failure):
                out = self._run(r'''
const failure = "FAILURE";
const calls=[];
API.invoke = async action => {
  calls.push(action);
  if (action === "desktop.child_window.context" && failure === "context")
    throw new Error("context failed");
  return {page:"variant_editor",
    context:{product:{product_id:3,category_id:5,name:"殼"},
      variant:{variant_id:7,attributes:{顏色:"紅"},price:490,models:[],barcodes:[]}}};
};
API.categoryFields = async () => {
  if (failure === "fields") throw new Error("fields failed");
  return [{field_id:11,name:"顏色",field_type:"select"}];
};
API.listModels = async () => {
  if (failure === "models") throw new Error("models failed");
  return [];
};
API.listCategories = async () => {
  if (failure === "categories") throw new Error("categories failed");
  return [{category_id:5, model_mode:"required"}];
};
API.updateVariantEditor = async payload => calls.push(["write", payload]);
window.CatalogFields = {
  loadFieldsWithOptions: async () => {
    if (failure === "options") throw new Error("options failed");
  },
  loadFieldUsage: async () => {
    if (failure === "usage") throw new Error("usage failed");
  },
  usageScope: () => ({brand_id:null, product_id:3}),
};
window.initFormAttrs = (fields, attrs) => ({...attrs});
window.buildAttrPayload = () => ({});
const s = mkState();
(async()=>{
  await s.load();
  await s.save();
  out.ready=s.ready; out.product=s.product; out.variant=s.variant;
  out.writes=calls.filter(item => Array.isArray(item) && item[0] === "write");
  done();
})();
'''.replace("FAILURE", failure))
                self.assertFalse(out["ready"])
                self.assertIsNone(out["product"])
                self.assertIsNone(out["variant"])
                self.assertEqual(out["writes"], [])

    def test_failed_save_keeps_user_state_and_window_open(self):
        out = self._run(r'''
const calls=[];
window.buildAttrPayload = () => ({顏色:"藍"});
API.updateVariantEditor = async payload => { calls.push(["update",payload]); throw new Error("儲存失敗"); };
API.invoke = async action => { calls.push(action); };
const s = mkState({variant:{variant_id:7}, product:{category_id:5},
  ready:true, fields:[], fieldOptions:{}, attrs:{顏色:"藍"}, modelIds:[21], price:590,
  deletedBarcodes:["B1"], pendingStoreCount:2,
  barcodes:[{barcode:"F2",source:"factory",existing:false}]});
(async()=>{
  await s.save();
  out.calls=calls; out.attrs=s.attrs; out.price=s.price;
  out.deleted=s.deletedBarcodes; out.barcodes=s.barcodes;
  out.pendingStoreCount=s.pendingStoreCount;
  out.error=s.error; out.saving=s.saving; done();
})();
''')
        self.assertEqual(out["calls"], [["update", {
            "id": 7,
            "fields": {"attributes": {"顏色": "藍"}, "price": 590},
            "model_ids": [21], "deleted_barcodes": ["B1"],
            "factory_barcodes": ["F2"], "store_barcode_count": 2,
        }]])
        self.assertEqual(out["attrs"], {"顏色": "藍"})
        self.assertEqual(out["price"], 590)
        self.assertEqual(out["deleted"], ["B1"])
        self.assertEqual(out["barcodes"], [
            {"barcode": "F2", "source": "factory", "existing": False}])
        self.assertEqual(out["pendingStoreCount"], 2)
        self.assertEqual(out["error"], "儲存失敗")
        self.assertFalse(out["saving"])

    def test_committed_save_retries_only_saved_true_after_close_failure(self):
        out = self._run(r'''
const calls=[];
window.buildAttrPayload = () => ({顏色:"紅"});
API.updateVariantEditor = async payload => calls.push(["update",payload]);
let closes=0;
API.invoke = async (action,payload) => {
  calls.push([action,payload]);
  if (action === "desktop.child_window.close" && ++closes === 1)
    throw new Error("close failed");
};
const s = mkState({variant:{variant_id:7}, product:{category_id:5}, fields:[],
  ready:true, fieldOptions:{}, attrs:{顏色:"紅"}, modelIds:[21], price:490,
  barcodes:[{barcode:"B1",source:"factory",existing:true}]});
s.removeBarcode(s.barcodes[0]);
s.factoryBarcode="F2"; s.addFactoryBarcode(); s.addStoreBarcode();
(async()=>{
  await s.save(); out.saveCalls=calls.slice(); out.committed=s.committed;
  calls.length=0;
  await s.cancel();
  await s.handleKeydown({key:"Escape"}); out.retryCalls=calls; done();
})();
''')
        self.assertEqual(out["saveCalls"], [
            ["update", {"id": 7, "fields": {"attributes": {"顏色": "紅"},
             "price": 490}, "model_ids": [21], "deleted_barcodes": ["B1"],
             "factory_barcodes": ["F2"], "store_barcode_count": 1}],
            ["desktop.child_window.close", {"saved": True}],
        ])
        self.assertTrue(out["committed"])
        self.assertEqual(out["retryCalls"], [
            ["desktop.child_window.close", {"saved": True}],
            ["desktop.child_window.close", {"saved": True}],
        ])

    def test_atomic_save_failure_keeps_complete_queue_for_retry(self):
        out = self._run(r'''
window.buildAttrPayload = () => ({});
API.updateVariantEditor = async () => { throw new Error("交易失敗"); };
API.invoke = async () => {};
const s = mkState({variant:{variant_id:7}, product:{category_id:5}, fields:[],
  ready:true, fieldOptions:{}, attrs:{}, modelIds:[], price:null,
  barcodes:[
    {barcode:"B1",source:"factory",existing:true},
    {barcode:"F2",source:"factory",existing:false},
    {barcode:"F3",source:"factory",existing:false},
  ]});
s.removeBarcode(s.barcodes[0]);
(async()=>{
  await s.save();
  out.deleted=s.deletedBarcodes;
  out.barcodes=s.barcodes;
  out.pendingStoreCount=s.pendingStoreCount;
  out.error=s.error;
  done();
})();
''')
        self.assertEqual(out["deleted"], ["B1"])
        self.assertEqual(out["barcodes"], [
            {"barcode": "F2", "source": "factory", "existing": False},
            {"barcode": "F3", "source": "factory", "existing": False},
        ])
        self.assertEqual(out["pendingStoreCount"], 0)
        self.assertEqual(out["error"], "交易失敗")

    def test_factory_barcode_enter_consumes_event_and_adds_without_saving(self):
        out = self._run(r'''
const s = mkState({factoryBarcode:"FACTORY-ENTER", barcodes:[]});
let prevented=false, stopped=false, saves=0;
s.save=()=>{saves += 1;};
const event={preventDefault:()=>{prevented=true;},stopPropagation:()=>{stopped=true;}};
s.handleFactoryBarcodeEnter(event);
if (!prevented && !stopped) s.save();
out.prevented=prevented; out.stopped=stopped; out.saves=saves;
out.barcodes=s.barcodes; out.input=s.factoryBarcode;
done();
''')
        self.assertTrue(out["prevented"])
        self.assertTrue(out["stopped"])
        self.assertEqual(out["saves"], 0)
        self.assertEqual(out["barcodes"], [{
            "barcode": "FACTORY-ENTER", "source": "factory", "existing": False}])
        self.assertEqual(out["input"], "")


if __name__ == "__main__":
    unittest.main()
