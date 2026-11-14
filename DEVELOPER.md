# DEVELOPER.md

給後續維護者的技術文件。只記現行規則、改了會壞的地方與非直覺取捨的理由；改動過程看 `git log`。

## 1. 架構

```
main.py → RuntimePaths.detect() → 版號檢查 → 升級前備份 → init_db(pos.db, require_existing=True) → 自動備份
     → DesktopApplication → pywebview（本機 static/index.html）
     → DesktopBridge → Facade → Service → Repository → SQLite
```

- 正式 runtime 不啟動 HTTP server；測試與正式程式都走 Desktop Bridge 與 Facade。
- `pos.db`、`backups/`、`error.log` 在 `main.py`（開發）或 `POS.exe`（打包）同層；`static/` 打包後在 `sys._MEIPASS/static`，由 `RuntimePaths` 決定。
- 正式啟動要求資料庫已存在，不會自動建立空白 DB。資料庫版號低於 `MIN_SUPPORTED_VERSION`（含有資料表卻讀不到版號、版號不是數字）時，在備份與任何寫入之前拒絕，跳「資料庫版本過舊」要求改用新版標準資料庫。資料庫版號比程式新時，在任何寫入（含種子）前停下並跳錯誤視窗要求更新程式；其他啟動失敗（升級、備份、初始化）也跳錯誤視窗並告知 error.log 位置，不可靜默結束。

### 關鍵檔案

| 路徑 | 說明 |
|---|---|
| `lib/auth.py` | 管理者身分、密碼雜湊、閒置逾時與唯一受限動作表 `ADMIN_ACTIONS` |
| `lib/desktop_bridge.py` | JS bridge：轉送 action、統一回傳格式、匯出存檔 |
| `lib/child_window.py` | 子視窗協調器：唯一子視窗、頁面白名單、傳入脈絡、關閉通知 |
| `lib/db_schema.py` | 現行 schema DDL 唯一來源、`MIN_SUPPORTED_VERSION` 與 migration 清單 |
| `lib/stock_ledger.py` | 庫存異動唯一入帳入口 `post()` |
| `lib/dayclose_service.py`／`lib/correction_service.py` | 每日立帳／單據更正紀錄與修正操作者 |
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

