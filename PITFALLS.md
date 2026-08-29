# 踩雷速查表（Pitfalls）

依主題分組；每條為「**症狀** → 解法（必要時括註原因）」。寫過的雷再踩會被直接點名。新雷修完隨手補一條；任務對照索引見 CLAUDE.md。

#### VUE：前端（Vue 3 prod 版＋pywebview）

- **VUE-1**: **設定頁（或任一頁）切入即整頁卡死、之後所有畫面更新拋 TypeError** → v-if/v-else(-if) 元素上掛了動態 `:key`，與 prod 版 Vue（`vue.global.prod.js`）`stringifyStatic` 靜態節點快取衝突：key 變動重建區塊後，快取 vnode 的 DOM 參照被清空。**勿在 v-if/v-else 元素掛動態 `:key`**，內容全走資料綁定即可（詳 DEVELOPER §2；v0.1.0 後設定頁曾因此崩潰）。⚠️ dev 版 Vue 測不出來。
- **VUE-2**: **bug 在 harness 瀏覽器／Bridge shim 下重現不了（或反之）** → 部分崩潰只在「真實 pywebview＋prod 版 Vue」穩定重現。前端改動最終驗證一律用真實 pywebview（以 `RuntimePaths` 指向 pos.db 副本＋repo static 開視窗走查）；Bridge shim 只供目視版面，**不是正式 runtime**。
- **VUE-3**: **自動走查收不到 JS 錯誤，畫面明明壞了** → prod 版 Vue 的錯誤只進 `console.error`，光掛 `window.onerror` 收不到；收集器要 hook `console.error`＋`window.onerror` 兩邊。
- **VUE-4**: **css／js 改了畫面沒變** → 忘記 bump `index.html` 的 `?v=`；css 與全部 js 共用同一版號，一起 bump。
- **VUE-5**: **快速連點清單／分頁後，畫面停在「前一個」選擇的資料** → 載入函式內多個 `await`，慢回應後到蓋掉新資料。多段 await 的載入函式要加載入序號戳記，寫入 state 前比對仍是最新請求才寫（參考 `settings.js` `loadCategoryDetail`）。
- **VUE-6**: **自動走查腳本一抓元件實例就 `Cannot read properties of undefined`** → prod 版 Vue 不掛 `__vueParentComponent`，`__vue_app__`／`app._instance` 也只在 dev 版才設；靠這些入口存取 Vue 狀態的走查腳本在真實 runtime 一定失敗。走查一律改走使用者真的會碰的 DOM（`setValue` 原生 setter＋派 `input`／`change`、點按鈕與 chip），順帶把事件繫結一起驗到。
- **VUE-7**: **走查截圖抓到的是別的視窗（瀏覽器、桌面）** → `ImageGrab` 依螢幕座標抓，pywebview 視窗沒在前景就抓到前景程式的畫面，維護者同時在用電腦時必然發生。版面判斷改用 DOM 量測（`clientWidth`/`scrollWidth`/`getBoundingClientRect`），截圖只當輔助、不作為證據。
- **VUE-8**: **子視窗開著時主視窗仍被滾輪／PageDown 捲走** → `inert` 只擋點擊與焦點，不擋捲動。要另外攔 `wheel`／`touchmove`／捲動鍵與 `scroll` 並還原位置（`window.PosDesktopLock`，DEVELOPER §2 款式修改視窗）；解鎖事件要涵蓋取消、Esc 與按視窗 X 三條路徑，漏一條主視窗就永久鎖死。
- **VUE-10**: **子視窗表單的排序、狀態欄位讀進來是空的/預設值** → `categories.fields` 與 `fields.list?category_id=` 形狀不同:前者是建檔用的精簡形狀(只有 name/field_type/required/default/options),`sort` 與 `cf_active` 只有後者才有。設定類的表單要用 `fields.list`,別看到「都是規格欄清單」就混用。
- **VUE-11**: **新開的子視窗底部按鈕列被切掉、內容超出視窗卻不出現捲軸** → `dialog-theme.css` 那條 `html, body, #variant-editor, … { height: 100% }` 是逐一列出各子視窗根元素的,漏列的視窗內容區就沒有高度上限,內容一長往視窗外長,`body` 又是 `overflow:hidden`,捲軸也出不來(規格設定視窗 `#field-editor` 踩過)。**新增子視窗時,根元素 id 要一併加進那條規則。**
- **VUE-9**: **子視窗開起來但預選／初始資料全是空的** → 外殼先掛載子元件、才非同步去取脈絡，元件 `mounted` 讀 props 時還是 null。外殼要等脈絡回來再掛載（`v-if="ready"`），或改用 watch 補寫。⚠️ 症狀是「有時對有時錯」，看機器快慢。
- **VUE-12**: **輸入框裡冒出一塊 80px 寬的灰底方塊（滑鼠移過去更明顯）** → 拿 `<button>` 當下拉箭頭／小圖示的點擊區所致：全域 `button` 有 `min-width: 80px`，`button:hover` 又會蓋掉自訂的透明背景。框內的裝飾性點擊區改用 `<span role="button">`，箭頭本身畫在輸入框的 `background-image` 上（與 `select` 同一個外框）。
- **VUE-13**: **候選清單／下拉裡的文字整排靠右** → `text-align` 會繼承。`.modal > label` 為了讓標籤貼著輸入框設了 `text-align: right`，而標籤是**沒有元素包住的純文字節點**，只排除「第一個元素子節點」擋不住（combobox 剛好就是第一個），整個下拉都被帶著跑。要對所有元素子節點覆寫回 `text-align: left`。
- **VUE-14**: **全形逗號批次替換有漏網** → 以「逗號前後是中文」為條件掃描時，`` `已填${subject},${action}後` `` 這種夾在樣板變數之間的逗號抓不到（前後是 `}` 與 `$`）。批次替換後要再掃一次「字串裡同時有中文與半形逗號」的字面值逐一確認。
- **VUE-15**: **清單只顯示部分項目時拖曳排序，沒顯示的那些順序全亂** → 排序 API（`resort`）是把送進來的 id 依序寫成 1..N，畫面被搜尋過濾後只送得出可見的幾筆，其餘項目仍留著舊號碼。**過濾狀態一律關閉排序**（`sortable-list` 的 `disabled`），不要試圖把可見順序合併回完整清單。
- **VUE-16**: **標題列放兩顆按鈕，中間那顆飄到正中央** → `.card-head` 是 `justify-content: space-between`，三個子元素就會被均分。兩顆以上要用 `.card-actions` 包成一組。
- **VUE-17**: **刪除一筆之後，其他還沒儲存的編輯全部被打回原狀** → 刪除完會重新載入清單，等於拿資料庫內容覆蓋畫面。會重新載入的動作要先收起未儲存的修改、做完再貼回（設定頁 `keepEdits`），並剔除已不存在的 id；也不可以「幫使用者先儲存再刪」——那是替使用者做決定。

