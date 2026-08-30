# -*- coding: utf-8 -*-
"""候選選取器(opt-picker)的選取行為 Node 測試。"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class OptPickerSelectionTests(unittest.TestCase):
    def _run(self, body):
        script = r'''
const fs = require("fs"), vm = require("vm");
const context = { window: {}, console };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);   // optpicker.js
const component = context.window.PosComponents["opt-picker"];
// 以最小替身呼叫 methods:computed(selected/selectedKeys)在替身裡直接給值
function picker(multiple, query, selected) {
  const ctx = {
    multiple, asList: true, query, selected: selected || [], emitted: null,
    selectedKeys: new Set((selected || []).map(v => v.toLowerCase())),
    $emit: (name, value) => { ctx.emitted = value; },
    $refs: { search: { focused: false, focus() { this.focused = true; } } },
  };
  for (const [name, fn] of Object.entries(component.methods)) ctx[name] = fn.bind(ctx);
  return ctx;
}
context.window.CatalogFields = { filterOptions: (usage) => (usage || []).slice() };
const out = {};
''' + body + r'''
process.stdout.write(JSON.stringify(out));
'''
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "run.js"
            path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                ["node", str(path), str(STATIC / "js" / "optpicker.js")],
                capture_output=True, text=True, encoding="utf-8")
            if result.returncode != 0:
                self.fail(result.stderr)
            return json.loads(result.stdout)

    def test_multi_select_keeps_query_and_focus_single_clears(self):
        out = self._run(r'''
const multi = picker(true, "透");
multi.pickMatch({ value: "透明" });
out.multiQuery = multi.query;
out.multiEmitted = multi.emitted;
out.multiFocused = multi.$refs.search.focused;

const single = picker(false, "透");
single.pickMatch({ value: "透明" });
out.singleQuery = single.query;
out.singleEmitted = single.emitted;
''')
        # 複選:字留著,結果清單跟著留,焦點回搜尋框,可以接著挑同一批的下一個
        self.assertEqual(out["multiQuery"], "透")
        self.assertEqual(out["multiEmitted"], ["透明"])
        self.assertTrue(out["multiFocused"])
        # 單選:選完就結束,清空
        self.assertEqual(out["singleQuery"], "")
        self.assertEqual(out["singleEmitted"], "透明")

    def test_custom_value_added_from_search_clears_query(self):
        out = self._run(r'''
const p = picker(true, "自訂款式");
p.addFromSearch();
out.query = p.query;
out.emitted = p.emitted;
''')
        # 自己打的新值:字串本身已成為選中的值,留著會讓「新增」按鈕再冒出來
        self.assertEqual(out["query"], "")
        self.assertEqual(out["emitted"], ["自訂款式"])


    # 四個區塊(已選／候選／搜尋框／搜尋結果)不因選取而變動行數
    def test_selected_option_stays_in_candidate_list_and_toggles_off(self):
        out = self._run(r'''
const usage = [
  { option_id: 1, value: "黑色", active: true, lead: true, lead_count: 8, usage_count: 8 },
  { option_id: 2, value: "白色", active: true, lead: true, lead_count: 3, usage_count: 3 },
];
const ctx = picker(true, "", ["黑色"]);
ctx.usage = usage; ctx.modelIds = [];
const call = (name) => component.computed[name].call(ctx);
ctx.pool = call("pool");
out.available = call("available").map(o => o.value);
out.selectedFlags = out.available.map(v => ctx.isSelectedVal(v));
// 搜尋結果同樣保留已選的值(標成已選),行數不會因為選取而變少
ctx.query = "色";
out.matches = call("matches").map(o => o.value);
// 再點一次已選的候選 = 取消選取
ctx.toggle({ value: "黑色" });
out.afterToggle = ctx.emitted;
''')
        self.assertEqual(out["available"], ["黑色", "白色"])
        self.assertEqual(out["selectedFlags"], [True, False])
        self.assertEqual(out["matches"], ["黑色", "白色"])
        self.assertEqual(out["afterToggle"], [])


if __name__ == "__main__":
    unittest.main()
