# DEVELOPER.md

給後續維護者的技術文件。只記現行規則、改了會壞的地方與非直覺取捨的理由；改動過程看 `git log`。

## 1. 架構

```
main.py → RuntimePaths.detect() → 升級前備份 → init_db(pos.db, require_existing=True) → 自動備份
     → DesktopApplication → pywebview（本機 static/index.html）
     → DesktopBridge → Facade → Service → Repository → SQLite
```

- 正式 runtime 不啟動 HTTP server；測試與正式程式都走 Desktop Bridge 與 Facade。
- `pos.db`、`backups/`、`error.log` 在 `main.py`（開發）或 `POS.exe`（打包）同層；`static/` 打包後在 `sys._MEIPASS/static`，由 `RuntimePaths` 決定。
- 正式啟動要求資料庫已存在，不會自動建立空白 DB。

### 關鍵檔案

| 路徑 | 說明 |
|---|---|
| `lib/auth.py` | 管理者身分、密碼雜湊、閒置逾時與唯一受限動作表 `ADMIN_ACTIONS` |
| `lib/desktop_bridge.py` | JS bridge：轉送 action、統一回傳格式、匯出存檔 |
| `lib/child_window.py` | 子視窗協調器：唯一子視窗、頁面白名單、傳入脈絡、關閉通知 |
| `lib/db_schema.py` | 現行 schema DDL 唯一來源與 migration |
| `lib/legacy_migrations.py` | 凍結的 v1–v13 migration DDL，只供舊資料庫升級 |
| `lib/product_rules.py` | 共用商品規則（欄位型別驗證、自取碼取號、員工代號檢查） |
| `lib/variant_batch_service.py` | 子產品批次建立與唯讀預檢 |
| `lib/label_printer.py`／`lib/label_layout.py` | 標籤機協定／標籤版面（純函式） |
| `static/js/pos_shared.js` | 共用 mixin 與掃描解析 `PosScan` |
| `static/js/variant_batch_logic.js` | 新增款式純邏輯，不碰 API，可用 Node 單測 |
| `static/js/confirm.js` | 確認／通知視窗公版 `PosConfirm` |
| `static/css/dialog-theme.css` | 子視窗公版 |
| `tools/` | `bump_version.py` 進版、`publish_release.py` 發布、`make_standard_db.py` 標準資料庫 |
| `tests/base.py` | `ConnTestCase`／`FacadeTestCase` 與 fixture |

## 2. 慣例

### 資料模型

- **Schema**：改現行 schema 一律在 `db_schema.py` 新增 migration，並同步 `SCHEMA`。搬表要明確開交易、保留原編號與流水號，失敗完整回復，不可關閉外鍵檢查。全新資料庫直接建 `SCHEMA`，所以 migration 要能面對欄位已存在的情況。
- **金額與數量**：一律 int（新台幣元，無小數）。
- **庫存採異動流水制**：不存目前庫存，一律由 `StockMovement` 加總（`product_data.stock_of`）。`kind`：`purchase`＋、`sale`－、`adjust`±、`return`＋。
- **售價**：只看 `Variant.price`，NULL 表示未定價。
- **商品結構**：`Product`（款）掛 `Category`／`Brand`；`Variant`（子產品）經 `VariantModel` 多對多掛型號；規格欄 `AttributeField` 經 `CategoryField` 掛種類，選項 `AttributeOption` 無上下層連動。
- **規格值**：存在 `VariantAttribute`（`option_id`／`text_value` 恰一非 NULL），改欄名或選項值立即生效。action 以 `attributes:{欄名:值}` 進出，由 `set_variant_attributes`／`attrs_by_variant` 轉換；select 值查無選項回驗證錯誤。
- **同名欄去重**：`AttributeField` 沒有 `category_id`，同名欄可能有多份，`UNIQUE` 也不套正規化；去重一律先以「欄名＋型別＋種類」查再插。
- **員工代號**：銷售、進貨、盤點、退貨的 `employee` 只收 1～6，後端必填（`require_employee`）。前端 `employee-picker` 共用同一選擇，切頁與結帳後保留。未選時按鈕**不反灰**，按下才提示，否則店員看不出為何沒反應。
- **關鍵輸入**：進貨與盤點掃描數量 > 0，實盤量 ≥ 0；單項折扣不超過品項小計，總額不為負，付款方式須在設定清單內。

### 條碼

