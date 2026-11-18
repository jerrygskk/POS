"""報損框重新掃描:查詢失敗或未完成時,不得扣到上一件商品。"""
import shutil
import subprocess
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "static" / "js" / "stockloss.js"


@unittest.skipUnless(shutil.which("node"), "Node.js is required for stock-loss UI runtime tests")
class StockLossRescanTests(unittest.TestCase):
    def _run(self, body):
        script = r'''
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
const sent=[],context={window:{PosComponents:{},PosAuth:{isAdmin:true}},console,
  API:{createStockLoss:async p=>{sent.push(p)}},PosConfirm:{notify:async()=>{}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1],"utf8"),context);
const comp=context.window.PosComponents["stock-loss"];
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return{promise,resolve,reject}};
const tick=async()=>{for(let i=0;i<8;i++)await Promise.resolve()};
const A={variant_id:1,name:"A",stock:5},B={variant_id:2,name:"B",stock:5};
function mk(){const s={$refs:{},$nextTick:()=>{},$emit:()=>{}};Object.assign(s,comp.data());
for(const[name,fn]of Object.entries(comp.methods))s[name]=fn.bind(s);return s}
async function scan(s,code){s.code=code;return s.scan()}
async function withA(){const s=mk();context.window.PosScan=async()=>({data:A});await scan(s,"A");s.qty=2;s.reason="摔壞";return s}
(async()=>{BODY})().catch(e=>{console.error(e);process.exitCode=1});
'''.replace("BODY", body)
        result = subprocess.run(["node", "-e", script, str(SOURCE)], text=True, capture_output=True,
                                encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_rescan_cannot_submit_previous_item(self):
        self._run(r'''
const s=await withA();context.window.PosScan=async()=>{throw new Error("查無條碼")};
await scan(s,"B");s.reason="摔壞";await s.submit();
assert.equal(s.item,null);assert.match(s.error,/查無條碼/);assert.equal(sent.length,0);
''')

    def test_pending_rescan_cannot_submit_previous_item(self):
        self._run(r'''
const s=await withA(),wait=deferred();context.window.PosScan=()=>wait.promise;
const pending=scan(s,"B");await tick();s.reason="摔壞";await s.submit();
assert.equal(s.item,null);assert.equal(sent.length,0);
wait.resolve({data:B});await pending;assert.equal(s.item.variant_id,2);
''')

    def test_late_response_cannot_replace_newer_scan(self):
        self._run(r'''
const s=mk(),first=deferred(),second=deferred();
context.window.PosScan=code=>code==="A"?first.promise:second.promise;
const a=scan(s,"A"),b=scan(s,"B");second.resolve({data:B});await b;
first.resolve({data:A});await a;assert.equal(s.item.variant_id,2);
''')