- **Schema**：`SCHEMA` 是唯一定義。`MIN_SUPPORTED_VERSION` 以前的升級步驟全部移除（目前等於 v27），較舊的資料庫一律拒絕開啟，改用母版工具轉出的新版標準資料庫。現行為 **v28**，`MIGRATIONS` 只有 v27→v28 一步（`_to_v28`：原名重建 `Sale`／`StockMovement`、補帳日；升級與種子在同一交易，commit 前跑外鍵檢查，失敗整個回復）；來源結構凍結在 `tests/fixtures/schema_v27.sql`。出 1.0.0 時把這一步連同該 fixture 與 `tests/test_migration_v28.py` 移除，`BASE_VERSION`、`MIN_SUPPORTED_VERSION` 改成當時版號。尚未上線期間，改結構直接改 `SCHEMA` 並重產母版；上線後再改結構才在 `MIGRATIONS` 加步驟：搬表要明確開交易、保留原編號與流水號，失敗完整回復，不可關閉外鍵檢查。
- **資料庫限制分兩層**：單筆能判斷的寫成 CHECK、複合外鍵、部分唯一索引；跨表規則放程式，在同一交易內檢查（`product_data.check_variant_attributes`、`returns_service.check_kind`、`product_data.check_field_name`）。
- **金額與數量**：一律 int（新台幣元，無小數）。
- **庫存採異動流水制**：不存目前庫存，一律由 `StockMovement` 加總（`product_data.stock_of`）。`kind`：`purchase`＋、`sale`－、`adjust`±、`return`＋、`repair`＋（返新）、`void`＋（作廢銷售單沖回，schema v28；與 `sale` 指向同一筆銷售明細，一筆明細各只能有一筆 `sale` 與一筆 `void`）。
- **異動來源（schema v26）**：每筆異動恰好指向一個來源明細（`receipt_item_id`／`sale_item_id`／`stocktake_item_id`／`return_item_id`），CHECK 限定來源與 kind、正負一致；複合外鍵保證來源與異動同款式；部分唯一索引防同一明細入帳兩次（返新可分批，不套用）。`employee` 只有返新記，其他看各自單據。
- **入帳唯一入口** `stock_ledger.post(conn, kind, source_id, qty=None, employee=None)`：數量一律由來源明細算出（返新除外），同交易檢查單據狀態與返新上限。進貨、結帳、盤點核准、退貨核准、返新五個寫入點都走它，不直接寫 `StockMovement`。
- **已入帳鎖定**：有異動指向的明細，被鎖欄位真的改值（`OLD.x IS NOT NEW.x`）或刪除時由 trigger 擋；`StockMovement` 全表不可改刪。⚠️ 另有新增前與更新前檢查擋 `INSERT／UPDATE OR REPLACE`：撞鍵時的隱含刪除不會觸發刪除 trigger（PITFALLS SQL-5）。返修結案／撤回、退貨原因、盤點備註、單據狀態不在鎖定範圍。
- **售價**：只看 `Variant.price`，NULL 表示未定價。
- **商品結構**：`Product`（款）掛 `Category`／`Brand`；`Variant`（子產品）經 `VariantModel` 多對多掛型號；規格欄 `AttributeField` 直接帶 `category_id`（schema v25 併入原 `CategoryField` 的排序、必填、預設選項），選項 `AttributeOption` 無上下層連動。
- **規格值**：存在 `VariantAttribute`（`option_id`／`text_value` 恰一非 NULL），改欄名或選項值立即生效。action 以 `attributes:{欄名:值}` 進出，由 `set_variant_attributes`／`attrs_by_variant` 轉換；select 值查無選項回驗證錯誤。
- **規格值一致性**：複合外鍵保證選項屬於該欄；跨表規則（欄位屬於商品的種類、select 單值、欄型別與值型別對應）由 `check_variant_attributes` 在寫入的同一交易檢查。已填規格的產品改種類回衝突，不自動清值。
- **同種類欄名不重複**：資料庫 `UNIQUE(category_id, name)` 只擋完全相同；全半形、大小寫、空白不同的同名由 `check_field_name`（`normalize_key`）擋。⚠️ `fields.create`／`update` 先 `BEGIN IMMEDIATE` 再查重與寫入，否則兩個操作交錯會各自通過檢查。
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
- **單項折扣與實售價**：原價唯讀（未標價品掃入時跳框輸入的值放在原價）；另有「實售價」欄，可由 95／9／85 切換鈕（共用 `.chip`，同列單選、再按取消）帶入「原價×折數」四捨五入到元，也可由店員直接輸入整數，手填即取消折數鈕。折扣金額 `lineDiscount`＝（原價－實售價）×數量；送後端仍是金額 `discount`，後端與 schema 不變。實售價高於原價時格子標紅，結帳前擋下（後端另驗 `0 ≤ discount ≤ 數量×原價`）。
- **進貨列印標籤**：一般進貨與返新進貨都不另設標籤欄，「列印 N 張」按鈕接在數量框後面（`.qty-label`），按過變「已加入 N 張」，數量改大只補差額。
- **金額欄**：原價、數量、整單折抵、會員折扣、實收都是文字框，輸入時只留數字。金額清空視為 0；實收清空維持空白（視為剛好付清）；數量打字途中可留白，離開欄位補回至少 1。
- **付款方式**：現金、轉帳、LINE Pay、台灣Pay（存 `Setting.payments`，schema v23 把舊的「刷卡」連同舊銷售單改名為轉帳；2026-11-14 把「行動支付」改名 LINE Pay，未上線故不做升級，直接改種子並重產母版）。`Setting.payments_disabled` 列出停用的付款方式（目前是台灣Pay）：`payments.list`、結帳、更正付款方式都只認啟用的（`SalesRepository.enabled_payments`），報表類（收銀合計、立帳預覽、立帳紀錄）用全部清單（`payments`），停用的仍佔一欄、金額 0。`Setting.payment_fees` 是「付款方式 → 抽成比例（百分比）」，沒列的視為 0（目前 LINE Pay 2.2）。⚠️ 這三個設定目前沒有畫面可改，只能改種子或資料庫；設定入口待維護者與店家確認後再做。舊資料庫開啟時種子只補缺的鍵，不會改既有的 `payments`。非現金一律剛好付清：前端實收鎖住顯示應收，後端也把 `paid` 強制改成應收、找零 0，不信任前端傳值。現金實收少於應收時前後端都擋（後端為準）。結帳成功後付款方式回到清單第一項（現金），避免下一單沿用上一單的付款方式記錯。會員折扣不要求先選會員。
- **小計列**：待結帳表 `tfoot`，有品項才顯示；金額未扣整單折抵與會員折扣，所以可能與應收不同。
- **紀錄查詢條件**：起日晚於迄日由後端拒絕；查詢失敗清空列表與合計，不留上一次結果。匯出 CSV 用最近一次查詢成功的條件，不讀輸入框當下的值（否則匯出內容與畫面不符）。
- **匯出 CSV**（`sales.export`）：含 BOM 讓 Excel 認得 UTF-8；員工後一欄為會員（只有名稱，刻意不帶電話：匯出檔易外流）。紀錄查詢明細的會員列則帶該會員全部電話（`member_phones`，依號碼排序以「、」串接，即時讀 `MemberPhone`，非結帳當下快照）。⚠️ 文字格開頭是 `= + - @`、tab、換行時補單引號（`_csv_text`），否則 Excel 開檔會當公式執行；會員名稱來自 LINE 匯入與店員輸入，不能假設安全。
- **進貨批次**：清單在 `window.PosReceiveDraft`，以 `stock.receive` 同一交易全有全無寫入，一次確認建一張進貨單（`Receipt`／`ReceiptItem`，同款式多列各自保留數量），回傳 `receipt_id`。⚠️ 確認／清空流程期間以 `locked` 鎖定（掃描、改數量、刪列、加標籤、重按都在方法內擋），另有世代編號 `generation`：掃描回來時編號不同就拒絕。只看鎖定擋不住「入庫完成、解鎖後才回來的舊掃描」，它會進下一批而重複入庫。數量欄不用 `v-model`（會直接改資料，方法擋不住）。
- **適用機型欄**：收銀、進貨、退貨明細用 `display_models_by_variant`，種類 `model_mode` 不是 `required` 時顯示「—」，資料不動。