- **來源**：`factory`（原廠）與 `store`（`TL` + 流水號，`Setting.next_store_barcode` 純計數、不回收）。手動輸入 `TL` 開頭一律擋。
- **共用**：主鍵 `(barcode, variant_id)`。原廠碼可被多個子產品共用；店內碼全域唯一（`idx_barcode_store`＋應用層檢查），原廠碼不得與店內碼撞號。
- **共用原廠碼必配店內碼**：由 `ensure_store_codes_for_shared` 補發。⚠️ 必須在該次操作**所有條碼寫完後**才呼叫，否則自己勾的「配自取碼」還沒寫入就先被補一組，變成兩組店內碼。
- **自取碼交易**：`next_store_barcode` 用呼叫端連線更新計數器，失敗 rollback 時一併回復。
- **掃描**：`barcodes.scan` 回 `{matches:[...]}`。前端一律經 `window.PosScan`：一筆直接帶入，多筆用 `PosConfirm.choose`；收銀傳 `sellableOnly`，停用／待處理品不可選，只有一筆時照帶、由結帳擋。

### 收銀與進貨

- **收銀鎖定**：購物車在 `window.PosCheckoutDraft`，切頁保留。送出到回應前以 `submitting` 鎖住並遞增 `generation`；加入商品在開頭與每個 `await` 回來後都要檢查。⚠️ 沒鎖時，等待中加入的商品會在結帳成功清空時無聲消失。
- **應收**：品項小計－整單折抵－會員折扣。會員折扣是點數折換的金額，店員手動輸入，存 `Sale.member_discount`；系統不記錄點數。
- **單項折扣**：只有 95／9／85 三顆切換鈕（共用 `.chip`），同列單選、再按取消。品項存折數 `rate`，折扣金額 `lineDiscount` 即時由「數量×單價」算出（折後價四捨五入到元），改數量或單價會自動重算；送後端仍是金額 `discount`，後端與 schema 不變。
- **金額欄**：單價、數量、整單折抵、會員折扣、實收都是文字框，輸入時只留數字。金額清空視為 0；實收清空維持空白（視為剛好付清）；數量打字途中可留白，離開欄位補回至少 1。
- **小計列**：待結帳表 `tfoot`，有品項才顯示；金額未扣整單折抵與會員折扣，所以可能與應收不同。
- **匯出 CSV**（`sales.export`）：含 BOM 讓 Excel 認得 UTF-8；員工後一欄為會員。⚠️ 文字格開頭是 `= + - @`、tab、換行時補單引號（`_csv_text`），否則 Excel 開檔會當公式執行；會員名稱來自 LINE 匯入與店員輸入，不能假設安全。
- **進貨批次**：清單在 `window.PosReceiveDraft`，以 `stock.receive` 同一交易全有全無寫入。⚠️ 確認／清空流程期間以 `locked` 鎖定（掃描、改數量、刪列、加標籤、重按都在方法內擋），另有世代編號 `generation`：掃描回來時編號不同就拒絕。只看鎖定擋不住「入庫完成、解鎖後才回來的舊掃描」，它會進下一批而重複入庫。數量欄不用 `v-model`（會直接改資料，方法擋不住）。
- **適用機型欄**：收銀、進貨、退貨明細用 `display_models_by_variant`，種類 `model_mode` 不是 `required` 時顯示「—」，資料不動。

### 權限（管理者／員工）

| 操作 | 員工 | 管理者 |
|---|---|---|
| 收銀、進貨、標籤列印、商品資料庫查詢（含已停用） | 可 | 可 |
| 盤點／退貨開單、掃描、修改進行中內容、送審、抽回 | 可 | 可 |
| 作廢進行中的盤點單／退貨單 | 可 | 可 |
| 銷售紀錄任意日期查詢與小結 | 可 | 可 |
| 待核准盤點改實盤量、選退貨處理方式、核准、查待核准數量 | 不可 | 可 |
| 作廢待核准的盤點單／退貨單 | 不可 | 可 |
| 銷售紀錄匯出 CSV | 不可 | 可 |
| 商品新增、修改、停用／啟用、刪除、待處理管理及編輯子視窗 | 不可 | 可 |
| 設定頁與管理者密碼變更 | 不可 | 可 |

