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
| `lib/db.py` | `get_conn` / `db_conn`(context manager)/ `init_db`,純資料層(零框架依賴) |
| `lib/db_schema.py` | 全部 DDL(唯一來源) |
| `lib/db_seed.py` | 共用欄(商品描述/顏色)、付款方式種子 |
| `lib/dbutil.py` | 會 raise HTTPException 的 DB helper(`require_exists`/`reject_if_referenced` 等) |
| `lib/product_rules.py` | 共用商品規則(`FIELD_TYPES`、欄位型別驗證、自取碼取號) |
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
| `tools/bump_version.py` | 進版工具(改 `version.py` + 產 `version_info.txt`) |
| `tools/import_excel.py` | 一次性匯入舊 Excel 產品清單(**不入庫**,僅本地) |
| `tests/` | 單元測試(`tests/base.py` 共用基底 `ApiTestCase`/`ConnTestCase` 與 fixture helper) |

## 2. 慣例

- **庫存採異動流水制**:不存「目前庫存」欄位,一律由 `StockMovement` 加總取得(`api/products.py:stock_of`)。`kind` 為 `purchase`(進貨,+)、`sale`(銷售,-)、`adjust`(盤點調整,±)。
- **金額一律 int**:新台幣元,無小數;數量亦為 int。
- **商品結構**:`Category`/`Brand`/`PhoneModel` 為正式資料表;`Product`(款)以 `category_id`/`brand_id` FK 掛種類/廠牌;`Variant`(變體)以 `VariantModel` 多對多掛適用型號(共用款可掛多筆型號);規格欄 `AttributeField` 依 `category_id` 掛種類(NULL 為共用欄,各種類需經 `CategoryField` 勾選啟用才可用);`AttributeOption` 無 parent 連動。
- **規格值正規化**:變體規格不再存 JSON,改由 `VariantAttribute(variant_id, field_id, option_id?, text_value?)` 關聯表承載(`CHECK` 約束 `option_id`/`text_value` 恰一非 NULL:select 欄存 `option_id`、text 欄存 `text_value`)。API 對外仍以 `attributes:{欄名:值}` dict 進出,讀寫由 `api/products.py` 的 `set_variant_attributes`/`attrs_by_variant` 在 dict 與關聯列間轉換(讀取一次 JOIN 撈齊避免 N+1)。因此**改欄名/改選項值即生效**,不需回掃變體;寫入時 select 值查無對應選項回 422。
- **規格選項生命週期**:`AttributeOption.active` 只控制新增選單可見性。有 `VariantAttribute` 引用時刪除會清除預設選項與限定型號後設為停用,保留既有商品關聯;0 使用中才硬刪除。設定頁重新加入同欄位、同值的停用選項時會恢復原 `option_id` 並重新啟用;商品建檔流程自動補選項時不會重新啟用。`GET /options` 的 `usage_count` 為引用該 `option_id` 的 distinct `variant_id` 數量。
- **選項限定型號**:`OptionModel(option_id, model_id)` 記錄選項只在特定型號出現(特別色)。`GET /options?field_id=&model_ids=` 過濾回「未綁任何型號的 ∪ 綁定含任一給定型號的」聯集,僅過濾建檔下拉,不回溯既有變體;未帶 `model_ids` 回全部。`PUT /options/{id}/models` 全量替換該選項的限定型號清單(空清單=改回通用)。
- **條碼混合**:`source` 分 `factory`(廠商既有)與 `store`(店內自取碼,`TL` + 流水號,`Setting.next_store_barcode` 純計數、刪除不回收);手動輸入 `TL` 開頭一律 422(系統保留字頭)。
- **自取碼交易語意**:`lib/product_rules.py:next_store_barcode` 使用呼叫端的同一條資料庫連線更新計數器,由呼叫端決定 commit;商品或條碼建立失敗造成 transaction rollback 時,計數器亦一併回復。
- **關鍵輸入驗證**:進貨數量與盤點掃描數量須大於 0,盤點實數不得小於 0;結帳單品折扣不可超過品項小計、總額不得為負,付款方式須存在設定清單。規格欄型別統一由 `lib/product_rules.py` 驗證。
- **盤點結案防重**:結案先以 `status='open'` 條件原子更新盤點單;不存在回 404,已結案回 409,避免重複產生 `adjust` 庫存異動。
- **有效售價**:`Variant.price` 不為 NULL 時採用,否則退回 `Product.default_price`,兩者皆 NULL 則售價為 `null`。
- **共用欄 NULL 去重提醒**:`AttributeField` 的共用欄 `category_id` 為 NULL;SQLite 的 `UNIQUE` 對 NULL 不視為相等,故去重不能單靠資料庫唯一鍵,需靠應用層先查再插。

### UI 風格規範(源自維護者 theme_guide,Apple HIG 風;定義於 `static/css/pos.css` 檔頭)

