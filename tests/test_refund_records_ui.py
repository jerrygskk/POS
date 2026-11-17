import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class RefundRecordsUiTest(unittest.TestCase):
    def run_dom(self, body):
        root = Path(__file__).resolve().parents[1]
        host = (root / "tests/permission_ui_host.js").read_text(encoding="utf-8")
        script = host + "\n(async()=>{process.exitCode=1;load('static/js/records.js');\n" + body + "\nassert.deepEqual(errors,[]);process.exitCode=0;})().catch(e=>{console.error(e);process.exitCode=1});"
        result = subprocess.run(["node","-e",script,str(root)],cwd=root,capture_output=True,text=True,encoding="utf-8",timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_summary_lines_and_filtered_notice_render_from_query_result(self):
        self.run_dom(r'''
const summary={count:2,total:1000,refunds_included:true,refund_count:1,refund_total:100,net_total:900,
 by_payment:[{payment_id:1,payment:"錢櫃收款",count:2,total:1000,cash_net:900}]};
const view=mount(context.window.PosPages['page-records'],template('records'),{summary});
assert.match(text(view.dom),/銷售 2 筆，共 1000 元/);
assert.match(text(view.dom),/退款 1 筆，共 100 元/);assert.match(text(view.dom),/淨額 900 元/);
assert.match(text(view.dom),/扣退款後 900/);
view.state.summary={...summary,refund_count:0,refund_total:0,net_total:1000,
 by_payment:[{payment_id:1,payment:"錢櫃收款",count:2,total:1000,cash_net:1000}]};await tick();
assert.ok(!text(view.dom).includes('扣退款後'));
view.state.summary={count:2,total:1000,refunds_included:false,by_payment:[]};await tick();
assert.match(text(view.dom),/退款未列入/);assert.ok(!text(view.dom).includes('淨額'));
view.app.unmount();
''')

    def test_return_list_member_total_and_next_day_tag_match_sales(self):
        self.run_dom(r'''
const row={return_id:1,started_at:'2026-11-16 10:00:00',approved_at:'2026-11-16 12:00:00',
 business_date:'2026-11-17',status:'closed',kind:'normal',employee:1,member_name:'測試會員',refund_amount:100,items:[]};
const view=mount(context.window.PosPages['page-records'],template('records'),{kind:'returns',returns:[row,{...row,return_id:2,business_date:'2026-11-16',refund_amount:50}]});
const mainRows=all(view.dom,n=>n.tag==='tbody')[0].children.filter(n=>n.tag==='tr');
const tagged=mainRows.find(n=>text(n).includes('計入次日帳'));
assert.ok(tagged);assert.ok(!text(tagged.children[2]).includes('計入次日帳'));
assert.match(text(tagged.children[3]),/2026-11-16.*計入次日帳/);
const tags=all(view.dom,n=>n.tag==='span'&&text(n)==='計入次日帳');assert.equal(tags.length,1);
assert.match(String(tags[0].props.class),/tag records-tag/);
assert.match(text(view.dom),/測試會員/);
const footer=all(view.dom,n=>n.tag==='tfoot')[0];assert.match(text(footer),/退款合計150/);
view.app.unmount();
''')

    def test_return_list_late_response_cannot_replace_new_query(self):
        self.run_dom(r'''
let release;let requests=0;
API.returnHistory=()=>++requests===1?new Promise(r=>release=r):Promise.resolve([{return_id:2,items:[]}]);
const view=mount(context.window.PosPages['page-records'],template('records'),{kind:'returns'});
const old=view.state.reload();await tick();await view.state.reload();
release([{return_id:1,items:[]}]);await old;await tick();
assert.equal(view.state.returns[0].return_id,2);view.app.unmount();
''')

    def test_diff_and_cash_net_use_refund_snapshot(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const ctx={window:{PosPages:{},PosAuth:{isAdmin:true}},console};vm.createContext(ctx);
vm.runInContext(fs.readFileSync('static/js/records.js','utf8'),ctx);
const p=ctx.window.PosPages['page-records'],s=p.data();
for(const[n,f]of Object.entries(p.computed))Object.defineProperty(s,n,{get:f.bind(s)});
s.payments=[{payment_id:1,is_cash:1},{payment_id:2,is_cash:0}];
s.dc.counted='9400';s.dc.preview={float_amount:0,cash_sales:10000,cash_refund:600};
assert.equal(s.dcDiff,0);
s.dc.records=[{cash_refund:600,sales_total:10000,diff:0,by_payment:[
{payment_id:1,payment:'現金',total:5000,fee_rate:0},
{payment_id:2,payment:'行動支付',total:5000,fee_rate:200}]}];
assert.equal(s.dcTotals.cash_refund,600);assert.equal(s.dcTotals.net_total,9300);
assert.deepEqual(Array.from(s.dcTotals.net),[4400,4900]);
'''
        result = subprocess.run(["node","-e",script],cwd=root,capture_output=True,text=True,encoding="utf-8",timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
