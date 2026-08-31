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
| `lib/db_seed.py` | 付款方式種子、新種類預設欄清單(規格欄由各種類自建,種子不建欄) |
| `lib/product_rules.py` | 共用商品規則(`FIELD_TYPES`、欄位型別驗證、自取碼取號) |
| `lib/variant_batch_service.py` | 子產品批次建立與唯讀預檢（共用 `_validate_batch`，`dry=True` 不寫入任何資料） |
| `lib/backup.py` | GFS 備份(日7/週4/月12) |
| `lib/label_printer.py` | NIIMBOT B1 協定與序列埠溝通(自動尋埠、送圖、錯誤轉譯) |
| `lib/label_layout.py` | 商品標籤版面繪製(純函式,輸入四個字串輸出 Pillow 影像) |
| `lib/*_service.py` | 正式 Facade／Service／Repository 應用層與資料存取實作 |
| `static/` | `index.html` + `css/pos.css` + `js/*.js`（Vue 3、DesktopBridge 包裝、各頁邏輯） |
| `static/variant_editor.html` | 款式修改子視窗頁面（獨立 Vue app，共用 `attrfields`／`modelpicker`／`optpicker` 元件） |
| `static/variant_batch.html` | 新增款式子視窗頁面（`variant_batch_window.js` 外殼＋組合輸入區與批次工作表樣板） |
| `static/js/variant_batch_logic.js` | 新增款式的純邏輯（組合展開、算式、條碼展開規則、欄寬計算、預檢結果分流）；不碰 API，可單獨以 Node 測試 |
| `static/field_editor.html` | 規格選項子視窗頁面（`field_editor.js`；設定頁自訂規格列的「✎ 選項」由此開窗，只管選項清單與建檔預設帶入值） |
| `static/js/pos_shared.js` | 主視窗與子視窗共用的全域 mixin（`guard`／`guardReload`／`attrText`） |
| `static/js/optpicker.js` | 規格值候選選取器 `opt-picker`／`tag-selector`（前排常用值＋搜尋＋當場新增；檔頭寫明三種輸入公版的分工） |
| `static/js/combobox.js` | 可搜尋下拉 `combo-box`（從既有主檔挑一筆，可新增未收錄的值） |
| `static/js/sortable.js` | 拖拉排序清單 `sortable-list`（⠿ 拖曳＋序號格搬位；`auto-save` 決定拖完直接寫入或交給該區塊的儲存鈕，`disabled` 供清單被過濾時停用排序） |
| `static/js/confirm.js` | 確認／通知視窗公版 `PosConfirm.ask()`／`notify()`（取代瀏覽器內建 confirm／alert） |
| `static/css/dialog-theme.css` | 子視窗對話框外觀（`.dialog-*` 公版，沿用 §2 UI 風格色票） |
| `tools/bump_version.py` | 進版工具(改 `version.py` + 產 `version_info.txt`) |
| `tests/` | 單元測試（`tests/base.py` 共用 `ConnTestCase`／`FacadeTestCase` 與 fixture helper） |

## 2. 慣例

