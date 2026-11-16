from lib.auth import IDLE_DEFAULT_MIN, hash_password

# 規格欄一律由各種類自己建一份,不留全域共用主檔:同名欄在不同種類的詞彙互不相干
# (手機殼的天峰藍 vs 傳輸線的黑白),共用會讓建檔候選混進別種類的值。
# 故種子不再建任何規格欄;新種類要帶的欄由設定頁建立流程逐一新建。
# 新種類建立時自動建的規格欄(選填),各種類自己一份;不需要可在設定頁刪除。
# (name, field_type)
NEW_CATEGORY_OWN_FIELDS = [("顏色", "select"), ("款式", "select")]
# 預設付款方式:(名稱, 啟用, 抽成比例×100, 是現金)。金流業者在提領時才扣抽成,所以只在報表合計算
DEFAULT_PAYMENTS = [("現金", 1, 0, 1), ("轉帳", 1, 0, 0), ("LINE Pay", 1, 220, 0), ("台灣Pay", 0, 0, 0)]

def seed(conn, fresh=False):
    # 只在空表時建預設;之後由設定頁管理,不再補回被改掉的項目
    if conn.execute("SELECT 1 FROM PaymentMethod").fetchone() is None:
        conn.executemany("INSERT INTO PaymentMethod(name,sort,active,fee_rate,is_cash) VALUES(?,?,?,?,?)",
                         [(name, sort, active, rate, cash)
                          for sort, (name, active, rate, cash) in enumerate(DEFAULT_PAYMENTS, 1)])
    conn.execute("INSERT OR IGNORE INTO Setting(key,value) VALUES('cash_float','6000')")  # 立帳預備金
    conn.execute("INSERT OR IGNORE INTO Setting(key,value) VALUES('idle_logout_min',?)",
                 (str(IDLE_DEFAULT_MIN),))  # 管理者閒置自動登出(分鐘),0＝不自動登出
    if conn.execute("SELECT 1 FROM Setting WHERE key='admin_password_hash'").fetchone() is None:
        conn.execute("INSERT OR IGNORE INTO Setting(key,value) VALUES('admin_password_hash',?)",
                     (hash_password("admin"),))
    # 型號必須掛裝置類型;清單全空時補一個「手機」,管理者刪掉改用別的就不再補
    if conn.execute("SELECT 1 FROM DeviceType").fetchone() is None:
        conn.execute("INSERT INTO DeviceType(name,sort) VALUES('手機',1)")
