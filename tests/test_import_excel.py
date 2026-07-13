"""匯入純函式單測:廠牌正規化、型號拆解、列解析、分組鍵。

樣本字串為虛構或去識別化,不含任何真實人名。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.import_excel import (
    normalize_brand, split_models, parse_row, product_key, product_name, clean,
    close_unbalanced_parens,
    glass_spec, glass_layout, glass_attrs,
    GLASS_SPEC_FIELD, GLASS_TAGS_FIELD, GLASS_LAYOUT_FIELD,
    COL_CODE, COL_CATEGORY, COL_BRAND, COL_SPEC, COL_DESC, COL_CAT1, COL_CAT2,
    COL_PHONE_BRAND, COL_PHONE_MODEL, COL_NOTE,
)
from base import raw_row as _raw


class TestClean(unittest.TestCase):
    def test_strip_and_none(self):
        self.assertEqual(clean("  x "), "x")
        self.assertIsNone(clean(None))
        self.assertIsNone(clean("   "))
        self.assertIsNone(clean("nan"))
        self.assertEqual(clean(123), "123")

    def test_integer_float_no_decimal(self):
        # 商品編碼/條碼被 Excel 讀成整數值的 float,不可帶「.0」
        self.assertEqual(clean(4711229542344.0), "4711229542344")
        self.assertEqual(clean(20.0), "20")
        # 真正有小數的值保留(非條碼欄,不強轉)
        self.assertEqual(clean(6.1), "6.1")


class TestCloseParens(unittest.TestCase):
    def test_missing_right(self):
        self.assertEqual(close_unbalanced_parens("磁吸(附掛環扣"), "磁吸(附掛環扣)")

    def test_balanced_noop(self):
        self.assertEqual(close_unbalanced_parens("磁吸(附掛環扣)"), "磁吸(附掛環扣)")
        self.assertEqual(close_unbalanced_parens("無括號"), "無括號")

    def test_nested_missing(self):
        self.assertEqual(close_unbalanced_parens("A(B(C"), "A(B(C))")

    def test_extra_right_untouched(self):
        # 多餘右括號不屬本函式職責,不應亂動
        self.assertEqual(close_unbalanced_parens("A)"), "A)")


class TestNormalizeBrand(unittest.TestCase):
    def test_strip_category_suffix(self):
        self.assertEqual(normalize_brand("DAPAD手機殼", "手機殼"), "DAPAD")
        self.assertEqual(normalize_brand("imos鏡頭貼", "鏡頭貼"), "imos")

    def test_strip_product_line_suffix(self):
        self.assertEqual(normalize_brand("COZY五倍強化", "鋼化玻璃"), "COZY")
        self.assertEqual(normalize_brand("COZY微晶盾", "鋼化玻璃"), "COZY")
        self.assertEqual(normalize_brand("DAPAD四角", "手機殼"), "DAPAD")
        self.assertEqual(normalize_brand("XMART皮套", "手機殼"), "XMART")

    def test_merge_same_brand_variants(self):
        self.assertEqual(normalize_brand("imos", "鋼化玻璃"), "imos")
        self.assertEqual(normalize_brand("imos手機殼", "手機殼"), "imos")
        self.assertEqual(normalize_brand("imos鏡頭貼", "鏡頭貼"), "imos")

    def test_keep_bilingual_full_name(self):
        self.assertEqual(normalize_brand("RS犀牛盾", "手機殼"), "RS犀牛盾")
        self.assertEqual(normalize_brand("Baseus倍思", "充電線"), "Baseus倍思")

    def test_clean_brand_unchanged(self):
        self.assertEqual(normalize_brand("HODA", "鋼化玻璃"), "HODA")

    def test_unresolvable_kept(self):
        self.assertEqual(normalize_brand("多卡槽牛皮皮套", "手機殼"), "多卡槽牛皮皮套")

    def test_rule_fallback_unseen(self):
        self.assertEqual(normalize_brand("FOO手機殼", "手機殼"), "FOO")

    def test_empty(self):
        self.assertIsNone(normalize_brand(None, "手機殼"))
        self.assertIsNone(normalize_brand("  ", "手機殼"))


class TestSplitModels(unittest.TestCase):
    def test_single(self):
        self.assertEqual(split_models("iPhone17pro")[0], ["iPhone 17 Pro"])

    def test_spacing_normalized(self):
        self.assertEqual(split_models("iPhone17 ProMax")[0], ["iPhone 17 Pro Max"])
        self.assertEqual(split_models("iPhone17promax")[0], ["iPhone 17 Pro Max"])
        self.assertEqual(split_models("iPhone17Air")[0], ["iPhone 17 Air"])

    def test_fullwidth_paren_size(self):
        # 尺寸括號用全形（）也要能去除,型號正常拆解(半形已有 test_size_paren 涵蓋)
        self.assertEqual(split_models("iPhone14（6.1）")[0], ["iPhone 14"])
        self.assertEqual(
            split_models("iPhone14/13/13pro（6.1）共用")[0],
            ["iPhone 14", "iPhone 13", "iPhone 13 Pro"])

    def test_split_with_prefix_completion(self):
        self.assertEqual(
            split_models("iPhone14/13/13pro(6.1)共用")[0],
            ["iPhone 14", "iPhone 13", "iPhone 13 Pro"])

    def test_split_promax_tail(self):
        self.assertEqual(
            split_models("iPhone16pro/16promax共用")[0],
            ["iPhone 16 Pro", "iPhone 16 Pro Max"])

    def test_legacy_models(self):
        self.assertEqual(
            split_models("iPhoneSE2/SE3/7/8(4.7)")[0],
            ["iPhone SE2", "iPhone SE3", "iPhone 7", "iPhone 8"])
        self.assertEqual(
            split_models("iPhone11promax/Xsmax(6.5)")[0],
            ["iPhone 11 Pro Max", "iPhone XS Max"])

    def test_typo_prefix(self):
        self.assertEqual(
            split_models("iPone12mini/12/11")[0],
            ["iPhone 12 mini", "iPhone 12", "iPhone 11"])

    def test_dedup(self):
        self.assertEqual(split_models("iPhone17pro/17 Pro")[0], ["iPhone 17 Pro"])

    def test_unparseable_kept_with_warning(self):
        names, warns = split_models("完全看不懂的型號")
        self.assertEqual(names, [])
        self.assertEqual(warns, ["完全看不懂的型號"])

    def test_empty(self):
        self.assertEqual(split_models(None), ([], []))
        self.assertEqual(split_models("  "), ([], []))

    def test_custom_brand_prefix(self):
        self.assertEqual(split_models("14", "Galaxy")[0], ["Galaxy 14"])


class TestParseRow(unittest.TestCase):
    def test_glass_row(self):
        rec = parse_row(_raw(
            **{COL_CODE: "TL100000001", COL_CATEGORY: "鋼化玻璃",
               COL_BRAND: "Dr.TOUGH硬博士", COL_SPEC: "亮面滿版",
               COL_CAT1: "滿版", COL_PHONE_BRAND: "iPhone",
               COL_PHONE_MODEL: "iPhone17/17pro/16pro共用"}))
        self.assertEqual(rec["barcode"], "TL100000001")
        self.assertEqual(rec["category"], "鋼化玻璃")
        self.assertEqual(rec["brand"], "Dr.TOUGH硬博士")
        self.assertEqual(rec["phone_brand"], "iPhone")
        self.assertEqual(rec["models"],
                         ["iPhone 17", "iPhone 17 Pro", "iPhone 16 Pro"])
        # 規格/分類1 進 select_attrs;分類2/商品描述空
        self.assertEqual(rec["select_attrs"], {"規格": "亮面滿版", "分類1": "滿版"})
        self.assertIsNone(rec["desc"])
        self.assertIsNone(rec["note"])

    def test_no_barcode_returns_none(self):
        self.assertIsNone(parse_row(_raw(**{COL_CATEGORY: "鋼化玻璃"})))

    def test_desc_and_cat2(self):
        rec = parse_row(_raw(**{
            COL_CODE: "X1", COL_CATEGORY: "手機殼", COL_BRAND: "imos手機殼",
            COL_DESC: "限量款", COL_CAT2: "透明", COL_NOTE: "備註內容"}))
        self.assertEqual(rec["brand"], "imos")
        self.assertEqual(rec["desc"], "限量款")
        self.assertEqual(rec["select_attrs"], {"分類2": "透明"})
        self.assertEqual(rec["note"], "備註內容")

    def test_model_without_phone_brand_not_split(self):
        # 無手機品牌 → 不拆型號(避免亂補前綴)
        rec = parse_row(_raw(**{
            COL_CODE: "X2", COL_CATEGORY: "充電線", COL_PHONE_MODEL: "iPhone17"}))
        self.assertEqual(rec["models"], [])

    def test_model_warnings(self):
        rec = parse_row(_raw(**{
            COL_CODE: "X3", COL_CATEGORY: "鋼化玻璃", COL_PHONE_BRAND: "iPhone",
            COL_PHONE_MODEL: "看不懂"}))
        self.assertEqual(rec["models"], [])
        self.assertEqual(rec["model_warnings"], ["看不懂"])

    def test_unresolvable_brand_flag(self):
        rec = parse_row(_raw(**{
            COL_CODE: "X4", COL_CATEGORY: "手機殼", COL_BRAND: "多卡槽牛皮皮套"}))
        self.assertFalse(rec["brand_resolvable"])
        self.assertEqual(rec["brand"], "多卡槽牛皮皮套")


class TestProductKey(unittest.TestCase):
    def test_key_by_category_and_brand(self):
        r1 = parse_row(_raw(**{COL_CODE: "A", COL_CATEGORY: "鋼化玻璃",
                               COL_BRAND: "HODA", COL_SPEC: "亮面"}))
        r2 = parse_row(_raw(**{COL_CODE: "B", COL_CATEGORY: "鋼化玻璃",
                               COL_BRAND: "HODA", COL_SPEC: "霧面"}))
        r3 = parse_row(_raw(**{COL_CODE: "C", COL_CATEGORY: "鋼化玻璃",
                               COL_BRAND: "imos", COL_SPEC: "亮面"}))
        # 同種類同廠牌 → 同款;不同廠牌 → 不同款
        self.assertEqual(product_key(r1), product_key(r2))
        self.assertNotEqual(product_key(r1), product_key(r3))

    def test_name(self):
        r = parse_row(_raw(**{COL_CODE: "A", COL_CATEGORY: "鋼化玻璃",
                              COL_BRAND: "HODA"}))
        self.assertEqual(product_name(r), "HODA 鋼化玻璃")

    def test_name_no_brand(self):
        r = parse_row(_raw(**{COL_CODE: "A", COL_CATEGORY: "雜項"}))
        self.assertEqual(product_name(r), "雜項")


class TestGlassSpec(unittest.TestCase):
    """spec §3 對照表拆解純函式。"""

    def test_table_values(self):
        cases = {
            "亮面滿版": (["亮面"], []),
            "霧面": (["霧面"], []),
            "霧面藍光": (["霧面", "藍光"], []),
            "抗AR藍光": (["藍光"], ["抗AR"]),
            "360防窺": (["防窺"], ["360度"]),
            "霧面防窺(360度)": (["霧面", "防窺"], ["360度"]),
            "五倍防窺": (["防窺"], ["五倍強化"]),
            "電競霧面": (["霧面"], ["電競"]),
            "亮面藍寶石": (["亮面"], ["藍寶石"]),
            "9M藍寶石": (["亮面"], ["9M藍寶石"]),
            "SGS認證無色偏藍光": (["藍光"], ["SGS認證", "無色偏"]),
            "霧面藍光(SGS認證)": (["霧面", "藍光"], ["SGS認證"]),
            "低藍光防窺": (["藍光", "防窺"], ["低藍光"]),
            "藍寶石低藍光": (["藍光"], ["藍寶石", "低藍光"]),
        }
        for value, expected in cases.items():
            self.assertEqual(glass_spec(value), expected, value)

    def test_base_word_order(self):
        # 基礎詞永遠依 亮面/霧面/藍光/防窺 順位
        self.assertEqual(glass_spec("防窺霧面")[0], ["霧面", "防窺"])

    def test_no_base_defaults_bright(self):
        # 四基礎皆未現 → 補亮面(通則)
        self.assertEqual(glass_spec("純詞條")[0], ["亮面"])

    def test_low_blue_counts_as_blue(self):
        bases, tags = glass_spec("藍寶石低藍光")
        self.assertIn("藍光", bases)
        self.assertIn("低藍光", tags)

    def test_layout(self):
        self.assertEqual(glass_layout(None), ("滿版", []))
        self.assertEqual(glass_layout(""), ("滿版", []))
        self.assertEqual(glass_layout("滿版"), ("滿版", []))
        self.assertEqual(glass_layout("滿版(白)"), ("滿版", ["白"]))
        self.assertEqual(glass_layout("滿版(黑)"), ("滿版", ["黑"]))

    def test_attrs_merges_layout_tag(self):
        ga = glass_attrs("9M藍寶石", "滿版(白)")
        self.assertEqual(ga[GLASS_SPEC_FIELD], ["亮面"])
        self.assertEqual(ga[GLASS_TAGS_FIELD], ["9M藍寶石", "白"])
        self.assertEqual(ga[GLASS_LAYOUT_FIELD], "滿版")

    def test_attrs_blank_layout_full(self):
        ga = glass_attrs("霧面防窺", None)
        self.assertEqual(ga[GLASS_SPEC_FIELD], ["霧面", "防窺"])
        self.assertEqual(ga[GLASS_TAGS_FIELD], [])
        self.assertEqual(ga[GLASS_LAYOUT_FIELD], "滿版")


if __name__ == "__main__":
    unittest.main()
