from pathlib import Path
from unittest.mock import Mock, patch

from base import ConnTestCase, make_client
from lib.db import get_conn
from lib.desktop_bridge import DesktopBridge


class TestStockLayers(ConnTestCase):
    def setUp(self):
        super().setUp()
        self.conn.execute("INSERT INTO Category(name) VALUES(?)", ("配件",))
        self.conn.execute(
            "INSERT INTO Product(name,category_id,active) VALUES(?,?,?)",
            ("測試商品", 1, 1),
        )
        self.conn.execute(
            "INSERT INTO Variant(product_id,active) VALUES(?,?)", (1, 1)
        )
        self.conn.commit()
        from lib.stock_service import StockFacade
        self.facade = StockFacade(self.db)
        self.bridge = DesktopBridge(logger=Mock(), facade=self.facade)

    def test_receive_writes_purchase_and_returns_accumulated_stock(self):
        first = self.facade.invoke(
            "stock.receive", {"variant_id": 1, "qty": 5, "note": "首批"}
        )
        second = self.facade.invoke("stock.receive", {"variant_id": 1, "qty": 3})
        self.assertEqual({"stock": 5}, first)
        self.assertEqual({"stock": 8}, second)
        row = self.conn.execute(
            "SELECT variant_id,qty,kind,note FROM StockMovement ORDER BY move_id"
        ).fetchone()
        self.assertEqual((1, 5, "purchase", "首批"), tuple(row))

    def test_detail_keeps_stock_and_latest_fifty_movements_shape(self):
        for qty in range(1, 53):
            self.facade.invoke("stock.receive", {"variant_id": 1, "qty": qty})
        result = self.facade.invoke("stock.detail", {"variant_id": 1})
        self.assertEqual(sum(range(1, 53)), result["stock"])
        self.assertEqual(50, len(result["movements"]))
        self.assertEqual(52, result["movements"][0]["qty"])
        self.assertEqual("purchase", result["movements"][0]["kind"])

    def test_bridge_rejects_zero_negative_bool_and_unknown_nested_payload(self):
        bad_payloads = [
            {"variant_id": 1, "qty": 0},
            {"variant_id": 1, "qty": -1},
            {"variant_id": 1, "qty": True},
            {"variant_id": True, "qty": 1},
            {"variant_id": 1, "qty": 1, "extra": {"qty": 2}},
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                result = self.bridge.invoke("stock.receive", payload)
                self.assertEqual("validation_error", result["error"]["code"])
        self.assertEqual(
            0, self.conn.execute("SELECT COUNT(*) FROM StockMovement").fetchone()[0]
        )

    def test_missing_variant_is_not_found_for_receive_and_detail(self):
        for action, payload in (
            ("stock.receive", {"variant_id": 999, "qty": 1}),
            ("stock.detail", {"variant_id": 999}),
        ):
            with self.subTest(action=action):
                result = self.bridge.invoke(action, payload)
                self.assertEqual("not_found", result["error"]["code"])

    def test_inactive_variant_preserves_existing_receive_semantics(self):
        self.conn.execute("UPDATE Variant SET active=0 WHERE variant_id=1")
        self.conn.commit()
        self.assertEqual(
            {"stock": 2},
            self.facade.invoke("stock.receive", {"variant_id": 1, "qty": 2}),
        )

    def test_transaction_rolls_back_when_stock_result_fails_after_insert(self):
        with patch("lib.stock_service.product_data.stock_of", side_effect=RuntimeError("boom")):
            result = self.bridge.invoke(
                "stock.receive", {"variant_id": 1, "qty": 4}
            )
        self.assertEqual("internal_error", result["error"]["code"])
        self.assertEqual(
            0, self.conn.execute("SELECT COUNT(*) FROM StockMovement").fetchone()[0]
        )

    def test_unknown_action_is_rejected_by_whitelist(self):
        result = self.bridge.invoke("stock.delete_all", {})
        self.assertEqual("validation_error", result["error"]["code"])

    def test_http_routes_use_facade_contract(self):
        client = make_client(self.db)
        with patch("api.stock.StockFacade") as facade_type:
            facade_type.return_value.invoke.return_value = {"stock": 7}
            response = client.post(
                "/api/stock/receive", json={"variant_id": 1, "qty": 2}
            )
            self.assertEqual({"stock": 7}, response.json())
            facade_type.return_value.invoke.assert_called_once_with(
                "stock.receive", {"variant_id": 1, "qty": 2, "note": None}
            )

        with patch("api.stock.StockFacade") as facade_type:
            facade_type.return_value.invoke.return_value = {
                "stock": 7, "movements": []
            }
            response = client.get("/api/stock/1")
            self.assertEqual(200, response.status_code)
            facade_type.return_value.invoke.assert_called_once_with(
                "stock.detail", {"variant_id": 1}
            )

    def test_http_and_frontend_contract_rejects_bool_and_routes_stock_to_bridge(self):
        client = make_client(self.db)
        self.assertEqual(
            422,
            client.post(
                "/api/stock/receive", json={"variant_id": 1, "qty": True}
            ).status_code,
        )
        source = (Path(__file__).parents[1] / "static" / "js" / "api.js").read_text(
            encoding="utf-8"
        )
        receive = (Path(__file__).parents[1] / "static" / "js" / "receive.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('API.invoke("stock.receive"', source)
        self.assertIn('API.invoke("stock.detail"', source)
        self.assertIn("API.receiveStock", receive)


if __name__ == "__main__":
    import unittest
    unittest.main()
