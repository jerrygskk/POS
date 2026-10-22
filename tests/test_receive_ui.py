"""批次進貨清單、非同步鎖定與模板契約。"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


@unittest.skipUnless(shutil.which("node"), "Node.js is required for receive UI runtime tests")
class ReceiveRuntimeTests(unittest.TestCase):
    def _run(self, body):
        script = r'''
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
const timers=[],errors=[],payloads=[],labelCalls=[];
const context={window:{PosPages:{},PosEmployee:{id:1}},API:{},
Vue:{reactive:x=>x},console,setTimeout:(f,ms)=>{timers.push([f,ms])},
PosConfirm:{ask:async()=>true}};
vm.createContext(context);
for(const file of process.argv.slice(1))vm.runInContext(fs.readFileSync(file,"utf8"),context);
const page=context.window.PosPages["page-receive"],API=context.API,confirm=context.PosConfirm;
const plain=x=>JSON.parse(JSON.stringify(x));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return{promise,resolve,reject}};
const tick=async()=>{for(let i=0;i<8;i++)await Promise.resolve()};
const hit=(n,stock=0)=>({code:"F"+n,data:{variant_id:n,name:"虛構商品"+n,
  attributes:{顏色:"黑"},attr_display:"黑",label_barcode:"TL"+n,stock}});
context.window.PosScan=async code=>hit(Number(code)||1);
API.receiveStock=async p=>{payloads.push(plain(p));return{items:p.items}};
context.window.PosLabelQueueAdd=async(...args)=>{labelCalls.push(args);return{result:"added"}};
function mk(){const s={showError:m=>errors.push(m),goPage:()=>{},
$refs:{scan:{focus(){}}}};Object.assign(s,page.data());
for(const[name,method]of Object.entries(page.methods))s[name]=method.bind(s);
return s}
async function scan(s,n){s.scanCode=String(n);await s.onScan()}
(async()=>{BODY})().catch(e=>{console.error(e);process.exitCode=1});
'''.replace("BODY", body)
        result = subprocess.run(
            ["node", "-e", script, str(STATIC / "js/receive.js")],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_repeat_and_rapid_scan_merge(self):
        self._run(r'''
const s=mk();await scan(s,1);await scan(s,2);
assert.equal(s.draft.rows[0].variant_id,2);assert.equal(s.draft.rows[0].qty,1);
context.window.PosScan=async()=>hit(1,9);await scan(s,1);
assert.deepEqual(plain(s.draft.rows.map(r=>[r.variant_id,r.qty,r.stock])),[[1,2,9],[2,1,0]]);
assert.equal(s.draft.highlightKey,1);
const d=deferred();context.window.PosScan=()=>d.promise;
s.scanCode="3";const a=s.onScan(),b=s.onScan();d.resolve(hit(3));await Promise.all([a,b]);
assert.equal(s.draft.rows.filter(r=>r.variant_id===3).length,1);assert.equal(s.draft.rows[0].qty,2);
assert.equal(page.computed.totalQty.call(s),5);
''')

    def test_cancel_not_found_and_server_error(self):
        self._run(r'''
const s=mk();await scan(s,1);context.window.PosScan=async()=>null;await scan(s," ");
assert.equal(s.scanCode,"");assert.equal(s.draft.rows.length,1);
context.window.PosScan=async()=>{throw Object.assign(new Error("missing"),{status:404})};
await scan(s,"NO");assert.equal(s.draft.notFound,"NO");assert.equal(s.scanCode,"");
context.window.PosScan=async()=>{throw Object.assign(new Error("server"),{status:500})};
await scan(s,"KEEP");assert.equal(s.scanCode,"KEEP");assert.deepEqual(errors,["server"]);
''')

    def test_quantity_label_difference_busy_and_removed_row(self):
        self._run(r'''
const s=mk();await scan(s,1);const row=s.draft.rows[0],event={target:{value:"4.8"}};
s.setQty(row,event);assert.equal(row.qty,4);assert.equal(event.target.value,4);
await s.addToLabels(row);assert.deepEqual(labelCalls,[[1,"TL1",4]]);assert.equal(row.labeled,4);
s.setQty(row,{target:{value:5}});await s.addToLabels(row);assert.equal(row.labeled,5);
row.qty=8;const d=deferred();context.window.PosLabelQueueAdd=async(...args)=>{labelCalls.push(args);return d.promise};
const first=s.addToLabels(row);await s.addToLabels(row);
assert.equal(labelCalls.length,3);s.removeRow(row);d.resolve({result:"added"});await first;
assert.equal(row.labeled,5);assert.equal(row.labelBusy,false);assert.equal(s.draft.rows.length,0);
''')

    def test_label_failure_messages(self):
        self._run(r'''
const s=mk();await scan(s,1);const row=s.draft.rows[0];
for(const result of ["printing","cancelled","blocked","invalid"]){
 context.window.PosLabelQueueAdd=async()=>({result});await s.addToLabels(row)}
assert.deepEqual(errors,["標籤列印中，請稍候再加入列印清單。",
"加入已取消，請重新加入列印清單。","此條碼在列印清單中已失效，請先至標籤列印頁移除該列。",
"此條碼已不屬於任何可列印的子產品。"]);assert.equal(row.labeled,0);
''')

    def test_clear_cancel_and_success(self):
        self._run(r'''
const s=mk();await scan(s,1);let message,options;
confirm.ask=async(m,o)=>{message=m;options=o;return false};await s.clearAll();
assert.equal(s.draft.rows.length,1);assert.equal(s.draft.locked,false);assert.equal(s.draft.generation,1);
assert.equal(message,"確定清空進貨清單？共 1 項。");assert.equal(options.danger,true);
confirm.ask=async()=>true;await s.clearAll();assert.equal(s.draft.rows.length,0);
assert.equal(s.draft.highlightKey,null);assert.equal(s.draft.locked,false);
''')

    def test_confirm_employee_qty_cancel_success_and_failure(self):
        self._run(r'''
const s=mk();await scan(s,1);await scan(s,2);await scan(s,2);
let asked=0;confirm.ask=async()=>{asked++;return false};
s.employee.id=null;await s.confirmReceive();assert.equal(asked,0);assert.equal(payloads.length,0);
assert.equal(errors.pop(),"請先選擇員工代號");s.employee.id=2;
s.draft.rows[0].qty=0;await s.confirmReceive();assert.equal(errors.pop(),"進貨數量必須為正整數");
s.draft.rows[0].qty=2;await s.confirmReceive();assert.equal(payloads.length,0);assert.equal(s.draft.locked,false);
confirm.ask=async(m,o)=>{assert.equal(m,"確認進貨？共 2 項、3 件，將寫入庫存。");assert.equal(o.title,"確認進貨");return true};
API.receiveStock=async()=>{throw new Error("write failed")};await s.confirmReceive();
assert.equal(s.draft.rows.length,2);assert.equal(s.draft.locked,false);assert.equal(errors.pop(),"write failed");
API.receiveStock=async p=>payloads.push(plain(p));await s.confirmReceive();
assert.deepEqual(payloads,[{employee:2,items:[{variant_id:2,qty:2},{variant_id:1,qty:1}]}]);
assert.equal(s.draft.rows.length,0);assert.equal(s.draft.doneMsg,"已入庫 2 項、共 3 件。");
assert.equal(s.draft.locked,false);
''')

    def test_confirmation_and_write_lock_every_mutation(self):
        self._run(r'''
const s=mk();await scan(s,1);const row=s.draft.rows[0],ask=deferred(),write=deferred();
let asks=0;confirm.ask=()=>{asks++;return ask.promise};
API.receiveStock=async p=>{payloads.push(plain(p));return write.promise};
async function blocked(){
 s.scanCode="2";await s.onScan();assert.equal(s.scanCode,"");
 assert.equal(errors.pop(),"進貨清單處理中，請稍候再掃描。");
 const event={target:{value:8}};s.setQty(row,event);assert.equal(event.target.value,1);
 s.removeRow(row);await s.addToLabels(row);await s.clearAll();await s.confirmReceive();
 assert.equal(row.qty,1);assert.equal(s.draft.rows.length,1);assert.equal(labelCalls.length,0);assert.equal(asks,1)}
const pending=s.confirmReceive();await blocked();assert.equal(payloads.length,0);
ask.resolve(true);await tick();assert.equal(payloads.length,1);await blocked();
write.resolve({});await pending;assert.equal(s.draft.locked,false);assert.equal(s.draft.rows.length,0);
''')

    def test_pending_scan_rejected_during_write(self):
        self._run(r'''
const s=mk();await scan(s,1);const scanWait=deferred(),write=deferred();
context.window.PosScan=()=>scanWait.promise;s.scanCode="2";const scanning=s.onScan();
API.receiveStock=async p=>{payloads.push(plain(p));return write.promise};
const receiving=s.confirmReceive();await tick();scanWait.resolve(hit(2));await scanning;
assert.equal(errors.pop(),"掃描期間進貨清單已變動，請重新掃描。");
assert.deepEqual(payloads,[{employee:1,items:[{variant_id:1,qty:1}]}]);
write.resolve({});await receiving;assert.equal(s.draft.rows.length,0);
''')

    def test_pending_scan_rejected_after_completed_receive_clear_or_cancel(self):
        self._run(r'''
for(const mode of ["receive","clear","cancel"]){
 const s=mk();context.window.PosScan=async()=>hit(1);await scan(s,1);
 const d=deferred();context.window.PosScan=()=>d.promise;s.scanCode="2";const scanning=s.onScan();
 if(mode==="clear")await s.clearAll();
 else{confirm.ask=async()=>mode!=="cancel";await s.confirmReceive()}
 assert.equal(s.draft.locked,false);const count=s.draft.rows.length;
 d.resolve(hit(2));await scanning;
 assert.equal(s.draft.rows.length,count);assert.equal(errors.pop(),"掃描期間進貨清單已變動，請重新掃描。");
 s.draft.rows=[];confirm.ask=async()=>true;
}
context.window.PosScan=async()=>hit(3);const s=mk();await scan(s,3);assert.equal(s.draft.rows.length,1);
''')

    def test_pre_send_snapshot_employee_and_rows_revalidated(self):
        self._run(r'''
const s=mk();await scan(s,1);
let d=deferred();confirm.ask=()=>d.promise;const first=s.confirmReceive();
s.employee.id=null;d.resolve(true);await first;assert.equal(payloads.length,0);
assert.equal(errors.pop(),"請先選擇員工代號");assert.equal(s.draft.locked,false);
s.employee.id=1;d=deferred();const second=s.confirmReceive();s.draft.rows[0].qty=8;
d.resolve(true);await second;assert.equal(payloads.length,0);
assert.equal(errors.pop(),"進貨清單已變動，請重新確認進貨。");assert.equal(s.draft.locked,false);
''')

    def test_state_survives_new_component_instance(self):
        self._run(r'''
const first=mk();await scan(first,1);const second=mk();
assert.equal(second.draft,first.draft);assert.equal(second.draft.rows[0].qty,1);
''')


class ReceiveTemplateTests(unittest.TestCase):
    def test_batch_template_and_guarded_quantity_contract(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        template = html.split('<template id="tpl-receive">')[1].split('</template>')[0]
        for text in ('btn-x', '確認進貨', '清空', 'receive-col-label', 'setQty', ':value="row.qty"'):
            self.assertIn(text, template)
        self.assertNotIn('!employee.id', template)
        self.assertNotIn('v-model.number="row.qty"', template)
        self.assertNotIn('v-model="row.qty"', template)
