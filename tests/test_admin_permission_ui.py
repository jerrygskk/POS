"""正式 Vue prod：權限隱藏、直接呼叫與盤點三狀態。"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = (ROOT / "tests/permission_ui_host.js").read_text(encoding="utf-8")


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class AdminPermissionUiTests(unittest.TestCase):
    def run_js(self, body):
        result = subprocess.run(["node", "-e", HOST + "\n(async()=>{\n" + body +
            "\n})().catch(e=>{console.error(e.stack);process.exitCode=1});", str(ROOT)],
            cwd=ROOT, text=True, encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr[-8000:])

    def test_catalog_employee_dom_hides_all_editing_and_keeps_printing(self):
        self.run_js(r'''
load("static/js/catalog.js");
const p={product_id:1,category_id:1,name:"fictional product",active:1,variants:[
 {variant_id:1,active:1,models:[],stock:5,price:10,effective_price:10,attributes:{},barcodes:[{source:"store",barcode:"F1"}]},
 {variant_id:2,active:0,models:[],stock:0,price:null,effective_price:null,attributes:{},barcodes:[]}]};
const view=mount(context.window.PosPages["page-catalog"],template("catalog"),{products:[p]});
for(const label of["新增","新增款式","修改規格","停用","啟用","刪除","管理","編輯"])
 assert.ok(!button(view.dom,label),`員工仍看得到 ${label}`);
assert.ok(button(view.dom,"印標籤"));assert.match(text(view.dom),/顯示已停用/);
const total=calls.length;
API.invoke=async()=>{assert.fail("員工不可呼叫編輯 API")};
for(const method of["openAddVariant","openVariantEditor","editInSettings","toggleProductActive","deleteProduct","toggleVariantActive","deleteVariant"])
 await view.state[method](p,p.variants[0]);
assert.equal(errors.filter(x=>x==="需要管理者權限").length,7);assert.equal(calls.length,total);
view.state.pending=true;await tick();assert.equal(view.state.pending,false);
auth.isAdmin=true;await tick();assert.ok(button(view.dom,"新增"));assert.ok(button(view.dom,"編輯"));
API.listCatalog=async()=>[p];
view.state.pending=true;await tick();auth.isAdmin=false;await tick();assert.equal(view.state.pending,false);
view.app.unmount();
''')

    def test_stocktake_status_role_matrix_for_buttons_and_inputs(self):
        self.run_js(r'''
load("static/js/stocktake.js");
for(const isAdmin of[false,true])for(const status of["open","pending","closed"]){
 auth.isAdmin=isAdmin;
 const view=mount(context.window.PosPages["page-stocktake"],template("stocktake"),{current:1,
  detail:{session_id:1,status,items:[{variant_id:1,name:"fictional",counted_qty:0,system_qty:1,diff:-1,needs_recount:0}]}});
 const inputs=all(view.dom,n=>n.tag==="input");
 assert.equal(!!inputs[0].props.disabled,status!=="open");
 assert.equal(!!inputs[1].props.disabled,!(status==="open"||(status==="pending"&&isAdmin)));
 assert.equal(!!button(view.dom,"結案"),status==="open");
 assert.equal(!!button(view.dom,"核准"),status==="pending"&&isAdmin);
 assert.equal(!!button(view.dom,"抽回修改"),status==="pending");
 if(status==="pending")assert.match(text(view.dom),isAdmin?/實盤量僅供修正原盤點時的數量/:/等待管理者核准/);
 view.app.unmount();
 const list=mount(context.window.PosPages["page-stocktake"],template("stocktake"),{sessions:[{session_id:1,status,employee:1}]});
 assert.ok(button(list.dom,status==="open"?"續盤":status==="pending"&&isAdmin?"審核":"檢視"));
 assert.equal(!!button(list.dom,"開新盤點單").props.disabled,false);list.app.unmount();
}
''')

    def test_same_zero_recount_confirmation_sends_value_and_allows_submit(self):
        self.run_js(r'''
load("static/js/stocktake.js");
let detail={session_id:1,status:"open",items:[{variant_id:1,name:"fictional",system_qty:2,counted_qty:0,diff:-2,needs_recount:1}]};
const view=mount(context.window.PosPages["page-stocktake"],template("stocktake"),{current:1,detail});
API.setStocktakeCounted=async p=>{calls.push(["set",JSON.parse(JSON.stringify(p))]);detail={...detail,items:[{...detail.items[0],needs_recount:0}]};};
API.stocktakeDetail=async()=>detail;API.closeStocktake=async sid=>calls.push(["close",sid]);API.listStocktakes=async()=>[];
assert.ok(button(view.dom,"確認"));assert.match(text(view.dom),/有 1 項需重新清點/);
await click(button(view.dom,"確認"));await tick();
assert.deepEqual(calls[0],["set",{session_id:1,variant_id:1,counted_qty:0}]);
assert.ok(!button(view.dom,"確認"));assert.equal(view.state.detail.items[0].needs_recount,0);
await click(button(view.dom,"結案"));await tick();assert.ok(calls.some(c=>c[0]==="close"&&c[1]===1));view.app.unmount();
''')

    def test_navigation_hides_settings_redirects_on_logout_and_badge_is_admin_only(self):
        self.run_js(r'''
let rootDefinition;
context.document.addEventListener=(n,f)=>{events[n]=f;};
context.Vue={...Vue,createApp(def){rootDefinition=def;return{mixin(){},component(){},mount(){}};}};
load("static/js/app.js");events.DOMContentLoaded();context.Vue=Vue;
const nav=html.split("<nav")[1].split("</nav>")[0];
const view=mount(rootDefinition,"<nav"+nav+"</nav>");
assert.ok(!button(view.dom,"設定"));assert.ok(button(view.dom,"管理者登入"));
auth.pendingStocktakes=3;await tick();assert.equal(all(view.dom,n=>n.props.class==="badge").length,0);
auth.isAdmin=true;await tick();assert.ok(button(view.dom,"設定"));
const badges=all(view.dom,n=>n.props.class==="badge").map(text);assert.ok(badges.includes("3"));
auth.pendingStocktakes=0;await tick();assert.ok(button(view.dom,"盤點"));
assert.deepEqual(all(view.dom,n=>n.props.class==="badge").map(text),["管理者"]);
view.state.goPage("settings");await tick();assert.equal(view.state.page,"settings");
auth.isAdmin=false;await tick();assert.equal(view.state.page,"checkout");assert.ok(!button(view.dom,"設定"));
assert.equal(all(view.dom,n=>n.props.class==="badge").length,0);view.app.unmount();
''')

    def test_pending_approve_rechecks_role_after_confirmation(self):
        self.run_js(r'''
load("static/js/stocktake.js");auth.isAdmin=true;
const view=mount(context.window.PosPages["page-stocktake"],template("stocktake"),{current:1,detail:{session_id:1,status:"pending",items:[]}});
let finish;context.PosConfirm.ask=()=>new Promise(resolve=>{finish=resolve;});
API.stocktakeDetail=async()=>({session_id:1,status:"pending",items:[]});
API.approveStocktake=async()=>assert.fail("確認期間登出仍送出核准");
const pending=view.state.approve();auth.isAdmin=false;finish(true);await pending;await tick();
assert.ok(errors.includes("需要管理者權限"));view.app.unmount();
''')

    def test_auth_activity_throttles_in_employee_child_without_initializing_or_polling(self):
        self.run_js(r'''
let now=0,touches=0,statuses=0,pending=0;
context.Date={now:()=>now};
API.authTouch=async()=>{touches++;return{is_admin:false};};
API.authStatus=async()=>{statuses++;return{is_admin:false};};
API.pendingStocktakeCount=async()=>{pending++;return{count:9};};
load("static/js/auth.js");
assert.equal(statuses,0);assert.equal(pending,0);
events.pointerdown();events.keydown();assert.equal(touches,1);
now=59999;events.keydown();assert.equal(touches,1);
now=60000;events.keydown();assert.equal(touches,2);
await context.window.PosAuthOps.refresh();assert.equal(pending,0);
API.authStatus=async()=>({is_admin:true});await context.window.PosAuthOps.refresh();
assert.equal(context.window.PosAuth.pendingStocktakes,9);assert.equal(pending,1);
API.authStatus=async()=>({is_admin:false});await context.window.PosAuthOps.refresh();
assert.equal(context.window.PosAuth.pendingStocktakes,0);
''')

    def test_forbidden_response_refreshes_auth_and_preserves_error(self):
        self.run_js(r'''
context.window.pywebview={api:{invoke:async()=>({ok:false,error:{code:"forbidden",message:"需要管理者權限"}})}};
load("static/js/api.js");
await assert.rejects(vm.runInContext("API.updateProduct(1,{active:0})",context),e=>e.status===403&&e.message==="需要管理者權限");
await tick();assert.ok(calls.some(c=>c[0]==="refresh"));
''')

    def test_password_input_keeps_failed_dialog_for_retry_and_blocks_duplicate_submit(self):
        self.run_js(r'''
function element(tag){const n=node(tag);n.appendChild=child=>{child.parent=n;n.children.push(child);return child;};
 n.remove=()=>{if(n.parent)n.parent.children=n.parent.children.filter(x=>x!==n);};return n;}
context.document={body:element("body"),createElement:element,
 addEventListener(n,f){events[n]=f;},removeEventListener(n,f){if(events[n]===f)delete events[n];}};
load("static/js/confirm.js");
let resolveValidation,validations=0;
const pending=context.window.PosConfirm.input({title:"管理者登入",inputType:"password",
 validate:()=>{validations++;return new Promise(resolve=>{resolveValidation=resolve;});}});
const box=context.document.body.children[0].children[0],field=box.children.find(n=>n.tag==="input"),actions=box.children.at(-1);
const ok=actions.children.at(-1),warning=actions.children[0];field.value="wrong";
ok.listeners.click[0]();ok.listeners.click[0]();assert.equal(validations,1);assert.equal(ok.disabled,true);
resolveValidation("密碼錯誤");await tick();assert.equal(warning.textContent,"密碼錯誤");
assert.equal(context.document.body.children.length,1);assert.equal(ok.disabled,false);
field.value="Admin";ok.listeners.click[0]();resolveValidation(null);
assert.equal(await pending,"Admin");assert.equal(context.document.body.children.length,0);
''')

    def test_password_dialog_blocks_all_cancel_paths_until_validation_completes(self):
        self.run_js(r'''
function element(tag){const n=node(tag);n.appendChild=child=>{child.parent=n;n.children.push(child);return child;};
 n.remove=()=>{if(n.parent)n.parent.children=n.parent.children.filter(x=>x!==n);};return n;}
context.document={body:element("body"),createElement:element,
 addEventListener(n,f){events[n]=f;},removeEventListener(n,f){if(events[n]===f)delete events[n];}};
load("static/js/confirm.js");
let resolveValidation;
const pending=context.window.PosConfirm.input({validate:()=>new Promise(resolve=>{resolveValidation=resolve;})});
const overlay=context.document.body.children[0],box=overlay.children[0],actions=box.children.at(-1),ok=actions.children.at(-1),cancel=actions.children.at(-2);
ok.listeners.click[0]();assert.equal(cancel.disabled,true);
cancel.listeners.click[0]();events.keydown({key:"Escape",preventDefault(){}});overlay.listeners.mousedown[0]({target:overlay});
await tick();assert.equal(context.document.body.children.length,1);
resolveValidation("密碼錯誤");await tick();assert.equal(cancel.disabled,false);
events.keydown({key:"Escape",preventDefault(){}});assert.equal(await pending,null);
assert.equal(context.document.body.children.length,0);assert.equal(events.keydown,undefined);
''')

    def test_receive_and_records_employee_dom_and_direct_guards(self):
        self.run_js(r'''
load("static/js/receive.js");load("static/js/records.js");
context.window.PosReceiveDraft.notFound="MISSING";
const receive=mount(context.window.PosPages["page-receive"],template("receive"));
assert.ok(!button(receive.dom,"前往商品設定"));assert.match(text(receive.dom),/請管理者先建立產品與子產品/);
receive.state.goSettings();assert.ok(errors.includes("需要管理者權限"));assert.equal(calls.length,0);
const records=mount(context.window.PosPages["page-records"],template("records"));
assert.ok(!button(records.dom,"匯出 CSV"));API.exportSales=async()=>assert.fail("員工匯出 CSV");
await records.state.exportCsv();assert.equal(errors.filter(e=>e==="需要管理者權限").length,2);
auth.isAdmin=true;await tick();assert.ok(button(receive.dom,"前往商品設定"));assert.ok(button(records.dom,"匯出 CSV"));
receive.app.unmount();records.app.unmount();
''')

    def test_password_dialog_thrown_validation_restores_cancel_and_sync_success_resolves_once(self):
        self.run_js(r'''
function element(tag){const n=node(tag);n.appendChild=child=>{child.parent=n;n.children.push(child);return child;};
 n.remove=()=>{n.removes=(n.removes||0)+1;if(n.parent)n.parent.children=n.parent.children.filter(x=>x!==n);};return n;}
context.document={body:element("body"),createElement:element,
 addEventListener(n,f){events[n]=f;},removeEventListener(n,f){if(events[n]===f)delete events[n];}};
load("static/js/confirm.js");
let pending=context.window.PosConfirm.input({validate:async()=>{throw new Error("validation failed");}});
let overlay=context.document.body.children[0],actions=overlay.children[0].children.at(-1),ok=actions.children.at(-1),cancel=actions.children.at(-2);
ok.listeners.click[0]();await tick();assert.equal(ok.disabled,false);assert.equal(cancel.disabled,false);
assert.equal(actions.children[0].textContent,"validation failed");cancel.listeners.click[0]();assert.equal(await pending,null);
pending=context.window.PosConfirm.input({value:"Admin",validate:value=>value==="Admin"?null:"wrong"});
overlay=context.document.body.children[0];actions=overlay.children[0].children.at(-1);ok=actions.children.at(-1);
ok.listeners.click[0]();ok.listeners.click[0]();assert.equal(await pending,"Admin");assert.equal(overlay.removes,1);
''')
