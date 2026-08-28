"""子產品批次建立前端頁邏輯 Node 煙霧測試(draft 快照、預覽、送出映射、詞條選取器)。"""

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


class VariantBatchUiTests(unittest.TestCase):
    def _run(self, body):
        script = r'''
const fs = require("fs"), vm = require("vm");
let lastInvoke = null;
const context = {
  window: { pywebview: { api: { invoke: async (action, payload) => {
    lastInvoke = { action, payload };
    if (action === "variants.batch_create" && payload.drafts && payload.drafts.__fail)
      return {ok:false, error:{code:"validation_error", message:"x",
        details:[{index:0, draft_id:"d1", errors:["規格重複"]}]}};
    if (action === "variants.batch_create")
      return {ok:true, data:{product_id:5, results:[{draft_id:"d1",variant_id:9,barcodes:[]}]}};
    if (action === "variants.field_usage") return {ok:true, data:[]};
    return {ok:true, data:{}};
  }}}},
  console, setTimeout, clearTimeout,
};
context.window.CatalogFields = { filterOptions: (list) => list || [] };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);  // variant_batch_logic.js
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);  // api.js
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);  // optpicker.js
vm.runInContext(fs.readFileSync(process.argv[4], "utf8"), context);  // variant_batch.js
const window = context.window;
const page = window.PosPages["page-variant-batch"];
const optPicker = window.PosComponents["opt-picker"];

function mkState(extra) {
  const s = { showError: () => {}, goPage: () => {} };
  for (const k of Object.keys(page.methods)) s[k] = page.methods[k].bind(s);
  Object.assign(s, page.data.call(s));
  Object.assign(s, extra || {});
  return s;
}
const out = {};
function done() { process.stdout.write(JSON.stringify(out)); }
BODY
'''.replace("BODY", body)
        result = subprocess.run(
            ["node", "-e", script, str(STATIC / "js" / "variant_batch_logic.js"),
             str(STATIC / "js" / "api.js"),
             str(STATIC / "js" / "optpicker.js"),
             str(STATIC / "js" / "variant_batch.js")],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8")
        if result.returncode != 0:
            self.fail(result.stderr)
        return json.loads(result.stdout)

    def test_expand_axes_and_formula_text(self):
        out = self._run(r'''
const logic = window.VariantBatchLogic;
const fields = [
  {name:"顏色", field_type:"select"},
  {name:"長度", field_type:"select"},
  {name:"特性詞條", field_type:"tags"},
  {name:"備註", field_type:"text"},
];
const expanded = logic.expandAxes(fields, {"顏色":["黑","白","粉"], "長度":["1m","2m"]});
out.names = expanded.axes.map(a => a.name);
out.count = expanded.count;
out.formula = logic.formulaText(expanded.axes);
out.single = logic.formulaText([{name:"顏色", values:["黑","白"]}]);
const empty = logic.expandAxes(fields.slice(2), {"特性詞條":"抗刮", "備註":"新品"});
out.emptyAxes = empty.axes;
out.emptyCount = empty.count;
done();
''')
        self.assertEqual(out["names"], ["顏色", "長度"])
        self.assertEqual(out["count"], 6)
        self.assertEqual(out["formula"], "3 個顏色 × 2 個長度＝6 筆")
        self.assertEqual(out["single"], "")
        self.assertEqual(out["emptyAxes"], [])
        self.assertEqual(out["emptyCount"], 1)

    def test_expand_rows_applies_shared_values_and_barcode_rule(self):
        out = self._run(r'''
const logic = window.VariantBatchLogic;
const fields = [
  {name:"顏色", field_type:"select"}, {name:"長度", field_type:"select"},
  {name:"規格", field_type:"multi"}, {name:"特性詞條", field_type:"tags"},
  {name:"備註", field_type:"text"},
];
const input = {attrs:{"顏色":["黑","白","粉"], "長度":["1m","2m"],
  "規格":["快充","防水"], "特性詞條":"抗刮", "備註":"新品"},
  price:100, model_ids:[7,8], barcode:"F1", store:true};
const rows = logic.expandRows(fields, input, 10);
out.count = rows.length;
out.first = rows[0]; out.last = rows[5];
const noAxes = logic.expandRows(fields.slice(2), input, 20);
out.noAxes = noAxes[0];
done();
''')
        self.assertEqual(out["count"], 6)
        self.assertEqual(out["first"]["attrs"]["顏色"], "黑")
        self.assertEqual(out["last"]["attrs"]["長度"], "2m")
        self.assertEqual(out["first"]["attrs"]["規格"], ["快充", "防水"])
        self.assertEqual(out["first"]["attrs"]["特性詞條"], "抗刮")
        self.assertEqual(out["first"]["model_ids"], [7, 8])
        self.assertEqual(out["first"]["price"], 100)
        self.assertEqual(out["first"]["barcode"], "")
        self.assertTrue(out["first"]["store"])
        self.assertEqual(out["noAxes"]["barcode"], "F1")

    def test_duplicate_row_deep_copies_and_clears_barcode(self):
        out = self._run(r'''
const logic = window.VariantBatchLogic;
const row = {draft_id:"d1", attrs:{"顏色":"黑", "規格":["快充"]},
  price:100, model_ids:[7], barcode:"F1", store:true};
const copy = logic.duplicateRow(row, 2);
copy.attrs["規格"].push("防水"); copy.model_ids.push(8);
out.copy = copy; out.original = row;
done();
''')
        self.assertNotEqual(out["copy"]["draft_id"], out["original"]["draft_id"])
        self.assertEqual(out["copy"]["barcode"], "")
        self.assertEqual(out["copy"]["store"], True)
        self.assertEqual(out["original"]["attrs"]["規格"], ["快充"])
        self.assertEqual(out["original"]["model_ids"], [7])

    def test_diff_field_names_detects_attribute_and_shared_value_changes(self):
        out = self._run(r'''
const logic = window.VariantBatchLogic;
const fields = [{name:"顏色",field_type:"select"}, {name:"長度",field_type:"select"}];
const same = [{attrs:{"顏色":"黑","長度":"1m"},price:100,barcode:"",model_ids:[7]},
              {attrs:{"顏色":"黑","長度":"2m"},price:100,barcode:"",model_ids:[7]}];
out.lengthOnly = Array.from(logic.diffFieldNames(same, fields));
same[1].price = 200;
out.withPrice = Array.from(logic.diffFieldNames(same, fields));
done();
''')
        self.assertEqual(out["lengthOnly"], ["長度"])
        self.assertIn("__price", out["withPrice"])

    def test_partition_precheck_and_duplicate_reference_text(self):
        out = self._run(r'''
const logic = window.VariantBatchLogic;
const rows = [{draft_id:"d1", attrs:{}}, {draft_id:"d2", attrs:{}}, {draft_id:"d3", attrs:{}}];
const result = logic.partitionPrecheck(rows, [
  {draft_id:"d1", existing_duplicate:true, related_variant_id:9, errors:["已存在"]},
  {draft_id:"d2", existing_duplicate:false, errors:["必填規格未填"]},
  {draft_id:"d3", existing_duplicate:false, errors:[]},
]);
out.kept = result.kept.map(r => r.draft_id);
out.skipped = result.skipped.map(s => [s.row.draft_id, s.related_variant_id]);
out.errors = result.errorsByDraftId;
out.firstRef = logic.dupRefText({related_draft_id:"d1"}, rows);
out.removedRef = logic.dupRefText({related_draft_id:"gone"}, rows);
done();
''')
        self.assertEqual(out["kept"], ["d2", "d3"])
        self.assertEqual(out["skipped"], [["d1", 9]])
        self.assertEqual(out["errors"], {"d2": ["必填規格未填"]})
        self.assertEqual(out["firstRef"], "與第 1 筆重複")
        self.assertEqual(out["removedRef"], "與已移除的一筆重複")

    def test_add_draft_snapshots_input_independently(self):
        out = self._run(r'''
const s = mkState({ productId: 5, formalFields: [], modelMode: "hidden" });
s.input.attrs = { "顏色": "紅" };
s.addDraft();
s.input.attrs["顏色"] = "藍";           // 修改主輸入區
out.draftColor = s.drafts[0].attrs["顏色"];   // 既有 draft 不連動
out.draftCount = s.drafts.length;
out.draftId = s.drafts[0].draft_id;
out.inputKept = s.input.attrs["顏色"];        // 加入後保留輸入
done();
''')
        self.assertEqual(out["draftColor"], "紅")
        self.assertEqual(out["draftCount"], 1)
        self.assertTrue(out["draftId"])
        self.assertEqual(out["inputKept"], "藍")

    def test_remove_and_undo_draft(self):
        out = self._run(r'''
const s = mkState({ productId: 5, formalFields: [], modelMode: "hidden" });
s.input.attrs = { "顏色": "紅" }; s.addDraft();
s.input.attrs = { "顏色": "藍" }; s.addDraft();
s.removeDraft(0);
out.afterRemove = s.drafts.length;
s.undoDelete();
out.afterUndo = s.drafts.length;
out.firstColor = s.drafts[0].attrs["顏色"];
done();
''')
        self.assertEqual(out["afterRemove"], 1)
        self.assertEqual(out["afterUndo"], 2)
        self.assertEqual(out["firstColor"], "紅")

    def test_edit_popup_deep_copy_cancel_does_not_mutate(self):
        out = self._run(r'''
const s = mkState({ productId: 5, formalFields: [], modelMode: "hidden" });
s.input.attrs = { "顏色": "紅" }; s.addDraft();
s.openEdit(0);
s.editing.draft.attrs["顏色"] = "改動";
s.cancelEdit();
out.afterCancel = s.drafts[0].attrs["顏色"];   // 取消不影響原 draft
s.openEdit(0);
s.editing.draft.attrs["顏色"] = "綠";
s.applyEdit();
out.afterApply = s.drafts[0].attrs["顏色"];
out.sameId = s.drafts[0].draft_id;
done();
''')
        self.assertEqual(out["afterCancel"], "紅")
        self.assertEqual(out["afterApply"], "綠")

    def test_build_payload_barcode_and_store_mapping(self):
        out = self._run(r'''
const fields = [{field_id:1,name:"顏色",field_type:"select"},
                {field_id:2,name:"特性詞條",field_type:"tags"}];
const s = mkState({ fields, drafts: [
  {draft_id:"d1", attrs:{"顏色":"紅","特性詞條":"A, B"}, price:100, model_ids:[7], barcode:"F1", store:false},
  {draft_id:"d2", attrs:{"顏色":"藍"}, price:null, model_ids:[], barcode:"", store:true},
]});
out.payload = s.buildPayload();
done();
''')
        p = out["payload"]
        self.assertEqual(p[0]["barcodes"], [{"barcode": "F1", "source": "factory"}])
        self.assertEqual(p[0]["attributes"]["顏色"], "紅")
        self.assertEqual(p[0]["attributes"]["特性詞條"], ["A", "B"])
        self.assertEqual(p[1]["barcodes"], [{"source": "store"}])

    def test_commit_failure_maps_errors_by_draft_id_and_keeps_drafts(self):
        out = self._run(r'''
const fields = [{field_id:1,name:"顏色",field_type:"select"}];
const s = mkState({ productId: 5, fields, catId: 1,
  featureField: {field_id:2, name:"特性詞條"},
  drafts: [{draft_id:"d1", attrs:{"顏色":"紅"}, price:null, model_ids:[], barcode:"", store:false}] });
// 讓 stub 走失敗分支
const origBuild = s.buildPayload;
s.buildPayload = () => { const arr = origBuild(); arr.__fail = true; return arr; };
(async () => {
  await s.commitAll();
  out.errors = s.commitErrors["d1"];
  out.keptDrafts = s.drafts.length;
  done();
})();
''')
        self.assertEqual(out["errors"], ["規格重複"])
        self.assertEqual(out["keptDrafts"], 1)

    def test_tag_selector_add_remove_emits_comma_string(self):
        # opt-picker(multiple=true, asList=false)= 特性詞條/tags 模式:逗號字串
        out = self._run(r'''
function mkTag(model, usage) {
  const s = { $emit: (ev, val) => { s._emitted = val; }, modelValue: model,
              usage: usage || [], multiple: true, asList: false, modelIds: [] };
  for (const k of Object.keys(optPicker.methods)) s[k] = optPicker.methods[k].bind(s);
  for (const k of Object.keys(optPicker.computed))
    Object.defineProperty(s, k, { get: optPicker.computed[k].bind(s), configurable: true });
  Object.assign(s, optPicker.data());
  return s;
}
let s = mkTag("A", [{option_id:1,value:"B",active:true,usage_count:3,model_ids:[]}]);
s.add("B");
out.added = s._emitted;
s = mkTag("A, B", []);
s.remove("A");
out.removed = s._emitted;
done();
''')
        self.assertEqual(out["added"], "A, B")
        self.assertEqual(out["removed"], "B")

    def test_opt_picker_single_select_replaces_value(self):
        # opt-picker(multiple=false)= select 模式:再選即取代,emit 字串
        out = self._run(r'''
function mkSel(model, usage) {
  const s = { $emit: (ev, val) => { s._emitted = val; }, modelValue: model,
              usage: usage || [], multiple: false, asList: false, modelIds: [] };
  for (const k of Object.keys(optPicker.methods)) s[k] = optPicker.methods[k].bind(s);
  for (const k of Object.keys(optPicker.computed))
    Object.defineProperty(s, k, { get: optPicker.computed[k].bind(s), configurable: true });
  Object.assign(s, optPicker.data());
  return s;
}
let s = mkSel("紅", [{option_id:1,value:"藍",active:true,usage_count:3,model_ids:[]}]);
s.add("藍");
out.replaced = s._emitted;       // 單選取代 → "藍"
s = mkSel("紅", []);
s.remove("紅");
out.cleared = s._emitted;        // 移除 → ""
done();
''')
        self.assertEqual(out["replaced"], "藍")
        self.assertEqual(out["cleared"], "")

    def test_opt_picker_multi_emits_array(self):
        # opt-picker(multiple=true, asList=true)= multi 模式:陣列
        out = self._run(r'''
function mkMulti(model, usage) {
  const s = { $emit: (ev, val) => { s._emitted = val; }, modelValue: model,
              usage: usage || [], multiple: true, asList: true, modelIds: [] };
  for (const k of Object.keys(optPicker.methods)) s[k] = optPicker.methods[k].bind(s);
  for (const k of Object.keys(optPicker.computed))
    Object.defineProperty(s, k, { get: optPicker.computed[k].bind(s), configurable: true });
  Object.assign(s, optPicker.data());
  return s;
}
let s = mkMulti(["A"], [{option_id:1,value:"B",active:true,usage_count:1,model_ids:[]}]);
s.add("B");
out.added = s._emitted;          // ["A","B"]
done();
''')
        self.assertEqual(out["added"], ["A", "B"])

    def test_opt_picker_enter_consumes_event_and_selects_candidate_without_form_save(self):
        out = self._run(r'''
const s = { $emit: (ev, val) => { s._emitted = val; }, modelValue: "",
  usage: [{option_id:1,value:"軍規",active:true,usage_count:1,model_ids:[]}],
  multiple: true, asList: false, modelIds: [] };
for (const k of Object.keys(optPicker.methods)) s[k] = optPicker.methods[k].bind(s);
for (const k of Object.keys(optPicker.computed))
  Object.defineProperty(s, k, { get: optPicker.computed[k].bind(s), configurable: true });
Object.assign(s, optPicker.data());
s.query = "軍規";
let prevented=false, stopped=false, saves=0;
const event={preventDefault:()=>{prevented=true;},stopPropagation:()=>{stopped=true;}};
s.handleSearchEnter(event);
if (!prevented && !stopped) saves += 1;
out.prevented=prevented; out.stopped=stopped; out.saves=saves;
out.selected=s._emitted; out.query=s.query;
done();
''')
        self.assertTrue(out["prevented"])
        self.assertTrue(out["stopped"])
        self.assertEqual(out["saves"], 0)
        self.assertEqual(out["selected"], "軍規")
        self.assertEqual(out["query"], "")

    def test_opt_picker_front_row_uses_lead_values_and_hides_rest_in_more(self):
        # 前排＝服務層標記 lead 的值(該廠牌/產品曾出現過);其餘一律收進「更多…」,
        # 完全沒有 lead 時才退回種類次數前 8。
        out = self._run(r'''
function mkPick(usage) {
  const s = { $emit: () => {}, modelValue: "", usage, multiple: false,
              asList: false, modelIds: [] };
  for (const k of Object.keys(optPicker.methods)) s[k] = optPicker.methods[k].bind(s);
  for (const k of Object.keys(optPicker.computed))
    Object.defineProperty(s, k, { get: optPicker.computed[k].bind(s), configurable: true });
  Object.assign(s, optPicker.data());
  return s;
}
const row = (id, value, extra) => Object.assign(
  {option_id:id, value, active:true, model_ids:[], usage_count:0, lead:false,
   lead_count:0}, extra || {});
let s = mkPick([
  row(1, "皮套", {lead:true, lead_count:6, usage_count:9}),
  row(2, "SolidX", {usage_count:20}),
  row(3, "透明", {usage_count:5}),
]);
out.leadTop = s.topChips.map(o => o.value);
out.leadMore = s.moreChips.map(o => o.value);
out.leadCounts = s.topChips.map(o => s.countOf(o));
// 沒有任何 lead:退回種類次數前 8(此處只有 3 筆,全部進前排)
s = mkPick([row(1, "A", {usage_count:3}), row(2, "B", {usage_count:1})]);
out.fallbackTop = s.topChips.map(o => o.value);
out.fallbackMore = s.moreChips.map(o => o.value);
out.fallbackCounts = s.topChips.map(o => s.countOf(o));
done();
''')
        self.assertEqual(out["leadTop"], ["皮套"])
        self.assertEqual(out["leadMore"], ["SolidX", "透明"])
        self.assertEqual(out["leadCounts"], [6])
        self.assertEqual(out["fallbackTop"], ["A", "B"])
        self.assertEqual(out["fallbackMore"], [])
        self.assertEqual(out["fallbackCounts"], [3, 1])


if __name__ == "__main__":
    unittest.main()
