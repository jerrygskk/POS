import json

from lib.normalize import normalize_key

# 全域欄位主檔(AttributeField 全域化後不再有 category_id):種類以 CategoryField 掛用
# (name, field_type)
DEFAULT_FIELDS = [
    ("商品描述", "text"),    # 自由文字
    ("顏色", "select"),      # 選單
]
DEFAULT_PAYMENTS = ["現金", "刷卡", "行動支付"]

def seed(conn):
    # AttributeField 全域化後無 category_id;正規化同名同型態去重(SQLite UNIQUE
    # 不套正規化),重跑須先查存在
    existing = {(normalize_key(r["name"]), r["field_type"])
                for r in conn.execute("SELECT name, field_type FROM AttributeField")}
    for name, ftype in DEFAULT_FIELDS:
        if (normalize_key(name), ftype) in existing:
            continue
        conn.execute("INSERT INTO AttributeField(name, field_type) VALUES(?, ?)",
                     (name, ftype))
        existing.add((normalize_key(name), ftype))
    conn.execute("INSERT OR IGNORE INTO Setting(key,value) VALUES('payments',?)",
                 (json.dumps(DEFAULT_PAYMENTS, ensure_ascii=False),))
