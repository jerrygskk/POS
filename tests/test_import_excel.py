"""Excel importer 的純函式與資料庫回歸測試。"""
import os
import tempfile
import unittest

from lib.db import get_conn, init_db
from tools import import_excel as importer


class TestCaseProductLines(unittest.TestCase):
    def _row(self, brand):
        return importer.parse_row({
            importer.COL_CODE: "TEST-CODE",
            importer.COL_CATEGORY: importer.CASE_CATEGORY,
            importer.COL_BRAND: brand,
        })

    def test_dapad_corner_case_is_its_own_product(self):
        regular = self._row("DAPAD手機殼")
        corner = self._row("DAPAD四角")

        self.assertEqual(corner["brand"], "DAPAD")
        self.assertEqual(importer.product_name(corner), "DAPAD 四角殼")
        self.assertNotEqual(importer.product_key(corner), importer.product_key(regular))
        self.assertEqual(corner["select_attrs"], {})

    def test_xmart_wallet_case_is_its_own_product(self):
        regular = self._row("XMART手機殼")
        wallet = self._row("XMART皮套")

        self.assertEqual(wallet["brand"], "XMART")
        self.assertEqual(importer.product_name(wallet), "XMART 皮套")
        self.assertNotEqual(importer.product_key(wallet), importer.product_key(regular))
        self.assertEqual(wallet["select_attrs"], {})

    def test_aceice_air_case_is_its_own_product_without_feature_tag(self):
        regular = self._row("ACEICE手機殼")
        air_case = self._row("ACEICE空壓保護殼")

        self.assertEqual(air_case["brand"], "ACEICE")
        self.assertEqual(importer.product_name(air_case), "ACEICE 空壓保護殼")
        self.assertNotEqual(importer.product_key(air_case), importer.product_key(regular))
        self.assertEqual(air_case["select_attrs"], {})
        self.assertEqual(importer.glass_brand_tags(air_case["brand_raw"]), [])

    def test_aceice_air_case_imports_exact_brand_and_product(self):
        record = self._row("ACEICE空壓保護殼")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")
            init_db(db_path)
            conn = get_conn(db_path)
            try:
                stats, warnings = importer.run_import(conn, [record])
                brand = conn.execute("SELECT name FROM Brand").fetchone()["name"]
                product = conn.execute("SELECT name FROM Product").fetchone()["name"]
                tag_count = conn.execute(
                    "SELECT COUNT(*) FROM AttributeField WHERE field_type='tags'"
                ).fetchone()[0]
            finally:
                conn.close()

        self.assertEqual((brand, product), ("ACEICE", "ACEICE 空壓保護殼"))
        self.assertEqual(tag_count, 0)
        self.assertEqual(stats["variants_total"], 1)
        self.assertEqual(warnings, [])

    def test_unbranded_wallet_case_keeps_product_line_without_fake_brand(self):
        record = self._row("多卡槽牛皮皮套")

        self.assertIsNone(record["brand"])
        self.assertEqual(record["product_line"], "多卡槽牛皮皮套")
        self.assertEqual(importer.product_name(record), "多卡槽牛皮皮套")
        self.assertEqual(
            importer.product_key(record),
            (importer.CASE_CATEGORY, None, "多卡槽牛皮皮套"),
        )

    def test_unbranded_wallet_case_imports_variant_models_and_attributes(self):
        record = importer.parse_row({
            importer.COL_CODE: "TEST-WALLET-CASE",
            importer.COL_CATEGORY: importer.CASE_CATEGORY,
            importer.COL_BRAND: "多卡槽牛皮皮套",
            importer.COL_SPEC: "多卡槽",
            importer.COL_CAT1: "黑色",
            importer.COL_PHONE_BRAND: "iPhone",
            importer.COL_PHONE_MODEL: "17 Pro",
        })
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")
            init_db(db_path)
            conn = get_conn(db_path)
            try:
                stats, warnings = importer.run_import(conn, [record])
                product = conn.execute(
                    "SELECT name, brand_id FROM Product"
                ).fetchone()
                brands = [row["name"] for row in conn.execute(
                    "SELECT name FROM Brand ORDER BY name"
                )]
                model = conn.execute(
                    "SELECT pm.name FROM VariantModel vm "
                    "JOIN PhoneModel pm ON pm.model_id=vm.model_id"
                ).fetchone()["name"]
                attrs = {
                    row["field_name"]: row["option_value"]
                    for row in conn.execute(
                        "SELECT af.name AS field_name, ao.value AS option_value "
                        "FROM VariantAttribute va "
                        "JOIN AttributeField af ON af.field_id=va.field_id "
                        "JOIN AttributeOption ao ON ao.option_id=va.option_id"
                    )
                }
            finally:
                conn.close()

        self.assertEqual((product["name"], product["brand_id"]),
                         ("多卡槽牛皮皮套", None))
        self.assertNotIn("多卡槽牛皮皮套", brands)
        self.assertNotIn("皮套", brands)
        self.assertNotIn("無廠牌", brands)
        self.assertEqual(model, "iPhone 17 Pro")
        self.assertEqual(attrs, {"款式": "多卡槽", "顏色": "黑色"})
        self.assertEqual(stats["variants_total"], 1)
        self.assertEqual(stats["barcodes_total"], 1)
        self.assertEqual(warnings, [])


