# 正規化重整 spec(2026-07-10 定案)

目標:治本。規格值正規化、手機品牌建表、schema 版本機制、資料庫層約束。
既有資料**不遷移**(全為測試資料,清掉重建);正式資料日後由匯入工具從 Excel 重灌。

## 1. Schema 變更

### 新表

```sql
PhoneBrand(
  phone_brand_id INTEGER PK AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,          -- iPhone / SAMSUNG / 其他
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
)

VariantAttribute(                      -- 取代 Variant.attributes JSON
  variant_id INTEGER NOT NULL FK → Variant,
  field_id   INTEGER NOT NULL FK → AttributeField,
  option_id  INTEGER FK → AttributeOption,  -- select 欄用
  text_value TEXT,                            -- text 欄用
  PRIMARY KEY(variant_id, field_id)
  -- 約束:option_id 與 text_value 恰一非 NULL(CHECK)
)

OptionModel(                           -- 選項限定型號(特別色)
  option_id INTEGER NOT NULL FK → AttributeOption,
  model_id  INTEGER NOT NULL FK → PhoneModel,
  PRIMARY KEY(option_id, model_id)
)
```

### 改表

- `PhoneModel.brand TEXT` → `phone_brand_id INTEGER NOT NULL FK → PhoneBrand`,UNIQUE(phone_brand_id, name)。
- `Variant.attributes` 欄位**移除**;變體規格一律由 VariantAttribute JOIN 組回。

### 機制

- `Setting` 存 `schema_version`;`lib/db.py` 的 `init_db` 改為 migration runner:
  依序執行「第 N→N+1 版」遷移清單,開檔自動升級,全新 DB 直接建最新版。
- `get_conn` 開 `PRAGMA foreign_keys=ON`。

## 2. OptionModel 語意(乙案)

- 選項未綁任何型號=通用,所有建檔都可選。
- 綁了型號=只在建檔已選該型號時出現於下拉;共用款掛多型號取聯集。
- **只過濾建檔下拉,不擋既有資料**:改綁定不回溯動既有變體。
- 設定頁選項維護加「限定型號」勾選(依品牌分組)。

## 3. 規格欄配置

- 各種類建專屬欄「規格」(select);Excel 分類1/分類2 有值的種類建同名專屬欄。
- 「商品描述」「顏色」維持共用欄(CategoryField 勾選啟用)。

## 4. 匯入工具(tools/import_excel.py 重寫)

資料源:`docs/產品清單.xlsm` 的「商品資料庫」工作表(667 筆,5 種類)。

| Excel 欄 | 進 DB |
|---|---|
| 商品編碼 | Barcode(factory;判重,可重跑) |
| 商品種類 | Category |
| 廠牌 | Brand + BrandCategory |
| 手機品牌 | PhoneBrand |
| 手機型號 | PhoneModel(共用字串拆解,如「iPhone17/17pro/16pro共用」→3 筆)→ VariantModel |
| 規格/商品描述/分類1/分類2 | AttributeField(依 §3)→ AttributeOption(自動補建)→ VariantAttribute |
| 備註 | Product.note |
| 庫存/登陸人/進貨人/日期 | **不匯**(庫存暫緩;人名為個資) |
| 價格 | Excel 無 → NULL,日後維護頁補 |

- 參數 `--category 鋼化玻璃`:只匯指定種類(驗收用);不帶參數全匯。
- 純函式可單測;匯後印對帳數字(款/變體/條碼/選項數)。

## 5. 驗收

1. 五批次完成、測試全綠、verifier CONFIRMED。
2. 匯入「鋼化玻璃」243 筆,維護者實際走建檔/收銀/庫存流程驗收。
3. 驗收通過後才全匯與做報表功能(報表 spec 另立)。

## 6. 後續(本輪不做)

- 報表:銷量/庫存統計,篩選 種類/廠牌/型號/規格/日期,CSV 匯出(需求已確認,排正規化後)。
- 庫存期初匯入(正式上線那天再議)。
