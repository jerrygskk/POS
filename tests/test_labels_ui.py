"""標籤頁前端狀態與模板契約測試。"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


@unittest.skipUnless(
    shutil.which("node"), "Node.js is required for labels UI runtime tests"
)
class LabelsUiRuntimeTests(unittest.TestCase):
    def _run(self, body):
        script = r'''
const fs = require("fs"), vm = require("vm");
const context = {window:{PosPages:{}}, API:{}, console};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const page = context.window.PosPages["page-labels"];
function mkState(extra) {
  const s = {showError:() => {}, takePageContext:() => null};
  Object.assign(s, context.window.PosMixin.methods, page.data());
  for (const [name, method] of Object.entries(page.methods)) s[name] = method.bind(s);
  return Object.assign(s, extra || {});
}
const API = vm.runInContext("API", context), out = {};
function done() { process.stdout.write(JSON.stringify(out)); }
BODY
'''.replace("BODY", body)
        result = subprocess.run(
            ["node", "-e", script, str(STATIC / "js" / "api.js"),
             str(STATIC / "js" / "pos_shared.js"), str(STATIC / "js" / "labels.js")],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8",
        )
        if result.returncode != 0:
            self.fail(result.stderr)
        return json.loads(result.stdout)

    def test_search_only_keeps_store_barcodes_including_mixed_sources(self):
        out = self._run(r'''
API.listCatalog = async params => {
  out.params = params;
  return [{name:"虛構商品",variants:[
    {variant_id:1,barcodes:[{source:"factory",barcode:"123"}]},
    {variant_id:2,barcodes:[{source:"store",barcode:"TL2"}]},
    {variant_id:3,barcodes:[{source:"factory",barcode:"456"},{source:"store",barcode:"TL3"}]},
    {variant_id:4,barcodes:[]},
  ]}];
};
(async () => {
  const s = mkState({query:" 虛構 "});
  await s.search();
  out.ids = s.results.map(r => r.variant_id);
  out.names = s.results.map(r => r.name);
  s.query = " "; await s.search(); out.empty = s.results;
  done();
})();
''')
        self.assertEqual(out["params"], {"q": "虛構"})
        self.assertEqual(out["ids"], [2, 3])
        self.assertEqual(out["names"], ["虛構商品", "虛構商品"])
        self.assertEqual(out["empty"], [])

    def test_context_selects_variant_and_normal_mount_stays_empty(self):
        out = self._run(r'''
const v = {variant_id:3,name:"虛構商品",barcodes:[{source:"store",barcode:"TL3"}]};
const s = mkState({takePageContext:() => ({variant:v})});
page.mounted.call(s);
const plain = mkState(); page.mounted.call(plain);
out.selected = s.selected; out.copies = s.copies; out.results = s.results;
out.plain = plain.selected; out.plainCopies = plain.copies;
done();
''')
        self.assertEqual(out["selected"]["variant_id"], 3)
        self.assertEqual(out["copies"], 1)
        self.assertEqual(out["results"], [])
        self.assertIsNone(out["plain"])
        self.assertEqual(out["plainCopies"], 1)

    def test_empty_message_only_after_search_and_resets_on_selection_or_query(self):
        out = self._run(r'''
API.listCatalog = async () => [{name:"虛構商品",variants:[
  {variant_id:1,barcodes:[{source:"factory",barcode:"123"}]}]}];
(async () => {
  const s = mkState({query:"虛構"});
  out.beforeSearch = s.searched;
  await s.search(); out.afterSearch = s.searched; out.results = s.results;
  s.select({variant_id:2}); out.afterSelection = s.searched;
  await s.search();
  s.query = "虛構新字"; page.watch.query.call(s);
  out.afterQuery = s.searched;
  done();
})();
''')
        self.assertFalse(out["beforeSearch"])
        self.assertTrue(out["afterSearch"])
        self.assertEqual(out["results"], [])
        self.assertFalse(out["afterSelection"])
        self.assertFalse(out["afterQuery"])

    def test_old_search_does_not_show_empty_message_for_changed_query(self):
        out = self._run(r'''
let resolveSearch;
API.listCatalog = () => new Promise(resolve => { resolveSearch = resolve; });
(async () => {
  const s = mkState({query:"虛構舊字"});
  const pending = s.search();
  s.query = "虛構新字"; page.watch.query.call(s);
  out.afterQuery = s.searched;
  resolveSearch([]); await pending;
  out.afterOldSearch = s.searched;
  done();
})();
''')
        self.assertFalse(out["afterQuery"])
        self.assertFalse(out["afterOldSearch"])

    def test_titles_and_row_text_use_complete_models_and_catalog_attributes(self):
        out = self._run(r'''
const s = mkState();
const row = {name:"虛構商品",models:["虛構甲","虛構乙"],price:50,
  attributes:{顏色:"黑"},barcodes:[{source:"factory",barcode:"123"},{source:"store",barcode:"TL5"}]};
out.title = s.modelsTitle(row); out.noTitle = s.modelsTitle({models:[]});
out.missingTitle = s.modelsTitle({}); out.text = s.rowText(row);
out.emptyText = s.rowText({name:"虛構商品",attributes:{},price:null,barcodes:[]});
done();
''')
        self.assertEqual(out["title"], "虛構甲、虛構乙")
        self.assertIsNone(out["noTitle"])
        self.assertIsNone(out["missingTitle"])
        self.assertEqual(out["text"], "虛構商品｜黑｜虛構甲、虛構乙｜$50｜TL5")
        self.assertEqual(out["emptyText"], "虛構商品")


class LabelsTemplateContractTests(unittest.TestCase):
    def test_empty_message_and_complete_model_titles(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        labels = html.split('<template id="tpl-labels">', 1)[1].split(
            '<template id="tpl-receive">', 1)[0]
        self.assertIn('v-if="searched && !results.length" class="done-msg"', labels)
        self.assertIn("查無可列印的商品。標籤僅適用於已建立店內條碼的款式。", labels)
        self.assertRegex(labels, r'class="search-item"[^>]*:title="modelsTitle\(r\)"')
        self.assertIn('<div :title="modelsTitle(selected)">{{ rowText(selected) }}</div>', labels)
        source = (STATIC / "js" / "labels.js").read_text(encoding="utf-8")
        self.assertIn('inject: ["showError", "takePageContext"]', source)
        self.assertNotIn('|| "尚無店內條碼"', source)


if __name__ == "__main__":
    unittest.main()
