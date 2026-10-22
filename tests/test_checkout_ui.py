"""結帳頁與共用輸入視窗的 Node 煙霧測試。"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class CheckoutUiTests(unittest.TestCase):
    def _run(self, body):
        script = r'''
const fs = require("fs"), vm = require("vm");
const context = {
  window: { PosPages: {}, PosConfirm: {}, PosEmployee: { id: 1 } }, API: {},
  Vue: { reactive: x => x },
  console, setTimeout, clearTimeout,
};
context.PosConfirm = context.window.PosConfirm;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const page = context.window.PosPages["page-checkout"];
function mkState(extra) {
  const errors = [];
  const s = { showError: message => errors.push(message),
    guard: async operation => operation() };
  Object.assign(s, page.data.call(s));
  for (const [name, method] of Object.entries(page.methods)) s[name] = method.bind(s);
  Object.assign(s, extra || {});
  s._errors = errors;
  return s;
}
const API = context.API, PosConfirm = context.PosConfirm, out = {};
function done() { process.stdout.write(JSON.stringify(out)); }
BODY
'''.replace("BODY", body)
        result = subprocess.run(
            ["node", "-e", script, str(STATIC / "js" / "checkout.js")],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8",
        )
        if result.returncode != 0:
            self.fail(result.stderr)
        return json.loads(result.stdout)

    def test_cart_and_order_discount_survive_new_component_state(self):
        out = self._run(r'''
const first = mkState();
(async () => {
  await first.addItem({variant_id:7, name:"保護殼", price:300,
    attributes:{顏色:"黑"}, attr_display:"黑"});
  first.draft.cart[0].qty = 2;
  first.draft.cart[0].unit_price = 250;
  first.draft.cart[0].discount = 30;
  first.draft.orderDiscount = 50;
  first.paid = 1000;
  const second = mkState();
  out.cart = second.draft.cart;
  out.orderDiscount = second.draft.orderDiscount;
  out.paid = second.paid;
  out.total = page.computed.total.call(second);
  done();
})();
''')
        self.assertEqual(out["cart"], [{
            "variant_id": 7, "name": "保護殼", "attributes": {"顏色": "黑"},
            "attr_display": "黑", "unit_price": 250, "qty": 2, "discount": 30}])
        self.assertEqual(out["orderDiscount"], 50)
        self.assertEqual(out["paid"], 0)
        self.assertEqual(out["total"], 420)

    def test_unpriced_item_uses_shared_input_and_cancel_does_not_add(self):
        out = self._run(r'''
let options;
PosConfirm.input = async value => { options = value; return null; };
const s = mkState();
(async () => {
  await s.addItem({variant_id:1, name:"未定價商品", price:null});
  out.options = {title:options.title, message:options.message,
    inputType:options.inputType, valid:options.validate("0"),
    negative:options.validate("-1"), decimal:options.validate("1.5")};
  out.cart = s.draft.cart.length;
  done();
})();
''')
        self.assertEqual(out["options"]["title"], "未定價商品")
        self.assertEqual(out["options"]["inputType"], "number")
        self.assertIsNone(out["options"]["valid"])
        self.assertEqual(out["options"]["negative"], "價格必須是非負整數")
        self.assertEqual(out["options"]["decimal"], "價格必須是非負整數")
        self.assertEqual(out["cart"], 0)

    def test_checkout_ignores_reentry_and_releases_submitting_after_request(self):
        out = self._run(r'''
let resolveCheckout, calls = 0;
API.checkout = () => { calls++; return new Promise(resolve => { resolveCheckout = resolve; }); };
const s = mkState({paid:100});
s.draft.cart = [{variant_id:1, qty:1, unit_price:100, discount:0}];
(async () => {
  const first = s.checkout();
  const second = s.checkout();
  out.during = {calls, submitting:s.draft.submitting};
  resolveCheckout({change:0, sale_id:9});
  await Promise.all([first, second]);
  out.after = {calls, submitting:s.draft.submitting, cart:s.draft.cart.length};
  done();
})();
''')
        self.assertEqual(out["during"], {"calls": 1, "submitting": True})
        self.assertEqual(out["after"], {"calls": 1, "submitting": False, "cart": 0})

    def test_pending_checkout_blocks_new_component_state(self):
        out = self._run(r'''
const resolvers = [];
let calls = 0;
API.checkout = () => { calls++; return new Promise(resolve => { resolvers.push(resolve); }); };
const first = mkState({paid:100});
first.draft.cart = [{variant_id:1, qty:1, unit_price:100, discount:0}];
(async () => {
  const pending = first.checkout();
  const second = mkState({paid:100});
  const repeated = second.checkout();
  out.calls = calls;
  for (const resolve of resolvers) resolve({change:0, sale_id:9});
  await Promise.all([pending, repeated]);
  out.submitting = context.window.PosCheckoutDraft.submitting;
  done();
})();
''')
        self.assertEqual(out["calls"], 1)
        self.assertFalse(out["submitting"])

    def test_checkout_failure_releases_shared_lock_and_preserves_draft(self):
        out = self._run(r'''
API.checkout = async () => { throw new Error("結帳失敗"); };
const s = mkState({paid:100});
s.draft.cart = [{variant_id:1, qty:1, unit_price:100, discount:0}];
s.draft.orderDiscount = 50;
s.guard = async operation => { try { await operation(); } catch (error) { s.showError(error.message); } };
(async () => {
  await s.checkout();
  out.draft = context.window.PosCheckoutDraft;
  out.errors = s._errors;
  out.paid = s.paid;
  done();
})();
''')
        self.assertEqual(out["draft"], {
            "cart": [{"variant_id": 1, "qty": 1, "unit_price": 100, "discount": 0}],
            "orderDiscount": 50, "submitting": False})
        self.assertEqual(out["errors"], ["結帳失敗"])
        self.assertEqual(out["paid"], 100)

    def test_successful_checkout_clears_global_draft(self):
        out = self._run(r'''
let submitted;
API.checkout = async value => { submitted = value; return {change:80, sale_id:10}; };
const s = mkState({paid:500});
s.draft.cart = [{variant_id:7, qty:2, unit_price:250, discount:30}];
s.draft.orderDiscount = 50;
(async () => {
  await s.checkout();
  out.submitted = submitted;
  out.draft = context.window.PosCheckoutDraft;
  out.paid = s.paid;
  out.total = page.computed.total.call(mkState());
  done();
})();
''')
        self.assertEqual(out["submitted"], {
            "payment": "現金", "order_discount": 50, "paid": 500, "employee": 1,
            "items": [{"variant_id": 7, "qty": 2, "unit_price": 250, "discount": 30}]})
        self.assertEqual(out["draft"], {"cart": [], "orderDiscount": 0, "submitting": False})
        self.assertEqual(out["paid"], 0)
        self.assertEqual(out["total"], 0)

    def test_checkout_without_employee_is_blocked_and_selection_is_kept(self):
        out = self._run(r'''
let calls = 0;
API.checkout = async () => { calls += 1; return {change:0, sale_id:1}; };
const s = mkState({paid:500});
s.draft.cart = [{variant_id:7, qty:1, unit_price:500, discount:0}];
(async () => {
  context.window.PosEmployee.id = null;
  await s.checkout();
  out.blockedCalls = calls; out.errors = s._errors.slice();
  context.window.PosEmployee.id = 3;
  await s.checkout();
  out.calls = calls; out.kept = context.window.PosEmployee.id;
  done();
})();
''')
        self.assertEqual(out["blockedCalls"], 0)
        self.assertEqual(out["errors"], ["請先選擇員工代號"])
        self.assertEqual(out["calls"], 1)
        self.assertEqual(out["kept"], 3)

    def test_cart_item_carries_current_stock(self):
        out = self._run(r'''
const s = mkState();
(async () => {
  await s.addItem({variant_id:7, name:"保護殼", price:300, stock:4});
  out.stock = s.draft.cart[0].stock;
  done();
})();
''')
        self.assertEqual(out["stock"], 4)


class ConfirmAndResourceContractTests(unittest.TestCase):
    def test_input_dialog_renders_validates_and_handles_close_interactions(self):
        script = r'''
const fs = require("fs"), vm = require("vm");
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = {}; this.parent = null; }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  remove() { if (!this.parent) return; const i = this.parent.children.indexOf(this); if (i >= 0) this.parent.children.splice(i, 1); this.parent = null; }
  addEventListener(type, listener) { (this.listeners[type] = this.listeners[type] || []).push(listener); }
  focus() { this.focused = true; }
  select() { this.selected = true; }
  fire(type, event) { for (const listener of this.listeners[type] || []) listener(event || {target:this}); }
}
const document = {
  body: new Element("body"), listeners: {},
  createElement: tag => new Element(tag),
  addEventListener(type, listener) { (this.listeners[type] = this.listeners[type] || []).push(listener); },
  removeEventListener(type, listener) { this.listeners[type] = (this.listeners[type] || []).filter(x => x !== listener); },
  key(key) { for (const listener of this.listeners.keydown || []) listener({key, preventDefault() {}}); },
};
const context = { window: {}, document, console };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const confirm = context.window.PosConfirm, out = {};
const overlay = () => document.body.children[0];
const parts = () => { const box = overlay().children[0], actions = box.children[3]; return {box, title:box.children[0], message:box.children[1], field:box.children[2], error:actions.children[0], cancel:actions.children[1], ok:actions.children[2]}; };
(async () => {
  let settled = false;
  const value = confirm.input({title:"輸入售價", message:"請輸入整數", value:"12", inputType:"number", validate:v => /^\d+$/.test(v) ? null : "請輸入整數"}).then(v => { settled = true; return v; });
  let p = parts();
  out.rendered = {title:p.title.textContent, message:p.message.textContent, value:p.field.value, inputType:p.field.type};
  p.field.value = "bad";
  document.key("Enter");
  out.invalid = {settled, overlays:document.body.children.length, error:p.error.textContent};
  p.field.value = "34";
  document.key("Enter");
  out.enter = {value:await value, overlays:document.body.children.length};
  const esc = confirm.input({}); document.key("Escape"); out.esc = {value:await esc, overlays:document.body.children.length};
  const outside = confirm.input({}); overlay().fire("mousedown", {target:overlay()}); out.overlay = {value:await outside, overlays:document.body.children.length};
  let boxSettled = false;
  const inside = confirm.input({}).then(v => { boxSettled = true; return v; });
  const box = overlay().children[0]; box.fire("mousedown", {target:box});
  out.box = {settledAfterClick:boxSettled, overlaysAfterClick:document.body.children.length};
  document.key("Escape"); out.box.value = await inside; out.box.overlaysAfterEscape = document.body.children.length;
  process.stdout.write(JSON.stringify(out));
})();
'''
        result = subprocess.run(
            ["node", "-e", script, str(STATIC / "js" / "confirm.js")],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8",
        )
        if result.returncode != 0:
            self.fail(result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["rendered"], {
            "title": "輸入售價", "message": "請輸入整數", "value": "12", "inputType": "number"})
        self.assertEqual(out["invalid"], {"settled": False, "overlays": 1, "error": "請輸入整數"})
        self.assertEqual(out["enter"], {"value": "34", "overlays": 0})
        self.assertEqual(out["esc"], {"value": None, "overlays": 0})
        self.assertEqual(out["overlay"], {"value": None, "overlays": 0})
        self.assertEqual(out["box"], {
            "settledAfterClick": False, "overlaysAfterClick": 1,
            "value": None, "overlaysAfterEscape": 0})

    def test_shared_resource_versions_are_consistent(self):
        # 只驗「四個頁面共用同一個版號」,不寫死數字:
        # 寫死會讓每次 bump ?v= 都要回頭改測試(踩過)。
        seen = {}
        for name in ("index.html", "variant_editor.html", "variant_batch.html",
                     "field_editor.html"):
            source = (STATIC / name).read_text(encoding="utf-8")
            versions = set(re.findall(r"\?v=(\d+)", source))
            self.assertEqual(len(versions), 1, f"{name} 版號不一致: {versions}")
            seen[name] = versions.pop()
        self.assertEqual(len(set(seen.values())), 1, f"四個頁面版號不一致: {seen}")


if __name__ == "__main__":
    unittest.main()
