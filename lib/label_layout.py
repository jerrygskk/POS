"""Rendering of the fixed 40 × 20 mm retail label."""
from io import BytesIO

from barcode import Code128
from barcode.writer import ImageWriter
from PIL import Image, ImageDraw, ImageFont

_SIZE = (320, 160)
_LEFT = 12
_TOP = 16
_BOTTOM = 8
_SEPARATOR = "｜"
_SPEC_SIZE = 14  # 規格字級固定，不隨內容長短變動
_BARCODE_MAX = 56
_BARCODE_MIN = 40
_BOLD_FONT = "C:/Windows/Fonts/msjhbd.ttc"
_REGULAR_FONT = "C:/Windows/Fonts/msjh.ttc"


def _font(path, size):
    return ImageFont.truetype(path, size)


def _width(draw, text, font):
    return draw.textbbox((0, 0), text, font=font)[2]


def _fit(draw, text, path, sizes, maximum):
    """回傳放得下的（文字, 字型）。

    先逐級縮小字級；縮到最小仍放不下才截斷，並補刪節號——直接砍字尾會讓
    店員看不出後面還有內容。
    """
    text = str(text or "")
    for size in sizes:
        font = _font(path, size)
        if not text or _width(draw, text, font) <= maximum:
            return text, font
    font = _font(path, sizes[-1])
    while text and _width(draw, text + "…", font) > maximum:
        text = text[:-1]
    return (text + "…" if text else text), font


def _wrap_two(draw, text, font, first_max, second_max):
    """把文字拆成兩行；拆不出合適的斷點回傳 None。優先斷在空白處。"""
    cut = len(text)
    while cut and _width(draw, text[:cut], font) > first_max:
        cut -= 1
    if not cut:
        return None
    space = text.rfind(" ", 0, cut + 1)
    if space > 0:
        cut = space
    head, tail = text[:cut].rstrip(), text[cut:].strip()
    if not head or not tail or _width(draw, tail, font) > second_max:
        return None
    return [head, tail]


def _name_lines(draw, name, price_width):
    """品名優先用大字單行；放不下才換兩行；再放不下才截斷補刪節號。"""
    name = str(name or "")
    first_max = 296 - price_width - (8 if price_width else 0)
    for size in (26, 24, 22, 20, 18):
        font = _font(_BOLD_FONT, size)
        if not name or _width(draw, name, font) <= first_max:
            return [name], font
    for size in (20, 18, 16):
        font = _font(_BOLD_FONT, size)
        lines = _wrap_two(draw, name, font, first_max, 296)
        if lines:
            return lines, font
    text, font = _fit(draw, name, _BOLD_FONT, (16,), first_max)
    return [text], font


def _spec_lines(draw, text, font, first_max, second_max):
    """規格斷在「｜」段落邊界，最多兩行；放不下的段落以刪節號帶過。

    段落中間硬砍會留下孤立的分隔線，看起來像壞字。
    """
    text = str(text or "")
    if not text:
        return [""]
    if _width(draw, text, font) <= first_max:
        return [text]

    segments = text.split(_SEPARATOR)
    lines, current, limit = [], [], first_max
    for segment in segments:
        candidate = _SEPARATOR.join(current + [segment])
        if current and _width(draw, candidate, font) > limit:
            lines.append(_SEPARATOR.join(current))
            current, limit = [segment], second_max
            if len(lines) == 2:
                break
        else:
            current.append(segment)
    if len(lines) < 2 and current:
        lines.append(_SEPARATOR.join(current))

    used = len(_SEPARATOR.join(lines))
    if used < len(text):
        last = lines[-1]
        cap = second_max if len(lines) > 1 else first_max
        while last and _width(draw, last + "…", font) > cap:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines[:2]


def _barcode_image(value, height):
    output = BytesIO()
    Code128(value, writer=ImageWriter()).write(output, options={
        "module_width": 0.23, "module_height": height / 8, "quiet_zone": 1.0,
        "font_size": 0, "text_distance": 0, "write_text": False, "dpi": 203,
    })
    output.seek(0)
    return Image.open(output).convert("RGB").resize((296, height), Image.Resampling.NEAREST)


def render_label(name, specification, price, barcode):
    image = Image.new("RGB", _SIZE, "white")
    draw = ImageDraw.Draw(image)
    # 熱感列印只有黑與白：灰階平滑的細筆畫會在二值化時被判成白色而消失，
    # 筆畫密的字（如「框」）會缺半邊。關掉平滑，直接畫成純黑白。
    draw.fontmode = "1"
    bold = _font(_BOLD_FONT, 22)
    price = str(price or "")
    price_width = _width(draw, price, bold) if price else 0
    name_lines, name_font = _name_lines(draw, name, price_width)

    # 號碼靠右擠進規格第一行，省下整整一行的高度給條碼。
    spec_font = _font(_REGULAR_FONT, _SPEC_SIZE)
    code_text = str(barcode or "")
    code_width = _width(draw, code_text, spec_font)
    spec_lines = _spec_lines(draw, specification, spec_font, 296 - code_width - 8, 296)

    def space_left():
        top = _TOP + len(name_lines) * (name_font.size + 4)
        top += len(spec_lines) * (spec_font.size + 4) + 2
        return _SIZE[1] - _BOTTOM - top

    # 條碼被壓到掃不動時，先犧牲規格第二行，再犧牲品名第二行；下緣留白絕不動。
    if space_left() < _BARCODE_MIN and len(spec_lines) > 1:
        spec_lines = _spec_lines(draw, specification, spec_font, 296 - code_width - 8, 0)
    if space_left() < _BARCODE_MIN and len(name_lines) > 1:
        name_font = _font(_BOLD_FONT, 18)
        name_lines = [_fit(draw, name, _BOLD_FONT, (18,),
                           296 - price_width - (8 if price else 0))[0]]

    y = _TOP
    if price:
        draw.text((320 - _LEFT - price_width, y), price, font=bold, fill="black")
    for line in name_lines:
        draw.text((_LEFT, y), line, font=name_font, fill="black")
        y += name_font.size + 4
    for index, line in enumerate(spec_lines):
        draw.text((_LEFT, y), line, font=spec_font, fill="black")
        if index == 0 and code_text:
            draw.text((320 - _LEFT - code_width, y), code_text, font=spec_font, fill="black")
        y += spec_font.size + 4
    y += 2

    height = min(_BARCODE_MAX, _SIZE[1] - _BOTTOM - y)
    image.paste(_barcode_image(barcode, height), (_LEFT, y))
    return image