### 立帳、作廢與更正（schema v28）

- **帳日** `Sale.business_date`：結帳時在 INSERT 同一句決定，當天已立帳就算隔天，否則算當天；結帳先 `BEGIN IMMEDIATE`，避免與立帳交錯。紀錄查詢的日期條件、合計、匯出一律看帳日，不看結帳時間；帳日與結帳日期不同的單在列表標「計入次日帳」。
- **每日立帳**（`dayclose_service`，限管理者，入口在紀錄查詢「立帳」）：`DayClose` 一個帳日一筆，記預備金、錢櫃現金總額、現金銷售、短溢、原因與員工代號。短溢＝錢櫃總額－預備金－現金銷售（不含已作廢），不為 0 時原因必填。`dayclose.close` 要帶預覽當下看到的現金銷售，後端同交易比對，不同就回衝突。不可替未來的帳日立帳；預備金存 `Setting.cash_float`。
- **立帳紀錄與月結圖片**：`dayclose.list` 每筆帶 `by_payment`（各付款方式的 `total` 與 `fee_rate`，每筆順序相同）與 `sales_total`。立帳後銷售單已鎖，金額直接從 `Sale` 加總，不另存快照；不含已作廢、不扣退貨（店家每天處理完退貨才立帳）。表尾列查詢區間的合計；有抽成時再列「抽成比例／抽成金額／實收」。抽成由前端 `dcTotals` 以**區間合計**乘比例四捨五入到元（金流業者在提領時才扣，每天的金額不扣）。「複製成圖片」（`dcCopyImage`）用 canvas 畫一張給公司看的報表放進剪貼簿：欄位是帳日、各付款方式、銷售合計、短溢、原因，日期由小到大，不列員工、立帳時間、預備金、錢櫃總額；與畫面表格是兩套欄位，改一邊要想另一邊。畫面表格的短溢原因不佔欄，只在有原因的帳日下面多一行；最右一欄是空白欄，吃掉多餘寬度，讓金額欄維持固定寬。
- **鎖帳**：帳日立帳後，該帳日的銷售單不可新增、改金額／付款方式／會員／帳日、作廢、刪除，由 trigger 擋（含兩種 REPLACE）；`DayClose` 本身不可改刪。立帳後要沖帳只能走退貨。
- **作廢銷售單**（`sales.void`，限管理者）：只限帳日未立帳、尚無退貨單、未作廢的單。原單保留，寫 `voided_at`、`void_reason`（原因代碼對應的文字；選「其他」必填說明），同交易以 `stock_ledger.post('void', …)` 沖回庫存，並同步進行中盤點單的系統庫存量。已作廢的單不可再改、不可退貨，合計與立帳的現金銷售都不計入，另列「已作廢 N 筆」。要重開就重新結一張。
- **更正付款方式**（`sales.set_payment`，限管理者）：只限帳日未立帳、未作廢；金額不動。**修正操作者**（`corrections.set_employee`，限管理者）：銷售、進貨、退貨、盤點四種單據的員工代號，不受立帳限制。兩者都寫一筆 `Correction`（單據種類、編號、欄位、原值、新值、時間），`Correction` 不可改刪，紀錄查詢展開後列出更正歷程。
- **不記是誰做的**（維護者 2026-11-10 裁示）：作廢、更正付款方式、修正操作者三個框都不問員工代號，資料庫沒有對應欄位；只有立帳記員工代號。前端用語固定為「修正操作者」，框標題依單據為「修正結帳／進貨／退貨／盤點操作者」。
- **防舊畫面**：三個動作都帶畫面上看到的原值（`expected_total`／`expected_payment`／`expected_employee`），後端同交易比對，不同回衝突。
- **前端**：三個框共用元件 `static/js/doccorrect.js`（`doc-correct`，`mode` 為 `void`／`payment`／`employee`）。管理者登出或閒置逾時時若停在立帳頁，切回收銀紀錄並清掉已載入的金額。

