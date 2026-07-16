import unittest

from lib.normalize import normalize_display, normalize_key


class TestNormalizeDisplay(unittest.TestCase):
    # 全形英數符號轉半形
    def test_fullwidth_to_halfwidth(self):
        self.assertEqual(normalize_display("ＡＢＣ１２３"), "ABC123")
        self.assertEqual(normalize_display("１２３．５"), "123.5")

    # 全形空白 U+3000 於頭尾去除
    def test_trim_ideographic_space(self):
        self.assertEqual(normalize_display("　abc　"), "abc")

    # tab 於頭尾去除
    def test_trim_tab(self):
        self.assertEqual(normalize_display("\tabc\t"), "abc")

    # 一般頭尾空白去除
    def test_trim_space(self):
        self.assertEqual(normalize_display("  abc  "), "abc")

    # 內部連續空白壓成一個半形空白(空白、tab、全形空白)
    def test_collapse_internal_whitespace(self):
        self.assertEqual(normalize_display("a   b"), "a b")
        self.assertEqual(normalize_display("a\t\tb"), "a b")
        self.assertEqual(normalize_display("a　　b"), "a b")
        self.assertEqual(normalize_display("a \t 　 b"), "a b")

    # 單一半形空白保留(中文分詞有意義)
    def test_single_space_preserved(self):
        self.assertEqual(normalize_display("亮面 霧面"), "亮面 霧面")
        self.assertEqual(normalize_display("紅 色"), "紅 色")

    # 單位空白:數字後接英文字母移除空白
    def test_unit_space_removed(self):
        self.assertEqual(normalize_display("200 cm"), "200cm")
        self.assertEqual(normalize_display("20 W"), "20W")

    # 單位空白:含小數
    def test_unit_space_removed_decimal(self):
        self.assertEqual(normalize_display("1.5 m"), "1.5m")

    # 英文後接數字不動(iPhone 15)
    def test_letter_then_digit_untouched(self):
        self.assertEqual(normalize_display("iPhone 15"), "iPhone 15")

    # 數字後接中文不動(單位規則僅限英文字母)
    def test_digit_then_chinese_untouched(self):
        self.assertEqual(normalize_display("5 個"), "5 個")
        self.assertEqual(normalize_display("200 公分"), "200 公分")

    # 保留原大小寫
    def test_case_preserved(self):
        self.assertEqual(normalize_display("Type-C"), "Type-C")
        self.assertEqual(normalize_display("type-c"), "type-c")

    # 空字串
    def test_empty_string(self):
        self.assertEqual(normalize_display(""), "")

    # 純空白(各種空白)回傳空字串
    def test_pure_whitespace(self):
        self.assertEqual(normalize_display("   "), "")
        self.assertEqual(normalize_display("　\t "), "")
        self.assertEqual(normalize_display("\t\t"), "")

    # 綜合:全形+單位空白+頭尾空白一起處理
    def test_combined(self):
        self.assertEqual(normalize_display("　２００ ｃｍ　"), "200cm")


class TestNormalizeKey(unittest.TestCase):
    # 大小寫不同 → key 相同(display 不同)
    def test_case_key_equal_display_differ(self):
        self.assertEqual(normalize_key("Type-C"), normalize_key("type-c"))
        self.assertNotEqual(normalize_display("Type-C"), normalize_display("type-c"))

    # key 為 display 的 casefold 結果
    def test_key_is_casefold_of_display(self):
        self.assertEqual(normalize_key("Type-C"), "type-c")
        self.assertEqual(normalize_key("ＡＢＣ"), "abc")

    # 全形+單位空白也套用於 key
    def test_key_applies_display_steps(self):
        self.assertEqual(normalize_key("２００ ＣＭ"), "200cm")

    # 中文不受 casefold 影響
    def test_key_chinese_unchanged(self):
        self.assertEqual(normalize_key("亮面 霧面"), "亮面 霧面")

    # 空字串
    def test_key_empty(self):
        self.assertEqual(normalize_key(""), "")


if __name__ == "__main__":
    unittest.main()