- **庫存採異動流水制**:不存「目前庫存」欄位,一律由 `StockMovement` 加總取得（`lib/product_data.py:stock_of`）。`kind` 為 `purchase`(進貨,+)、`sale`(銷售,-)、`adjust`(盤點調整,±)。
- **金額一律 int**:新台幣元,無小數;數量亦為 int。
- **商品結構**:`Category`/`Brand`/`PhoneModel` 為正式資料表;`Product`(款)以 `category_id`/`brand_id` FK 掛種類/廠牌;`Variant`(變體)以 `VariantModel` 多對多掛適用型號(共用款可掛多筆型號);規格欄 `AttributeField` 為欄位主檔,以 `CategoryField` 掛種類(一律一個欄只掛一個種類,見「新種類的預設模板」);`AttributeOption` 無 parent 連動。
- **規格值正規化**:變體規格不再存 JSON,改由 `VariantAttribute(variant_id, field_id, option_id?, text_value?)` 關聯表承載(`CHECK` 約束 `option_id`/`text_value` 恰一非 NULL:select 欄存 `option_id`、text 欄存 `text_value`)。Desktop action 以 `attributes:{欄名:值}` dict 進出，讀寫由 `lib/product_data.py` 的 `set_variant_attributes`/`attrs_by_variant` 在 dict 與關聯列間轉換（讀取一次 JOIN 撈齊避免 N+1）。因此**改欄名/改選項值即生效**,不需回掃變體;寫入時 select 值查無對應選項會回傳 validation error。
- **規格選項生命週期**:`AttributeOption.active` 只控制新增選單可見性。有 `VariantAttribute` 引用時刪除會清除預設選項與限定型號後設為停用,保留既有商品關聯;0 使用中才硬刪除。設定頁重新加入同欄位、同值的停用選項時會恢復原 `option_id` 並重新啟用;商品建檔流程自動補選項時不會重新啟用。`options.list` 的 `usage_count` 為引用該 `option_id` 的 distinct `variant_id` 數量。
- **選項限定型號**:`OptionModel(option_id, model_id)` 記錄選項只在特定型號出現(特別色)。`options.list` 搭配 `field_id`／`model_ids` 過濾時回傳「未綁任何型號的 ∪ 綁定含任一給定型號的」聯集,僅過濾建檔下拉,不回溯既有變體;未提供 `model_ids` 時回全部。`options.set_models` 以 `id`／`model_ids` 全量替換限定型號清單(空清單=改回通用)。
- **條碼混合**:`source` 分 `factory`(廠商既有)與 `store`(店內自取碼,`TL` + 流水號,`Setting.next_store_barcode` 純計數、刪除不回收);手動輸入 `TL` 開頭一律 422(系統保留字頭)。
- **自取碼交易語意**:`lib/product_rules.py:next_store_barcode` 使用呼叫端的同一條資料庫連線更新計數器,由呼叫端決定 commit;商品或條碼建立失敗造成 transaction rollback 時,計數器亦一併回復。
- **關鍵輸入驗證**:進貨數量與盤點掃描數量須大於 0,盤點實數不得小於 0;結帳單品折扣不可超過品項小計、總額不得為負,付款方式須存在設定清單。規格欄型別統一由 `lib/product_rules.py` 驗證。
- **盤點結案防重**:結案先以 `status='open'` 條件原子更新盤點單;不存在回 404,已結案回 409,避免重複產生 `adjust` 庫存異動。
- **有效售價**:`Variant.price` 不為 NULL 時採用,否則退回 `Product.default_price`,兩者皆 NULL 則售價為 `null`。
- **同名欄去重提醒**:`AttributeField` 沒有 `category_id`,同名欄可能有多份(各種類一份);SQLite 的 `UNIQUE` 也不套正規化,故去重不能靠資料庫唯一鍵,一律先以「欄名＋型別＋種類」查再插。
- **設定頁模板列與型號**:見下方「固定列：適用型號與特性詞條」「規格欄的移除」「新種類的預設模板」三節。
- **商品資料庫的廠牌篩選**:廠牌下拉只列「目前種類真的有商品」的廠牌(`brands.list` 帶 `with_products`,以 `Product.brand_id` 判斷,不看 `BrandCategory` 掛勾)。掛了種類卻一件商品都沒有的廠牌選了只會查無資料。切換種類時重撈清單,原本選的廠牌若不在新清單內自動清成「全部廠牌」。設定頁的廠牌歸屬仍走原本的掛勾清單,不受影響。
- **商品資料庫搜尋**:關鍵字以空白切成多個詞,採 **AND**(每個詞都要命中才算符合),大小寫與全半形以 `casefold` 正規化。比對範圍為商品名稱、種類、廠牌、款式的規格值(含 multi/tags 的每個值)、適用型號與條碼,命中時只保留符合的款式列。查無啟用中資料時另查一次停用資料,回報筆數並提供「顯示已停用」入口,不直接把停用商品混進結果。條碼只針對當頁款式查詢(`variant_id IN (...)`),不整表撈。
- **Schema 與 migration**:`lib/db_schema.py` 是現行 schema DDL 的唯一來源；`lib/legacy_migrations.py` 封存 v1–v13 的歷史 migration DDL。未來修改現行 schema 時，仍須依 migration 規則新增升級步驟，讓既有資料庫可安全演進。

### 子視窗(pywebview 款式修改／新增款式)

商品資料庫頁的「編輯」與「新增款式」都不開網頁對話框,而是由
`ChildWindowCoordinator` 建立**唯一一個** pywebview 子視窗。理由:規格、型號、
售價、條碼這些內容在單一網頁對話框裡會逼出巢狀彈窗與捲動層層相疊,違反 UI 從簡;
子視窗可獨立調整大小、由作業系統管理焦點。

**頁面白名單**:`CHILD_PAGES` 決定可開啟的頁面(`variant_editor`／`variant_batch`／`field_editor`)
與各自標題、尺寸。前端只送 key,**不送檔名**,避免任意本機檔被載入。

**尺寸與位置**:`CHILD_PAGES` 的預設尺寸開窗前會被 `fit_size()` 夾進工作區
(寬留 60、高留 80 給工作列與標題列,不低於 `min_size`),位置由 `fit_position()` 算成
水平置中、垂直取剩餘空間 1/3(中央偏上)。工作區以 `screen_work_size()` 實測並換算
邏輯像素——1920×1080 在 125% 縮放下可視高度只有約 816,寫死 820 會開到畫面外。