### 權限（管理者／員工）

| 操作 | 員工 | 管理者 |
|---|---|---|
| 收銀、進貨、標籤列印、商品資料庫查詢（含已停用） | 可 | 可 |
| 盤點／退貨開單、掃描、修改進行中內容、送審、抽回 | 可 | 可 |
| 作廢進行中的盤點單／退貨單 | 可 | 可 |
| 銷售紀錄任意日期查詢與小結 | 可 | 可 |
| 盤點退回重盤、選退貨處理方式、核准、查待核准數量 | 不可 | 可 |
| 作廢待核准的盤點單／退貨單 | 不可 | 可 |
| 銷售紀錄匯出 CSV | 不可 | 可 |
| 每日立帳、預備金設定、立帳紀錄查詢 | 不可 | 可 |
| 作廢銷售單、更正付款方式、修正單據操作者 | 不可 | 可 |
| 商品新增、修改、停用／啟用、刪除、待處理管理及編輯子視窗 | 不可 | 可 |
| 設定頁與管理者密碼變更 | 不可 | 可 |

- **唯一權限表**：`auth.ADMIN_ACTIONS`。新增會改資料的動作必須決定歸屬，並更新逐項列權限的測試。`DesktopFacade.invoke` 分派前檢查；Bridge 直接處理的入口另檢查；匯出在實際寫檔前再查一次。待核准單的修改與作廢不在表內，由服務層依單據狀態檢查。
- **前端**：員工畫面隱藏受限操作，但每個進入點仍要檢查；確認視窗後的破壞性操作在執行前再查身分與目標狀態。
- **身分**：只存後端記憶體，主視窗與子視窗共用，關程式不保留。初始密碼 `admin`，改密碼後立即登出。
- **閒置**：600 秒。後端讀取身分時先檢查逾時，前端不自行計時。`auth.status`、`auth.logout`、待核准數量查詢不續期。主動登出關閉子視窗；閒置逾時不關，下次儲存收到權限錯誤。
- **視窗標題**：主視窗顯示 `膜型機門市管理系統  [一般使用者／管理者模式]  - v版號`，由 `lib/desktop_bridge.py` 在每次 `auth.*` 回傳後同步；閒置逾時要等前端下次查 `auth.status` 才變，與選單的管理者標籤同時。子視窗標題不變。
- **密碼**：PBKDF2-HMAC-SHA256、200,000 次、16 bytes 鹽，`hmac.compare_digest` 比對。種子只補缺少的密碼。密碼與請求內容不得寫入錯誤記錄；標準資料庫不複製來源密碼。

### 盤點

狀態 `open`→`pending`→`closed`，另有 `cancelled`。同時只能有一張進行中或待核准單（`idx_stocktake_one_active`）。

- **送審不動庫存**，只有管理者核准才寫 `adjust`。任何身分可抽回。
- **差額公式**：實盤量 − 系統量（首次掃描或最近一次抽回時的快照），加入核准當下庫存。⚠️ 不能用目前庫存重算，否則送審期間的銷售會被重複扣除。待核准單不開放改實盤量（含管理者），有誤一律以「退回重盤」交回員工依現場數量修正；此時也禁止掃描新增。
- **抽回平移**：同一交易內，新系統量＝目前庫存；新實盤量＝舊實盤量＋（新系統量 − 舊系統量），讓未修改品項維持原差額。結果為負時實盤量設 0 並標 `needs_recount=1`，須重新確認才能結案或核准；掃描或改數量即清除，送出相同的 0 也算。
- **防重**：送審、抽回、核准都以來源狀態作條件式更新；核准更新成功一筆才寫調整，同一交易。
- **作廢**：進行中或待核准可作廢（待核准限管理者），記 `cancelled_at`／`cancelled_by`，不動庫存、保留紀錄。已核准不可作廢，以新盤點單修正。不提供真正刪除，避免做錯的單不留痕跡。
- **差異原因**：`StocktakeItem.note` 選填，只在進行中可改。
- **掃描**：按 Enter 先取出條碼並清空輸入框，再排隊依序處理（`scanOne`）；⚠️ 處理完才清空會吃掉掃描槍接著打入的下一筆。
- **紀錄查詢**：只列已核准與已作廢，日期用 `COALESCE(approved_at,cancelled_at)`。

### 會員

用途只有一個：結帳時確認客人真的是會員，**金額不因此改變**。`lib/member_service.py`（`members.*`）。

