"""退貨單掃描:等待期間換單時，不得寫入或改動另一張單的畫面。"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


@unittest.skipUnless(shutil.which("node"), "Node.js is required for returns UI runtime tests")
class ReturnsScanRuntimeTests(unittest.TestCase):
    def _run(self, body):
        script = r'''
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
const errors=[],scans=[],details=[];
const context={window:{PosPages:{},PosEmployee:{id:1},PosAuth:{isAdmin:false}},API:{},console};
vm.createContext(context);
for(const file of process.argv.slice(1))vm.runInContext(fs.readFileSync(file,"utf8"),context);
const page=context.window.PosPages["page-returns"],API=context.API;
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return{promise,resolve,reject}};
const tick=async()=>{for(let i=0;i<8;i++)await Promise.resolve()};
const card=rid=>({return_id:rid,status:"open",items:[]});
API.returnScan=async(...args)=>{scans.push(args)};
API.returnDetail=async rid=>{details.push(rid);return card(rid)};
context.window.PosScan=async code=>({code,data:{variant_id:7}});
function mk(){const s={showError:m=>errors.push(m),$refs:{},$nextTick:()=>{},
 async guard(fn){try{return await fn()}catch(e){this.showError(e.message)}}};
Object.assign(s,page.data());
for(const[name,fn]of Object.entries(page.computed))Object.defineProperty(s,name,{get:fn.bind(s)});
for(const[name,method]of Object.entries(page.methods))s[name]=method.bind(s);
s.current=1;s.detail=card(1);return s}
function switchTo(s,rid){s.current=rid;s.detail=card(rid);s.scanCode="B 單輸入"}
(async()=>{BODY})().catch(e=>{console.error(e);process.exitCode=1});
'''.replace("BODY", body)
        result = subprocess.run(
            ["node", "-e", script, str(STATIC / "js/returns.js")],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_scan_writes_to_same_card(self):
        self._run(r'''
const s=mk();s.scanCode="F7";await s.onScan();
assert.deepEqual(scans,[[1,7,1]]);assert.deepEqual(details,[1]);assert.equal(s.scanCode,"");
''')

    def test_switch_during_lookup_does_not_write(self):
        self._run(r'''
const s=mk(),d=deferred();context.window.PosScan=()=>d.promise;
s.scanCode="F7";const scanning=s.onScan();switchTo(s,2);
d.resolve({code:"F7",data:{variant_id:7}});await scanning;
assert.deepEqual(scans,[]);assert.deepEqual(details,[]);
assert.equal(s.detail.return_id,2);assert.equal(s.scanCode,"B 單輸入");
''')

    def test_switch_during_write_leaves_new_card_alone(self):
        self._run(r'''
const s=mk(),d=deferred();API.returnScan=(...args)=>{scans.push(args);return d.promise};
s.scanCode="F7";const scanning=s.onScan();await tick();switchTo(s,2);
d.resolve();await scanning;
assert.deepEqual(scans,[[1,7,1]]);assert.deepEqual(details,[]);
assert.equal(s.detail.return_id,2);assert.equal(s.scanCode,"B 單輸入");
''')

    def test_switch_during_refresh_keeps_new_detail(self):
        self._run(r'''
const s=mk(),d=deferred();API.returnDetail=rid=>{details.push(rid);return d.promise};
s.scanCode="F7";const scanning=s.onScan();await tick();switchTo(s,2);
d.resolve(card(1));await scanning;
assert.deepEqual(details,[1]);assert.equal(s.detail.return_id,2);assert.equal(s.scanCode,"B 單輸入");
''')


if __name__ == "__main__":
    unittest.main()
