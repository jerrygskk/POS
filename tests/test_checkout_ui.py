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
    def test_input_dialog_supports_validation_and_keyboard_cancellation(self):
        source = (STATIC / "js" / "confirm.js").read_text(encoding="utf-8")
        for token in ("input(options)", "options.inputType", "options.validate",
                      "error.textContent", 'event.key === "Escape"',
                      'event.key === "Enter"', "close(null)"):
            self.assertIn(token, source)

    def test_shared_resource_versions_are_192(self):
        for name in ("index.html", "variant_editor.html", "variant_batch.html",
                     "field_editor.html"):
            source = (STATIC / name).read_text(encoding="utf-8")
            versions = set(re.findall(r"\\?v=(\\d+)", source))
            self.assertEqual(versions, {"192"}, f"{name} 版號不一致: {versions}")


if __name__ == "__main__":
    unittest.main()
