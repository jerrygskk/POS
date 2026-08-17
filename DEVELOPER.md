# DEVELOPER.md

給後續維護者的技術文件。

## 1. 架構

```
main.py → RuntimePaths.detect() → init_db(pos.db, require_existing=True) → 自動備份
     → DesktopApplication → pywebview（本機 static/index.html）
     → DesktopBridge → Facade → Service → Repository → SQLite
```

啟動流程（`main.py`）：以 `RuntimePaths.detect()` 決定路徑 →
`lib.db.init_db(..., require_existing=True)` 驗證／升級既有 `pos.db` →
`lib.backup.run_auto_backup` 跑一次 GFS 備份 → `DesktopApplication` 建立單一 pywebview 視窗，
載入本機 `static/index.html` → 前端透過 `DesktopBridge` 呼叫 Facade／Service／Repository。正式 runtime 不啟動 HTTP server。

開發環境的 `pos.db` 位於 `main.py` 同層；PyInstaller onefile 環境則位於 `POS.exe` 同層。
正式啟動要求該資料庫已存在，不會自動建立全新空白 DB。開發環境的 `static/` 位於專案根目錄，
打包後則由 `RuntimePaths` 指向 `sys._MEIPASS/static`。程式不提供 HTTP adapter；測試與正式 runtime 都透過 Desktop Bridge 與 Facade。

### 檔案結構

| 路徑 | 說明 |
|---|---|
| `main.py` | Desktop-only 進入點：偵測路徑→驗證／升級既有 DB→備份→啟動桌面視窗 |
| `lib/runtime_paths.py` | 集中決定開發／onefile 的 DB、備份、錯誤記錄與 static 路徑 |
| `lib/desktop_application.py` | 建立 pywebview 視窗，串接 `DesktopBridge` 與各 Facade |
| `lib/desktop_bridge.py` | pywebview JS bridge：轉送 action、統一成功／錯誤 envelope、處理匯出存檔 |
| `lib/child_window.py` | 子視窗協調器：唯一子視窗、頁面白名單、傳入脈絡、關閉時通知主視窗解鎖 |
| `lib/version.py` | `VERSION` 字串 |
| `lib/db.py` | `get_conn` / `db_conn`(context manager)/ `init_db`,純資料層(零框架依賴) |
| `lib/db_schema.py` | 現行 schema DDL 唯一來源；未來變更仍依 migration 規則升級既有資料庫 |
| `lib/legacy_migrations.py` | 凍結的 v1–v13 migration DDL；僅供既有資料庫按版本升級，不作為現行 schema 定義 |
| `lib/db_seed.py` | 共用欄(商品描述/顏色)、付款方式種子 |
| `lib/product_rules.py` | 共用商品規則(`FIELD_TYPES`、欄位型別驗證、自取碼取號) |
| `lib/backup.py` | GFS 備份(日7/週4/月12) |
| `lib/label_printer.py` | NIIMBOT B1 協定與序列埠溝通(自動尋埠、送圖、錯誤轉譯) |
| `lib/label_layout.py` | 商品標籤版面繪製(純函式,輸入四個字串輸出 Pillow 影像) |
| `lib/*_service.py` | 正式 Facade／Service／Repository 應用層與資料存取實作 |
| `static/` | `index.html` + `css/pos.css` + `js/*.js`（Vue 3、DesktopBridge 包裝、各頁邏輯） |
| `static/variant_editor.html` | 款式修改子視窗頁面（獨立 Vue app，共用 `attrfields`／`modelpicker`／`optpicker` 元件） |
| `static/variant_batch.html` | 新增款式子視窗頁面（`variant_batch_window.js` 外殼＋既有建檔頁元件與樣板） |
| `static/js/pos_shared.js` | 主視窗與子視窗共用的全域 mixin（`guard`／`guardReload`／`attrText`） |
| `static/css/dialog-theme.css` | 子視窗對話框外觀（`.dialog-*` 公版，沿用 §2 UI 風格色票） |
| `tools/bump_version.py` | 進版工具(改 `version.py` + 產 `version_info.txt`) |
| `tests/` | 單元測試（`tests/base.py` 共用 `ConnTestCase`／`FacadeTestCase` 與 fixture helper） |