#### DATA：資料模型

- **DATA-1**: **填在 A 種類的值，跑到 B 種類的欄位去了** → 「特性詞條」這種**每個種類各自一份、名字却一樣**的欄位，不可以欄名到全表找。舊版退路是「按名字找、取編號最小的一筆」，資料庫一旦有第二份同名欄，所有種類都會寫進第一份。一律改成只認本種類 `CategoryField` 綁定的那一份（DEVELOPER §2 固定列）。⚠️ 症狀不會報錯，只會在別的種類看到不該出現的詞條。

#### PS：PowerShell 5.1／環境

- **PS-1**: **前端檔改完出現亂碼或多出 BOM** → PS 5.1 的 `Set-Content -Encoding utf8` 會塞 UTF-8 BOM；改檔一律用編輯工具，勿用 PowerShell 寫檔。
- **PS-2**: **多行 commit 訊息 subject 黏進 `@` 或整段變一行** → PowerShell here-string 所致；多行 commit 一律 Bash heredoc（`git commit -F - <<'EOF' … EOF`）。
- **PS-3**: **內嵌多行 python 指令失敗／中文輸出亂碼** → 多行 python 寫 scratchpad 檔再跑；中文輸出寫 UTF-8 檔再讀。

#### SQL：SQLite

- **SQL-1**: **共用欄（`category_id` NULL）出現重複列** → SQLite 的 `UNIQUE` 對 NULL 不視為相等，去重不能靠唯一鍵，應用層先查再插（DEVELOPER §2）。
- **SQL-2**: **交易中設 `PRAGMA foreign_keys` 沒效果** → 該 PRAGMA 在交易內是 no-op。migration 一旦 OFF，同交易後續（含 seed）FK 都是關的；要保護不能「seed 前開回 ON」，改在 commit 前跑 `PRAGMA foreign_key_check` 驗證，有違規就 rollback。
- **SQL-3**: **偶發 `database is locked` 直接報 500** → 另一條連線（如自動備份 `.backup()`）與寫入撞上，SQLite 預設不等待。`get_conn` 統一設 `PRAGMA busy_timeout=3000` 讓它自行重試；連線一律走 `get_conn` 單一來源，要加 PRAGMA 集中改一處。

#### PKG：打包（PyInstaller onefile）

- **PKG-1**: **打包後找不到 `static/index.html`／前端資源** → onefile 執行時將打包資源解壓至 `sys._MEIPASS`，不能用程式檔位置推算 static；正式 Desktop runtime 由 `RuntimePaths.detect()` 在 frozen 環境指向 `sys._MEIPASS/static`，`DesktopApplication` 再載入本機 `index.html`。新增打包資源比照處理（DEVELOPER §4）。
- **PKG-2**: **打包版雙擊完全沒反應、連 log 都沒有** → onefile 開機先把整包解壓到 C 槽 `%TEMP%`（可達上百 MB），發生在任何程式碼執行之前（bootloader 階段），自家錯誤處理攔不到也留不下紀錄；排查時先確認 C 槽可用空間。
- **PKG-3**: **清除指令靜默失敗** → 清除步驟用 PowerShell 語法；Git Bash 不識別 CMD 的 `del`/`rmdir`，指令可能靜默失敗。完整打包命令只以 DEVELOPER §4 為準。
- **PKG-4**: **fresh clone build 失敗（缺 `version_info.txt`）** → 該檔不入庫，先跑一次 `python tools/bump_version.py {現版號}` 產出（DEVELOPER §5）。

