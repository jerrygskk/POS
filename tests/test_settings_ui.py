"""設定頁前端邏輯 Node 煙霧測試(目前:型號排序在搜尋過濾下的位置回填)。"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class SettingsUiTests(unittest.TestCase):
    def _run(self, body):
        script = r'''
const fs = require("fs"), vm = require("vm");
const context = {
  window: { pywebview: { api: { invoke: async () => ({ ok: true, data: {} }) } } },
  PosConfirm: { ask: async () => true },
  console, setTimeout, clearTimeout,
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);  // api.js
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);  // settings.js
const window = context.window;
const page = window.PosPages["page-settings"];

function mkState(extra) {
  // guard／guardReload 由全域 mixin(pos_shared.js)提供,測試給等效替身
  const s = { showError: () => {},
              guard: async (fn) => { try { return await fn(); } catch (e) {} },
              guardReload: async (fn) => { try { await fn(); await s.reloadAll(); } catch (e) {} } };
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
             str(STATIC / "js" / "settings.js")],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8")
        if result.returncode != 0:
            self.fail(result.stderr)
        return json.loads(result.stdout)

    def _models(self):
        return """
const models = [
  { model_id: 1, brand_name: "iPhone", name: "17" },
  { model_id: 2, brand_name: "iPhone", name: "16" },
  { model_id: 3, brand_name: "iPhone", name: "11 Pro Max" },
  { model_id: 4, brand_name: "iPhone", name: "11 Pro" },
  { model_id: 5, brand_name: "iPhone", name: "11" },
  { model_id: 6, brand_name: "SAMSUNG", name: "S25" },
];
"""

    def test_full_list_drag_sends_that_brand_in_new_order(self):
        out = self._run(self._models() + r'''
const s = mkState({ models });
s.onModelSortPending("iPhone", [2, 1, 3, 4, 5]);
out.pending = s.pendingModelSort;
done();
''')
        self.assertEqual(out["pending"], {"iPhone": [2, 1, 3, 4, 5]})

    def test_sorting_is_disabled_while_filtering(self):
        """搜尋只看得到部分型號,此時一律不給拖、不給改序號——
        排序 API 會把送進來的 id 依序重編成 1..N,漏掉的型號號碼會全亂。"""
        html = (ROOT / "static/index.html").read_text(encoding="utf-8")
        # 只有排序被關掉;名稱、別名、系列在搜尋中照常可改
        self.assertIn(':disabled="!!modelQuery.trim()"', html)
        self.assertIn('<input class="name" v-model="m.name">', html)
        sortable = (STATIC / "js" / "sortable.js").read_text(encoding="utf-8")
        self.assertIn("disabled: { type: Boolean, default: false }", sortable)
        self.assertIn("if (this.disabled || src === dst", sortable)

    def test_delete_keeps_other_unsaved_edits(self):
        """刪除會重新載入清單。除了被刪掉的那筆之外,使用者還沒儲存的名稱修改與
        拖過的順序都要留著——重新載入把它們一起沖掉,等於幫使用者做了決定。"""
        out = self._run(self._models() + r'''
const s = mkState({ models: models.slice() });
s.snap = {};
for (const m of s.models) s.snap["models:" + m.model_id] = JSON.stringify(s._itemBody("models", m));
context.PosConfirm = window.PosConfirm = { ask: async () => true };
// 刪除第 3 筆(11 Pro Max);重新載入＝資料庫內容,未儲存的修改本來會被沖掉
s.reloadAll = async () => {
  s.models = models.filter(m => m.model_id !== 3)
    .map(m => Object.assign({}, m));
  s.snap = {};
  for (const m of s.models) s.snap["models:" + m.model_id] = JSON.stringify(s._itemBody("models", m));
};
(async () => {
  s.models[0].name = "17 改過";            // 未儲存的名稱修改
  s.onModelSortPending("iPhone", [2, 1, 3, 4, 5]);  // 未儲存的順序
  await s.deleteItem("models", s.models[2]);
  out.names = s.models.map(m => m.name);
  out.ids = s.models.map(m => m.model_id);
  out.pending = s.pendingModelSort;
  out.stillUnsaved = s.hasUnsaved("models");
  done();
})();
''')
        self.assertNotIn(3, out["ids"])              # 被刪的那筆不見了
        self.assertEqual(out["names"][0], "17 改過")  # 其餘未儲存的修改還在
        self.assertEqual(out["pending"], {"iPhone": [2, 1, 4, 5]})  # 順序剔除已刪 id
        self.assertTrue(out["stillUnsaved"])

    def test_unsaved_notice_covers_pending_model_order(self):
        out = self._run(self._models() + r'''
const s = mkState({ models });
// 比照載入後的狀態:先對現況取快照,才不會被當成「名稱改過」
s.snap = {};
for (const m of models) s.snap["models:" + m.model_id] = JSON.stringify(s._itemBody("models", m));
out.before = s.hasUnsaved("models");
s.onModelSortPending("iPhone", [2, 1, 3, 4, 5]);
out.after = s.hasUnsaved("models");
done();
''')
        self.assertFalse(out["before"])
        self.assertTrue(out["after"])


if __name__ == "__main__":
    unittest.main()