## 2. 慣例

- **庫存採異動流水制**:不存「目前庫存」欄位,一律由 `StockMovement` 加總取得（`lib/product_data.py:stock_of`）。`kind` 為 `purchase`(進貨,+)、`sale`(銷售,-)、`adjust`(盤點調整,±)。
- **金額一律 int**:新台幣元,無小數;數量亦為 int。
- **商品結構**:`Category`/`Brand`/`PhoneModel` 為正式資料表;`Product`(款)以 `category_id`/`brand_id` FK 掛種類/廠牌;`Variant`(變體)以 `VariantModel` 多對多掛適用型號(共用款可掛多筆型號);規格欄 `AttributeField` 依 `category_id` 掛種類(NULL 為共用欄,各種類需經 `CategoryField` 勾選啟用才可用);`AttributeOption` 無 parent 連動。
- **規格值正規化**:變體規格不再存 JSON,改由 `VariantAttribute(variant_id, field_id, option_id?, text_value?)` 關聯表承載(`CHECK` 約束 `option_id`/`text_value` 恰一非 NULL:select 欄存 `option_id`、text 欄存 `text_value`)。Desktop action 以 `attributes:{欄名:值}` dict 進出，讀寫由 `lib/product_data.py` 的 `set_variant_attributes`/`attrs_by_variant` 在 dict 與關聯列間轉換（讀取一次 JOIN 撈齊避免 N+1）。因此**改欄名/改選項值即生效**,不需回掃變體;寫入時 select 值查無對應選項會回傳 validation error。
- **規格選項生命週期**:`AttributeOption.active` 只控制新增選單可見性。有 `VariantAttribute` 引用時刪除會清除預設選項與限定型號後設為停用,保留既有商品關聯;0 使用中才硬刪除。設定頁重新加入同欄位、同值的停用選項時會恢復原 `option_id` 並重新啟用;商品建檔流程自動補選項時不會重新啟用。`options.list` 的 `usage_count` 為引用該 `option_id` 的 distinct `variant_id` 數量。
- **選項限定型號**:`OptionModel(option_id, model_id)` 記錄選項只在特定型號出現(特別色)。`options.list` 搭配 `field_id`／`model_ids` 過濾時回傳「未綁任何型號的 ∪ 綁定含任一給定型號的」聯集,僅過濾建檔下拉,不回溯既有變體;未提供 `model_ids` 時回全部。`options.set_models` 以 `id`／`model_ids` 全量替換限定型號清單(空清單=改回通用)。
- **條碼混合**:`source` 分 `factory`(廠商既有)與 `store`(店內自取碼,`TL` + 流水號,`Setting.next_store_barcode` 純計數、刪除不回收);手動輸入 `TL` 開頭一律 422(系統保留字頭)。
- **自取碼交易語意**:`lib/product_rules.py:next_store_barcode` 使用呼叫端的同一條資料庫連線更新計數器,由呼叫端決定 commit;商品或條碼建立失敗造成 transaction rollback 時,計數器亦一併回復。
- **關鍵輸入驗證**:進貨數量與盤點掃描數量須大於 0,盤點實數不得小於 0;結帳單品折扣不可超過品項小計、總額不得為負,付款方式須存在設定清單。規格欄型別統一由 `lib/product_rules.py` 驗證。
- **盤點結案防重**:結案先以 `status='open'` 條件原子更新盤點單;不存在回 404,已結案回 409,避免重複產生 `adjust` 庫存異動。
- **有效售價**:`Variant.price` 不為 NULL 時採用,否則退回 `Product.default_price`,兩者皆 NULL 則售價為 `null`。
- **共用欄 NULL 去重提醒**:`AttributeField` 的共用欄 `category_id` 為 NULL;SQLite 的 `UNIQUE` 對 NULL 不視為相等,故去重不能單靠資料庫唯一鍵,需靠應用層先查再插。
- **商品資料庫搜尋**:關鍵字以空白切成多個詞,採 **AND**(每個詞都要命中才算符合),大小寫與全半形以 `casefold` 正規化。比對範圍為商品名稱、種類、廠牌、款式的規格值(含 multi/tags 的每個值)、適用型號與條碼,命中時只保留符合的款式列。查無啟用中資料時另查一次停用資料,回報筆數並提供「顯示已停用」入口,不直接把停用商品混進結果。條碼只針對當頁款式查詢(`variant_id IN (...)`),不整表撈。
- **Schema 與 migration**:`lib/db_schema.py` 是現行 schema DDL 的唯一來源；`lib/legacy_migrations.py` 封存 v1–v13 的歷史 migration DDL。未來修改現行 schema 時，仍須依 migration 規則新增升級步驟，讓既有資料庫可安全演進。

