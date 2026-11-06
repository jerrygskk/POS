"""紀錄查詢「會員」類別:正式 Vue prod 渲染、員工／管理者按鈕、存檔前檢查。"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = (ROOT / "tests/permission_ui_host.js").read_text(encoding="utf-8")


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class RecordsMemberUiTests(unittest.TestCase):
    def run_js(self, body):
        result = subprocess.run(["node", "-e", HOST + "\n(async()=>{\nload(\"static/js/membereditor.js\");load(\"static/js/records.js\");\n" + body +
            "\n})().catch(e=>{console.error(e.stack);process.exitCode=1});", str(ROOT)],
            cwd=ROOT, text=True, encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr[-8000:])

    def test_member_kind_hides_dates_and_roles_control_buttons(self):
        self.run_js(r'''
const members=[{member_id:1,name:"王小明",phones:["0912000111","0988000111"],ts:"2026-11-06 10:00:00"}];
API.listMembers=async q=>{calls.push(["listMembers",q]);return members;};
const view=mount(context.window.PosPages["page-records"],template("records"));
const s=view.state;
assert.ok(all(view.dom,n=>text(n)==="資料設定"&&n.props.class==="cat-name").length===1);
await s.selectKind("members");await tick();
assert.equal(all(view.dom,n=>n.tag==="input"&&n.props.type==="date").length,0);
assert.ok(button(view.dom,"新增會員"));
assert.match(text(view.dom),/0912000111、0988000111/);
assert.ok(button(view.dom,"修改"));assert.ok(!button(view.dom,"刪除"));
API.deleteMember=async()=>assert.fail("員工不可刪除");
await s.removeMember(members[0]);assert.ok(errors.includes("需要管理者權限"));
auth.isAdmin=true;await tick();assert.ok(button(view.dom,"刪除"));
view.app.unmount();auth.isAdmin=false;
''')

    def test_save_validates_and_warns_shared_phone(self):
        self.run_js(r'''
let created=null,asked=null,listed=0;
API.listMembers=async()=>{listed++;return [];};
API.memberPhoneOwners=async p=>{calls.push(["owners",p]);return [{phone:"0912000111",names:["王太太"]}];};
API.createMember=async p=>{created=p;return {member_id:9};};
context.PosConfirm.ask=async m=>{asked=m;return false;};
const view=mount(context.window.PosPages["page-records"],template("records"));
const s=view.state;await s.selectKind("members");
s.newMember();await tick();
assert.match(text(view.dom),/新增會員/);
const def=context.window.PosComponents["member-editor"];
let closed=[];
const ed=mount(def,def.template,{},{member:{member_id:null,name:"",phones:[]},onClose:v=>closed.push(v)});
const e=ed.state;
e.form.name="  ";await e.save();assert.ok(errors.includes("請輸入會員名稱"));
e.form.name="王小明";e.form.phones=["12345"];await e.save();
assert.ok(errors.includes("電話格式不正確：12345"));
e.addPhoneRow();assert.equal(e.form.phones.length,2);e.removePhoneRow(0);
e.form.phones=["0912-000-111"];await e.save();
assert.equal(asked,"此電話已屬於：0912000111 王太太\n仍要儲存嗎？");
assert.equal(created,null);assert.equal(closed.length,0);
context.PosConfirm.ask=async()=>true;await e.save();
assert.equal(JSON.stringify(created),JSON.stringify({name:"王小明",phones:["0912000111"]}));assert.deepEqual(closed,[true]);
const before=listed;await s.memberEditorClosed(true);assert.equal(s.memberPopup,null);assert.equal(listed,before+1);
ed.app.unmount();view.app.unmount();
''')

    def test_admin_delete_rechecks_after_confirm(self):
        self.run_js(r'''
const m={member_id:1,name:"王小明",phones:["0912000111"],ts:""};
API.listMembers=async()=>[m];
let deleted=0;API.deleteMember=async()=>{deleted++;};
auth.isAdmin=true;
context.PosConfirm.ask=async()=>{auth.isAdmin=false;return true;};
const view=mount(context.window.PosPages["page-records"],template("records"));
const s=view.state;await s.selectKind("members");
await s.removeMember(m);
assert.equal(deleted,0);assert.ok(errors.includes("需要管理者權限"));
auth.isAdmin=true;context.PosConfirm.ask=async()=>true;
await s.removeMember(m);assert.equal(deleted,1);
view.app.unmount();auth.isAdmin=false;
''')

    def test_sales_show_member_and_filter_by_phone(self):
        self.run_js(r'''
let filters=null;
API.listPayments=async()=>["現金"];
API.listSales=async f=>{filters=f;return [{sale_id:1,ts:"2026-11-06 10:00",employee:1,payment:"現金",order_discount:0,member_discount:0,total:100,member_name:"王小明",items:[]}];};
API.salesSummary=async()=>({count:1,total:100,by_payment:{}});
const view=mount(context.window.PosPages["page-records"],template("records"));
const s=view.state;
s.memberPhone="111";await s.reload();await tick();s.toggle(1);await tick();
assert.equal(filters.member_phone,"111");
assert.match(text(view.dom),/王小明/);
assert.ok(all(view.dom,n=>n.tag==="input"&&n.props.placeholder==="會員電話").length===1);
view.app.unmount();
''')