- **色票**:背景 `#f2f2f7`|元件底 `#fff`|主文字 `#1c1c1e`|次要 `#636366`|佔位/停用字 `#aeaeb2`|邊框 `#c6c6c8`|hover 底/停用底 `#e5e5ea`|pressed 底/停用框 `#d1d1d6`|強調(焦點/選中/chip.on) `#8fa8c8`|主要鈕 `#a1b4cb`/hover `#4977b1`/pressed `#39649a`|危險 `#e74c3c`/hover `#c0392b`。換主色時全域搜尋一起換。
- **焦點一律 2px 藍灰框**:input/select 用 `border-color + inset box-shadow` 疊出 2px(不位移版面),button 用 `:focus-visible` outline;不用瀏覽器預設藍。
- **停用態統一**:底 `#e5e5ea`、字 `#aeaeb2`、框 `#d1d1d6`(primary 停用 `#d1d9e3`)。
- **圓角 8px**(chip/tag 圓膠囊除外);一般鈕 `min-width: 80px` 保持等寬,小型鈕(`.btn-sm`/chip/表格操作鈕)歸零。
- **表單對齊**:「標籤(固定寬 `--label-w`,預設 9em)＋輸入框」兩欄 grid,同表單內全部欄位對齊同一垂直線,列距 10px;label 內文字+輸入框靠 grid 匿名項對齊,html 不需加 span。巢狀框(如 `.spec-box`)在框內覆寫 `--label-w` 扣掉 padding+border,維持框內外同線。新表單一律照此規則。

## 3. 測試

```powershell
python -m unittest discover -s tests
```

目前 242 個測試,涵蓋 schema/migration、屬性/選單庫、規格值正規化(VariantAttribute)、選項限定型號(OptionModel)、商品/變體/條碼、進貨庫存、結帳/銷售紀錄、盤點、備份、匯入規則(七類拆解/透明填補/括號補齊)等模組,檔名皆 `test_*.py`。

### 匯入工具(`tools/import_excel.py`)規則補充

> ⚠️ 此檔為一次性工具,**不入庫**(已 gitignore,僅存在本地);相依它的 `tests/test_import_excel.py`、`tests/test_import_rules.py` 於正式匯入驗收後一併移除。fresh clone 無此檔時該兩支測試會失敗,屬預期。

- **鋼化玻璃複選欄名為「材質」**(常數 `GLASS_SPEC_FIELD`;非「規格」)。
- **手機殼款式/顏色兩欄皆空**(空壓殼等透明殼)→ 款式自動填「透明」,避免無規格。
- **規格類欄位補齊未閉合括號**(`close_unbalanced_parens`,套在 `select_attrs`):來源缺右括號(如「磁吸(附掛環扣」)自動補成「…環扣)」,全/半形皆處理。
- 工具可重跑;現有 `data/pos.db` 上線前會依最新規則重匯。

## 4. 打包

```powershell
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
Remove-Item -Force POS.spec -ErrorAction SilentlyContinue
pyinstaller --clean --onefile --name POS --add-data "static;static" --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.lifespan.on --version-file version_info.txt main.py
```

上列為標準打包指令:先清除舊 `build/`、`dist/`、`POS.spec`,再以 `--clean --onefile` 打包 static、版本資訊與 uvicorn hidden-import。`tools/build.ps1` 可供核對參數,實際打包不要執行該腳本。

產出 `dist/POS.exe`,雙擊即可執行,會在 exe 所在目錄自動建立 `data/`(含 `pos.db`、`backups/`)。

**`sys._MEIPASS` 雷**:PyInstaller onefile 模式執行時會把打包內容解壓至暫存目錄 `sys._MEIPASS`,原本用 `__file__` 推算的 `static/` 路徑在打包後會失效(該目錄不存在於暫存路徑下)。因此 `api/__init__.py` 新增 `_static_dir()`:`getattr(sys, "frozen", False)` 為真(即在 PyInstaller 環境執行)時改回傳 `os.path.join(sys._MEIPASS, "static")`,開發環境不受影響。

## 5. 版號控制

- 版號單一來源 `lib/version.py`(`__version__`),顯示版本一律 `from lib.version import __version__`,不寫死第二份。
- 進版一律跑 `python tools/bump_version.py {新版號}`,不手改 `version.py`(否則 `version_info.txt` 脫鉤)。
- `version_info.txt` 由工具自動產生(PyInstaller `--version-file` 用),勿手改;**不入庫**(已 gitignore),fresh clone 需先跑一次 `bump_version.py` 產出才能 build。
- 版號三碼 主.次.修,日常進第三碼;接受 1~4 碼,`version_info.txt` 自動補 0。
- tag 順序鐵則:文件/release note 先寫好 → 進版 commit → `git tag v{版號}` → push tag;tag 已 push 要移動:本地 `git tag -f` 後,遠端先刪(`git push origin :refs/tags/v{版號}`)再推。

## 6. 版本記錄

| 版本 | 說明 |
|---|---|
| 0.1.0 | 首版:收銀/進貨/盤點/銷售紀錄,PyInstaller 單一 exe 打包 |