### 子視窗(pywebview 款式修改／新增款式)

商品資料庫頁的「編輯」與「新增款式」都不開網頁對話框,而是由
`ChildWindowCoordinator` 建立**唯一一個** pywebview 子視窗。理由:規格、型號、
售價、條碼這些內容在單一網頁對話框裡會逼出巢狀彈窗與捲動層層相疊,違反 UI 從簡;
子視窗可獨立調整大小、由作業系統管理焦點。

**頁面白名單**:`CHILD_PAGES` 決定可開啟的頁面(`variant_editor`／`variant_batch`)
與各自標題、尺寸。前端只送 key,**不送檔名**,避免任意本機檔被載入。

**唯一性與脈絡**:協調器以 `RLock` 保護狀態,已開啟時再按只 `restore()` 既有視窗,
不會開第二個(編輯與新增也因此不會同時開)。子視窗自己不帶查詢參數,改由
`desktop.child_window.context` 向協調器拿主視窗開窗時傳入的脈絡副本
(`copy.deepcopy`,子視窗改不到主視窗資料)。子視窗有自己的 `DesktopBridge` 實例,
共用同一個 Facade。

**新增款式的入口與脈絡**:工具列的「新增款式」開空脈絡(子視窗內自己選種類與大產品);
商品列的「新增款式」帶 `category_id`／`product_id`,子視窗直接鎖定該款。
⚠️ 建檔頁在 `mounted` 讀 props 決定預選,外殼必須**先取到脈絡再掛載**
(`v-if="ready"`),否則預選永遠是空的(PITFALLS VUE-9)。
原本商品列的行內快速新增表單已移除:兩個入口統一走建檔流程,連帶都會做重複款式檢查。

**主視窗上鎖**(`window.PosDesktopLock`,`static/js/app.js`):開窗即對 `#app`
設 `inert` 並攔截 `wheel`／`touchmove`／捲動鍵與 `scroll` 事件、記住捲動位置並還原。
⚠️ 只設 `inert` 不夠——`inert` 擋得住點擊與焦點,擋不住滾輪與 PageDown 之類的
捲動;店員在子視窗打字時主視窗跟著滑走會誤以為程式壞了。解鎖靠協調器關窗時
`evaluate_js` 對主視窗派 `pos-child-window-closed` 事件,**取消、Esc、按視窗 X
三條路徑都會走到**(X 走 `window.events.closed`),避免主視窗永久鎖死。

**建檔完成的刷新**:新增款式子視窗每成功送出一批就記下 `saved`,關窗事件帶回主視窗
觸發商品資料庫重新查詢;沒建檔就關掉不會白跑一次查詢。

**儲存為單一交易**:前端不逐項呼叫 API,一次送 `variants.update_editor`
(規格、售價、型號清單、刪除條碼、新增原廠碼、待產生自取碼數量),
服務層在同一連線內完成,任一步失敗整筆 rollback、自取碼計數器一併回復,
錯誤訊息回子視窗且視窗保留讓店員修正。新出現的 select/multi/tags 值會自動
補進 `AttributeOption`(比照建檔流程,不重新啟用已停用選項)。

### 標籤列印(NIIMBOT B1)

⚠️ 動到此功能務必實機驗證,且**至少連續印兩張不同商品**。單元測試只驗證
「送出正確的位元組」,機器收到之後的行為完全測不到;整合當時七個問題全數
是實機才現形,症狀與根因見 `PITFALLS.md` LBL 組。