**唯一性與脈絡**:協調器以 `RLock` 保護狀態,已開啟時再按只 `restore()` 既有視窗,
不會開第二個(編輯與新增也因此不會同時開)。子視窗自己不帶查詢參數,改由
`desktop.child_window.context` 向協調器拿主視窗開窗時傳入的脈絡副本
(`copy.deepcopy`,子視窗改不到主視窗資料)。子視窗有自己的 `DesktopBridge` 實例,
共用同一個 Facade。

**新增款式的入口與脈絡**:工具列的「新增款式」開空脈絡(子視窗內自己選種類與產品);
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

### 新增款式的組合展開與批次工作表

新增款式子視窗(`variant_batch`)由「一次填一筆、加入預覽」改為
**組合輸入＋批次工作表**。原流程視窗一路往下長、看不出各筆差異、
要複製修改只能重填一次;實際使用最常見的是兩個規格交叉
(例如線的顏色×長度)。

**組合展開只認單值規格(select)**:候選區標「可複選以產生組合」,
勾第二個值的當下即進入展開(不設欄位級開關——主場景每次都要交叉,
多一個開關就是每次多一步),該欄的欄名下方顯示「已選擇 N 個（欄名）」徽章
(放欄名側而非輸入框下,避免出現／消失時把輸入框整排往下推),
底部常駐算式「3 個顏色 × 2 個長度＝6 筆」,刪回一個值自動退出。
`multi`／`tags`／適用型號**不參與展開**:它們本來就代表同一筆資料的多個值,
若也拿來交叉會與「產生多筆」語意混淆,欄旁固定標「套用至每一筆」。
⚠️ 空乘積為 1:沒有 select 展開軸(種類只有 text/multi/tags,或 select 全空)
時仍產生一筆,寫成 0 會讓這些種類一筆都建不出來。
預計筆數超過 30 先確認再展開——4 色×5 型號×3 長度瞬間就是 60 筆。

**條碼展開規則(寫死)**:原廠條碼只在預計產生一筆時自動帶入,
多筆時該欄停用並提示「請於預覽表逐筆掃描」;自取碼可套用至每一筆,
號碼由建立時逐筆產生;複製列時規格與售價照複製、**原廠條碼一律清空**、
自取碼勾選保留。這是批次建檔最容易把同一組條碼灌進多筆的地方。

**儲存格底色只表示「這格要修」**。原本另有一套淡藍底標示「這欄各列的值不一樣」
(差異軸),與紅色錯誤底色搶同一個視覺通道:把型號補好之後紅底退場、藍底補上,
看起來像在亂跳。差異提示的資訊量低(看內容就知道哪幾欄不一樣),整套移除
(`cell-diff`、`diffFields`、`diffFieldNames` 皆已刪除)。
整列紅底(dialog 公版的 `tr.invalid`)同樣不用:整列刷紅時已填好的欄位也是紅的,
像每一格都有問題。現在規則只有一句——**有紅就是要修,沒紅就是可以建**:
出錯的那一格紅底＋底下紅字說明,狀態欄再寫一次;
只有「與其他列重複」這種列層級的問題標在列上(上下紅框線)。

**預覽表的列號與狀態欄**:表頭標題「產品組合預覽（共 N 筆）」,最左固定一欄
列號「#」(44px)。列號不是裝飾——重複的狀態寫成「與第 N 筆重複」,沒有列號
使用者得自己從頭數;該句同時做成可點連結,點了把對方那列捲到畫面中央並閃一下
(對方已被刪除時不給連結,免得點了沒反應)。狀態欄本身不重要,欄寬壓到 96～108px
並改成表格中唯一允許折兩行的欄。序號、操作鈕、狀態三欄的內容置中,表頭欄名全部置中。
⚠️ 置中要寫成 `.batch-table .dialog-table td.xxx`:dialog 公版有一條
`.dialog-table td { text-align: left }`,只寫單一類別權重不夠會被壓回去。

**產生預覽後不自動收合組合輸入區**:原本按下「產生預覽」會自動收起輸入區、
把畫面切到預覽表;維護者裁示交給使用者自己按標題列切換。

**就地編輯與固定編輯區**:售價、條碼、單選規格直接在儲存格改;
多值規格、特性詞條、適用型號點格後在**表格上方的固定編輯區**改,
不再開巢狀彈窗(原本的「修改」popup 已移除)。理由:那些欄位要能新增選項、
重新啟用停用值、依型號過濾候選,塞進儲存格會變成又小又難操作的編輯器。
Escape 優先關固定編輯區,編輯區沒開才關子視窗,避免誤關丟掉整批工作表。

