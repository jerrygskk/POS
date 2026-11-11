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
 {variant_id:1,active:1,models:[],stock:5,price:10,attributes:{},barcodes:[{source:"store",barcode:"F1"}]},
 {variant_id:2,active:0,models:[],stock:0,price:null,attributes:{},barcodes:[]}]};
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
 assert.equal(!!inputs[1].props.disabled,status!=="open");
 assert.equal(!!button(view.dom,"送出"),status==="open");
 assert.equal(!!button(view.dom,"核准"),status==="pending"&&isAdmin);
 assert.ok(!button(view.dom,"抽回修改"));
 assert.equal(!!all(view.dom,n=>n.tag==="button"&&n.props?.title==="移除").length,status==="open");
 assert.equal(!!button(view.dom,"退回"),status==="pending"&&isAdmin);
 assert.equal(!!button(view.dom,"退回重盤（0 項）"),status==="pending"&&isAdmin);
 if(status==="pending"&&!isAdmin)assert.match(text(view.dom),/等待管理者核准/);
 view.app.unmount();
 const list=mount(context.window.PosPages["page-stocktake"],template("stocktake"),{sessions:[{session_id:1,status,employee:1}]});
 assert.ok(button(list.dom,status==="open"?"續盤":status==="pending"&&isAdmin?"審核":"檢視"));
 assert.equal(!!button(list.dom,"抽回修改"),status==="pending");
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
await click(button(view.dom,"送出"));await tick();assert.ok(calls.some(c=>c[0]==="close"&&c[1]===1));view.app.unmount();
''')

    def test_navigation_hides_settings_redirects_on_logout_and_badge_is_admin_only(self):
        self.run_js(r'''
