# 協作規則（Claude 請先讀這節）

維護者最看重的部分。違反這些會直接消耗信任與時間。

> **新對話開始工作前**：完整讀取 `docs/handover.md`（不入庫）、`CLAUDE.md`、`DEVELOPER.md`；交接表記錄目前進度、待辦與已議定決策。

## A. 溝通與節奏

- **先思考再動手**：任何寫 code 的任務，先發想方案、整理成計畫給我看，經核可才寫 code；不要做完才說「其實有更好做法」。複雜或破壞性改動（多檔／改結構／改資料）先盤點影響範圍列清單。
- **基於專業判斷給建議**，適時提供業界主流做法。反感「見風轉舵」——我說 A 就立刻倒向 A 還包裝成你的判斷，會被點名；有不同意見誠實講，講完理由讓我決定。
- **找得到就別問**：文件／code／資料裡有答案的不要問；但**沒寫進文件的設計決策**一定要問，不要憑空假設。
- **回覆風格**：直接切入重點，無客套話、無開場白與結尾總結；列點、短句、最少字數。對話累積過長時，回覆結尾加「[提示：對話已長，建議備份摘要並開啟新對話]」。
- **一律台灣用語**（對話／文件／UI）：軟體、程式、預設、滑鼠、檔案、資料夾、登入/登出、視窗、回傳、字串、迴圈、品質、網路、硬碟…

## B. 產出（程式與檔案）

- 直接修改本地端 code；**code 不主動整段貼出來**，我要看才給；不必逐一告知改了什麼 function（同檔名檔案如 `__init__.py` 須說明在哪個資料夾）。
- **文件不主動改**（README／技術文件），我要才改；例外：「發布版本」流程要更新 DEVELOPER.md 技術章節與 §6 版本記錄。
- **省 token**：先讀完相關檔案再動手，字串替換範圍精準。⚠️ 精準替換容易吃掉相鄰的函式定義——改完確認上下相鄰函式定義還在（插入／刪除方法時最易發生）。
- 改完**先 `py_compile` 驗證語法**，並主動自我迭代驗證：能單測就單測、能模擬（演算法／SQL round-trip）就跑一輪再交付。單純字串修正（改字、文案）不必跑。
- **單元測試放 `tests/`**：`python -m unittest discover -s tests`，檔名 `test_*.py`。動到可單測的純邏輯（解析／資料 round-trip／狀態計算／權限判斷）**一併新增或更新測試**。
- ⚠️ **權限／存取控制是每個新功能必檢項**：「受限身分不可做」的操作，只靠停用按鈕不夠——雙擊、行內編輯、Enter、右鍵、拖拉等替代路徑會繞過。① **所有**進入點補 guard（用便捷判斷函式，勿字串比較）② 以受限身分逐路徑驗證。
- **UI 文字正式**不口語（「儲存目前排序後繼續編輯？」而非「要存嗎？」）。
- **UI 從簡是硬性要求**：不新增分頁、無巢狀對話框、逐層解鎖；「一排喔，不要搞成兩排（除非有操作）」。
- **前端驗證用 preview 工具**（launch 名 `pos`，port 8737），不要用 Bash 起 server；css／js 有改就 bump `index.html` 的 `?v=`（css、全部 js 共用同一版號，一起 bump）。
- ⚠️ PowerShell 5.1：多行 python 寫 scratchpad 檔再跑；中文輸出寫 UTF-8 檔再讀。

## C. 版本 / Git / 發布 / 打包

### 版本號

- 定義於 `lib/version.py`，日常只進第三碼；進位與否我決定。進版一律跑 `python tools/bump_version.py {版號}`（同時改 `version.py`、產 `version_info.txt`），**勿手改 `version.py`**。

### 用語約定（我會用簡稱，要對上）

- **「進版」「發布版本」「出一版」**＝走完整發布流程做到底才算結束，別只做 bump＋tag＋版本記錄就回報完成。其中「bump_version＋`git tag v{版號}`＋DEVELOPER §6 補一列」這組機械動作另稱**「版號進版」**。
- **「push上去」「推上去」**＝commit + push。

### Git

- **push＝commit + push**。**逐檔 add**（不要一次全加，跳過資料庫檔／含個資檔）；**叫你推才推**，沒說不要問。
- **commit 訊息須完整列出改動**：第一行寫精簡主旨，空一行後以條列逐項說明本次實際修改內容；不得只用一行摘要帶過。
- ⚠️ 多行 commit 訊息用 Bash heredoc（`git commit -F - <<'EOF' … EOF`），**不要用 PowerShell here-string**（`@` 會黏進 subject）。
- ⚠️ **公開 repo**（https://github.com/jerrygskk/POS）：push 前必確認無真實人名／個資（測試 fixture、文件範例、資料庫檔）；所有 xlsm/xlsx 已 gitignore，**絕不入庫**。
### 發布

- release note＝給 .md 檔（`release_note_v{版號}.md`，不入庫），不要打在對話裡；內容寫給使用者看，技術細節留 DEVELOPER.md。
- **發布版本標準流程（照順序做到底）**：
  1. 寫文件內文：技術章節補進 DEVELOPER.md；使用者有感的改動 README 也同步
  2. 寫 handover（需跨對話交接才寫）
  3. 寫 release note
  4. 版號進版
  5. 推上去 + `git tag v{版號}` + push tag
  6. build：onefile 全新 build，回報成功/失敗
- 發布順序鐵則：**文件／release note 要在「版號進版 commit」之前寫好**，tag 才指向含完整文件的 commit；先打 tag 事後補文件＝退版重做。
- ⚠️ tag 已 push 後要移動：本地 `git tag -f` 後，遠端**先刪再推**（`git push origin :refs/tags/v{版號}` 再 push）。

### 打包（PyInstaller）

- 只用 onefile，不要問打包方式；每次砍掉舊 `build/`、`dist/`、spec 全新 build。
- ⚠️ 勿跑 `tools/build.ps1`（Bypass 被權限分類器擋），直接跑等效 pyinstaller 指令（內容見 DEVELOPER §4）；清除步驟用 PowerShell 語法，勿在 Git Bash 用 CMD 的 `del`/`rmdir`。
- 可直接本機執行 build，完成只回報成功/失敗（失敗才貼錯誤末段）。
