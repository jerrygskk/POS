"""日報表手填資料（daily_report.db）的讀寫。"""
import re
import sqlite3
from contextlib import closing

# 列數照老闆現用的 Excel 每日頁，PDF 的格子也依此配置；放寬前要先確認版面放得下
LINE_LIMITS = {"goods": 12, "owed": 6, "note": 12, "expense": 12}
WEATHERS = ("", "晴", "雨")
TEXT_MAX = 30
AMOUNT_MAX = 100_000_000
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS DayEntry(
  date TEXT PRIMARY KEY NOT NULL,
  weather TEXT NOT NULL DEFAULT '',
  payable INTEGER
);
CREATE TABLE IF NOT EXISTS Line(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('goods','owed','note','expense')),
  seq INTEGER NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  amount INTEGER
);
CREATE INDEX IF NOT EXISTS idx_line_date ON Line(date);
CREATE TABLE IF NOT EXISTS Setting(key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL);
"""


class StoreError(Exception):
    pass


class Store:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        with closing(self._conn()) as conn, conn:
            conn.executescript(SCHEMA)

    def _conn(self):
        return sqlite3.connect(self.db_path, timeout=3)

    def get_setting(self, key):
        with closing(self._conn()) as conn:
            row = conn.execute("SELECT value FROM Setting WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key, value):
        with closing(self._conn()) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO Setting(key, value) VALUES(?, ?)", (key, value))

    def month_entries(self, year, month):
        """{日期: 一天的手填資料}，沒填過的日子不在裡面。"""
        prefix = f"{year:04d}-{month:02d}-%"
        days = {}
        with closing(self._conn()) as conn:
            for date, weather, payable in conn.execute(
                    "SELECT date, weather, payable FROM DayEntry WHERE date LIKE ?", (prefix,)):
                days[date] = _empty_entry()
                days[date].update(weather=weather, payable=payable)
            for date, kind, text, amount in conn.execute(
                    "SELECT date, kind, text, amount FROM Line WHERE date LIKE ? ORDER BY date, kind, seq",
                    (prefix,)):
                days.setdefault(date, _empty_entry())[kind].append({"text": text, "amount": amount})
        return days

    def save_day(self, date, data):
        entry = validate_entry(date, data)
        # 主檔與明細同一個交易寫入，失敗就整天都不動
        with closing(self._conn()) as conn, conn:
            conn.execute("DELETE FROM Line WHERE date=?", (date,))
            conn.execute("INSERT OR REPLACE INTO DayEntry(date, weather, payable) VALUES(?, ?, ?)",
                         (date, entry["weather"], entry["payable"]))
            for kind in LINE_LIMITS:
                for seq, line in enumerate(entry[kind]):
                    conn.execute("INSERT INTO Line(date, kind, seq, text, amount) VALUES(?, ?, ?, ?, ?)",
                                 (date, kind, seq, line["text"], line["amount"]))
        return entry


def _empty_entry():
    return {"weather": "", "payable": None, **{kind: [] for kind in LINE_LIMITS}}


def _amount(value, label, allow_negative=False):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoreError(f"{label}必須是整數")
    if abs(value) > AMOUNT_MAX or (value < 0 and not allow_negative):
        raise StoreError(f"{label}金額不正確")
    return value


def validate_entry(date, data):
    if not isinstance(date, str) or not DATE_RE.match(date):
        raise StoreError("日期格式錯誤")
    if not isinstance(data, dict):
        raise StoreError("資料格式錯誤")
    weather = data.get("weather", "")
    if weather not in WEATHERS:
        raise StoreError("天氣只能是晴或雨")
    entry = {"weather": weather, "payable": _amount(data.get("payable"), "應付貨款")}
    names = {"goods": "貨款明細", "owed": "尚欠貨款", "note": "備註", "expense": "開（收）支明細"}
    for kind, limit in LINE_LIMITS.items():
        lines = data.get(kind) or []
        if not isinstance(lines, list):
            raise StoreError("資料格式錯誤")
        clean = []
        for line in lines:
            if not isinstance(line, dict):
                raise StoreError("資料格式錯誤")
            text = str(line.get("text") or "").strip()
            # 尚欠貨款照 Excel 每列一格自由填寫，不存金額；開支明細的收入以負數記
            amount = None if kind == "owed" else _amount(
                line.get("amount"), names[kind], allow_negative=(kind == "expense"))
            if not text and amount is None:
                continue
            if len(text) > TEXT_MAX:
                raise StoreError(f"{names[kind]}的文字最多 {TEXT_MAX} 字")
            clean.append({"text": text, "amount": amount})
        if len(clean) > limit:
            raise StoreError(f"{names[kind]}最多 {limit} 列")
        entry[kind] = clean
    return entry
