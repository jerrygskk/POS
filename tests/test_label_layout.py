"""Label layout behaviour: fixed size, wrapping rules, and print-safe pixels."""
import hashlib
import unittest
from unittest.mock import patch

from lib import label_layout
from lib.label_layout import _name_lines, _spec_lines, _font, render_label
from PIL import Image, ImageDraw

_SHORT_NAME = "犀牛盾 保護殼"
_LONG_NAME = "犀牛盾 iPhone 15 Pro Max 防摔殼"
_SHORT_SPEC = "MOD NX｜深湖藍"
_LONG_SPEC = "MOD NX｜深湖藍｜含邊框｜二入組｜附擦拭布"
_CODE = "TL0000205"


def _draw():
    return ImageDraw.Draw(Image.new("RGB", (320, 160), "white"))


class LabelLayoutTests(unittest.TestCase):
    def test_no_models_matches_baseline_pixels(self):
        # 條碼固定在底部、號碼在條碼下方後重新產生（已目視確認），鎖住無型號排版。
        cases = (
            (_SHORT_NAME, _SHORT_SPEC, "$590", "da2b3debdc0c0b7c8f0d13a9b7cf664ef1b7138139763b3e56d27c78bf87b998"),
            (_LONG_NAME, _LONG_SPEC, "$1290", "42e0cb7b643de48d14222a5a5890957d78e5a792fc85a0636499577702414e9c"),
            ("超長品名" * 40, _LONG_SPEC, "", "5c6dfde5048e8f66c2618b84b97ec27e621d0b579fd22a02a929a9af0613dec7"),
        )
        for name, spec, price, expected in cases:
            for kwargs in ({}, {"models": ()}):
                with self.subTest(name=name, kwargs=kwargs):
                    image = render_label(name, spec, price, _CODE, **kwargs)
                    self.assertEqual(hashlib.sha256(image.tobytes()).hexdigest(), expected)

    def test_model_line_lists_models_that_fit(self):
        draw = _draw()
        font = _font("C:/Windows/Fonts/msjh.ttc", 14)
        for models, expected in (((), ""), (("i17 ProMax",), "i17 ProMax"),
                                 (("i17", "i16", "i15"), "i17、i16、i15")):
            with self.subTest(models=models):
                self.assertEqual(label_layout._model_line(draw, models, font, 296), expected)

    def test_model_line_keeps_maximum_fitting_prefix_and_total(self):
        draw = _draw()
        font = _font("C:/Windows/Fonts/msjh.ttc", 14)
        models = [f"iPhone {i} Pro Max" for i in range(17, 9, -1)]
        line = label_layout._model_line(draw, models, font, 296)
        self.assertTrue(line.endswith(" 等 8 款"))
        self.assertLessEqual(label_layout._width(draw, line, font), 296)
        prefix = line.removesuffix(" 等 8 款")
        count = len(prefix.split("、"))
        self.assertEqual(prefix, "、".join(models[:count]))
        self.assertGreater(label_layout._width(draw, "、".join(models[:count + 1]) + " 等 8 款", font), 296)

    def test_overlong_model_keeps_ellipsis_and_total_suffix(self):
        # 虛構極端型號，確保第一個名稱本身就超過整行寬度。
        models = ["測試超長型號" * 12] * 8
        draw = _draw()
        font = _font("C:/Windows/Fonts/msjh.ttc", 14)
        line = label_layout._model_line(draw, models, font, 296)
        self.assertTrue(line.endswith("… 等 8 款"))
        self.assertLessEqual(label_layout._width(draw, line, font), 296)
        self.assertGreater(label_layout._width(draw, line.removesuffix("… 等 8 款") + "號… 等 8 款", font), 296)

    def test_single_overlong_model_has_no_total_suffix(self):
        # 虛構極端型號；單一型號只需刪節號，不顯示款數。
        draw = _draw()
        font = _font("C:/Windows/Fonts/msjh.ttc", 14)
        line = label_layout._model_line(draw, ["測試超長型號" * 12], font, 296)
        self.assertTrue(line.endswith("…"))
        self.assertNotIn("等 1 款", line)
        self.assertLessEqual(label_layout._width(draw, line, font), 296)

    def test_models_keep_bottom_margin_barcode_and_black_white_pixels(self):
        models = [f"iPhone {i} Pro Max" for i in range(17, 9, -1)]
        image = render_label(_LONG_NAME, _LONG_SPEC, "$1290", _CODE, models=models).convert("L")
        self.assertEqual(set(image.crop((0, 154, 320, 160)).get_flattened_data()), {255})
        self.assertIn(0, image.crop((12, 112, 308, 144)).get_flattened_data())
        self.assertEqual(set(image.get_flattened_data()) - {0, 255}, set())

    def test_models_sacrifice_second_name_line_before_second_spec_line(self):
        draw = _draw()
        price_width = label_layout._width(draw, "$1290", _font("C:/Windows/Fonts/msjhbd.ttc", 22))
        self.assertEqual(len(_name_lines(draw, _LONG_NAME, price_width)[0]), 2)
        font = _font("C:/Windows/Fonts/msjh.ttc", 14)
        spec = _spec_lines(draw, _LONG_SPEC, font, 296, 296)
        self.assertEqual(len(spec), 2)
        original = ImageDraw.ImageDraw.text
        with patch.object(ImageDraw.ImageDraw, "text", autospec=True, side_effect=original) as text:
            render_label(_LONG_NAME, _LONG_SPEC, "$1290", _CODE, models=["i17 ProMax"])
        printed = [(call.args[1], call.args[2]) for call in text.call_args_list]
        name = label_layout._fit(draw, _LONG_NAME, "C:/Windows/Fonts/msjhbd.ttc", (18,), 296 - price_width - 8)[0]
        self.assertEqual(printed, [((308 - price_width, 16), "$1290"), ((12, 16), name),
                                  ((12, 38), "i17 ProMax"), ((12, 56), spec[0]),
                                  ((12, 74), spec[1]),
                                  # 條碼固定頂 86、高 50;號碼置中、貼條碼下 2 點(字頂自帶 5 點空白)
                                  (((320 - label_layout._width(draw, _CODE, font)) // 2, 86 + 50 + 2 - 5), _CODE)])

    def test_barcode_position_is_fixed_regardless_of_content(self):
        """條碼固定在底部:內容多寡不影響條碼位置與高度。"""
        self.assertEqual((label_layout._BARCODE_TOP, label_layout._BARCODE_HEIGHT), (86, 50))
        self.assertEqual(label_layout._BARCODE_TOP + label_layout._BARCODE_HEIGHT + label_layout._CODE_AREA,
                         160 - label_layout._BOTTOM)
        cases = (("短", "", "", ()), (_SHORT_NAME, _SHORT_SPEC, "$590", ()),
                 (_LONG_NAME, _LONG_SPEC, "$1290", ()), (_LONG_NAME, _LONG_SPEC, "$1290", ["i17"]),
                 ("超長品名" * 40, _LONG_SPEC + "｜超長規格段落", "", [f"iPhone {i}" for i in range(17, 9, -1)]))
        for name, spec, price, models in cases:
            with self.subTest(name=name[:10], spec=spec):
                with patch.object(Image.Image, "paste", autospec=True, side_effect=Image.Image.paste) as paste:
                    with patch("lib.label_layout._barcode_image", wraps=label_layout._barcode_image) as barcode:
                        render_label(name, spec, price, _CODE, models=models)
                barcode.assert_called_once_with(_CODE, 50)
                self.assertEqual(paste.call_args.args[2], (12, 86))

    def test_minimal_content_after_sacrifice_fits_above_barcode(self):
        """最精簡內容(最大字品名一行＋型號一行＋規格一行)一定排得進條碼上方 2 點以外。"""
        spec_font = _font("C:/Windows/Fonts/msjh.ttc", 14)
        self.assertLessEqual(label_layout._TOP + (26 + 4) + 2 * (spec_font.size + 4), label_layout._TEXT_LIMIT)

    def test_barcode_bars_fill_reserved_height(self):
        """條碼產生器預設上下各留空白,歸零後黑條要佔滿預留高度。"""
        image = label_layout._barcode_image(_CODE, 50).convert("L")
        self.assertIn(0, image.crop((0, 0, 296, 1)).get_flattened_data())
        self.assertIn(0, image.crop((0, 49, 296, 50)).get_flattened_data())

    def test_render_label_has_fixed_printer_dimensions(self):
        image = render_label(_SHORT_NAME, _SHORT_SPEC, "$590", _CODE)
        self.assertEqual(image.size, (320, 160))
        self.assertEqual(image.width % 8, 0)

    def test_render_label_accepts_empty_price_and_absurd_name(self):
        image = render_label("超長品名" * 40, _LONG_SPEC, "", _CODE)
        self.assertEqual(image.size, (320, 160))

    def test_barcode_area_contains_black_pixels(self):
        image = render_label(_SHORT_NAME, _SHORT_SPEC, "$590", _CODE).convert("L")
        self.assertIn(0, image.crop((12, 90, 308, 140)).get_flattened_data())

    def test_text_is_drawn_without_anti_aliasing(self):
        """熱感列印只有黑白：灰階筆畫會在二值化時消失，筆畫密的字會缺半邊。"""
        image = render_label(_LONG_NAME, "MOD NX｜深湖藍｜含邊框", "$1290", _CODE).convert("L")
        self.assertEqual(set(image.get_flattened_data()) - {0, 255}, set())

    def test_number_sits_centered_below_barcode_above_margin(self):
        draw, font = _draw(), _font("C:/Windows/Fonts/msjh.ttc", 14)
        for code in (_CODE, "4711866050639", "FACTORY-LONG-ABC-123456789"):
            with self.subTest(code=code):
                original = ImageDraw.ImageDraw.text
                with patch.object(ImageDraw.ImageDraw, "text", autospec=True, side_effect=original) as text:
                    image = render_label(_LONG_NAME, _LONG_SPEC, "$1290", code, models=["i17"]).convert("L")
                (x, y), last = text.call_args_list[-1].args[1:3]
                self.assertEqual(last, code)
                self.assertEqual(x, (320 - label_layout._width(draw, code, font)) // 2)
                bottom = y + draw.textbbox((0, 0), code, font=font)[3]
                self.assertLessEqual(bottom, 160 - label_layout._BOTTOM)
                self.assertEqual(set(image.crop((0, 152, 320, 160)).get_flattened_data()), {255})

    def test_bottom_margin_is_never_consumed(self):
        for name, spec in ((_SHORT_NAME, _SHORT_SPEC), (_LONG_NAME, _LONG_SPEC)):
            with self.subTest(name=name):
                image = render_label(name, spec, "$1290", _CODE).convert("L")
                self.assertEqual(set(image.crop((0, 154, 320, 160)).get_flattened_data()), {255})

    def test_short_name_stays_on_one_line_and_long_name_wraps(self):
        draw = _draw()
        self.assertEqual(len(_name_lines(draw, _SHORT_NAME, 60)[0]), 1)
        self.assertEqual(len(_name_lines(draw, _LONG_NAME, 60)[0]), 2)

    def test_wrapped_name_uses_a_smaller_font_than_a_short_one(self):
        draw = _draw()
        self.assertGreater(_name_lines(draw, _SHORT_NAME, 60)[1].size,
                           _name_lines(draw, _LONG_NAME, 60)[1].size)

    def test_specification_wraps_only_at_the_separator(self):
        lines = _spec_lines(_draw(), _LONG_SPEC, _font("C:/Windows/Fonts/msjh.ttc", 14), 150, 296)
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertFalse(line.startswith("｜"))
            self.assertFalse(line.endswith("｜"))
        self.assertEqual("｜".join(lines), _LONG_SPEC)

    def test_specification_that_cannot_fit_two_lines_is_marked_as_truncated(self):
        lines = _spec_lines(_draw(), _LONG_SPEC, _font("C:/Windows/Fonts/msjh.ttc", 14), 60, 60)
        self.assertTrue(lines[-1].endswith("…"))

    def test_specification_short_enough_stays_on_one_line(self):
        lines = _spec_lines(_draw(), _SHORT_SPEC, _font("C:/Windows/Fonts/msjh.ttc", 14), 296, 296)
        self.assertEqual(lines, [_SHORT_SPEC])
