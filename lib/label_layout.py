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
# 條碼與號碼固定釘在標籤底部，不隨文字多寡上下移動：
# 號碼區 16 點＝與條碼間隔 2 點＋英數字墨跡最高到最低 14 點（含 Q、g、y 等下伸筆畫，
# 號碼不論含哪些字元都不會壓進下緣留白）；條碼固定 50 點，頂端固定在第 86 點。
# 文字排在 84 點以上（與條碼至少隔 2 點），放不下就依犧牲順序減行；
# 最精簡的品名一行＋型號一行＋規格一行只到 82 點，所以文字保證不會壓到條碼。
_CODE_AREA = 16
_BARCODE_HEIGHT = 50
_BARCODE_TOP = _SIZE[1] - _BOTTOM - _CODE_AREA - _BARCODE_HEIGHT  # 86
_TEXT_LIMIT = _BARCODE_TOP - 2  # 84
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


def _model_line(draw, models, font, maximum):
    if not models:
        return ""
    text = "、".join(models)
    if _width(draw, text, font) <= maximum:
        return text
    suffix = f" 等 {len(models)} 款" if len(models) > 1 else ""
    for count in range(len(models) - 1, 0, -1):
        text = "、".join(models[:count]) + suffix
        if _width(draw, text, font) <= maximum:
            return text
    text = models[0]
    while text and _width(draw, text + "…" + suffix, font) > maximum:
        text = text[:-1]
    return text + "…" + suffix


def _barcode_image(value, height):
    output = BytesIO()
    Code128(value, writer=ImageWriter()).write(output, options={
        "module_width": 0.23, "module_height": height / 8, "quiet_zone": 1.0,
        "font_size": 0, "text_distance": 0, "write_text": False, "dpi": 203,
        # 產生器預設上下各留約 5 點空白，會讓黑條比預留高度矮一截；版面已自行留白，故歸零。
        "margin_top": 0, "margin_bottom": 0,
    })
    output.seek(0)
    return Image.open(output).convert("RGB").resize((296, height), Image.Resampling.NEAREST)


# 有型號的商品靠規格區分款式，規格第二行比品名第二行重要，
# 所以空間不足時先縮品名；無型號仍沿用原來的犧牲順序與排版。
def render_label(name, specification, price, barcode, models=()):
    image = Image.new("RGB", _SIZE, "white")
    draw = ImageDraw.Draw(image)
    # 熱感列印只有黑與白：灰階平滑的細筆畫會在二值化時被判成白色而消失，
    # 筆畫密的字（如「框」）會缺半邊。關掉平滑，直接畫成純黑白。
    draw.fontmode = "1"
    bold = _font(_BOLD_FONT, 22)
    price = str(price or "")
    price_width = _width(draw, price, bold) if price else 0
    name_lines, name_font = _name_lines(draw, name, price_width)

    # 號碼印在條碼正下方（一般商品標籤慣例），規格兩行都能用整行寬度。
    spec_font = _font(_REGULAR_FONT, _SPEC_SIZE)
    code_text = str(barcode or "")
    code_width = _width(draw, code_text, spec_font)
    # 字型上方自帶約 5 點空白，以實際墨跡上緣對齊，貼著條碼下方 2 點。
    code_top = draw.textbbox((0, 0), code_text, font=spec_font)[1] if code_text else 0
    spec_lines = _spec_lines(draw, specification, spec_font, 296, 296)
    model_line = _model_line(draw, models, spec_font, 296)

    def space_left():
        top = _TOP + len(name_lines) * (name_font.size + 4)
        top += len(spec_lines) * (spec_font.size + 4) + 2
        if models:
            top += spec_font.size + 4
        return _TEXT_LIMIT - top

    if models:
        if space_left() < 0 and len(name_lines) > 1:
            name_font = _font(_BOLD_FONT, 18)
            name_lines = [_fit(draw, name, _BOLD_FONT, (18,),
                               296 - price_width - (8 if price else 0))[0]]
        if space_left() < 0 and len(spec_lines) > 1:
            spec_lines = _spec_lines(draw, specification, spec_font, 296, 0)
    else:
        # 文字超過條碼頂端時，先犧牲規格第二行，再犧牲品名第二行；條碼位置與下緣留白絕不動。
        if space_left() < 0 and len(spec_lines) > 1:
            spec_lines = _spec_lines(draw, specification, spec_font, 296, 0)
        if space_left() < 0 and len(name_lines) > 1:
            name_font = _font(_BOLD_FONT, 18)
            name_lines = [_fit(draw, name, _BOLD_FONT, (18,),
                               296 - price_width - (8 if price else 0))[0]]

    y = _TOP
    if price:
        draw.text((320 - _LEFT - price_width, y), price, font=bold, fill="black")
    for line in name_lines:
        draw.text((_LEFT, y), line, font=name_font, fill="black")
        y += name_font.size + 4
    if models:
        draw.text((_LEFT, y), model_line, font=spec_font, fill="black")
        y += spec_font.size + 4
    for line in spec_lines:
        draw.text((_LEFT, y), line, font=spec_font, fill="black")
        y += spec_font.size + 4

    image.paste(_barcode_image(barcode, _BARCODE_HEIGHT), (_LEFT, _BARCODE_TOP))
    if code_text:
        draw.text(((_SIZE[0] - code_width) // 2, _BARCODE_TOP + _BARCODE_HEIGHT + 2 - code_top),
                  code_text, font=spec_font, fill="black")
    return image
