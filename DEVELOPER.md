# DEVELOPER.md

給後續維護者的技術文件。

## 1. 架構

```
main.py → uvicorn(127.0.0.1:8737) → FastAPI app(api/create_app) → static/(Vue 無建置前端)
                                              │
                                          lib/db.py(SQLite)
```

啟動流程(`main.py`):確保 `data/` 目錄 → `lib.db.init_db` 建/補 schema →
`lib.backup.run_auto_backup` 跑一次 GFS 備份 → 起 uvicorn → `webbrowser.open` 開瀏覽器。

### 檔案結構

| 路徑 | 說明 |
|---|---|
| `main.py` | 進入點:備份→起 uvicorn→開瀏覽器 |
| `lib/version.py` | `VERSION` 字串 |
| `lib/db.py` | `get_conn` / `init_db`(唯一 DB 連線入口) |
| `lib/db_schema.py` | 全部 DDL(唯一來源) |
| `lib/db_seed.py` | 預設八屬性欄位、付款方式種子 |
| `lib/backup.py` | GFS 備份(日7/週4/月12) |
| `api/__init__.py` | `create_app()`:掛 router、掛 static(含 `_static_dir()` 打包路徑) |
| `api/attributes.py` | 屬性欄位/選單庫(含連動) |
| `api/catalog.py` | 種類/廠牌/型號維護 |
| `api/products.py` | 款/變體/條碼 CRUD、條碼查詢、店內條碼產生 |
| `api/stock.py` | 進貨、庫存查詢 |
| `api/sales.py` | 結帳、銷售紀錄、日結、CSV |
| `api/stocktake.py` | 盤點單 |
| `api/printing.py` | 條碼列印服務介面(stub) |
| `static/` | `index.html` + `css/pos.css` + `js/*.js`(Vue 3、fetch 包裝、各頁邏輯) |
| `tools/build.ps1` | PyInstaller 打包腳本 |
| `tools/import_excel.py` | 一次性匯入舊 Excel 產品清單 |
| `tests/` | 單元測試 |

## 2. 慣例

- **庫存採異動流水制**:不存「目前庫存」欄位,一律由 `StockMovement` 加總取得(`api/products.py:stock_of`)。`kind` 為 `purchase`(進貨,+)、`sale`(銷售,-)、`adjust`(盤點調整,±)。
- **金額一律 int**:新台幣元,無小數;數量亦為 int。
- **商品結構**:`Category`/`Brand`/`PhoneModel` 為正式資料表;`Product`(款)以 `category_id`/`brand_id` FK 掛種類/廠牌;`Variant`(變體)以 `VariantModel` 多對多掛適用型號(共用款可掛多筆型號);規格欄 `AttributeField` 依 `category_id` 掛種類(NULL 為共用欄,各種類需經 `CategoryField` 勾選啟用才可用);`AttributeOption` 無 parent 連動。
- **規格值正規化**:變體規格不再存 JSON,改由 `VariantAttribute(variant_id, field_id, option_id?, text_value?)` 關聯表承載(`CHECK` 約束 `option_id`/`text_value` 恰一非 NULL:select 欄存 `option_id`、text 欄存 `text_value`)。API 對外仍以 `attributes:{欄名:值}` dict 進出,讀寫由 `api/products.py` 的 `set_variant_attributes`/`attrs_by_variant` 在 dict 與關聯列間轉換(讀取一次 JOIN 撈齊避免 N+1)。因此**改欄名/改選項值即生效**,不需回掃變體;有 `VariantAttribute` 參照的選項硬刪回 409。寫入時 select 值查無對應選項回 422。
- **選項限定型號**:`OptionModel(option_id, model_id)` 記錄選項只在特定型號出現(特別色)。`GET /options?field_id=&model_ids=` 過濾回「未綁任何型號的 ∪ 綁定含任一給定型號的」聯集,僅過濾建檔下拉,不回溯既有變體;未帶 `model_ids` 回全部。`PUT /options/{id}/models` 全量替換該選項的限定型號清單(空清單=改回通用)。
- **條碼混合**:`source` 分 `factory`(廠商既有)與 `store`(店內自產,`SP` + 8 位流水,`Setting.next_store_barcode` 計數)。
- **有效售價**:`Variant.price` 不為 NULL 時採用,否則退回 `Product.default_price`,兩者皆 NULL 則售價為 `null`。
- **共用欄 NULL 去重提醒**:`AttributeField` 的共用欄 `category_id` 為 NULL;SQLite 的 `UNIQUE` 對 NULL 不視為相等,故去重不能單靠資料庫唯一鍵,需靠應用層先查再插。

## 3. 測試

```powershell
python -m unittest discover -s tests
```

目前 128 個測試,涵蓋 schema/migration、屬性/選單庫、規格值正規化(VariantAttribute)、選項限定型號(OptionModel)、商品/變體/條碼、進貨庫存、結帳/銷售紀錄、盤點、備份等模組,檔名皆 `test_*.py`。

## 4. 打包

```powershell
powershell -ExecutionPolicy Bypass -File tools/build.ps1
```

`tools/build.ps1` 內容:清除舊 `build/`、`dist/`、`POS.spec` → `pyinstaller --onefile --name POS --add-data "static;static"` 並加 uvicorn 相關 hidden-import(`uvicorn.logging`、`uvicorn.loops.auto`、`uvicorn.protocols.http.auto`、`uvicorn.lifespan.on`)→ 指定 `main.py`。

產出 `dist/POS.exe`,雙擊即可執行,會在 exe 所在目錄自動建立 `data/`(含 `pos.db`、`backups/`)。

**`sys._MEIPASS` 雷**:PyInstaller onefile 模式執行時會把打包內容解壓至暫存目錄 `sys._MEIPASS`,原本用 `__file__` 推算的 `static/` 路徑在打包後會失效(該目錄不存在於暫存路徑下)。因此 `api/__init__.py` 新增 `_static_dir()`:`getattr(sys, "frozen", False)` 為真(即在 PyInstaller 環境執行)時改回傳 `os.path.join(sys._MEIPASS, "static")`,開發環境不受影響。

## 5. 版本記錄

| 版本 | 說明 |
|---|---|
| 0.1.0 | 首版:收銀/進貨/盤點/銷售紀錄,PyInstaller 單一 exe 打包 |
