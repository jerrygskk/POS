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