**分層**:`label_printer.py` 只管跟機器溝通、`label_layout.py` 只管畫圖(純函式、可單測)、
`printing_service.py` 串接資料查詢。協定自行實作而非引用函式庫——參考實作
(`AndBondStyle/niimprint`)不在 PyPI 上,onefile 打包不宜依賴 GitHub 來源。

**硬體實測值**:USB 序列(CDC),`VID:PID=3513:0002` 自動尋埠(**不可寫死 COM 埠**,
換孔換機都會變);115200;兩軸皆 **8 點/mm(203dpi)**,40 × 20mm ＝ **320 × 160 點**;
列印濃度 5(B1 支援 1–5,D11 系列上限才是 3)。

**列印流程**:開埠後必須放掉 DTR → 心跳(順帶擋上蓋未關)→ 濃度 → 紙型 →
`start_print`(**須重試到機器回應內容為 1**)→ 每張各送一次頁面(頁面 → 尺寸 →
張數 → 逐列送圖 → `end_page_print` → **等狀態頁數累加**)→ `end_print`。
不論成功失敗都要送 `end_print`,否則工作留在未收工狀態會讓下次列印被回絕。
機器錯誤封包(type 219)的錯誤碼:`1` 上蓋開啟、`8` 卡紙/送紙異常。

**版面規則**(`label_layout.py`):品名優先大字單行(26→18px),放不下換兩行
(20→16px,斷在空白處);規格**字級固定 14px**,最多兩行且**只斷在「｜」段落邊界**;
號碼靠右擠進規格第一行(省一整行給條碼);條碼高 40–56 點,吃剩餘空間;
**下緣留白 8 點絕不犧牲**,空間不足時依序犧牲規格第二行、品名第二行。
⚠️ 畫字必須 `draw.fontmode = "1"` 關閉灰階平滑:熱感列印只有黑白,
灰色細筆畫二值化時會消失,筆畫密的字(如「框」)印出來缺半邊。

**業務規則**:只印 `source='store'` 的店內條碼(原廠條碼包裝上已有,回錯誤不印);
`Variant.price` 為 NULL 就留白不印;標籤尺寸固定 40 × 20mm 不做設定項;
失敗即取消,由使用者接好機器後自行重送,不做佇列與自動重試。
前端取資料走 `catalog.list`(**`products.list` 不回傳 `barcodes`**,用它會讓店內
條碼全部顯示成「尚無」)。

### UI 風格規範(源自維護者 theme_guide,Apple HIG 風;定義於 `static/css/pos.css` 檔頭)

- **色票**:背景 `#f2f2f7`|元件底 `#fff`|主文字 `#1c1c1e`|次要 `#636366`|佔位/停用字 `#aeaeb2`|邊框 `#c6c6c8`|hover 底/停用底 `#e5e5ea`|pressed 底/停用框 `#d1d1d6`|強調(焦點/選中/chip.on) `#8fa8c8`|主要鈕 `#a1b4cb`/hover `#4977b1`/pressed `#39649a`|危險 `#e74c3c`/hover `#c0392b`。換主色時全域搜尋一起換。
- **焦點一律 2px 藍灰框**:input/select 用 `border-color + inset box-shadow` 疊出 2px(不位移版面),button 用 `:focus-visible` outline;不用瀏覽器預設藍。
- **停用態統一**:底 `#e5e5ea`、字 `#aeaeb2`、框 `#d1d1d6`(primary 停用 `#d1d9e3`)。
- **圓角 8px**(chip/tag 圓膠囊除外);一般鈕 `min-width: 80px` 保持等寬,小型鈕(`.btn-sm`/chip/表格操作鈕)歸零。
- **表單對齊**:「標籤(固定寬 `--label-w`,預設 9em)＋輸入框」兩欄 grid,同表單內全部欄位對齊同一垂直線,列距 10px;label 內文字+輸入框靠 grid 匿名項對齊,html 不需加 span。巢狀框(如 `.spec-box`)在框內覆寫 `--label-w` 扣掉 padding+border,維持框內外同線。新表單一律照此規則。
- **設定頁結構**:單一左欄分群選單(`.cat-list`:「商品種類」清單＋「基礎資料」群組的廠牌/手機品牌與型號)＋右側單一內容區,由 `settings.js` 的 `section` 狀態(`category`/`brands`/`models`)切換;新增設定分區時在左欄基礎資料群組加一項、右側加一段 `v-else-if`,不另開大分頁。
- **子視窗外觀**:`static/css/dialog-theme.css` 是子視窗公版。細捲軸(8px、`#c7c7cc`)、
  文字選取反白(`#8fa8c8`)與按鈕 hover/pressed 灰階(`#e5e5ea`／`#d1d1d6`)取自維護者
  另一個專案已調校過的彈窗公版(`PoliceDocSys/lib/theme.py`);該專案的 checkbox 色塊
  刻意不採用(勾選狀態辨識度不足),本專案維持自己的 checkbox 樣式。
