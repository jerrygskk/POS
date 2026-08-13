"""Label layout behaviour: fixed size, wrapping rules, and print-safe pixels."""
import unittest

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