- **唯一權限表**：`auth.ADMIN_ACTIONS`。新增會改資料的動作必須決定歸屬，並更新逐項列權限的測試。`DesktopFacade.invoke` 分派前檢查；Bridge 直接處理的入口另檢查；匯出在實際寫檔前再查一次。待核准單的修改與作廢不在表內，由服務層依單據狀態檢查。
- **前端**：員工畫面隱藏受限操作，但每個進入點仍要檢查；確認視窗後的破壞性操作在執行前再查身分與目標狀態。
- **身分**：只存後端記憶體，主視窗與子視窗共用，關程式不保留。初始密碼 `admin`，改密碼後立即登出。
- **閒置**：600 秒。後端讀取身分時先檢查逾時，前端不自行計時。`auth.status`、`auth.logout`、待核准數量查詢不續期。主動登出關閉子視窗；閒置逾時不關，下次儲存收到權限錯誤。
- **視窗標題**：主視窗顯示 `POS  [一般使用者／管理者模式]  - v版號`，由 `lib/desktop_bridge.py` 在每次 `auth.*` 回傳後同步；閒置逾時要等前端下次查 `auth.status` 才變，與選單的管理者標籤同時。子視窗標題不變。
- **密碼**：PBKDF2-HMAC-SHA256、200,000 次、16 bytes 鹽，`hmac.compare_digest` 比對。種子只補缺少的密碼。密碼與請求內容不得寫入錯誤記錄；標準資料庫不複製來源密碼。

### 盤點

狀態 `open`→`pending`→`closed`，另有 `cancelled`。同時只能有一張進行中或待核准單（`idx_stocktake_one_active`）。

- **送審不動庫存**，只有管理者核准才寫 `adjust`。任何身分可抽回。
- **差額公式**：實盤量 − 系統量（首次掃描或最近一次抽回時的快照），加入核准當下庫存。⚠️ 不能用目前庫存重算，否則送審期間的銷售會被重複扣除。管理者在待核准單改實盤量，是修正當時的數量，不是重新清點；此時禁止掃描新增。
- **抽回平移**：同一交易內，新系統量＝目前庫存；新實盤量＝舊實盤量＋（新系統量 − 舊系統量），讓未修改品項維持原差額。結果為負時實盤量設 0 並標 `needs_recount=1`，須重新確認才能結案或核准；掃描或改數量即清除，送出相同的 0 也算。
- **防重**：送審、抽回、核准都以來源狀態作條件式更新；核准更新成功一筆才寫調整，同一交易。
- **作廢**：進行中或待核准可作廢（待核准限管理者），記 `cancelled_at`／`cancelled_by`，不動庫存、保留紀錄。已核准不可作廢，以新盤點單修正。不提供真正刪除，避免做錯的單不留痕跡。
- **差異原因**：`StocktakeItem.note` 選填，只在進行中可改。
- **紀錄查詢**：只列已核准與已作廢，日期用 `COALESCE(approved_at,cancelled_at)`。

### 會員

用途只有一個：結帳時確認客人真的是會員，**金額不因此改變**。`lib/member_service.py`（`members.*`）。

- **資料**：`Member`（名稱、`active`）與 `MemberPhone`（10 碼 `09xxxxxxxx`，資料庫 CHECK 擋格式）。電話與會員**多對多**：一位會員可多支電話，一支電話也可屬於多位會員（夫妻共用等）。`Sale.member_id` 可為 NULL（一般客人、舊單）。
- **刪除＝停用**（`active=0`）：已有銷售指向他，硬刪會讓舊單對不到人。停用後收銀查不到、結帳送他的 id 被擋，舊單仍顯示名稱。銷售單只存 id，不存名稱快照。
- **查詢**：輸入只留數字，3 碼比末三碼、10 碼比完整（`phone_pattern`，收銀與紀錄查詢的會員電話篩選共用），其他長度擋下。同一位會員多支電話都符合時只列一次。
- **收銀**：⚠️ 查到幾筆都跳 `PosConfirm.choose`，**一筆也不自動帶入**——店員要當面跟客人確認。查無會員只提示、不在收銀新增（事後到紀錄查詢新增）。帶入的會員放 `PosCheckoutDraft.member`，結帳成功才清，失敗保留。選擇框每列有「修改」（`choose` 的 `actionLabel`，點了回傳 `{action: i}`）：先關選擇框再開編輯框（不疊兩層），關閉後用同一組號碼重查、讓店員再選一次；`members.lookup` 因此一併回傳該會員全部電話 `phones`。
- **管理**：紀錄查詢側欄「會員」分組下的「資料設定」。新增／修改框是共用元件 `member-editor`（`static/js/membereditor.js`），紀錄查詢與收銀都用它。員工可新增、修改；刪除限管理者（`ADMIN_ACTIONS`＋Facade 交易內再查一次，前端確認框後也再查）。存檔前 `members.phone_owners` 查同號碼是否已屬別人，有就先確認。
- **初始名單**：一次性工具 `docs/member_import/import_members.py`（不入庫）把整理好的 Excel 匯進標準資料庫副本；每列一位會員，同名不合併。