- **資料**：`Member`（名稱、`active`）與 `MemberPhone`（10 碼 `09xxxxxxxx`，資料庫 CHECK 擋格式）。電話與會員**多對多**：一位會員可多支電話，一支電話也可屬於多位會員（夫妻共用等）。`Sale.member_id` 可為 NULL（一般客人、舊單）。
- **刪除＝停用**（`active=0`）：已有銷售指向他，硬刪會讓舊單對不到人。停用後收銀查不到、結帳送他的 id 被擋，舊單仍顯示名稱。銷售單只存 id，不存名稱快照。
- **查詢**：輸入只留數字，3 碼比末三碼、10 碼比完整（`phone_pattern`，收銀與紀錄查詢的會員電話篩選共用），其他長度擋下。同一位會員多支電話都符合時只列一次。
- **收銀**：⚠️ 查到幾筆都跳 `PosConfirm.choose`，**一筆也不自動帶入**——店員要當面跟客人確認。查無會員只提示、不在收銀新增（事後到紀錄查詢新增）。帶入的會員放 `PosCheckoutDraft.member`，結帳成功才清，失敗保留。選擇框每列有「修改」（`choose` 的 `actionLabel`，點了回傳 `{action: i}`）：先關選擇框再開編輯框（不疊兩層），關閉後用同一組號碼重查、讓店員再選一次；`members.lookup` 因此一併回傳該會員全部電話 `phones`。
- **管理**：紀錄查詢側欄「會員」分組下的「資料設定」。新增／修改框是共用元件 `member-editor`（`static/js/membereditor.js`），紀錄查詢與收銀都用它。員工可新增、修改；刪除限管理者（`ADMIN_ACTIONS`＋Facade 交易內再查一次，前端確認框後也再查）。存檔前 `members.phone_owners` 查同號碼是否已屬別人，有就在編輯框內顯示警示、儲存鈕改「仍要儲存」再按一次才送出（不另開確認框，避免兩層對話框）。名單搜尋的 `%`、`_` 照字面比對（LIKE 跳脫）。
- **初始名單**：一次性工具 `docs/member_import/import_members.py`（不入庫）把整理好的 Excel 匯進標準資料庫副本；每列一位會員，同名不合併。

### 退貨

`ReturnSession`／`ReturnItem`，狀態與作廢規則同盤點，但**不限同時一張**，也不受盤點影響。

- 每項可填銷售單編號（選填，填了須存在）與原因，送審後不可改。
- **種類（schema v27）**：`ReturnSession.kind` 為 `normal`／`repair`。一般退貨由管理者逐項選 `disposition`：`restock`（預設）、`discard`、`reject`，核准同一交易內只替 `restock` 寫 `kind='return'`。返修單的品項 `disposition` 為 NULL；兩者一致由 `check_kind` 在掃描、切換、改處理方式、核准時檢查。
- **切換種類**：`returns.set_kind` 限管理者、限待核准。切返修清空處理方式、切回一般全部設回加回庫存；⚠️ 種類沒變就不動，否則重送會把「不同意」改回加回庫存。
- **核准帶確認內容**：`returns.approve` 必帶確認框上看到的 `kind` 與各品項 `item_id`／`qty`／`disposition`，與資料庫現況不同回衝突「退貨單內容已變更」。前端在切換期間停用核准鈕。⚠️ 只靠前端停用擋不住「確認框開著時店員抽回改數量再送出」。
- 品項操作一律以 `ReturnItem.id`（`item_id`）定位。
- 原因上限 30 字（`REASON_MAX`，前端 `maxlength` 同步）：返新進貨與返修卡片都要一行放得下，不切字。返修結案理由同樣 30 字。
- **返修單**：核准後狀態 `repairing`，不動庫存。返修單抽回後再掃入的新品項沿用返修。
  - **返新進貨**：進貨頁切到「返新進貨」，輸入退貨單號帶出品項，可分批進貨（`returns.repair_receive`），每批寫 `StockMovement kind='repair'`、`return_item_id`＝退貨品項。已返新件數直接加總這些異動，不另存數字。
  - **結案／撤回結案**（限管理者，`returns.close_item`／`reopen_item`）：結案一次把該品項「未返新」的件數全部結掉，必填理由，存 `closed_qty`／`closed_reason`／`closed_at`；撤回把結案件數清回 0。所以同一品項不會同時有待返新與已結案。
  - 每個品項都返新或結案後，單自動轉 `closed` 並寫 `completed_at`；撤回結案會轉回 `repairing`。
  - **紀錄查詢**：退貨分「一般退貨／返修退貨」切換。返修區只收已核准的返修單（`repairing`／`closed`）；待核准、抽回後進行中的返修單留在一般區，否則兩區都找不到。一般退貨不列作廢單；返修退貨中的單不受日期篩選一律列出。返修明細是卡片：品名為主、規格為從，件數（數量／已返新／待返新或已結案，固定寬度、數字等寬、整組靠右）與狀態膠囊同列，原因獨立淺底區塊；「結案」白底紅框（`.btn-sm.convert`）、「撤回結案」同構藍框（`.btn-sm.subtle`），靠右。
