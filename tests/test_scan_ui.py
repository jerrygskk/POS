"""共用掃描解析、選擇公版與四個掃描入口。"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class ScanUiTests(unittest.TestCase):
    def run_js(self, script, files):
        result = subprocess.run(["node", "-e", script, *[str(ROOT / f) for f in files]],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_choose_items_focus_disabled_note_enter_and_cancel_paths(self):
        self.run_js(r'''
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element{
 constructor(tag){this.tagName=tag;this.children=[];this.listeners={}}
 appendChild(child){child.parent=this;this.children.push(child);return child}
 remove(){this.parent.children=this.parent.children.filter(x=>x!==this)}
 addEventListener(name,fn){(this.listeners[name]??=[]).push(fn)}
 focus(){this.focused=true}
 fire(name,event={target:this}){for(const fn of this.listeners[name]||[])fn(event)}
}
const document={body:new Element("body"),listeners:{},createElement:t=>new Element(t),
 addEventListener(n,f){(this.listeners[n]??=[]).push(f)},
 removeEventListener(n,f){this.listeners[n]=this.listeners[n].filter(x=>x!==f)},
 key(key){let prevented=false;for(const f of this.listeners.keydown||[])f({key,preventDefault(){prevented=true}});return prevented}};
const context={window:{},document};vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1],"utf8"),context);
const c=context.window.PosConfirm;
const options={title:"請選擇品項",message:"選擇",items:[{label:"停用",detail:"規格",note:"已停用",disabled:true},{label:"正常",detail:"店內碼",note:"",disabled:false}]};
const parts=()=>{const overlay=document.body.children[0],box=overlay.children[0];return{overlay,list:box.children[2],cancel:box.children[3].children[0]}};
(async()=>{
 let settled=false;const choice=c.choose(options).then(v=>{settled=true;return v});let p=parts();
 assert.equal(p.list.children[0].children[1].textContent,"已停用");assert.equal(p.list.children[1].focused,true);
 p.list.children[0].fire("click");await Promise.resolve();assert.equal(settled,false);
 assert.equal(document.key("Enter"),false);assert.equal(settled,false);
 p.list.children[1].fire("click");assert.equal(await choice,1);assert.equal(document.body.children.length,0);
 for(const mode of["escape","cancel","overlay"]){const pending=c.choose(options);p=parts();
  if(mode==="escape")document.key("Escape");else if(mode==="cancel")p.cancel.fire("click");else p.overlay.fire("mousedown");
  assert.equal(await pending,null)}
 const pending=c.choose({...options,items:[options.items[0]]});p=parts();assert.equal(p.cancel.focused,true);
 document.key("Escape");await pending;
})().catch(e=>{console.error(e);process.exitCode=1});
''', ["static/js/confirm.js"])

    def test_posscan_contract_and_errors(self):
        self.run_js(r'''
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
let calls=0,chosen,selection=1;
const matches=[{variant_id:1,name:"甲",attr_display:"",store_barcode:null,price:null,stock:0,status:"inactive"},
{variant_id:2,name:"乙",attr_display:"黑",store_barcode:"TL2",price:50,stock:3,status:"active"},
{variant_id:3,name:"丙",attr_display:"白",store_barcode:"TL3",price:60,stock:4,status:"issue"}];
const context={window:{},API:{scanBarcode:async()=>{calls++;return{matches}}},PosConfirm:{choose:async o=>{chosen=o;return selection}}};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[1],"utf8"),context);
const scan=context.window.PosScan;
(async()=>{
 assert.equal(await scan("  "),null);assert.equal(calls,0);
 context.API.scanBarcode=async()=>({matches:[matches[0]]});
 assert.equal((await scan(" SINGLE ",{sellableOnly:true})).data,matches[0]);assert.equal(chosen,undefined);
 context.API.scanBarcode=async()=>({matches});const result=await scan(" SHARED ",{sellableOnly:true});
 assert.equal(result.code,"SHARED");assert.equal(result.data,matches[1]);assert.equal(chosen.title,"請選擇品項");
 assert.equal(chosen.message,"條碼「SHARED」對應多個品項，請選擇：");
 assert.deepEqual(JSON.parse(JSON.stringify(chosen.items)),[
 {label:"甲",detail:"（無規格）｜店內碼 —｜未定價｜庫存 0",note:"已停用",disabled:true},
 {label:"乙",detail:"黑｜店內碼 TL2｜50 元｜庫存 3",note:"",disabled:false},
 {label:"丙",detail:"白｜店內碼 TL3｜60 元｜庫存 4",note:"待處理",disabled:true}]);
 await scan("SHARED");assert.equal(chosen.items[0].disabled,false);
 selection=null;assert.equal(await scan("SHARED"),null);
 const error=Object.assign(new Error("missing"),{status:404});context.API.scanBarcode=async()=>{throw error};
 await assert.rejects(scan("NONE"),e=>e===error);
})().catch(e=>{console.error(e);process.exitCode=1});
''', ["static/js/pos_shared.js"])

    def test_real_posscan_connects_four_pages_and_cancellation_clears_inputs(self):
        self.run_js(r'''
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
const context={window:{PosEmployee:{id:1}},Vue:{reactive:x=>x},API:{},console,setTimeout:()=>{},
PosConfirm:{choose:async()=>null}};vm.createContext(context);
for(const file of process.argv.slice(1))vm.runInContext(fs.readFileSync(file,"utf8"),context);
const API=context.API,errors=[],follow=[];
const data={variant_id:1,name:"虛構",price:10,stock:2,status:"active",label_barcode:"TL1"};
API.scanBarcode=async()=>({matches:[data,{...data,variant_id:2}]});
API.stocktakeScan=async p=>follow.push(p);API.stocktakeDetail=async()=>({items:[]});
API.checkLabels=async rows=>({items:rows.map(r=>({...r,valid:true,label:{name:"虛構",models:[]}}))});
API.previewLabel=async()=>({image:"preview"});API.listCatalog=()=>assert.fail("search on cancel");
function mk(name){const page=context.window.PosPages[name],s={showError:m=>errors.push(m),$refs:{scan:{focus(){}}},current:1};
 Object.assign(s,context.window.PosMixin.methods,page.data());for(const[k,v]of Object.entries(page.methods))s[k]=v.bind(s);return s}
(async()=>{
 const checkout=mk("page-checkout"),stock=mk("page-stocktake"),receive=mk("page-receive"),labels=mk("page-labels");
 for(const s of[checkout,stock,receive]){s.scanCode="SHARED";await s.onScan();assert.equal(s.scanCode,"")}
 labels.query="SHARED";await labels.submitInput();assert.equal(labels.query,"");assert.equal(labels.results.length,0);assert.equal(labels.searched,false);
 assert.equal(checkout.draft.cart.length,0);assert.equal(receive.draft.rows.length,0);assert.equal(labels.q.rows.length,0);assert.equal(follow.length,0);
 API.scanBarcode=async()=>({matches:[data]});
 checkout.scanCode="F1";await checkout.onScan();assert.equal(checkout.draft.cart[0].variant_id,1);
 stock.current=1;stock.scanCode="F1";await stock.onScan();assert.equal(follow.length,1);
 receive.scanCode="F1";await receive.onScan();assert.equal(receive.draft.rows[0].label_barcode,"TL1");
 labels.query="F1";await labels.submitInput();assert.equal(labels.q.rows[0].barcode,"TL1");assert.deepEqual(errors,[]);
})().catch(e=>{console.error(e);process.exitCode=1});
''', ["static/js/pos_shared.js", "static/js/checkout.js", "static/js/stocktake.js", "static/js/receive.js", "static/js/labels.js"])