let rootDefinition;
context.document.addEventListener=(n,f)=>{events[n]=f;};
context.Vue={...Vue,createApp(def){rootDefinition=def;return{mixin(){},component(){},mount(){}};}};
context.window.PosAppUpdate={run(){}};load("static/js/app.js");events.DOMContentLoaded();context.Vue=Vue;
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
API.pendingReturnCount=async()=>({count:4});
load("static/js/auth.js");
assert.equal(statuses,0);assert.equal(pending,0);
events.pointerdown();events.keydown();assert.equal(touches,1);
now=59999;events.keydown();assert.equal(touches,1);
now=60000;events.keydown();assert.equal(touches,2);
await context.window.PosAuthOps.refresh();assert.equal(pending,0);
API.authStatus=async()=>({is_admin:true});await context.window.PosAuthOps.refresh();
assert.equal(context.window.PosAuth.pendingStocktakes,9);assert.equal(pending,1);
assert.equal(context.window.PosAuth.pendingReturns,4);
API.authStatus=async()=>({is_admin:false});await context.window.PosAuthOps.refresh();
assert.equal(context.window.PosAuth.pendingStocktakes,0);assert.equal(context.window.PosAuth.pendingReturns,0);
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

    def test_records_expand_multiple_rows_and_all_in_every_kind(self):
        self.run_js(r'''
load("static/js/records.js");
const view=mount(context.window.PosPages["page-records"],template("records"));
const s=view.state;await tick();
assert.ok(!button(view.dom,"展開全部"));
const rows={sales:["sales",[{sale_id:1,items:[]},{sale_id:2,items:[]}]],receive:["receipts",[{receipt_id:1,items:[]},{receipt_id:2,items:[]}]],
 returns:["returns",[{return_id:1,items:[]},{return_id:2,items:[]}]],stocktake:["stocktakes",[{session_id:1,items:[]},{session_id:2,items:[]}]]};
for(const [kind,[field,data]] of Object.entries(rows)){
 await s.selectKind(kind);await tick();s[field]=data;await tick();
 assert.ok(button(view.dom,"展開全部"),kind);assert.ok(button(view.dom,"收合全部"),kind);
 const [a,b]=s.rowKeys;
 s.toggle(a);s.toggle(b);assert.ok(s.isOpen(a)&&s.isOpen(b),kind+" 應可同時展開");
 s.toggle(a);assert.ok(!s.isOpen(a)&&s.isOpen(b),kind+" 只收合該筆");
 s.expandAll();assert.ok(s.isOpen(a)&&s.isOpen(b));
 s.collapseAll();assert.ok(!s.isOpen(a)&&!s.isOpen(b));
 s.expandAll();await s.reload();assert.equal(s.expanded.size,0,kind+" 查詢後應全部收合");
}
assert.equal(s.minuteText("2026-11-04 16:52:58"),"2026-11-04 16:52");assert.equal(s.amountText(0),"—");assert.equal(s.amountText(50),"−50");assert.equal(s.salePrice({qty:2,unit_price:690,discount:68}),656);assert.equal(s.formulaText({order_discount:0,member_discount:0,total:656,items:[]}),"應收 656");assert.equal(s.formulaText({order_discount:6,member_discount:200,total:1040,items:[{qty:1,unit_price:690,discount:34},{qty:2,unit_price:295,discount:0}]}),"商品小計 1246 − 整單折抵 6 − 會員折扣 200 = 應收 1040");
const days=d=>Math.round((new Date(s.dateTo)-new Date(d))/86400000);
for(const kind of["receive","stocktake","returns"]){await s.selectKind(kind);assert.equal(days(s.dateFrom),6,kind);}
await s.selectKind("sales");assert.equal(s.dateFrom,s.dateTo);
view.app.unmount();
''')

    def test_records_dayclose_admin_only_fixed_submit_and_cleared_on_logout(self):
        self.run_js(r'''
process.exitCode=1;
load("static/js/records.js");
const preview={business_date:"",today:"2026-11-10",closed:false,record:null,float_amount:3000,float_setting:3000,cash_sales:500,
 by_payment:[{payment:"現金",count:1,total:500}],void_count:0,void_total:0,unclosed:["2026-11-08"],unclosed_count:1};
const record={business_date:"2026-11-07",closed_at:"2026-11-07 22:00:05",employee:2,float_amount:3000,counted_total:2900,cash_sales:0,diff:-100,reason:"少找"};
API.listPayments=async()=>[];API.listSales=async()=>[];API.salesSummary=async()=>null;
API.dayCloseCurrent=async()=>({business_date:"2999-01-01",today:"2026-11-10"});
API.dayClosePreview=async d=>({...preview,business_date:d});API.dayCloseList=async()=>[record];
const sent=[],asked=[];API.dayCloseClose=async p=>{sent.push(JSON.parse(JSON.stringify(p)));};
context.PosConfirm.ask=async m=>{asked.push(m);return true;};
const view=mount(context.window.PosPages["page-records"],template("records"));const s=view.state;await tick();
// 員工:選單看不到立帳,直接呼叫也進不去
assert.ok(!/立帳/.test(text(view.dom)));
await s.selectKind("dayclose");assert.equal(s.kind,"sales");assert.ok(errors.includes("需要管理者權限"));
// 收銀預設迄日用目前帳日(今天已立帳時是明天)
await s.enterKind("sales");assert.equal(s.dateTo,"2999-01-01");assert.notEqual(s.dateFrom,s.dateTo);
s.sales=[{sale_id:1,ts:"2026-11-10 23:30:00",business_date:"2026-11-11",items:[]},{sale_id:2,ts:"2026-11-10 10:00:00",business_date:"2026-11-10",items:[]}];
await tick();assert.equal(text(view.dom).split("計入次日帳").length-1,1);
auth.isAdmin=true;await tick();assert.match(text(view.dom),/立帳/);
await s.selectKind("dayclose");await tick();
assert.equal(s.dc.preview.cash_sales,500);assert.match(text(view.dom),/尚未立帳/);assert.match(text(view.dom),/少找/);
assert.ok(button(view.dom,"完成立帳"));assert.ok(button(view.dom,"11/08"));
// 必填:錢櫃現金總額、有短溢要原因
await s.dcClose();assert.ok(errors.includes("請輸入錢櫃現金總額"));
s.dc.counted="3400";await tick();assert.equal(s.dcDiff,-100);assert.match(text(view.dom),/−100/);
await s.dcClose();assert.ok(errors.includes("有短溢時請填寫原因"));assert.equal(sent.length,0);assert.equal(asked.length,0);
// 確認框不放實點與短溢;送出的是按下當下那一份,不讀確認框期間被改的輸入框
s.dc.reason=" 少找 ";const picked=s.dc.date;
context.PosConfirm.ask=async m=>{asked.push(m);s.dc.counted="9999";s.dc.reason="改掉";return true;};
await s.dcClose();await tick();
assert.equal(asked.length,1);assert.ok(!/3400|9999|短溢|−/.test(asked[0]));assert.match(asked[0],/不可再作廢或更正付款方式/);
assert.deepEqual(sent,[{business_date:picked,counted_total:3400,reason:"少找",employee:1,expected:{float_amount:3000,cash_sales:500}}]);
assert.equal(s.dc.counted,"");
// 已立帳的帳日:唯讀顯示當時紀錄,沒有按鈕
API.dayClosePreview=async d=>({...preview,business_date:d,closed:true,record});
await s.dcLoad("2026-11-07");await tick();
assert.ok(!button(view.dom,"完成立帳"));assert.match(text(view.dom),/已立帳/);await s.dcClose();assert.equal(sent.length,1);
// 確認框開著時管理者逾時:不送出,切回收銀紀錄並清掉立帳資料
API.dayClosePreview=async d=>({...preview,business_date:d});await s.dcLoad("2026-11-09");s.dc.counted="3500";
context.PosConfirm.ask=async()=>{auth.isAdmin=false;return true;};
await s.dcClose();await tick();await tick();
assert.equal(sent.length,1);assert.equal(s.kind,"sales");assert.equal(s.dc.preview,null);assert.equal(s.dc.records.length,0);
assert.ok(!/完成立帳|預備金|少找/.test(text(view.dom)));
// 晚到的預覽回應:已不是管理者就丟棄
auth.isAdmin=true;await tick();await s.selectKind("dayclose");await tick();
let release;API.dayClosePreview=()=>new Promise(r=>{release=r;});
const pending=s.dcLoad("2026-11-08");auth.isAdmin=false;await tick();release({...preview,business_date:"2026-11-08"});await pending;await tick();
assert.equal(s.dc.preview,null);assert.equal(s.kind,"sales");
view.app.unmount();process.exitCode=0;
''')

    def test_void_dialog_required_fields_single_submit_and_failure_keeps_content(self):
        self.run_js(r'''
process.exitCode=1;
load("static/js/employeepicker.js");load("static/js/doccorrect.js");
const shared=context.window.PosEmployee;shared.id=4;auth.isAdmin=true;
const def=context.window.PosComponents["doc-correct"];
const doc={type:"sale",label:"銷售單",id:9,ts:"2026-11-10 10:00:00",employee:1,payment:"現金",total:200,count:1};
const closes=[],sent=[];let fail=null,gate=null;
API.voidSale=async p=>{sent.push(JSON.parse(JSON.stringify(p)));if(gate)await gate;if(fail)throw fail;};
const view=mount(def,def.template,{},{mode:"void",doc,onClose:r=>closes.push(r)});const s=view.state;await tick();
assert.match(text(view.dom),/作廢後庫存將加回/);
// 不問是誰作廢的:框內沒有員工選擇器
assert.equal(all(view.dom,n=>/employee-picker/.test(String(n.props.class||""))).length,0);assert.ok(!/經手人|操作員工/.test(text(view.dom)));
// 必填:原因、選其他要說明;沒過不送出、不關框
await s.submit();assert.equal(s.error,"請選擇作廢原因");
const hidden=()=>all(view.dom,n=>n.tag==="label"&&/說明/.test(text(n)))[0].props.class;
assert.match(String(hidden()),/doc-hidden/);
s.reasonCode="other";await tick();assert.ok(!/doc-hidden/.test(String(hidden()||"")));
await s.submit();assert.match(s.error,/請填寫說明/);assert.equal(sent.length,0);assert.equal(closes.length,0);
// 已不是管理者:不送出
s.reasonText=" 客人反悔 ";auth.isAdmin=false;await s.submit();assert.equal(s.error,"需要管理者權限");assert.equal(sent.length,0);
auth.isAdmin=true;
// 送出中連點只送一次,也不能取消
let open;gate=new Promise(r=>{open=r;});
const first=s.submit();await tick();s.submit();s.cancel();await tick();
assert.equal(sent.length,1);assert.equal(closes.length,0);assert.equal(s.busy,true);
// 一般失敗:留在框內、內容保留
fail=Object.assign(new Error("資料庫操作失敗"),{code:"database_error"});open();await first;await tick();
assert.equal(s.error,"資料庫操作失敗");assert.equal(s.reasonText," 客人反悔 ");assert.equal(closes.length,0);assert.equal(s.busy,false);
// 衝突:關框並通知呼叫端重查
gate=null;fail=Object.assign(new Error("此單已作廢"),{code:"conflict"});await s.submit();
assert.deepEqual(closes,["conflict"]);assert.ok(errors.includes("此單已作廢"));
// 成功
fail=null;await s.submit();assert.deepEqual(closes,["conflict","saved"]);
assert.deepEqual(sent.at(-1),{sale_id:9,reason_code:"other",reason_text:"客人反悔",expected_total:200});
s.cancel();assert.equal(closes.at(-1),false);
view.app.unmount();process.exitCode=0;
''')

    def test_correct_dialog_payment_and_employee_modes(self):
        self.run_js(r'''
process.exitCode=1;
load("static/js/employeepicker.js");load("static/js/doccorrect.js");
const shared=context.window.PosEmployee;shared.id=4;auth.isAdmin=true;
const def=context.window.PosComponents["doc-correct"];const sent=[];
API.setSalePayment=async p=>{sent.push(["pay",JSON.parse(JSON.stringify(p))]);};
API.setDocEmployee=async p=>{sent.push(["emp",JSON.parse(JSON.stringify(p))]);};
API.voidSale=async()=>assert.fail("不該呼叫作廢");
// 更正付款方式:下拉不含目前的付款方式;沒選不送出
let closes=[];
let view=mount(def,def.template,{},{mode:"payment",doc:{type:"sale",label:"銷售單",id:9,ts:"2026-11-10 10:00:00",employee:1,payment:"現金",total:200,count:1},
 payments:["現金","轉帳","行動支付"],onClose:r=>closes.push(r)});let s=view.state;await tick();
assert.deepEqual([...s.otherPayments],["轉帳","行動支付"]);assert.match(text(view.dom),/金額會跟著變動/);assert.ok(button(view.dom,"確認更正"));
assert.ok(!/作廢後庫存/.test(text(view.dom)));
await s.submit();assert.equal(s.error,"請選擇新付款方式");assert.equal(sent.length,0);
assert.equal(all(view.dom,n=>/employee-picker/.test(String(n.props.class||""))).length,0);
s.payment="轉帳";await s.submit();
assert.deepEqual(sent,[["pay",{sale_id:9,payment:"轉帳",expected_payment:"現金"}]]);assert.deepEqual(closes,["saved"]);
view.app.unmount();
// 更正員工:要選新代號、不可與目前相同;舊單員工為空時 expected 送 null;沒有警示區
closes=[];
view=mount(def,def.template,{},{mode:"employee",doc:{type:"stocktake",label:"盤點單",id:3,ts:"2026-11-09 09:00:00",employee:undefined},onClose:r=>closes.push(r)});
s=view.state;await tick();
assert.match(text(view.dom),/修正盤點操作者/);assert.match(text(view.dom),/正確操作者/);assert.ok(button(view.dom,"確認修正"));assert.ok(!all(view.dom,n=>/doc-warning/.test(String(n.props.class||""))).length);
await s.submit();assert.equal(s.error,"請選擇正確操作者");
const pickers=all(view.dom,n=>/employee-picker/.test(String(n.props.class||"")));assert.equal(pickers.length,1);assert.ok(!/經手人/.test(text(view.dom)));
click(pickers[0].children.find(b=>text(b)==="2"));await tick();assert.equal(s.newEmployee,2);assert.equal(shared.id,4);   // 不回寫全店共用的選擇
await s.submit();assert.deepEqual(sent.at(-1),["emp",{doc_type:"stocktake",doc_id:3,employee:2,expected_employee:null}]);
assert.deepEqual(closes,["saved"]);view.app.unmount();
view=mount(def,def.template,{},{mode:"employee",doc:{type:"sale",label:"銷售單",id:9,ts:"",employee:2},onClose:()=>{}});
s=view.state;s.newEmployee=2;await s.submit();assert.equal(s.error,"與目前的操作者相同，不需修正");assert.equal(sent.length,2);
view.app.unmount();process.exitCode=0;
''')

    def test_records_correct_entries_per_kind_and_history(self):
        self.run_js(r'''
process.exitCode=1;
load("static/js/employeepicker.js");load("static/js/doccorrect.js");load("static/js/records.js");
const item={name:"x",qty:1,unit_price:100,discount:0,system_qty:1,counted_qty:1,diff:0};
const hist=[{ts:"2026-11-10 12:00:30",field:"employee",old_value:null,new_value:"2"},{ts:"2026-11-10 12:05:00",field:"payment",old_value:"現金",new_value:"轉帳"}];
const sale=(id,extra)=>({sale_id:id,ts:"2026-11-10 10:00:00",business_date:"2026-11-10",employee:1,payment:"現金",total:100,order_discount:0,member_discount:0,day_closed:false,voided_at:null,items:[item],corrections:[],...extra});
API.listSales=async()=>[sale(1,{corrections:hist}),sale(2,{day_closed:true}),sale(3,{voided_at:"2026-11-10 11:00:00",void_reason:"重複結帳"})];
API.salesSummary=async()=>({count:2,total:200,by_payment:[],void_count:1,void_total:100});
API.listReceipts=async()=>[{receipt_id:7,ts:"2026-11-10 09:00:00",employee:1,items:[item],corrections:[]},{receipt_id:8,ts:"2026-11-10 09:30:00",employee:2,items:[item],corrections:[]}];
API.returnHistory=async()=>[{return_id:4,started_at:"2026-11-10 13:00:00",status:"closed",employee:3,items:[item],corrections:[]}];
API.stocktakeHistory=async()=>[{session_id:5,started_at:"2026-11-10 14:00:00",status:"closed",approved_at:"x",employee:4,diff_count:0,items:[item],corrections:[]}];
const view=mount(context.window.PosPages["page-records"],template("records"));const s=view.state;s.payments=["現金","轉帳"];
const count=label=>buttons(view.dom).filter(b=>text(b)===label).length;
await s.reload();s.expandAll();await tick();
assert.equal(count("修正操作者")+count("更正付款方式")+count("作廢"),0);   // 員工看不到任何入口
s.openCorrect("employee","sale",s.sales[0]);assert.equal(s.correct,null);
assert.match(text(view.dom),/2026-11-10 12:00　操作者：— → 2/);assert.match(text(view.dom),/付款方式：現金 → 轉帳/);assert.ok(!/經手人/.test(text(view.dom)));
auth.isAdmin=true;await tick();
// 收銀:更正員工不限期限(三張都有);作廢與更正付款方式只有未作廢、未立帳那一張
assert.deepEqual([count("修正操作者"),count("更正付款方式"),count("作廢")],[3,1,1]);
click(button(view.dom,"更正付款方式"));await tick();
assert.equal(s.correct.mode,"payment");assert.equal(s.correct.doc.payment,"現金");assert.ok(button(view.dom,"確認更正"));
await s.correctClosed(false);
for(const [kind,type,id,emp] of [["receive","receipt",7,1],["returns","return",4,3],["stocktake","stocktake",5,4]]){
 await s.enterKind(kind);s.expandAll();await tick();
 assert.ok(count("修正操作者")>=1,kind);click(button(view.dom,"修正操作者"));await tick();
 assert.deepEqual([s.correct.mode,s.correct.doc.type,s.correct.doc.id,s.correct.doc.employee],["employee",type,id,emp],kind);
 // 更正後重查,展開狀態保留(進貨以單號當鍵)
 await s.correctClosed("saved");assert.ok(s.isOpen(id),kind);
}
view.app.unmount();process.exitCode=0;
''')

    def test_records_void_button_conditions_and_marks(self):
        self.run_js(r'''
process.exitCode=1;
load("static/js/employeepicker.js");load("static/js/doccorrect.js");load("static/js/records.js");
const mk=(id,extra)=>({sale_id:id,ts:"2026-11-10 10:00:00",business_date:"2026-11-10",employee:1,payment:"現金",total:100,order_discount:0,member_discount:0,day_closed:false,voided_at:null,items:[{name:"x",qty:1,unit_price:100,discount:0}],...extra});
const list=[mk(1),mk(2,{day_closed:true}),mk(3,{voided_at:"2026-11-10 11:00:00",void_reason:"重複結帳"})];
let loads=0;API.listSales=async()=>{loads++;return list;};
API.salesSummary=async()=>({count:2,total:200,by_payment:[],void_count:1,void_total:100});
const view=mount(context.window.PosPages["page-records"],template("records"));const s=view.state;
await s.reload();s.expandAll();await tick();
assert.match(text(view.dom),/合計 2 筆，共 200 元（已作廢 1 筆 100 元）/);
assert.match(text(view.dom),/原因：重複結帳/);assert.equal(text(view.dom).split("已作廢").length-1,3);
assert.ok(!button(view.dom,"作廢"));s.openVoid(list[0]);assert.equal(s.correct,null);assert.ok(errors.includes("需要管理者權限"));
auth.isAdmin=true;await tick();
assert.equal(buttons(view.dom).filter(b=>text(b)==="作廢").length,1);   // 已立帳、已作廢的單沒有按鈕
click(button(view.dom,"作廢"));await tick();assert.equal(s.correct.doc.id,1);assert.equal(s.correct.mode,"void");assert.ok(button(view.dom,"確認作廢"));
// 成功後重查並保留展開;取消不重查
const before=loads;await s.correctClosed(false);assert.equal(loads,before);assert.equal(s.correct,null);
s.openVoid(list[0]);await s.correctClosed("saved");assert.equal(loads,before+1);assert.ok(s.isOpen(1)&&s.isOpen(3));
// 開著框時登出:框關掉
s.openVoid(list[0]);auth.isAdmin=false;await tick();assert.equal(s.correct,null);
view.app.unmount();process.exitCode=0;
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