- 待核准盤點抽回時會重新快照，退貨加回自然併入。
- `ReturnSession.refund_amount` 為預留欄位，目前沒有畫面使用。

### 商品與規格設定

- **選項生命週期**：`active` 只控制新增選單。有引用時刪除＝清預設與限定型號後停用；零使用才硬刪。設定頁重新加入同值會恢復原 `option_id` 並啟用；建檔（批次新增、款式編輯）自動補選項不會重新啟用：批次新增遇到停用值預檢即列錯誤「選項已停用」，款式編輯只能沿用該款式原有的停用值。選項比對一律先正規化（大小寫、全半形、空白），找到既有選項就沿用既有字串。
- **規格重複**：款式編輯改成與同商品其他款式相同規格（`variant_signature`）時拒絕存檔；只在規格有變時比對，舊資料原本就重複的仍可改售價等欄位。批次新增同一筆草稿內條碼重複，預檢即列錯誤「條碼重複」。
- **選項限定型號**：`OptionModel`。`options.list` 帶 `model_ids` 回「未綁型號 ∪ 綁到任一給定型號」，只過濾建檔下拉，不回溯既有資料。`options.set_models` 全量替換，空清單＝通用。
- **規格欄一律不跨種類共用**（`db_seed.NEW_CATEGORY_OWN_FIELDS`）：同名欄的詞彙在各種類互不相干，共用會汙染建檔候選。新種類自動建自己的「顏色」「款式」，動作放在 `settings.js` 的 `attachDefaultFields`，不放 `categories.create`（否則改寫所有測試對空白種類的假設）。
- **手機型號固定列**：`Category.model_mode` 為 `required`／`hidden`。型號存 `VariantModel`，不是規格欄，故以固定列（`MODEL_ROW_ID`）呈現。`hidden` 時畫面顯示「—」，⚠️ 既有關聯保留，款式修改照原值送回，避免「關掉顯示」變成「刪掉資料」。
- **特性詞條固定列**：`tags` 每種類一份同名欄，開關啟用＝建本種類的欄，停用走 `categories.delete_field`。⚠️ 不可用欄名跨種類找詞條欄，只認本種類（`AttributeField.category_id`）的那份（`_resolve_field`、`_feature_field_id`、`_feature_id`）。
- **移除規格欄**：`categories.delete_field`＝移除掛勾＋清本種類填過的值，並刪此欄的 `VariantIssue`（⚠️ 漏了會被外鍵擋住）。沒人用才連欄位與選項一起清。確認視窗要顯示影響筆數（`cat_usage_count`）。
- **必填只往前生效**：改必填不停用既有子產品，因為零售現場停售等於當下賣不了。`set_field` 同步待補清單：改必填→缺值者補 `missing_required`；改選填→刪掉。建檔硬擋，修改既有子產品維持軟性。
- **可銷售判斷**：種類／產品／款式都啟用，且沒有 `duplicate_signature`。`missing_required` 不擋銷售；款式重複會掃錯商品所以擋。結帳與掃描同一條規則。匯入的問題款式 `active=0`，手動啟用前要排除所有待處理問題。
- **建檔預設帶入**：`AttributeField.default_option_id`（CHECK 限 select、複合外鍵限本欄選項） 只在建檔帶入，只對 select 有效，不影響顯示。`categories.set_field` 送 `null` 可清除，其餘欄位的 `null` 視為不動。
- **規格候選前排**：`variants.field_usage` 依「該廠牌用過→該產品用過→種類次數前 8」，因為規格值常是廠牌專屬。`PINNED_OPTION_VALUES`（亮面／霧面／藍光／防窺）固定排最前。
- **型號選取器**：`variants.model_usage` 依「該產品→該廠牌→全部展開」，搜尋一律搜全部。與規格選取器故意不同：搜尋框在候選上方、候選區固定高度可捲，因為型號有上百個。
- **商品資料庫搜尋**：空白切詞取 AND，`casefold` 正規化；比對名稱、種類、廠牌、規格值、型號、條碼。查無啟用資料才另查停用，提供「顯示已停用」入口，不混入結果。廠牌下拉只列目前種類真的有商品的廠牌。

### 子視窗

