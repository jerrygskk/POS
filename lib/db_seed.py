import json

# 共用欄(category_id NULL):各種類再以 CategoryField 勾選啟用
# (name, field_type)
DEFAULT_FIELDS = [
    ("商品描述", "text"),    # 自由文字
    ("顏色", "select"),      # 選單
]
DEFAULT_PAYMENTS = ["現金", "刷卡", "行動支付"]

def seed(conn):
    # 共用欄 category_id 為 NULL,SQLite UNIQUE 對 NULL 視為相異,
    # 不能靠 INSERT OR IGNORE 去重,重跑須先查存在
    for i, (name, ftype) in enumerate(DEFAULT_FIELDS):
        exists = conn.execute(
            "SELECT 1 FROM AttributeField WHERE category_id IS NULL AND name=?",
            (name,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO AttributeField(name, category_id, field_type, sort) "
                "VALUES(?, NULL, ?, ?)", (name, ftype, i))
    conn.execute("INSERT OR IGNORE INTO Setting(key,value) VALUES('payments',?)",
                 (json.dumps(DEFAULT_PAYMENTS, ensure_ascii=False),))