**版面:垂直整頁捲、水平只捲表格**。改版初期輸入區與預覽表各自垂直內捲,
維護者實際使用後指出兩層捲動容易迷路,已改回 dialog 公版的整頁垂直捲動
(`.batch-content { overflow-y: auto }`),輸入區與表格都不再垂直內捲;
底部動作列在捲動區之外,照樣永遠可見。
水平仍留在表格自身(`.batch-table { overflow-x: auto }`)——欄寬超出視窗時
若讓整頁橫捲,標題、產品摘要、輸入區會跟著左右跑。
⚠️ **代價:表頭釘不住**。`position: sticky` 只認最近的捲動容器,表格外框
為了橫捲仍是捲動容器、但垂直不捲,所以表頭會跟著頁面捲出畫面
(實測捲到底時 `th` 的 top 由 277 變 −758)。要同時保有整頁縱捲與釘住表頭,
只能讓表格自己垂直捲(回到兩層捲動)或另外寫 JS,目前選擇不做。
`.batch-table` 垂直用 `overflow-y: clip` 而非 `hidden`、`.batch-preview` 用
`overflow: clip`:`hidden` 會讓它們變成捲動容器,又長出第二層。

**欄寬:依內容計算、可拖曳、依種類記住(僅本次執行)**。原本五個欄位寫死 px、
其餘均分並一律省略號,長值(手機殼款式「黑色巨牆磁吸支架(附掛環扣)」、
行動電源產品型號 22 個半形寬)固定被切。現在由 `<colgroup>` 指定寬度,
寬度取「欄名與該欄實際值的最大字寬＋內距」,並依欄位型態加下拉箭頭等佔位;
表頭右緣可拖曳調整、雙擊還原自動寬。
⚠️ **字寬要實際量,不要估**:估算(半形 7.5px)對中文夠用,但英數在 11pt 約
8.2px,「抗AR膜360度」這種中英混排會低估而被切。實作以 canvas `measureText`
搭配儲存格的實際字型量測,`variant_batch_logic.js` 的半形估算只是無 DOM 時
(Node 測試)的退路。
適用型號欄整串最長需要約 380px,不切實際,維持上限 240px＋省略號,
滑鼠移上以 `title` 顯示完整清單;型號有別名時本來就優先顯示別名。
子視窗初始寬度因此由 980 提高到 1220(最小寬 940):實測最寬的鋼化玻璃
欄寬合計約 1141,1920×1080＠125% 的工作區放得下。
欄寬記在 `localStorage`(key 依種類)。⚠️ pywebview 預設私密模式,
WebView2 資料夾是暫存的,**關掉程式再開就會清空**,等於只在本次執行期間有效;
維護者裁示如此,不為此關閉私密模式或改存資料庫。

**組合輸入的標籤欄比 dialog 公版寬**:公版 `.dialog-shell .attr-row` 的標籤欄寫死 7em,
放不下「已選擇 N 個○○」徽章會折行;改成吃 `--label-w`,組合輸入區給 8em。

**組合輸入的售價／條碼列**:條碼欄的「請於預覽表逐筆掃描」提示會隨筆數
出現與消失,提示改為絕對定位、該排預留一行高度,否則底部對齊的版面會被
推動(PITFALLS VUE-19)。

### 子產品批次建立的唯讀預檢與錯誤契約

展開當下就要能標出「這一列有問題」,但重複判定必須與建立時完全一致——
若前端自己重做一套簽章規則,兩邊遲早會不一樣。因此新增唯讀預檢 action
`variants.batch_precheck`,payload 與 `variants.batch_create` 完全相同,
**共用同一段驗證程式**(`VariantBatchService._validate_batch(payload, dry)`)。

**唯讀由構造保證**:`dry=True` 時 `_resolve_option()` 不 INSERT 也不 UPDATE——
命中既有選項(含停用)回原 `option_id` 但不重新啟用,沒命中回
`("new", normalize_key(value))` 當簽章佔位。佔位可雜湊、同批同欄同值穩定相等,
因此批內判重照樣正確,而資料庫不會被寫進任何一筆。有專測驗證跑完預檢後
Variant／AttributeOption／Barcode／VariantAttribute 筆數完全不變。
product 層級失敗(產品不存在或已停用)仍照 `_require_product()` raise——
那不是某一列的問題,沒有 per-row 結果可回。

**結構化錯誤契約**(預檢與建立共用):每項錯誤是
`{code, field_id, message, related_variant_id, related_draft_id}`,
前端據此標到列與欄,不解析訊息字串。codes:`unknown_field`／
`select_multi_value`／`missing_required`／`missing_models`／`model_not_found`／
`store_prefix_barcode`／`duplicate_signature`／`duplicate_barcode`／`missing_barcode`。