### 退貨

`ReturnSession`／`ReturnItem`，狀態與作廢規則同盤點，但**不限同時一張**，也不受盤點影響。

- 每項可填銷售單編號（選填，填了須存在）與原因，送審後不可改。
- 管理者逐項選 `disposition`：`restock`（預設）、`discard`、`reject`。核准同一交易內只替 `restock` 寫 `kind='return'`。
- 待核准盤點抽回時會重新快照，退貨加回自然併入。
- `ReturnSession.refund_amount` 為預留欄位，目前沒有畫面使用。

### 商品與規格設定

- **選項生命週期**：`active` 只控制新增選單。有引用時刪除＝清預設與限定型號後停用；零使用才硬刪。設定頁重新加入同值會恢復原 `option_id` 並啟用；建檔自動補選項不會重新啟用。
- **選項限定型號**：`OptionModel`。`options.list` 帶 `model_ids` 回「未綁型號 ∪ 綁到任一給定型號」，只過濾建檔下拉，不回溯既有資料。`options.set_models` 全量替換，空清單＝通用。
- **規格欄一律不跨種類共用**（`db_seed.NEW_CATEGORY_OWN_FIELDS`）：同名欄的詞彙在各種類互不相干，共用會汙染建檔候選。新種類自動建自己的「顏色」「款式」，動作放在 `settings.js` 的 `attachDefaultFields`，不放 `categories.create`（否則改寫所有測試對空白種類的假設）。
- **手機型號固定列**：`Category.model_mode` 為 `required`／`hidden`。型號存 `VariantModel`，不是規格欄，故以固定列（`MODEL_ROW_ID`）呈現。`hidden` 時畫面顯示「—」，⚠️ 既有關聯保留，款式修改照原值送回，避免「關掉顯示」變成「刪掉資料」。
- **特性詞條固定列**：`tags` 每種類一份同名欄，開關啟用＝建本種類的欄，停用走 `categories.delete_field`。⚠️ 不可用欄名跨種類找詞條欄，只認本種類 `CategoryField` 綁定的那份（`_resolve_field`、`_feature_field_id`、`_feature_id`）。
- **移除規格欄**：`categories.delete_field`＝移除掛勾＋清本種類填過的值，並刪此欄的 `VariantIssue`（⚠️ 漏了會被外鍵擋住）。沒人用才連欄位與選項一起清。確認視窗要顯示影響筆數（`cat_usage_count`）。
- **必填只往前生效**：改必填不停用既有子產品，因為零售現場停售等於當下賣不了。`set_field` 同步待補清單：改必填→缺值者補 `missing_required`；改選填→刪掉。建檔硬擋，修改既有子產品維持軟性。
- **可銷售判斷**：種類／產品／款式都啟用，且沒有 `duplicate_signature`。`missing_required` 不擋銷售；款式重複會掃錯商品所以擋。結帳與掃描同一條規則。匯入的問題款式 `active=0`，手動啟用前要排除所有待處理問題。
- **建檔預設帶入**：`CategoryField.default_option_id` 只在建檔帶入，只對 select 有效，不影響顯示。`categories.set_field` 送 `null` 可清除，其餘欄位的 `null` 視為不動。
- **規格候選前排**：`variants.field_usage` 依「該廠牌用過→該產品用過→種類次數前 8」，因為規格值常是廠牌專屬。`PINNED_OPTION_VALUES`（亮面／霧面／藍光／防窺）固定排最前。
- **型號選取器**：`variants.model_usage` 依「該產品→該廠牌→全部展開」，搜尋一律搜全部。與規格選取器故意不同：搜尋框在候選上方、候選區固定高度可捲，因為型號有上百個。
- **商品資料庫搜尋**：空白切詞取 AND，`casefold` 正規化；比對名稱、種類、廠牌、規格值、型號、條碼。查無啟用資料才另查停用，提供「顯示已停用」入口，不混入結果。廠牌下拉只列目前種類真的有商品的廠牌。

### 子視窗