#### LBL：標籤機（NIIMBOT B1，USB 序列）

⚠️ 本組全部是**替身測試看不到、只有接上機器才會現形**的問題。標籤列印改動後，
單元測試全過不等於能印；務必實機印過，且**至少連續印兩張不同商品**。

- **LBL-1**: **所有指令都沒有回應（不是報錯，是完全靜默），像是機器不支援這套協定** → pyserial 開埠預設拉起 DTR，B1 在 DTR 被 assert 時完全不回話。開埠後必須 `serial.dtr = False`。
- **LBL-2**: **走紙正常、指令全回成功、卻印出全白** → ①熱感紙裝反（先查紙向再查程式，症狀與程式錯誤一模一樣）②列印流程漏下 `set_quantity()`，缺它時印字頭不作用。
- **LBL-3**: **列印被截在半途（約六成處），下半部內容連同號碼整行消失** → `end_page_print` 只代表「資料收到了」，此時實體列印仍在進行；太早送 `end_print` 會中止列印。要等狀態裡的頁數累加到張數再收工（明確訊號，不是盲等秒數）。
- **LBL-4**: **印多張只印出一張，然後卡在等待** → 設定張數**不會**讓機器自行重複列印。每一張都要自己送一次頁面（start_page → dimension → 送圖 → end_page → 等頁數）。
- **LBL-5**: **下一次列印被回絕，看起來像卡紙** → 上一個工作未收工（中途失敗直接關閉連線）會讓機器留在忙碌狀態。不論成功失敗都要送 `end_print`。
- **LBL-6**: **連續列印時第二張以後靜默失敗** → ①前一工作收尾期間機器以**回應內容 0**（不是錯誤封包）拒絕新工作，只看回應類型會誤判成已開始，整張圖被丟棄；`start_print` 要重試到它接受 ②開埠時未清除上一輪殘留的回應，舊進度封包被當成本次回應，解析錯亂。
- **LBL-7**: **每道指令固定慢 500 毫秒** → `read(1024)` 會等到收滿或逾時才回，回應只有幾位元組。改成先 `read(1)` 再依 `in_waiting` 收乾。⚠️ 測試替身要模擬 `in_waiting`，否則測不出也會誤擋。
- **LBL-8**: **筆畫密的中文字印出來缺半邊（如「框」少了木字旁）** → 畫字用灰階平滑，細筆畫是灰的，送印二值化時被判成白色而消失。`ImageDraw` 要設 `draw.fontmode = "1"` 關閉平滑。⚠️ 螢幕上看圖永遠正常，只有紙上看得出來；筆畫少的字沒事，用簡單測資會漏掉。
- **LBL-9**: **機器回錯誤封包（type 219）** → 錯誤碼實測：`1` ＝上蓋開啟、`8` ＝卡紙／送紙異常。要翻成可行動的中文訊息，否則店員只看到「沒有回應」會去重插 USB 線，方向完全錯誤。
- **LBL-10**: **COM 埠會變** → 同一台機器換 USB 孔或換電腦就變（實測 COM3 → COM4）。一律以 VID/PID `3513:0002` 自動尋埠，不可寫死。

## VUE-18 flex 高度鏈被同層規則壓住,內部容器捲不動

**症狀**：子視窗內容超出可視高度時被直接裁掉,沒有捲軸,下半部欄位與按鈕
完全按不到。加了 `overflow-y: auto` 到內部容器仍然無效。

**根因**：容器自己「不能收縮」。`.batch-content > .dialog-section { flex: 0 0 auto; }`
是兩個 class 的選擇器(0,2,0);後面單獨寫 `.batch-input { flex: 0 1 auto; }`
只有一個 class(0,1,0),優先權較低,被前者蓋掉,section 維持 `flex-shrink: 0`。
section 不收縮 → 內部 body 拿到的高度等於完整內容高度 → `overflow-y: auto`
永遠沒有可捲區間,規則形同虛設。

**解法**：改用同等或更高優先權覆寫,例如 `.batch-content > .batch-input`;
並確認整條鏈每一層都有 `min-height: 0`(flex 子項預設 `min-height: auto`
會撐開父層)。

**驗證方式**：不要只看畫面猜。以真實 CSS 建一份最小重現頁,量
`getComputedStyle(section).flexShrink` 與 `body.scrollHeight > body.clientHeight`;
再把舊規則壓回去確認會壞,雙向對照才算證實。

**相關**：VUE-11(子視窗根元素漏加進高度規則)。同一組症狀的不同成因:
VUE-11 是根元素沒有高度上限,VUE-18 是中間層不肯收縮。
