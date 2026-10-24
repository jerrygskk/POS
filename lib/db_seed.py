import json

from lib.auth import hash_password

# 規格欄一律由各種類自己建一份,不留全域共用主檔:同名欄在不同種類的詞彙互不相干
# (手機殼的天峰藍 vs 傳輸線的黑白),共用會讓建檔候選混進別種類的值。
# 故種子不再建任何規格欄;新種類要帶的欄由設定頁建立流程逐一新建。
# 新種類建立時自動建的規格欄(選填),各種類自己一份;不需要可在設定頁刪除。
# (name, field_type)
NEW_CATEGORY_OWN_FIELDS = [("顏色", "select"), ("款式", "select")]
DEFAULT_PAYMENTS = ["現金", "刷卡", "行動支付"]

def seed(conn, fresh=False):
    conn.execute("INSERT OR IGNORE INTO Setting(key,value) VALUES('payments',?)",
                 (json.dumps(DEFAULT_PAYMENTS, ensure_ascii=False),))
    if conn.execute("SELECT 1 FROM Setting WHERE key='admin_password_hash'").fetchone() is None:
        conn.execute("INSERT OR IGNORE INTO Setting(key,value) VALUES('admin_password_hash',?)",
                     (hash_password("admin"),))
    # 型號必須掛裝置類型;清單全空時補一個「手機」,管理者刪掉改用別的就不再補
    if conn.execute("SELECT 1 FROM DeviceType").fetchone() is None:
        conn.execute("INSERT INTO DeviceType(name,sort) VALUES('手機',1)")
