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

from lib.legacy_migrations import (
    _mig_attributefield_global,
    _mig_backfill_brand_category,
    _mig_category_model_mode,
    _mig_categoryfield_template,
    _mig_drop_brand_active,
    _mig_drop_product_default_price,
    _mig_field_multi,
    _mig_model_alias,
    _mig_model_series,
    _mig_phone_brand,
    _mig_variant_attributes,
    _mig_variant_issue,
)

def _mig_split_style_field(conn):
    """把全域共用的「款式」欄依種類拆成各自一份,選項不互通。

    原本「顏色」「款式」同為全域欄(新種類預設模板),但款式的詞彙各種類完全不同
    (手機殼「磁吸支架(附掛環扣)」vs AppleWatch玻璃「3D全玻璃」),共用會讓建檔候選
    混進別種類的款式。特性詞條先前已因同樣理由改為各種類一份,款式當時漏改。

    搬移規則:每個掛過此欄的種類各建一份同名 select 欄,只複製「該種類真的用過」
    的選項值(兩個種類都用過的值兩邊各一份),VariantAttribute 改指新選項;
    沒有任何種類用過的選項(款式A~C 種子)丟掉,原全域欄清空後刪除。
    """
    rows = conn.execute(
        "SELECT f.field_id FROM AttributeField f WHERE f.name='款式' "
        "AND f.field_type='select' AND (SELECT COUNT(*) FROM CategoryField cf "
        "WHERE cf.field_id=f.field_id) > 1").fetchall()
    for row in rows:
        old_fid = row[0]
        cats = [r[0] for r in conn.execute(
            "SELECT category_id FROM CategoryField WHERE field_id=? ORDER BY category_id",
            (old_fid,))]
        for cid in cats:
            new_fid = conn.execute(
                "INSERT INTO AttributeField(name,field_type) VALUES('款式','select')"
            ).lastrowid
            used = conn.execute(
                "SELECT DISTINCT o.option_id, o.value, o.sort, o.active "
                "FROM AttributeOption o JOIN VariantAttribute va ON va.option_id=o.option_id "
                "JOIN Variant v ON v.variant_id=va.variant_id "
                "JOIN Product p ON p.product_id=v.product_id "
                "WHERE o.field_id=? AND p.category_id=? ORDER BY o.sort, o.option_id",
                (old_fid, cid)).fetchall()
            copies = {}
            for opt_id, value, sort, active in used:
                copy_id = conn.execute(
                    "INSERT INTO AttributeOption(field_id,value,sort,active) "
                    "VALUES(?,?,?,?)", (new_fid, value, sort, active)).lastrowid
                conn.execute(
                    "UPDATE VariantAttribute SET field_id=?, option_id=? "
                    "WHERE option_id=? AND variant_id IN ("
                    "  SELECT v.variant_id FROM Variant v JOIN Product p "
                    "  ON p.product_id=v.product_id WHERE p.category_id=?)",
                    (new_fid, copy_id, opt_id, cid))
                # 限定型號(特別色)跟著複製,否則新選項會從「限定」變成「通用」
                conn.execute(
                    "INSERT INTO OptionModel(option_id,model_id) "
                    "SELECT ?, model_id FROM OptionModel WHERE option_id=?",
                    (copy_id, opt_id))
                copies[opt_id] = copy_id
            row_sort, required, active, default_id = conn.execute(
                "SELECT sort, required, active, default_option_id FROM CategoryField "
                "WHERE category_id=? AND field_id=?", (cid, old_fid)).fetchone()
            conn.execute(
                "INSERT INTO CategoryField(category_id,field_id,sort,required,active,"
                "default_option_id) VALUES(?,?,?,?,?,?)",
                (cid, new_fid, row_sort, required, active, copies.get(default_id)))
        conn.execute("DELETE FROM CategoryField WHERE field_id=?", (old_fid,))
        conn.execute(
            "DELETE FROM OptionModel WHERE option_id IN "
            "(SELECT option_id FROM AttributeOption WHERE field_id=?)", (old_fid,))
        conn.execute("DELETE FROM AttributeOption WHERE field_id=?", (old_fid,))
        conn.execute("DELETE FROM AttributeField WHERE field_id=?", (old_fid,))
    # 沒掛任何種類、也沒人填過值的全域款式欄(全新資料庫的種子)一併清掉:
    # 款式改為建立種類時各自新建,不再留一份共用主檔。
    conn.execute(
        "DELETE FROM AttributeOption WHERE field_id IN ("
        "  SELECT f.field_id FROM AttributeField f WHERE f.name='款式' "
        "  AND NOT EXISTS(SELECT 1 FROM CategoryField cf WHERE cf.field_id=f.field_id) "
        "  AND NOT EXISTS(SELECT 1 FROM VariantAttribute va WHERE va.field_id=f.field_id))")
    conn.execute(
        "DELETE FROM AttributeField WHERE name='款式' "
        "AND NOT EXISTS(SELECT 1 FROM CategoryField cf WHERE cf.field_id=AttributeField.field_id) "
        "AND NOT EXISTS(SELECT 1 FROM VariantAttribute va WHERE va.field_id=AttributeField.field_id) "
        "AND NOT EXISTS(SELECT 1 FROM AttributeOption o WHERE o.field_id=AttributeField.field_id)")


