# 給接手者（Claude／Codex 請先讀這節）

## 通則在維護者的全域設定

跨專案共通的規則（回覆風格、先計畫再寫 code、編碼、測試兩階與資料安全、權限 gate、Git／commit／發版通則等）
放在維護者的全域設定 **`C:\Users\user\.claude\CLAUDE.md`**（Claude 與 Codex 皆自動載入），本檔不重複，只寫本專案的事。

⚠️ 這是本檔唯一允許指向庫外檔案的地方，代價已知：該檔不隨專案版控，換機器就沒有。
因此**專案作業本身的規則一律留在本檔**——讀不到全域檔時，頂多少掉通用習慣，不會做錯專案的事。

> **新對話開始工作前**：完整讀取 `docs/handover.md`（不入庫）、`AGENTS.md`（本檔）、`DEVELOPER.md`、`PITFALLS.md`；交接表記錄目前進度、待辦與已議定決策。`docs/handover.md` 只補充當輪進度，長期有效的規則與技術決策要回寫正式文件。

## 這是什麼

- **技術棧**：Python + SQLite ＋ Vue 3 無建置前端（prod 版）＋ pywebview Desktop-only 桌面程式；不提供 HTTP adapter；使用者為手機配件行店員
- **目標環境**：Windows；PyInstaller `--onefile` 打包
- **文件分工**：`README.md`＝使用者門面；`DEVELOPER.md`＝技術文件（架構／慣例／測試／打包／版號／近三版記錄）；`HISTORY.md`＝較舊版本記錄；`PITFALLS.md`＝踩雷速查表（症狀→解法）；`AGENTS.md`＝協作規則（本檔）；`docs/handover.md`＝跨對話交接（不入庫）

## 任務對照表（動手前先讀哪裡）

依任務對照下表（皆在 DEVELOPER.md 與 PITFALLS.md）。寫過的雷再踩會被直接點名。

| 要做的事 | 動手前先讀 |
|----------|------------|
| 改 schema／migration／種子 | DEVELOPER §2 資料模型；PITFALLS SQL 組 |
| 商品／規格欄／選項邏輯 | DEVELOPER §2 資料模型、商品與規格設定 |
| 條碼／自取碼 | DEVELOPER §2 條碼 |
| 前端版面／樣式／JS | DEVELOPER §2 UI 風格規範；PITFALLS VUE 組 |
| 設定頁 | DEVELOPER §2 UI 風格規範；PITFALLS VUE-1（v-if 動態 `:key` 雷） |
| 測試 | DEVELOPER §3；PITFALLS TEST 組 |
| 打包 | DEVELOPER §4；PITFALLS PKG 組 |
| 版號／發布 | DEVELOPER §5＋本檔 C 節 |
| Shell／commit 操作 | PITFALLS PS 組 |

## B. 產出（程式與檔案）

- 改完先 `py_compile`；單純字串修正（改字、文案）不必跑測試。
- **單元測試放 `tests/`**：`python -m unittest discover -s tests`，檔名 `test_*.py`；交付前以此指令作為正式測試 gate，完整測試須全數通過，不得將缺少未入庫工具造成的失敗視為預期。
- **測試分兩階**（通則見全域設定）：②＝上述完整測試指令。**以下檔案一改就直接跑 ②**：`tests/base.py`（測試共用準備程式）、`lib/db_schema.py`、`lib/legacy_migrations.py`、migration／種子資料相關程式、`static/css/` 共用樣式。
- **權限 gate**（通則見全域設定）另加：刪除、覆寫等破壞性操作若經確認視窗延後執行，實際執行前再次檢查權限與目標狀態。
- **UI 從簡是硬性要求**：不新增分頁、無巢狀對話框、逐層解鎖；「一排喔，不要搞成兩排（除非有操作）」。
- ⚠️ **驗證畫面一律加驗小解析度 1280×750**（2026-10-30 維護者規定）：改到主視窗、子視窗、版面、按鈕、表格欄位或 `docs/` 小工具，驗證時除了正常大小，再以 1278×720 檢查一次，做法見 DEVELOPER §2 UI 風格規範「小解析度標準」。
- **前端目視 preview 可用 bridge 模擬工具**（launch 名 `pos`，port 8737），不要用 Bash 起 server；該模擬不是正式 runtime，最終行為驗證仍須跑真實 pywebview。css／js 有改就 bump `index.html` 的 `?v=`（css、全部 js 共用同一版號，一起 bump）。
- ⚠️ **Codex 沙盒內跑不起 pywebview**：2026-10-20 本機實測，以 unelevated 沙盒搭配預設資料夾、專案內資料夾、`--no-sandbox` 等設定，Edge WebView2 都無法啟動（跳出「無法建立資料目錄」、逾時或初始化失敗）；elevated 模式未能完成測試。跑真實 pywebview 走查時，**該指令直接申請在沙盒外執行**（需維護者同意），不要在沙盒內反覆嘗試或想辦法繞過；走查一律用暫存資料庫，暫存資料夾用完即刪。

## C. 版本 / Git / 發布 / 打包

### 版本號

- 定義於 `lib/version.py`，日常只進第三碼。進版一律跑 `python tools/bump_version.py {版號}`（同時改 `version.py`、產 `version_info.txt`）。
- 「bump_version＋`git tag v{版號}`＋DEVELOPER §6 補一列（被擠掉的舊列搬到 `HISTORY.md`）」這組機械動作稱**「版號進版」**；「進版／發布版本／出一版」是下方整套流程。

### Git

- ⚠️ **公開 repo**（https://github.com/jerrygskk/POS）：push 前必確認無真實人名／個資（測試 fixture、文件範例、資料庫檔）；所有 xlsm/xlsx 已 gitignore，**絕不入庫**。

### 發布

- release note 檔名 `release_note_v{版號}.md`，放專案根目錄（不入庫）。
- **發布版本標準流程（照順序做到底）**：
  1. 寫文件內文：技術章節補進 DEVELOPER.md、§6 版本記錄；使用者有感的改動 README 也同步
  2. 寫 handover（需跨對話交接才寫）
  3. 寫 release note（第 7 步會把它當 GitHub Release 的發布說明公開，任何人都看得到：不得寫真實人名或個資）
  4. 版號進版
  5. 推上去 + `git tag v{版號}` + push tag
  6. build：onefile 全新 build，回報成功/失敗
  7. 上傳發布：`python tools/publish_release.py`，回報成功/失敗（工具會讀 release note 當發布說明）
- 發布順序鐵則：**文件／release note 要在「版號進版 commit」之前寫好**，tag 才指向含完整文件的 commit；先打 tag 事後補文件＝退版重做。
- ⚠️ tag 已 push 後要移動：本地 `git tag -f` 後，遠端**先刪再推**（`git push origin :refs/tags/v{版號}` 再 push）。

### 打包（PyInstaller）

- 打包設定檔 `POS.spec` 入庫，不再刪除重產；`DEVELOPER.md` §4 是完整打包命令的唯一來源，直接依該節執行。
- 可直接本機執行 build，完成只回報成功/失敗（失敗才貼錯誤末段）。