**每筆至少要有一組條碼**:沒有原廠條碼就得勾「配自取碼」,否則回 `missing_barcode`
(顯示在條碼欄)。做成**軟性錯誤**而非結構性錯誤——嚴格批次是全有全無,等於擋住;
`tolerant_create`(匯入舊資料)不受影響,舊資料本來就可能沒碼。
款式修改視窗走另一個服務,既有的無條碼子產品照常編輯儲存。

**錯誤訊息寫在窄欄裡,不重述欄位自己看得到的東西**:訊息會顯示在該儲存格下方
(條碼欄寬約 165px、狀態欄僅 96～108px),所以是「此條碼已存在」而非
「條碼「4711…」已存在」、「「顏色」未填」而非「必填規格「顏色」未填」、
「需指定型號」而非「此種類須指定適用型號」。
⚠️ 批內重複以**首見筆的 `draft_id`** 回報,不用陣列索引:前端會略過、刪除、
複製列,索引會指到別筆;「與第 N 筆重複」的 N 由前端依目前顯示順序換算。

**重複列的處理**:與既有款式重複的列不進可提交清單,改列入「已略過 N 筆」
摘要(可展開看規格與對應既有款式),不計入建立筆數,**不能強制建立**。
既有款式若是停用或待處理筆,`catalog.list` 查不到它,摘要會說明狀態與
處理入口——只給一個款式編號使用者無從處理。

**交易語意不變**:`variants.batch_create` 仍是全有全無,任一錯誤整批不寫入;
`tolerant_create`(匯入等容錯路徑)只共用解析結果,忽略嚴格建立用的業務錯誤,
維持原本逐問題寫 `VariantIssue` 的行為。

### 固定列：適用型號與特性詞條(種類設定)

`Category.model_mode` 只有 `required`／`hidden` 兩種:`required`＝該種類的款式**必填**適用型號,
`hidden`＝該種類不使用型號。設定入口只有一個——設定頁「規格項目」清單最上面的
**固定列「手機型號」**,右側開關切換(`static/js/settings.js` 的 `MODEL_ROW_ID`)。
型號實際存於 `VariantModel` 關聯表,不是規格欄,故以固定列呈現而非真的建 `AttributeField`
(真的建成規格欄要重做既有型號關聯與型號排序)。

三處畫面一律照這個設定走:新增款式子視窗、款式修改子視窗、商品資料庫的型號欄。
設為 `hidden` 時型號欄顯示「—」,**既有 VariantModel 關聯保留不刪**,重新開啟就會再顯示;
款式修改視窗也不清空既有型號(送出時照原值送回),避免「關掉顯示」變成「刪掉資料」。

特性詞條(`field_type=tags`)**每個種類各自一份同名欄**,選項不互通。
同樣以固定列呼叫,右側開關啟用／停用:啟用就是替本種類建一份
`AttributeField`+`CategoryField`(`fields.create` 帶 `category_id`),停用走一般規格欄的
`categories.delete_field`(先問影響筆數)。理由:各種類的詞條完全不同,
合成共用欄會讓建檔下拉混進別種類的詞條。

⚠️ 因為同名欄有多份,**不可以欄名跨種類找詞條欄**。
`product_data._resolve_field`、`variant_batch_service._feature_field_id`、
`variant_issue_service._feature_id` 一律只認本種類 `CategoryField` 綁定的那一份;
舊版的「按名稱取 field_id 最小的一筆」退路會把詞條寫進別的種類那一份。

### 必填切換只往前生效(既有資料不動)

改必填**不再因為種類已有子產品而鎖住**。規則只約束之後寫入的資料,既有子產品
不停用、不強制補值——零售現場停售等於當下賣不了東西,沒有系統敢這樣做。

`settings_service.set_field` 在 `required` 改變時同步待補清單
(`_sync_required_issues`):改成必填→該種類目前缺此欄值的子產品各補一筆
`VariantIssue.missing_required`(重複不疊加);改回選填→把此欄的該類問題筆刪掉。
建檔仍硬擋必填,修改既有子產品維持軟性(可存,只有原本就是待處理筆才重驗)。
(舊規則「已有子產品的種類暫時鎖住必填切換」已作廢。)

### 產品名稱自動組裝

新增產品的欄位順序是**廠牌→名稱→備註**,焦點落在廠牌;選了廠牌就把
「廠牌 種類」(中間一個半形空格,與既有資料寫法一致)填進名稱。使用者一動手打過名稱
就不再接管(`nameDirty`),此時名稱欄下方改出現「重新自動命名」把控制權還回去。
自動名稱與此種類既有產品撞名時(同廠牌多條產品線),在儲存那一列即時顯示紅字提示,
不等按了儲存才由後端擋。

