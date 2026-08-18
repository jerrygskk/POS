import json

from lib.normalize import normalize_key

# 全域欄位主檔(AttributeField 全域化後不再有 category_id):種類以 CategoryField 掛用
# (name, field_type)
DEFAULT_FIELDS = [
    ("商品描述", "text"),    # 自由文字
    ("顏色", "select"),      # 選單
    ("款式", "select"),      # 選單;新種類預設模板之一
]
# 新種類的預設模板欄位:建立種類時自動掛上(選填),不需要可在設定頁刪除。
NEW_CATEGORY_FIELDS = ["顏色", "款式"]
# 全新資料庫的起始選項:給店員看得懂的起點,值本身預期會被改掉。
# 只在建立全新資料庫時補,既有資料庫升級一律不碰選單庫。
DEFAULT_OPTIONS = {
    "顏色": ["黑色", "白色", "透明"],
    "款式": ["款式A", "款式B", "款式C"],
}
DEFAULT_PAYMENTS = ["現金", "刷卡", "行動支付"]

def seed(conn, fresh=False):
    # AttributeField 全域化後無 category_id;正規化同名同型態去重(SQLite UNIQUE
    # 不套正規化),重跑須先查存在
    existing = {(normalize_key(r["name"]), r["field_type"])
                for r in conn.execute("SELECT name, field_type FROM AttributeField")}
    created = {}
    for name, ftype in DEFAULT_FIELDS:
        if (normalize_key(name), ftype) in existing:
            continue
        cur = conn.execute("INSERT INTO AttributeField(name, field_type) VALUES(?, ?)",
                           (name, ftype))
        existing.add((normalize_key(name), ftype))
        created[name] = cur.lastrowid
    # 起始選項只補給「全新資料庫本次建立的欄位」:既有資料庫的選單庫一律不碰
    for name, field_id in (created.items() if fresh else ()):
        for sort, value in enumerate(DEFAULT_OPTIONS.get(name, []), start=1):
            conn.execute(
                "INSERT INTO AttributeOption(field_id,value,sort) VALUES(?,?,?)",
                (field_id, value, sort))
    conn.execute("INSERT OR IGNORE INTO Setting(key,value) VALUES('payments',?)",
                 (json.dumps(DEFAULT_PAYMENTS, ensure_ascii=False),))