def _mig_split_shared_fields(conn):
    """把所有跨種類共用的規格欄依種類拆成各自一份(款式之外的其餘欄)。

    v14 先拆了款式;實務上「顏色」也一樣會混:手機殼的天峰藍不該出現在傳輸線的
    候選裡。與其教店員分辨哪些欄共用,不如一律不共用——設定頁就不必解釋這件事。
    搬移規則同 v14:每個種類各建一份同名同型別的欄,只複製該種類用過的選項
    (含限定型號與模板預設值),text 欄直接改指新欄;沒人用過的選項丟掉,原欄刪除。
    """
    shared = conn.execute(
        "SELECT f.field_id, f.name, f.field_type, f.active FROM AttributeField f "
        "WHERE (SELECT COUNT(*) FROM CategoryField cf WHERE cf.field_id=f.field_id) > 1"
    ).fetchall()
    for old_fid, name, field_type, active in shared:
        _split_field_by_category(conn, old_fid, name, field_type, active)
    # 沒掛任何種類、也沒人填過值的全域欄(種子的商品描述之類)一併清掉:
    # 欄位一律由種類自己建,不再留共用主檔。
    conn.execute(
        "DELETE FROM AttributeOption WHERE field_id IN ("
        "  SELECT f.field_id FROM AttributeField f "
        "  WHERE NOT EXISTS(SELECT 1 FROM CategoryField cf WHERE cf.field_id=f.field_id) "
        "  AND NOT EXISTS(SELECT 1 FROM VariantAttribute va WHERE va.field_id=f.field_id))")
    conn.execute(
        "DELETE FROM AttributeField WHERE "
        "NOT EXISTS(SELECT 1 FROM CategoryField cf WHERE cf.field_id=AttributeField.field_id) "
        "AND NOT EXISTS(SELECT 1 FROM VariantAttribute va "
        "               WHERE va.field_id=AttributeField.field_id) "
        "AND NOT EXISTS(SELECT 1 FROM AttributeOption o "
        "               WHERE o.field_id=AttributeField.field_id)")


def _split_field_by_category(conn, old_fid, name, field_type, active=1):
    """把一個跨種類共用的欄拆成每個種類各一份,並刪掉原欄。"""
    cats = [r[0] for r in conn.execute(
        "SELECT category_id FROM CategoryField WHERE field_id=? ORDER BY category_id",
        (old_fid,))]
    for cid in cats:
        new_fid = conn.execute(
            "INSERT INTO AttributeField(name,field_type,active) VALUES(?,?,?)",
            (name, field_type, active)).lastrowid
        copies = {}
        used = conn.execute(
            "SELECT DISTINCT o.option_id, o.value, o.sort, o.active "
            "FROM AttributeOption o JOIN VariantAttribute va ON va.option_id=o.option_id "
            "JOIN Variant v ON v.variant_id=va.variant_id "
            "JOIN Product p ON p.product_id=v.product_id "
            "WHERE o.field_id=? AND p.category_id=? ORDER BY o.sort, o.option_id",
            (old_fid, cid)).fetchall()
        for opt_id, value, sort, opt_active in used:
            copy_id = conn.execute(
                "INSERT INTO AttributeOption(field_id,value,sort,active) VALUES(?,?,?,?)",
                (new_fid, value, sort, opt_active)).lastrowid
            conn.execute(
                "UPDATE VariantAttribute SET field_id=?, option_id=? "
                "WHERE option_id=? AND variant_id IN ("
                "  SELECT v.variant_id FROM Variant v JOIN Product p "
                "  ON p.product_id=v.product_id WHERE p.category_id=?)",
                (new_fid, copy_id, opt_id, cid))
            # 限定型號(特別色)跟著複製,否則新選項會從「限定」變成「通用」
            conn.execute(
                "INSERT INTO OptionModel(option_id,model_id) "
                "SELECT ?, model_id FROM OptionModel WHERE option_id=?",
                (copy_id, opt_id))
            copies[opt_id] = copy_id
        # text 欄沒有選項,值直接改指新欄
        conn.execute(
            "UPDATE VariantAttribute SET field_id=? WHERE field_id=? AND option_id IS NULL "
            "AND variant_id IN (SELECT v.variant_id FROM Variant v JOIN Product p "
            "ON p.product_id=v.product_id WHERE p.category_id=?)",
            (new_fid, old_fid, cid))
        row_sort, required, cf_active, default_id = conn.execute(
            "SELECT sort, required, active, default_option_id FROM CategoryField "
            "WHERE category_id=? AND field_id=?", (cid, old_fid)).fetchone()
        conn.execute(
            "INSERT INTO CategoryField(category_id,field_id,sort,required,active,"
            "default_option_id) VALUES(?,?,?,?,?,?)",
            (cid, new_fid, row_sort, required, cf_active, copies.get(default_id)))
    conn.execute("DELETE FROM CategoryField WHERE field_id=?", (old_fid,))
    conn.execute(
        "DELETE FROM OptionModel WHERE option_id IN "
        "(SELECT option_id FROM AttributeOption WHERE field_id=?)", (old_fid,))
    conn.execute("DELETE FROM AttributeOption WHERE field_id=?", (old_fid,))
    conn.execute("DELETE FROM AttributeField WHERE field_id=?", (old_fid,))


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
    (BASE_VERSION + 13, _mig_split_style_field),
    (BASE_VERSION + 14, _mig_split_shared_fields),
]

# 最新版號 = 初版 + 遷移筆數;全新 DB 建 SCHEMA 即為此版
SCHEMA_VERSION = BASE_VERSION + len(MIGRATIONS)