### 建檔預設帶入值(原「預設值」)

`CategoryField.default_option_id` 的用途是**建檔時自動帶入**(`static/js/api.js`
的 `initFormAttrs`),只對 select 欄有效,入口在規格選項子視窗。
⚠️ 它一度還兼「顯示時省略等於預設值的規格值」,已移除:商品名稱一律完整顯示,
省略會讓店員以為那筆沒填。`categories.set_field` 允許送 `default_option_id: null`
清除(其餘欄位的 `null` 一律視為不動)。

### 規格候選的前排範圍(廠牌→產品→種類)

建檔／修改的規格候選 chip 前排不看種類總次數,改採三層退路(`variants.field_usage` 帶
`brand_id`／`product_id`):**該廠牌用過 → 該產品用過 → 都沒有才退回種類次數前 8**。
服務層在每個選項上標 `lead`／`lead_count`,前端把 `lead` 的值全放前排、其餘收進「更多…」。
理由:規格值常是某廠牌專屬(如 SolidX 只有一家有),用種類次數排會把別家的款式推到最前;
廠牌欄為空的商品(無品牌皮套之類)退回產品仍能收斂。全新產品的第一筆無歷史可用,
只能退回種類次數。

`product_rules.PINNED_OPTION_VALUES`(亮面／霧面／藍光／防窺)為固定次序,一律排最前且
不受次數影響——玻璃貼的鍍膜有店內慣用順序,浮動排序會讓店員每次都要找。

### 型號選取器(model-picker)

外觀與操作**比照規格值的候選選取器**:已選型號在最上、其次搜尋框、再來候選 chip,
點一下切換選取。原本 chip 內包勾選方塊、外面再套一層可收合的廠牌樹,
同一個畫面上兩種複選長得不一樣,店員得學兩套操作;廠牌也不再預設收合。

**前排與「更多…」**:型號上百個,全部攤開太擠。新增 `variants.model_usage`,
規則與規格值的 `variants.field_usage` 同一套:**該產品用過 → 該廠牌用過 →
都沒有則不指定**(前端全部展開)。前排以外收進「更多…（N）」。
已選的型號一律看得到;**搜尋時一律搜全部**,找舊機型不必先按「更多…」。
查詢是兩句 GROUP BY(該種類的型號關聯、該產品或該廠牌的關聯),換產品時抓一次。

⚠️ 兩處與規格值選取器**故意不同**:①搜尋框放在候選區**上面**(型號候選全展開很長,
放下面會被推到看不見);②候選區保留固定高度可捲(`.model-picker { max-height: 200px }`)
——型號上百個,全展開不捲會把整個視窗撐爛。

### 規格欄的移除(設定頁模板列紅色 ✕)

規格欄雖然掛勾在 `CategoryField`,但實務上一個欄只屬於一個種類(見下方
「新種類的預設模板」),故 ✕ 的語意是
**從此種類移除掛勾＋清掉此種類商品填過的值**(`categories.delete_field`);
其他種類不受影響。等到沒有任何種類再用、也沒有任何商品的值,才把欄位本身與其選項
一起清掉(沿用零使用自動清理的慣例)。手機型號與特性詞條為固定列,不給 ✕,改走右側開關。
確認視窗必須顯示影響筆數(`fields.list` 回傳的 `cat_usage_count`),
只寫「無法復原」店員無從判斷。

### 新種類的預設模板

新增種類時自動建「顏色」「款式」兩個規格欄(選填),空白模板讓店員無從下手。
兩個都是**本種類自己一份**。

⚠️ **規格欄一律不跨種類共用**(`db_seed.NEW_CATEGORY_OWN_FIELDS`)。同名欄在不同種類的
詞彙互不相干——手機殼的「天峰藍」不該出現在傳輸線的候選、AppleWatch玻璃的款式不該
混進手機殼的——共用一份會讓建檔候選互相汙染(特性詞條最早就是因此拆開的)。
與其在設定頁教店員分辨哪些欄共用、再給一個切換,不如一律不共用:畫面上不必解釋
「共用」是什麼,規則一句話講完。

既有資料庫由兩個 migration 拆開:v14 `_mig_split_style_field` 拆款式,
v15 `_mig_split_shared_fields` 拆其餘所有跨種類共用欄(顏色、框色、產品型號…)。
搬移規則:每個種類各建一份同名同型別的欄,只複製**該種類真的用過**的選項
(含限定型號與模板預設值),text 欄的值直接改指新欄;沒人用過的選項與原共用欄清掉。
種子(`db_seed`)因此不再建任何規格欄與起始選項,只留付款方式。
匯入工具(未入庫)照同一條規則實作:只重用已綁本種類的欄,查無就新建。

