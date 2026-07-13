"""匯入工具其餘七類規則單測:充電線/行動電源/藍芽耳機/AppleWatch玻璃 拆解、
廠牌尾綴詞條、檔內重覆條碼改發自取碼(TL)+ 重跑不重覆。

樣本字串為虛構或去識別化,不含任何真實人名。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import get_conn, init_db
from tools.import_excel import (
    split_earphone_brand, split_powerbank_spec, is_cable_length,
    normalize_connector, parse_cable, split_watch_glass, glass_brand_tags,
    normalize_brand, category_attr_writes, parse_row, run_import,
    COL_CODE, COL_CATEGORY, COL_BRAND, COL_SPEC, COL_DESC, COL_CAT1, COL_CAT2,
    COL_PHONE_BRAND, COL_PHONE_MODEL, COL_NOTE,
)


def _raw(**kw):
    base = {c: None for c in (
        COL_CODE, COL_CATEGORY, COL_BRAND, COL_SPEC, COL_DESC, COL_CAT1,
        COL_CAT2, COL_PHONE_BRAND, COL_PHONE_MODEL, COL_NOTE)}
    base.update(kw)
    return base


# ===================== 純函式 =====================

class TestEarphoneBrand(unittest.TestCase):
    def test_normal_split(self):
        self.assertEqual(split_earphone_brand("AWEI T80氣傳導耳掛"),
                         ("AWEI", "T80氣傳導耳掛", False))
        self.assertEqual(split_earphone_brand("Genten TS-100"),
                         ("Genten", "TS-100", False))

    def test_rest_keeps_internal_space(self):
        self.assertEqual(split_earphone_brand("iSee Lite pro"),
                         ("iSee", "Lite pro", False))

    def test_double_space(self):
        self.assertEqual(split_earphone_brand("G'FIVE  GT-BT300"),
                         ("G'FIVE", "GT-BT300", False))

    def test_trailing_dash_brand(self):
        # 「DA-」→ 去尾端「-」→「DA」,不可疑
        self.assertEqual(split_earphone_brand("DA- DABT10 ANC降躁"),
                         ("DA", "DABT10 ANC降躁", False))

    def test_suspicious_brand_with_digit(self):
        brand, model, susp = split_earphone_brand("T6   Max")
        self.assertEqual((brand, model), ("T6", "Max"))
        self.assertTrue(susp)

    def test_empty(self):
        self.assertEqual(split_earphone_brand(None), (None, None, False))
        self.assertEqual(split_earphone_brand("  "), (None, None, False))


class TestPowerbankSpec(unittest.TestCase):
    def test_split_color(self):
        self.assertEqual(split_powerbank_spec("4代 CC-柔霧白"), ("4代 CC", "柔霧白"))
        self.assertEqual(split_powerbank_spec("4代 CL-柔霧白"), ("4代 CL", "柔霧白"))

    def test_last_dash_used(self):
        self.assertEqual(split_powerbank_spec("a-b-c"), ("a-b", "c"))

    def test_no_dash(self):
        self.assertEqual(split_powerbank_spec("MAGO Qi2 10000mAh"),
                         ("MAGO Qi2 10000mAh", None))

    def test_empty(self):
        self.assertEqual(split_powerbank_spec(None), (None, None))


class TestCableParse(unittest.TestCase):
    def test_is_length(self):
        self.assertTrue(is_cable_length("50公分"))
        self.assertTrue(is_cable_length("300公分"))
        self.assertFalse(is_cable_length("Type-C"))
        self.assertFalse(is_cable_length("公分"))

    def test_normalize_connector(self):
        self.assertEqual(normalize_connector("iPhone"), ("Lightning", True))
        self.assertEqual(normalize_connector("USB-Type-C"), ("Type-C", True))
        self.assertEqual(normalize_connector("Lightning"), ("Lightning", True))
        self.assertEqual(normalize_connector("Type-C"), ("Type-C", True))
        self.assertEqual(normalize_connector("PD"), ("PD", True))
        self.assertEqual(normalize_connector("雙C"), ("雙C", True))
        self.assertEqual(normalize_connector("MicroUSB"), ("MicroUSB", False))

    def test_style_a_spec_length_desc_connector(self):
        # 規格=長度、描述=接頭 → 接頭取自描述
        info = parse_cable("50公分", "Lightning")
        self.assertEqual(info["長度"], "50公分")
        self.assertEqual(info["接頭"], "Lightning")
        self.assertEqual(info["特性詞條"], [])
        self.assertEqual(info["warnings"], [])
        info2 = parse_cable("100公分", "USB-Type-C")
        self.assertEqual(info2["接頭"], "Type-C")
        self.assertEqual(info2["長度"], "100公分")

    def test_style_b_spec_connector_desc_length(self):
        # 規格=接頭、描述=長度 → 長度取自描述
        info = parse_cable("iPhone", "120公分")
        self.assertEqual(info["接頭"], "Lightning")
        self.assertEqual(info["長度"], "120公分")
        self.assertEqual(info["特性詞條"], [])

    def test_prefix_5a_into_tags(self):
        info = parse_cable("5A PD", "50公分")
        self.assertEqual(info["接頭"], "PD")
        self.assertEqual(info["長度"], "50公分")
        self.assertEqual(info["特性詞條"], ["5A"])

    def test_prefix_knight_into_tags(self):
        info = parse_cable("騎士 Type-C", "150公分")
        self.assertEqual(info["接頭"], "Type-C")
        self.assertEqual(info["特性詞條"], ["騎士"])

    def test_prefix_with_iphone(self):
        info = parse_cable("5A iPhone", "50公分")
        self.assertEqual(info["接頭"], "Lightning")
        self.assertEqual(info["特性詞條"], ["5A"])

    def test_unknown_connector_to_tags(self):
        # 無法辨識的接頭怪值:不污染「接頭」欄,改入特性詞條並警告
        info = parse_cable("MicroUSB", "50公分")
        self.assertIsNone(info["接頭"])
        self.assertEqual(info["特性詞條"], ["MicroUSB"])
        self.assertEqual(info["長度"], "50公分")
        self.assertEqual(len(info["warnings"]), 1)

    def test_unknown_connector_with_prefix_to_tags(self):
        # 前綴詞條照舊 + 怪值核心也進詞條
        info = parse_cable("5A 怪接頭XYZ", "50公分")
        self.assertIsNone(info["接頭"])
        self.assertEqual(info["特性詞條"], ["5A", "怪接頭XYZ"])


class TestWatchGlass(unittest.TestCase):
    def test_split_size(self):
        self.assertEqual(split_watch_glass("3D全玻璃 42mm"),
                         ("3D全玻璃", "42mm", False))
        self.assertEqual(split_watch_glass("3D全玻璃 49mm"),
                         ("3D全玻璃", "49mm", False))

    def test_no_size_warns(self):
        self.assertEqual(split_watch_glass("3D全玻璃"), ("3D全玻璃", None, True))
        # 尾段非 \d+mm → 整串入款式 + 警告
        self.assertEqual(split_watch_glass("全 玻璃"), ("全 玻璃", None, True))

    def test_empty(self):
        self.assertEqual(split_watch_glass(None), (None, None, False))


class TestGlassBrandTags(unittest.TestCase):
    def test_three_brand_tag_pairs(self):
        self.assertEqual(normalize_brand("硬派6倍強化", "鋼化玻璃"), "硬派")
        self.assertEqual(glass_brand_tags("硬派6倍強化"), ["6倍強化"])
        self.assertEqual(normalize_brand("COZY五倍強化", "鋼化玻璃"), "COZY")
        self.assertEqual(glass_brand_tags("COZY五倍強化"), ["五倍強化"])
        self.assertEqual(normalize_brand("COZY微晶盾", "鋼化玻璃"), "COZY")
        self.assertEqual(glass_brand_tags("COZY微晶盾"), ["微晶盾"])

    def test_no_tag_brand(self):
        self.assertEqual(glass_brand_tags("HODA"), [])
        self.assertEqual(glass_brand_tags(None), [])

    def test_new_aliases(self):
        self.assertEqual(normalize_brand("ETON_Watch玻璃", "AppleWatch玻璃"), "ETON")
        self.assertEqual(normalize_brand("ACEICE_Ai_Watch玻璃", "AppleWatch玻璃"),
                         "ACEICE")
        self.assertEqual(normalize_brand("犀牛盾充電線", "充電線"), "RS犀牛盾")


class TestCategoryAttrWrites(unittest.TestCase):
    def _writes(self, d):
        return category_attr_writes(parse_row(_raw(**d)))

    def test_case_rename(self):
        opts, texts, warns = self._writes(
            {COL_CODE: "X", COL_CATEGORY: "手機殼", COL_SPEC: "透明磁吸",
             COL_CAT1: "黑色"})
        self.assertEqual(opts, [("款式", "透明磁吸"), ("顏色", "黑色")])

    def test_case_both_empty_fills_transparent(self):
        # 手機殼款式/顏色兩欄皆空 → 款式填「透明」
        opts, _, _ = self._writes(
            {COL_CODE: "X", COL_CATEGORY: "手機殼"})
        self.assertEqual(opts, [("款式", "透明")])

    def test_case_partial_not_filled(self):
        # 只缺一欄(有顏色)→ 不補透明
        opts, _, _ = self._writes(
            {COL_CODE: "X", COL_CATEGORY: "手機殼", COL_CAT1: "黑色"})
        self.assertEqual(opts, [("顏色", "黑色")])

    def test_case_close_unbalanced_paren(self):
        # 來源缺右括號:匯入自動補齊(「磁吸(附掛環扣」→「磁吸(附掛環扣)」)
        opts, _, _ = self._writes(
            {COL_CODE: "X", COL_CATEGORY: "手機殼",
             COL_SPEC: "荒野廢土磁吸(附掛環扣", COL_CAT1: "黑色"})
        self.assertEqual(opts[0], ("款式", "荒野廢土磁吸(附掛環扣)"))

    def test_lens_rename(self):
        opts, _, _ = self._writes(
            {COL_CODE: "X", COL_CATEGORY: "鏡頭貼", COL_SPEC: "藍寶石",
             COL_CAT1: "燒鈦"})
        self.assertEqual(opts, [("材質", "藍寶石"), ("框色", "燒鈦")])

    def test_earphone_model_text_and_color(self):
        opts, texts, warns = self._writes(
            {COL_CODE: "X", COL_CATEGORY: "藍芽耳機",
             COL_BRAND: "AWEI T80氣傳導耳掛", COL_CAT1: "白色"})
        self.assertEqual(opts, [("顏色", "白色")])
        self.assertEqual(texts, [("型號", "T80氣傳導耳掛")])
        self.assertEqual(warns, [])

    def test_earphone_suspicious_warns(self):
        _, _, warns = self._writes(
            {COL_CODE: "X", COL_CATEGORY: "藍芽耳機", COL_BRAND: "T6   Max"})
        self.assertEqual(len(warns), 1)

    def test_powerbank_color_from_spec(self):
        opts, _, _ = self._writes(
            {COL_CODE: "X", COL_CATEGORY: "行動電源", COL_SPEC: "4代 CC-柔霧白"})
        self.assertEqual(opts, [("規格", "4代 CC"), ("顏色", "柔霧白")])

    def test_powerbank_color_from_cat1_priority(self):
        # 規格含「-」會拆出顏色(柔霧白),但分類1(奶茶)須優先蓋過,真正驗到優先權分支
        opts, _, _ = self._writes(
            {COL_CODE: "X", COL_CATEGORY: "行動電源",
             COL_SPEC: "4代 CC-柔霧白", COL_CAT1: "奶茶"})
        self.assertEqual(opts, [("規格", "4代 CC"), ("顏色", "奶茶")])

    def test_powerbank_color_cat1_when_spec_no_color(self):
        # 規格不含「-」→ 拆不出顏色,退回用分類1
        opts, _, _ = self._writes(
            {COL_CODE: "X", COL_CATEGORY: "行動電源",
             COL_SPEC: "MAGO Qi2 10000mAh", COL_CAT1: "奶茶"})
        self.assertEqual(opts, [("規格", "MAGO Qi2 10000mAh"), ("顏色", "奶茶")])

    def test_cable_style_a(self):
        opts, texts, _ = self._writes(
            {COL_CODE: "X", COL_CATEGORY: "充電線", COL_SPEC: "50公分",
             COL_DESC: "Lightning", COL_CAT1: "黑色"})
        self.assertEqual(opts, [("接頭", "Lightning"), ("長度", "50公分"),
                                ("顏色", "黑色")])
        self.assertEqual(texts, [])            # 描述不入共用欄

    def test_cable_style_b_prefix(self):
        opts, texts, _ = self._writes(
            {COL_CODE: "X", COL_CATEGORY: "充電線", COL_SPEC: "5A PD",
             COL_DESC: "50公分"})
        self.assertEqual(opts, [("接頭", "PD"), ("長度", "50公分"),
                                ("特性詞條", "5A")])

    def test_watch_split(self):
        opts, _, warns = self._writes(
            {COL_CODE: "X", COL_CATEGORY: "AppleWatch玻璃", COL_SPEC: "3D全玻璃 42mm"})
        self.assertEqual(opts, [("款式", "3D全玻璃"), ("尺寸", "42mm")])
        self.assertEqual(warns, [])

    def test_watch_bad_size_warns(self):
        opts, _, warns = self._writes(
            {COL_CODE: "X", COL_CATEGORY: "AppleWatch玻璃", COL_SPEC: "3D全玻璃"})
        self.assertEqual(opts, [("款式", "3D全玻璃")])
        self.assertEqual(len(warns), 1)


# ===================== run_import 整合 =====================

class TestRunImportIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pos.db")
        init_db(self.db)
        self.conn = get_conn(self.db)

    def tearDown(self):
        self.conn.close()

    def _records(self, rows):
        return [parse_row(_raw(**r)) for r in rows]

    def _barcodes(self):
        return {r["barcode"]: (r["variant_id"], r["source"])
                for r in self.conn.execute(
                    "SELECT barcode, variant_id, source FROM Barcode")}

    def test_watch_fields_created(self):
        recs = self._records([
            {COL_CODE: "W1", COL_CATEGORY: "AppleWatch玻璃",
             COL_BRAND: "ETON_Watch玻璃", COL_SPEC: "3D全玻璃 42mm"},
        ])
        run_import(self.conn, recs)
        rows = self.conn.execute(
            "SELECT f.name AS fname, o.value AS val FROM VariantAttribute va "
            "JOIN AttributeField f ON f.field_id=va.field_id "
            "JOIN AttributeOption o ON o.option_id=va.option_id").fetchall()
        got = {(r["fname"], r["val"]) for r in rows}
        self.assertIn(("款式", "3D全玻璃"), got)
        self.assertIn(("尺寸", "42mm"), got)
        # 廠牌併入 ETON
        brand = self.conn.execute(
            "SELECT name FROM Brand").fetchone()["name"]
        self.assertEqual(brand, "ETON")

    def test_glass_brand_tag(self):
        recs = self._records([
            {COL_CODE: "G1", COL_CATEGORY: "鋼化玻璃",
             COL_BRAND: "硬派6倍強化", COL_SPEC: "亮面滿版"},
        ])
        run_import(self.conn, recs)
        tags = {r["val"] for r in self.conn.execute(
            "SELECT o.value AS val FROM VariantAttribute va "
            "JOIN AttributeField f ON f.field_id=va.field_id "
            "JOIN AttributeOption o ON o.option_id=va.option_id "
            "WHERE f.name='特性詞條'")}
        self.assertIn("6倍強化", tags)
        self.assertEqual(
            self.conn.execute("SELECT name FROM Brand").fetchone()["name"], "硬派")

    def test_dup_barcode_reassign_and_rerun(self):
        # 先放一列既有 TL 碼(TL100000005),驗證計數器種子;再放一組檔內重覆(不同屬性)
        rows = [
            {COL_CODE: "TL100000005", COL_CATEGORY: "行動電源",
             COL_BRAND: "Koopin行動電源", COL_SPEC: "半固態 5000mAh"},
            {COL_CODE: "4711229542344", COL_CATEGORY: "行動電源",
             COL_BRAND: "Lapo行動電源", COL_SPEC: "4代 CC-柔霧白"},
            {COL_CODE: "4711229542344", COL_CATEGORY: "行動電源",
             COL_BRAND: "Lapo行動電源", COL_SPEC: "4代 CL-柔霧白"},
        ]
        stats, warnings = run_import(self.conn, self._records(rows))
        self.conn.commit()
        self.assertEqual(stats["reassigned"], 1)
        bcs = self._barcodes()
        # 原條碼保留 factory;改發 TL 越過既有 TL100000005 → TL100000006,store
        self.assertIn("4711229542344", bcs)
        self.assertEqual(bcs["4711229542344"][1], "factory")
        self.assertIn("TL100000006", bcs)
        self.assertEqual(bcs["TL100000006"][1], "store")
        # 兩個不同 CC/CL 變體 + TL100000005 那筆 = 3 變體
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM Variant").fetchone()["c"], 3)
        self.assertTrue(any("改發自取碼" in w for w in warnings))

        # 重跑:同 Product + 相同屬性組合已存在 → 全跳過,不新增變體/條碼
        stats2, _ = run_import(self.conn, self._records(rows))
        self.conn.commit()
        self.assertEqual(stats2["added_variants"], 0)
        self.assertEqual(stats2["added_barcodes"], 0)
        self.assertEqual(stats2["reassigned"], 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM Variant").fetchone()["c"], 3)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM Barcode").fetchone()["c"], 3)

    def test_identical_dup_not_duplicated(self):
        # 完全相同的兩列共用同條碼 → 只建一個變體,不改發
        rows = [
            {COL_CODE: "DUP1", COL_CATEGORY: "手機殼", COL_BRAND: "imos手機殼",
             COL_SPEC: "透明磁吸", COL_PHONE_BRAND: "iPhone",
             COL_PHONE_MODEL: "iPhone17"},
            {COL_CODE: "DUP1", COL_CATEGORY: "手機殼", COL_BRAND: "imos手機殼",
             COL_SPEC: "透明磁吸", COL_PHONE_BRAND: "iPhone",
             COL_PHONE_MODEL: "iPhone17"},
        ]
        stats, _ = run_import(self.conn, self._records(rows))
        self.conn.commit()
        self.assertEqual(stats["reassigned"], 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM Variant").fetchone()["c"], 1)


if __name__ == "__main__":
    unittest.main()
