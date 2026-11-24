"""日報表 PDF：A4 直式一面放兩天，每天照紙本日報表的格子；月總表照 Excel「總表」的 22 欄。"""
import math
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle

FONT, BOLD = "MSJH", "MSJH-Bold"
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
# 單日版面以 A4 橫式座標設計；A4 橫式縮成 1/√2 剛好是 A4 直式的上下半張(紙本的 A5 橫式)
PAGE_W, PAGE_H = landscape(A4)
HALF_SCALE = 1 / math.sqrt(2)
MARGIN = 10 * mm


def _fonts():
    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT, r"C:\Windows\Fonts\msjh.ttc", subfontIndex=0))
        pdfmetrics.registerFont(TTFont(BOLD, r"C:\Windows\Fonts\msjhbd.ttc", subfontIndex=0))


def money(value):
    return "" if value is None else f"{value:,}"


class _Sheet:
    """以「距頁面上緣」的毫米座標畫格子，換算成 reportlab 由下往上的座標。"""

    def __init__(self, c):
        self.c = c

    def box(self, x, y, w, h, width=0.8):
        self.c.setLineWidth(width)
        self.c.rect(MARGIN + x * mm, PAGE_H - MARGIN - (y + h) * mm, w * mm, h * mm)

    def dotted(self, x, y, w):
        self.c.setDash(1, 2)
        self.c.setLineWidth(0.4)
        yy = PAGE_H - MARGIN - y * mm
        self.c.line(MARGIN + x * mm, yy, MARGIN + (x + w) * mm, yy)
        self.c.setDash()

    def line(self, x, y, w, width=0.8):
        self.c.setLineWidth(width)
        yy = PAGE_H - MARGIN - y * mm
        self.c.line(MARGIN + x * mm, yy, MARGIN + (x + w) * mm, yy)

    def text(self, x, y, value, size=11, bold=False, align="left", max_w=None, gray=False):
        """y 是文字基線距上緣的毫米數；放不下 max_w 時縮小字級，不截字。gray＝標題用的深灰字。"""
        font = BOLD if bold else FONT
        if max_w is not None:
            while size > 5 and pdfmetrics.stringWidth(value, font, size) > max_w * mm:
                size -= 0.5
        self.c.setFont(font, size)
        self.c.setFillColor(LABEL if gray else colors.black)
        px, py = MARGIN + x * mm, PAGE_H - MARGIN - y * mm
        {"left": self.c.drawString, "right": self.c.drawRightString,
         "center": self.c.drawCentredString}[align](px, py, value)
        self.c.setFillColor(colors.black)

    def label(self, x, y, value, size=10.5, align="center", max_w=None):
        # 標題字加字距：中文標題字距開一點較易讀，數字則不加
        spaced = " ".join(value) if len(value) <= 5 else value
        self.text(x, y, spaced, size, bold=True, align=align, max_w=max_w, gray=True)

    def header_cell(self, x, y, w, h, label, value, head=None):
        """紙本「上標題、下數字」的一格：標題列與數字列之間有格線。"""
        head = HEAD if head is None else head
        self.box(x, y, w, h)
        self.line(x, y + head, w)
        self.label(x + w / 2, y + head / 2 + 1.5, label, max_w=w - 2)
        self.text(x + w - PAD, y + h - 3.6, value, VALUE, bold=True, align="right", max_w=w - 2 * PAD)

    def lines(self, x, y, w, items, count, line_h, size=9.5, text_only=False):
        """count 列有虛線的明細，文字靠左、金額靠右。"""
        for i in range(count):
            ly = y + line_h * (i + 1)
            self.dotted(x + PAD, ly, w - 2 * PAD)
            if i < len(items):
                item = items[i]
                self.text(x + PAD, ly - 1.3, item["text"], size,
                          max_w=(w - 2 * PAD) * (1 if text_only else 0.64))
                if not text_only:
                    self.text(x + w - PAD, ly - 1.3, money(item["amount"]), size, align="right")

    def list_head(self, x, y, w, title, total):
        self.label(x + PAD, y + 5.2, title, 10.5, align="left")
        if total is not None:
            self.text(x + w - PAD, y + 5.4, money(total), 12.5, bold=True, align="right")
            width = pdfmetrics.stringWidth(money(total), BOLD, 12.5) / mm
            self.text(x + w - PAD - width - 1.5, y + 5.2, "合計", 8.5, align="right", gray=True)


LABEL = colors.HexColor("#3a3a3c")
HEAD = 8        # 標題列高度(mm)
PAD = 3         # 格內左右留白(mm)
VALUE = 15      # 大數字字級