建欄的動作在設定頁建立流程(`settings.js` 的 `attachDefaultFields`),不放在
`categories.create`——服務層自動建欄位會改寫所有測試 fixture 對「空白種類」的假設。

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
- **設定頁「規格項目」清單**：所有欄位就地編輯，一列一行——序號、名稱、型態、✎ 選項、必填／選填、✕、啟用開關。手機型號與特性詞條為固定列，排在最前面、只有啟用開關，其餘控制項不顯示（不要用停用態的灰欄位表達「不可改」，看起來像壞掉）。自訂規格套拖拉排序公版 `sortable-list`（`active-key="cf_active"`），存檔時逐欄寫回 `CategoryField.sort`。列上改動一律即時存檔，成敗都重讀該種類模板，失敗時控制項才不會停在沒存進去的狀態。
- **開關公版**（`.switch`，`pos.css`）：左右兩側寫「不使用／使用」（`.switch-label`，目前狀態加 `.on` 轉黑字）；關閉 `--pressed`、開啟 `--accent`，圓鈕白底。要用 `<button role="switch">` 並給 `aria-checked`，不用 `<div>`（鍵盤不能操作）。新的開關一律套這一組。
- **必填／選填用切換式標籤**（`.chip-toggle`）：關閉＝白底灰框寫「選填」，開啟＝淡藍底主色字寫「必填」，文字隨狀態切換。不可用勾選框（太小、看不出狀態），鎖定時**不轉灰**，只是點不動並以 title 說明原因。
- **輸入控制項三選一，不要再造第四種**：
  - `opt-picker`（`static/js/optpicker.js`）：值域會長大、需要排出常用值的欄位（規格值、特性詞條）。前排該範圍用過的值＋次數、可搜尋、可當場新增；需要 `variants.field_usage`。
    版面由上而下固定四塊：**已選／候選／搜尋框／搜尋結果**。⚠️ 三條規則一起才不會跳版：①已選區最少一行、最多兩行（`.tag-picked`），搜尋結果區保留一行高度、結果多就往下長（`.tag-matches`；維護者裁示不要捲軸，寧可把下面的欄位往下推），空狀態顯示灰字；②候選 chip 被選走後**留在原位只換成已選樣式**（再點一次取消），不從候選區抽走；③複選（`multiple`）點候選後**保留搜尋字與結果、焦點回搜尋框**，讓使用者接著挑同一批的下一個（打「透」連續選透明、透明磁吸），單選才清空。自己打字新增的值一律清空搜尋字（那串字已成為選中的值）。
  - `combo-box`（`static/js/combobox.js`）：從既有主檔挑一筆（廠牌之類），只有搜尋與「新增○○」。右側箭頭畫在輸入框的 `background-image`（與 select 同一個外框）；⚠️ 點擊區不可用 `<button>`——全域 `button` 有 `min-width: 80px` 與 hover 底色，會在框內冒出一塊灰。取得焦點不自動展開清單（會蓋住下面的欄位），點箭頭或開始打字才展開。
  - 原生 `<select>`：選項固定且少（型態、狀態）。
  原本 select/multi/tags 還有一條「候選未載入就退回 datalist／勾選框」的退路，已移除：它會靜默換成外觀完全不同的介面，出事看不出來，現在改顯示「候選載入中…」。