class TestGlassProductLines(unittest.TestCase):
    def test_cozy_glass_product_lines_parse_as_separate_products_without_brand_tags(self):
        fivefold = importer.parse_row({
            importer.COL_CODE: "TEST-COZY-FIVEFOLD",
            importer.COL_CATEGORY: importer.GLASS_CATEGORY,
            importer.COL_BRAND: "COZY五倍強化",
            importer.COL_SPEC: "亮面",
            importer.COL_CAT1: "滿版",
        })
        crystal = importer.parse_row({
            importer.COL_CODE: "TEST-COZY-CRYSTAL",
            importer.COL_CATEGORY: importer.GLASS_CATEGORY,
            importer.COL_BRAND: "COZY微晶盾",
            importer.COL_SPEC: "亮面",
            importer.COL_CAT1: "滿版",
        })

        self.assertEqual(
            (fivefold["brand"], fivefold["product_line"],
             importer.product_name(fivefold)),
            ("COZY", "五倍強化", "COZY 五倍強化"),
        )
        self.assertEqual(
            (crystal["brand"], crystal["product_line"],
             importer.product_name(crystal)),
            ("COZY", "微晶盾", "COZY 微晶盾"),
        )
        self.assertNotEqual(
            importer.product_key(fivefold), importer.product_key(crystal)
        )
        self.assertEqual(importer.glass_brand_tags(fivefold["brand_raw"]), [])
        self.assertEqual(importer.glass_brand_tags(crystal["brand_raw"]), [])

    def test_cozy_glass_product_lines_import_as_two_products_without_brand_tags(self):
        records = [
            importer.parse_row({
                importer.COL_CODE: "TEST-COZY-FIVEFOLD",
                importer.COL_CATEGORY: importer.GLASS_CATEGORY,
                importer.COL_BRAND: "COZY五倍強化",
                importer.COL_SPEC: "亮面",
                importer.COL_CAT1: "滿版",
            }),
            importer.parse_row({
                importer.COL_CODE: "TEST-COZY-CRYSTAL",
                importer.COL_CATEGORY: importer.GLASS_CATEGORY,
                importer.COL_BRAND: "COZY微晶盾",
                importer.COL_SPEC: "亮面",
                importer.COL_CAT1: "滿版",
            }),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")
            init_db(db_path)
            conn = get_conn(db_path)
            try:
                stats, warnings = importer.run_import(conn, records)
                products = [row["name"] for row in conn.execute(
                    "SELECT name FROM Product ORDER BY name"
                )]
                tag_values = conn.execute(
                    "SELECT COUNT(*) FROM VariantAttribute va "
                    "JOIN AttributeField af ON af.field_id=va.field_id "
                    "WHERE af.name=?",
                    (importer.GLASS_TAGS_FIELD,),
                ).fetchone()[0]
            finally:
                conn.close()

        self.assertEqual(products, ["COZY 五倍強化", "COZY 微晶盾"])
        self.assertEqual(tag_values, 0)
        self.assertEqual(stats["variants_total"], 2)
        self.assertEqual(warnings, [])

    def test_cozy_five_fold_privacy_spec_does_not_repeat_product_line_as_tag(self):
        record = importer.parse_row({
            importer.COL_CODE: "TEST-COZY-FIVEFOLD-PRIVACY",
            importer.COL_CATEGORY: importer.GLASS_CATEGORY,
            importer.COL_BRAND: "COZY五倍強化",
            importer.COL_SPEC: "五倍防窺",
            importer.COL_CAT1: "滿版",
        })

        # 「五倍強化」已在款名裡,規格不可再長出同名詞條。
        self.assertEqual(importer.product_name(record), "COZY 五倍強化")
        self.assertEqual(importer.glass_spec("五倍防窺"), (["防窺"], []))

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")
            init_db(db_path)
            conn = get_conn(db_path)
            try:
                stats, warnings = importer.run_import(conn, [record])
                specs = [row[0] for row in conn.execute(
                    "SELECT ao.value FROM VariantAttribute va "
                    "JOIN AttributeField af ON af.field_id=va.field_id "
                    "JOIN AttributeOption ao ON ao.option_id=va.option_id "
                    "WHERE af.name=?",
                    (importer.GLASS_SPEC_FIELD,),
                )]
                tag_values = conn.execute(
                    "SELECT COUNT(*) FROM VariantAttribute va "
                    "JOIN AttributeField af ON af.field_id=va.field_id "
                    "WHERE af.name=?",
                    (importer.GLASS_TAGS_FIELD,),
                ).fetchone()[0]
            finally:
                conn.close()

        self.assertEqual(specs, ["防窺"])
        self.assertEqual(tag_values, 0)
        self.assertEqual(stats["variants_total"], 1)
        self.assertEqual(warnings, [])

    def test_adamas_super_tough_keeps_six_fold_tag(self):
        records = [
            importer.parse_row({
                importer.COL_CODE: f"TEST-ADAMAS-{index:02d}",
                importer.COL_CATEGORY: importer.GLASS_CATEGORY,
                importer.COL_BRAND: "ADAMAS超強硬派",
                importer.COL_SPEC: "亮面",
                importer.COL_CAT1: "滿版",
            })
            for index in range(12)
        ]

        self.assertTrue(all(record["brand"] == "ADAMAS" for record in records))
        self.assertTrue(
            all(record["product_line"] == "超強硬派" for record in records)
        )
        self.assertTrue(
            all(importer.product_name(record) == "ADAMAS 超強硬派"
                for record in records)
        )
        # 款名「ADAMAS 超強硬派」不含倍數,6 倍必須留在詞條才查得到。
        self.assertTrue(
            all(importer.glass_brand_tags(record["brand_raw"]) == ["6倍強化"]
                for record in records)
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")
            init_db(db_path)
            conn = get_conn(db_path)
            try:
                stats, warnings = importer.run_import(conn, records)
                products = [tuple(row) for row in conn.execute(
                    "SELECT b.name, p.name FROM Product p "
                    "JOIN Brand b ON b.brand_id=p.brand_id"
                )]
                tag_values = [tuple(row) for row in conn.execute(
                    "SELECT ao.value, COUNT(*) FROM VariantAttribute va "
                    "JOIN AttributeField af ON af.field_id=va.field_id "
                    "JOIN AttributeOption ao ON ao.option_id=va.option_id "
                    "WHERE af.name=? GROUP BY ao.value",
                    (importer.GLASS_TAGS_FIELD,),
                )]
            finally:
                conn.close()

        self.assertEqual(products, [("ADAMAS", "ADAMAS 超強硬派")])
        self.assertEqual(tag_values, [("6倍強化", 12)])
        self.assertEqual(stats["variants_total"], 12)
        self.assertEqual(stats["barcodes_total"], 12)
        self.assertEqual(warnings, [])


class TestEarphoneProductModel(unittest.TestCase):
    def test_mees_t6_max_keeps_brand_and_product_model(self):
        record = importer.parse_row({
            importer.COL_CODE: "TEST-MEES-T6-MAX",
            importer.COL_CATEGORY: importer.EARPHONE_CATEGORY,
            importer.COL_BRAND: "MEES T6 MAX",
            importer.COL_CAT1: "黑色",
        })

        self.assertEqual(record["brand"], "MEES")
        self.assertEqual(record["earphone_model"], "T6 MAX")
        self.assertFalse(record["earphone_suspicious"])
        self.assertEqual(importer.product_name(record), "MEES 藍芽耳機")

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")
            init_db(db_path)
            conn = get_conn(db_path)
            try:
                stats, warnings = importer.run_import(conn, [record])
                result = conn.execute(
                    "SELECT b.name AS brand, p.name AS product, "
                    "va.text_value AS product_model, ao.value AS color "
                    "FROM Product p "
                    "JOIN Brand b ON b.brand_id=p.brand_id "
                    "JOIN Variant v ON v.product_id=p.product_id "
                    "JOIN VariantAttribute va ON va.variant_id=v.variant_id "
                    "JOIN AttributeField af ON af.field_id=va.field_id "
                    "LEFT JOIN VariantAttribute color_va "
                    "  ON color_va.variant_id=v.variant_id "
                    "LEFT JOIN AttributeField color_af "
                    "  ON color_af.field_id=color_va.field_id AND color_af.name='顏色' "
                    "LEFT JOIN AttributeOption ao "
                    "  ON ao.option_id=color_va.option_id "
                    "WHERE af.name='產品型號' AND color_af.field_id IS NOT NULL"
                ).fetchone()
            finally:
                conn.close()

        self.assertEqual(
            (result["brand"], result["product"],
             result["product_model"], result["color"]),
            ("MEES", "MEES 藍芽耳機", "T6 MAX", "黑色"),
        )
        self.assertEqual(stats["variants_total"], 1)
        self.assertFalse(any("廠牌可疑" in warning for warning in warnings))


class TestAceiceWatchGlass(unittest.TestCase):
    def _record(self):
        return importer.parse_row({
            importer.COL_CODE: "TEST-WATCH-GLASS",
            importer.COL_CATEGORY: importer.WATCH_CATEGORY,
            importer.COL_BRAND: "ACEICEWatch玻璃",
            importer.COL_SPEC: "3D全玻璃 45mm",
        })

    def test_normalized_excel_value_uses_aceice_brand_without_product_line(self):
        record = self._record()

        self.assertEqual(record["brand"], "ACEICE")
        self.assertIsNone(record["product_line"])
        self.assertEqual(importer.product_name(record), "ACEICE AppleWatch玻璃")
        self.assertEqual(importer.category_attr_writes(record), (
            [(importer.F_STYLE, "3D全玻璃"), (importer.F_SIZE, "45mm")],
            [],
            [],
        ))

    def test_import_keeps_watch_style_and_size_without_feature_tag(self):
        record = self._record()
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")
            init_db(db_path)
            conn = get_conn(db_path)
            try:
                stats, warnings = importer.run_import(conn, [record])
                product = conn.execute(
                    "SELECT p.name AS product, b.name AS brand "
                    "FROM Product p JOIN Brand b ON b.brand_id=p.brand_id"
                ).fetchone()
                attrs = {
                    row["field_name"]: row["option_value"]
                    for row in conn.execute(
                        "SELECT af.name AS field_name, ao.value AS option_value "
                        "FROM VariantAttribute va "
                        "JOIN AttributeField af ON af.field_id=va.field_id "
                        "JOIN AttributeOption ao ON ao.option_id=va.option_id"
                    )
                }
            finally:
                conn.close()

        self.assertEqual(dict(product), {
            "product": "ACEICE AppleWatch玻璃",
            "brand": "ACEICE",
        })
        self.assertEqual(attrs, {"款式": "3D全玻璃", "尺寸": "45mm"})
        self.assertNotIn("特性詞條", attrs)
        self.assertEqual(stats["variants_total"], 1)
        self.assertEqual(warnings, [])


class TestPhoneModelSorting(unittest.TestCase):
    def test_sort_key_orders_newest_generation_and_largest_variant_first(self):
        names = [
            "iPhone 16", "iPhone 17 Pro", "iPhone XR", "iPhone 13 mini",
            "iPhone SE3", "iPhone 17 Pro Max", "iPhone XS", "iPhone 17",
            "iPhone 13 Pro", "iPhone 17 Air", "iPhone XS Max", "iPhone 14",
            "iPhone 16 Plus", "iPhone SE2", "iPhone 12",
        ]

        self.assertEqual(importer.sorted_model_names(names), [
            "iPhone 17 Pro Max", "iPhone 17 Pro", "iPhone 17 Air", "iPhone 17",
            "iPhone 16 Plus", "iPhone 16",
            "iPhone 14",
            "iPhone SE3",                      # 2022,排在 14 與 13 之間
            "iPhone 13 Pro", "iPhone 13 mini",
            "iPhone 12",
            "iPhone SE2",                      # 2020,排在 12 與 11 之間
            "iPhone XS Max", "iPhone XS",
            "iPhone XR",                       # 同代入門款,排在標準款之後
        ])

    def test_sort_key_puts_unparsable_model_last(self):
        self.assertEqual(
            importer.sorted_model_names(["未知機型", "iPhone 12"]),
            ["iPhone 12", "未知機型"],
        )

    def test_import_writes_model_sort_and_keeps_manual_order(self):
        records = [
            importer.parse_row({
                importer.COL_CODE: f"TEST-MODEL-SORT-{index:02d}",
                importer.COL_CATEGORY: importer.GLASS_CATEGORY,
                importer.COL_BRAND: "HODA",
                importer.COL_SPEC: "亮面",
                importer.COL_CAT1: "滿版",
                importer.COL_PHONE_BRAND: "iPhone",
                importer.COL_PHONE_MODEL: model,
            })
            for index, model in enumerate(["16", "17promax", "13mini", "17"])
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")
            init_db(db_path)
            conn = get_conn(db_path)
            try:
                stats, warnings = importer.run_import(conn, records)
                ordered = [row["name"] for row in conn.execute(
                    "SELECT name FROM PhoneModel ORDER BY sort, model_id")]
                # 手動排序過(sort>0)的型號不得被下一次匯入覆蓋
                conn.execute(
                    "UPDATE PhoneModel SET sort=99 WHERE name='iPhone 17 Pro Max'")
                importer.run_import(conn, records)
                manual = conn.execute(
                    "SELECT sort FROM PhoneModel WHERE name='iPhone 17 Pro Max'"
                ).fetchone()[0]
            finally:
                conn.close()

        self.assertEqual(ordered, [
            "iPhone 17 Pro Max", "iPhone 17", "iPhone 16", "iPhone 13 mini",
        ])
        self.assertEqual(stats["resorted_models"], 4)
        self.assertEqual(manual, 99)
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