- **唯一視窗**：`ChildWindowCoordinator` 只開一個，再按只 `restore()`。`CHILD_PAGES` 是頁面白名單，前端只送 key、不送檔名，避免載入任意本機檔。
- **尺寸**：`fit_size()` 夾進實測工作區。寫死高度在 1920×1080＠125% 會開到畫面外。
- **脈絡**：子視窗以 `desktop.child_window.context` 取深複製。⚠️ 外殼要先取到脈絡再掛載（`v-if="ready"`，VUE-9）。
- **主視窗上鎖**（`PosDesktopLock`）：`inert` 擋不住滾輪與捲動鍵，要另外攔截（VUE-8）。解鎖靠關窗事件，取消、Esc、按 X 三條路都要走到，否則主視窗永久鎖死。
- **款式修改為單一交易**：一次送 `variants.update_editor`，任一步失敗整筆 rollback、計數器回復，視窗保留。
- **根元素**：新增子視窗要把根元素 id 加進 `dialog-theme.css` 高度規則（VUE-11）。

### 新增款式與批次預檢

- **組合展開只認 select**：勾第二個值就展開；`multi`／`tags`／型號是同一筆的多個值，不參與。⚠️ 空乘積為 1，沒有展開軸時仍產生一筆。超過 30 筆先確認。
- **條碼展開**：原廠碼只在一筆時帶入，多筆時逐筆掃；自取碼可套用至每筆。複製列時原廠碼一律清空，這裡最容易把同一組條碼灌進多筆。
- **底色只表示「這格要修」**：有紅就是要修，沒紅就是可以建（VUE-20）。列層級問題標上下紅框線。
- **版面**：垂直整頁捲、水平只捲表格，避免兩層捲動。⚠️ 代價是表頭釘不住（`sticky` 只認最近的捲動容器），不做。垂直用 `overflow-y: clip`，`hidden` 會多長一層捲動容器。
- **欄寬**：取欄名與值的最大字寬，⚠️ 要用 canvas `measureText` 實量，估算會切掉中英混排；`variant_batch_logic.js` 的估算只給 Node 測試用。適用型號上限 240px。寬度記在 `localStorage`，⚠️ pywebview 私密模式下關程式就清空，已接受，不改存資料庫。
- **固定編輯區**：多值規格、詞條、型號在表格上方固定區編輯，不開巢狀視窗。Esc 先關編輯區，才關子視窗，避免誤關丟掉工作表。
- **預檢與建立共用驗證**：`variants.batch_precheck` 與 `batch_create` payload 相同，共用 `_validate_batch(payload, dry)`，免得判重規則兩邊不一致。`dry=True` 不寫入：未命中選項回 `("new", normalize_key(value))` 當簽章佔位，有測試驗證筆數不變。
- **錯誤格式**：`{code, field_id, message, related_variant_id, related_draft_id}`，前端據此標列與欄，不解析字串。codes：`unknown_field`、`select_multi_value`、`missing_required`、`missing_models`、`model_not_found`、`store_prefix_barcode`、`duplicate_signature`、`duplicate_barcode`、`missing_barcode`。
- **批內重複**：⚠️ 用首見筆的 `draft_id` 回報，不用陣列索引（前端會略過、刪除、複製列）。
- **原廠碼重複不是錯誤**：`duplicate_barcode` 只代表與店內碼撞號。原廠碼重複回 `notices`，顯示「（已重複）」不擋。⚠️ 要先統計整批再標，否則同批第一筆標不到。
- **每筆至少一組條碼**：否則 `missing_barcode`。`tolerant_create` 與款式修改不受影響，舊資料本來就可能沒碼。
- **重複列**：與既有款式重複的列不能強制建立，列入「已略過 N 筆」；既有款式若停用或待處理要說明狀態與處理入口。
- **交易**：`batch_create` 全有全無；`tolerant_create` 只共用解析結果，逐問題寫 `VariantIssue`。
- **訊息要短**：顯示在窄欄內，不重述欄位看得到的內容。

### 標籤列印（NIIMBOT B1）

⚠️ 動到此功能務必實機驗證，且**至少連續印兩張不同商品**。測試只驗證送出的位元組，症狀與根因見 PITFALLS LBL 組。協定自行實作，因為參考實作 `AndBondStyle/niimprint` 不在 PyPI。