def shown(items):
    # 明細只畫有填的列數，空白時預留 4 列，與畫面預設列數一致；列高仍以上限計，填滿時才排得下
    return max(4, len(items))


def _draw_day(s, row):
    W = (PAGE_W - 2 * MARGIN) / mm
    entry = row["entry"]
    y, m, d = row["date"].split("-")
    s.text(W / 2 - 14, 7, f"{int(m)}", 20, bold=True, align="right")
    s.text(W / 2 - 12, 7, "月", 12, gray=True)
    s.text(W / 2 + 12, 7, f"{int(d)}", 20, bold=True, align="right")
    s.text(W / 2 + 14, 7, "日", 12, gray=True)
    s.text(0, 7, f"{int(y)} 年　{row['weekday']}", 9.5, gray=True)
    if not row["closed"]:
        s.text(W, 7, "尚未立帳", 9.5, align="right", gray=True)

    # 第一段：營業額｜晴雨｜累計營業額｜開(收)支｜累計開支｜當日餘額
    top, h = 10, 21
    widths = [47, 15, 53, 53, 53]
    widths.append(W - sum(widths))
    values = [("營業額", row["revenue"]), (None, None), ("累計營業額", row["cum_revenue"]),
              ("開(收)支", row["expense"]), ("累計開支", row["cum_expense"]), ("當日餘額", row["balance"])]
    x = 0
    for w, (label, value) in zip(widths, values):
        if label is None:
            # 晴、雨上下各印一次，當天的天氣畫圈
            # 天氣格自己用虛線平分上下兩半，晴、雨等高；不必和左右格的標題線對齊
            s.box(x, top, w, h)
            s.dotted(x, top + h / 2, w)
            for mark, cy in (("晴", top + h / 4), ("雨", top + h * 3 / 4)):
                s.text(x + w / 2, cy + 1.5, mark, 10.5, bold=True, align="center")
                if entry["weather"] == mark:
                    s.c.setLineWidth(0.8)
                    s.c.circle(MARGIN + (x + w / 2) * mm, PAGE_H - MARGIN - cy * mm, 3.3 * mm)
        else:
            s.header_cell(x, top, w, h, label, money(value))
        x += w

    # 第二段：當日貨款(左右兩欄各 6 列，照 Excel)｜累計貨款｜尚欠貨款 6 列｜累計現流／應付貨款／尚餘現流
    top2, h2 = top + h, 44
    c1, c2, c3 = 110, 47, 58
    c4 = W - c1 - c2 - c3
    line_h = (h2 - HEAD) / 6
    s.box(0, top2, c1, h2)
    s.list_head(0, top2, c1, "當日貨款", row["goods"])
    half = c1 / 2
    goods = entry["goods"]
    left = (shown(goods) + 1) // 2
    s.lines(0, top2 + HEAD, half, goods[:left], left, line_h, 9)
    s.lines(half, top2 + HEAD, half, goods[left:], shown(goods) - left, line_h, 9)
    s.header_cell(c1, top2, c2, 24, "累計貨款", money(row["cum_goods"]))
    s.box(c1, top2 + 24, c2, h2 - 24)
    s.text(c1 + PAD, top2 + 31.5, "總貨款", 8.5, gray=True)
    s.text(c1 + c2 - PAD, top2 + 31.8, money(row["goods_total"]), 11, bold=True, align="right")
    s.box(c1 + c2, top2, c3, h2)
    s.list_head(c1 + c2, top2, c3, "尚欠貨款", None)
    # 尚欠貨款照 Excel 每列一格，只有文字
    s.lines(c1 + c2, top2 + HEAD, c3, entry["owed"], shown(entry["owed"]), line_h, 9, text_only=True)
    x4 = c1 + c2 + c3
    for i, (label, value) in enumerate([("累計現流", row["cum_cash"]), ("應付貨款", entry["payable"]),
                                        ("尚餘現流", row["remain"])]):
        ry = top2 + h2 / 3 * i
        s.box(x4, ry, c4, h2 / 3)
        s.label(x4 + PAD, ry + h2 / 6 + 1.6, label, 10.5, align="left")
        s.text(x4 + c4 - PAD, ry + h2 / 6 + 2.2, money(value), 14, bold=True, align="right")

    # 第三段：每日個績｜①～⑩，上方當日、下方累計；POS 只有 6 位員工，⑦～⑩ 留空
    top3, h3 = top2 + h2, 30
    tw = 20
    s.box(0, top3, tw, h3)
    s.label(tw / 2, top3 + h3 / 2 - 1, "每日", 10.5)
    s.label(tw / 2, top3 + h3 / 2 + 4.5, "個績", 10.5)
    cw = (W - tw) / 5
    for i in range(10):
        cx, cy = tw + cw * (i % 5), top3 + h3 / 2 * (i // 5)
        s.box(cx, cy, cw, h3 / 2)
        s.text(cx + PAD, cy + 6, CIRCLED[i], 10.5, gray=True)
        if i < 6 and row["emp_active"][i]:
            if row["employees"] is not None:
                s.text(cx + cw - PAD, cy + 7.2, money(row["employees"][i]), 13, bold=True, align="right")
            s.dotted(cx + PAD, cy + 9.2, cw - 2 * PAD)
            s.text(cx + cw - PAD, cy + 13.2, f"累計 {money(row['cum_employees'][i])}", 8, align="right", gray=True)

    # 第四段：備註｜開(收)支明細，各 12 列
    top4 = top3 + h3
    # 列高以 12 列排滿為準；格子高度只到實際列數(兩格取多的)，不留整片空白
    line_h4 = ((PAGE_H - 2 * MARGIN) / mm - top4 - HEAD - 1.5) / 12
    h4 = HEAD + line_h4 * max(shown(entry["note"]), shown(entry["expense"])) + 2.5
    bw = W / 2
    for i, (title, kind, total) in enumerate([("備註", "note", row["note_total"]),
                                              ("開(收)支明細", "expense", row["expense"])]):
        bx = bw * i
        s.box(bx, top4, bw, h4)
        s.list_head(bx, top4, bw, title, total)
        s.lines(bx, top4 + HEAD, bw, entry[kind], shown(entry[kind]), line_h4)


MONTH_HEADERS = ["日期", "星期", "當日\n營業額", "累計\n營業額", "當日\n開支", "累計\n開支", "當日\n餘額",
                 "累計\n現流", "貨款", "累計\n貨款"] + [
    t for n in range(1, 7) for t in (f"員{n}", f"員{n}\n累計")]


def pair_of(rows, day):
    """固定配對：單數日在上、下一天在下(1、2 日一面，3、4 日一面…)；月底單出的那天下半留白。"""
    first = day - 1 if day % 2 == 0 else day
    return rows[first - 1], rows[first] if first < len(rows) else None


def day_pages_pdf(path, pairs):
    """pairs：[(上半天, 下半天或 None), …]，每組一面。"""
    _fonts()
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    for top, bottom in pairs:
        for row, base in ((top, height / 2), (bottom, 0)):
            if row is None:
                continue
            c.saveState()
            c.translate(0, base)
            c.scale(HALF_SCALE, HALF_SCALE)
            _draw_day(_Sheet(c), row)
            c.restoreState()
        # 中間一條淺灰虛線，裁成兩張 A5 時對齊用
        c.setStrokeColor(colors.HexColor("#c7c7cc"))
        c.setDash(3, 3)
        c.setLineWidth(0.5)
        c.line(0, height / 2, width, height / 2)
        c.setDash()
        c.setStrokeColor(colors.black)
        c.showPage()
    c.save()


def month_pdf(path, year, month, rows):
    _fonts()
    cell = ParagraphStyle("cell", fontName=FONT, fontSize=7, leading=8.5, alignment=1)
    data = [[Paragraph(h.replace("\n", "<br/>"), cell) for h in MONTH_HEADERS]]
    for r in rows:
        if not r["has_data"]:
            # 照 Excel 總表:那天沒有任何資料就整列留白,不印延續的累計數字
            data.append([r["date"][5:].replace("-", "/"), r["weekday"][-1]] + [""] * 20)
            continue
        emp = r["employees"] or [None] * 6
        line = [r["date"][5:].replace("-", "/"), r["weekday"][-1], money(r["revenue"]),
                money(r["cum_revenue"]), money(r["expense"]), money(r["cum_expense"]),
                money(r["balance"]), money(r["cum_cash"]), money(r["goods"]), money(r["goods_total"])]
        for n in range(6):
            line += [money(emp[n]), money(r["cum_employees"][n])] if r["emp_active"][n] else ["", ""]
        data.append(line)
    widths = [13 * mm, 8 * mm] + [12.9 * mm] * 20
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), FONT, 7),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e5ea")),
        ("ALIGN", (0, 1), (1, -1), "CENTER"),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 1), (-1, -1), 2.2),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 2.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 1.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1.5),
    ]))
    title = ParagraphStyle("title", fontName=BOLD, fontSize=13, leading=16, alignment=1, spaceAfter=4)
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=MARGIN)
    doc.build([Paragraph(f"{year} 年 {month} 月　月總表", title), table])
