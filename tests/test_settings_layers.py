import tempfile
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.db import db_conn, init_db
from lib.desktop_bridge import DesktopBridge
from lib.settings_service import SettingsFacade


class SettingsLayersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "pos.db"
        init_db(self.db)
        self.facade = SettingsFacade(self.db)
        self.bridge = DesktopBridge(facade=self.facade)

    def tearDown(self):
        self.tmp.cleanup()

    def test_bridge_unknown_action_is_stable_validation_error(self):
        result = self.bridge.invoke("__dict__", {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "validation_error")

    def test_bridge_rejects_invalid_action_and_payload_at_boundary(self):
        cases = [
            (None, {}, "validation_error"),
            ("categories.create", [], "validation_error"),
            ("categories.create", {}, "validation_error"),
            ("options.list", {}, "validation_error"),
            ("categories.delete", {"id": "1"}, "validation_error"),
            ("options.set_models", {"id": 1, "model_ids": ["2"]}, "validation_error"),
        ]
        for action, payload, code in cases:
            with self.subTest(action=action, payload=payload):
                result = self.bridge.invoke(action, payload)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], code)

    def test_bool_is_not_accepted_as_nullable_integer(self):
        cases = [
            ("categories.create", {"name": "殼", "sort": True}),
            ("fields.list", {"category_id": True}),
            ("fields.create", {"name": "版型", "category_id": True}),
            ("fields.create", {"name": "版型", "default_option_id": True}),
            ("models.create", {"phone_brand_id": True, "name": "15"}),
            ("models.list", {"phone_brand_id": True}),
        ]
        for action, payload in cases:
            with self.subTest(action=action, payload=payload):
                result = self.bridge.invoke(action, payload)
                self.assertEqual("validation_error", result["error"]["code"])

        self.assertTrue(self.bridge.invoke("categories.list", {"all": True})["ok"])
        self.assertTrue(self.bridge.invoke("categories.list", {"all": 0})["ok"])
        self.assertTrue(self.bridge.invoke("fields.list", {"category_id": None})["ok"])

    def test_unknown_top_level_key_is_rejected_without_create(self):
        result = self.bridge.invoke(
            "categories.create", {"name": "保護殼", "unexpected": True}
        )

        self.assertEqual("validation_error", result["error"]["code"])
        with db_conn(self.db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM Category WHERE name=?", ("保護殼",)
            ).fetchone()[0]
        self.assertEqual(0, count)

    def test_update_fields_reject_unknown_keys_without_writing(self):
        cases = [
            ("categories", "categories.create", "category_id", {"name": "類別"}),
            ("brands", "brands.create", "brand_id", {"name": "廠牌"}),
            ("phone_brands", "phone_brands.create", "phone_brand_id", {"name": "手機廠牌"}),
        ]
        phone_brand_id = self.facade.invoke(
            "phone_brands.create", {"name": "型號廠牌"}
        )["phone_brand_id"]
        model_id = self.facade.invoke(
            "models.create", {"phone_brand_id": phone_brand_id, "name": "型號"}
        )["model_id"]
        field_id = self.facade.invoke("fields.create", {"name": "欄位"})["field_id"]
        self.facade.invoke("options.create", {"field_id": field_id, "value": "選項"})
        option_id = self.facade.invoke("options.list", {"field_id": field_id})[0]["option_id"]

        updates = []
        tables = {
            "categories": ("Category", "category_id"),
            "brands": ("Brand", "brand_id"),
            "phone_brands": ("PhoneBrand", "phone_brand_id"),
        }
        for action_prefix, create_action, id_key, create_payload in cases:
            item_id = self.facade.invoke(create_action, create_payload)[id_key]
            updates.append((f"{action_prefix}.update", item_id, *tables[action_prefix], "name"))
        updates.extend([
            ("models.update", model_id, "PhoneModel", "model_id", "name"),
            ("fields.update", field_id, "AttributeField", "field_id", "name"),
            ("options.update", option_id, "AttributeOption", "option_id", "value"),
        ])

        for action, item_id, table, id_column, value_column in updates:
            with self.subTest(action=action):
                result = self.bridge.invoke(
                    action,
                    {"id": item_id, "fields": {value_column: "已改名", "unexpected": "value"}},
                )
                self.assertEqual("validation_error", result["error"]["code"])
                with db_conn(self.db) as conn:
                    name = conn.execute(
                        f"SELECT {value_column} FROM {table} WHERE {id_column}=?", (item_id,)
                    ).fetchone()[0]
                self.assertNotEqual("已改名", name)

    def test_update_fields_reject_wrong_types(self):
        cases = [
            ("categories.update", {"name": 1}),
            ("brands.update", {"sort": True}),
            ("phone_brands.update", {"active": "1"}),
            ("models.update", {"alias": 1}),
            ("fields.update", {"default_option_id": True}),
            ("options.update", {"value": None}),
        ]
        for action, fields in cases:
            with self.subTest(action=action):
                result = self.bridge.invoke(action, {"id": 1, "fields": fields})
                self.assertEqual("validation_error", result["error"]["code"])

    def test_field_name_and_type_update_is_atomic(self):
        field_id = self.facade.invoke(
            "fields.create", {"name": "原欄位", "field_type": "select"}
        )["field_id"]
        self.facade.invoke("fields.update", {
            "id": field_id,
            "fields": {"name": "新欄位", "field_type": "text"},
        })
        with db_conn(self.db) as conn:
            row = conn.execute(
                "SELECT name,field_type FROM AttributeField WHERE field_id=?", (field_id,)
            ).fetchone()
        self.assertEqual(tuple(row), ("新欄位", "text"))

        result = self.bridge.invoke("fields.update", {
            "id": field_id,
            "fields": {"name": "不應套用", "field_type": "invalid"},
        })
        self.assertEqual(result["error"]["code"], "validation_error")
        with db_conn(self.db) as conn:
            row = conn.execute(
                "SELECT name,field_type FROM AttributeField WHERE field_id=?", (field_id,)
            ).fetchone()
        self.assertEqual(tuple(row), ("新欄位", "text"))

    def test_settings_frontend_guards_category_load_and_combines_field_patch(self):
        source = (Path(__file__).parents[1] / "static/js/settings.js").read_text(encoding="utf-8")
        self.assertIn("const seq = ++this._loadSeq", source)
        self.assertIn("if (seq !== this._loadSeq) return", source)
        # 規格欄的儲存已搬到規格設定子視窗(field_editor.js)
        editor = (Path(__file__).parents[1] / "static/js/field_editor.js").read_text(encoding="utf-8")
        self.assertIn("await API.updateField(fieldId, patch)", editor)
        self.assertIn("await API.setCategoryField(this.categoryId, fieldId, setFields)", editor)

    def test_settings_template_list_has_model_row_and_row_actions(self):
        source = (Path(__file__).parents[1] / "static/js/settings.js").read_text(encoding="utf-8")
        html = (Path(__file__).parents[1] / "static/index.html").read_text(encoding="utf-8")
        # 手機型號為模板固定列,點列或列上按鈕切換 model_mode
        self.assertIn('const MODEL_ROW_ID = "__model__"', source)
        self.assertIn('name: "手機型號", field_type: "model"', source)
        self.assertIn('cat.model_mode === "required" ? "hidden" : "required"', source)
        self.assertIn('isModelRow(f)', html)
        # 新種類預設帶顏色與款式(選填)
        self.assertIn('const DEFAULT_CATEGORY_FIELDS = ["顏色", "款式"]', source)
        self.assertIn("await this.attachDefaultFields(r.category_id)", source)
        self.assertIn("{ sort: sort++, required: 0, active: 1 }", source)
        # 模板列操作鈕:✎ 修改與紅色 ✕(刪除前顯示影響筆數)
        self.assertIn('@click.stop="openFieldPopup(f)"', html)
        self.assertIn('class="btn-sm danger" @click.stop="deleteTemplateField(f)"', html)
        self.assertIn("此種類有 ${used} 筆商品填過此規格", source)
        self.assertIn("await API.deleteCategoryField(this.selCatId, f.field_id)", source)
        # 規格編輯改開子視窗:原本的網頁遮罩對話框已移除
        self.assertIn('page: "field_editor"', source)
        self.assertNotIn("fieldPopup", source)
        self.assertNotIn("fieldPopup", html)
        self.assertIn("window.PosDesktopLock.lock()", source)

    def test_field_editor_child_window_is_registered_and_self_contained(self):
        from lib.child_window import CHILD_PAGES
        self.assertEqual(CHILD_PAGES["field_editor"]["file"], "field_editor.html")
        page = (Path(__file__).parents[1] / "static/field_editor.html").read_text(encoding="utf-8")
        self.assertIn('<form class="dialog-shell" @submit.prevent="save">', page)
        self.assertIn("dialog-theme.css", page)
        editor = (Path(__file__).parents[1] / "static/js/field_editor.js").read_text(encoding="utf-8")
        self.assertIn('API.invoke("desktop.child_window.context", {})', editor)
        self.assertIn('API.invoke("desktop.child_window.close", { saved: true })', editor)

    def test_category_reference_guard_is_in_service(self):
        cid = self.facade.invoke("categories.create", {"name": "殼"})["category_id"]
        with db_conn(self.db) as conn:
            conn.execute("INSERT INTO Product(name,category_id) VALUES('商品',?)", (cid,))
            conn.commit()
        result = self.bridge.invoke("categories.delete", {"id": cid})
        self.assertEqual(result["error"]["code"], "conflict")

    def test_transaction_rolls_back_partial_link_replacement(self):
        bid = self.facade.invoke("brands.create", {"name": "廠牌"})["brand_id"]
        cid = self.facade.invoke("categories.create", {"name": "殼"})["category_id"]
        self.facade.invoke("brands.set_categories", {"id": bid, "category_ids": [cid]})
        result = self.bridge.invoke(
            "brands.set_categories", {"id": bid, "category_ids": [999999]})
        self.assertFalse(result["ok"])
        with db_conn(self.db) as conn:
            rows = conn.execute(
                "SELECT category_id FROM BrandCategory WHERE brand_id=?", (bid,)
            ).fetchall()
        self.assertEqual([cid], [row[0] for row in rows])

    def test_referenced_option_is_deactivated_and_links_are_cleared(self):
        cid = self.facade.invoke("categories.create", {"name": "膜"})["category_id"]
        fid = self.facade.invoke("fields.create", {"name": "版型", "category_id": cid})["field_id"]
        self.facade.invoke("options.create", {"field_id": fid, "value": "亮面"})
        oid = self.facade.invoke("options.list", {"field_id": fid})[0]["option_id"]
        with db_conn(self.db) as conn:
            product = conn.execute("INSERT INTO Product(name,category_id) VALUES('膜',?)", (cid,)).lastrowid
            variant = conn.execute("INSERT INTO Variant(product_id) VALUES(?)", (product,)).lastrowid
            conn.execute("INSERT INTO VariantAttribute(variant_id,field_id,option_id) VALUES(?,?,?)", (variant, fid, oid))
            conn.execute("UPDATE CategoryField SET default_option_id=? WHERE field_id=? AND category_id=?", (oid, fid, cid))
            conn.commit()
        out = self.facade.invoke("options.delete", {"id": oid})
        self.assertFalse(out["deleted"])
        with db_conn(self.db) as conn:
            self.assertEqual(0, conn.execute("SELECT active FROM AttributeOption WHERE option_id=?", (oid,)).fetchone()[0])
            self.assertIsNone(conn.execute("SELECT default_option_id FROM CategoryField WHERE field_id=?", (fid,)).fetchone()[0])

    def test_settings_frontend_uses_bridge_transport_with_bounded_wait(self):
        source = (Path(__file__).parents[1] / "static/js/api.js").read_text(encoding="utf-8")
        self.assertIn('window.addEventListener("pywebviewready"', source)
        self.assertIn("10000", source)
        self.assertIn("api.invoke(action, payload || {})", source)
        settings = (Path(__file__).parents[1] / "static/js/settings.js").read_text(encoding="utf-8")
        self.assertNotIn("fetch(", settings)

    def test_bridge_runtime_maps_error_codes_to_desktop_status(self):
        api_path = Path(__file__).parents[1] / "static/js/api.js"
        script = api_path.read_text(encoding="utf-8") + """
const codes = ['validation_error','not_found','conflict','database_error','internal_error'];
API._bridge = () => Promise.resolve({invoke: async (_a, p) => ({
  ok: false, error: {code: p.code, message: '失敗', details: {field: 'x'}}
})});
Promise.all(codes.map(async code => {
  try { await API.invoke('categories.create', {code}); }
  catch (e) { return {code: e.code, status: e.status, details: e.details}; }
})).then(rows => process.stdout.write(JSON.stringify(rows)));
"""
        proc = subprocess.run(
            ["node", "-"], input="global.window=global;\n" + script,
            text=True, encoding="utf-8", capture_output=True, check=True,
        )
        rows = {row["code"]: row for row in json.loads(proc.stdout)}
        self.assertEqual(422, rows["validation_error"]["status"])
        self.assertEqual(404, rows["not_found"]["status"])
        self.assertEqual(409, rows["conflict"]["status"])
        self.assertEqual(500, rows["database_error"]["status"])
        self.assertEqual(500, rows["internal_error"]["status"])
        self.assertEqual({"field": "x"}, rows["conflict"]["details"])


if __name__ == "__main__":
    unittest.main()