- **硬體**：USB 序列，`VID:PID=3513:0002` 自動尋埠（不可寫死 COM 埠）；115200；8 點/mm，40 × 20mm＝320 × 160 點；濃度 5。
- **流程**：開埠放掉 DTR → 心跳 → 濃度 → 紙型 → `start_print`（重試到回應 1）→ 每張各送頁面（頁面 → 尺寸 → 張數 → 逐列送圖 → `end_page_print` → 等頁數累加）→ `end_print`。⚠️ 尺寸指令要用 6 位元組，否則重插 USB 後第一頁空白（LBL-11）；不論成敗都要送 `end_print`。錯誤碼（type 219）：`1` 上蓋開啟、`8` 卡紙。
- **版面**：品名單行 26→18px，放不下換兩行 20→16px；規格固定 14px，最多兩行，只斷在「｜」；條碼頂端固定第 86 點、高 50 點；文字不超過第 84 點，放不下依序犧牲規格第二行、品名第二行（有型號時相反）；下緣留白 8 點不犧牲。⚠️ 畫字要 `draw.fontmode = "1"`，灰階筆畫二值化後會消失。
- **印哪個碼**：一律店內碼，沒有才印原廠碼（多組取字串排序第一）。加入後條碼固定。`Variant.price` 為 NULL 就留白。
- **列印清單**（`PosLabelQueue`）：送印前 `printing.check` 整批比對，內容變更即停，失效條碼只能移除重加。逐筆 `printing.barcode`，後端再驗 `(barcode, variant_id)`。失敗即停，該列標「請確認張數」，按「確認張數」才能重送（機器不回報已印張數，不做續印）。後端以模組鎖擋同時列印。
- **加入**：一律走 `PosLabelQueueAdd`。前端取資料用 `catalog.list`，`products.list` 不回傳 `barcodes`。

### 補售價

設定頁「工具 → 補售價」開子視窗。`variants.price_list`／`price_save` 都在 `ADMIN_ACTIONS`。存檔同一交易，任一筆不合格整批不寫，逐筆走 `update_variant` 使待處理筆重驗一致。「填入選取列」只套用畫面上看得到的已選列。

### 線上更新

開啟時查 GitHub Releases，有新版詢問，核對後換檔重開；原始碼執行一律回報無更新。

- **信任根**：維護者 GitHub 帳號（沒有程式碼簽章）。須維持帳號兩步驟驗證、repo「不可變更 release」。
- **只認一種格式**：tag 為 `v`＋三段 ASCII 數字；asset 叫 `POS.exe`、狀態 `uploaded`、1 byte～500 MB、有 `sha256:`，網址正好是 `https://github.com/jerrygskk/POS/releases/download/v{版號}/POS.exe`。不符就安靜不更新。
- **詢問時機**：閒置才問（無對話框、未鎖畫面、購物車／進貨清單／標籤清單皆空、規格設定子視窗沒開）。
- **權限**：`desktop.update.*` 員工可用，且不延長管理者閒置計時，否則下載進度輪詢會一直保持登入。
- **換檔**：鎖畫面並攔按鍵（避免條碼槍輸入進收銀）。下載到 `.new`，重讀核對大小與 SHA-256，執行中 exe 改 `.old`、新檔改回（失敗還原）。⚠️ 以乾淨環境變數啟動新版，否則會誤用舊版 `_MEI*`。
- **同時更新**：`POS.exe.update.lock` 以 `msvcrt.locking` 全程持有。
- **關閉時**：下載中取消、最多等 3 秒；已進入換檔要等完成，不得停在 exe 已改名的中間狀態。
- **失敗**：查詢失敗只寫 warning（更新用自己的 logger，桌面 logger 門檻 ERROR）；404 不寫。
- **升級前備份**：版號小於 `SCHEMA_VERSION` 先備份 `backups/pos_upgrade_{時間}_v{舊}-v{新}.db`（留 5 份）；失敗就停止啟動。⚠️ 唯讀開檔用 `Path(...).resolve().as_uri() + "?mode=ro"`，自己拼路徑遇到 `#`、`%` 會開錯檔。

### 標準資料庫（`tools/make_standard_db.py`）

產生「只有商品與設定、沒有營業紀錄」的資料庫：`init_db` 建全新資料庫後逐表原樣複製，庫存全為 0。不覆蓋既有檔案，來源唯讀。

