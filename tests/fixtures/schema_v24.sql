-- v24（正規化前最後一版）的完整結構，凍結自 commit 803df7e 的 lib/db_schema.py。
-- 只供 tests/test_standard_db.py 建立母版工具的轉換來源，不可隨現行 SCHEMA 修改。
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
CREATE TABLE IF NOT EXISTS DeviceType(        -- 裝置類型:手機／平板／耳機…(管理者維護)
  device_type_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS PhoneModel(
  model_id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone_brand_id INTEGER NOT NULL REFERENCES PhoneBrand(phone_brand_id),
  device_type_id INTEGER NOT NULL REFERENCES DeviceType(device_type_id),
  name TEXT NOT NULL,
  alias TEXT,                       -- 顯示別名(空=顯示全名)
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(phone_brand_id, device_type_id, name)  -- 不同類型可同名(如手機與平板同叫 Pro)
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
CREATE TABLE IF NOT EXISTS Barcode(               -- 原廠碼可多個子產品共用;店內碼全域唯一(部分唯一索引)
  barcode TEXT NOT NULL,
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  source TEXT NOT NULL CHECK(source IN ('factory','store')),
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  PRIMARY KEY(barcode, variant_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_barcode_store ON Barcode(barcode) WHERE source='store';
CREATE INDEX IF NOT EXISTS idx_barcode_variant ON Barcode(variant_id);
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
  qty INTEGER NOT NULL,           -- 進貨+/銷售-/盤點±/退貨加回+/返新進貨+
  kind TEXT NOT NULL CHECK(kind IN ('purchase','sale','adjust','return','repair')),
  ref_id INTEGER,                 -- sale_id、session_id 或 return_id
  note TEXT,
  employee INTEGER CHECK(employee BETWEEN 1 AND 6),  -- 進貨操作員工代號(銷售看 Sale、盤點看 StocktakeSession)
  ts TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_move_variant ON StockMovement(variant_id);
CREATE TABLE IF NOT EXISTS Sale(
  sale_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  payment TEXT NOT NULL,
  order_discount INTEGER NOT NULL DEFAULT 0,  -- 整單折抵(元)
  member_discount INTEGER NOT NULL DEFAULT 0,  -- 會員折扣(元,客人以點數折換)
  total INTEGER NOT NULL,         -- 應收
  paid INTEGER NOT NULL,          -- 實收
  change INTEGER NOT NULL DEFAULT 0,
  employee INTEGER CHECK(employee BETWEEN 1 AND 6),  -- 結帳員工代號;舊單為 NULL
  member_id INTEGER REFERENCES Member(member_id)  -- 會員;一般客人與舊單為 NULL
);
CREATE TABLE IF NOT EXISTS SaleItem(
  item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  sale_id INTEGER NOT NULL REFERENCES Sale(sale_id),
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  qty INTEGER NOT NULL,
  unit_price INTEGER NOT NULL,    -- 原價(商品售價或未定價時店員輸入)
  discount INTEGER NOT NULL DEFAULT 0  -- 單品折扣(元)
);
CREATE TABLE IF NOT EXISTS StocktakeSession(
  session_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  ended_at TEXT,
  submitted_at TEXT,
  approved_at TEXT,
  cancelled_at TEXT,
  employee INTEGER CHECK(employee BETWEEN 1 AND 6),  -- 盤點員工代號
  cancelled_by INTEGER CHECK(cancelled_by BETWEEN 1 AND 6),  -- 作廢員工代號
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','pending','closed','cancelled')),
  note TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_stocktake_one_active
  ON StocktakeSession((status IN ('open','pending'))) WHERE status IN ('open','pending');
CREATE TABLE IF NOT EXISTS StocktakeItem(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES StocktakeSession(session_id),
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  system_qty INTEGER NOT NULL,    -- 首次掃描或最近一次抽回時的庫存快照
  counted_qty INTEGER NOT NULL DEFAULT 0,
  needs_recount INTEGER NOT NULL DEFAULT 0 CHECK(needs_recount IN (0,1)),
  note TEXT,                      -- 差異原因(選填)
  UNIQUE(session_id, variant_id)
);
CREATE TABLE IF NOT EXISTS ReturnSession(      -- 退貨單:送審後由管理者決定各品項處理方式
  return_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  submitted_at TEXT,
  approved_at TEXT,
  cancelled_at TEXT,
  employee INTEGER CHECK(employee BETWEEN 1 AND 6),
  cancelled_by INTEGER CHECK(cancelled_by BETWEEN 1 AND 6),
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','pending','repairing','closed','cancelled')),
  refund_amount INTEGER,                        -- 退款金額:預留,尚未使用
  completed_at TEXT                             -- 返修單全部品項返新或結案的時間
);
CREATE TABLE IF NOT EXISTS ReturnItem(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  return_id INTEGER NOT NULL REFERENCES ReturnSession(return_id),
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  qty INTEGER NOT NULL CHECK(qty > 0),
  sale_id INTEGER REFERENCES Sale(sale_id),     -- 原銷售單(選填)
  reason TEXT,
  disposition TEXT NOT NULL DEFAULT 'restock'
    CHECK(disposition IN ('restock','discard','reject','repair')),  -- 加回庫存／退貨不入庫／不同意／返修不入庫
  closed_qty INTEGER NOT NULL DEFAULT 0,        -- 返修結案件數(廠商不換不修)，已返新件數看 StockMovement kind=repair
  closed_reason TEXT,
  closed_at TEXT,
  UNIQUE(return_id, variant_id)
);
CREATE TABLE IF NOT EXISTS Setting(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS VariantIssue(      -- 子產品待處理異常(必填缺值/簽章重複)
  issue_id INTEGER PRIMARY KEY,
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  issue_type TEXT NOT NULL
    CHECK(issue_type IN ('missing_required','duplicate_signature')),
  field_id INTEGER REFERENCES AttributeField(field_id),        -- 相關欄位(適用時)
  source_value TEXT,                                           -- 觸發異常的原始值(適用時)
  related_variant_id INTEGER REFERENCES Variant(variant_id)    -- 衝突的另一子產品(適用時)
);
CREATE INDEX IF NOT EXISTS idx_variant_issue_variant ON VariantIssue(variant_id);
CREATE TABLE IF NOT EXISTS Member(
  member_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,  -- 刪除＝停用:舊銷售仍指向他
  ts TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS MemberPhone(
  member_id INTEGER NOT NULL REFERENCES Member(member_id),
  phone TEXT NOT NULL CHECK(length(phone)=10 AND phone GLOB '09[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),
  PRIMARY KEY(member_id, phone)  -- 多對多:同一支電話可屬於多位會員
);
CREATE INDEX IF NOT EXISTS idx_memberphone_phone ON MemberPhone(phone);
