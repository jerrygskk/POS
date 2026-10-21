"""標籤清單執行期、非同步競態與模板契約。"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


@unittest.skipUnless(shutil.which("node"), "Node.js is required for labels UI runtime tests")
class LabelsUiRuntimeTests(unittest.TestCase):
    def _run(self, body):
        script = r'''
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
const context={window:{PosPages:{}},API:{},Vue:{reactive:x=>x},console,
PosConfirm:{ask:async()=>true,notify:()=>{}}};vm.createContext(context);
for(const file of process.argv.slice(1))vm.runInContext(fs.readFileSync(file,"utf8"),context);
const page=context.window.PosPages["page-labels"],API=vm.runInContext("API",context);
const fresh=()=>({rows:[],selectedKey:null,previewUrl:"",printing:false,notice:""});
context.window.PosLabelQueue=fresh();
const errors=[],printed=[],notices=[];
const label=n=>({name:`虛構商品${n}`,specification:"黑",price:"$50",models:["虛構型號"]});
const item=n=>({key:`${n}|TL${n}`,variant_id:n,barcode:`TL${n}`,copies:1,label:label(n),status:"ready"});
const plain=x=>JSON.parse(JSON.stringify(x));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return{promise,resolve,reject}};
const tick=async()=>{for(let i=0;i<12;i++)await Promise.resolve()};
API.checkLabels=async rows=>({items:rows.map(r=>({...r,valid:true,label:label(r.variant_id)}))});
API.previewLabel=async(id,barcode)=>({image:`image:${id}|${barcode}`});
API.printBarcode=async(...args)=>{printed.push(args)};
context.PosConfirm.notify=text=>notices.push(text);
function mkState(extra){const s={showError:message=>errors.push(message),takePageContext:()=>null};
Object.assign(s,context.window.PosMixin.methods,page.data());
for(const[name,method]of Object.entries(page.methods))s[name]=method.bind(s);
return Object.assign(s,extra||{})}
(async()=>{BODY})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace("BODY", body)
        result = subprocess.run(
            ["node", "-e", script, str(STATIC / "js/api.js"), str(STATIC / "js/pos_shared.js"),
             str(STATIC / "js/labels.js")], cwd=ROOT, text=True, capture_output=True,
            encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_default_barcode_sources_and_binary_order(self):
        self._run(r'''
const s=mkState(),b=(source,...codes)=>codes.map(barcode=>({source,barcode}));
for(const[barcodes,want]of [[b("factory","F2"),"F2"],[b("store","TL2"),"TL2"],
[[...b("factory","000"),...b("store","TL2","TL10")],"TL10"],
[b("factory","a","Z","2"),"2"],[b("store","a","Z"),"Z"],[[],""]])
assert.equal(s.defaultBarcode({barcodes}),want);
assert.equal(s.defaultBarcode({}),"");
''')

    def test_scan_uses_exact_input_barcode(self):
        self._run(r'''
const checked=[];API.scanBarcode=async code=>{assert.equal(code,"471000");return{variant_id:1}};
API.checkLabels=async rows=>{checked.push(plain(rows));return{items:[{valid:true,label:label(1)}]}};
const s=mkState({query:" 471000 "});await s.submitInput();
assert.deepEqual(checked,[[{variant_id:1,barcode:"471000"}]]);
assert.equal(s.q.rows[0].barcode,"471000");assert.equal(s.query,"");assert.equal(s.searched,false);
''')

    def test_distinct_codes_and_duplicate_increments_without_clearing_status(self):
        self._run(r'''
const s=mkState();await s.addRow(1,"TL1");await s.addRow(1,"F1");await s.addRow(1,"TL1");
assert.deepEqual(plain(s.q.rows.map(r=>[r.key,r.copies])),[["1|TL1",2],["1|F1",1]]);
s.q.rows[0].status="verify";await s.addRow(1,"TL1");assert.equal(s.q.rows[0].status,"verify");
s.q.rows[0].status="invalid";await s.addRow(1,"TL1");assert.equal(s.q.rows[0].copies,3);
assert.equal(s.q.notice,"此條碼已失效，請先移除該列後重新加入。");
''')

    def test_not_found_search_keeps_factory_and_store(self):
        self._run(r'''
for(const status of[404,422]){API.scanBarcode=async()=>{throw Object.assign(new Error("missing"),{status})};
API.listCatalog=async params=>{assert.deepEqual(plain(params),{q:"虛構"});return[{name:"虛構商品",variants:[
{variant_id:1,barcodes:[{source:"factory",barcode:"F1"}]},
{variant_id:2,barcodes:[{source:"store",barcode:"TL2"}]},{variant_id:3,barcodes:[]}]}]};
const s=mkState({query:" 虛構 "});await s.submitInput();assert.deepEqual(plain(s.results.map(r=>r.variant_id)),[1,2]);
assert.equal(s.results[0].name,"虛構商品");assert.equal(s.searched,true)}
''')

    def test_empty_search_and_query_watch(self):
        self._run(r'''
API.scanBarcode=async()=>{throw Object.assign(new Error("missing"),{status:404})};API.listCatalog=async()=>[];
const s=mkState({query:"虛構"});assert.equal(s.searched,false);await s.submitInput();assert.equal(s.searched,true);
s.query="新";page.watch.query.call(s);assert.equal(s.searched,false);s.query=" ";await s.submitInput();assert.equal(s.results.length,0);
''')

    def test_scan_server_error_does_not_search(self):
        self._run(r'''
API.scanBarcode=async()=>{throw Object.assign(new Error("錯誤"),{status:500})};API.listCatalog=()=>assert.fail("search");
const s=mkState({query:"X"});await s.submitInput();assert.deepEqual(errors,["錯誤"]);assert.equal(s.q.rows.length,0);
''')

    def test_pick_uses_default_and_clears_search(self):
        self._run(r'''
const s=mkState({query:"虛構",results:[{}],searched:true});await s.pick({variant_id:1,
barcodes:[{source:"factory",barcode:"F1"},{source:"store",barcode:"TL1"}]});
assert.equal(s.q.rows[0].barcode,"TL1");assert.equal(s.query,"");assert.equal(s.searched,false);assert.equal(s.results.length,0);
''')

    def test_mount_context_remount_and_printing_notice(self):
        self._run(r'''
const variant={variant_id:1,barcodes:[{source:"factory",barcode:"F1"}]};
const s=mkState({takePageContext:()=>({variant})});await page.mounted.call(s);assert.equal(s.q.rows[0].barcode,"F1");
const next=mkState();await page.mounted.call(next);assert.equal(next.q,s.q);assert.ok(s.q.previewUrl);
s.q.printing=true;await s.addFromContext(variant);assert.equal(s.q.rows[0].copies,1);
assert.equal(s.q.notice,"標籤列印中，請稍候再加入。");s.q.printing=false;await s.addFromContext({variant_id:2});
assert.equal(errors.at(-1),"此子產品沒有條碼，無法列印標籤。");
''')

    def test_mount_selects_first_row_when_nothing_selected(self):
        self._run(r'''
const q=context.window.PosLabelQueue;
await context.window.PosLabelQueueAdd(5,"TL5",3);await context.window.PosLabelQueueAdd(6,"TL6",1);
assert.equal(q.selectedKey,null);
const s=mkState();await page.mounted.call(s);
assert.equal(q.selectedKey,"5|TL5");assert.equal(q.previewUrl,"image:5|TL5");
q.selectedKey="6|TL6";const again=mkState();await page.mounted.call(again);
assert.equal(q.selectedKey,"6|TL6");
q.rows=[];const empty=mkState();await page.mounted.call(empty);
assert.equal(q.selectedKey,null);assert.equal(q.previewUrl,"");
''')

    def test_all_mutation_entrypoints_guard_printing(self):
        self._run(r'''
const s=mkState({query:"X",results:[{}],searched:true});s.q.rows=[item(1)];s.q.rows[0].status="verify";s.q.printing=true;
const before=JSON.stringify(s.q);context.PosConfirm.ask=()=>assert.fail("confirm");
for(const name of["scanBarcode","checkLabels","printBarcode","listCatalog"])API[name]=()=>assert.fail(name);
await s.submitInput();await s.pick({variant_id:2});s.setCopies(s.q.rows[0],9);s.confirmCopies(s.q.rows[0]);
s.removeRow(s.q.rows[0]);await s.clearAll();await s.printAll();assert.equal(JSON.stringify(s.q),before);assert.equal(s.query,"X");
''')

    def test_all_success_prints_in_order_and_clears(self):
        self._run(r'''
const s=mkState();s.q.rows=[item(1),item(2)];s.q.rows[1].copies=3;await s.selectRow("1|TL1");await s.printAll();
assert.deepEqual(printed,[[1,"TL1",1],[2,"TL2",3]]);assert.equal(s.q.rows.length,0);assert.equal(s.q.printing,false);
assert.equal(s.q.selectedKey,null);assert.equal(s.q.previewUrl,"");assert.deepEqual(notices,["已列印 4 張標籤。"]);
''')

    def test_second_failure_keeps_remaining_rows(self):
        self._run(r'''
API.printBarcode=async(...args)=>{printed.push(args);if(args[0]===2)throw new Error("故障")};
const s=mkState();s.q.rows=[item(1),item(2),item(3)];await s.printAll();assert.deepEqual(printed,[[1,"TL1",1],[2,"TL2",1]]);
assert.deepEqual(plain(s.q.rows.map(r=>[r.variant_id,r.status])),[[2,"verify"],[3,"ready"]]);
assert.equal(s.q.printing,false);assert.equal(s.q.selectedKey,"2|TL2");assert.match(s.q.notice,/故障.*確認張數/);
''')

    def test_first_failure_confirm_without_edit_and_retry(self):
        self._run(r'''
API.printBarcode=async()=>{throw new Error("故障")};const s=mkState();s.q.rows=[item(1)];await s.printAll();
assert.equal(s.q.rows[0].status,"verify");s.confirmCopies(s.q.rows[0]);assert.equal(s.q.rows[0].status,"ready");assert.equal(s.q.notice,"");
API.printBarcode=async(...args)=>printed.push(args);await s.printAll();assert.deepEqual(printed,[[1,"TL1",1]]);
''')

    def test_copies_do_not_clear_verify_and_normalize(self):
        self._run(r'''
const s=mkState();s.q.rows=[item(1)];const row=s.q.rows[0];row.status="verify";
for(const[value,want]of[["3.8",3],[0,1],[-4,1],["bad",1],[Infinity,1]]){s.setCopies(row,value);assert.equal(row.copies,want);assert.equal(row.status,"verify")}
await s.printAll();assert.equal(printed.length,0);assert.match(s.q.notice,/確認張數/);
''')

    def test_failed_print_invalid_requires_removal(self):
        self._run(r'''
let count=0;API.checkLabels=async rows=>({items:rows.map(r=>({valid:++count!==3,label:label(r.variant_id)}))});
API.printBarcode=async()=>{throw new Error("失效")};const s=mkState();s.q.rows=[item(1),item(2)];await s.printAll();
assert.equal(s.q.rows[0].status,"invalid");s.confirmCopies(s.q.rows[0]);assert.equal(s.q.rows[0].status,"invalid");
API.printBarcode=async(...args)=>printed.push(args);await s.printAll();assert.equal(printed.length,0);
s.removeRow(s.q.rows[0]);await s.printAll();assert.deepEqual(printed,[[2,"TL2",1]]);
''')

    def test_changed_content_blocks_once_and_refreshes_preview(self):
        self._run(r'''
API.checkLabels=async rows=>({items:rows.map(r=>({valid:true,label:{...label(r.variant_id),price:"$99"}}))});
const s=mkState();s.q.rows=[item(1),item(2)];await s.selectRow("1|TL1");s.q.previewUrl="old";await s.printAll();await tick();
assert.equal(printed.length,0);assert.equal(s.q.rows[0].status,"changed");assert.equal(s.q.rows[0].label.price,"$99");
assert.equal(s.q.previewUrl,"image:1|TL1");await s.printAll();assert.equal(printed.length,2);assert.equal(s.q.rows.length,0);
''')

    def test_invalid_batch_sends_nothing(self):
        self._run(r'''
API.checkLabels=async()=>({items:[{valid:true,label:label(1)},{valid:false,label:null}]});
const s=mkState();s.q.rows=[item(1),item(2)];await s.printAll();assert.equal(printed.length,0);
assert.equal(s.q.rows[0].status,"ready");assert.equal(s.q.rows[1].status,"invalid");assert.equal(s.q.printing,false);
''')

    def test_double_print_while_pending_sends_once(self):
        self._run(r'''
const d=deferred();API.printBarcode=async(...args)=>{printed.push(args);await d.promise};const s=mkState();s.q.rows=[item(1)];
const pending=s.printAll();await tick();assert.equal(s.q.printing,true);await s.printAll();assert.equal(printed.length,1);
d.resolve();await pending;assert.equal(s.q.printing,false);
''')

    def test_remount_during_print_shares_lock(self):
        self._run(r'''
const d=deferred();API.printBarcode=async(...args)=>{printed.push(args);await d.promise};const s=mkState();s.q.rows=[item(1)];
const pending=s.printAll();await tick();const next=mkState();await page.mounted.call(next);assert.equal(next.q.printing,true);
await next.printAll();assert.equal(printed.length,1);d.resolve();await pending;assert.equal(next.q.rows.length,0);
''')

    def test_preview_latest_selection_wins(self):
        self._run(r'''
const d=deferred();API.previewLabel=async id=>id===1?d.promise:{image:"B"};const s=mkState();s.q.rows=[item(1),item(2)];
const first=s.selectRow("1|TL1");await s.selectRow("2|TL2");d.resolve({image:"A"});await first;assert.equal(s.q.previewUrl,"B");
''')

    def test_clear_cancel_leaves_queue(self):
        self._run(r'''
context.PosConfirm.ask=async message=>{assert.equal(message,"確定清空列印清單？");return false};
const s=mkState();s.q.rows=[item(1)];await s.clearAll();assert.equal(s.q.rows.length,1);
''')

    def test_old_search_does_not_replace_new_query(self):
        self._run(r'''
API.scanBarcode=async()=>{throw Object.assign(new Error("missing"),{status:404})};const d=deferred();API.listCatalog=()=>d.promise;
const s=mkState({query:"舊"});const pending=s.submitInput();await tick();s.query="新";page.watch.query.call(s);
d.resolve([{name:"舊",variants:[{variant_id:1,barcodes:[{source:"store",barcode:"TL1"}]}]}]);await pending;
assert.equal(s.results.length,0);assert.equal(s.searched,false);
''')

    def test_concurrent_adds_merge_same_key(self):
        self._run(r'''
const d=deferred();API.checkLabels=()=>d.promise;const s=mkState();const a=s.addRow(1,"TL1"),b=s.addRow(1,"TL1");
d.resolve({items:[{valid:true,label:label(1)}]});await Promise.all([a,b]);assert.equal(s.q.rows.length,1);assert.equal(s.q.rows[0].copies,2);
''')

    def test_pending_add_cannot_mutate_during_or_after_print(self):
        self._run(r'''
for(const finishFirst of[false,true]){context.window.PosLabelQueue=fresh();const s=mkState();s.q.rows=[item(1)];
const d=deferred(),printing=deferred();API.checkLabels=rows=>rows[0].variant_id===2?d.promise:Promise.resolve({items:[{valid:true,label:label(1)}]});
API.printBarcode=()=>printing.promise;const add=s.addRow(2,"TL2"),run=s.printAll();await tick();if(finishFirst){printing.resolve();await run}
d.resolve({items:[{valid:true,label:label(2)}]});await add;assert.equal(s.q.rows.some(r=>r.variant_id===2),false);printing.resolve();await run}
''')

    def test_clear_invalidates_pending_preview_and_add(self):
        self._run(r'''
const d=deferred(),a=deferred();API.previewLabel=()=>d.promise;API.checkLabels=()=>a.promise;const s=mkState();s.q.rows=[item(1)];
const preview=s.selectRow("1|TL1"),add=s.addRow(2,"TL2");await s.clearAll();d.resolve({image:"old"});a.resolve({items:[{valid:true,label:label(2)}]});
await Promise.all([preview,add]);assert.equal(s.q.previewUrl,"");assert.equal(s.q.rows.length,0);assert.equal(s.q.selectedKey,null);
''')

    def test_confirmation_rechecks_lock_and_status(self):
        self._run(r'''
const s=mkState();s.q.rows=[item(1)];let d=deferred();context.PosConfirm.ask=()=>d.promise;
const clear=s.clearAll();s.q.printing=true;d.resolve(true);await clear;assert.equal(s.q.rows.length,1);s.q.printing=false;
d=deferred();const print=s.printAll();s.q.rows[0].status="verify";d.resolve(true);await print;assert.equal(printed.length,0);
''')

    def test_invalid_add_and_failed_batch_check(self):
        self._run(r'''
API.checkLabels=async()=>({items:[{valid:false,label:null}]});const s=mkState();await s.addRow(1,"TL1");assert.equal(s.q.rows.length,0);
assert.equal(errors.at(-1),"此條碼已不屬於任何可列印的子產品。");s.q.rows=[item(1)];API.checkLabels=async()=>{throw new Error("檢查失敗")};
await s.printAll();assert.equal(s.q.printing,false);assert.equal(s.q.rows.length,1);assert.equal(printed.length,0);assert.equal(errors.at(-1),"檢查失敗");
''')

    def test_titles_row_text_same_label_and_status_text(self):
        self._run(r'''
const s=mkState(),row={name:"虛構商品",models:["虛構甲","虛構乙"],price:50,attributes:{顏色:"黑"},
barcodes:[{source:"factory",barcode:"F1"},{source:"store",barcode:"TL5"}]};
assert.equal(s.modelsTitle(row),"虛構甲、虛構乙");assert.equal(s.modelsTitle({}),null);
assert.equal(s.rowText(row),"虛構商品｜黑｜虛構甲、虛構乙｜$50｜TL5");
assert.equal(s.rowText({name:"虛構商品",attributes:{},price:null,barcodes:[]}),"虛構商品");assert.equal(s.sameLabel(label(1),label(1)),true);
for(const changes of[{name:"不同"},{specification:"不同"},{price:""},{models:[]},{models:["乙","甲"]}])assert.equal(s.sameLabel(label(1),{...label(1),...changes}),false);
assert.deepEqual(["ready","changed","verify","invalid"].map(status=>s.statusText({status})),
["","資料已更新，請確認預覽","請核對已印張數","條碼已失效，請移除後重新加入"]);
''')


class LabelsTemplateContractTests(unittest.TestCase):
    def test_queue_template_and_shared_state_contract(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        labels = html.split('<template id="tpl-labels">', 1)[1].split('</template>', 1)[0]
        for text in ['class="label-queue"', 'class="label-preview"', '查無此條碼或商品。',
                     '@change="setCopies(row, $event.target.value)"', ':title="modelsTitle(r)"',
                     ':disabled="q.printing"', '@click="selectRow(row.key)"', 'row.label.models']:
            self.assertIn(text, labels)
        self.assertNotIn('v-model.number="row.copies"', labels)
        self.assertNotIn('查無可列印的商品', labels)
        self.assertNotRegex(labels, r'v-if="[^"]*"[^>]*:key=')
        source = (STATIC / "js/labels.js").read_text(encoding="utf-8")
        self.assertIn('window.PosLabelQueue = Vue.reactive(', source)
        self.assertIn('inject: ["showError", "takePageContext"]', source)
        self.assertNotIn('localeCompare', source)
        self.assertNotIn('|| "尚無店內條碼"', source)


if __name__ == "__main__":
    unittest.main()
