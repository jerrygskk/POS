# 踩雷速查表（Pitfalls）

依主題分組；每條為「**症狀** → 解法（必要時括註原因）」。寫過的雷再踩會被直接點名。新雷修完隨手補一條；任務對照索引見 CLAUDE.md。

#### VUE：前端（Vue 3 prod 版＋pywebview）

- **VUE-1**: **設定頁（或任一頁）切入即整頁卡死、之後所有畫面更新拋 TypeError** → v-if/v-else(-if) 元素上掛了動態 `:key`，與 prod 版 Vue（`vue.global.prod.js`）`stringifyStatic` 靜態節點快取衝突：key 變動重建區塊後，快取 vnode 的 DOM 參照被清空。**勿在 v-if/v-else 元素掛動態 `:key`**，內容全走資料綁定即可（詳 DEVELOPER §2；v0.1.0 後設定頁曾因此崩潰）。⚠️ dev 版 Vue 測不出來。
- **VUE-2**: **bug 在 harness 瀏覽器／HTTP 模擬下重現不了（或反之）** → 部分崩潰只在「真實 pywebview＋prod 版 Vue」穩定重現。前端改動最終驗證一律用真實 pywebview（以 `RuntimePaths` 指向 pos.db 副本＋repo static 開視窗走查），HTTP 模擬（shim 注入 `window.pywebview.api.invoke`→fetch）只當目視版面用。
- **VUE-3**: **自動走查收不到 JS 錯誤，畫面明明壞了** → prod 版 Vue 的錯誤只進 `console.error`，光掛 `window.onerror` 收不到；收集器要 hook `console.error`＋`window.onerror` 兩邊。
- **VUE-4**: **css／js 改了畫面沒變** → 忘記 bump `index.html` 的 `?v=`；css 與全部 js 共用同一版號，一起 bump。
- **VUE-5**: **快速連點清單／分頁後，畫面停在「前一個」選擇的資料** → 載入函式內多個 `await`，慢回應後到蓋掉新資料。多段 await 的載入函式要加載入序號戳記，寫入 state 前比對仍是最新請求才寫（參考 `settings.js` `loadCategoryDetail`）。

#### PS：PowerShell 5.1／環境

- **PS-1**: **前端檔改完出現亂碼或多出 BOM** → PS 5.1 的 `Set-Content -Encoding utf8` 會塞 UTF-8 BOM；改檔一律用編輯工具，勿用 PowerShell 寫檔。
- **PS-2**: **多行 commit 訊息 subject 黏進 `@` 或整段變一行** → PowerShell here-string 所致；多行 commit 一律 Bash heredoc（`git commit -F - <<'EOF' … EOF`）。
- **PS-3**: **內嵌多行 python 指令失敗／中文輸出亂碼** → 多行 python 寫 scratchpad 檔再跑；中文輸出寫 UTF-8 檔再讀。

#### SQL：SQLite

- **SQL-1**: **共用欄（`category_id` NULL）出現重複列** → SQLite 的 `UNIQUE` 對 NULL 不視為相等，去重不能靠唯一鍵，應用層先查再插（DEVELOPER §2）。
- **SQL-2**: **交易中設 `PRAGMA foreign_keys` 沒效果** → 該 PRAGMA 在交易內是 no-op。migration 一旦 OFF，同交易後續（含 seed）FK 都是關的；要保護不能「seed 前開回 ON」，改在 commit 前跑 `PRAGMA foreign_key_check` 驗證，有違規就 rollback。
- **SQL-3**: **偶發 `database is locked` 直接報 500** → 另一條連線（如自動備份 `.backup()`）與寫入撞上，SQLite 預設不等待。`get_conn` 統一設 `PRAGMA busy_timeout=3000` 讓它自行重試；連線一律走 `get_conn` 單一來源，要加 PRAGMA 集中改一處。

#### PKG：打包（PyInstaller onefile）

- **PKG-1**: **打包後 static 404／找不到資源** → onefile 執行時解壓至 `sys._MEIPASS`，`__file__` 推算的路徑失效；`api/__init__.py` 的 `_static_dir()` 已處理（`sys.frozen` 判斷），新增打包資源比照辦理（DEVELOPER §4）。
- **PKG-2**: **打包版雙擊完全沒反應、連 log 都沒有** → onefile 開機先把整包解壓到 C 槽 `%TEMP%`（可達上百 MB），發生在任何程式碼執行之前（bootloader 階段），自家錯誤處理攔不到也留不下紀錄；排查時先確認 C 槽可用空間。
- **PKG-3**: **`tools/build.ps1` 執行被擋／清除指令靜默失敗** → Bypass 被權限分類器擋，直接跑等效 pyinstaller 指令（DEVELOPER §4）；清除步驟用 PowerShell 語法，Git Bash 不識別 CMD 的 `del`/`rmdir` 會靜默失敗。
- **PKG-4**: **fresh clone build 失敗（缺 `version_info.txt`）** → 該檔不入庫，先跑一次 `python tools/bump_version.py {現版號}` 產出（DEVELOPER §5）。

#### TEST：測試

- **TEST-1**: **fresh clone 跑測試，`test_import_excel`／`test_import_rules` 失敗** → `tools/import_excel.py` 為一次性工具不入庫，兩支測試相依它，無此檔即失敗屬預期（DEVELOPER §3）；正式匯入驗收後一併移除。
