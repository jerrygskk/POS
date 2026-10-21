"""進貨頁「加入列印清單」執行期與模板契約。"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


@unittest.skipUnless(shutil.which("node"), "Node.js is required for receive UI runtime tests")
class ReceiveLabelQueueRuntimeTests(unittest.TestCase):
    def _run(self, body):
        script = r'''
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
const context={window:{PosPages:{}},API:{},Vue:{reactive:x=>x},console,
PosConfirm:{ask:async()=>true,notify:()=>{}}};vm.createContext(context);
for(const file of process.argv.slice(1))vm.runInContext(fs.readFileSync(file,"utf8"),context);
const page=context.window.PosPages["page-receive"],API=vm.runInContext("API",context);
context.window.PosLabelQueue={rows:[],selectedKey:null,previewUrl:"",printing:false,notice:""};
const q=context.window.PosLabelQueue,errors=[],checked=[];
const label=n=>({name:`虛構商品${n}`,specification:"黑",price:"$50",models:[]});
const plain=x=>JSON.parse(JSON.stringify(x));
const deferred=()=>{let resolve;const promise=new Promise(a=>{resolve=a});return{promise,resolve}};
API.scanBarcode=async code=>({variant_id:7,name:"虛構商品7",attributes:{},stock:0});
API.receiveStock=async p=>({stock:p.qty});
API.checkLabels=async rows=>{checked.push(plain(rows));
  return{items:rows.map(r=>({...r,valid:true,label:label(r.variant_id)}))}};
function mkState(){const s={showError:m=>errors.push(m),goPage:()=>{},$refs:{scan:{focus(){}}}};
Object.assign(s,context.window.PosMixin.methods,page.data());
for(const[name,method]of Object.entries(page.methods))s[name]=method.bind(s);
return s}
async function received(s,code,...qtys){s.scanCode=code;await s.onScan();
  for(const n of qtys){s.qty=n;await s.doReceive();}}
(async()=>{BODY})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace("BODY", body)
        result = subprocess.run(
            ["node", "-e", script, str(STATIC / "js/api.js"), str(STATIC / "js/pos_shared.js"),
             str(STATIC / "js/labels.js"), str(STATIC / "js/receive.js")], cwd=ROOT, text=True,
            capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_button_appears_only_after_receive_and_accumulates(self):
        self._run(r'''
const s=mkState();await received(s,"4710000000015");
assert.equal(s.labelQty,0);
s.qty=5;await s.doReceive();s.qty=3;await s.doReceive();
assert.equal(s.labelQty,8);assert.equal(s.qty,1);
''')

    def test_adds_scanned_code_with_received_quantity(self):
        self._run(r'''
const s=mkState();await received(s," FACTORY-1 ",4);
await s.addToLabels();
assert.deepEqual(checked,[[{variant_id:7,barcode:"FACTORY-1"}]]);
assert.equal(q.rows.length,1);assert.equal(q.rows[0].barcode,"FACTORY-1");
assert.equal(q.rows[0].copies,4);assert.equal(q.rows[0].status,"ready");
assert.equal(s.labelQty,0);assert.equal(s.labelMsg,"已加入列印清單 4 張。");
assert.deepEqual(errors,[]);
''')

    def test_same_code_merges_copies_and_new_scan_resets(self):
        self._run(r'''
const s=mkState();await received(s,"TL7",2);await s.addToLabels();
await received(s,"TL7",3);await s.addToLabels();
assert.equal(q.rows.length,1);assert.equal(q.rows[0].copies,5);assert.equal(checked.length,1);
s.qty=1;await s.doReceive();assert.equal(s.labelQty,1);
await received(s,"OTHER");assert.equal(s.labelQty,0);assert.equal(s.labelMsg,"");
assert.equal(s.scannedCode,"OTHER");
''')

    def test_printing_keeps_quantity_for_retry(self):
        self._run(r'''
const s=mkState();await received(s,"TL7",2);q.printing=true;
await s.addToLabels();
assert.equal(q.rows.length,0);assert.equal(s.labelQty,2);assert.equal(checked.length,0);
assert.deepEqual(errors,["標籤列印中，請稍候再加入列印清單。"]);
q.printing=false;await s.addToLabels();assert.equal(q.rows[0].copies,2);
''')

    def test_invalid_and_blocked_rows_are_not_added(self):
        self._run(r'''
const s=mkState();await received(s,"TL7",2);
API.checkLabels=async rows=>({items:rows.map(r=>({...r,valid:false,label:null}))});
await s.addToLabels();
assert.equal(q.rows.length,0);assert.equal(s.labelQty,2);
q.rows.push({key:"7|TL7",variant_id:7,barcode:"TL7",copies:1,label:label(7),status:"invalid"});
await s.addToLabels();
assert.equal(q.rows[0].copies,1);assert.equal(s.labelQty,2);
assert.deepEqual(errors,["此條碼已不屬於任何可列印的子產品。",
  "此條碼在列印清單中已失效，請先至標籤列印頁移除該列。"]);
''')

    def test_api_failure_keeps_quantity(self):
        self._run(r'''
const s=mkState();await received(s,"TL7",2);
API.checkLabels=async()=>{throw new Error("虛構錯誤")};
await s.addToLabels();
assert.equal(s.labelQty,2);assert.equal(q.rows.length,0);assert.deepEqual(errors,["虛構錯誤"]);
assert.equal(s.labelBusy,false);
''')

    def test_double_click_adds_once(self):
        self._run(r'''
const s=mkState();await received(s,"TL7",2);const d=deferred();
API.checkLabels=async rows=>{checked.push(plain(rows));await d.promise;
  return{items:rows.map(r=>({...r,valid:true,label:label(7)}))}};
const first=s.addToLabels();await s.addToLabels();d.resolve();await first;
assert.equal(checked.length,1);assert.equal(q.rows.length,1);assert.equal(q.rows[0].copies,2);
''')

    def test_not_found_scan_clears_pending_quantity(self):
        self._run(r'''
const s=mkState();await received(s,"TL7",2);
API.scanBarcode=async()=>{const e=new Error("找不到此條碼");e.status=404;throw e};
s.scanCode="NONE";await s.onScan();
assert.equal(s.hit,null);assert.equal(s.labelQty,0);assert.equal(s.scannedCode,"");
''')

    def test_cancelled_add_keeps_quantity(self):
        self._run(r'''
const s=mkState();await received(s,"TL7",2);const d=deferred();
API.checkLabels=async rows=>{await d.promise;return{items:rows.map(r=>({...r,valid:true,label:label(7)}))}};
const pending=s.addToLabels();q.printing=true;d.resolve();await pending;q.printing=false;
assert.equal(q.rows.length,0);assert.equal(s.labelQty,2);
assert.deepEqual(errors,["加入已取消，請重新加入列印清單。"]);
''')

    def test_slow_receive_does_not_count_toward_next_item(self):
        self._run(r'''
const s=mkState();await received(s,"CODE-A");const d=deferred();
API.receiveStock=async p=>{await d.promise;return{stock:p.qty}};
s.qty=5;const pending=s.doReceive();
API.scanBarcode=async()=>({variant_id:8,name:"虛構商品8",attributes:{},stock:0});
s.scanCode="CODE-B";await s.onScan();s.qty=2;
d.resolve();await pending;
assert.equal(s.hit.variant_id,8);assert.equal(s.labelQty,0);assert.equal(s.qty,2);
assert.equal(s.scannedCode,"CODE-B");
''')

    def test_receive_during_pending_add_keeps_new_quantity(self):
        self._run(r'''
const s=mkState();await received(s,"TL7",5);const d=deferred();
API.checkLabels=async rows=>{await d.promise;return{items:rows.map(r=>({...r,valid:true,label:label(7)}))}};
const pending=s.addToLabels();s.qty=3;await s.doReceive();
d.resolve();await pending;
assert.equal(q.rows[0].copies,5);assert.equal(s.labelQty,3);
assert.equal(s.labelMsg,"已加入列印清單 5 張。");
''')

    def test_switching_item_during_pending_add_leaves_new_item_alone(self):
        self._run(r'''
const s=mkState();await received(s,"CODE-A",5);const d=deferred();
API.checkLabels=async rows=>{checked.push(plain(rows));await d.promise;
  return{items:rows.map(r=>({...r,valid:true,label:label(r.variant_id)}))}};
const pending=s.addToLabels();
API.scanBarcode=async()=>({variant_id:8,name:"虛構商品8",attributes:{},stock:0});
s.scanCode="CODE-B";await s.onScan();s.qty=2;await s.doReceive();
d.resolve();await pending;
assert.deepEqual(checked,[[{variant_id:7,barcode:"CODE-A"}]]);
assert.equal(q.rows[0].key,"7|CODE-A");assert.equal(q.rows[0].copies,5);
assert.equal(s.labelQty,2);assert.equal(s.labelMsg,"");
''')

    def test_rescanning_same_code_keeps_accumulated_quantity(self):
        self._run(r'''
const s=mkState();await received(s,"TL7",5);await received(s,"TL7",3);
assert.equal(s.labelQty,8);
await received(s,"OTHER-CODE");assert.equal(s.labelQty,0);
''')


class ReceiveTemplateContractTests(unittest.TestCase):
    def test_add_to_label_queue_button(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        receive = html.split('<template id="tpl-receive">', 1)[1].split("</template>", 1)[0]
        self.assertIn('<button v-if="labelQty" :disabled="labelBusy" @click="addToLabels">'
                      "加入列印清單（{{ labelQty }} 張）</button>", receive)
        self.assertIn('<span v-if="labelMsg">{{ labelMsg }}</span>', receive)
        source = (STATIC / "js" / "receive.js").read_text(encoding="utf-8")
        self.assertIn("window.PosLabelQueueAdd(variantId, code, qty)", source)


if __name__ == "__main__":
    unittest.main()
