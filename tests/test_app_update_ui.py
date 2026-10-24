"""線上更新前端流程:詢問時機、鎖畫面、進度文字與失敗處理。"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "static" / "js" / "app_update.js"

PRELUDE = r'''
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
const context={window:{},console};vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1],"utf8"),context);
const U=context.window.PosAppUpdate;
const ASK="已有新版本 v0.1.2（目前 v0.1.1），是否立即更新？更新完成後程式會自動重新啟動。";
function make(script,extra){
  const log=[];
  const queue={};for(const[k,v]of Object.entries(script))queue[k]=[...v];
  const deps={
    api:{invoke:async a=>{log.push(a);const q=queue[a];
      const v=q&&q.length>1?q.shift():(q?q[0]:{busy:false});
      if(v instanceof Error)throw v;return v}},
    confirm:{ask:async(m,o)=>{log.push(["ask",m,o.confirmText,o.cancelText]);return deps.answer},
      notify:async m=>{log.push(["notify",m])}},
    lock:{_locked:false,lock(){log.push("lock")},unlock(){log.push("unlock")}},
    overlay:()=>{log.push("overlay");return{setText:t=>log.push(["text",t]),close:()=>log.push("close")}},
    wait:async()=>{log.push("wait");if(deps.onWait)deps.onWait()},
    checkout:{cart:[],submitting:false},labels:{rows:[],printing:false},receive:{rows:[],locked:false},
    hasModal:()=>false,answer:true};
  return Object.assign(deps,extra||{}),{deps,log};
}
const NEW={available:true,version:"0.1.2",current:"0.1.1"};
const flat=log=>log.map(x=>Array.isArray(x)?x[0]:x);
(async()=>{BODY})().catch(e=>{console.error(e);process.exitCode=1});
'''


@unittest.skipUnless(shutil.which("node"), "Node.js is required for app update UI tests")
class AppUpdateUiTests(unittest.TestCase):
    def _run(self, body):
        result = subprocess.run(["node", "-e", PRELUDE.replace("BODY", body), str(SCRIPT)],
                                cwd=ROOT, text=True, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_update_does_nothing(self):
        self._run('''const{deps,log}=make({"desktop.update.check":[{available:false}]});
await U.run(deps);assert.deepEqual(flat(log),["desktop.update.check"]);''')

    def test_check_error_is_quiet(self):
        self._run('''const{deps,log}=make({"desktop.update.check":[new Error("x")]});
await U.run(deps);assert.deepEqual(flat(log),["desktop.update.check"]);''')

    def test_later_does_nothing(self):
        self._run('''const{deps,log}=make({"desktop.update.check":[NEW]});deps.answer=false;
await U.run(deps);const f=flat(log);
assert.ok(f.includes("ask"));assert.ok(!f.includes("desktop.update.start"));assert.ok(!f.includes("lock"));''')

    def test_success_flow(self):
        self._run('''const{deps,log}=make({"desktop.update.check":[NEW],"desktop.update.start":[{state:"downloading"}],
"desktop.update.status":[{state:"downloading",got:0,total:100},{state:"downloading",got:45,total:100},{state:"done",got:100,total:100}],
"desktop.update.restart":[null]});
await U.run(deps);
const ask=log.find(x=>x[0]==="ask");assert.deepEqual(ask,["ask",ASK,"更新","稍後"]);
const f=flat(log);assert.ok(f.indexOf("desktop.update.start")<f.indexOf("lock"));
assert.ok(f.indexOf("lock")<f.indexOf("overlay"));
assert.deepEqual(log.filter(x=>x[0]==="text").map(x=>x[1]),["下載新版本中（0%）","下載新版本中（45%）","下載完成，正在重新啟動…"]);
assert.equal(f[f.length-1],"desktop.update.restart");assert.ok(!f.includes("unlock"));''')

    def test_failure_flow(self):
        self._run('''const{deps,log}=make({"desktop.update.check":[NEW],"desktop.update.start":[{}],
"desktop.update.status":[{state:"failed",got:0,total:0}]});
await U.run(deps);const f=flat(log);
assert.ok(f.includes("close"));assert.ok(f.includes("unlock"));
assert.deepEqual(log.find(x=>x[0]==="notify"),["notify",U.FAILED]);assert.ok(!f.includes("desktop.update.restart"));''')

    def test_start_error_notifies(self):
        self._run('''const msg="程式所在資料夾無法寫入，請將程式放在可寫入的資料夾，或向維護者索取新版本。";
const{deps,log}=make({"desktop.update.check":[NEW],"desktop.update.start":[new Error(msg)]});
await U.run(deps);assert.deepEqual(log.find(x=>x[0]==="notify"),["notify",msg]);assert.ok(!flat(log).includes("lock"));''')

    def test_waits_while_locked(self):
        self._run('''const{deps,log}=make({"desktop.update.check":[NEW]});deps.answer=false;
deps.lock._locked=true;deps.onWait=()=>{deps.lock._locked=false};
await U.run(deps);const f=flat(log);assert.ok(f.indexOf("wait")<f.indexOf("ask"));''')

    def test_waits_while_cart_or_labels_busy(self):
        self._run('''const cases=[d=>d.checkout.cart.push(1),d=>{d.checkout.submitting=true},d=>d.labels.rows.push(1),
d=>{d.labels.printing=true},d=>d.receive.rows.push(1),d=>{d.receive.locked=true},d=>{d.hasModal=()=>true},null];
for(const set of cases){
  const busy=set?[]:[{busy:true},{busy:false}];
  const{deps,log}=make({"desktop.update.check":[NEW],"desktop.update.busy":set?[{busy:false}]:busy});deps.answer=false;
  if(set){set(deps);deps.onWait=()=>{deps.checkout={cart:[],submitting:false};deps.labels={rows:[],printing:false};
    deps.receive={rows:[],locked:false};deps.hasModal=()=>false}}
  await U.run(deps);const f=flat(log);
  assert.ok(f.indexOf("wait")>=0&&f.indexOf("wait")<f.indexOf("ask"),String(set));
  assert.equal(f.filter(x=>x==="ask").length,1);
}''')

    def test_status_error_retries_then_fails(self):
        self._run('''let r=make({"desktop.update.check":[NEW],"desktop.update.start":[{}],
"desktop.update.status":[new Error("a"),new Error("b"),{state:"failed",got:0,total:0}]});
await U.run(r.deps);assert.equal(r.log.filter(x=>x==="desktop.update.status").length,3);
r=make({"desktop.update.check":[NEW],"desktop.update.start":[{}],"desktop.update.status":[new Error("a")]});
await U.run(r.deps);const f=flat(r.log);
assert.equal(f.filter(x=>x==="desktop.update.status").length,3);
assert.ok(f.includes("close"));assert.ok(f.includes("unlock"));assert.deepEqual(r.log.find(x=>x[0]==="notify"),["notify",U.FAILED]);''')

    def test_restart_error_unlocks(self):
        self._run('''const msg="新版本已安裝完成，但無法自動重新啟動，請關閉程式後重新開啟。";
const{deps,log}=make({"desktop.update.check":[NEW],"desktop.update.start":[{}],
"desktop.update.status":[{state:"done",got:1,total:1}],"desktop.update.restart":[new Error(msg)]});
await U.run(deps);const f=flat(log);assert.ok(f.includes("close"));assert.ok(f.includes("unlock"));
assert.deepEqual(log.find(x=>x[0]==="notify"),["notify",msg]);''')

    def test_swapping_state_text(self):
        self._run('''const{deps,log}=make({"desktop.update.check":[NEW],"desktop.update.start":[{}],
"desktop.update.status":[{state:"swapping",got:100,total:100},{state:"done",got:100,total:100}],"desktop.update.restart":[null]});
await U.run(deps);assert.ok(log.some(x=>x[0]==="text"&&x[1]==="下載新版本中（100%）"));
assert.ok(!flat(log).includes("unlock"));''')


if __name__ == "__main__":
    unittest.main()
