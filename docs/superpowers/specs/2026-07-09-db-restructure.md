# 資料庫架構重整 Spec(種類/廠牌/型號正式化 + 種類規格欄)

2026-07-09。維護者已核可方向,本文件為實作前最終確認版。

## 1. 背景與問題

初版把種類、廠牌、型號全部塞在 `AttributeOption` 字串選單,靠 `parent_value` 字串連動,造成:

1. 廠牌被灌成「廠牌+種類」複合字串(`DAPAD手機殼`、`COZY充電線`),同廠牌拆成多筆,跨配件的廠牌無法統一維護。
2. 型號含複合共用字串(`iPhone14/13/13pro(6.1)共用`),查單一型號查不到共用商品。
3. 字串連動改名即斷鏈,且無獨立維護入口,資料庫瀏覽/建檔編排不直覺。

## 2. 已定案的設計決策

| 決策 | 內容 |
|---|---|
| 種類/廠牌/型號正式資料表化 | `Category`、`Brand`、`PhoneModel` 各自獨立維護 |
| 廠牌跨種類 | `BrandCategory` 多對多;建檔時依種類過濾廠牌下拉 |
| 型號掛變體層 | `VariantModel` 多對多;共用款一變體掛多型號 |
| 手機品牌 | 不另建表,`PhoneModel.brand` 一個欄位(iPhone/SAMSUNG…) |
| 規格欄跟著種類走 | `AttributeField.category_id`;每種類自訂欄位+選項 |
| 共用欄 | `category_id` NULL(顏色、商品描述);`CategoryField` 讓各種類勾選啟用 |
| 規格不做廠牌層過濾 | YAGNI,同種類選項全列;太雜再說 |
| 分類1 | 內容依種類分流進新規格欄(版型/顏色等) |
| 分類2 | 淘汰,不匯入 |
| 既有資料 | 砍掉重匯(DB 無真實交易);重寫 `tools/import_excel.py` |
| 建檔流程順序 | 種類 → 型號(可複選)→ 廠牌(依種類過濾)→ 該種類規格欄 |

## 3. Schema 變更(`lib/db_schema.py`)

新表:

```sql
CREATE TABLE Category(
  category_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE Brand(
  brand_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE BrandCategory(
  brand_id INTEGER NOT NULL REFERENCES Brand(brand_id),
  category_id INTEGER NOT NULL REFERENCES Category(category_id),
  PRIMARY KEY(brand_id, category_id)
);
CREATE TABLE PhoneModel(
  model_id INTEGER PRIMARY KEY AUTOINCREMENT,
  brand TEXT NOT NULL,              -- iPhone / SAMSUNG …
  name TEXT NOT NULL,
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(brand, name)
);
CREATE TABLE VariantModel(
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  model_id INTEGER NOT NULL REFERENCES PhoneModel(model_id),
  PRIMARY KEY(variant_id, model_id)
);
CREATE TABLE CategoryField(       -- 種類啟用哪些共用欄
  category_id INTEGER NOT NULL REFERENCES Category(category_id),
  field_id INTEGER NOT NULL REFERENCES AttributeField(field_id),
  PRIMARY KEY(category_id, field_id)
);
```

改表:

- `Product`:`category TEXT` → `category_id INTEGER REFERENCES Category`;新增 `brand_id INTEGER REFERENCES Brand`(可空,雜項品可無廠牌)。
- `AttributeField`:新增 `category_id INTEGER REFERENCES Category`(NULL=共用欄);`name` 的 UNIQUE 改為 `UNIQUE(category_id, name)`。
- `AttributeOption`:移除 `parent_field_id`、`parent_value`(字串連動退場);UNIQUE 改 `(field_id, value)`,NULL 去重問題隨之消失。
- `Variant.attributes` JSON 保留,鍵=欄位名(僅規格欄,不再含種類/廠牌/型號)。

種子(`lib/db_seed.py`):預設共用欄「商品描述」(自由文字)、「顏色」(選單);不再種八屬性欄。

