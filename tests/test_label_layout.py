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
        # 修改前直接載入基準 commit 836190f 的 label_layout.py 產生，鎖住無型號排版。
        cases = (
            (_SHORT_NAME, _SHORT_SPEC, "$590", "5aa21f34ca801b1b819d31887cd849bfc0e21d11f205578478c679020a65963a"),
            (_LONG_NAME, _LONG_SPEC, "$1290", "257af7a91f4d4ccffccf56a146597dcc37443f1fc937b2406f477dadaa765cc7"),
            ("超長品名" * 40, _LONG_SPEC, "", "762f4f2b1e10bfab5d50662056bff99d92eaacca5666a916c683e3ae9c07d3ba"),
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
        spec = _spec_lines(draw, _LONG_SPEC, font, 296 - label_layout._width(draw, _CODE, font) - 8, 296)
        self.assertEqual(len(spec), 2)
        original = ImageDraw.ImageDraw.text
        with patch.object(ImageDraw.ImageDraw, "text", autospec=True, side_effect=original) as text:
            render_label(_LONG_NAME, _LONG_SPEC, "$1290", _CODE, models=["i17 ProMax"])
        printed = [(call.args[1], call.args[2]) for call in text.call_args_list]
        name = label_layout._fit(draw, _LONG_NAME, "C:/Windows/Fonts/msjhbd.ttc", (18,), 296 - price_width - 8)[0]
        self.assertEqual(printed, [((308 - price_width, 16), "$1290"), ((12, 16), name),
                                  ((12, 38), "i17 ProMax"), ((12, 56), spec[0]),
                                  ((308 - label_layout._width(draw, _CODE, font), 56), _CODE),
                                  ((12, 74), spec[1])])

    def test_single_name_model_spec_lines_leave_at_least_minimum_barcode_height(self):
        draw = _draw()
        name_font = _name_lines(draw, _SHORT_NAME, 0)[1]
        spec_font = _font("C:/Windows/Fonts/msjh.ttc", 14)
        top = label_layout._TOP + (name_font.size + 4) + 2 * (spec_font.size + 4) + 2
        self.assertEqual(top, 16 + 30 + 18 + 18 + 2)
        self.assertEqual(top, 84)
        self.assertEqual(label_layout._SIZE[1] - label_layout._BOTTOM - top, 68)
        self.assertGreaterEqual(68, label_layout._BARCODE_MIN)
        with patch("lib.label_layout._barcode_image", wraps=label_layout._barcode_image) as barcode:
            render_label(_SHORT_NAME, _SHORT_SPEC, "", _CODE, models=["i17"])
        barcode.assert_called_once_with(_CODE, 56)

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
