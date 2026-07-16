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
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);  // api.js
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);  // variant_batch.js
const window = context.window;
const page = window.PosPages["page-variant-batch"];
const tagSel = window.PosComponents["tag-selector"];

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
            ["node", "-e", script, str(STATIC / "js" / "api.js"),
             str(STATIC / "js" / "variant_batch.js")],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8")
        if result.returncode != 0:
            self.fail(result.stderr)
        return json.loads(result.stdout)

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
        out = self._run(r'''
function mkTag(model, usage) {
  const s = { $emit: (ev, val) => { s._emitted = val; }, modelValue: model, usage: usage || [] };
  for (const k of Object.keys(tagSel.methods)) s[k] = tagSel.methods[k].bind(s);
  // computed 以 getter 掛上
  for (const k of Object.keys(tagSel.computed))
    Object.defineProperty(s, k, { get: tagSel.computed[k].bind(s), configurable: true });
  Object.assign(s, tagSel.data());
  return s;
}
let s = mkTag("A", [{option_id:1,value:"B",active:true,usage_count:3}]);
s.add("B");
out.added = s._emitted;
s = mkTag("A, B", []);
s.remove("A");
out.removed = s._emitted;
done();
''')
        self.assertEqual(out["added"], "A, B")
        self.assertEqual(out["removed"], "B")


if __name__ == "__main__":
    unittest.main()