- ⚠️ 每張資料表與設定鍵都要歸在工具的「複製」或「不複製」清單，未歸類就停止。會員（`Member`、`MemberPhone`）屬「複製」。`next_store_barcode` 必須帶，否則新條碼會撞號。
- **母版**：`docs/standard_db/pos_standard.db`（`docs/` 不入庫，換機器要自行複製）。schema 改版後母版落後工具會停止：複製一份、`init_db` 升級，以副本為 `--source` 產出，核對後換掉母版。

```
python tools/make_standard_db.py --source pos.db --output docs/standard_db/pos_standard.db
```

- **清乾淨 `pos.db`**：關掉 POS → `python tools/make_standard_db.py`（由母版產生根目錄 `pos_standard.db`）→ 自行換名成 `pos.db`。

### UI 風格規範

定義於 `static/css/pos.css` 檔頭，換色時全域搜尋。

- **色票**：背景 `#f2f2f7`｜元件底 `#fff`｜主文字 `#1c1c1e`｜次要 `#636366`｜佔位／停用字 `#aeaeb2`｜邊框 `#c6c6c8`｜hover 底／停用底 `#e5e5ea`｜pressed 底／停用框 `#d1d1d6`｜強調 `#8fa8c8`｜危險 `#e74c3c`。主要鈕顏色以 `pos.css` 的變數為準。
- **基本**：圓角 8px；焦點 2px 藍灰框且不位移版面；一般鈕 `min-width: 80px`，小型鈕（`.btn-sm`、chip、表格操作鈕）歸零。
- **小解析度標準 1280×750**：實際筆電 1920×1200 縮放 150%。用資料庫副本開視窗，移到 (0, 0)、設為 1278×720，以 DOM 量測為證據，確認外框沒超出、控制項沒被擠窄、表格下方內容沒被推出畫面。
- **表單對齊**：標籤（固定寬 `--label-w`）＋輸入框兩欄 grid，同表單對齊同一垂直線；巢狀框內覆寫 `--label-w`。對話框標籤欄用 px，em 會跟提示字級縮水（文字繼承見 VUE-13）。
- **固定欄寬表格**：掃入明細用 `.cart-table`／`.fixed-table`（`table-layout: fixed`，欄寬用 `col-wNN`）。⚠️ 只設 `min-width` 掃入後照樣跳（VUE-22）。
- **紀錄查詢列表**：`.records-table` 只留橫線、固定欄寬、數字靠右；列尾 `.records-toggle` 顯示項數，展開時轉淺藍。明細為灰底 `.records-detail`，商品欄佔剩餘寬度並允許折行，不截斷（品名＋規格中位數 30、最長 67 個半形字）。盤點按鈕的項數是明細筆數，差異數另留「差異項數」欄。
- **商品資料庫欄寬**：條碼欄 210px 放得下 13 碼與「印標籤」鈕（鈕固定靠右），更長的以省略號顯示、滑鼠移上看全文；同款式條碼全部列出，自取碼排第一。操作欄只有管理者顯示，未登入時卡片與表格一起縮窄。
- **頂部選單**：不折行不收縮，窄視窗以媒體查詢縮內距。
- **輸入控制項三選一，不要造第四種**：
  - `opt-picker`：值域會長大的欄位。由上而下固定「已選／候選／搜尋框／搜尋結果」。⚠️ 不跳版三規則：已選區 1～2 行、結果區保留一行高度；候選被選走留在原位只換樣式；複選點候選保留搜尋字與焦點。候選未載入時顯示「候選載入中…」，不退回其他介面。
  - `combo-box`：從主檔挑一筆。⚠️ 點擊區不可用 `<button>`（VUE-12）；取得焦點不自動展開，否則蓋住下面的欄位。
  - 原生 `<select>`：選項固定且少。
- **開關公版**（`.switch`）：`<button role="switch">`＋`aria-checked`。
- **必填／選填**（`.chip-toggle`）：不用勾選框；鎖定時不轉灰，以 title 說明原因。不可改的項目直接不顯示控制項，灰欄位看起來像壞掉。
- **提醒與錯誤**（`.notice-bar`／`.error-bar`）：⚠️ 錯誤顯示在觸發它的區塊內（設定頁 `errorScope`），不丟到整頁最上面。
- **確認視窗**：一律 `PosConfirm`。⚠️ 不可用內建 `confirm()`／`alert()`，pywebview 會畫成深色系統框。`choose` 不攔 Enter。
- **設定頁**：左欄分群選單＋右側單一內容區（`section` 切換），新分區加在左欄基礎資料群組，不開大分頁。規格項目就地編輯、一列一行，改動即時存檔，成敗都重讀模板，否則失敗時控制項會停在沒存進去的狀態。
- **清單維護儲存**：可即時寫入的區塊連排序也即時寫；批次儲存的區塊用 `.card-actions`（VUE-16）並顯示未儲存提醒。⚠️ 會重載清單的動作要先收起未儲存修改、做完貼回（`keepEdits`，VUE-17），不可代替使用者先存。⚠️ 手機品牌與型號同頁，品牌即時寫入不可重載整頁，成功時只更新本機；`pendingModelSort`、`collapsedBrands` 以品牌名當鍵，改名時要從目前所在名稱搬過去。
- **長清單**：⚠️ 過濾時關閉排序（VUE-15）。
- **子視窗外觀**：`dialog-theme.css`；覆寫公版要寫足選擇器層級（VUE-21）。

