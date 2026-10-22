"""盤點詳細資料載入時不得先切入尚未有資料的畫面。"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class StocktakeLoadingTests(unittest.TestCase):
    def run_js(self, body):
        script = r'''
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
const context={window:{PosPages:{},PosEmployee:{id:1}},API:{},console};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1],"utf8"),context);
const page=context.window.PosPages["page-stocktake"],API=context.API;
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return{promise,resolve,reject}};
function mk(){const state={...page.data(),focuses:0,$refs:{scan:{focus(){state.focuses++}}},$nextTick:f=>f()};
for(const[name,method]of Object.entries(page.methods))state[name]=method.bind(state);return state}
(async()=>{BODY})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace("BODY", body)
        result = subprocess.run(["node", "-e", script, str(ROOT / "static/js/stocktake.js")],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_enter_keeps_list_until_detail_loaded_then_publishes_matching_session(self):
        self.run_js(r'''
const state=mk(),wait=deferred();let requested;
API.stocktakeDetail=id=>{requested=id;return wait.promise};
const pending=state.enter(7);
assert.equal(requested,7);assert.equal(state.current,null);assert.equal(state.detail,null);
assert.equal(state.focuses,0);
const detail={session_id:7,status:"open",items:[]};wait.resolve(detail);await pending;
assert.equal(state.current,7);assert.equal(state.detail,detail);assert.equal(state.focuses,1);
''')

    def test_failed_detail_load_preserves_existing_screen_and_focus(self):
        self.run_js(r'''
const state=mk();API.stocktakeDetail=async()=>{throw new Error("載入失敗")};
await assert.rejects(state.enter(7),/載入失敗/);
assert.equal(state.current,null);assert.equal(state.detail,null);assert.equal(state.focuses,0);
const previous={session_id:3,status:"closed",items:[]};state.current=3;state.detail=previous;
await assert.rejects(state.enter(8),/載入失敗/);
assert.equal(state.current,3);assert.equal(state.detail,previous);assert.equal(state.focuses,0);
''')