- **提醒條與錯誤條同一種外觀**（`.notice-bar`／`.error-bar`）：圓角方塊＋左側粗線＋淡底深字，前綴 ⚠。未儲存提醒用黃系、錯誤用紅系。⚠️ 錯誤訊息顯示在**觸發它的那個區塊內**（設定頁以 `errorScope` 分辨 category／brands／phoneBrands／models），不要丟到整頁最上面——訊息離發生的地方太遠，店員看不到自己剛按的那顆鈕出了什麼事。不要再自創滿版色條。
- **清單維護的儲存規則**：名稱可即時寫入的區塊（手機品牌、規格項目）連拖拉排序也即時寫入，不放儲存鈕；仍需批次儲存的區塊（廠牌、手機型號）則「名稱修改＋拖過的順序」一起等按鈕，標題列用 `.card-actions` 收「復原／儲存修改」兩顆，並在有未儲存內容時顯示提醒條。⚠️ 會重新載入清單的動作（新增、刪除）必須先把未儲存的修改收起來、做完再貼回（`keepEdits`／`_collectEdits`／`_restoreEdits`），只有被刪掉的那筆會消失；**不可以代替使用者先儲存**，寫入資料庫永遠是使用者按下儲存的事。
- **長清單分組**：手機型號依品牌分組，每組標題右側一顆收合／展開鈕（狀態各組獨立），另有「快速搜尋」比對名稱／別名／系列；有搜尋字串時符合的組自動展開、不符的整組隱藏。⚠️ **過濾狀態下必須關閉排序**（`sortable-list` 的 `disabled`）：排序 API 是把送進來的 id 依序重編 1..N，只送畫面上那幾筆會把其餘項目的號碼全打亂。展開收合套 `.collapse-*` 過場。
- **確認／通知視窗一律用 `PosConfirm`**（`static/js/confirm.js`）：`PosConfirm.ask()`／`PosConfirm.notify()`，白底置中、Enter＝確定、Esc／點遮罩＝取消，破壞性操作的確定鈕用危險色。⚠️ 不可用瀏覽器內建的 `confirm()`／`alert()`——pywebview 會畫成深色系統對話框、貼在視窗上緣，與程式外觀完全不同。
- **對話框欄位列**（`.modal > label`）：標籤欄固定 `--label-w`（px，不用 em——em 會跟著較小的提示字級縮水而對不齊），標籤靠右貼著輸入框；⚠️ `text-align: right` 只給標籤那段純文字，所有元素子節點要覆寫回靠左，否則會一路繼承進候選清單裡。欄位下方的一句提示用 `.field-note`（縮排對齊輸入框左緣），送出前的警告用 `.modal-warn`（與按鈕同列、靠左、危險色）。
- **子視窗外觀**:`static/css/dialog-theme.css` 是子視窗公版。細捲軸(8px、`#c7c7cc`)、
  文字選取反白(`#8fa8c8`)與按鈕 hover/pressed 灰階(`#e5e5ea`／`#d1d1d6`)取自維護者
  另一個專案已調校過的彈窗公版(`PoliceDocSys/lib/theme.py`);該專案的 checkbox 色塊
  刻意不採用(勾選狀態辨識度不足),本專案維持自己的 checkbox 樣式。
- ⚠️ **勿在 v-if/v-else 元素上掛動態 `:key`**:與 prod 版 Vue(`vue.global.prod.js`)的 `stringifyStatic` 靜態節點快取衝突,key 變動重建區塊後快取 vnode 的 DOM 參照被清空,之後所有畫面更新拋 TypeError、整個 app 卡死;dev 版 Vue 測不出來,務必以 prod 版驗證(v0.1.0 後設定頁曾因此崩潰)。內容全走資料綁定即可,不需 key 強制重建。

## 3. 測試

```powershell
python -m unittest discover -s tests
```

目前入庫 584 個測試,涵蓋 schema/migration、Desktop action 契約、屬性/選單庫、規格值正規化(VariantAttribute)、選項限定型號(OptionModel)、商品/變體/條碼、進貨庫存、結帳/銷售紀錄、盤點、備份、標籤版面與列印協定等模組,檔名皆 `test_*.py`。商品資料庫頁的前端邏輯測試會由 Python 呼叫 Node.js 執行；環境缺少 Node.js 時該測試類別會明確標記為 skipped,其餘 Python 測試仍照常執行。
⚠️ `tests/test_import_excel.py` 與 `tools/import_excel.py` 一樣**不入庫**(直接 import 該工具,
缺檔會 error 而非 skip);兩者仍在使用中的機器上會多跑 13 項,匯入驗收結束後一起刪除。

⚠️ 前端改動的最終驗證一律以真實 pywebview 走查(`RuntimePaths` 指向 `pos.db` 副本＋repo `static/`),
走查腳本**只能操作 DOM**,不可去抓 Vue 元件實例——prod 版 Vue 沒有 `__vue_app__`／`__vueParentComponent`
(PITFALLS VUE-6);版面判斷用 DOM 量測而非截圖(VUE-7)。

⚠️ 標籤列印的測試以替身模擬機器,**通過不等於印得出來**;改動該功能一律另外實機驗證(見 §2 標籤列印)。

## 4. 打包

```powershell
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
Remove-Item -Force POS.spec -ErrorAction SilentlyContinue
pyinstaller --clean --onefile --noconsole --version-file version_info.txt --icon "assets/POS.ico" --name POS --add-data "static;static" main.py
```

本節是完整打包命令的唯一真實來源。上列命令先清除舊 `build/`、`dist/`、`POS.spec`，
再以 `--clean --onefile` 打包 static 與版本資訊，並以 `assets/POS.ico` 作為執行檔圖示；Desktop-only runtime 不需要 uvicorn hidden-import。
`--noconsole` 讓 exe 啟動時不另開命令列視窗（GUI 程式不需要主控台）；因此程式不得依賴
stdout／stderr 顯示訊息，錯誤一律寫入 exe 同層的 `error.log`（`main.py` 已如此處理）。
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