## 3. 測試

```powershell
python -m unittest discover -s tests
```

- 前端邏輯測試由 Python 呼叫 Node.js，沒裝 Node 時該類別標 skipped。
- ⚠️ `tests/test_import_excel.py` 與 `tools/import_excel.py` 都**不入庫**，有這兩支的機器會多跑 29 項，匯入驗收後一起刪。
- ⚠️ 前端最終驗證用真實 pywebview 走查（資料庫副本＋repo `static/`）；走查只操作 DOM（VUE-6），版面用 DOM 量測（VUE-7）。
- ⚠️ 標籤測試以替身模擬機器，通過不等於印得出來。

## 4. 打包

```powershell
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
python -m PyInstaller --clean --noconfirm POS.spec
```

- 本節是打包命令唯一來源。產出 `dist/POS.exe`，執行前將 `pos.db` 放在同層。
- `console=False`：程式不得依賴 stdout／stderr，錯誤一律寫 `error.log`。
- `POS.spec` 排除 `tkinter`、`pytest`，剔除 PATH 上 Git for Windows 混入的 OpenSSL DLL，`upx=False`（防毒誤判）。
- `python-barcode` 內附字型未被收錄但不影響：字級傳 0，在載入字型前就回傳。

**發布上傳**（版號進版 commit 與 tag 都推上去之後，需已登入 `gh`）：

```powershell
python tools/publish_release.py
```

工具核對 exe 版本、工作區乾淨且 HEAD 為該版 tag、遠端有同 tag、該版未公開、有 `release_note_v{版號}.md`；建草稿後核對檔名、大小、SHA-256 才公開（公開後改不回來）。`gh release` 帶 `-R jerrygskk/POS`；`gh api` 不支援 `-R`，寫完整路徑；檔案路徑不得含 `#`。

## 5. 版號控制

- 單一來源 `lib/version.py`（`__version__`），顯示版本一律 import，不寫死。
- 進版一律 `python tools/bump_version.py {新版號}`；`version_info.txt` 由工具產生、不入庫，fresh clone 要先跑一次才能 build。
- 版號三碼 主.次.修，日常進第三碼。

## 6. 版本記錄

> 只留「改了什麼、為什麼」的骨架，細節查對應 tag 與 `git log`。

| 版本 | 摘要 |
|------|------|
| v0.1.4 | **紀錄查詢改版與收銀折扣，schema v21**。①紀錄查詢改橫線表格、項數鈕與明細小表格，可多筆展開，依類別預設日期。②收銀新增會員折扣、小計列與純數字金額欄。③明細加適用機型、補售價併入主程式、訂小解析度標準。④商品資料庫售價併為一欄、操作欄僅管理者顯示、條碼全部列出。 |
| v0.1.3 | **退貨單、單據作廢與盤點差異原因，schema v20**。①退貨單送審後由管理者逐項選加回庫存、不入庫或不同意，只有加回才動庫存。②盤點單與退貨單可作廢，保留紀錄、不提供真正刪除。③紀錄查詢預設日期改用本機時間；找不到資料庫時跳提示。 |
| v0.1.2 | **線上更新與兩層權限，schema v16–v19**。①GitHub Releases 線上更新，閒置才問、核對後換檔；另有 `publish_release.py` 與升級前備份。②管理者／員工權限，盤點改送審核准；結帳、進貨、盤點記錄員工代號。③原廠碼可共用、批次進貨，型號改掛裝置類型。 |

本節只留最近三個版本；**被擠掉的逐版記錄全部在 [HISTORY.md](HISTORY.md)**，進版時把被擠掉的那一列搬過去。
