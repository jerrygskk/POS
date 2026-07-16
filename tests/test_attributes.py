import unittest
from lib.db import get_conn
from base import ApiTestCase

class TestAttributes(ApiTestCase):
    def test_seed_common_fields(self):
        # 種子只留兩個共用欄:商品描述、顏色
        names = [f["name"] for f in self.c.get("/api/fields").json()]
        self.assertIn("商品描述", names)
        self.assertIn("顏色", names)

    def test_rename_field(self):
        fid = self.c.get("/api/fields").json()[0]["field_id"]
        r = self.c.put(f"/api/fields/{fid}", json={"name": "描述"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("描述", [f["name"] for f in self.c.get("/api/fields").json()])

    def test_category_specific_field(self):
        cid = self.c.post("/api/categories", json={"name": "鋼化玻璃"}).json()["category_id"]
        r = self.c.post("/api/fields", json={"name": "版型", "category_id": cid})
        self.assertEqual(r.status_code, 200)
        # ?category_id 只回該種類專屬欄
        got = self.c.get(f"/api/fields?category_id={cid}").json()
        self.assertEqual([f["name"] for f in got], ["版型"])
        # ?common=1 只回共用欄(category_id NULL)
        common = self.c.get("/api/fields?common=1").json()
        self.assertTrue(all(f["category_id"] is None for f in common))
        self.assertNotIn("版型", [f["name"] for f in common])

    def test_options_by_field(self):
        fid = self.c.post("/api/fields", json={"name": "版型"}).json()["field_id"]
        self.c.post("/api/options", json={"field_id": fid, "value": "亮面"})
        self.c.post("/api/options", json={"field_id": fid, "value": "霧面"})
        vals = [o["value"] for o in self.c.get(f"/api/options?field_id={fid}").json()]
        self.assertEqual(vals, ["亮面", "霧面"])

    def test_duplicate_option_idempotent(self):
        fid = self.c.get("/api/fields").json()[0]["field_id"]
        self.c.post("/api/options", json={"field_id": fid, "value": "黑"})
        r = self.c.post("/api/options", json={"field_id": fid, "value": "黑"})
        self.assertEqual(r.status_code, 200)   # 重複靜默成功,不炸
        opts = self.c.get(f"/api/options?field_id={fid}").json()
        self.assertEqual(len([o for o in opts if o["value"] == "黑"]), 1)

    def test_reactivate_inactive_option_restores_same_id_without_duplicate(self):
        fid = self.c.post("/api/fields", json={"name": "版型"}).json()["field_id"]
        oid = self._opt(fid, "亮面")
        self.c.patch(f"/api/options/{oid}", json={"active": 0})

        r = self.c.post("/api/options", json={
            "field_id": fid, "value": "亮面", "reactivate": True})

        self.assertEqual(r.status_code, 200)
        opts = self.c.get(f"/api/options?field_id={fid}&all=1").json()
        matches = [o for o in opts if o["value"] == "亮面"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["option_id"], oid)
        self.assertEqual(matches[0]["active"], 1)

    def test_duplicate_inactive_option_without_reactivate_stays_inactive(self):
        fid = self.c.post("/api/fields", json={"name": "版型"}).json()["field_id"]
        oid = self._opt(fid, "亮面")
        self.c.patch(f"/api/options/{oid}", json={"active": 0})

        r = self.c.post("/api/options", json={"field_id": fid, "value": "亮面"})

        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get(f"/api/options?field_id={fid}").json(), [])
        all_opts = self.c.get(f"/api/options?field_id={fid}&all=1").json()
        self.assertEqual(len(all_opts), 1)
        self.assertEqual(all_opts[0]["option_id"], oid)
        self.assertEqual(all_opts[0]["active"], 0)

    def _opt(self, fid, value):
        self.c.post("/api/options", json={"field_id": fid, "value": value})
        return next(o["option_id"] for o in
                    self.c.get(f"/api/options?field_id={fid}").json()
                    if o["value"] == value)

    def test_rename_option(self):
        fid = self.c.post("/api/fields", json={"name": "版型"}).json()["field_id"]
        oid = self._opt(fid, "亮面")
        r = self.c.patch(f"/api/options/{oid}", json={"value": "高亮"})
        self.assertEqual(r.status_code, 200)
        vals = [o["value"] for o in self.c.get(f"/api/options?field_id={fid}").json()]
        self.assertIn("高亮", vals)
        self.assertNotIn("亮面", vals)

    def test_rename_option_conflict_409(self):
        fid = self.c.post("/api/fields", json={"name": "版型"}).json()["field_id"]
        self._opt(fid, "亮面")
        oid2 = self._opt(fid, "霧面")
        r = self.c.patch(f"/api/options/{oid2}", json={"value": "亮面"})
        self.assertEqual(r.status_code, 409)

    def test_deactivate_option_hidden_from_fields(self):
        cid = self.c.post("/api/categories", json={"name": "保護貼"}).json()["category_id"]
        fid = self.c.post("/api/fields", json={"name": "版型", "category_id": cid}).json()["field_id"]
        oid = self._opt(fid, "亮面")
        self.c.patch(f"/api/options/{oid}", json={"active": 0})
        # 維護頁 all=1 仍看得到停用者
        allopts = self.c.get(f"/api/options?field_id={fid}&all=1").json()
        self.assertIn("亮面", [o["value"] for o in allopts])
        # 預設(建檔下拉)不回停用者
        self.assertEqual(self.c.get(f"/api/options?field_id={fid}").json(), [])
        # categories/{id}/fields 只回啟用選項
        fields = self.c.get(f"/api/categories/{cid}/fields").json()
        opts = next(f["options"] for f in fields if f["field_id"] == fid)
        self.assertEqual(opts, [])

    def test_delete_option_removed_from_fields(self):
        cid = self.c.post("/api/categories", json={"name": "保護貼"}).json()["category_id"]
        fid = self.c.post("/api/fields", json={"name": "版型", "category_id": cid}).json()["field_id"]
        oid = self._opt(fid, "亮面")
        r = self.c.delete(f"/api/options/{oid}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get(f"/api/options?field_id={fid}&all=1").json(), [])
        fields = self.c.get(f"/api/categories/{cid}/fields").json()
        opts = next(f["options"] for f in fields if f["field_id"] == fid)
        self.assertEqual(opts, [])

    def test_referenced_delete_hides_option_preserves_attributes_and_clears_links(self):
        cid = self.create_category("保護貼")
        fid = self.create_field("版型", cid)
        oid = self._opt(fid, "亮面")
        mid = self.create_model(self.create_phone_brand("測試品牌"), "測試型號")
        self.c.put(f"/api/fields/{fid}", json={"default_option_id": oid})
        self.c.put(f"/api/options/{oid}/models", json={"model_ids": [mid]})
        self.c.post("/api/products", json={
            "name": "膜", "category_id": cid,
            "variants": [
                {"attributes": {"版型": "亮面"}, "barcodes": []},
                {"attributes": {"版型": "亮面"}, "barcodes": []},
            ],
        })

        listed = self.c.get(f"/api/options?field_id={fid}").json()
        self.assertEqual(listed[0]["usage_count"], 2)
        r = self.c.delete(f"/api/options/{oid}")

        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["deleted"])
        self.assertEqual(self.c.get(f"/api/options?field_id={fid}").json(), [])
        hidden = self.c.get(f"/api/options?field_id={fid}&all=1").json()[0]
        self.assertEqual(hidden["active"], 0)
        self.assertEqual(hidden["usage_count"], 2)
        with get_conn(self.db) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM VariantAttribute WHERE option_id=?", (oid,)
            ).fetchone()[0], 2)
            self.assertIsNone(conn.execute(
                "SELECT default_option_id FROM CategoryField WHERE field_id=?", (fid,)
            ).fetchone()[0])
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM OptionModel WHERE option_id=?", (oid,)
            ).fetchone()[0], 0)

    def test_unreferenced_delete_removes_option_and_clears_default(self):
        cid = self.create_category("保護貼")
        fid = self.create_field("版型", cid)
        oid = self._opt(fid, "亮面")
        self.c.put(f"/api/fields/{fid}", json={"default_option_id": oid})

        r = self.c.delete(f"/api/options/{oid}")

        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["deleted"])
        self.assertEqual(self.c.get(f"/api/options?field_id={fid}&all=1").json(), [])
        with get_conn(self.db) as conn:
            self.assertIsNone(conn.execute(
                "SELECT default_option_id FROM CategoryField WHERE field_id=?", (fid,)
            ).fetchone()[0])
            self.assertIsNone(conn.execute(
                "SELECT option_id FROM AttributeOption WHERE option_id=?", (oid,)
            ).fetchone())

    def test_add_option_unknown_field_returns_404(self):
        r = self.c.post("/api/options",
                        json={"field_id": 999999, "value": "不存在"})
        self.assertEqual(r.status_code, 404)

    def test_invalid_field_type_rejected_on_add_and_patch(self):
        r = self.c.post("/api/fields", json={"name": "壞欄", "field_type": "number"})
        self.assertEqual(r.status_code, 422)

        fid = self.c.get("/api/fields").json()[0]["field_id"]
        r = self.c.put(f"/api/fields/{fid}", json={"field_type": "number"})
        self.assertEqual(r.status_code, 422)

    def test_default_option_must_be_created_with_field_and_belong_to_field(self):
        fid = self.c.post("/api/fields", json={"name": "版型一"}).json()["field_id"]
        other_fid = self.c.post("/api/fields", json={"name": "版型二"}).json()["field_id"]
        other_oid = self._opt(other_fid, "亮面")

        r = self.c.post("/api/fields", json={
            "name": "不應有預設", "default_option_id": other_oid})
        self.assertEqual(r.status_code, 422)

        r = self.c.put(f"/api/fields/{fid}",
                       json={"default_option_id": 999999})
        self.assertEqual(r.status_code, 422)
        r = self.c.put(f"/api/fields/{fid}",
                       json={"default_option_id": other_oid})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
