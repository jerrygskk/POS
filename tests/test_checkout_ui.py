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
  window: { PosPages: {}, PosConfirm: {} }, API: {},
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
  out.cart = s.cart.length;
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
const s = mkState({cart:[{variant_id:1, qty:1, unit_price:100, discount:0}], paid:100});
(async () => {
  const first = s.checkout();
  const second = s.checkout();
  out.during = {calls, submitting:s.submitting};
  resolveCheckout({change:0, sale_id:9});
  await Promise.all([first, second]);
  out.after = {calls, submitting:s.submitting, cart:s.cart.length};
  done();
})();
''')
        self.assertEqual(out["during"], {"calls": 1, "submitting": True})
        self.assertEqual(out["after"], {"calls": 1, "submitting": False, "cart": 0})


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

    def test_shared_resource_versions_are_193(self):
        for name in ("index.html", "variant_editor.html", "variant_batch.html",
                     "field_editor.html"):
            source = (STATIC / name).read_text(encoding="utf-8")
            versions = set(re.findall(r"\?v=(\d+)", source))
            self.assertEqual(versions, {"193"}, f"{name} 版號不一致: {versions}")


if __name__ == "__main__":
    unittest.main()
