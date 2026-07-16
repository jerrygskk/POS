import json
from pathlib import Path
import subprocess
from unittest.mock import Mock, patch

from base import ConnTestCase, make_client
from lib.desktop_bridge import DesktopBridge


class TestStocktakeLayers(ConnTestCase):
    def setUp(self):
        super().setUp()
        self.conn.execute("INSERT INTO Category(name) VALUES('C')")
        self.conn.execute("INSERT INTO Product(name,category_id) VALUES('P',1)")
        self.conn.execute("INSERT INTO Variant(product_id) VALUES(1)")
        self.conn.execute("INSERT INTO StockMovement(variant_id,qty,kind) VALUES(1,5,'purchase')")
        self.conn.commit()
        from lib.stocktake_service import StocktakeFacade
        self.facade = StocktakeFacade(self.db)
        self.bridge = DesktopBridge(logger=Mock(), facade=self.facade)

    def test_create_scan_detail_set_and_list_round_trip(self):
        created = self.facade.invoke("stocktake.create", {"operator": "A", "note": None})
        sid = created["session_id"]
        self.assertEqual({"system_qty": 5, "counted_qty": 2}, self.facade.invoke(
            "stocktake.scan", {"session_id": sid, "variant_id": 1, "qty": 2}))
        self.assertEqual({"system_qty": 5, "counted_qty": 3}, self.facade.invoke(
            "stocktake.scan", {"session_id": sid, "variant_id": 1, "qty": 1}))
        self.assertEqual({"ok": True}, self.facade.invoke(
            "stocktake.set_counted", {"session_id": sid, "variant_id": 1, "counted_qty": 4}))
        detail = self.facade.invoke("stocktake.detail", {"session_id": sid})
        self.assertEqual((4, -1), (detail["items"][0]["counted_qty"], detail["items"][0]["diff"]))
        self.assertEqual(sid, self.facade.invoke("stocktake.list", {})[0]["session_id"])

    def test_close_is_atomic_and_adjusts_once(self):
        sid = self.facade.invoke("stocktake.create", {})["session_id"]
        self.facade.invoke("stocktake.scan", {"session_id": sid, "variant_id": 1, "qty": 4})
        self.assertEqual({"ok": True}, self.facade.invoke("stocktake.close", {"session_id": sid}))
        second = self.bridge.invoke("stocktake.close", {"session_id": sid})
        self.assertEqual("conflict", second["error"]["code"])
        self.assertEqual(1, self.conn.execute(
            "SELECT COUNT(*) FROM StockMovement WHERE kind='adjust' AND ref_id=?", (sid,)).fetchone()[0])

    def test_close_rolls_back_status_and_adjustment(self):
        sid = self.facade.invoke("stocktake.create", {})["session_id"]
        self.facade.invoke("stocktake.scan", {"session_id": sid, "variant_id": 1, "qty": 4})
        with patch("lib.stocktake_service.StocktakeRepository.add_adjustment", side_effect=RuntimeError("secret")):
            result = self.bridge.invoke("stocktake.close", {"session_id": sid})
        self.assertEqual("internal_error", result["error"]["code"])
        self.assertNotIn("secret", str(result))
        self.assertEqual("open", self.conn.execute(
            "SELECT status FROM StocktakeSession WHERE session_id=?", (sid,)).fetchone()[0])

    def test_validation_rejects_bool_malformed_unknown_and_ranges(self):
        cases = [
            ("stocktake.create", {"operator": {"bad": 1}}),
            ("stocktake.list", {"extra": 1}),
            ("stocktake.detail", {"session_id": True}),
            ("stocktake.scan", {"session_id": 1, "variant_id": 1, "qty": 0}),
            ("stocktake.scan", {"session_id": 1, "variant_id": 1, "qty": True}),
            ("stocktake.set_counted", {"session_id": 1, "variant_id": 1, "counted_qty": -1}),
            ("stocktake.set_counted", {"session_id": 1, "variant_id": 1, "counted_qty": False}),
        ]
        for action, payload in cases:
            with self.subTest(action=action, payload=payload):
                self.assertEqual("validation_error", self.bridge.invoke(action, payload)["error"]["code"])
        self.assertEqual("validation_error", self.bridge.invoke("stocktake.erase", {})["error"]["code"])

    def test_missing_session_and_item_are_not_found(self):
        self.assertEqual("not_found", self.bridge.invoke(
            "stocktake.detail", {"session_id": 999})["error"]["code"])
        self.assertEqual("not_found", self.bridge.invoke(
            "stocktake.close", {"session_id": 999})["error"]["code"])
        sid = self.facade.invoke("stocktake.create", {})["session_id"]
        self.assertEqual("not_found", self.bridge.invoke("stocktake.set_counted", {
            "session_id": sid, "variant_id": 1, "counted_qty": 0})["error"]["code"])
        self.assertEqual("not_found", self.bridge.invoke("stocktake.scan", {
            "session_id": sid, "variant_id": 999, "qty": 1})["error"]["code"])

    def test_http_routes_are_thin_facade_adapters_and_contract(self):
        client = make_client(self.db)
        with patch("api.stocktake.StocktakeFacade") as facade_type:
            facade_type.return_value.invoke.return_value = {"session_id": 8}
            response = client.post("/api/stocktake", json={"operator": "A", "note": None})
            self.assertEqual({"session_id": 8}, response.json())
            facade_type.return_value.invoke.assert_called_once_with(
                "stocktake.create", {"operator": "A", "note": None})
        self.assertEqual(422, client.post("/api/stocktake/1/scan", json={
            "variant_id": 1, "qty": True}).status_code)

    def test_frontend_routes_every_stocktake_url_to_bridge(self):
        source = (Path(__file__).parents[1] / "static/js/api.js").read_text(encoding="utf-8")
        for action in ("stocktake.create", "stocktake.list", "stocktake.detail",
                       "stocktake.scan", "stocktake.set_counted", "stocktake.close"):
            self.assertIn(action, source)
        self.assertNotIn("/api", source)

    def test_frontend_runtime_maps_stocktake_and_preserves_existing_routes(self):
        api_path = Path(__file__).parents[1] / "static/js/api.js"
        script = api_path.read_text(encoding="utf-8") + r"""
const calls=[];
API._bridge=()=>Promise.resolve({invoke:async(action,payload)=>{
  calls.push({action,payload}); return {ok:true,data:{}};
}});
(async()=>{
  await API.listStocktakes();
  await API.createStocktake({operator:'A'});
  await API.stocktakeDetail(7);
  await API.stocktakeScan({variant_id:3,session_id:7,qty:1});
  await API.setStocktakeCounted({counted_qty:4,session_id:7,variant_id:3});
  await API.closeStocktake(7);
  await API.stockDetail(3);
  await API.listProducts({q:'x'});
  await API.listSales({date:'2026-07-15'});
  await API.listCategories({all:0});
  process.stdout.write(JSON.stringify(calls));
})().catch(error=>{console.error(error.stack);process.exit(1);});
"""
        proc = subprocess.run(
            ["node", "-"], input="global.window=global;\n" + script,
            text=True, encoding="utf-8", capture_output=True, check=True,
        )
        self.assertEqual([
            {"action": "stocktake.list", "payload": {}},
            {"action": "stocktake.create", "payload": {"operator": "A"}},
            {"action": "stocktake.detail", "payload": {"session_id": 7}},
            {"action": "stocktake.scan", "payload": {"variant_id": 3, "session_id": 7, "qty": 1}},
            {"action": "stocktake.set_counted", "payload": {"counted_qty": 4, "session_id": 7, "variant_id": 3}},
            {"action": "stocktake.close", "payload": {"session_id": 7}},
            {"action": "stock.detail", "payload": {"variant_id": 3}},
            {"action": "products.list", "payload": {"q": "x"}},
            {"action": "sales.list", "payload": {"date": "2026-07-15"}},
            {"action": "categories.list", "payload": {"all": 0}},
        ], json.loads(proc.stdout))