- ⚠️ **勿在 v-if/v-else 元素上掛動態 `:key`**:與 prod 版 Vue(`vue.global.prod.js`)的 `stringifyStatic` 靜態節點快取衝突,key 變動重建區塊後快取 vnode 的 DOM 參照被清空,之後所有畫面更新拋 TypeError、整個 app 卡死;dev 版 Vue 測不出來,務必以 prod 版驗證(v0.1.0 後設定頁曾因此崩潰)。內容全走資料綁定即可,不需 key 強制重建。

## 3. 測試

```powershell
python -m unittest discover -s tests
```

目前 557 個測試,涵蓋 schema/migration、Desktop action 契約、屬性/選單庫、規格值正規化(VariantAttribute)、選項限定型號(OptionModel)、商品/變體/條碼、進貨庫存、結帳/銷售紀錄、盤點、備份、標籤版面與列印協定等模組,檔名皆 `test_*.py`。商品資料庫頁的前端邏輯測試會由 Python 呼叫 Node.js 執行；環境缺少 Node.js 時該測試類別會明確標記為 skipped,其餘 Python 測試仍照常執行。

⚠️ 前端改動的最終驗證一律以真實 pywebview 走查(`RuntimePaths` 指向 `pos.db` 副本＋repo `static/`),
走查腳本**只能操作 DOM**,不可去抓 Vue 元件實例——prod 版 Vue 沒有 `__vue_app__`／`__vueParentComponent`
(PITFALLS VUE-6);版面判斷用 DOM 量測而非截圖(VUE-7)。

⚠️ 標籤列印的測試以替身模擬機器,**通過不等於印得出來**;改動該功能一律另外實機驗證(見 §2 標籤列印)。

## 4. 打包

```powershell
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
Remove-Item -Force POS.spec -ErrorAction SilentlyContinue
pyinstaller --clean --onefile --version-file version_info.txt --icon "assets/POS.ico" --name POS --add-data "static;static" main.py
```

本節是完整打包命令的唯一真實來源。上列命令先清除舊 `build/`、`dist/`、`POS.spec`，
再以 `--clean --onefile` 打包 static 與版本資訊，並以 `assets/POS.ico` 作為執行檔圖示；Desktop-only runtime 不需要 uvicorn hidden-import。
產出 `dist/POS.exe`。執行前須將既有 `pos.db` 放在 exe 同層；備份寫入同層的 `backups/`。

標籤列印新增的三個相依（`pyserial`、`pillow`、`python-barcode`）不需額外 hidden-import，
PyInstaller 自動收錄，含尋埠所需的 `serial.tools.list_ports_windows`（實測 v0.1.0 後，
onefile 產出約 32.6 MB，其中 Pillow 為大宗）。`python-barcode` 內附的
`fonts/DejaVuSansMono.ttf` 未被收錄但不影響：本專案傳入的字級為 0，
`ImageWriter._paint_text` 在載入字型前就先回傳。

**`sys._MEIPASS` 雷**：PyInstaller onefile 模式執行時會把打包資源解壓至暫存目錄 `sys._MEIPASS`；
若仍以程式檔位置推算 `static/`，打包後會找不到前端。`RuntimePaths.detect()` 在 frozen 環境將
`static_dir` 指向 `sys._MEIPASS/static`，`DesktopApplication` 再以該路徑載入本機 `index.html`；
`pos.db`、`backups/` 與 `error.log` 則維持在 exe 同層，不放入暫存解壓目錄。

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