- **唯一視窗**：`ChildWindowCoordinator` 只開一個，再按只 `restore()`。`CHILD_PAGES` 是頁面白名單，前端只送 key、不送檔名，避免載入任意本機檔。
- **尺寸**：`fit_size()` 夾進實測工作區。寫死高度在 1920×1080＠125% 會開到畫面外。
- **脈絡**：子視窗以 `desktop.child_window.context` 取深複製。⚠️ 外殼要先取到脈絡再掛載（`v-if="ready"`，VUE-9）。
- **主視窗上鎖**（`PosDesktopLock`）：`inert` 擋不住滾輪與捲動鍵，要另外攔截（VUE-8）。解鎖靠關窗事件，取消、Esc、按 X 三條路都要走到，否則主視窗永久鎖死。
- **關窗通知 `saved`**：子視窗開著時主視窗是鎖住的，所以 bridge 收到任何 `ADMIN_ACTIONS` 動作就以 `begin_write`／`end_write` 包住，按 X 關窗也會通知主視窗重讀；否則批次建立後按 X，主視窗看不到新款式。⚠️ 寫入途中按 X，關窗通知延到寫入結束才發（`_close_pending`），先發會讓主視窗讀到尚未提交的資料、提交後又沒人通知。⚠️ 子視窗的 Esc 處理要先看 `event.defaultPrevented`（確認框已吃掉這次 Esc 就不關窗），送出中也不關窗。
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
- **條碼寬度**：Code128 不縮放，自行依單位圖樣畫成整數點寬並置中（縮放會使同寬的條印成 1 或 2 點，長碼掃不到）。條碼區 296 點（左右各留 12 點兼靜區）；每單位優先 2 點（≤148 單位），否則 1 點（≤296 單位），再長就不能印：加入清單時 `printing.check` 回 `too_long` 擋下，預覽與列印也擋。單位數＝11×（碼字數＋3）＋2，約 24 個英數字元以上印不出來。
- **張數上限**：每列 99 張（`printing_service.MAX_COPIES` 與 `labels.js` 的 `MAX_COPIES` 須同值），重複加入累加也不得超過。
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
- **換檔兩次改名都失敗**：原 `POS.exe` 已不存在時不刪 `.new`，error.log 寫明手動把 `.new` 或 `.old` 改回 `POS.exe`。
- **升級前備份**：版號低於 `MIN_SUPPORTED_VERSION` 先拒絕、不備份；介於最低支援版與 `SCHEMA_VERSION` 之間才先備份 `backups/pos_upgrade_{時間}_v{舊}-v{新}.db`（留 5 份）；失敗就停止啟動。⚠️ 唯讀開檔用 `Path(...).resolve().as_uri() + "?mode=ro"`，自己拼路徑遇到 `#`、`%` 會開錯檔。

### 開發時啟動

雙擊根目錄 `launch_pos.cmd`：以 Python 3.12 的 `pythonw.exe` 跑 `main.py`，不開命令視窗；開的是根目錄的 `pos.db`。

### 標準資料庫（`tools/make_standard_db.py`）

把 v24 資料庫的「商品與設定」轉成最新格式的新檔（母版）：營業紀錄一律不帶，庫存全為 0。來源以唯讀連線開啟，輸出檔已存在就停止。

- **轉換**：`CategoryField` 併入 `AttributeField`（啟用取兩者皆啟用），保留原主鍵與 `sqlite_sequence`。單一交易寫入、`defer_foreign_keys`，commit 前跑外鍵檢查，再以 `check_variant_attributes` 核對單選單值與欄型別。⚠️ 每張資料表與設定鍵都要歸在「複製」或「不複製」清單，未歸類就停止；`payments` 不複製、依種子預設（來源母版還是舊的「行動支付」清單）；`next_store_barcode` 必須帶，否則新條碼會撞號。
- **對帳**：轉完逐表比對資料列、設定鍵與流水號，經正式 `init_db` 再開一次，最後核對來源雜湊值未變。任一步失敗都刪掉輸出檔（含 `-journal`）。
- **來源結構凍結**在 `tests/fixtures/schema_v24.sql`，測試用它造 v24 來源。
- **母版**：v24 原始檔 `docs/standard_db/pos_standard.db`（轉換來源，不要刪）與轉出的 `pos_standard_v28.db`（`docs/` 不入庫，換機器要自行複製）。`DayClose`、`Correction` 屬營業紀錄，不複製。結構有改就重新轉出：

```
python tools/make_standard_db.py --source docs/standard_db/pos_standard.db --output docs/standard_db/pos_standard_v28_new.db
```

- **清乾淨 `pos.db`**：關掉 POS，複製母版換名成 `pos.db`。

### UI 風格規範

定義於 `static/css/pos.css` 檔頭，換色時全域搜尋。

