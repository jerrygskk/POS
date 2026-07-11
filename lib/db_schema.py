SCHEMA = """
CREATE TABLE IF NOT EXISTS Category(
  category_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS Brand(
  brand_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS BrandCategory(
  brand_id INTEGER NOT NULL REFERENCES Brand(brand_id),
  category_id INTEGER NOT NULL REFERENCES Category(category_id),
  PRIMARY KEY(brand_id, category_id)
);
CREATE TABLE IF NOT EXISTS PhoneBrand(
  phone_brand_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,        -- iPhone / SAMSUNG …
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS PhoneModel(
  model_id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone_brand_id INTEGER NOT NULL REFERENCES PhoneBrand(phone_brand_id),
  name TEXT NOT NULL,
  alias TEXT,                       -- 顯示別名(空=顯示全名)
  series TEXT,                      -- 系列(自由文字,如「17 系列」;空=未分系列)
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(phone_brand_id, name)
);
CREATE TABLE IF NOT EXISTS Product(
  product_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  category_id INTEGER REFERENCES Category(category_id),  -- 可空;API 層建檔強制
  brand_id INTEGER REFERENCES Brand(brand_id),           -- 可空:雜項品可無廠牌
  default_price INTEGER,          -- 可空:建檔可不填價
  note TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS Variant(
  variant_id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES Product(product_id),
  price INTEGER,                  -- 可空:覆蓋款預設價
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS VariantAttribute(    -- 取代 Variant.attributes JSON
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  field_id INTEGER NOT NULL REFERENCES AttributeField(field_id),
  option_id INTEGER REFERENCES AttributeOption(option_id),  -- select/multi/tags 欄用
  text_value TEXT,                                          -- text 欄用
  -- multi/tags 允許同 (variant_id, field_id) 多筆;select/text 由應用層維持單筆。
  -- 唯一索引擋同欄重覆選同一選項(兼作 multi/tags 去重)。
  -- option_id 與 text_value 恰一非 NULL(XOR)
  CHECK((option_id IS NULL) <> (text_value IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_va_unique
  ON VariantAttribute(variant_id, field_id, option_id);
CREATE TABLE IF NOT EXISTS OptionModel(         -- 選項限定型號(特別色)
  option_id INTEGER NOT NULL REFERENCES AttributeOption(option_id),
  model_id INTEGER NOT NULL REFERENCES PhoneModel(model_id),
  PRIMARY KEY(option_id, model_id)
);
CREATE TABLE IF NOT EXISTS Barcode(
  barcode TEXT PRIMARY KEY,
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  source TEXT NOT NULL CHECK(source IN ('factory','store')),
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS AttributeField(
  field_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  category_id INTEGER REFERENCES Category(category_id),  -- NULL=共用欄
  field_type TEXT NOT NULL DEFAULT 'select'
    CHECK(field_type IN ('select','text','multi','tags')),
  default_option_id INTEGER REFERENCES AttributeOption(option_id),  -- 建檔預設帶入
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(category_id, name)       -- 注意:SQLite 對 NULL 視為相異,共用欄去重由應用層處理
);
CREATE TABLE IF NOT EXISTS AttributeOption(
  option_id INTEGER PRIMARY KEY AUTOINCREMENT,
  field_id INTEGER NOT NULL REFERENCES AttributeField(field_id),
  value TEXT NOT NULL,
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(field_id, value)
);
CREATE TABLE IF NOT EXISTS VariantModel(
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  model_id INTEGER NOT NULL REFERENCES PhoneModel(model_id),
  PRIMARY KEY(variant_id, model_id)
);
CREATE TABLE IF NOT EXISTS CategoryField(       -- 種類啟用哪些共用欄
  category_id INTEGER NOT NULL REFERENCES Category(category_id),
  field_id INTEGER NOT NULL REFERENCES AttributeField(field_id),
  PRIMARY KEY(category_id, field_id)
);
CREATE TABLE IF NOT EXISTS StockMovement(
  move_id INTEGER PRIMARY KEY AUTOINCREMENT,
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  qty INTEGER NOT NULL,           -- 進貨+/銷售-/盤點±
  kind TEXT NOT NULL CHECK(kind IN ('purchase','sale','adjust')),
  ref_id INTEGER,                 -- sale_id 或 session_id
  note TEXT,
  ts TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_move_variant ON StockMovement(variant_id);
CREATE TABLE IF NOT EXISTS Sale(
  sale_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  payment TEXT NOT NULL,
  order_discount INTEGER NOT NULL DEFAULT 0,  -- 整單折抵(元)
  total INTEGER NOT NULL,         -- 應收
  paid INTEGER NOT NULL,          -- 實收
  change INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS SaleItem(
  item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  sale_id INTEGER NOT NULL REFERENCES Sale(sale_id),
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  qty INTEGER NOT NULL,
  unit_price INTEGER NOT NULL,    -- 成交單價
  discount INTEGER NOT NULL DEFAULT 0  -- 單品折扣(元)
);
CREATE TABLE IF NOT EXISTS StocktakeSession(
  session_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  ended_at TEXT,
  operator TEXT,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
  note TEXT
);
CREATE TABLE IF NOT EXISTS StocktakeItem(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES StocktakeSession(session_id),
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  system_qty INTEGER NOT NULL,    -- 開盤當下快照
  counted_qty INTEGER NOT NULL DEFAULT 0,
  UNIQUE(session_id, variant_id)
);
CREATE TABLE IF NOT EXISTS Setting(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# 初版 schema 版號:讀不到 schema_version 的既有 DB 一律視為此版
BASE_VERSION = 1

# 遷移清單:[(目標版號, callable(conn)), ...],第 N 筆將 DB 由 (N) 升至 (N+1)。
# 目標版號須連續遞增(BASE_VERSION+1, +2, ...)。callable 只用 conn.execute,
# 勿自行 commit/executescript(runner 已包在同一交易裡)。

def _mig_phone_brand(conn):
    """v1→v2:手機品牌建表。既有 PhoneModel.brand 字串去重建 PhoneBrand,
    再改 PhoneModel.brand → phone_brand_id FK(建新表→搬資料→改名)。"""
    # 無版號但結構已是新版的 DB(如全新 SCHEMA 建立後被清版號)不需轉換
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(PhoneModel)")}
    if "brand" not in cols:
        return
    # 搬表期間關閉 FK 強制:VariantModel 參照 PhoneModel(model_id),
    # DROP 舊表會觸發隱式刪除。因保留 model_id 值,轉換後參照仍成立。
    # (SQLite 建議的改結構程序;此設定須在交易外設定,init_db 於 executescript
    #  後為自動提交狀態,故此處有效。)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("""CREATE TABLE IF NOT EXISTS PhoneBrand(
      phone_brand_id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL UNIQUE,
      sort INTEGER NOT NULL DEFAULT 0,
      active INTEGER NOT NULL DEFAULT 1
    )""")
    # 既有 brand 字串去重,依首次出現(最小 model_id)排序給 sort
    brands = [r["brand"] for r in conn.execute(
        "SELECT brand FROM PhoneModel GROUP BY brand ORDER BY MIN(model_id)")]
    for i, name in enumerate(brands):
        conn.execute("INSERT OR IGNORE INTO PhoneBrand(name, sort) VALUES(?,?)",
                     (name, i))
    # 新表 + 回填 phone_brand_id + 換名(保留 model_id 以維持 VariantModel FK)
    conn.execute("""CREATE TABLE PhoneModel_new(
      model_id INTEGER PRIMARY KEY AUTOINCREMENT,
      phone_brand_id INTEGER NOT NULL REFERENCES PhoneBrand(phone_brand_id),
      name TEXT NOT NULL,
      sort INTEGER NOT NULL DEFAULT 0,
      active INTEGER NOT NULL DEFAULT 1,
      UNIQUE(phone_brand_id, name)
    )""")
    conn.execute("""INSERT INTO PhoneModel_new(model_id, phone_brand_id, name, sort, active)
      SELECT m.model_id, pb.phone_brand_id, m.name, m.sort, m.active
      FROM PhoneModel m JOIN PhoneBrand pb ON m.brand = pb.name""")
    conn.execute("DROP TABLE PhoneModel")
    conn.execute("ALTER TABLE PhoneModel_new RENAME TO PhoneModel")

def _mig_variant_attributes(conn):
    """v2→v3:規格值正規化。建 VariantAttribute / OptionModel,
    並移除 Variant.attributes 欄(SQLite 走建新表→搬資料→改名)。
    既有 attributes JSON 一律丟棄(維護者確認皆為測試資料);
    但本遷移須能在含資料的 DB 上跑不炸。"""
    # 搬表期間關閉 FK 強制:多表(Barcode/VariantModel/SaleItem/StockMovement/
    # StocktakeItem/VariantAttribute)參照 Variant(variant_id)。因保留 variant_id
    # 值,轉換後參照仍成立。(設定須在交易外,init_db 於 executescript 後為自動提交態)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("""CREATE TABLE IF NOT EXISTS VariantAttribute(
      variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
      field_id INTEGER NOT NULL REFERENCES AttributeField(field_id),
      option_id INTEGER REFERENCES AttributeOption(option_id),
      text_value TEXT,
      PRIMARY KEY(variant_id, field_id),
      CHECK((option_id IS NULL) <> (text_value IS NULL))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS OptionModel(
      option_id INTEGER NOT NULL REFERENCES AttributeOption(option_id),
      model_id INTEGER NOT NULL REFERENCES PhoneModel(model_id),
      PRIMARY KEY(option_id, model_id)
    )""")
    # attributes 欄已退場的 DB(如全新 SCHEMA)不需重建
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(Variant)")}
    if "attributes" not in cols:
        return
    conn.execute("""CREATE TABLE Variant_new(
      variant_id INTEGER PRIMARY KEY AUTOINCREMENT,
      product_id INTEGER NOT NULL REFERENCES Product(product_id),
      price INTEGER,
      active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""")
    # 保留 variant_id 以維持各參照表 FK;attributes JSON 不搬(丟棄)。
    # 只搬新表存在且舊表也有的欄(舊版 Variant 可能缺 price/active/created_at,
    # 缺者由新表預設值補上)。
    copy = [c for c in ("variant_id", "product_id", "price", "active", "created_at")
            if c in cols]
    collist = ", ".join(copy)
    conn.execute(f"INSERT INTO Variant_new({collist}) SELECT {collist} FROM Variant")
    conn.execute("DROP TABLE Variant")
    conn.execute("ALTER TABLE Variant_new RENAME TO Variant")

def _mig_field_multi(conn):
    """v3→v4:欄型加 multi/tags;AttributeField 加 default_option_id;
    VariantAttribute 放寬多筆(移除複合 PK,改唯一索引 (variant_id,field_id,option_id))。
    SQLite 走建新表→搬資料→改名。既有值不轉換(鋼化玻璃將清庫重匯)。"""
    # 搬表期間關閉 FK 強制(多表參照 AttributeField/Variant/AttributeOption)。
    # 保留各 id 值,轉換後參照仍成立。(設定須在交易外,init_db 於 executescript
    #  後為自動提交態,故此處有效。)
    conn.execute("PRAGMA foreign_keys=OFF")
    # AttributeField:擴充 field_type CHECK + 新增 default_option_id
    fcols = {r["name"] for r in conn.execute("PRAGMA table_info(AttributeField)")}
    if "default_option_id" not in fcols:
        conn.execute("""CREATE TABLE AttributeField_new(
          field_id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          category_id INTEGER REFERENCES Category(category_id),
          field_type TEXT NOT NULL DEFAULT 'select'
            CHECK(field_type IN ('select','text','multi','tags')),
          default_option_id INTEGER REFERENCES AttributeOption(option_id),
          sort INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1,
          UNIQUE(category_id, name)
        )""")
        conn.execute(
            "INSERT INTO AttributeField_new"
            "(field_id, name, category_id, field_type, sort, active) "
            "SELECT field_id, name, category_id, field_type, sort, active "
            "FROM AttributeField")
        conn.execute("DROP TABLE AttributeField")
        conn.execute("ALTER TABLE AttributeField_new RENAME TO AttributeField")
    # VariantAttribute:移除複合 PK,改唯一索引允許 multi/tags 多筆
    vacols = {r["name"] for r in conn.execute("PRAGMA table_info(VariantAttribute)")}
    va_pk = any(r["pk"] for r in conn.execute("PRAGMA table_info(VariantAttribute)"))
    if vacols and va_pk:
        conn.execute("""CREATE TABLE VariantAttribute_new(
          variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
          field_id INTEGER NOT NULL REFERENCES AttributeField(field_id),
          option_id INTEGER REFERENCES AttributeOption(option_id),
          text_value TEXT,
          CHECK((option_id IS NULL) <> (text_value IS NULL))
        )""")
        conn.execute(
            "INSERT INTO VariantAttribute_new"
            "(variant_id, field_id, option_id, text_value) "
            "SELECT variant_id, field_id, option_id, text_value FROM VariantAttribute")
        conn.execute("DROP TABLE VariantAttribute")
        conn.execute("ALTER TABLE VariantAttribute_new RENAME TO VariantAttribute")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_va_unique "
                 "ON VariantAttribute(variant_id, field_id, option_id)")

def _mig_model_alias(conn):
    """v4→v5:PhoneModel 加顯示別名欄(空=顯示全名)。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(PhoneModel)")}
    if "alias" not in cols:
        conn.execute("ALTER TABLE PhoneModel ADD COLUMN alias TEXT")

def _mig_model_series(conn):
    """v5→v6:PhoneModel 加系列欄(自由文字,空=未分系列)。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(PhoneModel)")}
    if "series" not in cols:
        conn.execute("ALTER TABLE PhoneModel ADD COLUMN series TEXT")

MIGRATIONS = [
    (BASE_VERSION + 1, _mig_phone_brand),
    (BASE_VERSION + 2, _mig_variant_attributes),
    (BASE_VERSION + 3, _mig_field_multi),
    (BASE_VERSION + 4, _mig_model_alias),
    (BASE_VERSION + 5, _mig_model_series),
]

# 最新版號 = 初版 + 遷移筆數;全新 DB 建 SCHEMA 即為此版
SCHEMA_VERSION = BASE_VERSION + len(MIGRATIONS)
