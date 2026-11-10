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
  -- multi/tags 允許同 (variant_id, field_id) 多筆;select 單值、欄型別與值型別對應、
  -- 規格欄種類＝商品種類,由 product_data.check_variant_attributes 檢查。
  -- 唯一索引擋同欄重覆選同一選項(兼作 multi/tags 去重)。
  -- option_id 與 text_value 恰一非 NULL(XOR)
  CHECK((option_id IS NULL) <> (text_value IS NULL)),
  FOREIGN KEY(option_id, field_id) REFERENCES AttributeOption(option_id, field_id)  -- 選項必屬於該欄
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_va_unique
  ON VariantAttribute(variant_id, field_id, option_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_va_text_unique
  ON VariantAttribute(variant_id, field_id) WHERE text_value IS NOT NULL;
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
CREATE TABLE IF NOT EXISTS AttributeField(       -- 規格欄:每欄隸屬一個種類,不跨種類共用
  field_id INTEGER PRIMARY KEY AUTOINCREMENT,
  category_id INTEGER NOT NULL REFERENCES Category(category_id),
  name TEXT NOT NULL,
  field_type TEXT NOT NULL DEFAULT 'select'
    CHECK(field_type IN ('select','text','multi','tags')),
  sort INTEGER NOT NULL DEFAULT 0,
  required INTEGER NOT NULL DEFAULT 0 CHECK(required IN (0,1)),
  default_option_id INTEGER,                       -- 建檔預設帶入,只限 select
  active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(category_id, name),                       -- 正規化同名(全半形、大小寫)由應用層再擋
  CHECK(default_option_id IS NULL OR field_type='select'),
  FOREIGN KEY(default_option_id, field_id) REFERENCES AttributeOption(option_id, field_id)
);
CREATE INDEX IF NOT EXISTS idx_field_category ON AttributeField(category_id);
CREATE TABLE IF NOT EXISTS AttributeOption(
  option_id INTEGER PRIMARY KEY AUTOINCREMENT,
  field_id INTEGER NOT NULL REFERENCES AttributeField(field_id),
  value TEXT NOT NULL,
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(field_id, value),
  UNIQUE(option_id, field_id)                      -- 供規格值與預設選項的複合外鍵
);
CREATE TABLE IF NOT EXISTS VariantModel(
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  model_id INTEGER NOT NULL REFERENCES PhoneModel(model_id),
  PRIMARY KEY(variant_id, model_id)
);
CREATE TABLE IF NOT EXISTS StockMovement(
  move_id INTEGER PRIMARY KEY AUTOINCREMENT,
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  qty INTEGER NOT NULL,           -- 進貨+/銷售-/盤點±/退貨加回+/返新進貨+/作廢沖回+
  kind TEXT NOT NULL CHECK(kind IN ('purchase','sale','adjust','return','repair','void')),
  -- 來源明細:恰一個非 NULL,且須符合 kind(purchase→進貨明細、sale／void→銷售明細、
  -- adjust→盤點明細、return/repair→退貨品項)。複合外鍵保證來源與異動同款式。
  receipt_item_id INTEGER,
  sale_item_id INTEGER,
  stocktake_item_id INTEGER,
  return_item_id INTEGER,
  note TEXT,
  employee INTEGER CHECK(employee BETWEEN 1 AND 6),  -- 只有返新(repair)記操作員工,其他類型看各自單據
  ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  CHECK((receipt_item_id IS NOT NULL) + (sale_item_id IS NOT NULL)
        + (stocktake_item_id IS NOT NULL) + (return_item_id IS NOT NULL) = 1),
  CHECK((kind='purchase' AND receipt_item_id IS NOT NULL AND qty > 0)
     OR (kind='sale' AND sale_item_id IS NOT NULL AND qty < 0)
     OR (kind='void' AND sale_item_id IS NOT NULL AND qty > 0)
     OR (kind='adjust' AND stocktake_item_id IS NOT NULL AND qty <> 0)
     OR (kind IN ('return','repair') AND return_item_id IS NOT NULL AND qty > 0)),
  CHECK((kind='repair') = (employee IS NOT NULL)),
  FOREIGN KEY(receipt_item_id, variant_id) REFERENCES ReceiptItem(receipt_item_id, variant_id),
  FOREIGN KEY(sale_item_id, variant_id) REFERENCES SaleItem(item_id, variant_id),
  FOREIGN KEY(stocktake_item_id, variant_id) REFERENCES StocktakeItem(id, variant_id),
  FOREIGN KEY(return_item_id, variant_id) REFERENCES ReturnItem(id, variant_id)
);
CREATE INDEX IF NOT EXISTS idx_move_variant ON StockMovement(variant_id);
-- 同一明細只能入帳一次;返新(repair)可分批,不套用。銷售明細可各有一筆銷售與一筆作廢沖回
CREATE UNIQUE INDEX IF NOT EXISTS idx_move_receipt_item ON StockMovement(receipt_item_id) WHERE receipt_item_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_move_sale_item ON StockMovement(sale_item_id) WHERE kind='sale';
CREATE UNIQUE INDEX IF NOT EXISTS idx_move_void_item ON StockMovement(sale_item_id) WHERE kind='void';
CREATE UNIQUE INDEX IF NOT EXISTS idx_move_stocktake_item ON StockMovement(stocktake_item_id) WHERE stocktake_item_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_move_return_item ON StockMovement(return_item_id) WHERE kind='return';
CREATE INDEX IF NOT EXISTS idx_move_repair ON StockMovement(return_item_id) WHERE kind='repair';
CREATE TABLE IF NOT EXISTS Receipt(             -- 一般進貨單(返新不建進貨單,直接指向退貨品項)
  receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  employee INTEGER NOT NULL CHECK(employee BETWEEN 1 AND 6)
);
CREATE TABLE IF NOT EXISTS ReceiptItem(
  receipt_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  receipt_id INTEGER NOT NULL REFERENCES Receipt(receipt_id),
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  qty INTEGER NOT NULL CHECK(qty > 0),           -- 同款式可多列,各自保留數量
  UNIQUE(receipt_item_id, variant_id)
);
CREATE INDEX IF NOT EXISTS idx_receipt_item_receipt ON ReceiptItem(receipt_id);
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
  member_id INTEGER REFERENCES Member(member_id),  -- 會員;一般客人與舊單為 NULL
  -- 帳日:當天已點帳後才結的單算隔天的帳,由結帳的 INSERT 同一句決定
  business_date TEXT NOT NULL
    CHECK(business_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  voided_at TEXT,                 -- 作廢時間;NULL＝有效
  void_reason TEXT,               -- 不記是誰作廢的(維護者 2026-11-10 裁示),只記時間與原因
  CHECK((voided_at IS NULL) = (void_reason IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_sale_business_date ON Sale(business_date);
CREATE TABLE IF NOT EXISTS SaleItem(
  item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  sale_id INTEGER NOT NULL REFERENCES Sale(sale_id),
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  qty INTEGER NOT NULL,
  unit_price INTEGER NOT NULL,    -- 原價(商品售價或未定價時店員輸入)
  discount INTEGER NOT NULL DEFAULT 0,  -- 單品折扣(元)
  UNIQUE(item_id, variant_id)
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
  UNIQUE(session_id, variant_id),
  UNIQUE(id, variant_id)
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
  completed_at TEXT,                            -- 返修單全部品項返新或結案的時間
  kind TEXT NOT NULL DEFAULT 'normal' CHECK(kind IN ('normal','repair'))  -- 整張單的模式:一般／返修
);
CREATE TABLE IF NOT EXISTS ReturnItem(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  return_id INTEGER NOT NULL REFERENCES ReturnSession(return_id),
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  qty INTEGER NOT NULL CHECK(qty > 0),
  sale_id INTEGER REFERENCES Sale(sale_id),     -- 原銷售單(選填)
  reason TEXT,
  -- 一般退貨:加回庫存／退貨不入庫／不同意。返修單的品項為 NULL
  -- (與父單 kind 一致由 returns_service._check_kind 在同一交易檢查)
  disposition TEXT DEFAULT 'restock' CHECK(disposition IN ('restock','discard','reject')),
  closed_qty INTEGER NOT NULL DEFAULT 0,        -- 返修結案件數(廠商不換不修)，已返新件數看 StockMovement kind=repair
  closed_reason TEXT,
  closed_at TEXT,
  CHECK(closed_qty BETWEEN 0 AND qty),
  CHECK(closed_qty = 0 OR (closed_reason IS NOT NULL AND closed_at IS NOT NULL)),
  CHECK(closed_qty = 0 OR disposition IS NULL),  -- 只有返修品項能結案
  UNIQUE(return_id, variant_id),
  UNIQUE(id, variant_id)
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
CREATE TABLE IF NOT EXISTS DayClose(           -- 每日點帳:一個帳日一筆,不可改刪
  business_date TEXT PRIMARY KEY NOT NULL   -- TEXT 主鍵不寫 NOT NULL 可塞多筆 NULL
    CHECK(business_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  closed_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  employee INTEGER NOT NULL CHECK(employee BETWEEN 1 AND 6),
  float_amount INTEGER NOT NULL CHECK(float_amount >= 0),    -- 點帳當下的預備金
  counted_total INTEGER NOT NULL CHECK(counted_total >= 0),  -- 輸入的錢櫃總額(含預備金)
  cash_sales INTEGER NOT NULL CHECK(cash_sales >= 0),        -- 點帳當下的現金銷售(不含作廢)
  diff INTEGER NOT NULL,                                     -- 短溢,負＝短
  reason TEXT,
  CHECK(diff = counted_total - float_amount - cash_sales),
  CHECK(diff = 0 OR (reason IS NOT NULL AND length(trim(reason)) > 0))
);
CREATE TABLE IF NOT EXISTS Correction(         -- 更正紀錄(付款方式、員工):恰一個來源單據,不可改刪
  correction_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  sale_id INTEGER REFERENCES Sale(sale_id),
  return_id INTEGER REFERENCES ReturnSession(return_id),
  receipt_id INTEGER REFERENCES Receipt(receipt_id),
  stocktake_id INTEGER REFERENCES StocktakeSession(session_id),
  field TEXT NOT NULL CHECK(field IN ('payment','employee')),
  old_value TEXT,                    -- 舊銷售單的員工可能是 NULL
  new_value TEXT NOT NULL,           -- 不記是誰更正的,只記時間與改前改後
  CHECK((sale_id IS NOT NULL) + (return_id IS NOT NULL)
        + (receipt_id IS NOT NULL) + (stocktake_id IS NOT NULL) = 1),
  CHECK(field <> 'payment' OR sale_id IS NOT NULL),
  CHECK(old_value IS NOT new_value)
);
CREATE INDEX IF NOT EXISTS idx_correction_sale ON Correction(sale_id) WHERE sale_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_correction_return ON Correction(return_id) WHERE return_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_correction_receipt ON Correction(receipt_id) WHERE receipt_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_correction_stocktake ON Correction(stocktake_id) WHERE stocktake_id IS NOT NULL;
-- 已入帳明細鎖定:有庫存異動指向的明細,只擋被鎖欄位真的改值(OLD.x IS NOT NEW.x)與刪除。
-- 返修結案／撤回結案、退貨原因、盤點備註、needs_recount、單據狀態不在鎖定範圍。
CREATE TRIGGER IF NOT EXISTS trg_receiptitem_lock_update BEFORE UPDATE ON ReceiptItem
  WHEN EXISTS(SELECT 1 FROM StockMovement WHERE receipt_item_id=OLD.receipt_item_id) AND (OLD.receipt_id IS NOT NEW.receipt_id OR OLD.variant_id IS NOT NEW.variant_id OR OLD.qty IS NOT NEW.qty)
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_receiptitem_lock_delete BEFORE DELETE ON ReceiptItem
  WHEN EXISTS(SELECT 1 FROM StockMovement WHERE receipt_item_id=OLD.receipt_item_id)
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_saleitem_lock_update BEFORE UPDATE ON SaleItem
  WHEN EXISTS(SELECT 1 FROM StockMovement WHERE sale_item_id=OLD.item_id) AND (OLD.sale_id IS NOT NEW.sale_id OR OLD.variant_id IS NOT NEW.variant_id OR OLD.qty IS NOT NEW.qty OR OLD.unit_price IS NOT NEW.unit_price OR OLD.discount IS NOT NEW.discount)
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_saleitem_lock_delete BEFORE DELETE ON SaleItem
  WHEN EXISTS(SELECT 1 FROM StockMovement WHERE sale_item_id=OLD.item_id)
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_stocktakeitem_lock_update BEFORE UPDATE ON StocktakeItem
  WHEN EXISTS(SELECT 1 FROM StockMovement WHERE stocktake_item_id=OLD.id) AND (OLD.session_id IS NOT NEW.session_id OR OLD.variant_id IS NOT NEW.variant_id OR OLD.system_qty IS NOT NEW.system_qty OR OLD.counted_qty IS NOT NEW.counted_qty)
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_stocktakeitem_lock_delete BEFORE DELETE ON StocktakeItem
  WHEN EXISTS(SELECT 1 FROM StockMovement WHERE stocktake_item_id=OLD.id)
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_returnitem_lock_update BEFORE UPDATE ON ReturnItem
  WHEN EXISTS(SELECT 1 FROM StockMovement WHERE return_item_id=OLD.id) AND (OLD.return_id IS NOT NEW.return_id OR OLD.variant_id IS NOT NEW.variant_id OR OLD.qty IS NOT NEW.qty OR OLD.disposition IS NOT NEW.disposition)
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_returnitem_lock_delete BEFORE DELETE ON ReturnItem
  WHEN EXISTS(SELECT 1 FROM StockMovement WHERE return_item_id=OLD.id)
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_sale_lock_update BEFORE UPDATE ON Sale
  WHEN EXISTS(SELECT 1 FROM SaleItem si JOIN StockMovement m ON m.sale_item_id=si.item_id WHERE si.sale_id=OLD.sale_id)
   AND (OLD.order_discount IS NOT NEW.order_discount OR OLD.member_discount IS NOT NEW.member_discount
        OR OLD.total IS NOT NEW.total OR OLD.paid IS NOT NEW.paid OR OLD.change IS NOT NEW.change
        OR OLD.business_date IS NOT NEW.business_date)
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
-- INSERT OR REPLACE 撞到既有列時會先隱含刪除,刪除觸發器不會觸發(未開 recursive_triggers):
-- 新增前若會撞到已入帳的列(主鍵或任一唯一鍵)就擋下
CREATE TRIGGER IF NOT EXISTS trg_receiptitem_lock_replace BEFORE INSERT ON ReceiptItem
  WHEN EXISTS(SELECT 1 FROM StockMovement WHERE receipt_item_id=NEW.receipt_item_id)
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_saleitem_lock_replace BEFORE INSERT ON SaleItem
  WHEN EXISTS(SELECT 1 FROM StockMovement WHERE sale_item_id=NEW.item_id)
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_stocktakeitem_lock_replace BEFORE INSERT ON StocktakeItem
  WHEN EXISTS(SELECT 1 FROM StocktakeItem i JOIN StockMovement m ON m.stocktake_item_id=i.id
              WHERE i.id=NEW.id OR (i.session_id=NEW.session_id AND i.variant_id=NEW.variant_id))
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_returnitem_lock_replace BEFORE INSERT ON ReturnItem
  WHEN EXISTS(SELECT 1 FROM ReturnItem i JOIN StockMovement m ON m.return_item_id=i.id
              WHERE i.id=NEW.id OR (i.return_id=NEW.return_id AND i.variant_id=NEW.variant_id))
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_sale_lock_replace BEFORE INSERT ON Sale
  WHEN EXISTS(SELECT 1 FROM SaleItem si JOIN StockMovement m ON m.sale_item_id=si.item_id WHERE si.sale_id=NEW.sale_id)
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
-- UPDATE OR REPLACE 把別列改成撞上已入帳列的鍵時,同樣會隱含刪除已入帳列:更新前一併擋下
CREATE TRIGGER IF NOT EXISTS trg_receiptitem_lock_update_replace BEFORE UPDATE ON ReceiptItem
  WHEN NEW.receipt_item_id IS NOT OLD.receipt_item_id
   AND EXISTS(SELECT 1 FROM StockMovement WHERE receipt_item_id=NEW.receipt_item_id)
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_saleitem_lock_update_replace BEFORE UPDATE ON SaleItem
  WHEN NEW.item_id IS NOT OLD.item_id
   AND EXISTS(SELECT 1 FROM StockMovement WHERE sale_item_id=NEW.item_id)
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_sale_lock_update_replace BEFORE UPDATE ON Sale
  WHEN NEW.sale_id IS NOT OLD.sale_id
   AND EXISTS(SELECT 1 FROM SaleItem si JOIN StockMovement m ON m.sale_item_id=si.item_id WHERE si.sale_id=NEW.sale_id)
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_stocktakeitem_lock_update_replace BEFORE UPDATE ON StocktakeItem
  WHEN EXISTS(SELECT 1 FROM StocktakeItem i JOIN StockMovement m ON m.stocktake_item_id=i.id
              WHERE i.id<>OLD.id AND (i.id=NEW.id OR (i.session_id=NEW.session_id AND i.variant_id=NEW.variant_id)))
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_returnitem_lock_update_replace BEFORE UPDATE ON ReturnItem
  WHEN EXISTS(SELECT 1 FROM ReturnItem i JOIN StockMovement m ON m.return_item_id=i.id
              WHERE i.id<>OLD.id AND (i.id=NEW.id OR (i.return_id=NEW.return_id AND i.variant_id=NEW.variant_id)))
  BEGIN SELECT RAISE(ABORT, '已入帳明細不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_stockmovement_no_replace BEFORE INSERT ON StockMovement
  WHEN EXISTS(SELECT 1 FROM StockMovement WHERE move_id=NEW.move_id
              OR receipt_item_id=NEW.receipt_item_id
              OR (sale_item_id=NEW.sale_item_id AND kind=NEW.kind)
              OR stocktake_item_id=NEW.stocktake_item_id
              OR (kind='return' AND NEW.kind='return' AND return_item_id=NEW.return_item_id))
  BEGIN SELECT RAISE(ABORT, '庫存異動不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_stockmovement_no_update BEFORE UPDATE ON StockMovement
  BEGIN SELECT RAISE(ABORT, '庫存異動不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_stockmovement_no_delete BEFORE DELETE ON StockMovement
  BEGIN SELECT RAISE(ABORT, '庫存異動不可修改或刪除'); END;
-- 鎖帳:帳日已點帳的銷售單,員工代號以外一律不可新增、修改、刪除(只看帳日,不看有沒有庫存異動)。
-- 連同兩種 REPLACE 撞上已點帳單的主鍵一併擋下
CREATE TRIGGER IF NOT EXISTS trg_sale_closed_insert BEFORE INSERT ON Sale
  WHEN EXISTS(SELECT 1 FROM DayClose WHERE business_date=NEW.business_date)
    OR EXISTS(SELECT 1 FROM Sale s JOIN DayClose d ON d.business_date=s.business_date WHERE s.sale_id=NEW.sale_id)
  BEGIN SELECT RAISE(ABORT, '此帳日已點帳，銷售單不可新增或修改'); END;
CREATE TRIGGER IF NOT EXISTS trg_sale_closed_update BEFORE UPDATE ON Sale
  WHEN (EXISTS(SELECT 1 FROM DayClose WHERE business_date=OLD.business_date)
        OR EXISTS(SELECT 1 FROM DayClose WHERE business_date=NEW.business_date))
   AND (OLD.sale_id IS NOT NEW.sale_id OR OLD.ts IS NOT NEW.ts OR OLD.payment IS NOT NEW.payment
        OR OLD.order_discount IS NOT NEW.order_discount OR OLD.member_discount IS NOT NEW.member_discount
        OR OLD.total IS NOT NEW.total OR OLD.paid IS NOT NEW.paid OR OLD.change IS NOT NEW.change
        OR OLD.member_id IS NOT NEW.member_id OR OLD.business_date IS NOT NEW.business_date
        OR OLD.voided_at IS NOT NEW.voided_at OR OLD.void_reason IS NOT NEW.void_reason)
  BEGIN SELECT RAISE(ABORT, '此帳日已點帳，銷售單不可新增或修改'); END;
CREATE TRIGGER IF NOT EXISTS trg_sale_closed_update_replace BEFORE UPDATE ON Sale
  WHEN NEW.sale_id IS NOT OLD.sale_id
   AND EXISTS(SELECT 1 FROM Sale s JOIN DayClose d ON d.business_date=s.business_date WHERE s.sale_id=NEW.sale_id)
  BEGIN SELECT RAISE(ABORT, '此帳日已點帳，銷售單不可新增或修改'); END;
CREATE TRIGGER IF NOT EXISTS trg_sale_closed_delete BEFORE DELETE ON Sale
  WHEN EXISTS(SELECT 1 FROM DayClose WHERE business_date=OLD.business_date)
  BEGIN SELECT RAISE(ABORT, '此帳日已點帳，銷售單不可新增或修改'); END;
-- 作廢不可取消:作廢欄位寫入後不可再變,已作廢的單不可改付款方式、不可刪除、
-- 不可被 REPLACE 蓋掉(員工代號仍可更正)
CREATE TRIGGER IF NOT EXISTS trg_sale_void_final BEFORE UPDATE ON Sale
  WHEN OLD.voided_at IS NOT NULL
   AND (OLD.voided_at IS NOT NEW.voided_at OR OLD.void_reason IS NOT NEW.void_reason)
  BEGIN SELECT RAISE(ABORT, '已作廢的銷售單不可恢復或修改'); END;
CREATE TRIGGER IF NOT EXISTS trg_sale_void_payment BEFORE UPDATE ON Sale
  WHEN OLD.voided_at IS NOT NULL AND OLD.payment IS NOT NEW.payment
  BEGIN SELECT RAISE(ABORT, '已作廢的銷售單不可恢復或修改'); END;
CREATE TRIGGER IF NOT EXISTS trg_sale_void_keep_delete BEFORE DELETE ON Sale
  WHEN OLD.voided_at IS NOT NULL
  BEGIN SELECT RAISE(ABORT, '已作廢的銷售單不可恢復或修改'); END;
CREATE TRIGGER IF NOT EXISTS trg_sale_void_keep_insert BEFORE INSERT ON Sale
  WHEN EXISTS(SELECT 1 FROM Sale WHERE sale_id=NEW.sale_id AND voided_at IS NOT NULL)
  BEGIN SELECT RAISE(ABORT, '已作廢的銷售單不可恢復或修改'); END;
CREATE TRIGGER IF NOT EXISTS trg_sale_void_keep_update BEFORE UPDATE ON Sale
  WHEN NEW.sale_id IS NOT OLD.sale_id
   AND EXISTS(SELECT 1 FROM Sale WHERE sale_id=NEW.sale_id AND voided_at IS NOT NULL)
  BEGIN SELECT RAISE(ABORT, '已作廢的銷售單不可恢復或修改'); END;
-- 點帳紀錄與更正紀錄:不可修改、不可刪除,新增撞主鍵(INSERT OR REPLACE)也擋
CREATE TRIGGER IF NOT EXISTS trg_dayclose_no_replace BEFORE INSERT ON DayClose
  WHEN EXISTS(SELECT 1 FROM DayClose WHERE business_date=NEW.business_date)
  BEGIN SELECT RAISE(ABORT, '點帳紀錄不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_dayclose_no_update BEFORE UPDATE ON DayClose
  BEGIN SELECT RAISE(ABORT, '點帳紀錄不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_dayclose_no_delete BEFORE DELETE ON DayClose
  BEGIN SELECT RAISE(ABORT, '點帳紀錄不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_correction_no_replace BEFORE INSERT ON Correction
  WHEN EXISTS(SELECT 1 FROM Correction WHERE correction_id=NEW.correction_id)
  BEGIN SELECT RAISE(ABORT, '更正紀錄不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_correction_no_update BEFORE UPDATE ON Correction
  BEGIN SELECT RAISE(ABORT, '更正紀錄不可修改或刪除'); END;
CREATE TRIGGER IF NOT EXISTS trg_correction_no_delete BEFORE DELETE ON Correction
  BEGIN SELECT RAISE(ABORT, '更正紀錄不可修改或刪除'); END;
"""

import sqlite3


def statements():
    """把 SCHEMA 拆成單句,供升級時在同一交易內逐句執行(executescript 會自行提交)。

    trigger 內有分號,不能直接用分號切;以 sqlite3.complete_statement 逐行累積判斷。
    """
    out, buf = [], ""
    for line in SCHEMA.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            out.append(buf.strip())
            buf = ""
    if any(l.strip() and not l.strip().startswith("--") for l in buf.splitlines()):
        raise RuntimeError("SCHEMA 結尾有不完整的語句")
    return out


def _table_ddl(table):
    prefix = f"CREATE TABLE IF NOT EXISTS {table}("
    for stmt in statements():
        start = stmt.find(prefix)  # 語句前面可能帶著註解行;表內的註解原樣保留,與全新資料庫存下的定義一致
        if start >= 0 and all(l.strip().startswith("--") for l in stmt[:start].splitlines()):
            return stmt[start:]
    raise RuntimeError(f"SCHEMA 找不到資料表 {table}")


_SALE_V27_COLUMNS = "sale_id,ts,payment,order_discount,member_discount,total,paid,change,employee,member_id"
_MOVE_V27_COLUMNS = ("move_id,variant_id,qty,kind,receipt_item_id,sale_item_id,stocktake_item_id,"
                     "return_item_id,note,employee,ts")


def _to_v28(conn):
    """v27→v28:Sale 加帳日與作廢欄位、StockMovement 加 void 種類。出 1.0.0 時移除。

    用「原名直接重建」:資料先搬到暫存表,原表丟掉後用原名建新結構再搬回,保留主鍵與流水號。
    不用「建新表再改名」:改名前新表不算子表的父表,延後的外鍵違規消不掉;改名也會被其他表上
    引用 StockMovement 的 trigger 擋下。掛在兩張表上的 trigger 與索引隨 DROP 消失,由呼叫端
    接著逐句執行 SCHEMA 重建;DayClose、Correction 同樣由 SCHEMA 建立。
    """
    bad = [str(r[0]) for r in conn.execute("SELECT sale_id FROM Sale WHERE date(ts) IS NULL ORDER BY sale_id")]
    if bad:
        shown = "、".join(bad[:20]) + ("…" if len(bad) > 20 else "")
        raise RuntimeError(f"資料庫升級中止：{len(bad)} 張銷售單的時間無法判斷帳日（單號 {shown}）")
    conn.execute("PRAGMA defer_foreign_keys=ON")  # 外鍵檢查不關,延到提交前整體檢查
    for table, columns, extra_cols, extra_exprs in (
            ("Sale", _SALE_V27_COLUMNS, ",business_date", ",date(ts)"),
            ("StockMovement", _MOVE_V27_COLUMNS, "", "")):
        seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
        conn.execute(f"CREATE TEMP TABLE _old AS SELECT * FROM {table}")
        conn.execute(f"DROP TABLE {table}")
        conn.execute(_table_ddl(table))
        conn.execute(f"INSERT INTO {table}({columns}{extra_cols}) SELECT {columns}{extra_exprs} FROM _old")
        conn.execute("DROP TABLE _old")
        if seq is not None:
            conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
            conn.execute("INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)", (table, seq[0]))


# 版號規則:新結構第一版為 BASE_VERSION;MIGRATIONS 為之後的升級步驟,
# 第 N 筆將 DB 由 BASE_VERSION+N-1 升至 BASE_VERSION+N。callable 只用 conn.execute,
# 勿自行 commit/executescript(runner 已包在同一交易裡)。
# 舊版(v24 以前)的升級步驟已移除:低於 MIN_SUPPORTED_VERSION 的資料庫一律拒絕開啟,
# 主檔改由 tools/make_standard_db.py 唯讀轉出新母版。
# v27→v28 是上線前唯一保留的升級步驟(維護者 2026-11-10 裁示),出 1.0.0 時移除。
BASE_VERSION = 27
MIGRATIONS = [(28, _to_v28)]

# 最新版號 = 新結構第一版 + 升級步驟筆數;全新 DB 建 SCHEMA 即為此版
SCHEMA_VERSION = BASE_VERSION + len(MIGRATIONS)
# 程式能升級的最低版號;更舊的資料庫在任何寫入前拒絕
MIN_SUPPORTED_VERSION = BASE_VERSION