## 4. API 變更

新增:

- `GET/POST/PATCH/DELETE /api/categories`(含停用;有商品掛著者硬刪回 409)
- `GET/POST/PATCH/DELETE /api/brands` + `PUT /api/brands/{id}/categories`(掛種類)
- `GET/POST/PATCH/DELETE /api/models`(query 依 brand 過濾)
- `PUT /api/variants/{id}/models`(變體掛型號,整組替換)
- `GET /api/categories/{id}/fields`:回該種類專屬欄+已啟用共用欄(含選項),建檔畫面一次拿齊

修改:

- `POST /api/products`、變體建立:收 `category_id`、`brand_id`、`model_ids[]`
- 商品查詢/資料庫瀏覽:回關聯名稱,支援 `category_id`/`brand_id`/`model_id` 篩選
- 停用 guard 延伸:停用的種類/廠牌/型號不出現在建檔下拉(既有商品照常顯示與銷售)
- `api/attributes.py` 選單連動邏輯移除,改依 `category_id` 給欄位+選項

## 5. 前端(UI 簡化為驗收標準)

**使用者無電腦背景,畫面從簡是硬性要求:**

- 維護入口集中在「設定」頁,分三個卡片:配件種類、廠牌、手機型號。**不新增分頁**。
- 每個維護畫面 = 一個清單 + 一個「新增」按鈕;列上只有改名、排序(上下移)、停用、刪除。無巢狀對話框。
- 種類卡片點入後,同畫面下方管理該種類的規格欄與選項(以及共用欄勾選),不跳頁。
- 建檔(進貨頁快速建檔)是一條由上而下的固定順序表單:種類 → 型號(可複選,checkbox 清單附搜尋框)→ 廠牌 → 規格欄。上層未選時下層反白不可點,選了才展開,不會一次丟八個下拉。
- 所有下拉維持「選單 + 手打自增」(打了不存在的值,存檔時自動入庫)。
- 商品資料庫瀏覽頁:頂部三個篩選下拉(種類/廠牌/型號)+ 關鍵字,其餘沿用現有款展開變體版面。
- 文字正式、無術語(「配件種類」不叫 Category;錯誤訊息說人話)。

收銀、盤點、銷售紀錄頁不動(只讀變體/條碼)。

## 6. 匯入重寫(`tools/import_excel.py`)

砍掉 `data/pos.db` 重建重匯。規則:

1. **廠牌正規化**:複合字串去掉種類尾綴還原純廠牌(`DAPAD手機殼`→`DAPAD`、`COZY充電線`→`COZY`);建對照表寫在腳本內,匯入時自動建 `BrandCategory`。
2. **型號拆解**:`iPhone14/13/13pro(6.1)共用` 類字串以規則拆成多個標準型號名,一變體掛多筆 `VariantModel`;拆不動的列警告輸出,人工補。
3. **分類1 分流**:依所屬種類寫入對應規格欄(玻璃貼→「版型」、殼/線→「顏色」等);對照表寫在腳本內,未涵蓋值警告輸出。
4. 分類2、含糊欄、人名欄(建檔人等)一律不讀。
5. 可重跑(條碼判重)。匯入結束印統計 + 警告清單。

## 7. 測試(`tests/`)

- 新表 CRUD 與 409/422 guard(停用種類不可建檔、掛商品的種類不可硬刪)
- `GET /api/categories/{id}/fields` 專屬欄+共用欄合併正確
- 變體掛多型號後,以任一型號篩選可查得
- 匯入單元測試:廠牌正規化、型號拆解、分類1 分流(以樣本字串驗證)
- 既有 50 測試改到全綠(屬性連動相關測試改寫或移除)

## 8. 不做(YAGNI)

- 規格欄的廠牌層過濾
- 手機品牌獨立資料表
- 舊 DB migration(直接重匯)
- 多機、會員、價格歷史等(沿用原清單)
