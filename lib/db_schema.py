from lib.normalize import normalize_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS Category(
  category_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  model_mode TEXT NOT NULL DEFAULT 'hidden'
    CHECK(model_mode IN ('required','hidden'))  -- required=適用型號必填,hidden=不使用
);
CREATE TABLE IF NOT EXISTS Brand(
  brand_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  sort INTEGER NOT NULL DEFAULT 0
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
CREATE TABLE IF NOT EXISTS AttributeField(       -- 全域欄位主檔;種類關係屬性移至 CategoryField
  field_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  field_type TEXT NOT NULL DEFAULT 'select'
    CHECK(field_type IN ('select','text','multi','tags')),
  active INTEGER NOT NULL DEFAULT 1
  -- 正規化同名去重由應用層處理(SQLite UNIQUE 不套用正規化)
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
CREATE TABLE IF NOT EXISTS CategoryField(       -- 種類模板:承載排序、必要性、預設值與模板層級啟用
  category_id INTEGER NOT NULL REFERENCES Category(category_id),
  field_id INTEGER NOT NULL REFERENCES AttributeField(field_id),
  sort INTEGER NOT NULL DEFAULT 0,
  required INTEGER NOT NULL DEFAULT 0,
  default_option_id INTEGER REFERENCES AttributeOption(option_id),  -- 建檔預設帶入
  active INTEGER NOT NULL DEFAULT 1,               -- 模板層級啟用,不動 AttributeField.active
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
CREATE TABLE IF NOT EXISTS VariantIssue(      -- 子產品待處理異常(必填缺值/條碼或簽章重複)
  issue_id INTEGER PRIMARY KEY,
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  issue_type TEXT NOT NULL
    CHECK(issue_type IN ('missing_required','duplicate_barcode','duplicate_signature')),
  field_id INTEGER REFERENCES AttributeField(field_id),        -- 相關欄位(適用時)
  source_value TEXT,                                           -- 觸發異常的原始值(適用時)
  related_variant_id INTEGER REFERENCES Variant(variant_id)    -- 衝突的另一子產品(適用時)
);
CREATE INDEX IF NOT EXISTS idx_variant_issue_variant ON VariantIssue(variant_id);
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

# v8 CategoryField.required 初始值常數表(維護者核定)。
# key=正規化種類名 → {正規化欄名, ...}(該組合 required=1);未列到一律 0。
# 「特性詞條」不列入即恆為 0。
_V8_REQUIRED_RAW = {
    "鋼化玻璃": ("材質", "版型"),
    "充電線": ("接頭", "長度"),
    "鏡頭貼": ("框色",),
    "AppleWatch玻璃": ("款式", "尺寸"),
    "插座": ("規格",),
    "藍芽耳機": ("型號", "顏色"),
    "行動電源": ("規格",),
}
_V8_REQUIRED = {
    normalize_key(cat): {normalize_key(f) for f in fields}
    for cat, fields in _V8_REQUIRED_RAW.items()
}


def _v8_required(cat_name, field_name):
    """依常數表判定 (種類名, 欄名) 是否必填;正規化比對,未列到回 0。"""
    if cat_name is None or field_name is None:
        return 0
    fields = _V8_REQUIRED.get(normalize_key(cat_name))
    if not fields:
        return 0
    return 1 if normalize_key(field_name) in fields else 0


def _mig_category_model_mode(conn):
    """v6→v7:Category 加 model_mode(required/hidden,預設 hidden)。
    初始值:該種類任一 Variant 有 VariantModel 列 → required,否則 hidden。"""
    # 此為本批(v7–v13)第一個遷移;趁 executescript 後仍為自動提交狀態把
    # FK 強制關閉,使後續重建表(v8–v11 DROP 被參照表)不觸發隱式刪除。
    conn.execute("PRAGMA foreign_keys=OFF")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(Category)")}
    if "model_mode" not in cols:
        conn.execute(
            "ALTER TABLE Category ADD COLUMN model_mode TEXT NOT NULL "
            "DEFAULT 'hidden' CHECK(model_mode IN ('required','hidden'))")
    # 極舊版 DB 的 Product 可能無 category_id(字串 category 制);無此欄即無可回填
    # 的資料,維持預設 hidden 即可。
    pcols = {r["name"] for r in conn.execute("PRAGMA table_info(Product)")}
    if "category_id" in pcols:
        conn.execute(
            "UPDATE Category SET model_mode='required' WHERE category_id IN ("
            "  SELECT DISTINCT p.category_id FROM Product p"
            "  JOIN Variant v ON v.product_id=p.product_id"
            "  JOIN VariantModel vm ON vm.variant_id=v.variant_id"
            "  WHERE p.category_id IS NOT NULL)")


def _mig_categoryfield_template(conn):
    """v7→v8:CategoryField 模板化(加 sort/required/default_option_id/active)。
    回填:既有連結列保留;AttributeField.category_id 非 NULL 的專用欄補建對應列,
    sort／default_option_id 自 AttributeField 搬入,active=1;
    required 依維護者核定常數表(正規化比對),未列到一律 0。
    須在 v9(AttributeField 全域化)之前執行,才讀得到 AttributeField.category_id。"""
    conn.execute("PRAGMA foreign_keys=OFF")
    cfcols = {r["name"] for r in conn.execute("PRAGMA table_info(CategoryField)")}
    if "sort" in cfcols:
        return  # 已模板化
    conn.execute("""CREATE TABLE CategoryField_new(
      category_id INTEGER NOT NULL REFERENCES Category(category_id),
      field_id INTEGER NOT NULL REFERENCES AttributeField(field_id),
      sort INTEGER NOT NULL DEFAULT 0,
      required INTEGER NOT NULL DEFAULT 0,
      default_option_id INTEGER REFERENCES AttributeOption(option_id),
      active INTEGER NOT NULL DEFAULT 1,
      PRIMARY KEY(category_id, field_id)
    )""")
    cat_name = {r["category_id"]: r["name"]
                for r in conn.execute("SELECT category_id, name FROM Category")}
    fld = {r["field_id"]: r for r in conn.execute(
        "SELECT field_id, name, category_id, default_option_id, sort "
        "FROM AttributeField")}
    seen = set()
    # (1) 既有連結列:sort/default_option_id 用預設(共用欄無專屬模板值),
    #     required 依常數表。
    for r in conn.execute("SELECT category_id, field_id FROM CategoryField"):
        key = (r["category_id"], r["field_id"])
        if key in seen:
            continue
        seen.add(key)
        f = fld.get(r["field_id"])
        req = _v8_required(cat_name.get(r["category_id"]),
                           f["name"] if f else None)
        conn.execute(
            "INSERT INTO CategoryField_new"
            "(category_id, field_id, sort, required, default_option_id, active) "
            "VALUES(?,?,0,?,NULL,1)",
            (r["category_id"], r["field_id"], req))
    # (2) 專用欄(AttributeField.category_id 非 NULL):補建列,sort/default 自搬。
    for fid, f in fld.items():
        cid = f["category_id"]
        if cid is None:
            continue
        key = (cid, fid)
        if key in seen:
            continue
        seen.add(key)
        req = _v8_required(cat_name.get(cid), f["name"])
        conn.execute(
            "INSERT INTO CategoryField_new"
            "(category_id, field_id, sort, required, default_option_id, active) "
            "VALUES(?,?,?,?,?,1)",
            (cid, fid, f["sort"], req, f["default_option_id"]))
    conn.execute("DROP TABLE CategoryField")
    conn.execute("ALTER TABLE CategoryField_new RENAME TO CategoryField")


def _mig_attributefield_global(conn):
    """v8→v9:AttributeField 全域化(移除 category_id/default_option_id/sort)。
    以 normalize_key 合併同名同 field_type 欄位(保留最小 field_id);
    AttributeOption、VariantAttribute、OptionModel、CategoryField 引用改指存活 id。
    AttributeOption 正規化同值合併(保留最小 option_id)。
    前置:正規化同名但 field_type 不同 → raise 中止並列出衝突。"""
    conn.execute("PRAGMA foreign_keys=OFF")
    fcols = {r["name"] for r in conn.execute("PRAGMA table_info(AttributeField)")}
    if "category_id" not in fcols:
        return  # 已全域化
    fields = conn.execute(
        "SELECT field_id, name, field_type, active FROM AttributeField "
        "ORDER BY field_id").fetchall()
    # 依正規化名分組,檢查同名不同型態
    groups = {}
    for r in fields:
        groups.setdefault(normalize_key(r["name"]), []).append(r)
    conflicts = []
    for key, rows in groups.items():
        types = {r["field_type"] for r in rows}
        if len(types) > 1:
            names = sorted({r["name"] for r in rows})
            conflicts.append(f"  {names}:field_type={sorted(types)}")
    if conflicts:
        raise ValueError(
            "AttributeField 正規化同名但 field_type 不同,無法自動合併,"
            "請人工處理後再升級:\n" + "\n".join(conflicts))
    # field 存活對映(每組最小 field_id)
    fieldmap, survivors = {}, []
    for rows in groups.values():
        survivor = min(rows, key=lambda r: r["field_id"])
        survivors.append(survivor)
        for r in rows:
            fieldmap[r["field_id"]] = survivor["field_id"]
    # 選項:先套 field 對映,再依 (新field, 正規化value) 合併,存活取最小 option_id
    options = conn.execute(
        "SELECT option_id, field_id, value, sort, active FROM AttributeOption "
        "ORDER BY option_id").fetchall()
    ogroups = {}
    for o in options:
        newf = fieldmap.get(o["field_id"], o["field_id"])
        ogroups.setdefault((newf, normalize_key(o["value"])), []).append(o)
    optionmap, osurvivors = {}, []
    for (newf, _vkey), rows in ogroups.items():
        survivor = min(rows, key=lambda r: r["option_id"])
        osurvivors.append((survivor, newf))
        for o in rows:
            optionmap[o["option_id"]] = survivor["option_id"]
    # 重建 AttributeField(全域型)
    conn.execute("""CREATE TABLE AttributeField_new(
      field_id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      field_type TEXT NOT NULL DEFAULT 'select'
        CHECK(field_type IN ('select','text','multi','tags')),
      active INTEGER NOT NULL DEFAULT 1
    )""")
    for s in survivors:
        conn.execute(
            "INSERT INTO AttributeField_new(field_id, name, field_type, active) "
            "VALUES(?,?,?,?)",
            (s["field_id"], s["name"], s["field_type"], s["active"]))
    conn.execute("DROP TABLE AttributeField")
    conn.execute("ALTER TABLE AttributeField_new RENAME TO AttributeField")
    # 重建 AttributeOption(套 field 對映與選項合併)
    conn.execute("""CREATE TABLE AttributeOption_new(
      option_id INTEGER PRIMARY KEY AUTOINCREMENT,
      field_id INTEGER NOT NULL REFERENCES AttributeField(field_id),
      value TEXT NOT NULL,
      sort INTEGER NOT NULL DEFAULT 0,
      active INTEGER NOT NULL DEFAULT 1,
      UNIQUE(field_id, value)
    )""")
    for survivor, newf in osurvivors:
        conn.execute(
            "INSERT INTO AttributeOption_new(option_id, field_id, value, sort, active) "
            "VALUES(?,?,?,?,?)",
            (survivor["option_id"], newf, survivor["value"],
             survivor["sort"], survivor["active"]))
    conn.execute("DROP TABLE AttributeOption")
    conn.execute("ALTER TABLE AttributeOption_new RENAME TO AttributeOption")
    # VariantAttribute:重建套 field/option 對映,依唯一索引鍵去重
    va = conn.execute(
        "SELECT variant_id, field_id, option_id, text_value "
        "FROM VariantAttribute").fetchall()
    conn.execute("DROP TABLE VariantAttribute")
    conn.execute("""CREATE TABLE VariantAttribute(
      variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
      field_id INTEGER NOT NULL REFERENCES AttributeField(field_id),
      option_id INTEGER REFERENCES AttributeOption(option_id),
      text_value TEXT,
      CHECK((option_id IS NULL) <> (text_value IS NULL))
    )""")
    seen_va = set()
    for r in va:
        nf = fieldmap.get(r["field_id"], r["field_id"])
        no = (optionmap.get(r["option_id"], r["option_id"])
              if r["option_id"] is not None else None)
        if no is not None:
            key = (r["variant_id"], nf, no)
            if key in seen_va:
                continue
            seen_va.add(key)
        conn.execute(
            "INSERT INTO VariantAttribute(variant_id, field_id, option_id, text_value) "
            "VALUES(?,?,?,?)", (r["variant_id"], nf, no, r["text_value"]))
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_va_unique "
                 "ON VariantAttribute(variant_id, field_id, option_id)")
    # OptionModel:option_id 改指存活,去重 PK
    om = conn.execute("SELECT option_id, model_id FROM OptionModel").fetchall()
    conn.execute("DROP TABLE OptionModel")
    conn.execute("""CREATE TABLE OptionModel(
      option_id INTEGER NOT NULL REFERENCES AttributeOption(option_id),
      model_id INTEGER NOT NULL REFERENCES PhoneModel(model_id),
      PRIMARY KEY(option_id, model_id)
    )""")
    seen_om = set()
    for r in om:
        no = optionmap.get(r["option_id"], r["option_id"])
        key = (no, r["model_id"])
        if key in seen_om:
            continue
        seen_om.add(key)
        conn.execute("INSERT INTO OptionModel(option_id, model_id) VALUES(?,?)",
                     (no, r["model_id"]))
    # CategoryField:field_id / default_option_id 改指存活,去重 PK
    cf = conn.execute(
        "SELECT category_id, field_id, sort, required, default_option_id, active "
        "FROM CategoryField").fetchall()
    conn.execute("DROP TABLE CategoryField")
    conn.execute("""CREATE TABLE CategoryField(
      category_id INTEGER NOT NULL REFERENCES Category(category_id),
      field_id INTEGER NOT NULL REFERENCES AttributeField(field_id),
      sort INTEGER NOT NULL DEFAULT 0,
      required INTEGER NOT NULL DEFAULT 0,
      default_option_id INTEGER REFERENCES AttributeOption(option_id),
      active INTEGER NOT NULL DEFAULT 1,
      PRIMARY KEY(category_id, field_id)
    )""")
    seen_cf = set()
    for r in cf:
        nf = fieldmap.get(r["field_id"], r["field_id"])
        ndo = (optionmap.get(r["default_option_id"], r["default_option_id"])
               if r["default_option_id"] is not None else None)
        key = (r["category_id"], nf)
        if key in seen_cf:
            continue
        seen_cf.add(key)
        conn.execute(
            "INSERT INTO CategoryField"
            "(category_id, field_id, sort, required, default_option_id, active) "
            "VALUES(?,?,?,?,?,?)",
            (r["category_id"], nf, r["sort"], r["required"], ndo, r["active"]))


def _mig_drop_product_default_price(conn):
    """v9→v10:移除 Product.default_price。先把有值的預設價搬入無價 Variant
    (兩者皆 NULL 維持 NULL,絕不臆造),再重建 Product 去欄。"""
    conn.execute("PRAGMA foreign_keys=OFF")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(Product)")}
    if "default_price" not in cols:
        return
    conn.execute(
        "UPDATE Variant SET price=("
        "  SELECT p.default_price FROM Product p WHERE p.product_id=Variant.product_id) "
        "WHERE Variant.price IS NULL AND ("
        "  SELECT p.default_price FROM Product p WHERE p.product_id=Variant.product_id"
        ") IS NOT NULL")
    conn.execute("""CREATE TABLE Product_new(
      product_id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      category_id INTEGER REFERENCES Category(category_id),
      brand_id INTEGER REFERENCES Brand(brand_id),
      note TEXT,
      active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""")
    conn.execute(
        "INSERT INTO Product_new"
        "(product_id, name, category_id, brand_id, note, active, created_at) "
        "SELECT product_id, name, category_id, brand_id, note, active, created_at "
        "FROM Product")
    conn.execute("DROP TABLE Product")
    conn.execute("ALTER TABLE Product_new RENAME TO Product")


def _mig_drop_brand_active(conn):
    """v10→v11:移除 Brand.active(既有停用廠牌視為可用)。"""
    conn.execute("PRAGMA foreign_keys=OFF")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(Brand)")}
    if "active" not in cols:
        return
    conn.execute("""CREATE TABLE Brand_new(
      brand_id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL UNIQUE,
      sort INTEGER NOT NULL DEFAULT 0
    )""")
    conn.execute(
        "INSERT INTO Brand_new(brand_id, name, sort) "
        "SELECT brand_id, name, sort FROM Brand")
    conn.execute("DROP TABLE Brand")
    conn.execute("ALTER TABLE Brand_new RENAME TO Brand")


def _mig_backfill_brand_category(conn):
    """v11→v12:由既有 Product 回填 BrandCategory(廠牌可用種類)。"""
    # 極舊版 DB 的 Product 可能無 brand_id/category_id 欄,無從回填即略過。
    pcols = {r["name"] for r in conn.execute("PRAGMA table_info(Product)")}
    if "brand_id" not in pcols or "category_id" not in pcols:
        return
    conn.execute(
        "INSERT OR IGNORE INTO BrandCategory(brand_id, category_id) "
        "SELECT DISTINCT brand_id, category_id FROM Product "
        "WHERE brand_id IS NOT NULL AND category_id IS NOT NULL")


def _mig_variant_issue(conn):
    """v12→v13:建 VariantIssue(子產品待處理異常)與 variant_id 索引。"""
    conn.execute("""CREATE TABLE IF NOT EXISTS VariantIssue(
      issue_id INTEGER PRIMARY KEY,
      variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
      issue_type TEXT NOT NULL
        CHECK(issue_type IN ('missing_required','duplicate_barcode','duplicate_signature')),
      field_id INTEGER REFERENCES AttributeField(field_id),
      source_value TEXT,
      related_variant_id INTEGER REFERENCES Variant(variant_id)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_variant_issue_variant "
                 "ON VariantIssue(variant_id)")


MIGRATIONS = [
    (BASE_VERSION + 1, _mig_phone_brand),
    (BASE_VERSION + 2, _mig_variant_attributes),
    (BASE_VERSION + 3, _mig_field_multi),
    (BASE_VERSION + 4, _mig_model_alias),
    (BASE_VERSION + 5, _mig_model_series),
    (BASE_VERSION + 6, _mig_category_model_mode),
    (BASE_VERSION + 7, _mig_categoryfield_template),
    (BASE_VERSION + 8, _mig_attributefield_global),
    (BASE_VERSION + 9, _mig_drop_product_default_price),
    (BASE_VERSION + 10, _mig_drop_brand_active),
    (BASE_VERSION + 11, _mig_backfill_brand_category),
    (BASE_VERSION + 12, _mig_variant_issue),
]

# 最新版號 = 初版 + 遷移筆數;全新 DB 建 SCHEMA 即為此版
SCHEMA_VERSION = BASE_VERSION + len(MIGRATIONS)