- **色票**：背景 `#f2f2f7`｜元件底 `#fff`｜主文字 `#1c1c1e`｜次要 `#636366`｜佔位／停用字 `#aeaeb2`｜邊框 `#c6c6c8`｜hover 底／停用底 `#e5e5ea`｜pressed 底／停用框 `#d1d1d6`｜強調 `#8fa8c8`｜危險 `#e74c3c`。主要鈕顏色以 `pos.css` 的變數為準。
- **基本**：圓角 8px；焦點 2px 藍灰框且不位移版面；一般鈕 `min-width: 80px`，小型鈕（`.btn-sm`、chip、表格操作鈕）歸零。
- **小解析度標準 1280×750**：實際筆電 1920×1200 縮放 150%。用資料庫副本開視窗，移到 (0, 0)、設為 1278×720，以 DOM 量測為證據，確認外框沒超出、控制項沒被擠窄、表格下方內容沒被推出畫面。
- **表單對齊**：標籤（固定寬 `--label-w`）＋輸入框兩欄 grid，同表單對齊同一垂直線；巢狀框內覆寫 `--label-w`。對話框標籤欄用 px，em 會跟提示字級縮水（文字繼承見 VUE-13）。
- **固定欄寬表格**：掃入明細用 `.cart-table`／`.fixed-table`（`table-layout: fixed`，欄寬用 `col-wNN`）。⚠️ 只設 `min-width` 掃入後照樣跳（VUE-22）。
- **紀錄查詢列表**：右側 `.records-panel` 寬度上限 980px（1278 寬視窗下立帳表放得下五種付款方式；⚠️ 預設視窗 1124 寬時四種就會出現橫向捲軸，約需 1200）。`.records-table` 只留橫線、固定欄寬、數字靠右；列尾 `.records-toggle` 顯示項數，展開時轉白底藍灰框。展開中的單以 `tr:has(.records-toggle.on)` 認出（四種紀錄共用、不必改模板），主列與明細同一塊 `--accent-soft` 底、左側 4px `--accent` 條，明細 `.records-detail` 是白底圓角小卡，明細格底下 5px 元件底色把相鄰兩筆展開的單隔開。收銀明細欄為「原價／實售價／小計」，實售價＝原價－單項折扣÷數量（前端換算，資料仍存折扣金額）；表尾一行算式「商品小計 − 整單折抵 − 會員折扣 = 應收」，只列有發生的折抵，全無折抵只寫「應收 ○」。主列折抵金額帶減號，0 顯示「—」。明細，商品欄佔剩餘寬度並允許折行，不截斷（品名＋規格中位數 30、最長 67 個半形字）。盤點按鈕的項數是明細筆數，差異數另留「差異項數」欄。單號欄寬 80（放得下六位數，56 只夠三位數會折行）。收銀合計列固定兩行：第一行筆數與總額，第二行 `.pay-breakdown` 依設定清單順序固定列出每種付款方式的筆數與金額（沒交易列 0，清單外的舊付款方式排最後）；總額未扣退貨。
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

全專案走查清單與紀錄方式見入庫的 [tests/WALKTHROUGH.md](tests/WALKTHROUGH.md)。只有維護者明確要求走查才執行；修改、推送、進版、打包與發布均不自動觸發，也不以完整走查作為發布前置條件。依2026-11-08實測縮為7項複雜UI互動，每項選一個代表流程；已正常且未受改動影響者沿用，已知未修問題不重現，不重跑例行操作或等待閒置逾時。日常必要驗證與完整自動測試規定不變，走查結果另行記錄。

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
| v0.2.1 | **每日立帳、作廢銷售單與單據更正，schema v28**。①銷售單加帳日，紀錄查詢的日期、合計、匯出改看帳日；v27 資料庫開啟時自動升級。②每日立帳（限管理者），立帳後該帳日的銷售單鎖住，沖帳只能走退貨。③作廢銷售單（限管理者、未立帳、尚無退貨單），原單保留、庫存沖回、不計入合計。④更正付款方式與修正操作者（銷售、進貨、退貨、盤點），每次留更正紀錄。 |
| v0.2.0 | **會員、退貨返修與資料庫正規化，schema v27**。①會員名單、收銀查詢會員、紀錄查詢顯示會員電話。②收銀實售價欄、折數按鈕，付款方式改轉帳。③退貨返修流程與返新進貨，返修改為退貨單整張種類。④建立進貨單，庫存異動有明確來源；規格欄直接隸屬種類。⑤v27 以前的舊資料庫一律拒開。⑥修正大量收銀、盤點、返修、連點與焦點問題。 |
| v0.1.4 | **紀錄查詢改版與收銀折扣，schema v21**。①紀錄查詢改橫線表格、項數鈕與明細小表格，可多筆展開，依類別預設日期。②收銀新增會員折扣、小計列與純數字金額欄。③明細加適用機型、補售價併入主程式、訂小解析度標準。④商品資料庫售價併為一欄、操作欄僅管理者顯示、條碼全部列出。 |

本節只留最近三個版本；**被擠掉的逐版記錄全部在 [HISTORY.md](HISTORY.md)**，進版時把被擠掉的那一列搬過去。
