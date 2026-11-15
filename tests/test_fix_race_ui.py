"""前端等待回應期間被新操作搶先時,舊回應不得蓋掉畫面(盤點明細、銷售紀錄查詢)。"""
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def run_node(script, js_file):
    result = subprocess.run(["node", "-e", script, str(STATIC / "js" / js_file)],
                            cwd=ROOT, text=True, capture_output=True, check=True, encoding="utf-8")
    return json.loads(result.stdout)


class RaceUiTests(unittest.TestCase):
    def test_stale_stocktake_detail_does_not_overwrite_other_session(self):
        script = r'''
const fs = require("fs"), vm = require("vm");
let release;
const context = {window: {PosPages: {}}, API: {
  stocktakeDetail: sid => new Promise(r => { release = () => r({session_id: sid}); })}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const page = context.window.PosPages["page-stocktake"];
const state = {current: 1, detail: {session_id: 1}};
(async () => {
  const pending = page.methods.refreshDetail.call(state, 1);
  state.current = 2; state.detail = {session_id: 2};   // 等待期間切到 B 單
  release(); await pending;
  process.stdout.write(JSON.stringify(state.detail));
})().catch(e => { console.error(e); process.exit(1); });
'''
        self.assertEqual(run_node(script, "stocktake.js"), {"session_id": 2})

    def test_interleaved_sales_queries_keep_latest_list_summary_and_filters_together(self):
        script = r'''
const fs = require("fs"), vm = require("vm");
const waits = [];
const later = value => new Promise(r => waits.push(() => r(value)));
const context = {window: {PosPages: {}}, API: {
  listSales: f => later(["list-" + f.date_from]),
  salesSummary: f => later({tag: "sum-" + f.date_from})}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const page = context.window.PosPages["page-records"];
const state = {kind: "sales", payment: "", memberPhone: "", dateTo: "2026-11-08",
  collapseAll() {}, guard: async op => op()};
const flush = () => new Promise(r => setTimeout(r, 0));
(async () => {
  state.dateFrom = "A"; const qa = page.methods.reload.call(state); await flush();
  state.dateFrom = "B"; const qb = page.methods.reload.call(state); await flush();
  // 交錯放行:B 的列表先回、A 的列表與合計後回、最後 B 的合計
  waits[1](); await flush(); waits[0](); await flush();
  while (waits.length < 4) await flush();
  waits[2](); await flush(); waits[3](); await Promise.all([qa, qb]);
  process.stdout.write(JSON.stringify({sales: state.sales, summary: state.summary,
    query: state.salesQuery && state.salesQuery.date_from}));
})().catch(e => { console.error(e); process.exit(1); });
'''
        out = run_node(script, "records.js")
        self.assertEqual(out["sales"], ["list-B"])
        self.assertEqual(out["summary"], {"tag": "sum-B"})
        self.assertEqual(out["query"], "B")

    RELOAD_RACE = r'''
const fs = require("fs"), vm = require("vm");
const waits = [], errors = [];
const later = tag => new Promise((ok, fail) => waits.push(bad => bad ? fail(new Error("fail-" + tag)) : ok([tag])));
const context = {window: {PosPages: {}}, API: {
  dayCloseList: r => later(r.date_from), listReceipts: r => later(r.date_from),
  returnHistory: r => later(r.date_from), stocktakeHistory: r => later(r.date_from),
  listMembers: q => later(q)}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const page = context.window.PosPages["page-records"];
const [kind, field] = process.argv[2].split(":");
const state = {kind, isAdmin: true, returnKind: "normal", memberQuery: "", dateTo: "x", dc: {records: ["old"]},
  collapseAll() {}, showError(m) { errors.push(m); }, guard: async op => op()};
const ask = tag => { state.dateFrom = tag; state.memberQuery = tag; return page.methods.reload.call(state); };
const shown = () => field === "dc" ? state.dc.records : state[field];
(async () => {
  const out = {};
  const qa = ask("A");
  out.cleared = field === "dc" ? state.dc.records : null;   // 立帳:送出當下就清空
  const qb = ask("B");
  waits[1](); await qb; waits[0](); await qa;                 // 新查詢先回,舊查詢後回
  out.late = shown();
  const qc = ask("C"), qd = ask("D");
  waits[3](); await qd; waits[2](true); await qc;             // 舊查詢後來才失敗
  out.lateError = shown(); out.staleErrors = errors.slice();
  const qe = ask("E"); waits[4](true); await qe;              // 最新查詢失敗才提示
  out.errors = errors;
  process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
'''

    def test_late_reply_of_older_query_never_overwrites_or_reports(self):
        for kind, field in (("dayclose", "dc"), ("receive", "receipts"), ("returns", "returns"),
                            ("stocktake", "stocktakes"), ("members", "members")):
            with self.subTest(kind=kind):
                result = subprocess.run(["node", "-e", self.RELOAD_RACE, str(STATIC / "js" / "records.js"), f"{kind}:{field}"],
                                        cwd=ROOT, text=True, capture_output=True, check=True, encoding="utf-8")
                out = json.loads(result.stdout)
                self.assertEqual(out["late"], ["B"])
                self.assertEqual(out["lateError"], ["D"])
                self.assertEqual(out["staleErrors"], [])
                self.assertEqual(out["errors"], ["fail-E"])
                if kind == "dayclose":
                    self.assertEqual(out["cleared"], [])

    def test_switching_kind_discards_pending_query(self):
        script = r'''
const fs = require("fs"), vm = require("vm");
let release;
const context = {window: {PosPages: {}}, API: {
  listReceipts: () => new Promise(r => { release = () => r(["late"]); }),
  stocktakeHistory: async () => ["take"]}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const page = context.window.PosPages["page-records"];
const state = {kind: "receive", receipts: ["shown"], collapseAll() {}, showError() {}};
state.reload = page.methods.reload; state.dcLoad = async () => {};
(async () => {
  const pending = page.methods.reload.call(state);
  await page.methods.enterKind.call(state, "stocktake");
  release(); await pending;
  process.stdout.write(JSON.stringify({receipts: state.receipts, stocktakes: state.stocktakes}));
})().catch(e => { console.error(e); process.exit(1); });
'''
        self.assertEqual(run_node(script, "records.js"), {"receipts": ["shown"], "stocktakes": ["take"]})


if __name__ == "__main__":
    unittest.main()
