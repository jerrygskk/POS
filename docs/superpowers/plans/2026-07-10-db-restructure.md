# 資料庫架構重整 實作計畫

> Spec:`docs/superpowers/specs/2026-07-09-db-restructure.md`(已核可,為唯一需求來源)。
> 執行方式:每批委派 executor,批內 TDD、批尾 commit,批間 verifier fresh-context 驗證。

**Goal:** 種類/廠牌/型號正式資料表化、種類規格欄、Excel 重匯與前端維護/建檔流程改版。

## Global Constraints

- 台灣用語;UI 文字正式、無術語(見 spec §5,為驗收標準)
- 金額/數量 int;庫存流水制不變
- 測試:`python -m unittest discover -s tests`,檔名 `test_*.py`
- 改完 `py_compile`;逐檔 git add(跳過 data/、xlsm);不 push
- commit 訊息用 Bash heredoc

---

### 批次 1:Schema + 種子

**Files:** `lib/db_schema.py`、`lib/db_seed.py`、`tests/test_schema.py`(改)、新 `tests/test_catalog_tables.py`

- 新表 Category/Brand/BrandCategory/PhoneModel/VariantModel/CategoryField(DDL 照 spec §3)
- Product:category→category_id FK、加 brand_id FK(可空)
- AttributeField 加 category_id(NULL=共用欄),UNIQUE(category_id,name);AttributeOption 移除 parent_*,UNIQUE(field_id,value)
- 種子改:共用欄「商品描述」「顏色」,不再種八屬性欄
- 既有測試改綠;`init_db` 對舊 DB 不需相容(重建)

### 批次 2:API

**Files:** 新 `api/catalog.py`(categories/brands/models);改 `api/attributes.py`、`api/products.py`、`api/__init__.py`;tests 新 `test_catalog_api.py`、改 `test_attributes.py`、`test_products.py`

- Endpoint 清單與 guard 規則照 spec §4(409 掛商品硬刪、422 停用不可售不變、停用不入建檔下拉)
- `GET /api/categories/{id}/fields` 回專屬欄+已啟用共用欄含選項
- `PUT /api/variants/{id}/models` 整組替換
- products 建立/查詢走 FK,支援 category_id/brand_id/model_id 篩選,回傳含名稱

### 批次 3:匯入重寫 + 重匯

**Files:** `tools/import_excel.py` 重寫;新 `tests/test_import_rules.py`

- 規則照 spec §6:廠牌正規化對照表、共用型號字串拆解、分類1 分流、分類2/人名欄不讀、可重跑、警告清單
- 純函式(正規化/拆解/分流)獨立可單測
- 執行:刪 `data/pos.db` → 重匯 → 統計與警告貼回報告

### 批次 4:前端

**Files:** `static/js/*.js`、`static/index.html`、`static/css/pos.css`

- 設定頁三卡片維護(種類含規格欄與共用欄勾選、廠牌含掛種類、型號),UI 條款照 spec §5
- 進貨快速建檔:種類→型號(複選+搜尋)→廠牌(過濾)→規格欄,上層未選下層鎖定
- 商品資料庫瀏覽:三篩選下拉+關鍵字
- preview 工具實跑驗證(console 無錯、逐流程操作)

### 批次 5:收尾

- verifier 全案驗證(50+ 測試全綠、重匯資料抽查、前端流程)
- DEVELOPER.md 更新(schema/慣例章節)
- 重打包 dist/POS.exe、啟動驗證
